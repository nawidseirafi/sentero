#!/usr/bin/env bash
set -euo pipefail

# Prepare the Debian host so NetworkManager owns the physical network
# interfaces used by Sentero. A mixed ifupdown/NetworkManager setup can start
# two DHCP clients on the same Ethernet link and give the box two LAN addresses.
# Safe defaults:
# - back up ifupdown configuration before changing anything
# - remove legacy physical-interface stanzas so each link has one DHCP owner
# - preserve loopback, aliases, bridges, VLANs and other non-physical stanzas
# - enforce ifupdown managed=true through a Sentero drop-in

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte als root ausfuehren." >&2
  exit 1
fi

BACKUP_ROOT="/var/backups/sentero-network"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"
NM_DROPIN="/etc/NetworkManager/conf.d/10-sentero-managed.conf"

mkdir -p "$BACKUP_DIR" /etc/NetworkManager/conf.d

if [ -f /etc/network/interfaces ]; then
  cp -a /etc/network/interfaces "$BACKUP_DIR/interfaces"
fi
if [ -d /etc/network/interfaces.d ]; then
  mkdir -p "$BACKUP_DIR/interfaces.d"
  cp -a /etc/network/interfaces.d/. "$BACKUP_DIR/interfaces.d/" 2>/dev/null || true
fi
if [ -f /etc/NetworkManager/NetworkManager.conf ]; then
  cp -a /etc/NetworkManager/NetworkManager.conf "$BACKUP_DIR/NetworkManager.conf"
fi
if [ -d /etc/NetworkManager/conf.d ]; then
  mkdir -p "$BACKUP_DIR/NetworkManager-conf.d"
  cp -a /etc/NetworkManager/conf.d/. "$BACKUP_DIR/NetworkManager-conf.d/" 2>/dev/null || true
fi

cat > "$NM_DROPIN" <<'EONM'
# Managed by Sentero. The appliance uses NetworkManager for Wi-Fi client mode
# and for the temporary Sentero-Setup-XXXX access point.
[ifupdown]
managed=true
EONM
chmod 0644 "$NM_DROPIN"

# Discover physical interfaces without depending on NetworkManager's current
# managed/unmanaged state.
mapfile -t ETHERNET_DEVS < <(
  for p in /sys/class/net/*; do
    [ -e "$p" ] || continue
    dev="$(basename "$p")"
    [ "$dev" != "lo" ] || continue
    [ ! -d "$p/wireless" ] || continue
    [ -e "$p/device" ] || continue
    if [[ "$dev" == docker* || "$dev" == br-* || "$dev" == veth* || "$dev" == virbr* || "$dev" == tun* || "$dev" == tap* || "$dev" == wg* || "$dev" == tailscale* || "$dev" == zt* || "$dev" == wl* || "$dev" == wlan* ]]; then
      continue
    fi
    printf '%s\n' "$dev"
  done | sort -u
)

mapfile -t WIFI_DEVS < <(
  for p in /sys/class/net/*/wireless; do
    [ -d "$p" ] || continue
    basename "$(dirname "$p")"
  done | sort -u
)

if [ "${#WIFI_DEVS[@]}" -eq 0 ] && command -v iw >/dev/null 2>&1; then
  mapfile -t WIFI_DEVS < <(iw dev 2>/dev/null | awk '$1=="Interface" {print $2}' | sort -u)
fi

if [ "${#ETHERNET_DEVS[@]}" -gt 0 ]; then
  echo "Ethernet-Interface(s): ${ETHERNET_DEVS[*]}"
fi

if [ "${#WIFI_DEVS[@]}" -eq 0 ]; then
  echo "WARNUNG: Kein WLAN-Interface gefunden. NetworkManager wurde trotzdem vorbereitet." >&2
else
  echo "WLAN-Interface(s): ${WIFI_DEVS[*]}"
fi

# Remove ifupdown ownership for physical appliance links. NetworkManager then
# becomes the only DHCP client for those devices.
NETWORK_LIST="$(IFS=,; echo "${ETHERNET_DEVS[*]:-},${WIFI_DEVS[*]:-}")"
export SENTERO_NETWORK_DEVICES="$NETWORK_LIST"
python3 - <<'PY'
from __future__ import annotations
import os
import re
from pathlib import Path

devices = {x for x in os.environ.get("SENTERO_NETWORK_DEVICES", "").split(",") if x}
if not devices:
    raise SystemExit(0)

paths = [Path("/etc/network/interfaces")]
idir = Path("/etc/network/interfaces.d")
if idir.is_dir():
    paths.extend(sorted(p for p in idir.iterdir() if p.is_file()))

iface_re = re.compile(r"^\s*iface\s+(\S+)\s+")
auto_re = re.compile(r"^(\s*)(auto|allow-hotplug)\s+(.+?)\s*$")

for path in paths:
    if not path.exists():
        continue
    original = path.read_text(encoding="utf-8", errors="replace").splitlines(True)
    out: list[str] = []
    i = 0
    changed = False
    while i < len(original):
        line = original[i]
        stripped = line.strip()
        m = iface_re.match(line)
        if m and m.group(1) in devices:
            changed = True
            i += 1
            # Continuation/options belong to this iface stanza until the next
            # top-level non-comment/non-blank directive.
            while i < len(original):
                nxt = original[i]
                if nxt.startswith((" ", "\t")) or not nxt.strip() or nxt.lstrip().startswith("#"):
                    i += 1
                    continue
                break
            continue
        m = auto_re.match(line)
        if m:
            names = m.group(3).split()
            kept = [n for n in names if n not in devices]
            if len(kept) != len(names):
                changed = True
                if kept:
                    out.append(f"{m.group(1)}{m.group(2)} {' '.join(kept)}\n")
                i += 1
                continue
        out.append(line)
        i += 1
    if changed:
        path.write_text("".join(out), encoding="utf-8")
        print(f"Bereinigt: {path}")
PY

systemctl enable NetworkManager.service >/dev/null 2>&1 || true
systemctl restart NetworkManager.service

# Force each physical device into managed mode as a final guard and verify it.
for dev in "${ETHERNET_DEVS[@]}" "${WIFI_DEVS[@]}"; do
  nmcli device set "$dev" managed yes >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    STATE="$(nmcli -g GENERAL.STATE device show "$dev" 2>/dev/null | head -n1 || true)"
    [ "$STATE" != "10 (unmanaged)" ] && [ "$STATE" != "10 (nicht verwaltet)" ] && break
    sleep 0.25
  done
  REASON="$(nmcli -g GENERAL.REASON device show "$dev" 2>/dev/null | head -n1 || true)"
  if nmcli -g GENERAL.STATE device show "$dev" 2>/dev/null | grep -Eq '^10([[:space:]]|$|[[:space:]]*\()'; then
    echo "FEHLER: $dev ist weiterhin 'unmanaged'. Grund: ${REASON:-unbekannt}" >&2
    echo "Backup: $BACKUP_DIR" >&2
    exit 1
  fi
  echo "$dev wird jetzt von NetworkManager verwaltet."
done

echo "NetworkManager vorbereitet. Backup: $BACKUP_DIR"
