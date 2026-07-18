"""Connecteur interne des capteurs virtuels (capteurs calculés par formule).

Une seule instance, créée automatiquement au premier démarrage (ligne
``connectors`` de type ``virtual``, repérée par le réglage
``virtual_connector_id``) ; elle n'apparaît pas dans l'admin et ne se
supprime pas. Ses périphériques sont créés par l'utilisateur (onglet
Périphériques, section « Capteurs virtuels ») et portent leur formule dans
``meta["formula"]``.

``poll()`` évalue les formules (module ``formula``) : le poller historise le
résultat comme n'importe quelle mesure, et la chaîne « NaN » signale une
valeur incalculable (affichée « invalide », jamais historisée). Le poller
interroge ce connecteur en dernier, pour calculer sur les valeurs fraîches
du cycle.

Un capteur virtuel **sans formule** est un état réglable à la main : il est
pilotable depuis sa tuile (``set_value`` écrit ``last_value`` — on/off
traduits en 1/0 pour garder un historique numérique) et ``poll()`` renvoie
sa valeur courante, donc elle s'historise à chaque cycle s'il est surveillé.
Dès qu'une formule est associée, le pilotage est refusé (et l'admin force
``controllable`` à 0).
"""
from __future__ import annotations

import math
import time

from .base import Connector
from .. import db, formula


class VirtualConnector(Connector):
    type_name = "virtual"

    def __init__(self, connector_id: int, name: str, config: dict):
        super().__init__(connector_id, name, config)
        self._cache: dict = {}   # texte de formule -> AST (ou None si invalide)

    def discover(self) -> list[dict]:
        return []                # les capteurs virtuels se créent à la main

    def poll(self, devices: list[dict]) -> dict[str, float | str]:
        ctx = None
        out: dict[str, float | str] = {}
        for d in devices:
            text = (d.get("meta") or {}).get("formula") or ""
            if not text.strip():
                # état réglé à la main : la valeur courante, rejouée à chaque
                # cycle pour être historisée (« NaN » tant que jamais réglé)
                out[d["external_id"]] = d.get("last_value") or "NaN"
                continue
            if ctx is None:
                ctx = formula.Resolver()
            v = formula.compute(text, ctx, self._cache)
            out[d["external_id"]] = "NaN" if math.isnan(v) else v
        return out

    def set_value(self, device: dict, value: str) -> bool:
        if ((device.get("meta") or {}).get("formula") or "").strip():
            self.last_error = "un capteur calculé ne se pilote pas"
            return False
        v = {"on": "1", "off": "0"}.get(str(value).strip().lower(), str(value).strip())
        db.get_conn().execute(
            "UPDATE devices SET last_value=?, last_seen=? WHERE id=?",
            (v, int(time.time()), device["id"]))
        db.get_conn().commit()
        return True
