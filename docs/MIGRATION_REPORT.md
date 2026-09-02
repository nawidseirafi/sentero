# Sentero Migration Report

> Historisches Migrationsdokument. Angaben zu frueheren Sensorquellen beschreiben den Zwischenstand der Extraktion und nicht den aktuellen Laufzeitstand.

## Copied Files

- `agent-api/backend/agents/sentero/*` -> `sentero/backend/`
- `agent-api/frontend/src/apps/sentero/*` -> `sentero/frontend/src/`
- `agent-api/frontend/index.html` -> `sentero/frontend/index.html`
- `agent-api/frontend/package.json` / lockfile / Vite / TypeScript / PostCSS / Tailwind config -> `sentero/frontend/`

## New Standalone Files

- `sentero/backend/main.py`
- `sentero/backend/paths.py`
- `sentero/backend/config.py`
- `sentero/backend/services/messaging.py`
- `sentero/backend/services/llm/factory.py`
- `sentero/backend/sensor_sources/*`
- `sentero/backend/update_service.py`
- `sentero/config/sentero.yaml`
- `sentero/docker/Dockerfile`
- `sentero/docker/mosquitto.conf`
- `sentero/requirements.txt`
- `sentero/.env.example`
- `sentero/deployment_build.py`
- `sentero/README.md`
- `sentero/docs/*`
- `sentero/etc/esp32/*`

## Removed Dependencies

- Removed Sentero from RoboterSteve frontend entry selection.
- Removed `@sentero` Vite/TypeScript aliases from RoboterSteve.
- Removed Sentero-specific auth middleware from RoboterSteve FastAPI `main.py`.
- Removed Sentero backend agent directory from `agent-api/backend/agents/sentero`.
- Removed Sentero frontend app directory from `agent-api/frontend/src/apps/sentero`.
- Removed `agent-api/editions/sentero.yaml`.
- Removed `agent-api/tests/test_sentero_discovery_candidates.py`.
- Removed Sentero agent manifest from the new standalone project.
- Removed system update panel usage from standalone Sentero settings.
- Reintroduced a standalone Sentero update mechanism without RoboterSteve editions, agent registry or orchestrator dependencies.
- Replaced RoboterSteve imports in Sentero backend with local `backend.*` package imports.

## Sensor Architecture

Aktueller Stand:
- Zigbee laeuft ueber Zigbee2MQTT; ESP32/generische Sensoren laufen direkt ueber denselben MQTT-Broker.
- Der persistente MQTT-Listener/-Cache bleibt Bestandteil der Sensorpipeline.
- Die Box-v2-Deployment-Schicht verwendet Docker Compose plus hostseitigen Updater.

## Open TODOs

- Add production authentication/TLS configuration for Mosquitto.
- Add tests for standalone auth, setup, behavior assessment and sensor-source selection.
- Replace the rule-based local LLM fallback with configurable provider clients if AI assessment is required in production.
- Add signed manifests before using ZIP updates for unattended production deployments.
- Add/version the full `box/` deployment tree if initial customer appliance packages should be built from this repository.

## Remaining RoboterSteve Couplings

- None in runtime imports of the standalone Sentero backend.
- The standalone frontend has its own local API client and no longer imports RoboterSteve shared code.
- Development und Produktion verwenden die gleiche MQTT-/Zigbee2MQTT-Sensorarchitektur.
- Historical Sentero references remain in RoboterSteve documentation/update tooling and in unused shared frontend API type/method definitions. They are not part of the active Sentero runtime after extraction, but should be cleaned in a follow-up documentation/build-tools pass if RoboterSteve must contain zero textual Sentero references.

## Verification

- Die Testsuite wird mit `python -W default::ResourceWarning -m unittest discover -s tests -p "test_*.py" -v` ausgefuehrt. Die konkrete Testanzahl ist versionsabhaengig.
