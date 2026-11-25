// Демонстрационные заказы без DOM. Цены и названия фиксируются в момент оформления; статусы условные — действий сотрудников не имитируются.
import { validateCheckout } from "./validation.js";
import { cartLines } from "./cart-logic.js";
import { orderQuote } from "./pricing.js";
import { dishAllergens } from "./allergens.js";

export const ORDER_VERSION = 1;
export const newAttemptId = (rand = Math.random) => "a" + Math.floor(rand() * 1e12).toString(36) + Date.now().toString(36);

export function makeOrderNumber(existing, now = new Date(), rand = Math.random) {
  const used = new Set((existing || []).map((o) => o.number));
  const p = (n) => String(n).padStart(2, "0");
  const day = `${p(now.getFullYear() % 100)}${p(now.getMonth() + 1)}${p(now.getDate())}`;
  for (let i = 0; i < 200; i++) {
    const n = `BS-${day}-${String(Math.floor(rand() * 9000) + 1000)}`;
    if (!used.has(n)) return n;
  }
  for (let k = 1000; k < 10000; k++) { const n = `BS-${day}-${k}`; if (!used.has(n)) return n; }
  return `BS-${day}-${Date.now()}`;
}

// Оформление заказа. Дубликат по attemptId возвращает уже созданный заказ — двойной клик не создаёт второй.
export function placeOrder({ cart, form, attemptId, dishesById, settings, ingredients, allergenList, orders, now = new Date(), rand = Math.random }) {
  const list = Array.isArray(orders) ? orders : [];
  if (attemptId) { const dup = list.find((o) => o.attemptId === attemptId); if (dup) return { ok: true, duplicate: true, order: dup }; }
  const lines = cartLines(cart || { items: [] }, dishesById);
  if (!lines.length) return { ok: false, error: "empty" };
  const unavailable = lines.filter((l) => l.unavailable).map((l) => l.dish.id);
  if (unavailable.length) return { ok: false, error: "unavailable", unavailable };
  const chk = validateCheckout(form, settings, (allergenList || []).map((a) => a.id));
  if (!chk.ok) return { ok: false, error: "invalid", errors: chk.errors };
  const v = chk.values, subtotal = lines.reduce((s, l) => s + l.lineTotal, 0), q = orderQuote(subtotal, v.mode, settings);
  if (!q.minOrderOk) return { ok: false, error: "min_order", minOrder: q.minOrder, subtotal };
  const items = lines.map((l) => ({ id: l.dish.id, name: l.dish.name, price: l.dish.price, qty: l.qty, lineTotal: l.dish.price * l.qty }));
  // аллергены, заявленные клиентом, и блюда заказа, которые их содержат или могут содержать
  const conflicts = [];
  if (v.allergy.answer === "yes" && ingredients) {
    for (const l of lines) {
      const a = dishAllergens(l.dish, ingredients), hit = v.allergy.allergens.filter((x) => a.contains.includes(x) || a.may.includes(x));
      if (hit.length) conflicts.push({ dishId: l.dish.id, name: l.dish.name, allergens: hit });
    }
  }
  const method = (settings.payments || []).find((p) => p.id === v.payment);
  const iso = now.toISOString();
  const order = {
    version: ORDER_VERSION, attemptId: attemptId || null, number: makeOrderNumber(list, now, rand), createdAt: iso, updatedAt: iso,
    demo: true, status: "new", mode: v.mode,
    customer: { name: v.name, phone: v.phone, email: v.email },
    delivery: v.mode === "delivery" ? { street: v.street, apt: v.apt, entrance: v.entrance, floor: v.floor, intercom: v.intercom, courierComment: v.courierComment } : null,
    pickupTime: v.mode === "pickup" ? v.pickupTime : null,
    comment: v.comment, allergy: v.allergy, allergyConflicts: conflicts,
    items, totals: { subtotal: q.subtotal, fee: q.fee, service: q.service, total: q.total },
    payment: { method: v.payment, methodName: method ? method.name : v.payment, status: "awaiting", attempts: 0 },
  };
  return { ok: true, order, orders: [order, ...list] };
}

// Имитация онлайн-оплаты: результат задаёт посетитель, никаких данных карты нет. Оплаченный заказ повторно не оплачивается.
export function applyPayment(order, outcome, now = new Date()) {
  if (!order || order.payment.method !== "online") return { ok: false, error: "not_online", order };
  if (order.payment.status === "paid") return { ok: false, error: "already_paid", order };
  if (outcome !== "success" && outcome !== "failure") return { ok: false, error: "bad_outcome", order };
  const payment = { ...order.payment, attempts: order.payment.attempts + 1, status: outcome === "success" ? "paid" : "failed" };
  return { ok: true, order: { ...order, payment, updatedAt: now.toISOString() } };
}

// После отказа можно выбрать другой способ оплаты; оплаченный заказ менять нельзя
export function switchPaymentMethod(order, methodId, settings, now = new Date()) {
  const m = (settings.payments || []).find((p) => p.id === methodId);
  if (!m) return { ok: false, error: "unknown_method", order };
  if (order.payment.status === "paid") return { ok: false, error: "already_paid", order };
  return { ok: true, order: { ...order, payment: { ...order.payment, method: m.id, methodName: m.name, status: "awaiting" }, updatedAt: now.toISOString() } };
}

export const needsPayment = (o) => o.payment.method === "online" && o.payment.status !== "paid";

// Условная шкала статусов для страницы заказа; текущий отмечен, действий сотрудников нет
export function orderTimeline(order, settings) {
  const steps = (settings.orderStatuses || []).filter((s) => !s.terminal && (!s.deliveryOnly || order.mode === "delivery"));
  const idx = Math.max(0, steps.findIndex((s) => s.id === order.status));
  return steps.map((s, i) => ({ id: s.id, name: s.name, state: i < idx ? "done" : i === idx ? "current" : "todo" }));
}

// Заказ из хранилища: всё, что не похоже на заказ, отбрасывается; числа пересчитываются, а не берутся на веру
export function validateStoredOrder(o) {
  if (!o || typeof o !== "object" || o.demo !== true) return false;
  if (typeof o.number !== "string" || !/^BS-\d{6}-\d{4,}$/.test(o.number)) return false;
  if (o.mode !== "delivery" && o.mode !== "pickup") return false;
  if (!o.customer || typeof o.customer.name !== "string" || !o.payment || typeof o.payment.method !== "string" || typeof o.payment.status !== "string") return false;
  if (!Array.isArray(o.items) || !o.items.length || !o.totals) return false;
  let sum = 0;
  for (const i of o.items) {
    if (!i || typeof i.name !== "string" || !Number.isInteger(i.price) || i.price < 0 || !Number.isInteger(i.qty) || i.qty < 1 || i.lineTotal !== i.price * i.qty) return false;
    sum += i.lineTotal;
  }
  const t = o.totals;
  return t.subtotal === sum && Number.isInteger(t.fee) && Number.isInteger(t.service) && t.total === t.subtotal + t.fee + t.service;
}
export function normalizeOrders(raw) {
  const list = Array.isArray(raw) ? raw : [], seen = new Set(), out = [];
  for (const o of list) { if (validateStoredOrder(o) && !seen.has(o.number)) { seen.add(o.number); out.push(o); } }
  return out.slice(0, 50);
}
