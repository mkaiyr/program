import { url } from "../core.js";
import { esc, fmtMoney } from "../format.js";
import { filterOrders, sortOrders, allowedStatuses, changeStatus } from "../admin-logic.js";
import { loadOrders, updateOrder, ordersState } from "../orders-store.js";
import { boot } from "./common.js";
import { requireDemoAdmin, mountAdminFrame, statusBadge } from "./admin-common.js";
import { nameOf, fmtDate, payBadge } from "./order-common.js";

const mount = document.getElementById("aorders");
if (requireDemoAdmin()) {
  mountAdminFrame("orders");
  boot(render, mount);
}

function render(data) {
  const S = data.settings;
  const st = { q: "", status: "all", sort: "desc", selected: new URLSearchParams(location.search).get("n") || "", confirm: false, msg: "", msgKind: "" };
  mount.innerHTML = `<div class="alert alert--warn" id="corrupt" role="alert" hidden>Часть данных о заказах повреждена и не показана.</div>
    <div class="toolbar" role="search"><div class="field"><label for="f-q">Поиск по номеру</label><input class="input" id="f-q" type="search" placeholder="BS-261118-1234 или 1234" autocomplete="off"></div>
      <div class="field"><label for="f-status">Статус</label><select class="select" id="f-status"><option value="all">Все статусы</option>${S.orderStatuses.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></div>
      <div class="field"><label for="f-sort">Сортировка</label><select class="select" id="f-sort"><option value="desc">Сначала новые</option><option value="asc">Сначала старые</option></select></div></div>
    <p class="small muted" id="count" role="status" aria-live="polite"></p>
    <div class="admin-orders"><div id="list"></div><section id="detail" class="card admin-detail" aria-label="Подробности заказа" tabindex="-1"></section></div>`;
  const $ = (id) => document.getElementById(id);

  function drawList() {
    const all = loadOrders(), rows = sortOrders(filterOrders(all, { query: st.q, status: st.status }), st.sort);
    $("corrupt").hidden = ordersState() !== "corrupt";
    $("count").textContent = all.length ? `Показано заказов: ${rows.length} из ${all.length}` : "";
    $("list").innerHTML = !all.length ? `<div class="empty card" id="empty-all"><p><strong>Заказов пока нет.</strong></p><p class="muted">Они появятся после оформления демонстрационного заказа на сайте.</p><a class="btn btn--primary" href="${url("menu.html")}">Открыть меню сайта</a></div>`
      : !rows.length ? `<div class="empty card" id="empty-filter"><p><strong>Ничего не найдено.</strong></p><p class="muted">Измените поиск или статус.</p></div>`
      : `<table class="admin-table"><thead><tr><th scope="col">Номер</th><th scope="col">Время</th><th scope="col">Получение</th><th scope="col">Оплата</th><th scope="col">Статус</th><th scope="col">Сумма</th></tr></thead><tbody>${rows.map((o) => `<tr class="${o.number === st.selected ? "is-selected" : ""}" data-row="${esc(o.number)}">
        <td data-label="Номер"><button type="button" class="linkbtn" data-open="${esc(o.number)}"${o.number === st.selected ? ' aria-current="true"' : ""}>${esc(o.number)}</button></td>
        <td data-label="Время">${esc(fmtDate(o.createdAt))}</td><td data-label="Получение">${o.mode === "delivery" ? "Доставка" : "Самовывоз"}</td>
        <td data-label="Оплата">${payBadge(o.payment.status, S)}</td><td data-label="Статус">${statusBadge(o, S)}</td><td data-label="Сумма"><strong>${fmtMoney(o.totals.total)}</strong></td></tr>`).join("")}</tbody></table>`;
  }
  function drawDetail() {
    const o = loadOrders().find((x) => x.number === st.selected), box = $("detail");
    if (!o) { box.innerHTML = st.selected ? `<h2>Заказ не найден</h2><p class="muted">Заказа ${esc(st.selected)} нет в этом браузере.</p>` : `<h2>Подробности заказа</h2><p class="muted">Выберите заказ в списке.</p>`; return; }
    const next = allowedStatuses(o, S), d = o.delivery, a = o.allergy;
    const addr = d ? [d.street, d.apt && "кв./офис " + d.apt, d.entrance && "подъезд " + d.entrance, d.floor && "этаж " + d.floor, d.intercom && "домофон " + d.intercom].filter(Boolean).join(", ") : S.cafe.address;
    const phone = o.customer.phone ? `+7 (${o.customer.phone.slice(0, 3)}) ${o.customer.phone.slice(3, 6)}-${o.customer.phone.slice(6, 8)}-${o.customer.phone.slice(8)}` : "";
    box.innerHTML = `<h2 tabindex="-1" id="detail-title">Заказ <span data-detail-number>${esc(o.number)}</span></h2>
      <p>${statusBadge(o, S)} ${payBadge(o.payment.status, S)}</p>
      ${st.msg ? `<p class="alert ${st.msgKind === "ok" ? "alert--ok" : "alert--danger"}" id="admin-msg" role="${st.msgKind === "ok" ? "status" : "alert"}">${esc(st.msg)}</p>` : ""}
      ${next.length ? `<div class="status-form"><div class="field"><label for="status-select">Новый статус</label><select class="select" id="status-select">${next.filter((s) => s !== "cancelled").map((s) => `<option value="${esc(s)}">${esc(nameOf(S.orderStatuses, s))}</option>`).join("")}</select></div>
        <button type="button" class="btn btn--primary" id="apply-status"${next.length === 1 ? " disabled" : ""}>Изменить статус</button>
        ${st.confirm ? `<button type="button" class="btn btn--primary btn--danger" id="cancel-yes">Да, отменить заказ</button><button type="button" class="btn btn--ghost" id="cancel-no">Не отменять</button>` : `<button type="button" class="btn btn--secondary" id="cancel-order">Отменить заказ</button>`}</div>`
        : `<p class="small muted" id="final-note">Статус «${esc(nameOf(S.orderStatuses, o.status))}» конечный: изменить заказ нельзя.</p>`}
      <p class="small muted">${esc(S.notices.demoStatus)}</p>
      <p class="alert alert--warn" id="contacts-warning" role="note">Контакты клиента показаны полностью только в админке. Хранилище браузера локальное и не защищено: не вводите настоящие данные.</p>
      <dl class="kv"><dt>Оформлен</dt><dd>${esc(fmtDate(o.createdAt))}</dd><dt>Получение</dt><dd>${o.mode === "delivery" ? "Доставка" : "Самовывоз"}</dd>
        <dt>${d ? "Адрес" : "Адрес кафе"}</dt><dd>${esc(addr)}</dd>${o.pickupTime ? `<dt>Время</dt><dd>${esc(nameOf(S.pickup.timeOptions, o.pickupTime))}</dd>` : ""}${d && d.courierComment ? `<dt>Курьеру</dt><dd>${esc(d.courierComment)}</dd>` : ""}
        <dt>Клиент</dt><dd>${esc(o.customer.name)}</dd><dt>Телефон</dt><dd>${esc(phone)}</dd><dt>Оплата</dt><dd>${esc(o.payment.methodName)}</dd>
        <dt>Комментарий клиента</dt><dd data-comment>${o.comment ? esc(o.comment) : "—"}</dd><dt>Аллергия</dt><dd>${a.answer === "no" ? "Нет" : a.answer === "unsure" ? "Не уверен(а)" : esc([...a.allergens.map((x) => (data.allergenList.find((y) => y.id === x) || { name: x }).name), a.other].filter(Boolean).join(", "))}</dd></dl>
      <h3>Состав</h3><ul class="sum-lines">${o.items.map((i) => `<li><span>${esc(i.name)} × ${i.qty}</span><span>${fmtMoney(i.lineTotal)}</span></li>`).join("")}</ul>
      <dl class="sum"><div><dt>Блюда</dt><dd>${fmtMoney(o.totals.subtotal)}</dd></div><div><dt>Доставка</dt><dd>${o.mode === "pickup" ? "без доставки" : o.totals.fee ? fmtMoney(o.totals.fee) : "бесплатно"}</dd></div></dl>
      <p class="sum__total"><span>Итого</span><strong data-detail-total>${fmtMoney(o.totals.total)}</strong></p>
      ${Array.isArray(o.history) && o.history.length ? `<h3>История статусов</h3><ol class="history">${o.history.map((h) => `<li>${esc(nameOf(S.orderStatuses, h.status))} <span class="muted small">${esc(fmtDate(h.at))}</span></li>`).join("")}</ol>` : ""}
      <a class="btn btn--ghost" href="${url("order.html")}?n=${encodeURIComponent(o.number)}">Страница заказа →</a>`;
  }
  const apply = (nextStatus) => {
    const o = loadOrders().find((x) => x.number === st.selected); if (!o) return;
    const r = changeStatus(o, nextStatus, S);
    if (!r.ok) { st.msg = "Такой переход недоступен."; st.msgKind = "err"; }
    else if (!updateOrder(r.order)) { st.msg = "Не удалось сохранить изменение в браузере. Статус не изменён."; st.msgKind = "err"; }
    else { st.msg = `Статус изменён: ${nameOf(S.orderStatuses, nextStatus)}.`; st.msgKind = "ok"; }
    st.confirm = false; drawList(); drawDetail();
  };
  $("f-q").addEventListener("input", (e) => { st.q = e.target.value; drawList(); });
  $("f-status").addEventListener("change", (e) => { st.status = e.target.value; drawList(); });
  $("f-sort").addEventListener("change", (e) => { st.sort = e.target.value; drawList(); });
  mount.addEventListener("click", (e) => {
    const open = e.target.closest("[data-open]");
    if (open) { st.selected = open.dataset.open; st.confirm = false; st.msg = ""; history.replaceState(null, "", "?n=" + encodeURIComponent(st.selected)); drawList(); drawDetail(); $("detail-title")?.focus(); $("detail").scrollIntoView({ block: "nearest" }); return; }
    if (e.target.closest("#apply-status")) apply($("status-select").value);
    else if (e.target.closest("#cancel-order")) { st.confirm = true; st.msg = ""; drawDetail(); $("cancel-yes").focus(); }
    else if (e.target.closest("#cancel-no")) { st.confirm = false; drawDetail(); $("cancel-order").focus(); }
    else if (e.target.closest("#cancel-yes")) apply("cancelled");
  });
  drawList(); drawDetail();
}
