"""Registre des connecteurs disponibles."""
from .eedomus import EedomusConnector
from .wes_mqtt import WesMqttConnector
from .yamaha import YamahaConnector
from .virtual import VirtualConnector

REGISTRY = {
    EedomusConnector.type_name: EedomusConnector,
    WesMqttConnector.type_name: WesMqttConnector,
    YamahaConnector.type_name: YamahaConnector,
    # interne (capteurs calculés) : instance unique seedée par init_db,
    # jamais proposé à la création dans l'admin
    VirtualConnector.type_name: VirtualConnector,
}
