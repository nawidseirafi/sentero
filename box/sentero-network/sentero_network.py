#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

SOCKET_PATH = Path("/run/sentero-network/network.sock")
BOX_DIR = Path("/opt/sentero/box")
AP_CONNECTION = "sentero-setup-ap"
AP_ADDRESS = "192.168.50.1/24"


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def wifi_device() -> str | None:
    result = run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], 8)
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi":
            return parts[0]
    return None


def setup_ssid() -> str:
    try:
        ident = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    except OSError:
        ident = socket.gethostname()
    suffix = hashlib.sha256((ident or "sentero").encode()).hexdigest()[:4].upper()
    return f"Sentero-Setup-{suffix}"


def connection_rows() -> list[tuple[str, str, str, str]]:
    result = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], 8)
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 3)
        if len(parts) == 4:
            rows.append(tuple(parts))  # type: ignore[arg-type]
    return rows


def connectivity() -> str:
    result = run(["nmcli", "-t", "networking", "connectivity", "check"], 15)
    return (result.stdout or "").strip().lower()


def status() -> dict[str, Any]:
    rows = connection_rows()
    ethernet_active = any(t == "ethernet" and s in {"connected", "connecting"} for _, t, s, _ in rows)
    wifi_active = any(t == "wifi" and s == "connected" and c != AP_CONNECTION for _, t, s, c in rows)
    ap_active = any(t == "wifi" and s == "connected" and c == AP_CONNECTION for _, t, s, c in rows)
    con = connectivity()
    internet = con == "full"
    active = "ethernet" if ethernet_active else "wifi" if wifi_active else "none"
    ip = None
    for dev, typ, state, conn in rows:
        if state == "connected" and ((typ == "ethernet" and ethernet_active) or (typ == "wifi" and wifi_active)):
            r = run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", dev], 8)
            value = (r.stdout or "").strip().splitlines()
            if value:
                ip = value[0].split("/", 1)[0]
                break
    dev = wifi_device()
    wifi_ap = False
    if dev:
        iw = run(["iw", "list"], 8)
        wifi_ap = iw.returncode == 0 and "* AP" in iw.stdout
    return {
        "ok": True,
        "active_connection": active,
        # Local network readiness and Internet reachability are deliberately
        # separate. A valid DHCP/local-LAN connection must never be torn down
        # merely because NetworkManager's Internet probe is slow, blocked or
        # reports limited/portal/unknown.
        "network_ready": active != "none",
        "internet_reachable": internet,
        "ethernet_active": ethernet_active,
        "wifi_active": wifi_active,
        "setup_ap_active": ap_active,
        "setup_ap_ssid": setup_ssid(),
        "ip_address": ip,
        "connectivity": con,
        "capabilities": {"ethernet": True, "wifi": bool(dev), "wifi_ap": bool(dev) and wifi_ap, "cellular": False},
    }


def scan_wifi() -> dict[str, Any]:
    dev = wifi_device()
    if not dev:
        return {"ok": False, "networks": [], "message": "Kein WLAN-Adapter gefunden."}
    # Scanning can fail on adapters that cannot scan while operating as AP.
    # NetworkManager still often returns its cached list, which is sufficient
    # for onboarding and avoids dropping the setup AP.
    result = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", dev], 20)
    networks: dict[str, dict[str, Any]] = {}
    for row in csv.reader(io.StringIO(result.stdout), delimiter=":", escapechar="\\"):
        if not row:
            continue
        ssid = (row[0] if len(row) > 0 else "").strip()
        if not ssid or ssid == setup_ssid():
            continue
        try:
            signal = max(0, min(100, int(row[1] if len(row) > 1 else 0)))
        except ValueError:
            signal = 0
        secured = bool((row[2] if len(row) > 2 else "").strip())
        if ssid not in networks or signal > networks[ssid]["signal"]:
            networks[ssid] = {"ssid": ssid, "signal": signal, "secured": secured}
    return {"ok": result.returncode == 0, "networks": sorted(networks.values(), key=lambda x: x["signal"], reverse=True)}


def stop_setup_ap() -> dict[str, Any]:
    result = run(["nmcli", "connection", "down", AP_CONNECTION], 15)
    # code 10 / unknown connection is harmless: AP is already down.
    return {"ok": result.returncode in {0, 10}, "active": False, "message": "Setup-WLAN beendet."}


def start_setup_ap() -> dict[str, Any]:
    dev = wifi_device()
    if not dev:
        return {"ok": False, "active": False, "message": "Kein WLAN-Adapter gefunden."}
    run(["nmcli", "connection", "delete", AP_CONNECTION], 10)
    result = run([
        "nmcli", "connection", "add", "type", "wifi", "ifname", dev,
        "con-name", AP_CONNECTION, "autoconnect", "no", "ssid", setup_ssid(),
        "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
        "ipv4.method", "shared", "ipv4.addresses", AP_ADDRESS,
        "ipv6.method", "disabled",
    ], 20)
    if result.returncode != 0:
        return {"ok": False, "active": False, "message": "Setup-WLAN konnte nicht konfiguriert werden."}
    # Temporary open AP: no label/password is required for first onboarding.
    run(["nmcli", "connection", "modify", AP_CONNECTION, "802-11-wireless-security.key-mgmt", ""], 10)
    result = run(["nmcli", "connection", "up", AP_CONNECTION], 30)
    return {
        "ok": result.returncode == 0,
        "active": result.returncode == 0,
        "ssid": setup_ssid(),
        "local_ip_url": "http://192.168.50.1:8080",
        "message": "Setup-WLAN gestartet." if result.returncode == 0 else "Setup-WLAN konnte nicht gestartet werden.",
    }


def post_connect_stack() -> None:
    # After onboarding the box has Internet. Pull/start the remaining appliance
    # containers without making the browser request wait for large downloads.
    try:
        subprocess.Popen(
            ["/usr/bin/docker", "compose", "up", "-d"],
            cwd=str(BOX_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def connect_wifi(ssid: str, password: str) -> dict[str, Any]:
    ssid = ssid.strip()
    if not ssid:
        return {"ok": False, "message": "Bitte wählen Sie ein WLAN aus."}
    dev = wifi_device()
    if not dev:
        return {"ok": False, "message": "Kein WLAN-Adapter gefunden."}

    # A single-radio box usually cannot keep AP and client mode active at the
    # same time. Stop AP only for the connection attempt and restore it when
    # authentication fails, so the customer is never locked out.
    stop_setup_ap()
    args = ["nmcli", "device", "wifi", "connect", ssid, "ifname", dev]
    if password:
        args.extend(["password", password])
    result = run(args, 60)
    if result.returncode != 0:
        start_setup_ap()
        return {"ok": False, "message": "WLAN konnte nicht verbunden werden. Bitte prüfen Sie das Passwort.", "active": False}

    # Wait only for a real local Wi-Fi connection + DHCP address. Internet
    # reachability is informational and may legitimately be limited/unknown.
    deadline = time.time() + 30
    current = status()
    while time.time() < deadline:
        if current.get("wifi_active") and current.get("ip_address"):
            break
        time.sleep(1)
        current = status()

    if not (current.get("wifi_active") and current.get("ip_address")):
        # Authentication/DHCP really failed. Restore the setup AP so the user
        # can correct SSID/password instead of being locked out.
        run(["nmcli", "connection", "down", ssid], 10)
        start_setup_ap()
        return {"ok": False, "message": "WLAN-Verbindung konnte nicht vollständig hergestellt werden. Das Setup-WLAN wurde wieder gestartet.", "active": False}

    threading.Thread(target=post_connect_stack, daemon=True).start()
    message = "WLAN ist verbunden." if current.get("internet_reachable") else "WLAN ist verbunden. Internet ist derzeit noch nicht erreichbar."
    return {"ok": True, "message": message, "active": True, "status": current}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    action = str(request.get("action") or "")
    if action == "status":
        return status()
    if action == "scan_wifi":
        return scan_wifi()
    if action == "start_setup_ap":
        return start_setup_ap()
    if action == "stop_setup_ap":
        return stop_setup_ap()
    if action == "connect_wifi":
        return connect_wifi(str(request.get("ssid") or ""), str(request.get("password") or ""))
    return {"ok": False, "message": f"Unbekannte Aktion: {action}"}


def serve() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o666)
        server.listen(16)
        while True:
            conn, _ = server.accept()
            with conn:
                try:
                    raw = b""
                    while b"\n" not in raw and len(raw) < 65536:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        raw += chunk
                    request = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
                    response = handle(request)
                except Exception as exc:
                    response = {"ok": False, "message": f"Netzwerkdienst-Fehler: {exc.__class__.__name__}"}
                try:
                    conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                except BrokenPipeError:
                    pass


if __name__ == "__main__":
    serve()
