suite("orders");
const S2 = DATA.settings, D = DATA.dishesById;
const okForm = { mode: "delivery", name: "Айгерим", phone: "+7 701 234-56-78", street: "Абая 10", payment: "cash", allergy: { answer: "no" } };
const price = (id) => D[id].price;
const cartOf = (...p) => ({ items: p.map(([id, qty]) => ({ id, qty })), comment: "" });
const place = (o) => placeOrder({ cart: cartOf(["cheeseburger", 2]), form: okForm, attemptId: "att1", dishesById: D, settings: S2, ingredients: DATA.ingredients, allergenList: DATA.allergenList, orders: [], ...o });
const NOW = new Date(2026, 10, 18, 12, 0, 0);
test("доставка: снимок цен, сумма и стоимость доставки", () => {
  const sub = price("cheeseburger") * 2, r = place({ now: NOW });
  assert.ok(r.ok); const o = r.order;
  assert.equal(o.items[0].price, price("cheeseburger")); assert.equal(o.items[0].qty, 2); assert.equal(o.items[0].lineTotal, sub);
  assert.equal(o.totals.subtotal, sub);
  const q = orderQuote(sub, "delivery", S2); assert.equal(o.totals.fee, q.fee); assert.equal(o.totals.total, sub + q.fee);
  assert.equal(o.demo, true); assert.equal(o.status, "new"); assert.equal(o.payment.status, "awaiting"); assert.equal(o.delivery.street, "Абая 10");
  assert.ok(/^BS-261118-\d{4}$/.test(o.number), o.number);
});
test("самовывоз: без доставки и без адреса, минимальной суммы нет", () => {
  const r = place({ cart: cartOf(["cheeseburger", 1]), form: { ...okForm, mode: "pickup", street: "", pickupTime: "30" } });
  assert.ok(r.ok, JSON.stringify(r)); assert.equal(r.order.totals.fee, 0); assert.equal(r.order.delivery, null); assert.equal(r.order.pickupTime, "30");
});
test("бесплатная доставка от порога", () => {
  const n = Math.ceil(S2.delivery.freeFrom / price("cheeseburger")), r = place({ cart: cartOf(["cheeseburger", Math.min(n, 20)]) });
  if (price("cheeseburger") * Math.min(n, 20) >= S2.delivery.freeFrom) assert.equal(r.order.totals.fee, 0); else assert.ok(false, "порог недостижим в тесте");
});
test("ниже минимального заказа доставка отклоняется, самовывоз нет", () => {
  const cheap = DATA.menu.dishes.filter((d) => d.available).sort((a, b) => a.price - b.price)[0];
  if (cheap.price * 1 >= S2.delivery.minOrder) return;
  const r = place({ cart: cartOf([cheap.id, 1]) }); assert.equal(r.ok, false); assert.equal(r.error, "min_order");
  assert.ok(place({ cart: cartOf([cheap.id, 1]), form: { ...okForm, mode: "pickup", street: "", pickupTime: "asap" } }).ok);
});
test("пустая корзина и недоступное блюдо", () => {
  assert.equal(place({ cart: cartOf() }).error, "empty");
  const r = place({ cart: cartOf(["cheeseburger", 1], ["onion-rings", 1]) }); assert.equal(r.error, "unavailable"); assert.deepEqual(r.unavailable, ["onion-rings"]);
});
test("неверная форма: ошибки по полям, заказ не создаётся", () => {
  const r = place({ form: { ...okForm, phone: "123", name: "" } }); assert.equal(r.error, "invalid"); assert.ok(r.errors.phone && r.errors.name); assert.equal(r.order, undefined);
});
test("двойной клик: тот же attemptId возвращает тот же заказ", () => {
  const a = place({}), b = place({ orders: a.orders });
  assert.ok(b.ok && b.duplicate); assert.equal(b.order.number, a.order.number);
});
test("дубликат ищется раньше проверок: заказ уже создан, корзина могла опустеть", () => {
  const a = place({}), b = place({ orders: a.orders, cart: cartOf() }); assert.ok(b.ok && b.duplicate);
});
test("номера заказов уникальны даже при одинаковом генераторе", () => {
  let orders = []; const seen = new Set();
  for (let i = 0; i < 30; i++) { const r = place({ attemptId: "x" + i, orders, rand: () => 0.5 }); assert.ok(r.ok); assert.ok(!seen.has(r.order.number)); seen.add(r.order.number); orders = r.orders; }
});
test("цена в заказе не меняется при изменении меню", () => {
  const o = place({}).order, changed = { ...D, cheeseburger: { ...D.cheeseburger, price: 99999 } };
  assert.equal(o.items[0].price, price("cheeseburger")); assert.equal(placeOrder({ cart: cartOf(["cheeseburger", 2]), form: okForm, attemptId: "n", dishesById: changed, settings: S2, orders: [] }).order.items[0].price, 99999);
});
test("конфликт аллергии: блюдо с молоком при аллергии на молоко", () => {
  const r = place({ form: { ...okForm, allergy: { answer: "yes", allergens: ["milk"] } } });
  assert.ok(r.ok); assert.ok(r.order.allergyConflicts.length >= 1); assert.deepEqual(r.order.allergyConflicts[0].allergens, ["milk"]);
});
test("онлайн-оплата: успех, отказ, повтор после отказа, нельзя оплатить дважды", () => {
  const o = place({ form: { ...okForm, payment: "online" } }).order;
  const f = applyPayment(o, "failure"); assert.equal(f.order.payment.status, "failed"); assert.equal(f.order.payment.attempts, 1); assert.ok(needsPayment(f.order));
  const s = applyPayment(f.order, "success"); assert.equal(s.order.payment.status, "paid"); assert.equal(s.order.payment.attempts, 2); assert.ok(!needsPayment(s.order));
  const again = applyPayment(s.order, "failure"); assert.equal(again.ok, false); assert.equal(again.order.payment.status, "paid");
  assert.equal(applyPayment(o, "weird").ok, false);
});
test("оплата при получении не проходит через имитацию и статус «оплачено» не получает", () => {
  for (const m of ["cash", "card_on_delivery"]) { const o = place({ form: { ...okForm, payment: m } }).order; assert.equal(applyPayment(o, "success").ok, false); assert.equal(o.payment.status, "awaiting"); }
});
test("смена способа оплаты после отказа", () => {
  const o = applyPayment(place({ form: { ...okForm, payment: "online" } }).order, "failure").order, r = switchPaymentMethod(o, "cash", S2);
  assert.ok(r.ok); assert.equal(r.order.payment.method, "cash"); assert.equal(r.order.payment.status, "awaiting");
  assert.equal(switchPaymentMethod(r.order, "nope", S2).ok, false);
  const paid = applyPayment(place({ form: { ...okForm, payment: "online" } }).order, "success").order; assert.equal(switchPaymentMethod(paid, "cash", S2).ok, false);
});
test("шкала статусов: самовывоз без «передан курьеру»", () => {
  const d = place({}).order, p = place({ form: { ...okForm, mode: "pickup", street: "", pickupTime: "asap" } }).order;
  assert.ok(orderTimeline(d, S2).some((s) => s.id === "out_for_delivery")); assert.ok(!orderTimeline(p, S2).some((s) => s.id === "out_for_delivery"));
  assert.equal(orderTimeline(d, S2)[0].state, "current");
});
test("повреждённое хранилище заказов отфильтровывается", () => {
  const good = place({}).order;
  assert.deepEqual(normalizeOrders(null), []); assert.deepEqual(normalizeOrders("мусор"), []); assert.deepEqual(normalizeOrders({}), []);
  const tampered = { ...good, totals: { ...good.totals, total: 1 } }, noItems = { ...good, number: "BS-261118-1111", items: [] }, bad = { ...good, number: "BS-261118-2222", items: [{ name: "x", price: -5, qty: 1, lineTotal: -5 }] };
  const r = normalizeOrders([null, 5, "x", {}, tampered, noItems, bad, good, good]);
  assert.equal(r.length, 1); assert.equal(r[0].number, good.number);
});
