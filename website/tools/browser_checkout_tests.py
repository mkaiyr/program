#!/usr/bin/env python3
"""Браузерные тесты оформления заказа (этап 4) в настоящем WebKit: доставка и самовывоз, три способа оплаты, имитация успеха и отказа,
проверка формы, минимальная сумма, бесплатная доставка, двойное нажатие, очистка корзины, сохранение и восстановление заказа,
повреждённое хранилище, сбой записи, недоступное блюдо, пустая корзина — на экранах 390, 768 и 1280 px.
Запуск из корня репозитория:  python3 tools/browser_checkout_tests.py [--shots ПАПКА] [--quick] [--root ДРУГАЯ_КОПИЯ]
Переходы между страницами делаются нажатием кнопок и ссылок; следующая фаза ждёт элемент, который есть только на целевой странице."""
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
    print("ПРЕДУПРЕЖДЕНИЕ: нужны macOS и swiftc — браузерные тесты оформления пропущены"); sys.exit(0)
TMP = Path(tempfile.mkdtemp())
BIN = TMP / "webkit_run"
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

SETUP = r"""
const q = (s, r = document) => r.querySelector(s), qa = (s, r = document) => [...r.querySelectorAll(s)], wait = (ms) => new Promise(r => setTimeout(r, ms));
const txt = (el) => el ? el.textContent.replace(/\s+/g, ' ').trim() : null;
const vis = (el) => !!el && !el.hidden && getComputedStyle(el).display !== 'none';
const overflow = () => document.documentElement.scrollWidth > window.innerWidth + 1;
const cart = () => JSON.parse(localStorage.getItem('bistro.cart.v1') || 'null');
const orders = () => { try { return JSON.parse(localStorage.getItem('bistro.orders.v1') || '[]'); } catch (e) { return 'corrupt'; } };
const seed = (items, comment = '') => localStorage.setItem('bistro.cart.v1', JSON.stringify({ items, comment }));
const setv = (id, v) => { const e = document.getElementById(id); e.value = v; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); };
const pick = (name, val) => q(`input[name="${name}"][value="${val}"]`).click();
const fill = (o = {}) => {
  const c = { mode: 'delivery', name: 'Айгерим', phone: '+7 701 234-56-78', street: 'Абая 10', payment: 'cash', allergy: 'no', ...o };
  pick('mode', c.mode); setv('name', c.name); setv('phone', c.phone); setv('email', 'client@example.invalid');
  if (c.mode === 'delivery') { setv('street', c.street); if (c.apt) setv('apt', c.apt); } else setv('pickupTime', c.pickupTime || 'asap');
  pick('payment', c.payment); pick('allergy', c.allergy);
  for (const a of (c.allergens || [])) q(`input[name="allergen"][value="${a}"]`).click();
};
const badge = () => { const b = q('[data-cart-count]'); return b && !b.hidden && getComputedStyle(b).display !== 'none' ? b.textContent : ''; };
"""
def js(b): return SETUP + b

def plan_for(width, height, tag):
    ph, names = [], {}
    shot = (lambda n: str(SHOTS / f"checkout_{n}_{tag}.png")) if SHOTS else (lambda n: None)
    def phase(name, url, waitfor, script, shotname=None):
        d = {"waitFor": waitfor, "script": js(script), "timeout": 12}
        if url: d["url"] = BASE + url
        if shotname and SHOTS: d.update(screenshot=shot(shotname), full=True)
        names[name] = len(ph); ph.append(d)

    # A. пустая корзина
    phase("empty", "checkout.html", ".state", "return JSON.stringify({h1: txt(q('h1')), text: txt(q('.state')), form: !!q('#checkout-form'), banner: txt(q('#demo-banner')), overflow: overflow()});", "empty")
    # B. доставка: ошибки формы, переключение способа, итоги
    phase("seed1", "index.html", ".cat-tile", "seed([{id:'cheeseburger', qty:2}]); return 'ok';")
    phase("form", "checkout.html", "#checkout-form", r"""
      const out = {};
      const sum = () => ({ sub: txt(q('[data-sum=subtotal]')), fee: txt(q('[data-sum=fee]')), total: txt(q('[data-sum=total]')), free: txt(q('[data-sum=free]')) });
      out.delivery = { sum: sum(), addr: vis(q('#delivery-block')), pickup: vis(q('#pickup-block')), banner: txt(q('#demo-banner')), note: (vis(q('.demo-note')) ? txt(q('.demo-note')) : ''), pay: qa('input[name=payment]').map(i => i.value), cardInputs: qa('input').filter(i => /card|cvc|cvv|pan/i.test(i.name + i.id + i.autocomplete)).length, overflow: overflow() };
      q('#submit').click(); await wait(150);
      out.empty = { cnt: txt(q('#summary .small')), headerH: Math.round(q('#site-header').getBoundingClientRect().height), box: vis(q('#form-error')), boxText: txt(q('#form-error')), name: txt(q('#name-err')), phone: txt(q('#phone-err')), street: txt(q('#street-err')), allergy: txt(q('#allergy-err')), invalid: q('#name').getAttribute('aria-invalid'), focus: document.activeElement.id, orders: orders().length, cart: cart().items.length };
      setv('name', 'Айгерим'); setv('phone', '123'); pick('allergy', 'no'); setv('street', 'Абая 10'); q('#submit').click(); await wait(120);
      out.badPhone = { phone: txt(q('#phone-err')), name: vis(q('#name-err')), focus: document.activeElement.id, orders: orders().length };
      pick('mode', 'pickup'); await wait(60);
      out.pickup = { sum: sum(), addr: vis(q('#delivery-block')), pickup: vis(q('#pickup-block')), times: qa('#pickupTime option').map(o => o.value), overflow: overflow() };
      pick('mode', 'delivery'); await wait(60);
      out.back = sum();
      return JSON.stringify(out);""", "form_errors")
    # B2. валидная доставка с наличными, двойное нажатие
    phase("submit1", None, "#checkout-form", r"""
      fill({ mode: 'delivery', payment: 'cash', apt: '12' }); const f = q('#checkout-form');
      f.requestSubmit(); f.requestSubmit(); q('#submit').click(); q('#submit').click();
      return JSON.stringify({ sync: orders().length });""")
    phase("order1", None, "[data-order-number]", r"""
      const o = orders(), out = { n: txt(q('[data-order-number]')), ok: !!q('#success'), okText: txt(q('#success')), count: o.length, text: txt(q('.cart-layout')), cartItems: cart().items.length, badge: badge(), banner: txt(q('#demo-banner')), note: (vis(q('.demo-note')) ? txt(q('.demo-note')) : ''),
        status: txt(q('[data-order-status]')), tl: qa('.timeline li').map(x => txt(x)), total: txt(q('[data-order-total]')), overflow: overflow(), toPay: !!q('#to-payment'), url: location.search };
      out.stored = o[0] ? { items: o[0].items, totals: o[0].totals, pay: o[0].payment, mode: o[0].mode, phone: o[0].customer.phone, demo: o[0].demo } : null;
      const body = document.body.textContent + document.documentElement.outerHTML;
      out.leaks = ['Абая 10', '234-56', '234 56', '7012345678', 'офис 12', 'кв./офис', 'tel:', 'client@'].filter(w => body.includes(w));
      localStorage.setItem('test.n1', out.n); return JSON.stringify(out);""", "order_delivery")
    # B3. восстановление: заказ открывается из списка, повторная загрузка страницы списка
    phase("list1", "orders.html", ".order-row", r"""
      const n = localStorage.getItem('test.n1');
      return JSON.stringify({ rows: qa('.order-row').length, text: txt(q('.order-row')), n, has: txt(q('.order-row')).includes(n), note: (vis(q('.demo-note')) ? txt(q('.demo-note')) : ''), overflow: overflow(), href: q('.order-row a').getAttribute('href') });""", "orders")
    phase("open1", None, ".order-row", "q('.order-row a').click(); return 'ok';")
    phase("order1b", None, "[data-order-number]", r"""
      return JSON.stringify({ n: txt(q('[data-order-number]')), same: txt(q('[data-order-number]')) === localStorage.getItem('test.n1'), success: !!q('#success'), total: txt(q('[data-order-total]')), text: txt(q('.cart-layout')) });""")
    # C. после заказа страница оформления снова пустая (корзина очищена), заказ не дублируется
    phase("again", "checkout.html", ".state", "return JSON.stringify({ form: !!q('#checkout-form'), orders: orders().length });")
    # D. самовывоз + онлайн-оплата: отказ, повтор, успех; аллергия
    phase("seed2", "index.html", ".cat-tile", "seed([{id:'cheeseburger', qty:1}], 'без лука'); return 'ok';")
    phase("form2", "checkout.html", "#checkout-form", r"""
      const r = { comment: q('#comment').value, minNote: !!q('#min-note') };
      pick('mode', 'pickup'); await wait(50); r.minPickup = !!q('#min-note'); r.fee = txt(q('[data-sum=fee]')); r.total = txt(q('[data-sum=total]'));
      fill({ mode: 'pickup', pickupTime: '60', payment: 'online', allergy: 'yes', allergens: ['milk'] }); r.detail = vis(q('#allergy-detail'));
      q('#submit').click(); return JSON.stringify(r);""", "pickup_form")
    phase("pay1", None, "[data-pay-total]", r"""
      const r = { total: txt(q('[data-pay-total]')), note: (vis(q('.demo-note')) ? txt(q('.demo-note')) : ''), cardFields: qa('input').filter(i => /card|cvc|cvv|number/i.test(i.name + i.id + i.autocomplete + i.placeholder)).length, inputs: qa('input[type=text],input[type=tel],input[type=number],input[type=password]').length,
        cartItems: cart().items.length, status: orders()[0].payment.status, overflow: overflow() };
      return JSON.stringify(r);""", "payment")
    phase("pay_fail", None, "#pay-ok", r"""
      q('#pay-fail').click(); await wait(200);
      return JSON.stringify({ failed: vis(q('#pay-failed')), text: txt(q('#pay-failed')), stored: orders()[0].payment, other: !!q('#other-method'), ok: !!q('#pay-ok'), overflow: overflow() });""", "payment_failed")
    phase("pay_ok", None, "#pay-ok", r"""
      q('#pay-ok').click(); q('#pay-ok')?.click(); q('#pay-ok')?.click(); return 'ok';""")
    phase("order2", None, "[data-order-number]", r"""
      const o = orders()[0];
      return JSON.stringify({ paid: !!q('#success'), okText: txt(q('#success')), text: txt(q('.cart-layout')), stored: o.payment, pickup: o.pickupTime, mode: o.mode, count: orders().length, conflicts: o.allergyConflicts.map(c => c.dishId + ':' + c.allergens.join()), warn: txt(q('.alert--warn')), toPay: !!q('#to-payment'), overflow: overflow() });""", "order_online")
    # E. бесплатная доставка и карта при получении
    phase("seed3", "index.html", ".cat-tile", "seed([{id:'cheeseburger', qty:4}]); return 'ok';")
    phase("form3", "checkout.html", "#checkout-form", r"""
      const r = { fee: txt(q('[data-sum=fee]')), total: txt(q('[data-sum=total]')), free: txt(q('[data-sum=free]')) };
      fill({ payment: 'card_on_delivery' }); q('#submit').click(); return JSON.stringify(r);""")
    phase("order3", None, "[data-order-number]", r"""
      const o = orders()[0]; return JSON.stringify({ fee: o.totals.fee, total: o.totals.total, pay: o.payment, text: txt(q('.cart-layout')), toPay: !!q('#to-payment'), count: orders().length });""")
    # F. ниже минимальной суммы
    phase("seed4", "index.html", ".cat-tile", "seed([{id:'fries', qty:1}]); return 'ok';")
    phase("min", "checkout.html", "#checkout-form", r"""
      const before = orders().length, r = { note: txt(q('#min-note')) };
      fill({ mode: 'delivery' }); q('#submit').click(); await wait(200);
      r.box = vis(q('#form-error')) ? txt(q('#form-error')) : ''; r.after = orders().length - before; r.cart = cart().items.length; r.btn = !q('#submit').disabled;
      pick('mode', 'pickup'); await wait(60); r.noteGone = !q('#min-note'); r.total = txt(q('[data-sum=total]'));
      return JSON.stringify(r);""", "min_order")
    # G. недоступное блюдо
    phase("seed5", "index.html", ".cat-tile", "seed([{id:'cheeseburger', qty:1}, {id:'onion-rings', qty:1}]); return 'ok';")
    phase("unavail", "checkout.html", "#checkout-form", r"""
      const before = orders().length, r = { note: txt(q('#unavailable-note')) };
      fill({}); q('#submit').click(); await wait(200);
      r.box = vis(q('#form-error')) ? txt(q('#form-error')) : ''; r.after = orders().length - before; r.cart = cart().items.length;
      return JSON.stringify(r);""", "unavailable")
    # H. сбой записи заказа: корзина не очищается
    phase("seed6", "index.html", ".cat-tile", "seed([{id:'cheeseburger', qty:2}]); return 'ok';")
    phase("savefail", "checkout.html", "#checkout-form", r"""
      const before = orders().length, orig = Storage.prototype.setItem;
      Storage.prototype.setItem = function (k, v) { if (k === 'bistro.orders.v1') throw new DOMException('quota', 'QuotaExceededError'); return orig.call(this, k, v); };
      fill({}); q('#submit').click(); await wait(250);
      const r = { box: vis(q('#form-error')) ? txt(q('#form-error')) : '', after: orders().length - before, cart: cart().items.length, btn: !q('#submit').disabled, page: location.pathname.split('/').pop() };
      Storage.prototype.setItem = orig;
      q('#submit').click(); return JSON.stringify(r);""", "save_failed")
    phase("order_after_fail", None, "[data-order-number]", "return JSON.stringify({ count: orders().length, cart: cart().items.length });")
    # H2. тихий сбой: setItem не бросает исключение, но ничего не записывает
    phase("seed6b", "index.html", ".cat-tile", "seed([{id:'cheeseburger', qty:2}]); return 'ok';")
    phase("silentfail", "checkout.html", "#checkout-form", r"""
      const before = orders().length, orig = Storage.prototype.setItem;
      Storage.prototype.setItem = function (k, v) { if (k === 'bistro.orders.v1') return; return orig.call(this, k, v); };
      fill({}); q('#submit').click(); await wait(250);
      const r = { box: vis(q('#form-error')) ? txt(q('#form-error')) : '', after: orders().length - before, cart: cart().items.length, page: location.pathname.split('/').pop() };
      Storage.prototype.setItem = orig; q('#submit').click(); return JSON.stringify(r);""")
    phase("order_after_silent", None, "[data-order-number]", "return JSON.stringify({ cart: cart().items.length });")
    # I. повреждённые данные заказов
    phase("corrupt", "orders.html", ".order-row", r"""
      const good = localStorage.getItem('bistro.orders.v1'); localStorage.setItem('test.good', good);
      const arr = JSON.parse(good); arr.push({ junk: 1 }, 'x', null); localStorage.setItem('bistro.orders.v1', JSON.stringify(arr)); return 'ok';""")
    phase("corrupt2", "orders.html", ".order-row", r"""
      const r = { alert: vis(q('#corrupt')), rows: qa('.order-row').length };
      q('#repair').click(); await wait(100); r.after = { alert: !!q('#corrupt'), stored: orders().length, rows: qa('.order-row').length };
      localStorage.setItem('bistro.orders.v1', '{не json'); return JSON.stringify(r);""")
    phase("corrupt3", "orders.html", "#corrupt", r"""
      const r = { alert: vis(q('#corrupt')), empty: !!q('.state'), h1: txt(q('h1')), overflow: overflow() };
      return JSON.stringify(r);""", "corrupt")
    phase("notfound", "order.html?n=BS-000000-0000", ".state", "return JSON.stringify({ h1: txt(q('h1')), text: txt(q('.state')) });")
    phase("seed7", "index.html", ".cat-tile", "seed([{id:'cheeseburger', qty:2}]); return 'ok';")
    phase("with_corrupt", "checkout.html", "#checkout-form", "fill({}); q('#submit').click(); return 'ok';")
    phase("order_with_corrupt", None, "[data-order-number]", "return JSON.stringify({ count: orders().length, cart: cart().items.length });")
    # J. остальные страницы без горизонтальной прокрутки
    phase("final_orders", "orders.html", ".order-row", "return JSON.stringify({ rows: qa('.order-row').length, nav: qa('.nav__link').map(a => txt(a)), overflow: overflow() });", "orders_final")
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
    r = get("empty")
    if r: E("пусто" in r["text"] and not r["form"] and r["banner"] == "Демонстрационная версия. Реальные заказы не принимаются" and not r["overflow"], f"пустая корзина: {r}")
    r = get("form")
    if r:
        d = r["delivery"]
        E(d["sum"] == {"sub": "4 580 ₸", "fee": "500 ₸", "total": "5 080 ₸", "free": "До бесплатной доставки осталось 3 420 ₸"}, f"доставка: итоги {d['sum']}")
        E(d["addr"] and not d["pickup"], "доставка: адрес показан, самовывоз скрыт")
        E(d["banner"] == "Демонстрационная версия. Реальные заказы не принимаются" and "Реальные заказы не принимаются" in d["note"], f"оформление: тексты демонстрации {d['banner']} / {d['note']}")
        E(d["pay"] == ["online", "cash", "card_on_delivery"] and d["cardInputs"] == 0, f"оформление: способы оплаты {d['pay']}, полей карты {d['cardInputs']}")
        E(not d["overflow"] and not r["pickup"]["overflow"], "оформление: горизонтальная прокрутка")
        e = r["empty"]
        E(e["box"] and e["name"] == "Укажите имя" and e["phone"] == "Укажите номер телефона" and e["street"] == "Укажите улицу и дом" and e["allergy"] == "Ответьте на вопрос об аллергии", f"пустая форма: ошибки {e}")
        E("undefined" not in e["cnt"] and "2 позиции" in e["cnt"] and "," not in e["boxText"] and e["boxText"] == "Проверьте форму: 4 поля заполнены неверно.", f"тексты формы: {e['cnt']} / {e['boxText']}")
        E(e["headerH"] <= 90, f"шапка в один ряд: высота {e['headerH']}px")
        E(e["invalid"] == "true" and e["focus"] == "name" and e["orders"] == 0 and e["cart"] == 1, f"пустая форма: фокус/заказ/корзина {e}")
        b = r["badPhone"]
        E("формате" in b["phone"] and not b["name"] and b["focus"] == "phone" and b["orders"] == 0, f"неверный телефон: {b}")
        p = r["pickup"]
        E(p["sum"]["fee"] == "без доставки" and p["sum"]["total"] == "4 580 ₸" and p["pickup"] and not p["addr"] and p["times"] == ["asap", "30", "60", "120"], f"самовывоз: {p}")
        E(r["back"]["total"] == "5 080 ₸", f"возврат на доставку: {r['back']}")
    r = get("submit1")
    if r: E(r["sync"] == 1, f"двойное нажатие: сразу после четырёх нажатий заказов {r['sync']} (нужен 1)")
    r = get("order1")
    if r:
        s, n = r["stored"], r["n"]
        E(bool(s) and r["count"] == 1, f"двойное нажатие: заказов в хранилище {r['count']} (нужно ровно 1)")
        E(__import__("re").fullmatch(r"BS-\d{6}-\d{4}", n or "") is not None and r["ok"] and "Заказ оформлен" in r["okText"], f"страница успеха: {n} / {r['okText']}")
        E(r["cartItems"] == 0 and r["badge"] == "", f"корзина очищена после успеха: {r['cartItems']} / бейдж «{r['badge']}»")
        if s:
            E(s["items"] == [{"id": "cheeseburger", "name": "Чизбургер", "price": 2290, "qty": 2, "lineTotal": 4580}] and s["totals"] == {"subtotal": 4580, "fee": 500, "service": 0, "total": 5080}, f"снимок заказа: {s['items']} {s['totals']}")
            E(s["pay"]["method"] == "cash" and s["pay"]["status"] == "awaiting" and s["mode"] == "delivery" and s["phone"] == "7012345678" and s["demo"] is True, f"заказ: оплата/режим/телефон {s}")
        E(r["leaks"] == [], f"страница заказа раскрывает контакты: {r['leaks']}")
        E(r["status"] == "Новый" and r["tl"][0] == "Новый" and "Передан курьеру" in r["tl"], f"статус и шкала: {r['status']} {r['tl']}")
        E("Статус демонстрационный" in r["text"] and "кафе его не получит" in r["note"] and "Чизбургер × 2" in r["text"] and "Наличными при получении" in r["text"] and "Абая •••" in r["text"] and "+7 (701) ***-**-78" in r["text"] and "c***@example.invalid" in r["text"], f"содержимое заказа: {r['text'][:300]}")
        E(r["total"] == "5 080 ₸" and not r["toPay"] and not r["overflow"] and r["banner"] == "Демонстрационная версия. Реальные заказы не принимаются", f"заказ: итог/кнопка оплаты/прокрутка/плашка {r['total']} {r['toPay']} {r['overflow']}")
    r = get("list1")
    if r: E(r["rows"] == 1 and r["has"] and "5 080" in r["text"] and not r["overflow"] and "Реальные заказы не принимаются" in r["note"], f"список заказов: {r}")
    r = get("order1b")
    if r: E(r["same"] and not r["success"] and r["total"] == "5 080 ₸" and "Чизбургер × 2" in r["text"], f"заказ открыт из списка (восстановление): {r}")
    r = get("again")
    if r: E(not r["form"] and r["orders"] == 1, f"повторная загрузка оформления после заказа: {r}")
    r = get("form2")
    if r:
        E(r["comment"] == "без лука" and r["minPickup"] is False and r["fee"] == "без доставки" and r["total"] == "2 290 ₸" and r["detail"], f"самовывоз: комментарий корзины, итоги {r}")
    r = get("pay1")
    if r: E(r["total"] == "2 290 ₸" and "деньги не списываются" in r["note"] and r["cardFields"] == 0 and r["inputs"] == 0 and r["cartItems"] == 0 and r["status"] == "awaiting" and not r["overflow"], f"страница оплаты: {r}")
    r = get("pay_fail")
    if r: E(r["failed"] and "Оплата не прошла" in r["text"] and r["stored"]["status"] == "failed" and r["stored"]["attempts"] == 1 and r["other"] and r["ok"] and not r["overflow"], f"отказ оплаты: {r}")
    r = get("order2")
    if r:
        E(r["paid"] and "оплата прошла" in r["okText"] and r["stored"]["status"] == "paid" and r["stored"]["attempts"] == 2 and r["count"] == 2 and not r["toPay"], f"успех оплаты после отказа (двойной клик = одна попытка): {r}")
        E(r["mode"] == "pickup" and r["pickup"] == "60" and "Через час" in r["text"] and "Оплачено" in r["text"] and "Онлайн-оплата" in r["text"], f"самовывоз/оплата на странице заказа: {r['text'][:260]}")
        E(r["conflicts"] == ["cheeseburger:milk"] and r["warn"] and "Чизбургер" in r["warn"], f"аллергия: предупреждение {r['conflicts']} {r['warn']}")
        E(not r["overflow"], "заказ с оплатой: горизонтальная прокрутка")
    r = get("form3")
    if r: E(r["fee"] == "бесплатно" and r["total"] == "9 160 ₸" and r["free"] == "Доставка бесплатная", f"бесплатная доставка: {r}")
    r = get("order3")
    if r: E(r["fee"] == 0 and r["total"] == 9160 and r["pay"]["method"] == "card_on_delivery" and r["pay"]["status"] == "awaiting" and "Банковской картой при получении" in r["text"] and not r["toPay"] and r["count"] == 3, f"карта при получении и бесплатная доставка: {r}")
    r = get("min")
    if r: E(r["note"] and "Минимальный заказ для доставки — 2 000 ₸" in r["note"] and "Минимальный заказ" in r["box"] and r["after"] == 0 and r["cart"] == 1 and r["btn"] and r["noteGone"] and r["total"] == "1 190 ₸", f"ниже минимума: {r}")
    r = get("unavail")
    if r: E(r["note"] and "Луковые кольца" in r["note"] and "недоступное" in r["box"] and r["after"] == 0 and r["cart"] == 2, f"недоступное блюдо: {r}")
    r = get("savefail")
    if r: E("Не удалось сохранить" in r["box"] and r["after"] == 0 and r["cart"] == 1 and r["btn"] and r["page"] == "checkout.html", f"сбой записи: заказ не создан, корзина цела: {r}")
    r = get("silentfail")
    if r: E("Не удалось сохранить" in r["box"] and r["after"] == 0 and r["cart"] == 1 and r["page"] == "checkout.html", f"тихий сбой записи (setItem молчит): заказ не считается созданным, корзина цела: {r}")
    r = get("order_after_silent")
    if r: E(r["cart"] == 0, f"после тихого сбоя повторная попытка проходит: {r}")
    r = get("order_after_fail")
    if r: E(r["cart"] == 0, f"после восстановления записи заказ оформляется и корзина очищается: {r}")
    r = get("corrupt2")
    if r: E(r["alert"] and r["rows"] == 5 and r["after"] == {"alert": False, "stored": 5, "rows": 5}, f"смешанные данные заказов: {r}")
    r = get("corrupt3")
    if r: E(r["alert"] and r["empty"] and "Мои заказы" in r["h1"] and not r["overflow"], f"полностью повреждённое хранилище: {r}")
    r = get("notfound")
    if r: E("Заказ не найден" in r["h1"] and "нет в этом браузере" in r["text"], f"несуществующий заказ: {r}")
    r = get("order_with_corrupt")
    if r: E(r["count"] == 1 and r["cart"] == 0, f"оформление при повреждённом хранилище: {r}")
    r = get("final_orders")
    if r: E(not r["overflow"] and "Заказы" in r["nav"], f"шапка со ссылкой на заказы без прокрутки: {r}")
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
print(f"Браузерные проверки оформления (WebKit): {checks}" + (f"; скриншоты: {SHOTS}" if SHOTS else ""))
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "браузерные тесты оформления пройдены" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
