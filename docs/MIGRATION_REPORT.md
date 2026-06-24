# Sentero Migration Report

## Copied Files

- `agent-api/backend/agents/sentero/*` -> `sentero/backend/`
- `agent-api/frontend/src/apps/sentero/*` -> `sentero/frontend/src/`
- `agent-api/frontend/index.html` -> `sentero/frontend/index.html`
- `agent-api/frontend/package.json` / lockfile / Vite / TypeScript / PostCSS / Tailwind config -> `sentero/frontend/`

## New Standalone Files

- `sentero/backend/main.py`
- `sentero/backend/paths.py`
- `sentero/backend/config.py`
- `sentero/backend/services/homeassistant_service.py`
- `sentero/backend/services/messaging.py`
- `sentero/backend/services/llm/factory.py`
- `sentero/backend/sensor_sources/*`
- `sentero/backend/update_service.py`
- `sentero/config/sentero.yaml`
- `sentero/docker/Dockerfile`
- `sentero/docker/mosquitto.conf`
- `sentero/docker-compose.yml`
- `sentero/requirements.txt`
- `sentero/.env.example`
- `sentero/deployment_build.py`
- `sentero/README.md`
- `sentero/docs/SENSOR_ARCHITECTURE.md`

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

- Added `SENTERO_SENSOR_SOURCE=homeassistant|mqtt|mixed`.
- Added Home Assistant adapter for development.
- Added Zigbee2MQTT/MQTT-oriented production adapter scaffold.
- Added mixed adapter for transitional deployments.
- Docker Compose now includes Mosquitto and optional Zigbee2MQTT profile.

## Open TODOs

- Implement persistent MQTT subscription/event ingestion instead of the current bootstrap scaffold in `backend/sensor_sources/zigbee2mqtt.py`.
- Add production authentication/TLS configuration for Mosquitto.
- Add tests for standalone auth, setup, behavior assessment and sensor-source selection.
- Replace the rule-based local LLM fallback with configurable provider clients if AI assessment is required in production.
- Add signed manifests before using ZIP updates for unattended production deployments.

## Remaining RoboterSteve Couplings

- None in runtime imports of the standalone Sentero backend.
- The standalone frontend has its own local API client and no longer imports RoboterSteve shared code.
- Development mode may still use Home Assistant by design; this is not required for production.
- Historical Sentero references remain in RoboterSteve documentation/update tooling and in unused shared frontend API type/method definitions. They are not part of the active Sentero runtime after extraction, but should be cleaned in a follow-up documentation/build-tools pass if RoboterSteve must contain zero textual Sentero references.

## Verification

- `PYTHONPYCACHEPREFIX=/tmp/sentero-pycache python3 -m compileall sentero/backend` passed.
- `PYTHONPATH=/Users/nawid/Projects/roboterSteve/sentero /Users/nawid/Projects/roboterSteve/venv/bin/python -c "import backend.main"` passed.
- `SenteroUpdateService().check_for_updates()` passed against the local standalone manifest.
- `cd sentero/frontend && npm install && npm run build` passed.
