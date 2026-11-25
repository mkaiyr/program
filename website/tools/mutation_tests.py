#!/usr/bin/env python3
"""Контрольные поломки этапа 4: в копию сайта (во временной папке) намеренно вносится ошибка в критичный сценарий оформления заказа,
после чего JS-тесты и браузерные тесты оформления ОБЯЗАНЫ упасть. Если тесты прошли на сломанной копии — сами тесты ненадёжны.
Исходный сайт не меняется. Запуск:  python3 tools/mutation_tests.py [--quick]   (--quick: только JS-тесты и один размер экрана)"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "tools"
JS = "docs/assets/js/"
# (название, файл, что заменить, на что)
MUTANTS = [
    ("двойной заказ: проверка attemptId отключена", JS + "orders-logic.js", "if (attemptId) { const dup", "if (false) { const dup"),
    ("двойное нажатие: защита busy отключена", JS + "pages/checkout.js", "if (busy) return;", ""),
    ("корзина очищается, даже если заказ не сохранён", JS + "pages/checkout.js", "if (!r.duplicate && !saveOrders(r.orders)) { setBusy(false); failBox(", "if (!r.duplicate && !saveOrders(r.orders)) { saveCart(clearCart()); setBusy(false); failBox("),
    ("запись заказа не перечитывается (сбой незаметен)", JS + "orders-store.js", "return localStorage.getItem(ORDERS_KEY) === s;", "return true;"),
    ("минимальный заказ не проверяется", JS + "orders-logic.js", "if (!q.minOrderOk) return", "if (false) return"),
    ("телефон принимает любые цифры", JS + "validation.js", "if (d.length !== 10) return null;", "if (d.length < 1) return null;"),
    ("телефон из одинаковых цифр допускается", JS + "validation.js", "if (/^(\\d)\\1+$/.test(d)) return null;", ""),
    ("цена берётся из меню позже, а не снимается при заказе", JS + "orders-logic.js", "price: l.dish.price, qty: l.qty", "price: l.dish.price + 1, qty: l.qty"),
    ("недоступное блюдо проходит в заказ", JS + "orders-logic.js", "if (unavailable.length) return", "if (false) return"),
    ("отказ оплаты помечает заказ оплаченным", JS + "orders-logic.js", 'outcome === "success" ? "paid" : "failed"', '"paid"'),
    ("оплаченный заказ можно оплатить повторно", JS + "orders-logic.js", 'if (order.payment.status === "paid") return { ok: false, error: "already_paid", order };\n  if (outcome', 'if (outcome'),
    ("бесплатная доставка не применяется", JS + "pricing.js", "const free = sub >= (del.freeFrom ?? Infinity);", "const free = false;"),
    ("адрес обязателен и при самовывозе", JS + "validation.js", 'if (v.mode === "delivery") {\n    v.street', 'if (true) {\n    v.street'),
    ("повреждённые заказы не отфильтровываются", JS + "orders-logic.js", "for (const o of list) { if (validateStoredOrder(o) &&", "for (const o of list) { if (o &&"),
    ("демо-статус без пояснения на странице заказа", JS + "pages/order.js", "<p class=\"small muted\">${esc(S.notices.demoStatus)}</p>", ""),
    ("нет плашки демонстрации на странице оформления", JS + "pages/checkout.js", '<p class="demo-note" role="note">', '<p hidden class="demo-note" role="note">'),
    ("онлайн-оплата запрашивает данные карты", JS + "pages/payment.js", '<div class="cart-actions">\n          <button type="button" class="btn btn--primary" id="pay-ok"', '<input name="card-number" autocomplete="cc-number"><div class="cart-actions">\n          <button type="button" class="btn btn--primary" id="pay-ok"'),
    # ---- этап 5: демо-админка ----
    ("поиск по номеру игнорируется", JS + "pages/admin-orders.js", "query: st.q,", 'query: "",'),
    ("фильтр по статусу игнорируется", JS + "pages/admin-orders.js", "status: st.status }", 'status: "all" }'),
    ("сортировка перепутана", JS + "admin-logic.js", '(dir === "asc" ? 1 : -1)', '(dir === "asc" ? -1 : 1)'),
    ("статус можно вернуть назад", JS + "admin-logic.js", "...steps.slice(i + 1), ", "...steps, "),
    ("изменение статуса не сохраняется", JS + "pages/admin-orders.js", "else if (!updateOrder(r.order))", "else if (false)"),
    ("отмена без подтверждения", JS + "pages/admin-orders.js", 'st.confirm = true; st.msg = ""; drawDetail(); $("cancel-yes").focus(); }', 'apply("cancelled"); }'),
    ("панель считает отменённые в сумме", JS + "admin-logic.js", 'orders.filter((o) => o.status !== "cancelled").reduce', "orders.filter(() => true).reduce"),
    ("панель путает новые заказы", JS + "admin-logic.js", 'new: n("new")', 'new: n("accepted")'),
    ("«луковые кольца» можно включить", JS + "admin-logic.js", 'export const LOCKED_AVAILABILITY = ["onion-rings"];', "export const LOCKED_AVAILABILITY = [];"),
    ("доступность из админки не доходит до сайта", JS + "data.js", "dishes = applyAvailability(baseDishes, overrides);", "dishes = baseDishes;"),
    ("админка открыта без входа", JS + "pages/admin-common.js", "if (isAdmin()) return true;", "return true;"),
    ("выход не снимает вход", JS + "admin-store.js", "localStorage.removeItem(ADMIN_KEY);", ""),
    ("публичный список заказов показывает имя клиента", JS + "pages/orders.js", '<span class="muted small">${esc(fmtDate(o.createdAt))}</span>', '<span class="muted small">${esc(fmtDate(o.createdAt))} ${esc(o.customer.name)}</span>'),
    ("предупреждение админки скрыто", "docs/admin/index.html", '<p class="admin-warning" id="admin-warning" role="note">', '<p hidden class="admin-warning" id="admin-warning" role="note">'),
    ("сброс доступности не работает", JS + "pages/admin-menu.js", 'commit({}, "Изменения сброшены', 'commit(loadOverrides(baseById), "Изменения сброшены'),
    # ---- этап 6: маскирование контактов ----
    ("страница заказа показывает полный телефон", JS + "pages/order.js", "esc(maskPhone(order.customer.phone))", "esc(order.customer.phone)"),
    ("страница заказа показывает полный адрес", JS + "pages/order.js", "esc(maskAddress(d.street))", "esc(d.street)"),
    ("страница заказа показывает почту целиком", JS + "pages/order.js", "esc(maskEmail(order.customer.email))", "esc(order.customer.email)"),
    ("предупреждение о контактах в админке скрыто", JS + "pages/admin-orders.js", '<p class="alert alert--warn" id="contacts-warning"', '<p hidden class="alert alert--warn" id="contacts-warning"'),
    # ---- этап 6: аудит страниц ----
    ("битая ссылка в корзине", JS + "pages/cart.js", 'href="${url("menu.html")}">← Продолжить выбор', 'href="${url("menu-old.html")}">← Продолжить выбор'),
    ("внешняя ссылка в подвале сайта", JS + "layout.js", '<a href="${url("admin/login.html")}">Демо-админка</a>', '<a href="https://example.com/">Демо-админка</a>'),
    # ---- этап 8: демо-карта и виртуальный курьер ----
    ("[карта] курьер едет по прямой, а не по дороге", JS + "courier-demo.js", "lat: a[0] + (b[0] - a[0]) * t, lon: a[1] + (b[1] - a[1]) * t,", "lat: path.points[0][0] + (path.points[path.points.length - 1][0] - path.points[0][0]) * (d / path.length), lon: path.points[0][1] + (path.points[path.points.length - 1][1] - path.points[0][1]) * (d / path.length),"),
    ("[карта] на паузе курьер продолжает ехать", JS + "courier-demo.js", 'this.phase === "running" ? this.base +', 'this.phase !== "idle" ? this.base +'),
    ("[карта] сброс не возвращает курьера к кафе", JS + "courier-demo.js", 'reset() { this.phase = "idle"; this.base = 0; this.runFrom = 0; }', 'reset() { this.phase = "idle"; this.runFrom = 0; }'),
    ("[карта] после доставки нельзя запустить снова", JS + "courier-demo.js", 'if (this.phase === "running") return false;\n    this.phase = "running"; this.base = 0;', 'if (this.phase !== "idle") return false;\n    this.phase = "running"; this.base = 0;'),
    ("[карта] этап «почти у клиента» никогда не наступает", JS + "courier-demo.js", 'remaining <= ALMOST_M(this.path.length) ? "almost" : "on_the_way"', '"on_the_way"'),
    ("[карта] настоящий GPS подменён заглушкой", JS + "courier-demo.js", 'export function createRealProvider() { throw new Error(', 'export function createRealProvider() { return { source: "real_courier" }; throw new Error('),
    ("[карта] демо пишет в корзину покупателя", JS + "pages/track-demo.js", 'const DEMO_ORDER = "DEMO-0001";', 'const DEMO_ORDER = "DEMO-0001"; localStorage.setItem("bistro.cart.v1", "{}");'),
    ("[карта] маршрут нарисован прямой линией", JS + "pages/track-demo.js", "L.polyline(routeObj.route,", "L.polyline([routeObj.route[0], routeObj.route[routeObj.route.length - 1]],"),
    ("[карта] убрано авторство OpenStreetMap", JS + "pages/track-demo.js", 'attribution: `<a href="${esc(m.attributionUrl)}" target="_blank" rel="noopener">${esc(m.attribution)}</a>`', 'attribution: ""'),
    ("[карта] метка «ДЕМО-РЕЖИМ» на карте скрыта", JS + "pages/track-demo.js", 'tag.textContent = "ДЕМО-РЕЖИМ";', 'tag.textContent = "ДЕМО-РЕЖИМ"; tag.hidden = true;'),
    # ---- единый каталог: контракт ----
    ("[каталог] проверка принимает повторяющиеся id блюд", JS + "catalog-source.js", "    if (ids.has(d.id)) errors.push(`${at}: id повторяется`); ids.add(d.id);", "    ids.add(d.id);"),
    ("[каталог] цена-строка проходит проверку", JS + "catalog-source.js", "if (!Number.isInteger(d.price) || d.price <= 0)", "if (d.price <= 0)"),
    ("[каталог] скрытые блюда попадают в публичное меню", JS + "catalog-source.js", "export const publicDishes = (dishes) => dishes.filter((d) => d.active !== false);", "export const publicDishes = (dishes) => dishes;"),
    ("[каталог] источник api «работает» без сервера", JS + "catalog-source.js", '  if (mode === "api") throw new Error(', '  if (false) throw new Error('),
]
quick = "--quick" in sys.argv
# Сначала убеждаемся, что на целой копии тесты проходят: иначе «пойманные» поломки ничего не доказывают
for label, cmd in (("JS-тесты", [sys.executable, str(T / "run_js_tests.py")]), ("браузерные тесты оформления", [sys.executable, str(T / "browser_checkout_tests.py"), "--quick"]), ("браузерные тесты админки", [sys.executable, str(T / "browser_admin_tests.py"), "--quick"]), ("аудит страниц", [sys.executable, str(T / "browser_audit_tests.py"), "--quick"]), ("браузерные тесты карты", [sys.executable, str(T / "browser_map_tests.py"), "--quick"])):
    if subprocess.run(cmd, capture_output=True, text=True).returncode != 0: print(f"ОШИБКА: {label} не проходят на неизменённом сайте — контрольные поломки не запускались"); sys.exit(1)
survived, caught = [], []
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None   # --only ТЕКСТ: только поломки, в названии которых есть ТЕКСТ
for name, rel, old, new in MUTANTS:
    if ONLY and ONLY not in name: continue
    tmp = Path(tempfile.mkdtemp()); work = tmp / "site"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    f = work / rel; src = f.read_text(encoding="utf-8")
    if old not in src: print(f"ОШИБКА: мутация «{name}»: в {rel} не найден заменяемый фрагмент"); survived.append(name + " (фрагмент не найден)"); shutil.rmtree(tmp); continue
    f.write_text(src.replace(old, new, 1), encoding="utf-8")
    by = None
    r = subprocess.run([sys.executable, str(T / "run_js_tests.py"), str(work)], capture_output=True, text=True)
    if r.returncode != 0: by = "JS-тесты"
    else:
        cmd = [sys.executable, str(T / "browser_checkout_tests.py"), "--root", str(work), "--quick"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0: by = "браузерные тесты оформления"
        elif subprocess.run([sys.executable, str(T / "browser_admin_tests.py"), "--root", str(work), "--quick"], capture_output=True, text=True).returncode != 0: by = "браузерные тесты админки"
        elif subprocess.run([sys.executable, str(T / "browser_audit_tests.py"), "--root", str(work), "--quick"], capture_output=True, text=True).returncode != 0: by = "аудит страниц"
        elif subprocess.run([sys.executable, str(T / "browser_map_tests.py"), "--root", str(work), "--quick"], capture_output=True, text=True).returncode != 0: by = "браузерные тесты карты"
        elif not quick:
            r = subprocess.run([sys.executable, str(T / "browser_tests.py"), "--root", str(work), "--quick"], capture_output=True, text=True)
            if r.returncode != 0: by = "браузерные тесты сайта"
            else:
                r = subprocess.run([sys.executable, str(T / "validate_data.py"), str(work)], capture_output=True, text=True)
                if r.returncode != 0: by = "проверка данных"
    (caught if by else survived).append(name)
    print(("ПОЙМАНО  " if by else "ПРОПУЩЕНО") + f" — {name}" + (f"  [{by}]" if by else ""))
    shutil.rmtree(tmp, ignore_errors=True)
print(f"Контрольных поломок: {len(caught) + len(survived)}; поймано: {len(caught)}; пропущено: {len(survived)}")
for s in survived: print("ОШИБКА: тесты не заметили поломку:", s)
print("ИТОГ:", "все контрольные поломки пойманы" if not survived else f"пропущено: {len(survived)}")
sys.exit(1 if survived else 0)
