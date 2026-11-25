import { url } from "../core.js";
import { isAdmin, login } from "../admin-store.js";
import { mountLayout } from "../layout.js";

mountLayout();
const mount = document.getElementById("login");
const out = new URLSearchParams(location.search).get("out");
mount.innerHTML = `<div class="state card login-card">
  ${out ? '<p class="alert alert--ok" role="status" id="logged-out">Вы вышли из демо-админки.</p>' : ""}
  ${isAdmin() ? '<p class="alert alert--ok" role="status" id="already">Вход в демо-админку уже выполнен в этом браузере.</p>' : ""}
  <p class="muted">Вход нужен только для показа: пароля нет, проверки личности нет.</p>
  <button type="button" class="btn btn--primary btn--block" id="login-btn">Войти как демо-админ</button>
  <p class="field-error" id="login-err" role="alert" hidden>Не удалось войти: хранилище браузера недоступно.</p>
  <a class="btn btn--ghost" href="${url("index.html")}">← На сайт</a></div>`;
document.getElementById("login-btn").addEventListener("click", () => {
  if (login()) location.assign(url("admin/index.html"));
  else document.getElementById("login-err").hidden = false;
});
