import { url } from "../core.js";
import { esc, fmtMoney } from "../format.js";
import { dashboardStats, recentOrders } from "../admin-logic.js";
import { loadOrders, ordersState } from "../orders-store.js";
import { boot } from "./common.js";
import { requireDemoAdmin, mountAdminFrame, statusBadge } from "./admin-common.js";
import { fmtDate } from "./order-common.js";

const mount = document.getElementById("dash");
if (requireDemoAdmin()) {
  mountAdminFrame("index");
  boot((data) => {
    const S = data.settings, orders = loadOrders(), st = dashboardStats(orders), recent = recentOrders(orders, 5);
    const card = (k, label, v) => `<div class="stat card"><p class="stat__label">${label}</p><p class="stat__value" data-stat="${k}">${v}</p></div>`;
    mount.innerHTML = `${ordersState() === "corrupt" ? '<div class="alert alert--warn" role="alert">Часть данных о заказах повреждена и не учитывается.</div>' : ""}
      <div class="stat-grid">${card("total", "Всего заказов", st.total)}${card("new", "Новые", st.new)}${card("completed", "Выполненные", st.completed)}${card("cancelled", "Отменённые", st.cancelled)}${card("sum", "Сумма заказов (без отменённых)", fmtMoney(st.sum))}</div>
      <section class="card admin-block" aria-labelledby="recent-title"><h2 id="recent-title">Последние заказы</h2>
        ${recent.length ? `<ul class="recent">${recent.map((o) => `<li><a href="${url("admin/orders.html")}?n=${encodeURIComponent(o.number)}"><strong>${esc(o.number)}</strong></a><span class="muted small">${esc(fmtDate(o.createdAt))}</span>${statusBadge(o, S)}<strong>${fmtMoney(o.totals.total)}</strong></li>`).join("")}</ul><a class="btn btn--ghost" href="${url("admin/orders.html")}">Все заказы →</a>`
          : `<div class="empty" id="dash-empty"><p><strong>Заказов пока нет.</strong></p><p class="muted">Оформите демонстрационный заказ на сайте — он появится здесь, а показатели пересчитаются.</p><a class="btn btn--primary" href="${url("menu.html")}">Открыть меню сайта</a></div>`}</section>`;
  }, mount);
}
