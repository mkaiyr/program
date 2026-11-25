suite("admin");
const SA = DATA.settings;
const mk = (n, o = {}) => ({ number: n, createdAt: "2026-11-18T10:00:00.000Z", status: "new", mode: "delivery", payment: { method: "cash", methodName: "Наличными при получении", status: "awaiting", attempts: 0 }, totals: { subtotal: 4580, fee: 500, service: 0, total: 5080 }, ...o });
test("поиск по номеру: часть номера, регистр, префикс", () => {
  const l = [mk("BS-261118-1234"), mk("BS-261118-5678")];
  assert.equal(filterOrders(l, { query: "1234" }).length, 1); assert.equal(filterOrders(l, { query: "bs-261118-5678" }).length, 1);
  assert.equal(filterOrders(l, { query: "bs 261118" }).length, 2); assert.equal(filterOrders(l, { query: "9999" }).length, 0); assert.equal(filterOrders(l, { query: "" }).length, 2);
});
test("фильтр по статусу", () => {
  const l = [mk("BS-261118-1111"), mk("BS-261118-2222", { status: "cancelled" })];
  assert.deepEqual(filterOrders(l, { status: "cancelled" }).map((o) => o.number), ["BS-261118-2222"]); assert.equal(filterOrders(l, { status: "all" }).length, 2); assert.equal(filterOrders(l, { status: "ready" }).length, 0);
});
test("сортировка по времени в обе стороны, исходный массив не меняется", () => {
  const l = [mk("BS-261118-1111", { createdAt: "2026-11-18T09:00:00Z" }), mk("BS-261118-2222", { createdAt: "2026-11-18T11:00:00Z" }), mk("BS-261118-3333", { createdAt: "2026-11-18T10:00:00Z" })];
  assert.deepEqual(sortOrders(l, "desc").map((o) => o.number.slice(-4)), ["2222", "3333", "1111"]); assert.deepEqual(sortOrders(l, "asc").map((o) => o.number.slice(-4)), ["1111", "3333", "2222"]); assert.equal(l[0].number, "BS-261118-1111");
});
test("статусы: только вперёд, самовывоз без «передан курьеру», конечные неизменны", () => {
  assert.deepEqual(allowedStatuses(mk("a"), SA), ["accepted", "preparing", "ready", "out_for_delivery", "completed", "cancelled"]);
  assert.deepEqual(allowedStatuses(mk("a", { mode: "pickup" }), SA), ["accepted", "preparing", "ready", "completed", "cancelled"]);
  assert.deepEqual(allowedStatuses(mk("a", { status: "preparing" }), SA), ["ready", "out_for_delivery", "completed", "cancelled"]);
  assert.deepEqual(allowedStatuses(mk("a", { status: "completed" }), SA), []); assert.deepEqual(allowedStatuses(mk("a", { status: "cancelled" }), SA), []);
  for (const s of allowedStatuses(mk("a"), SA)) assert.ok(SA.orderStatuses.some((x) => x.id === s), s);
});
test("смена статуса: история, время, запрет перехода назад и из конечного", () => {
  const now = new Date("2026-11-18T12:00:00Z"), r = changeStatus(mk("a"), "accepted", SA, now);
  assert.ok(r.ok); assert.equal(r.order.status, "accepted"); assert.equal(r.order.updatedAt, now.toISOString()); assert.deepEqual(r.order.history, [{ status: "accepted", at: now.toISOString() }]);
  assert.equal(changeStatus(r.order, "new", SA).ok, false); assert.equal(changeStatus(mk("a", { mode: "pickup" }), "out_for_delivery", SA).ok, false); assert.equal(changeStatus(mk("a"), "бред", SA).ok, false);
  const c = cancelOrder(mk("a"), SA); assert.ok(c.ok); assert.equal(c.order.status, "cancelled"); assert.equal(changeStatus(c.order, "accepted", SA).ok, false); assert.equal(cancelOrder(c.order, SA).ok, false);
});
test("оплата при смене статуса: возврат при отмене оплаченного, оплата при выдаче наличных", () => {
  const paid = mk("a", { payment: { method: "online", methodName: "Онлайн-оплата", status: "paid", attempts: 1 } });
  assert.equal(cancelOrder(paid, SA).order.payment.status, "refunded"); assert.equal(cancelOrder(mk("a"), SA).order.payment.status, "awaiting");
  assert.equal(changeStatus(mk("a", { status: "ready" }), "completed", SA).order.payment.status, "paid");
  assert.equal(changeStatus({ ...paid, status: "ready" }, "completed", SA).order.payment.status, "paid");
  const unpaid = mk("a", { status: "ready", payment: { method: "online", methodName: "Онлайн-оплата", status: "failed", attempts: 1 } }); assert.equal(changeStatus(unpaid, "completed", SA).order.payment.status, "failed");
});
test("исходный заказ при смене статуса не мутируется", () => { const o = mk("a"); changeStatus(o, "accepted", SA); assert.equal(o.status, "new"); assert.equal(o.history, undefined); });
test("dashboard: пусто — нули; считает только реальные заказы; сумма без отменённых", () => {
  assert.deepEqual(dashboardStats([]), { total: 0, new: 0, cancelled: 0, completed: 0, inProgress: 0, sum: 0 });
  const s = dashboardStats([mk("a"), mk("b", { status: "cancelled" }), mk("c", { status: "completed", totals: { subtotal: 1000, fee: 0, service: 0, total: 1000 } }), mk("d", { status: "preparing" })]);
  assert.deepEqual(s, { total: 4, new: 1, cancelled: 1, completed: 1, inProgress: 1, sum: 5080 + 1000 + 5080 });
});
test("последние заказы: новые первыми, не больше n", () => {
  const l = Array.from({ length: 8 }, (_, i) => mk("BS-261118-" + (1000 + i), { createdAt: `2026-11-18T${String(10 + i).padStart(2, "0")}:00:00Z` }));
  const r = recentOrders(l, 5); assert.equal(r.length, 5); assert.equal(r[0].number, "BS-261118-1007");
});
test("доступность: переопределения только для известных блюд, «луковые кольца» зафиксированы", () => {
  const B = DATA.dishesById;
  assert.deepEqual(normalizeOverrides({ fries: false, "onion-rings": true, nope: false, cheeseburger: true, caesar: "yes" }, B), { fries: false });   // cheeseburger уже доступен → не переопределение
  assert.deepEqual(normalizeOverrides(null, B), {}); assert.deepEqual(normalizeOverrides([1], B), {}); assert.deepEqual(normalizeOverrides("x", B), {});
  assert.equal(setAvailability({}, "onion-rings", true, B).error, "locked"); assert.equal(setAvailability({}, "nope", true, B).error, "unknown");
  const r = setAvailability({}, "fries", false, B); assert.deepEqual(r.overrides, { fries: false }); assert.deepEqual(setAvailability(r.overrides, "fries", true, B).overrides, {});
});
test("applyAvailability меняет только выбранные блюда и не трогает цены, составы и аллергены", () => {
  const d = DATA.menu.dishes, a = applyAvailability(d, { fries: false });
  assert.equal(a.find((x) => x.id === "fries").available, false); assert.equal(a.find((x) => x.id === "onion-rings").available, false); assert.equal(a.find((x) => x.id === "caesar").available, true);
  assert.equal(JSON.stringify(a.map((x) => ({ ...x, available: 0 }))), JSON.stringify(d.map((x) => ({ ...x, available: 0 })))); assert.equal(d.find((x) => x.id === "fries").available, true);
});
test("фильтр блюд: поиск, категория, доступность", () => {
  const d = DATA.menu.dishes;
  assert.ok(filterDishes(d, { query: "чизбургер" }).some((x) => x.id === "cheeseburger")); assert.equal(filterDishes(d, { query: "zzzz" }).length, 0);
  const cat = d[0].category; assert.ok(filterDishes(d, { category: cat }).every((x) => x.category === cat));
  assert.deepEqual(filterDishes(d, { availability: "unavailable" }).map((x) => x.id), ["onion-rings"]); assert.equal(filterDishes(d, { availability: "available" }).length, d.length - 1);
});
