import { url } from "../core.js";
import { esc, fmtMoney } from "../format.js";
import { loadOrders, saveOrders, ordersState } from "../orders-store.js";
import { mountLayout } from "../layout.js";
import { boot } from "./common.js";
import { nameOf, fmtDate, payBadge } from "./order-common.js";

mountLayout();
const mount = document.getElementById("orders");

function render(data) {
  const S = data.settings;
  function draw() {
    const list = loadOrders(), state = ordersState();
    mount.innerHTML = `<div class="container"><h1 class="page-title">Мои заказы</h1>
      <p class="demo-note" role="note">${esc(S.notices.demoBanner)}. Заказы хранятся только в этом браузере.</p>
      ${state === "corrupt" ? `<div class="alert alert--warn" role="alert" id="corrupt"><strong>Часть данных о заказах повреждена.</strong> Показаны только читаемые заказы.${list.length ? "" : " Читаемых заказов нет."} <button type="button" class="btn btn--sm btn--secondary" id="repair">Убрать повреждённые записи</button></div>` : ""}
      ${list.length ? `<ul class="order-list">${list.map((o) => `<li class="card order-row"><a href="${url("order.html")}?n=${encodeURIComponent(o.number)}"><strong>${esc(o.number)}</strong></a><span class="muted small">${esc(fmtDate(o.createdAt))}</span><span>${o.mode === "delivery" ? "Доставка" : "Самовывоз"} · ${esc(nameOf(S.orderStatuses, o.status))}</span>${payBadge(o.payment.status, S)}<strong>${fmtMoney(o.totals.total)}</strong></li>`).join("")}</ul>`
        : `<div class="state card"><h2>Заказов пока нет</h2><p class="muted">Оформите демонстрационный заказ — он появится здесь.</p><a class="btn btn--primary" href="${url("menu.html")}">Открыть меню</a></div>`}</div>`;
  }
  mount.addEventListener("click", (e) => { if (e.target.closest("#repair")) { saveOrders(loadOrders()); draw(); } });
  draw();
}
boot(render, mount);
