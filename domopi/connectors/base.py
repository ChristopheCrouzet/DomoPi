"""Interface commune des connecteurs domotiques.

Un connecteur sait :
  - discover() : lister les périphériques disponibles côté contrôleur
  - poll()     : retourner la valeur courante des périphériques surveillés
  - set_value(): piloter un actionneur (si autorisé côté DomoPi)
"""
from __future__ import annotations


class Connector:
    type_name = "base"

    def __init__(self, connector_id: int, name: str, config: dict):
        self.id = connector_id
        self.name = name
        self.config = config
        self.last_error = ""   # détail du dernier échec de set_value (pour l'API)

    def start(self):
        """Démarrage (connexion MQTT, etc.). Optionnel."""

    def stop(self):
        """Arrêt propre. Optionnel."""

    def discover(self) -> list[dict]:
        """Liste de dicts : external_id, name, kind, unit, room, meta."""
        raise NotImplementedError

    def poll(self, devices: list[dict]) -> dict[str, float | str]:
        """{external_id: valeur} pour les périphériques demandés."""
        raise NotImplementedError

    def set_value(self, device: dict, value: str) -> bool:
        raise NotImplementedError
