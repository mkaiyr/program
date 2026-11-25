// Логика демонстрационной админки без DOM. Статусы берутся из settings.orderStatuses (те же, что на сайте), своей системы статусов нет.
import { normText } from "./format.js";

// «Луковые кольца» сохраняют исходное состояние до отдельного решения: ни переопределение из браузера, ни админка его не меняют
export const LOCKED_AVAILABILITY = ["onion-rings"];

// Переопределения доступности: { id: true|false } только для известных блюд и только если они отличаются от исходных данных
export function normalizeOverrides(raw, baseById) {
  const out = {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
  for (const [id, v] of Object.entries(raw)) {
    if (typeof v !== "boolean" || LOCKED_AVAILABILITY.includes(id)) continue;
    if (baseById && (!baseById[id] || baseById[id].available === v)) continue;
    out[id] = v;
  }
  return out;
}
export const applyAvailability = (dishes, overrides) => dishes.map((d) => (Object.prototype.hasOwnProperty.call(overrides, d.id) ? { ...d, available: overrides[d.id] } : d));
export function setAvailability(overrides, id, value, baseById) {
  if (!baseById[id]) return { ok: false, error: "unknown", overrides };
  if (LOCKED_AVAILABILITY.includes(id)) return { ok: false, error: "locked", overrides };
  const next = { ...overrides };
  if (baseById[id].available === !!value) delete next[id]; else next[id] = !!value;
  return { ok: true, overrides: next };
}

// ---------- заказы ----------
// Поиск по номеру: регистр, «BS-» и пробелы не важны; ищется любая часть номера
const numKey = (s) => String(s ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
export function filterOrders(orders, { query = "", status = "all" } = {}) {
  const k = numKey(query);
  return orders.filter((o) => (status === "all" || o.status === status) && (!k || numKey(o.number).includes(k)));
}
export const sortOrders = (orders, dir = "desc") => [...orders].sort((a, b) => (dir === "asc" ? 1 : -1) * (Date.parse(a.createdAt) - Date.parse(b.createdAt) || a.number.localeCompare(b.number)));

// Допустимые следующие статусы: только вперёд по шагам заказа (для самовывоза без «передан курьеру») или отмена. Выполненный и отменённый заказ не меняются.
export function allowedStatuses(order, settings) {
  if (order.status === "completed" || order.status === "cancelled") return [];
  const steps = (settings.orderStatuses || []).filter((s) => !s.terminal && (!s.deliveryOnly || order.mode === "delivery")).map((s) => s.id);
  const i = steps.indexOf(order.status);
  return [...steps.slice(i + 1), "cancelled"];
}
export function changeStatus(order, next, settings, now = new Date()) {
  if (!allowedStatuses(order, settings).includes(next)) return { ok: false, error: "not_allowed", order };
  const pay = { ...order.payment };
  if (next === "cancelled" && pay.status === "paid") pay.status = "refunded";                                              // демонстрационный возврат
  if (next === "completed" && pay.method !== "online" && pay.status === "awaiting") pay.status = "paid";                   // оплата при получении
  const at = now.toISOString();
  return { ok: true, order: { ...order, status: next, payment: pay, updatedAt: at, history: [...(Array.isArray(order.history) ? order.history : []), { status: next, at }] } };
}
export const cancelOrder = (order, settings, now) => changeStatus(order, "cancelled", settings, now);

// Показатели считаются только из сохранённых заказов. Сумма — по неотменённым заказам.
export function dashboardStats(orders) {
  const n = (s) => orders.filter((o) => o.status === s).length;
  return { total: orders.length, new: n("new"), cancelled: n("cancelled"), completed: n("completed"),
           inProgress: orders.filter((o) => !["new", "cancelled", "completed"].includes(o.status)).length,
           sum: orders.filter((o) => o.status !== "cancelled").reduce((s, o) => s + o.totals.total, 0) };
}
export const recentOrders = (orders, n = 5) => sortOrders(orders, "desc").slice(0, n);

// ---------- блюда ----------
export function filterDishes(dishes, { query = "", category = "all", availability = "all" } = {}) {
  const k = normText(query);
  return dishes.filter((d) => (category === "all" || d.category === category) && (availability === "all" || (availability === "available") === !!d.available) && (!k || normText(d.name).includes(k)));
}
