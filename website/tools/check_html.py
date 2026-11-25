#!/usr/bin/env python3
"""Структурные проверки HTML-страниц сайта: язык, заголовок, описание, viewport, один h1, ориентиры, alt и размеры картинок,
нет встроенных обработчиков (onclick и т. п.), безопасные внешние ссылки. Только стандартная библиотека.
Запуск из корня репозитория:  python3 tools/check_html.py  [корень]"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
SITE = ROOT / "docs"
errors = []
def err(m): errors.append(m)

class P(HTMLParser):
    def __init__(self):
        super().__init__(); self.tags = []; self.title = ""; self._t = False; self.meta = {}; self.imgs = []; self.links = []; self.lang = None; self.handlers = []; self.ids = []
        self.h = {1: 0, 2: 0}; self.scripts = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs); self.tags.append(tag)
        if tag == "html": self.lang = a.get("lang")
        if tag == "title": self._t = True
        if tag == "meta" and a.get("name"): self.meta[a["name"]] = a.get("content", "")
        if tag == "img": self.imgs.append(a)
        if tag == "a": self.links.append(a)
        if tag == "script": self.scripts.append(a)
        if tag in ("h1", "h2"): self.h[int(tag[1])] += 1
        if a.get("id"): self.ids.append(a["id"])
        for k in a:
            if k.startswith("on"): self.handlers.append(f"<{tag} {k}>")
    def handle_endtag(self, tag):
        if tag == "title": self._t = False
    def handle_data(self, d):
        if self._t: self.title += d

SCRIPT_H1 = {"index.html", "dish.html", "menu.html", "cart.html", "checkout.html", "payment.html", "order.html", "orders.html"}   # заголовок h1 создаётся скриптом из данных
pages = sorted(p for p in SITE.rglob("*.html"))
for page in pages:
    rel = page.relative_to(SITE); text = page.read_text(encoding="utf-8"); p = P(); p.feed(text)
    if not text.lstrip().lower().startswith("<!doctype html>"): err(f"{rel}: нет <!doctype html> (иначе режим совместимости)")
    if p.lang != "ru" and rel.name != "404.html": err(f"{rel}: у <html> должен быть lang=\"ru\"")
    if p.lang is None: err(f"{rel}: нет lang")
    if not 8 <= len(p.title.strip()) <= 70: err(f"{rel}: заголовок страницы — от 8 до 70 символов (сейчас {len(p.title.strip())})")
    if "viewport" not in p.meta or "width=device-width" not in p.meta["viewport"]: err(f"{rel}: нет meta viewport")
    if rel.name not in ("404.html", "styleguide.html") and not 40 <= len(p.meta.get("description", "")) <= 200: err(f"{rel}: meta description — от 40 до 200 символов")
    if rel.name != "404.html":
        if "main" not in p.tags: err(f"{rel}: нет <main>")
        if not any(a.get("class") == "skip-link" for a in p.links): err(f"{rel}: нет ссылки «К содержимому» (skip-link)")
        if "noscript" not in p.tags and rel.name != "styleguide.html": err(f"{rel}: нет <noscript> с пояснением, что нужен JavaScript")
    h1 = len(re.findall(r"<h1\b", text))
    if rel.name == "404.html": ok = h1 == 1
    elif rel.as_posix() in SCRIPT_H1: ok = h1 == 0   # h1 создаёт скрипт страницы (заголовок героя, название блюда, «Корзина», «Меню»)
    elif rel.name == "styleguide.html": ok = h1 >= 1
    else: ok = h1 == 1
    if not ok: err(f"{rel}: неверное число <h1> в статической разметке ({h1})")
    for im in p.imgs:
        if "alt" not in im: err(f"{rel}: <img> без alt ({im.get('src')})")
        if not (im.get("width") and im.get("height")) and not (im.get("src") or "").endswith(".svg"): err(f"{rel}: <img> без width/height ({im.get('src')})")
    for hnd in p.handlers: err(f"{rel}: встроенный обработчик события {hnd} (используйте addEventListener)")
    for a in p.links:
        if a.get("target") == "_blank" and "noopener" not in (a.get("rel") or ""): err(f"{rel}: ссылка target=_blank без rel=noopener")
    for s in p.scripts:
        src = s.get("src") or ""
        if src.startswith(("http:", "https:", "//")): err(f"{rel}: внешний скрипт {src[:50]}")
    if len(p.ids) != len(set(p.ids)): err(f"{rel}: повторяются id: {sorted({i for i in p.ids if p.ids.count(i) > 1})}")
    if re.search(r"<(script|link)[^>]+(src|href)=\"https?:", text): err(f"{rel}: подключение внешнего ресурса")
print(f"Страниц проверено: {len(pages)}")
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "структура HTML корректна" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
