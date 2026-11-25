#!/usr/bin/env python3
"""Раздаёт docs/ так, как GitHub Pages раздаёт проектный сайт: только под префиксом /<репозиторий>/, всё остальное — 404.
Затем обходит страницы, проверяет каждый ресурс и данные, и убеждается, что 404.html отдаётся с кодом 404.
Запуск из корня репозитория:  python3 tools/check_subpath.py [имя_репозитория] [корень]"""
import http.server
import json
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

REPO = sys.argv[1] if len(sys.argv) > 1 else "kafe-bistro"
ROOT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent
SITE = ROOT / "docs"
PREFIX = f"/{REPO}/"
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml",
        ".woff2": "font/woff2", ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".txt": "text/plain; charset=utf-8"}

class PagesLike(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == f"/{REPO}":  # Pages перенаправляет на адрес со слешем
            self.send_response(301); self.send_header("Location", PREFIX); self.end_headers(); return
        if path.startswith(PREFIX):
            rel = urllib.parse.unquote(path[len(PREFIX):])
            f = (SITE / rel).resolve()
            if f.is_dir(): f = f / "index.html"
            if str(f).startswith(str(SITE)) and f.is_file():
                body = f.read_bytes(); self.send_response(200)
                self.send_header("Content-Type", MIME.get(f.suffix, "application/octet-stream")); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        body = (SITE / "404.html").read_bytes(); self.send_response(404)
        self.send_header("Content-Type", MIME[".html"]); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PagesLike)
base = f"http://127.0.0.1:{srv.server_address[1]}"
threading.Thread(target=srv.serve_forever, daemon=True).start()
errors, checked = [], 0

def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r: return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e: return e.code, e.read(), e.headers.get("Content-Type", "")

class Refs(HTMLParser):
    def __init__(self): super().__init__(); self.refs = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("href", "src"):
            if a.get(k): self.refs.append(a[k])

def check(url, what):
    global checked
    checked += 1
    st, body, ctype = get(url)
    if st != 200: errors.append(f"{what}: {url.replace(base, '')} → HTTP {st}")
    return st, body, ctype

st, body, _ = check(base + PREFIX, "главная")
check(base + f"/{REPO}", "адрес без завершающего слеша (редирект)")
for page in sorted(SITE.rglob("*.html")):
    rel = page.relative_to(SITE).as_posix()
    if rel == "404.html": continue
    url = base + PREFIX + rel
    st, body, ctype = check(url, f"страница {rel}")
    if "text/html" not in ctype: errors.append(f"{rel}: тип {ctype}")
    refs = Refs(); refs.feed(body.decode("utf-8"))
    text = body.decode("utf-8")
    for r in refs.refs + re.findall(r'from\s+["\']([^"\']+)["\']', text):
        if re.match(r"^(https?:|mailto:|tel:|#|data:)", r): continue
        target = urllib.parse.urljoin(url, r.split("#")[0])
        if target.split("?")[0].startswith(base + PREFIX) is False: errors.append(f"{rel}: ссылка «{r}» уводит за пределы /{REPO}/ → {target.replace(base, '')}"); continue
        st2, b2, c2 = check(target, f"ресурс со страницы {rel}")
        # CSS: вложенные url(...) и @font-face
        if target.endswith(".css") and st2 == 200:
            for m in re.finditer(r"url\(\s*[\"']?([^)\"']+)", b2.decode("utf-8")):
                v = m.group(1)
                if not v.startswith(("data:", "http")): check(urllib.parse.urljoin(target, v), f"url() из {target.replace(base + PREFIX, '')}")
        # JS-модули: импорты рекурсивно (весь граф модулей страницы)
        if target.endswith(".js") and st2 == 200:
            todo, seen_js = [(target, b2)], {target}
            while todo:
                cur, src = todo.pop()
                for m in re.finditer(r'from\s+["\']([^"\']+)["\']', src.decode("utf-8")):
                    nxt = urllib.parse.urljoin(cur, m.group(1))
                    if nxt in seen_js: continue
                    seen_js.add(nxt); s3, b3, _ = check(nxt, f"import из {cur.replace(base + PREFIX, '')}")
                    if s3 == 200: todo.append((nxt, b3))

# данные: каждый JSON отдаётся и разбирается; их читает страница через data-root
for f in sorted((SITE / "data").glob("*.json")):
    st, b, c = check(base + PREFIX + "data/" + f.name, f"данные {f.name}")
    try: json.loads(b)
    except Exception as e: errors.append(f"data/{f.name}: не JSON — {e}")  # noqa: BLE001
# шрифты и значок отдаются с верным типом
for f in sorted((SITE / "assets" / "fonts").glob("*.woff2")):
    st, b, c = check(base + PREFIX + "assets/fonts/" + f.name, f"шрифт {f.name}")
    if st == 200 and b[:4] != b"wOF2": errors.append(f"{f.name}: не WOFF2")
# корневой адрес (без префикса) на Pages проектного сайта — 404; корневые абсолютные пути не должны работать
for p in ("/", "/assets/css/base.css", "/data/menu.json", "/index.html"):
    st, _, _ = get(base + p); checked += 1
    if st != 404: errors.append(f"{p} без префикса /{REPO}/ отдаётся с кодом {st} (на Pages будет 404): сайт не должен на это опираться")
# несуществующая страница: код 404 и содержимое 404.html
st, b, _ = get(base + PREFIX + "no/such/page.html"); checked += 1
if st != 404 or "Страница не найдена" not in b.decode("utf-8"): errors.append(f"несуществующий адрес: HTTP {st}, содержимое 404.html не отдано")
srv.shutdown()
print(f"Раздача под префиксом {PREFIX}: проверено запросов — {checked}")
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "сайт корректно работает в подкаталоге" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
