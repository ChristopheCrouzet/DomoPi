"""Connecteur eedomus — API locale.

Doc : https://doc.eedomus.com/en/index.php/API_eedomus
Principe : mêmes appels que l'API cloud mais sur http://<ip_box>/api/
Fonctions disponibles en LOCAL : auth.test, periph.caract, periph.list,
periph.value_list, periph.value (get/set), periph.macro.
NB : periph.history n'est PAS disponible en local — c'est DomoPi qui
construit l'historique par échantillonnage périodique.

Config attendue : {"host": "192.168.1.x", "api_user": "...", "api_secret": "..."}
"""
from __future__ import annotations

import json

import httpx

from .base import Connector
from .. import journal

TIMEOUT = 10.0


class EedomusConnector(Connector):
    type_name = "eedomus"

    @property
    def base_url(self) -> str:
        return f"http://{self.config['host']}/api/get"

    def _params(self, **kw) -> dict:
        return {"api_user": self.config.get("api_user", ""),
                "api_secret": self.config.get("api_secret", ""), **kw}

    def _call(self, action: str, **kw):
        r = httpx.get(self.base_url, params=self._params(action=action, **kw),
                      timeout=TIMEOUT)
        r.raise_for_status()
        data = json.loads(r.text)
        if data.get("success") != 1:
            raise RuntimeError(f"eedomus {action}: {data}")
        return data.get("body")

    def test(self) -> bool:
        try:
            self._call("auth.test")
            return True
        except Exception as exc:
            journal.error(self.name, f"auth.test échoué : {exc}")
            return False

    def discover(self) -> list[dict]:
        body = self._call("periph.list")
        out = []
        for p in body or []:
            usage = (p.get("usage_name") or "").lower()
            actuator = any(k in usage for k in
                           ("lampe", "light", "prise", "volet", "shutter",
                            "chauffage", "heater", "switch", "actionneur", "portail"))
            dimmable = any(k in usage for k in
                           ("volet", "shutter", "variateur", "dimmer", "store"))
            out.append({
                "external_id": str(p.get("periph_id")),
                "name": p.get("name", f"periph {p.get('periph_id')}"),
                "kind": "actuator" if actuator else "sensor",
                "unit": "",
                "room": p.get("room_name", ""),
                "dimmable": actuator and dimmable,
                "meta": {"usage": p.get("usage_name", "")},
            })
        return out

    def poll(self, devices: list[dict]) -> dict[str, float | str]:
        """Un seul periph.list par cycle : contient last_value pour chaque périph."""
        body = self._call("periph.list")
        by_id = {str(p.get("periph_id")): p for p in body or []}
        values: dict[str, float | str] = {}
        for d in devices:
            p = by_id.get(d["external_id"])
            if p is None or p.get("last_value") in (None, ""):
                journal.error(self.name,
                              f"pas de réponse pour '{d['name']}' ({d['external_id']})")
                continue
            values[d["external_id"]] = p["last_value"]
        return values

    def set_value(self, device: dict, value: str) -> bool:
        try:
            self._call("periph.value", periph_id=device["external_id"], value=value)
            journal.info(self.name, f"pilotage '{device['name']}' -> {value}")
            return True
        except Exception as exc:
            journal.error(self.name, f"échec pilotage '{device['name']}' : {exc}")
            return False
