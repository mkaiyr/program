// Форматирование и нормализация текста. Чистые функции без доступа к DOM и браузеру (их проверяют автотесты tests/js).
export const NBSP = " ";

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 2290 -> «2 290 ₸» (неразрывные пробелы, чтобы сумма не переносилась по строкам)
export function fmtMoney(n) {
  const v = Math.round(Number(n) || 0);
  const s = String(Math.abs(v)).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  return (v < 0 ? "−" : "") + s + NBSP + "₸";
}

// plural(5, "блюдо", "блюда", "блюд") -> "блюд"
export function plural(n, one, few, many) {
  const a = Math.abs(Math.trunc(n)) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}

// Для поиска: нижний регистр, ё = е, без знаков препинания
export function normText(s) {
  return String(s ?? "").toLowerCase().replace(/ё/g, "е").replace(/[^a-zа-я0-9]+/g, " ").trim();
}

// Маскирование контактов для публичных страниц: полный телефон, адрес и почта там не нужны
export function maskPhone(d10) {
  const d = String(d10 ?? "").replace(/\D/g, "");
  return d.length === 10 ? `+7 (${d.slice(0, 3)}) ***-**-${d.slice(8)}` : "•••";
}
export function maskAddress(street) {
  const lead = String(street ?? "").split(/\d/)[0].replace(/[\s,.]+$/, "").trim();
  return (lead ? lead + " " : "") + "•••";   // остаётся название улицы, дом, квартира и прочее скрыты
}
export function maskEmail(email) {
  const m = /^([^@\s])[^@\s]*@(.+)$/.exec(String(email ?? ""));
  return m ? `${m[1]}***@${m[2]}` : "•••";
}
