import { esc, fmtMoney, NBSP } from "./format.js";
import { dishAllergens, allergenNames, allergenShort, ingredientLine } from "./allergens.js";

// Все функции возвращают строки HTML; всё динамическое экранируется. ctx = { root, ingredients, allergenList, categoriesById, cartQty, settings }.
const PLACEHOLDER = "assets/img/placeholder-dish.svg";
const dishHref = (ctx, id) => ctx.root + "dish.html?id=" + encodeURIComponent(id);

export function photoTag(dish, ctx, size) {
  const p = dish.photo;
  if (!p) return `<img class="photo photo--placeholder" src="${ctx.root + PLACEHOLDER}" alt="Фото скоро появится" width="800" height="600" loading="lazy" decoding="async">`;
  const base = ctx.root + "assets/img/dishes/";
  if (size === "main") return `<img class="photo" src="${base + esc(p.file)}" alt="${esc(p.alt || dish.name)}" width="${p.width}" height="${p.height}" decoding="async" fetchpriority="high">`;
  return `<img class="photo" src="${base + esc(p.thumb)}" srcset="${base + esc(p.thumb)} 480w, ${base + esc(p.file)} ${p.width}w" sizes="(min-width: 60rem) 22rem, (min-width: 40rem) 45vw, 92vw" alt="${esc(p.alt || dish.name)}" width="480" height="360" loading="lazy" decoding="async">`;
}

export function stepperHTML(id, qty, name) {
  const i = esc(id), n = esc(name);
  return `<div class="stepper" role="group" aria-label="Количество: ${n}">
    <button type="button" class="stepper__btn" data-dec="${i}" aria-label="Убавить: ${n}">−</button>
    <span class="stepper__qty" aria-live="polite">${qty}</span>
    <button type="button" class="stepper__btn" data-inc="${i}" aria-label="Добавить ещё: ${n}">+</button></div>`;
}

export function allergenSummary(dish, ctx) {
  const a = dishAllergens(dish, ctx.ingredients);
  return a.contains.length ? "Содержит: " + allergenShort(a.contains, ctx.allergenList).join(", ") : "";
}

export function dishCardHTML(dish, ctx) {
  const id = esc(dish.id), inCart = (ctx.cartQty && ctx.cartQty[dish.id]) || 0, href = dishHref(ctx, dish.id);
  const badges = (dish.recommended ? `<span class="badge">Рекомендуем</span>` : "") + (!dish.available ? `<span class="badge badge--danger">Нет в наличии</span>` : "");
  const action = !dish.available ? `<button type="button" class="btn btn--secondary btn--sm" disabled>Нет в наличии</button>`
    : inCart ? stepperHTML(dish.id, inCart, dish.name)
    : `<button type="button" class="btn btn--primary btn--sm" data-add="${id}" aria-label="В корзину: ${esc(dish.name)}">В корзину</button>`;
  const al = allergenSummary(dish, ctx);
  return `<article class="card dish-card${dish.available ? "" : " is-unavailable"}" data-dish="${id}">
  <a class="dish-card__media" href="${href}" tabindex="-1" aria-hidden="true">${photoTag(dish, ctx, "thumb")}</a>
  <div class="dish-card__body">
    <div class="dish-card__badges">${badges}</div>
    <h3 class="dish-card__title"><a href="${href}">${esc(dish.name)}</a></h3>
    <p class="dish-card__desc">${esc(dish.description)}</p>
    <p class="dish-card__meta small muted">${dish.weight.value}${NBSP}${esc(dish.weight.unit)}${al ? " · " + esc(al) : ""}</p>
    <div class="dish-card__foot"><span class="price">${fmtMoney(dish.price)}</span>${action}</div>
  </div></article>`;
}

export function allergenBlockHTML(dish, ctx) {
  const a = dishAllergens(dish, ctx.ingredients), s = ctx.settings || {};
  const row = (title, ids, cls) => ids.length ? `<p class="allergens__row"><span class="badge ${cls}">${title}</span> ${esc(allergenNames(ids, ctx.allergenList).join(", "))}</p>` : "";
  const none = !a.contains.length && !a.may.length ? `<p class="allergens__row muted">Сведения об аллергенах не подтверждены. Если у вас аллергия, уточните состав у сотрудника.</p>` : "";
  return `<section class="allergens card" aria-labelledby="allergens-title"><h2 id="allergens-title" class="allergens__title">Аллергены</h2>
    ${row("Содержит", a.contains, "badge--warn")}${row("Может содержать", a.may, "")}${none}
    <p class="small muted">${esc(s.notices && s.notices.ingredientsDisclaimer || "")}</p>
    <p class="small muted">${esc(s.allergy && s.allergy.crossContaminationNote || "")}</p></section>`;
}

export function dishDetailHTML(dish, ctx) {
  const cat = ctx.categoriesById[dish.category], inCart = (ctx.cartQty && ctx.cartQty[dish.id]) || 0, id = esc(dish.id);
  const buy = !dish.available ? `<button type="button" class="btn btn--secondary" disabled>Нет в наличии</button><p class="muted small">Это блюдо временно недоступно, добавить его в корзину нельзя.</p>`
    : `<div class="buy"><label class="visually-hidden" for="qty">Количество</label>
       <div class="stepper stepper--lg" role="group" aria-label="Количество порций"><button type="button" class="stepper__btn" data-qty-dec aria-label="Убавить">−</button>
       <input id="qty" class="stepper__input" type="number" inputmode="numeric" min="1" max="20" value="1" aria-label="Количество порций"><button type="button" class="stepper__btn" data-qty-inc aria-label="Добавить">+</button></div>
       <button type="button" class="btn btn--primary" data-add-detail="${id}">В корзину</button></div>
       ${inCart ? `<p class="small muted" data-in-cart>Сейчас в корзине: ${inCart} шт.</p>` : `<p class="small muted" data-in-cart hidden></p>`}`;
  return `<div class="dish-detail">
    <div class="dish-detail__media">${photoTag(dish, ctx, "main")}</div>
    <div class="dish-detail__info">
      <p class="small"><a href="${ctx.root}menu.html?cat=${encodeURIComponent(dish.category)}">← ${esc(cat ? cat.name : "Меню")}</a></p>
      <h1>${esc(dish.name)}</h1>
      <div class="dish-card__badges">${dish.recommended ? `<span class="badge">Рекомендуем</span>` : ""}${!dish.available ? `<span class="badge badge--danger">Нет в наличии</span>` : ""}</div>
      <p class="price price--lg">${fmtMoney(dish.price)}</p>
      <p class="muted">${esc(dish.description)}</p>
      <p class="small muted">${dish.weight.value}${NBSP}${esc(dish.weight.unit)}</p>
      ${buy}
      <section class="composition" aria-labelledby="comp-title"><h2 id="comp-title" class="composition__title">Состав</h2><p class="composition__text">${esc(ingredientLine(dish, ctx.ingredients))}</p></section>
      ${allergenBlockHTML(dish, ctx)}
    </div></div>`;
}

export function cartLineHTML(line, ctx) {
  const d = line.dish, id = esc(d.id), href = dishHref(ctx, d.id);
  const warn = line.unavailable ? `<p class="field-error">Блюдо временно недоступно и не входит в сумму. Удалите его из корзины.</p>` : "";
  return `<li class="cart-line${line.unavailable ? " is-unavailable" : ""}" data-line="${id}">
    <a class="cart-line__media" href="${href}" tabindex="-1" aria-hidden="true">${photoTag(d, ctx, "thumb")}</a>
    <div class="cart-line__main"><h3 class="cart-line__title"><a href="${href}">${esc(d.name)}</a></h3>
      <p class="small muted">${fmtMoney(d.price)} за порцию</p>${warn}</div>
    <div class="cart-line__qty">${stepperHTML(d.id, line.qty, d.name)}</div>
    <p class="cart-line__total price">${fmtMoney(line.lineTotal)}</p>
    <button type="button" class="btn btn--ghost btn--sm cart-line__remove" data-remove="${id}" aria-label="Удалить из корзины: ${esc(d.name)}">Удалить</button></li>`;
}
