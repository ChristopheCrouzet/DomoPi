"""Génération d'icônes SVG par IA (Claude Sonnet) — « Générer par IA » de l'admin.

Un simple appel à l'API Messages d'Anthropic, sans état côté serveur :
l'historique de conversation vit côté navigateur et est renvoyé entier à
chaque requête (bouton « Ajuster »). La clé ANTHROPIC_API_KEY vient de
l'environnement du service (/etc/domopi/domopi.env) — jamais en base, jamais
exposée au frontend. Voir CLAUDE.md (section « Génération d'icônes par IA »).
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

MODEL = "claude-sonnet-5"
MAX_TOKENS = 8000
TIMEOUT_S = 120.0
MAX_SVG_BYTES = 30_000

# Contrat de style : celui de tools/make_icons.py, que les icônes générées
# doivent suivre pour se fondre dans le jeu existant.
SYSTEM_PROMPT = """\
Tu génères des icônes SVG pour DomoPi, un serveur domotique dont le jeu \
d'icônes suit un style filaire strict. Contrat de style, à respecter à la lettre :

- balise racine : <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" \
fill="none" stroke-linecap="round" stroke-linejoin="round">
- motif centré sur (24,24), marges d'environ 4 px, lisible à 42 px ;
- traits principaux stroke-width 2.2, détails 1.5 à 1.8, fill="none" sauf \
aplats très légers (fill-opacity 0.15 à 0.2) ;
- palette exclusive : ambre #e8a13c (état actif, accents), gris #8b97a5 \
(détails, état inactif), blanc cassé #dfe6ee (structures) ;
- uniquement des éléments path, circle, rect, ellipse, line, polyline, \
polygon, g ; pas de texte, pas de dégradé, pas de script, pas d'image, \
aucune référence externe ;
- pour un périphérique à deux états, produire une paire nom_on / nom_off : \
version « on » accentuée d'ambre (halo, rayons), version « off » au contour \
gris — comme une ampoule allumée/éteinte.

Réponds toujours ainsi : une ou deux phrases d'accompagnement, puis chaque \
icône dans un bloc :

<icon name="nom_en_minuscules">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" ...>...</svg>
</icon>

Noms : minuscules, chiffres et underscore uniquement ([a-z0-9_]), en \
français, suffixes _on/_off pour les paires. 1 à 6 icônes par réponse. Si la \
demande est ambiguë, choisis une interprétation raisonnable et signale-la \
dans le texte d'accompagnement.
"""

_ICON_RE = re.compile(r'<icon\s+name="([^"]+)"\s*>\s*(.*?)\s*</icon>', re.S)
_NAME_RE = re.compile(r"[a-z0-9_]{1,48}")
_FORBIDDEN_TAGS = {"script", "foreignobject", "image", "use", "iframe",
                   "embed", "audio", "video", "animate", "set"}

_client = None


class IconAIError(Exception):
    """Erreur à présenter telle quelle à l'utilisateur (message en français)."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _get_client():
    global _client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise IconAIError(
            "Clé API Anthropic non configurée (ajouter ANTHROPIC_API_KEY dans "
            "/etc/domopi/domopi.env puis redémarrer le service)", 503)
    if _client is None:
        import anthropic  # import paresseux : pas de coût au démarrage
        _client = anthropic.AsyncAnthropic(timeout=TIMEOUT_S)
    return _client


def sanitize_svg(svg: str) -> str:
    """Valide un SVG (bien formé, viewBox 48×48, éléments sûrs) et le renvoie.

    Appliquée à la génération ET à la sauvegarde : on ne fait confiance ni au
    modèle ni au navigateur.
    """
    if not isinstance(svg, str) or not svg.strip():
        raise IconAIError("SVG vide", 400)
    if len(svg.encode("utf-8")) > MAX_SVG_BYTES:
        raise IconAIError("SVG trop volumineux", 400)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        raise IconAIError("SVG mal formé", 400)

    def local(tag):
        return tag.rsplit("}", 1)[-1].lower()

    if local(root.tag) != "svg":
        raise IconAIError("La racine n'est pas <svg>", 400)
    if (root.get("viewBox") or "").split() != ["0", "0", "48", "48"]:
        raise IconAIError("viewBox attendu : 0 0 48 48", 400)
    for el in root.iter():
        if not isinstance(el.tag, str):  # commentaires / instructions de traitement
            raise IconAIError("Contenu non graphique interdit dans le SVG", 400)
        if local(el.tag) in _FORBIDDEN_TAGS:
            raise IconAIError(f"Élément interdit dans le SVG : <{local(el.tag)}>", 400)
        for attr, val in el.attrib.items():
            a = attr.rsplit("}", 1)[-1].lower()
            if a.startswith("on") or a.endswith("href"):
                raise IconAIError("Attribut interdit dans le SVG", 400)
            if "url(" in val.lower():
                raise IconAIError("Référence interdite dans le SVG", 400)
    return svg


async def generate(messages: list[dict]) -> dict:
    """Appelle Claude et renvoie {text, icons: [{name, svg}], raw}.

    `raw` est la réponse complète du modèle (blocs <icon> compris) : le
    frontend la conserve dans l'historique pour que le modèle revoie ses
    propres SVG lors d'un « Ajuster ».
    """
    client = _get_client()
    import anthropic
    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            output_config={"effort": "high"},
            messages=messages,
        )
    except anthropic.AuthenticationError:
        raise IconAIError("Clé API Anthropic invalide ou révoquée", 503)
    except anthropic.RateLimitError:
        raise IconAIError("Limite de débit de l'API Anthropic atteinte, "
                          "réessayer dans une minute", 429)
    except anthropic.APIConnectionError:
        raise IconAIError("Impossible de joindre l'API Anthropic "
                          "(réseau/DNS du Pi)", 502)
    except anthropic.APIStatusError as e:
        raise IconAIError(f"Erreur de l'API Anthropic ({e.status_code})", 502)

    if resp.stop_reason == "refusal":
        raise IconAIError("Demande refusée par le modèle — reformuler", 400)

    raw = "".join(b.text for b in resp.content if b.type == "text")
    icons, rejected = [], 0
    for m in _ICON_RE.finditer(raw):
        name = m.group(1).strip()
        if not _NAME_RE.fullmatch(name):
            rejected += 1
            continue
        try:
            icons.append({"name": name, "svg": sanitize_svg(m.group(2))})
        except IconAIError:
            rejected += 1
    text = _ICON_RE.sub("", raw).strip()
    if rejected:
        text += f"\n({rejected} icône(s) rejetée(s) par la validation)"
    if resp.stop_reason == "max_tokens":
        text += "\n(réponse tronquée — demander moins d'icônes à la fois)"
    return {"text": text, "icons": icons, "raw": raw}
