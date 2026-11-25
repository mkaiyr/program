"""Раздаёт docs/ так, как GitHub Pages раздаёт проектный сайт: только под префиксом /<репозиторий>/, всё остальное — 404.html с кодом 404.
Используется tools/browser_tests.py. start() возвращает (сервер, базовый адрес)."""
import http.server
import threading
import urllib.parse
from pathlib import Path

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml",
        ".woff2": "font/woff2", ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".txt": "text/plain; charset=utf-8"}

def start(root: Path, repo: str = "kafe-bistro"):
    site, prefix = (root / "docs").resolve(), f"/{repo}/"   # resolve: временные папки macOS лежат за ссылкой /var → /private/var
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path.startswith(prefix):
                f = (site / urllib.parse.unquote(path[len(prefix):])).resolve()
                if f.is_dir(): f = f / "index.html"
                if str(f).startswith(str(site)) and f.is_file():
                    body = f.read_bytes(); self.send_response(200); self.send_header("Content-Type", MIME.get(f.suffix, "application/octet-stream"))
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            body = (site / "404.html").read_bytes(); self.send_response(404); self.send_header("Content-Type", MIME[".html"])
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}{prefix}"
