import { addItem, setQty, removeItem, MAX_QTY } from "./cart-logic.js";
import { loadCart, saveCart } from "./cart-store.js";
import { toast } from "./toast.js";

// Общие обработчики кнопок корзины (делегирование событий): data-add, data-inc, data-dec, data-remove
export function bindCartActions(container, data, afterChange) {
  container.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-add],[data-inc],[data-dec],[data-remove]");
    if (!btn || !container.contains(btn)) return;
    const id = btn.dataset.add || btn.dataset.inc || btn.dataset.dec || btn.dataset.remove;
    const cart = loadCart(data.dishesById), cur = cart.items.find((i) => i.id === id), name = data.dishesById[id]?.name || "";
    if ("add" in btn.dataset || "inc" in btn.dataset) {
      const r = addItem(cart, id, 1, data.dishesById);
      if (r.error === "max") return toast("Не больше " + MAX_QTY + " порций одного блюда", "warn");
      if (r.error) { toast("Это блюдо сейчас недоступно", "warn"); return afterChange(id, cart); }
      saveCart(r.cart); if ("add" in btn.dataset) toast("Добавлено в корзину: " + name);
      return afterChange(id, r.cart);
    }
    if ("dec" in btn.dataset) { const next = setQty(cart, id, (cur ? cur.qty : 0) - 1); saveCart(next); return afterChange(id, next); }
    if ("remove" in btn.dataset) { const next = removeItem(cart, id); saveCart(next); toast("Удалено: " + name); return afterChange(id, next); }
  });
}

// Перерисовывает одну карточку и возвращает фокус на её кнопку (клавиатура не «теряется»)
export function refreshCard(container, dish, renderCard, ctx) {
  const old = container.querySelector(`[data-dish="${CSS.escape(dish.id)}"]`);
  if (!old) return;
  const hadFocus = old.contains(document.activeElement);
  const tpl = document.createElement("template"); tpl.innerHTML = renderCard(dish, ctx).trim(); old.replaceWith(tpl.content.firstElementChild);
  if (hadFocus) { const n = container.querySelector(`[data-dish="${CSS.escape(dish.id)}"]`); (n.querySelector("[data-inc]") || n.querySelector("[data-add]") || n.querySelector("a"))?.focus(); }
}
