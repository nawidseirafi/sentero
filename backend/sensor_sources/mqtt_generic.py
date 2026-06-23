from __future__ import annotations

from .zigbee2mqtt import Zigbee2MqttSensorSource


class MqttGenericSensorSource(Zigbee2MqttSensorSource):
    name = "mqtt"

