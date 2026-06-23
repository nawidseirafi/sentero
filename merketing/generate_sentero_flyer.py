from __future__ import annotations

from pathlib import Path
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent
OUT_PNG = ROOT / "sentero_flyer_a5_preview.png"
OUT_PDF = ROOT / "sentero_flyer_a5.pdf"
CONTACT_URL = "https://www.sentero.de"

W, H = 1748, 2480  # DIN A5 at 300 dpi
SAGE = (107, 191, 135)
INK = (21, 32, 25)
MUTED = (86, 101, 92)
PAPER = (250, 252, 248)
SOFT = (240, 247, 241)


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    candidates = {
        "regular": [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ],
        "bold": [
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ],
        "black": [
            "/System/Library/Fonts/Supplemental/Arial Black.ttf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
        ],
    }
    for path in candidates.get(weight, candidates["regular"]):
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default(size=size)


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


def cover_image(path: Path, size: tuple[int, int], focus: tuple[float, float]) -> Image.Image:
    img = Image.open(path).convert("RGB")
    src_w, src_h = img.size
    dst_w, dst_h = size
    scale = max(dst_w / src_w, dst_h / src_h)
    new_w = math.ceil(src_w * scale)
    new_h = math.ceil(src_h * scale)
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    fx, fy = focus
    left = int((new_w - dst_w) * fx)
    top = int((new_h - dst_h) * fy)
    return img.crop((left, top, left + dst_w, top + dst_h))


def paste_round(base: Image.Image, img: Image.Image, box: tuple[int, int], radius: int) -> None:
    shadow = Image.new("RGBA", (img.width + 80, img.height + 80), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((40, 40, 40 + img.width, 40 + img.height), radius=radius, fill=(37, 58, 45, 48))
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    base.alpha_composite(shadow, (box[0] - 40, box[1] - 24))
    mask = rounded_mask(img.size, radius)
    base.paste(img.convert("RGBA"), box, mask)


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=fnt)
    return bbox[2] - bbox[0]

def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if text_width(draw, candidate, fnt) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def draw_wrapped(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt, fill, width: int, leading: int) -> int:
    x, y = xy
    for line in wrap(draw, text, fnt, width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += leading
    return y


def draw_check(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int) -> None:
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=SAGE)
    draw.line((x - 9, y, x - 2, y + 8, x + 12, y - 11), fill=(255, 255, 255), width=5, joint="curve")


def gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


GF_EXP, GF_LOG = gf_tables()


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coeff in enumerate(poly):
            nxt[j] ^= coeff
            nxt[j + 1] ^= gf_mul(coeff, GF_EXP[i])
        poly = nxt
    return poly


def rs_remainder(data: list[int], degree: int) -> list[int]:
    gen = rs_generator(degree)
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(degree):
            rem[i] ^= gf_mul(gen[i + 1], factor)
    return rem


def bits_from_int(value: int, length: int) -> list[int]:
    return [(value >> i) & 1 for i in range(length - 1, -1, -1)]


def make_qr_payload(text: str) -> list[int]:
    raw = text.encode("iso-8859-1")
    bits = [0, 1, 0, 0] + bits_from_int(len(raw), 8)
    for byte in raw:
        bits.extend(bits_from_int(byte, 8))
    data_capacity_bits = 34 * 8
    bits.extend([0] * min(4, data_capacity_bits - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    pad = [0xEC, 0x11]
    data: list[int] = []
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i : i + 8]:
            byte = (byte << 1) | bit
        data.append(byte)
    idx = 0
    while len(data) < 34:
        data.append(pad[idx % 2])
        idx += 1
    return data + rs_remainder(data, 10)


def qr_format_bits(mask: int) -> int:
    data = (1 << 3) | mask  # error correction L
    value = data << 10
    generator = 0x537
    for i in range(14, 9, -1):
        if value & (1 << i):
            value ^= generator << (i - 10)
    return ((data << 10) | value) ^ 0x5412


def mask_bit(mask: int, row: int, col: int) -> bool:
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return (row // 2 + col // 3) % 2 == 0
    if mask == 5:
        return ((row * col) % 2 + (row * col) % 3) == 0
    if mask == 6:
        return (((row * col) % 2 + (row * col) % 3) % 2) == 0
    return (((row + col) % 2 + (row * col) % 3) % 2) == 0


def add_finder(matrix: list[list[int | None]], reserved: list[list[bool]], row: int, col: int) -> None:
    for r in range(-1, 8):
        for c in range(-1, 8):
            rr, cc = row + r, col + c
            if 0 <= rr < 25 and 0 <= cc < 25:
                reserved[rr][cc] = True
                matrix[rr][cc] = 0
    for r in range(7):
        for c in range(7):
            rr, cc = row + r, col + c
            dark = r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
            matrix[rr][cc] = 1 if dark else 0


def add_format(matrix: list[list[int | None]], mask: int) -> None:
    bits = bits_from_int(qr_format_bits(mask), 15)
    first = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    second = [(24, 8), (23, 8), (22, 8), (21, 8), (20, 8), (19, 8), (18, 8), (8, 24), (8, 23), (8, 22), (8, 21), (8, 20), (8, 19), (8, 18), (8, 17)]
    for bit, (r, c) in zip(bits, first):
        matrix[r][c] = bit
    for bit, (r, c) in zip(bits, second):
        matrix[r][c] = bit


def make_qr(text: str, scale: int = 10, border: int = 4) -> Image.Image:
    size = 25
    matrix: list[list[int | None]] = [[None for _ in range(size)] for _ in range(size)]
    reserved = [[False for _ in range(size)] for _ in range(size)]
    add_finder(matrix, reserved, 0, 0)
    add_finder(matrix, reserved, 0, 18)
    add_finder(matrix, reserved, 18, 0)
    for i in range(size):
        if not reserved[6][i]:
            matrix[6][i] = 1 if i % 2 == 0 else 0
            reserved[6][i] = True
        if not reserved[i][6]:
            matrix[i][6] = 1 if i % 2 == 0 else 0
            reserved[i][6] = True
    for r in range(16, 21):
        for c in range(16, 21):
            reserved[r][c] = True
            matrix[r][c] = 1 if r in (16, 20) or c in (16, 20) or (r == 18 and c == 18) else 0
    matrix[17][8] = 1
    reserved[17][8] = True
    for i in range(9):
        reserved[8][i] = True
        reserved[i][8] = True
    for i in range(8):
        reserved[8][24 - i] = True
        reserved[24 - i][8] = True

    data_bits: list[int] = []
    for byte in make_qr_payload(text):
        data_bits.extend(bits_from_int(byte, 8))
    bit_idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not reserved[row][c]:
                    matrix[row][c] = data_bits[bit_idx] if bit_idx < len(data_bits) else 0
                    bit_idx += 1
        upward = not upward
        col -= 2

    best_matrix = None
    best_score = 10**9
    for mask in range(8):
        candidate = [[0 if cell is None else cell for cell in row] for row in matrix]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and mask_bit(mask, r, c):
                    candidate[r][c] ^= 1
        add_format(candidate, mask)
        score = 0
        for r in range(size):
            run_color = candidate[r][0]
            run = 1
            for c in range(1, size):
                if candidate[r][c] == run_color:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run_color = candidate[r][c]
                    run = 1
            if run >= 5:
                score += 3 + run - 5
        for c in range(size):
            run_color = candidate[0][c]
            run = 1
            for r in range(1, size):
                if candidate[r][c] == run_color:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run_color = candidate[r][c]
                    run = 1
            if run >= 5:
                score += 3 + run - 5
        if score < best_score:
            best_score = score
            best_matrix = candidate

    qr_size = (size + border * 2) * scale
    img = Image.new("RGB", (qr_size, qr_size), "white")
    qd = ImageDraw.Draw(img)
    assert best_matrix is not None
    for r in range(size):
        for c in range(size):
            if best_matrix[r][c]:
                x = (c + border) * scale
                y = (r + border) * scale
                qd.rectangle((x, y, x + scale - 1, y + scale - 1), fill=(21, 32, 25))
    return img


def crop_visible(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    alpha_bbox = rgba.getbbox()
    if alpha_bbox:
        return rgba.crop(alpha_bbox)
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    diff = ImageChops.difference(rgba, bg)
    bbox = diff.getbbox()
    return rgba.crop(bbox) if bbox else rgba


def main() -> None:
    canvas = Image.new("RGBA", (W, H), PAPER + (255,))
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle((-160, -140, 520, 520), radius=280, fill=(232, 246, 236, 255))
    draw.rounded_rectangle((1260, 90, 1940, 770), radius=320, fill=(242, 247, 240, 255))

    margin = 118
    hero_y = 112
    hero_h = 820
    gap = 36
    mother_w = 900
    daughter_w = W - margin * 2 - mother_w - gap

    mother = cover_image(ROOT / "Margarete.png", (mother_w, hero_h), (0.48, 0.24))
    daughter = cover_image(ROOT / "Daniela.png", (daughter_w, hero_h), (0.50, 0.22))
    paste_round(canvas, mother, (margin, hero_y), 88)
    paste_round(canvas, daughter, (margin + mother_w + gap, hero_y), 88)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((margin, hero_y, margin + mother_w, hero_y + hero_h), radius=88, fill=(0, 0, 0, 0))
    canvas.alpha_composite(overlay)

    label_font = font(28, "bold")
    for x, y, label in [
        (margin + 42, hero_y + hero_h - 92, "Margarete, 78"),
        (margin + mother_w + gap + 42, hero_y + hero_h - 92, "Daniela, 48"),
    ]:
        tw = text_width(draw, label, label_font)
        draw.rounded_rectangle((x, y, x + tw + 78, y + 50), radius=25, fill=(255, 255, 255, 226))
        draw.ellipse((x + 20, y + 18, x + 34, y + 32), fill=SAGE)
        draw.text((x + 48, y + 10), label, font=label_font, fill=(36, 49, 41))

    status_w, status_h = 540, 126
    status_x = (W - status_w) // 2
    status_y = hero_y + hero_h - 42
    draw.rounded_rectangle((status_x, status_y, status_x + status_w, status_y + status_h), radius=42, fill=(255, 255, 255, 242), outline=(203, 233, 213), width=2)
    draw.ellipse((status_x + 42, status_y + 46, status_x + 68, status_y + 72), fill=SAGE)
    draw.text((status_x + 94, status_y + 28), "Alles in Ordnung", font=font(34, "bold"), fill=INK)
    draw.text((status_x + 94, status_y + 72), "Ein gutes Gefühl im Alltag", font=font(24), fill=MUTED)

    y = 1014
    headline = font(128, "black")
    draw.text((margin, y), "MEHR RUHE.", font=headline, fill=INK)
    y += 116
    draw.text((margin, y), "WENIGER SORGEN.", font=headline, fill=SAGE)

    y += 166
    draw_wrapped(
        draw,
        (margin, y),
        "Ein beruhigendes Fenster in den Alltag Ihrer Liebsten.",
        font(52, "bold"),
        (53, 66, 56),
        W - margin * 2,
        62,
    )

    y += 125
    draw_wrapped(
        draw,
        (margin, y),
        "Sentero erkennt diskret, ob der Alltag wie gewohnt verläuft.",
        font(38),
        MUTED,
        1500,
        52,
    )

    promise_y = y + 100
    draw.rounded_rectangle((margin, promise_y, W - margin, promise_y + 170), radius=44, fill=(240, 248, 242), outline=(207, 233, 214), width=2)
    draw.text((margin + 200, promise_y + 60), "Ohne Kamera, ohne Cloud-Zwang, ohne komplizierte Technik.", font=font(38, "bold"), fill=INK)

    benefits_y = promise_y + 220
    benefits = [
        "Keine Kamera",
        "Lokal verarbeitet",
        "Datenschutzfreundlich",
        "Benachrichtigungen für Angehörige",
        "Einfache Einrichtung",
    ]
    col_w = (W - margin * 2 - 64) // 2
    for idx, item in enumerate(benefits):
        col = idx % 2
        row = idx // 2
        x = margin + col * (col_w + 64)
        yy = benefits_y + row * 64
        draw_check(draw, (x + 22, yy + 22), 20)
        draw.text((x + 58, yy + 2), item, font=font(31, "bold"), fill=(39, 52, 44))

    story_y = benefits_y + 210
    story = (
        "Viele ältere Menschen möchten selbstständig leben.\n\n"
        "Viele Angehörige möchten einfach wissen, dass alles in Ordnung ist.\n\n"
        "Sentero verbindet beides. Unauffällige Sensoren erkennen, ob der Alltag wie gewohnt verläuft "
        "und informieren Angehörige nur dann, wenn etwas ungewöhnlich erscheint."
    )
    draw_wrapped(draw, (margin, story_y), story, font(29), (67, 82, 72), W - margin * 2, 39)

    footer_y = H - 250
    draw.line((margin, footer_y, W - margin, footer_y), fill=(220, 230, 222), width=2)
    logo_y = footer_y + 32
    logo = crop_visible(Image.open(ROOT / "sentero_logo_transparent.png"))
    logo_w = 250
    logo_h = int(logo.height * (logo_w / logo.width))
    logo = logo.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
    canvas.alpha_composite(logo, (margin - 6, logo_y - 18))

    qr = make_qr(CONTACT_URL, scale=7, border=3).resize((150, 150), Image.Resampling.NEAREST)
    qr_x = W - margin - 150
    qr_y = footer_y + 32
    draw.rounded_rectangle((qr_x - 16, qr_y - 16, qr_x + 166, qr_y + 166), radius=26, fill=(255, 255, 255), outline=(217, 232, 221), width=2)
    canvas.paste(qr, (qr_x, qr_y))
    draw.text((qr_x - 304, qr_y + 20), "Mehr erfahren", font=font(30, "bold"), fill=INK)
    draw.text((qr_x - 304, qr_y + 62), "www.sentero.de", font=font(28, "bold"), fill=SAGE)
    draw_wrapped(
        draw,
        (qr_x - 304, qr_y + 102),
        "Scannen und unverbindlich informieren.",
        font(22),
        MUTED,
        260,
        30,
    )

    rgb = canvas.convert("RGB")
    rgb.save(OUT_PNG, quality=96, dpi=(300, 300))
    rgb.save(OUT_PDF, "PDF", resolution=300.0)
    print(OUT_PNG)
    print(OUT_PDF)


if __name__ == "__main__":
    main()
