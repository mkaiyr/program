#!/usr/bin/env python3
"""Проверка данных демонстрационного сайта: схема, ссылки между файлами, согласованность аллергенов с составом.
Только стандартная библиотека. Запуск из корня репозитория:  python3 tools/validate_data.py  [корень]
Код возврата 0 — всё в порядке, 1 — есть ошибки."""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent  # необязательный аргумент — корень репозитория (для тестов проверки)
DATA = ROOT / "docs" / "data"
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ISLUG = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
STATUSES = {"contains", "may_contain"}
UNITS = {"г", "мл", "шт."}
ALLERGEN_IDS = {"gluten", "crustaceans", "eggs", "fish", "peanuts", "soy", "milk", "nuts", "celery", "mustard", "sesame", "sulphites", "molluscs", "lupin"}
ORDER_STATUSES = ["new", "accepted", "preparing", "ready", "out_for_delivery", "completed", "cancelled"]

# Если в названии или составе ингредиента встречается слово — соответствующий аллерген должен быть указан (минимум статус).
# Это страховка от забытых аллергенов, а не замена проверки реальной рецептуры.
KEYWORD_RULES = [
    (r"сыр|сливк|молок|творог|сметан|пармезан|фета|моцарелл|чеддер|сливочн", "milk", {"contains"}),
    (r"булочк|тесто|паста|лапша|сухарик|панировк|кляр|мука|печень", "gluten", {"contains"}),
    (r"яйц|майонез", "eggs", {"contains"}),
    (r"анчоус|рыб", "fish", {"contains"}),
    (r"сельдерей", "celery", {"contains"}),
    (r"соев", "soy", {"contains"}),
    (r"горчиц", "mustard", {"contains", "may_contain"}),
]
# Формулировки, которые нельзя использовать без подтверждения: обещания безопасности и полезности.
FORBIDDEN_CLAIMS = re.compile(r"без\s+глютен|безглютен|без\s+лактоз|без\s+аллерген|веган|вегетариан|диет|полезн|гипоаллерген|органическ|\bэко\b|лучш|самый|№\s*1", re.I)

errors, warnings = [], []
def err(msg): errors.append(msg)
def warn(msg): warnings.append(msg)

def load(name):
    p = DATA / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        err(f"{name}: не читается как JSON — {e}")
        return None

menu, ing_file, settings = load("menu.json"), load("ingredients.json"), load("settings.json")
if None in (menu, ing_file, settings):
    print("\n".join("ОШИБКА: " + e for e in errors)); sys.exit(1)

for name, doc in (("menu.json", menu), ("ingredients.json", ing_file), ("settings.json", settings)):
    if doc.get("schema") != 1:
        err(f"{name}: schema должна быть 1")

# ---------- ingredients.json ----------
allergens = {a["id"]: a["name"] for a in ing_file.get("allergens", [])}
if set(allergens) != ALLERGEN_IDS:
    err(f"ingredients.json: список аллергенов должен быть ровно {sorted(ALLERGEN_IDS)}; отличия: {sorted(set(allergens) ^ ALLERGEN_IDS)}")
if any(not n.strip() for n in allergens.values()):
    err("ingredients.json: у аллергена пустое название")
ings = ing_file.get("ingredients", {})
for iid, ing in ings.items():
    if not ISLUG.match(iid): err(f"ингредиент «{iid}»: идентификатор должен быть вида snake_case латиницей")
    if not isinstance(ing.get("name"), str) or not ing["name"].strip(): err(f"ингредиент «{iid}»: нет названия")
    al = ing.get("allergens", {})
    for a, st in al.items():
        if a not in ALLERGEN_IDS: err(f"ингредиент «{iid}»: неизвестный аллерген «{a}»")
        if st not in STATUSES: err(f"ингредиент «{iid}»: недопустимый статус «{st}» у «{a}»")
    free = ing.get("confirmed_free", [])
    if not isinstance(free, list) or any(a not in ALLERGEN_IDS for a in free): err(f"ингредиент «{iid}»: confirmed_free должен быть списком известных аллергенов")
    if set(free) & set(al): err(f"ингредиент «{iid}»: аллерген одновременно «не содержит» и указан как возможный: {sorted(set(free) & set(al))}")
    text = (ing.get("name", "") + " " + ing.get("detail", "")).lower()
    for pattern, allergen, okst in KEYWORD_RULES:
        if re.search(pattern, text) and al.get(allergen) not in okst:
            err(f"ингредиент «{iid}» ({ing.get('name')}): по тексту «{text.strip()}» ожидается аллерген «{allergen}» ({'/'.join(sorted(okst))}), указано: {al.get(allergen)}")
    if FORBIDDEN_CLAIMS.search(text): err(f"ингредиент «{iid}»: недопустимая формулировка в названии или описании")

# ---------- menu.json ----------
cats = menu.get("categories", [])
cat_ids = [c["id"] for c in cats]
if len(set(cat_ids)) != len(cat_ids): err("menu.json: повторяются идентификаторы категорий")
expected_cats = ["burgers", "pizza", "salads", "hot", "sides", "starters", "drinks", "desserts", "sauces"]
if cat_ids != expected_cats: err(f"menu.json: категории и их порядок должны быть {expected_cats}, сейчас {cat_ids}")
if [c["sort"] for c in cats] != list(range(1, len(cats) + 1)): err("menu.json: sort категорий должен идти подряд с 1")
for c in cats:
    if not SLUG.match(c["id"]) or not c.get("name", "").strip(): err(f"категория «{c.get('id')}»: некорректный id или название")

dishes = menu.get("dishes", [])
if len(dishes) != 30: err(f"menu.json: на этапе 1 должно быть 30 блюд, сейчас {len(dishes)}")
ids, names = Counter(d["id"] for d in dishes), Counter(d["name"].strip().lower() for d in dishes)
for k, n in ids.items():
    if n > 1: err(f"повторяется id блюда «{k}»")
for k, n in names.items():
    if n > 1: err(f"повторяется название блюда «{k}»")
used = set()
derived = {}
for d in dishes:
    i = d["id"]
    if not SLUG.match(i): err(f"блюдо «{i}»: id должен быть kebab-case латиницей")
    if d.get("category") not in cat_ids: err(f"блюдо «{i}»: неизвестная категория «{d.get('category')}»")
    p = d.get("price")
    if not isinstance(p, int) or isinstance(p, bool) or not 100 <= p <= 100000 or p % 10: err(f"блюдо «{i}»: цена должна быть целым числом тенге от 100 до 100000, кратным 10 (сейчас {p!r})")
    desc = d.get("description", "")
    if not 20 <= len(desc) <= 200: err(f"блюдо «{i}»: описание — от 20 до 200 символов (сейчас {len(desc)})")
    if FORBIDDEN_CLAIMS.search(desc + " " + d.get("name", "")): err(f"блюдо «{i}»: недопустимая формулировка (обещание безопасности, диета, «лучший» и т. п.)")
    if re.search(r"https?://|www\.", desc): err(f"блюдо «{i}»: в описании ссылка")
    w = d.get("weight", {})
    if not isinstance(w.get("value"), int) or w["value"] <= 0 or w.get("unit") not in UNITS: err(f"блюдо «{i}»: некорректные вес или объём {w}")
    li = d.get("ingredients", [])
    if not li: err(f"блюдо «{i}»: пустой состав")
    if len(set(li)) != len(li): err(f"блюдо «{i}»: ингредиент указан дважды")
    for x in li:
        if x not in ings: err(f"блюдо «{i}»: неизвестный ингредиент «{x}»")
        used.add(x)
    for flag in ("recommended", "available"):
        if not isinstance(d.get(flag), bool): err(f"блюдо «{i}»: {flag} должен быть true/false")
    ph = d.get("photo")
    if ph is not None:
        if not isinstance(ph, dict) or not {"file", "thumb"} <= set(ph) or not set(ph) <= {"file", "thumb", "alt", "width", "height"}: err(f"блюдо «{i}»: photo — null или объект с file, thumb и необязательными alt, width, height")
        else:
            for k in ("file", "thumb"):
                if re.search(r"^(https?:)?//", str(ph[k])): err(f"блюдо «{i}»: photo.{k} — внешняя ссылка; на публичном сайте допускаются только свои файлы")
                elif not (ROOT / "docs" / "assets" / "img" / "dishes" / ph[k]).is_file(): err(f"блюдо «{i}»: файл фото не найден: {ph[k]}")
    # итоговые аллергены блюда: сильнейший статус среди ингредиентов
    agg = {}
    for x in li:
        for a, st in ings.get(x, {}).get("allergens", {}).items():
            if st == "contains" or agg.get(a) is None: agg[a] = st if agg.get(a) != "contains" else "contains"
    derived[i] = agg
if [d["sort"] for d in dishes] != list(range(1, len(dishes) + 1)): err("menu.json: sort блюд должен идти подряд с 1")
for c in cat_ids:
    n = sum(1 for d in dishes if d["category"] == c)
    if n < 2: err(f"категория «{c}»: меньше двух блюд ({n})")
for x in set(ings) - used:
    err(f"ингредиент «{x}» не используется ни в одном блюде")

# Согласованность на уровне блюда: если ингредиент содержит аллерген, блюдо обязано его показывать
for d in dishes:
    for x in d["ingredients"]:
        for a, st in ings.get(x, {}).get("allergens", {}).items():
            got = derived[d["id"]].get(a)
            if got is None or (st == "contains" and got != "contains"):
                err(f"блюдо «{d['id']}»: аллерген «{a}» ингредиента «{x}» не попал в итог блюда")
# контроль недоступных блюд: хотя бы одно (для демонстрации) и не больше трёх
unavailable = [d["id"] for d in dishes if not d["available"]]
if not 1 <= len(unavailable) <= 3: warn(f"недоступных блюд {len(unavailable)}: ожидается 1–3 для демонстрации")

# ---------- settings.json ----------
dl, pr = settings.get("delivery", {}), settings.get("pricing", {})
if (dl.get("fee"), dl.get("freeFrom"), dl.get("minOrder")) != (500, 8000, 2000): err(f"settings.json: доставка должна быть 500 / бесплатно от 8000 / минимум 2000, сейчас {dl.get('fee')} / {dl.get('freeFrom')} / {dl.get('minOrder')}")
if pr.get("serviceChargePercent") != 0: err("settings.json: надбавка за обслуживание на сайте должна быть 0")
if [s["id"] for s in settings.get("orderStatuses", [])] != ORDER_STATUSES: err(f"settings.json: статусы заказа должны быть {ORDER_STATUSES}")
if [p["id"] for p in settings.get("payments", [])] != ["online", "cash", "card_on_delivery"]: err("settings.json: способы оплаты — online, cash, card_on_delivery")
if not next((p for p in settings["payments"] if p["id"] == "online"), {}).get("demo"): err("settings.json: online-оплата должна быть помечена demo")
if not settings.get("cafe", {}).get("isDemoContacts"): err("settings.json: контакты должны быть помечены isDemoContacts")
if "демо" not in (settings.get("cafe", {}).get("address", "") + settings.get("cafe", {}).get("phone", "")).lower(): err("settings.json: адрес и телефон должны содержать пометку «демо»")
if settings.get("notices", {}).get("demoBanner") != "Демонстрационная версия. Реальные заказы не принимаются": err("settings.json: demoBanner должен быть ровно «Демонстрационная версия. Реальные заказы не принимаются»")
if settings.get("notices", {}).get("adminWarning") != "Демонстрационная админка. Данные хранятся локально в браузере. Настоящей защиты доступа нет": err("settings.json: adminWarning должен быть ровно «Демонстрационная админка. Данные хранятся локально в браузере. Настоящей защиты доступа нет»")
opts = settings.get("pickup", {}).get("timeOptions", [])
if not opts or opts[0].get("id") != "asap" or len({o.get("id") for o in opts}) != len(opts) or any(not o.get("name") for o in opts): err("settings.json: pickup.timeOptions — непустой список с уникальными id, первый — «asap»")
for n in ("demoBanner", "demoPayment", "demoOrder", "demoStatus", "adminWarning", "ingredientsDisclaimer"):
    if not settings.get("notices", {}).get(n): err(f"settings.json: нет текста notices.{n}")

# ---------- публичные данные не содержат ссылок на источники ----------
for name in ("menu.json", "ingredients.json", "settings.json"):
    txt = (DATA / name).read_text(encoding="utf-8")
    for m in re.finditer(r"https?://\S+|www\.\S+", txt):
        err(f"{name}: ссылка в публичных данных: {m.group(0)[:50]}")

# ---------- итог ----------
per_cat = Counter(d["category"] for d in dishes)
print(f"Блюд: {len(dishes)}; категорий: {len(cats)}; ингредиентов: {len(ings)} (используются все)")
print("По категориям:", ", ".join(f"{c['name']} — {per_cat[c['id']]}" for c in cats))
print(f"Рекомендуем: {sum(d['recommended'] for d in dishes)}; недоступно: {unavailable}")
print("Блюд с аллергенами «содержит»:", sum(1 for a in derived.values() if "contains" in a.values()), "из", len(dishes))
# ---------- демо-маршруты (карта и виртуальный курьер) ----------
import math
def _hav(a, b):
    r = 6371008.8; p1, p2 = math.radians(a[0]), math.radians(b[0]); dl = math.radians(b[1] - a[1]); dp = p2 - p1
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(h)))
ALMATY = (43.1, 43.4, 76.7, 77.1)   # широта от/до, долгота от/до
inb = lambda p: ALMATY[0] < p[0] < ALMATY[1] and ALMATY[2] < p[1] < ALMATY[3]
try: routes = json.loads((DATA / "demo-routes.json").read_text(encoding="utf-8"))
except Exception as e: routes = None; err(f"demo-routes.json: не читается ({e})")
if routes:
    mp = routes.get("map", {})
    if not all(k in mp for k in ("center", "zoom", "tileUrl", "attribution", "attributionUrl")): err("demo-routes.json: в map нет center/zoom/tileUrl/attribution/attributionUrl")
    if not str(mp.get("tileUrl", "")).startswith("https://") or not all(x in str(mp.get("tileUrl")) for x in ("{z}", "{x}", "{y}")): err("demo-routes.json: tileUrl — https-адрес с {z}/{x}/{y}")
    cafe = routes.get("cafe", {})
    if not inb((cafe.get("lat", 0), cafe.get("lon", 0))): err("demo-routes.json: кафе вне Алматы")
    ds = routes.get("destinations", [])
    if len(ds) < 4: err("demo-routes.json: нужно не меньше 4 тестовых точек")
    if len({d.get("id") for d in ds}) != len(ds): err("demo-routes.json: id точек повторяются")
    for d in ds:
        i, rt = d.get("id"), d.get("route", [])
        if len(rt) < 20 or not all(isinstance(p, list) and len(p) == 2 and inb(p) for p in rt): err(f"demo-routes.json «{i}»: мало точек или точка вне Алматы"); continue
        if _hav(rt[0], (cafe["lat"], cafe["lon"])) > 5: err(f"demo-routes.json «{i}»: маршрут начинается не у кафе")
        if _hav(rt[-1], (d["lat"], d["lon"])) > 5: err(f"demo-routes.json «{i}»: маршрут заканчивается не в точке назначения")
        ln = sum(_hav(rt[k - 1], rt[k]) for k in range(1, len(rt)))
        if abs(ln - d.get("distanceM", 0)) / max(1, d.get("distanceM", 1)) > 0.05: err(f"demo-routes.json «{i}»: длина ломаной {round(ln)} м, заявлено {d.get('distanceM')} м")
        if not d.get("durationS", 0) > 0 or not str(d.get("note", "")).startswith("Тестовая точка"): err(f"demo-routes.json «{i}»: нет времени или пометки «Тестовая точка»")
        if ln < _hav(rt[0], rt[-1]) * 0.999: err(f"demo-routes.json «{i}»: маршрут короче прямой — данные испорчены")
    print(f"Демо-маршрутов: {len(ds)}; точек геометрии: {sum(len(d.get('route', [])) for d in ds)}")
for w in warnings: print("ПРЕДУПРЕЖДЕНИЕ:", w)
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "данные корректны" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
