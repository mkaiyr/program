#!/usr/bin/env python3
"""Браузерные тесты демо-админки (этап 5) в настоящем WebKit на 390, 768 и 1280 px: вход и выход, пустое состояние, заказы из хранилища,
поиск, фильтр, сортировка, смена статуса, отмена, сохранение после перезагрузки, доступность блюд (и её отражение на сайте), расчёт панели,
сбой записи, повреждённые данные и отсутствие персональных данных на публичной странице заказов.
Запуск:  python3 tools/browser_admin_tests.py [--shots ПАПКА] [--quick] [--root ДРУГАЯ_КОПИЯ]
Все персональные данные в тестах выдуманные."""
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
    print("ПРЕДУПРЕЖДЕНИЕ: нужны macOS и swiftc — браузерные тесты админки пропущены"); sys.exit(0)
TMP = Path(tempfile.mkdtemp()); BIN = TMP / "webkit_run"
subprocess.run(["swiftc", "-O", str(Path(__file__).resolve().parent / "webkit_run.swift"), "-o", str(BIN)], capture_output=True, text=True)
if not BIN.exists(): print("ОШИБКА: не удалось собрать раннер WebKit"); sys.exit(1)
if SHOTS: SHOTS.mkdir(parents=True, exist_ok=True)
LIVE = args[args.index("--url") + 1].rstrip("/") + "/" if "--url" in args else None   # --url АДРЕС: проверить опубликованный сайт
srv, BASE = (None, LIVE) if LIVE else pages_server.start(ROOT)
errors, checks = [], 0
def expect(cond, msg):
    global checks
    checks += 1
    if not cond: errors.append(msg)

WARN = "Демонстрационная админка. Данные хранятся локально в браузере. Настоящей защиты доступа нет"
SETUP = r"""
const q = (s, r = document) => r.querySelector(s), qa = (s, r = document) => [...r.querySelectorAll(s)], wait = (ms) => new Promise(r => setTimeout(r, ms));
const txt = (el) => el ? el.textContent.replace(/\s+/g, ' ').trim() : null;
const vis = (el) => !!el && !el.hidden && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
const overflow = () => document.documentElement.scrollWidth > window.innerWidth + 1;
const orders = () => { try { return JSON.parse(localStorage.getItem('bistro.orders.v1') || '[]'); } catch (e) { return 'corrupt'; } };
const mkOrder = (n, at, o) => ({ version: 1, attemptId: null, number: n, createdAt: at, updatedAt: at, demo: true, status: 'new', mode: 'delivery',
  customer: { name: 'Секретнаяфамилия Тест', phone: '7012345678', email: '' }, delivery: { street: 'Секретная улица 99', apt: '', entrance: '', floor: '', intercom: '', courierComment: '' }, pickupTime: null,
  comment: '', allergy: { answer: 'no', allergens: [], other: '' }, allergyConflicts: [], items: [{ id: 'cheeseburger', name: 'Чизбургер', price: 2290, qty: 2, lineTotal: 4580 }],
  totals: { subtotal: 4580, fee: 500, service: 0, total: 5080 }, payment: { method: 'cash', methodName: 'Наличными при получении', status: 'awaiting', attempts: 0 }, ...o });
const SEED = () => [
  mkOrder('BS-261118-1001', '2026-11-18T07:00:00.000Z', { comment: 'позвонить за 5 минут' }),
  mkOrder('BS-261118-2002', '2026-11-18T08:00:00.000Z', { status: 'accepted', mode: 'pickup', delivery: null, pickupTime: '30', items: [{ id: 'fries', name: 'Картофель фри', price: 1190, qty: 2, lineTotal: 2380 }], totals: { subtotal: 2380, fee: 0, service: 0, total: 2380 }, payment: { method: 'online', methodName: 'Онлайн-оплата', status: 'paid', attempts: 1 } }),
  mkOrder('BS-261118-3003', '2026-11-18T09:00:00.000Z', { status: 'completed', items: [{ id: 'cheeseburger', name: 'Чизбургер', price: 2290, qty: 4, lineTotal: 9160 }], totals: { subtotal: 9160, fee: 0, service: 0, total: 9160 }, payment: { method: 'card_on_delivery', methodName: 'Банковской картой при получении', status: 'paid', attempts: 0 } }),
  mkOrder('BS-261118-4004', '2026-11-18T09:30:00.000Z', { status: 'cancelled', items: [{ id: 'cheeseburger', name: 'Чизбургер', price: 2290, qty: 1, lineTotal: 2290 }], totals: { subtotal: 2290, fee: 500, service: 0, total: 2790 } }),
];
const rows = () => qa('[data-row]').map(r => r.dataset.row.slice(-4));
const setv = (id, v) => { const e = document.getElementById(id); e.value = v; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); };
"""
def js(b): return SETUP + b

def plan_for(width, height, tag):
    ph, names = [], {}
    shot = (lambda n: str(SHOTS / f"admin_{n}_{tag}.png")) if SHOTS else (lambda n: None)
    def phase(name, url, waitfor, script, shotname=None):
        d = {"waitFor": waitfor, "script": js(script), "timeout": 12}
        if url: d["url"] = BASE + url
        if shotname and SHOTS: d.update(screenshot=shot(shotname), full=True)
        names[name] = len(ph); ph.append(d)
    # 1. без входа админка закрыта (условно) и ведёт на вход
    phase("guard1", "admin/index.html", "#login-btn", "return JSON.stringify({ path: location.pathname.split('/').pop(), warn: (vis(q('#admin-warning')) ? txt(q('#admin-warning')) : ''), banner: txt(q('#demo-banner')), btn: txt(q('#login-btn')), overflow: overflow(), side: !!q('#admin-side') });", "login")
    phase("guard2", "admin/orders.html", "#login-btn", "return JSON.stringify({ path: location.pathname.split('/').pop() });")
    phase("guard3", "admin/menu.html", "#login-btn", "return JSON.stringify({ path: location.pathname.split('/').pop() });")
    # 2. вход
    phase("login", None, "#login-btn", "q('#login-btn').click(); return 'ok';")
    phase("dash_empty", None, ".stat-grid", r"""
      const st = {}; qa('[data-stat]').forEach(e => st[e.dataset.stat] = txt(e));
      return JSON.stringify({ path: location.pathname.split('/').pop(), st, empty: vis(q('#dash-empty')), warn: (vis(q('#admin-warning')) ? txt(q('#admin-warning')) : ''), nav: qa('.admin-nav__link').map(a => txt(a)), side: vis(q('#admin-side')), sideW: Math.round(q('#admin-side').getBoundingClientRect().width), overflow: overflow(), h1: txt(q('h1')) });""", "dash_empty")
    phase("orders_empty", "admin/orders.html", "#empty-all", "return JSON.stringify({ empty: vis(q('#empty-all')), overflow: overflow(), rows: rows().length });")
    phase("menu_empty_orders", "orders.html", ".state", "return JSON.stringify({ text: txt(q('.state')) });")
    # 3. заказы из хранилища
    phase("seed", "index.html", ".cat-tile", "localStorage.setItem('bistro.orders.v1', JSON.stringify(SEED())); return 'ok';")
    phase("dash", "admin/index.html", ".stat-grid", r"""
      const st = {}; qa('[data-stat]').forEach(e => st[e.dataset.stat] = txt(e));
      return JSON.stringify({ st, recent: qa('.recent li').map(li => txt(li.querySelector('strong')).slice(-4)), empty: !!q('#dash-empty'), overflow: overflow() });""", "dash")
    phase("list", "admin/orders.html", "[data-row]", r"""
      const out = { rows: rows(), count: txt(q('#count')), overflow: overflow(), tableHead: q('thead').getBoundingClientRect().width > 5 };
      setv('f-q', '1001'); out.q1 = rows(); setv('f-q', 'bs-261118-3003'); out.q2 = rows(); setv('f-q', '9999'); out.q3 = { rows: rows(), empty: vis(q('#empty-filter')) }; setv('f-q', '');
      setv('f-status', 'cancelled'); out.cancelled = rows(); setv('f-status', 'new'); out.new = rows(); setv('f-status', 'ready'); out.ready = { rows: rows(), empty: vis(q('#empty-filter')) }; setv('f-status', 'all');
      setv('f-sort', 'asc'); out.asc = rows(); setv('f-sort', 'desc'); out.desc = rows();
      return JSON.stringify(out);""", "orders")
    phase("detail", None, "[data-row]", r"""
      q('[data-open="BS-261118-1001"]').click(); await wait(100);
      const d = q('#detail'), warnC = vis(q('#contacts-warning')) ? txt(q('#contacts-warning')) : '', opts = qa('#status-select option').map(o => o.value);
      const out = { warnC, title: txt(q('[data-detail-number]')), text: txt(d), opts, link: q('#detail a[href*="order.html"]')?.getAttribute('href'), total: txt(q('[data-detail-total]')), comment: txt(q('[data-comment]')), url: location.search, overflow: overflow(), sel: !!q('tr.is-selected') };
      q('[data-open="BS-261118-2002"]').click(); await wait(100); out.pickupOpts = qa('#status-select option').map(o => o.value); out.pickupText = txt(q('#detail'));
      q('[data-open="BS-261118-3003"]').click(); await wait(100); out.done = { final: vis(q('#final-note')), select: !!q('#status-select'), cancel: !!q('#cancel-order') };
      q('[data-open="BS-261118-1001"]').click(); await wait(100);
      return JSON.stringify(out);""", "order_detail")
    phase("status", None, "#status-select", r"""
      setv('status-select', 'preparing'); q('#apply-status').click(); await wait(120);
      const o = orders().find(x => x.number === 'BS-261118-1001');
      return JSON.stringify({ msg: txt(q('#admin-msg')), badge: txt(q('#detail [data-status]')), stored: o.status, hist: o.history.map(h => h.status), row: txt(q('[data-row="BS-261118-1001"] [data-status]')), opts: qa('#status-select option').map(o => o.value) });""")
    phase("cancel", None, "#cancel-order", r"""
      q('#cancel-order').click(); await wait(80);
      const out = { confirm: vis(q('#cancel-yes')), stillNot: orders().find(x => x.number === 'BS-261118-1001').status };
      q('#cancel-no').click(); await wait(80); out.back = vis(q('#cancel-order')) && !q('#cancel-yes'); out.afterNo = orders().find(x => x.number === 'BS-261118-1001').status;
      q('#cancel-order').click(); await wait(80); q('#cancel-yes').click(); await wait(120);
      const o = orders().find(x => x.number === 'BS-261118-1001');
      out.res = { msg: txt(q('#admin-msg')), stored: o.status, pay: o.payment.status, final: vis(q('#final-note')), select: !!q('#status-select'), hist: o.history.map(h => h.status) };
      return JSON.stringify(out);""", "cancelled")
    phase("persist", "admin/orders.html?n=BS-261118-1001", "[data-detail-number]", r"""
      return JSON.stringify({ n: txt(q('[data-detail-number]')), badge: txt(q('#detail [data-status]')), final: vis(q('#final-note')), cancelledRows: (setv('f-status', 'cancelled'), rows()) });""")
    phase("dash2", "admin/index.html", ".stat-grid", r"""
      const st = {}; qa('[data-stat]').forEach(e => st[e.dataset.stat] = txt(e)); return JSON.stringify({ st });""")
    # 4. сбой записи при смене статуса
    phase("savefail", "admin/orders.html?n=BS-261118-2002", "#apply-status", r"""
      const orig = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { if (k === 'bistro.orders.v1') return; return orig.call(this, k, v); };
      setv('status-select', 'ready'); q('#apply-status').click(); await wait(120);
      const r = { msg: txt(q('#admin-msg')), stored: orders().find(x => x.number === 'BS-261118-2002').status, badge: txt(q('#detail [data-status]')) };
      Storage.prototype.setItem = orig; return JSON.stringify(r);""")
    # 5. меню: доступность
    phase("menu", "admin/menu.html", "[data-dish]", r"""
      const ids = () => qa('[data-dish]').map(r => r.dataset.dish);
      const out = { all: ids().length, count: txt(q('#m-count')), onion: { btnDisabled: q('[data-dish="onion-rings"] button').disabled, text: txt(q('[data-dish="onion-rings"] [data-av]')), note: !!q('#lock-note'), toggle: !!q('[data-dish="onion-rings"] [data-toggle]') }, overflow: overflow() };
      setv('m-av', 'unavailable'); out.unavailable = ids(); setv('m-av', 'all');
      setv('m-q', 'фри'); out.search = ids(); setv('m-q', 'zzzz'); out.none = vis(q('#m-empty')); setv('m-q', '');
      const cat = qa('#m-cat option')[1].value; setv('m-cat', cat); out.cat = { n: ids().length, ok: ids().length > 0 && ids().length < 30 }; setv('m-cat', 'all');
      q('[data-toggle="fries"]').click(); await wait(100);
      out.toggled = { badge: txt(q('[data-dish="fries"] [data-av]')), btn: txt(q('[data-dish="fries"] button')), count: txt(q('#m-count')), msg: txt(q('#m-msg')), stored: JSON.parse(localStorage.getItem('bistro.menu.availability.v1') || 'null') };
      return JSON.stringify(out);""", "menu")
    phase("public_menu", "menu.html", "#grid .dish-card", r"""
      const card = (id) => q(`[data-dish="${id}"]`);
      return JSON.stringify({ fries: { disabled: !!q('button[disabled]', card('fries')), add: !!q('[data-add]', card('fries')), badge: txt(q('.badge--danger', card('fries'))) }, caesar: { add: !!q('[data-add]', card('caesar')) }, onion: { add: !!q('[data-add]', card('onion-rings')), badge: txt(q('.badge--danger', card('onion-rings'))) } });""")
    phase("menu_reset", "admin/menu.html", "[data-dish]", r"""
      q('#m-reset').click(); await wait(100);
      return JSON.stringify({ stored: JSON.parse(localStorage.getItem('bistro.menu.availability.v1') || 'null'), fries: txt(q('[data-dish="fries"] [data-av]')), btn: q('#m-reset').disabled, msg: txt(q('#m-msg')) });""")
    phase("public_menu2", "menu.html", "#grid .dish-card", "return JSON.stringify({ fries: !!q('[data-add]', q('[data-dish=\"fries\"]')) });")
    # 6. публичная страница заказов не раскрывает персональные данные
    phase("privacy", "orders.html", ".order-row", r"""
      const html = document.documentElement.outerHTML, t = document.body.textContent;
      const leaks = ['Секретн', 'фамилия', '701', '234', 'улица', '99', 'tel:', 'courier'].filter(w => t.includes(w) || html.includes(w));
      return JSON.stringify({ rows: qa('.order-row').length, leaks, overflow: overflow(), text: txt(q('.order-row')) });""", "public_orders")
    # 7. выход
    phase("logout_go", "admin/index.html", "#admin-logout", "q('#admin-logout').click(); return 'ok';")
    phase("logout", None, "#logged-out", "return JSON.stringify({ path: location.pathname.split('/').pop(), session: localStorage.getItem('bistro.admin.session.v1'), already: !!q('#already') });")
    phase("after_logout", "admin/orders.html", "#login-btn", "return JSON.stringify({ path: location.pathname.split('/').pop() });")
    phase("tamper", "admin/login.html", "#login-btn", "localStorage.setItem('bistro.admin.session.v1', 'не json'); return 'ok';")
    phase("tamper2", "admin/index.html", "#login-btn", "return JSON.stringify({ path: location.pathname.split('/').pop() });")
    # 8. повреждённые заказы: панель не падает
    phase("login2", "admin/login.html", "#login-btn", "q('#login-btn').click(); return 'ok';")
    phase("dash_corrupt", "admin/index.html", ".stat-grid", r"""
      localStorage.setItem('bistro.orders.v1', '{не json'); return 'ok';""")
    phase("dash_corrupt2", "admin/index.html", ".stat-grid", r"""
      const st = {}; qa('[data-stat]').forEach(e => st[e.dataset.stat] = txt(e)); return JSON.stringify({ st, empty: !!q('#dash-empty'), alert: txt(q('.alert--warn')), overflow: overflow() });""", "dash_corrupt")
    return {"width": width, "height": height, "phases": ph}, names

def run(width, height, tag):
    plan, names = plan_for(width, height, tag)
    f = TMP / f"plan_{tag}.json"; f.write_text(json.dumps(plan), encoding="utf-8")
    try: res = subprocess.run([str(BIN), str(f)], capture_output=True, text=True, timeout=420)
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
    wide = width >= 960
    r = get("guard1")
    if r: E(r["path"] == "login.html" and r["warn"] == WARN and r["btn"] == "Войти как демо-админ" and r["banner"].startswith("Демонстрационная версия") and not r["overflow"] and not r["side"], f"вход: {r}")
    for k in ("guard2", "guard3"):
        r = get(k)
        if r: E(r["path"] == "login.html", f"без входа админка открывает вход: {r}")
    r = get("dash_empty")
    if r:
        E(r["path"] == "index.html" and r["st"] == {"total": "0", "new": "0", "completed": "0", "cancelled": "0", "sum": "0 ₸"} and r["empty"], f"панель без заказов: {r['st']} пусто={r['empty']}")
        E(r["warn"] == WARN and r["nav"] == ["Панель", "Заказы", "Меню", "Сайт"] and r["side"] and not r["overflow"] and r["h1"] == "Панель управления", f"каркас админки: {r}")
        E((r["sideW"] < 300) if wide else (r["sideW"] >= width - 2), f"боковая панель: ширина {r['sideW']} при экране {width}")
    r = get("orders_empty")
    if r: E(r["empty"] and r["rows"] == 0 and not r["overflow"], f"заказы: пусто: {r}")
    r = get("menu_empty_orders")
    if r: E("Заказов пока нет" in r["text"], f"публичный список: пусто: {r}")
    r = get("dash")
    if r:
        E(r["st"] == {"total": "4", "new": "1", "completed": "1", "cancelled": "1", "sum": "16 620 ₸"} and not r["empty"], f"панель из заказов: {r['st']}")
        E(r["recent"] == ["4004", "3003", "2002", "1001"] and not r["overflow"], f"последние заказы: {r['recent']}")
    r = get("list")
    if r:
        E(r["rows"] == ["4004", "3003", "2002", "1001"] and "Показано заказов: 4 из 4" in r["count"] and not r["overflow"], f"список: {r['rows']} {r['count']}")
        E(r["tableHead"] == (width >= 768), f"таблица/карточки: заголовок таблицы виден={r['tableHead']} при ширине {width}")
        E(r["q1"] == ["1001"] and r["q2"] == ["3003"] and r["q3"] == {"rows": [], "empty": True}, f"поиск: {r['q1']} {r['q2']} {r['q3']}")
        E(r["cancelled"] == ["4004"] and r["new"] == ["1001"] and r["ready"] == {"rows": [], "empty": True}, f"фильтр статуса: {r['cancelled']} {r['new']} {r['ready']}")
        E(r["asc"] == ["1001", "2002", "3003", "4004"] and r["desc"] == ["4004", "3003", "2002", "1001"], f"сортировка: {r['asc']} {r['desc']}")
    r = get("detail")
    if r:
        E(r["title"] == "BS-261118-1001" and r["total"] == "5 080 ₸" and r["comment"] == "позвонить за 5 минут" and r["sel"], f"подробности: {r['title']} {r['total']} {r['comment']}")
        E("Чизбургер × 2" in r["text"] and "Наличными при получении" in r["text"] and "Секретная улица 99" in r["text"] and "Доставка" in r["text"] and "Статус демонстрационный" in r["text"], f"подробности: состав/оплата/адрес: {r['text'][:300]}")
        E(r["opts"] == ["accepted", "preparing", "ready", "out_for_delivery", "completed"] and r["link"].endswith("order.html?n=BS-261118-1001") and "BS-261118-1001" in r["url"] and not r["overflow"], f"подробности: статусы/ссылка {r['opts']} {r['link']}")
        E("не защищено" in r["warnC"] and "+7 (701) 234-56-78" in r["text"], f"админка: предупреждение о контактах и полные данные: {r['warnC']}")
        E(r["pickupOpts"] == ["preparing", "ready", "completed"] and "Самовывоз" in r["pickupText"] and "Через 30 минут" in r["pickupText"], f"самовывоз: статусы {r['pickupOpts']}")
        E(r["done"] == {"final": True, "select": False, "cancel": False}, f"выполненный заказ неизменяем: {r['done']}")
    r = get("status")
    if r: E("Статус изменён: Готовится" in r["msg"] and r["badge"] == "Готовится" and r["stored"] == "preparing" and r["hist"] == ["preparing"] and r["row"] == "Готовится" and r["opts"] == ["ready", "out_for_delivery", "completed"], f"смена статуса: {r}")
    r = get("cancel")
    if r:
        E(r["confirm"] and r["stillNot"] == "preparing" and r["back"] and r["afterNo"] == "preparing", f"отмена: подтверждение не отменяет сразу: {r}")
        E(r["res"]["stored"] == "cancelled" and r["res"]["final"] and not r["res"]["select"] and r["res"]["hist"] == ["preparing", "cancelled"] and r["res"]["pay"] == "awaiting", f"отмена: результат {r['res']}")
    r = get("persist")
    if r: E(r["n"] == "BS-261118-1001" and r["badge"] == "Отменён" and r["final"] and r["cancelledRows"] == ["4004", "1001"], f"после перезагрузки статус сохранён: {r}")
    r = get("dash2")
    if r: E(r["st"] == {"total": "4", "new": "0", "completed": "1", "cancelled": "2", "sum": "11 540 ₸"}, f"панель пересчитана после отмены: {r['st']}")
    r = get("savefail")
    if r: E("Не удалось сохранить" in r["msg"] and r["stored"] == "accepted" and r["badge"] == "Принят", f"сбой записи статуса: {r}")
    r = get("menu")
    if r:
        E(r["all"] == 30 and "Недоступных: 1" in r["count"] and not r["overflow"], f"меню админки: {r['all']} {r['count']}")
        E(r["onion"] == {"btnDisabled": True, "text": "Нет в наличии", "note": True, "toggle": False}, f"луковые кольца зафиксированы: {r['onion']}")
        E(r["unavailable"] == ["onion-rings"] and r["search"] == ["fries"] and r["none"] and r["cat"]["ok"], f"меню: фильтры {r['unavailable']} {r['search']} {r['none']} {r['cat']}")
        t = r["toggled"]
        E(t["badge"] == "Нет в наличии" and t["btn"] == "Сделать доступным" and "Недоступных: 2" in t["count"] and "Картофель фри: теперь недоступно" in t["msg"] and t["stored"] == {"fries": False}, f"выключение блюда: {t}")
    r = get("public_menu")
    if r: E(r["fries"]["disabled"] and not r["fries"]["add"] and r["fries"]["badge"] == "Нет в наличии" and r["caesar"]["add"] and not r["onion"]["add"], f"блюдо выключено и на сайте: {r}")
    r = get("menu_reset")
    if r: E(r["stored"] == {} and r["fries"] == "Доступно" and r["btn"] and "сброшены" in r["msg"], f"сброс доступности: {r}")
    r = get("public_menu2")
    if r: E(r["fries"], f"после сброса блюдо снова в продаже: {r}")
    r = get("privacy")
    if r: E(r["rows"] == 4 and r["leaks"] == [] and not r["overflow"], f"публичные заказы: утечка персональных данных {r['leaks']} / {r['text']}")
    r = get("logout")
    if r: E(r["path"] == "login.html" and r["session"] is None and not r["already"], f"выход: {r}")
    r = get("after_logout")
    if r: E(r["path"] == "login.html", f"после выхода админка закрыта: {r}")
    r = get("tamper2")
    if r: E(r["path"] == "login.html", f"испорченный флаг входа не пускает: {r}")
    r = get("dash_corrupt2")
    if r: E(r["st"]["total"] == "0" and r["empty"] and r["alert"] and "повреждена" in r["alert"] and not r["overflow"], f"повреждённые заказы: {r}")
    for c in out.get("console", []):
        if c.startswith(("error|", "rejection|", "console.error|")): errors.append(f"[{tag}] ошибка в консоли страницы: {c[:200]}")

try:
    check_viewport(390, 800, "phone")
    if not QUICK:
        check_viewport(768, 1000, "tablet")
        check_viewport(1280, 900, "desktop")
except (KeyError, TypeError, IndexError, AttributeError) as e:
    errors.append(f"сценарий вернул неполные данные: {type(e).__name__}: {e}")
if srv: srv.shutdown()
print(f"Браузерные проверки админки (WebKit): {checks}" + (f"; скриншоты: {SHOTS}" if SHOTS else ""))
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "браузерные тесты админки пройдены" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
