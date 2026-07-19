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

- **Périphériques** : cochez ceux à historiser. Pour les sorties (actionneurs),
  cochez « pilotable » pour autoriser leur commande depuis les pages.
  Associez à chaque périphérique une icône « état actif » et « état inactif ».
- **Pages** : arborescentes, plusieurs racines possibles. Chaque page a un fond
  paramétrable (couleur ou image), et une option « double rendu » pour définir
  des widgets différents sur smartphone et sur PC. On y pose des widgets :
  périphérique (icône + valeur), graphe, lien vers une autre page, ou texte.
- **Graphes** : boutons de période configurables (par défaut 24 h, 4 j, 15 j,
  30 j, 90 j et 6 mois) dans Paramètres → Réglages généraux → **« Paramétrage
  des courbes »** : pour chaque durée, libellé du bouton et affichage au choix —
  **« Toute la courbe »** (chaque mesure au pas de collecte, limité à la
  rétention du brut) ou **« Min / Moy / Max »** (trois courbes, pas horaire
  sous 15 jours puis journalier, en puisant dans les archives au-delà de la
  rétention du brut).
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
  Un capteur virtuel **sans formule** est un état **réglable à la main** :
  marqué pilotable (avec échelle, icônes et unité au besoin), il se règle
  depuis sa tuile comme une sortie et sa valeur est historisée s'il est
  surveillé — pratique pour une consigne ou un mode purement logiciel,
  utilisable ensuite dans les formules d'autres capteurs virtuels.
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

## Sauvegarde

L'essentiel tient dans `/var/lib/domopi` (base + clé de session) et
`/etc/domopi` (compte admin, certificat). Une copie de ces deux dossiers suffit
à restaurer l'installation.

---

Pour les détails techniques (architecture, ajout d'un connecteur, conventions),
voir **CLAUDE.md**.
