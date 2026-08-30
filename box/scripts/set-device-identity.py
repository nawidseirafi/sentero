#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from sentero_device_identity import (
    DeviceIdentityError,
    IdentityConflictError,
    create_device_identity,
    identity_status,
)


BOX_DIR = Path(__file__).resolve().parents[1]


def regenerate_label() -> dict[str, str]:
    script = BOX_DIR / "scripts" / "generate-setup-label.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(BOX_DIR),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "QR-/Setup-Label konnte nicht erzeugt werden.").strip())
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            parsed[key.strip().lower()] = value.strip()
    return parsed


def print_status(data: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if data.get("identity_provisioned"):
        print(f"Seriennummer: {data.get('serial_number')}")
        print(f"Geräte-ID:    {data.get('device_id')}")
        print(f"Setup-WLAN:   {data.get('setup_ssid')}")
    elif data.get("identity_error"):
        print(f"Geräteidentität beschädigt: {data.get('identity_error')}", file=sys.stderr)
    else:
        print("Seriennummer: Nicht provisioniert")
        print("Geräte-ID:    Nicht provisioniert")
        print(f"Setup-WLAN:   {data.get('setup_ssid')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentero Geräteidentität provisionieren oder anzeigen.")
    parser.add_argument("--serial", help="Offizielle Seriennummer im Format STB-XXXXXXXX.")
    parser.add_argument("--show", action="store_true", help="Geräteidentität anzeigen.")
    parser.add_argument("--json", action="store_true", help="Maschinenlesbare JSON-Ausgabe.")
    args = parser.parse_args()

    if args.serial:
        try:
            identity = create_device_identity(args.serial)
            label = regenerate_label()
        except IdentityConflictError as exc:
            print(str(exc), file=sys.stderr)
            return 3
        except (DeviceIdentityError, ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

        result = {
            "identity_provisioned": True,
            "serial_number": identity.serial_number,
            "device_id": identity.device_id,
            "created_at": identity.created_at,
            "setup_ssid": label.get("ssid") or identity_status().get("setup_ssid"),
            "label": label.get("label"),
            "qr": label.get("qr"),
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("Sentero Geräteidentität eingerichtet.")
            print()
            print(f"Seriennummer: {result['serial_number']}")
            print(f"Geräte-ID:    {result['device_id']}")
            print(f"Setup-WLAN:   {result['setup_ssid']}")
            if result.get("label"):
                print(f"QR-Aufkleber: {result['label']}")
        return 0

    if args.show or args.json:
        data = identity_status()
        print_status(data, as_json=args.json)
        return 1 if data.get("identity_error") else 0

    parser.error("--serial oder --show ist erforderlich.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
