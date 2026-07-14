# deploy.ps1 — pousse le code local sur le Pi de test et redémarre le service.
#
# Usage :  powershell -File tools\deploy.ps1
#
# Prérequis (une seule fois) :
#   - clé SSH installée pour l'utilisateur `claude` sur PI-SERVER
#     (Host PI-SERVER dans ~/.ssh/config, IdentityFile ~/.ssh/domopi_pi)
#   - sur le Pi : /opt/domopi appartient à claude, et sudoers autorise
#     `systemctl restart domopi` sans mot de passe (voir CLAUDE.md).
#
# Le script n'envoie que le code (domopi/, static/, requirements.txt) :
# la base, la clé de session et la config nginx/mosquitto ne sont pas touchées.
# Les icônes/fonds ajoutés par l'utilisateur sont préservés (tar n'efface pas
# les fichiers absents de l'archive).

$ErrorActionPreference = "Stop"
$RemoteHost = "PI-SERVER"
$Root = Split-Path -Parent $PSScriptRoot
$Tarball = Join-Path $env:TEMP "domopi-deploy.tar.gz"

Write-Host "[1/4] Archive du code..."
tar -czf $Tarball -C $Root domopi static requirements.txt
if ($LASTEXITCODE -ne 0) { throw "tar a échoué" }

Write-Host "[2/4] Envoi vers ${RemoteHost}..."
scp -q $Tarball "${RemoteHost}:/tmp/domopi-deploy.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "scp a échoué" }
Remove-Item $Tarball

Write-Host "[3/4] Déploiement + redémarrage du service..."
$remote = @'
set -e
old=$(md5sum /opt/domopi/requirements.txt | cut -d" " -f1)
tar -xzf /tmp/domopi-deploy.tar.gz -C /opt/domopi
rm /tmp/domopi-deploy.tar.gz
new=$(md5sum /opt/domopi/requirements.txt | cut -d" " -f1)
if [ "$old" != "$new" ]; then
  echo "requirements.txt modifié : mise à jour des dépendances..."
  /opt/domopi/venv/bin/pip install -q -r /opt/domopi/requirements.txt
fi
sudo -n /usr/bin/systemctl restart domopi
sleep 3
systemctl is-active domopi
'@
$remote | ssh $RemoteHost "bash -s"
if ($LASTEXITCODE -ne 0) { throw "le déploiement distant a échoué" }

Write-Host "[4/4] Vérification de l'API..."
$code = ssh $RemoteHost 'curl -sk -o /dev/null -w "%{http_code}" https://127.0.0.1/api/me'
if ($code -eq "401") {
    Write-Host "OK : service actif, API répond (401 attendu sans session)."
} else {
    Write-Warning "Réponse inattendue de /api/me : '$code' (401 attendu)."
    ssh $RemoteHost 'journalctl -u domopi -n 20 --no-pager 2>/dev/null || sudo -n journalctl -u domopi -n 20 --no-pager'
    throw "vérification échouée"
}
