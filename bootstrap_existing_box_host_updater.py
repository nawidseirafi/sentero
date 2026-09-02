#!/usr/bin/env python3
"""One-time bridge for boxes that still run the pre-v12 image-only updater.

Run this once after publishing a v12-format release and before installing that
release through the Sentero UI. It upgrades only the host updater itself; the
subsequent normal Sentero update then installs the complete host payload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BOX_DIR = Path('/opt/sentero/box')
ENV_FILE = BOX_DIR / '.env'


def read_env() -> dict[str, str]:
    out = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit('Bitte als root ausfuehren.')
    env = read_env()
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', default=env.get('SENTERO_UPDATE_CHANNEL', 'stable'))
    parser.add_argument('--manifest', default=env.get('SENTERO_UPDATE_MANIFEST_URL') or env.get('UPDATE_MANIFEST_URL') or '')
    args = parser.parse_args()
    manifest_url = args.manifest
    if not manifest_url:
        base = env.get('SENTERO_UPDATE_BASE_URL') or env.get('UPDATE_BASE_URL')
        if not base:
            raise SystemExit('Kein Update-Server in .env konfiguriert.')
        manifest_url = f"{base.rstrip('/')}/{args.channel}/latest.json"

    with urllib.request.urlopen(manifest_url, timeout=30) as r:
        manifest = json.loads(r.read().decode('utf-8'))
    latest = manifest.get('channels', {}).get(args.channel, manifest)
    appliance = latest.get('appliance') if isinstance(latest, dict) else None
    if not isinstance(appliance, dict) or not appliance.get('host_payload'):
        raise SystemExit('Das aktuelle Release enthaelt noch keine v12 Host-Schicht.')
    url = str(appliance.get('bundle_url') or '')
    expected = str(appliance.get('sha256') or '').lower()
    if not url or len(expected) != 64:
        raise SystemExit('Ungueltige Bundle-Metadaten.')

    with tempfile.TemporaryDirectory(prefix='sentero-updater-bootstrap-') as td:
        bundle = Path(td) / 'bundle.zip'
        with urllib.request.urlopen(url, timeout=60) as r, bundle.open('wb') as f:
            shutil.copyfileobj(r, f, 1024 * 1024)
        if sha256(bundle).lower() != expected:
            raise SystemExit('SHA-256-Pruefung fehlgeschlagen.')

        with zipfile.ZipFile(bundle) as z:
            required = {
                'host/sentero-updater/sentero_updater.py': BOX_DIR / 'sentero-updater/sentero_updater.py',
                'host/systemd/sentero-updater.service': BOX_DIR / 'systemd/sentero-updater.service',
            }
            for member, destination in required.items():
                if member not in z.namelist():
                    raise SystemExit(f'Fehlt im Bundle: {member}')
                destination.parent.mkdir(parents=True, exist_ok=True)
                backup = destination.with_suffix(destination.suffix + '.pre-v12')
                if destination.exists() and not backup.exists():
                    shutil.copy2(destination, backup)
                tmp = destination.with_name(destination.name + '.new')
                tmp.write_bytes(z.read(member))
                tmp.chmod(0o755 if destination.suffix == '.py' else 0o644)
                os.replace(tmp, destination)

    subprocess.run(['install', '-m', '0644', str(BOX_DIR / 'systemd/sentero-updater.service'), '/etc/systemd/system/sentero-updater.service'], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'restart', 'sentero-updater.service'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'sentero-updater.service'], check=True)
    print('Sentero Host-Updater v12 ist aktiv. Jetzt kann das normale UI-Update die komplette Host-Schicht installieren.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
