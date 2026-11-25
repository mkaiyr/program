suite("pricing");
const S = DATA.settings;
test("настройки доставки совпадают с согласованными (500 / 8000 / 2000, без надбавки)", () => {
  assert.equal(S.delivery.fee, 500); assert.equal(S.delivery.freeFrom, 8000); assert.equal(S.delivery.minOrder, 2000); assert.equal(S.pricing.serviceChargePercent, 0);
});
test("доставка: платная ниже порога, бесплатная от 8000", () => {
  assert.equal(orderQuote(2000, "delivery", S).fee, 500); assert.equal(orderQuote(7999, "delivery", S).fee, 500);
  assert.equal(orderQuote(8000, "delivery", S).fee, 0); assert.equal(orderQuote(12000, "delivery", S).fee, 0);
});
test("доставка: сколько осталось до бесплатной", () => {
  assert.equal(orderQuote(7999, "delivery", S).freeLeft, 1); assert.equal(orderQuote(3000, "delivery", S).freeLeft, 5000); assert.equal(orderQuote(8000, "delivery", S).freeLeft, 0);
});
test("минимальный заказ 2000 действует только для доставки", () => {
  assert.equal(orderQuote(1999, "delivery", S).minOrderOk, false); assert.equal(orderQuote(2000, "delivery", S).minOrderOk, true);
  assert.equal(orderQuote(500, "pickup", S).minOrderOk, true);
});
test("самовывоз бесплатный, итог = сумма блюд", () => {
  const q = orderQuote(3500, "pickup", S); assert.equal(q.fee, 0); assert.equal(q.total, 3500);
});
test("итог доставки = блюда + доставка, надбавки за обслуживание нет", () => {
  const q = orderQuote(3500, "delivery", S); assert.equal(q.total, 4000); assert.equal(q.service, 0);
  assert.equal(orderQuote(8000, "delivery", S).total, 8000);
});
test("пустая корзина: доставка 0", () => {
  const q = orderQuote(0, "delivery", S); assert.equal(q.fee, 0); assert.equal(q.total, 0);
});
test("надбавка настраивается (если когда-нибудь понадобится): округление", () => {
  const s2 = { ...S, pricing: { serviceChargePercent: 10 } }; assert.equal(orderQuote(2295, "pickup", s2).service, 230); assert.equal(orderQuote(2295, "pickup", s2).total, 2525);
});
test("отрицательные и дробные суммы нормализуются", () => {
  assert.equal(orderQuote(-100, "pickup", S).subtotal, 0); assert.equal(orderQuote(1999.6, "delivery", S).subtotal, 2000);
});
