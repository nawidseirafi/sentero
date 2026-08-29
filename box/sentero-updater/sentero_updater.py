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
from pathlib import Path, PurePosixPath
from typing import Any


BOX_DIR = Path(os.getenv("SENTERO_BOX_DIR", "/opt/sentero/box"))
ENV_FILE = BOX_DIR / ".env"
SOCKET_PATH = Path(os.getenv("SENTERO_UPDATER_SOCKET", "/run/sentero-updater/updater.sock"))
STATE_FILE = BOX_DIR / "data" / "sentero" / "system" / "host_update_state.json"
BACKUP_DIR = BOX_DIR / "backups"
DB_FILE = BOX_DIR / "data" / "sentero" / "sentero.db"
MAX_BUNDLE_BYTES = int(os.getenv("SENTERO_UPDATER_MAX_BUNDLE_BYTES", str(1024 * 1024 * 1024)))
HEALTH_TIMEOUT_SECONDS = int(os.getenv("SENTERO_UPDATER_HEALTH_TIMEOUT_SECONDS", "120"))
COMPOSE_TIMEOUT_SECONDS = int(os.getenv("SENTERO_UPDATER_COMPOSE_TIMEOUT_SECONDS", "90"))

# Only these host paths may ever be replaced by an appliance update. Persistent
# customer/runtime state (.env, data, backups, MQTT passwords, Zigbee runtime
# config, Ollama data, ...) is intentionally not in this allow-list.
HOST_FILE_ROOTS = {
    ".env.example",
    "docker-compose.yml",
    "scripts",
    "sentero-network",
    "sentero-updater",
    "systemd",
    "zigbee2mqtt/data/configuration.yaml.example",
}


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
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def log(message: str) -> None:
    print(f"[{utc_now()}] {message}", flush=True)


def run(command: list[str], *, cwd: Path = BOX_DIR, timeout: float | None = None) -> str:
    started = time.monotonic()
    display = " ".join(command)
    log(f"START {display}")
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        stdout = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        log(f"TIMEOUT after {elapsed:.1f}s: {display}; stdout={stdout!r}; stderr={stderr!r}")
        raise RuntimeError(f"Befehl hat nach {elapsed:.0f}s das Zeitlimit ueberschritten: {display}") from exc
    except subprocess.CalledProcessError as exc:
        elapsed = time.monotonic() - started
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        log(f"FAILED after {elapsed:.1f}s: {display}; rc={exc.returncode}; stdout={stdout!r}; stderr={stderr!r}")
        raise
    elapsed = time.monotonic() - started
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    log(f"DONE after {elapsed:.1f}s: {display}; stdout={stdout!r}; stderr={stderr!r}")
    return stdout


def run_no_fail(command: list[str], *, cwd: Path = BOX_DIR) -> bool:
    return subprocess.run(command, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    latest = channels.get(channel) if isinstance(channels, dict) else manifest
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


def safe_host_path(relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise RuntimeError(f"Ungueltiger Host-Dateipfad: {relative!r}")
    normalized = pure.as_posix()
    allowed = False
    for root in HOST_FILE_ROOTS:
        if normalized == root or normalized.startswith(root.rstrip("/") + "/"):
            allowed = True
            break
    if not allowed:
        raise RuntimeError(f"Host-Datei ist nicht fuer Updates freigegeben: {relative}")
    return BOX_DIR.joinpath(*pure.parts)


def extract_and_verify_host_files(archive: zipfile.ZipFile, release: dict[str, Any], target: Path) -> list[dict[str, Any]]:
    metadata = release.get("host_files") or []
    if not isinstance(metadata, list):
        raise RuntimeError("Ungueltige Host-Dateiliste im Release.")
    target.mkdir(parents=True, exist_ok=True)
    verified: list[dict[str, Any]] = []
    names = set(archive.namelist())
    for item in metadata:
        if not isinstance(item, dict):
            raise RuntimeError("Ungueltiger Host-Dateieintrag im Release.")
        relative = str(item.get("path") or "")
        expected = str(item.get("sha256") or "").lower()
        mode = int(item.get("mode") or 0o644) & 0o777
        safe_host_path(relative)  # validates allow-list and traversal
        member = f"host/{relative}"
        if member not in names:
            raise RuntimeError(f"Host-Datei fehlt im Bundle: {relative}")
        data = archive.read(member)
        if len(expected) != 64 or sha256_bytes(data).lower() != expected:
            raise RuntimeError(f"SHA-256-Pruefung der Host-Datei fehlgeschlagen: {relative}")
        destination = target.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(mode)
        verified.append({"path": relative, "mode": mode})
    return verified


def backup_host_files(files: list[dict[str, Any]], backup_root: Path) -> None:
    for item in files:
        relative = str(item["path"])
        source = safe_host_path(relative)
        if not source.exists() or not source.is_file():
            continue
        destination = backup_root.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def install_host_files(files: list[dict[str, Any]], staged_root: Path) -> list[str]:
    updated: list[str] = []
    for item in files:
        relative = str(item["path"])
        mode = int(item.get("mode") or 0o644) & 0o777
        source = staged_root.joinpath(*PurePosixPath(relative).parts)
        destination = safe_host_path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".sentero-new")
        shutil.copyfile(source, temp)
        temp.chmod(mode)
        os.replace(temp, destination)
        updated.append(relative)
    return updated


def restore_host_files(files: list[dict[str, Any]], backup_root: Path) -> None:
    for item in files:
        relative = str(item["path"])
        backup = backup_root.joinpath(*PurePosixPath(relative).parts)
        destination = safe_host_path(relative)
        if backup.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)


def wait_for_sentero_health(timeout_seconds: int = HEALTH_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout_seconds
    last = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", "sentero", "curl", "-fsS", "http://127.0.0.1:8080/health"],
            cwd=BOX_DIR,
            text=True,
            capture_output=True,
        )
        if result.returncode == 0:
            return
        last = (result.stderr or result.stdout or "").strip()
        time.sleep(2)
    raise RuntimeError(f"Sentero wurde nicht rechtzeitig healthy: {last or 'Timeout'}")


def refresh_host_services(updated_files: list[str]) -> bool:
    if not updated_files:
        return False
    if any(path.startswith("systemd/") for path in updated_files):
        run(["systemctl", "daemon-reload"])
    if any(path.startswith("sentero-network/") or path == "systemd/sentero-network.service" for path in updated_files):
        run(["systemctl", "restart", "sentero-network.service"])
    return any(path.startswith("sentero-updater/") or path == "systemd/sentero-updater.service" for path in updated_files)


def schedule_self_restart() -> bool:
    unit = f"sentero-updater-restart-{os.getpid()}"
    return run_no_fail([
        "systemd-run",
        "--quiet",
        "--collect",
        f"--unit={unit}",
        "--on-active=2s",
        "/bin/systemctl",
        "restart",
        "sentero-updater.service",
    ])


def install_update(payload: dict[str, Any]) -> dict[str, Any]:
    env = read_env(ENV_FILE)
    channel = str(payload.get("channel") or env.get("SENTERO_UPDATE_CHANNEL") or "stable")
    target_version = str(payload.get("target_version") or "").strip()
    if channel not in {"stable", "beta", "dev"}:
        raise RuntimeError("Ungueltiger Update-Channel.")
    if not target_version:
        raise RuntimeError("target_version fehlt.")

    state: dict[str, Any] = {
        "status": "running",
        "channel": channel,
        "target_version": target_version,
        "started_at": utc_now(),
    }
    write_state(state)

    previous_image = run(["docker", "inspect", "--format={{.Config.Image}}", "sentero"]) if container_exists("sentero") else ""
    previous_version = env.get("SENTERO_VERSION", "")
    db_backup: Path | None = None
    updated_host_files: list[str] = []
    updater_restart_required = False

    with tempfile.TemporaryDirectory(prefix="sentero-update-") as tmp:
        tmpdir = Path(tmp)
        host_stage = tmpdir / "host-stage"
        host_backup = tmpdir / "host-backup"
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
            release = json.loads(archive.read("release.json").decode("utf-8"))
            if str(release.get("version") or "") != target_version:
                raise RuntimeError("release.json passt nicht zur Zielversion.")
            image_data_target = tmpdir / "sentero-image.tar"
            with archive.open("sentero-image.tar") as source, image_data_target.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            host_files = extract_and_verify_host_files(archive, release, host_stage)

        db_backup = backup_sqlite(target_version)
        backup_host_files(host_files, host_backup)

        try:
            # Load the app first, then atomically replace only allow-listed host
            # files. The new compose file is therefore already active when the
            # versioned application container is recreated.
            run(["docker", "load", "-i", str(tmpdir / "sentero-image.tar")])
            updated_host_files = install_host_files(host_files, host_stage)
            replace_env_value(ENV_FILE, "SENTERO_VERSION", target_version)
            # Updating the Sentero app must never implicitly pull/start unrelated
            # dependencies such as ollama. On a freshly provisioned box that can
            # turn a seconds-long app restart into a multi-minute operation.
            run(
                ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "sentero"],
                timeout=COMPOSE_TIMEOUT_SECONDS,
            )
            wait_for_sentero_health()
            updater_restart_required = refresh_host_services(updated_host_files)
        except Exception:
            restore_host_files(host_files, host_backup)
            if previous_version:
                replace_env_value(ENV_FILE, "SENTERO_VERSION", previous_version)
            restore_sqlite(db_backup)
            # Best-effort application rollback. If the previous compose image
            # reference is still available locally, restore its version/tag.
            if previous_image and ":" in previous_image:
                previous_tag = previous_image.rsplit(":", 1)[1]
                replace_env_value(ENV_FILE, "SENTERO_VERSION", previous_tag)
                run_no_fail(["docker", "compose", "up", "-d", "sentero"])
            run_no_fail(["systemctl", "daemon-reload"])
            if any(path.startswith("sentero-network/") or path == "systemd/sentero-network.service" for path in updated_host_files):
                run_no_fail(["systemctl", "restart", "sentero-network.service"])
            raise

    final = {
        **state,
        "status": "success",
        "finished_at": utc_now(),
        "db_backup": str(db_backup) if db_backup else None,
        "host_files_updated": len(updated_host_files),
        "host_update_applied": bool(updated_host_files),
        "updater_restart_required": updater_restart_required,
    }
    write_state(final)
    return {"ok": True, **final, "_restart_updater_after_response": updater_restart_required}


def container_exists(name: str) -> bool:
    result = subprocess.run(["docker", "inspect", name], text=True, capture_output=True)
    return result.returncode == 0


def replace_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
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


def read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "")
    if action == "status":
        return {"ok": True, "state": read_state()}
    if action != "install":
        return {"ok": False, "error": "Nicht unterstuetzte Aktion."}
    try:
        return install_update(payload)
    except Exception as exc:
        write_state({
            "status": "failed",
            "target_version": str(payload.get("target_version") or ""),
            "error": str(exc),
            "finished_at": utc_now(),
        })
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
        restart_after_response = False
        with connection:
            raw = connection.recv(65536).split(b"\n", 1)[0].decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw or "{}")
                response = handle(payload)
            except json.JSONDecodeError:
                response = {"ok": False, "error": "Ungueltige JSON-Anfrage."}
            restart_after_response = bool(response.pop("_restart_updater_after_response", False))
            connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        if restart_after_response:
            schedule_self_restart()


if __name__ == "__main__":
    main()
