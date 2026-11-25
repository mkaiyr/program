#!/usr/bin/env python3
"""Проверка синтаксиса JavaScript (файлы docs/**/*.js и встроенные <script type="module"> в HTML) встроенным движком macOS
(JavaScriptCore через osascript). Node на машине разработчика не нужен. Только macOS; без osascript проверка пропускается (код 0 с предупреждением).
Исполняется только разбор текста, код страницы не запускается.  Запуск:  python3 tools/check_js_syntax.py  [корень]"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
SITE = ROOT / "docs"
JXA = r"""
ObjC.import('Foundation');
function run(argv) {
  const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
  const out = [];
  for (const path of argv) {
    let src = $.NSString.stringWithContentsOfFileEncodingError($(path), $.NSUTF8StringEncoding, null).js;
    src = src.replace(/\bimport\s+(?:[\w*{}\s,]+?\s+from\s+)?["'][^"']+["']\s*;?/g, '').replace(/^\s*export\s*\{[^}]*\}\s*(?:from\s*["'][^"']+["'])?\s*;?/gm, '').replace(/^\s*export\s+(async\s+function|function|const|let|class)/gm, '$1');
    try { new AsyncFunction(src); out.push('OK\t' + path); } catch (e) { out.push('ERR\t' + path + '\t' + e.message); }
  }
  return out.join('\n');
}
"""
if not shutil.which("osascript"):
    print("ПРЕДУПРЕЖДЕНИЕ: osascript недоступен (не macOS) — проверка синтаксиса JS пропущена"); sys.exit(0)
tmp = Path(tempfile.mkdtemp())
targets, labels = [], {}
for js in sorted(SITE.rglob("*.js")):
    targets.append(js); labels[str(js)] = str(js.relative_to(SITE))
for page in sorted(SITE.rglob("*.html")):
    for i, m in enumerate(re.finditer(r'<script\b[^>]*type="module"[^>]*>(.*?)</script>', page.read_text(encoding="utf-8"), re.S)):
        f = tmp / f"{page.stem}-{i}.js"; f.write_text(m.group(1), encoding="utf-8"); targets.append(f); labels[str(f)] = f"{page.relative_to(SITE)} (встроенный модуль {i + 1})"
script = tmp / "probe.js"; script.write_text(JXA, encoding="utf-8")
r = subprocess.run(["osascript", "-l", "JavaScript", str(script), *map(str, targets)], capture_output=True, text=True, timeout=60)
bad = 0
for line in r.stdout.splitlines():
    parts = line.split("\t")
    if parts[0] == "ERR": bad += 1; print("ОШИБКА:", labels.get(parts[1], parts[1]), "—", parts[2])
print(f"Проверено скриптов: {len(targets)}")
if r.returncode != 0: bad += 1; print("ОШИБКА: osascript:", r.stderr.strip()[:200])
print("ИТОГ:", "синтаксис корректен" if not bad else f"ошибок: {bad}")
sys.exit(1 if bad else 0)
