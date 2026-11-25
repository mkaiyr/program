#!/usr/bin/env python3
"""Запускает все проверки подряд и печатает сводку. Запуск из корня репозитория:  python3 tools/check_all.py"""
import subprocess
import sys
from pathlib import Path

T = Path(__file__).resolve().parent
CHECKS = [("данные", "validate_data.py"), ("фотографии и реестр лицензий", "check_photos.py"), ("пути и ресурсы", "check_paths.py"), ("контраст цветов", "check_contrast.py"),
          ("структура HTML", "check_html.py"), ("гигиена репозитория (секреты, пути, внешние адреса)", "check_hygiene.py"), ("синтаксис JavaScript", "check_js_syntax.py"), ("логика JavaScript (автотесты)", "run_js_tests.py"), ("раздача в подкаталоге (как GitHub Pages)", "check_subpath.py"), ("браузерные тесты (WebKit: сценарии на 3 размерах экрана)", "browser_tests.py"), ("браузерные тесты оформления заказа (WebKit)", "browser_checkout_tests.py"), ("браузерные тесты демо-админки (WebKit)", "browser_admin_tests.py"), ("сверка меню: самопроверка инструмента", "run_py_tests.py"), ("итоговый аудит страниц (WebKit)", "browser_audit_tests.py"), ("браузерные тесты демо-карты (WebKit, реальные тайлы)", "browser_map_tests.py")]
failed = []
for title, script in CHECKS:
    r = subprocess.run([sys.executable, str(T / script)], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or ["(нет вывода)"])[-1]
    print(f"{'✔' if r.returncode == 0 else '✘'} {title}: {last.replace('ИТОГ: ', '')}")
    if r.returncode: failed.append(title); print(r.stdout[-1500:], r.stderr[-500:])
print("ИТОГ:", "все проверки пройдены" if not failed else f"не пройдено: {', '.join(failed)}")
sys.exit(1 if failed else 0)
