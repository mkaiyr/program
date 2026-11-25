import { root } from "../core.js";
import { getData } from "../data.js";
import { loadCart, cartQtyMap } from "../cart-store.js";

// Контекст построения HTML и показ ошибки загрузки с кнопкой «Повторить»
export const makeCtx = (data, cart) => ({ root, ingredients: data.ingredients, allergenList: data.allergenList, categoriesById: data.categoriesById,
  settings: data.settings, cartQty: cartQtyMap(cart || loadCart(data.dishesById)) });

export async function boot(render, mount) {
  mount.setAttribute("aria-busy", "true");
  try { const data = await getData(); mount.removeAttribute("aria-busy"); render(data); }
  catch (e) {
    mount.removeAttribute("aria-busy");
    mount.innerHTML = `<div class="state card" role="alert"><h2>Не удалось загрузить данные</h2><p class="muted">Проверьте соединение и попробуйте ещё раз.</p><button type="button" class="btn btn--primary" data-retry>Повторить</button></div>`;
    mount.querySelector("[data-retry]").addEventListener("click", () => boot(render, mount));
  }
}
