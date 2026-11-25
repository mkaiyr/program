// Короткие уведомления; контейнер объявляется скринридерам (role="status")
let box = null;
export function toast(message, kind) {
  if (!box) {
    box = document.createElement("div"); box.className = "toasts"; box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite"); document.body.appendChild(box);
  }
  const el = document.createElement("div"); el.className = "toast" + (kind ? " toast--" + kind : ""); el.textContent = message; box.appendChild(el);
  setTimeout(() => { el.classList.add("is-leaving"); setTimeout(() => el.remove(), 250); }, 2600);
}
