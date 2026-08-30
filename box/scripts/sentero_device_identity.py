#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BOX_DIR = Path(os.getenv("SENTERO_BOX_DIR", "/opt/sentero/box"))
IDENTITY_DIR = Path(os.getenv("SENTERO_DEVICE_IDENTITY_DIR", str(BOX_DIR / "device")))
IDENTITY_FILE = Path(os.getenv("SENTERO_DEVICE_IDENTITY_FILE", str(IDENTITY_DIR / "identity.json")))
SCHEMA_VERSION = 1
SERIAL_RE = re.compile(r"^STB-[0-9]{8}$")
SETUP_SSID_PREFIX = "Sentero-Setup"


class DeviceIdentityError(RuntimeError):
    pass


class IdentityNotProvisioned(DeviceIdentityError):
    pass


class IdentityConflictError(DeviceIdentityError):
    pass


class InvalidIdentityError(DeviceIdentityError):
    pass


@dataclass(frozen=True)
class DeviceIdentity:
    schema_version: int
    serial_number: str
    device_id: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "serial_number": self.serial_number,
            "device_id": self.device_id,
            "created_at": self.created_at,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_serial_number(serial_number: str) -> str:
    value = (serial_number or "").strip().upper()
    if not SERIAL_RE.fullmatch(value):
        raise ValueError("Ungueltige Seriennummer. Erwartetes Format: STB-XXXXXXXX mit exakt 8 Dezimalziffern.")
    return value


def _validate_created_at(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidIdentityError("created_at fehlt in der Geräteidentität.")
    normalized = value.strip()
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidIdentityError("created_at in der Geräteidentität ist ungültig.") from exc
    return normalized


def validate_device_identity(data: Any) -> DeviceIdentity:
    if not isinstance(data, dict):
        raise InvalidIdentityError("identity.json muss ein JSON-Objekt enthalten.")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise InvalidIdentityError("Unbekannte oder fehlende schema_version in identity.json.")
    try:
        serial_number = validate_serial_number(str(data.get("serial_number") or ""))
    except ValueError as exc:
        raise InvalidIdentityError(str(exc)) from exc
    raw_device_id = data.get("device_id")
    if not isinstance(raw_device_id, str) or not raw_device_id.strip():
        raise InvalidIdentityError("device_id fehlt in der Geräteidentität.")
    try:
        parsed = uuid.UUID(raw_device_id.strip())
    except ValueError as exc:
        raise InvalidIdentityError("device_id ist keine gültige UUID.") from exc
    if parsed.version != 4:
        raise InvalidIdentityError("device_id muss eine UUID Version 4 sein.")
    created_at = _validate_created_at(data.get("created_at"))
    return DeviceIdentity(
        schema_version=SCHEMA_VERSION,
        serial_number=serial_number,
        device_id=str(parsed),
        created_at=created_at,
    )


def load_device_identity(path: Path = IDENTITY_FILE) -> DeviceIdentity:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise IdentityNotProvisioned("Keine Sentero Geräteidentität provisioniert.") from exc
    except OSError as exc:
        raise InvalidIdentityError(f"Geräteidentität konnte nicht gelesen werden: {exc}") from exc
    if not raw.strip():
        raise InvalidIdentityError("identity.json ist leer.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidIdentityError("identity.json enthält kein gültiges JSON.") from exc
    return validate_device_identity(data)


def has_provisioned_identity(path: Path = IDENTITY_FILE) -> bool:
    try:
        load_device_identity(path)
        return True
    except IdentityNotProvisioned:
        return False


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o755)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".identity-", suffix=".tmp", dir=str(path.parent), text=True)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.chmod(0o644)
        os.replace(tmp_path, path)
        _fsync_parent(path.parent)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def create_device_identity(serial_number: str, path: Path = IDENTITY_FILE) -> DeviceIdentity:
    serial = validate_serial_number(serial_number)
    try:
        existing = load_device_identity(path)
    except IdentityNotProvisioned:
        identity = DeviceIdentity(SCHEMA_VERSION, serial, str(uuid.uuid4()), utc_now())
        _atomic_write_json(path, identity.as_dict())
        return load_device_identity(path)
    except InvalidIdentityError:
        raise
    if existing.serial_number != serial:
        raise IdentityConflictError(
            "Diese Sentero Box besitzt bereits die Seriennummer:\n\n"
            f"{existing.serial_number}\n\n"
            "Die Geräteidentität wird aus Sicherheitsgründen nicht überschrieben."
        )
    return existing


def legacy_setup_suffix() -> str:
    try:
        ident = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    except OSError:
        ident = socket.gethostname()
    return hashlib.sha256((ident or "sentero").encode()).hexdigest()[:4].upper()


def get_setup_suffix(identity: DeviceIdentity | None = None) -> str:
    if identity is not None:
        return identity.serial_number[-4:]
    try:
        return load_device_identity().serial_number[-4:]
    except (IdentityNotProvisioned, InvalidIdentityError):
        return legacy_setup_suffix()


def get_setup_ssid(identity: DeviceIdentity | None = None) -> str:
    return f"{SETUP_SSID_PREFIX}-{get_setup_suffix(identity)}"


def identity_status(include_legacy: bool = True) -> dict[str, Any]:
    try:
        identity = load_device_identity()
        return {
            "identity_provisioned": True,
            "serial_number": identity.serial_number,
            "device_id": identity.device_id,
            "created_at": identity.created_at,
            "setup_ssid": get_setup_ssid(identity),
        }
    except IdentityNotProvisioned:
        result: dict[str, Any] = {
            "identity_provisioned": False,
            "serial_number": None,
            "device_id": None,
            "setup_ssid": get_setup_ssid(),
        }
        if include_legacy:
            result["legacy_box_id"] = legacy_setup_suffix()
        return result
    except InvalidIdentityError as exc:
        return {
            "identity_provisioned": False,
            "serial_number": None,
            "device_id": None,
            "setup_ssid": get_setup_ssid(identity=None),
            "identity_error": str(exc),
        }


def _print_identity(data: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if data.get("identity_provisioned"):
        print(f"Seriennummer: {data.get('serial_number')}")
        print(f"Geräte-ID:    {data.get('device_id')}")
        print(f"Setup-WLAN:   {data.get('setup_ssid')}")
    else:
        error = data.get("identity_error")
        if error:
            print(f"Geräteidentität beschädigt: {error}", file=sys.stderr)
        else:
            print("Seriennummer: Nicht provisioniert")
            print("Geräte-ID:    Nicht provisioniert")
            print(f"Setup-WLAN:   {data.get('setup_ssid')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentero Geräteidentität anzeigen oder validieren.")
    parser.add_argument("--show", action="store_true", help="Geräteidentität anzeigen.")
    parser.add_argument("--json", action="store_true", help="Maschinenlesbare JSON-Ausgabe.")
    parser.add_argument("--setup-ssid", action="store_true", help="Nur die aktuell gültige Setup-WLAN-SSID ausgeben.")
    parser.add_argument("--check-provisioned", action="store_true", help="Exit 0 bei gültiger Identity, 10 bei Legacy, 1 bei beschädigter Identity.")
    parser.add_argument("--validate-serial", help="Seriennummer validieren und normalisiert ausgeben.")
    args = parser.parse_args()

    try:
        if args.check_provisioned:
            try:
                load_device_identity()
                return 0
            except IdentityNotProvisioned:
                return 10
            except InvalidIdentityError as exc:
                print(f"Geräteidentität beschädigt: {exc}", file=sys.stderr)
                return 1
        if args.validate_serial:
            print(validate_serial_number(args.validate_serial))
            return 0
        if args.setup_ssid:
            print(get_setup_ssid())
            return 0
        if args.show or args.json:
            data = identity_status()
            _print_identity(data, as_json=args.json)
            return 1 if data.get("identity_error") else 0
        parser.print_help()
        return 0
    except DeviceIdentityError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
