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

device_global_ipv4() {
  local dev="$1" ip
  # Kernel address state is authoritative. NetworkManager may call a perfectly
  # usable installer-managed Ethernet link "connected (externally)".
  ip="$(ip -o -4 addr show dev "$dev" scope global 2>/dev/null | awk '$3 == "inet" {split($4,a,"/"); if (a[1] !~ /^169\.254\./) {print a[1]; exit}}')"
  if [ -n "$ip" ]; then
    printf '%s\n' "$ip"
    return 0
  fi
  ip="$(nmcli -g IP4.ADDRESS device show "$dev" 2>/dev/null | head -n1 | cut -d/ -f1 || true)"
  [ -n "$ip" ] && [[ "$ip" != 169.254.* ]] && printf '%s\n' "$ip"
}


start_setup_ap_via_agent() {
  "$PYTHON_BIN" - <<'PYCODE'
import json
import os
import socket

path = os.getenv("SENTERO_NETWORK_SOCKET", "/run/sentero-network/network.sock")
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(35)
    client.connect(path)
    client.sendall(b'{"action":"start_setup_ap"}\n')
    raw = b""
    while b"\n" not in raw:
        chunk = client.recv(65536)
        if not chunk:
            break
        raw += chunk
response = json.loads(raw.split(b"\n", 1)[0] or b"{}")
if not response.get("ok"):
    raise SystemExit(str(response.get("message") or "Setup-WLAN konnte nicht gestartet werden."))
print(response.get("ssid") or "Sentero-Setup")
PYCODE
}

local_network_state() {
  local dev typ state conn ip
  while IFS=: read -r dev typ state conn; do
    [ -n "${dev:-}" ] || continue
    [ "$conn" != "sentero-setup-ap" ] || continue
    case "$typ" in
      ethernet|wifi)
        ip="$(device_global_ipv4 "$dev" || true)"
        if [ -n "$ip" ]; then
          printf '%s|%s|%s\n' "$typ" "$dev" "$ip"
          return 0
        fi
        ;;
    esac
  done < <(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status 2>/dev/null || true)
  return 1
}

# Give NetworkManager a short window to activate Ethernet DHCP or a previously
# saved Wi-Fi profile. Provisioning is needed only when neither local path gets
# a usable IPv4 address.
NETWORK_STATE=""
for _ in $(seq 1 20); do
  NETWORK_STATE="$(local_network_state || true)"
  [ -n "$NETWORK_STATE" ] && break
  sleep 1
done

if [ -n "$NETWORK_STATE" ]; then
  IFS='|' read -r NETWORK_TYPE NETWORK_DEVICE NETWORK_IP <<<"$NETWORK_STATE"
  # A stale setup AP must not remain visible when the customer is already
  # connected by LAN or a saved Wi-Fi profile.
  nmcli connection down sentero-setup-ap >/dev/null 2>&1 || true
  echo "Sentero: lokales Netzwerk aktiv (${NETWORK_TYPE}, ${NETWORK_IP}); Setup-WLAN bleibt aus."
  exec "$DOCKER_BIN" compose up -d
fi

echo "Sentero: weder LAN noch gespeichertes WLAN mit IPv4 verfügbar; starte Provisionierungsoberfläche."
"$DOCKER_BIN" compose up -d --no-deps sentero
if AP_SSID="$(start_setup_ap_via_agent 2>/dev/null)"; then
  echo "Sentero: Setup-WLAN aktiv (${AP_SSID})."
else
  echo "WARNUNG: Setup-WLAN konnte nicht automatisch gestartet werden." >&2
fi
