suite("courier");
const R = DATA.routes, DEST = R.destinations, L = { route: [[43.0, 76.0], [43.0, 76.01], [43.01, 76.01]], durationS: 600 };   // Г-образный маршрут
const drive = (sim, step, until) => { const out = []; let t = 0; sim.start(0); for (; t <= until; t += step) out.push(sim.snapshot(t)); return out; };
test("расстояние: 0,01° долготы на широте Алматы ≈ 0,8 км", () => { const d = haversine([43.2389, 76.9455], [43.2389, 76.9555]); assert.ok(d > 780 && d < 830, String(d)); assert.equal(haversine([1, 1], [1, 1]), 0); });
test("ломаная: накопленные расстояния растут, концы совпадают, выход за пределы ограничивается", () => {
  const p = buildPath(L.route); assert.ok(p.cum.every((v, i) => i === 0 || v > p.cum[i - 1])); assert.equal(p.cum[0], 0);
  assert.deepEqual([positionAt(p, 0).lat, positionAt(p, 0).lon], [43.0, 76.0]); assert.deepEqual([positionAt(p, p.length).lat, positionAt(p, p.length).lon], [43.01, 76.01]);
  assert.equal(positionAt(p, -50).lat, 43.0); assert.equal(positionAt(p, 1e9).lat, 43.01); assert.equal(positionAt(p, NaN).lat, 43.0);
});
test("маршрут короче двух точек или с мусором отклоняется", () => { assert.throws(() => buildPath([])); assert.throws(() => buildPath([[1, 1]])); assert.throws(() => buildPath([[1, 1], ["x", 2]])); assert.equal(buildPath([[1, 1], [1, 1.01], null]).points.length, 2); });
test("Г-образный путь: на полпути курьер за поворотом, а не на диагонали", () => {
  const p = buildPath(L.route), mid = positionAt(p, p.length / 2), diag = [43.005, 76.005];
  assert.ok(distanceToPath(p, [mid.lat, mid.lon]) < 0.5); assert.ok(haversine([mid.lat, mid.lon], diag) > 300, "точка на диагонали, а не на дороге");
});
test("реальные маршруты: данные согласованы (старт у кафе, конец у точки, длина ≈ заявленной)", () => {
  assert.ok(DEST.length >= 4);
  for (const d of DEST) {
    const p = buildPath(d.route);
    assert.ok(haversine([R.cafe.lat, R.cafe.lon], d.route[0]) < 5, d.id + ": начало не у кафе"); assert.ok(haversine([d.lat, d.lon], d.route[d.route.length - 1]) < 5, d.id + ": конец не у точки");
    assert.ok(Math.abs(p.length - d.distanceM) / d.distanceM < 0.05, `${d.id}: длина ломаной ${Math.round(p.length)} м, заявлено ${d.distanceM}`);
    assert.ok(d.route.length >= 20, d.id + ": слишком мало точек"); assert.ok(d.route.every((q) => q[0] > 43.1 && q[0] < 43.4 && q[1] > 76.7 && q[1] < 77.1), d.id + ": точка вне Алматы");
  }
});
test("реальные маршруты: курьер всегда на линии маршрута, а не на прямой", () => {
  let maxDev = 0;
  for (const d of DEST) {
    const p = buildPath(d.route), a = d.route[0], b = d.route[d.route.length - 1], straight = buildPath([a, b]);
    for (let i = 0; i <= 150; i++) {
      const pos = positionAt(p, p.length * i / 150), q = [pos.lat, pos.lon];
      assert.ok(distanceToPath(p, q) < 0.5, `${d.id}: точка ${i} в стороне от маршрута`); maxDev = Math.max(maxDev, distanceToPath(straight, q));
    }
  }
  assert.ok(maxDev > 100, "ни один маршрут не отличается от прямой — это не дорожная геометрия: " + Math.round(maxDev));
});
test("сценарий: все семь этапов по порядку, старт у кафе, финиш в точке назначения", () => {
  const sim = new DemoSimulation(DEST[1]); assert.equal(sim.snapshot(0).phase, "idle"); assert.equal(sim.snapshot(0).stage, null);
  const snaps = drive(sim, 100, sim.total + 500), seen = [];
  for (const s of snaps) if (seen[seen.length - 1] !== s.stage) seen.push(s.stage);
  assert.deepEqual(seen, ["accepted", "preparing", "ready", "picked_up", "on_the_way", "almost", "delivered"]);
  assert.ok(snaps.every((s, i) => i === 0 || s.stageIndex >= snaps[i - 1].stageIndex), "этап пошёл назад");
  const first = snaps[0], last = snaps[snaps.length - 1];
  assert.ok(haversine([first.lat, first.lon], [R.cafe.lat, R.cafe.lon]) < 5); assert.equal(last.phase, "finished"); assert.ok(haversine([last.lat, last.lon], [DEST[1].lat, DEST[1].lon]) < 5);
  assert.equal(last.etaSeconds, 0); assert.ok(Math.abs(last.remainingM) < 1e-6);
});
test("курьер стоит у кафе, пока заказ принимают и готовят, и только потом едет", () => {
  const sim = new DemoSimulation(DEST[0]); sim.start(0);
  for (const t of [0, 2000, 8000, 14000, sim.pre - 1]) { const s = sim.snapshot(t); assert.ok(haversine([s.lat, s.lon], [R.cafe.lat, R.cafe.lon]) < 1, t + ": уехал раньше времени"); }
  assert.ok(haversine([sim.snapshot(sim.pre + 5000).lat, sim.snapshot(sim.pre + 5000).lon], [R.cafe.lat, R.cafe.lon]) > 20);
});
test("движение плавное и только вперёд; шаг по расстоянию ограничен", () => {
  const sim = new DemoSimulation(DEST[3]), snaps = drive(sim, 50, sim.total), maxStep = Math.max(...snaps.map((s, i) => i ? s.distanceM - snaps[i - 1].distanceM : 0)), minStep = Math.min(...snaps.map((s, i) => i ? s.distanceM - snaps[i - 1].distanceM : 0));
  assert.ok(minStep >= 0, "курьер поехал назад"); assert.ok(maxStep < sim.path.length / 40 * 1.8, "рывок: " + maxStep);
});
test("пауза замораживает курьера, продолжение идёт с того же места", () => {
  const sim = new DemoSimulation(DEST[0]); sim.start(0); const a = sim.snapshot(20000); assert.ok(sim.pause(20000));
  const b = sim.snapshot(50000), c = sim.snapshot(90000); assert.equal(b.phase, "paused"); assert.equal(b.elapsedMs, a.elapsedMs); assert.equal(c.lat, a.lat); assert.equal(c.lon, a.lon);
  assert.ok(sim.resume(90000)); const d = sim.snapshot(90000); assert.equal(d.lat, a.lat); const e = sim.snapshot(92000); assert.ok(e.elapsedMs > a.elapsedMs && e.elapsedMs - a.elapsedMs <= 2000 + 1);
});
test("лишние команды игнорируются: пауза без запуска, продолжение без паузы, второй запуск", () => {
  const sim = new DemoSimulation(DEST[0]); assert.equal(sim.pause(0), false); assert.equal(sim.resume(0), false); assert.ok(sim.start(0)); assert.equal(sim.start(5000), false); assert.equal(sim.resume(6000), false);
  assert.equal(sim.snapshot(6000).elapsedMs, 6000); assert.ok(sim.pause(6000)); assert.equal(sim.pause(7000), false);
});
test("сброс возвращает курьера к кафе и в состояние «не запущено»", () => {
  const sim = new DemoSimulation(DEST[0]); sim.start(0); const mid = sim.snapshot(sim.pre + 20000); assert.ok(mid.distanceM > 0);
  sim.reset(); const s = sim.snapshot(999999); assert.equal(s.phase, "idle"); assert.equal(s.stage, null); assert.equal(s.elapsedMs, 0); assert.ok(haversine([s.lat, s.lon], [R.cafe.lat, R.cafe.lon]) < 1);
  sim.start(1000); assert.equal(sim.snapshot(1000).elapsedMs, 0);
});
test("повторный запуск после доставки идёт с начала", () => {
  const sim = new DemoSimulation(DEST[0]); sim.start(0); assert.equal(sim.snapshot(sim.total + 1000).phase, "finished");
  assert.ok(sim.start(100000)); const s = sim.snapshot(100000); assert.equal(s.phase, "running"); assert.equal(s.stage, "accepted"); assert.equal(s.elapsedMs, 0); assert.equal(sim.snapshot(100000 + sim.total + 5).phase, "finished");
});
test("ускорение (speed) сокращает демонстрацию, значения вне диапазона ограничиваются", () => {
  const f = new DemoSimulation(DEST[0], { speed: 10 }); f.start(0); assert.equal(f.snapshot(f.total / 10 + 1).phase, "finished");
  assert.equal(clampSpeed(1000), MAX_SPEED); assert.equal(clampSpeed(0.001), MIN_SPEED); assert.equal(clampSpeed("abc"), 1); assert.equal(clampSpeed(-5), 1); assert.equal(clampSpeed(2), 2);
});
test("примерное время только убывает и честно помечено как демонстрационное значение маршрута", () => {
  const sim = new DemoSimulation(DEST[2]), snaps = drive(sim, 200, sim.total); assert.ok(snaps.every((s, i) => i === 0 || s.etaSeconds <= snaps[i - 1].etaSeconds + 1));
  const idle = new DemoSimulation(DEST[2]).snapshot(0); assert.equal(idle.etaSeconds, DEST[2].durationS + DEMO_PREP_SECONDS);
});
test("источники координат: демо работает, настоящий GPS не подключён и не подменяется", () => {
  assert.deepEqual(LOCATION_SOURCES, ["demo", "real_courier"]); const p = createDemoProvider(DEST[0]); assert.equal(p.source, "demo"); p.start(0); assert.equal(p.snapshot(1).source, "demo");
  assert.throws(() => createRealProvider(), /не подключён/);
});
