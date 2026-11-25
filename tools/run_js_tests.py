#!/usr/bin/env python3
"""Запускает автотесты чистой JavaScript-логики (tests/js/*.test.js) встроенным движком macOS (JavaScriptCore через osascript).
Модули из docs/assets/js склеиваются в одну область видимости (import/export вырезаются), поэтому в тестируемых модулях не должно быть DOM.
Ожидаемые аллергены блюд считает независимо Python — JavaScript обязан дать то же. Только macOS; без osascript проверка пропускается.
Запуск из корня репозитория:  python3 tools/run_js_tests.py  [корень]"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
SITE, TESTS = ROOT / "docs", ROOT / "tests" / "js"
MODULES = ["format", "allergens", "search", "pricing", "cart-logic", "html", "validation", "orders-logic", "admin-logic", "courier-demo", "catalog-source"]   # порядок важен: модуль использует имена предыдущих
if not shutil.which("osascript"):
    print("ПРЕДУПРЕЖДЕНИЕ: osascript недоступен (не macOS) — JavaScript-тесты пропущены"); sys.exit(0)

def strip_module(src: str) -> str:
    src = re.sub(r"\bimport\s+(?:[\w*{}\s,]+?\s+from\s+)?[\"'][^\"']+[\"']\s*;?", "", src)
    src = re.sub(r"^\s*export\s*\{[^}]*\}\s*(?:from\s*[\"'][^\"']+[\"'])?\s*;?", "", src, flags=re.M)
    return re.sub(r"^\s*export\s+(async\s+function|function|const|let|class)", r"\1", src, flags=re.M)

menu = json.loads((SITE / "data" / "menu.json").read_text(encoding="utf-8"))
ing = json.loads((SITE / "data" / "ingredients.json").read_text(encoding="utf-8"))
settings = json.loads((SITE / "data" / "settings.json").read_text(encoding="utf-8"))
routes = json.loads((SITE / "data" / "demo-routes.json").read_text(encoding="utf-8"))
expected = {}
for d in menu["dishes"]:   # независимый расчёт аллергенов блюда на Python
    contains, may = set(), set()
    for x in d["ingredients"]:
        for a, st in ing["ingredients"][x]["allergens"].items():
            (contains if st == "contains" else may).add(a)
    expected[d["id"]] = {"contains": sorted(contains), "may": sorted(may - contains)}

harness = """
const __results = []; let __cur = "";
function test(name, fn) { try { fn(); __results.push({ name: __cur + name, ok: true }); } catch (e) { __results.push({ name: __cur + name, ok: false, error: String(e && e.message || e) }); } }
function suite(n) { __cur = n + ": "; }
const assert = {
  ok(v, m) { if (!v) throw new Error(m || "ожидалось истинное значение"); },
  equal(a, b, m) { if (a !== b) throw new Error((m ? m + ": " : "") + "ожидалось " + JSON.stringify(b) + ", получено " + JSON.stringify(a)); },
  deepEqual(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((m ? m + ": " : "") + "ожидалось " + JSON.stringify(b) + ", получено " + JSON.stringify(a)); },
  includes(h, n, m) { if (!String(h).includes(n)) throw new Error((m ? m + ": " : "") + "нет «" + n + "» в «" + String(h).slice(0, 160) + "»"); },
  throws(fn, re) { let err = null; try { fn(); } catch (e) { err = e; } if (!err) throw new Error("ожидалось исключение"); if (re && !re.test(String(err.message))) throw new Error("исключение «" + err.message + "» не подходит под " + re); },
  excludes(h, n, m) { if (String(h).includes(n)) throw new Error((m ? m + ": " : "") + "найдено лишнее «" + n + "»"); },
};
const DATA = { menu: __MENU__, ingredients: __ING__.ingredients, allergenList: __ING__.allergens, settings: __SETTINGS__, routes: __ROUTES__ };
DATA.dishesById = Object.fromEntries(DATA.menu.dishes.map((d) => [d.id, d]));
DATA.categoriesById = Object.fromEntries(DATA.menu.categories.map((c) => [c.id, c]));
const EXPECTED_ALLERGENS = __EXPECTED__;
"""
harness = harness.replace("__MENU__", json.dumps(menu)).replace("__ING__", json.dumps(ing)).replace("__SETTINGS__", json.dumps(settings)).replace("__ROUTES__", json.dumps(routes)).replace("__EXPECTED__", json.dumps(expected))
parts = [harness]
for m in MODULES: parts.append(f"// ---- {m}.js\n" + strip_module((SITE / "assets" / "js" / f"{m}.js").read_text(encoding="utf-8")))
for t in sorted(TESTS.glob("*.test.js")): parts.append(f"// ---- {t.name}\n{{\n" + t.read_text(encoding="utf-8") + "\n}")   # свой блок на файл: константы тестов не конфликтуют
body = "\n".join(parts) + "\nreturn JSON.stringify(__results);"
script = "function run() {\n" + body + "\n}\n"
tmp = Path(tempfile.mkdtemp()); f = tmp / "tests.js"; f.write_text(script, encoding="utf-8")
r = subprocess.run(["osascript", "-l", "JavaScript", str(f)], capture_output=True, text=True, timeout=120)
out = (r.stdout or r.stderr).strip()
try: results = json.loads(out)
except Exception:
    print("ОШИБКА: тесты не запустились:", (r.stderr or out)[:600]); sys.exit(1)
bad = [x for x in results if not x["ok"]]
for x in bad: print("ОШИБКА:", x["name"], "—", x["error"])
print(f"Тестов: {len(results)}; пройдено: {len(results) - len(bad)}")
print("ИТОГ:", "JavaScript-тесты пройдены" if not bad else f"не пройдено: {len(bad)}")
sys.exit(1 if bad else 0)
