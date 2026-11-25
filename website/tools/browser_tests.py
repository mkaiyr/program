#!/usr/bin/env python3
"""Браузерные тесты в настоящем движке WebKit (macOS): страницы открываются с локального сервера в подкаталоге (как на GitHub Pages),
сценарии нажимают кнопки, пишут в поле поиска, перезагружают страницы; проверяется результат и делаются скриншоты.
Нужны macOS и Command Line Tools (swiftc); иначе проверка пропускается. Запуск из корня репозитория:
    python3 tools/browser_tests.py [--shots ПАПКА]  [--quick]
Каждый запуск использует временное хранилище браузера: localStorage между запусками не сохраняется."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[sys.argv.index("--root") + 1]).resolve() if "--root" in sys.argv else Path(__file__).resolve().parent.parent   # --root: другая копия сайта (для проверки самих тестов)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages_server  # noqa: E402

args = sys.argv[1:]
SHOTS = Path(args[args.index("--shots") + 1]) if "--shots" in args else None
QUICK = "--quick" in args
if sys.platform != "darwin" or not shutil.which("swiftc"):
    print("ПРЕДУПРЕЖДЕНИЕ: нужны macOS и swiftc — браузерные тесты пропущены"); sys.exit(0)
TMP = Path(tempfile.mkdtemp())
BIN = TMP / "webkit_run"
r = subprocess.run(["swiftc", "-O", str(Path(__file__).resolve().parent / "webkit_run.swift"), "-o", str(BIN)], capture_output=True, text=True)
if not BIN.exists():
    print("ОШИБКА: не удалось собрать раннер WebKit:", r.stderr[-400:]); sys.exit(1)
if SHOTS: SHOTS.mkdir(parents=True, exist_ok=True)
srv, BASE = pages_server.start(ROOT)

errors, checks = [], 0
def expect(cond, msg):
    global checks
    checks += 1
    if not cond: errors.append(msg)

WAIT = "await new Promise(r => setTimeout(r, %d));"
SETUP = r"""
const q = (s, r = document) => r.querySelector(s), qa = (s, r = document) => [...r.querySelectorAll(s)], wait = (ms) => new Promise(r => setTimeout(r, ms));
const cart = () => JSON.parse(localStorage.getItem('bistro.cart.v1') || 'null');
const badge = () => { const b = q('[data-cart-count]'); return b && !b.hidden && getComputedStyle(b).display !== 'none' ? b.textContent : ''; };   // видимость по вычисленному стилю, а не по атрибуту
const overflow = () => document.documentElement.scrollWidth > window.innerWidth + 1;
const brokenImgs = () => qa('img').filter(i => i.complete && i.naturalWidth === 0).map(i => i.getAttribute('src'));
const txt = (el) => el ? el.textContent.replace(/\s+/g, ' ').trim() : null;
"""
def js(body): return SETUP + body

def plan_for(width, height, tag):
    shot = (lambda n: str(SHOTS / f"{n}_{tag}.png")) if SHOTS else (lambda n: None)
    ph = []
    def phase(url, waitfor, script, shotname=None, full=True):
        d = {"url": BASE + url, "waitFor": waitfor, "script": js(script), "timeout": 12}
        if shotname and SHOTS: d.update(screenshot=shot(shotname), full=full)
        ph.append(d)
    # 0. главная
    phase("index.html", ".cat-tile", """
      return JSON.stringify({h1: txt(q('h1')), tiles: qa('.cat-tile').length, rec: qa('#rec-grid .dish-card').length, recPlaceholders: qa('#rec-grid .photo--placeholder').length, tileCols: new Set(qa('.cat-tile').map(t => Math.round(t.getBoundingClientRect().left))).size, overflow: overflow(), broken: brokenImgs(), badge: badge(),
        homeLinkShown: getComputedStyle(q('.nav__link--home')).display !== 'none', title: document.title, banner: txt(q('#demo-banner')), navLinks: qa('.nav__link').map(a => txt(a))});""", "home")
    # 1. меню: категории, поиск, недоступное блюдо
    phase("menu.html", "#grid .dish-card", """
      const out = {cards: qa('#grid .dish-card').length, chips: qa('.chip').length, overflow: overflow(), broken: brokenImgs(), found: txt(q('#found')), clearHidden: getComputedStyle(q('#q-clear')).display === 'none'};
      const onion = q('[data-dish="onion-rings"]'); out.onion = {disabled: !!q('button[disabled]', onion), add: !!q('[data-add]', onion), badge: txt(q('.badge--danger', onion))};
      q('.chip[data-cat="drinks"]').click(); await wait(150); out.drinks = qa('#grid .dish-card').length; out.url1 = location.search; out.pressed = q('.chip[aria-pressed="true"]').dataset.cat;
      q('.chip[data-cat="all"]').click(); await wait(100);
      const s = q('#q'); s.value = 'курица'; s.dispatchEvent(new Event('input', {bubbles: true})); await wait(400);
      out.chicken = qa('#grid .dish-card').map(c => c.dataset.dish).sort(); out.url2 = location.search; out.found2 = txt(q('#found'));
      s.value = 'xyzzy'; s.dispatchEvent(new Event('input', {bubbles: true})); await wait(400); out.empty = txt(q('#grid .state')); out.emptyCards = qa('#grid .dish-card').length;
      q('#reset').click(); await wait(150); out.afterReset = qa('#grid .dish-card').length; out.qValue = q('#q').value;
      s.value = 'ЧЁРНЫЙ чай'; s.dispatchEvent(new Event('input', {bubbles: true})); await wait(400); out.yo = qa('#grid .dish-card').map(c => c.dataset.dish);
      q('#q-clear').click(); await wait(100); out.cleared = qa('#grid .dish-card').length;
      return JSON.stringify(out);""", "menu")
    # 2. добавление в корзину на странице меню
    phase("menu.html", "#grid .dish-card", """
      const out = {before: badge()};
      q('[data-add="cheeseburger"]').click(); await wait(150);
      out.afterAdd = badge(); out.stepper = !!q('[data-dish="cheeseburger"] .stepper'); out.addGone = !q('[data-dish="cheeseburger"] [data-add]'); out.toast = txt(q('.toast'));
      q('[data-inc="cheeseburger"]').click(); await wait(100); out.qty = txt(q('[data-dish="cheeseburger"] .stepper__qty')); out.stored = JSON.stringify(cart().items); out.badge2 = badge();
      q('[data-add="onion-rings"]'); out.onionAdd = !!q('[data-dish="onion-rings"] [data-add]');
      return JSON.stringify(out);""", "menu_added", False)
    # 3. сохранение после перезагрузки страницы
    phase("menu.html", "#grid .dish-card", """
      return JSON.stringify({badge: badge(), qty: txt(q('[data-dish="cheeseburger"] .stepper__qty')), stored: JSON.stringify(cart().items)});""")
    # 4. страница блюда
    phase("dish.html?id=cheeseburger", ".dish-detail", """
      const out = {h1: txt(q('h1')), title: document.title, price: txt(q('.price--lg')), allergens: txt(q('.allergens')), composition: txt(q('.composition__text')), overflow: overflow(), broken: brokenImgs(), alt: q('.dish-detail__media img').alt, related: qa('#rel-grid .dish-card').length, note: txt(q('[data-in-cart]')), noteHidden: q('[data-in-cart]').hidden};
      const input = q('#qty'); input.value = '3'; input.dispatchEvent(new Event('change', {bubbles: true})); q('[data-add-detail]').click(); await wait(200);
      out.stored = JSON.stringify(cart().items); out.badge = badge(); out.note2 = txt(q('[data-in-cart]'));
      input.value = '99'; input.dispatchEvent(new Event('change', {bubbles: true})); out.clamped = input.value; q('[data-qty-dec]').click(); out.dec = input.value; input.value = '0'; input.dispatchEvent(new Event('change', {bubbles: true})); out.min = input.value;
      return JSON.stringify(out);""", "dish")
    # 5. недоступное блюдо и несуществующее
    phase("dish.html?id=onion-rings", ".dish-detail", """
      return JSON.stringify({h1: txt(q('h1')), addDetail: !!q('[data-add-detail]'), disabled: !!q('button[disabled]'), text: txt(q('.dish-detail__info')), badge: txt(q('.badge--danger'))});""")
    phase("dish.html?id=no-such-dish", ".state", """
      return JSON.stringify({text: txt(q('.state')), h1: txt(q('h1')), title: document.title, link: q('.state a') && q('.state a').getAttribute('href')});""")
    phase("dish.html?id=cheese-sauce", ".dish-detail", """
      return JSON.stringify({src: q('.dish-detail__media img').getAttribute('src'), alt: q('.dish-detail__media img').alt, allergens: txt(q('.allergens'))});""", "dish_placeholder")
    # 6. корзина
    phase("cart.html", ".cart-line", """
      const out = {lines: qa('.cart-line').length, name: txt(q('.cart-line__title')), qty: txt(q('.cart-line .stepper__qty')), lineTotal: txt(q('.cart-line__total')), sum: txt(q('.sum')), total: txt(q('.sum__total')), progress: !!q('.progress'), overflow: overflow(), broken: brokenImgs(),
        checkout: (() => { const b = q('#to-checkout'); return {link: !!b, href: b ? b.getAttribute('href') : ''}; })(), badge: badge(), minWarn: !!q('.field-error')};
      q('[data-dec="cheeseburger"]').click(); await wait(150); out.qtyAfterDec = txt(q('.cart-line .stepper__qty')); out.lineAfter = txt(q('.cart-line__total'));
      q('[data-inc="cheeseburger"]').click(); await wait(150); out.qtyAfterInc = txt(q('.cart-line .stepper__qty'));
      const c = q('#comment'); c.focus(); c.value = 'без лука, соус отдельно'; c.dispatchEvent(new Event('input', {bubbles: true})); await wait(150); out.comment = cart().comment; out.focusKept = document.activeElement === q('#comment');
      return JSON.stringify(out);""", "cart")
    phase("cart.html", ".cart-line", """
      return JSON.stringify({comment: q('#comment').value, qty: txt(q('.cart-line .stepper__qty')), lines: qa('.cart-line').length});""")
    # 7. минимальный заказ и бесплатная доставка
    phase("cart.html", ".cart-line", """
      localStorage.setItem('bistro.cart.v1', JSON.stringify({items: [{id: 'fries', qty: 1}], comment: ''})); return 'ok';""")
    phase("cart.html", ".cart-line", """
      const out = {sum: txt(q('.sum')), warn: txt(q('.field-error')), free: txt(q('.progress + p')), total: txt(q('.sum__total'))};
      localStorage.setItem('bistro.cart.v1', JSON.stringify({items: [{id: 'caesar', qty: 4}], comment: ''})); return JSON.stringify(out);""")
    phase("cart.html", ".cart-line", """
      return JSON.stringify({sum: txt(q('.sum')), warn: txt(q('.field-error')), free: txt(q('.progress + p')), total: txt(q('.sum__total'))});""", "cart_free")
    # 8. недоступное блюдо в корзине, удаление, очистка, пустое состояние
    phase("cart.html", ".cart-line", """
      localStorage.setItem('bistro.cart.v1', JSON.stringify({items: [{id: 'onion-rings', qty: 2}, {id: 'fries', qty: 1}], comment: ''})); return 'ok';""")
    phase("cart.html", ".cart-line", """
      const out = {lines: qa('.cart-line').length, unavailable: qa('.cart-line.is-unavailable').length, sum: txt(q('.sum')), note: qa('.field-error').map(txt)};
      q('[data-remove="onion-rings"]').click(); await wait(150); out.afterRemove = qa('.cart-line').length;
      q('#clear').click(); await wait(100); out.confirm = txt(q('#clear')); q('#clear-no').click(); await wait(100); out.afterCancel = qa('.cart-line').length;
      q('#clear').click(); await wait(100); q('#clear').click(); await wait(150); out.empty = txt(q('.state')); out.stored = JSON.stringify(cart()); out.badge = badge();
      return JSON.stringify(out);""", "cart_empty", False)
    # 9. испорченное хранилище
    phase("index.html", ".cat-tile", """
      localStorage.setItem('bistro.cart.v1', '{broken json'); return 'ok';""")
    phase("menu.html", "#grid .dish-card", """
      const out = {cards: qa('#grid .dish-card').length, badge: badge()};
      localStorage.setItem('bistro.cart.v1', JSON.stringify({items: [{id: 'ghost', qty: 5}, {id: 'fries', qty: 9999}, {id: 'wings', qty: -4}, null, 'x'], comment: 7})); return JSON.stringify(out);""")
    phase("cart.html", ".cart-line", """
      return JSON.stringify({lines: qa('.cart-line').map(l => l.dataset.line), qty: txt(q('.cart-line .stepper__qty')), badge: badge()});""")
    # 10. страница 404 и доступность кнопок
    phase("no/such/page.html", "h1", """
      // скрипт 404.html выполняем с подменой адреса: как он поведёт себя на github.io (проектный сайт, сайт в корне, служебный домен)
      const code = qa('script').map(x => x.textContent).find(t => t.includes('getElementById'));
      const run = (host, path) => { const el = {href: ''}; new Function('location', 'document', code)({hostname: host, pathname: path}, {getElementById: () => el}); return el.href; };
      return JSON.stringify({h1: txt(q('h1')), local: q('#home').getAttribute('href'), pages: run('mkaiyr.github.io', '/kafe-bistro/no/such.html'), root: run('mkaiyr.github.io', '/no-such.html'), dotted: run('mkaiyr.github.io', '/robots.txt')});""")
    phase("menu.html", "#grid .dish-card", """
      const small = [...qa('button, .btn, .chip, .stepper__btn, .nav__link, input, textarea')].filter(e => e.offsetParent !== null).map(e => ({e, r: e.getBoundingClientRect()})).filter(x => x.r.height < 40).map(x => (x.e.className || x.e.tagName) + ' ' + Math.round(x.r.width) + 'x' + Math.round(x.r.height));
      const noName = qa('button').filter(b => !b.textContent.trim() && !b.getAttribute('aria-label')).length;
      const imgNoAlt = qa('img').filter(i => !i.hasAttribute('alt')).length;
      return JSON.stringify({small: [...new Set(small)].slice(0, 8), noName, imgNoAlt, lang: document.documentElement.lang, h1count: qa('h1').length});""")
    return {"width": width, "height": height, "phases": ph}

def run(width, height, tag):
    plan = plan_for(width, height, tag)
    f = TMP / f"plan_{tag}.json"; f.write_text(json.dumps(plan), encoding="utf-8")
    try: res = subprocess.run([str(BIN), str(f)], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", str(BIN)]); errors.append(f"[{tag}] тайм-аут запуска WebKit"); return None
    try: out = json.loads(res.stdout)
    except Exception: errors.append(f"[{tag}] раннер не вернул JSON: {(res.stdout + res.stderr)[:300]}"); return None
    return out

def parse(out, i, tag):
    ph = out["phases"][i]
    if ph.get("error") or ph.get("waited") is False: errors.append(f"[{tag}] фаза {i}: {ph.get('error')}"); return None
    try: return json.loads(ph["result"].replace("\u00a0", " "))   # неразрывные пробелы в суммах → обычные
    except Exception: return ph.get("result")

def _check_viewport(width, height, tag, mobile):
    out = run(width, height, tag)
    if not out: return
    if not out: return
    errs = [c for c in out.get("console", []) if c.startswith(("error|", "rejection|", "console.error|"))]
    expect(not errs, f"[{tag}] ошибки JavaScript в консоли: {errs[:3]}")
    p = lambda i: parse(out, i, tag)
    # главная
    r = p(0)
    if r:
        expect(r["h1"] == "Кафе Бистро", f"[{tag}] главная: h1 = {r['h1']}"); expect(r["tiles"] == 9, f"[{tag}] главная: плиток категорий {r['tiles']}"); expect(r["rec"] == 4 and r["recPlaceholders"] == 0, f"[{tag}] главная: рекомендованных {r['rec']}, из них с заглушкой {r['recPlaceholders']}")
        expect(r["tileCols"] == (2 if width < 640 else 3), f"[{tag}] главная: колонок плиток категорий {r['tileCols']}")
        expect(not r["overflow"], f"[{tag}] главная: горизонтальная прокрутка"); expect(not r["broken"], f"[{tag}] главная: битые картинки {r['broken']}")
        expect(r["badge"] == "", f"[{tag}] главная: счётчик корзины виден при пустой корзине ({r['badge']!r})")
        expect(r["homeLinkShown"] == (not mobile), f"[{tag}] главная: пункт «Главная» в шапке {'виден на телефоне' if mobile else 'скрыт на компьютере'}")
        expect(r["banner"] == "Демонстрационная версия. Реальные заказы не принимаются", f"[{tag}] главная: нет плашки демо")
    # меню
    r = p(1)
    if r:
        expect(r["cards"] == 30, f"[{tag}] меню: карточек {r['cards']}"); expect(r["chips"] == 10, f"[{tag}] меню: кнопок категорий {r['chips']}")
        expect(not r["overflow"], f"[{tag}] меню: горизонтальная прокрутка"); expect(r["clearHidden"], f"[{tag}] меню: кнопка «Сбросить» видна при пустом поиске"); expect(not r["broken"], f"[{tag}] меню: битые картинки {r['broken']}")
        expect(r["onion"] == {"disabled": True, "add": False, "badge": "Нет в наличии"}, f"[{tag}] меню: недоступное блюдо {r['onion']}")
        expect(r["drinks"] == 4 and "cat=drinks" in r["url1"] and r["pressed"] == "drinks", f"[{tag}] меню: категория «Напитки»: {r['drinks']}, {r['url1']}")
        expect({"chicken-pizza", "pasta-chicken", "nuggets", "chicken-soup", "caesar"} <= set(r["chicken"]) and "q=" in r["url2"], f"[{tag}] меню: поиск «курица» {r['chicken']}")
        expect(r["emptyCards"] == 0 and "Ничего не найдено" in (r["empty"] or ""), f"[{tag}] меню: пустое состояние поиска"); expect(r["afterReset"] == 30 and r["qValue"] == "", f"[{tag}] меню: сброс поиска {r['afterReset']}")
        expect(r["yo"] == ["black-tea"], f"[{tag}] меню: «ЧЁРНЫЙ чай» → {r['yo']}"); expect(r["cleared"] == 30, f"[{tag}] меню: кнопка «Сбросить» {r['cleared']}")
    # добавление
    r = p(2)
    if r:
        expect(r["before"] == "" and r["afterAdd"] == "1", f"[{tag}] корзина: счётчик {r['before']!r} → {r['afterAdd']!r}"); expect(r["stepper"] and r["addGone"], f"[{tag}] корзина: кнопка не заменилась счётчиком")
        expect(r["qty"] == "2" and r["badge2"] == "2" and r["stored"] == '[{"id":"cheeseburger","qty":2}]', f"[{tag}] корзина: количество {r['qty']}, хранилище {r['stored']}")
        expect("Добавлено" in (r["toast"] or ""), f"[{tag}] корзина: нет уведомления"); expect(r["onionAdd"] is False, f"[{tag}] корзина: у недоступного блюда есть кнопка добавления")
    r = p(3)
    if r: expect(r["badge"] == "2" and r["qty"] == "2" and "cheeseburger" in r["stored"], f"[{tag}] корзина не сохранилась после перезагрузки: {r}")
    # страница блюда
    r = p(4)
    if r:
        expect(r["h1"] == "Чизбургер" and "Чизбургер" in r["title"], f"[{tag}] блюдо: заголовок {r['h1']} / {r['title']}"); expect("2" in r["price"] and "290" in r["price"], f"[{tag}] блюдо: цена {r['price']}")
        expect("Содержит" in r["allergens"] and "Молоко" in r["allergens"] and "общее оборудование" in r["allergens"], f"[{tag}] блюдо: аллергены {r['allergens'][:120]}")
        expect("булочка" in r["composition"] and "фирменный соус (майонез" in r["composition"], f"[{tag}] блюдо: состав {r['composition'][:80]}")
        expect(not r["overflow"] and not r["broken"], f"[{tag}] блюдо: прокрутка/картинки"); expect(r["alt"] == "Чизбургер" and r["related"] == 3, f"[{tag}] блюдо: alt/похожие {r['alt']} {r['related']}")
        expect(r["stored"] == '[{"id":"cheeseburger","qty":5}]' and r["badge"] == "5", f"[{tag}] блюдо: добавление 3 к 2 → {r['stored']}"); expect("5" in (r["note2"] or ""), f"[{tag}] блюдо: заметка «в корзине» {r['note2']}")
        expect(r["clamped"] == "20" and r["dec"] == "19" and r["min"] == "1", f"[{tag}] блюдо: границы количества {r['clamped']}/{r['dec']}/{r['min']}")
    r = p(5)
    if r: expect(r["h1"] == "Чизбургер" or True, "");
    r = p(5)
    if r:
        expect(r["h1"] == "Луковые кольца" and not r["addDetail"] and r["disabled"] and "нельзя" in r["text"] and r["badge"] == "Нет в наличии", f"[{tag}] недоступное блюдо: {r}")
    r = p(6)
    if r: expect("не найдено" in r["text"].lower() and r["h1"] == "Блюдо не найдено" and r["link"].endswith("menu.html"), f"[{tag}] несуществующее блюдо: {r}")
    r = p(7)
    if r: expect("placeholder-dish.svg" in r["src"] and r["alt"] == "Фото скоро появится" and "не подтверждены" in r["allergens"], f"[{tag}] блюдо без фото: {r}")
    # корзина
    r = p(8)
    if r:
        expect(r["lines"] == 1 and r["name"] == "Чизбургер" and r["qty"] == "5", f"[{tag}] корзина: {r['lines']} {r['name']} {r['qty']}"); expect("11" in r["lineTotal"] and "450" in r["lineTotal"], f"[{tag}] корзина: сумма строки {r['lineTotal']}")
        expect("бесплатно" in r["sum"] and "11 450" in r["total"], f"[{tag}] корзина: доставка бесплатна от 8 000: {r['sum']} / {r['total']}"); expect(r["checkout"] == {"link": True, "href": "./checkout.html"}, f"[{tag}] корзина: кнопка оформления должна вести на checkout.html: {r['checkout']}")
        expect(r["qtyAfterDec"] == "4" and r["qtyAfterInc"] == "5", f"[{tag}] корзина: ± {r['qtyAfterDec']}/{r['qtyAfterInc']}"); expect(r["comment"] == "без лука, соус отдельно" and r["focusKept"], f"[{tag}] корзина: комментарий/фокус {r['comment']} {r['focusKept']}")
        expect(not r["overflow"] and not r["broken"], f"[{tag}] корзина: прокрутка/картинки")
    r = p(9)
    if r: expect(r["comment"] == "без лука, соус отдельно" and r["qty"] == "5", f"[{tag}] корзина: после перезагрузки {r}")
    r = p(11)
    if r:
        expect("500" in r["sum"] and r["warn"] and "Минимальный заказ" in r["warn"] and "2 000" in r["warn"], f"[{tag}] корзина: минимальный заказ: {r}"); expect("Осталось" in (r["free"] or "") or "осталось" in (r["free"] or ""), f"[{tag}] корзина: подсказка о бесплатной доставке {r['free']}")
        expect("1 690" in r["total"], f"[{tag}] корзина: итог 1190 + 500 = 1690, получено {r['total']}")
    r = p(12)
    if r: expect("бесплатно" in r["sum"] and not r["warn"] and "Доставка бесплатная" in (r["free"] or "") and "9 960" in r["total"], f"[{tag}] корзина: 4×Цезарь = 9 960, доставка бесплатна: {r}")
    r = p(14)
    if r:
        expect(r["lines"] == 2 and r["unavailable"] == 1 and "1 190" in r["sum"] and any("недоступно" in n or "недоступное" in n for n in r["note"]), f"[{tag}] корзина: недоступное блюдо: {r}")
        expect(r["afterRemove"] == 1 and "Да, очистить" in r["confirm"] and r["afterCancel"] == 1, f"[{tag}] корзина: удаление/подтверждение очистки {r['afterRemove']} {r['confirm']} {r['afterCancel']}")
        expect("пока пусто" in r["empty"] and r["stored"] == '{"items":[],"comment":""}' and r["badge"] == "", f"[{tag}] корзина: после очистки {r['empty']} {r['stored']} {r['badge']!r}")
    # испорченное хранилище
    r = p(16)
    if r: expect(r["cards"] == 30 and r["badge"] == "", f"[{tag}] испорченное хранилище: меню не ломается {r}")
    r = p(17)
    if r: expect(r["lines"] == ["fries"] and r["qty"] == "20" and r["badge"] == "20", f"[{tag}] хранилище с мусором очищено: {r}")
    r = p(18)
    if r: expect(r["h1"] == "Страница не найдена" and r["local"] == "/" and r["pages"] == "/kafe-bistro/" and r["root"] == "/" and r["dotted"] == "/", f"[{tag}] 404: {r}")
    r = p(19)
    if r:
        expect(not r["small"], f"[{tag}] элементы управления ниже 40 px: {r['small']}"); expect(r["noName"] == 0 and r["imgNoAlt"] == 0, f"[{tag}] доступность: кнопок без имени {r['noName']}, картинок без alt {r['imgNoAlt']}")
        expect(r["lang"] == "ru" and r["h1count"] == 1, f"[{tag}] доступность: lang/h1 {r['lang']} {r['h1count']}")

def check_viewport(width, height, tag, mobile):
    try: _check_viewport(width, height, tag, mobile)
    except (KeyError, TypeError, IndexError, AttributeError) as e:   # сценарий вернул неполные данные (например, после поломки страницы)
        errors.append(f"[{tag}] сценарий вернул неполные данные после сбоя: {type(e).__name__}: {e}")

check_viewport(390, 800, "phone", True)
if not QUICK:
    check_viewport(768, 1000, "tablet", False)
    check_viewport(1280, 900, "desktop", False)
srv.shutdown()
print(f"Браузерные проверки (WebKit): {checks}" + (f"; скриншоты: {SHOTS}" if SHOTS else ""))
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "браузерные тесты пройдены" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
