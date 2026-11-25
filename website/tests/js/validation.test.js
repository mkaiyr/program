suite("validation");
const S = DATA.settings, AL = DATA.allergenList.map((a) => a.id);
const good = { mode: "delivery", name: "Айгерим", phone: "+7 701 234-56-78", street: "Абая 10", payment: "cash", allergy: { answer: "no" } };
const v = (o) => validateCheckout({ ...good, ...o }, S, AL);
test("телефон: допустимые записи", () => {
  for (const p of ["+7 701 234-56-78", "8 (701) 234 56 78", "7012345678", "87012345678", "+77012345678"]) assert.equal(normalizePhone(p), "7012345678", p);
  assert.equal(formatPhone("7012345678"), "+7 (701) 234-56-78");
});
test("телефон: недопустимые записи", () => {
  for (const p of ["", "123", "+7 701 234-56", "+7 701 234-56-789", "abc", "+7 000 000 00 00", "+7 777 777 77 77", "+7 201 234 56 78", "+1 701 234 56 78", "97012345678"]) assert.equal(normalizePhone(p), null, p);
});
test("корректная форма проходит", () => assert.ok(v({}).ok));
test("пустые обязательные поля", () => {
  const r = validateCheckout({}, S, AL);
  for (const k of ["mode", "name", "phone", "payment", "allergy"]) assert.ok(r.errors[k], k);
});
test("имя: цифры и один символ отклоняются, дефис и апостроф нет", () => {
  assert.ok(v({ name: "А" }).errors.name); assert.ok(v({ name: "12345" }).errors.name); assert.ok(v({ name: "   " }).errors.name);
  assert.ok(!v({ name: "Анна-Мария" }).errors.name); assert.ok(!v({ name: "O'Neil" }).errors.name);
});
test("адрес нужен только при доставке", () => {
  assert.ok(v({ street: "" }).errors.street); assert.ok(v({ street: "Абая" }).errors.street);
  const p = v({ mode: "pickup", street: "", pickupTime: "asap" });
  assert.ok(p.ok); assert.equal(p.values.street, undefined);
});
test("самовывоз: время только из списка настроек", () => {
  assert.ok(v({ mode: "pickup", pickupTime: "" }).errors.pickupTime); assert.ok(v({ mode: "pickup", pickupTime: "999" }).errors.pickupTime);
  assert.ok(v({ mode: "pickup", pickupTime: "60" }).ok);
});
test("способ оплаты только из настроек", () => { assert.ok(v({ payment: "bitcoin" }).errors.payment); for (const m of ["online", "cash", "card_on_delivery"]) assert.ok(v({ payment: m }).ok, m); });
test("почта необязательна, но если есть — проверяется", () => { assert.ok(v({ email: "a@example.kz" }).ok); assert.ok(v({ email: "не почта" }).errors.email); });
test("аллергия: «да» требует аллергены или описание; неизвестные отбрасываются", () => {
  assert.ok(v({ allergy: { answer: "yes" } }).errors.allergy);
  assert.ok(v({ allergy: { answer: "yes", allergens: ["нет-такого"] } }).errors.allergy);
  assert.deepEqual(v({ allergy: { answer: "yes", allergens: ["milk", "milk", "x"] } }).values.allergy.allergens, ["milk"]);
  assert.ok(v({ allergy: { answer: "yes", other: "киви" } }).ok); assert.ok(v({ allergy: { answer: "unsure" } }).ok);
  assert.ok(v({ allergy: { answer: "maybe" } }).errors.allergy);
});
test("длинные поля обрезаются", () => { const r = v({ comment: "я".repeat(500), courierComment: "я".repeat(500) }).values; assert.equal(r.comment.length, 300); assert.equal(r.courierComment.length, 200); });
