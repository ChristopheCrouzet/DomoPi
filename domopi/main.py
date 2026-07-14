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
    conn = db.get_conn()
    for d in found:
        conn.execute(
            "INSERT INTO devices(connector_id,external_id,name,kind,unit,room,meta,dimmable) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(connector_id,external_id) DO UPDATE SET "
            "unit=excluded.unit, room=excluded.room, meta=excluded.meta",
            (cid, d["external_id"], d["name"], d["kind"], d.get("unit", ""),
             d.get("room", ""), json.dumps(d.get("meta", {})),
             1 if d.get("dimmable") else 0))
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
        resp["server_ip"] = _lan_ip()
    return resp


# ================================================================ périphériques
@app.get("/api/devices")
async def list_devices(request: Request, monitored: int | None = None):
    auth.require_user(request)
    q = ("SELECT d.*, c.name AS connector_name, c.type AS connector_type "
         "FROM devices d JOIN connectors c ON c.id=d.connector_id")
    if monitored is not None:
        q += f" WHERE d.monitored={1 if monitored else 0}"
    rows = db.get_conn().execute(q + " ORDER BY d.room, d.name").fetchall()
    return [dict(r) | {"meta": json.loads(r["meta"])} for r in rows]


@app.put("/api/devices/{did}")
async def update_device(did: int, request: Request):
    auth.require_admin(request)
    b = await request.json()
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
    if not row:
        raise HTTPException(404, "Périphérique inconnu")
    conn.execute(
        "UPDATE devices SET name=?, monitored=?, controllable=?, dimmable=?, "
        "icon_on=?, icon_off=?, unit=?, room=? WHERE id=?",
        (b.get("name", row["name"]),
         1 if b.get("monitored", row["monitored"]) else 0,
         1 if b.get("controllable", row["controllable"]) and row["kind"] == "actuator" else 0,
         1 if b.get("dimmable", row["dimmable"]) and row["kind"] == "actuator" else 0,
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
        raise HTTPException(502, "Échec de la commande")
    return {"ok": True}


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
        "INSERT INTO pages(parent_id,title,background,dual_layout,sort_order) "
        "VALUES(?,?,?,?,?)",
        (b.get("parent_id"), b.get("title", "Nouvelle page"), b.get("background", ""),
         1 if b.get("dual_layout") else 0, int(b.get("sort_order", 0))))
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
        "UPDATE pages SET parent_id=?, title=?, background=?, dual_layout=?, "
        "sort_order=? WHERE id=?",
        (b.get("parent_id"), b.get("title", ""), b.get("background", ""),
         1 if b.get("dual_layout") else 0, int(b.get("sort_order", 0)), pid))
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
        "d.controllable, d.last_value, d.last_seen, p2.title AS target_title "
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
