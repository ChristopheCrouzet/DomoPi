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

    def __init__(self, connector_id, name, config):
        super().__init__(connector_id, name, config)
        self._vlists: dict[str, list[str]] = {}   # periph_id -> valeurs permises

    def _value_list(self, periph_id: str) -> list[str]:
        """Valeurs acceptées par le périphérique (periph.value_list), en cache."""
        vl = self._vlists.get(periph_id)
        if vl is None:
            body = self._call("periph.value_list", periph_id=periph_id)
            if isinstance(body, dict):
                body = body.get("values") or body.get("value_list") or []
            vl = [str(x.get("value") if isinstance(x, dict) else x)
                  for x in (body or [])]
            self._vlists[periph_id] = vl
        return vl

    def _params(self, **kw) -> dict:
        return {"api_user": self.config.get("api_user", ""),
                "api_secret": self.config.get("api_secret", ""), **kw}

    def _call(self, action: str, endpoint: str = "get", **kw):
        # L'API eedomus a deux points d'entrée : /api/get (lectures) et
        # /api/set (commandes periph.value / periph.macro).
        url = f"http://{self.config['host']}/api/{endpoint}"
        r = httpx.get(url, params=self._params(action=action, **kw),
                      timeout=TIMEOUT)
        r.raise_for_status()
        # L'API locale répond en Latin-1 sans déclarer de charset : décoder
        # nous-mêmes (UTF-8 si valide, sinon Latin-1) plutôt que r.text, qui
        # suppose UTF-8 et remplace é/°/… par U+FFFD.
        try:
            text = r.content.decode("utf-8")
        except UnicodeDecodeError:
            text = r.content.decode("latin-1")
        data = json.loads(text)
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
        """Valeurs courantes des périphériques demandés.

        L'API cloud renvoie last_value dans periph.list, mais l'API LOCALE ne
        le fournit pas : on tente d'abord periph.list (une seule requête, au
        cas où le firmware le donne), puis periph.caract périphérique par
        périphérique pour ceux restés sans valeur.
        """
        try:
            body = self._call("periph.list")
            by_id = {str(p.get("periph_id")): p for p in body or []}
        except Exception as exc:
            journal.debug(self.name, f"periph.list indisponible : {exc}")
            by_id = {}
        values: dict[str, float | str] = {}
        for d in devices:
            v = (by_id.get(d["external_id"]) or {}).get("last_value")
            if v in (None, ""):
                try:
                    c = self._call("periph.caract", periph_id=d["external_id"])
                    v = (c or {}).get("last_value")
                except Exception as exc:
                    journal.debug(self.name,
                                  f"periph.caract {d['external_id']} : {exc}")
                    v = None
            if v in (None, ""):
                # le poller journalise l'absence de réponse à sa cadence ;
                # ici (appelé aussi par le rafraîchissement rapide) : debug.
                journal.debug(self.name,
                              f"pas de réponse pour '{d['name']}' ({d['external_id']})")
                continue
            values[d["external_id"]] = v
        return values

    def set_value(self, device: dict, value: str) -> bool:
        # eedomus attend des valeurs numériques : on/off -> 100/0.
        v = {"on": "100", "off": "0"}.get(value.strip().lower(), value.strip())
        ext = device["external_id"]
        try:
            self._call("periph.value", endpoint="set", periph_id=ext, value=v)
            journal.info(self.name, f"pilotage '{device['name']}' -> {v}")
            return True
        except Exception as exc:
            if "Unknown peripheral value" not in str(exc):
                self.last_error = str(exc)
                journal.error(self.name, f"échec pilotage '{device['name']}' : {exc}")
                return False
        # Valeur hors value_list (périph non variateur, paliers imposés...) :
        # rabattre sur la valeur autorisée numériquement la plus proche.
        try:
            target = float(v.replace(",", "."))
            candidates = []
            for x in self._value_list(ext):
                try:
                    candidates.append((abs(float(str(x).replace(",", ".")) - target), x))
                except ValueError:
                    continue
            if not candidates:
                raise RuntimeError(
                    f"valeur {v} refusée et aucune valeur numérique permise "
                    f"(value_list : {self._value_list(ext)})")
            best = min(candidates)[1]
            self._call("periph.value", endpoint="set", periph_id=ext, value=best)
            journal.info(self.name,
                         f"pilotage '{device['name']}' -> {best} "
                         f"(valeur {v} non permise, rabattue)")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            journal.error(self.name, f"échec pilotage '{device['name']}' : {exc}")
            return False
