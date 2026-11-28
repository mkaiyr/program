#!/usr/bin/env python3
"""Проверка образца public_menu() НАСТОЯЩИМ валидатором сайта (validateCatalog/catalogFromRaw/dishAllergens из website/docs/assets/js, файлы только читаются).
Запуск: python3 validate_sample.py [путь_к_образцу.json]   (macOS: JavaScriptCore через osascript)."""
import json, re, subprocess, sys, tempfile
from pathlib import Path

SITE_JS = Path("<HOME>/cashier_app/website/docs/assets/js")
sample_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "public_menu_sample.json"
sample = json.loads(sample_path.read_text(encoding="utf-8"))


def strip_module(src):
    src = re.sub(r"\bimport\s+(?:[\w*{}\s,]+?\s+from\s+)?[\"'][^\"']+[\"']\s*;?", "", src)
    src = re.sub(r"^\s*export\s*\{[^}]*\}\s*(?:from\s*[\"'][^\"']+[\"'])?\s*;?", "", src, flags=re.M)
    return re.sub(r"^\s*export\s+(async\s+function|function|const|let|class)", r"\1", src, flags=re.M)


# независимый расчёт аллергенов блюд на Python по словарю из самого ответа
expected = {}
for d in sample["dishes"]:
    contains, may = set(), set()
    for x in d["ingredients"]:
        for a, st in sample["ingredients"][x]["allergens"].items():
            (contains if st == "contains" else may).add(a)
    expected[d["id"]] = {"contains": sorted(contains), "may": sorted(may - contains)}

body = f"""
{strip_module((SITE_JS / "catalog-source.js").read_text(encoding="utf-8"))}
{strip_module((SITE_JS / "allergens.js").read_text(encoding="utf-8"))}
function run() {{
  const sample = {json.dumps(sample)}; const expected = {json.dumps(expected)}; const out = {{}};
  out.validate = validateCatalog(sample);
  try {{ const c = catalogFromRaw(sample, "static"); out.catalog = {{ ok: true, dishes: c.dishes.length, categories: c.categories.length }}; }} catch (e) {{ out.catalog = {{ ok: false, error: String(e.message) }}; }}
  out.allergens = sample.dishes.map((d) => {{ const a = dishAllergens(d, sample.ingredients); return {{ id: d.id, contains: a.contains.sort(), may: a.may.sort(), ok: JSON.stringify(a.contains.sort()) === JSON.stringify(expected[d.id].contains) && JSON.stringify(a.may.sort()) === JSON.stringify(expected[d.id].may) }}; }});
  out.names = allergenNames(["milk", "gluten"], sample.allergens);
  // негатив: валидатор обязан отвергать испорченный каталог (проверка самого валидатора)
  const broken = JSON.parse(JSON.stringify(sample)); broken.dishes[0].id = "Плохой Слаг"; out.rejects_bad_slug = !validateCatalog(broken).ok;
  const broken2 = JSON.parse(JSON.stringify(sample)); broken2.dishes[0].price = 12.5; out.rejects_fractional_price = !validateCatalog(broken2).ok;
  return JSON.stringify(out);
}}
"""
tmp = Path(tempfile.mkdtemp()) / "v.js"
tmp.write_text(body, encoding="utf-8")
r = subprocess.run(["osascript", "-l", "JavaScript", str(tmp)], capture_output=True, text=True, timeout=120)
try:
    res = json.loads((r.stdout or r.stderr).strip())
except Exception:
    print("ОШИБКА запуска:", (r.stderr or r.stdout)[:600]); sys.exit(1)
ok = res["validate"]["ok"] and res["catalog"]["ok"] and all(a["ok"] for a in res["allergens"]) and res["rejects_bad_slug"] and res["rejects_fractional_price"] and len(res["names"]) == 2
print("validateCatalog:", res["validate"])
print("catalogFromRaw:", res["catalog"])
print("аллергены по словарю из ответа (JS = независимый Python):", [(a["id"], a["contains"], a["may"], a["ok"]) for a in res["allergens"]])
print("названия аллергенов:", res["names"])
print("валидатор отвергает плохой слаг и дробную цену:", res["rejects_bad_slug"], res["rejects_fractional_price"])
print("ИТОГ:", "образец public_menu() принят валидатором сайта" if ok else "НЕ ПРОЙДЕНО")
sys.exit(0 if ok else 1)
