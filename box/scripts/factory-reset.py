#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BOX_DIR = Path(os.getenv("SENTERO_BOX_DIR", "/opt/sentero/box"))
STATE_FILE = Path(os.getenv("SENTERO_FACTORY_RESET_STATE", "/var/lib/sentero/factory-reset-state.json"))
NO_REBOOT = os.getenv("SENTERO_FACTORY_RESET_NO_REBOOT", "").lower() in {"1", "true", "yes", "on"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_state(status: str, **extra: Any) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, "updated_at": utc_now(), **extra}
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def log(message: str) -> None:
    print(f"[{utc_now()}] {message}", flush=True)


def run(command: list[str], *, timeout: float = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
    log("RUN " + " ".join(command))
    result = subprocess.run(command, cwd=BOX_DIR, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Befehl fehlgeschlagen ({result.returncode}): {' '.join(command)}: {detail}")
    return result


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


def render_env_template(template: str, env: dict[str, str]) -> str:
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    return pattern.sub(lambda match: env.get(match.group(1), os.getenv(match.group(1), "")), template)


def metadata(path: Path) -> tuple[int, int, int]:
    try:
        stat = path.stat()
        return stat.st_uid, stat.st_gid, stat.st_mode & 0o777
    except OSError:
        return 0, 0, 0o755


def recreate_dir(path: Path, uid: int, gid: int, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(path, uid, gid)
    except PermissionError:
        pass
    path.chmod(mode or 0o755)


def stage_directory(path: Path, staging_root: Path) -> dict[str, Any]:
    uid, gid, mode = metadata(path)
    staged = staging_root / path.relative_to(BOX_DIR)
    staged.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    if existed:
        os.replace(path, staged)
    recreate_dir(path, uid, gid, mode)
    return {"path": path, "staged": staged, "existed": existed, "uid": uid, "gid": gid, "mode": mode}


def restore_directory(item: dict[str, Any]) -> None:
    path: Path = item["path"]
    staged: Path = item["staged"]
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    if item["existed"] and staged.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, path)


def nm_wifi_uuids() -> list[str]:
    result = run(["nmcli", "-t", "-f", "UUID,TYPE", "connection", "show"], timeout=15, check=False)
    if result.returncode != 0:
        raise RuntimeError("NetworkManager-Verbindungen konnten nicht gelesen werden.")
    uuids: list[str] = []
    for line in result.stdout.splitlines():
        uuid, sep, typ = line.partition(":")
        if sep and typ.strip() in {"wifi", "802-11-wireless"} and uuid.strip():
            uuids.append(uuid.strip())
    return uuids


def backup_networkmanager(staging_root: Path) -> Path | None:
    source = Path(os.getenv("SENTERO_NM_CONNECTION_DIR", "/etc/NetworkManager/system-connections"))
    if not source.exists():
        return None
    target = staging_root / "networkmanager-system-connections"
    shutil.copytree(source, target, symlinks=True)
    return target


def restore_networkmanager(backup: Path | None) -> None:
    if backup is None or not backup.exists():
        return
    destination = Path(os.getenv("SENTERO_NM_CONNECTION_DIR", "/etc/NetworkManager/system-connections"))
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(backup, destination, symlinks=True)
    run(["nmcli", "connection", "reload"], timeout=15, check=False)


def regenerate_zigbee_configuration() -> None:
    data_dir = BOX_DIR / "zigbee2mqtt" / "data"
    template = data_dir / "configuration.yaml.example"
    if not template.exists():
        raise RuntimeError("Zigbee2MQTT-Konfigurationsvorlage fehlt.")
    env = read_env(BOX_DIR / ".env")
    rendered = render_env_template(template.read_text(encoding="utf-8"), env)
    (data_dir / "configuration.yaml").write_text(rendered, encoding="utf-8")


def perform_reset() -> None:
    started_at = utc_now()
    write_state("running", started_at=started_at, phase="stop_services")

    # Stop application containers before touching their persistent files.  The
    # host network/updater services intentionally stay alive until reboot.
    run(["docker", "compose", "down", "--remove-orphans"], timeout=120, check=True)

    with tempfile.TemporaryDirectory(prefix="sentero-factory-reset-", dir="/var/tmp") as temp_name:
        staging_root = Path(temp_name)
        network_backup = backup_networkmanager(staging_root)
        staged_dirs: list[dict[str, Any]] = []
        try:
            write_state("running", started_at=started_at, phase="stage_customer_data")
            for relative in ("data/sentero", "zigbee2mqtt/data", "mosquitto/data", "mosquitto/log", "backups"):
                staged_dirs.append(stage_directory(BOX_DIR / relative, staging_root))

            # Sentero runs as UID/GID 10001 in the application container.
            data_dir = BOX_DIR / "data" / "sentero"
            try:
                os.chown(data_dir, 10001, 10001)
            except PermissionError:
                pass
            data_dir.chmod(0o755)

            # Preserve only the static Zigbee template, then generate a fresh
            # coordinator configuration with the box's internal MQTT secret.
            old_zigbee = next(item for item in staged_dirs if item["path"] == BOX_DIR / "zigbee2mqtt/data")
            old_template = old_zigbee["staged"] / "configuration.yaml.example"
            new_template = BOX_DIR / "zigbee2mqtt/data/configuration.yaml.example"
            if not old_template.exists():
                raise RuntimeError("Zigbee2MQTT-Konfigurationsvorlage fehlt im bestehenden System.")
            shutil.copy2(old_template, new_template)
            regenerate_zigbee_configuration()

            write_state("running", started_at=started_at, phase="remove_saved_wifi")
            # Delete Wi-Fi profiles only. Ethernet profiles and externally
            # managed LAN configuration remain untouched.
            for uuid in nm_wifi_uuids():
                result = run(["nmcli", "connection", "delete", "uuid", uuid], timeout=20, check=False)
                if result.returncode != 0:
                    raise RuntimeError(f"Gespeichertes WLAN-Profil konnte nicht gelöscht werden: {uuid}")
            run(["nmcli", "connection", "reload"], timeout=15, check=False)

            # A successful reset deliberately preserves system software,
            # current Sentero version, .env/internal MQTT credentials, box ID,
            # generated QR label, config/sentero.yaml and Ollama models.
            write_state(
                "completed",
                finished_at=utc_now(),
                phase="reboot",
                preserved=["software", "version", "box_identity", "device_identity", "ethernet", "mqtt_internal_credentials", "qr_label", "ollama_models"],
                reset=["sentero_data", "users", "sensors", "automations", "notifications", "zigbee_runtime", "mqtt_runtime", "saved_wifi", "backups"],
            )
        except Exception:
            log("Reset preparation failed; restoring staged customer data and NetworkManager state.")
            for item in reversed(staged_dirs):
                restore_directory(item)
            restore_networkmanager(network_backup)
            # The compose stack was stopped at the beginning.  Restore normal
            # operation as a best effort so a failed reset does not strand the
            # customer with an otherwise healthy box offline.
            run(["systemctl", "restart", "sentero-box.service"], timeout=90, check=False)
            raise

    if NO_REBOOT:
        log("Factory reset completed; reboot suppressed by test setting.")
        return

    # Return control to systemd and request a clean reboot.  On the next boot
    # sentero-box.service applies the normal LAN-first state machine.
    log("Factory reset completed; rebooting appliance.")
    subprocess.Popen(["systemctl", "reboot"], cwd=BOX_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def main() -> int:
    try:
        perform_reset()
        return 0
    except Exception as exc:
        log(f"Factory reset failed: {exc}")
        write_state("failed", finished_at=utc_now(), error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
