#!/usr/bin/env python3
"""Снимает скриншоты указанных страниц сайта в настоящем WebKit (macOS) на заданных ширинах экрана.
Запуск:  python3 tools/screenshot_pages.py ПАПКА ширина[,ширина…] страница [страница…]
Пример:  python3 tools/screenshot_pages.py shots 390,1280 "dish.html?id=onion-rings" "menu.html?cat=sauces"
Имя файла: <страница>_<ширина>.png (знаки ? = & / заменяются на _)."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages_server  # noqa: E402

if len(sys.argv) < 4: raise SystemExit(__doc__)
out_dir, widths, pages = Path(sys.argv[1]), [int(w) for w in sys.argv[2].split(",")], sys.argv[3:]
if not shutil.which("swiftc"): raise SystemExit("нужен swiftc (Command Line Tools)")
out_dir.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp()); binp = tmp / "wk"
subprocess.run(["swiftc", "-O", str(Path(__file__).resolve().parent / "webkit_run.swift"), "-o", str(binp)], capture_output=True)
srv, base = pages_server.start(Path(__file__).resolve().parent.parent)
for w in widths:
    phases = []
    for p in pages:
        name = re.sub(r"[^A-Za-z0-9.-]+", "_", p) + f"_{w}.png"
        wait = ".dish-detail" if p.startswith("dish.html") else "#grid .dish-card" if p.startswith("menu.html") else "main"
        phases.append({"url": base + p, "waitFor": wait, "timeout": 15, "script": "await new Promise(r => setTimeout(r, 900)); return 'ok'", "screenshot": str(out_dir / name), "full": True})
    plan = tmp / f"plan_{w}.json"; plan.write_text(json.dumps({"width": w, "height": 900, "phases": phases}), encoding="utf-8")
    res = subprocess.run([str(binp), str(plan)], capture_output=True, text=True, timeout=300)
    for ph, p in zip(json.loads(res.stdout)["phases"], pages):
        print(w, p, "OK" if ph.get("screenshot") else f"ОШИБКА {ph.get('error') or ph.get('screenshot_error')}")
srv.shutdown()
