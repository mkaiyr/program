import { loadJSON } from "./core.js";
import { loadCatalog } from "./catalog-source.js";
import { applyAvailability } from "./admin-logic.js";
import { loadOverrides } from "./admin-store.js";

let pending = null;
// Данные загружаются один раз; при ошибке следующий вызов пробует снова (кнопка «Повторить»)
export function getData() {
  if (!pending) {
    pending = Promise.all([loadJSON("settings.json"), loadCatalog(loadJSON), loadJSON("ingredients.json")]).then(([settings, menu, ing]) => {
      // доступность может быть изменена в демо-админке (только в этом браузере); исходные данные остаются в baseDishes
      const baseDishes = [...menu.dishes].sort((a, b) => a.sort - b.sort), baseById = Object.fromEntries(baseDishes.map((d) => [d.id, d]));
      const overrides = loadOverrides(baseById), dishes = applyAvailability(baseDishes, overrides);
      const categories = [...menu.categories].sort((a, b) => a.sort - b.sort);
      return { settings, menu, dishes, baseDishes, baseById, overrides, categories, ingredients: ing.ingredients, allergenList: ing.allergens,
               dishesById: Object.fromEntries(dishes.map((d) => [d.id, d])), categoriesById: Object.fromEntries(categories.map((c) => [c.id, c])) };
    }).catch((e) => { pending = null; throw e; });
  }
  return pending;
}
