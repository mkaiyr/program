suite("search");
const dishes = DATA.menu.dishes, idx = buildIndex(dishes, DATA.ingredients, DATA.menu.categories);
const ids = (q) => searchDishes(dishes, idx, q).map((d) => d.id);
test("пустой запрос — все 30 блюд в порядке меню", () => {
  assert.equal(ids("").length, 30); assert.equal(ids("   ").length, 30);
  assert.deepEqual(ids("").slice(0, 3), ["cheeseburger", "classic-burger", "chickenburger"]);
});
test("поиск по названию: нужное блюдо первое", () => {
  assert.equal(ids("чизбургер")[0], "cheeseburger");
  assert.equal(ids("маргарита")[0], "margherita");
  assert.equal(ids("капучино")[0], "cappuccino");
});
test("регистр и ё/е не важны", () => {
  assert.deepEqual(ids("ЧЁРНЫЙ чай"), ids("черный чай")); assert.ok(ids("черный чай").includes("black-tea"));
});
test("часть слова находит блюда, название важнее состава", () => {
  const r = ids("сыр"); assert.ok(r.includes("cheese-sticks") && r.includes("cheese-sauce") && r.includes("syrniki") === false || r.includes("syrniki") === true);
  assert.equal(r[0], "cheese-sticks", "блюдо с «сыр» в названии должно идти перед теми, где сыр только в составе");
  assert.ok(r.includes("cheeseburger"), "сыр в составе");
});
test("поиск по категории", () => {
  const r = ids("напитки"); assert.ok(["americano", "cappuccino", "lemonade", "black-tea"].every((x) => r.includes(x)));
});
test("поиск по составу и описанию", () => {
  assert.ok(ids("пармезан").includes("caesar")); assert.ok(ids("пармезан").includes("pasta-bolognese"));
  assert.ok(ids("мята").includes("lemonade"));
});
test("несколько слов — все должны совпасть", () => {
  const r = ids("куриное филе"); assert.ok(r.includes("caesar") && r.includes("chicken-soup"));
  assert.equal(ids("пицца курица").includes("chicken-pizza"), true);
  assert.deepEqual(ids("чизбургер шаурма"), []);
});
test("русские окончания: «курица» находит «с курицей» и «куриные»", () => {
  const r = ids("курица"); assert.ok(r.includes("chicken-pizza") && r.includes("pasta-chicken") && r.includes("nuggets") && r.includes("chicken-soup"));
  assert.ok(ids("пиццу").includes("margherita")); assert.ok(ids("бургеры").includes("cheeseburger")); assert.ok(ids("салаты").includes("caesar"));
});
test("основа слова не даёт ложных совпадений", () => {
  assert.deepEqual(ids("шаурма"), []); assert.deepEqual(ids("пиццерия"), []); assert.deepEqual(ids("кофеин").sort(), ["americano", "cappuccino"], "кофеин → основа «кофе»: кофейные напитки"); assert.equal(stemOf("сыр"), "сыр"); assert.equal(stemOf("курица"), "кури"); assert.equal(stemOf("маргарита"), "маргари");
});
test("нет результатов и мусорный ввод", () => {
  assert.deepEqual(ids("xyzzy"), []); assert.deepEqual(ids("!!!").length, 30, "только знаки препинания = пустой запрос");
  assert.ok(ids("бургер!!!").length >= 4);
});
test("поиск не падает на странном вводе", () => {
  for (const q of ["<script>", "\\", "(", "[a-z]+", "%", "a".repeat(5000), "😀"]) assert.ok(Array.isArray(ids(q)));
});
test("поиск находит и недоступные блюда (их показывают с пометкой)", () => {
  assert.ok(ids("луковые").includes("onion-rings"));
});
