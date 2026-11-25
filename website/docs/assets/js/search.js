import { normText } from "./format.js";

// Поиск по названию, категории, составу и описанию. Все слова запроса должны найтись (И); совпадение в названии весит больше.
export function buildIndex(dishes, ingredients, categories) {
  const cat = Object.fromEntries(categories.map((c) => [c.id, normText(c.name)]));
  return Object.fromEntries(dishes.map((d) => [d.id, {
    name: normText(d.name),
    cat: cat[d.category] || "",
    ing: normText((d.ingredients || []).map((i) => (ingredients[i] ? ingredients[i].name : i)).join(" ")),
    desc: normText(d.description),
  }]));
}

// Основа слова для русских окончаний: «курица» находит «курицей», «пиццу» — «пицца». Короткие слова (до 4 букв) не усекаются.
export function stemOf(t) {
  if (t.length < 5) return t;
  return t.slice(0, t.length - (t.length >= 6 ? 2 : 1));
}

export function searchDishes(dishes, index, query) {
  const tokens = normText(query).split(" ").filter(Boolean);
  if (!tokens.length) return dishes.slice();
  const scored = [];
  for (const d of dishes) {
    const ix = index[d.id];
    if (!ix) continue;
    let score = 0, ok = true;
    for (const t of tokens) {
      let s = 0;
      for (const q of t === stemOf(t) ? [t] : [t, stemOf(t)]) {   // сначала слово целиком, затем его основа
        if (ix.name.split(" ").some((w) => w.startsWith(q))) s = Math.max(s, 6);
        else if (ix.name.includes(q)) s = Math.max(s, 4);
        else if (ix.cat.includes(q)) s = Math.max(s, 3);
        else if (ix.ing.includes(q)) s = Math.max(s, 2);
        else if (ix.desc.includes(q)) s = Math.max(s, 1);
      }
      if (!s) { ok = false; break; }
      score += s;
    }
    if (ok) scored.push({ d, score });
  }
  scored.sort((a, b) => b.score - a.score || a.d.sort - b.d.sort);
  return scored.map((x) => x.d);
}
