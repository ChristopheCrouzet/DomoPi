#!/bin/bash
# =============================================================================
# DomoPi — bascule du certificat auto-signé vers un certificat Let's Encrypt
#
# Usage (en session SSH sur le Pi) :  sudo domopi-https mondomaine.exemple.fr
#                                     sudo domopi-https            (interactif)
#
# Prérequis, vérifiés par le script :
#   - le nom de domaine pointe sur votre connexion (DNS public) ;
#   - le port 80 est redirigé par la box vers ce Pi — c'est par lui que
#     Let's Encrypt dépose et relit son jeton (challenge HTTP-01), à l'émission
#     comme à chaque renouvellement automatique.
#
# Relançable sans risque : un certificat déjà valide n'est pas réémis (le quota
# d'émission de Let's Encrypt n'est pas gaspillé), seuls les liens et le
# rechargement de nginx sont refaits.
# =============================================================================
set -euo pipefail

TLS_DIR=/etc/domopi/tls
WEBROOT=/var/www/certbot
NGINX_SITE=/etc/nginx/sites-available/domopi

msg()  { echo -e "\033[1;33m==> $*\033[0m"; }
ok()   { echo -e "\033[1;32m    $*\033[0m"; }
warn() { echo -e "\033[1;35m    $*\033[0m"; }
fail() { echo -e "\033[1;31mERREUR : $*\033[0m" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Lancez ce script avec sudo."
[ -f "$NGINX_SITE" ] || fail "Configuration nginx de DomoPi introuvable ($NGINX_SITE) : lancez d'abord install.sh."

# ---------------------------------------------------------------- domaine
DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
    read -rp "Nom de domaine public du Pi (ex. maison.exemple.fr) : " DOMAIN
fi
[ -n "$DOMAIN" ] || fail "Aucun nom de domaine fourni."
case "$DOMAIN" in
    *[!a-zA-Z0-9.-]*|-*|.*|*.) fail "Nom de domaine invalide : $DOMAIN" ;;
    *.*) ;;
    *) fail "Nom de domaine invalide (il faut un nom complet, avec un point) : $DOMAIN" ;;
esac

EMAIL="${2:-}"

# ---------------------------------------------------------------- prérequis
msg "Vérification des prérequis…"

command -v certbot >/dev/null 2>&1 || {
    msg "Installation de certbot…"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq certbot
}
ok "certbot : $(certbot --version 2>&1)"

install -d -m 755 "$WEBROOT"

# Le bloc de validation doit exister dans la configuration nginx, et la
# redirection vers HTTPS doit être DANS « location / » : un « return » de niveau
# serveur s'exécute avant le choix de la location et avalerait le jeton.
grep -q 'acme-challenge' "$NGINX_SITE" \
    || fail "Le bloc /.well-known/acme-challenge/ manque dans $NGINX_SITE.
         Reposez la configuration livrée : deploy/nginx-domopi.conf"

IP_PUB=$(getent hosts "$DOMAIN" | awk '{print $1; exit}' || true)
[ -n "$IP_PUB" ] || fail "Le nom « $DOMAIN » ne se résout pas. Vérifiez le DNS (ou le DNS dynamique de votre box)."
ok "$DOMAIN se résout en $IP_PUB"

nginx -t >/dev/null 2>&1 || fail "La configuration nginx actuelle est invalide (« nginx -t » pour le détail)."
systemctl is-active --quiet nginx || { msg "Démarrage de nginx…"; systemctl start nginx; }

# Le jeton est-il réellement servi en clair ? Contrôle en local (le port 80 vu
# de l'extérieur, lui, ne peut être vérifié que par Let's Encrypt : c'est ce que
# fait la répétition à blanc juste après).
TOKEN="domopi-selftest-$$"
echo "ok" > "$WEBROOT/$TOKEN"
trap 'rm -f "$WEBROOT/$TOKEN"' EXIT
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
       -H "Host: $DOMAIN" "http://127.0.0.1/.well-known/acme-challenge/$TOKEN" || echo 000)
[ "$CODE" = "200" ] || fail "nginx ne sert pas le jeton de validation en clair (code HTTP $CODE au lieu de 200).
         La redirection vers HTTPS doit être dans « location / », pas au niveau du serveur."
ok "nginx sert bien le jeton de validation (HTTP 200)"
rm -f "$WEBROOT/$TOKEN"; trap - EXIT

# ---------------------------------------------------------------- émission
LIVE=/etc/letsencrypt/live/$DOMAIN
if [ -f "$LIVE/fullchain.pem" ] \
   && openssl x509 -checkend 604800 -noout -in "$LIVE/fullchain.pem" >/dev/null 2>&1; then
    msg "Certificat déjà présent et valide pour $DOMAIN : émission ignorée."
else
    # Demandée seulement ici : inutile de la réclamer sur une relance où
    # l'émission est ignorée.
    if [ -z "$EMAIL" ]; then
        read -rp "Adresse e-mail (avis d'expiration Let's Encrypt, vide pour ne pas en donner) : " EMAIL
    fi
    if [ -n "$EMAIL" ]; then EMAIL_ARG=(-m "$EMAIL")
    else EMAIL_ARG=(--register-unsafely-without-email); fi

    msg "Répétition à blanc (serveur de test, ne consomme pas le quota d'émission)…"
    certbot certonly --dry-run --webroot -w "$WEBROOT" -d "$DOMAIN" \
        --non-interactive --agree-tos "${EMAIL_ARG[@]}" \
        || fail "La validation à blanc a échoué.
         Cause la plus fréquente : le port 80 n'est pas redirigé par la box vers ce Pi.
         Détail complet dans /var/log/letsencrypt/letsencrypt.log"
    ok "Validation à blanc réussie."

    msg "Demande du certificat définitif…"
    certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
        --non-interactive --agree-tos "${EMAIL_ARG[@]}" \
        || fail "L'émission a échoué alors que la répétition à blanc était passée.
         S'il s'agit d'un dépassement de quota (« too many certificates »), le nom de
         domaine est partagé avec d'autres abonnés (cas des *.hd.free.fr) : il faut
         attendre, ou prendre un nom de domaine à vous.
         Détail complet dans /var/log/letsencrypt/letsencrypt.log"
fi

# ---------------------------------------------------------------- branchement
# Rechargement de nginx après chaque renouvellement. Un crochet *global*
# (renewal-hooks/deploy) plutôt qu'un --deploy-hook attaché au certificat : il
# s'applique même si le certificat a été émis auparavant à la main, cas où
# l'émission est ignorée plus haut. Sans lui, nginx continuerait de servir en
# mémoire le certificat expiré jusqu'au prochain redémarrage.
HOOK=/etc/letsencrypt/renewal-hooks/deploy/domopi-reload-nginx.sh
install -d -m 755 "$(dirname "$HOOK")"
cat > "$HOOK" <<'EOF'
#!/bin/sh
# Posé par domopi-https : recharge nginx après un renouvellement réussi.
systemctl reload nginx
EOF
chmod 755 "$HOOK"

# Des liens, et non une copie : install.sh réécrit la configuration nginx à
# chaque passage, et certbot remplace les fichiers à chaque renouvellement.
msg "Branchement de nginx sur le certificat…"
ln -sfn "$LIVE/fullchain.pem" "$TLS_DIR/domopi.crt"
ln -sfn "$LIVE/privkey.pem"   "$TLS_DIR/domopi.key"
nginx -t || fail "Configuration nginx invalide après le branchement (rien n'a été rechargé)."
systemctl reload nginx
ok "nginx rechargé."

# ---------------------------------------------------------------- vérification
msg "Vérification depuis le Pi…"
SUBJ=$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" 2>/dev/null \
       | openssl x509 -noout -subject -issuer -enddate 2>/dev/null || true)
[ -n "$SUBJ" ] && echo "$SUBJ" | sed 's/^/    /'

msg "Vérification du renouvellement automatique…"
if certbot renew --dry-run >/dev/null 2>&1; then
    ok "Renouvellement à blanc réussi."
else
    warn "Le renouvellement à blanc a échoué : le certificat expirera dans 90 jours."
    warn "Vérifiez que le port 80 reste redirigé (« certbot renew --dry-run » pour le détail)."
fi
systemctl list-timers certbot.timer --no-pager 2>/dev/null | sed -n '2p;3p' | sed 's/^/    /' || true

echo
ok "Terminé. https://$DOMAIN/ doit maintenant s'ouvrir sans avertissement."
echo "    Le certificat sera renouvelé automatiquement (vers J-30)."
echo "    Ne refermez pas le port 80 sur la box : le renouvellement en dépend."
