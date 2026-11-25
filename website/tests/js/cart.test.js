suite("cart");
const by = DATA.dishesById;
test("добавление: неизвестное и недоступное блюдо отклоняются", () => {
  assert.equal(addItem(emptyCart(), "no-such", 1, by).error, "unknown");
  const r = addItem(emptyCart(), "onion-rings", 1, by);
  assert.equal(r.error, "unavailable"); assert.deepEqual(r.cart.items, []);
});
test("добавление и повторное добавление увеличивают количество", () => {
  let c = addItem(emptyCart(), "fries", 1, by).cart; c = addItem(c, "fries", 2, by).cart;
  assert.deepEqual(c.items, [{ id: "fries", qty: 3 }]);
});
test("исходная корзина не меняется (иммутабельность)", () => {
  const c0 = addItem(emptyCart(), "fries", 1, by).cart, snap = JSON.stringify(c0);
  addItem(c0, "fries", 5, by); setQty(c0, "fries", 9); removeItem(c0, "fries");
  assert.equal(JSON.stringify(c0), snap);
});
test("предел количества 20", () => {
  let c = addItem(emptyCart(), "fries", 25, by).cart; assert.equal(c.items[0].qty, 20);
  assert.equal(addItem(c, "fries", 1, by).error, "max"); assert.equal(setQty(c, "fries", 99).items[0].qty, 20);
});
test("setQty: ноль, отрицательное и мусор удаляют позицию, дробное округляется вниз", () => {
  const c = addItem(emptyCart(), "fries", 3, by).cart;
  for (const q of [0, -1, "x", null, NaN]) assert.deepEqual(setQty(c, "fries", q).items, [], "значение " + q);
  assert.equal(setQty(c, "fries", 2.9).items[0].qty, 2);
});
test("нормализация: чужие id, дубли, дробные и огромные количества", () => {
  const raw = { items: [{ id: "fries", qty: 2.7 }, { id: "fries", qty: 5 }, { id: "ghost", qty: 1 }, { id: "wings", qty: -3 }, { id: "caesar", qty: 999 }, null, { id: 5, qty: 1 }, { id: "cheesecake", qty: "3" }], comment: "x".repeat(400) };
  const c = normalizeCart(raw, by);
  assert.deepEqual(c.items, [{ id: "fries", qty: 2 }, { id: "caesar", qty: 20 }, { id: "cheesecake", qty: 3 }]);
  assert.equal(c.comment.length, 300);
});
test("нормализация: испорченное хранилище даёт пустую корзину", () => {
  for (const raw of [null, undefined, "строка", 42, [], { items: "нет" }, { items: {} }]) assert.deepEqual(normalizeCart(raw, by).items, []);
});
test("цена и название берутся из меню, а не из хранилища", () => {
  const c = normalizeCart({ items: [{ id: "fries", qty: 2, price: 1, name: "Подмена", total: 2 }] }, by);
  assert.deepEqual(Object.keys(c.items[0]).sort(), ["id", "qty"]);
  const lines = cartLines(c, by); assert.equal(lines[0].lineTotal, 2 * by["fries"].price); assert.equal(cartSubtotal(lines), 2 * by["fries"].price);
});
test("недоступное блюдо остаётся в корзине, но не входит в сумму", () => {
  const c = { items: [{ id: "onion-rings", qty: 2 }, { id: "fries", qty: 1 }], comment: "" };
  const lines = cartLines(c, by); assert.equal(lines.length, 2);
  assert.equal(lines.find((l) => l.dish.id === "onion-rings").unavailable, true);
  assert.equal(cartSubtotal(lines), by["fries"].price); assert.equal(cartCount(c), 3);
});
test("итоги: 2×чизбургер + капучино", () => {
  let c = addItem(emptyCart(), "cheeseburger", 2, by).cart; c = addItem(c, "cappuccino", 1, by).cart;
  assert.equal(cartSubtotal(cartLines(c, by)), 2 * 2290 + 1190); assert.equal(cartCount(c), 3);
});
test("комментарий: ограничение длины, очистка корзины", () => {
  let c = setComment(addItem(emptyCart(), "fries", 1, by).cart, "без лука" + "!".repeat(400)); assert.equal(c.comment.length, 300);
  assert.deepEqual(clearCart(), { items: [], comment: "" });
});
test("удаление отсутствующей позиции безопасно", () => {
  const c = addItem(emptyCart(), "fries", 1, by).cart; assert.deepEqual(removeItem(c, "nothing").items, c.items);
});
