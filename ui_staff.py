"""Раздел «Сотрудники»: список, создание, изменение, смена пароля, активация (sql/13_staff_management.sql).

Вход остаётся прежним (код + пароль через Supabase Auth). Права проверяет сервер:
кнопки видны только администратору, но решает функция admin_*.
"""

import tkinter as tk
from tkinter import messagebox, ttk

import ui_theme as theme
from db import AppError, check_password, check_staff
from ui_base import Panel
from ui_dialogs import FormDialog

ROLE_LABELS = {"admin": "Администратор", "cashier": "Кассир", "kitchen": "Кухня", "bar": "Бар"}
ROLE_BY_LABEL = {label: key for key, label in ROLE_LABELS.items()}
ALL_ROLES = "Все роли"


class StaffPanel(Panel):
    def __init__(self, master, db, on_auth_error, current_user_id=None):
        super().__init__(master, db, on_auth_error)
        self.current_user_id = current_user_id
        self.staff = {}
        self._build()
        self.reload()

    def _build(self):
        head, right = theme.header(self, "Сотрудники", "Учётные записи, роли и доступ")
        head.pack(fill="x")
        self.new_btn = ttk.Button(right, text="＋ Новый сотрудник", style="Primary.TButton", command=self.create)
        self.new_btn.pack(side="right")
        self.reload_btn = ttk.Button(right, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=8)

        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=theme.GAP)
        ttk.Label(filters, text="Поиск по имени:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._render())
        ttk.Entry(filters, textvariable=self.search_var, width=24).pack(side="left", padx=8)
        ttk.Label(filters, text="Роль:").pack(side="left", padx=(12, 0))
        self.role_box = ttk.Combobox(filters, values=[ALL_ROLES, *ROLE_LABELS.values()], state="readonly", width=16)
        self.role_box.set(ALL_ROLES)
        self.role_box.pack(side="left", padx=8)
        self.role_box.bind("<<ComboboxSelected>>", lambda _e: self._render())
        self.count_label = ttk.Label(filters, text="", style="Muted.TLabel")
        self.count_label.pack(side="right")

        frame, self.tree = theme.make_table(
            self, [("name", "Имя", 240, "w"), ("code", "Код", 90, "center"), ("role", "Роль", 140, "w"),
                   ("status", "Статус", 110, "center")], height=10, tags={"off": theme.DISABLED_FG})
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.tree.bind("<Double-1>", lambda _e: self.edit())

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(theme.GAP, 0))
        self.edit_btn = ttk.Button(actions, text="Изменить", command=self.edit)
        self.edit_btn.pack(side="left")
        self.pwd_btn = ttk.Button(actions, text="Сменить пароль", command=self.change_password)
        self.pwd_btn.pack(side="left", padx=8)
        self.toggle_btn = ttk.Button(actions, text="Отключить", command=self.toggle_active)
        self.toggle_btn.pack(side="left")
        self.build_status(self).pack(fill="x", pady=(8, 0))
        self.busy_widgets += [self.new_btn, self.reload_btn]
        self._update_buttons()

    def set_busy(self, busy):
        super().set_busy(busy)
        if not busy:
            self._update_buttons()
        else:
            for btn in (self.edit_btn, self.pwd_btn, self.toggle_btn):
                btn.config(state="disabled")

    # ---------- Список ----------

    def reload(self, done_text=None):
        if self.busy:
            return
        self.show_status("Загрузка сотрудников...", "gray")
        self.run(self.db.load_staff, lambda rows: self._loaded(rows, done_text))

    def _loaded(self, rows, done_text):
        self.staff = {row["id"]: row for row in rows}
        self._render()
        self.show_status(done_text or "", "green" if done_text else "gray")

    def visible(self):
        query = self.search_var.get().strip().lower()
        role = ROLE_BY_LABEL.get(self.role_box.get())
        return [row for row in self.staff.values()
                if (not query or query in row["full_name"].lower()) and (role is None or row["role"] == role)]

    def _render(self):
        selected = self._selected_id()
        self.tree.delete(*self.tree.get_children())
        rows = self.visible()
        for row in rows:
            self.tree.insert("", "end", iid=row["id"], values=(
                row["full_name"], row["employee_code"] or "—", ROLE_LABELS[row["role"]],
                "Активен" if row["is_active"] else "Отключён"), tags=() if row["is_active"] else ("off",))
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        self.count_label.config(text=f"Показано {len(rows)} из {len(self.staff)}")
        self._update_buttons()

    def _selected_id(self):
        selected = self.tree.selection()
        return selected[0] if selected else None

    def _selected(self):
        return self.staff.get(self._selected_id())

    def _update_buttons(self):
        row = self._selected()
        state = "normal" if row and not self.busy else "disabled"
        for btn in (self.edit_btn, self.pwd_btn, self.toggle_btn):
            btn.config(state=state)
        if row:
            self.toggle_btn.config(text="Отключить" if row["is_active"] else "Активировать")

    # ---------- Действия ----------

    def create(self):
        if self.busy:
            return

        def submit(v):
            try:
                check_staff(v["name"], ROLE_BY_LABEL.get(v["role"]), v["code"])
                check_password(v["password"])
            except AppError as e:
                return str(e)
            self.show_status("Создание сотрудника...", "gray")
            role = ROLE_BY_LABEL[v["role"]]
            self.run(lambda: self.db.create_staff(v["name"], role, v["code"], v["password"]),
                     lambda row: self.reload(f"Сотрудник «{row['full_name']}» создан. Код для входа: {row['employee_code']}."))
            return None

        FormDialog(self, "Новый сотрудник",
                   [("name", "Имя:", "", False), ("role", "Роль:", ROLE_LABELS["cashier"], False, list(ROLE_LABELS.values())),
                    ("code", "Код (4–6 цифр):", "", False), ("password", "Пароль (от 8):", "", True)],
                   submit, ok_text="Создать")

    def edit(self):
        row = self._selected()
        if row is None or self.busy:
            return

        def submit(v):
            try:
                check_staff(v["name"], ROLE_BY_LABEL.get(v["role"]), v["code"])
            except AppError as e:
                return str(e)
            role = ROLE_BY_LABEL[v["role"]]
            self.show_status("Сохранение...", "gray")
            self.run(lambda: self.db.update_staff(row["id"], v["name"], role, v["code"], row["is_active"]),
                     lambda _r: self.reload("Изменения сохранены."))
            return None

        FormDialog(self, "Сотрудник", [
            ("name", "Имя:", row["full_name"], False),
            ("role", "Роль:", ROLE_LABELS[row["role"]], False, list(ROLE_LABELS.values())),
            ("code", "Код (4–6 цифр):", row["employee_code"], False)], submit, ok_text="Сохранить",
            hint="Роль и код действуют со следующего входа сотрудника.")

    def change_password(self):
        row = self._selected()
        if row is None or self.busy:
            return

        def submit(v):
            if v["password"] != v["repeat"]:
                return "Пароли не совпадают."
            try:
                check_password(v["password"])
            except AppError as e:
                return str(e)
            self.show_status("Смена пароля...", "gray")
            self.run(lambda: self.db.set_staff_password(row["id"], v["password"]),
                     lambda _r: self._password_changed(row))
            return None

        FormDialog(self, f"Пароль: {row['full_name']}",
                   [("password", "Новый пароль:", "", True), ("repeat", "Повторите:", "", True)], submit,
                   ok_text="Сменить")

    def _password_changed(self, row):
        self.show_status(f"Пароль сотрудника «{row['full_name']}» изменён.", "green")

    def toggle_active(self):
        row = self._selected()
        if row is None or self.busy:
            return
        activate = not row["is_active"]
        if not activate and not messagebox.askyesno(
                "Отключить сотрудника",
                f"Отключить «{row['full_name']}»? Войти он больше не сможет, текущий сеанс завершится "
                "при следующем запросе. Историю заказов это не затрагивает.", parent=self):
            return
        self.show_status("Сохранение...", "gray")
        self.run(lambda: self.db.update_staff(row["id"], row["full_name"], row["role"], row["employee_code"], activate),
                 lambda _r: self.reload("Сотрудник активирован." if activate else "Сотрудник отключён."))
