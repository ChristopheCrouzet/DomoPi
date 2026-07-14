"""Authentification DomoPi — stdlib uniquement (PBKDF2 + cookie signé HMAC)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Request, HTTPException

from . import db

SECRET_PATH = os.environ.get("DOMOPI_SECRET", "/var/lib/domopi/secret.key")
SESSION_TTL = 12 * 3600
_secret: bytes | None = None


def _get_secret() -> bytes:
    global _secret
    if _secret is None:
        os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
        if os.path.exists(SECRET_PATH):
            _secret = open(SECRET_PATH, "rb").read()
        else:
            _secret = secrets.token_bytes(32)
            with open(SECRET_PATH, "wb") as f:
                f.write(_secret)
            os.chmod(SECRET_PATH, 0o600)
    return _secret


# ------------------------------------------------------------ mots de passe
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


# ------------------------------------------------------------ sessions
def make_token(user_id: int, username: str, role: str) -> str:
    payload = json.dumps({"u": user_id, "n": username, "r": role,
                          "e": int(time.time()) + SESSION_TTL}).encode()
    sig = hmac.new(_get_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload).decode() + "." + \
        base64.urlsafe_b64encode(sig).decode()


def parse_token(token: str) -> dict | None:
    try:
        p64, s64 = token.split(".")
        payload = base64.urlsafe_b64decode(p64)
        sig = base64.urlsafe_b64decode(s64)
        if not hmac.compare_digest(
                sig, hmac.new(_get_secret(), payload, hashlib.sha256).digest()):
            return None
        data = json.loads(payload)
        if data.get("e", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def current_user(request: Request) -> dict | None:
    token = request.cookies.get("domopi_session")
    return parse_token(token) if token else None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Connexion requise")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user.get("r") != "admin":
        raise HTTPException(403, "Réservé à l'administrateur")
    return user


def authenticate(username: str, password: str) -> dict | None:
    row = db.get_conn().execute(
        "SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row and verify_password(password, row["password_hash"]):
        return {"id": row["id"], "username": row["username"], "role": row["role"]}
    return None


def ensure_admin(username: str, password: str):
    """Crée le compte admin initial s'il n'existe aucun utilisateur."""
    conn = db.get_conn()
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        conn.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'admin')",
                     (username, hash_password(password)))
        conn.commit()
