#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BOX_DIR = Path(os.getenv("SENTERO_BOX_DIR", "/opt/sentero/box"))
ENV_FILE = BOX_DIR / ".env"
SOCKET_PATH = Path(os.getenv("SENTERO_UPDATER_SOCKET", "/run/sentero-updater/updater.sock"))
STATE_FILE = BOX_DIR / "data" / "sentero" / "system" / "host_update_state.json"
BACKUP_DIR = BOX_DIR / "backups"
DB_FILE = BOX_DIR / "data" / "sentero" / "sentero.db"
MAX_BUNDLE_BYTES = int(os.getenv("SENTERO_UPDATER_MAX_BUNDLE_BYTES", str(1024 * 1024 * 1024)))
TARGET_PLATFORM = os.getenv("SENTERO_TARGET_PLATFORM", "linux/amd64").strip().lower() or "linux/amd64"
HEALTH_TIMEOUT_SECONDS = int(os.getenv("SENTERO_UPDATER_HEALTH_TIMEOUT", "90"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_state(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def run(command: list[str], *, cwd: Path = BOX_DIR) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()




def image_platform(image: str) -> str:
    return run([
        "docker", "image", "inspect", image,
        "--format", "{{.Os}}/{{.Architecture}}",
    ]).strip().lower()


def wait_for_health(container: str = "sentero", timeout_seconds: int = HEALTH_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + max(timeout_seconds, 1)
    last_error = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "curl", "-fsS", "http://127.0.0.1:8080/health"],
            cwd=BOX_DIR,
            text=True,
            capture_output=True,
        )
        if result.returncode == 0:
            return
        last_error = (result.stderr or result.stdout or "healthcheck failed").strip()
        time.sleep(2)
    raise RuntimeError(
        f"Sentero healthcheck nach {timeout_seconds}s fehlgeschlagen: {last_error}"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_url(env: dict[str, str], channel: str) -> str:
    configured = env.get("SENTERO_UPDATE_MANIFEST_URL") or env.get("UPDATE_MANIFEST_URL") or ""
    if configured:
        return configured
    base_url = env.get("SENTERO_UPDATE_BASE_URL") or env.get("UPDATE_BASE_URL") or ""
    if not base_url:
        raise RuntimeError("Kein Update-Server konfiguriert.")
    return f"{base_url.rstrip('/')}/{channel}/latest.json"


def load_json_url(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_for_channel(manifest: dict[str, Any], channel: str) -> dict[str, Any]:
    channels = manifest.get("channels")
    if isinstance(channels, dict):
        latest = channels.get(channel)
    else:
        latest = manifest
    if not isinstance(latest, dict):
        raise RuntimeError(f"Channel nicht im Manifest gefunden: {channel}")
    return latest


def download(url: str, target: Path, max_bytes: int) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as output:
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError("Update-Bundle ist groesser als erlaubt.")
            output.write(chunk)


def backup_sqlite(target_version: str) -> Path | None:
    if not DB_FILE.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"sentero-db-before-{target_version}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite"
    source = sqlite3.connect(DB_FILE)
    try:
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return backup_path


def restore_sqlite(backup_path: Path | None) -> None:
    if backup_path and backup_path.exists():
        shutil.copy2(backup_path, DB_FILE)


def install_update(payload: dict[str, Any]) -> dict[str, Any]:
    env = read_env(ENV_FILE)
    channel = str(payload.get("channel") or env.get("SENTERO_UPDATE_CHANNEL") or "stable")
    target_version = str(payload.get("target_version") or "").strip()
    if channel not in {"stable", "beta", "dev"}:
        raise RuntimeError("Ungueltiger Update-Channel.")
    if not target_version:
        raise RuntimeError("target_version fehlt.")

    state = {"status": "running", "channel": channel, "target_version": target_version, "started_at": utc_now()}
    write_state(state)

    previous_image = run(["docker", "inspect", "--format={{.Config.Image}}", "sentero"]) if container_exists("sentero") else ""
    db_backup: Path | None = None
    with tempfile.TemporaryDirectory(prefix="sentero-update-") as tmp:
        tmpdir = Path(tmp)
        manifest = load_json_url(manifest_url(env, channel))
        latest = latest_for_channel(manifest, channel)
        if str(latest.get("latest_version") or "") != target_version:
            raise RuntimeError("Manifest-Version passt nicht zur angeforderten Zielversion.")
        appliance = latest.get("appliance")
        if not isinstance(appliance, dict):
            raise RuntimeError("Manifest enthaelt kein appliance-Bundle.")
        bundle_url = str(appliance.get("bundle_url") or "")
        expected_hash = str(appliance.get("sha256") or "").lower()
        expected_size = int(appliance.get("size_bytes") or 0)
        if not bundle_url or len(expected_hash) != 64:
            raise RuntimeError("Ungueltige Bundle-Metadaten.")
        if expected_size and expected_size > MAX_BUNDLE_BYTES:
            raise RuntimeError("Update-Bundle ist groesser als erlaubt.")

        bundle = tmpdir / "bundle.zip"
        download(bundle_url, bundle, MAX_BUNDLE_BYTES)
        if sha256(bundle).lower() != expected_hash:
            raise RuntimeError("SHA-256-Pruefung fehlgeschlagen.")

        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            if {"release.json", "sentero-image.tar"} - names:
                raise RuntimeError("Update-Bundle ist unvollstaendig.")
            archive.extract("release.json", tmpdir)
            archive.extract("sentero-image.tar", tmpdir)

        release = json.loads((tmpdir / "release.json").read_text(encoding="utf-8"))
        release_version = str(release.get("version") or "").strip()
        release_image = str(release.get("image") or appliance.get("image") or "").strip()
        image_repo, image_tag = split_image_reference(release_image)
        if release_version != target_version:
            raise RuntimeError("Release-Version passt nicht zur angeforderten Zielversion.")
        if image_tag != target_version:
            raise RuntimeError("Docker-Image-Tag passt nicht zur angeforderten Zielversion.")

        db_backup = backup_sqlite(target_version)
        run(["docker", "load", "-i", str(tmpdir / "sentero-image.tar")])
        actual_platform = image_platform(release_image)
        if actual_platform != TARGET_PLATFORM:
            raise RuntimeError(
                f"Docker-Image hat Plattform {actual_platform}, erwartet wird {TARGET_PLATFORM}."
            )
        # Keep repository and version separate because docker-compose.yml combines
        # them as ${SENTERO_IMAGE}:${SENTERO_VERSION}. This also repairs older
        # installations that accidentally stored a tagged image in SENTERO_IMAGE.
        replace_env_value(ENV_FILE, "SENTERO_IMAGE", image_repo)
        replace_env_value(ENV_FILE, "SENTERO_VERSION", target_version)
        run(["docker", "compose", "up", "-d", "sentero"])
        wait_for_health("sentero")

    final = {**state, "status": "success", "finished_at": utc_now(), "db_backup": str(db_backup) if db_backup else None}
    write_state(final)
    return {"ok": True, **final}



def split_image_reference(image: str) -> tuple[str, str]:
    """Return (repository, tag) for an explicitly tagged Docker image."""
    image = image.strip()
    if not image or "@" in image:
        raise RuntimeError("Release enthaelt kein gueltiges getaggtes Docker-Image.")
    slash = image.rfind("/")
    colon = image.rfind(":")
    if colon <= slash or colon == len(image) - 1:
        raise RuntimeError("Release enthaelt kein gueltiges getaggtes Docker-Image.")
    return image[:colon], image[colon + 1 :]

def container_exists(name: str) -> bool:
    result = subprocess.run(["docker", "inspect", name], text=True, capture_output=True)
    return result.returncode == 0


def replace_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    output = []
    for line in lines:
        if line.startswith(f"{key}="):
            output.append(f"{key}={value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("action") != "install":
        return {"ok": False, "error": "Nicht unterstuetzte Aktion."}
    try:
        return install_update(payload)
    except Exception as exc:
        write_state({"status": "failed", "error": str(exc), "finished_at": utc_now()})
        return {"ok": False, "error": str(exc)}


def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o660)
    try:
        os.chown(SOCKET_PATH, 0, 10001)
    except PermissionError:
        pass
    server.listen(5)
    while True:
        connection, _ = server.accept()
        with connection:
            raw = connection.recv(65536).split(b"\n", 1)[0].decode("utf-8", errors="replace")
            response = handle(json.loads(raw or "{}"))
            try:
                connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            except BrokenPipeError:
                # The caller may have disconnected while a long update was running.
                # The persisted state remains authoritative; keep the updater alive.
                pass


if __name__ == "__main__":
    main()
