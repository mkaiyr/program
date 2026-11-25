import { normalizeOverrides } from "./admin-logic.js";

// Демонстрационный вход и переопределения доступности блюд. Всё хранится в localStorage этого браузера и НЕ защищено.
export const ADMIN_KEY = "bistro.admin.session.v1";
export const AVAIL_KEY = "bistro.menu.availability.v1";

export function isAdmin() { try { const v = JSON.parse(localStorage.getItem(ADMIN_KEY) || "null"); return !!v && v.demoAdmin === true; } catch (e) { return false; } }
export function login() { try { localStorage.setItem(ADMIN_KEY, JSON.stringify({ demoAdmin: true, at: new Date().toISOString() })); return isAdmin(); } catch (e) { return false; } }
export function logout() { try { localStorage.removeItem(ADMIN_KEY); } catch (e) { /* нечего удалять */ } }

export function loadOverrides(baseById) {
  try { const v = localStorage.getItem(AVAIL_KEY); return v == null ? {} : normalizeOverrides(JSON.parse(v), baseById || null); } catch (e) { return {}; }
}
export function saveOverrides(map) {
  try { const s = JSON.stringify(map); localStorage.setItem(AVAIL_KEY, s); return localStorage.getItem(AVAIL_KEY) === s; } catch (e) { return false; }
}
