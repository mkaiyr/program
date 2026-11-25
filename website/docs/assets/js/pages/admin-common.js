import { url } from "../core.js";
import { esc } from "../format.js";
import { isAdmin, logout } from "../admin-store.js";
import { mountLayout } from "../layout.js";

export const ADMIN_NAV = [
  { id: "index", href: "admin/index.html", label: "Панель" },
  { id: "orders", href: "admin/orders.html", label: "Заказы" },
  { id: "menu", href: "admin/menu.html", label: "Меню" },
];
// Защита здесь условная: это проверка флага в localStorage, а не авторизация. Любой может выставить его сам.
export function requireDemoAdmin() {
  if (isAdmin()) return true;
  location.replace(url("admin/login.html"));
  return false;
}
export function mountAdminFrame(active) {
  mountLayout();
  const side = document.getElementById("admin-side");
  side.innerHTML = `<a class="wordmark admin-side__brand" href="${url("index.html")}" aria-label="Кафе Бистро — на сайт"><img class="wordmark__mark" src="${url("assets/img/logo-mark.svg")}" alt="" width="34" height="34"><span class="wordmark__text">Кафе <em>Бистро</em></span></a>
    <p class="admin-side__tag">Демо-админка</p>
    <nav class="admin-nav" aria-label="Разделы админки">${ADMIN_NAV.map((n) => `<a class="admin-nav__link" href="${url(n.href)}"${n.id === active ? ' aria-current="page"' : ""}>${esc(n.label)}</a>`).join("")}
      <a class="admin-nav__link" href="${url("index.html")}">Сайт</a></nav>
    <button type="button" class="btn btn--secondary btn--sm admin-side__out" id="admin-logout">Выйти</button>`;
  document.getElementById("admin-logout").addEventListener("click", () => { logout(); location.assign(url("admin/login.html") + "?out=1"); });
}
export const statusBadge = (o, S) => {
  const name = (S.orderStatuses.find((s) => s.id === o.status) || { name: o.status }).name;
  return `<span class="badge ${o.status === "completed" ? "badge--ok" : o.status === "cancelled" ? "badge--danger" : o.status === "new" ? "badge--warn" : ""}" data-status="${esc(o.status)}">${esc(name)}</span>`;
};
