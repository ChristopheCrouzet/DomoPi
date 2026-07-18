"""Moteur de formules des capteurs virtuels.

Une formule combine des constantes numériques (point décimal), des références
de capteurs entre accolades — ``{Nom du capteur}``, insensibles à la casse,
aux accents et aux espaces superflus — les opérateurs ``+ - * /``, des
parenthèses, et des fonctions d'historique :

  Deriver({capteur}, durée)   dérivée : variation par heure, calculée entre la
                              dernière mesure et celle d'il y a « durée »
                              (6min à 24h). Ex : puissance kW depuis un
                              compteur d'énergie kWh avec durée = 1h.
  Min/Max/Moy({capteur}, plage)
                              agrégat sur une plage : glissante (``2h``,
                              ``30min``, 168h maxi), ``heure`` (heure
                              courante), ``jour`` (journée courante) ou
                              ``hier`` (journée d'hier complète, minuit à
                              minuit — heure locale).

Les durées s'écrivent nombre + unité ``h`` (défaut) ou ``min``. Le séparateur
d'arguments est ``,`` ou ``;``. Les fonctions exigent un capteur *surveillé*
(elles lisent la table ``measures``) ; une référence simple lit la dernière
valeur connue, même non historisée.

Toute impossibilité de calcul (division par zéro, référence inconnue ou non
numérique, historique insuffisant) donne ``NaN`` : le poller l'affiche
« invalide » et n'historise pas (lever de crayon sur les graphes).
"""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata

from . import db

NAN = float("nan")

DERIVE_MIN_H = 0.1      # 6 minutes
DERIVE_MAX_H = 24.0
SLIDING_MAX_H = 168.0   # plage glissante maxi des agrégats (7 jours)

# Fonctions : nom normalisé -> (libellé canonique, agrégat SQL ou "deriver")
FUNCS = {"deriver": "deriver", "max": "MAX", "min": "MIN", "moy": "AVG"}


class FormulaError(ValueError):
    def __init__(self, msg: str, pos: int = 0):
        super().__init__(msg)
        self.pos = pos


def _norm(s: str) -> str:
    """Normalise un nom : accents retirés, espaces réduits, casse ignorée."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


# ---------------------------------------------------------------- analyse
_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<ref>\{[^{}]*\})"
    r"|(?P<ident>[^\W\d_][\w]*)"
    r"|(?P<op>[-+*/(),;]))", re.UNICODE)


def _tokenize(text: str) -> list[tuple[str, str, int]]:
    toks, i = [], 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if not m:
            if text[i:].strip():
                raise FormulaError(f"caractère inattendu « {text[i:].strip()[0]} »", i)
            break
        for kind in ("num", "ref", "ident", "op"):
            v = m.group(kind)
            if v is not None:
                toks.append((kind, v, m.start(kind)))
                break
        i = m.end()
    return toks


class _Parser:
    """Descente récursive -> AST en tuples :
    ('num', v) ('ref', nom) ('neg', x) ('bin', op, g, d)
    ('deriver', nom, heures) ('agg', 'MIN|AVG|MAX', nom, spec)
    spec = ('dur', heures) | ('cal', 'heure'|'jour')
    """

    def __init__(self, text: str):
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else ("end", "", len(self.text))

    def _next(self):
        t = self._peek()
        self.i += 1
        return t

    def _expect_op(self, op: str, what: str):
        kind, v, pos = self._next()
        if kind != "op" or v != op:
            raise FormulaError(f"« {op} » attendu {what}", pos)

    def parse(self):
        node = self.expr()
        kind, v, pos = self._peek()
        if kind != "end":
            raise FormulaError(f"élément inattendu « {v} »", pos)
        return node

    def expr(self):
        node = self.term()
        while True:
            kind, v, _ = self._peek()
            if kind == "op" and v in "+-":
                self.i += 1
                node = ("bin", v, node, self.term())
            else:
                return node

    def term(self):
        node = self.factor()
        while True:
            kind, v, _ = self._peek()
            if kind == "op" and v in "*/":
                self.i += 1
                node = ("bin", v, node, self.factor())
            else:
                return node

    def factor(self):
        kind, v, pos = self._next()
        if kind == "op" and v == "-":
            return ("neg", self.factor())
        if kind == "op" and v == "+":
            return self.factor()
        if kind == "num":
            return ("num", float(v))
        if kind == "ref":
            name = v[1:-1].strip()
            if not name:
                raise FormulaError("référence vide {}", pos)
            return ("ref", name)
        if kind == "op" and v == "(":
            node = self.expr()
            self._expect_op(")", "pour fermer la parenthèse")
            return node
        if kind == "ident":
            fn = FUNCS.get(_norm(v))
            if fn is None:
                raise FormulaError(
                    f"fonction inconnue « {v} » (Deriver, Min, Max, Moy)", pos)
            self._expect_op("(", f"après {v}")
            rkind, rv, rpos = self._next()
            if rkind != "ref":
                raise FormulaError(
                    f"{v} attend un capteur {{...}} en premier argument", rpos)
            name = rv[1:-1].strip()
            if not name:
                raise FormulaError("référence vide {}", rpos)
            sep, sv, spos = self._next()
            if sep != "op" or sv not in (",", ";"):
                raise FormulaError("« , » attendu entre les arguments", spos)
            spec = self._range(fn)
            self._expect_op(")", f"pour fermer {v}(...)")
            if fn == "deriver":
                return ("deriver", name, spec[1])
            return ("agg", fn, name, spec)
        raise FormulaError("nombre, {capteur}, fonction ou parenthèse attendu", pos)

    def _range(self, fn: str):
        """Durée « 1h » / « 30min » / « 1.5 h », ou mot-clé heure / jour."""
        kind, v, pos = self._next()
        if kind == "ident":
            key = _norm(v)
            if key in ("heure", "jour", "hier"):
                if fn == "deriver":
                    raise FormulaError(
                        "Deriver attend une durée (ex : 1h, 30min)", pos)
                return ("cal", key)
            raise FormulaError(f"plage invalide « {v} » (durée, heure, jour ou hier)", pos)
        if kind != "num":
            raise FormulaError(
                "durée ou plage attendue (ex : 1h, 30min, heure, jour, hier)", pos)
        hours = float(v)
        ukind, uv, upos = self._peek()
        if ukind == "ident":
            u = _norm(uv)
            if u not in ("h", "min"):
                raise FormulaError(f"unité inconnue « {uv} » (h ou min)", upos)
            self.i += 1
            if u == "min":
                hours /= 60.0
        if fn == "deriver" and not DERIVE_MIN_H <= hours <= DERIVE_MAX_H:
            raise FormulaError("durée de Deriver entre 6min et 24h", pos)
        if fn != "deriver" and not 0 < hours <= SLIDING_MAX_H:
            raise FormulaError("plage glissante entre 0 (exclu) et 168h", pos)
        return ("dur", hours)


def parse(text: str):
    return _Parser(text).parse()


def collect_refs(ast) -> list[tuple[str, bool]]:
    """[(nom, besoin_d_historique)] — un nom peut apparaître plusieurs fois."""
    out: list[tuple[str, bool]] = []
    def walk(n):
        if n[0] == "ref":
            out.append((n[1], False))
        elif n[0] == "deriver":
            out.append((n[1], True))
        elif n[0] == "agg":
            out.append((n[2], True))
        elif n[0] == "neg":
            walk(n[1])
        elif n[0] == "bin":
            walk(n[2]); walk(n[3])
    walk(ast)
    return out


def validate(text: str) -> dict:
    """Contrôle complet pour l'éditeur : syntaxe + résolution des références.

    Retourne {ok, error?, pos?, refs: [{name, monitored, found}], warnings: []}.
    """
    if not text.strip():
        return {"ok": False, "error": "formule vide", "pos": 0, "refs": [], "warnings": []}
    try:
        ast = parse(text)
    except FormulaError as e:
        return {"ok": False, "error": str(e), "pos": e.pos, "refs": [], "warnings": []}
    rows = db.get_conn().execute("SELECT id,name,monitored FROM devices").fetchall()
    by_name: dict[str, list] = {}
    for r in rows:
        by_name.setdefault(_norm(r["name"]), []).append(r)
    refs, warnings, error = [], [], None
    seen = set()
    for name, hist in collect_refs(ast):
        if (_norm(name), hist) in seen:
            continue
        seen.add((_norm(name), hist))
        matches = by_name.get(_norm(name), [])
        refs.append({"name": name, "found": len(matches) == 1,
                     "monitored": bool(matches and matches[0]["monitored"])})
        if not matches:
            error = error or f"capteur inconnu : {{{name}}}"
        elif len(matches) > 1:
            error = error or (f"nom ambigu : {{{name}}} correspond à "
                              f"{len(matches)} périphériques (renommez-en un)")
        elif hist and not matches[0]["monitored"]:
            error = error or (f"{{{name}}} doit être surveillé (Surv.) pour "
                              "les fonctions d'historique")
        elif not matches[0]["monitored"]:
            warnings.append(f"{{{name}}} n'est pas surveillé : sa valeur ne se "
                            "rafraîchit que lorsqu'il est affiché")
    if error:
        return {"ok": False, "error": error, "pos": 0, "refs": refs, "warnings": warnings}
    return {"ok": True, "refs": refs, "warnings": warnings}


def history_users(name: str) -> list[str]:
    """Capteurs virtuels dont une fonction d'historique (Deriver/Min/Max/Moy)
    référence `name` — la surveillance de ce capteur ne doit pas être retirée."""
    vid = db.virtual_connector_id()
    if not vid:
        return []
    key = _norm(name)
    out = []
    for r in db.get_conn().execute(
            "SELECT name, meta FROM devices WHERE connector_id=?", (vid,)).fetchall():
        try:
            text = json.loads(r["meta"] or "{}").get("formula") or ""
            ast = parse(text) if text.strip() else None
        except (ValueError, FormulaError):
            continue
        if ast and any(hist and _norm(n) == key for n, hist in collect_refs(ast)):
            out.append(r["name"])
    return out


# ---------------------------------------------------------------- évaluation
class Resolver:
    """Contexte d'évaluation d'un cycle : résolution des noms et historique.

    Construit une fois par cycle de collecte (une requête sur devices), puis
    partagé par toutes les formules du cycle.
    """

    def __init__(self):
        self.now = int(time.time())
        self.by_name: dict[str, list] = {}
        for r in db.get_conn().execute(
                "SELECT id,name,last_value FROM devices").fetchall():
            self.by_name.setdefault(_norm(r["name"]), []).append(r)

    def _dev(self, name: str):
        m = self.by_name.get(_norm(name), [])
        return m[0] if len(m) == 1 else None

    def current(self, name: str) -> float:
        d = self._dev(name)
        if d is None:
            return NAN
        try:
            return float(str(d["last_value"]).replace(",", "."))
        except (TypeError, ValueError):
            return NAN

    def deriver(self, name: str, hours: float) -> float:
        d = self._dev(name)
        if d is None:
            return NAN
        conn = db.get_conn()
        r1 = conn.execute("SELECT ts,value FROM measures WHERE device_id=? "
                          "ORDER BY ts DESC LIMIT 1", (d["id"],)).fetchone()
        span = hours * 3600
        tol = max(900.0, span / 4)
        # dernière mesure trop vieille = dérivée périmée -> invalide
        if r1 is None or self.now - r1["ts"] > tol:
            return NAN
        target = r1["ts"] - span
        r0 = conn.execute("SELECT ts,value FROM measures WHERE device_id=? "
                          "AND ts<=? ORDER BY ts DESC LIMIT 1",
                          (d["id"], int(target))).fetchone()
        if r0 is None or target - r0["ts"] > tol or r1["ts"] <= r0["ts"]:
            return NAN
        return (r1["value"] - r0["value"]) / ((r1["ts"] - r0["ts"]) / 3600.0)

    def agg(self, fn: str, name: str, spec) -> float:
        d = self._dev(name)
        if d is None:
            return NAN
        t1 = self.now
        if spec[0] == "dur":
            t0 = self.now - int(spec[1] * 3600)
        else:  # plages calendaires, en heure locale
            lt = time.localtime(self.now)
            day = self.now - lt.tm_hour * 3600 - lt.tm_min * 60 - lt.tm_sec
            if spec[1] == "heure":
                t0 = self.now - lt.tm_min * 60 - lt.tm_sec
            elif spec[1] == "jour":
                t0 = day
            else:  # hier : la journée d'hier complète, minuit à minuit
                t0, t1 = day - 86400, day - 1
        row = db.get_conn().execute(
            f"SELECT {fn}(value) v, COUNT(*) n FROM measures "
            "WHERE device_id=? AND ts BETWEEN ? AND ?",
            (d["id"], t0, t1)).fetchone()
        return row["v"] if row["n"] else NAN


def evaluate(ast, ctx: Resolver) -> float:
    """Évalue l'AST ; toute impossibilité donne NaN (jamais d'exception)."""
    k = ast[0]
    if k == "num":
        return ast[1]
    if k == "ref":
        return ctx.current(ast[1])
    if k == "neg":
        return -evaluate(ast[1], ctx)
    if k == "bin":
        left = evaluate(ast[2], ctx)
        right = evaluate(ast[3], ctx)
        if ast[1] == "+":
            return left + right
        if ast[1] == "-":
            return left - right
        if ast[1] == "*":
            return left * right
        return left / right if right else NAN
    if k == "deriver":
        return ctx.deriver(ast[1], ast[2])
    return ctx.agg(ast[1], ast[2], ast[3])


def compute(text: str, ctx: Resolver, cache: dict | None = None) -> float:
    """Formule -> valeur ; NaN si vide, invalide ou incalculable."""
    if not text.strip():
        return NAN
    if cache is not None and text in cache:
        ast = cache[text]
    else:
        try:
            ast = parse(text)
        except FormulaError:
            ast = None
        if cache is not None:
            if len(cache) > 200:
                cache.clear()
            cache[text] = ast
    if ast is None:
        return NAN
    try:
        v = float(evaluate(ast, ctx))
        return v if math.isfinite(v) else NAN   # inf (dépassement) = invalide aussi
    except (ArithmeticError, TypeError, ValueError):
        return NAN
