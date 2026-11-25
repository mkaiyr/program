import { url } from "../core.js";
import { esc } from "../format.js";
import { dishDetailHTML, dishCardHTML } from "../html.js";
import { addItem, MAX_QTY } from "../cart-logic.js";
import { loadCart, saveCart, onCartChange } from "../cart-store.js";
import { toast } from "../toast.js";
import { bindCartActions, refreshCard } from "../cart-ui.js";
import { mountLayout } from "../layout.js";
import { boot, makeCtx } from "./common.js";

mountLayout();
const mount = document.getElementById("dish");

function render(data) {
  const id = new URLSearchParams(location.search).get("id"), dish = data.dishesById[id];
  if (!dish) {
    mount.innerHTML = `<div class="container"><div class="state card"><h1>Блюдо не найдено</h1><p class="muted">Такого блюда нет в меню. Возможно, ссылка устарела.</p><a class="btn btn--primary" href="${url("menu.html")}">Открыть меню</a></div></div>`;
    document.title = "Блюдо не найдено — Кафе Бистро (демо)"; return;
  }
  document.title = dish.name + " — Кафе Бистро (демо)";
  const related = data.dishes.filter((d) => d.category === dish.category && d.id !== dish.id).sort((a, b) => b.available - a.available).slice(0, 3);
  const draw = () => {
    const ctx = makeCtx(data);
    mount.innerHTML = `<div class="container">${dishDetailHTML(dish, ctx)}
      ${related.length ? `<section class="section related" aria-labelledby="rel-title"><h2 id="rel-title">Другие блюда категории</h2><div class="dish-grid" id="rel-grid">${related.map((d) => dishCardHTML(d, ctx)).join("")}</div></section>` : ""}</div>`;
    const grid = document.getElementById("rel-grid");
    if (grid) bindCartActions(grid, data, (rid) => { refreshCard(grid, data.dishesById[rid], dishCardHTML, makeCtx(data)); updateNote(); });
    const input = document.getElementById("qty");
    if (input) {
      const clamp = () => { input.value = String(Math.min(MAX_QTY, Math.max(1, Math.floor(Number(input.value)) || 1))); };
      input.addEventListener("change", clamp);
      mount.querySelector("[data-qty-dec]").addEventListener("click", () => { input.value = String(Number(input.value) - 1); clamp(); });
      mount.querySelector("[data-qty-inc]").addEventListener("click", () => { input.value = String(Number(input.value) + 1); clamp(); });
      mount.querySelector("[data-add-detail]").addEventListener("click", () => {
        clamp(); const r = addItem(loadCart(data.dishesById), dish.id, Number(input.value), data.dishesById);
        if (r.error === "max") return toast("Не больше " + MAX_QTY + " порций одного блюда", "warn");
        if (r.error) return toast("Это блюдо сейчас недоступно", "warn");
        saveCart(r.cart); toast("Добавлено в корзину: " + dish.name); updateNote(true);
      });
    }
  };
  function updateNote(withLink) {
    const note = mount.querySelector("[data-in-cart]"); if (!note) return;
    const n = (loadCart(data.dishesById).items.find((i) => i.id === dish.id) || {}).qty || 0;
    note.hidden = !n; note.innerHTML = n ? `Сейчас в корзине: ${n} шт. ${withLink ? `<a href="${url("cart.html")}">Перейти в корзину</a>` : ""}` : "";
  }
  draw(); onCartChange(() => updateNote());
}
boot(render, mount);
