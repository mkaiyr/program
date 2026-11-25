"""Чек в виде картинки и команд ESC/POS для термопринтера 80 мм.

Почему растр. Встроенные кодовые страницы термопринтеров (CP866, CP1251 и т. п.) не содержат казахских букв қ ұ ү ғ ң і ә ө,
поэтому текстовая печать их теряет или заменяет. Здесь чек рисуется картинкой с встроенным шрифтом DejaVu Sans Mono
(кириллица, казахские буквы, ₸ и №) и отправляется принтеру как растровое изображение (команда GS v 0) — результат не зависит
от кодовых страниц принтера и шрифтов компьютера.

80 мм: печатная область обычно 72 мм = 576 точек при 203 dpi (часть моделей — 512 точек; настраивается). Нужен Pillow.
"""

from pathlib import Path

from receipt import COLS

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:  # без Pillow работает только текстовый предпросмотр
    Image = ImageDraw = ImageFont = ImageOps = None

FONT_DIR = Path(__file__).with_name("fonts")
FONT_PX = 22
WIDTH_DOTS = 576
BAND = 128                    # строк растра в одной команде: длинный чек отправляется кусками
PROBE = "\U0010FFFF"          # символ, которого нет ни в одном шрифте: его «пустая рамка» — образец отсутствующей буквы


class RenderError(Exception):
    pass


def available():
    return Image is not None


def _require():
    if Image is None:
        raise RenderError("Для печати и графического предпросмотра нужна библиотека Pillow: pip install Pillow")


def load_fonts(px=FONT_PX):
    _require()
    try:
        return (ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), px),
                ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), px))
    except OSError as e:
        raise RenderError("Не найден шрифт чека (папка fonts/): переустановите приложение.") from e


def _ink(font, ch):
    mask = font.getmask(ch)
    return bytes(mask), mask.size


def missing_chars(font, text):
    """Символы текста, которых нет в шрифте (шрифт рисует вместо них пустую рамку)."""
    probe = _ink(font, PROBE)
    return sorted({ch for ch in text if ch.strip() and _ink(font, ch) == probe})


def render_receipt(receipt, width_dots=WIDTH_DOTS, font_px=FONT_PX):
    """Чек -> картинка 1 бит (чёрное на белом) шириной width_dots. Возвращает (картинка, строка с заменёнными символами или '')."""
    _require()
    if width_dots % 8 or not 256 <= width_dots <= 832:
        raise RenderError("Ширина печати должна быть кратна 8 точкам (для 80 мм — 576 или 512).")
    regular, bold = load_fonts(font_px)
    cell = regular.getlength("0")
    if receipt.cols * cell > width_dots + 0.5:
        raise RenderError("Строка чека шире печатной области: уменьшите шрифт или число знаков.")
    asc, desc = regular.getmetrics(); height = asc + desc + 4
    margin = int((width_dots - receipt.cols * cell) // 2)   # поля: край бумаги у многих принтеров не печатается
    lost = set()
    rows = []
    for ln in receipt.lines:
        if ln.kind == "text" and ln.text:
            bad = missing_chars(bold if ln.bold else regular, ln.text)
            if bad:
                lost.update(bad); ln = type(ln)("".join("?" if c in bad else c for c in ln.text), ln.bold, ln.align, ln.kind)
        rows.append(ln)
    img = Image.new("L", (width_dots, height * len(rows) + 16), 255)
    d = ImageDraw.Draw(img); y = 8
    for ln in rows:
        if ln.kind == "rule":
            d.line([(margin, y + height // 2), (width_dots - margin, y + height // 2)], fill=0, width=2)
        elif ln.kind == "text" and ln.text:
            font = bold if ln.bold else regular; w = font.getlength(ln.text)
            x = {"center": (width_dots - w) / 2, "right": width_dots - margin - w}.get(ln.align, margin)
            d.text((max(0, x), y), ln.text, font=font, fill=0)
        y += height
    return img.point(lambda v: 255 if v > 140 else 0).convert("1"), "".join(sorted(lost))


# ---------- ESC/POS ----------

ESC_INIT = b"\x1b@"
CUT_PARTIAL = b"\x1dV\x42\x00"          # GS V 66 0: подача и частичная обрезка


def escpos_raster(img, band=BAND):
    """Картинка (1 бит) -> растровые команды GS v 0 (куски по band строк). Чёрная точка = 1."""
    _require()
    img = img.convert("1")
    w, h = img.size
    if w % 8:
        raise RenderError("Ширина картинки должна быть кратна 8.")
    ink = ImageOps.invert(img.convert("L")).convert("1")   # чернила -> 1
    wb, out = w // 8, bytearray()
    for top in range(0, h, band):
        part = ink.crop((0, top, w, min(h, top + band)))
        rows = part.size[1]
        out += b"\x1dv0\x00" + bytes([wb & 255, wb >> 8, rows & 255, rows >> 8]) + part.tobytes()
    return bytes(out)


def escpos_job(img, cut=True, feed=3):
    """Полное задание печати: инициализация, чек, подача бумаги, обрезка."""
    data = ESC_INIT + escpos_raster(img) + b"\x1bd" + bytes([max(0, min(feed, 255))])
    return data + (CUT_PARTIAL if cut else b"")


def decode_escpos_raster(data):
    """Обратное преобразование (для проверок): команды GS v 0 -> картинка. Нужна, чтобы доказать, что байты содержат именно этот чек."""
    _require()
    i, strips, width = 0, [], None
    while i < len(data):
        if data[i:i + 4] == b"\x1dv0\x00":
            wb, rows = data[i + 4] | data[i + 5] << 8, data[i + 6] | data[i + 7] << 8
            body = data[i + 8:i + 8 + wb * rows]
            if len(body) != wb * rows: raise RenderError("Команда растра оборвана.")
            strips.append(Image.frombytes("1", (wb * 8, rows), body)); width = wb * 8; i += 8 + wb * rows
        else:
            i += 1
    if not strips: raise RenderError("В данных нет растровых команд.")
    out = Image.new("1", (width, sum(s.size[1] for s in strips)), 0); y = 0
    for s in strips: out.paste(s, (0, y)); y += s.size[1]
    return ImageOps.invert(out.convert("L")).convert("1")
