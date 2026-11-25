// Проверка данных формы оформления заказа (без DOM). Возвращает { ok, errors: {поле: текст}, values } — values уже очищены от лишнего.
export const NAME_RE = /^[\p{L}][\p{L}\s.'’-]{1,59}$/u;

// Телефон Казахстана и России: +7 / 8 / без кода; итог — 10 национальных цифр или null
export function normalizePhone(raw) {
  let d = String(raw ?? "").replace(/\D/g, "");
  if (d.length === 11 && (d[0] === "7" || d[0] === "8")) d = d.slice(1);
  if (d.length !== 10) return null;
  if (!/[3-9]/.test(d[0])) return null;
  if (/^(\d)\1+$/.test(d)) return null;
  return d;
}
export const formatPhone = (d10) => (d10 ? `+7 (${d10.slice(0, 3)}) ${d10.slice(3, 6)}-${d10.slice(6, 8)}-${d10.slice(8, 10)}` : "");

const clean = (v, max) => String(v ?? "").replace(/\s+/g, " ").trim().slice(0, max);
const cleanMultiline = (v, max) => String(v ?? "").replace(/[ \t]+/g, " ").trim().slice(0, max);
export const ALLERGY_ANSWERS = ["no", "yes", "unsure"];

export function validateCheckout(f, settings, allergenIds) {
  f = f || {};
  const errors = {}, v = {};
  v.mode = f.mode === "delivery" ? "delivery" : f.mode === "pickup" ? "pickup" : "";
  if (!v.mode) errors.mode = "Выберите способ получения";
  v.name = clean(f.name, 60);
  if (!v.name) errors.name = "Укажите имя";
  else if (!NAME_RE.test(v.name)) errors.name = "Имя: от 2 до 60 букв";
  const ph = normalizePhone(f.phone);
  v.phone = ph;
  if (!String(f.phone ?? "").trim()) errors.phone = "Укажите номер телефона";
  else if (!ph) errors.phone = "Введите номер в формате +7 700 123-45-67";
  v.email = clean(f.email, 80);
  if (v.email && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v.email)) errors.email = "Проверьте адрес электронной почты или оставьте поле пустым";
  v.comment = cleanMultiline(f.comment, 300);
  if (v.mode === "delivery") {
    v.street = clean(f.street, 120);
    if (!v.street) errors.street = "Укажите улицу и дом";
    else if (v.street.length < 5 || !/\d/.test(v.street)) errors.street = "Укажите улицу и номер дома, например: Абая 10";
    v.apt = clean(f.apt, 20); v.entrance = clean(f.entrance, 10); v.floor = clean(f.floor, 10); v.intercom = clean(f.intercom, 20);
    v.courierComment = cleanMultiline(f.courierComment, 200);
  } else if (v.mode === "pickup") {
    const ids = (settings.pickup?.timeOptions || []).map((o) => o.id);
    v.pickupTime = ids.includes(f.pickupTime) ? f.pickupTime : "";
    if (!v.pickupTime) errors.pickupTime = "Выберите время самовывоза";
  }
  const methods = (settings.payments || []).map((p) => p.id);
  v.payment = methods.includes(f.payment) ? f.payment : "";
  if (!v.payment) errors.payment = "Выберите способ оплаты";
  const a = f.allergy || {};
  v.allergy = { answer: ALLERGY_ANSWERS.includes(a.answer) ? a.answer : "", allergens: [], other: "" };
  if (!v.allergy.answer) errors.allergy = "Ответьте на вопрос об аллергии";
  else if (v.allergy.answer === "yes") {
    const known = new Set(allergenIds || []);
    v.allergy.allergens = [...new Set(Array.isArray(a.allergens) ? a.allergens : [])].filter((x) => known.has(x));
    v.allergy.other = clean(a.other, 100);
    if (!v.allergy.allergens.length && !v.allergy.other) errors.allergy = "Отметьте аллергены или опишите их в поле «другое»";
  }
  return { ok: Object.keys(errors).length === 0, errors, values: v };
}
