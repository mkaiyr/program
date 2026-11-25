import { esc, plural } from "../format.js";
import { dishCardHTML } from "../html.js";
import { buildIndex, searchDishes } from "../search.js";
import { bindCartActions, refreshCard } from "../cart-ui.js";
import { onCartChange, loadCart } from "../cart-store.js";
import { mountLayout } from "../layout.js";
import { boot, makeCtx } from "./common.js";

mountLayout();
const mount = document.getElementById("menu");

function render(data) {
  const { dishes, categories, categoriesById, dishesById } = data, index = buildIndex(dishes, data.ingredients, categories);
  const params = new URLSearchParams(location.search);
  const state = { cat: categoriesById[params.get("cat")] ? params.get("cat") : "all", q: (params.get("q") || "").slice(0, 80) };
  mount.innerHTML = `<div class="container">
    <h1 class="page-title">Меню</h1>
    <form class="search" role="search" id="search-form"><label class="visually-hidden" for="q">Поиск блюд</label>
      <input id="q" class="input search__input" type="search" placeholder="Найти блюдо или ингредиент" autocomplete="off" maxlength="80" value="${esc(state.q)}">
      <button type="button" class="btn btn--ghost btn--sm" id="q-clear" ${state.q ? "" : "hidden"}>Сбросить</button></form>
    <nav class="chips" aria-label="Категории" id="chips"></nav>
    <p class="small muted" id="found" role="status" aria-live="polite"></p>
    <div class="dish-grid" id="grid"></div></div>`;
  const grid = document.getElementById("grid"), chips = document.getElementById("chips"), found = document.getElementById("found"), q = document.getElementById("q"), clear = document.getElementById("q-clear");
  const results = () => searchDishes(dishes, index, state.q);
  function draw() {
    const all = results(), counts = Object.fromEntries(categories.map((c) => [c.id, all.filter((d) => d.category === c.id).length]));
    const list = state.cat === "all" ? all : all.filter((d) => d.category === state.cat);
    chips.innerHTML = [`<button type="button" class="chip" data-cat="all" aria-pressed="${state.cat === "all"}">Все <span class="chip__n">${all.length}</span></button>`]
      .concat(categories.map((c) => `<button type="button" class="chip" data-cat="${esc(c.id)}" aria-pressed="${state.cat === c.id}"${counts[c.id] ? "" : " disabled"}>${esc(c.name)} <span class="chip__n">${counts[c.id]}</span></button>`)).join("");
    found.textContent = list.length ? `Найдено: ${list.length} ${plural(list.length, "блюдо", "блюда", "блюд")}` : "";
    const ctx = makeCtx(data);
    grid.innerHTML = list.length ? list.map((d) => dishCardHTML(d, ctx)).join("")
      : `<div class="state card"><h2>Ничего не найдено</h2><p class="muted">Попробуйте другое слово или сбросьте поиск.</p><button type="button" class="btn btn--primary" id="reset">Сбросить поиск и категорию</button></div>`;
    clear.hidden = !state.q;
    const next = new URLSearchParams(); if (state.cat !== "all") next.set("cat", state.cat); if (state.q) next.set("q", state.q);
    history.replaceState(null, "", location.pathname + (next.toString() ? "?" + next : ""));
    document.title = (state.cat !== "all" ? categoriesById[state.cat].name + " — " : "") + "Меню — Кафе Бистро (демо)";
  }
  document.getElementById("search-form").addEventListener("submit", (e) => e.preventDefault());
  let t; q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = q.value; draw(); }, 140); });
  clear.addEventListener("click", () => { q.value = ""; state.q = ""; draw(); q.focus(); });
  chips.addEventListener("click", (e) => { const b = e.target.closest("[data-cat]"); if (b && !b.disabled) { state.cat = b.dataset.cat; draw(); chips.querySelector(`[data-cat="${CSS.escape(state.cat)}"]`)?.focus(); } });
  grid.addEventListener("click", (e) => { if (e.target.closest("#reset")) { state.q = ""; state.cat = "all"; q.value = ""; draw(); q.focus(); } });
  bindCartActions(grid, data, (id) => refreshCard(grid, dishesById[id], dishCardHTML, makeCtx(data)));
  onCartChange(() => { for (const d of dishes) refreshCard(grid, d, dishCardHTML, makeCtx(data, loadCart(dishesById))); });
  draw();
}
boot(render, mount);
