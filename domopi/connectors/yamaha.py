"""Connecteur Yamaha YNC — amplis AV réseau (testé : RX-V773).

Protocole : XML sur HTTP, POST http://<ip_ampli>/YamahaRemoteControl/ctrl
(YNC / "Yamaha Network Control", celui des applis AV Controller).

Périphériques exposés (liste fixe, pas de vraie découverte côté ampli) :
  - system_power  SYSTEM POWER   on/off  (veille réseau globale)
  - main_power    MAIN ON/OFF    on/off  (zone principale)
  - zone2_power   ZONE 2 ON/OFF  on/off
  - enhancer      ENHANCER       on/off  (Compressed Music Enhancer)
  - main_volume   MAIN VOLUME    0-100 % (converti en dB, pas de 0.5 dB) —
                                 échelle « Yamaha - Volume » type consigne :
                                 le clic ouvre le réglage, pas de on/off
                                 (un on enverrait le volume à 100 %)
  - sleep         SLEEP          énumération 0/30/60/90/120 (minutes)
  - input         ENTRÉE         énumération 1..N (liste lue sur l'ampli,
                                 libellés personnalisés inclus, ex. « PS3 »)
  - surround      MODE SURROUND  énumération 1..19 (programmes DSP)
  - scene_1..4    SCÈNE 1..4     impulsion : « on » lance la scène, pas
                                 d'état relisible (poll retombe à 0)

Les énumérations arrivent avec une échelle dédiée (boutons seuls) créée à
la découverte si absente — modifiable ensuite dans l'admin (supprimer des
boutons, renommer...) : la re-découverte ne la recrée que si elle a été
supprimée, et ne touche jamais l'échelle d'un périphérique déjà importé.

Config attendue : {"host": "192.168.1.x",
                   "vol_min_db": -80.5, "vol_max_db": 16.5}
La plage dB <-> 0-100 % est configurable : abaisser vol_max_db (ex. 0)
si on veut que 100 % corresponde à un maximum raisonnable.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from .base import Connector
from .. import journal

TIMEOUT = 5.0

# external_id -> (nom affiché, chemin XML de la zone YNC)
POWER_ZONES = {
    "system_power": ("SYSTEM POWER", "System"),
    "main_power": ("MAIN ON/OFF", "Main_Zone"),
    "zone2_power": ("ZONE 2 ON/OFF", "Zone_2"),
}
VOLUME_ID, SLEEP_ID, INPUT_ID = "main_volume", "sleep", "input"
SURROUND_ID, ENHANCER_ID = "surround", "enhancer"
SCENES = {f"scene_{n}": f"Scene {n}" for n in (1, 2, 3, 4)}

SLEEP_STOPS = [(0, "Off"), (30, "30 min"), (60, "60 min"),
               (90, "90 min"), (120, "120 min")]

# Programmes DSP, dans l'ordre du desc.xml de l'ampli (codes 1..19).
SOUND_PROGRAMS = [
    "Hall in Munich", "Hall in Vienna", "Chamber", "Cellar Club",
    "The Roxy Theatre", "The Bottom Line", "Sports", "Action Game",
    "Roleplaying Game", "Music Video", "Standard", "Spectacle", "Sci-Fi",
    "Adventure", "Drama", "Mono Movie", "Surround Decoder",
    "2ch Stereo", "7ch Stereo",
]

# Entrées jamais proposées (service disparu) — garde aussi la liste sous
# la limite des 20 valeurs d'une échelle.
INPUT_EXCLUDE = {"Napster"}
MAX_STOPS = 20


class YamahaConnector(Connector):
    type_name = "yamaha"

    def __init__(self, connector_id, name, config):
        super().__init__(connector_id, name, config)
        self._input_cache: list[tuple[str, str]] | None = None  # (param, libellé)

    # ------------------------------------------------ bas niveau
    def _rpc(self, cmd: str, body: str) -> ET.Element:
        host = str(self.config.get("host", "")).strip()
        if not host:
            raise RuntimeError("adresse de l'ampli non configurée")
        r = httpx.post(f"http://{host}/YamahaRemoteControl/ctrl",
                       content=f'<YAMAHA_AV cmd="{cmd}">{body}</YAMAHA_AV>',
                       headers={"Content-Type": "text/xml"}, timeout=TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        rc = root.get("RC")
        if rc != "0":
            # RC=3 : paramètre invalide ; RC=4 : commande refusée (ex. ampli
            # en veille pour un réglage de volume ou de programme).
            raise RuntimeError(f"réponse YNC RC={rc}")
        return root

    def _vol_bounds(self) -> tuple[float, float]:
        try:
            vmin = float(self.config.get("vol_min_db", -80.5))
            vmax = float(self.config.get("vol_max_db", 16.5))
        except (TypeError, ValueError):
            vmin, vmax = -80.5, 16.5
        return (vmin, vmax) if vmax > vmin else (-80.5, 16.5)

    def _inputs(self) -> list[tuple[str, str]]:
        """Entrées sélectionnables (param YNC, libellé perso), codées 1..N.

        Liste lue sur l'ampli (Input_Sel_Item) et mise en cache : son ordre
        est stable (figé par le firmware), les codes le sont donc aussi.
        """
        if self._input_cache is None:
            root = self._rpc("GET", "<Main_Zone><Input><Input_Sel_Item>GetParam"
                                    "</Input_Sel_Item></Input></Main_Zone>")
            items = []
            for it in root.find(".//Input_Sel_Item") or []:
                param = it.findtext("Param") or ""
                if not param or param in INPUT_EXCLUDE:
                    continue
                items.append((param, it.findtext("Title") or param))
            self._input_cache = items[:MAX_STOPS]
        return self._input_cache

    # ------------------------------------------------ interface Connector
    def discover(self) -> list[dict]:
        # L'appel _inputs() vérifie au passage que l'ampli répond.
        self._input_cache = None
        inputs = self._inputs()
        enum_scale = dict(hide_slider=True, toggle_click=False)
        devs = [{"external_id": ext, "name": name, "kind": "actuator",
                 "unit": "", "room": "", "meta": {}}
                for ext, (name, _zone) in POWER_ZONES.items()]
        devs.append({"external_id": ENHANCER_ID, "name": "ENHANCER",
                     "kind": "actuator", "unit": "", "room": "", "meta": {}})
        # Échelle dédiée de type « consigne » (toggle_click=0) : le clic
        # ouvre directement le réglage — jamais de marche/arrêt qui
        # enverrait brutalement le volume à 100 %.
        devs.append({"external_id": VOLUME_ID, "name": "MAIN VOLUME",
                     "kind": "actuator", "unit": "%", "room": "",
                     "meta": {}, "dimmable": True,
                     "scale": {"name": "Yamaha - Volume", "unit": "%",
                               "vmin": 0, "vmax": 100, "step": 1,
                               "hide_slider": False, "toggle_click": False}})
        devs.append({"external_id": SLEEP_ID, "name": "SLEEP",
                     "kind": "actuator", "unit": "", "room": "", "meta": {},
                     "scale": dict(enum_scale, name="Yamaha - Sleep",
                                   vmin=0, vmax=120, step=30,
                                   stops=[{"value": v, "label": l}
                                          for v, l in SLEEP_STOPS])})
        devs.append({"external_id": INPUT_ID, "name": "ENTRÉE",
                     "kind": "actuator", "unit": "", "room": "", "meta": {},
                     "scale": dict(enum_scale, name="Yamaha - Entrée",
                                   vmin=1, vmax=len(inputs), step=1,
                                   stops=[{"value": i + 1, "label": title}
                                          for i, (_p, title) in enumerate(inputs)])})
        devs.append({"external_id": SURROUND_ID, "name": "MODE SURROUND",
                     "kind": "actuator", "unit": "", "room": "", "meta": {},
                     "scale": dict(enum_scale, name="Yamaha - Surround",
                                   vmin=1, vmax=len(SOUND_PROGRAMS), step=1,
                                   stops=[{"value": i + 1, "label": p}
                                          for i, p in enumerate(SOUND_PROGRAMS)])})
        # Noms des scènes personnalisés sur l'ampli (« BD/DVD PS3 »...) —
        # repris dans le nom initial du périphérique (jamais écrasé ensuite).
        titles = {}
        try:
            root = self._rpc("GET", "<Main_Zone><Scene><Scene_Sel_Item>GetParam"
                                    "</Scene_Sel_Item></Scene></Main_Zone>")
            for it in root.find(".//Scene_Sel_Item") or []:
                titles[it.findtext("Param") or ""] = it.findtext("Title") or ""
        except Exception:
            pass
        for ext, param in SCENES.items():
            t = titles.get(param, "")
            devs.append({"external_id": ext,
                         "name": f"SCÈNE {ext[-1]}" + (f" ({t})" if t else ""),
                         "kind": "actuator", "unit": "", "room": "", "meta": {}})
        return devs

    def poll(self, devices: list[dict]) -> dict[str, float | str]:
        wanted = {d["external_id"] for d in devices}
        values: dict[str, float | str] = {}
        # Scènes : pas d'état côté ampli, la tuile retombe toujours sur off.
        for ext in wanted & SCENES.keys():
            values[ext] = 0
        vmin, vmax = self._vol_bounds()
        try:
            if "system_power" in wanted:
                root = self._rpc("GET", "<System><Power_Control><Power>GetParam"
                                        "</Power></Power_Control></System>")
                values["system_power"] = 1 if root.findtext(".//Power") == "On" else 0
            main_ids = {"main_power", VOLUME_ID, SLEEP_ID, INPUT_ID,
                        SURROUND_ID, ENHANCER_ID}
            if wanted & main_ids:
                root = self._rpc("GET", "<Main_Zone><Basic_Status>GetParam"
                                        "</Basic_Status></Main_Zone>")
                if "main_power" in wanted:
                    values["main_power"] = \
                        1 if root.findtext(".//Power_Control/Power") == "On" else 0
                if VOLUME_ID in wanted:
                    db = float(root.findtext(".//Volume/Lvl/Val", "0")) / 10
                    pct = (db - vmin) / (vmax - vmin) * 100
                    values[VOLUME_ID] = round(max(0.0, min(100.0, pct)), 1)
                if SLEEP_ID in wanted:
                    s = root.findtext(".//Power_Control/Sleep", "Off")
                    values[SLEEP_ID] = 0 if s == "Off" else int(s.split()[0])
                if INPUT_ID in wanted:
                    sel = root.findtext(".//Input/Input_Sel", "")
                    for i, (param, _t) in enumerate(self._inputs()):
                        if param == sel:
                            values[INPUT_ID] = i + 1
                            break
                if SURROUND_ID in wanted:
                    prog = root.findtext(".//Current/Sound_Program", "")
                    if prog in SOUND_PROGRAMS:
                        values[SURROUND_ID] = SOUND_PROGRAMS.index(prog) + 1
                if ENHANCER_ID in wanted:
                    values[ENHANCER_ID] = \
                        1 if root.findtext(".//Current/Enhancer") == "On" else 0
            if "zone2_power" in wanted:
                root = self._rpc("GET", "<Zone_2><Basic_Status>GetParam"
                                        "</Basic_Status></Zone_2>")
                values["zone2_power"] = \
                    1 if root.findtext(".//Power_Control/Power") == "On" else 0
        except Exception as exc:
            journal.debug(self.name, f"lecture ampli impossible : {exc}")
        return values

    def set_value(self, device: dict, value: str) -> bool:
        ext = device["external_id"]
        v = value.strip().lower()

        def is_on() -> bool:
            try:
                return v in ("on", "true") or float(v.replace(",", ".")) > 0
            except ValueError:
                return False

        def num() -> float:
            return float(value.replace(",", "."))

        try:
            if ext in POWER_ZONES:
                zone = POWER_ZONES[ext][1]
                state = "On" if is_on() else "Standby"
                self._rpc("PUT", f"<{zone}><Power_Control><Power>{state}"
                                 f"</Power></Power_Control></{zone}>")
            elif ext == ENHANCER_ID:
                state = "On" if is_on() else "Off"
                self._rpc("PUT", "<Main_Zone><Surround><Program_Sel><Current>"
                                 f"<Enhancer>{state}</Enhancer>"
                                 "</Current></Program_Sel></Main_Zone>")
            elif ext in SCENES:
                if not is_on():
                    return True          # « off » sans objet sur une impulsion
                state = SCENES[ext]
                self._rpc("PUT", "<Main_Zone><Scene>"
                                 f"<Scene_Sel>{state}</Scene_Sel>"
                                 "</Scene></Main_Zone>")
            elif ext == VOLUME_ID:
                vmin, vmax = self._vol_bounds()
                pct = max(0.0, min(100.0, num()))
                db = vmin + pct / 100 * (vmax - vmin)
                val10 = int(round(db * 2) / 2 * 10)   # pas de 0.5 dB
                self._rpc("PUT", "<Main_Zone><Volume><Lvl>"
                                 f"<Val>{val10}</Val><Exp>1</Exp><Unit>dB</Unit>"
                                 "</Lvl></Volume></Main_Zone>")
                state = f"{pct:g} % ({val10 / 10:+.1f} dB)"
            elif ext == SLEEP_ID:
                m = min((mv for mv, _l in SLEEP_STOPS),
                        key=lambda mv: abs(mv - num()))
                state = "Off" if m == 0 else f"{m} min"
                self._rpc("PUT", "<Main_Zone><Power_Control>"
                                 f"<Sleep>{state}</Sleep>"
                                 "</Power_Control></Main_Zone>")
            elif ext == INPUT_ID:
                inputs = self._inputs()
                i = int(round(num()))
                if not 1 <= i <= len(inputs):
                    raise RuntimeError(f"code entrée {value} hors liste")
                state = inputs[i - 1][0]
                self._rpc("PUT", "<Main_Zone><Input>"
                                 f"<Input_Sel>{state}</Input_Sel>"
                                 "</Input></Main_Zone>")
            elif ext == SURROUND_ID:
                i = int(round(num()))
                if not 1 <= i <= len(SOUND_PROGRAMS):
                    raise RuntimeError(f"code programme {value} hors liste")
                state = SOUND_PROGRAMS[i - 1]
                self._rpc("PUT", "<Main_Zone><Surround><Program_Sel><Current>"
                                 f"<Sound_Program>{state}</Sound_Program>"
                                 "</Current></Program_Sel></Main_Zone>")
            else:
                raise RuntimeError(f"périphérique inconnu : {ext}")
            journal.info(self.name, f"pilotage '{device['name']}' -> {state}")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            journal.error(self.name, f"échec pilotage '{device['name']}' : {exc}")
            return False
