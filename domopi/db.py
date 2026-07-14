"""DomoPi - couche base de données SQLite.

Tables :
  users            comptes (admin / reader)
  settings         clé/valeur (intervalle de polling, rétention, verbosité...)
  connectors       instances de contrôleurs (eedomus, wes_mqtt)
  devices          périphériques découverts / surveillés
  measures         mesures brutes (pas = intervalle de polling, 5 min par défaut)
  measures_daily   archives journalières min/moy/max (après purge du brut)
  pages            pages de visualisation (arborescentes, plusieurs racines)
  widgets          éléments posés sur les pages
  journal          journal applicatif horodaté
"""
import os
import sqlite3
import time
import threading

DB_PATH = os.environ.get("DOMOPI_DB", "/var/lib/domopi/domopi.db")

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'reader'          -- 'admin' | 'reader'
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connectors(
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,                          -- 'eedomus' | 'wes_mqtt'
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config TEXT NOT NULL DEFAULT '{}'            -- JSON
);
CREATE TABLE IF NOT EXISTS devices(
  id INTEGER PRIMARY KEY,
  connector_id INTEGER NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL,                   -- periph_id eedomus / unique_id MQTT
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'sensor',         -- 'sensor' | 'actuator'
  unit TEXT DEFAULT '',
  room TEXT DEFAULT '',
  monitored INTEGER NOT NULL DEFAULT 0,        -- historisé par le poller
  controllable INTEGER NOT NULL DEFAULT 0,     -- pilotage autorisé (actionneurs)
  dimmable INTEGER NOT NULL DEFAULT 0,         -- pilotage 0-100 % (gradateur, volet)
  hidden INTEGER NOT NULL DEFAULT 0,           -- non proposé pour les nouveaux widgets
  icon_on TEXT DEFAULT '',
  icon_off TEXT DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}',             -- JSON (topics MQTT, values eedomus...)
  last_value TEXT DEFAULT NULL,
  last_seen INTEGER DEFAULT NULL,              -- epoch s ; NULL = jamais vu
  UNIQUE(connector_id, external_id)
);
CREATE TABLE IF NOT EXISTS measures(
  device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL,                         -- epoch s
  value REAL NOT NULL,
  PRIMARY KEY(device_id, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS measures_daily(
  device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  day INTEGER NOT NULL,                        -- epoch s à minuit UTC
  vmin REAL NOT NULL, vavg REAL NOT NULL, vmax REAL NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY(device_id, day)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS pages(
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES pages(id) ON DELETE CASCADE,  -- NULL = racine
  title TEXT NOT NULL,
  background TEXT DEFAULT '',                  -- couleur CSS ou /backgrounds/xxx
  dual_layout INTEGER NOT NULL DEFAULT 0,      -- variantes mobile/PC séparées
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS widgets(
  id INTEGER PRIMARY KEY,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  layout TEXT NOT NULL DEFAULT 'both',         -- 'both' | 'mobile' | 'desktop'
  wtype TEXT NOT NULL,                         -- 'device' | 'graph' | 'pagelink' | 'label'
  device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
  target_page_id INTEGER REFERENCES pages(id) ON DELETE CASCADE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  options TEXT NOT NULL DEFAULT '{}'           -- JSON (taille graphe, libellé...)
);
CREATE TABLE IF NOT EXISTS journal(
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  level TEXT NOT NULL,                         -- 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
  source TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_measures_dev_ts ON measures(device_id, ts);
CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal(ts);
"""

DEFAULT_SETTINGS = {
    "poll_interval_s": "300",        # 5 minutes, réglable
    "raw_retention_days": "120",     # brut conservé N jours puis agrégé en journalier
    "journal_level": "medium",       # verbose | medium | errors
    "journal_retention_days": "30",
    "site_title": "DomoPi",
}


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    # Migrations légères pour les bases créées avec un schéma antérieur
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(devices)").fetchall()}
    if "dimmable" not in cols:
        conn.execute("ALTER TABLE devices ADD COLUMN dimmable INTEGER NOT NULL DEFAULT 0")
    if "hidden" not in cols:
        conn.execute("ALTER TABLE devices ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    conn.commit()


# ---------------------------------------------------------------- settings
def get_setting(key: str, default: str | None = None) -> str | None:
    row = get_conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


# ---------------------------------------------------------------- mesures
def store_measure(device_id: int, ts: int, value: float):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO measures(device_id,ts,value) VALUES(?,?,?)",
        (device_id, ts, value))
    conn.execute(
        "UPDATE devices SET last_value=?, last_seen=? WHERE id=?",
        (str(value), ts, device_id))
    conn.commit()


def query_series(device_id: int, t_from: int, t_to: int) -> dict:
    """Retourne la série adaptée à la fenêtre demandée.

    - fenêtre <= 4 jours  : points bruts (une courbe)
    - fenêtre  > 4 jours  : agrégat horaire min/moy/max (3 courbes)
    - fenêtre >= 15 jours : agrégat journalier min/moy/max (3 courbes)
    Les données au-delà de la rétention brute proviennent de measures_daily.
    """
    span = t_to - t_from
    conn = get_conn()
    if span <= 4 * 86400:
        rows = conn.execute(
            "SELECT ts, value FROM measures WHERE device_id=? AND ts BETWEEN ? AND ? ORDER BY ts",
            (device_id, t_from, t_to)).fetchall()
        return {"mode": "raw",
                "points": [{"t": r["ts"], "v": r["value"]} for r in rows]}

    bucket = 86400 if span >= 15 * 86400 else 3600
    rows = conn.execute(
        "SELECT (ts/?)*? AS b, MIN(value) mn, AVG(value) av, MAX(value) mx "
        "FROM measures WHERE device_id=? AND ts BETWEEN ? AND ? GROUP BY b ORDER BY b",
        (bucket, bucket, device_id, t_from, t_to)).fetchall()
    points = [{"t": r["b"], "min": r["mn"], "avg": round(r["av"], 3), "max": r["mx"]}
              for r in rows]

    if bucket == 86400:  # compléter avec les archives journalières
        arch = conn.execute(
            "SELECT day, vmin, vavg, vmax FROM measures_daily "
            "WHERE device_id=? AND day BETWEEN ? AND ? ORDER BY day",
            (device_id, t_from, t_to)).fetchall()
        seen = {p["t"] for p in points}
        for r in arch:
            if r["day"] not in seen:
                points.append({"t": r["day"], "min": r["vmin"],
                               "avg": round(r["vavg"], 3), "max": r["vmax"]})
        points.sort(key=lambda p: p["t"])
    return {"mode": "daily" if bucket == 86400 else "hourly", "points": points}


def rollup_and_purge():
    """Tâche quotidienne : archive le brut au-delà de la rétention puis le purge.

    S'applique à toutes les grandeurs mesurées indifféremment (température,
    précipitations, luminosité, pression, consommation…) : les agrégats
    min/moy/max ont un sens pour n'importe quelle grandeur physique.
    """
    conn = get_conn()
    retention = int(get_setting("raw_retention_days", "120"))
    cutoff = int(time.time()) - retention * 86400
    conn.execute(
        "INSERT OR REPLACE INTO measures_daily(device_id,day,vmin,vavg,vmax,n) "
        "SELECT device_id,(ts/86400)*86400,MIN(value),AVG(value),MAX(value),COUNT(*) "
        "FROM measures WHERE ts < ? GROUP BY device_id,(ts/86400)*86400", (cutoff,))
    conn.execute("DELETE FROM measures WHERE ts < ?", (cutoff,))
    conn.commit()


def purge_journal():
    """Tâche hebdomadaire : purge des entrées de journal au-delà de la rétention."""
    conn = get_conn()
    jret = int(get_setting("journal_retention_days", "30"))
    conn.execute("DELETE FROM journal WHERE ts < ?", (int(time.time()) - jret * 86400,))
    conn.commit()
