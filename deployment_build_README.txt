Sentero deployment_build.py – Appliance v2

Ersetzt den alten dateibasierten Update-ZIP-Build.

Wichtig:
- erwartet docker/Dockerfile.appliance
- optional erwartet box/ fuer das initiale Kunden-Deployment
- baut Frontend innerhalb des Docker-Multi-Stage-Builds
- erzeugt sentero-box-<version>.zip mit release.json + sentero-image.tar
- erzeugt latest.json mit appliance.bundle_url/sha256/size_bytes
- behält build/updates/sentero/<channel>/... als Server-Verzeichnisstruktur bei

Beispiel:
DOCKER_DEFAULT_PLATFORM=linux/amd64 \
python3 deployment_build.py \
  --version 0.2.0 \
  --base-url https://seirafi.de/robotersteve/sentero \
  --release-note "Sentero Box Update 0.2.0"

Nur Metadaten/Test ohne Docker-Export:
  python3 deployment_build.py --version 0.2.0 \
    --base-url https://seirafi.de/robotersteve/sentero \
    --skip-docker-build --skip-docker-save
