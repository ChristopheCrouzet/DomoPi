# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ce fichier s'adresse à Claude Code (ou tout développeur) reprenant ce projet.
Il décrit l'architecture, les conventions et les points d'attention.

## Vue d'ensemble

DomoPi est un serveur de supervision domotique auto-hébergé pour Raspberry Pi
2/3. Backend **Python 3 / FastAPI / uvicorn** (un seul worker), base **SQLite**,
frontend **vanilla JS sans dépendance** (aucun bundler, aucun framework, aucun
CDN). En production, uvicorn écoute sur `127.0.0.1:8000` derrière **nginx** (TLS)
et un broker **Mosquitto** local sert le connecteur WES.

Contrainte directrice : conçu à l'origine pour un Pi 2 (CPU ARM lent, ~1 Go
RAM). D'où : pas de dépendances lourdes, SQLite plutôt qu'un SGBD, agrégation
des mesures pour limiter le volume, un seul worker, graphes rendus en SVG côté
client sans librairie. Le matériel réel est aujourd'hui un **Raspberry Pi 4
Model B** (aarch64, Debian 13 « trixie », Python 3.13) — les roues binaires
pip (piwheels/manylinux aarch64) sont disponibles, donc pas de compilation à
l'installation — mais la philosophie frugale reste la règle.

## Arborescence du dépôt

Racine du projet sur cette machine : `M:\Domotique\Domopi\domopi setup\`
(cible de déploiement : un Raspberry Pi sous Raspbian).

```
domopi setup/
├── domopi/                  paquet Python (backend)
│   ├── main.py              app FastAPI : toutes les routes /api/*, sert le statique
│   ├── db.py                schéma SQLite, réglages, query_series, rollup/purge
│   ├── backup.py            archives .tar.gz, planification, export FTP, restauration
│   ├── export_ods.py        export ODS des mesures (zip + XML écrits à la main)
│   ├── auth.py              PBKDF2 + cookie de session signé HMAC, rôles
│   ├── journal.py           journal applicatif avec niveaux de verbosité
│   ├── formula.py           moteur de formules des capteurs virtuels
│   ├── poller.py            boucle asyncio de collecte + rollup quotidien + purge journal hebdo
│   └── connectors/
│       ├── base.py          classe abstraite Connector
│       ├── eedomus.py       API locale eedomus (httpx)
│       ├── wes_mqtt.py      client MQTT HA-Discovery (paho-mqtt)
│       ├── virtual.py       connecteur interne des capteurs virtuels (formules)
│       └── __init__.py      REGISTRY {type: classe}
├── static/                  frontend
│   ├── index.html           visualiseur (SPA)
│   ├── admin.html           interface d'administration
│   ├── css/app.css          thème unique « tableau électrique »
│   ├── js/app.js            logique visualiseur
│   ├── js/charts.js         renderChart() — SVG min/moy/max, sans dépendance
│   ├── js/tile.js           tuiles + réglage sur échelle + graphe (partagés)
│   ├── js/admin.js          logique admin
│   ├── icons/               36 icônes SVG générées + uploads utilisateur
│   └── backgrounds/         fonds de page (uploads)
├── tools/
│   ├── make_icons.py        générateur des icônes SVG intégrées
│   ├── make_notice.py       générateur de NOTICE-DomoPi.odt (ODT écrit à la main)
│   │                        — n'écrase jamais la notice existante (--force)
│   ├── setup-https.sh       bascule Let's Encrypt, posé en /usr/local/sbin/domopi-https
│   ├── deploy.ps1           déploiement dev vers le Pi de test (voir plus bas)
│   └── deploy-remote.sh     partie exécutée sur le Pi par deploy.ps1
├── deploy/                  domopi.service, nginx-domopi.conf, proxy-params,
│                            mosquitto-domopi.conf
├── doc/notice/              les 11 captures de la notice (versionnées : la
│                            notice se régénère sans navigateur ; les (re)faire
│                            avec .claude/skills/run-domopi/notice_shots.py)
├── install.sh              installeur idempotent (sudo bash install.sh)
├── requirements.txt
├── README.md               doc utilisateur
├── NOTICE-DomoPi.odt       notice utilisateur — **générée**, ne pas retoucher
└── CLAUDE.md               ce fichier
```

## Chemins en production (posés par install.sh)

| Rôle | Chemin |
|------|--------|
| Code | `/opt/domopi` (copie de `domopi/` + `static/` + `requirements.txt` + venv) |
| Données | `/var/lib/domopi/domopi.db`, `/var/lib/domopi/secret.key` |
| Sauvegardes | `/var/lib/domopi/backups/domopi-backup-AAAAMMJJ-HHMMSS.tar.gz` (réglage `backup_dir`) |
| Config | `/etc/domopi/domopi.env` (admin), `/etc/domopi/tls/` (certificat) |
| systemd | `/etc/systemd/system/domopi.service` |
| nginx | `/etc/nginx/sites-available/domopi`, `/etc/nginx/conf.d/domopi-limits.conf` |
| Mosquitto | `/etc/mosquitto/conf.d/domopi.conf`, `/etc/mosquitto/domopi_passwd` |

Variables d'environnement (service) : `DOMOPI_DB`, `DOMOPI_SECRET`,
`DOMOPI_STATIC`, `DOMOPI_ADMIN_USER`, `DOMOPI_ADMIN_PASSWORD`.

## Développement local

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export DOMOPI_DB=/tmp/dev.db DOMOPI_SECRET=/tmp/dev.key \
       DOMOPI_STATIC=$PWD/static DOMOPI_ADMIN_PASSWORD=devpass123
uvicorn domopi.main:app --reload --port 8000
# http://127.0.0.1:8000   (admin / devpass123, admin sur /static/admin.html)
```

Équivalent Windows (PowerShell), depuis `domopi setup\` :

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DOMOPI_DB = "$env:TEMP\dev.db"; $env:DOMOPI_SECRET = "$env:TEMP\dev.key"
$env:DOMOPI_STATIC = "$PWD\static"; $env:DOMOPI_ADMIN_PASSWORD = "devpass123"
uvicorn domopi.main:app --reload --port 8000
```

Le cookie de session est posé avec `secure=True`, mais **Chrome l'accepte sur
`127.0.0.1`** (origine réputée sûre — vérifié) : inutile de monter du HTTPS ou
de retoucher le drapeau dans `main.py:login` pour travailler en local. Le
piège ne concerne que les clients HTTP Python (voir « Tests »).

Aucune étape de build côté frontend : recharger la page suffit. Pour lancer
l'app **déjà peuplée** et prendre des captures en une commande, voir « Tests ».

## Déploiement de test sur le Pi (dev)

Un Raspberry Pi de test (`PI-SERVER`, résolvable sur le LAN — Pi 4 Model B,
aarch64, Debian 13, Python 3.13) héberge une installation DomoPi standard. Pour y pousser le code local et redémarrer :

```powershell
powershell -File "tools\deploy.ps1"
```

Le script archive `domopi/` + `static/` + `requirements.txt`, l'envoie en SSH
(utilisateur `claude`, clé `~/.ssh/domopi_pi`, config dans `~/.ssh/config` —
avec `KexAlgorithms curve25519-sha256` pour contourner un bug du client
OpenSSH 9.5 de Windows face à OpenSSH ≥ 10), l'extrait dans `/opt/domopi`,
réinstalle les dépendances si `requirements.txt` a changé, redémarre le
service (`sudo systemctl restart domopi`, autorisé sans mot de passe via
`/etc/sudoers.d/claude-domopi`) et vérifie que l'API répond (401 attendu sur
`/api/me`). Base, secret et config nginx/mosquitto ne sont jamais touchés.

Accès direct pour diagnostic : `ssh PI-SERVER "journalctl -u domopi -n 50 --no-pager"`.

Le script appelle explicitement le `tar` de Windows
(`%SystemRoot%\System32\tar.exe`) : lancé depuis Git Bash, un `tar` non
qualifié se résout vers le `tar` MSYS, qui échoue sur le chemin du projet
(lettre de lecteur + espace dans « domopi setup ») avec un « tar a echoue »
intermittent selon le shell appelant.

Le compte `claude` n'a le droit sudo que sur `systemctl start|stop|restart
domopi` : toute retouche de `/etc/nginx` ou de `/var/lib/domopi` (propriété
`domopi`, 750) doit être faite par l'utilisateur. Pratique dans ce cas :
préparer le fichier corrigé dans `/home/claude/` et fournir la commande
`ssh -t PI-SERVER "sudo cp … && sudo nginx -t && sudo systemctl reload nginx"`.

Ce circuit est réservé au développement ; l'installation propre reste
`sudo bash install.sh` (à remettre à jour en fin de projet).

## Tests

Il n'y a pas encore de suite pytest. En attendant, cinq harnais **exécutés et
vérifiés** tiennent ce rôle ; ils vivent dans le skill `/run-domopi`
(`M:\Domotique\Domopi\.claude\skills\run-domopi\`, hors dépôt comme tout
l'outillage Claude — cf. le `CLAUDE.md` racine) :

| Fichier | Niveau | Rôle |
|---|---|---|
| `driver.py` | app complète | lance uvicorn sur une base jetable, peuple un jeu de démo, appelle l'API authentifiée, prend les captures Playwright |
| `direct_example.py` | cœur métier | `query_series` et `formula` **sans serveur** (base SQLite temporaire + `fastapi.testclient`) — squelette de la future suite pytest |
| `backup_example.py` | cœur métier | `backup.py` **sans serveur** : archive et manifeste, rétention, planification (écrêtage des mois, rattrapage), restauration des icônes, fusion d'historique (appariement, non-écrasement), écrasement complet avec et sans conservation de l'historique, garde-fous (traversée de chemin, archive étrangère, réglages invalides) — 50 contrôles |
| `backup_ftp_example.py` | export FTP | `ftp_upload`/`ftp_test` contre un **serveur FTP minimal en stdlib** (PASV et mode actif, anonyme, création du dossier distant, **serveur qui acquiesce au CWD sans changer de dossier**, canal de données coupé, transfert tronqué, dépôt vide de 0 octet, et **FTPS réel** sur certificat auto-signé : serveur exigeant la reprise de session TLS (avec contrôle négatif prouvant que le `ftplib` nu se fait bien refuser) et clôture TLS impolie — les deux pannes Freebox) — 44 contrôles |
| `backup_api_example.py` | API + rôles | parcours HTTP complet sur le serveur de dév : réglages et leurs 400, sauvegarde et suivi du job, téléchargement, import (dont le refus sans session **avant** lecture du corps), restaurations, 409 de concurrence, suppression, refus 403 pour un lecteur — 49 contrôles, ré-entrant |

```powershell
$py  = "M:\Domotique\Domopi\domopi setup\venv\Scripts\python.exe"
$drv = "M:\Domotique\Domopi\.claude\skills\run-domopi\driver.py"
& $py $drv smoke                  # enchaînement complet -> « SMOKE OK »
& $py $drv reset                  # base vierge + relance
& $py $drv seed                   # jeu de démo seul (ré-entrant)
& $py $drv api GET /api/devices   # appel authentifié
& $py $drv shots                  # 6 captures dans %TEMP%\domopi-dev\shots\
& $py $drv mobile                 # rendu iPhone portrait : invariants mesurés
& $py "$sk\backup_example.py"     # sauvegarde/restauration, sans serveur
& $py "$sk\backup_ftp_example.py" # export FTP contre un serveur FTP jetable
& $py "$sk\backup_api_example.py" # API sauvegarde/restauration (serveur requis)
```

(`$sk` = `M:\Domotique\Domopi\.claude\skills\run-domopi`.)

`smoke` couvre : connexion, 401 sans session, jeu de démo (30 j d'historique
au pas de 5 min), lecture de toutes les routes de liste, les **trois régimes
de `query_series`** (`raw` / `hourly` / `daily`), validation de formule (cas
valide et invalide), `POST /set` + `refresh`, refus 400 d'une échelle
invalide, puis les captures (visualiseur 1280×900 et 390×844, dialogue
d'échelle ouvert, admin). Rien n'est écrit dans le dépôt : base, secret, log
et captures vont dans `%TEMP%\domopi-dev\`, et le Pi n'est jamais touché.

Le jeu de démo n'exige **aucune box** : tout repose sur des capteurs virtuels
(température, index de consommation, une `Puissance` calculée par
`Deriver(...)`, une consigne et un gradateur avec leurs échelles), une page
« Maison », une sous-page « Étage » et une page racine « Garage » visée par un
widget `pagelink`.

`mobile` **mesure dans la page** les conventions d'interface fragiles, sur
320×568 / 375×667 / 390×844 / 430×932 en portrait, côté visualiseur **et**
admin : grille à exactement 3 colonnes sous 700 px, tuiles toutes à 122 px,
`scrollWidth <= clientWidth` (aucun défilement horizontal), aucun élément
débordant à droite, boutons de plage jamais sous le titre dans `.chart-head`,
et position réelle des trois barres collantes de l'admin. À relancer après
toute retouche de `app.css` ou de `app.js:renderPage`.

Au 25/07/2026, le **visualiseur** passe les quatre largeurs (à 320 px la valeur
perd son unité par ellipse, `16,81 …` — conséquence assumée des tuiles
uniformes en `nowrap`). L'**admin**, lui, échoue sur les quatre largeurs :

> Le tableau des périphériques n'a pas de conteneur à défilement horizontal.
> Il se tasse à sa largeur mini (836 px) : la page entière défile
> latéralement (bandeau et onglets compris) et seules ~4 colonnes sur 11 sont
> visibles sur un iPhone 14 ; les libellés de colonne se replient, la ligne
> d'en-tête passe de 29 px (bureau) à 67 px, et `#dev-filters top:113px`
> retombe **dans** l'en-tête de tri en masquant les champs de filtre.
> Attention : un simple `overflow-x: auto` autour du tableau casserait les
> trois `position: sticky` (elles se référeraient à ce conteneur, qui ne
> défile pas verticalement). La **liste de cartes en mobile a été prototypée
> le 25/07/2026 puis écartée** : elle supprimait le débordement mais donnait
> ~10 lignes par périphérique, défilement interminable sur un parc réel.
> Piste restante : masquer les colonnes secondaires sous 700 px en gardant le
> tableau et ses en-têtes collants.

Le calage vertical, lui, tient : onglets à −1 px sous le bandeau, en-tête de
tri à −6 px sous les onglets (recouvrement voulu, pas de jour).

Piège de contenu : une sous-page produit **déjà** une tuile « dossier »
(fusion des sous-pages et des widgets dans `app.js:renderPage`) — y ajouter un
widget `pagelink` vers cette même sous-page affiche la tuile en double.

### Pièges à connaître avant d'écrire un script de test

- **Cookie `Secure`** : `httpx` et `fastapi.testclient` refusent de le renvoyer
  sur `http://` — le login rend 200 et l'appel suivant 401. Poser l'en-tête à
  la main, `c.headers["Cookie"] = f"domopi_session={r.cookies['domopi_session']}"`.
  Surcharger `return_ok_secure` d'un `CookieJar` **ne suffit pas** (httpx garde
  le jar, le cookie n'est pas envoyé pour autant).
- **`db.DB_PATH` et `auth.SECRET_PATH` sont figés à l'import** du module, pas
  relus à chaque appel : poser `DOMOPI_DB` / `DOMOPI_SECRET` **avant** tout
  `import domopi.*`, sinon le script écrit dans les chemins de production.
- **`PYTHONPATH`** : un script lancé depuis l'extérieur du projet ne trouve pas
  le paquet `domopi` (`sys.path[0]` = dossier du script, pas le cwd).
- **Verrou de fichier Windows** : ne pas supprimer la base serveur allumé —
  `Remove-Item` échoue en silence et le `seed` suivant s'empile sur les
  anciennes données (points en double, écarts `21 s / 279 s` au lieu de
  `300 s`). Passer par `driver.py reset`, qui arrête le serveur d'abord.
- Bruit attendu et sans gravité : `401` sur `/api/me` avant connexion et
  `GET /favicon.ico → 404` (aucun favicon n'est servi).

Vérifications faites à la génération du projet, à reprendre le jour où la
suite pytest sera écrite : `bash -n install.sh`, imports des modules, smoke
des routes (login, settings, connectors, pages, icons), `query_series` sur
20 jours simulés, et — pour les échelles de pilotage — migrations, CRUD
`/api/scales`, validations 400, affectations, capteurs pilotables.

## Base de données (db.py)

Tables : `users`, `settings`, `connectors`, `scales` (échelles de pilotage),
`devices`, `measures` (brut), `measures_daily` (archives), `pages`, `widgets`,
`journal`. `measures` et
`measures_daily` sont en `WITHOUT ROWID` (clé composite device+temps).

### Logique d'agrégation — `query_series(device_id, t_from, t_to, mode)`

C'est le cœur métier, à préserver. En `mode="auto"` (historique), selon
l'amplitude de la fenêtre demandée :

- **≤ 4 jours** → points bruts, `mode="raw"` (une seule courbe côté client).
- **> 4 jours** → agrégation `GROUP BY` au pas **horaire**, `mode="hourly"`,
  chaque point = `{t, min, avg, max}` (trois courbes).
- **≥ 15 jours** → agrégation au pas **journalier**, `mode="daily"`, complétée
  par les archives `measures_daily` pour les données au-delà de la rétention
  brute.

Le paramètre `mode` de `/api/series/{id}` (`auto` | `raw` | `minmax`) permet de
forcer le rendu : `raw` = points bruts quelle que soit la fenêtre (donc limité
à la rétention du brut), `minmax` = agrégat (journalier si ≥ 15 j, sinon
horaire). `charts.js` s'adapte au champ `mode` renvoyé (courbe simple vs bande
min-max + 3 lignes).

Les **durées proposées sous les graphes** sont configurables : réglage
`chart_ranges` (JSON `[{label, span_s, mode}]`, mode `raw`/`minmax`, validé et
trié par `main.py:_check_chart_ranges`, 1 à 8 entrées), édité dans l'admin
(onglet « Paramètres » → « Paramétrage des courbes » — cartes + dialogue sur le
modèle des échelles, enregistrement immédiat) et exposé aux lecteurs
via `/api/display`. Les défauts (24 h et 4 j bruts, 15/30/90 j et 6 mois en
min/moy/max) sont dupliqués dans `db.py:DEFAULT_SETTINGS`, `app.js` et
`admin.js` (`DEFAULT_RANGES`) — garder les trois cohérents. La « Fenêtre par
défaut du graphe » d'un widget mémorise `range_s` : si cette durée disparaît
du réglage, le graphe retombe sur le premier bouton.

L'onglet **« Paramètres »** de l'admin regroupe « Échelles de pilotage »,
« Affichage » (chiffres significatifs, séparateurs) et « Paramétrage des
courbes ».

### Enregistrement des réglages : à la volée, sans bouton

**Règle d'interface : aucun bouton « Enregistrer » pour un réglage.** Comme le
tableau des périphériques, tous les champs de réglage partent sur l'événement
`change` — donc à la sortie de la cellule — via `admin.js:wireSetting(sel, key,
get, after)` : un `PUT /api/settings` par champ (la route accepte les corps
partiels), toast « Enregistré », et sur refus (400) message + `loadSettings()`
pour que l'écran ne montre jamais une valeur absente de la base. Les boutons
`#s-save` (réglages généraux/affichage) et `#bk-save` (sauvegarde) ont été
supprimés le 27/07/2026 — l'incohérence était visible : le reste de l'admin
enregistrait déjà à la volée. Trois points à respecter en ajoutant un réglage :

- `wireSetting` refuse d'emblée un `<input type="number">` vide ou hors
  `min`/`max` (`checkValidity()`) : sans bouton, il n'y a plus d'étape où
  arbitrer une saisie en cours. Déclarer les bornes dans le HTML.
- Couplages entre champs : le rappel `get` peut renvoyer `null` pour ne rien
  envoyer. Deux cas réels — activer les sauvegardes automatiques sans échéance
  (case re-décochée + message) et une date effacée (on n'écrase pas
  `backup_next_ts` : un `0` ferait replanifier le collecteur).
- `#bk-ftp-test` continue d'appeler `saveBackupSettings()` (envoi groupé) avant
  le test : le clic déclenche bien le `change` du champ quitté, mais rien ne
  garantit que ce `PUT` soit arrivé avant le `POST` du test.

### Rétention — `rollup_and_purge()` et `purge_journal()`

Deux tâches distinctes, appelées par le poller à des cadences différentes :

- `rollup_and_purge()` — **quotidienne**. Agrège en journalier tout le brut plus
  vieux que `raw_retention_days` (défaut 120 j) dans `measures_daily`, puis le
  supprime. S'applique indifféremment à toutes les grandeurs mesurées
  (température, précipitations, luminosité, pression, consommation…) : les
  agrégats min/moy/max valent pour n'importe quelle grandeur physique.
- `purge_journal()` — **hebdomadaire**. Supprime les entrées de journal au-delà
  de `journal_retention_days` (défaut 30 j). Séparée de l'archivage des mesures
  pour éviter d'écrire dans la table `journal` à chaque cycle quotidien.

Le poller garde deux horodatages (`last_rollup`, `last_journal_purge`) et
déclenche chaque tâche quand son délai est dépassé ; au premier démarrage les
deux valent 0, donc les tâches passent une fois au lancement.

## Sauvegarde et restauration (backup.py)

Livrée le 25/07/2026. Rubrique « Sauvegarde et restauration » de l'onglet
« Réglages généraux et comptes ». Une archive `.tar.gz` réunit **toutes les
données utilisateur** : `manifest.json` (écrit **en premier** dans le tar, pour
être relu sans décompresser l'archive entière), `README.txt`, `domopi.db`,
`secret.key`, `icons/`, `backgrounds/`. Elle contient donc les identifiants des
box et la clé de session → créée en `0600`, à traiter comme un secret. Le nom
porte l'horodatage à la seconde, suffixé `-2`, `-3`… si ce nom est déjà pris
(deux sauvegardes dans la même seconde n'écrasent pas la précédente).

- **Instantané de la base** par `VACUUM INTO` (repli sur `Connection.backup()`
  si SQLite < 3.27) : la base est en **WAL**, une copie de fichier à chaud
  serait incohérente. Ne jamais remplacer `domopi.db` par un `os.replace` —
  les connexions sont ouvertes et thread-locales.
- **Exclusivité** : un `threading.Lock` non bloquant ; sauvegarde et
  restauration ne peuvent pas se chevaucher. L'état (`phase`, `pct`, `message`,
  `error`, `report`) est publié par `job_state()` et exposé dans `GET
  /api/backups` → l'admin lance l'opération puis interroge cet état toutes les
  1,5 s (les routes ne bloquent pas l'unique worker : `asyncio.to_thread`).
- **Planification** : réglages `backup_auto`, `backup_next_ts` (epoch de la
  prochaine échéance), `backup_period` (`backup.PERIODS` : 1d/2d/1w/2w/1m/2m/
  6m/1y). `poller.run_forever` appelle `run_scheduled()` à chaque cycle ;
  l'échéance suivante est posée **avant** l'exécution (un échec ne relance pas
  une sauvegarde à chaque cycle) et calculée par pas entiers en heure locale
  (`next_after`) : l'heure choisie est conservée, un arrêt prolongé ne produit
  qu'un seul rattrapage. Le mois est ajouté avec écrêtage du jour (31 janvier
  + 1 mois → 28 février). La sauvegarde automatique tourne **dans le thread du
  collecteur** : sur une grosse base, elle décale le cycle de collecte suivant.
- **Rétention** (`backup_keep`, défaut 8) : ne purge que les noms générés
  `domopi-backup-*.tar.gz` — une archive importée ou déposée à la main n'est
  jamais supprimée automatiquement.
- **Export FTP** (`ftplib`, aucune dépendance) : `backup_ftp_*` — hôte, port,
  dossier distant (créé s'il manque), anonyme ou identifiants, `PASV`, `FTPS`
  explicite (`FTP_TLS` + `prot_p()`). Un échec d'export ne fait pas échouer la
  sauvegarde locale : il part dans le rapport et le journal.
  **Validé en production le 25/07/2026** sur une Freebox (FTPS + PASV, disque
  USB « My Book Duo Raid »). Cinq précautions, toutes nées de cet échec réel —
  le diagnostic a demandé trois tours parce que les symptômes (`ECONNRESET`,
  fichier de 0 octet) ne désignaient pas leur cause :
  - **`_FTPS` reprend la session TLS sur le canal de données** — *la* cause de
    l'échec, à lire en premier (détail deux points plus bas).
  - **La réponse de `CWD` n'est pas fiable** : certains serveurs (partages
    réseau montés) répondent 250 sans changer de dossier quand la cible
    n'existe pas. `_ftp_chdir` compare donc `PWD` avant/après chaque niveau et
    crée (`MKD`) ce qui manque réellement — sans quoi le `STOR` partirait dans
    le mauvais dossier. Robustesse utile, mais ce n'était pas la panne.
  - **Le témoin de `ftp_test()` fait `PROBE_BYTES` (256 Ko)**, pas quelques
    octets : le témoin de 12 octets d'origine « réussissait » (il déposait un
    fichier vide sans que rien ne le vérifie) là où une vraie archive
    échouait — **test vert, sauvegarde rouge**, le pire des cas. Le test
    emprunte désormais le même chemin qu'un vrai envoi (même dossier, même
    canal de données, taille relue) : un test vert prédit un export vert.
  - **Taille relue par `SIZE`** après chaque dépôt (test et envoi réel) :
    détecte un transfert tronqué ou **vide** qu'aucune erreur n'a signalé.
    C'est ce contrôle qui a rendu la panne visible. Serveur sans `SIZE` →
    contrôle ignoré, jamais bloquant. Le reste incomplet est effacé du serveur
    (`_ftp_cleanup`) pour ne pas laisser de fichiers vides derrière.
  - **`_FTPS` reprend la session TLS sur le canal de données** — le point qui
    faisait échouer tout export FTPS (Freebox, 25/07/2026) :
    `FTP_TLS.ntransfercmd` enveloppe la connexion de données dans une session
    TLS **neuve**, alors que la plupart des serveurs FTPS exigent la reprise de
    celle du canal de commande (vsftpd `require_ssl_reuse=YES`, **son défaut** ;
    proftpd `NoSessionReuseRequired` pour s'en passer). Sans reprise, le serveur
    coupe la connexion de données dès la poignée de main : `ECONNRESET` et
    **fichier créé mais vide**, en PASV **comme** en actif — d'où le faux
    diagnostic « pare-feu / mode actif ». `_FTPS.ntransfercmd` court-circuite
    donc `FTP_TLS` (appel direct à `FTP.ntransfercmd`) et enveloppe lui-même en
    passant `session=self.sock.session`. Corollaire : le contexte TLS est
    **plafonné à TLS 1.2** (`_tls_client_context`) — la reprise ne survit pas
    aux tickets de TLS 1.3 sur ces serveurs. La demande correspondante à
    CPython n'a jamais été retenue : ne pas « simplifier » vers `FTP_TLS`.
  - **`_FTPS` tolère aussi une clôture TLS impolie** : en
    fin de `storbinary`, ftplib appelle `unwrap()` sur la connexion de données
    pour échanger le `close_notify`. Les serveurs embarqués (Freebox, NAS)
    ferment sèchement à la place → `ECONNRESET` **alors que le fichier est
    intégralement arrivé**. `ntransfercmd` enveloppe donc `unwrap` pour ignorer
    l'échec de cette seule étape ; c'est sans risque puisque `SIZE` tranche
    ensuite. Ne pas « simplifier » en revenant à `FTP_TLS` : l'export FTPS
    échouerait sur un fichier pourtant bien déposé.
  Les messages distinguent les étages : connexion, authentification, création
  de dossier, refus du serveur (droits/quota), canal de données coupé (avec la
  piste PASV/actif), dépôt incomplet. Leçon de méthode : une coupure du canal
  de données FTPS **n'est pas** une présomption de pare-feu — si le mode actif
  *et* le mode passif échouent alors que le FTP en clair passe, c'est la couche
  TLS du canal de données, pas le réseau.
- **Restauration**, trois volets combinables (`POST /api/restore`) :
  `icons` (icônes + fonds, écrase les fichiers de même nom), `history`
  (**fusion** : appariement `(type de contrôleur, external_id)` avec repli sur
  le nom normalisé — `formula._norm` — non ambigu, puis `INSERT OR IGNORE` sur
  `measures` et `measures_daily` : les points déjà en base gagnent), `full`
  (base de l'archive à l'identique). Option `keep_history` du mode complet :
  un instantané de la base courante est pris avant l'écrasement puis refusionné
  après, ce qui conserve les mesures accumulées depuis la sauvegarde. Option
  `secret` : remplace `secret.key` (déconnecte tout le monde,
  `auth.forget_secret()` vide le cache module).
- **Écrasement sans substitution de fichier** (`_restore_full`) : la base de
  l'archive est `ATTACH`ée, chaque table vidée puis recopiée en une transaction,
  sur l'**intersection des colonnes** des deux schémas (une archive au schéma
  antérieur passe donc), `PRAGMA foreign_keys=OFF` le temps de l'opération,
  puis `db.init_db()` (migrations + réglages par défaut manquants) et
  `poller.reset_instances()` (les contrôleurs viennent de changer en bloc).
  Après ce mode, **les comptes sont ceux de l'archive**.
- **Sécurité d'extraction** : `tarfile.extract()` n'est jamais utilisé. Seuls
  sont extraits les membres d'une liste blanche (`manifest.json`, `README.txt`,
  `domopi.db`, `secret.key`, `icons|backgrounds/<nom sûr>`), copiés à la main
  par `extractfile()` → aucune traversée de chemin possible. Un fichier sans
  manifeste `{"app": "DomoPi"}` est rejeté (import comme restauration).
- **Chemin d'écriture** : `domopi.service` a `ProtectSystem=full` et
  `ReadWritePaths=/var/lib/domopi …` → viser un autre dossier (disque USB)
  exige d'ajouter le chemin à l'unité. `check_setting("backup_dir", …)` teste
  l'écriture au moment de l'enregistrement et renvoie un 400 explicite.
- **Import d'archive en corps brut** (`POST /api/backups/upload?name=…`, et non
  en multipart) : avec `UploadFile`, FastAPI analyse et met en cache **tout le
  corps avant** d'exécuter la fonction, donc avant `require_admin` — un appelant
  non authentifié pouvait faire écrire jusqu'à `MAX_UPLOAD_MB` sur la carte SD
  avant son 401 (mesuré : 9 Mo transférés en entier). En corps brut, le contrôle
  d'accès précède la lecture : 401 après ~300 Ko en vol, rien sur le disque.
  Côté navigateur, `fetch(url, {body: fichier})` — pas de `FormData`.
- **nginx** : l'import d'archive a sa propre `location = /api/backups/upload`
  (`client_max_body_size 1024m`, `proxy_request_buffering off`, timeouts 600 s)
  et le téléchargement une `location ~ ^/api/backups/[^/]+/download$` (timeout
  600 s) — la limite globale du site reste à 8 Mo. Limite applicative :
  `backup.MAX_UPLOAD_MB`. Ces deux blocs **recopient les quatre
  `proxy_set_header`** au lieu d'inclure `domopi-proxy-params` : ce fichier fixe
  `proxy_read_timeout` à 120 s et nginx refuse la directive en double dans un
  même bloc (`"proxy_read_timeout" directive is duplicate`, refus au `nginx -t`).
  `deploy.ps1` ne touchant pas nginx, une installation existante doit recevoir
  ces blocs à la main (ou par `install.sh`), sinon l'import reste plafonné à
  8 Mo ; la restauration depuis une archive locale, elle, fonctionne sans.
  Vérifier une conf nginx **sans root** est possible et doit être fait avant de
  demander un `reload` : copier le candidat dans un bac à sable, remplacer les
  chemins de certificat par un couple auto-signé et les ports par des ports
  hauts, puis `/usr/sbin/nginx -t -c <candidat> -p <bac>` (avec `pid`,
  `error_log`, `access_log` et les `*_temp_path` dans le bac à sable) — et faire
  la contre-épreuve sur la version fautive.
- **install.sh** crée `/var/lib/domopi/backups` (750, `domopi:domopi`). Le
  dossier réel est de toute façon créé à la première sauvegarde par le service.
- **Interface** (`admin.js`, rubrique en bas de l'onglet « Réglages généraux et
  comptes ») :
  - `toast(msg, ms = 2600)` accepte une durée : le résultat du test FTP, long à
    lire, s'affiche 4600 ms.
  - Piège rencontré : les libellés du `<select>` de périodicité viennent du
    serveur (`GET /api/backups`) et peuvent arriver **après** `loadSettings()` —
    poser `.value` sur un `<select>` encore vide est sans effet et la valeur
    enregistrée semblait ignorée. La valeur voulue est donc mémorisée dans
    `bkPeriod` et réappliquée au remplissage des options. Même précaution pour
    tout futur `<select>` alimenté par le serveur.
  - Pendant une opération, l'admin interroge `GET /api/backups` toutes les
    1,5 s ; le job survit à la fermeture de la page (au retour, la rubrique
    reprend le suivi). Une restauration `full`/`history` recharge la page à la
    fin (réglages, pages et périphériques ont pu changer en bloc).
  - CSS ajouté : `input[type=datetime-local]` dans la liste des champs stylés,
    `input:disabled { opacity: .5 }` (identifiants FTP grisés en mode anonyme),
    `a.btn` (le lien « Télécharger » présenté en bouton).
- **Dossiers statiques** : `STATIC_DIR` / `ICONS_DIR` / `BACKGROUNDS_DIR` sont
  définis dans `backup.py` (qui les archive) et importés par `main.py` — une
  seule définition.

## Icônes (static/icons/ + tools/make_icons.py)

Les icônes SVG intégrées (36 à ce jour) sont **générées** par `tools/make_icons.py`
(géométrie calculée, centrée sur (24,24), viewBox 48×48, traits principaux 2.2
et détails 1.5, palette ambre/gris/blanc du thème). Pour modifier ou ajouter
une icône : éditer ce script puis le relancer (`python3 tools/make_icons.py`),
ne pas retoucher les SVG à la main. Contrôle visuel recommandé : rasteriser en
planche contact avec cairosvg + Pillow avant livraison (vérifier symétrie,
chevauchements, lisibilité à 42 px). La skill **`/icone`**
(`M:\Domotique\Domopi\.claude\skills\icone\SKILL.md`, hors dépôt — les
ressources Claude vivent à la racine de l'espace de travail) encadre ce flux
pour les demandes ponctuelles d'icônes : contrat de style, méthode, planche
contact, déploiement — l'utiliser dès qu'on demande une icône. Les icônes ajoutées par l'utilisateur via
l'upload ne sont pas concernées et sont préservées par l'installeur (`cp -n`).

### Génération d'icônes par IA (`domopi/icon_ai.py`)

Livrée le 18/07/2026 (genèse dans `M:\Domotique\Domopi\ROADMAP-icones-IA.md`,
hors dépôt) : bouton « ✨ Générer par IA » dans l'onglet Icônes de l'admin →
dialogue prompt → appel **Claude Sonnet** (`claude-sonnet-5`, effort high)
depuis le backend → prévisualisation des SVG (42 et 96 px, nom éditable,
case par icône) → Valider (ajout à `static/icons/`) / Ajuster (itératif,
historique de conversation conservé côté navigateur — le serveur est
stateless, le champ `raw` de la réponse repart dans l'historique pour que le
modèle revoie ses propres SVG) / Annuler.

Architecture :

- **SDK officiel `anthropic`** (validé sur PI-SERVER : aarch64 → roues
  binaires, pas de compilation), client `AsyncAnthropic` → l'appel ne bloque
  pas l'unique worker uvicorn.
- **Clé API** : `ANTHROPIC_API_KEY` dans `/etc/domopi/domopi.env` (root, 600).
  Jamais en base, jamais envoyée au navigateur ; routes `require_admin`
  (`POST /api/icons/generate` pour la prévisualisation — aucune écriture
  disque — et `POST /api/icons/generate/save` pour l'enregistrement).
- **Style** : prompt système reprenant le contrat de `make_icons.py`
  (ci-dessus) pour que les icônes générées se fondent dans le jeu existant.
- **Sanitisation SVG** systématique (génération **et** sauvegarde) : XML bien
  formé, pas de `script`/`foreignObject`/handlers `on*`/référence externe.

## Pilotage proportionnel : échelles (`scales` + `devices.scale_id`)

Le pilotage proportionnel repose sur des **échelles** réutilisables (table
`scales`), affectées aux périphériques via `devices.scale_id` (NULL =
tout-ou-rien). `controllable` et `scale_id` sont autorisés aussi sur les
**capteurs** : les capteurs virtuels d'une box eedomus (consignes, modes) se
pilotent comme des sorties. Une échelle définit : `unit` (optionnelle,
**recopiée** dans `devices.unit` par l'admin au moment du choix de l'échelle —
simple transfert, pas de liaison), plage `vmin`/`vmax`, `step` (cran du curseur **et**
format d'affichage : 10 → « 20 », 0.1 → « 19.5 »), `hide_slider` (boutons
seuls), `send_delay_s` (tempo d'auto-validation du curseur), `toggle_click`
(1 = clic court marche/arrêt + double-clic/appui long pour le réglage ;
0 = le clic ouvre directement le réglage — consignes de chauffage, modes) et
`stops` (JSON, 0 ou 2 à 20 valeurs `{value, label?, icon?}`, triées côté
serveur). Exemples : gradateur 0-100 %, consigne 12-25 °C par 0.5 avec boutons
12/18/19/20/21/23, mode radiateur 0-3 avec 4 boutons texte+icône.

- **Admin** : onglet « Paramètres » (CRUD `/api/scales`, admin) ; affectation
  dans l'onglet « Périphériques », colonne « Échelle ». Suppression d'une
  échelle → les périphériques repassent à NULL.
- **Migration** (`init_db()`) : colonne `scale_id` ajoutée, et — une seule
  fois, marqué par le réglage `default_scale_id` — création d'une échelle
  « 0 - 100 % » (boutons 0/25/50/75/100) affectée aux devices `dimmable=1`
  (avec unité `%` posée si vide, pour préserver l'affichage).
- **Échelles fournies par un connecteur** : `discover()` peut joindre une clé
  `scale` (spec complète, voir `base.py`) à un périphérique — l'import crée
  l'échelle si aucune de ce nom n'existe et l'affecte aux **nouveaux**
  périphériques. Une échelle existante n'est jamais modifiée ni dupliquée
  (retouches utilisateur conservées ; la supprimer pour qu'elle soit recréée
  depuis le connecteur). C'est le mécanisme des énumérations Yamaha
  (« Yamaha - Sleep / Entrée / Surround »).
- **Découverte** : `dimmable` reste l'heuristique (eedomus : `usage_name`
  contient volet/shutter/variateur/dimmer/store ; WES/HA : présence de
  `set_position_topic`/`brightness_command_topic`) — un **nouveau** périphérique
  détecté proportionnel reçoit l'échelle `default_scale_id` (et son unité si le
  connecteur n'en fournit pas) ; la re-découverte ne touche jamais `scale_id`
  et ne vide jamais une unité renseignée quand le connecteur n'en fournit pas.
- **Envoi** : inchangé, par `POST /api/devices/{id}/set`. eedomus :
  `periph.value` accepte le numérique (on/off → 100/0). WES/MQTT :
  `set_value()` route une valeur numérique vers `set_position_topic` (volet)
  ou `brightness_command_topic` (gradateur) si présents, sinon
  `command_topic` ; on/off restent sur `command_topic` avec
  `payload_on`/`payload_off`. Les connecteurs ne lisent pas l'échelle.
- **Lecture** : pour un cover HA sans `state_topic`, le connecteur utilise
  `position_topic` comme état (et s'abonne aux deux).
- **UI** (`app.js`) : `GET /api/devices` embarque l'objet `scale` (jamais la
  config du connecteur). Dialog `openScale` : curseur borné/cranté + boutons
  des `stops` (grille `.scale-btns`, jusqu'à 20). Sur la tuile : si la valeur
  courante correspond à un stop (au demi-cran près), son icône/texte remplace
  ceux du périphérique ; sinon, état partiel affiché par superposition icône
  off + icône on découpée par `clip-path: inset(calc(100% - var(--pct)) 0 0 0)`
  (classe `.stack`) — `--pct` = position normalisée sur la plage de l'échelle.

## Essai et export d'un périphérique (admin → colonne « Type »)

Livré le 27/07/2026. Dans le tableau des périphériques, le texte
« sortie »/« capteur » de la colonne Type est un lien : il ouvre une **mini
page de visualisation** du périphérique (`admin.js:openProbe`), pour vérifier
d'un coup l'affichage, le pilotage et les données.

- **Tuile 1** : exactement la tuile du visualiseur (`tile.js`) — ordres on/off,
  réglage sur échelle (curseur + boutons), format des valeurs et icônes s'y
  testent pour de vrai, avec les mêmes règles de clic.
- **Tuiles 2 et 3** (si historique) : liens `GET /api/devices/{id}/export?kind=`
  `detailed` (brut au pas de collecte) ou `summary` (min/moy/max par jour,
  archives comprises) → fichier **ODS**.
- **Tuile 4** (si historique) : `DELETE /api/devices/{id}/history` après
  `confirm()` — efface `measures` et `measures_daily`, jamais `last_value`.
- **Graphe** : le widget habituel, avec ses boutons de plage.
- **Diagnostic** (`GET /api/devices/{id}/diag`, admin) : une section commune
  (contrôleur, identifiant côté contrôleur, valeur courante, âge de la
  dernière lecture, surveillé, pilotable) plus, si le connecteur en propose
  une, sa propre section — le connecteur MQTT y met topic d'état, abonnement,
  nombre de messages, **âge du dernier message et sa charge utile brute**,
  template et valeur extraite, topic et charges utiles de commande. Le
  connecteur remplit cela via le point d'extension facultatif
  `Connector.diagnose(device)` (`base.py`) : `{title, rows: [(clé, valeur)],
  notes: [str]}`. **Ne jamais y faire figurer d'identifiant** — ce contenu
  part au navigateur. Un `diagnose()` qui échoue n'empêche pas la réponse.
- **Suivi pendant l'ouverture** : toutes les `PROBE_TICK_S` (5 s), la fenêtre
  relit le périphérique (`POST /api/devices/refresh`, sans historiser) puis
  reconstruit la tuile et le diagnostic — indispensable pour voir bouger une
  sortie actionnée depuis l'appareil lui-même (interrupteur mural, interface
  du WES). Le graphe n'est **pas** re-rendu (il perdrait zoom et plage). Le
  timer est arrêté par `dlg.onclose` ; `dialog()` remet `onclose` à `null`
  pour les autres fenêtres.

Points à connaître :

- `GET /api/devices/{id}/history-info` (admin) donne `{measures, daily,
  first_ts, last_ts}` : c'est lui qui décide « 1 tuile ou 4 » et alimente les
  volumes affichés. Un périphérique **dé-surveillé garde son historique** —
  ne pas conditionner ces tuiles à `monitored`.
- `openProbe` **relit `/api/devices` à l'ouverture** et fusionne le résultat
  par `Object.assign` (jamais un remplacement : les lignes du tableau tiennent
  des références sur ces objets). Sans cette relecture, changer l'échelle dans
  le tableau ne met à jour que `scale_id`, pas l'objet `scale` embarqué, et la
  tuile s'affichait en simple marche/arrêt.
- Le dialogue vit dans `#dlg` ; les tuiles ouvrent leur réglage dans
  **`#zoom-dlg`**, ajouté à `admin.html` pour l'occasion — deux `showModal()`
  imbriqués, le second par-dessus.
- Le tableau des **capteurs virtuels** n'a pas de colonne Type, donc pas encore
  ce lien.

### export_ods.py — ODS sans dépendance

Un `.ods` est un zip : `mimetype` (**stocké non compressé, en premier** — exigé
par la spécification), `META-INF/manifest.xml`, `content.xml`. Le XML est écrit
à la main : pas d'odfpy ni de pandas sur le Pi. Horodatages en **cellules date**
(heure locale, style d'affichage JJ/MM/AAAA HH:MM:SS) pour que les tableurs
trient et tracent correctement ; valeurs en cellules `float`.
`MAX_ROWS = 200 000` garde-fou mémoire : au-delà, seules les mesures les plus
récentes partent. La construction tourne dans `asyncio.to_thread` (l'unique
worker uvicorn ne doit pas bloquer sur une grosse base).

## Capteurs virtuels (formula.py + connectors/virtual.py)

Capteurs **calculés par formule**, créés dans l'admin (onglet Périphériques,
section « Capteurs virtuels »). Implémentation : un connecteur interne unique
`type='virtual'` (seedé par `init_db()`, marqué par le réglage
`virtual_connector_id`, masqué dans l'admin, non supprimable) ; chaque capteur
virtuel est une ligne `devices` ordinaire de ce connecteur (`kind='sensor'`,
`external_id='virt-<ms>'`), la formule vivant dans `meta["formula"]` — aucun
changement de schéma. Tout le reste (widgets, échelles, icônes, historique,
graphes, rollup) fonctionne donc sans cas particulier.

- **Langage** (`formula.py`) : constantes (point décimal), références
  `{Nom du capteur}` (casse/accents/espaces superflus ignorés — nom ambigu =
  erreur de validation), opérateurs `+ - * /`, parenthèses, fonctions
  `Deriver({c}, durée)` (dérivée par heure, durée 6min-24h — puissance kW
  depuis un compteur kWh) et `Min/Max/Moy({c}, plage)` (plage glissante ≤168h,
  `heure` = heure courante, `jour` = journée courante, `hier` = journée d'hier
  complète, heure locale).
  Séparateur d'arguments `,` ou `;`. Les fonctions lisent `measures` → elles
  exigent un capteur surveillé ; une référence simple lit `last_value`.
- **Évaluation** : `poll_once()` interroge le connecteur virtuel **en dernier**
  (ORDER BY dans poller.py) pour calculer sur les valeurs fraîches du cycle.
  Un `Resolver` par cycle (snapshot des devices) est partagé par toutes les
  formules ; les AST sont mis en cache par texte dans l'instance du connecteur.
  `/api/devices/refresh` recalcule aussi à la demande (widgets affichés).
- **NaN** : division par zéro, référence introuvable, historique insuffisant…
  → le connecteur renvoie la chaîne `"NaN"` ; le poller pose
  `last_value='NaN'` **sans historiser** → tuile « invalide » (app.js) et
  lever de crayon sur les graphes (charts.js casse le tracé quand l'écart
  entre points dépasse 1,5 × l'écart médian).
- **API** : `POST /api/devices/virtual` (création), `DELETE /api/devices/{id}`
  (capteurs virtuels seulement), `POST /api/formula/check` (validation pour
  l'éditeur — syntaxe + résolution des références), clé `formula` acceptée par
  `PUT /api/devices/{id}` (validée avant enregistrement).
- **UI** : tableau dédié sous le tableau des périphériques (mêmes colonnes
  qu'un capteur + Formule tronquée + bouton `…` → éditeur avec validation en
  direct, listes cliquables des fonctions et des capteurs, aide syntaxe).
- **Garde-fou** : `PUT /api/devices/{id}` refuse (400) de retirer la
  surveillance d'un capteur référencé par une fonction d'historique d'une
  formule (`formula.history_users`) — une référence simple n'exige rien.
- **Sans formule = état réglable à la main** : `set_value()` écrit
  `last_value` (on/off traduits en 1/0) et `poll()` rejoue la valeur courante
  → historisée à chaque cycle si surveillé. Avec formule, le pilotage est
  refusé et `PUT /api/devices/{id}` force `controllable=0` (case grisée dans
  l'admin) ; Échelle/Icônes/Unité restent actifs dans les deux cas
  (affichage).
- **`kind` suit la formule** (27/07/2026) : un périphérique virtuel **avec**
  formule est un `sensor` (« capteur » — valeur calculée), **sans** formule un
  `actuator` (« sortie » — état posé à la main). C'est une conséquence, jamais
  un champ éditable : `create_virtual_device` et `update_device` le
  recalculent, et `db.init_db()` réaligne les périphériques du connecteur
  virtuel à chaque démarrage (bases antérieures). Le type est **indépendant de
  « Pilotable »** : une sortie non pilotable est un cas normal (état affiché,
  non réglable depuis la tuile). La colonne « Type » du tableau des capteurs
  virtuels l'affiche, avec le lien vers la fenêtre d'essai et d'export ; sa
  largeur (61 px) a été financée en ramenant `.fcell { max-width }` de 300 à
  238 px — c'est cette borne, et non le `width` en % de l'en-tête, qui décide
  de la largeur de la colonne Formule (tableau inchangé : 1068 px).
- Corollaire côté tuile (`tile.js`) : le clic ouvre le graphe pour **tout
  périphérique non pilotable**, plus seulement pour `kind === "sensor"` — sans
  quoi la tuile d'une sortie virtuelle non pilotable devenait inerte.

## Collecte (poller.py)

`run_forever()` : boucle asyncio lancée au démarrage de l'app (`startup`).
À chaque cycle, pour chaque connecteur actif, `poll_once()` récupère les valeurs
des périphériques `monitored=1`, convertit en float et appelle
`db.store_measure` (les valeurs non numériques sont stockées comme `last_value`
texte, sans historique). L'intervalle (`poll_interval_s`, défaut 300, plancher
30) est relu **à chaque cycle** → un changement de réglage est pris en compte
sans redémarrage. `get_instance()` met en cache les instances de connecteurs et
les reconstruit si la config en base a changé.

En complément, `POST /api/devices/refresh` (main.py) lit **à la demande** la
valeur courante d'une liste de périphériques (même non surveillés) via
`inst.poll()`, met à jour `last_value`/`last_seen` mais **n'historise pas**.
Le visualiseur l'appelle pour les widgets état+valeur de la page affichée
uniquement (pas les graphes), à la cadence `live_refresh_s` configurée **par
contrôleur** (défaut 10 s, 0 = désactivé, plancher 5 s — exposée aux lecteurs
via `/api/devices`, jamais le reste de la config) ; suspendu si l'onglet est
masqué. Les « pas de réponse » sont journalisés en
erreur par le poller à sa cadence, en debug seulement dans les connecteurs
(sinon le rafraîchissement 10 s inonderait le journal).

## Connecteurs

Pour **ajouter un connecteur**, créer une classe héritant de
`connectors/base.py:Connector`, avec `type_name` et les méthodes `discover()`,
`poll(devices)`, `set_value(device, value)` (et `start()`/`stop()` si état
persistant, ex. MQTT). L'enregistrer dans `connectors/__init__.py:REGISTRY`.
L'UI admin le proposera automatiquement si on ajoute aussi ses champs de
config dans `static/js/admin.js:CONN_FIELDS` / `CONN_DEFAULTS`.

- **eedomus** : `poll()` tente `periph.list` (une requête) mais l'API **locale**
  n'y renvoie pas `last_value` (contrairement au cloud) → repli sur
  `periph.caract` périphérique par périphérique. Pas d'historique local côté
  box. Détection actionneur par `usage_name`. Attention : lectures sur
  `/api/get`, commandes sur **`/api/set`** (deux points d'entrée distincts) ;
  `set_value` traduit on/off en 100/0. La box répond en Latin-1 sans le
  déclarer (décodage manuel dans `_call`).
  Doc : https://doc.eedomus.com/en/index.php/API_eedomus
- **yamaha** : amplis AV Yamaha via l'API **YNC** (XML sur HTTP,
  `POST /YamahaRemoteControl/ctrl` — testé sur RX-V773). Liste fixe de 13
  périphériques : `system_power`/`main_power`/`zone2_power`/`enhancer`/
  `mute` (on/off — mute : « on » = son coupé), `main_volume` (0-100 %, échelle dédiée « Yamaha - Volume » de
  type consigne — `toggle_click=0`, le clic ouvre le réglage : un clic
  marche/arrêt enverrait le volume à 100 %), `sleep`/`input`/`surround`
  (énumérations livrées avec une
  échelle dédiée « Yamaha - … » boutons seuls, créée à l'import via la clé
  `scale` de `discover()` — l'utilisateur peut ensuite l'éditer, elle n'est
  jamais recréée tant qu'elle existe), `scene_1..4` (impulsions : « on »
  lance la scène, pas d'état relisible, poll retombe à 0). La liste des
  entrées (avec libellés personnalisés) est lue sur l'ampli
  (`Input_Sel_Item`, ordre stable → codes 1..N, Napster exclu, 20 max) ;
  les 19 programmes DSP sont codés en dur dans l'ordre du `desc.xml`.
  Volume converti % <-> dB sur la plage `vol_min_db`/`vol_max_db` de la
  config (défaut -80.5/+16.5, pas de 0.5 dB). Piloter volume/entrée/
  programme exige l'ampli allumé (RC=4 sinon).
- **wes_mqtt** : en réalité un client **Home Assistant MQTT Discovery**
  générique. Il s'abonne à `homeassistant/+/+/config` (et `+/+/+/config`),
  mémorise les entités et leurs `state_topic`, met en cache les derniers
  payloads, et `poll()` lit ce cache. `set_value` publie sur `command_topic`.
  Doc : https://www.cartelectronic-blog.fr/wes-et-homeassistant-en-mqtt/
  (guide utilisateur : il ne documente **pas** le format des annonces — pour
  savoir ce que publie vraiment un appareil, capturer le broker :
  `mosquitto_sub -h 127.0.0.1 -u … -P … -t '#' -v -W 30`).

  Quatre points appris sur les E/S TOR du WES (SW1-24 et relais), 27/07/2026 :

  - **Les annonces HA Discovery sont souvent abrégées** : `pl_on`/`pl_off`,
    `stat_on`/`stat_off`, `dev`… Ne lire que la forme longue faisait retomber
    `payload_on` sur son défaut `"ON"` alors que l'appareil attendait `"1"` :
    commande publiée sur le bon topic, **ignorée en silence** — symptôme « les
    relais ne se pilotent pas ». Toute clé lue doit accepter les deux formes
    (helper `g()` dans `_handle_config`).
  - **`_extract` gère plus que `{{ value_json.X }}`** : crochets simples ou
    doubles, chemins imbriqués, index de liste, filtres `| int`. Une forme non
    gérée renvoyait `None`, donc « pas de réponse » sans autre explication.
  - **`_live_meta()`** : la dernière annonce reçue prime sur la copie en base
    (figée à la découverte). Une correction de lecture des annonces s'applique
    ainsi sans re-découverte.
  - **Trois pannes de lecture à distinguer** (ce que fait `diagnose()`, et que
    le journal en mode verbeux dit maintenant explicitement) : l'appareil
    n'annonce **pas de topic d'état** ; le topic est annoncé mais **rien n'a
    été publié depuis la connexion** (état non *retained*, publié seulement au
    changement) ; le **template ne trouve rien** dans la charge utile. Le
    connecteur horodate chaque message reçu (`_times`, `_counts`) pour cela.

## API (main.py)

Toutes les routes sont sous `/api`. Auth par dépendance :
`auth.require_user` (connecté) ou `auth.require_admin`. Points notables :
`POST /api/devices/{id}/set` est accessible aux lecteurs **si** le périphérique
est `controllable`. `GET /api/connectors/{id}/discover` fait la découverte +
l'upsert des devices. Uploads (`/api/icons/upload`, `/api/backgrounds/upload`)
validés (nom `[A-Za-z0-9._-]`, extension, taille).

`SETTABLE` (main.py) liste les clés de réglage modifiables via l'API — l'étendre
si on ajoute un réglage.

Note : un widget « page web externe » encapsulée en iframe (avec relais
same-origin `/ext/` pour le contenu mixte http/HTTPS) a été tenté puis
**retiré** (juillet 2026, cf. historique git) : les interfaces embarquées des
appareils du LAN sont trop dynamiques pour survivre à un relais sans
WebSocket. Pour intégrer un appareil, préférer un connecteur dédié (cf. le
connecteur yamaha, né de ce constat).

## Frontend

Aucune étape de build. `app.js` et `admin.js` sont des IIFE vanilla.
`charts.js` expose `window.renderChart(container, data, opts)`.

`tile.js` expose **`window.DomoTile`** : tuile de périphérique (`card`),
dialogue de réglage sur échelle (`openScale`), graphe et ses boutons de plage
(`chart`, `openChart`), formats (`fmtNum`, `fmtScale`, `isOn`). Extrait
d'`app.js` le 27/07/2026 pour être partagé avec l'admin (dialogue « Tester et
exporter ») : une seule implémentation des règles d'interaction fines (clic
court / double-clic / appui long, tempo d'auto-validation, état partiel en
`clip-path`). Chaque écran lui passe son contexte par
`DomoTile.configure({api, toast, dlg, body, onAck, mobile})` — l'`api` du
visualiseur renvoie au login sur 401, celle de l'admin non — puis
`setDisplay()` (chiffres significatifs, séparateurs) et `setRanges()` (durées
des graphes) au chargement des réglages. `window.fmtNum` reste posé par
`tile.js` pour `charts.js` : **charger `tile.js` avant `app.js`/`admin.js`**.

Le thème
(couleurs, mono) est centralisé dans les variables CSS de `:root` (app.css).
Responsive : bascule mobile/desktop au seuil **700 px** (constante répétée dans
`app.js:isMobile()` et les media queries CSS — garder les deux cohérents).
Le « double rendu » d'une page filtre les widgets par `layout`
(`both`/`mobile`/`desktop`).

### Conventions d'interface (issues des retours utilisateur — à préserver)

- **Tuiles uniformes** : toutes les tuiles d'une page (périphériques, liens de
  page) ont la même hauteur fixe (`.grid > .card`, 122 px) ; nom et valeur en
  `nowrap` + ellipse ; « sans réponse » est un badge en surimpression (absolu,
  haut-droite) pour ne pas modifier la hauteur. Les textes libres (`.wide`) et
  les graphes gardent leur hauteur propre. Mobile : **3 colonnes
  fixes** (`repeat(3, 1fr)`) — 4 colonnes étaient trop serrées sur iPhone 14
  en portrait, et 3 colonnes restent garanties dès 320 px de large.
- **En-tête des graphes** : deux cellules côte à côte — titre + valeur à
  gauche (1 ou 2 lignes), boutons de plage dans `.chart-head .ranges` à droite
  (alignés à droite, repliables sur 2 lignes) : les boutons ne passent jamais
  sous le titre, pour ne pas réduire la hauteur du graphe sur mobile.
- **Zoom des graphes** (`charts.js`) : purement côté client — une *vue*
  `{t0,t1,vmin,vmax}` sur les points déjà chargés, aucune requête refaite.
  Souris : clic maintenu puis glissé > 6 px écran → rectangle de sélection ;
  chaque axe n'est zoomé que si le glissé le dépasse **sur cet axe** (glissé
  horizontal = temps seul, l'axe non retenu s'affiche pleine hauteur/largeur
  dans le rectangle). Tactile : pincement à deux doigts, facteur calculé
  séparément sur l'écartement en X et en Y (seuil `PINCH_MIN`), borné à la vue
  complète (on ne dézoome pas au-delà des données ; pincement refermé =
  retour à l'origine). Le geste **décale aussi** la vue : la donnée visée au
  départ par le milieu des doigts reste sous le milieu **courant** des doigts
  — sans quoi, deux doigts partant du même côté du cadre (où écarter revient
  autant à translater) donnent un zoom qui ne suit pas la main. Corollaire
  utile : à deux doigts sans écartement, on fait glisser la fenêtre (un axe
  sous `PINCH_MIN` ne change pas d'échelle mais se décale quand même). Les vues s'empilent → boutons « Zoom précédent » /
  « Zoom initial » dans la légende (`.zoom-ctl`, masqués sans zoom).
  Les courbes et les pastilles du curseur sont découpées par un `clipPath`
  sur le cadre ; l'étiquette du curseur reste lisible hors cadre.
  `renderChart(container, data, {view, onZoom})` : `view` restaure un zoom,
  `onZoom` le remonte à `app.js`, qui le porte sur `box._zoom` pour le
  reposer après la MAJ « soft » de 60 s (comme le bouton de plage actif) —
  changer de plage l'annule.
- **Niveau visuel de consigne** : tuile pilotable dont l'échelle affiche la
  barre → classe `.lvl` + variable `--lvl` (position min→max en %) : la tuile
  s'éclaircit depuis le bas (dégradé blanc ~10 % d'opacité). Rien pour les
  échelles « boutons seuls ».
- **Ordre d'affichage** : tuiles de sous-pages et widgets partagent la même
  numérotation (fusion triée dans `app.js:renderPage` ; à ordre égal, la
  sous-page passe en premier). Le « + Ajouter un widget » pré-remplit
  « ordre max + 1 » calculé **sur les widgets seuls** : ne pas y intégrer les
  sous-pages — l'utilisateur place ses dossiers très en début (-20) ou très en
  fin (+20) et préfère une collision d'ordre (sans gravité) à de gros sauts de
  numérotation.
- **En-têtes collants** (admin, tableau des périphériques) : les deux lignes
  d'en-tête (tri + filtres) sont `position: sticky` avec des offsets en dur
  dans app.css (84 px / 113 px), calés sur le bandeau (46 px) + les onglets,
  avec 1-3 px de recouvrement pour éviter tout jour — à ajuster si la hauteur
  du bandeau change.
- **Mémorisations `localStorage` (admin)** : `domopi_dev_filters` = filtres de
  colonnes du tableau des périphériques, restaurés à l'ouverture (la recherche
  globale, elle, repart volontairement vide) ; `domopi_last_icon` = dernière
  icône choisie — le dialogue de choix propose un bouton « Reprendre la
  dernière icône » en bas à droite, avec `autofocus` (Entrée valide), et
  l'icône est surlignée/centrée dans la galerie (usage : équiper une série de
  périphériques de la même icône).
- **Capteurs virtuels pilotables** : « Pilotable » et « Échelle » sont offerts
  pour tous les périphériques, capteurs compris (consignes/modes eedomus) ;
  seul `dimmable` reste réservé aux sorties côté serveur. Sur la tuile, les
  icônes on/off basculent pour tout périphérique **pilotable** (actionneur ou
  capteur) ; un capteur non pilotable garde son icône fixe (`icon_on` sinon
  `icon_off`).

## Points d'attention Pi 2/3

- Garder **un seul worker** uvicorn (RAM + accès SQLite concurrent).
- SQLite en mode **WAL** (déjà activé). Éviter les requêtes non bornées sur
  `measures` : toujours filtrer par device + fenêtre temporelle.
- Ne pas introduire de dépendance lourde (numpy, pandas, matplotlib…) : les
  graphes sont volontairement rendus en SVG côté client.
- Le premier `apt-get`/`pip install` de l'installeur est long sur Pi 2 — c'est
  normal, ne pas le paralléliser à outrance.

## Idées d'évolution

- Tests pytest + CI.
- Migration/versionnage de schéma (actuellement `CREATE TABLE IF NOT EXISTS`,
  pas de migrations incrémentales).
- ~~Support Let's Encrypt~~ — fait le 27/07/2026 : `tools/setup-https.sh`,
  posé par `install.sh` en `/usr/local/sbin/domopi-https`. Trois pièges qui ont
  motivé sa forme actuelle, à ne pas défaire :
  - le bloc `location ^~ /.well-known/acme-challenge/` de `nginx-domopi.conf`
    doit rester **avant** la redirection, et celle-ci dans `location /` : un
    `return` de niveau serveur s'exécute avant le choix de la location et
    avalerait le jeton (ACME suivrait vers 443, où tout part dans uvicorn → 404) ;
  - le certificat est branché par **liens** `/etc/domopi/tls/domopi.{crt,key}` →
    `/etc/letsencrypt/live/<domaine>/` : `install.sh` réécrit la conf nginx à
    chaque passage, une retouche de `ssl_certificate` serait perdue. D'où aussi
    le garde-fou « lien cassé → retour à l'auto-signé » dans `install.sh`
    (sinon `openssl` écrirait *à travers* le lien) ;
  - le rechargement de nginx après renouvellement passe par le crochet
    **global** `/etc/letsencrypt/renewal-hooks/deploy/` et non par un
    `--deploy-hook` attaché au certificat : le script ignore l'émission quand
    un certificat valide existe déjà (émis à la main, par exemple), et le
    crochet doit exister dans ce cas aussi.
- Le renouvellement exige que le **port 80 reste redirigé** vers le Pi (HTTP-01 ;
  DNS-01 est hors de portée sur un domaine fourni par le FAI).
- Connecteurs supplémentaires (Zigbee2MQTT réutiliserait le client HA-Discovery).
- Export CSV des séries, seuils/alertes sur mesures.
