#!/usr/bin/env python3
"""Добавляет или заменяет фото блюда файлом с Wikimedia Commons: проверяет лицензию КОНКРЕТНОГО ФАЙЛА через API (нужны CC0 и AttributionRequired=false),
скачивает оригинал, кадрирует 4:3, делает основной снимок 1200×900 и миниатюру 480×360 (WebP), обновляет menu.json (только поле photo) и реестр
registry/photo-licenses.json. Цены, составы и прочие данные меню не меняются.
Запуск:  python3 tools/add_commons_photo.py ИД_БЛЮДА "File:Имя файла.jpg" [--focus X,Y] [--zoom 1..4] [--match exact|approximate] [--note "пояснение"]
Требуется Pillow; сеть — только curl (системные сертификаты)."""
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import date
from html import unescape
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
MENU, REG, IMG = ROOT / "docs" / "data" / "menu.json", ROOT / "registry" / "photo-licenses.json", ROOT / "docs" / "assets" / "img" / "dishes"
UA = "bistro-demo-photo-tool/1.0 (license-checked)"
MAIN, THUMB, MAX_MAIN, MAX_THUMB = (1200, 900), (480, 360), 250 * 1024, 60 * 1024

def curl(url, out=None):
    """Запрос через curl с повтором: Wikimedia отвечает 429 при частых запросах — тогда ждём (30, 60, 90… секунд) и пробуем снова."""
    import time
    for attempt in range(1, 9):
        cmd = ["curl", "-sSL", "-m", "180", "-A", UA, "-w", "\n%{http_code}"] + (["-o", str(out)] if out else []) + [url]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode: raise SystemExit(f"curl: {r.stderr.decode()[:200]}")
        body, _, code = r.stdout.rpartition(b"\n") if not out else (b"", b"", r.stdout.strip().rpartition(b"\n")[2])
        if code.strip() == b"429": time.sleep(30 * attempt); continue
        if not code.strip().startswith(b"2"): raise SystemExit(f"HTTP {code.decode().strip()} для {url[:80]}")
        return body if not out else b""
    raise SystemExit("Wikimedia продолжает отвечать 429 (слишком много запросов): повторите позже")

def commons_info(title):
    q = urllib.parse.urlencode({"action": "query", "titles": title, "prop": "imageinfo", "iiprop": "url|size|sha1|timestamp|extmetadata", "format": "json", "formatversion": 2})
    d = json.loads(curl("https://commons.wikimedia.org/w/api.php?" + q))
    page = d["query"]["pages"][0]
    if page.get("missing") or not page.get("imageinfo"): raise SystemExit(f"Файл не найден на Commons: {title}")
    return page["imageinfo"][0]

def sha256(b): return hashlib.sha256(b).hexdigest()

def webp(img, size, qualities, limit):
    out = None
    for q in qualities:
        buf = io.BytesIO(); img.save(buf, "WEBP", quality=q, method=6)
        if len(buf.getvalue()) <= limit: return buf.getvalue(), q
        out = (buf.getvalue(), q)
    raise SystemExit(f"Не удалось уложиться в {limit // 1024} КБ даже при q={out[1]}")

def main():
    a = sys.argv[1:]
    if len(a) < 2: raise SystemExit(__doc__)
    dish_id, title = a[0], a[1]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    fx, fy = (float(v) for v in opt("--focus", "0.5,0.5").split(","))
    menu, reg = json.loads(MENU.read_text(encoding="utf-8")), json.loads(REG.read_text(encoding="utf-8"))
    dish = next((d for d in menu["dishes"] if d["id"] == dish_id), None)
    if not dish: raise SystemExit(f"Нет блюда «{dish_id}» в menu.json")
    info = commons_info(title if title.startswith("File:") else "File:" + title); md = info["extmetadata"]
    lic, attr = md.get("LicenseShortName", {}).get("value", ""), md.get("AttributionRequired", {}).get("value", "")
    if lic.strip().upper() not in ("CC0", "CC0 1.0") or attr != "false": raise SystemExit(f"Лицензия файла не подходит: {lic!r}, AttributionRequired={attr!r}")
    tmp = Path(tempfile.mkdtemp()) / "orig"; curl(info["url"], tmp); raw = tmp.read_bytes()
    if hashlib.sha1(raw).hexdigest() != info["sha1"]: raise SystemExit("Контрольная сумма скачанного файла не совпала с данными Commons")
    for e in reg["photos"]:
        if e["dish_id"] != dish_id and e.get("original_sha256") == sha256(raw): raise SystemExit(f"Этот снимок уже используется для «{e['dish_id']}»")
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    W, H = im.size; target = 4 / 3
    cw, ch = (int(H * target), H) if W / H > target else (W, int(W / target))
    zoom = max(1.0, min(float(opt("--zoom", "1")), 4.0)); cw, ch = int(cw / zoom), int(ch / zoom)       # --zoom: взять кадр крупнее (убрать лишнее вокруг блюда)
    left, top = int((W - cw) * min(max(fx, 0), 1)), int((H - ch) * min(max(fy, 0), 1))
    im = im.crop((left, top, left + cw, top + ch))
    main = ImageOps.autocontrast(im.resize(MAIN, Image.LANCZOS), cutoff=0.3, preserve_tone=True).filter(ImageFilter.UnsharpMask(1.2, 60, 3))
    thumb = ImageOps.autocontrast(im.resize(THUMB, Image.LANCZOS), cutoff=0.3, preserve_tone=True).filter(ImageFilter.UnsharpMask(1.0, 50, 3))
    mb, mq = webp(main, MAIN, (80, 76, 72, 68, 64), MAX_MAIN); tb, tq = webp(thumb, THUMB, (78, 74, 70, 66, 62), MAX_THUMB)
    (IMG / f"{dish_id}.webp").write_bytes(mb); (IMG / f"{dish_id}-thumb.webp").write_bytes(tb)
    dish["photo"] = {"file": f"{dish_id}.webp", "thumb": f"{dish_id}-thumb.webp", "alt": dish["name"], "width": MAIN[0], "height": MAIN[1]}
    author = unescape(re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", ""))).strip() or "не указан"
    page_url = "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(info["descriptionurl"].split("/wiki/")[-1], safe=":/(),_-.%")
    entry = {"dish_id": dish_id, "file": f"{dish_id}.webp", "thumb": f"{dish_id}-thumb.webp", "source_type": "stock", "source_name": "Wikimedia Commons", "source_url": info["descriptionurl"],
             "file_url": info["url"], "original_title": title.removeprefix("File:"), "author": author, "license": "CC0 1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
             "license_verification": f"API Wikimedia Commons: LicenseShortName={lic}, AttributionRequired={attr}, {md.get('UsageTerms', {}).get('value', '')}",
             "credit": unescape(re.sub(r"<[^>]+>", "", md.get("Credit", {}).get("value", ""))).strip()[:120], "file_timestamp": info["timestamp"], "commons_sha1": info["sha1"], "original_size": f"{W}x{H}",
             "retrieved_at": date.today().isoformat(), "checked_by": "Claude (проверка лицензии файла через API Commons + визуальный отбор и сверка с составом блюда)",
             "processing": {"crop": f"4:3, фокус ({fx:g}, {fy:g})" + (f", увеличение ×{zoom:g}" if zoom > 1 else ""), "main": f"{MAIN[0]}x{MAIN[1]} WebP q{mq}", "thumb": f"{THUMB[0]}x{THUMB[1]} WebP q{tq}",
                            "adjustments": "автоконтраст по яркости 0,3% (цвета не сдвигаются), unsharp mask; без тонирования"},
             "original_sha256": sha256(raw), "file_sha256": sha256(mb), "thumb_sha256": sha256(tb), "dish_name": dish["name"],
             "license_evidence": {"commons_api": {"LicenseShortName": lic, "AttributionRequired": attr, "UsageTerms": md.get("UsageTerms", {}).get("value", ""), "rechecked": date.today().isoformat()}},
             "match": opt("--match", "exact"), "match_note": opt("--note", ""), "photo_quality": "любительский снимок (не студийная съёмка)"}
    old = next((e for e in reg["photos"] if e["dish_id"] == dish_id), None)
    if old:
        reg["photos"] = [e for e in reg["photos"] if e["dish_id"] != dish_id]
        reg.setdefault("replaced_photos", []).append({"dish_id": dish_id, "dish_name": dish["name"], "action": "заменён снимок", "why": opt("--why", "лучшее соответствие блюду"), "was": f"{old['source_name']} — {old['original_title']}", "now": f"Wikimedia Commons — {entry['original_title']}"})
    reg["photos"].append(entry); reg["photos"].sort(key=lambda e: [d["id"] for d in menu["dishes"]].index(e["dish_id"]))
    reg["without_photo"] = [w for w in reg["without_photo"] if w["dish_id"] != dish_id]
    reg["updated"] = date.today().isoformat()
    MENU.write_text(json.dumps(menu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); REG.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{dish_id}: {W}x{H} → {MAIN[0]}x{MAIN[1]}, основной {len(mb) // 1024} КБ (q{mq}), миниатюра {len(tb) // 1024} КБ (q{tq}), лицензия {lic}")

main()
