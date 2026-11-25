import { esc, fmtMoney } from "../format.js";
import { filterDishes, setAvailability, applyAvailability, LOCKED_AVAILABILITY } from "../admin-logic.js";
import { loadOverrides, saveOverrides } from "../admin-store.js";
import { boot } from "./common.js";
import { requireDemoAdmin, mountAdminFrame } from "./admin-common.js";

const mount = document.getElementById("amenu");
if (requireDemoAdmin()) { mountAdminFrame("menu"); boot(render, mount); }

function render(data) {
  const base = data.baseDishes, baseById = data.baseById, st = { query: "", category: "all", availability: "all", msg: "", kind: "" };
  mount.innerHTML = `<p class="demo-note" role="note">Доступность блюд меняется только в этом браузере и сразу отражается в меню сайта. Цены, составы и аллергены здесь не редактируются.</p>
    <div class="toolbar" role="search"><div class="field"><label for="m-q">Поиск блюда</label><input class="input" id="m-q" type="search" autocomplete="off"></div>
      <div class="field"><label for="m-cat">Категория</label><select class="select" id="m-cat"><option value="all">Все категории</option>${data.categories.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select></div>
      <div class="field"><label for="m-av">Доступность</label><select class="select" id="m-av"><option value="all">Все</option><option value="available">Доступные</option><option value="unavailable">Недоступные</option></select></div></div>
    <div class="menu-head"><p class="small muted" id="m-count" role="status" aria-live="polite"></p><button type="button" class="btn btn--ghost btn--sm" id="m-reset">Сбросить изменения доступности</button></div>
    <p class="alert" id="m-msg" role="status" hidden></p><div id="m-list"></div>`;
  const $ = (id) => document.getElementById(id);
  function draw() {
    const ov = loadOverrides(baseById), dishes = applyAvailability(base, ov), rows = filterDishes(dishes, st), off = dishes.filter((d) => !d.available).length;
    $("m-count").textContent = `Показано блюд: ${rows.length} из ${dishes.length}. Недоступных: ${off}.`;
    $("m-reset").disabled = !Object.keys(ov).length;
    const m = $("m-msg"); m.hidden = !st.msg; m.textContent = st.msg; m.className = "alert " + (st.kind === "ok" ? "alert--ok" : "alert--danger");
    $("m-list").innerHTML = rows.length ? `<table class="admin-table"><thead><tr><th scope="col">Блюдо</th><th scope="col">Категория</th><th scope="col">Цена</th><th scope="col">Состояние</th><th scope="col">Действие</th></tr></thead><tbody>${rows.map((d) => {
      const locked = LOCKED_AVAILABILITY.includes(d.id), changed = Object.prototype.hasOwnProperty.call(ov, d.id);
      return `<tr data-dish="${esc(d.id)}" class="${d.available ? "" : "is-off"}"><td data-label="Блюдо"><strong>${esc(d.name)}</strong></td><td data-label="Категория">${esc(data.categoriesById[d.category]?.name || d.category)}</td><td data-label="Цена">${fmtMoney(d.price)}</td>
        <td data-label="Состояние"><span class="badge ${d.available ? "badge--ok" : "badge--danger"}" data-av>${d.available ? "Доступно" : "Нет в наличии"}</span>${changed ? ' <span class="small muted">изменено</span>' : ""}</td>
        <td data-label="Действие">${locked ? `<button type="button" class="btn btn--secondary btn--sm" disabled aria-describedby="lock-note">Зафиксировано</button>` : `<button type="button" class="btn btn--secondary btn--sm" data-toggle="${esc(d.id)}" data-to="${d.available ? "off" : "on"}">${d.available ? "Сделать недоступным" : "Сделать доступным"}</button>`}</td></tr>`; }).join("")}</tbody></table>
      <p class="small muted" id="lock-note">«Луковые кольца» сохраняют исходное состояние до отдельного решения.</p>` : `<div class="empty card" id="m-empty"><p><strong>Ничего не найдено.</strong></p></div>`;
  }
  const commit = (map, okMsg) => { st.msg = saveOverrides(map) ? okMsg : "Не удалось сохранить изменение в браузере."; st.kind = st.msg === okMsg ? "ok" : "err"; draw(); };
  $("m-q").addEventListener("input", (e) => { st.query = e.target.value; st.msg = ""; draw(); });
  $("m-cat").addEventListener("change", (e) => { st.category = e.target.value; draw(); });
  $("m-av").addEventListener("change", (e) => { st.availability = e.target.value; draw(); });
  mount.addEventListener("click", (e) => {
    const t = e.target.closest("[data-toggle]");
    if (t) { const id = t.dataset.toggle, r = setAvailability(loadOverrides(baseById), id, t.dataset.to === "on", baseById); if (r.ok) { commit(r.overrides, `${baseById[id].name}: ${t.dataset.to === "on" ? "теперь доступно" : "теперь недоступно"}.`); document.querySelector(`[data-toggle="${CSS.escape(id)}"]`)?.focus(); } }
    if (e.target.closest("#m-reset")) commit({}, "Изменения сброшены: доступность как в исходных данных.");
  });
  draw();
}
