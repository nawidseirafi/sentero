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
_WIFI_AP_CACHE: dict[str, Any] = {"device": None, "supported": False, "checked_at": 0.0}


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # nmcli state strings are parsed below. Force stable English output even on
    # German customer systems.
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, env=env)


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


def device_ipv4(device: str) -> str | None:
    # Prefer the kernel's actual address state. Debian/NetworkManager may report
    # installer- or systemd-networkd-managed Ethernet as "connected (externally)".
    # Such a link is fully usable and must count as local-network readiness even
    # when NetworkManager does not own the connection profile.
    result = run(["ip", "-o", "-4", "addr", "show", "dev", device, "scope", "global"], 5)
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        try:
            idx = parts.index("inet")
            value = parts[idx + 1]
        except (ValueError, IndexError):
            continue
        ip = value.split("/", 1)[0].strip()
        if ip and not ip.startswith("169.254."):
            return ip

    # Fallback for systems where `ip` output is unexpectedly unavailable.
    result = run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", device], 8)
    for value in (result.stdout or "").splitlines():
        value = value.strip()
        if value:
            ip = value.split("/", 1)[0]
            if ip and not ip.startswith("169.254."):
                return ip
    return None


def connectivity() -> str:
    # Do not force NetworkManager's active connectivity probe here. `check` can
    # block for many seconds when DNS/Internet is slow and this endpoint is read
    # during normal page navigation. The cached NM connectivity state is enough
    # for the informational Internet badge and returns immediately.
    result = run(["nmcli", "-t", "networking", "connectivity"], 3)
    return (result.stdout or "").strip().lower()


def wifi_ap_supported(device: str | None) -> bool:
    if not device:
        return False
    now = time.monotonic()
    if _WIFI_AP_CACHE["device"] == device and now - float(_WIFI_AP_CACHE["checked_at"]) < 300:
        return bool(_WIFI_AP_CACHE["supported"])
    iw = run(["iw", "list"], 5)
    supported = iw.returncode == 0 and "* AP" in iw.stdout
    _WIFI_AP_CACHE.update({"device": device, "supported": supported, "checked_at": now})
    return supported


def status() -> dict[str, Any]:
    rows = connection_rows()

    ethernet_ip: str | None = None
    wifi_ip: str | None = None
    ethernet_device: str | None = None
    wifi_client_device: str | None = None
    ap_active = False

    for dev, typ, state, conn in rows:
        # The setup AP is identified by its dedicated connection name. Do not
        # depend on the localized/extended NetworkManager state string here.
        if typ == "wifi" and conn == AP_CONNECTION:
            if device_ipv4(dev):
                ap_active = True
            continue

        # A real global IPv4 address is authoritative. NetworkManager can label
        # valid installer-managed Ethernet as "connected (externally)", which
        # previously caused LAN to be ignored because state != "connected".
        if typ == "ethernet" and ethernet_ip is None:
            ip = device_ipv4(dev)
            if ip:
                ethernet_ip = ip
                ethernet_device = dev
        elif typ == "wifi" and wifi_ip is None:
            ip = device_ipv4(dev)
            if ip:
                wifi_ip = ip
                wifi_client_device = dev

    # Ethernet is deliberately preferred whenever it has a usable local IPv4
    # address. Wi-Fi may stay configured as a fallback, but onboarding is not
    # needed while LAN is usable.
    if ethernet_ip:
        active = "ethernet"
        ip = ethernet_ip
    elif wifi_ip:
        active = "wifi"
        ip = wifi_ip
    else:
        active = "none"
        ip = None

    con = connectivity()
    internet = con == "full"
    # Reuse the already-read device rows instead of spawning another nmcli
    # process for every status request. AP capability is effectively static and
    # therefore cached for five minutes.
    dev = next((row[0] for row in rows if row[1] == "wifi"), None)
    wifi_ap = wifi_ap_supported(dev)

    return {
        "ok": True,
        "active_connection": active,
        # A local IPv4 address is the readiness criterion. Internet is a
        # separate informational state and must never trigger onboarding.
        "network_ready": ip is not None,
        "internet_reachable": internet,
        "ethernet_active": ethernet_ip is not None,
        "wifi_active": wifi_ip is not None,
        "setup_ap_active": ap_active,
        "setup_ap_ssid": setup_ssid(),
        "ip_address": ip,
        "ethernet_ip_address": ethernet_ip,
        "wifi_ip_address": wifi_ip,
        "ethernet_device": ethernet_device,
        "wifi_device": wifi_client_device or dev,
        "connectivity": con,
        "capabilities": {"ethernet": True, "wifi": bool(dev), "wifi_ap": bool(dev) and wifi_ap, "cellular": False},
    }


def scan_wifi() -> dict[str, Any]:
    dev = wifi_device()
    if not dev:
        return {"ok": False, "networks": [], "message": "Kein WLAN-Adapter gefunden."}
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

    # If LAN is already usable, Wi-Fi may still be configured from Settings,
    # but the box remains reachable over Ethernet while the Wi-Fi profile is
    # created. The setup AP itself is stopped because the single radio cannot
    # normally be AP and client at once.
    stop_setup_ap()
    args = ["nmcli", "device", "wifi", "connect", ssid, "ifname", dev]
    if password:
        args.extend(["password", password])
    result = run(args, 60)
    if result.returncode != 0:
        # Restore onboarding only when no other local path (especially LAN)
        # keeps the box reachable.
        current = status()
        if not current.get("network_ready"):
            start_setup_ap()
        return {"ok": False, "message": "WLAN konnte nicht verbunden werden. Bitte prüfen Sie das Passwort.", "active": False, "status": current}

    deadline = time.time() + 30
    current = status()
    while time.time() < deadline:
        if current.get("wifi_active") and current.get("wifi_ip_address"):
            break
        time.sleep(1)
        current = status()

    if not (current.get("wifi_active") and current.get("wifi_ip_address")):
        run(["nmcli", "connection", "down", ssid], 10)
        current = status()
        if not current.get("network_ready"):
            start_setup_ap()
            current = status()
        return {"ok": False, "message": "WLAN-Verbindung konnte nicht vollständig hergestellt werden.", "active": False, "status": current}

    threading.Thread(target=post_connect_stack, daemon=True).start()
    message = "WLAN ist verbunden." if current.get("internet_reachable") else "WLAN ist lokal verbunden. Internet ist derzeit noch nicht erreichbar."
    return {"ok": True, "message": message, "active": True, "status": current}



def _service_state(unit: str) -> str:
    result = run(["systemctl", "is-active", unit], 4)
    return (result.stdout or "").strip().lower() or "unknown"


def _container_state(name: str) -> dict[str, Any]:
    result = run([
        "/usr/bin/docker", "inspect", name,
        "--format", '{{json .State}}',
    ], 6)
    if result.returncode != 0 or not (result.stdout or "").strip():
        return {"present": False, "running": False, "health": None}
    try:
        state = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"present": True, "running": False, "health": None}
    health = None
    if isinstance(state.get("Health"), dict):
        health = str(state["Health"].get("Status") or "").strip().lower() or None
    return {
        "present": True,
        "running": bool(state.get("Running")),
        "status": str(state.get("Status") or "").strip().lower(),
        "health": health,
    }


def _env_value(name: str) -> str:
    env_file = BOX_DIR / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def system_status() -> dict[str, Any]:
    net = status()
    profiles = {item.strip() for item in _env_value("COMPOSE_PROFILES").split(",") if item.strip()}
    zigbee_enabled = "zigbee" in profiles
    zigbee_path = _env_value("ZIGBEE_ADAPTER_HOST")
    zigbee_adapter = bool(zigbee_path and Path(zigbee_path).exists())

    sentero = _container_state("sentero")
    mqtt = _container_state("sentero-mosquitto")
    zigbee = _container_state("sentero-zigbee2mqtt")
    ollama = _container_state("sentero-ollama")

    def item(key: str, label: str, state: str, detail: str = "") -> dict[str, Any]:
        return {"key": key, "label": label, "state": state, "detail": detail}

    sentero_ok = sentero.get("running") and sentero.get("health") in {None, "healthy"}
    mqtt_ok = bool(mqtt.get("running"))
    ollama_ok = bool(ollama.get("running"))
    updater_ok = _service_state("sentero-updater.service") == "active"
    network_agent_ok = _service_state("sentero-network.service") == "active"
    mdns_ok = _service_state("avahi-daemon.service") == "active"

    services = [
        item("sentero", "Sentero", "ok" if sentero_ok else "error", "Bereit" if sentero_ok else "Nicht erreichbar"),
        item("network", "Netzwerk", "ok" if net.get("network_ready") else "warning",
             (f"Ethernet · {net.get('ethernet_ip_address')}" if net.get("ethernet_active") else
              f"WLAN · {net.get('wifi_ip_address')}" if net.get("wifi_active") else
              "Keine lokale Verbindung")),
        item("mqtt", "Nachrichten", "ok" if mqtt_ok else "error", "Bereit" if mqtt_ok else "Nicht aktiv"),
    ]

    if zigbee_enabled:
        if not zigbee_adapter:
            services.append(item("zigbee", "Zigbee", "error", "Adapter nicht erkannt"))
        elif zigbee.get("running"):
            services.append(item("zigbee", "Zigbee", "ok", "Verbunden"))
        else:
            services.append(item("zigbee", "Zigbee", "error", "Dienst nicht aktiv"))
    else:
        services.append(item("zigbee", "Zigbee", "inactive", "Nicht eingerichtet"))

    services.extend([
        item("ollama", "Lokale KI", "ok" if ollama_ok else "warning", "Bereit" if ollama_ok else "Nicht aktiv"),
        item("updater", "Updates", "ok" if updater_ok else "warning", "Bereit" if updater_ok else "Wird geprüft"),
        item("mdns", "sentero.local", "ok" if mdns_ok else "warning", "Aktiv" if mdns_ok else "Nicht aktiv"),
    ])

    core_states = [row["state"] for row in services if row["key"] in {"sentero", "network", "mqtt", "zigbee", "updater"} and row["state"] != "inactive"]
    if "error" in core_states:
        overall = "error"
        summary = "Ein Bereich benötigt Aufmerksamkeit."
    elif "warning" in core_states or not network_agent_ok:
        overall = "warning"
        summary = "Sentero läuft mit einer Einschränkung."
    else:
        overall = "ok"
        summary = "Alles bereit."

    return {
        "ok": True,
        "overall": overall,
        "summary": summary,
        "checked_at": datetime_now(),
        "services": services,
        "network": {
            "active_connection": net.get("active_connection"),
            "ip_address": net.get("ip_address"),
            "internet_reachable": net.get("internet_reachable"),
        },
    }


def datetime_now() -> str:
    # ISO UTC without importing a large dependency; time is only informational.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def handle(request: dict[str, Any]) -> dict[str, Any]:
    action = str(request.get("action") or "")
    if action == "status":
        return status()
    if action == "system_status":
        return system_status()
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
