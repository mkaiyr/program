import { url } from "../core.js";
import { esc, fmtMoney, maskPhone, maskAddress, maskEmail } from "../format.js";
import { orderTimeline, needsPayment } from "../orders-logic.js";
import { findOrder, ordersState } from "../orders-store.js";
import { allergenNames } from "../allergens.js";
import { mountLayout } from "../layout.js";
import { boot } from "./common.js";
import { orderNumberFromUrl, nameOf, fmtDate, payBadge, notFoundHTML } from "./order-common.js";

mountLayout();
const mount = document.getElementById("order");

function render(data) {
  const S = data.settings, q = new URLSearchParams(location.search), order = findOrder(orderNumberFromUrl());
  if (!order) { mount.innerHTML = notFoundHTML(S, ordersState() === "corrupt").replace('href="orders.html"', `href="${url("orders.html")}"`); return; }
  const st = S.orderStatuses.find((s) => s.id === order.status) || { name: order.status, hint: "" };
  const a = order.allergy, d = order.delivery;
  const allergyText = a.answer === "no" ? "Нет" : a.answer === "unsure" ? "Не уверен(а)" : [...allergenNames(a.allergens, data.allergenList), a.other].filter(Boolean).join(", ");
  const head = q.get("new") ? `<div class="alert alert--ok" role="status" id="success"><strong>Заказ оформлен!</strong> Это демонстрация: ${esc(S.notices.demoOrder)}</div>` : q.get("paid") ? `<div class="alert alert--ok" role="status" id="success"><strong>Демонстрационная оплата прошла.</strong> Деньги не списывались.</div>` : q.get("switched") ? `<div class="alert alert--ok" role="status">Способ оплаты изменён.</div>` : "";
  mount.innerHTML = `<div class="container"><h1 class="page-title">Заказ <span data-order-number>${esc(order.number)}</span></h1>
    ${head}
    <p class="demo-note" role="note">${esc(S.notices.demoBanner)}. ${esc(S.notices.demoOrder)}</p>
    <div class="cart-layout"><section class="card order-card" aria-labelledby="st-title">
      <h2 id="st-title">Статус</h2>
      <p><span class="badge badge--warn" data-order-status>${esc(st.name)}</span> ${payBadge(order.payment.status, S)}</p>
      <p class="small muted">${esc(S.notices.demoStatus)}</p>
      <ol class="timeline" aria-label="Условные этапы заказа">${orderTimeline(order, S).map((s) => `<li class="timeline__${s.state}"${s.state === "current" ? ' aria-current="step"' : ""}>${esc(s.name)}</li>`).join("")}</ol>
      ${needsPayment(order) ? `<a class="btn btn--primary" href="${url("payment.html")}?n=${encodeURIComponent(order.number)}" id="to-payment">Перейти к демонстрационной оплате</a>` : ""}
      <h2>Получение</h2>
      <dl class="kv"><dt>Способ</dt><dd>${order.mode === "delivery" ? "Доставка" : "Самовывоз"}</dd>
        ${d ? `<dt>Адрес</dt><dd data-masked>${esc(maskAddress(d.street))}</dd>`
          : `<dt>Адрес кафе</dt><dd>${esc(S.cafe.address)}</dd><dt>Время</dt><dd>${esc(nameOf(S.pickup.timeOptions, order.pickupTime))}</dd>`}
        <dt>Имя</dt><dd>${esc(order.customer.name)}</dd><dt>Телефон</dt><dd data-masked>${esc(maskPhone(order.customer.phone))}</dd>
        ${order.customer.email ? `<dt>Почта</dt><dd data-masked>${esc(maskEmail(order.customer.email))}</dd>` : ""}
        <dt>Оформлен</dt><dd>${esc(fmtDate(order.createdAt))}</dd><dt>Оплата</dt><dd>${esc(order.payment.methodName)}</dd>
        <dt>Контакты</dt><dd class="muted">Телефон, адрес и почта скрыты: в демонстрации полные данные на этой странице не показываются.</dd><dt>Аллергия</dt><dd>${esc(allergyText)}</dd>${order.comment ? `<dt>Комментарий</dt><dd>${esc(order.comment)}</dd>` : ""}</dl>
      ${order.allergyConflicts.length ? `<div class="alert alert--warn" role="note"><strong>Внимание.</strong> ${order.allergyConflicts.map((c) => `«${esc(c.name)}»: ${esc(allergenNames(c.allergens, data.allergenList).join(", "))}`).join("; ")}. ${esc(S.allergy.crossContaminationNote)}</div>` : ""}
    </section>
    <aside class="cart-summary card" aria-labelledby="items-title"><h2 id="items-title">Состав заказа</h2>
      <ul class="sum-lines">${order.items.map((i) => `<li><span>${esc(i.name)} × ${i.qty} <span class="muted small">(${fmtMoney(i.price)})</span></span><span>${fmtMoney(i.lineTotal)}</span></li>`).join("")}</ul>
      <dl class="sum"><div><dt>Блюда</dt><dd>${fmtMoney(order.totals.subtotal)}</dd></div><div><dt>Доставка</dt><dd>${order.mode === "pickup" ? "без доставки" : order.totals.fee ? fmtMoney(order.totals.fee) : "бесплатно"}</dd></div></dl>
      <p class="sum__total"><span>Итого</span><strong data-order-total>${fmtMoney(order.totals.total)}</strong></p>
      <a class="btn btn--ghost btn--block" href="${url("orders.html")}">Все мои заказы</a><a class="btn btn--ghost btn--block" href="${url("menu.html")}">В меню</a></aside></div></div>`;
}
boot(render, mount);
