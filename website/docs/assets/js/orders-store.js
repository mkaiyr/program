import { normalizeOrders } from "./orders-logic.js";

// Заказы в браузере. В отличие от корзины запасной памяти нет: если запись не удалась, заказ не считается сохранённым.
export const ORDERS_KEY = "bistro.orders.v1";
const ATTEMPT_KEY = "bistro.attempt.v1";

export function loadOrders() {
  try { const v = localStorage.getItem(ORDERS_KEY); return v == null ? [] : normalizeOrders(JSON.parse(v)); } catch (e) { return []; }
}
export function ordersState() {   // "ok" | "empty" | "corrupt" — чтобы страница сказала правду, если хранилище испорчено
  try {
    const v = localStorage.getItem(ORDERS_KEY); if (v == null) return "empty";
    const raw = JSON.parse(v); if (!Array.isArray(raw)) return "corrupt";
    return normalizeOrders(raw).length < raw.length ? "corrupt" : raw.length ? "ok" : "empty";
  } catch (e) { return "corrupt"; }
}
// Записывает и сразу перечитывает: true только если заказы действительно лежат в хранилище
export function saveOrders(list) {
  try {
    const s = JSON.stringify(list); localStorage.setItem(ORDERS_KEY, s);
    return localStorage.getItem(ORDERS_KEY) === s;
  } catch (e) { return false; }
}
export const findOrder = (number) => loadOrders().find((o) => o.number === number) || null;
export function updateOrder(order) {
  const list = loadOrders(), i = list.findIndex((o) => o.number === order.number);
  if (i < 0) return false;
  list[i] = order; return saveOrders(list);
}
// Идентификатор попытки живёт в sessionStorage: обновление страницы или «назад» не создаст второй заказ
export function attemptId(make) {
  try { let a = sessionStorage.getItem(ATTEMPT_KEY); if (!a) { a = make(); sessionStorage.setItem(ATTEMPT_KEY, a); } return a; } catch (e) { return make(); }
}
export const resetAttempt = () => { try { sessionStorage.removeItem(ATTEMPT_KEY); } catch (e) { /* не страшно */ } };
