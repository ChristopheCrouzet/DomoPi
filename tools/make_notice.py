#!/usr/bin/env python3
"""Génère NOTICE-DomoPi.odt — notice utilisateur au format OpenDocument Text.

Même parti pris que export_ods.py : un .odt est un zip (mimetype stocké non
compressé et en premier, META-INF/manifest.xml, styles.xml, content.xml) écrit
à la main, sans odfpy ni pandoc — rien à installer.

Le document est *généré* : pour le corriger, éditer ce script et le relancer,
ne pas retoucher le .odt (comme make_icons.py pour les icônes).

    python3 tools/make_notice.py [chemin/de/sortie.odt]
"""
import datetime
import re
import sys
import zipfile
from pathlib import Path

FORCE = "--force" in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith("-")]
OUT = Path(_args[0]) if _args else \
    Path(__file__).resolve().parent.parent / "NOTICE-DomoPi.odt"

NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"'
)

# Captures produites par .claude/skills/run-domopi/notice_shots.py (hors dépôt :
# il dépend de Playwright). Les PNG, eux, sont versionnés ici pour que la
# notice se régénère sans navigateur.
IMG_DIR = Path(__file__).resolve().parent.parent / "doc" / "notice"

# --------------------------------------------------------------------- outils

def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def png_size(path):
    """Largeur/hauteur d'un PNG, lues dans son en-tête IHDR (pas de Pillow)."""
    d = path.read_bytes()[:24]
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} n'est pas un PNG")
    return int.from_bytes(d[16:20], "big"), int.from_bytes(d[20:24], "big")


def inline(t):
    """Micro-balisage du texte source : **gras**, `code`, //italique//."""
    out = esc(t)
    out = re.sub(r"\*\*(.+?)\*\*", r'<text:span text:style-name="Gras">\1</text:span>', out)
    out = re.sub(r"`(.+?)`", r'<text:span text:style-name="Mono">\1</text:span>', out)
    out = re.sub(r"//(.+?)//", r'<text:span text:style-name="Ital">\1</text:span>', out)
    return out


class Doc:
    """Accumule le corps du document et les styles automatiques des tableaux."""

    def __init__(self):
        self.body = []
        self.auto = []
        self.ntab = 0
        self.nfig = 0
        self.pictures = {}   # nom de fichier -> chemin source
        self.missing = []

    # --- blocs ---------------------------------------------------------
    def h(self, level, text, first=False):
        style = f"Titre{level}" + ("Debut" if first and level == 1 else "")
        self.body.append(
            f'<text:h text:outline-level="{level}" text:style-name="{style}">'
            f"{inline(text)}</text:h>")

    def p(self, text, style="Corps"):
        self.body.append(f'<text:p text:style-name="{style}">{inline(text)}</text:p>')

    def spacer(self):
        self.body.append('<text:p text:style-name="Corps"/>')

    def ul(self, items):
        li = "".join(
            f'<text:list-item><text:p text:style-name="Puce">{inline(i)}</text:p>'
            f"</text:list-item>" for i in items)
        self.body.append(f'<text:list text:style-name="LPuce">{li}</text:list>')

    def ol(self, items):
        li = "".join(
            f'<text:list-item><text:p text:style-name="Puce">{inline(i)}</text:p>'
            f"</text:list-item>" for i in items)
        self.body.append(f'<text:list text:style-name="LNum">{li}</text:list>')

    def code(self, block):
        lines = block.strip("\n").split("\n")
        for i, ln in enumerate(lines):
            style = "Code"
            if len(lines) == 1:
                style = "CodeSeul"
            elif i == 0:
                style = "CodeHaut"
            elif i == len(lines) - 1:
                style = "CodeBas"
            n = len(ln) - len(ln.lstrip(" "))
            txt = (f'<text:s text:c="{n}"/>' if n else "") + esc(ln.lstrip(" "))
            self.body.append(f'<text:p text:style-name="{style}">{txt}</text:p>')

    def note(self, text):
        self.p(text, "Note")

    def img(self, nom, legende, max_w=15.0, max_h=10.5):
        """Insère une capture, mise à l'échelle pour tenir dans la page.

        Une image absente n'interrompt pas la génération : elle est signalée
        en fin de traitement et le document se fait sans elle.
        """
        src = IMG_DIR / nom
        if not src.exists():
            self.missing.append(nom)
            return
        w, h = png_size(src)
        cw, ch = max_w, max_w * h / w
        if ch > max_h:                       # portrait (capture mobile)
            ch, cw = max_h, max_h * w / h
        self.pictures[nom] = src
        self.nfig += 1
        self.body.append(
            f'<text:p text:style-name="Figure">'
            f'<draw:frame draw:style-name="Fr" draw:name="fig{self.nfig}" '
            f'text:anchor-type="as-char" svg:width="{cw:.2f}cm" '
            f'svg:height="{ch:.2f}cm" draw:z-index="0">'
            f'<draw:image xlink:href="Pictures/{nom}" xlink:type="simple" '
            f'xlink:show="embed" xlink:actuate="onLoad"/>'
            f"</draw:frame></text:p>")
        self.body.append(
            f'<text:p text:style-name="Legende">Figure {self.nfig} — '
            f"{inline(legende)}</text:p>")

    def table(self, headers, rows, widths):
        self.ntab += 1
        name = f"T{self.ntab}"
        total = sum(widths)
        self.auto.append(
            f'<style:style style:name="{name}" style:family="table">'
            f'<style:table-properties style:width="16.6cm" table:align="margins"/>'
            f"</style:style>")
        cols = ""
        for i, w in enumerate(widths):
            cw = 16.6 * w / total
            self.auto.append(
                f'<style:style style:name="{name}.{i}" style:family="table-column">'
                f'<style:table-column-properties style:column-width="{cw:.3f}cm"/>'
                f"</style:style>")
            cols += f'<table:table-column table:style-name="{name}.{i}"/>'

        def row(cells, cell_style, para_style):
            tds = "".join(
                f'<table:table-cell table:style-name="{cell_style}" '
                f'office:value-type="string"><text:p text:style-name="{para_style}">'
                f"{inline(c)}</text:p></table:table-cell>" for c in cells)
            return f"<table:table-row>{tds}</table:table-row>"

        head = row(headers, "CelEntete", "CelEnteteP")
        corps = "".join(row(r, "Cel", "CelP") for r in rows)
        self.body.append(
            f'<table:table table:name="{name}" table:style-name="{name}">{cols}'
            f"<table:table-header-rows>{head}</table:table-header-rows>{corps}"
            f"</table:table>")
        self.spacer()


# --------------------------------------------------------------------- styles

STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles """ + NS + """ office:version="1.2">
<office:font-face-decls>
 <style:font-face style:name="Sans" svg:font-family="Carlito, Calibri, 'Liberation Sans', sans-serif" style:font-family-generic="swiss"/>
 <style:font-face style:name="Mono" svg:font-family="'DejaVu Sans Mono', Consolas, 'Liberation Mono', monospace" style:font-family-generic="modern" style:font-pitch="fixed"/>
</office:font-face-decls>
<office:styles>
 <style:default-style style:family="paragraph">
  <style:paragraph-properties fo:hyphenate="false" style:writing-mode="lr-tb"/>
  <style:text-properties style:font-name="Sans" fo:font-size="10.5pt" fo:language="fr" fo:country="FR"/>
 </style:default-style>
 <style:style style:name="Standard" style:family="paragraph"/>
 <style:style style:name="Corps" style:family="paragraph" style:parent-style-name="Standard">
  <style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0.18cm" fo:text-align="justify" fo:line-height="118%"/>
 </style:style>
 <style:style style:name="Titre" style:family="paragraph">
  <style:paragraph-properties fo:margin-top="5cm" fo:margin-bottom="0.3cm" fo:text-align="center"/>
  <style:text-properties fo:font-size="30pt" fo:font-weight="bold" fo:color="#1a2430"/>
 </style:style>
 <style:style style:name="SousTitre" style:family="paragraph">
  <style:paragraph-properties fo:margin-bottom="0.2cm" fo:text-align="center"/>
  <style:text-properties fo:font-size="13pt" fo:color="#7a6440"/>
 </style:style>
 <style:style style:name="Garde" style:family="paragraph">
  <style:paragraph-properties fo:text-align="center" fo:margin-top="0.1cm"/>
  <style:text-properties fo:font-size="9.5pt" fo:color="#6b7280"/>
 </style:style>
 <style:style style:name="Titre1" style:family="paragraph">
  <style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0.45cm" fo:break-before="page"
    fo:padding-bottom="0.12cm" fo:border-bottom="0.06cm solid #e0a13a" fo:keep-with-next="always"/>
  <style:text-properties fo:font-size="20pt" fo:font-weight="bold" fo:color="#1a2430"/>
 </style:style>
 <style:style style:name="Titre1Debut" style:family="paragraph" style:parent-style-name="Titre1">
  <style:paragraph-properties fo:break-before="auto"/>
 </style:style>
 <style:style style:name="Titre2" style:family="paragraph">
  <style:paragraph-properties fo:margin-top="0.55cm" fo:margin-bottom="0.2cm" fo:keep-with-next="always"/>
  <style:text-properties fo:font-size="13.5pt" fo:font-weight="bold" fo:color="#2b3a4a"/>
 </style:style>
 <style:style style:name="Titre3" style:family="paragraph">
  <style:paragraph-properties fo:margin-top="0.4cm" fo:margin-bottom="0.15cm" fo:keep-with-next="always"/>
  <style:text-properties fo:font-size="11.5pt" fo:font-weight="bold" fo:color="#3c4a5a"/>
 </style:style>
 <style:style style:name="Puce" style:family="paragraph" style:parent-style-name="Corps">
  <style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0.10cm" fo:text-align="start"/>
 </style:style>
 <style:style style:name="Code" style:family="paragraph">
  <style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0cm" fo:margin-left="0.2cm"
    fo:padding-left="0.35cm" fo:padding-right="0.2cm" fo:background-color="#f4f2ee"
    fo:border-left="0.08cm solid #e0a13a" fo:text-align="start" fo:line-height="112%"/>
  <style:text-properties style:font-name="Mono" fo:font-size="9pt" fo:color="#23303d"/>
 </style:style>
 <style:style style:name="CodeHaut" style:family="paragraph" style:parent-style-name="Code">
  <style:paragraph-properties fo:margin-top="0.1cm" fo:padding-top="0.15cm"/>
 </style:style>
 <style:style style:name="CodeBas" style:family="paragraph" style:parent-style-name="Code">
  <style:paragraph-properties fo:margin-bottom="0.25cm" fo:padding-bottom="0.15cm"/>
 </style:style>
 <style:style style:name="CodeSeul" style:family="paragraph" style:parent-style-name="Code">
  <style:paragraph-properties fo:margin-top="0.1cm" fo:margin-bottom="0.25cm"
    fo:padding-top="0.15cm" fo:padding-bottom="0.15cm"/>
 </style:style>
 <style:style style:name="Note" style:family="paragraph" style:parent-style-name="Corps">
  <style:paragraph-properties fo:margin-top="0.2cm" fo:margin-bottom="0.25cm" fo:margin-left="0.2cm"
    fo:padding="0.2cm" fo:background-color="#f0f4f8" fo:border-left="0.08cm solid #6b8ba4"/>
  <style:text-properties fo:font-size="10pt"/>
 </style:style>
 <style:style style:name="CelEnteteP" style:family="paragraph">
  <style:paragraph-properties fo:margin="0cm" fo:text-align="start"/>
  <style:text-properties fo:font-size="10pt" fo:font-weight="bold" fo:color="#1a2430"/>
 </style:style>
 <style:style style:name="CelP" style:family="paragraph">
  <style:paragraph-properties fo:margin="0cm" fo:text-align="start" fo:line-height="112%"/>
  <style:text-properties fo:font-size="10pt"/>
 </style:style>
 <style:style style:name="Pied" style:family="paragraph">
  <style:paragraph-properties fo:text-align="center"/>
  <style:text-properties fo:font-size="8.5pt" fo:color="#8a8a8a"/>
 </style:style>
 <style:style style:name="Figure" style:family="paragraph">
  <style:paragraph-properties fo:margin-top="0.35cm" fo:margin-bottom="0.1cm"
    fo:text-align="center" fo:keep-with-next="always"/>
 </style:style>
 <style:style style:name="Legende" style:family="paragraph">
  <style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0.4cm" fo:text-align="center"/>
  <style:text-properties fo:font-size="9pt" fo:font-style="italic" fo:color="#6b7280"/>
 </style:style>
 <style:style style:name="Fr" style:family="graphic">
  <style:graphic-properties style:vertical-pos="top" style:vertical-rel="baseline"
    style:horizontal-pos="center" style:horizontal-rel="paragraph"
    fo:border="0.02cm solid #b9c0c8" fo:padding="0cm"/>
 </style:style>
 <style:style style:name="Gras" style:family="text">
  <style:text-properties fo:font-weight="bold"/></style:style>
 <style:style style:name="Ital" style:family="text">
  <style:text-properties fo:font-style="italic"/></style:style>
 <style:style style:name="Mono" style:family="text">
  <style:text-properties style:font-name="Mono" fo:font-size="9pt" fo:color="#7a4f10"/></style:style>
 <style:style style:name="CelEntete" style:family="table-cell">
  <style:table-cell-properties fo:background-color="#e6e9ed" fo:padding="0.12cm"
    fo:border="0.02cm solid #b9c0c8"/></style:style>
 <style:style style:name="Cel" style:family="table-cell">
  <style:table-cell-properties fo:padding="0.12cm" fo:border="0.02cm solid #d3d8dd"/></style:style>
</office:styles>
<office:automatic-styles>
 <style:page-layout style:name="pm1">
  <style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm"
    style:print-orientation="portrait" fo:margin-top="2cm" fo:margin-bottom="1.6cm"
    fo:margin-left="2.2cm" fo:margin-right="2.2cm"/>
  <style:footer-style>
   <style:header-footer-properties fo:min-height="0.6cm" fo:margin-top="0.5cm"/>
  </style:footer-style>
 </style:page-layout>
</office:automatic-styles>
<office:master-styles>
 <style:master-page style:name="Standard" style:page-layout-name="pm1">
  <style:footer>
   <text:p text:style-name="Pied">DomoPi &#8212; Notice d&#8217;utilisation &#183; page
    <text:page-number text:select-page="current">1</text:page-number></text:p>
  </style:footer>
 </style:master-page>
</office:master-styles>
</office:document-styles>"""

LIST_STYLES = """
 <text:list-style style:name="LPuce">
  <text:list-level-style-bullet text:level="1" text:bullet-char="&#8226;">
   <style:list-level-properties text:space-before="0.4cm" text:min-label-width="0.4cm"/>
  </text:list-level-style-bullet>
 </text:list-style>
 <text:list-style style:name="LNum">
  <text:list-level-style-number text:level="1" style:num-suffix="." style:num-format="1">
   <style:list-level-properties text:space-before="0.4cm" text:min-label-width="0.5cm"/>
  </text:list-level-style-number>
 </text:list-style>"""


# -------------------------------------------------------------------- contenu

def build():
    d = Doc()
    today = datetime.date.today().strftime("%d/%m/%Y")

    # ---------------------------------------------------------- page de garde
    d.p("DomoPi", "Titre")
    d.p("Notice d’utilisation", "SousTitre")
    d.p("Supervision domotique auto-hébergée sur Raspberry Pi", "Garde")
    d.p(f"Version du {today}", "Garde")
    d.p("Ce document vous appartient : complétez-le librement.", "Garde")

    d.h(1, "Sommaire")
    d.ol([
        "Installation initiale",
        "Initialisation des contrôleurs domotiques (WES, eedomus)",
        "Utilisation normale : consulter et éditer les pages",
        "Dépannage : journal et fenêtre d’essai d’un périphérique",
        "Capteurs virtuels : syntaxe et création",
        "Mettre en place un certificat HTTPS valide",
    ])
    d.note("Deux rôles existent. L’**administrateur** voit le bouton "
           "**Paramètres** du bandeau et accède à tout ce qui est décrit ici. "
           "Un compte **lecteur** ne voit que les pages de visualisation et peut piloter "
           "les sorties autorisées. Les chapitres 1, 2, 5 et 6 supposent le compte "
           "administrateur ; le chapitre 3 concerne tout le monde.")

    # ============================================================ 1
    d.h(1, "1. Installation initiale")
    d.p("DomoPi s’installe sur une Raspbian (Raspberry Pi OS) fraîchement "
        "flashée, Lite ou Desktop. Comptez un Raspberry Pi 2 ou 3 au minimum "
        "(un Pi 4 est plus confortable) et une carte SD de 8 Go.")

    d.h(2, "1.1 Lancer l’installeur")
    d.p("Copiez le dossier du projet sur le Pi, puis depuis ce dossier :")
    d.code("sudo bash install.sh")
    d.p("L’installeur est **relançable sans risque** : il préserve la "
        "configuration, les identifiants, le certificat et les icônes déjà "
        "ajoutées. Il met en place :")
    d.ul([
        "**nginx** en frontal HTTPS (certificat auto-signé au départ, "
        "voir le chapitre 6), avec redirection du port 80 vers le 443 ;",
        "**Mosquitto**, le broker MQTT local utilisé par le WES ;",
        "**Python 3**, l’application et son service `systemd` ;",
        "la base **SQLite** et le dossier des sauvegardes dans `/var/lib/domopi`.",
    ])
    d.p("Deux questions vous sont posées : l’**identifiant et le mot de passe "
        "administrateur** de DomoPi, puis des **identifiants MQTT** (à reporter "
        "ensuite dans le WES). Notez-les.")

    d.h(2, "1.2 Première connexion")
    d.p("L’interface répond sur **https://<adresse-ip-du-pi>/**. Le navigateur "
        "affiche un avertissement de sécurité : c’est normal tant que le "
        "certificat est auto-signé — passez outre, ou lisez le chapitre 6 pour "
        "obtenir un certificat reconnu.")

    d.h(2, "1.3 Accès depuis l’extérieur")
    d.p("Pour consulter DomoPi hors de chez vous, redirigez sur votre box :")
    d.table(
        ["Port", "À rediriger ?", "Pourquoi"],
        [["443", "Oui", "L’interface web en HTTPS."],
         ["80", "Seulement pour Let’s Encrypt",
          "Validation du certificat et redirection vers HTTPS (chapitre 6)."],
         ["1883", "**Jamais**", "Broker MQTT : il doit rester interne."],
         ["8000", "**Jamais**",
          "L’application elle-même, qui n’écoute que sur 127.0.0.1."]],
        [1.2, 3, 6])

    d.h(2, "1.4 Emplacements et commandes utiles")
    d.table(
        ["Élément", "Chemin"],
        [["Application", "`/opt/domopi`"],
         ["Base SQLite, clé de session, sauvegardes", "`/var/lib/domopi`"],
         ["Configuration (compte admin, certificat)", "`/etc/domopi`"],
         ["Service systemd", "`/etc/systemd/system/domopi.service`"],
         ["Configuration nginx", "`/etc/nginx/sites-available/domopi`"]],
        [4, 6])
    d.code("""
systemctl status domopi            # état du service
journalctl -u domopi -f            # traces système, en direct
systemctl restart domopi           # redémarrer après une mise à jour
systemctl status nginx mosquitto   # frontal HTTPS et broker MQTT
""")
    d.note("**Génération d’icônes par IA** (facultatif) : ajoutez "
           "`ANTHROPIC_API_KEY=sk-ant-…` dans `/etc/domopi/domopi.env` puis "
           "`sudo systemctl restart domopi`. Sans cette clé, tout le reste "
           "fonctionne normalement et seul le bouton « Générer par IA » "
           "renvoie une erreur.")

    # ============================================================ 2
    d.h(1, "2. Initialisation des contrôleurs domotiques")
    d.p("Un **contrôleur** est une box ou un appareil qui expose des "
        "périphériques. Tout se passe dans **Paramètres** (bandeau, compte "
        "administrateur) → onglet **Réglages généraux et comptes** → "
        "rubrique **Contrôleurs domotiques**.")
    d.p("La marche à suivre est la même pour tous : déclarer le "
        "contrôleur, lancer **Découvrir les périphériques**, puis "
        "choisir dans l’onglet **Périphériques** ceux à suivre.")
    d.img("02-controleurs.png",
          "La rubrique //Contrôleurs domotiques//. Chaque contrôleur déclaré "
          "s’y configure et s’y interroge ; les boutons du bas ajoutent un "
          "nouveau contrôleur.")

    d.h(2, "2.1 Box eedomus (API locale)")
    d.ol([
        "Sur le portail eedomus : //Mon compte → Paramètres → API//. "
        "Activez l’**API locale** et relevez `api_user` et `api_secret`.",
        "Dans DomoPi : **Ajouter une box eedomus**, puis renseignez "
        "l’**adresse IP locale** de la box, `api_user` et `api_secret`.",
        "Cliquez **Découvrir les périphériques**.",
    ])
    d.note("L’API //locale// de l’eedomus ne donne pas l’historique "
           "(il est réservé au cloud). DomoPi constitue donc son **propre "
           "historique** en échantillonnant au pas de collecte, 5 minutes par "
           "défaut : les courbes ne commencent qu’à partir de "
           "l’installation.")

    d.h(2, "2.2 WES Cartelectronic (MQTT)")
    d.p("Le WES est lu par MQTT au format //Home Assistant Discovery// : il "
        "publie lui-même la liste de ses entités, DomoPi les reçoit. "
        "Firmware **0.9 bêta 05 minimum**.")
    d.ol([
        "Dans l’interface du WES, menu MQTT : activez MQTT et pointez-le sur le "
        "Pi, `<adresse-ip-du-pi>:1883`, avec les identifiants MQTT saisis à "
        "l’installation.",
        "Dans DomoPi : **Ajouter un WES (MQTT)**. En local, laissez "
        "`host = 127.0.0.1`, `port = 1883` et le préfixe `homeassistant` ; "
        "recopiez les mêmes identifiants MQTT.",
        "Patientez quelques instants — le WES doit avoir publié ses "
        "annonces — puis **Découvrir les périphériques**.",
    ])
    d.note("Rien ne remonte ? La découverte ne voit que ce qui a déjà "
           "été publié sur le broker. Vérifiez d’abord les "
           "identifiants MQTT côté WES, puis relancez la découverte après "
           "une minute. Le diagnostic décrit au chapitre 4 dit précisément "
           "quel message a été reçu, et quand.")

    d.h(2, "2.3 Ampli audio-vidéo Yamaha")
    d.p("Les amplis Yamaha pilotables par le réseau (API YNC) sont "
        "également reconnus : indiquez l’adresse IP de l’ampli. La "
        "découverte crée une liste fixe d’éléments — marche/arrêt, "
        "volume, entrée, programme sonore, scènes — avec leurs échelles "
        "de réglage prêtes à l’emploi. Piloter le volume, l’entrée "
        "ou le programme suppose l’ampli allumé.")

    d.h(2, "2.4 Régler les périphériques découverts")
    d.p("Onglet **Périphériques**. Chaque ligne se règle directement dans "
        "le tableau ; **il n’y a pas de bouton Enregistrer** : une case part au "
        "clic, un champ de saisie dès que vous en sortez, et le message "
        "« Enregistré » le confirme.")
    d.table(
        ["Colonne", "Rôle"],
        [["Surv.", "Historiser ce périphérique (indispensable pour les courbes "
                   "et pour les fonctions des capteurs virtuels)."],
         ["Masquer", "Ne plus le proposer lors de la création d’un widget. "
                     "N’efface rien."],
         ["Nom, Pièce", "Libellés libres, utilisés partout dans "
                            "l’interface et dans les formules."],
         ["Type", "Capteur ou sortie. **Ce mot est un lien** vers la fenêtre "
                  "d’essai (chapitre 4)."],
         ["Unité", "Affichée sur la tuile et les graphes (°C, kWh, %…)."],
         ["Pilotable", "Autorise la commande depuis les pages, y compris pour les "
                       "lecteurs."],
         ["Échelle", "Réglage proportionnel (gradateur, consigne, mode) au lieu "
                          "d’un simple marche/arrêt."],
         ["Icônes on / off", "Icône pour l’état actif et pour "
                                  "l’état inactif."]],
        [2.2, 7.8])
    d.img("02-peripheriques.png",
          "Le tableau des périphériques : une ligne par appareil, tout se "
          "règle sur place. Les deux en-têtes (tri et filtres) restent "
          "visibles pendant le défilement.")
    d.p("Les **échelles** se créent dans l’onglet **Paramètres**, rubrique "
        "« Échelles de pilotage » : unité, plage minimum/maximum, "
        "résolution du curseur, temporisation d’auto-validation, et de 2 à "
        "20 valeurs prédéfinies affichées en boutons. Une même échelle "
        "sert à plusieurs périphériques.")
    d.note("Vous pouvez relancer une découverte quand vous voulez : elle ajoute "
           "les nouveautés sans jamais écraser vos réglages (ni "
           "l’échelle, ni une unité que vous avez saisie).")

    # ============================================================ 3
    d.h(1, "3. Utilisation normale")

    d.h(2, "3.1 Consulter")
    d.p("La page d’accueil présente vos **pages de visualisation** sous forme "
        "de tuiles de taille uniforme : périphériques, graphes, textes et "
        "**dossiers** (les sous-pages). Un clic sur un dossier descend d’un "
        "niveau. Sur smartphone, la grille passe à trois colonnes.")
    d.img("03-accueil.png",
          "Une page de visualisation : une tuile-dossier, des tuiles de "
          "périphériques, un texte libre et deux graphes.")
    d.img("03-mobile.png",
          "La même page sur smartphone : trois colonnes, tuiles de hauteur "
          "identique. Rien à configurer, l’affichage s’adapte.", max_h=11.5)
    d.ul([
        "Une tuile de **capteur** affiche l’icône, le nom et la valeur ; "
        "un clic ouvre son graphe.",
        "Une tuile de **sortie pilotable** bascule marche/arrêt au clic. Si une "
        "échelle lui est affectée, un **double-clic** (ou un **appui long** au "
        "doigt) ouvre le réglage : curseur crané et boutons de valeurs. "
        "Certaines échelles, comme les consignes, ouvrent directement ce "
        "réglage dès le clic simple.",
        "Un badge **« sans réponse »** en haut à droite signale un "
        "appareil qui ne répond plus ; la mention //invalide// signale un "
        "capteur virtuel dont la formule n’a pas pu être calculée.",
        "Les valeurs des tuiles affichées se rafraîchissent toutes les "
        "10 secondes environ, sans recharger la page.",
    ])

    d.img("03-reglage-echelle.png",
          "Le réglage d’une consigne : curseur borné et cranté par "
          "l’échelle, plus ses valeurs en boutons.", max_w=11.5)

    d.h(3, "Les graphes")
    d.p("Sous chaque graphe, des boutons de période (par défaut 24 h, 4 j, "
        "15 j, 30 j, 90 j et 6 mois). Les périodes courtes montrent **chaque "
        "mesure**, les longues une synthèse **minimum / moyenne / maximum** "
        "(trois courbes) qui puise dans les archives bien au-delà de la "
        "conservation du détail.")
    d.p("**Zoom** — à la souris : clic maintenu puis glissé pour dessiner un "
        "rectangle ; un glissé horizontal ne zoome que le temps. Au doigt : deux "
        "doigts qu’on écarte zooment, qu’on rapproche dézooment, et la "
        "courbe suit les doigts — deux doigts déplacés sans les écarter "
        "font glisser la fenêtre. Deux boutons apparaissent alors : "
        "**Zoom précédent** et **Zoom initial**. Le zoom ne fait que changer "
        "l’échelle des points déjà affichés ; changer de période le "
        "remet à zéro.")
    d.img("03-graphe.png",
          "Un graphe et ses boutons de période. Les boutons restent à droite "
          "du titre, y compris sur mobile, pour ne pas rogner la hauteur de "
          "la courbe.")

    d.h(2, "3.2 Créer et modifier une page")
    d.p("**Paramètres → onglet Pages de visualisation.** Les pages sont "
        "arborescentes : plusieurs racines possibles, sous-pages illimitées. "
        "**+ Nouvelle page racine** crée une page ; l’icône crayon en "
        "modifie une.")
    d.table(
        ["Champ de la page", "Effet"],
        [["Titre", "Nom affiché en haut de la page et sur sa tuile-dossier."],
         ["Page parente", "Vide = page racine ; sinon la page devient une "
                          "sous-page, matérialisée par une tuile-dossier chez "
                          "son parent."],
         ["Icône", "Icône de la tuile-dossier."],
         ["Fond : image / couleur", "Une image déposée dans l’onglet "
                                    "« Icônes et fonds de page », ou "
                                    "une couleur CSS (`#1a2430`). L’image "
                                    "l’emporte."],
         ["Ordre", "Position parmi les tuiles du parent (petit = en haut)."],
         ["Double rendu smartphone / PC", "Permet ensuite de destiner chaque "
                                          "widget à l’un des deux affichages."]],
        [3.4, 6.6])
    d.img("03-admin-pages.png",
          "L’arborescence des pages et, en dessous, les widgets de la page "
          "choisie.")
    d.p("Une fois la page sélectionnée, la rubrique **Widgets de la page** "
        "s’ouvre en dessous. **+ Ajouter un widget** propose quatre types :")
    d.table(
        ["Type de widget", "Contenu"],
        [["Périphérique (icône + valeur)", "Une tuile : état, valeur, "
                                                     "et pilotage si le périphérique "
                                                     "est pilotable."],
         ["Graphe (courbes historiques)", "La courbe d’un capteur, avec ses "
                                          "boutons de période. Le champ "
                                          "//Fenêtre par défaut// choisit la "
                                          "période affichée à "
                                          "l’ouverture."],
         ["Lien vers une page", "Une tuile-dossier supplémentaire, vers "
                                "n’importe quelle page."],
         ["Texte libre", "Un bandeau de texte, pour titrer ou commenter une "
                         "zone de la page."]],
        [3.6, 6.4])
    d.img("03-widget.png",
          "Le dialogue d’un widget. Les champs proposés suivent le type "
          "choisi : //Périphérique// pour une tuile, //Page cible// pour un "
          "lien, //Texte// pour un libellé.", max_w=12.5)
    d.p("Trois réglages communs à tous les widgets : le **Libellé "
        "affiché** (facultatif, remplace le nom du périphérique), le "
        "**Rendu** (//Smartphone et PC//, //Smartphone uniquement// ou //PC "
        "uniquement//, si la page est en double rendu) et l’**Ordre**.")
    d.note("**Ordre d’affichage** : les tuiles de sous-pages et les widgets "
           "partagent la même numérotation. Une bonne habitude consiste à "
           "placer les dossiers très en amont (`-20`) ou très en aval "
           "(`+20`) des widgets, quitte à ce que deux éléments partagent le "
           "même numéro — c’est sans gravité.")

    d.h(2, "3.3 Comptes")
    d.p("Onglet **Réglages généraux et comptes**. À côté de "
        "l’administrateur, créez autant de comptes **lecteur** que "
        "nécessaire : ils consultent les pages et pilotent les sorties "
        "autorisées, sans accéder à aucun réglage.")

    d.h(2, "3.4 Sauvegardes")
    d.p("Même onglet, rubrique **Sauvegarde et restauration**. Une archive "
        "réunit **toutes vos données** : réglages, contrôleurs //avec "
        "leurs identifiants//, périphériques, pages, comptes, historique, "
        "icônes et fonds.")
    d.ul([
        "**Sauvegarder maintenant** produit une archive immédiatement ; "
        "l’opération continue même si vous quittez la page.",
        "**Sauvegardes automatiques** : donnez d’abord la date et l’heure de "
        "la prochaine échéance et une périodicité, //puis// cochez "
        "« Activer ».",
        "**Export FTP** facultatif vers un NAS ou une box : préférez FTPS, "
        "l’archive contenant vos identifiants. Le bouton de test vérifie "
        "l’accès //et// le droit d’écriture.",
        "La **restauration** est sélective : icônes seules, fusion des "
        "historiques (rien n’est écrasé, seuls les trous sont comblés), "
        "ou restauration complète — qui remplace alors aussi les comptes.",
    ])
    d.note("Une archive contient les identifiants de vos box : **traitez-la comme "
           "un mot de passe**.")

    # ============================================================ 4
    d.h(1, "4. Dépannage")
    d.p("Deux outils répondent à la quasi-totalité des questions : le "
        "**journal**, pour savoir //ce qui s’est passé//, et la **fenêtre "
        "d’essai** d’un périphérique, pour savoir //pourquoi une valeur ne "
        "remonte pas//.")

    d.h(2, "4.1 Le journal")
    d.p("Bouton **Journal** dans le bandeau, accessible à tous. Il liste les "
        "événements datés : démarrages, découvertes, commandes "
        "envoyées, absences de réponse, erreurs de connexion, sauvegardes.")
    d.p("La **verbosité** se règle dans **Paramètres → Réglages "
        "généraux et comptes** :")
    d.table(
        ["Niveau", "Ce qui est consigné", "Quand l’utiliser"],
        [["Verbeux", "Chaque changement de capteur ou d’actionneur.",
          "Le temps d’une mise au point — le journal grossit vite."],
         ["Moyen", "Actions principales, avertissements et erreurs.",
          "Réglage courant."],
         ["Erreurs", "Absences de réponse et erreurs seulement.",
          "Installation stabilisée."]],
        [1.8, 5.2, 3.6])
    d.img("04-journal.png",
          "Le journal, filtrable par niveau. La source (`auth`, `poller`, "
          "`system`, nom du contrôleur…) dit quelle partie de "
          "l’application a écrit la ligne.")
    d.p("Les entrées sont purgées automatiquement, une fois par semaine, "
        "au-delà de la durée de conservation configurée.")
    d.p("Pour les pannes plus basses — service qui ne démarre pas, erreur "
        "Python — les traces système se lisent en SSH :")
    d.code("journalctl -u domopi -n 100 --no-pager")

    d.h(2, "4.2 La fenêtre d’essai d’un périphérique")
    d.p("Dans l’onglet **Périphériques**, cliquez sur le mot **« capteur »** "
        "ou **« sortie »** de la colonne //Type// : c’est un lien. Une "
        "fenêtre s’ouvre, composée comme une page de visualisation.")
    d.ul([
        "**La tuile telle que la verront vos lecteurs** — de quoi essayer un "
        "ordre marche/arrêt, un gradateur ou une consigne et vérifier le "
        "format affiché avant de la poser sur une page.",
        "**Télécharger les données régulières** : toutes les mesures "
        "au pas de collecte, au format **ODS** (LibreOffice Calc, Excel).",
        "**Télécharger les données synthétiques** : un minimum, une "
        "moyenne et un maximum par jour, archives comprises — donc sur toute "
        "la profondeur d’historique.",
        "**Effacer l’historique** de ce périphérique (confirmation "
        "demandée) ; la valeur courante et les widgets sont conservés.",
        "Le **graphe** habituel avec ses boutons de période.",
        "Un **diagnostic** technique, détaillé ci-dessous.",
    ])
    d.img("04-essai.png",
          "La fenêtre d’essai : la tuile réelle, les deux exports, "
          "l’effacement de l’historique, le graphe et le diagnostic.",
          max_h=13.0)
    d.p("Tant que la fenêtre reste ouverte, la tuile et le diagnostic sont "
        "**relus toutes les 5 secondes** : actionnez l’appareil lui-même "
        "(interrupteur mural, interface du WES) et regardez la valeur suivre. "
        "C’est la façon la plus rapide de vérifier un câblage ou une "
        "association.")
    d.note("Les trois tuiles de données n’apparaissent que si des mesures ont "
           "déjà été enregistrées. Un périphérique dont on a "
           "décoché //Surv.// **garde** son historique et donc ses boutons "
           "d’export.")

    d.h(3, "Lire le diagnostic")
    d.p("La partie commune indique le contrôleur, l’identifiant côté "
        "contrôleur, la valeur courante et l’**âge de la dernière "
        "lecture** — souvent la première chose à regarder. Pour un appareil "
        "MQTT (WES), s’y ajoutent le //topic// d’état, le nombre de messages "
        "reçus, la **date et le contenu brut du dernier message**, le modèle "
        "d’extraction appliqué et la valeur qui en sort.")
    d.p("Ces informations distinguent les **trois pannes de lecture** :")
    d.table(
        ["Ce que dit le diagnostic", "Cause", "Que faire"],
        [["Aucun //topic// d’état annoncé",
          "L’appareil n’a pas publié d’annonce complète pour cette "
          "entité.",
          "Vérifier la version du firmware, puis relancer la découverte."],
         ["//Topic// annoncé, aucun message reçu",
          "L’état n’est publié qu’au changement et rien n’a "
          "bougé depuis la connexion.",
          "Actionner l’appareil : la valeur doit apparaître dans les "
          "secondes qui suivent."],
         ["Message reçu, valeur extraite vide",
          "Le modèle d’extraction ne correspond pas au contenu reçu.",
          "Comparer le contenu brut affiché et le modèle ; relancer la "
          "découverte, qui reprend les annonces à jour."]],
        [3.4, 3.6, 3.6])
    d.note("Le diagnostic ne contient **jamais** de mot de passe ni de clé "
           "d’API : il peut être recopié tel quel dans une demande d’aide.")

    # ============================================================ 5
    d.h(1, "5. Capteurs virtuels")
    d.p("Un **capteur virtuel** est une valeur **calculée par formule** à "
        "partir des autres capteurs. Il se comporte ensuite comme un capteur "
        "ordinaire : il est historisé à chaque cycle de collecte, se trace, "
        "s’exporte et se pose sur une page.")
    d.p("**Paramètres → onglet Périphériques → rubrique Capteurs "
        "virtuels → + Ajouter un capteur virtuel.** Le bouton `[…]` de la "
        "colonne //Formule// ouvre l’éditeur : il **valide la formule en "
        "direct** et propose, d’un clic, la liste des fonctions et celle des "
        "capteurs disponibles.")

    d.img("05-editeur-formule.png",
          "L’éditeur de formule : validation en direct sous le champ, "
          "rappel des fonctions à gauche, capteurs insérables d’un clic à "
          "droite.", max_w=13.5)

    d.h(2, "5.1 Syntaxe")
    d.table(
        ["Élément", "Notation", "Exemple"],
        [["Nombre", "Point décimal", "`21.5`"],
         ["Référence à un capteur", "Nom entre accolades",
          "`{Température salon}`"],
         ["Opérateurs", "`+` `-` `*` `/` et parenthèses",
          "`({A} + {B}) / 2`"],
         ["Séparateur d’arguments", "Virgule ou point-virgule",
          "`Moy({A}, 3h)`"]],
        [3.4, 3.6, 3.6])
    d.p("Le nom d’un capteur se reconnaît **sans tenir compte de la casse, "
        "des accents ni des espaces en trop**. En revanche, si deux capteurs "
        "portent le même nom, la formule est refusée : renommez-en un.")

    d.h(2, "5.2 Fonctions")
    d.table(
        ["Fonction", "Rôle", "Plage acceptée"],
        [["`Deriver({capteur}, durée)`",
          "Variation **par heure** sur la durée donnée : convertit un "
          "compteur (kWh) en débit (kW).", "de `6min` à `24h`"],
         ["`Min({capteur}, plage)`", "Valeur minimale sur la plage.",
          "jusqu’à `168h`, ou `heure`, `jour`, `hier`"],
         ["`Max({capteur}, plage)`", "Valeur maximale sur la plage.", "idem"],
         ["`Moy({capteur}, plage)`", "Moyenne sur la plage.", "idem"]],
        [4.2, 5.4, 3.6])
    d.p("Les trois mots-clés de plage désignent : `heure` l’heure en "
        "cours, `jour` la journée en cours, `hier` la journée d’hier "
        "complète (heure locale).")

    d.h(2, "5.3 Exemples")
    d.code("""
Deriver({Compteur EDF}, 1h)
    → puissance instantanée en kW à partir du compteur en kWh

Deriver({Compteur EDF}, 1h) - {Départ Chauffage} - {Départ Cumulus}
    → consommation des autres usages

Max({Température extérieure}, hier) - Min({Température extérieure}, hier)
    → amplitude thermique de la veille

({Température salon} + {Température chambre}) / 2
    → moyenne de deux pièces
""")

    d.h(2, "5.4 Règles à connaître")
    d.ul([
        "Les fonctions `Deriver`, `Min`, `Max` et `Moy` lisent l’**historique** : "
        "le capteur référencé doit être **surveillé** (case //Surv.// "
        "cochée). Une référence simple `{Capteur}`, elle, se contente de la "
        "dernière valeur connue.",
        "Pour cette raison, DomoPi **refuse de décocher //Surv.//** sur un "
        "capteur utilisé par une fonction d’historique, et vous le dit.",
        "Un calcul impossible — division par zéro, historique insuffisant, "
        "référence disparue — donne la valeur //invalide// : la tuile "
        "l’affiche et le graphe laisse un trou, sans fausser les moyennes.",
        "Les capteurs virtuels sont calculés **en dernier** à chaque cycle : "
        "ils travaillent donc sur les valeurs fraîches du cycle en cours.",
        "Un capteur virtuel peut en référencer un autre.",
    ])

    d.h(2, "5.5 Sans formule : un état réglable à la main")
    d.p("Laissez la formule vide et l’élément devient une **sortie "
        "virtuelle** : un état purement logiciel, que vous réglez depuis sa "
        "tuile si vous le cochez //Pilotable// (avec échelle, icônes et "
        "unité au besoin). Utile pour une consigne ou un mode maison, "
        "réutilisable ensuite dans les formules des autres capteurs virtuels. "
        "La colonne //Type// suit cette logique : **sortie** tant qu’il n’y a "
        "pas de formule, **capteur** dès qu’on en pose une.")

    # ============================================================ 6
    d.h(1, "6. Mettre en place un certificat HTTPS valide")
    d.p("À l’installation, DomoPi se dote d’un certificat **auto-signé** : "
        "la liaison est bien chiffrée, mais aucune autorité ne garantit "
        "l’identité du serveur, d’où l’avertissement du navigateur. "
        "C’est sans conséquence sur un réseau local. Dès que DomoPi est "
        "publié sur Internet, un **certificat Let’s Encrypt** — gratuit, "
        "reconnu par tous les navigateurs — fait disparaître "
        "l’avertissement.")

    d.h(2, "6.1 Les deux prérequis")
    d.ol([
        "**Un nom de domaine public** qui pointe sur votre connexion. Le nom "
        "gratuit proposé par votre fournisseur convient (chez Free, un "
        "`monnom.hd.free.fr` déclaré dans la Freebox).",
        "**Le port 80 redirigé** par la box vers le Pi, en plus du 443. "
        "C’est par lui que Let’s Encrypt dépose et relit son jeton de "
        "validation — à l’émission **et à chaque renouvellement**.",
    ])
    d.note("Le port 80 n’expose rien de l’application : il ne sert que le "
           "jeton de validation et renvoie tout le reste vers HTTPS. Le mot de "
           "passe de session, lui, n’est jamais émis en clair.")

    d.h(2, "6.2 La commande")
    d.p("Ouvrez une session SSH sur le Pi et lancez, avec votre nom de domaine :")
    d.code("sudo domopi-https mondomaine.exemple.fr")
    d.p("Le script est **relançable sans risque** et s’occupe de tout :")
    d.ul([
        "il installe `certbot` si besoin ;",
        "il vérifie que le nom se résout et que le jeton de validation est "
        "bien servi — et le dit clairement si ce n’est pas le cas ;",
        "il fait d’abord une **répétition à blanc**, sur le serveur de "
        "test, qui ne consomme pas le quota d’émission ;",
        "il demande le certificat, branche nginx dessus et recharge le service ;",
        "il vérifie enfin que le **renouvellement automatique** fonctionne.",
    ])
    d.p("Un certificat déjà valide n’est pas réémis. En cas "
        "d’échec, le script indique l’étape fautive ; le détail "
        "complet est dans `/var/log/letsencrypt/letsencrypt.log`.")

    d.h(2, "6.3 Ensuite")
    d.p("Le certificat vaut 90 jours et est **renouvelé automatiquement** "
        "environ un mois avant l’échéance ; vous n’avez rien à "
        "faire. Deux pièges à connaître :")
    d.ul([
        "**Ne refermez pas le port 80** sur la box. Le renouvellement échouerait "
        "en silence et le site basculerait en certificat expiré trois mois plus "
        "tard.",
        "Si votre adresse IP publique change, vérifiez que le nom de domaine "
        "suit (DNS dynamique de la box).",
    ])
    d.p("Pour contrôler à tout moment :")
    d.code("""
sudo certbot certificates      # certificats installés et dates d'expiration
sudo certbot renew --dry-run   # répétition du renouvellement
""")
    d.note("**Nom de domaine gratuit et quota** : les noms du type "
           "`*.hd.free.fr` sont, du point de vue de Let’s Encrypt, un seul et "
           "même domaine partagé entre tous les abonnés. Si "
           "l’émission est refusée pour dépassement de quota "
           "(//too many certificates already issued//), il faut attendre, ou "
           "prendre un nom de domaine à soi pour quelques euros par an.")

    return d


# ------------------------------------------------------------------- écriture

def write_odt(doc, path):
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<office:document-content {NS} office:version="1.2">'
        "<office:automatic-styles>" + LIST_STYLES + "".join(doc.auto) +
        "</office:automatic-styles>"
        "<office:body><office:text>" + "".join(doc.body) +
        "</office:text></office:body></office:document-content>")

    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    meta = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" office:version="1.2">'
        "<office:meta><dc:title>DomoPi — Notice d’utilisation</dc:title>"
        "<dc:language>fr-FR</dc:language>"
        "<meta:generator>DomoPi tools/make_notice.py</meta:generator>"
        f"<meta:creation-date>{now}</meta:creation-date>"
        f"<dc:date>{now}</dc:date></office:meta></office:document-meta>")

    pics = "".join(
        f'<manifest:file-entry manifest:full-path="Pictures/{n}" '
        f'manifest:media-type="image/png"/>' for n in doc.pictures)
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" manifest:version="1.2" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
        + pics + "</manifest:manifest>")

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype : premier membre, NON compressé (exigence de la spécification)
        z.writestr(zipfile.ZipInfo("mimetype"),
                   "application/vnd.oasis.opendocument.text",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", manifest)
        z.writestr("styles.xml", STYLES)
        z.writestr("meta.xml", meta)
        z.writestr("content.xml", content)
        for nom, src in doc.pictures.items():
            # Déjà compressés : les stocker tels quels évite de gonfler le
            # temps de génération pour ~1 % de gain.
            z.writestr(f"Pictures/{nom}", src.read_bytes(),
                       compress_type=zipfile.ZIP_STORED)


if __name__ == "__main__":
    doc = build()
    cible = OUT
    # La notice est destinée à être enrichie à la main : on n'écrase jamais un
    # fichier existant sans y être forcé, la version générée est déposée à côté.
    if cible.exists() and not FORCE:
        cible = OUT.with_name(
            f"{OUT.stem}-genere-{datetime.date.today():%Y%m%d}{OUT.suffix}")
        print(f"« {OUT.name} » existe déjà et n'a pas été touché.")
        print("  (--force pour l'écraser)")
    write_odt(doc, cible)
    print(f"OK  {cible}  ({cible.stat().st_size / 1024:.1f} Ko, "
          f"{len(doc.pictures)} captures)")
    if doc.missing:
        print("captures manquantes (document généré sans elles) : "
              + ", ".join(doc.missing))
        print(f"  les produire : python .claude/skills/run-domopi/notice_shots.py")
        sys.exit(1)
