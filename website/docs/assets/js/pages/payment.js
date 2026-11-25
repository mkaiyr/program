import { url } from "../core.js";
import { esc, fmtMoney } from "../format.js";
import { applyPayment, switchPaymentMethod, needsPayment } from "../orders-logic.js";
import { findOrder, updateOrder, ordersState } from "../orders-store.js";
import { mountLayout } from "../layout.js";
import { boot } from "./common.js";
import { orderNumberFromUrl, notFoundHTML } from "./order-common.js";

mountLayout();
const mount = document.getElementById("payment");

function render(data) {
  const S = data.settings, number = orderNumberFromUrl();
  let order = findOrder(number), busy = false, message = "";
  if (!order) { mount.innerHTML = notFoundHTML(S, ordersState() === "corrupt").replace('href="orders.html"', `href="${url("orders.html")}"`); return; }
  const orderHref = () => url("order.html") + "?n=" + encodeURIComponent(order.number);
  function draw() {
    if (!needsPayment(order)) {
      mount.innerHTML = `<div class="container"><h1 class="page-title">Оплата заказа ${esc(order.number)}</h1><div class="state card"><h2>${order.payment.status === "paid" ? "Заказ уже оплачен" : "Для этого заказа онлайн-оплата не нужна"}</h2><p class="muted">Способ оплаты: ${esc(order.payment.methodName)}.</p><a class="btn btn--primary" href="${orderHref()}">Открыть заказ</a></div></div>`;
      return;
    }
    const failed = order.payment.status === "failed";
    const others = S.payments.filter((p) => p.id !== "online");
    mount.innerHTML = `<div class="container"><h1 class="page-title">Оплата заказа ${esc(order.number)}</h1>
      <p class="demo-note" role="note">${esc(S.notices.demoBanner)}. ${esc(S.notices.demoPayment)}</p>
      <div class="state card pay-card">
        <p class="muted">К оплате</p><p class="price price--lg" data-pay-total>${fmtMoney(order.totals.total)}</p>
        ${failed ? `<div class="alert alert--danger" role="alert" id="pay-failed"><strong>Оплата не прошла</strong> (имитация отказа). Деньги не списывались. Можно повторить или выбрать другой способ.</div>` : ""}
        ${message ? `<div class="alert alert--danger" role="alert">${esc(message)}</div>` : ""}
        <p class="small muted">Это имитация: ни карта, ни банк не участвуют. Выберите, чем закончится демонстрационная оплата.</p>
        <div class="cart-actions">
          <button type="button" class="btn btn--primary" id="pay-ok"${busy ? " disabled" : ""}>${failed ? "Повторить: имитировать успешную оплату" : "Имитировать успешную оплату"}</button>
          <button type="button" class="btn btn--secondary" id="pay-fail"${busy ? " disabled" : ""}>Имитировать отказ в оплате</button></div>
        ${failed ? `<div class="field"><label for="other-method">Или выберите другой способ оплаты</label><select class="select" id="other-method">${others.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select><button type="button" class="btn btn--ghost" id="switch">Перейти на выбранный способ</button></div>` : ""}
        <a class="btn btn--ghost" href="${orderHref()}">Открыть заказ без оплаты</a></div></div>`;
  }
  function apply(outcome) {
    if (busy) return; busy = true; message = "";
    const r = applyPayment(order, outcome);
    if (!r.ok) { busy = false; if (r.error === "already_paid") { order = r.order; } draw(); return; }
    if (!updateOrder(r.order)) { busy = false; message = "Не удалось сохранить результат оплаты в браузере. Повторите попытку."; draw(); return; }
    order = r.order;
    if (outcome === "success") { location.assign(orderHref() + "&paid=1"); return; }
    busy = false; draw(); document.getElementById("pay-failed")?.focus();
  }
  mount.addEventListener("click", (e) => {
    if (e.target.closest("#pay-ok")) apply("success");
    else if (e.target.closest("#pay-fail")) apply("failure");
    else if (e.target.closest("#switch")) {
      const r = switchPaymentMethod(order, document.getElementById("other-method").value, S);
      if (!r.ok || !updateOrder(r.order)) { message = "Не удалось сменить способ оплаты."; draw(); return; }
      location.assign(orderHref() + "&switched=1");
    }
  });
  draw();
}
boot(render, mount);
