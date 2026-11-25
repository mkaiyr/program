#!/usr/bin/env python3
"""Запускает Python-тесты инструментов (tests/py). Запуск из корня репозитория:  python3 tools/run_py_tests.py"""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests" / "py")], capture_output=True, text=True, cwd=root)
tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
print("\n".join(tail))
print("ИТОГ:", "Python-тесты инструментов пройдены" if r.returncode == 0 else "есть ошибки")
sys.exit(r.returncode)
