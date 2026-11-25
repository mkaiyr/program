// Единая точка получения каталога блюд для сайта. Сейчас источник — статический файл data/menu.json (демонстрация).
// Позже источником станет серверный API общей базы (публичное представление каталога, без привилегированных ключей): тогда меняется только loadCatalog().
// Любой источник обязан отдавать данные в одном формате (контракт) — validateCatalog() проверяет это и отклоняет испорченные данные, а не «чинит» их молча.
// Цены на сайте — только из каталога: корзина хранит лишь id и количество, поэтому цену, пришедшую из браузера, никто не принимает на веру.
export const CATALOG_CONTRACT_VERSION = 1;
export const CATALOG_SOURCES = { static: "статический файл (демо)", api: "серверный API общей базы (ещё не подключён)" };
export const CATALOG_MODE = "static";

const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);

// Проверка формата каталога. Возвращает { ok, errors[] }. Не изменяет данные.
export function validateCatalog(raw) {
  const errors = [];
  if (!isObj(raw)) return { ok: false, errors: ["каталог не объект"] };
  if (!Array.isArray(raw.categories) || !raw.categories.length) errors.push("нет списка категорий");
  if (!Array.isArray(raw.dishes) || !raw.dishes.length) errors.push("нет списка блюд");
  if (errors.length) return { ok: false, errors };
  const cats = new Set();
  for (const c of raw.categories) {
    if (!isObj(c) || typeof c.id !== "string" || !SLUG.test(c.id) || typeof c.name !== "string" || !c.name.trim()) errors.push(`категория повреждена: ${JSON.stringify(c).slice(0, 60)}`);
    else if (cats.has(c.id)) errors.push(`категория «${c.id}» повторяется`); else cats.add(c.id);
  }
  const ids = new Set();
  for (const d of raw.dishes) {
    const at = isObj(d) && typeof d.id === "string" ? `блюдо «${d.id}»` : "блюдо без id";
    if (!isObj(d) || typeof d.id !== "string" || !SLUG.test(d.id)) { errors.push(`${at}: id должен быть латинским слагом`); continue; }
    if (ids.has(d.id)) errors.push(`${at}: id повторяется`); ids.add(d.id);
    if (typeof d.name !== "string" || !d.name.trim() || d.name.length > 100) errors.push(`${at}: нет названия (1–100 знаков)`);
    if (!cats.has(d.category)) errors.push(`${at}: неизвестная категория «${d.category}»`);
    if (!Number.isInteger(d.price) || d.price <= 0) errors.push(`${at}: цена — целое число тенге больше нуля (получено ${JSON.stringify(d.price)})`);
    if (typeof d.available !== "boolean") errors.push(`${at}: available должно быть true или false`);
    if (d.active !== undefined && typeof d.active !== "boolean") errors.push(`${at}: active должно быть true или false`);
    if (!Array.isArray(d.ingredients)) errors.push(`${at}: нет списка состава`);
    if (d.photo !== null && d.photo !== undefined && !(isObj(d.photo) && typeof d.photo.file === "string" && typeof d.photo.thumb === "string")) errors.push(`${at}: фото — объект с file и thumb или null`);
  }
  return { ok: errors.length === 0, errors };
}

// «Активность» и «доступность» — разные вещи: неактивное блюдо скрыто из публичного меню (но остаётся в системе и в истории заказов),
// недоступное показывается, но заказать его нельзя. Публичное меню получает только активные блюда.
export const publicDishes = (dishes) => dishes.filter((d) => d.active !== false);

// Выбор источника и сборка каталога из полученных данных (синхронно — так логику можно проверить без ожидания сети).
export function checkMode(mode) {
  if (mode === "api") throw new Error("Серверный каталог ещё не подключён: он появится вместе с общей базой меню, кассы и склада.");
  if (mode !== "static") throw new Error("Неизвестный источник каталога");
}
export function catalogFromRaw(raw, mode = CATALOG_MODE) {
  checkMode(mode);
  const v = validateCatalog(raw);
  if (!v.ok) throw new Error("Каталог не прошёл проверку: " + v.errors.slice(0, 3).join("; "));
  return { ...raw, dishes: publicDishes(raw.dishes), source: mode, contractVersion: CATALOG_CONTRACT_VERSION };
}

// Загрузка каталога из выбранного источника. fetchJson(name) — функция чтения данных (в браузере — loadJSON).
export async function loadCatalog(fetchJson, mode = CATALOG_MODE) {
  checkMode(mode);
  return catalogFromRaw(await fetchJson("menu.json"), mode);
}
