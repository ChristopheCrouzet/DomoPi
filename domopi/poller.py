"""Collecteur DomoPi.

Boucle principale : toutes les `poll_interval_s` secondes (300 s par défaut,
réglable dans les paramètres), interroge chaque connecteur actif pour tous
ses périphériques surveillés et historise les valeurs numériques.
Une tâche quotidienne archive/purge le brut au-delà de la rétention.
"""
from __future__ import annotations

import asyncio
import json
import math
import time

from . import backup, db, journal
from .connectors import REGISTRY

_instances: dict[int, object] = {}   # connector_id -> instance démarrée


def reset_instances():
    """Arrête et oublie les instances en cache (après restauration de la base :
    les contrôleurs et leurs configurations viennent de changer en bloc)."""
    for inst in list(_instances.values()):
        try:
            inst.stop()
        except Exception as exc:
            journal.debug("poller", f"arrêt d'instance en erreur : {exc}")
    _instances.clear()


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
    # Le connecteur virtuel passe en dernier : ses formules calculent ainsi
    # sur les valeurs fraîches des capteurs relevés dans ce même cycle.
    for c in conn.execute("SELECT id,name FROM connectors WHERE enabled=1 "
                          "ORDER BY (type='virtual'), id").fetchall():
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
                journal.error(c["name"], f"pas de réponse pour '{d['name']}'")
                continue
            journal.debug(c["name"], f"{d['name']} = {v}")
            try:
                val = float(str(v).replace(",", "."))
            except ValueError:
                # valeur non numérique (état texte) : on met à jour l'état courant
                conn.execute("UPDATE devices SET last_value=?, last_seen=? WHERE id=?",
                             (str(v), now, d["id"]))
                conn.commit()
                continue
            if math.isnan(val):
                # capteur calculé incalculable (division par zéro...) : état
                # « NaN » affiché invalide, jamais historisé -> lever de
                # crayon sur les graphes
                conn.execute("UPDATE devices SET last_value='NaN', last_seen=? WHERE id=?",
                             (now, d["id"]))
                conn.commit()
            else:
                db.store_measure(d["id"], now, val)
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
        # Sauvegarde automatique : échéance datée, avancée par backup.py selon
        # la périodicité choisie (le réglage est relu à chaque cycle).
        try:
            await asyncio.to_thread(backup.run_scheduled, started)
        except Exception as exc:
            journal.error("backup", f"échec de la sauvegarde automatique : {exc}")
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
