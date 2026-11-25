// Виртуальный курьер для демо-карты: геометрия маршрута и сценарий движения. Чистая логика без DOM, сети и хранилища.
// Курьер не реален: координаты считаются по заранее построенному дорожному маршруту, GPS не используется, заказы и склад не затрагиваются.
export const EARTH_M = 6371008.8;
const rad = (d) => (d * Math.PI) / 180;

// Расстояние между точками [lat, lon] в метрах
export function haversine(a, b) {
  const dLat = rad(b[0] - a[0]), dLon = rad(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

// Маршрут как ломаная: накопленные расстояния нужны, чтобы по пройденному пути найти точку именно НА дороге, а не на прямой между концами
export function buildPath(coords) {
  const pts = (coords || []).filter((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (pts.length < 2) throw new Error("В маршруте меньше двух точек");
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + haversine(pts[i - 1], pts[i]));
  return { points: pts, cum, length: cum[cum.length - 1] };
}

export function positionAt(path, distance) {
  const d = Math.min(Math.max(Number(distance) || 0, 0), path.length), c = path.cum;
  let lo = 0, hi = c.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (c[mid] <= d) lo = mid; else hi = mid; }
  const a = path.points[lo], b = path.points[hi], seg = c[hi] - c[lo], t = seg > 0 ? (d - c[lo]) / seg : 0;
  const bearing = (Math.atan2(Math.sin(rad(b[1] - a[1])) * Math.cos(rad(b[0])), Math.cos(rad(a[0])) * Math.sin(rad(b[0])) - Math.sin(rad(a[0])) * Math.cos(rad(b[0])) * Math.cos(rad(b[1] - a[1]))) * 180 / Math.PI + 360) % 360;
  return { lat: a[0] + (b[0] - a[0]) * t, lon: a[1] + (b[1] - a[1]) * t, bearing, index: lo, distance: d };
}

// Расстояние от точки до ломаной (плоское приближение, достаточно для городских расстояний): проверка, что курьер идёт по маршруту
export function distanceToPath(path, p) {
  const k = Math.cos(rad(p[0])) * 111320, m = 110540; let best = Infinity;
  for (let i = 1; i < path.points.length; i++) {
    const a = path.points[i - 1], b = path.points[i];
    const ax = (a[1] - p[1]) * k, ay = (a[0] - p[0]) * m, bx = (b[1] - p[1]) * k, by = (b[0] - p[0]) * m, dx = bx - ax, dy = by - ay;
    const t = dx || dy ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) : 0;
    best = Math.min(best, Math.hypot(ax + dx * t, ay + dy * t));
  }
  return best;
}

// ---------- сценарий ----------
export const STAGES = [
  { id: "accepted", name: "Заказ принят" }, { id: "preparing", name: "Заказ готовится" }, { id: "ready", name: "Заказ готов" },
  { id: "picked_up", name: "Курьер забрал заказ" }, { id: "on_the_way", name: "Курьер в пути" }, { id: "almost", name: "Курьер почти у клиента" }, { id: "delivered", name: "Заказ доставлен" },
];
// Время сценария сжато до ~1 минуты, чтобы демонстрацию можно было посмотреть. Это НЕ реальные сроки доставки.
export const DEFAULT_TIMING = { acceptedMs: 3000, preparingMs: 8000, readyMs: 3000, pickedUpMs: 2500, travelMs: 40000 };
export const DEMO_PREP_SECONDS = 600;   // условное время приготовления для показа примерного времени
export const MIN_SPEED = 0.25, MAX_SPEED = 50;
export const clampSpeed = (s) => (Number.isFinite(+s) && +s > 0 ? Math.min(MAX_SPEED, Math.max(MIN_SPEED, +s)) : 1);
// Лёгкий разгон и торможение: движение плавное, но не «застревает» на концах
export const ease = (p) => { const x = Math.min(1, Math.max(0, p)); return 0.7 * x + 0.3 * 0.5 * (1 - Math.cos(Math.PI * x)); };

const ALMOST_M = (len) => Math.max(150, len * 0.12);

// Симуляция управляется внешними часами (now в мс): без таймеров внутри, поэтому её можно проверять без ожидания
export class DemoSimulation {
  constructor(route, opts = {}) {
    this.path = buildPath(route.route || route.coordinates);
    this.durationS = Number(route.durationS) || Math.round(this.path.length / 8);
    this.timing = { ...DEFAULT_TIMING, ...(opts.timing || {}) };
    this.speed = clampSpeed(opts.speed ?? 1);
    this.pre = this.timing.acceptedMs + this.timing.preparingMs + this.timing.readyMs + this.timing.pickedUpMs;
    this.total = this.pre + this.timing.travelMs;
    this.reset();
  }
  reset() { this.phase = "idle"; this.base = 0; this.runFrom = 0; }
  start(now) {
    if (this.phase === "running") return false;
    this.phase = "running"; this.base = 0; this.runFrom = now; return true;    // из «finished» и «idle» — всегда с начала
  }
  pause(now) { if (this.phase !== "running") return false; this.base = this._elapsed(now); this.phase = "paused"; return true; }
  resume(now) { if (this.phase !== "paused") return false; this.phase = "running"; this.runFrom = now; return true; }
  _elapsed(now) {
    const e = this.phase === "running" ? this.base + Math.max(0, now - this.runFrom) * this.speed : this.base;
    return Math.min(this.total, e);
  }
  snapshot(now) {
    let e = this._elapsed(now);
    if (this.phase === "running" && e >= this.total) { this.phase = "finished"; this.base = this.total; e = this.total; }
    const t = this.timing, travelFrom = this.pre, p = Math.min(1, Math.max(0, (e - travelFrom) / t.travelMs));
    const distance = ease(p) * this.path.length, remaining = this.path.length - distance, pos = positionAt(this.path, distance);
    let stage;
    if (this.phase === "idle") stage = null;
    else if (e >= this.total) stage = "delivered";
    else if (e < t.acceptedMs) stage = "accepted";
    else if (e < t.acceptedMs + t.preparingMs) stage = "preparing";
    else if (e < t.acceptedMs + t.preparingMs + t.readyMs) stage = "ready";
    else if (e < this.pre) stage = "picked_up";
    else stage = remaining <= ALMOST_M(this.path.length) ? "almost" : "on_the_way";
    const prepLeft = Math.max(0, this.pre - e) / this.pre;
    const eta = stage === null ? this.durationS + DEMO_PREP_SECONDS : Math.round(prepLeft * DEMO_PREP_SECONDS + (1 - ease(p)) * this.durationS);
    return { phase: this.phase, stage, stageIndex: stage ? STAGES.findIndex((s) => s.id === stage) : -1, elapsedMs: e, progress: p, distanceM: distance, remainingM: remaining,
             lat: pos.lat, lon: pos.lon, bearing: pos.bearing, etaSeconds: this.phase === "finished" ? 0 : eta, totalMs: this.total, source: "demo" };
  }
}

// Контракт источника координат. Сейчас есть только демонстрационный; настоящий GPS не подключён и подменять его случайными значениями нельзя.
// Любой источник возвращает { lat, lon, source: "demo" | "real_courier", at } и управляется start/pause/resume/reset.
export const LOCATION_SOURCES = ["demo", "real_courier"];
export function createDemoProvider(route, opts) {
  const sim = new DemoSimulation(route, opts);
  return { source: "demo", sim, start: (n) => sim.start(n), pause: (n) => sim.pause(n), resume: (n) => sim.resume(n), reset: () => sim.reset(), snapshot: (n) => sim.snapshot(n) };
}
export function createRealProvider() { throw new Error("Реальный GPS курьера не подключён: нужны авторизация курьера, назначение заказа и согласие на геолокацию (этап 9)."); }
