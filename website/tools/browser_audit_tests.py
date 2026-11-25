#!/usr/bin/env python3
"""Итоговый аудит страниц в настоящем WebKit на 390, 768 и 1280 px: каждая страница сайта и админки открывается, на ней нет ошибок JavaScript,
нет горизонтальной прокрутки, все картинки загружаются, все ссылки (в том числе созданные скриптом) ведут на существующие страницы внутри
подкаталога сайта, внешних ссылок и ресурсов нет; плюс переходы по основным путям посетителя. Делает итоговые скриншоты.
Запуск:  python3 tools/browser_audit_tests.py [--shots ПАПКА] [--quick] [--root ДРУГАЯ_КОПИЯ]    Все данные в тестах выдуманные."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[sys.argv.index("--root") + 1]).resolve() if "--root" in sys.argv else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages_server  # noqa: E402

args = sys.argv[1:]
SHOTS = Path(args[args.index("--shots") + 1]) if "--shots" in args else None
QUICK = "--quick" in args
if sys.platform != "darwin" or not shutil.which("swiftc"):
    print("ПРЕДУПРЕЖДЕНИЕ: нужны macOS и swiftc — аудит страниц пропущен"); sys.exit(0)
TMP = Path(tempfile.mkdtemp()); BIN = TMP / "webkit_run"
subprocess.run(["swiftc", "-O", str(Path(__file__).resolve().parent / "webkit_run.swift"), "-o", str(BIN)], capture_output=True, text=True)
if not BIN.exists(): print("ОШИБКА: не удалось собрать раннер WebKit"); sys.exit(1)
if SHOTS: SHOTS.mkdir(parents=True, exist_ok=True)
LIVE = args[args.index("--url") + 1].rstrip("/") + "/" if "--url" in args else None   # --url АДРЕС: проверить уже опубликованный сайт вместо локального сервера
srv, BASE = (None, LIVE) if LIVE else pages_server.start(ROOT)
PREFIX = BASE[BASE.index("/", 8):]          # /kafe-bistro/
errors, checks = [], 0
def expect(cond, msg):
    global checks
    checks += 1
    if not cond: errors.append(msg)

SETUP = r"""
const q = (s, r = document) => r.querySelector(s), qa = (s, r = document) => [...r.querySelectorAll(s)], wait = (ms) => new Promise(r => setTimeout(r, ms));
const txt = (el) => el ? el.textContent.replace(/\s+/g, ' ').trim() : null;
const PREFIX = '__PREFIX__';
const audit = async () => {
  qa('img').forEach(i => { i.loading = 'eager'; });
  await Promise.race([Promise.all(qa('img').map(i => i.complete ? null : new Promise(r => { i.addEventListener('load', r); i.addEventListener('error', r); }))), wait(5000)]);
  const broken = qa('img').filter(i => i.complete && i.naturalWidth === 0).map(i => i.getAttribute('src'));
  const hrefs = [...new Set(qa('a[href]').map(a => a.href))];
  const external = hrefs.filter(h => !h.startsWith(location.origin + PREFIX) && !['https://www.openstreetmap.org/copyright', 'https://leafletjs.com/'].includes(h));   // согласованные внешние ссылки — авторство карты и библиотеки Leaflet
  const bad = [];
  for (const h of hrefs.filter(h => h.startsWith(location.origin + PREFIX))) { try { const r = await fetch(h); if (!r.ok) bad.push(h.replace(location.origin, '') + ' → ' + r.status); } catch (e) { bad.push(h + ' → сбой'); } }
  const res = qa('script[src],link[href],img[src],source[src]').map(e => e.src || e.href).filter(u => u && !u.startsWith('data:') && !u.startsWith(location.origin + PREFIX) && !u.startsWith('https://tile.openstreetmap.org/'));   // и тайлы карты
  return { overflow: document.documentElement.scrollWidth > window.innerWidth + 1, broken, external, bad, extRes: res, links: hrefs.length, imgs: qa('img').length, h1: qa('h1').length, banner: txt(q('#demo-banner')), title: document.title };
};
const SEED = () => {
  localStorage.setItem('bistro.cart.v1', JSON.stringify({ items: [{ id: 'cheeseburger', qty: 2 }, { id: 'fries', qty: 1 }], comment: '' }));
  localStorage.setItem('bistro.admin.session.v1', JSON.stringify({ demoAdmin: true, at: '2026-11-18T00:00:00Z' }));
  const o = (n, at, x) => ({ version: 1, attemptId: null, number: n, createdAt: at, updatedAt: at, demo: true, status: 'new', mode: 'delivery', customer: { name: 'Тестовый Клиент', phone: '7012345678', email: 'client@example.invalid' },
    delivery: { street: 'Тестовая 1', apt: '5', entrance: '', floor: '', intercom: '', courierComment: '' }, pickupTime: null, comment: 'без лука', allergy: { answer: 'no', allergens: [], other: '' }, allergyConflicts: [],
    items: [{ id: 'cheeseburger', name: 'Чизбургер', price: 2290, qty: 2, lineTotal: 4580 }], totals: { subtotal: 4580, fee: 500, service: 0, total: 5080 }, payment: { method: 'online', methodName: 'Онлайн-оплата', status: 'awaiting', attempts: 0 }, ...x });
  localStorage.setItem('bistro.orders.v1', JSON.stringify([o('BS-261118-7001', '2026-11-18T10:00:00.000Z', {}), o('BS-261118-7002', '2026-11-18T11:00:00.000Z', { status: 'accepted', payment: { method: 'cash', methodName: 'Наличными при получении', status: 'awaiting', attempts: 0 } })]));
};
""".replace("__PREFIX__", PREFIX)

# (имя, адрес, ожидаемый элемент, число h1: 1 — страницы, где h1 создаётся скриптом, тоже 1 после загрузки)
PAGES = [
    ("home", "index.html", ".cat-tile"), ("menu", "menu.html", "#grid .dish-card"), ("search", "menu.html?q=курица", "#grid .dish-card"), ("dish", "dish.html?id=cheeseburger", ".dish-detail"),
    ("dish_unavailable", "dish.html?id=onion-rings", ".dish-detail"), ("cart", "cart.html", ".cart-line"), ("checkout", "checkout.html", "#checkout-form"), ("payment", "payment.html?n=BS-261118-7001", "[data-pay-total]"),
    ("order", "order.html?n=BS-261118-7001", "[data-order-number]"), ("orders", "orders.html", ".order-row"), ("admin_login", "admin/login.html", "#login-btn"), ("admin_dashboard", "admin/index.html", ".stat-grid"),
    ("admin_orders", "admin/orders.html?n=BS-261118-7001", "[data-detail-number]"), ("admin_menu", "admin/menu.html", "[data-dish]"), ("track_demo", "track-demo.html?speed=6", ".map-pin--courier"),
]

def plan_for(width, height, tag):
    ph, names = [], {}
    def phase(name, url, waitfor, script, shot=None):
        d = {"waitFor": waitfor, "script": SETUP + script, "timeout": 12}
        if url: d["url"] = BASE + url
        if shot and SHOTS: d.update(screenshot=str(SHOTS / f"final_{shot}_{tag}.png"), full=True)
        names[name] = len(ph); ph.append(d)
    phase("seed", "index.html", ".cat-tile", "SEED(); return 'ok';")
    for name, url, wf in PAGES:
        phase(name, url, wf, "await wait(250); return JSON.stringify(await audit());", name)
    phase("notfound", "no/such/page.html", "h1", "return JSON.stringify({ h1: txt(q('h1')), link: q('a[href]')?.getAttribute('href'), res: await audit() });", "404")
    # переходы посетителя: главная → меню → блюдо → корзина → оформление; ссылки шапки
    phase("flow1", "index.html", ".cat-tile", "q('.nav__link[href$=\"menu.html\"]').click(); return 'ok';")
    phase("flow2", None, "#grid .dish-card", "q('#grid .dish-card .dish-card__title a').click(); return 'ok';")
    phase("flow3", None, ".dish-detail", "const t = txt(q('h1')); q('[data-add-detail]').click(); await wait(200); q('.nav__link[href$=\"cart.html\"]').click(); return JSON.stringify({ dish: t });")
    phase("flow4", None, ".cart-line, .state", "const r = { lines: qa('.cart-line').length }; q('#to-checkout').click(); return JSON.stringify(r);")
    phase("flow5", None, "#checkout-form", "return JSON.stringify({ path: location.pathname.replace(location.pathname.slice(0, location.pathname.lastIndexOf('/') + 1), ''), h1: txt(q('h1')) });")
    phase("flow6", "admin/login.html", "#login-btn", "q('#login-btn').click(); return 'ok';")
    phase("flow7", None, ".stat-grid", "q('.admin-nav__link[href$=\"orders.html\"]').click(); return 'ok';")
    phase("flow8", None, "[data-row]", "q('[data-open]').click(); await wait(100); return JSON.stringify({ n: txt(q('[data-detail-number]')) });")
    return {"width": width, "height": height, "phases": ph}, names

def run(width, height, tag):
    plan, names = plan_for(width, height, tag)
    f = TMP / f"plan_{tag}.json"; f.write_text(json.dumps(plan), encoding="utf-8")
    try: res = subprocess.run([str(BIN), str(f)], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", str(BIN)]); errors.append(f"[{tag}] тайм-аут запуска WebKit"); return None, None
    try: return json.loads(res.stdout), names
    except Exception: errors.append(f"[{tag}] раннер не вернул JSON: {(res.stdout + res.stderr)[:300]}"); return None, None

def check_viewport(width, height, tag):
    out, names = run(width, height, tag)
    if not out: return
    def get(name):
        ph = out["phases"][names[name]]
        if ph.get("error") or ph.get("waited") is False: errors.append(f"[{tag}] фаза «{name}»: {ph.get('error')}"); return None
        try: return json.loads(ph["result"].replace(" ", " "))
        except Exception: return ph.get("result")
    E = lambda c, m: expect(c, f"[{tag}] {m}")
    for name, url, _ in PAGES:
        r = get(name)
        if not r: continue
        E(not r["overflow"], f"{name}: горизонтальная прокрутка")
        E(r["broken"] == [], f"{name}: не загрузились картинки {r['broken']}")
        E(r["bad"] == [], f"{name}: битые ссылки {r['bad']}")
        E(r["external"] == [] and r["extRes"] == [], f"{name}: внешние ссылки/ресурсы {r['external']} {r['extRes']}")
        E(r["h1"] == 1 and r["links"] > 0 and 8 <= len(r["title"]) <= 70, f"{name}: h1={r['h1']}, ссылок={r['links']}, заголовок «{r['title']}»")
        E(r["banner"] == "Демонстрационная версия. Реальные заказы не принимаются", f"{name}: плашка демонстрации «{r['banner']}»")
        if name in ("home", "menu", "dish"): E(r["imgs"] > 0, f"{name}: на странице нет картинок")
    r = get("notfound")
    if r: # ссылка на главную на 404 зависит от хоста (локально «/», на Pages — подкаталог): её логику проверяет browser_tests.py, здесь — страница, ресурсы и ссылки
        E(r["h1"] == "Страница не найдена" and r["res"]["bad"] == [] and r["res"]["extRes"] == [] and not r["res"]["broken"] and r["link"], f"404: {r}")
    r = get("flow3")
    if r: E(r["dish"], f"переход меню → блюдо: {r}")
    r = get("flow4")
    if r: E(r["lines"] >= 2, f"корзина после перехода (2 позиции из заготовки + добавленное блюдо): {r}")
    r = get("flow5")
    if r: E(r["path"] == "checkout.html" and "Оформление" in r["h1"], f"корзина → оформление: {r}")
    r = get("flow8")
    if r: E(r["n"] in ("BS-261118-7001", "BS-261118-7002"), f"вход → панель → заказы → подробности: {r}")
    for c in out.get("console", []):
        if c.startswith(("error|", "rejection|", "console.error|")): errors.append(f"[{tag}] ошибка JavaScript: {c[:220]}")

try:
    check_viewport(390, 800, "phone")
    if not QUICK:
        check_viewport(768, 1000, "tablet")
        check_viewport(1280, 900, "desktop")
except (KeyError, TypeError, IndexError, AttributeError) as e:
    errors.append(f"сценарий вернул неполные данные: {type(e).__name__}: {e}")
if srv: srv.shutdown()
print(f"Проверок аудита (WebKit): {checks}" + (f"; скриншоты: {SHOTS}" if SHOTS else ""))
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "аудит страниц пройден" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
