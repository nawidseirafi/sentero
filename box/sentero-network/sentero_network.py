#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import http.server
import io
import json
import os
import shutil
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
AP_IP = "192.168.50.1"
CAPTIVE_HTTP_PORT = 18080
CAPTIVE_NFT_TABLE = "sentero_captive"
CAPTIVE_DNS_DIR = Path("/etc/NetworkManager/dnsmasq-shared.d")
CAPTIVE_DNS_FILE = CAPTIVE_DNS_DIR / "90-sentero-captive.conf"
_CAPTIVE_SERVER: http.server.ThreadingHTTPServer | None = None
_CAPTIVE_THREAD: threading.Thread | None = None
_CAPTIVE_REDIRECT_DEVICE: str | None = None
_DIRECT_CAPTIVE_SERVER: http.server.ThreadingHTTPServer | None = None
_DIRECT_CAPTIVE_THREAD: threading.Thread | None = None
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
        "setup_portal_url": f"http://{AP_IP}:8080",
        "setup_wifi_qr_url": f"http://{AP_IP}/setup-wifi-qr.svg",
        "ip_address": ip,
        "ethernet_ip_address": ethernet_ip,
        "wifi_ip_address": wifi_ip,
        "ethernet_device": ethernet_device,
        "wifi_device": wifi_client_device or dev,
        "connectivity": con,
        "capabilities": {"ethernet": True, "wifi": bool(dev), "wifi_ap": bool(dev) and wifi_ap, "cellular": False},
    }



def _qr_matrix_v4_l(text: str) -> list[list[bool]]:
    """Create a standards-compliant QR Code (Version 4, ECC L, byte mode).

    Version 4-L carries up to 80 data codewords, which is far more than the
    deterministic Sentero setup-WiFi payload. Keeping the encoder fixed makes
    it small, dependency-free and available even when the box has no Internet.
    """
    version = 4
    size = 17 + version * 4
    data_codewords = 80
    ecc_codewords = 20

    raw = text.encode("utf-8")
    if len(raw) > 78:
        raise ValueError("QR payload too large")

    bits: list[int] = []
    def append_bits(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    append_bits(0b0100, 4)  # byte mode
    append_bits(len(raw), 8)
    for byte in raw:
        append_bits(byte, 8)
    capacity = data_codewords * 8
    for _ in range(min(4, capacity - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)
    data = [sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    while len(data) < data_codewords:
        data.append(pad[(len(data) - ((len(bits) + 7) // 8)) & 1])

    # Reed-Solomon over GF(256), primitive polynomial 0x11D.
    def gf_mul(x: int, y: int) -> int:
        z = 0
        for i in range(7, -1, -1):
            z = (z << 1) ^ ((z >> 7) * 0x11D)
            if (y >> i) & 1:
                z ^= x
        return z & 0xFF

    divisor = [0] * (ecc_codewords - 1) + [1]
    root = 1
    for i in range(ecc_codewords):
        for j in range(ecc_codewords):
            divisor[j] = gf_mul(divisor[j], root)
            if j + 1 < ecc_codewords:
                divisor[j] ^= divisor[j + 1]
        root = gf_mul(root, 0x02)
    rem = [0] * ecc_codewords
    for byte in data:
        factor = byte ^ rem.pop(0)
        rem.append(0)
        for i, coefficient in enumerate(divisor):
            rem[i] ^= gf_mul(coefficient, factor)
    codewords = data + rem
    data_bits = [(byte >> i) & 1 for byte in codewords for i in range(7, -1, -1)]

    modules = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def set_function(row: int, col: int, dark: bool) -> None:
        if 0 <= row < size and 0 <= col < size:
            modules[row][col] = dark
            function[row][col] = True

    def finder(center_row: int, center_col: int) -> None:
        for dr in range(-4, 5):
            for dc in range(-4, 5):
                row, col = center_row + dr, center_col + dc
                if 0 <= row < size and 0 <= col < size:
                    dist = max(abs(dr), abs(dc))
                    set_function(row, col, dist != 2 and dist != 4)

    finder(3, 3)
    finder(3, size - 4)
    finder(size - 4, 3)
    for i in range(8, size - 8):
        set_function(6, i, i % 2 == 0)
        set_function(i, 6, i % 2 == 0)

    # Version 4 has alignment centers [6, 26]; only (26,26) is not occupied.
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            set_function(26 + dr, 26 + dc, max(abs(dr), abs(dc)) != 1)

    def format_bits(mask: int) -> None:
        # Error correction level L is binary 01 in the QR format field.
        value = (0b01 << 3) | mask
        remv = value
        for _ in range(10):
            remv = (remv << 1) ^ ((remv >> 9) * 0x537)
        value = ((value << 10) | remv) ^ 0x5412
        bit = lambda i: ((value >> i) & 1) != 0
        for i in range(6):
            set_function(i, 8, bit(i))
        set_function(7, 8, bit(6))
        set_function(8, 8, bit(7))
        set_function(8, 7, bit(8))
        for i in range(9, 15):
            set_function(8, 14 - i, bit(i))
        for i in range(8):
            set_function(8, size - 1 - i, bit(i))
        for i in range(8, 15):
            set_function(size - 15 + i, 8, bit(i))
        set_function(size - 8, 8, True)

    format_bits(0)

    bit_index = 0
    upward = True
    col = size - 1
    while col >= 1:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for current_col in (col, col - 1):
                if function[row][current_col]:
                    continue
                dark = bit_index < len(data_bits) and data_bits[bit_index] == 1
                bit_index += 1
                # Mask pattern 0.
                if (row + current_col) % 2 == 0:
                    dark = not dark
                modules[row][current_col] = dark
        upward = not upward
        col -= 2
    return modules


def setup_wifi_qr_svg() -> bytes:
    payload = f"WIFI:T:nopass;S:{setup_ssid()};;"
    matrix = _qr_matrix_v4_l(payload)
    border = 4
    size = len(matrix) + border * 2
    path_parts: list[str] = []
    for row, line in enumerate(matrix):
        for col, dark in enumerate(line):
            if dark:
                path_parts.append(f"M{col + border},{row + border}h1v1h-1z")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        'shape-rendering="crispEdges" role="img" aria-label="Sentero Setup WLAN QR-Code">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<path d="{"".join(path_parts)}" fill="black"/></svg>'
    )
    return svg.encode("utf-8")


class _CaptiveHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SenteroCaptive/1.0"

    def do_HEAD(self) -> None:
        self._respond(send_body=False)

    def do_GET(self) -> None:
        self._respond(send_body=True)

    def _respond(self, send_body: bool) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/setup-wifi-qr.svg":
            body = setup_wifi_qr_svg()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        # Any ordinary HTTP request (including Apple/Android/Windows captive
        # probes) is deliberately redirected to the existing Sentero setup UI.
        location = f"http://{AP_IP}:8080/"
        body = b"Sentero Setup"
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _ensure_captive_http_server() -> bool:
    global _CAPTIVE_SERVER, _CAPTIVE_THREAD
    if _CAPTIVE_SERVER is not None:
        return True
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", CAPTIVE_HTTP_PORT), _CaptiveHandler)
        server.daemon_threads = True
    except OSError:
        return False
    _CAPTIVE_SERVER = server
    _CAPTIVE_THREAD = threading.Thread(target=server.serve_forever, name="sentero-captive-http", daemon=True)
    _CAPTIVE_THREAD.start()
    return True


def _ensure_direct_captive_server() -> bool:
    """Fallback for systems without nftables: listen directly on AP port 80."""
    global _DIRECT_CAPTIVE_SERVER, _DIRECT_CAPTIVE_THREAD
    if _DIRECT_CAPTIVE_SERVER is not None:
        return True
    try:
        server = http.server.ThreadingHTTPServer((AP_IP, 80), _CaptiveHandler)
        server.daemon_threads = True
    except OSError:
        return False
    _DIRECT_CAPTIVE_SERVER = server
    _DIRECT_CAPTIVE_THREAD = threading.Thread(target=server.serve_forever, name="sentero-captive-http80", daemon=True)
    _DIRECT_CAPTIVE_THREAD.start()
    return True


def _stop_direct_captive_server() -> None:
    global _DIRECT_CAPTIVE_SERVER, _DIRECT_CAPTIVE_THREAD
    server = _DIRECT_CAPTIVE_SERVER
    _DIRECT_CAPTIVE_SERVER = None
    _DIRECT_CAPTIVE_THREAD = None
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass


def _write_captive_dns_config() -> bool:
    """Make NetworkManager's shared dnsmasq resolve every name to the setup box."""
    try:
        CAPTIVE_DNS_DIR.mkdir(parents=True, exist_ok=True)
        desired = (
            "# Sentero captive portal; used only by NetworkManager shared connections.\n"
            f"address=/#/{AP_IP}\n"
            f"dhcp-option=option:dns-server,{AP_IP}\n"
        )
        if not CAPTIVE_DNS_FILE.exists() or CAPTIVE_DNS_FILE.read_text(encoding="utf-8") != desired:
            CAPTIVE_DNS_FILE.write_text(desired, encoding="utf-8")
        return True
    except OSError:
        return False


def _remove_captive_dns_config() -> None:
    try:
        CAPTIVE_DNS_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _disable_captive_redirect() -> None:
    global _CAPTIVE_REDIRECT_DEVICE
    if shutil.which("nft"):
        run(["nft", "delete", "table", "inet", CAPTIVE_NFT_TABLE], 5)
    _CAPTIVE_REDIRECT_DEVICE = None


def _enable_captive_redirect(device: str) -> bool:
    """Intercept HTTP only on the setup Wi-Fi and hand it to our redirector."""
    global _CAPTIVE_REDIRECT_DEVICE
    if not shutil.which("nft"):
        return False
    if _CAPTIVE_REDIRECT_DEVICE == device:
        return True
    _disable_captive_redirect()
    commands = [
        ["nft", "add", "table", "inet", CAPTIVE_NFT_TABLE],
        ["nft", "add", "chain", "inet", CAPTIVE_NFT_TABLE, "prerouting",
         "{", "type", "nat", "hook", "prerouting", "priority", "dstnat", ";", "policy", "accept", ";", "}"],
        ["nft", "add", "rule", "inet", CAPTIVE_NFT_TABLE, "prerouting",
         "iifname", device, "tcp", "dport", "80", "redirect", "to", f":{CAPTIVE_HTTP_PORT}"],
    ]
    ok = all(run(command, 5).returncode == 0 for command in commands)
    if ok:
        _CAPTIVE_REDIRECT_DEVICE = device
    return ok


def _sync_captive_portal() -> None:
    current = status()
    if current.get("setup_ap_active"):
        dev = str(current.get("wifi_device") or wifi_device() or "")
        _ensure_captive_http_server()
        if dev:
            if _enable_captive_redirect(dev):
                _stop_direct_captive_server()
            else:
                _ensure_direct_captive_server()
    else:
        _disable_captive_redirect()
        _stop_direct_captive_server()


def _captive_maintenance_loop() -> None:
    while True:
        try:
            _sync_captive_portal()
        except Exception:
            pass
        time.sleep(10)

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
    _disable_captive_redirect()
    _stop_direct_captive_server()
    _remove_captive_dns_config()
    result = run(["nmcli", "connection", "down", AP_CONNECTION], 15)
    return {"ok": result.returncode in {0, 10}, "active": False, "message": "Setup-WLAN beendet."}


def start_setup_ap() -> dict[str, Any]:
    dev = wifi_device()
    if not dev:
        return {"ok": False, "active": False, "message": "Kein WLAN-Adapter gefunden."}
    # The shared-mode dnsmasq reads this directory when the AP is brought up.
    # Wildcard DNS plus HTTP interception makes Apple/Android/Windows detect a
    # captive portal and open the setup page automatically.
    _write_captive_dns_config()
    _ensure_captive_http_server()
    _disable_captive_redirect()
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
    captive = False
    if result.returncode == 0:
        captive = _enable_captive_redirect(dev)
        if not captive:
            captive = _ensure_direct_captive_server()
    return {
        "ok": result.returncode == 0,
        "active": result.returncode == 0,
        "ssid": setup_ssid(),
        "local_ip_url": f"http://{AP_IP}:8080",
        "captive_portal": captive,
        "wifi_qr_url": f"http://{AP_IP}/setup-wifi-qr.svg",
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
    _ensure_captive_http_server()
    threading.Thread(target=_captive_maintenance_loop, name="sentero-captive-maintenance", daemon=True).start()
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
