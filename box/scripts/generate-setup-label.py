#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
from pathlib import Path

from sentero_device_identity import IdentityNotProvisioned, InvalidIdentityError, get_setup_ssid, load_device_identity

DEFAULT_OUTPUT_DIR = Path('/opt/sentero/box/generated')


def wifi_payload(ssid: str) -> str:
    # Sentero's onboarding AP is deliberately open. Escape characters that are
    # significant in the de-facto Wi-Fi QR format, even though the generated
    # Sentero SSID currently contains none of them.
    escaped = ''.join('\\' + ch if ch in r'\\;,:"' else ch for ch in ssid)
    return f'WIFI:T:nopass;S:{escaped};;'


def qr_matrix_v4_l(text: str) -> list[list[bool]]:
    """Create a dependency-free QR Code (Version 4, ECC L, byte mode)."""
    version = 4
    size = 17 + version * 4
    data_codewords = 80
    ecc_codewords = 20

    raw = text.encode('utf-8')
    if len(raw) > 78:
        raise ValueError('QR payload too large')

    bits: list[int] = []

    def append_bits(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    append_bits(0b0100, 4)
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
    original_len = len(data)
    while len(data) < data_codewords:
        data.append(pad[(len(data) - original_len) & 1])

    def gf_mul(x: int, y: int) -> int:
        z = 0
        for i in range(7, -1, -1):
            z = (z << 1) ^ ((z >> 7) * 0x11D)
            if (y >> i) & 1:
                z ^= x
        return z & 0xFF

    divisor = [0] * (ecc_codewords - 1) + [1]
    root = 1
    for _ in range(ecc_codewords):
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

    for dr in range(-2, 3):
        for dc in range(-2, 3):
            set_function(26 + dr, 26 + dc, max(abs(dr), abs(dc)) != 1)

    def format_bits(mask: int) -> None:
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
                if (row + current_col) % 2 == 0:
                    dark = not dark
                modules[row][current_col] = dark
        upward = not upward
        col -= 2
    return modules


def qr_path(matrix: list[list[bool]], x: float, y: float, module: float, border: int = 4) -> str:
    parts: list[str] = []
    for row, line in enumerate(matrix):
        for col, dark in enumerate(line):
            if dark:
                px = x + (col + border) * module
                py = y + (row + border) * module
                parts.append(f'M{px:.3f},{py:.3f}h{module:.3f}v{module:.3f}h-{module:.3f}z')
    return ''.join(parts)


def plain_qr_svg(ssid: str) -> str:
    matrix = qr_matrix_v4_l(wifi_payload(ssid))
    border = 4
    size = len(matrix) + border * 2
    path = qr_path(matrix, 0, 0, 1, border)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        'shape-rendering="crispEdges" role="img" aria-label="Sentero Setup WLAN QR-Code">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<path d="{path}" fill="black"/></svg>\n'
    )


def provisioned_serial_number() -> str | None:
    try:
        return load_device_identity().serial_number
    except IdentityNotProvisioned:
        return None
    except InvalidIdentityError as exc:
        raise RuntimeError(f'Geräteidentität beschädigt: {exc}') from exc


def label_svg(ssid: str, serial_number: str | None) -> str:
    """50 x 30 mm print label, vector-only and label-printer friendly."""
    matrix = qr_matrix_v4_l(wifi_payload(ssid))
    # The QR occupies 26 mm square including the 4-module quiet zone.
    qr_x, qr_y, qr_mm = 2.0, 2.0, 26.0
    total_modules = len(matrix) + 8
    module = qr_mm / total_modules
    path = qr_path(matrix, qr_x, qr_y, module, 4)
    safe_ssid = html.escape(ssid)
    safe_serial = html.escape(serial_number or '')
    serial_line = (
        f'  <text x="30" y="12" font-family="Arial, Helvetica, sans-serif" font-size="2.55" font-weight="700" fill="#111">{safe_serial}</text>\n'
        if serial_number else
        '  <text x="30" y="12" font-family="Arial, Helvetica, sans-serif" font-size="2.1" font-weight="600" fill="#555">Nicht provisioniert</text>\n'
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="30mm" viewBox="0 0 50 30" role="img" aria-label="Sentero Setup Etikett">
  <rect width="50" height="30" rx="2" fill="white"/>
  <path d="{path}" fill="black" shape-rendering="crispEdges"/>
  <text x="30" y="7" font-family="Arial, Helvetica, sans-serif" font-size="3.2" font-weight="700" fill="#111">Sentero Setup</text>
{serial_line.rstrip()}
  <text x="30" y="16.2" font-family="Arial, Helvetica, sans-serif" font-size="2.05" font-weight="600" fill="#111">Scannen zum Einrichten</text>
  <text x="30" y="20" font-family="Arial, Helvetica, sans-serif" font-size="1.75" fill="#444">WLAN wird automatisch verbunden.</text>
  <text x="30" y="25.2" font-family="Arial, Helvetica, sans-serif" font-size="1.7" font-weight="700" fill="#111">{safe_ssid}</text>
  <text x="30" y="28" font-family="Arial, Helvetica, sans-serif" font-size="1.45" fill="#666">setup.sentero.local</text>
</svg>\n'''


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate the per-box Sentero Setup Wi-Fi sticker.')
    parser.add_argument('--ssid', help='Override the deterministic Sentero setup SSID.')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    serial_number = provisioned_serial_number()
    ssid = (args.ssid or get_setup_ssid()).strip()
    if not ssid:
        parser.error('SSID must not be empty')

    args.output_dir.mkdir(parents=True, exist_ok=True)
    qr_file = args.output_dir / 'sentero-setup-wifi-qr.svg'
    label_file = args.output_dir / 'sentero-setup-label.svg'
    info_file = args.output_dir / 'sentero-setup-label.txt'

    qr_file.write_text(plain_qr_svg(ssid), encoding='utf-8')
    label_file.write_text(label_svg(ssid, serial_number), encoding='utf-8')
    info_file.write_text(
        f'Sentero Setup WLAN\nSeriennummer: {serial_number or "Nicht provisioniert"}\nSSID: {ssid}\nQR payload: {wifi_payload(ssid)}\nLabel: {label_file}\n',
        encoding='utf-8',
    )
    qr_file.chmod(0o644)
    label_file.chmod(0o644)
    info_file.chmod(0o644)

    print(f'SSID={ssid}')
    print(f'QR={qr_file}')
    print(f'LABEL={label_file}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
