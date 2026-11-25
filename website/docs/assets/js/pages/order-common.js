import { esc } from "../format.js";
// Общие куски страниц заказа: поиск номера в адресе и подписи статусов
export const orderNumberFromUrl = () => new URLSearchParams(location.search).get("n") || "";
export const nameOf = (list, id) => (list.find((x) => x.id === id) || { name: id }).name;
export const fmtDate = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleString("ru-RU", { dateStyle: "long", timeStyle: "short" }); };
export const payBadge = (status, S) => `<span class="badge ${status === "paid" ? "badge--ok" : status === "failed" ? "badge--danger" : "badge--warn"}">${esc(nameOf(S.paymentStatuses, status))}</span>`;
export const notFoundHTML = (S, corrupt) => `<div class="container"><h1 class="page-title">Заказ не найден</h1><div class="state card"><h2>Такого демонстрационного заказа нет в этом браузере</h2><p class="muted">${corrupt ? "Данные заказов в браузере повреждены, поэтому заказ прочитать не удалось. " : ""}Демонстрационные заказы хранятся только в браузере, где они были оформлены. ${esc(S.notices.demoBanner)}.</p><a class="btn btn--primary" href="orders.html">Мои заказы</a></div></div>`;
