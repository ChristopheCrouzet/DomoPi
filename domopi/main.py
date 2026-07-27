"""DomoPi — application FastAPI (API + fichiers statiques).

Lancement production : uvicorn domopi.main:app (via systemd, derrière nginx TLS).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time

from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

from . import backup, db, auth, export_ods, formula, icon_ai, journal, poller
from .connectors import REGISTRY
# Chemins des dossiers statiques : définis une seule fois, dans backup.py (qui
# les archive) et réutilisés ici (uploads, montage /static).
from .backup import STATIC_DIR, ICONS_DIR, BACKGROUNDS_DIR

app = FastAPI(title="DomoPi", docs_url=None, redoc_url=None)


@app.middleware("http")
async def static_cache_control(request: Request, call_next):
    """Force la revalidation du statique (no-cache + ETag -> 304).

    Sans Cache-Control, Safari mobile applique un cache heuristique et peut
    garder plusieurs jours d'anciens CSS/JS après une mise à jour.
    """
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.on_event("startup")
async def startup():
    db.init_db()
    auth.ensure_admin(os.environ.get("DOMOPI_ADMIN_USER", "admin"),
                      os.environ.get("DOMOPI_ADMIN_PASSWORD", "changeme"))
    asyncio.create_task(poller.run_forever())
    journal.info("system", "DomoPi démarré")


# ================================================================ session
@app.post("/api/login")
async def login(request: Request, response: Response):
    body = await request.json()
    user = auth.authenticate(body.get("username", ""), body.get("password", ""))
    if not user:
        journal.warning("auth", f"échec de connexion pour '{body.get('username','')}'")
        raise HTTPException(401, "Identifiants invalides")
    token = auth.make_token(user["id"], user["username"], user["role"])
    response.set_cookie("domopi_session", token, httponly=True, samesite="strict",
                        secure=True, max_age=auth.SESSION_TTL, path="/")
    journal.info("auth", f"connexion de {user['username']}")
    return {"username": user["username"], "role": user["role"]}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    u = auth.current_user(request)
    response.delete_cookie("domopi_session", path="/")
    if u:
        journal.info("auth", f"déconnexion de {u['n']}")
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    u = auth.current_user(request)
    return {"username": u["n"], "role": u["r"]} if u else JSONResponse({}, 401)


# ================================================================ paramètres
SETTABLE = {"poll_interval_s", "raw_retention_days", "journal_level",
            "journal_retention_days", "site_title",
            "display_sig_digits", "display_thousands_sep", "display_decimal_sep",
            "chart_ranges",
            # sauvegarde/restauration (validés par backup.check_setting)
            "backup_dir", "backup_keep", "backup_auto", "backup_next_ts",
            "backup_period", "backup_ftp_enabled", "backup_ftp_host",
            "backup_ftp_port", "backup_ftp_anon", "backup_ftp_user",
            "backup_ftp_pass", "backup_ftp_dir", "backup_ftp_pasv",
            "backup_ftp_tls"}

# Réglages d'affichage exposés aux lecteurs (le visualiseur formate les valeurs
# des tuiles avec, sans avoir accès aux réglages complets réservés à l'admin).
DISPLAY_KEYS = ("display_sig_digits", "display_thousands_sep",
                "display_decimal_sep", "chart_ranges")


def _check_chart_ranges(value: str) -> str:
    """Valide et normalise le réglage chart_ranges (JSON [{label, span_s, mode}]).

    Renvoie la forme canonique triée par durée croissante ; 400 si invalide.
    """
    try:
        lst = json.loads(value)
        assert isinstance(lst, list) and 1 <= len(lst) <= 8
        out = []
        for r in lst:
            label = str(r["label"]).strip()
            span = int(r["span_s"])
            mode = r["mode"]
            assert label and len(label) <= 12
            assert 3600 <= span <= 366 * 86400
            assert mode in ("raw", "minmax")
            out.append({"label": label, "span_s": span, "mode": mode})
        out.sort(key=lambda r: r["span_s"])
        return json.dumps(out, ensure_ascii=False)
    except (AssertionError, KeyError, TypeError, ValueError):
        raise HTTPException(400, "Paramétrage des courbes invalide : chaque durée "
                            "doit avoir un libellé (12 caractères max), une durée "
                            "entre 1 heure et 1 an et un mode d'affichage.")


@app.get("/api/display")
async def get_display(request: Request):
    auth.require_user(request)
    return {k: db.get_setting(k, db.DEFAULT_SETTINGS[k]) for k in DISPLAY_KEYS}


@app.get("/api/settings")
async def get_settings(request: Request):
    auth.require_admin(request)
    rows = db.get_conn().execute("SELECT key,value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@app.put("/api/settings")
async def put_settings(request: Request):
    auth.require_admin(request)
    body = await request.json()
    for k, v in body.items():
        if k in SETTABLE:
            if k == "chart_ranges":
                v = _check_chart_ranges(str(v))
            elif k.startswith("backup_"):
                try:
                    v = backup.check_setting(k, str(v))
                except ValueError as exc:
                    raise HTTPException(400, str(exc))
            db.set_setting(k, str(v))
    journal.info("settings", f"paramètres modifiés : {', '.join(body)}")
    return {"ok": True}


# ================================================================ utilisateurs
@app.get("/api/users")
async def list_users(request: Request):
    auth.require_admin(request)
    rows = db.get_conn().execute("SELECT id,username,role FROM users").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/users")
async def create_user(request: Request):
    auth.require_admin(request)
    b = await request.json()
    role = "admin" if b.get("role") == "admin" else "reader"
    if not b.get("username") or not b.get("password"):
        raise HTTPException(400, "username et password requis")
    try:
        db.get_conn().execute(
            "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
            (b["username"], auth.hash_password(b["password"]), role))
        db.get_conn().commit()
    except Exception:
        raise HTTPException(409, "Ce nom d'utilisateur existe déjà")
    journal.info("users", f"utilisateur créé : {b['username']} ({role})")
    return {"ok": True}


@app.delete("/api/users/{uid}")
async def delete_user(uid: int, request: Request):
    u = auth.require_admin(request)
    if uid == u["u"]:
        raise HTTPException(400, "Impossible de supprimer son propre compte")
    db.get_conn().execute("DELETE FROM users WHERE id=?", (uid,))
    db.get_conn().commit()
    return {"ok": True}


@app.put("/api/users/{uid}/password")
async def change_password(uid: int, request: Request):
    u = auth.require_user(request)
    if u["r"] != "admin" and u["u"] != uid:
        raise HTTPException(403, "Non autorisé")
    b = await request.json()
    db.get_conn().execute("UPDATE users SET password_hash=? WHERE id=?",
                          (auth.hash_password(b["password"]), uid))
    db.get_conn().commit()
    return {"ok": True}


# ================================================================ connecteurs
@app.get("/api/connector-types")
async def connector_types(request: Request):
    auth.require_admin(request)
    return [t for t in REGISTRY if t != "virtual"]   # interne, pas créable


@app.get("/api/connectors")
async def list_connectors(request: Request):
    auth.require_admin(request)
    rows = db.get_conn().execute("SELECT * FROM connectors").fetchall()
    return [dict(r) | {"config": json.loads(r["config"])} for r in rows]


@app.post("/api/connectors")
async def create_connector(request: Request):
    auth.require_admin(request)
    b = await request.json()
    if b.get("type") not in REGISTRY:
        raise HTTPException(400, "Type de connecteur inconnu")
    cur = db.get_conn().execute(
        "INSERT INTO connectors(type,name,enabled,config) VALUES(?,?,?,?)",
        (b["type"], b.get("name", b["type"]), 1 if b.get("enabled", True) else 0,
         json.dumps(b.get("config", {}))))
    db.get_conn().commit()
    journal.info("connectors", f"connecteur ajouté : {b.get('name', b['type'])}")
    return {"id": cur.lastrowid}


@app.put("/api/connectors/{cid}")
async def update_connector(cid: int, request: Request):
    auth.require_admin(request)
    b = await request.json()
    db.get_conn().execute(
        "UPDATE connectors SET name=?, enabled=?, config=? WHERE id=?",
        (b.get("name", ""), 1 if b.get("enabled", True) else 0,
         json.dumps(b.get("config", {})), cid))
    db.get_conn().commit()
    return {"ok": True}


@app.delete("/api/connectors/{cid}")
async def delete_connector(cid: int, request: Request):
    auth.require_admin(request)
    if cid == db.virtual_connector_id():
        raise HTTPException(400, "Le connecteur des capteurs virtuels ne se supprime pas")
    db.get_conn().execute("DELETE FROM connectors WHERE id=?", (cid,))
    db.get_conn().commit()
    return {"ok": True}


def _lan_ip() -> str:
    """IPv4 LAN du serveur (connexion UDP fictive : choisit juste la route)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


@app.get("/api/connectors/{cid}/discover")
async def discover(cid: int, request: Request):
    """Découverte + import (mise à jour) des périphériques du connecteur."""
    auth.require_admin(request)
    inst = poller.get_instance(cid)
    if inst is None:
        raise HTTPException(404, "Connecteur inconnu ou désactivé")
    try:
        found = await asyncio.to_thread(inst.discover)
    except Exception as exc:
        journal.error("discover", f"échec découverte connecteur {cid} : {exc}")
        raise HTTPException(502, f"Découverte impossible : {exc}")
    # Connecteurs MQTT : après une (re)connexion au broker, les annonces
    # retained du WES arrivent avec un léger délai — retenter quelques
    # secondes avant de conclure à l'absence de périphériques.
    if not found and getattr(inst, "broker_connected", None) is not None:
        for _ in range(8):
            await asyncio.sleep(1)
            found = await asyncio.to_thread(inst.discover)
            if found:
                break
    conn = db.get_conn()
    # Échelle affectée d'office aux nouveaux périphériques détectés « proportionnels »
    # (l'échelle par défaut peut avoir été supprimée : on vérifie qu'elle existe).
    ds = conn.execute("SELECT id, unit FROM scales WHERE id="
                      "(SELECT value FROM settings WHERE key='default_scale_id')").fetchone()
    default_sid = ds["id"] if ds else None
    default_unit = ds["unit"] if ds else ""
    for d in found:
        unit = d.get("unit", "") or (default_unit if d.get("dimmable") else "")
        # Échelle fournie par le connecteur (énumérations Yamaha...) :
        # retrouvée par son nom, créée si absente. Jamais mise à jour à la
        # re-découverte (les retouches utilisateur — valeurs supprimées,
        # renommages — sont conservées ; supprimer l'échelle pour repartir
        # de la liste du connecteur).
        sid = default_sid if d.get("dimmable") else None
        spec = d.get("scale")
        if spec:
            row = conn.execute("SELECT id FROM scales WHERE name=?",
                               (spec["name"],)).fetchone()
            if row:
                sid = row["id"]
            else:
                sid = conn.execute(
                    "INSERT INTO scales(name,unit,vmin,vmax,step,hide_slider,"
                    "send_delay_s,toggle_click,stops) VALUES(?,?,?,?,?,?,?,?,?)",
                    (spec["name"], spec.get("unit", ""),
                     float(spec.get("vmin", 0)), float(spec.get("vmax", 100)),
                     float(spec.get("step", 1)),
                     1 if spec.get("hide_slider") else 0,
                     float(spec.get("send_delay_s", 1.5)),
                     1 if spec.get("toggle_click") else 0,
                     json.dumps(sorted(spec.get("stops", []),
                                       key=lambda s: s["value"])))).lastrowid
        # La re-découverte ne vide jamais une unité renseignée (à la main ou
        # via l'unité d'une échelle) quand le connecteur n'en fournit pas —
        # et ne touche jamais scale_id.
        conn.execute(
            "INSERT INTO devices(connector_id,external_id,name,kind,unit,room,meta,"
            "dimmable,scale_id) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(connector_id,external_id) DO UPDATE SET "
            "unit=CASE WHEN excluded.unit='' THEN unit ELSE excluded.unit END, "
            "room=excluded.room, meta=excluded.meta",
            (cid, d["external_id"], d["name"], d["kind"], unit,
             d.get("room", ""), json.dumps(d.get("meta", {})),
             1 if d.get("dimmable") else 0, sid))
        # Le nom n'est pas écrasé à la re-découverte (renommages utilisateur),
        # sauf s'il contient U+FFFD : nom corrompu par l'ancien bug d'encodage.
        conn.execute(
            "UPDATE devices SET name=? WHERE connector_id=? AND external_id=? "
            "AND instr(name, ?) > 0",
            (d["name"], cid, d["external_id"], "�"))
    conn.commit()
    journal.info("discover", f"{len(found)} périphériques découverts (connecteur {cid})")
    resp: dict = {"count": len(found)}
    # Pour les connecteurs MQTT : état de la connexion DomoPi <-> broker et
    # IP LAN du Pi, utilisés par l'aide à la configuration côté client.
    bc = getattr(inst, "broker_connected", None)
    if bc is not None:
        resp["broker_connected"] = bc
        resp["connected_s"] = getattr(inst, "connected_s", 0)
        resp["server_ip"] = _lan_ip()
    return resp


# ================================================================ échelles
def _scale_row(b: dict) -> tuple:
    """Valide le corps d'une échelle et renvoie le tuple pour INSERT/UPDATE."""
    try:
        name = str(b.get("name", "")).strip()
        unit = str(b.get("unit") or "").strip()
        vmin = float(b.get("vmin", 0))
        vmax = float(b.get("vmax", 100))
        step = float(b.get("step", 1))
        delay = float(b.get("send_delay_s", 1.5))
        stops = [{"value": float(s.get("value")),
                  "label": str(s.get("label") or "").strip(),
                  "icon": str(s.get("icon") or "")}
                 for s in (b.get("stops") or [])]
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(400, "Données d'échelle invalides")
    if not name:
        raise HTTPException(400, "Nom de l'échelle requis")
    if vmax <= vmin:
        raise HTTPException(400, "Le maximum doit être supérieur au minimum")
    if step <= 0:
        raise HTTPException(400, "La résolution doit être positive")
    if stops and not 2 <= len(stops) <= 20:
        raise HTTPException(400, "La série doit compter de 2 à 20 valeurs (ou aucune)")
    stops.sort(key=lambda s: s["value"])
    return (name, unit, vmin, vmax, step, 1 if b.get("hide_slider") else 0,
            max(0.0, min(delay, 30.0)), 1 if b.get("toggle_click", True) else 0,
            json.dumps(stops))


@app.get("/api/scales")
async def list_scales(request: Request):
    auth.require_admin(request)
    rows = db.get_conn().execute("SELECT * FROM scales ORDER BY name").fetchall()
    return [dict(r) | {"stops": json.loads(r["stops"] or "[]")} for r in rows]


@app.post("/api/scales")
async def create_scale(request: Request):
    auth.require_admin(request)
    vals = _scale_row(await request.json())
    cur = db.get_conn().execute(
        "INSERT INTO scales(name,unit,vmin,vmax,step,hide_slider,send_delay_s,"
        "toggle_click,stops) VALUES(?,?,?,?,?,?,?,?,?)", vals)
    db.get_conn().commit()
    journal.info("scales", f"échelle créée : {vals[0]}")
    return {"id": cur.lastrowid}


@app.put("/api/scales/{sid}")
async def update_scale(sid: int, request: Request):
    auth.require_admin(request)
    vals = _scale_row(await request.json())
    db.get_conn().execute(
        "UPDATE scales SET name=?, unit=?, vmin=?, vmax=?, step=?, hide_slider=?, "
        "send_delay_s=?, toggle_click=?, stops=? WHERE id=?", vals + (sid,))
    db.get_conn().commit()
    return {"ok": True}


@app.delete("/api/scales/{sid}")
async def delete_scale(sid: int, request: Request):
    auth.require_admin(request)
    conn = db.get_conn()
    conn.execute("UPDATE devices SET scale_id=NULL WHERE scale_id=?", (sid,))
    conn.execute("DELETE FROM scales WHERE id=?", (sid,))
    conn.commit()
    journal.info("scales", f"échelle supprimée : {sid}")
    return {"ok": True}


# ================================================================ périphériques
@app.get("/api/devices")
async def list_devices(request: Request, monitored: int | None = None):
    auth.require_user(request)
    q = ("SELECT d.*, c.name AS connector_name, c.type AS connector_type, "
         "c.config AS connector_config, s.name AS s_name, s.unit AS s_unit, "
         "s.vmin AS s_vmin, s.vmax AS s_vmax, s.step AS s_step, "
         "s.hide_slider AS s_hide, s.send_delay_s AS s_delay, "
         "s.toggle_click AS s_toggle, s.stops AS s_stops "
         "FROM devices d JOIN connectors c ON c.id=d.connector_id "
         "LEFT JOIN scales s ON s.id=d.scale_id")
    if monitored is not None:
        q += f" WHERE d.monitored={1 if monitored else 0}"
    rows = db.get_conn().execute(q + " ORDER BY d.room, d.name").fetchall()
    out = []
    for r in rows:
        d = dict(r) | {"meta": json.loads(r["meta"])}
        # Seule la cadence de rafraîchissement du connecteur est exposée —
        # jamais sa config complète (identifiants).
        try:
            lrs = int(json.loads(d.pop("connector_config")).get("live_refresh_s", 10))
        except (ValueError, TypeError):
            lrs = 10
        d["live_refresh_s"] = max(0, lrs)
        # Échelle de pilotage embarquée (utilisée par le visualiseur).
        s = {k: d.pop("s_" + k, None)
             for k in ("name", "unit", "vmin", "vmax", "step", "hide", "delay",
                       "toggle", "stops")}
        if d.get("scale_id") and s["vmin"] is not None:
            try:
                stops = json.loads(s["stops"] or "[]")
            except ValueError:
                stops = []
            d["scale"] = {"id": d["scale_id"], "name": s["name"], "unit": s["unit"],
                          "vmin": s["vmin"], "vmax": s["vmax"], "step": s["step"],
                          "hide_slider": s["hide"], "send_delay_s": s["delay"],
                          "toggle_click": s["toggle"], "stops": stops}
        else:
            d["scale"] = None
        out.append(d)
    return out


@app.post("/api/devices/virtual")
async def create_virtual_device(request: Request):
    """Crée un capteur virtuel (capteur calculé par formule)."""
    auth.require_admin(request)
    b = await request.json()
    vid = db.virtual_connector_id()
    if not vid:
        raise HTTPException(500, "Connecteur virtuel absent")
    text = str(b.get("formula") or "").strip()
    if text:
        chk = formula.validate(text)
        if not chk["ok"]:
            raise HTTPException(400, f"Formule invalide : {chk['error']}")
    name = str(b.get("name") or "").strip() or "Capteur calculé"
    conn = db.get_conn()
    # Type déduit de la formule, comme à la modification : sans formule, c'est
    # une sortie (état posé à la main) ; avec formule, un capteur calculé.
    cur = conn.execute(
        "INSERT INTO devices(connector_id,external_id,name,kind,unit,room,"
        "monitored,meta) VALUES(?,?,?,?,?,?,1,?)",
        (vid, f"virt-{int(time.time() * 1000)}", name,
         "sensor" if text else "actuator",
         str(b.get("unit") or ""), str(b.get("room") or ""),
         json.dumps({"formula": text})))
    conn.commit()
    journal.info("devices", f"capteur virtuel créé : {name}")
    return {"id": cur.lastrowid}


@app.delete("/api/devices/{did}")
async def delete_device(did: int, request: Request):
    """Suppression — capteurs virtuels seulement (les périphériques d'un
    contrôleur reviendraient à la découverte suivante)."""
    auth.require_admin(request)
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
    if not row:
        raise HTTPException(404, "Périphérique inconnu")
    if row["connector_id"] != db.virtual_connector_id():
        raise HTTPException(400, "Seuls les capteurs virtuels se suppriment ici")
    conn.execute("DELETE FROM devices WHERE id=?", (did,))
    conn.commit()
    journal.info("devices", f"capteur virtuel supprimé : {row['name']}")
    return {"ok": True}


@app.post("/api/formula/check")
async def check_formula(request: Request):
    """Validation d'une formule pour l'éditeur (syntaxe + références)."""
    auth.require_admin(request)
    b = await request.json()
    return formula.validate(str(b.get("formula") or ""))


@app.put("/api/devices/{did}")
async def update_device(did: int, request: Request):
    auth.require_admin(request)
    b = await request.json()
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
    if not row:
        raise HTTPException(404, "Périphérique inconnu")
    # Formule (capteurs virtuels uniquement, clé "formula" du corps) : validée
    # avant enregistrement dans meta ; vide autorisé (capteur alors invalide).
    meta = json.loads(row["meta"] or "{}")
    is_virtual = row["connector_id"] == db.virtual_connector_id()
    if "formula" in b and is_virtual:
        text = str(b.get("formula") or "").strip()
        if text:
            chk = formula.validate(text)
            if not chk["ok"]:
                raise HTTPException(400, f"Formule invalide : {chk['error']}")
        meta["formula"] = text
    # Un capteur calculé (formule associée) ne se pilote pas : seul un capteur
    # virtuel sans formule est un état réglable à la main.
    computed = is_virtual and (meta.get("formula") or "").strip()
    # Le type d'un périphérique virtuel suit sa formule : avec formule c'est
    # un capteur (valeur calculée), sans formule c'est une sortie (état posé
    # à la main) — pilotable ou non, l'utilisateur reste libre de la case.
    kind = ("sensor" if computed else "actuator") if is_virtual else row["kind"]
    # La surveillance ne se retire pas tant qu'une fonction d'historique
    # (Deriver/Min/Max/Moy) d'un capteur virtuel s'appuie sur ce capteur.
    if row["monitored"] and not b.get("monitored", True):
        users = formula.history_users(row["name"])
        if users:
            raise HTTPException(400,
                f"Surveillance requise : « {row['name']} » alimente une fonction "
                f"d'historique (Deriver/Min/Max/Moy) de : {', '.join(users)}. "
                "Modifiez d'abord cette formule.")
    # Échelle de pilotage : l'échelle doit exister. Autorisée aussi sur les
    # capteurs (capteurs virtuels pilotables d'une box : consignes, modes...).
    sid = b.get("scale_id", row["scale_id"])
    if sid in ("", None):
        sid = None
    else:
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            raise HTTPException(400, "Échelle invalide")
        if not conn.execute("SELECT 1 FROM scales WHERE id=?", (sid,)).fetchone():
            raise HTTPException(400, "Échelle inconnue")
    conn.execute(
        "UPDATE devices SET name=?, monitored=?, controllable=?, dimmable=?, "
        "scale_id=?, hidden=?, icon_on=?, icon_off=?, unit=?, room=?, meta=?, "
        "kind=? WHERE id=?",
        (b.get("name", row["name"]),
         1 if b.get("monitored", row["monitored"]) else 0,
         # pilotable aussi pour un capteur (capteur virtuel d'une box)
         1 if b.get("controllable", row["controllable"]) and not computed else 0,
         1 if b.get("dimmable", row["dimmable"]) and kind == "actuator" else 0,
         sid,
         1 if b.get("hidden", row["hidden"]) else 0,
         b.get("icon_on", row["icon_on"]), b.get("icon_off", row["icon_off"]),
         b.get("unit", row["unit"]), b.get("room", row["room"]),
         json.dumps(meta), kind, did))
    conn.commit()
    return {"ok": True}


@app.post("/api/devices/{did}/set")
async def set_device(did: int, request: Request):
    auth.require_user(request)          # lecteurs autorisés à piloter si controllable
    b = await request.json()
    row = db.get_conn().execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
    if not row:
        raise HTTPException(404, "Périphérique inconnu")
    if not row["controllable"]:
        raise HTTPException(403, "Pilotage non autorisé pour ce périphérique")
    inst = poller.get_instance(row["connector_id"])
    if inst is None:
        raise HTTPException(502, "Connecteur indisponible")
    dev = dict(row) | {"meta": json.loads(row["meta"])}
    ok = await asyncio.to_thread(inst.set_value, dev, str(b.get("value", "")))
    if not ok:
        detail = getattr(inst, "last_error", "")
        raise HTTPException(502, f"Échec de la commande — {detail}" if detail
                            else "Échec de la commande")
    return {"ok": True}


@app.post("/api/devices/refresh")
async def refresh_devices(request: Request):
    """Rafraîchit à la demande la valeur courante de périphériques (widgets
    état+valeur affichés), y compris non surveillés. Ne stocke aucune mesure :
    l'historique reste au pas du poller."""
    auth.require_user(request)
    b = await request.json()
    ids = [int(i) for i in (b.get("ids") or [])][:60]
    if not ids:
        return []
    conn = db.get_conn()
    qmarks = ",".join("?" * len(ids))
    rows = [dict(r) | {"meta": json.loads(r["meta"])} for r in conn.execute(
        f"SELECT * FROM devices WHERE id IN ({qmarks})", ids).fetchall()]
    now = int(time.time())
    by_conn: dict[int, list] = {}
    for d in rows:
        by_conn.setdefault(d["connector_id"], []).append(d)
    for cid, devs in by_conn.items():
        inst = poller.get_instance(cid)
        if inst is None:
            continue
        try:
            values = await asyncio.to_thread(inst.poll, devs)
        except Exception as exc:
            journal.debug("refresh", f"échec rafraîchissement connecteur {cid} : {exc}")
            continue
        for d in devs:
            v = values.get(d["external_id"])
            if v is not None:
                conn.execute("UPDATE devices SET last_value=?, last_seen=? WHERE id=?",
                             (str(v), now, d["id"]))
    conn.commit()
    out = conn.execute(
        f"SELECT id,last_value,last_seen FROM devices WHERE id IN ({qmarks})",
        ids).fetchall()
    return [dict(r) for r in out]


@app.get("/api/devices/{did}/history-info")
async def device_history_info(did: int, request: Request):
    """Volume d'historique d'un périphérique (dialogue « Tester et exporter »).

    Sert à décider si les tuiles d'export et d'effacement ont un objet : un
    périphérique dé-surveillé garde son historique tant qu'on ne l'efface pas.
    """
    auth.require_admin(request)
    conn = db.get_conn()
    raw = conn.execute("SELECT COUNT(*) c, MIN(ts) a, MAX(ts) b FROM measures "
                       "WHERE device_id=?", (did,)).fetchone()
    day = conn.execute("SELECT COUNT(*) c, MIN(day) a FROM measures_daily "
                       "WHERE device_id=?", (did,)).fetchone()
    first = min([t for t in (raw["a"], day["a"]) if t], default=None)
    return {"measures": raw["c"], "daily": day["c"],
            "first_ts": first, "last_ts": raw["b"]}


@app.get("/api/devices/{did}/export")
async def export_device(did: int, request: Request, kind: str = "detailed"):
    """Export ODS de l'historique d'un périphérique (lien direct du navigateur).

    kind=detailed : mesures brutes au pas de collecte ; kind=summary : min /
    moyenne / max par jour (archives comprises). Voir export_ods.py.
    """
    auth.require_admin(request)
    if kind not in ("detailed", "summary"):
        raise HTTPException(400, "Type d'export inconnu")
    row = db.get_conn().execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
    if not row:
        raise HTTPException(404, "Périphérique inconnu")
    # Construction dans un thread : sur une grosse base, elle bloquerait
    # l'unique worker uvicorn.
    data, fname, n = await asyncio.to_thread(export_ods.device_export, dict(row), kind)
    journal.info("devices", f"export ODS « {row['name']} » "
                            f"({'synthèses' if kind == 'summary' else 'mesures'}) "
                            f": {n} ligne(s)")
    return Response(content=data, media_type=export_ods.MIMETYPE,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"',
                             "Cache-Control": "no-store"})


@app.delete("/api/devices/{did}/history")
async def delete_device_history(did: int, request: Request):
    """Efface l'historique d'un périphérique (mesures + archives journalières)."""
    auth.require_admin(request)
    row = db.get_conn().execute("SELECT name FROM devices WHERE id=?", (did,)).fetchone()
    if not row:
        raise HTTPException(404, "Périphérique inconnu")
    n_raw, n_day = await asyncio.to_thread(db.delete_history, did)
    journal.warning("devices", f"historique effacé pour « {row['name']} » : "
                               f"{n_raw} mesure(s), {n_day} jour(s) archivé(s)")
    return {"ok": True, "measures": n_raw, "daily": n_day}


# ================================================================ séries
@app.get("/api/series/{did}")
async def series(did: int, request: Request, t_from: int, t_to: int,
                 mode: str = "auto"):
    auth.require_user(request)
    if t_to <= t_from:
        raise HTTPException(400, "Fenêtre temporelle invalide")
    if mode not in ("auto", "raw", "minmax"):
        raise HTTPException(400, "Mode d'affichage invalide")
    return db.query_series(did, t_from, t_to, mode)


# ================================================================ pages/widgets
def _page_dict(r):
    return dict(r)


@app.get("/api/pages")
async def list_pages(request: Request):
    auth.require_user(request)
    rows = db.get_conn().execute(
        "SELECT * FROM pages ORDER BY parent_id, sort_order, id").fetchall()
    return [_page_dict(r) for r in rows]


@app.post("/api/pages")
async def create_page(request: Request):
    auth.require_admin(request)
    b = await request.json()
    cur = db.get_conn().execute(
        "INSERT INTO pages(parent_id,title,background,icon,dual_layout,sort_order) "
        "VALUES(?,?,?,?,?,?)",
        (b.get("parent_id"), b.get("title", "Nouvelle page"), b.get("background", ""),
         b.get("icon", ""), 1 if b.get("dual_layout") else 0,
         int(b.get("sort_order", 0))))
    db.get_conn().commit()
    journal.info("pages", f"page créée : {b.get('title','')}")
    return {"id": cur.lastrowid}


@app.put("/api/pages/{pid}")
async def update_page(pid: int, request: Request):
    auth.require_admin(request)
    b = await request.json()
    if b.get("parent_id") == pid:
        raise HTTPException(400, "Une page ne peut pas être son propre parent")
    db.get_conn().execute(
        "UPDATE pages SET parent_id=?, title=?, background=?, icon=?, "
        "dual_layout=?, sort_order=? WHERE id=?",
        (b.get("parent_id"), b.get("title", ""), b.get("background", ""),
         b.get("icon", ""), 1 if b.get("dual_layout") else 0,
         int(b.get("sort_order", 0)), pid))
    db.get_conn().commit()
    return {"ok": True}


@app.delete("/api/pages/{pid}")
async def delete_page(pid: int, request: Request):
    auth.require_admin(request)
    db.get_conn().execute("DELETE FROM pages WHERE id=?", (pid,))
    db.get_conn().commit()
    return {"ok": True}


@app.get("/api/pages/{pid}/widgets")
async def list_widgets(pid: int, request: Request):
    auth.require_user(request)
    rows = db.get_conn().execute(
        "SELECT w.*, d.name AS device_name, d.kind, d.unit, d.icon_on, d.icon_off, "
        "d.controllable, d.last_value, d.last_seen, p2.title AS target_title, "
        "p2.icon AS target_icon "
        "FROM widgets w LEFT JOIN devices d ON d.id=w.device_id "
        "LEFT JOIN pages p2 ON p2.id=w.target_page_id "
        "WHERE w.page_id=? ORDER BY w.sort_order, w.id", (pid,)).fetchall()
    return [dict(r) | {"options": json.loads(r["options"])} for r in rows]


@app.post("/api/pages/{pid}/widgets")
async def create_widget(pid: int, request: Request):
    auth.require_admin(request)
    b = await request.json()
    cur = db.get_conn().execute(
        "INSERT INTO widgets(page_id,layout,wtype,device_id,target_page_id,"
        "sort_order,options) VALUES(?,?,?,?,?,?,?)",
        (pid, b.get("layout", "both"), b.get("wtype", "device"), b.get("device_id"),
         b.get("target_page_id"), int(b.get("sort_order", 0)),
         json.dumps(b.get("options", {}))))
    db.get_conn().commit()
    return {"id": cur.lastrowid}


@app.put("/api/widgets/{wid}")
async def update_widget(wid: int, request: Request):
    auth.require_admin(request)
    b = await request.json()
    db.get_conn().execute(
        "UPDATE widgets SET layout=?, wtype=?, device_id=?, target_page_id=?, "
        "sort_order=?, options=? WHERE id=?",
        (b.get("layout", "both"), b.get("wtype", "device"), b.get("device_id"),
         b.get("target_page_id"), int(b.get("sort_order", 0)),
         json.dumps(b.get("options", {})), wid))
    db.get_conn().commit()
    return {"ok": True}


@app.delete("/api/widgets/{wid}")
async def delete_widget(wid: int, request: Request):
    auth.require_admin(request)
    db.get_conn().execute("DELETE FROM widgets WHERE id=?", (wid,))
    db.get_conn().commit()
    return {"ok": True}


# ================================================================ journal
@app.get("/api/journal")
async def get_journal(request: Request, limit: int = 200, level: str = ""):
    auth.require_user(request)
    q = "SELECT ts,level,source,message FROM journal"
    args: list = []
    if level in ("DEBUG", "INFO", "WARNING", "ERROR"):
        q += " WHERE level=?"
        args.append(level)
    q += " ORDER BY ts DESC, id DESC LIMIT ?"
    args.append(min(limit, 2000))
    return [dict(r) for r in db.get_conn().execute(q, args).fetchall()]


# ================================================================ sauvegardes
# Sauvegarde et restauration tournent dans un thread (verrou dans backup.py) :
# l'unique worker uvicorn reste disponible pendant l'opération, dont l'avancement
# est publié par backup.job_state() et lu par l'admin via GET /api/backups.
async def _run_bg(fn, *args):
    """Lance une opération de sauvegarde/restauration en tâche de fond.

    L'erreur n'est pas propagée (personne n'attend la réponse) : elle est déjà
    consignée dans l'état du job et dans le journal applicatif.
    """
    try:
        await asyncio.to_thread(fn, *args)
    except backup.BackupError:
        pass


@app.get("/api/backups")
async def list_backups(request: Request):
    """Archives présentes, dossier utilisé et avancement de l'opération en cours."""
    auth.require_admin(request)
    return {"dir": backup.backup_dir(),
            "default_dir": backup.default_backup_dir(),
            "periods": {k: v[0] for k, v in backup.PERIODS.items()},
            "max_upload_mb": backup.MAX_UPLOAD_MB,
            "entries": backup.list_backups(),
            "job": backup.job_state()}


@app.post("/api/backups/run")
async def run_backup_now(request: Request):
    auth.require_admin(request)
    if backup.job_state()["running"]:
        raise HTTPException(409, "Une sauvegarde ou une restauration est déjà en cours")
    asyncio.create_task(_run_bg(backup.run_backup, "manuelle"))
    return {"started": True}


@app.post("/api/backups/upload")
async def upload_backup(request: Request, name: str = ""):
    """Dépose une archive venue du poste client dans le dossier de sauvegarde.

    L'archive arrive en **corps brut** (le nom de fichier en paramètre `name`),
    et non en multipart : avec `UploadFile`, FastAPI analyse et met en cache le
    corps entier **avant** d'exécuter la fonction, donc avant le contrôle
    d'accès — un appelant non authentifié pouvait faire écrire jusqu'à
    MAX_UPLOAD_MB sur la carte SD avant de récolter son 401. Ici, on refuse
    avant d'avoir lu un seul octet.
    """
    auth.require_admin(request)
    try:
        part, dest = backup.upload_paths(name)
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc))
    size, limit = 0, backup.MAX_UPLOAD_MB * 1024 * 1024
    try:
        with open(part, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        413, f"Archive trop volumineuse (max {backup.MAX_UPLOAD_MB} Mo)")
                out.write(chunk)
    except HTTPException:
        backup.abort_upload(part)
        raise
    except (OSError, ClientDisconnect) as exc:
        backup.abort_upload(part)
        raise HTTPException(500, f"Envoi interrompu : {exc}")
    try:
        return await asyncio.to_thread(backup.finish_upload, part, dest)
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/backups/{name}/download")
async def download_backup(name: str, request: Request):
    auth.require_admin(request)
    try:
        path = backup.archive_path(name)
    except backup.BackupError as exc:
        raise HTTPException(404, str(exc))
    return FileResponse(path, media_type="application/gzip", filename=name)


@app.delete("/api/backups/{name}")
async def delete_backup(name: str, request: Request):
    auth.require_admin(request)
    try:
        backup.delete_backup(name)
    except backup.BackupError as exc:
        raise HTTPException(404, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"Suppression impossible : {exc}")
    return {"ok": True}


@app.post("/api/backups/ftp-test")
async def test_ftp(request: Request):
    """Teste la configuration FTP d'export (connexion, identifiants, écriture)."""
    auth.require_admin(request)
    try:
        return await asyncio.to_thread(backup.ftp_test)
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/restore")
async def restore(request: Request):
    """Restauration depuis une archive du dossier de sauvegarde.

    Body : {name, icons, history, full, keep_history, secret} — cf. backup.py.
    """
    auth.require_admin(request)
    b = await request.json()
    if backup.job_state()["running"]:
        raise HTTPException(409, "Une sauvegarde ou une restauration est déjà en cours")
    opts = {k: bool(b.get(k)) for k in ("icons", "history", "full",
                                        "keep_history", "secret")}
    try:
        backup.archive_path(str(b.get("name") or ""))
        if not any(opts[k] for k in ("icons", "history", "full")):
            raise backup.BackupError("Rien à restaurer : cochez au moins un élément")
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc))
    asyncio.create_task(_run_bg(backup.run_restore, str(b["name"]), opts))
    return {"started": True}


# ================================================================ icônes/fonds
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")


def _list_files(folder, exts):
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder)
                  if os.path.splitext(f)[1].lower() in exts)


@app.get("/api/icons")
async def list_icons(request: Request):
    auth.require_user(request)
    return _list_files(ICONS_DIR, {".svg", ".png"})


@app.get("/api/backgrounds")
async def list_backgrounds(request: Request):
    auth.require_user(request)
    return _list_files(BACKGROUNDS_DIR, {".jpg", ".jpeg", ".png", ".webp", ".svg"})


async def _upload(folder: str, file: UploadFile, exts: set, max_mb: int):
    name = os.path.basename(file.filename or "")
    if not _SAFE_NAME.match(name) or os.path.splitext(name)[1].lower() not in exts:
        raise HTTPException(400, f"Nom ou format de fichier invalide ({', '.join(exts)})")
    data = await file.read()
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(413, f"Fichier trop volumineux (max {max_mb} Mo)")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)
    return name


@app.post("/api/icons/upload")
async def upload_icon(request: Request, file: UploadFile = File(...)):
    auth.require_admin(request)
    name = await _upload(ICONS_DIR, file, {".svg", ".png"}, 1)
    journal.info("icons", f"icône ajoutée : {name}")
    return {"name": name}


@app.post("/api/icons/generate")
async def generate_icons_ai(request: Request):
    """Prévisualisation d'icônes générées par IA — aucune écriture disque.

    Body : {messages: [{role, content}]} — l'historique complet, tenu par le
    navigateur (stateless côté serveur, voir icon_ai.py).
    """
    auth.require_admin(request)
    body = await request.json()
    messages = body.get("messages")
    if not isinstance(messages, list) or not (1 <= len(messages) <= 40):
        raise HTTPException(400, "Historique de conversation invalide")
    clean = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) \
                or not content.strip() or len(content) > 200_000:
            raise HTTPException(400, "Historique de conversation invalide")
        clean.append({"role": role, "content": content})
    if clean[0]["role"] != "user":
        raise HTTPException(400, "Historique de conversation invalide")
    try:
        result = await icon_ai.generate(clean)
    except icon_ai.IconAIError as e:
        raise HTTPException(e.status, str(e))
    journal.info("icons",
                 f"génération IA : {len(result['icons'])} icône(s) proposée(s)")
    return result


@app.post("/api/icons/generate/save")
async def save_generated_icons(request: Request):
    """Enregistre dans static/icons/ des icônes validées par l'utilisateur."""
    auth.require_admin(request)
    body = await request.json()
    items = body.get("icons")
    if not isinstance(items, list) or not (1 <= len(items) <= 12):
        raise HTTPException(400, "Liste d'icônes invalide")
    to_write, seen = [], set()
    for it in items:
        name = str(it.get("name") or "").strip()
        if name.lower().endswith(".svg"):
            name = name[:-4]
        if not _SAFE_NAME.match(name):
            raise HTTPException(400, f"Nom d'icône invalide : « {name} »")
        if name in seen:
            raise HTTPException(400, f"Nom en double : « {name} »")
        seen.add(name)
        try:
            svg = icon_ai.sanitize_svg(it.get("svg"))
        except icon_ai.IconAIError as e:
            raise HTTPException(e.status, f"« {name} » : {e}")
        path = os.path.join(ICONS_DIR, name + ".svg")
        if os.path.exists(path):
            raise HTTPException(409, f"L'icône « {name} » existe déjà")
        to_write.append((name, path, svg))
    os.makedirs(ICONS_DIR, exist_ok=True)
    names = []
    for name, path, svg in to_write:
        # newline="\n" : même sortie sous Windows et Linux (cf. make_icons.py)
        with open(path, "w", newline="\n", encoding="utf-8") as f:
            f.write(svg if svg.endswith("\n") else svg + "\n")
        names.append(name + ".svg")
    journal.info("icons", "icônes IA ajoutées : " + ", ".join(names))
    return {"names": names}


@app.post("/api/backgrounds/upload")
async def upload_background(request: Request, file: UploadFile = File(...)):
    auth.require_admin(request)
    name = await _upload(BACKGROUNDS_DIR, file,
                         {".jpg", ".jpeg", ".png", ".webp", ".svg"}, 5)
    journal.info("backgrounds", f"fond ajouté : {name}")
    return {"name": name}


# ================================================================ statique
@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
