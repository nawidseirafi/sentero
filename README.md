# Sentero

Sentero is a standalone care-signal product extracted from RoboterSteve.

## Architecture

- `backend/`: FastAPI API, authentication, behavior assessment, setup flow, notification channels and sensor mapping.
- `frontend/`: Vite/React Sentero app.
- `config/`: standalone Sentero configuration.
- `docker/`: container and Mosquitto configuration.
- `data/`: runtime SQLite and adapter data.
- `docs/`: product documentation.

Sentero does not use RoboterSteve editions, agent registry, orchestrator, agent control or RoboterSteve-specific APIs.

## Sensor Sources

Configure the source through:

```bash
SENTERO_SENSOR_SOURCE=homeassistant|mqtt|mixed
```

Development may use Home Assistant. Production is designed for Zigbee2MQTT, Mosquitto, MQTT and ESP32 sensors without Home Assistant.

## Local Development

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080
```

In another shell:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Docker

Production Docker uses Mosquitto/Zigbee2MQTT/MQTT by default and does not require Home Assistant. Build the frontend first, then start the stack:

```bash
cd frontend
npm install
npm run build
cd ..
docker compose up --build -d
```

For a production Zigbee2MQTT container as part of the stack:

```bash
docker compose --profile production up --build -d
```

Docker forces `SENTERO_SENSOR_SOURCE=mqtt` unless `SENTERO_DOCKER_SENSOR_SOURCE` is set explicitly. This keeps local Home Assistant development settings from leaking into the production container.

## Deployment Build

Create an installable directory and update ZIP artifacts:

```bash
UPDATE_BASE_URL=https://seirafi.de/robotersteve/sentero python3 deployment_build.py --version 0.1.1
```

Outputs:

- `build/sentero/`
- `build/updates/sentero/stable/latest.json`
- `build/updates/sentero/stable/releases/sentero-<version>.zip`

## Updates

Sentero has a standalone update API under `/api/sentero/system/update/*`.

Default mode is `dry_run`, so update checks and UI flow work without modifying files. For ZIP-based application updates, publish `build/updates/sentero/stable/latest.json` and the matching ZIP files under `stable/releases/`, then set:

```bash
SENTERO_UPDATE_MODE=zip
UPDATE_BASE_URL=https://example.com/sentero
```

The runtime derives `https://example.com/sentero/stable/latest.json`; the build derives ZIP download URLs like `https://example.com/sentero/stable/releases/sentero-<version>.zip`.

The ZIP installer updates application files only and never overwrites `.env`, `data/`, `backups/`, virtualenvs or `node_modules`.
