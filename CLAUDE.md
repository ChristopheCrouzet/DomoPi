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

Contrainte directrice : ça doit tourner sur un Pi 2 (CPU ARM lent, ~1 Go RAM).
D'où : pas de dépendances lourdes, SQLite plutôt qu'un SGBD, agrégation des
mesures pour limiter le volume, un seul worker, graphes rendus en SVG côté
client sans librairie.

## Arborescence du dépôt

Racine du projet sur cette machine : `M:\Domotique\Domopi\domopi setup\`
(cible de déploiement : un Raspberry Pi sous Raspbian).

```
domopi setup/
├── domopi/                  paquet Python (backend)
│   ├── main.py              app FastAPI : toutes les routes /api/*, sert le statique
│   ├── db.py                schéma SQLite, réglages, query_series, rollup/purge
│   ├── auth.py              PBKDF2 + cookie de session signé HMAC, rôles
│   ├── journal.py           journal applicatif avec niveaux de verbosité
│   ├── poller.py            boucle asyncio de collecte + rollup quotidien + purge journal hebdo
│   └── connectors/
│       ├── base.py          classe abstraite Connector
│       ├── eedomus.py       API locale eedomus (httpx)
│       ├── wes_mqtt.py      client MQTT HA-Discovery (paho-mqtt)
│       └── __init__.py      REGISTRY {type: classe}
├── static/                  frontend
│   ├── index.html           visualiseur (SPA)
│   ├── admin.html           interface d'administration
│   ├── css/app.css          thème unique « tableau électrique »
│   ├── js/app.js            logique visualiseur
│   ├── js/charts.js         renderChart() — SVG min/moy/max, sans dépendance
│   ├── js/admin.js          logique admin
│   ├── icons/               32 icônes SVG livrées + uploads utilisateur
│   └── backgrounds/         fonds de page (uploads)
├── tools/
│   ├── make_icons.py        générateur des icônes SVG intégrées
│   ├── deploy.ps1           déploiement dev vers le Pi de test (voir plus bas)
│   └── deploy-remote.sh     partie exécutée sur le Pi par deploy.ps1
├── deploy/                  domopi.service, nginx-domopi.conf, proxy-params,
│                            mosquitto-domopi.conf
├── install.sh              installeur idempotent (sudo bash install.sh)
├── requirements.txt
├── README.md               doc utilisateur
└── CLAUDE.md               ce fichier
```

## Chemins en production (posés par install.sh)

| Rôle | Chemin |
|------|--------|
| Code | `/opt/domopi` (copie de `domopi/` + `static/` + `requirements.txt` + venv) |
| Données | `/var/lib/domopi/domopi.db`, `/var/lib/domopi/secret.key` |
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
# http://127.0.0.1:8000  (le cookie est Secure : tester en HTTPS ou assouplir
# temporairement le flag secure dans main.py:login pour du HTTP local)
```

Équivalent Windows (PowerShell), depuis `domopi setup\` :

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DOMOPI_DB = "$env:TEMP\dev.db"; $env:DOMOPI_SECRET = "$env:TEMP\dev.key"
$env:DOMOPI_STATIC = "$PWD\static"; $env:DOMOPI_ADMIN_PASSWORD = "devpass123"
uvicorn domopi.main:app --reload --port 8000
```

## Déploiement de test sur le Pi (dev)

Un Raspberry Pi de test (`PI-SERVER`, résolvable sur le LAN) héberge une
installation DomoPi standard. Pour y pousser le code local et redémarrer :

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

Ce circuit est réservé au développement ; l'installation propre reste
`sudo bash install.sh` (à remettre à jour en fin de projet).

## Tests

Il n'y a pas encore de suite de tests automatisés. Les vérifications faites à la
génération : `bash -n install.sh`, imports des modules, smoke test des routes
(login, settings, connectors, pages, icons), et validation de `query_series` sur
20 jours de données simulées. Un bon premier chantier serait d'en faire des
tests pytest (httpx.AsyncClient + base SQLite temporaire).

## Base de données (db.py)

Tables : `users`, `settings`, `connectors`, `devices`, `measures` (brut),
`measures_daily` (archives), `pages`, `widgets`, `journal`. `measures` et
`measures_daily` sont en `WITHOUT ROWID` (clé composite device+temps).

### Logique d'agrégation — `query_series(device_id, t_from, t_to)`

C'est le cœur métier, à préserver. Selon l'amplitude de la fenêtre demandée :

- **≤ 4 jours** → points bruts, `mode="raw"` (une seule courbe côté client).
- **> 4 jours** → agrégation `GROUP BY` au pas **horaire**, `mode="hourly"`,
  chaque point = `{t, min, avg, max}` (trois courbes).
- **≥ 15 jours** → agrégation au pas **journalier**, `mode="daily"`, complétée
  par les archives `measures_daily` pour les données au-delà de la rétention
  brute.

Les seuils (4 j, 15 j) sont en dur dans `query_series`. `charts.js` s'adapte au
champ `mode` renvoyé (courbe simple vs bande min-max + 3 lignes).

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

## Icônes (static/icons/ + tools/make_icons.py)

Les 32 icônes SVG intégrées sont **générées** par `tools/make_icons.py`
(géométrie calculée, centrée sur (24,24), viewBox 48×48, traits principaux 2.2
et détails 1.5, palette ambre/gris/blanc du thème). Pour modifier ou ajouter
une icône : éditer ce script puis le relancer (`python3 tools/make_icons.py`),
ne pas retoucher les SVG à la main. Contrôle visuel recommandé : rasteriser en
planche contact avec cairosvg + Pillow avant livraison (vérifier symétrie,
chevauchements, lisibilité à 42 px). Les icônes ajoutées par l'utilisateur via
l'upload ne sont pas concernées et sont préservées par l'installeur (`cp -n`).

## Pilotage 0-100 % (attribut `dimmable`)

`devices.dimmable` (ajouté par migration dans `init_db()`) marque les sorties
pilotables en pourcentage : gradateurs de lampes, ouverture partielle de volets.

- **Découverte** : pré-coché par heuristique — eedomus si `usage_name` contient
  volet/shutter/variateur/dimmer/store ; WES/HA si la config discovery expose
  `set_position_topic` ou `brightness_command_topic`. Modifiable dans l'admin
  (colonne « 0-100 % », sorties uniquement, forcé à 0 côté serveur pour un
  capteur).
- **Envoi** : la valeur transite par `POST /api/devices/{id}/set` comme les
  ordres on/off. eedomus : `periph.value` accepte directement 0-100.
  WES/MQTT : `set_value()` route une valeur numérique vers
  `set_position_topic` (volet) ou `brightness_command_topic` (gradateur) si
  présents, sinon `command_topic` ; on/off restent sur `command_topic` avec
  `payload_on`/`payload_off`.
- **Lecture** : pour un cover HA sans `state_topic`, le connecteur utilise
  `position_topic` comme état (et s'abonne aux deux).
- **UI** (`app.js`) : carte d'un périphérique `dimmable` -> clic ouvre un
  curseur 0-100 % (dialog `openDimmer`) avec raccourcis 0 % / 100 %. Icône :
  si 0 < valeur < 100, superposition icône off + icône on découpée par
  `clip-path: inset(calc(100% - var(--pct)) 0 0 0)` (classe `.stack`,
  css dans app.css) — l'icône « on » se révèle depuis le bas à hauteur du
  pourcentage. La valeur s'affiche en `%`.

## Collecte (poller.py)

`run_forever()` : boucle asyncio lancée au démarrage de l'app (`startup`).
À chaque cycle, pour chaque connecteur actif, `poll_once()` récupère les valeurs
des périphériques `monitored=1`, convertit en float et appelle
`db.store_measure` (les valeurs non numériques sont stockées comme `last_value`
texte, sans historique). L'intervalle (`poll_interval_s`, défaut 300, plancher
30) est relu **à chaque cycle** → un changement de réglage est pris en compte
sans redémarrage. `get_instance()` met en cache les instances de connecteurs et
les reconstruit si la config en base a changé.

## Connecteurs

Pour **ajouter un connecteur**, créer une classe héritant de
`connectors/base.py:Connector`, avec `type_name` et les méthodes `discover()`,
`poll(devices)`, `set_value(device, value)` (et `start()`/`stop()` si état
persistant, ex. MQTT). L'enregistrer dans `connectors/__init__.py:REGISTRY`.
L'UI admin le proposera automatiquement si on ajoute aussi ses champs de
config dans `static/js/admin.js:CONN_FIELDS` / `CONN_DEFAULTS`.

- **eedomus** : un seul `periph.list` par cycle (contient `last_value` de tous
  les périphériques → économe). Pas d'historique local côté box. Détection
  actionneur par `usage_name`. Doc : https://doc.eedomus.com/en/index.php/API_eedomus
- **wes_mqtt** : en réalité un client **Home Assistant MQTT Discovery**
  générique. Il s'abonne à `homeassistant/+/+/config` (et `+/+/+/config`),
  mémorise les entités et leurs `state_topic`, met en cache les derniers
  payloads, et `poll()` lit ce cache. `set_value` publie sur `command_topic`.
  Doc : https://www.cartelectronic-blog.fr/wes-et-homeassistant-en-mqtt/

## API (main.py)

Toutes les routes sont sous `/api`. Auth par dépendance :
`auth.require_user` (connecté) ou `auth.require_admin`. Points notables :
`POST /api/devices/{id}/set` est accessible aux lecteurs **si** le périphérique
est `controllable`. `GET /api/connectors/{id}/discover` fait la découverte +
l'upsert des devices. Uploads (`/api/icons/upload`, `/api/backgrounds/upload`)
validés (nom `[A-Za-z0-9._-]`, extension, taille).

`SETTABLE` (main.py) liste les clés de réglage modifiables via l'API — l'étendre
si on ajoute un réglage.

## Frontend

Aucune étape de build. `app.js` et `admin.js` sont des IIFE vanilla.
`charts.js` expose `window.renderChart(container, data, opts)`. Le thème
(couleurs, mono) est centralisé dans les variables CSS de `:root` (app.css).
Responsive : bascule mobile/desktop au seuil **700 px** (constante répétée dans
`app.js:isMobile()` et les media queries CSS — garder les deux cohérents).
Le « double rendu » d'une page filtre les widgets par `layout`
(`both`/`mobile`/`desktop`).

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
- Support Let's Encrypt directement dans l'installeur (aujourd'hui manuel,
  documenté dans le README).
- Connecteurs supplémentaires (Zigbee2MQTT réutiliserait le client HA-Discovery).
- Export CSV des séries, seuils/alertes sur mesures.
