"""Registre des connecteurs disponibles."""
from .eedomus import EedomusConnector
from .wes_mqtt import WesMqttConnector
from .yamaha import YamahaConnector

REGISTRY = {
    EedomusConnector.type_name: EedomusConnector,
    WesMqttConnector.type_name: WesMqttConnector,
    YamahaConnector.type_name: YamahaConnector,
}
