"""Окно администратора: управление меню.

Скрытие кнопки для кассира — только удобство. Права проверяются в Supabase
(функции admin_* и политика menu_select_admin из sql/02_admin_menu.sql).
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import ui_theme as theme
from db import STATIONS, AppError, check_menu_item, parse_menu_code
from search import search
from ui_order import fmt_money

STATUS_TEXT = {True: "Есть", False: "Нет"}
STATION_BY_LABEL = {label: key for key, label in STATIONS.items()}


class MenuAdminWindow(tk.Toplevel):
    def __init__(self, master, db, on_close, on_auth_error):
        super().__init__(master)
        self.db = db
        self.on_close = on_close
        self.on_auth_error = on_auth_error
        self.items = {}         # id -> {id, name, category, price, is_available}
        self.editing_id = None  # None — добавление новой позиции
        self.refill_form = False  # заполнить форму при следующем выборе строки (после загрузки с сервера)
        self.busy = False
        self.closed = False  # окно уничтожено: результаты фоновых запросов игнорируются

        self.title("Управление меню")
        self.minsize(760, 600)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._build()
        self.grab_set()  # модальное окно: касса недоступна, пока открыто меню
        self.reload()

    # ---------- Разметка ----------

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Управление меню", style="H1.TLabel").pack(side="left")
        self.close_btn = ttk.Button(top, text="Закрыть", command=self.close)
        self.close_btn.pack(side="right")
        self.reload_btn = ttk.Button(top, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=6)

        search_row = ttk.Frame(root)
        search_row.pack(fill="x", pady=(0, 8))
        ttk.Label(search_row, text="Поиск по коду или названию:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._render_tree())
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var, width=28)
        self.search_entry.pack(side="left", padx=8)
        ttk.Button(search_row, text="✕", width=2, command=lambda: self.search_var.set("")).pack(side="left")
        self.found_label = ttk.Label(search_row, text="", style="Muted.TLabel")
        self.found_label.pack(side="right")

        cols = ("code", "name", "category", "station", "price", "status")
        # Небольшая запрошенная высота: таблица растягивается, если есть место, но не вытесняет форму
        self.tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="browse", height=5)
        for col, title, width, anchor in (("code", "Код", 70, "center"), ("name", "Позиция", 220, "w"),
                                          ("category", "Категория", 120, "w"), ("station", "Станция", 80, "center"),
                                          ("price", "Цена", 110, "e"), ("status", "В продаже", 80, "center")):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.tag_configure("off", foreground=theme.DISABLED_FG)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        list_btns = ttk.Frame(root)
        list_btns.pack(fill="x", pady=6)
        self.new_btn = ttk.Button(list_btns, text="Новая позиция", command=self.new_item)
        self.new_btn.pack(side="left")
        self.toggle_btn = ttk.Button(list_btns, text="Есть / Нет", command=self.toggle_available)
        self.toggle_btn.pack(side="left", padx=6)

        form = ttk.LabelFrame(root, text="Новая позиция", padding=8)
        form.pack(fill="x")
        self.form = form
        # Две колонки полей: форма помещается в окно вместе с таблицей
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        ttk.Label(form, text="Код:").grid(row=0, column=0, sticky="w", pady=2)
        code_row = ttk.Frame(form)
        code_row.grid(row=0, column=1, sticky="w", padx=(6, 12), pady=2)
        self.code_entry = ttk.Entry(code_row, width=10)
        self.code_entry.pack(side="left")
        self.code_hint = ttk.Label(code_row, text="", style="Muted.TLabel")
        self.code_hint.pack(side="left", padx=8)
        # Где готовится: позиции бара уходят на экран бара, остальные — на кухню
        ttk.Label(form, text="Готовится:").grid(row=0, column=2, sticky="w", pady=2)
        self.station_box = ttk.Combobox(form, values=list(STATION_BY_LABEL), state="readonly", width=12)
        self.station_box.grid(row=0, column=3, sticky="w", padx=(6, 0), pady=2)

        ttk.Label(form, text="Название:").grid(row=1, column=0, sticky="w", pady=2)
        self.name_entry = ttk.Entry(form)
        self.name_entry.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=2)

        ttk.Label(form, text="Категория:").grid(row=2, column=0, sticky="w", pady=2)
        self.category_box = ttk.Combobox(form)
        self.category_box.grid(row=2, column=1, sticky="ew", padx=(6, 12), pady=2)
        ttk.Label(form, text="Цена, ₸:").grid(row=2, column=2, sticky="w", pady=2)
        self.price_entry = ttk.Entry(form, width=14)
        self.price_entry.grid(row=2, column=3, sticky="w", padx=(6, 0), pady=2)

        self.save_btn = ttk.Button(form, text="Добавить", style="Primary.TButton", command=self.save)
        self.save_btn.grid(row=3, column=3, sticky="e", pady=(6, 0))
        self.price_entry.bind("<Return>", lambda _e: self.save())

        self.status = ttk.Label(root, text="", style="Status.TLabel", wraplength=600)
        self.status.pack(fill="x", pady=(6, 0))

        self.new_item()

    # ---------- Фоновые запросы ----------

    def _run(self, task, on_ok, on_err):
        """Выполняет сетевой запрос в потоке, результат обрабатывает в потоке Tk."""
        self._set_busy(True)
        # Результат передаётся через корневое окно: при выходе из аккаунта это окно
        # уничтожается вместе с кассой, и команда after, поставленная на него, исчезла бы
        root = self._root()

        def deliver(callback, value):
            try:
                root.after(0, self._finish, callback, value)
            except (RuntimeError, tk.TclError):
                pass  # приложение уже закрыто

        def worker():
            try:
                result = task()
            except AppError as e:
                deliver(on_err, e)
            except Exception as e:  # непредвиденная ошибка не должна молча ронять поток
                deliver(on_err, AppError(f"Непредвиденная ошибка: {e}"))
            else:
                deliver(on_ok, result)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, callback, value):
        if self.closed:
            return  # окно закрыли (выход из аккаунта), пока шёл запрос: виджетов уже нет
        self._set_busy(False)
        callback(value)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.save_btn, self.toggle_btn, self.new_btn, self.reload_btn, self.close_btn):
            btn.config(state=state)
        if not busy:
            self._update_toggle_btn()

    def _show_status(self, text, color="black"):
        self.status.config(text=text, foreground=theme.message_color(color))

    # ---------- Список ----------

    def reload(self, select_id=None, done_text=None):
        if self.busy:
            return
        self._show_status("Загрузка меню...", "gray")
        self._run(self.db.load_all_menu, lambda items: self._loaded(items, select_id, done_text),
                  self._handle_error)

    def _loaded(self, items, select_id, done_text):
        self.items = {item["id"]: item for item in items}
        self.category_box["values"] = sorted({item["category"] for item in items})

        if select_id is None:
            select_id = self.editing_id
        if select_id in self.items:
            self.editing_id = select_id
            self.refill_form = True  # показать в форме сохранённые на сервере значения
            self._render_tree()
            if not self.tree.exists(str(select_id)):  # скрыта поиском — форма всё равно с данными сервера
                self.refill_form = False
                self._show_in_form(self.items[select_id])
        else:
            self._render_tree()
            self.new_item()
        if done_text:
            self._show_status(done_text, "green")
        else:
            off = sum(1 for item in items if not item["is_available"])
            self._show_status(f"Позиций: {len(items)}, из них не в продаже: {off}", "gray")

    def _render_tree(self):
        """Таблица с учётом поиска по коду или названию (локально, без запроса к серверу)."""
        if not hasattr(self, "tree"):
            return
        found = search(self.items.values(), self.search_var.get())
        self.tree.delete(*self.tree.get_children())
        for item in found:
            self.tree.insert("", "end", iid=str(item["id"]), tags=() if item["is_available"] else ("off",),
                             values=(item.get("code", ""), item["name"], item["category"],
                                     STATIONS.get(item.get("station", "kitchen"), ""), fmt_money(item["price"]),
                                     STATUS_TEXT[item["is_available"]]))
        if self.search_var.get().strip():
            self.found_label.config(text=f"Найдено: {len(found)}" if found else "Ничего не найдено")
        else:
            self.found_label.config(text="")
        # Редактируемая позиция остаётся выбранной, если видна
        if self.editing_id is not None and self.tree.exists(str(self.editing_id)):
            self.tree.selection_set(str(self.editing_id))
            self.tree.see(str(self.editing_id))
        self._update_toggle_btn()

    def _selected_id(self):
        selected = self.tree.selection()
        return int(selected[0]) if selected else None

    def _on_select(self):
        item = self.items.get(self._selected_id())
        if item is None:
            return
        if item["id"] == self.editing_id and not self.refill_form:
            # та же позиция снова выбрана после поиска — не затирать несохранённый ввод
            self._update_toggle_btn()
            return
        self.refill_form = False
        self._show_in_form(item)

    def _show_in_form(self, item):
        self.editing_id = item["id"]
        self.form.config(text=f"Изменение: {item['name']}")
        self.save_btn.config(text="Сохранить изменения")
        self._fill_form(item["name"], item["category"], f"{item['price']:.2f}")
        self._set_code_field(str(item.get("code", "")), editable=False)
        self.station_box.set(STATIONS[item.get("station", "kitchen")])
        self._update_toggle_btn()

    def _set_code_field(self, value, editable):
        self.code_entry.config(state="normal")
        self.code_entry.delete(0, tk.END)
        self.code_entry.insert(0, value)
        # Код постоянный: вручную задаётся только при создании
        self.code_entry.config(state="normal" if editable else "readonly")
        self.code_hint.config(text="пусто — назначится автоматически" if editable else "код постоянный")

    def _update_toggle_btn(self):
        item = self.items.get(self._selected_id())
        if item is None:
            self.toggle_btn.config(text="Есть / Нет", state="disabled")
        else:
            self.toggle_btn.config(text="Снять с продажи (Нет)" if item["is_available"]
                                   else "Вернуть в продажу (Есть)", state="normal")

    def _fill_form(self, name, category, price):
        for widget, value in ((self.name_entry, name), (self.category_box, category), (self.price_entry, price)):
            widget.delete(0, tk.END)
            widget.insert(0, value)

    def new_item(self):
        if self.busy:
            return
        self.editing_id = None
        self.tree.selection_remove(*self.tree.selection())
        self.form.config(text="Новая позиция")
        self.save_btn.config(text="Добавить")
        self._fill_form("", "", "")
        self._set_code_field("", editable=True)
        self.station_box.set(STATIONS["kitchen"])
        self._update_toggle_btn()
        self.name_entry.focus_set()

    # ---------- Сохранение ----------

    def save(self):
        if self.busy:
            return
        name, category, price_text = self.name_entry.get(), self.category_box.get(), self.price_entry.get()
        code_text = self.code_entry.get() if self.editing_id is None else ""
        station = STATION_BY_LABEL.get(self.station_box.get(), "kitchen")
        try:
            clean = check_menu_item(name, category, price_text)
            parse_menu_code(code_text)
        except AppError as e:
            self._show_status(str(e), "red")
            messagebox.showwarning("Проверьте данные", str(e), parent=self)
            return

        item_id = self.editing_id
        if item_id is None:
            self._show_status("Добавление позиции...", "gray")
            self._run(lambda: self.db.add_menu_item(name, category, price_text, code_text, station),
                      lambda row: self._saved(row, "Позиция добавлена"), self._handle_error)
            return

        old = self.items.get(item_id)
        if old and (old["name"], old["category"], old["price"]) == clean and old.get("station", "kitchen") == station:
            self._show_status("Изменений нет.", "gray")
            return
        self._show_status("Сохранение...", "gray")
        self._run(lambda: self.db.update_menu_item(item_id, name, category, price_text, station),
                  lambda row: self._saved(row, "Изменения сохранены"), self._handle_error)

    def toggle_available(self):
        if self.busy:
            return
        item = self.items.get(self._selected_id())
        if item is None:
            self._show_status("Выберите позицию в списке.", "orange")
            return
        available = not item["is_available"]
        if not available and not messagebox.askyesno(
                "Снять с продажи", f"Снять «{item['name']}» с продажи?\nКассиры перестанут её видеть.",
                parent=self):
            return
        self._show_status("Сохранение...", "gray")
        message = "Позиция возвращена в продажу" if available else "Позиция снята с продажи"
        self._run(lambda: self.db.set_menu_item_available(item["id"], available),
                  lambda row: self._saved(row, message), self._handle_error)

    def _saved(self, row, message):
        code = f" (код {row['code']})" if row.get("code") else ""
        text = (f"{message}: {row['name']}{code} — {fmt_money(row['price'])}, "
                f"в продаже: {STATUS_TEXT[row['is_available']]}")
        self.editing_id = row["id"]
        self._show_status(text, "green")
        messagebox.showinfo("Сохранено", text, parent=self)
        self.reload(select_id=row["id"], done_text=text)

    # ---------- Ошибки и закрытие ----------

    def _handle_error(self, error):
        self._show_status(str(error), "red")
        if error.code == "auth":
            messagebox.showerror("Доступ", str(error), parent=self)
            self.destroy()
            self.on_auth_error()
        elif error.code == "forbidden":
            messagebox.showerror("Нет прав", str(error), parent=self)
            self.close()
        elif error.code == "network":
            messagebox.showerror("Нет связи", str(error), parent=self)
        elif error.code == "invalid":
            messagebox.showwarning("Не сохранено", str(error), parent=self)
        else:
            messagebox.showerror("Ошибка", str(error), parent=self)

    def close(self):
        if self.busy:
            return
        self.grab_release()
        self.destroy()
        self.on_close()

    def destroy(self):
        self.closed = True
        super().destroy()
