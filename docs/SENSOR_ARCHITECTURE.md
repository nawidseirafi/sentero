# Sentero Sensor Architecture

Sentero is source-agnostic. Home Assistant is available only as a development adapter and is not required in production.

## Sources

- Home Assistant Adapter: development mode, useful while pairing and testing existing HA entities.
- Zigbee2MQTT Adapter: production source for Zigbee sensors via Mosquitto.
- MQTT Generic Adapter: production source for ESP32 and custom MQTT sensors.

The ESP32/WLAN sensor payload and provisioning contract is documented in `docs/ESP32_WIFI_SENSOR_README.md`.

## Configuration

Set `SENTERO_SENSOR_SOURCE` to one of:

- `homeassistant`
- `mqtt`
- `mixed`

Production deployments should use `mqtt` or `mixed` with Zigbee2MQTT and Mosquitto.
