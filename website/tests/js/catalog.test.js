suite("catalog");
const good = () => JSON.parse(JSON.stringify(DATA.menu));
const bad = (mut) => { const m = good(); mut(m); return validateCatalog(m); };
test("настоящее меню сайта проходит проверку контракта", () => { const v = validateCatalog(DATA.menu); assert.ok(v.ok, v.errors.join("; ")); });
test("повторяющийся id блюда, неизвестная категория и повторная категория отклоняются", () => {
  assert.ok(!bad((m) => { m.dishes[1].id = m.dishes[0].id; }).ok);
  assert.ok(bad((m) => { m.dishes[0].category = "nope"; }).errors.join().includes("неизвестная категория"));
  assert.ok(!bad((m) => { m.categories.push({ ...m.categories[0] }); }).ok);
});
test("цена: только целое число тенге больше нуля (не строка, не дробь, не ноль, не отрицательная)", () => {
  for (const p of ["2290", 2290.5, 0, -5, null, NaN, undefined]) assert.ok(!bad((m) => { m.dishes[0].price = p; }).ok, String(p));
});
test("доступность и активность — только true/false", () => {
  assert.ok(!bad((m) => { m.dishes[0].available = "yes"; }).ok); assert.ok(!bad((m) => { m.dishes[0].active = 1; }).ok); assert.ok(bad((m) => { m.dishes[0].active = false; }).ok);
});
test("название, id, состав, фото и структура обязательны", () => {
  assert.ok(!bad((m) => { m.dishes[0].name = "  "; }).ok); assert.ok(!bad((m) => { m.dishes[0].id = "Бургер 1"; }).ok); assert.ok(!bad((m) => { delete m.dishes[0].ingredients; }).ok);
  assert.ok(!bad((m) => { m.dishes[0].photo = { file: "a.webp" }; }).ok); assert.ok(bad((m) => { m.dishes[0].photo = null; }).ok);
  for (const x of [null, [], "меню", 5, {}, { categories: [], dishes: [] }, { categories: [{ id: "a", name: "А" }] }]) assert.ok(!validateCatalog(x).ok);
});
test("скрытое (неактивное) блюдо не попадает в публичное меню, а недоступное попадает", () => {
  const m = good(); m.dishes[0].active = false; const pub = publicDishes(m.dishes);
  assert.equal(pub.length, m.dishes.length - 1); assert.ok(!pub.some((d) => d.id === m.dishes[0].id)); assert.ok(pub.some((d) => d.available === false));
});
test("сборка каталога: статический источник проходит, испорченные данные отклоняются, API пока не подключён", () => {
  const ok = catalogFromRaw(good()); assert.equal(ok.source, "static"); assert.equal(ok.contractVersion, 1); assert.equal(ok.dishes.length, DATA.menu.dishes.length);
  const broken = good(); broken.dishes[0].price = -1;
  assert.throws(() => catalogFromRaw(broken), /не прошёл проверку/); assert.throws(() => catalogFromRaw({ categories: [], dishes: [] }), /не прошёл проверку/);
  assert.throws(() => catalogFromRaw(good(), "api"), /не подключён/); assert.throws(() => catalogFromRaw(good(), "xml"), /Неизвестный/); assert.throws(() => checkMode("api"), /не подключён/);
  const hidden = good(); hidden.dishes[0].active = false; assert.equal(catalogFromRaw(hidden).dishes.length, hidden.dishes.length - 1);
});
test("loadCatalog возвращает промис и не принимает api до подключения", () => {
  assert.ok(typeof loadCatalog(async () => good()).then === "function");
  assert.ok(typeof loadCatalog(async () => good(), "api").catch === "function");   // отказ приходит промисом, а не синхронным исключением
});
