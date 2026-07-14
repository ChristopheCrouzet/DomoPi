#!/bin/bash
# Partie distante du déploiement dev (exécutée sur le Pi par tools/deploy.ps1).
# Extrait /tmp/domopi-deploy.tar.gz dans /opt/domopi, met à jour les
# dépendances si requirements.txt a changé, redémarre le service et vérifie
# que l'API répond (401 attendu sans session).
set -e

old=$(md5sum /opt/domopi/requirements.txt | cut -d" " -f1)
tar -xzf /tmp/domopi-deploy.tar.gz -C /opt/domopi
rm -f /tmp/domopi-deploy.tar.gz
new=$(md5sum /opt/domopi/requirements.txt | cut -d" " -f1)
if [ "$old" != "$new" ]; then
  echo "requirements.txt modifié : mise à jour des dépendances..."
  /opt/domopi/venv/bin/pip install -q -r /opt/domopi/requirements.txt
fi

sudo -n /usr/bin/systemctl restart domopi
sleep 3
state=$(systemctl is-active domopi)
echo "service : $state"
[ "$state" = "active" ]

code=$(curl -sk -o /dev/null -w "%{http_code}" https://127.0.0.1/api/me)
echo "API /api/me : $code"
if [ "$code" != "401" ]; then
  echo "--- derniers logs du service ---"
  journalctl -u domopi -n 20 --no-pager || true
  exit 1
fi
echo "déploiement OK"
