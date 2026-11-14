"""Окно администратора: управление меню и решения по позициям кухни и бара.

Скрытие кнопки для кассира — только удобство. Права проверяются в Supabase
(функции admin_* и политика menu_select_admin из sql/02_admin_menu.sql).
Позиции, предложенные станциями, ждут решения: «Подтвердить» или «Отклонить»
(admin_approve_menu_item / admin_reject_menu_item, sql/09_station_menu.sql).
"""

import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import ui_theme as theme
from db import (MAX_REJECT_REASON, MENU_STATUSES, STATIONS, AppError, check_menu_item, menu_status,
                parse_menu_code, unexpected_error)
from search import search
from ui_order import fmt_money

STATION_BY_LABEL = {label: key for key, label in STATIONS.items()}
TOGGLEABLE = ("on_sale", "out_of_stock")  # «Есть / Нет» — только у подтверждённых позиций


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
        self.pending_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(search_row, text="Только ждущие решения", variable=self.pending_only,
                        command=self._render_tree).pack(side="right", padx=12)

        cols = ("code", "name", "category", "station", "price", "status")
        # Небольшая запрошенная высота: таблица растягивается, если есть место, но не вытесняет форму
        self.tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="browse", height=5)
        for col, title, width, anchor in (("code", "Код", 70, "center"), ("name", "Позиция", 220, "w"),
                                          ("category", "Категория", 120, "w"), ("station", "Станция", 80, "center"),
                                          ("price", "Цена", 110, "e"), ("status", "Статус", 120, "center")):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.tag_configure("off", foreground=theme.DISABLED_FG)
        self.tree.tag_configure("pending", foreground=theme.WARNING)
        self.tree.tag_configure("rejected", foreground=theme.DISABLED_FG)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        list_btns = ttk.Frame(root)
        list_btns.pack(fill="x", pady=6)
        self.new_btn = ttk.Button(list_btns, text="Новая позиция", command=self.new_item)
        self.new_btn.pack(side="left")
        self.toggle_btn = ttk.Button(list_btns, text="Есть / Нет", command=self.toggle_available)
        self.toggle_btn.pack(side="left", padx=6)
        # Решение по позиции, предложенной кухней или баром
        self.reject_btn = ttk.Button(list_btns, text="Отклонить", style="Danger.TButton", command=self.reject)
        self.reject_btn.pack(side="right")
        self.approve_btn = ttk.Button(list_btns, text="Подтвердить", style="Success.TButton", command=self.approve)
        self.approve_btn.pack(side="right", padx=6)

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
                deliver(on_err, unexpected_error(e))
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
        for btn in (self.save_btn, self.toggle_btn, self.new_btn, self.reload_btn, self.close_btn,
                    self.approve_btn, self.reject_btn):
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
            text = f"Позиций: {len(items)}, из них не в продаже: {off}"
            pending = sum(1 for item in items if menu_status(item) == "pending")
            if pending:
                text += f". Ждут решения: {pending} — отметьте «Только ждущие решения»"
            self._show_status(text, "orange" if pending else "gray")

    def _render_tree(self):
        """Таблица с учётом поиска по коду или названию (локально, без запроса к серверу)."""
        if not hasattr(self, "tree"):
            return
        items = self.items.values()
        if self.pending_only.get():
            items = [item for item in items if menu_status(item) == "pending"]
        found = search(items, self.search_var.get())
        self.tree.delete(*self.tree.get_children())
        for item in found:
            status = menu_status(item)
            tags = {"on_sale": (), "out_of_stock": ("off",)}.get(status, (status,))
            self.tree.insert("", "end", iid=str(item["id"]), tags=tags,
                             values=(item.get("code", ""), item["name"], item["category"],
                                     STATIONS.get(item.get("station", "kitchen"), ""), fmt_money(item["price"]),
                                     MENU_STATUSES[status]))
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
        if menu_status(item) == "rejected":
            self.form.config(text=f"Отклонена: {item['name']} — изменить нельзя")
        else:
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
        """Кнопки по состоянию позиции: наличие — у подтверждённых, решение — у ожидающих."""
        item = self.items.get(self._selected_id())
        status = menu_status(item) if item is not None else None
        if status in TOGGLEABLE:
            self.toggle_btn.config(text="Снять с продажи (Нет)" if item["is_available"]
                                   else "Вернуть в продажу (Есть)", state="normal")
        else:
            self.toggle_btn.config(text="Есть / Нет", state="disabled")
        decision = "normal" if status == "pending" else "disabled"
        self.approve_btn.config(state=decision)
        self.reject_btn.config(state=decision)
        # Отклонённую позицию сервер изменить не даст (ITEM_REJECTED) — кнопка сразу неактивна
        editing = self.items.get(self.editing_id)
        rejected = editing is not None and menu_status(editing) == "rejected"
        self.save_btn.config(state="disabled" if rejected or self.busy else "normal")

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
        if menu_status(item) not in TOGGLEABLE:
            return  # ожидающую позицию выводит в продажу решение, отклонённую — никак
        available = not item["is_available"]
        if not available and not messagebox.askyesno(
                "Снять с продажи", f"Снять «{item['name']}» с продажи?\nКассиры перестанут её видеть.",
                parent=self):
            return
        self._show_status("Сохранение...", "gray")
        message = "Позиция возвращена в продажу" if available else "Позиция снята с продажи"
        self._run(lambda: self.db.set_menu_item_available(item["id"], available),
                  lambda row: self._saved(row, message), self._handle_error)

    # ---------- Решения по позициям станций ----------

    def _selected_pending(self):
        item = self.items.get(self._selected_id())
        if item is None or menu_status(item) != "pending":
            self._show_status("Выберите позицию, которая ждёт решения.", "orange")
            return None
        return item

    def approve(self):
        if self.busy:
            return
        item = self._selected_pending()
        if item is None:
            return
        station = STATIONS.get(item.get("station", "kitchen"), "")
        answer = messagebox.askyesnocancel(
            "Подтвердить позицию",
            f"«{item['name']}» ({station}, {fmt_money(item['price'])}).\n\n"
            "Да — подтвердить и сразу в продажу.\nНет — подтвердить, но пока «нет в наличии».", parent=self)
        if answer is None:
            return
        self._show_status("Сохранение решения...", "gray")
        self._run(lambda: self.db.approve_menu_item(item["id"], answer),
                  lambda row: self._saved(row, "Позиция подтверждена"), self._handle_error)

    def reject(self):
        if self.busy:
            return
        item = self._selected_pending()
        if item is None:
            return
        reason = simpledialog.askstring(
            "Отклонить позицию",
            f"Отклонить «{item['name']}»? Позиция не попадёт в продажу.\n"
            f"Причина для станции (необязательно, до {MAX_REJECT_REASON} символов):", parent=self)
        if reason is None:
            return  # «Отмена»
        if len(reason.strip()) > MAX_REJECT_REASON:
            messagebox.showwarning("Причина", f"Не длиннее {MAX_REJECT_REASON} символов.", parent=self)
            return
        self._show_status("Сохранение решения...", "gray")
        self._run(lambda: self.db.reject_menu_item(item["id"], reason),
                  lambda row: self._saved(row, "Позиция отклонена"), self._handle_error)

    def _saved(self, row, message):
        code = f" (код {row['code']})" if row.get("code") else ""
        text = f"{message}: {row['name']}{code} — {fmt_money(row['price'])}, {MENU_STATUSES[menu_status(row)]}"
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
