#!/bin/bash
# =============================================================================
# DomoPi — installeur pour Raspbian / Raspberry Pi OS (Lite ou Desktop)
# Cible : Raspberry Pi 2 ou 3, distribution fraîchement flashée.
#
# Installe et configure :
#   - nginx (reverse proxy HTTPS, certificat auto-signé, durcissement basique)
#   - Mosquitto (broker MQTT local pour le WES Cartelectronic)
#   - Python 3 + venv + application DomoPi (FastAPI/uvicorn, service systemd)
#   - Base SQLite dans /var/lib/domopi
#
# Usage :  sudo bash install.sh
# Relançable sans risque (idempotent).
# =============================================================================
set -euo pipefail

APP_DIR=/opt/domopi
DATA_DIR=/var/lib/domopi
CONF_DIR=/etc/domopi
TLS_DIR=$CONF_DIR/tls
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

msg()  { echo -e "\033[1;33m==> $*\033[0m"; }
fail() { echo -e "\033[1;31mERREUR : $*\033[0m" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Lancez cet installeur avec sudo."
[ -f "$SRC_DIR/domopi/main.py" ] || fail "Lancez install.sh depuis le dossier du projet."

# ---------------------------------------------------------------- paquets
msg "Mise à jour des paquets système (peut être long sur Pi 2/3)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx mosquitto mosquitto-clients \
    python3 python3-venv python3-pip openssl ca-certificates curl

# ---------------------------------------------------------------- compte
msg "Création du compte système domopi…"
id domopi >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin domopi

# ---------------------------------------------------------------- fichiers
msg "Installation de l'application dans $APP_DIR…"
mkdir -p "$APP_DIR" "$DATA_DIR" "$CONF_DIR" "$TLS_DIR"
cp -r "$SRC_DIR/domopi" "$APP_DIR/"
find "$APP_DIR/domopi" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
# statique : ne pas écraser les icônes/fonds ajoutés par l'utilisateur
mkdir -p "$APP_DIR/static"
cp -rn "$SRC_DIR/static/." "$APP_DIR/static/" 2>/dev/null || true
cp -f  "$SRC_DIR/static/index.html" "$SRC_DIR/static/admin.html" "$APP_DIR/static/"
cp -rf "$SRC_DIR/static/css" "$SRC_DIR/static/js" "$APP_DIR/static/"
mkdir -p "$APP_DIR/static/icons" "$APP_DIR/static/backgrounds"
cp -n "$SRC_DIR/static/icons/." "$APP_DIR/static/icons/" -r 2>/dev/null || true
cp "$SRC_DIR/requirements.txt" "$APP_DIR/"

msg "Environnement Python (venv)…"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

chown -R domopi:domopi "$DATA_DIR" "$APP_DIR/static/icons" "$APP_DIR/static/backgrounds"
chmod 750 "$DATA_DIR"

# ---------------------------------------------------------------- admin
if [ ! -f "$CONF_DIR/domopi.env" ]; then
    msg "Compte administrateur DomoPi"
    read -rp "  Nom d'utilisateur admin [admin] : " ADMIN_USER
    ADMIN_USER=${ADMIN_USER:-admin}
    while true; do
        read -rsp "  Mot de passe admin : " ADMIN_PASS; echo
        [ ${#ADMIN_PASS} -ge 8 ] && break
        echo "  Le mot de passe doit faire au moins 8 caractères."
    done
    cat > "$CONF_DIR/domopi.env" << EOF
DOMOPI_ADMIN_USER=$ADMIN_USER
DOMOPI_ADMIN_PASSWORD=$ADMIN_PASS
EOF
    chmod 600 "$CONF_DIR/domopi.env"
else
    msg "Compte admin déjà configuré ($CONF_DIR/domopi.env), conservé."
fi

# ---------------------------------------------------------------- MQTT
if [ ! -f /etc/mosquitto/domopi_passwd ]; then
    msg "Broker MQTT local (Mosquitto) — identifiants pour le WES"
    read -rp "  Utilisateur MQTT [wes] : " MQTT_USER
    MQTT_USER=${MQTT_USER:-wes}
    read -rsp "  Mot de passe MQTT : " MQTT_PASS; echo
    touch /etc/mosquitto/domopi_passwd
    mosquitto_passwd -b /etc/mosquitto/domopi_passwd "$MQTT_USER" "$MQTT_PASS"
    chown mosquitto:mosquitto /etc/mosquitto/domopi_passwd
    chmod 600 /etc/mosquitto/domopi_passwd
    echo "  -> Renseignez ces identifiants dans le WES (menu MQTT) ET dans le"
    echo "     connecteur WES de DomoPi (Paramètres > Contrôleurs)."
else
    msg "Identifiants MQTT déjà présents, conservés."
fi
cp "$SRC_DIR/deploy/mosquitto-domopi.conf" /etc/mosquitto/conf.d/domopi.conf
systemctl enable mosquitto >/dev/null
systemctl restart mosquitto

# ---------------------------------------------------------------- TLS
if [ ! -f "$TLS_DIR/domopi.crt" ]; then
    msg "Génération d'un certificat TLS auto-signé (10 ans)…"
    HOSTIP=$(hostname -I | awk '{print $1}')
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -keyout "$TLS_DIR/domopi.key" -out "$TLS_DIR/domopi.crt" \
        -subj "/CN=domopi.local" \
        -addext "subjectAltName=DNS:domopi.local,DNS:$(hostname),IP:${HOSTIP:-127.0.0.1}" \
        >/dev/null 2>&1
    chmod 600 "$TLS_DIR/domopi.key"
    echo "  (Le navigateur affichera un avertissement : certificat auto-signé."
    echo "   Migration Let's Encrypt possible plus tard, voir README.)"
fi

# ---------------------------------------------------------------- nginx
msg "Configuration de nginx (HTTPS)…"
cp "$SRC_DIR/deploy/domopi-proxy-params" /etc/nginx/domopi-proxy-params
cp "$SRC_DIR/deploy/nginx-domopi.conf" /etc/nginx/sites-available/domopi
grep -q limit_req_zone /etc/nginx/conf.d/domopi-limits.conf 2>/dev/null || \
    echo 'limit_req_zone $binary_remote_addr zone=domopi_login:1m rate=5r/m;' \
    > /etc/nginx/conf.d/domopi-limits.conf
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/domopi /etc/nginx/sites-enabled/domopi
nginx -t
systemctl enable nginx >/dev/null
systemctl restart nginx

# ---------------------------------------------------------------- systemd
msg "Service systemd domopi…"
cp "$SRC_DIR/deploy/domopi.service" /etc/systemd/system/domopi.service
systemctl daemon-reload
systemctl enable domopi >/dev/null
systemctl restart domopi

# ---------------------------------------------------------------- bilan
sleep 3
HOSTIP=$(hostname -I | awk '{print $1}')
if systemctl is-active --quiet domopi; then
    msg "Installation terminée !"
    echo
    echo "  Interface web  : https://${HOSTIP:-<ip-du-pi>}/"
    echo "  Compte admin   : celui saisi ci-dessus (Paramètres > Contrôleurs pour"
    echo "                   ajouter votre box eedomus et votre WES)."
    echo "  Broker MQTT    : ${HOSTIP}:1883 (à renseigner dans le WES, FW >= 0.9b05)"
    echo "  Journal app    : bouton « Journal » dans le bandeau."
    echo "  Services       : systemctl status domopi nginx mosquitto"
    echo
    echo "  Exposition Internet : redirigez UNIQUEMENT le port 443 vers ce Pi."
    echo "  Ne redirigez jamais le port 1883 (MQTT) ni le 8000."
else
    fail "Le service domopi n'a pas démarré. Consultez : journalctl -u domopi -n 50"
fi
