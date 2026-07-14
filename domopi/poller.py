"""Collecteur DomoPi.

Boucle principale : toutes les `poll_interval_s` secondes (300 s par défaut,
réglable dans les paramètres), interroge chaque connecteur actif pour tous
ses périphériques surveillés et historise les valeurs numériques.
Une tâche quotidienne archive/purge le brut au-delà de la rétention.
"""
from __future__ import annotations

import asyncio
import json
import time

from . import db, journal
from .connectors import REGISTRY

_instances: dict[int, object] = {}   # connector_id -> instance démarrée


def get_instance(connector_id: int):
    """Instance (re)construite depuis la conf en base, démarrée si besoin."""
    row = db.get_conn().execute(
        "SELECT * FROM connectors WHERE id=? AND enabled=1", (connector_id,)).fetchone()
    if row is None:
        inst = _instances.pop(connector_id, None)
        if inst:
            inst.stop()
        return None
    inst = _instances.get(connector_id)
    cfg = json.loads(row["config"])
    if inst is None or inst.config != cfg or inst.name != row["name"]:
        if inst:
            inst.stop()
        cls = REGISTRY.get(row["type"])
        if cls is None:
            journal.error("poller", f"type de connecteur inconnu : {row['type']}")
            return None
        inst = cls(row["id"], row["name"], cfg)
        inst.start()
        _instances[connector_id] = inst
    return inst


def poll_once():
    conn = db.get_conn()
    now = int(time.time())
    for c in conn.execute("SELECT id,name FROM connectors WHERE enabled=1").fetchall():
        inst = get_instance(c["id"])
        if inst is None:
            continue
        devices = [dict(d) | {"meta": json.loads(d["meta"])} for d in conn.execute(
            "SELECT * FROM devices WHERE connector_id=? AND monitored=1",
            (c["id"],)).fetchall()]
        if not devices:
            continue
        try:
            values = inst.poll(devices)
        except Exception as exc:
            journal.error(c["name"], f"échec du cycle de collecte : {exc}")
            continue
        for d in devices:
            v = values.get(d["external_id"])
            if v is None:
                continue
            journal.debug(c["name"], f"{d['name']} = {v}")
            try:
                db.store_measure(d["id"], now, float(str(v).replace(",", ".")))
            except ValueError:
                # valeur non numérique (état texte) : on met à jour l'état courant
                conn.execute("UPDATE devices SET last_value=?, last_seen=? WHERE id=?",
                             (str(v), now, d["id"]))
                conn.commit()
    journal.debug("poller", "cycle de collecte terminé")


async def run_forever():
    db.init_db()
    journal.info("poller", "démarrage du collecteur")
    last_rollup = 0.0
    last_journal_purge = 0.0
    while True:
        started = time.time()
        try:
            await asyncio.to_thread(poll_once)
        except Exception as exc:
            journal.error("poller", f"erreur inattendue : {exc}")
        # Archivage/purge des mesures : quotidien
        if started - last_rollup > 86400:
            try:
                await asyncio.to_thread(db.rollup_and_purge)
                last_rollup = started
                journal.info("poller", "archivage/purge des mesures effectué")
            except Exception as exc:
                journal.error("poller", f"échec archivage : {exc}")
        # Purge du journal : hebdomadaire
        if started - last_journal_purge > 7 * 86400:
            try:
                await asyncio.to_thread(db.purge_journal)
                last_journal_purge = started
                journal.info("poller", "purge hebdomadaire du journal effectuée")
            except Exception as exc:
                journal.error("poller", f"échec purge du journal : {exc}")
        interval = max(30, int(db.get_setting("poll_interval_s", "300")))
        await asyncio.sleep(max(5, interval - (time.time() - started)))
