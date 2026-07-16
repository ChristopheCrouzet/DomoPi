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
│   ├── icons/               36 icônes SVG générées + uploads utilisateur
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
20 jours de données simulées. Les échelles de pilotage ont été validées par des
scripts smoke jetables (`fastapi.testclient` + base SQLite temporaire recréant
l'état legacy : migrations, CRUD `/api/scales`, validations 400, affectations,
capteurs pilotables) — c'est le bon modèle pour le premier chantier : en faire
une vraie suite pytest.

## Base de données (db.py)

Tables : `users`, `settings`, `connectors`, `scales` (échelles de pilotage),
`devices`, `measures` (brut), `measures_daily` (archives), `pages`, `widgets`,
`journal`. `measures` et
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
  `POST /YamahaRemoteControl/ctrl` — testé sur RX-V773). Liste fixe de 12
  périphériques : `system_power`/`main_power`/`zone2_power`/`enhancer`
  (on/off), `main_volume` (0-100 % marqué `dimmable` → échelle 0-100 %
  par défaut), `sleep`/`input`/`surround` (énumérations livrées avec une
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

## API (main.py)

Toutes les routes sont sous `/api`. Auth par dépendance :
`auth.require_user` (connecté) ou `auth.require_admin`. Points notables :
`POST /api/devices/{id}/set` est accessible aux lecteurs **si** le périphérique
est `controllable`. `GET /api/connectors/{id}/discover` fait la découverte +
l'upsert des devices. Uploads (`/api/icons/upload`, `/api/backgrounds/upload`)
validés (nom `[A-Za-z0-9._-]`, extension, taille).

`SETTABLE` (main.py) liste les clés de réglage modifiables via l'API — l'étendre
si on ajoute un réglage.

`/ext/{widget_id}/{chemin}` (hors `/api`, connecté) : relais same-origin des
widgets « page web externe » (`wtype='weblink'`, `options.url`, tuile 🌐 qui
ouvre la cible dans une iframe sous le bandeau). Sert à encapsuler dans une
iframe une cible **http** du LAN alors que DomoPi est servi en HTTPS (sinon
contenu mixte bloqué par le navigateur). Restreint à l'origine de l'URL du
widget ; GET/POST seulement, pas de WebSocket, cookies de la cible non
relayés — les pages très dynamiques (ex. interface d'un ampli Yamaha) ne
passent pas : préférer alors un connecteur dédié. Authentification HTTP Basic
de la cible : statique (`options.auth_user`/`auth_pass`, saisis dans l'admin —
**jamais renvoyés aux lecteurs** par `GET /widgets`, remplacés par le drapeau
`has_auth`) ou dynamique (Authorization du navigateur transmis, défi 401
`WWW-Authenticate` relayé → boîte de connexion native). Le visualiseur passe
par le relais si les protocoles page / cible divergent **ou** si la cible a
une authentification ; sinon l'iframe pointe directement sur l'URL.

## Frontend

Aucune étape de build. `app.js` et `admin.js` sont des IIFE vanilla.
`charts.js` expose `window.renderChart(container, data, opts)`. Le thème
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
- Support Let's Encrypt directement dans l'installeur (aujourd'hui manuel,
  documenté dans le README).
- Connecteurs supplémentaires (Zigbee2MQTT réutiliserait le client HA-Discovery).
- Export CSV des séries, seuils/alertes sur mesures.
