import { url } from "../core.js";
import { esc, fmtMoney } from "../format.js";
import { cartLineHTML } from "../html.js";
import { cartLines, cartSubtotal, setComment, clearCart, MAX_COMMENT } from "../cart-logic.js";
import { orderQuote } from "../pricing.js";
import { loadCart, saveCart, onCartChange } from "../cart-store.js";
import { bindCartActions } from "../cart-ui.js";
import { mountLayout } from "../layout.js";
import { boot, makeCtx } from "./common.js";

mountLayout();
const mount = document.getElementById("cart");

function render(data) {
  let confirming = false;
  function draw(keepFocus) {
    const cart = loadCart(data.dishesById), lines = cartLines(cart, data.dishesById), ctx = makeCtx(data, cart);
    if (!lines.length) {
      mount.innerHTML = `<div class="container"><h1 class="page-title">Корзина</h1><div class="state card"><h2>В корзине пока пусто</h2><p class="muted">Добавьте блюда из меню — они сохранятся в этом браузере.</p><a class="btn btn--primary" href="${url("menu.html")}">Открыть меню</a></div></div>`;
      return;
    }
    const sub = cartSubtotal(lines), q = orderQuote(sub, "delivery", data.settings), hasUnavailable = lines.some((l) => l.unavailable);
    const progress = q.freeFrom ? Math.min(100, Math.round(sub / q.freeFrom * 100)) : 0;
    const freeNote = sub === 0 ? "" : q.freeLeft > 0 ? `До бесплатной доставки осталось ${fmtMoney(q.freeLeft)}` : "Доставка бесплатная";
    const minWarn = sub > 0 && !q.minOrderOk ? `<p class="field-error">Минимальный заказ для доставки — ${fmtMoney(q.minOrder)}. Самовывоз без минимальной суммы.</p>` : "";
    const active = document.activeElement && document.activeElement.id === "comment";
    mount.innerHTML = `<div class="container"><h1 class="page-title">Корзина</h1>
      <div class="cart-layout"><section aria-labelledby="lines-title"><h2 id="lines-title" class="visually-hidden">Состав заказа</h2>
        <ul class="cart-lines card">${lines.map((l) => cartLineHTML(l, ctx)).join("")}</ul>
        <div class="field cart-comment"><label for="comment">Комментарий к заказу</label>
          <textarea id="comment" class="textarea" maxlength="${MAX_COMMENT}" placeholder="Например: без лука, соус отдельно">${esc(cart.comment)}</textarea>
          <p class="small muted">Пожелания носят информационный характер: рецептуру блюд мы не меняем, при необходимости сотрудник свяжется с вами.</p></div>
        <div class="cart-actions"><a class="btn btn--ghost" href="${url("menu.html")}">← Продолжить выбор</a>
          <button type="button" class="btn btn--ghost" id="clear">${confirming ? "Да, очистить корзину" : "Очистить корзину"}</button>${confirming ? `<button type="button" class="btn btn--ghost" id="clear-no">Отмена</button>` : ""}</div></section>
      <aside class="cart-summary card" aria-labelledby="sum-title"><h2 id="sum-title">Итого</h2>
        <dl class="sum"><div><dt>Блюда</dt><dd>${fmtMoney(sub)}</dd></div><div><dt>Доставка</dt><dd>${q.fee ? fmtMoney(q.fee) : "бесплатно"}</dd></div></dl>
        ${freeNote ? `<div class="progress" role="img" aria-label="${esc(freeNote)}"><span style="width:${progress}%"></span></div><p class="small muted">${esc(freeNote)}</p>` : ""}
        ${minWarn}${hasUnavailable ? `<p class="field-error">В корзине есть недоступное блюдо: оно не входит в сумму.</p>` : ""}
        <p class="sum__total"><span>Итого с доставкой</span><strong>${fmtMoney(q.total)}</strong></p>
        <p class="small muted">Если выберете самовывоз, доставка не оплачивается: ${fmtMoney(sub)}. Способ получения выбирается при оформлении.</p>
        <a class="btn btn--primary btn--block" href="${url("checkout.html")}" id="to-checkout">Оформить заказ</a>
        <p class="small muted" id="checkout-note">${esc(data.settings.notices.demoBanner)}: заказ будет демонстрационным.</p></aside></div></div>`;
    if (keepFocus && active) { const c = document.getElementById("comment"); c.focus(); c.setSelectionRange(c.value.length, c.value.length); }
  }
  mount.addEventListener("input", (e) => { if (e.target.id === "comment") saveCart(setComment(loadCart(data.dishesById), e.target.value)); });
  mount.addEventListener("click", (e) => {
    if (e.target.closest("#clear")) { if (!confirming) { confirming = true; draw(); document.getElementById("clear").focus(); } else { confirming = false; saveCart(clearCart()); } }
    if (e.target.closest("#clear-no")) { confirming = false; draw(); document.getElementById("clear").focus(); }
  });
  bindCartActions(mount, data, () => { draw(); const first = mount.querySelector("[data-inc]"); first?.focus(); });
  // изменение в другой вкладке (не комментарий в этой): перерисовать
  onCartChange(() => { if (!(document.activeElement && document.activeElement.id === "comment")) draw(); });
  draw();
}
boot(render, mount);
