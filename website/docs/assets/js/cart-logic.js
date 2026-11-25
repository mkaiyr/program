// Логика корзины без доступа к браузеру: хранятся только id и количество, цена и доступность — всегда из меню.
export const MAX_QTY = 20;
export const MAX_COMMENT = 300;

export const emptyCart = () => ({ items: [], comment: "" });

// Приводит любые данные из хранилища к безопасному виду: чужие id, дубли, дробные и огромные количества отбрасываются или ограничиваются
export function normalizeCart(raw, dishesById) {
  const out = emptyCart();
  if (!raw || typeof raw !== "object") return out;
  const seen = new Set();
  for (const it of Array.isArray(raw.items) ? raw.items : []) {
    if (!it || typeof it.id !== "string" || seen.has(it.id)) continue;
    if (dishesById && !dishesById[it.id]) continue;
    const q = Math.floor(Number(it.qty));
    if (!(q >= 1)) continue;
    seen.add(it.id);
    out.items.push({ id: it.id, qty: Math.min(q, MAX_QTY) });
  }
  out.comment = typeof raw.comment === "string" ? raw.comment.slice(0, MAX_COMMENT) : "";
  return out;
}

export function addItem(cart, id, qty, dishesById) {
  const dish = dishesById[id];
  if (!dish) return { cart, error: "unknown" };
  if (!dish.available) return { cart, error: "unavailable" };
  const q = Math.max(1, Math.floor(Number(qty) || 1));
  const items = cart.items.map((i) => ({ ...i }));
  const cur = items.find((i) => i.id === id);
  if (cur) { if (cur.qty >= MAX_QTY) return { cart, error: "max" }; cur.qty = Math.min(MAX_QTY, cur.qty + q); }
  else items.push({ id, qty: Math.min(MAX_QTY, q) });
  return { cart: { ...cart, items }, error: null };
}

export function setQty(cart, id, qty) {
  const q = Math.floor(Number(qty));
  if (!(q >= 1)) return removeItem(cart, id);
  return { ...cart, items: cart.items.map((i) => (i.id === id ? { ...i, qty: Math.min(MAX_QTY, q) } : i)) };
}

export const removeItem = (cart, id) => ({ ...cart, items: cart.items.filter((i) => i.id !== id) });
export const setComment = (cart, text) => ({ ...cart, comment: String(text ?? "").slice(0, MAX_COMMENT) });
export const clearCart = () => emptyCart();
export const cartCount = (cart) => cart.items.reduce((n, i) => n + i.qty, 0);

// Строки корзины с актуальными ценами; недоступное блюдо помечается и в сумму не входит
export function cartLines(cart, dishesById) {
  return cart.items.filter((i) => dishesById[i.id]).map((i) => {
    const dish = dishesById[i.id], unavailable = !dish.available;
    return { dish, qty: i.qty, unavailable, lineTotal: unavailable ? 0 : dish.price * i.qty };
  });
}
export const cartSubtotal = (lines) => lines.reduce((s, l) => s + l.lineTotal, 0);
