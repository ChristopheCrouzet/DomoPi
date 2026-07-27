# DomoPi

Mini-serveur de **supervision domotique** pour Raspberry Pi 2/3, à installer sur
une Raspbian fraîchement flashée. DomoPi interroge périodiquement vos
périphériques domotiques, historise leurs valeurs, trace des courbes et permet
de piloter les sorties autorisées, le tout derrière une interface web
responsive (smartphone / PC) exposable sur Internet en HTTPS.

Les grandeurs suivies sont quelconques — température, précipitations,
luminosité, pression, humidité, consommation électrique, états de sorties… —
tout périphérique numérique remonté par un connecteur est historisé et tracé
de la même façon (les courbes min/moy/max ont un sens pour n'importe quelle
grandeur physique).

Connecteurs fournis :

- **eedomus** via l'API locale de la box (`http://<ip>/api/`)
- **WES** (Cartelectronic) via **MQTT** au format Home Assistant Discovery
  (firmware WES ≥ 0.9 bêta 05)

---

## Installation

Sur le Raspberry Pi (Raspbian Lite ou Desktop), copiez le dossier du projet puis :

```bash
sudo bash install.sh
```

L'installeur s'occupe de tout : nginx (HTTPS auto-signé), Mosquitto (broker MQTT
local), Python + l'application, le service systemd et la base SQLite. Il vous
demande un identifiant/mot de passe administrateur et des identifiants MQTT.

À la fin, l'interface est disponible sur **https://<ip-du-pi>/**
(le navigateur affiche un avertissement de sécurité, normal avec un certificat
auto-signé — voir plus bas pour Let's Encrypt).

Le script est **relançable** sans risque : il préserve la configuration, les
identifiants, le certificat et les icônes/fonds déjà ajoutés.

### Prérequis matériels/réseau

- Raspberry Pi 2 ou 3 (ou plus récent), carte SD 8 Go minimum.
- Pour l'exposition Internet : une redirection de port sur votre box vers le Pi,
  **uniquement le port 443**. Ne redirigez jamais le port 1883 (MQTT) ni le 8000.

---

## Configuration des contrôleurs

Tout se fait dans **Paramètres** (bandeau supérieur, compte admin).

### eedomus (API locale)

1. Sur le portail eedomus : *Mon compte → Paramètres → API*, récupérez
   `api_user` et `api_secret`, et activez l'API locale.
2. Dans DomoPi : *Paramètres → Contrôleurs → Ajouter une box eedomus*.
   Renseignez l'IP locale de la box, `api_user`, `api_secret`.
3. Cliquez **Découvrir les périphériques**, puis cochez ceux à surveiller.

> Remarque : l'API locale eedomus ne fournit pas l'historique
> (`periph.history` est réservé au cloud). DomoPi construit donc son **propre
> historique** par échantillonnage au pas de collecte (5 min par défaut).

### WES Cartelectronic (MQTT)

1. Firmware WES **≥ 0.9 bêta 05** requis.
2. Dans le WES : activez MQTT, pointez-le vers le broker du Pi
   (`<ip-du-pi>:1883`) avec les identifiants MQTT saisis à l'installation.
   Le WES publie alors ses entités au format Home Assistant Discovery.
3. Dans DomoPi : *Paramètres → Contrôleurs → Ajouter un WES (MQTT)*.
   En local, laissez `host = 127.0.0.1`, `port = 1883`, préfixe
   `homeassistant`, et renseignez les identifiants MQTT.
4. Laissez tourner quelques instants (le temps que le WES publie), puis
   **Découvrir les périphériques**.

---

## Utilisation

Dans l'administration, **tout est enregistré à la volée** : une case ou une
liste déroulante part au clic, un champ de saisie dès que vous en sortez (un
message « Enregistré » le confirme). Il n'y a aucun bouton « Enregistrer ». Une
valeur refusée par le serveur est signalée et le champ revient à ce qui est
réellement enregistré.

- **Périphériques** : cochez ceux à historiser. Pour les sorties (actionneurs),
  cochez « pilotable » pour autoriser leur commande depuis les pages.
  Associez à chaque périphérique une icône « état actif » et « état inactif ».
- **Essayer un périphérique et récupérer ses données** : dans le tableau des
  périphériques, cliquez sur le mot **« sortie »** ou **« capteur »** de la
  colonne *Type*. Une fenêtre s'ouvre, composée comme une page de
  visualisation :
  - la **tuile telle que la verront vos lecteurs** — de quoi tester un ordre
    marche/arrêt, un gradateur ou une consigne et vérifier le format affiché ;
  - **« Télécharger les données régulières »** : toutes les mesures au pas de
    collecte, en fichier **ODS** (LibreOffice Calc, Excel) ;
  - **« Télécharger les données synthétiques »** : un min / moyenne / max par
    jour, archives comprises — donc bien au-delà de la rétention du brut ;
  - **« Effacer l'historique »** : supprime les mesures de ce périphérique
    (confirmation demandée) ; la valeur courante et les widgets sont conservés ;
  - le **graphe** habituel, avec ses boutons de période.

  Les trois dernières tuiles n'apparaissent que si des données ont été
  enregistrées.
- **Pages** : arborescentes, plusieurs racines possibles. Chaque page a un fond
  paramétrable (couleur ou image), et une option « double rendu » pour définir
  des widgets différents sur smartphone et sur PC. On y pose des widgets :
  périphérique (icône + valeur), graphe, lien vers une autre page, ou texte.
- **Graphes** : boutons de période configurables (par défaut 24 h, 4 j, 15 j,
  30 j, 90 j et 6 mois) dans Paramètres → onglet « Paramètres » → **« Paramétrage
  des courbes »** : pour chaque durée, libellé du bouton et affichage au choix —
  **« Toute la courbe »** (chaque mesure au pas de collecte, limité à la
  rétention du brut) ou **« Min / Moy / Max »** (trois courbes, pas horaire
  sous 15 jours puis journalier, en puisant dans les archives au-delà de la
  rétention du brut).
- **Zoom sur un graphe** : à la souris, un clic maintenu puis glissé dessine un
  rectangle de zoom (un axe n'est retenu que si le glissé dépasse ~6 px dessus —
  un glissé horizontal zoome donc le temps seul) ; au doigt, deux doigts qu'on
  écarte zooment, qu'on rapproche dézooment (axe par axe, selon l'écartement
  en largeur et en hauteur), et la courbe suit les doigts — deux doigts
  déplacés sans les écarter font glisser la fenêtre. Deux boutons
  apparaissent alors sous le graphe :
  **« Zoom précédent »** (échelle d'avant la dernière action) et **« Zoom
  initial »** (échelle d'origine). Le zoom ne fait que changer l'échelle des
  points déjà affichés — changer de période le remet à zéro.
- **Journal** : bouton dans le bandeau. Verbosité réglable dans Paramètres :
  *verbeux* (chaque changement), *moyen* (actions + warnings + erreurs),
  *erreurs* (absences de réponse et erreurs seules). Purge automatique
  **hebdomadaire** au-delà de la rétention configurée.
- **Pilotage** : clic sur une sortie pilotable = bascule marche/arrêt. Si une
  **échelle** est affectée à la sortie (gradateur de lampe, ouverture partielle
  de volet, consigne de chauffage, mode de radiateur…), un double-clic ou un
  appui long ouvre le réglage : curseur borné et cranté selon l'échelle, plus
  une série de boutons de valeurs prédéfinies (avec texte et icône optionnels).
  Les échelles se définissent dans Paramètres → onglet « Paramètres » : unité
  optionnelle (recopiée sur le périphérique au moment du choix de l'échelle),
  plage min/max, résolution, tempo d'auto-validation du curseur, barre
  masquable, et 2 à 20 valeurs. Les capteurs virtuels d'une box (consignes,
  modes eedomus…) peuvent aussi être marqués pilotables et recevoir une
  échelle. Si la valeur courante correspond à une valeur de la série,
  son icône et son texte s'affichent sur la tuile ; sinon, pour un état
  partiel, l'icône révèle visuellement le niveau (icône « on » découpée à
  hauteur de la position sur l'échelle, superposée à l'icône « off »). Une
  échelle peut aussi remplacer la bascule marche/arrêt du clic court par
  l'ouverture directe du réglage (utile pour les consignes).
- **Capteurs virtuels** : capteurs **calculés par formule** à partir des autres
  capteurs (onglet Périphériques, section « Capteurs virtuels »). Une formule
  combine constantes, opérateurs `+ - * /`, parenthèses, références
  `{Nom du capteur}` et fonctions : `Deriver({compteur}, 1h)` (dérivée par
  heure — ex. puissance kW depuis un compteur d'énergie kWh, durée de 6min à
  24h) et `Min` / `Max` / `Moy` (`{capteur}`, plage glissante en h/min, ou
  `heure` / `jour` courants, ou `hier` — journée d'hier complète). Exemple :
  `Deriver({Compteur EDF}, 1h) - {Départ Chauffage} - {Départ Cumulus}`.
  L'éditeur (bouton `…`) valide la formule en direct et liste fonctions et
  capteurs insérables d'un clic. La valeur est recalculée et historisée à
  chaque cycle de collecte (graphes compris) ; un calcul impossible (division
  par zéro…) affiche *invalide* sur la tuile et laisse un trou dans le graphe.
  Un élément virtuel **sans formule** est un état **réglable à la main** :
  marqué pilotable (avec échelle, icônes et unité au besoin), il se règle
  depuis sa tuile comme une sortie et sa valeur est historisée s'il est
  surveillé — pratique pour une consigne ou un mode purement logiciel,
  utilisable ensuite dans les formules d'autres capteurs virtuels. La colonne
  **Type** suit d'ailleurs la formule : **sortie** tant qu'il n'y en a pas,
  **capteur** dès qu'une formule est posée. Une sortie peut rester non
  pilotable (simple état affiché) ; et comme dans le tableau des
  périphériques, ce mot est un lien vers la fenêtre d'essai et d'export.
- **Icônes** : l'onglet « Icônes et fonds de page » accepte vos propres
  fichiers SVG/PNG, et propose un bouton « ✨ **Générer par IA** »
  (administrateur) : décrivez l'icône souhaitée, prévisualisez les
  propositions (dessinées dans le style du jeu intégré), affinez par
  retouches successives — la conversation continue tant que le dialogue est
  ouvert — puis validez pour les ajouter à la galerie. Nécessite une clé API
  Anthropic dans `/etc/domopi/domopi.env` (`ANTHROPIC_API_KEY=sk-ant-…`, puis
  `sudo systemctl restart domopi`) ; chaque génération consomme quelques
  centimes de crédit API. Sans clé configurée, le bouton affiche une erreur
  explicite et le reste de DomoPi fonctionne normalement.
- **Comptes** : un administrateur, plus des comptes « lecteur » (consultation et
  pilotage des sorties autorisées, sans accès aux réglages).

---

## Sécurité

- HTTPS obligatoire (redirection 80 → 443), en-têtes de durcissement,
  HSTS, limitation de débit sur `/api/login`.
- Mots de passe hachés en PBKDF2-SHA256 (200 000 itérations).
- Sessions par cookie signé HMAC (`HttpOnly`, `Secure`, `SameSite=strict`),
  valables 12 h.
- L'application n'écoute qu'en local (127.0.0.1:8000) ; seul nginx est exposé.

### Passer à un certificat Let's Encrypt (optionnel)

Si le Pi est joignable sur un nom de domaine public :

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d mondomaine.exemple.fr
```

Certbot adapte la configuration nginx et gère le renouvellement. Vous pouvez
ensuite retirer les directives `ssl_certificate*` pointant vers le certificat
auto-signé si certbot ne l'a pas déjà fait.

---

## Exploitation

```bash
systemctl status domopi          # état du service applicatif
journalctl -u domopi -f          # logs applicatifs (niveau système)
systemctl restart domopi         # redémarrer après une mise à jour
systemctl status nginx mosquitto # reverse proxy et broker MQTT
```

Emplacements :

| Élément                | Chemin                          |
|------------------------|---------------------------------|
| Application            | `/opt/domopi`                   |
| Base SQLite + secret   | `/var/lib/domopi`               |
| Config (admin, TLS)    | `/etc/domopi`                   |
| Service systemd        | `/etc/systemd/system/domopi.service` |
| Config nginx           | `/etc/nginx/sites-available/domopi`  |
| Config Mosquitto       | `/etc/mosquitto/conf.d/domopi.conf`  |

---

## Sauvegarde et restauration

Tout se fait depuis l'interface : **Paramètres → « Réglages généraux et
comptes » → « Sauvegarde et restauration »**.

Une sauvegarde est une archive `.tar.gz` réunissant **toutes les données
utilisateur** : la base complète (réglages, contrôleurs *avec leurs
identifiants*, périphériques, échelles, pages et widgets, comptes, historique
des mesures et journal), les icônes, les fonds de page et la clé de signature
des sessions. Elle est écrite en `0600` dans `/var/lib/domopi/backups`
(dossier réglable) et **contient des identifiants : traitez-la comme un
secret**.

Ne sont pas sauvegardés, car hors de portée du service : `/etc/domopi/domopi.env`
(mot de passe admin initial, `ANTHROPIC_API_KEY`), le certificat TLS de
`/etc/domopi/tls/` et les configurations nginx/mosquitto — ces trois-là sont
reposés par `install.sh` sur une machine neuve.

- **Sauvegarder maintenant** lance l'archive immédiatement ; l'avancement
  s'affiche sous les boutons et l'opération se poursuit même si vous quittez la
  page.
- **Sauvegardes automatiques** : donnez la date et l'heure de la prochaine
  sauvegarde, une périodicité, puis cochez « Activer » (la case se refuse tant
  que l'échéance n'est pas renseignée) — tous les jours, tous les
  2 jours, chaque semaine ou 2 semaines, tous les mois, 2 mois, 6 mois ou tous
  les ans). L'heure choisie est conservée d'une échéance à l'autre ; si le Pi
  était éteint, une seule sauvegarde de rattrapage est faite au redémarrage.
- **Archives conservées** : au-delà de ce nombre, les plus anciennes archives
  *générées automatiquement* sont supprimées après chaque sauvegarde (0 =
  illimité). Les archives importées ou déposées à la main ne sont jamais
  purgées.
- **Export FTP** (optionnel) : chaque archive réussie est déposée sur un serveur
  FTP — serveur, port, dossier distant (créé s'il manque), mode anonyme ou
  identifiants, mode **PASV** et **FTPS** (TLS explicite, recommandé puisque
  l'archive contient vos identifiants de box). Le bouton « Tester la connexion
  FTP » vérifie l'accès *et* le droit d'écriture. Un échec d'envoi n'annule pas
  la sauvegarde locale : il est consigné dans le journal.
- **Dossier des sauvegardes** : par défaut `/var/lib/domopi/backups`. Pour
  écrire ailleurs (disque USB…), ajoutez le chemin à `ReadWritePaths=` dans
  `/etc/systemd/system/domopi.service` puis
  `systemctl daemon-reload && systemctl restart domopi` — sinon systemd refuse
  l'écriture et l'interface le signale.

### Restaurer

Chaque archive listée offre **Restaurer**, **Télécharger** et **Supprimer** ; le
champ d'import permet d'envoyer une archive depuis votre PC (utile pour migrer
vers un nouveau Pi). La restauration est sélective :

| À restaurer | Effet |
|---|---|
| Icônes et fonds de page | Les fichiers de même nom sont remplacés, les autres conservés. |
| Historiques des capteurs présents dans les deux versions | **Fusion** : les capteurs sont appariés par contrôleur + identifiant (à défaut par nom) et seuls les trous sont comblés — les mesures déjà en base ne sont jamais écrasées. |
| Tout restaurer, paramètres compris | **Écrase la base actuelle** par celle de l'archive : réglages, contrôleurs, périphériques, échelles, pages, widgets, comptes. Au choix, la base de l'archive *à l'identique*, ou en conservant l'historique accumulé depuis la sauvegarde (les mesures actuelles sont réinjectées après l'écrasement). |

Après une restauration complète, **les comptes et mots de passe sont ceux de
l'archive**. La clé de session n'est remplacée que si vous cochez l'option
correspondante (cela déconnecte immédiatement tout le monde).

Sauvegarde et restauration s'excluent l'une l'autre et se déroulent côté
serveur : la supervision continue de tourner pendant l'opération.

---

Pour les détails techniques (architecture, ajout d'un connecteur, conventions),
voir **CLAUDE.md**.
