#!/usr/bin/env bash
set -euo pipefail
export PATH="${SENTERO_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
export LC_ALL=C
export LANG=C
BOX_DIR="${SENTERO_BOX_DIR:-/opt/sentero/box}"
DOCKER_BIN="${SENTERO_DOCKER_BIN:-/usr/bin/docker}"
PYTHON_BIN="${SENTERO_PYTHON_BIN:-/usr/bin/python3}"
export SENTERO_NETWORK_SOCKET="${SENTERO_NETWORK_SOCKET:-/run/sentero-network/network.sock}"
cd "$BOX_DIR"

# Start the local surface before handing stack promotion to the network agent.
# One owner coordinates NetworkManager, onboarding and automatic recovery.
"$DOCKER_BIN" compose up -d --no-deps sentero
"$PYTHON_BIN" - <<'PYCODE'
import json
import os
import socket
import time

path = os.environ["SENTERO_NETWORK_SOCKET"]
deadline = time.monotonic() + 20
while True:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(path)
            client.sendall(b'{"action":"ensure_network"}\n')
            raw = b""
            while b"\n" not in raw and len(raw) < 65536:
                chunk = client.recv(65536)
                if not chunk:
                    break
                raw += chunk
        if json.loads(raw.split(b"\n", 1)[0]).get("ok"):
            break
    except (OSError, ValueError):
        pass
    if time.monotonic() >= deadline:
        raise SystemExit("Sentero: Netzwerkdienst nicht bereit; kein Setup-AP erzwungen.")
    time.sleep(1)
print("Sentero: lokale Netzwerkpruefung und Recovery an Netzwerkdienst uebergeben.")
PYCODE
