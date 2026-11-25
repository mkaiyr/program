// Безопасная обёртка над localStorage: приватный режим и переполненное хранилище не должны ломать сайт (тогда данные живут до закрытия вкладки).
const mem = {};
export function readJSON(key) {
  try { const v = localStorage.getItem(key); return v == null ? (mem[key] ?? null) : JSON.parse(v); } catch (e) { return mem[key] ?? null; }
}
export function writeJSON(key, value) {
  mem[key] = value;
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; }
}
