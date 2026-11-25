// Корень страницы и загрузка данных. Адреса строятся от data-root ("./" или "../"): сайт живёт в подкаталоге GitHub Pages.
export const root = document.documentElement.dataset.root || "./";
export const url = (p) => root + p;

export async function loadJSON(name) {
  const res = await fetch(url("data/" + name), { cache: "no-cache" });
  if (!res.ok) throw new Error("Не удалось загрузить " + name + " (" + res.status + ")");
  return res.json();
}
