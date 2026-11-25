import { url } from "../core.js";
import { esc, fmtMoney, plural } from "../format.js";
import { dishCardHTML } from "../html.js";
import { bindCartActions, refreshCard } from "../cart-ui.js";
import { onCartChange, loadCart } from "../cart-store.js";
import { mountLayout } from "../layout.js";
import { boot, makeCtx } from "./common.js";

mountLayout();
const mount = document.getElementById("home");

function render(data) {
  const { settings: s, dishes, categories, dishesById } = data;
  const heroPick = ["margherita", "chickenburger", "cappuccino"].map((id) => dishesById[id]).filter((d) => d && d.photo);
  // На витрине главной — рекомендованные блюда с фотографиями; если их меньше четырёх, добираем остальными
  const recAll = dishes.filter((d) => d.recommended && d.available);
  const rec = [...recAll.filter((d) => d.photo), ...recAll.filter((d) => !d.photo)].slice(0, 4);
  const del = s.delivery, cafe = s.cafe;
  const collage = heroPick.map((d, i) => `<img class="hero__photo hero__photo--${i + 1}" src="${url("assets/img/dishes/" + d.photo.file)}" alt="${esc(d.photo.alt || d.name)}" width="${d.photo.width}" height="${d.photo.height}" ${i ? 'loading="lazy"' : 'fetchpriority="high"'} decoding="async">`).join("");
  const tiles = categories.map((c) => {
    const items = dishes.filter((d) => d.category === c.id), withPhoto = items.find((d) => d.photo);
    const img = withPhoto ? `<img src="${url("assets/img/dishes/" + withPhoto.photo.thumb)}" alt="" width="480" height="360" loading="lazy" decoding="async">` : `<img src="${url("assets/img/placeholder-dish.svg")}" alt="" width="800" height="600" loading="lazy" decoding="async">`;
    return `<a class="cat-tile" href="${url("menu.html?cat=" + encodeURIComponent(c.id))}">${img}<span class="cat-tile__text"><strong>${esc(c.name)}</strong><span class="small">${items.length} ${plural(items.length, "блюдо", "блюда", "блюд")}</span></span></a>`;
  }).join("");
  const ctx = makeCtx(data);
  mount.innerHTML = `
  <section class="hero"><div class="container hero__grid">
    <div class="hero__text rise"><p class="badge badge--warn">Демонстрационная версия</p>
      <h1 class="hero__title">Кафе Бистро</h1><p class="hero__lead">${esc(cafe.tagline)}. ${esc(cafe.description)}</p>
      <div class="hero__cta"><a class="btn btn--primary" href="${url("menu.html")}">Смотреть меню</a><a class="btn btn--secondary" href="#delivery">Доставка и самовывоз</a></div></div>
    <div class="hero__collage rise" aria-hidden="false">${collage}</div></div></section>
  <section class="section" aria-labelledby="cats-title"><div class="container"><h2 id="cats-title">Категории меню</h2><div class="cat-grid">${tiles}</div></div></section>
  <section class="section section--alt" aria-labelledby="rec-title"><div class="container"><div class="section__head"><h2 id="rec-title">Рекомендуем</h2><a href="${url("menu.html")}">Всё меню →</a></div>
    <div class="dish-grid dish-grid--four" id="rec-grid">${rec.map((d) => dishCardHTML(d, ctx)).join("")}</div></div></section>
  <section class="section" id="delivery" aria-labelledby="del-title"><div class="container"><h2 id="del-title">Доставка и самовывоз</h2>
    <div class="info-grid">
      <div class="card info"><h3>Доставка</h3><p>${fmtMoney(del.fee)}, бесплатно от ${fmtMoney(del.freeFrom)}.</p><p class="muted">Минимальный заказ: ${fmtMoney(del.minOrder)}. Время: ${esc(del.etaText)}.</p></div>
      <div class="card info"><h3>Самовывоз</h3><p>Бесплатно. Готовность: ${esc(del.pickupEtaText)}.</p><p class="muted">${esc(cafe.address)}</p></div>
      <div class="card info"><h3>Часы работы</h3>${cafe.hours.map((h) => `<p>${esc(h.days)}: ${esc(h.time)}</p>`).join("")}<p class="muted">${esc(cafe.phone)}</p></div>
    </div><p class="small muted">${esc(s.notices.demoBanner)} Адрес, телефон и часы работы условные.</p></div></section>`;
  const grid = document.getElementById("rec-grid");
  bindCartActions(grid, data, (id) => refreshCard(grid, dishesById[id], dishCardHTML, makeCtx(data)));
  onCartChange(() => { for (const d of rec) refreshCard(grid, d, dishCardHTML, makeCtx(data, loadCart(dishesById))); });
}
boot(render, mount);
