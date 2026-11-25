#!/usr/bin/env python3
"""Проверка путей и ресурсов сайта для GitHub Pages в подкаталоге (https://имя.github.io/репозиторий/).
Правила: только относительные пути; data-root страницы соответствует её глубине; все файлы, на которые есть ссылки, существуют;
нет внешних ресурсов (шрифты, скрипты, картинки только свои); нет корневых абсолютных путей вида "/assets/...".
Запуск из корня репозитория:  python3 tools/check_paths.py  [корень]"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
SITE = ROOT / "docs"
errors, notes = [], []
referenced = set()

def err(m): errors.append(m)
def is_external(v): return bool(re.match(r"^(https?:)?//", v))
def is_skippable(v): return not v or v.startswith(("#", "mailto:", "tel:", "data:", "javascript:"))

def resolve(base_file: Path, ref: str):
    ref = ref.split("#")[0].split("?")[0]
    target = (base_file.parent / ref).resolve()
    return target

class Page(HTMLParser):
    def __init__(self): super().__init__(); self.refs = []; self.root = None
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html": self.root = a.get("data-root")
        for key in ("href", "src", "poster", "data-src"):
            if key in a and a[key] is not None: self.refs.append((tag, key, a[key]))
        if "srcset" in a and a["srcset"]:
            for part in a["srcset"].split(","): self.refs.append((tag, "srcset", part.strip().split()[0]))

pages = sorted(p for p in SITE.rglob("*.html"))
for page in pages:
    rel = page.relative_to(SITE)
    text = page.read_text(encoding="utf-8")
    pp = Page(); pp.feed(text)
    is404 = rel.name == "404.html"
    depth = len(rel.parts) - 1
    if not is404:
        want = "./" if depth == 0 else "../" * depth
        if pp.root != want: err(f"{rel}: data-root должен быть «{want}» (сейчас {pp.root!r})")
    for tag, key, v in pp.refs:
        if is_skippable(v): continue
        if is_external(v):
            if not (tag == "a" and key == "href"): err(f"{rel}: внешний ресурс <{tag} {key}={v[:50]}> — допускаются только свои файлы")
            continue
        if v.startswith("/"):
            err(f"{rel}: корневой абсолютный путь «{v}» сломается в подкаталоге Pages")
            continue
        if is404 and not v.startswith(("http", "./")) and key in ("href", "src") and tag != "a":
            err(f"{rel}: ресурс «{v}» на 404-странице ненадёжен (отдаётся на любой глубине) — встраивайте стили и скрипты")
        t = resolve(page, v)
        try: t.relative_to(SITE)
        except ValueError: err(f"{rel}: «{v}» выходит за пределы папки сайта"); continue
        if not t.exists() and not (v.endswith("/") and (t / "index.html").exists()):
            if not (tag == "a" and key == "href" and v.split("?")[0] in PLANNED): err(f"{rel}: не найден файл «{v}»")
        else:
            referenced.add(t)
    # встроенные модули: import ... from "…"
    for m in re.finditer(r'from\s+["\']([^"\']+)["\']', text):
        v = m.group(1)
        if v.startswith("/") or is_external(v): err(f"{rel}: импорт «{v}» — только относительные пути")
        elif not resolve(page, v).exists(): err(f"{rel}: не найден модуль «{v}»")
        else: referenced.add(resolve(page, v))

# страницы, которые появятся на следующих этапах: ссылки на них допустимы только в навигации layout.js с ready:false
PLANNED = set()

for js in sorted(SITE.rglob("*.js")):
    rel = js.relative_to(SITE); text = js.read_text(encoding="utf-8")
    for m in re.finditer(r"""(?:from|import\()\s*["']([^"']+)["']""", text):
        v = m.group(1)
        if v.startswith("/") or is_external(v): err(f"{rel}: импорт «{v}» — только относительные пути")
        elif not resolve(js, v).exists(): err(f"{rel}: не найден модуль «{v}»")
        else: referenced.add(resolve(js, v))
    for m in re.finditer(r"""["'`](/(?:assets|data|admin|docs)/[^"'`]*)["'`]""", text):
        err(f"{rel}: корневой абсолютный путь «{m.group(1)}»")
    for m in re.finditer(r"""fetch\(\s*["'`](https?:[^"'`]+)""", text):
        err(f"{rel}: запрос к внешнему адресу {m.group(1)[:50]}")
    # ready:false в навигации — страницы-«на потом»: проверяем только готовые
    for m in re.finditer(r'\{\s*href:\s*"([^"]+)",\s*label:\s*"[^"]*",\s*ready:\s*(true|false)', text):
        target = SITE / m.group(1)
        if m.group(2) == "true" and not target.exists(): err(f"{rel}: страница «{m.group(1)}» помечена ready:true, но файла нет")
        if m.group(2) == "false" and target.exists(): notes.append(f"{rel}: страница «{m.group(1)}» уже существует, но ready:false (включите ссылку)")

for css in sorted(SITE.rglob("*.css")):
    rel = css.relative_to(SITE); text = css.read_text(encoding="utf-8")
    for m in re.finditer(r"url\(\s*[\"']?([^)\"']+)[\"']?\s*\)", text):
        v = m.group(1).strip()
        if is_skippable(v): continue
        if is_external(v) or v.startswith("/"): err(f"{rel}: url({v[:50]}) — внешний или корневой путь"); continue
        t = resolve(css, v)
        if not t.exists(): err(f"{rel}: не найден файл url({v})")
        else: referenced.add(t)
    if "@import" in text and re.search(r"@import\s+url\(\s*[\"']?https?:", text): err(f"{rel}: внешний @import")

# служебные файлы Pages
if not (SITE / ".nojekyll").exists(): err("docs/.nojekyll отсутствует (иначе Pages обработает сайт Jekyll-ом)")
if not (SITE / "404.html").exists(): err("docs/404.html отсутствует")
if not (SITE / "index.html").exists(): err("docs/index.html отсутствует")

# размеры
files = [p for p in SITE.rglob("*") if p.is_file()]
total = sum(p.stat().st_size for p in files)
for p in files:
    if p.stat().st_size > 1_000_000: err(f"{p.relative_to(SITE)}: файл больше 1 МБ ({p.stat().st_size // 1024} КБ)")
unref = [p.relative_to(SITE) for p in files if p not in referenced and p.suffix in (".woff2", ".svg", ".webp", ".png", ".jpg", ".css", ".js") and p.name != "favicon.svg"]
print(f"Страниц: {len(pages)}; файлов: {len(files)}; общий размер: {total / 1024:.0f} КБ")
if len(unref) > 10: print(f"СПРАВКА: файлов, ещё не упомянутых на страницах (подключатся на следующих этапах): {len(unref)}")
else:
    for u in unref: print("СПРАВКА: файл не упомянут ни на одной странице (подключится позже?):", u)
for n in notes: print("СПРАВКА:", n)
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "пути корректны" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
