# deploy.ps1 - pousse le code local sur le Pi de test et redemarre le service.
#
# Usage :  powershell -File tools\deploy.ps1
#
# Prerequis (une seule fois) :
#   - cle SSH installee pour l'utilisateur `claude` sur PI-SERVER
#     (Host PI-SERVER dans ~/.ssh/config, IdentityFile ~/.ssh/domopi_pi)
#   - sur le Pi : /opt/domopi appartient a claude, et sudoers autorise
#     `systemctl restart domopi` sans mot de passe (voir CLAUDE.md).
#
# La logique cote Pi est dans deploy-remote.sh (envoye a chaque deploiement).
# Seul le code est envoye (domopi/, static/, requirements.txt) : la base, la
# cle de session et la config nginx/mosquitto ne sont pas touchees. Les
# icones/fonds ajoutes par l'utilisateur sont preserves (tar n'efface pas les
# fichiers absents de l'archive).
#
# NB : fichier volontairement en ASCII pur (PowerShell 5.1 lit les .ps1 sans
# BOM en cp1252) ; ne pas y introduire d'accents.

$ErrorActionPreference = "Stop"
$RemoteHost = "PI-SERVER"
$Root = Split-Path -Parent $PSScriptRoot
$Tarball = Join-Path $env:TEMP "domopi-deploy.tar.gz"

Write-Host "[1/3] Archive du code..."
# tar de Windows explicitement : lance depuis Git Bash, le PATH herite fait
# resoudre "tar" vers le tar MSYS, qui echoue sur le chemin Windows du projet
# (lettre de lecteur + espace dans "domopi setup").
$Tar = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path $Tar)) { $Tar = "tar" }
& $Tar -czf $Tarball --exclude "__pycache__" -C $Root domopi static requirements.txt
if ($LASTEXITCODE -ne 0) { throw "tar a echoue" }

Write-Host "[2/3] Envoi vers ${RemoteHost}..."
scp -q $Tarball "$Root\tools\deploy-remote.sh" "${RemoteHost}:/tmp/"
if ($LASTEXITCODE -ne 0) { throw "scp a echoue" }
Remove-Item $Tarball

Write-Host "[3/3] Deploiement + redemarrage du service..."
ssh $RemoteHost "bash /tmp/deploy-remote.sh"
if ($LASTEXITCODE -ne 0) { throw "le deploiement distant a echoue" }
