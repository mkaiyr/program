// Расчёт итога заказа. Цены берутся из данных меню, а не из браузера; настройки доставки — из settings.json.
export function orderQuote(subtotal, mode, settings) {
  const del = settings.delivery || {}, pr = settings.pricing || {};
  const sub = Math.max(0, Math.round(subtotal));
  const service = Math.round(sub * (pr.serviceChargePercent || 0) / 100);
  if (mode !== "delivery") return { mode: "pickup", subtotal: sub, fee: 0, service, total: sub + service, minOrder: 0, minOrderOk: true, freeLeft: 0, freeFrom: 0 };
  const free = sub >= (del.freeFrom ?? Infinity);
  const fee = sub === 0 || free ? 0 : (del.fee || 0);
  return { mode: "delivery", subtotal: sub, fee, service, total: sub + fee + service, minOrder: del.minOrder || 0, minOrderOk: sub >= (del.minOrder || 0),
           freeLeft: free ? 0 : Math.max(0, (del.freeFrom || 0) - sub), freeFrom: del.freeFrom || 0 };
}
