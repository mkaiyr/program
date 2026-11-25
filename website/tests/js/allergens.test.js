suite("allergens");
const AL = DATA.allergenList;
test("аллергены каждого блюда совпадают с независимым расчётом Python", () => {
  for (const d of DATA.menu.dishes) {
    const a = dishAllergens(d, DATA.ingredients), e = EXPECTED_ALLERGENS[d.id];
    assert.deepEqual([...a.contains].sort(), e.contains, d.id + " contains"); assert.deepEqual([...a.may].sort(), e.may, d.id + " may");
  }
});
test("«может содержать» не дублирует «содержит»", () => {
  for (const d of DATA.menu.dishes) { const a = dishAllergens(d, DATA.ingredients); assert.equal(a.may.filter((x) => a.contains.includes(x)).length, 0, d.id); }
});
test("чизбургер: глютен, молоко и яйца из булки, сыра и соуса", () => {
  const a = dishAllergens(DATA.dishesById["cheeseburger"], DATA.ingredients); ["gluten", "milk", "eggs"].forEach((x) => assert.ok(a.contains.includes(x), x));
});
test("американо: аллергены не указаны (это не значит «безопасно»)", () => {
  const a = dishAllergens(DATA.dishesById["americano"], DATA.ingredients); assert.deepEqual(a.contains, []); assert.deepEqual(a.may, []);
});
test("аллерген из соуса и полуфабриката учитывается: Цезарь — рыба из соуса", () => {
  assert.ok(dishAllergens(DATA.dishesById["caesar"], DATA.ingredients).contains.includes("fish"));
});
test("названия по-русски в порядке справочника", () => {
  assert.deepEqual(allergenNames(["milk", "gluten"], AL), ["Глютенсодержащие злаки", "Молоко"]);
  assert.deepEqual(allergenShort(["milk", "gluten", "eggs"], AL), ["глютен", "яйца", "молоко"]);
});
test("строка состава: составные ингредиенты раскрываются", () => {
  const s = ingredientLine(DATA.dishesById["cheeseburger"], DATA.ingredients);
  assert.includes(s, "булочка"); assert.includes(s, "фирменный соус (майонез, горчица, специи)");
});
test("неизвестный ингредиент не ломает расчёт", () => {
  const d = { ingredients: ["no_such", "bun"] }; assert.ok(dishAllergens(d, DATA.ingredients).contains.includes("gluten")); assert.includes(ingredientLine(d, DATA.ingredients), "no_such");
});
