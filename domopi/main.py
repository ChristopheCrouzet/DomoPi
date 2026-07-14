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

from . import db, auth, journal, poller
from .connectors import REGISTRY

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.environ.get("DOMOPI_STATIC", os.path.join(BASE_DIR, "static"))
ICONS_DIR = os.path.join(STATIC_DIR, "icons")
BACKGROUNDS_DIR = os.path.join(STATIC_DIR, "backgrounds")

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
            "journal_retention_days", "site_title"}


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
    return list(REGISTRY.keys())


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
        # La re-découverte ne vide jamais une unité renseignée (à la main ou
        # via l'unité d'une échelle) quand le connecteur n'en fournit pas.
        conn.execute(
            "INSERT INTO devices(connector_id,external_id,name,kind,unit,room,meta,"
            "dimmable,scale_id) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(connector_id,external_id) DO UPDATE SET "
            "unit=CASE WHEN excluded.unit='' THEN unit ELSE excluded.unit END, "
            "room=excluded.room, meta=excluded.meta",
            (cid, d["external_id"], d["name"], d["kind"], unit,
             d.get("room", ""), json.dumps(d.get("meta", {})),
             1 if d.get("dimmable") else 0,
             default_sid if d.get("dimmable") else None))
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


@app.put("/api/devices/{did}")
async def update_device(did: int, request: Request):
    auth.require_admin(request)
    b = await request.json()
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
    if not row:
        raise HTTPException(404, "Périphérique inconnu")
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
        "scale_id=?, hidden=?, icon_on=?, icon_off=?, unit=?, room=? WHERE id=?",
        (b.get("name", row["name"]),
         1 if b.get("monitored", row["monitored"]) else 0,
         # pilotable aussi pour un capteur (capteur virtuel d'une box)
         1 if b.get("controllable", row["controllable"]) else 0,
         1 if b.get("dimmable", row["dimmable"]) and row["kind"] == "actuator" else 0,
         sid,
         1 if b.get("hidden", row["hidden"]) else 0,
         b.get("icon_on", row["icon_on"]), b.get("icon_off", row["icon_off"]),
         b.get("unit", row["unit"]), b.get("room", row["room"]), did))
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


# ================================================================ séries
@app.get("/api/series/{did}")
async def series(did: int, request: Request, t_from: int, t_to: int):
    auth.require_user(request)
    if t_to <= t_from:
        raise HTTPException(400, "Fenêtre temporelle invalide")
    return db.query_series(did, t_from, t_to)


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
