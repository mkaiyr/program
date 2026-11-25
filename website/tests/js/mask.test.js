suite("mask");
test("телефон: видны код и две последние цифры", () => {
  assert.equal(maskPhone("7012345678"), "+7 (701) ***-**-78"); assert.equal(maskPhone("bad"), "•••"); assert.equal(maskPhone(null), "•••");
  assert.ok(!maskPhone("7012345678").includes("234") && !maskPhone("7012345678").includes("56"));
});
test("адрес: остаётся только название улицы", () => {
  assert.equal(maskAddress("Абая 10"), "Абая •••"); assert.equal(maskAddress("ул. Тестовая, 5, кв. 7"), "ул. Тестовая •••"); assert.equal(maskAddress("12"), "•••"); assert.equal(maskAddress(""), "•••");
  assert.ok(!maskAddress("Абая 10").includes("10"));
});
test("почта: видна первая буква и домен", () => { assert.equal(maskEmail("anna@example.kz"), "a***@example.kz"); assert.equal(maskEmail("не почта"), "•••"); });
