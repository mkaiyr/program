#!/usr/bin/env python3
"""Гигиена репозитория перед публикацией: нет секретов и ключей, локальных путей пользователя, личных адресов почты, внешних адресов в сайте
(кроме согласованных), служебных папок (.git, __pycache__, .DS_Store) и файлов, которые не должны попасть на Pages.
Запуск из корня репозитория:  python3 tools/check_hygiene.py [корень]"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
SITE = ROOT / "docs"
TEXT = {".html", ".js", ".css", ".json", ".svg", ".txt", ".md", ".py", ".swift", ".gitignore", ".xml", ".toml", ".yml", ".yaml", ".ini", ""}
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
errors = []
def err(m): errors.append(m)

SECRETS = [("JWT-токен", r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}"), ("ключ sk-/pk-", r"\b(sk|pk)[-_](live|test|proj)?[-_]?[A-Za-z0-9]{20,}"), ("service_role", r"service_role"),
           ("адрес Supabase", r"supabase\.(co|com|io)"), ("закрытый ключ", r"-----BEGIN [A-Z ]*PRIVATE KEY"), ("AWS-ключ", r"AKIA[0-9A-Z]{16}"),
           ("присвоение секрета", r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']"), ("токен GitHub", r"gh[pousr]_[A-Za-z0-9]{30,}")]
LOCAL = [("путь пользователя macOS", r"/Users/[A-Za-z0-9._-]+"), ("путь пользователя Linux", r"/home/[A-Za-z0-9._-]+/"), ("путь пользователя Windows", r"[A-Z]:\\Users\\")]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
OK_MAIL_DOMAINS = {"example.com", "example.org", "example.kz", "example.invalid"}
ALLOWED_URLS = ("https://tile.openstreetmap.org/", "https://www.openstreetmap.org/copyright", "http://www.w3.org/2000/svg", "https://github.com/mkaiyr/", "https://mkaiyr.github.io/", "http://127.0.0.1", "http://localhost")

files = [p for p in ROOT.rglob("*") if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)]
for p in files:
    rel = p.relative_to(ROOT).as_posix()
    if p.name == ".DS_Store": err(f"{rel}: служебный файл macOS"); continue
    if p.suffix.lower() in {".webp", ".woff2", ".png", ".jpg", ".jpeg", ".ico"} or p.suffix.lower() not in TEXT and p.name not in {".nojekyll", ".gitignore"}: continue
    try: text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError: continue
    if rel == "tools/check_hygiene.py": continue
    for label, rx in SECRETS:
        if re.search(rx, text): err(f"{rel}: похоже на секрет — {label}")
    for label, rx in LOCAL:
        m = re.search(rx, text)
        if m: err(f"{rel}: {label} «{m.group(0)}»")
    for m in EMAIL.finditer(text):
        if m.group(1).lower() not in OK_MAIL_DOMAINS and not m.group(0).startswith(("noreply@",)): err(f"{rel}: адрес почты «{m.group(0)}» (допустимы только example.*)")
    if rel.startswith("docs/") and not rel.startswith(("docs/assets/fonts/", "docs/assets/vendor/")):   # тексты лицензий шрифтов содержат адреса, но это не ссылки страниц
        for m in re.finditer(r"https?://[^\s\"'<>)`]+", text):
            if not m.group(0).startswith(ALLOWED_URLS): err(f"{rel}: внешний адрес «{m.group(0)[:70]}» на публичной части")
# публичная часть: только ожидаемое
for p in SITE.rglob("*") if SITE.exists() else []:
    if p.is_file() and p.suffix.lower() in {".py", ".swift", ".sh", ".env", ".pem", ".key", ".sql", ".log", ".bak", ".map", ".ini", ".zip"}: err(f"docs/{p.relative_to(SITE).as_posix()}: служебный файл не должен публиковаться")
    if p.is_dir() and p.name in {"registry", "tools", "tests", "node_modules", ".git"}: err(f"docs/{p.relative_to(SITE).as_posix()}: служебная папка внутри docs/")
if not (SITE / ".nojekyll").exists(): err("docs/.nojekyll отсутствует")
print(f"Проверено файлов: {len(files)}")
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "гигиена репозитория в порядке" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
