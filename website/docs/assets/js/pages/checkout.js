import { url } from "../core.js";
import { esc, fmtMoney, plural } from "../format.js";
import { cartLines, cartSubtotal, clearCart } from "../cart-logic.js";
import { orderQuote } from "../pricing.js";
import { loadCart, saveCart } from "../cart-store.js";
import { validateCheckout, formatPhone, normalizePhone } from "../validation.js";
import { placeOrder, newAttemptId } from "../orders-logic.js";
import { loadOrders, saveOrders, attemptId, resetAttempt } from "../orders-store.js";
import { mountLayout } from "../layout.js";
import { boot } from "./common.js";

mountLayout();
const mount = document.getElementById("checkout");

const field = (id, label, extra = "", hint = "") => `<div class="field"><label for="${id}">${label}</label>${extra}<p class="field-error" id="${id}-err" hidden></p>${hint}</div>`;
const input = (id, attrs = "") => `<input class="input" id="${id}" name="${id}" ${attrs} aria-describedby="${id}-err">`;

function render(data) {
  const S = data.settings, cart0 = loadCart(data.dishesById), lines0 = cartLines(cart0, data.dishesById);
  if (!lines0.length) {
    mount.innerHTML = `<div class="container"><h1 class="page-title">Оформление заказа</h1><div class="state card"><h2>В корзине пока пусто</h2><p class="muted">Чтобы оформить демонстрационный заказ, добавьте блюда из меню.</p><div class="cart-actions"><a class="btn btn--primary" href="${url("menu.html")}">Открыть меню</a><a class="btn btn--ghost" href="${url("orders.html")}">Мои заказы</a></div></div></div>`;
    return;
  }
  const allergenIds = data.allergenList.map((a) => a.id);
  mount.innerHTML = `<div class="container"><h1 class="page-title">Оформление заказа</h1>
    <p class="demo-note" role="note">${esc(S.notices.demoBanner)}. ${esc(S.notices.demoOrder)}</p>
    <div class="cart-layout checkout-layout">
    <form id="checkout-form" novalidate aria-describedby="form-error">
      <div class="alert alert--danger" id="form-error" role="alert" tabindex="-1" hidden></div>
      <fieldset class="fieldset"><legend>Способ получения</legend>
        <div class="choice-row" id="mode-row">
          <label class="choice"><input type="radio" name="mode" value="delivery" checked><span><strong>Доставка</strong><small>${esc(S.delivery.etaText)}</small></span></label>
          <label class="choice"><input type="radio" name="mode" value="pickup"><span><strong>Самовывоз</strong><small>${esc(S.delivery.pickupEtaText)}</small></span></label></div>
        <p class="field-error" id="mode-err" hidden></p></fieldset>
      <fieldset class="fieldset"><legend>Контакты</legend>
        ${field("name", "Имя", input("name", 'autocomplete="given-name" maxlength="60" required'))}
        ${field("phone", "Телефон", input("phone", 'type="tel" inputmode="tel" autocomplete="tel" maxlength="24" placeholder="+7 700 123-45-67" required'))}
        ${field("email", "Электронная почта (необязательно)", input("email", 'type="email" autocomplete="email" maxlength="80"'))}</fieldset>
      <fieldset class="fieldset" id="delivery-block"><legend>Адрес доставки</legend>
        ${field("street", "Улица и дом", input("street", 'autocomplete="street-address" maxlength="120" placeholder="Например: Абая 10"'))}
        <div class="field-row">${field("apt", "Квартира / офис", input("apt", 'maxlength="20"'))}${field("entrance", "Подъезд", input("entrance", 'maxlength="10"'))}${field("floor", "Этаж", input("floor", 'maxlength="10"'))}${field("intercom", "Домофон", input("intercom", 'maxlength="20"'))}</div>
        ${field("courierComment", "Комментарий курьеру", `<textarea class="textarea textarea--sm" id="courierComment" maxlength="200" aria-describedby="courierComment-err"></textarea>`)}</fieldset>
      <fieldset class="fieldset" id="pickup-block" hidden><legend>Самовывоз</legend>
        <p class="small muted">Адрес: ${esc(S.cafe.address)}</p>
        ${field("pickupTime", "Когда заберёте", `<select class="select" id="pickupTime" aria-describedby="pickupTime-err">${S.pickup.timeOptions.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("")}</select>`)}</fieldset>
      <fieldset class="fieldset"><legend>Способ оплаты</legend>
        <div class="choice-col">${S.payments.map((p, i) => `<label class="choice"><input type="radio" name="payment" value="${esc(p.id)}"${i === 0 ? " checked" : ""}><span><strong>${esc(p.name)}</strong><small>${esc(p.hint)}</small></span></label>`).join("")}</div>
        <p class="field-error" id="payment-err" hidden></p>
        <p class="small muted">${esc(S.notices.demoPayment)}</p></fieldset>
      <fieldset class="fieldset"><legend>${esc(S.allergy.question)}</legend>
        <div class="choice-row" id="allergy-row">${S.allergy.options.map((o) => `<label class="choice choice--sm"><input type="radio" name="allergy" value="${esc(o.id)}"><span><strong>${esc(o.name)}</strong></span></label>`).join("")}</div>
        <div id="allergy-detail" hidden><p class="small">Отметьте продукты:</p><div class="check-grid">${data.allergenList.map((a) => `<label class="check"><input type="checkbox" name="allergen" value="${esc(a.id)}"> ${esc(a.name)}</label>`).join("")}</div>
          ${field("allergyOther", "Другое", input("allergyOther", 'maxlength="100"'))}</div>
        <p class="field-error" id="allergy-err" hidden></p>
        <p class="small muted">${esc(S.allergy.crossContaminationNote)}</p></fieldset>
      <div class="field"><label for="comment">Комментарий к заказу</label><textarea class="textarea" id="comment" maxlength="300">${esc(cart0.comment)}</textarea></div>
      <button type="submit" class="btn btn--primary btn--block" id="submit">Оформить демонстрационный заказ</button>
      <p class="small muted">Нажимая кнопку, вы создаёте демонстрационный заказ: он сохранится только в этом браузере. ${esc(S.notices.demoBanner)}.</p>
    </form>
    <aside class="cart-summary card" aria-labelledby="sum-title" id="summary"></aside></div></div>`;

  const form = document.getElementById("checkout-form"), $ = (id) => document.getElementById(id);
  const val = (id) => $(id).value;
  const checked = (name) => form.querySelector(`input[name="${name}"]:checked`)?.value || "";
  const mode = () => checked("mode") || "delivery";
  let busy = false;

  function drawSummary() {
    const cart = loadCart(data.dishesById), lines = cartLines(cart, data.dishesById), sub = cartSubtotal(lines), q = orderQuote(sub, mode(), S);
    const unavailable = lines.filter((l) => l.unavailable), cnt = lines.reduce((n, l) => n + l.qty, 0);
    const free = q.mode === "delivery" ? (q.freeLeft > 0 ? `До бесплатной доставки осталось ${fmtMoney(q.freeLeft)}` : "Доставка бесплатная") : "";
    $("summary").innerHTML = `<h2 id="sum-title">Ваш заказ</h2>
      <ul class="sum-lines">${lines.map((l) => `<li class="${l.unavailable ? "is-unavailable" : ""}"><span>${esc(l.dish.name)} × ${l.qty}${l.unavailable ? ' <em class="field-error">недоступно</em>' : ""}</span><span>${l.unavailable ? "—" : fmtMoney(l.lineTotal)}</span></li>`).join("")}</ul>
      <p class="small muted">${cnt} ${plural(cnt, "позиция", "позиции", "позиций")} · <a href="${url("cart.html")}">изменить в корзине</a></p>
      <dl class="sum"><div><dt>Блюда</dt><dd data-sum="subtotal">${fmtMoney(q.subtotal)}</dd></div><div><dt>${q.mode === "delivery" ? "Доставка" : "Самовывоз"}</dt><dd data-sum="fee">${q.fee ? fmtMoney(q.fee) : q.mode === "delivery" ? "бесплатно" : "без доставки"}</dd></div></dl>
      ${free ? `<p class="small muted" data-sum="free">${esc(free)}</p>` : ""}
      ${unavailable.length ? `<p class="field-error" id="unavailable-note" role="alert">Недоступно: ${unavailable.map((l) => esc(l.dish.name)).join(", ")}. Уберите это блюдо в корзине, чтобы оформить заказ.</p>` : ""}
      ${q.mode === "delivery" && !q.minOrderOk && sub > 0 ? `<p class="field-error" id="min-note" role="alert">Минимальный заказ для доставки — ${fmtMoney(q.minOrder)}. Добавьте блюд на ${fmtMoney(q.minOrder - sub)} или выберите самовывоз.</p>` : ""}
      <p class="sum__total"><span>Итого</span><strong data-sum="total">${fmtMoney(q.total)}</strong></p>`;
  }
  function syncMode() {
    const d = mode() === "delivery";
    $("delivery-block").hidden = !d; $("pickup-block").hidden = d; drawSummary();
  }
  function setErr(id, msg) {
    const e = $(id + "-err"); if (!e) return;
    e.hidden = !msg; e.textContent = msg || "";
    const el = $(id); if (el) { if (msg) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid"); }
  }
  const FIELDS = ["mode", "name", "phone", "email", "street", "pickupTime", "payment", "allergy"];
  const ORDER = ["mode", "name", "phone", "email", "street", "pickupTime", "payment", "allergy"];
  function collect() {
    return { mode: checked("mode"), name: val("name"), phone: val("phone"), email: val("email"), street: val("street"), apt: val("apt"), entrance: val("entrance"), floor: val("floor"), intercom: val("intercom"),
      courierComment: val("courierComment"), pickupTime: val("pickupTime"), payment: checked("payment"), comment: val("comment"),
      allergy: { answer: checked("allergy"), allergens: [...form.querySelectorAll('input[name="allergen"]:checked')].map((c) => c.value), other: val("allergyOther") } };
  }
  function showErrors(errors) {
    for (const f of FIELDS) setErr(f, errors[f]);
    const first = ORDER.find((f) => errors[f]);
    const box = $("form-error");
    if (first) { box.hidden = false; box.textContent = "Проверьте форму: " + Object.keys(errors).length + " " + plural(Object.keys(errors).length, "поле заполнено неверно", "поля заполнены неверно", "полей заполнено неверно") + "."; }
    else { box.hidden = true; box.textContent = ""; }
    if (first) { const t = first === "mode" || first === "payment" || first === "allergy" ? form.querySelector(`input[name="${first}"]`) : $(first); t?.focus(); }
  }
  function failBox(text) { const b = $("form-error"); b.hidden = false; b.textContent = text; b.focus(); }
  function setBusy(v) { busy = v; const b = $("submit"); b.disabled = v; b.textContent = v ? "Оформляем…" : "Оформить демонстрационный заказ"; }

  form.addEventListener("change", (e) => {
    if (e.target.name === "mode") syncMode();
    if (e.target.name === "allergy") $("allergy-detail").hidden = e.target.value !== "yes";
    if (e.target.name === "payment" || e.target.name === "allergy" || e.target.name === "mode") setErr(e.target.name, "");
  });
  $("phone").addEventListener("blur", () => { const d = normalizePhone(val("phone")); if (d) $("phone").value = formatPhone(d); });
  form.addEventListener("input", (e) => { if (e.target.id && $(e.target.id + "-err") && !$(e.target.id + "-err").hidden) setErr(e.target.id, ""); });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (busy) return;                       // повторное нажатие: пока заказ оформляется, второе событие игнорируется
    setBusy(true);
    try {
      const cart = loadCart(data.dishesById), values = collect();
      const r = placeOrder({ cart, form: values, attemptId: attemptId(newAttemptId), dishesById: data.dishesById, settings: S, ingredients: data.ingredients, allergenList: data.allergenList, orders: loadOrders() });
      if (!r.ok) {
        setBusy(false);
        if (r.error === "invalid") { showErrors(r.errors); return; }
        showErrors({});
        if (r.error === "empty") { render(data); return; }
        if (r.error === "unavailable") { drawSummary(); failBox("В корзине есть недоступное блюдо. Уберите его в корзине и повторите оформление."); return; }
        if (r.error === "min_order") { drawSummary(); failBox(`Минимальный заказ для доставки — ${fmtMoney(r.minOrder)}. Добавьте блюда или выберите самовывоз.`); return; }
        failBox("Не удалось оформить заказ. Повторите попытку."); return;
      }
      if (!r.duplicate && !saveOrders(r.orders)) { setBusy(false); failBox("Не удалось сохранить заказ в браузере (возможно, хранилище отключено или переполнено). Заказ не оформлен, корзина не изменена."); return; }
      saveCart(clearCart());                // корзина очищается только после того, как заказ реально сохранён
      resetAttempt();
      location.assign(url(r.order.payment.method === "online" && r.order.payment.status !== "paid" ? "payment.html" : "order.html") + "?n=" + encodeURIComponent(r.order.number) + (r.duplicate ? "" : "&new=1"));
    } catch (err) { setBusy(false); failBox("Не удалось оформить заказ из-за непредвиденной ошибки. Корзина не изменена."); console.error(err); }
  });
  syncMode();
}
boot(render, mount);
