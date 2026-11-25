suite("html");
const ctx = (extra) => ({ root: "./", ingredients: DATA.ingredients, allergenList: DATA.allergenList, categoriesById: DATA.categoriesById, cartQty: {}, settings: DATA.settings, ...extra });
test("карточка каждого из 30 блюд собирается без мусора", () => {
  for (const d of DATA.menu.dishes) {
    const h = dishCardHTML(d, ctx());
    ["undefined", "NaN", "[object", "null"].forEach((bad) => assert.excludes(h, bad, d.id + ": " + bad));
    assert.includes(h, d.name); assert.includes(h, "dish.html?id=" + d.id);
  }
});
test("все адреса в HTML относительные, внешних ресурсов нет", () => {
  for (const d of DATA.menu.dishes) for (const h of [dishCardHTML(d, ctx()), dishDetailHTML(d, ctx())]) {
    assert.excludes(h, "http://"); assert.excludes(h, "https://"); assert.excludes(h, 'src="/'); assert.excludes(h, 'href="/');
  }
});
test("корень вложенной папки учитывается", () => {
  assert.includes(dishCardHTML(DATA.dishesById["fries"], ctx({ root: "../" })), 'src="../assets/img/dishes/fries-thumb.webp"');
});
test("доступное блюдо: кнопка «В корзину»; недоступное: кнопка выключена и нет data-add", () => {
  const ok = dishCardHTML(DATA.dishesById["fries"], ctx()), no = dishCardHTML(DATA.dishesById["onion-rings"], ctx());
  assert.includes(ok, 'data-add="fries"'); assert.excludes(no, "data-add"); assert.includes(no, "disabled"); assert.includes(no, "Нет в наличии");
});
test("блюдо в корзине: вместо кнопки счётчик", () => {
  const h = dishCardHTML(DATA.dishesById["fries"], ctx({ cartQty: { fries: 3 } }));
  assert.includes(h, 'data-inc="fries"'); assert.includes(h, 'data-dec="fries"'); assert.excludes(h, "data-add");
});
test("фото: у блюда с фото своя картинка, у блюда без фото — заглушка", () => {
  assert.includes(photoTag(DATA.dishesById["fries"], ctx(), "thumb"), "fries-thumb.webp");
  const ph = photoTag(DATA.dishesById["cheese-sauce"], ctx(), "thumb"); assert.includes(ph, "placeholder-dish.svg"); assert.includes(ph, "Фото скоро появится");
  assert.excludes(photoTag(DATA.dishesById["fries"], ctx(), "thumb"), "placeholder");
});
test("у каждой картинки есть alt, ширина и высота (без прыжков вёрстки)", () => {
  for (const d of DATA.menu.dishes) for (const size of ["thumb", "main"]) { const t = photoTag(d, ctx(), size); assert.ok(/ alt="[^"]+"/.test(t), d.id); assert.ok(/ width="\d+"/.test(t) && / height="\d+"/.test(t), d.id); }
});
test("защита от внедрения разметки: название и описание экранируются", () => {
  const evil = { ...DATA.dishesById["fries"], id: 'x"><script>', name: '<img src=x onerror=alert(1)>', description: '"><b>' };
  for (const h of [dishCardHTML(evil, ctx()), dishDetailHTML(evil, ctx()), cartLineHTML({ dish: evil, qty: 1, lineTotal: 1, unavailable: false }, ctx())]) {
    assert.excludes(h, "<img src=x"); assert.excludes(h, "<script>"); assert.excludes(h, "<b>"); assert.includes(h, "&lt;");
  }
});
test("страница блюда: состав и аллергены; блок аллергенов у чизбургера и у американо", () => {
  const h = dishDetailHTML(DATA.dishesById["cheeseburger"], ctx());
  assert.includes(h, "Состав"); assert.includes(h, "Содержит"); assert.includes(h, "Молоко"); assert.includes(h, DATA.settings.allergy.crossContaminationNote);
  const a = dishDetailHTML(DATA.dishesById["americano"], ctx()); assert.includes(a, "не подтверждены"); assert.excludes(a, "badge--warn");
});
test("страница блюда: недоступное блюдо нельзя добавить", () => {
  const h = dishDetailHTML(DATA.dishesById["onion-rings"], ctx()); assert.excludes(h, "data-add-detail"); assert.includes(h, "добавить его в корзину нельзя");
  assert.includes(dishDetailHTML(DATA.dishesById["fries"], ctx()), 'data-add-detail="fries"');
});
test("строка корзины: цена, итог, удаление, пометка недоступного", () => {
  const line = { dish: DATA.dishesById["fries"], qty: 2, lineTotal: 2380, unavailable: false };
  const h = cartLineHTML(line, ctx()); assert.includes(h, "2 380 ₸"); assert.includes(h, 'data-remove="fries"'); assert.includes(h, 'data-inc="fries"');
  const u = cartLineHTML({ dish: DATA.dishesById["onion-rings"], qty: 1, lineTotal: 0, unavailable: true }, ctx()); assert.includes(u, "не входит в сумму");
});
test("сводка аллергенов на карточке", () => {
  assert.includes(allergenSummary(DATA.dishesById["cheeseburger"], ctx()), "Содержит:"); assert.equal(allergenSummary(DATA.dishesById["americano"], ctx()), "");
});
