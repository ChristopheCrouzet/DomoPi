"""Export des mesures au format ODS (OpenDocument Spreadsheet), sans dépendance.

Un .ods est un zip contenant `mimetype` (stocké, non compressé, en premier),
`META-INF/manifest.xml` et `content.xml`. On écrit ce XML à la main : pas
d'odfpy ni de pandas sur le Pi (cf. CLAUDE.md, « Points d'attention Pi 2/3 »).

Deux exports par périphérique :

- « régulières » (`detailed`) : les mesures brutes telles qu'elles sont
  stockées, au pas de collecte (`poll_interval_s`), limitées à la rétention du
  brut — c'est ce que trace « Toute la courbe ».
- « synthétiques » (`summary`) : un min / moyenne / max par jour, agrégé depuis
  le brut et complété par les archives journalières (`measures_daily`), donc
  au-delà de la rétention du brut.

Les horodatages sont écrits en cellules date (heure locale du serveur, comme
l'affichage de l'application) avec un style d'affichage JJ/MM/AAAA HH:MM:SS :
les tableurs les trient et les tracent comme des dates, pas comme du texte.
"""
from __future__ import annotations

import io
import re
import time
import zipfile

from . import db

# Garde-fou mémoire (Pi) : au-delà, seules les lignes les plus récentes sont
# exportées. 200 000 lignes ~ 30 Mo de XML avant compression.
MAX_ROWS = 200_000

MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"

_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="{mt}" manifest:version="1.2"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""

_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
 office:version="1.2">
 <office:automatic-styles>
  <number:date-style style:name="NDT">
   <number:day number:style="long"/><number:text>/</number:text>
   <number:month number:style="long"/><number:text>/</number:text>
   <number:year number:style="long"/><number:text> </number:text>
   <number:hours number:style="long"/><number:text>:</number:text>
   <number:minutes number:style="long"/><number:text>:</number:text>
   <number:seconds number:style="long"/>
  </number:date-style>
  <number:date-style style:name="ND">
   <number:day number:style="long"/><number:text>/</number:text>
   <number:month number:style="long"/><number:text>/</number:text>
   <number:year number:style="long"/>
  </number:date-style>
  <style:style style:name="CDT" style:family="table-cell" style:data-style-name="NDT"/>
  <style:style style:name="CD" style:family="table-cell" style:data-style-name="ND"/>
  <style:style style:name="CH" style:family="table-cell">
   <style:text-properties fo:font-weight="bold"/></style:style>
  <style:style style:name="COL" style:family="table-column">
   <style:table-column-properties style:column-width="4.2cm"/></style:style>
 </office:automatic-styles>
 <office:body><office:spreadsheet>
"""

_TAIL = " </office:spreadsheet></office:body></office:document-content>\n"


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _cell_text(v: str, style: str = "") -> str:
    st = f' table:style-name="{style}"' if style else ""
    return (f'<table:table-cell{st} office:value-type="string">'
            f"<text:p>{_esc(v)}</text:p></table:table-cell>")


def _cell_num(v) -> str:
    if v is None:
        return "<table:table-cell/>"
    return (f'<table:table-cell office:value-type="float" office:value="{v!r}">'
            f"<text:p>{v!r}</text:p></table:table-cell>")


def _cell_date(ts: int, day_only: bool = False) -> str:
    """Cellule date à partir d'un epoch, en heure locale du serveur."""
    t = time.localtime(ts)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", t)
    shown = time.strftime("%d/%m/%Y" if day_only else "%d/%m/%Y %H:%M:%S", t)
    return (f'<table:table-cell table:style-name="{"CD" if day_only else "CDT"}" '
            f'office:value-type="date" office:date-value="{iso}">'
            f"<text:p>{shown}</text:p></table:table-cell>")


def _sheet_name(name: str) -> str:
    """Nom de feuille accepté par les tableurs (pas de []*?:/\\, 31 car. maxi)."""
    return (re.sub(r"[\[\]\*\?:/\\]", " ", name).strip() or "Mesures")[:31]


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s or "capteur"


def build_ods(sheet: str, headers: list[str], rows) -> bytes:
    """Construit le .ods. `rows` = itérable de listes de fragments de cellule."""
    parts = [_HEAD,
             f'  <table:table table:name="{_esc(_sheet_name(sheet))}">\n',
             f'   <table:table-column table:style-name="COL" '
             f'table:number-columns-repeated="{len(headers)}"/>\n',
             "   <table:table-row>" +
             "".join(_cell_text(h, "CH") for h in headers) + "</table:table-row>\n"]
    for cells in rows:
        parts.append("   <table:table-row>" + "".join(cells) + "</table:table-row>\n")
    parts.append("  </table:table>\n")
    parts.append(_TAIL)
    content = "".join(parts).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype en premier et NON compressé : exigé par la spécification ODF.
        z.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", _MANIFEST.format(mt=MIMETYPE))
        z.writestr("content.xml", content)
    return buf.getvalue()


def device_export(device: dict, kind: str) -> tuple[bytes, str, int]:
    """Rend (contenu .ods, nom de fichier, nombre de lignes) pour un périphérique."""
    name = device["name"] or f"capteur-{device['id']}"
    unit = (device["unit"] or "").strip()
    col = f"Valeur ({unit})" if unit else "Valeur"
    conn = db.get_conn()
    stamp = time.strftime("%Y%m%d-%H%M")

    if kind == "summary":
        # Un point par jour : brut agrégé + archives journalières au-delà de la
        # rétention du brut (même logique que les graphes « Min / Moy / Max »).
        pts = db.query_series(device["id"], 0, int(time.time()) + 86400, "minmax")["points"]
        rows = [[_cell_date(p["t"], True), _cell_num(p.get("min")),
                 _cell_num(p.get("avg")), _cell_num(p.get("max"))] for p in pts]
        headers = ["Jour", f"Min{f' ({unit})' if unit else ''}",
                   f"Moyenne{f' ({unit})' if unit else ''}",
                   f"Max{f' ({unit})' if unit else ''}"]
        fname = f"domopi-{_slug(name)}-syntheses-{stamp}.ods"
        return build_ods(name, headers, rows), fname, len(rows)

    total = conn.execute("SELECT COUNT(*) c FROM measures WHERE device_id=?",
                         (device["id"],)).fetchone()["c"]
    # Au-delà du garde-fou, on garde les mesures les plus récentes.
    q = ("SELECT ts, value FROM measures WHERE device_id=? "
         "ORDER BY ts DESC LIMIT ?" if total > MAX_ROWS else
         "SELECT ts, value FROM measures WHERE device_id=? ORDER BY ts LIMIT ?")
    res = conn.execute(q, (device["id"], MAX_ROWS)).fetchall()
    if total > MAX_ROWS:
        res = list(reversed(res))
    rows = [[_cell_date(r["ts"]), _cell_num(r["value"])] for r in res]
    fname = f"domopi-{_slug(name)}-mesures-{stamp}.ods"
    return build_ods(name, ["Date et heure", col], rows), fname, len(rows)
