"""Connecteur WES (Cartelectronic) via MQTT — format Home Assistant Discovery.

Doc : https://www.cartelectronic-blog.fr/wes-et-homeassistant-en-mqtt/
Prérequis : firmware WES >= 0.9 bêta 05, MQTT activé et pointé vers le
broker Mosquitto local du Raspberry (port 1883).

Le WES publie ses entités sur homeassistant/<component>/<id>/config
(sensor, binary_sensor, switch...). Ce connecteur est en fait un client
"HA MQTT Discovery" générique : tout appareil publiant à ce format
sera découvert.

Config attendue : {"host": "127.0.0.1", "port": 1883,
                   "username": "", "password": "",
                   "discovery_prefix": "homeassistant"}
"""
from __future__ import annotations

import json
import re
import threading
import time

import paho.mqtt.client as mqtt

from .base import Connector
from .. import journal


class WesMqttConnector(Connector):
    type_name = "wes_mqtt"

    def __init__(self, connector_id, name, config):
        super().__init__(connector_id, name, config)
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()
        self._connected = False                  # état DomoPi <-> broker
        self._connected_at = 0.0                 # epoch de la dernière connexion
        self._discovered: dict[str, dict] = {}   # external_id -> descriptor
        self._values: dict[str, str] = {}        # state_topic -> payload brut
        self._times: dict[str, float] = {}       # state_topic -> epoch de réception
        self._counts: dict[str, int] = {}        # state_topic -> messages reçus
        self._subscribed: set[str] = set()       # topics d'état abonnés

    @property
    def broker_connected(self) -> bool:
        """Vrai si DomoPi est actuellement connecté au broker MQTT."""
        return self._connected

    @property
    def connected_s(self) -> int:
        """Ancienneté de la connexion au broker, en secondes (0 si déconnecté)."""
        return int(time.time() - self._connected_at) if self._connected else 0

    # ------------------------------------------------------------- MQTT
    def start(self):
        if self._client:
            return
        prefix = self.config.get("discovery_prefix", "homeassistant")
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                        client_id=f"domopi-{self.id}")
        if self.config.get("username"):
            c.username_pw_set(self.config["username"], self.config.get("password", ""))

        def on_connect(cl, ud, flags, rc, props=None):
            if getattr(rc, "is_failure", False) or (isinstance(rc, int) and rc != 0):
                self._connected = False
                journal.error(self.name, f"connexion au broker MQTT refusée : {rc} "
                                         "(identifiants MQTT du connecteur ?)")
                return
            self._connected = True
            self._connected_at = time.time()
            cl.subscribe([(f"{prefix}/+/+/config", 0), (f"{prefix}/+/+/+/config", 0)])
            journal.info(self.name, "connecté au broker MQTT")

        def on_disconnect(*a):
            self._connected = False
            journal.warning(self.name, "broker MQTT déconnecté")

        def on_connect_fail(*a):
            self._connected = False
            journal.error(self.name, "broker MQTT injoignable (hôte/port ?)")

        c.on_connect = on_connect
        c.on_message = self._on_message
        c.on_disconnect = on_disconnect
        c.on_connect_fail = on_connect_fail
        c.connect_async(self.config.get("host", "127.0.0.1"),
                        int(self.config.get("port", 1883)), keepalive=60)
        c.loop_start()
        self._client = c

    def stop(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False

    @staticmethod
    def _decode(payload: bytes) -> str:
        """UTF-8 si valide, sinon Latin-1 (certains firmwares publient en Latin-1)."""
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return payload.decode("latin-1")

    def _on_message(self, client, userdata, msg):
        try:
            if msg.topic.endswith("/config"):
                self._handle_config(msg)
            else:
                with self._lock:
                    self._values[msg.topic] = self._decode(msg.payload)
                    self._times[msg.topic] = time.time()
                    self._counts[msg.topic] = self._counts.get(msg.topic, 0) + 1
        except Exception as exc:
            journal.error(self.name, f"message MQTT invalide {msg.topic} : {exc}")

    def _handle_config(self, msg):
        cfg = json.loads(self._decode(msg.payload))
        parts = msg.topic.split("/")
        component = parts[1]                 # sensor, binary_sensor, switch...
        g = lambda *keys, default="": next(  # noqa: E731 — lecture d'une clé HA
            (cfg[k] for k in keys if cfg.get(k) not in (None, "")), default)
        uid = g("unique_id", "uniq_id") or "/".join(parts[1:-1])
        state_topic = g("state_topic", "stat_t")
        position_topic = g("position_topic", "pos_t")
        set_position_topic = g("set_position_topic", "set_pos_t")
        brightness_cmd = g("brightness_command_topic", "bri_cmd_t")
        brightness_state = g("brightness_state_topic", "bri_stat_t")
        # Pour un volet, la position est souvent l'état le plus utile
        if not state_topic:
            state_topic = position_topic or brightness_state
        dimmable = bool(set_position_topic or brightness_cmd)
        dev = cfg.get("device") or cfg.get("dev") or {}
        desc = {
            "external_id": uid,
            "name": g("name") or uid,
            "kind": "actuator" if component in ("switch", "light", "cover") else "sensor",
            "unit": g("unit_of_measurement", "unit_of_meas"),
            "room": dev.get("name") or dev.get("n") or "WES",
            "dimmable": dimmable,
            "meta": {
                "component": component,
                "state_topic": state_topic,
                "position_topic": position_topic,
                "command_topic": g("command_topic", "cmd_t"),
                "set_position_topic": set_position_topic,
                "brightness_command_topic": brightness_cmd,
                "value_template": g("value_template", "val_tpl"),
                # Charge utile des commandes. Les formes abrégées (pl_on/pl_off)
                # sont la norme des annonces HA Discovery : les ignorer faisait
                # envoyer « ON » à un appareil qui attend « 1 » — commande sans
                # effet, sans erreur (WES : relais).
                "payload_on": g("payload_on", "pl_on", default="ON"),
                "payload_off": g("payload_off", "pl_off", default="OFF"),
                # Valeurs d'état correspondantes, quand elles diffèrent de la
                # charge utile des commandes (facultatif en HA Discovery).
                "state_on": g("state_on", "stat_on"),
                "state_off": g("state_off", "stat_off"),
            },
        }
        with self._lock:
            new = uid not in self._discovered
            self._discovered[uid] = desc
        if self._client:
            for t in (state_topic, position_topic):
                if t:
                    self._client.subscribe(t)
                    with self._lock:
                        self._subscribed.add(t)
        if new:
            journal.info(self.name, f"découvert : {desc['name']} ({component})")

    # -------------------------------------------------------- interface
    def discover(self) -> list[dict]:
        with self._lock:
            return list(self._discovered.values())

    @staticmethod
    def _extract(payload: str, template: str):
        """Extraction minimale d'un template HA sur une charge utile JSON.

        Formes gérées : {{ value_json.SW1 }}, {{ value_json['SW1'] }},
        {{ value_json["SW1"] }}, chemins imbriqués ({{ value_json.TOR.SW1 }}),
        et filtres ignorés ({{ value_json.SW1 | int }}). Sans template, la
        charge utile est renvoyée telle quelle.
        """
        if not (template and "value_json" in template):
            return payload
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        # Partie utile du template : ce qui suit « value_json », filtres et
        # accolades retirés.
        expr = template.split("value_json", 1)[1].split("}}")[0].split("|")[0].strip()
        # .a['b']["c"] -> ['a', 'b', 'c']
        keys = [k for k in re.split(r"[.\[\]'\"]+", expr) if k]
        for k in keys:
            if isinstance(data, list):
                try:
                    data = data[int(k)]
                    continue
                except (ValueError, IndexError):
                    return None
            if not isinstance(data, dict) or k not in data:
                return None
            data = data[k]
        return data if not isinstance(data, (dict, list)) else json.dumps(data)

    def poll(self, devices: list[dict]) -> dict[str, float | str]:
        values: dict[str, float | str] = {}
        with self._lock:
            snapshot = dict(self._values)
        for d in devices:
            # La dernière annonce reçue prime sur la copie en base (_live_meta) :
            # une correction de lecture des annonces s'applique sans exiger une
            # re-découverte.
            meta = self._live_meta(d)
            topic = meta.get("state_topic") or ""
            if not topic:
                journal.debug(self.name, f"'{d['name']}' : aucun topic d'état "
                                         "déclaré par l'appareil (annonce sans "
                                         "state_topic) — lecture impossible")
                continue
            payload = snapshot.get(topic)
            if payload is None:
                # le poller journalise l'absence de réponse à sa cadence ;
                # ici (appelé aussi par le rafraîchissement 10 s) : debug.
                journal.debug(self.name, f"'{d['name']}' : rien reçu sur « {topic} » "
                                         "depuis la connexion au broker (l'appareil "
                                         "publie-t-il cet état, et en retained ?)")
                continue
            tpl = meta.get("value_template", "")
            v = self._extract(payload, tpl)
            if v is None:
                journal.debug(self.name, f"'{d['name']}' : « {tpl} » ne trouve rien "
                                         f"dans la charge utile de « {topic} »")
                continue
            # state_on/state_off : ramène l'état à on/off quand l'appareil
            # publie autre chose que sa charge utile de commande.
            son = meta.get("state_on") or ""
            soff = meta.get("state_off") or ""
            if son and str(v) == str(son):
                v = "on"
            elif soff and str(v) == str(soff):
                v = "off"
            values[d["external_id"]] = v
        return values

    def _live_meta(self, device: dict) -> dict:
        """Meta de la dernière annonce reçue, complétée par celle en base.

        La copie en base date de la dernière découverte : préférer l'annonce
        courante évite d'exiger une re-découverte après une correction de
        lecture des annonces (charge utile des commandes, topics).
        """
        with self._lock:
            live = (self._discovered.get(device.get("external_id"), {})
                    .get("meta", {}))
        return {**device.get("meta", {}), **{k: v for k, v in live.items() if v != ""}}

    def set_value(self, device: dict, value: str) -> bool:
        meta = self._live_meta(device)
        if not self._client:
            self.last_error = "client MQTT indisponible"
            journal.error(self.name, f"client MQTT indisponible pour '{device['name']}'")
            return False
        # Valeur numérique 0-100 -> topic de position (volet) ou de luminosité
        # (gradateur) si le périphérique en publie un ; sinon command_topic.
        v = value.strip()
        is_num = v.replace(".", "", 1).lstrip("-").isdigit()
        if is_num and (meta.get("set_position_topic") or meta.get("brightness_command_topic")):
            topic = meta.get("set_position_topic") or meta.get("brightness_command_topic")
            self._client.publish(topic, v)
            journal.info(self.name, f"pilotage '{device['name']}' -> {v} %")
            return True
        topic = meta.get("command_topic", "")
        if not topic:
            self.last_error = "pas de command_topic pour ce périphérique"
            journal.error(self.name, f"pas de command_topic pour '{device['name']}'")
            return False
        payload = {"on": meta.get("payload_on", "ON"),
                   "off": meta.get("payload_off", "OFF")}.get(v.lower(), v)
        self._client.publish(topic, payload)
        journal.info(self.name, f"pilotage '{device['name']}' -> {payload}")
        return True

    # ---------------------------------------------------------- diagnostic
    def diagnose(self, device: dict) -> dict:
        """Ce que le connecteur sait de ce périphérique, tel quel.

        Sert à distinguer, sans accès au broker, les trois pannes de lecture :
        appareil qui n'annonce pas de topic d'état, topic annoncé mais jamais
        publié (état non retenu, publication seulement sur changement), et
        template qui ne trouve rien dans la charge utile reçue.
        """
        meta = self._live_meta(device)
        topic = meta.get("state_topic") or ""
        with self._lock:
            payload = self._values.get(topic)
            ts = self._times.get(topic)
            count = self._counts.get(topic, 0)
            subscribed = topic in self._subscribed
            announced = device.get("external_id") in self._discovered
            n_announced = len(self._discovered)
        rows = [
            ("Broker MQTT",
             (f"connecté depuis {self.connected_s} s" if self.broker_connected
              else "NON CONNECTÉ") +
             f" — {self.config.get('host', '127.0.0.1')}:{self.config.get('port', 1883)}"),
            ("Annonces reçues", f"{n_announced} appareil(s) ; celui-ci "
                                f"{'est annoncé' if announced else 'n’est PAS annoncé'}"),
            ("Composant", meta.get("component") or "—"),
            ("Topic d'état", topic or "aucun (l'appareil n'en annonce pas)"),
            ("Abonnement", "actif" if subscribed else
                           ("—" if not topic else "pas encore abonné")),
            ("Messages reçus", f"{count} sur ce topic"),
            ("Dernier message",
             "aucun depuis la connexion au broker" if ts is None else
             f"il y a {int(time.time() - ts)} s"),
            ("Charge utile", "—" if payload is None else payload[:400]),
            ("Template", meta.get("value_template") or "(aucun : charge utile brute)"),
            ("Valeur extraite",
             "—" if payload is None else
             repr(self._extract(payload, meta.get("value_template", "")))),
            ("Topic de commande", meta.get("command_topic") or "aucun"),
            ("Charges utiles on / off",
             f"{meta.get('payload_on', 'ON')} / {meta.get('payload_off', 'OFF')}"),
        ]
        if meta.get("state_on") or meta.get("state_off"):
            rows.append(("États on / off annoncés",
                         f"{meta.get('state_on') or '—'} / {meta.get('state_off') or '—'}"))
        if meta.get("set_position_topic") or meta.get("brightness_command_topic"):
            rows.append(("Topic proportionnel",
                         meta.get("set_position_topic")
                         or meta.get("brightness_command_topic")))
        notes = []
        if not self.broker_connected:
            notes.append("DomoPi n'est pas connecté au broker : ni lecture ni "
                         "commande ne peuvent aboutir.")
        elif not topic:
            notes.append("L'annonce de cet appareil ne contient pas de topic "
                         "d'état : sa valeur ne peut pas être relue. Vérifiez "
                         "la configuration MQTT côté appareil.")
        elif ts is None:
            notes.append("Aucun message reçu sur ce topic depuis la connexion : "
                         "l'appareil ne publie probablement son état qu'au "
                         "changement, sans le marquer « retained ». Agissez sur "
                         "la sortie pour voir si un message arrive.")
        elif payload is not None and self._extract(
                payload, meta.get("value_template", "")) is None:
            notes.append("Le template ne trouve rien dans la charge utile reçue : "
                         "la valeur reste inchangée côté DomoPi.")
        return {"title": "Diagnostic MQTT", "rows": rows, "notes": notes}
