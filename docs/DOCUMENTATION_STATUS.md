# Dokumentationsstatus

Stand: nach MQTT-/Home-Assistant-Cleanup und Sentero Box v2.

Verbindliche Architektur:
- Zigbee -> Zigbee2MQTT -> Mosquitto/MQTT -> Sentero
- ESP32/generische MQTT-Sensoren -> derselbe Mosquitto/MQTT -> Sentero
- Home Assistant ist keine Sensorquelle mehr.
- Es gibt keinen separaten `wifi_esphome`-/ESPHome-Provisioningpfad.
- Sentero Box v2 aktualisiert das Sentero-Image ueber den hostseitigen Appliance-Updater.
- Persistente Daten und Kundenkonfiguration liegen ausserhalb des austauschbaren Images.
- Lokales LLM wird ueber Ollama angebunden; externe Provider bleiben optionale Entwicklungs-/Fallback-Konfiguration.

Historische Dokumente duerfen fruehere Architekturentscheidungen nennen, muessen diese aber klar als historisch kennzeichnen.
