// Общая шапка, плашка «демо» и подвал. Все адреса строятся от data-root страницы ("./" в корне, "../" в admin/):
// сайт живёт в подкаталоге GitHub Pages (/kafe-bistro/), пути, начинающиеся с косой черты, здесь недопустимы.
import { url, loadJSON } from "./core.js";
import { loadCart, onCartChange } from "./cart-store.js";
import { cartCount } from "./cart-logic.js";
import { getData } from "./data.js";
export { url, loadJSON };

// ready: страница уже существует. Ссылки на ещё не созданные страницы не показываются.
const NAV = [
  { href: "index.html", label: "Главная", ready: true, home: true },
  { href: "menu.html", label: "Меню", ready: true },
  { href: "cart.html", label: "Корзина", ready: true, cart: true },
  { href: "orders.html", label: "Заказы", ready: true, orders: true },
  { href: "styleguide.html", label: "Стиль", ready: false },   // служебная страница дизайн-системы: в навигации для посетителей не показывается
];

function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

export async function mountLayout() {
  let settings = null;
  try { settings = await loadJSON("settings.json"); } catch (e) { /* шапка работает и без настроек */ }
  const cafe = settings?.cafe ?? { name: "Кафе Бистро" };
  const banner = document.getElementById("demo-banner");
  if (banner) banner.textContent = settings?.notices?.demoBanner ?? "Демонстрационная версия. Реальные заказы не принимаются";
  const header = document.getElementById("site-header");
  if (header) {
    const current = location.pathname.split("/").pop() || "index.html";
    const links = NAV.filter((n) => n.ready).map((n) =>
      `<a class="nav__link${n.home ? " nav__link--home" : ""}" href="${url(n.href)}"${n.href === current ? ' aria-current="page"' : ""}>${esc(n.label)}${n.cart ? '<span class="cart-badge" data-cart-count hidden></span>' : ""}</a>`).join("");
    header.innerHTML = `<div class="container site-header__row">
      <a class="wordmark" href="${url("index.html")}" aria-label="${esc(cafe.name)} — на главную">
        <img class="wordmark__mark" src="${url("assets/img/logo-mark.svg")}" alt="" width="34" height="34">
        <span class="wordmark__text">Кафе <em>Бистро</em></span></a>
      <nav class="nav" aria-label="Основное меню">${links}</nav></div>`;
  }
  const footer = document.getElementById("site-footer");
  if (footer) {
    footer.innerHTML = `<div class="container">
      <p><strong>${esc(cafe.name)}</strong> — демонстрационный проект. Контакты ниже условные.</p>
      <p class="small">${esc(cafe.address ?? "")} · ${esc(cafe.phone ?? "")}</p>
      <p class="small">Заказы не принимаются, платежи не выполняются, данные сохраняются только в вашем браузере.</p>
      <p class="small"><a href="${url("track-demo.html")}">Демо: отслеживание доставки</a> · <a href="${url("admin/login.html")}">Демо-админка</a></p></div>`;
  }
  let dishesById = null;   // после загрузки меню счётчик не учитывает чужие и удалённые блюда
  const paint = () => {
    const n = cartCount(loadCart(dishesById));
    document.querySelectorAll("[data-cart-count]").forEach((el) => { el.hidden = n === 0; el.textContent = String(n); el.setAttribute("aria-label", "позиций в корзине: " + n); });
  };
  paint(); onCartChange(paint);
  getData().then((d) => { dishesById = d.dishesById; paint(); }).catch(() => {});
}
