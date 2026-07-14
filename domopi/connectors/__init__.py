"""Registre des connecteurs disponibles."""
from .eedomus import EedomusConnector
from .wes_mqtt import WesMqttConnector

REGISTRY = {
    EedomusConnector.type_name: EedomusConnector,
    WesMqttConnector.type_name: WesMqttConnector,
}
