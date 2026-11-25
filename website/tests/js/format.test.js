suite("format");
test("fmtMoney: тысячи неразрывными пробелами и знак тенге", () => {
  assert.equal(fmtMoney(2290), "2 290 ₸");
  assert.equal(fmtMoney(0), "0 ₸");
  assert.equal(fmtMoney(1234567), "1 234 567 ₸");
  assert.equal(fmtMoney(999), "999 ₸");
  assert.equal(fmtMoney(1000), "1 000 ₸");
});
test("fmtMoney: мусор не ломает вывод", () => {
  assert.equal(fmtMoney(undefined), "0 ₸");
  assert.equal(fmtMoney("abc"), "0 ₸");
  assert.equal(fmtMoney(1234.6), "1 235 ₸");
  assert.equal(fmtMoney(-500), "−500 ₸");
});
test("plural: русские окончания", () => {
  const f = (n) => plural(n, "блюдо", "блюда", "блюд");
  assert.deepEqual([1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 100, 101, 111].map(f),
    ["блюдо", "блюда", "блюда", "блюд", "блюд", "блюд", "блюд", "блюдо", "блюда", "блюд", "блюд", "блюдо", "блюд"]);
});
test("normText: регистр, ё, знаки", () => {
  assert.equal(normText("  Чёрный   ЧАЙ!! "), "черный чай");
  assert.equal(normText("Бургер-«Чиз»"), "бургер чиз");
  assert.equal(normText(null), "");
});
test("esc: экранирует разметку и кавычки", () => {
  assert.equal(esc('<img src=x onerror="a()">&\''), "&lt;img src=x onerror=&quot;a()&quot;&gt;&amp;&#39;");
  assert.equal(esc(null), "");
});
