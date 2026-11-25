import { readJSON, writeJSON } from "./store.js";
import { normalizeCart, cartCount } from "./cart-logic.js";

// Корзина в браузере: ключ с версией, содержимое всегда проходит нормализацию. Меняется в одной вкладке — обновляется во всех.
export const CART_KEY = "bistro.cart.v1";
const listeners = new Set();
let storageBound = false;

export const loadCart = (dishesById) => normalizeCart(readJSON(CART_KEY), dishesById || null);
export function saveCart(cart) { writeJSON(CART_KEY, cart); for (const fn of listeners) fn(cart); }
export const cartQtyMap = (cart) => Object.fromEntries(cart.items.map((i) => [i.id, i.qty]));
export const rawCartCount = () => cartCount(loadCart(null));

export function onCartChange(fn) {
  listeners.add(fn);
  if (!storageBound) {
    storageBound = true;
    window.addEventListener("storage", (e) => { if (e.key === CART_KEY) { const c = loadCart(null); for (const f of listeners) f(c); } });
  }
  return () => listeners.delete(fn);
}
