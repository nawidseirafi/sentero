from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent
OUT_PNG = ROOT / "sentero_handbuch_endkunden_preview.png"
OUT_PDF = ROOT / "sentero_handbuch_endkunden.pdf"

W, H = 2480, 3508  # DIN A4 at 300 dpi
MARGIN_X = 190
MARGIN_TOP = 185
MARGIN_BOTTOM = 180

SAGE = (107, 191, 135)
SAGE_DARK = (48, 132, 79)
INK = (25, 34, 29)
MUTED = (85, 99, 91)
PAPER = (250, 252, 248)
SOFT = (239, 247, 241)
LINE = (213, 228, 218)
WARNING = (180, 112, 44)
CRITICAL = (169, 62, 56)


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    candidates = {
        "regular": [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ],
        "bold": [
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
        "black": [
            "/System/Library/Fonts/Supplemental/Arial Black.ttf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
    }
    for path in candidates.get(weight, candidates["regular"]):
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default(size=size)


def crop_visible(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    alpha_bbox = rgba.getbbox()
    if alpha_bbox:
        return rgba.crop(alpha_bbox)
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    diff = ImageChops.difference(rgba, bg)
    bbox = diff.getbbox()
    return rgba.crop(bbox) if bbox else rgba


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


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    width: int,
    leading: int,
) -> int:
    x, y = xy
    for line in wrap(draw, text, fnt, width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += leading
    return y


def draw_logo(canvas: Image.Image, xy: tuple[int, int], width: int) -> None:
    logo_path = ROOT / "sentero_logo_transparent.png"
    if not logo_path.exists():
        return
    logo = crop_visible(Image.open(logo_path))
    height = int(logo.height * (width / logo.width))
    logo = logo.resize((width, height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(logo, xy)


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


def paste_cover_photo(canvas: Image.Image, img: Image.Image, box: tuple[int, int], radius: int) -> None:
    x, y = box
    shadow = Image.new("RGBA", (img.width + 100, img.height + 100), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((50, 50, 50 + img.width, 50 + img.height), radius=radius, fill=(35, 56, 43, 42))
    shadow = shadow.filter(ImageFilter.GaussianBlur(34))
    canvas.alpha_composite(shadow, (x - 50, y - 30))

    photo = img.convert("RGBA")
    overlay = Image.new("RGBA", photo.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, int(photo.height * 0.64), photo.width, photo.height), fill=(18, 31, 24, 78))
    photo.alpha_composite(overlay)

    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo.width, photo.height), radius=radius, fill=255)
    canvas.paste(photo, (x, y), mask)


def new_page(title: str | None = None, number: int | None = None, show_logo: bool = True) -> Image.Image:
    page = Image.new("RGBA", (W, H), PAPER + (255,))
    draw = ImageDraw.Draw(page)
    draw.rounded_rectangle((-270, -250, 660, 660), radius=380, fill=(232, 246, 236, 255))
    draw.rounded_rectangle((1880, 90, 2730, 940), radius=410, fill=(242, 247, 240, 255))
    if show_logo:
        draw_logo(page, (W - MARGIN_X - 215, 78), 215)
    if title:
        draw.text((MARGIN_X, MARGIN_TOP), title, font=font(62, "black"), fill=INK)
        draw.line((MARGIN_X, MARGIN_TOP + 96, W - MARGIN_X, MARGIN_TOP + 96), fill=LINE, width=3)
    if number is not None:
        footer = f"Sentero Handbuch | {number}"
        draw.text((MARGIN_X, H - 104), footer, font=font(28), fill=(105, 117, 110))
        draw.line((MARGIN_X, H - 145, W - MARGIN_X, H - 145), fill=LINE, width=2)
    return page


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=SAGE, text_fill=(255, 255, 255)) -> int:
    x, y = xy
    fnt = font(31, "bold")
    width = text_width(draw, text, fnt) + 58
    draw.rounded_rectangle((x, y, x + width, y + 58), radius=29, fill=fill)
    draw.text((x + 29, y + 12), text, font=fnt, fill=text_fill)
    return width


def section(draw: ImageDraw.ImageDraw, x: int, y: int, title: str, body: str, width: int) -> int:
    draw.text((x, y), title, font=font(45, "bold"), fill=INK)
    y += 64
    return draw_wrapped(draw, (x, y), body, font(34), MUTED, width, 48) + 34


def bullet_list(draw: ImageDraw.ImageDraw, x: int, y: int, items: list[str], width: int) -> int:
    for item in items:
        draw.ellipse((x, y + 13, x + 20, y + 33), fill=SAGE)
        y = draw_wrapped(draw, (x + 46, y), item, font(33), INK, width - 46, 46)
        y += 18
    return y


@dataclass
class Step:
    title: str
    text: str


def step_card(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, number: int, step: Step) -> int:
    body_lines = wrap(draw, step.text, font(29), width - 168)
    height = max(170, 104 + len(body_lines) * 40)
    draw.rounded_rectangle((x, y, x + width, y + height), radius=26, fill=(255, 255, 255), outline=LINE, width=2)
    draw.ellipse((x + 36, y + 42, x + 104, y + 110), fill=SAGE)
    draw.text((x + 60, y + 55), str(number), font=font(32, "bold"), fill=(255, 255, 255))
    draw.text((x + 135, y + 38), step.title, font=font(35, "bold"), fill=INK)
    draw_wrapped(draw, (x + 135, y + 88), step.text, font(29), MUTED, width - 168, 40)
    return y + height + 28


def status_row(draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int], label: str, text: str, width: int) -> int:
    draw.rounded_rectangle((x, y, x + width, y + 124), radius=24, fill=(255, 255, 255), outline=LINE, width=2)
    draw.ellipse((x + 34, y + 42, x + 74, y + 82), fill=color)
    draw.text((x + 105, y + 28), label, font=font(34, "bold"), fill=INK)
    draw_wrapped(draw, (x + 105, y + 73), text, font(27), MUTED, width - 140, 36)
    return y + 148


def info_card(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    title: str,
    body: str,
    accent: tuple[int, int, int] = SAGE,
    fill: tuple[int, int, int] = (255, 255, 255),
) -> int:
    body_lines = wrap(draw, body, font(30), width - 86)
    height = max(190, 116 + len(body_lines) * 42)
    draw.rounded_rectangle((x, y, x + width, y + height), radius=28, fill=fill, outline=LINE, width=2)
    draw.rounded_rectangle((x, y, x + 13, y + height), radius=6, fill=accent)
    draw.text((x + 42, y + 34), title, font=font(37, "bold"), fill=INK)
    draw_wrapped(draw, (x + 42, y + 92), body, font(30), MUTED, width - 86, 42)
    return y + height + 30


def title_page() -> Image.Image:
    page = new_page(show_logo=False)
    draw = ImageDraw.Draw(page)
    draw.rounded_rectangle((1540, -170, 2680, 970), radius=520, fill=(236, 247, 239, 255))
    draw_logo(page, (MARGIN_X - 12, 154), 360)

    photo_x, photo_y, photo_w, photo_h = 1320, 690, 970, 1410
    photo_path = ROOT / "raum.png"
    if photo_path.exists():
        photo = cover_image(photo_path, (photo_w, photo_h), (0.63, 0.47))
        paste_cover_photo(page, photo, (photo_x, photo_y), 64)
        draw.text((photo_x + 70, photo_y + photo_h - 180), "Lokal. Diskret. Ohne Kamera.", font=font(48, "bold"), fill=(255, 255, 255))
        draw.line((photo_x + 72, photo_y + photo_h - 100, photo_x + 560, photo_y + photo_h - 100), fill=(131, 207, 158), width=7)

    pill(draw, (MARGIN_X, 620), "Endkunden-Handbuch")
    draw.text((MARGIN_X, 760), "Sentero", font=font(135, "black"), fill=INK)
    draw.text((MARGIN_X, 910), "einfach einrichten\nund sicher nutzen", font=font(76, "bold"), fill=SAGE_DARK)
    draw_wrapped(
        draw,
        (MARGIN_X, 1160),
        "Dieses Handbuch erklärt die Einrichtung und den täglichen Gebrauch der Sentero Box in einfachen Schritten.",
        font(42),
        MUTED,
        870,
        58,
    )

    draw.rounded_rectangle((MARGIN_X, 1545, MARGIN_X + 610, 1650), radius=52, fill=(255, 255, 255), outline=LINE, width=2)
    draw.ellipse((MARGIN_X + 40, 1582, MARGIN_X + 70, 1612), fill=SAGE)
    draw.text((MARGIN_X + 94, 1575), "Bereit für den Alltag", font=font(34, "bold"), fill=INK)

    y = 2270
    y = bullet_list(
        draw,
        MARGIN_X,
        y,
        [
            "Für Angehörige und betreute Personen verständlich geschrieben.",
            "Mit Checklisten für Netzwerk, Räume, Sensoren, Kontakte und Benachrichtigungen.",
            "Enthält Alltag, Datenschutz, Updates, Störungshilfe und Zurücksetzen.",
        ],
        1680,
    )
    draw.text((MARGIN_X, H - 300), "Stand: August 2026", font=font(31, "bold"), fill=MUTED)
    draw.text((MARGIN_X, H - 245), "www.sentero.de", font=font(34, "bold"), fill=SAGE_DARK)
    return page


def overview_page(number: int) -> Image.Image:
    page = new_page("1. Was Sentero macht", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    y = section(
        draw,
        MARGIN_X,
        y,
        "Kurz erklärt",
        "Sentero sammelt Signale von ausgewählten Sensoren, wertet den Alltag lokal aus und informiert vertraute Personen bei Auffälligkeiten oder technischen Problemen.",
        W - 2 * MARGIN_X,
    )
    y = section(
        draw,
        MARGIN_X,
        y,
        "Was Sentero bewusst nicht ist",
        "Sentero ist keine Kamera, keine Sprachüberwachung und kein Notrufersatz. Es ersetzt keine medizinische Betreuung und keine direkte Hilfe in akuten Situationen.",
        W - 2 * MARGIN_X,
    )
    y = section(
        draw,
        MARGIN_X,
        y,
        "Sicherheitsmeldungen",
        "Direkte Sicherheitsmeldungen, zum Beispiel ein von einem Rauchmelder gemeldeter Alarm, werden unabhängig von der Verhaltensanalyse behandelt. Sentero verarbeitet solche Meldungen, ersetzt aber keinen zertifizierten Rauchwarnmelder.",
        W - 2 * MARGIN_X,
    )
    y += 10
    draw.text((MARGIN_X, y), "Die wichtigsten Bereiche", font=font(45, "bold"), fill=INK)
    y += 76
    cards = [
        ("Dashboard", "Zeigt den Tagesstatus, Aufenthaltsort, letzte Bewegung und die Verhaltensanalyse."),
        ("Wizard", "Führt durch Profil, Räume, Sensoren, Kontakte und Benachrichtigungskanäle."),
        ("Einstellungen", "Verwaltet Netzwerk, Sensoren, Kontakte, Benachrichtigungen, Transparenz, Konto, System und Updates."),
        ("Aktivität", "Zeigt Sensorereignisse, Systemhinweise und Verlauf."),
    ]
    col_w = (W - 2 * MARGIN_X - 42) // 2
    for idx, (title, body) in enumerate(cards):
        x = MARGIN_X + (idx % 2) * (col_w + 42)
        yy = y + (idx // 2) * 330
        draw.rounded_rectangle((x, yy, x + col_w, yy + 265), radius=28, fill=(255, 255, 255), outline=LINE, width=2)
        draw.text((x + 42, yy + 38), title, font=font(39, "bold"), fill=INK)
        draw_wrapped(draw, (x + 42, yy + 98), body, font(30), MUTED, col_w - 84, 42)
    y += 715
    y = section(
        draw,
        MARGIN_X,
        y,
        "Lokal-first",
        "Die Auswertung läuft auf der Sentero Box im Zuhause. Bei fehlendem Internet arbeitet die lokale Sensorik weiter. Ausgehende Nachrichten werden, soweit möglich, später nachgesendet.",
        W - 2 * MARGIN_X,
    )
    return page


def setup_page(number: int) -> Image.Image:
    page = new_page("2. Erste Einrichtung", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    y = section(
        draw,
        MARGIN_X,
        y,
        "Vorbereitung",
        "Stellen Sie die Box an einem trockenen Ort mit Stromanschluss auf. Wenn möglich, verbinden Sie die Box per LAN-Kabel mit dem Router. LAN ist die einfachste und stabilste Variante.",
        W - 2 * MARGIN_X,
    )
    steps = [
        Step("Strom anschließen", "Netzteil einstecken und zwei bis drei Minuten warten, bis die Box gestartet ist."),
        Step("Mit Sentero verbinden", "Bei LAN öffnen Sie im Browser http://sentero.local:8080. Ohne LAN scannen Sie den QR-Aufkleber oder wählen das WLAN Sentero-Setup-XXXX."),
        Step("Heim-WLAN wählen", "Wenn die Setup-Seite erscheint, wählen Sie Ihr WLAN aus und geben das WLAN-Passwort ein. Bei LAN kann dieser Schritt entfallen."),
        Step("Konto anlegen", "Legen Sie das erste Benutzerkonto an. Diese Person verwaltet Sentero und darf später Systemaktionen ausführen."),
        Step("Wizard abschließen", "Tragen Sie Profil, Räume, Sensoren, vertraute Personen und Benachrichtigungen ein."),
    ]
    for idx, step in enumerate(steps, 1):
        y = step_card(draw, MARGIN_X, y, W - 2 * MARGIN_X, idx, step)
    y += 20
    draw.rounded_rectangle((MARGIN_X, y, W - MARGIN_X, y + 255), radius=30, fill=SOFT, outline=(197, 224, 205), width=2)
    draw.text((MARGIN_X + 42, y + 36), "Wenn die Verbindung abbricht", font=font(38, "bold"), fill=INK)
    draw_wrapped(
        draw,
        (MARGIN_X + 42, y + 96),
        "Beim Wechsel vom Setup-WLAN ins Heimnetz kann das Smartphone kurz die Verbindung verlieren. Warten Sie einen Moment und öffnen Sie anschließend http://sentero.local:8080. Wenn die WLAN-Verbindung nicht klappt, erscheint das Setup-WLAN wieder.",
        font(30),
        MUTED,
        W - 2 * MARGIN_X - 84,
        42,
    )
    return page


def wizard_page(number: int) -> Image.Image:
    page = new_page("3. Wizard: Profil, Räume und Sensoren", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    y = section(
        draw,
        MARGIN_X,
        y,
        "Profil",
        "Im Profil wird festgelegt, für wen Sentero den Alltag betrachtet. Der Name hilft in der Anzeige und in Nachrichten. Hinweise können besondere Gewohnheiten enthalten, zum Beispiel spätes Aufstehen am Wochenende.",
        W - 2 * MARGIN_X,
    )
    y = section(
        draw,
        MARGIN_X,
        y,
        "Räume",
        "Wählen Sie nur Räume aus, in denen Sensoren wirklich genutzt werden. Typische Räume sind Wohnzimmer, Küche, Bad, Schlafzimmer, Flur und Eingang.",
        W - 2 * MARGIN_X,
    )
    y += 20
    draw.text((MARGIN_X, y), "Sensoren einrichten", font=font(45, "bold"), fill=INK)
    y += 78
    items = [
        "Präsenzsensoren erkennen Anwesenheit oder Bewegung, ohne ein Bild aufzunehmen.",
        "Tür- und Fensterkontakte erkennen, ob ein Kontakt geöffnet oder geschlossen wurde.",
        "Rauchmelder melden Rauch und können bei einem Alarm eine unmittelbare Benachrichtigung auslösen.",
        "Stromsensoren können den aktuellen Verbrauch oder Zählerstand als Zusatzinformation anzeigen, sofern sie eingerichtet sind.",
        "Sensoren werden einem Raum zugeordnet. Dadurch kann Sentero verständliche Meldungen geben, zum Beispiel Küche oder Bad.",
        "Wenn ein Sensor nicht erreichbar ist, zeigt Sentero einen Prüfhinweis an.",
    ]
    y = bullet_list(draw, MARGIN_X, y, items, W - 2 * MARGIN_X)
    y += 20
    draw.rounded_rectangle((MARGIN_X, y, W - MARGIN_X, y + 395), radius=30, fill=(255, 255, 255), outline=LINE, width=2)
    draw.text((MARGIN_X + 42, y + 38), "Gute Platzierung", font=font(38, "bold"), fill=INK)
    placement = (
        "Sensoren sollten den normalen Alltag erkennen, aber nicht stören. Platzieren Sie sie stabil, nicht direkt hinter großen Metallflächen und nicht so, dass sie regelmäßig verdeckt werden. "
        "Ein Sensor pro wichtigem Bereich ist meist besser als viele Sensoren an zufälligen Stellen."
    )
    draw_wrapped(draw, (MARGIN_X + 42, y + 98), placement, font(31), MUTED, W - 2 * MARGIN_X - 84, 44)
    return page


def contacts_page(number: int) -> Image.Image:
    page = new_page("4. Kontakte und Benachrichtigungen", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    y = section(
        draw,
        MARGIN_X,
        y,
        "Vertraute Personen",
        "Vertraute Personen sind Angehörige oder betreuende Personen, die Hinweise erhalten und freigeschaltete Fragen stellen dürfen. Hinterlegen Sie mindestens eine erreichbare Kontaktmöglichkeit.",
        W - 2 * MARGIN_X,
    )
    y = section(
        draw,
        MARGIN_X,
        y,
        "E-Mail",
        "E-Mail ist Benachrichtigungs- und Anfragekanal. Für eine neue Anfrage muss der Betreff mit Sentero: beginnen. Die eigentliche Frage steht im Nachrichtentext.",
        W - 2 * MARGIN_X,
    )
    draw.rounded_rectangle((MARGIN_X, y, W - MARGIN_X, y + 245), radius=28, fill=(255, 255, 255), outline=LINE, width=2)
    draw.text((MARGIN_X + 42, y + 34), "Beispiel", font=font(36, "bold"), fill=INK)
    draw.text((MARGIN_X + 42, y + 92), "Betreff: Sentero: Frage zum Tagesablauf", font=font(31, "bold"), fill=SAGE_DARK)
    draw.text((MARGIN_X + 42, y + 145), "Nachricht: Ist heute alles in Ordnung?", font=font(31), fill=MUTED)
    y += 285
    y = section(
        draw,
        MARGIN_X,
        y,
        "Telegram",
        "Telegram kann zusätzlich als Benachrichtigungs- und Anfragekanal verbunden werden. Jede vertraute Person nutzt dafür ihren persönlichen Einladungslink oder QR-Code.",
        W - 2 * MARGIN_X,
    )
    draw.text((MARGIN_X, y), "Mögliche Fragen", font=font(45, "bold"), fill=INK)
    y += 76
    col_w = (W - 2 * MARGIN_X - 42) // 2
    questions = [
        "Ist alles in Ordnung?",
        "Wann wurde zuletzt Aktivität erkannt?",
        "Wo wurde zuletzt Aktivität erkannt?",
        "Gab es heute Auffälligkeiten?",
        "Wie ist die Temperatur in der Wohnung?",
        "Wie hoch ist der Stromverbrauch?",
        "Sind alle Türen und Fenster zu?",
        "Wie war die vergangene Nacht?",
    ]
    for idx, question in enumerate(questions):
        x = MARGIN_X + (idx % 2) * (col_w + 42)
        yy = y + (idx // 2) * 70
        draw.ellipse((x, yy + 13, x + 20, yy + 33), fill=SAGE)
        draw.text((x + 44, yy), question, font=font(29), fill=INK)
    y += 330
    draw.rounded_rectangle((MARGIN_X, y, W - MARGIN_X, y + 390), radius=30, fill=SOFT, outline=(197, 224, 205), width=2)
    draw.text((MARGIN_X + 42, y + 38), "Berechtigungen bewusst setzen", font=font(38, "bold"), fill=INK)
    draw_wrapped(
        draw,
        (MARGIN_X + 42, y + 98),
        "Geben Sie jeder vertrauten Person nur die Informationen frei, die sie wirklich benötigt. Antworten sind lesend: Sentero ändert darüber keine Einstellungen und löst keine Systemaktionen aus.",
        font(31),
        MUTED,
        W - 2 * MARGIN_X - 84,
        44,
    )
    return page


def alerts_page(number: int) -> Image.Image:
    page = new_page("5. Hinweise und Alarme", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    y = section(
        draw,
        MARGIN_X,
        y,
        "Drei Arten von Meldungen",
        "Sentero unterscheidet direkte Sicherheitsalarme, Auffälligkeiten im Tagesablauf und technische Hinweise. So bleibt klar, was dringend ist und was geprüft werden sollte.",
        W - 2 * MARGIN_X,
    )
    y = info_card(
        draw,
        MARGIN_X,
        y,
        W - 2 * MARGIN_X,
        "Sicherheitsalarm",
        "Beispiel: Rauch erkannt. Ein direkt erkanntes Sicherheitsereignis wird sofort an freigeschaltete Vertrauenspersonen gemeldet.",
        CRITICAL,
        (255, 248, 247),
    )
    y = info_card(
        draw,
        MARGIN_X,
        y,
        W - 2 * MARGIN_X,
        "Auffälligkeit",
        "Beispiel: ungewöhnlich lange keine Aktivität. Sentero erkennt eine Abweichung vom bekannten Tagesablauf. Das ist kein automatisch bestätigter Notfall.",
        WARNING,
        (255, 252, 246),
    )
    y = info_card(
        draw,
        MARGIN_X,
        y,
        W - 2 * MARGIN_X,
        "Technischer Hinweis",
        "Beispiel: Ein Sensor liefert keine aktuellen Daten oder eine Batterie ist schwach. Sentero weist darauf hin, dass ein Gerät geprüft werden sollte.",
        SAGE,
        (255, 255, 255),
    )
    y += 24
    draw.rounded_rectangle((MARGIN_X, y, W - MARGIN_X, y + 350), radius=30, fill=SOFT, outline=(197, 224, 205), width=2)
    draw.text((MARGIN_X + 42, y + 38), "Rauchalarm ist getrennt vom Alltag", font=font(38, "bold"), fill=INK)
    draw_wrapped(
        draw,
        (MARGIN_X + 42, y + 98),
        "Ein Rauchalarm wird nicht als normale Verhaltensauffälligkeit erklärt. Wenn ein eingerichteter Rauchmelder Rauch meldet, behandelt Sentero diese Meldung als Sicherheitsalarm. Sentero ersetzt keinen zertifizierten Rauchwarnmelder und keinen Notrufdienst.",
        font(31),
        MUTED,
        W - 2 * MARGIN_X - 84,
        44,
    )
    return page


def daily_use_page(number: int) -> Image.Image:
    page = new_page("6. Tägliche Nutzung", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    y = section(
        draw,
        MARGIN_X,
        y,
        "Dashboard lesen",
        "Das Dashboard zeigt auf einen Blick, ob der Alltag normal wirkt. Es zeigt Aufenthaltsort, letzte Bewegung, Aufstehzeitpunkt und Lernstand. Sicherheitsalarme werden davon getrennt behandelt.",
        W - 2 * MARGIN_X,
    )
    draw.text((MARGIN_X, y), "Statusfarben", font=font(45, "bold"), fill=INK)
    y += 82
    y = status_row(draw, MARGIN_X, y, SAGE, "Normal", "Der Tagesverlauf passt zu den bekannten Gewohnheiten.", W - 2 * MARGIN_X)
    y = status_row(draw, MARGIN_X, y, (207, 174, 70), "Leichte Abweichung", "Sentero erkennt eine kleinere Veränderung. Eine Prüfung ist meist nicht dringend.", W - 2 * MARGIN_X)
    y = status_row(draw, MARGIN_X, y, WARNING, "Auffällig", "Der Tagesablauf weicht deutlich vom bekannten Verhalten ab. Eine Nachfrage kann sinnvoll sein.", W - 2 * MARGIN_X)
    y = status_row(draw, MARGIN_X, y, CRITICAL, "Kritisch", "Sentero hat eine dringende Auffälligkeit oder einen wichtigen Alarm erkannt. Die Situation sollte zeitnah geprüft werden.", W - 2 * MARGIN_X)
    y += 20
    y = section(
        draw,
        MARGIN_X,
        y,
        "Lernphase",
        "Sentero lernt in den ersten Tagen typische Abläufe. Verhaltenshinweise können während dieser Zeit vorsichtiger sein. Je regelmäßiger Daten eingehen, desto besser kann Sentero Veränderungen bewerten. Direkte Sicherheitsmeldungen wie ein Rauchalarm funktionieren unabhängig von der Lernphase.",
        W - 2 * MARGIN_X,
    )
    return page


def settings_page(number: int) -> Image.Image:
    page = new_page("7. Einstellungen und Pflege", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    blocks = [
        ("Netzwerk", "Zeigt LAN, WLAN, Mobilfunk, lokales Netzwerk, Internet und Setup-WLAN getrennt an. Bei Problemen kann das Setup-WLAN erneut gestartet werden."),
        ("Sensoren", "Sensoren können geprüft, umbenannt, einem Raum zugeordnet oder entfernt werden. Batteriestatus und Erreichbarkeit helfen bei der Wartung."),
        ("Transparenz", "Zeigt Exporte, Benachrichtigungen und Freigaben. So bleibt nachvollziehbar, welche Informationen genutzt oder versendet wurden."),
        ("Konto", "Hier verwalten Sie Benutzerangaben und Anmeldung."),
        ("System", "Zeigt Systemzustand, Version, Updates und Werkseinstellungen."),
    ]
    for title, body in blocks:
        y = section(draw, MARGIN_X, y, title, body, W - 2 * MARGIN_X)
    draw.rounded_rectangle((MARGIN_X, y, W - MARGIN_X, y + 305), radius=30, fill=(255, 255, 255), outline=LINE, width=2)
    draw.text((MARGIN_X + 42, y + 38), "Updates", font=font(38, "bold"), fill=INK)
    draw_wrapped(
        draw,
        (MARGIN_X + 42, y + 98),
        "Unter Einstellungen -> System können Sie nach Updates suchen und verfügbare Updates installieren. Kundendaten, Räume, Sensorzuordnungen und Benachrichtigungseinstellungen bleiben bei normalen Updates erhalten.",
        font(31),
        MUTED,
        W - 2 * MARGIN_X - 84,
        44,
    )
    return page


def troubleshooting_page(number: int) -> Image.Image:
    page = new_page("8. Hilfe bei Problemen", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    cases = [
        ("Sentero ist nicht erreichbar", "Prüfen Sie Strom und Netzwerkkabel. Öffnen Sie http://sentero.local:8080. Wenn kein Heimnetz verbunden ist, suchen Sie nach Sentero-Setup-XXXX."),
        ("Ein Sensor meldet keine Daten", "Prüfen Sie Batterie, Stromversorgung und Position. In Einstellungen -> Sensoren kann der Sensor erneut geprüft werden."),
        ("Keine Benachrichtigung kommt an", "Verwenden Sie in den Einstellungen die Funktion Testnachricht senden. Prüfen Sie E-Mail-Einstellungen, Kontaktadresse und bei Telegram, ob die persönliche Verbindung eingerichtet wurde."),
        ("Internet ist weg", "Sentero arbeitet lokal weiter. Ausgehende Nachrichten werden nach Möglichkeit später versendet, sobald die Verbindung wieder da ist."),
        ("Update schlägt fehl", "Versuchen Sie es später erneut. Wenn der Fehler bleibt, kontaktieren Sie den Support und nennen Sie die angezeigte Version sowie den Update-Status."),
    ]
    for idx, (title, body) in enumerate(cases, 1):
        y = step_card(draw, MARGIN_X, y, W - 2 * MARGIN_X, idx, Step(title, body))
    y += 10
    draw.rounded_rectangle((MARGIN_X, y, W - MARGIN_X, y + 390), radius=30, fill=(255, 248, 247), outline=(222, 183, 178), width=3)
    draw.text((MARGIN_X + 42, y + 38), "ACHTUNG - Werkseinstellungen", font=font(38, "bold"), fill=CRITICAL)
    draw_wrapped(
        draw,
        (MARGIN_X + 42, y + 98),
        "Beim Zurücksetzen werden persönliche Daten, Benutzer, Sensorzuordnungen, Kontakte, Benachrichtigungseinstellungen, lokale Verlaufsdaten, Sensorkopplungen, gespeicherte WLANs und lokale Backups gelöscht. Sentero-Version, Box-ID und Systemsoftware bleiben erhalten. Nutzen Sie diese Funktion nur, wenn die Box neu eingerichtet werden soll.",
        font(31),
        MUTED,
        W - 2 * MARGIN_X - 84,
        44,
    )
    return page


def checklist_page(number: int) -> Image.Image:
    page = new_page("9. Kurze Checklisten", number)
    draw = ImageDraw.Draw(page)
    y = MARGIN_TOP + 160
    checklists = [
        ("Ersteinrichtung", ["Box steht stabil und hat Strom.", "LAN verbunden oder Setup-WLAN erreichbar.", "Erstes Benutzerkonto angelegt.", "Profil und Räume gespeichert.", "Mindestens ein Sensor eingerichtet.", "Mindestens ein Kontakt hinterlegt.", "Benachrichtigungskanal getestet.", "Rauchmelder geprüft, falls vorhanden."]),
        ("Regelmäßige Prüfung", ["Dashboard kurz ansehen.", "Sensoren auf Batterie und Erreichbarkeit prüfen.", "Testnachricht senden, wenn längere Zeit keine Meldung kam.", "Updates prüfen."]),
        ("Bei Wohnungswechsel oder Neuvergabe", ["Werkseinstellungen ausführen.", "Box nach Neustart neu verbinden.", "Profil, Räume, Sensoren und Kontakte neu einrichten."]),
    ]
    for title, items in checklists:
        draw.text((MARGIN_X, y), title, font=font(45, "bold"), fill=INK)
        y += 76
        for item in items:
            draw.rounded_rectangle((MARGIN_X, y + 2, MARGIN_X + 36, y + 38), radius=7, outline=SAGE_DARK, width=3)
            draw_wrapped(draw, (MARGIN_X + 62, y), item, font(32), INK, W - 2 * MARGIN_X - 62, 44)
            y += 58
        y += 46
    draw.line((MARGIN_X, H - 520, W - MARGIN_X, H - 520), fill=LINE, width=3)
    draw.text((MARGIN_X, H - 460), "Support-Notizen", font=font(42, "bold"), fill=INK)
    draw_wrapped(
        draw,
        (MARGIN_X, H - 395),
        "Halten Sie bei Rückfragen die Sentero-Version, den Netzwerkstatus und den angezeigten Systemzustand bereit. Diese Informationen finden Sie unter Einstellungen -> System.",
        font(31),
        MUTED,
        W - 2 * MARGIN_X,
        44,
    )
    return page


def main() -> None:
    pages = [
        title_page(),
        overview_page(1),
        setup_page(2),
        wizard_page(3),
        contacts_page(4),
        alerts_page(5),
        daily_use_page(6),
        settings_page(7),
        troubleshooting_page(8),
        checklist_page(9),
    ]
    rgb_pages = [page.convert("RGB") for page in pages]
    rgb_pages[0].save(OUT_PNG, quality=96, dpi=(300, 300))
    rgb_pages[0].save(OUT_PDF, "PDF", resolution=300.0, save_all=True, append_images=rgb_pages[1:])
    print(OUT_PNG)
    print(OUT_PDF)


if __name__ == "__main__":
    main()
