#!/usr/bin/env python3
"""Проверка фотографий блюд: файлы, формат, пропорции, размеры, соответствие внутреннему реестру лицензий и отсутствие
следов источников в публичной части сайта. Только стандартная библиотека.
Запуск из корня репозитория:  python3 tools/check_photos.py  [корень]"""
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
SITE = ROOT / "docs"
IMG = SITE / "assets" / "img" / "dishes"
MAX_MAIN, MAX_THUMB = 250 * 1024, 60 * 1024
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
# Названия и адреса источников: на публичной части сайта их быть не должно (они только во внутреннем реестре вне docs/)
SOURCE_WORDS = re.compile(r"rawpixel|wikimedia|wikipedia|openverse|unsplash|pexels|pixabay|flickr|stocksnap|shutterstock|istock|gettyimages|creativecommons|cc0", re.I)
errors = []
def err(m): errors.append(m)

def webp_size(data: bytes):
    """Размер WebP из заголовка (VP8, VP8L, VP8X) или None."""
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP": return None
    kind = data[12:16]
    if kind == b"VP8 ": w, h = struct.unpack("<HH", data[26:30]); return w & 0x3FFF, h & 0x3FFF
    if kind == b"VP8L": b = struct.unpack("<I", data[21:25])[0]; return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    if kind == b"VP8X": return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    return None

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

try:
    menu = json.loads((SITE / "data" / "menu.json").read_text(encoding="utf-8"))
    reg = json.loads((ROOT / "registry" / "photo-licenses.json").read_text(encoding="utf-8"))
except Exception as e:  # noqa: BLE001
    print("ОШИБКА: не читаются menu.json или registry/photo-licenses.json —", e); sys.exit(1)

dishes = {d["id"]: d for d in menu["dishes"]}
with_photo = {i: d["photo"] for i, d in dishes.items() if d.get("photo")}
without = [i for i, d in dishes.items() if not d.get("photo")]
entries = {}
for e in reg.get("photos", []):
    if e.get("dish_id") in entries: err(f"реестр: блюдо «{e.get('dish_id')}» указано дважды")
    entries[e.get("dish_id")] = e

# ---------- файлы фото ----------
referenced = set()
for i, ph in with_photo.items():
    for key, maxb, size in (("file", MAX_MAIN, None), ("thumb", MAX_THUMB, (480, 360))):
        f = IMG / ph[key]; referenced.add(f.name)
        if not re.fullmatch(r"[a-z0-9-]+(-thumb)?\.webp", ph[key]): err(f"«{i}»: имя файла «{ph[key]}» — латиница, цифры, дефис, расширение .webp"); continue
        if not f.is_file(): err(f"«{i}»: нет файла {ph[key]}"); continue
        data = f.read_bytes(); wh = webp_size(data)
        if wh is None: err(f"«{i}»: {ph[key]} — не WebP"); continue
        w, h = wh
        if len(data) > maxb: err(f"«{i}»: {ph[key]} — {len(data) // 1024} КБ, больше лимита {maxb // 1024} КБ")
        if abs(w * 3 - h * 4) > 4: err(f"«{i}»: {ph[key]} — {w}x{h}, не 4:3")
        if size and (w, h) != size: err(f"«{i}»: миниатюра {w}x{h}, ожидается {size[0]}x{size[1]}")
        if key == "file":
            if not 640 <= w <= 1600: err(f"«{i}»: ширина основного фото {w}px вне диапазона 640–1600")
            if (ph.get("width"), ph.get("height")) != (w, h): err(f"«{i}»: width/height в menu.json ({ph.get('width')}x{ph.get('height')}) не совпадают с файлом ({w}x{h})")
        if re.search(rb"https?://|rawpixel|wikimedia|unsplash|pexels|pixabay|openverse", data, re.I): err(f"«{i}»: в {ph[key]} найдены следы источника (метаданные)")
    if not ph.get("alt", "").strip(): err(f"«{i}»: у фото нет alt")
for f in sorted(IMG.glob("*")) if IMG.exists() else []:
    if f.name not in referenced: err(f"файл {f.name} не подключён ни к одному блюду")
if not (SITE / "assets" / "img" / "placeholder-dish.svg").is_file(): err("нет заглушки assets/img/placeholder-dish.svg")

# ---------- реестр ----------
REQ = ("dish_id", "dish_name", "file", "thumb", "source_type", "source_name", "source_url", "file_url", "original_title", "author", "license", "license_url",
       "license_verification", "retrieved_at", "original_sha256", "file_sha256", "thumb_sha256", "processing")
ALLOWED_SOURCES = {"ISO Republic", "Foodiesfeed", "Negative Space", "Wikimedia Commons"}
AGGREGATORS = re.compile(r"rawpixel|openverse|pxhere|free-images|pickupimage|pixabay|pexels|unsplash\.com", re.I)   # агрегаторы и сторонние перепубликации — не источники
seen_orig = {}
for i, e in entries.items():
    for k in REQ:
        if not e.get(k): err(f"реестр «{i}»: пусто поле {k}")
    if i not in dishes: err(f"реестр: блюда «{i}» нет в menu.json"); continue
    if i not in with_photo: err(f"реестр: для «{i}» есть запись, но в menu.json фото нет"); continue
    ph = with_photo[i]
    if e.get("file") != ph["file"] or e.get("thumb") != ph["thumb"]: err(f"реестр «{i}»: имена файлов не совпадают с menu.json")
    if e.get("dish_name") != dishes[i]["name"]: err(f"реестр «{i}»: название блюда не совпадает с menu.json")
    if e.get("license") != "CC0 1.0" or e.get("license_url") != CC0_URL: err(f"реестр «{i}»: допускается только CC0 1.0 с ссылкой на {CC0_URL}")
    if not str(e.get("source_url", "")).startswith("https://"): err(f"реестр «{i}»: source_url должен быть https-ссылкой")
    if e.get("source_name") not in ALLOWED_SOURCES or AGGREGATORS.search(str(e.get("source_name", "")) + " " + str(e.get("source_url", ""))):
        err(f"реестр «{i}»: источник «{e.get('source_name')}» не допускается (разрешены только первоисточники: {', '.join(sorted(ALLOWED_SOURCES))})")
    ev = e.get("license_evidence") or {}
    if e.get("source_name") == "Wikimedia Commons":
        c = ev.get("commons_api") or {}
        if c.get("LicenseShortName") != "CC0" or c.get("AttributionRequired") != "false": err(f"реестр «{i}»: нет доказательства лицензии Commons (LicenseShortName=CC0, AttributionRequired=false)")
    else:
        pg = ev.get("page") or {}
        if "CC0" not in str(pg.get("statement", "")): err(f"реестр «{i}»: в доказательстве нет заявления CC0 со страницы первоисточника")
        if not re.fullmatch(r"[0-9a-f]{64}", str(pg.get("page_sha256", ""))): err(f"реестр «{i}»: нет контрольной суммы страницы-доказательства")
        host = lambda u: re.sub(r"^https?://(www\.)?", "", str(u)).split("/")[0]
        if host(pg.get("checked_url")) != host(e.get("source_url")): err(f"реестр «{i}»: страница-доказательство и source_url с разных сайтов")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(e.get("retrieved_at", ""))): err(f"реестр «{i}»: retrieved_at — ГГГГ-ММ-ДД")
    for key, name in (("file_sha256", "file"), ("thumb_sha256", "thumb")):
        f = IMG / ph[name]
        if f.is_file() and e.get(key) != sha(f): err(f"реестр «{i}»: контрольная сумма {name} не совпадает — файл заменён или перепутан")
    o = e.get("original_sha256")
    if o in seen_orig: err(f"реестр: один и тот же исходный снимок у блюд «{seen_orig[o]}» и «{i}»")
    seen_orig[o] = i
for i in with_photo:
    if i not in entries: err(f"у блюда «{i}» есть фото, но нет записи в реестре лицензий")
listed = [w.get("dish_id") for w in reg.get("without_photo", [])]
if sorted(listed) != sorted(without): err(f"реестр: список «без фото» {sorted(listed)} не совпадает с menu.json {sorted(without)}")
for w in reg.get("without_photo", []):
    if not w.get("reason"): err(f"реестр: у блюда без фото «{w.get('dish_id')}» нет причины")
if (SITE / "registry").exists() or any("registry" in str(p) for p in SITE.rglob("*") if p.is_dir()): err("реестр не должен лежать внутри docs/ (иначе он публикуется)")

# ---------- публичная часть без ссылок на источники ----------
TEXT = {".html", ".js", ".css", ".json", ".svg", ".txt", ".md", ".xml"}
for p in sorted(SITE.rglob("*")):
    if not p.is_file() or p.suffix.lower() not in TEXT or p.parts[-2:-1] == ("fonts",): continue
    for m in SOURCE_WORDS.finditer(p.read_text(encoding="utf-8", errors="ignore")):
        err(f"{p.relative_to(SITE)}: на публичной части упоминание источника или лицензии «{m.group(0)}»")
        break

print(f"Фото подключено: {len(with_photo)}; без фото (заглушка): {len(without)} — {', '.join(sorted(without)) or 'нет'}")
main_sz = sum((IMG / ph["file"]).stat().st_size for ph in with_photo.values() if (IMG / ph["file"]).is_file())
thumb_sz = sum((IMG / ph["thumb"]).stat().st_size for ph in with_photo.values() if (IMG / ph["thumb"]).is_file())
print(f"Размер: основные {main_sz // 1024} КБ, миниатюры {thumb_sz // 1024} КБ, всего {(main_sz + thumb_sz) // 1024} КБ")
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "фотографии и реестр корректны" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
