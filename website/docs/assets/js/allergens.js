// Аллергены блюда определяются по ингредиентам (включая соусы и полуфабрикаты); «не указано» не означает «не содержит».
export function dishAllergens(dish, ingredients) {
  const contains = new Set(), may = new Set();
  for (const id of dish.ingredients || []) {
    const al = (ingredients[id] && ingredients[id].allergens) || {};
    for (const [a, st] of Object.entries(al)) {
      if (st === "contains") contains.add(a);
      else if (st === "may_contain") may.add(a);
    }
  }
  for (const a of contains) may.delete(a);
  return { contains: [...contains], may: [...may] };
}

// Порядок как в справочнике аллергенов, названия по-русски
export function allergenNames(ids, allergenList) {
  const order = allergenList.map((a) => a.id);
  const names = Object.fromEntries(allergenList.map((a) => [a.id, a.name]));
  return [...ids].sort((x, y) => order.indexOf(x) - order.indexOf(y)).map((id) => names[id] || id);
}

// «булочка, говяжья котлета, фирменный соус (майонез, горчица, специи)»
export function ingredientLine(dish, ingredients) {
  return (dish.ingredients || []).map((id) => {
    const i = ingredients[id];
    if (!i) return id;
    return i.detail ? i.name + " (" + i.detail + ")" : i.name;
  }).join(", ");
}

// Краткие названия для карточек блюд
const SHORT = { gluten: "глютен", crustaceans: "ракообразные", eggs: "яйца", fish: "рыба", peanuts: "арахис", soy: "соя", milk: "молоко", nuts: "орехи",
  celery: "сельдерей", mustard: "горчица", sesame: "кунжут", sulphites: "сульфиты", molluscs: "моллюски", lupin: "люпин" };
export function allergenShort(ids, allergenList) {
  const order = allergenList.map((a) => a.id);
  return [...ids].sort((x, y) => order.indexOf(x) - order.indexOf(y)).map((id) => SHORT[id] || id);
}
