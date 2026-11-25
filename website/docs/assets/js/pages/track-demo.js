// Демо-отслеживание: виртуальный курьер едет по заранее построенному дорожному маршруту. Ничего не пишет в хранилище и не обращается к серверу маршрутов.
import { url, loadJSON } from "../core.js";
import { esc, fmtMoney } from "../format.js";
import { createDemoProvider, STAGES, clampSpeed } from "../courier-demo.js";
import { mountLayout } from "../layout.js";

mountLayout();
const mount = document.getElementById("track");
const DEMO_ORDER = "DEMO-0001";   // не настоящий номер: в журнал заказов не попадает
const speed = clampSpeed(new URLSearchParams(location.search).get("speed") || 1);

async function start() {
  mount.setAttribute("aria-busy", "true");
  try {
    if (!window.L) throw new Error("Не загрузилась библиотека карты");
    const data = await loadJSON("demo-routes.json");
    if (!Array.isArray(data.destinations) || !data.destinations.length) throw new Error("Нет маршрутов");
    mount.removeAttribute("aria-busy"); render(data);
  } catch (e) {
    mount.removeAttribute("aria-busy");
    mount.innerHTML = `<div class="state card" role="alert" id="track-error"><h2>Не удалось загрузить демонстрацию</h2><p class="muted">Карта или маршруты недоступны. Проверьте соединение и повторите. Реальные заказы это не затрагивает.</p><button type="button" class="btn btn--primary" data-retry>Повторить</button></div>`;
    mount.querySelector("[data-retry]").addEventListener("click", start);
    console.warn("demo track:", e.message);
  }
}

const fmtMin = (s) => `≈ ${Math.max(1, Math.round(s / 60))} мин`;
const fmtKm = (m) => (m / 1000).toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " км";
const pin = (cls, text) => L.divIcon({ className: "map-pin " + cls, html: `<span>${esc(text)}</span>`, iconSize: [0, 0] });

function render(data) {
  const dests = data.destinations;
  mount.innerHTML = `<p class="demo-note" role="note" id="demo-note"><strong>ДЕМО-РЕЖИМ.</strong> Курьер виртуальный: GPS не используется, реальные заказы, касса и склад не затрагиваются. Время и расстояние демонстрационные, а не гарантия доставки.</p>
    <div class="track-layout">
      <section class="card track-card" aria-labelledby="trk-title">
        <h2 id="trk-title">Заказ <span data-demo-order>${DEMO_ORDER}</span> <span class="demo-chip">ДЕМО</span></h2>
        <dl class="kv"><dt>Курьер</dt><dd data-courier>Демо-курьер (виртуальный)</dd>
          <dt>Куда</dt><dd><label class="visually-hidden" for="dest">Тестовая точка доставки</label><select class="select" id="dest">${dests.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("")}</select></dd>
          <dt>Статус</dt><dd id="st" role="status" aria-live="polite"></dd>
          <dt>Примерное время (демо)</dt><dd id="eta"></dd>
          <dt>Маршрут (демо)</dt><dd id="dist"></dd></dl>
        <div class="track-controls" role="group" aria-label="Управление демонстрацией">
          <button type="button" class="btn btn--primary" id="b-start">Запустить</button>
          <button type="button" class="btn btn--secondary" id="b-pause" hidden>Пауза</button>
          <button type="button" class="btn btn--primary" id="b-resume" hidden>Продолжить</button>
          <button type="button" class="btn btn--ghost" id="b-reset">Сбросить</button></div>
        <ol class="stages" aria-label="Этапы демонстрационного заказа">${STAGES.map((s) => `<li data-stage="${s.id}">${esc(s.name)}</li>`).join("")}</ol>
        <p class="small muted">Тестовые точки — общественные места Алматы, а не адреса клиентов. ${esc(data.source)}</p>
      </section>
      <section class="track-map" aria-label="Карта доставки"><div id="map" role="region" aria-label="Карта Алматы с маршрутом демо-курьера"></div><p class="small muted map-note" id="map-note" role="status"></p></section>
    </div>`;
  const $ = (id) => document.getElementById(id), m = data.map;
  const map = L.map("map", { minZoom: m.minZoom, maxZoom: m.maxZoom, zoomSnap: 0.5 }).setView(m.center, m.zoom);
  let loaded = 0, failed = 0;
  const tiles = L.tileLayer(m.tileUrl, { maxZoom: m.maxZoom, minZoom: m.minZoom, attribution: `<a href="${esc(m.attributionUrl)}" target="_blank" rel="noopener">${esc(m.attribution)}</a>` }).addTo(map);
  tiles.on("tileload", () => { loaded++; if (loaded === 1) $("map-note").textContent = ""; });
  tiles.on("tileerror", () => { failed++; if (failed >= 3 && !loaded) $("map-note").textContent = "Подложка карты не загрузилась (нет связи с сервером карты). Маршрут и курьер показаны без неё."; });
  const tag = document.createElement("div"); tag.className = "map-demo-tag"; tag.textContent = "ДЕМО-РЕЖИМ"; $("map").appendChild(tag);
  L.marker([data.cafe.lat, data.cafe.lon], { icon: pin("map-pin--cafe", "Кафе"), keyboard: false, interactive: false }).addTo(map);
  const points = dests.map((d) => L.marker([d.lat, d.lon], { icon: pin("map-pin--point", d.name), keyboard: false, title: d.name }).on("click", () => choose(d.id)).addTo(map));
  let line = null, destPin = null, courier = null, provider = null, raf = 0, tm = 0, shownStage = "__", routeObj = null;
  const stopLoop = () => { cancelAnimationFrame(raf); clearTimeout(tm); raf = 0; tm = 0; };

  function build(id) {
    routeObj = dests.find((d) => d.id === id) || dests[0];
    if (line) map.removeLayer(line); if (destPin) map.removeLayer(destPin); if (courier) map.removeLayer(courier);
    line = L.polyline(routeObj.route, { color: "#B4482A", weight: 5, opacity: 0.85 }).addTo(map);
    destPin = L.marker([routeObj.lat, routeObj.lon], { icon: pin("map-pin--dest", "Клиент (тест)"), keyboard: false, interactive: false, zIndexOffset: 500 }).addTo(map);
    courier = L.marker([data.cafe.lat, data.cafe.lon], { icon: pin("map-pin--courier", "Демо-курьер"), keyboard: false, interactive: false, zIndexOffset: 1000 }).addTo(map);
    provider = createDemoProvider(routeObj, { speed });
    map.fitBounds(line.getBounds(), { padding: [48, 48] });
    $("dest").value = routeObj.id; shownStage = "__"; paint(provider.snapshot(performance.now()));
  }
  function paint(s) {
    courier.setLatLng([s.lat, s.lon]);
    const name = s.phase === "idle" ? "Демонстрация не запущена" : s.phase === "paused" ? "Пауза: " + STAGES[s.stageIndex].name : STAGES[s.stageIndex].name + (s.phase === "finished" ? " (демо)" : "");
    if (name !== shownStage) {
      shownStage = name; $("st").textContent = name;
      document.querySelectorAll(".stages li").forEach((li, i) => { li.className = s.stageIndex < 0 ? "" : i < s.stageIndex ? "stage--done" : i === s.stageIndex ? "stage--current" : ""; if (i === s.stageIndex) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current"); });
    }
    $("eta").textContent = s.phase === "finished" ? "доставлено" : fmtMin(s.etaSeconds) + " (демо)";
    $("dist").textContent = s.phase === "idle" ? fmtKm(routeObj.distanceM) : `осталось ${fmtKm(s.remainingM)} из ${fmtKm(routeObj.distanceM)}`;
    $("b-start").hidden = s.phase === "running" || s.phase === "paused"; $("b-start").textContent = s.phase === "finished" ? "Запустить снова" : "Запустить";
    $("b-pause").hidden = s.phase !== "running"; $("b-resume").hidden = s.phase !== "paused"; $("b-reset").disabled = s.phase === "idle";
  }
  // Положение считается по часам, поэтому пропущенные кадры (фоновая вкладка, лёгкое устройство) не сбивают маршрут; таймер страхует на случай, когда браузер не отдаёт кадры анимации
  function loop() { stopLoop(); const s = provider.snapshot(performance.now()); paint(s); if (s.phase === "running") { raf = requestAnimationFrame(loop); tm = setTimeout(loop, 250); } }
  function run(fn) { fn(performance.now()); loop(); }
  function choose(id) { stopLoop(); build(id); }
  $("b-start").addEventListener("click", () => run((n) => provider.start(n)));
  $("b-pause").addEventListener("click", () => run((n) => provider.pause(n)));
  $("b-resume").addEventListener("click", () => run((n) => provider.resume(n)));
  $("b-reset").addEventListener("click", () => { stopLoop(); provider.reset(); shownStage = "__"; paint(provider.snapshot(performance.now())); map.fitBounds(line.getBounds(), { padding: [48, 48] }); });
  $("dest").addEventListener("change", (e) => choose(e.target.value));
  build(dests[0].id);
}
start();
