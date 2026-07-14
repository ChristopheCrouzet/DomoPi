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
import threading

import paho.mqtt.client as mqtt

from .base import Connector
from .. import journal


class WesMqttConnector(Connector):
    type_name = "wes_mqtt"

    def __init__(self, connector_id, name, config):
        super().__init__(connector_id, name, config)
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()
        self._discovered: dict[str, dict] = {}   # external_id -> descriptor
        self._values: dict[str, str] = {}        # state_topic -> payload brut

    # ------------------------------------------------------------- MQTT
    def start(self):
        if self._client:
            return
        prefix = self.config.get("discovery_prefix", "homeassistant")
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                        client_id=f"domopi-{self.id}")
        if self.config.get("username"):
            c.username_pw_set(self.config["username"], self.config.get("password", ""))
        c.on_connect = lambda cl, ud, fl, rc, pr=None: (
            cl.subscribe([(f"{prefix}/+/+/config", 0), (f"{prefix}/+/+/+/config", 0)]),
            journal.info(self.name, "connecté au broker MQTT"))
        c.on_message = self._on_message
        c.on_disconnect = lambda *a: journal.warning(self.name, "broker MQTT déconnecté")
        c.connect_async(self.config.get("host", "127.0.0.1"),
                        int(self.config.get("port", 1883)), keepalive=60)
        c.loop_start()
        self._client = c

    def stop(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

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
        except Exception as exc:
            journal.error(self.name, f"message MQTT invalide {msg.topic} : {exc}")

    def _handle_config(self, msg):
        cfg = json.loads(self._decode(msg.payload))
        parts = msg.topic.split("/")
        component = parts[1]                 # sensor, binary_sensor, switch...
        uid = cfg.get("unique_id") or cfg.get("uniq_id") or "/".join(parts[1:-1])
        state_topic = cfg.get("state_topic") or cfg.get("stat_t") or ""
        position_topic = cfg.get("position_topic") or cfg.get("pos_t") or ""
        set_position_topic = cfg.get("set_position_topic") or cfg.get("set_pos_t") or ""
        brightness_cmd = (cfg.get("brightness_command_topic")
                          or cfg.get("bri_cmd_t") or "")
        brightness_state = (cfg.get("brightness_state_topic")
                            or cfg.get("bri_stat_t") or "")
        # Pour un volet, la position est souvent l'état le plus utile
        if not state_topic:
            state_topic = position_topic or brightness_state
        dimmable = bool(set_position_topic or brightness_cmd)
        desc = {
            "external_id": uid,
            "name": cfg.get("name") or uid,
            "kind": "actuator" if component in ("switch", "light", "cover") else "sensor",
            "unit": cfg.get("unit_of_measurement") or cfg.get("unit_of_meas") or "",
            "room": (cfg.get("device") or {}).get("name", "WES"),
            "dimmable": dimmable,
            "meta": {
                "component": component,
                "state_topic": state_topic,
                "position_topic": position_topic,
                "command_topic": cfg.get("command_topic") or cfg.get("cmd_t") or "",
                "set_position_topic": set_position_topic,
                "brightness_command_topic": brightness_cmd,
                "value_template": cfg.get("value_template") or cfg.get("val_tpl") or "",
                "payload_on": cfg.get("payload_on", "ON"),
                "payload_off": cfg.get("payload_off", "OFF"),
            },
        }
        with self._lock:
            new = uid not in self._discovered
            self._discovered[uid] = desc
        if self._client:
            for t in (state_topic, position_topic):
                if t:
                    self._client.subscribe(t)
        if new:
            journal.info(self.name, f"découvert : {desc['name']} ({component})")

    # -------------------------------------------------------- interface
    def discover(self) -> list[dict]:
        with self._lock:
            return list(self._discovered.values())

    @staticmethod
    def _extract(payload: str, template: str):
        """Extraction minimale : gère les templates {{ value_json.xxx }}."""
        if template and "value_json" in template:
            try:
                data = json.loads(payload)
                key = template.split("value_json.")[1].split("}}")[0] \
                              .split("|")[0].strip(" .)")
                return data.get(key)
            except Exception:
                return None
        return payload

    def poll(self, devices: list[dict]) -> dict[str, float | str]:
        values: dict[str, float | str] = {}
        with self._lock:
            snapshot = dict(self._values)
            discovered = dict(self._discovered)
        for d in devices:
            meta = d.get("meta", {})
            desc = discovered.get(d["external_id"], {})
            topic = meta.get("state_topic") or desc.get("meta", {}).get("state_topic", "")
            payload = snapshot.get(topic)
            if payload is None:
                journal.error(self.name,
                              f"pas de réponse pour '{d['name']}' (topic {topic or '?'})")
                continue
            v = self._extract(payload, meta.get("value_template", ""))
            if v is not None:
                values[d["external_id"]] = v
        return values

    def set_value(self, device: dict, value: str) -> bool:
        meta = device.get("meta", {})
        if not self._client:
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
            journal.error(self.name, f"pas de command_topic pour '{device['name']}'")
            return False
        payload = {"on": meta.get("payload_on", "ON"),
                   "off": meta.get("payload_off", "OFF")}.get(v.lower(), v)
        self._client.publish(topic, payload)
        journal.info(self.name, f"pilotage '{device['name']}' -> {payload}")
        return True
