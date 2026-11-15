"""Разделы административной оболочки: главная панель, меню, категории, настройки."""

import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

import ui_theme as theme
from db import (MENU_STATUSES, ORDER_STATUSES, AppError, category_not_empty_text, menu_status, sort_categories,
                to_local_time)
from money import SERVICE_PERCENT, fmt_money
from reports import period_range
from ui_base import Panel
from ui_dialogs import FormDialog


def _safe(call):
    """(результат, None) или (None, текст ошибки): один недоступный источник не ломает всю панель."""
    try:
        return call(), None
    except AppError as e:
        if e.code == "auth":
            raise
        return None, str(e)


class DashboardPanel(Panel):
    """Главная: выручка сегодня, заказы, средний чек, сотрудники, активная смена, последние заказы."""

    def __init__(self, master, db, on_auth_error):
        super().__init__(master, db, on_auth_error)
        head, right = theme.header(self, "Главная панель", date.today().strftime("Сегодня, %d.%m.%Y"))
        head.pack(fill="x")
        self.reload_btn = ttk.Button(right, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right")
        self.busy_widgets.append(self.reload_btn)

        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=theme.GAP)
        self.values = {}
        for col, (key, title) in enumerate((("revenue", "Выручка сегодня"), ("orders", "Заказов"),
                                            ("avg", "Средний чек"), ("staff", "Сотрудники"),
                                            ("shift", "Кассовая смена"))):
            cards.columnconfigure(col, weight=1, uniform="dash")
            box, value, hint = theme.stat_card(cards, title)
            box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
            self.values[key] = (value, hint)

        ttk.Label(self, text="Последние заказы", style="H2.TLabel").pack(anchor="w", pady=(theme.GAP, 6))
        frame, self.tree = theme.make_table(
            self, [("id", "№", 60, "center"), ("time", "Время", 140, "w"), ("cashier", "Кассир", 170, "w"),
                   ("total", "Итого", 110, "e"), ("status", "Статус", 120, "center")], height=8,
            tags={**theme.STATUS_COLORS, "voided": theme.DISABLED_FG})
        frame.pack(fill="both", expand=True)
        self.build_status(self).pack(fill="x", pady=(8, 0))
        self.reload()

    def reload(self):
        if self.busy:
            return
        self.show_status("Загрузка...", "gray")

        def task():
            start, end = period_range("Сегодня")
            return {"report": _safe(lambda: self.db.load_report(start, end)),
                    "staff": _safe(self.db.load_staff),
                    "shifts": _safe(lambda: self.db.shift_history(50)),
                    "orders": _safe(lambda: self.db.load_orders(limit=8))}
        self.run(task, self._loaded)

    def _loaded(self, data):
        problems = []
        report, err = data["report"]
        if report:
            self.values["revenue"][0].config(text=fmt_money(report["revenue"]))
            self.values["revenue"][1].config(text=f"надбавка {fmt_money(report['service_total'])}")
            self.values["orders"][0].config(text=str(report["orders_count"]))
            self.values["orders"][1].config(text=f"аннулировано: {report['voided_count']}")
            self.values["avg"][0].config(text=fmt_money(report["average_check"]))
            self.values["avg"][1].config(text="за сегодня")
        elif err:
            problems.append(err)
        staff, err = data["staff"]
        if staff is not None:
            active = [r for r in staff if r["is_active"]]
            self.values["staff"][0].config(text=str(len(active)))
            self.values["staff"][1].config(text=f"активных; всего {len(staff)}")
        elif err:
            problems.append(err)
        shifts, err = data["shifts"]
        if shifts is not None:
            open_shifts = [s for s in shifts if not s["closed_at"]]
            if open_shifts:
                first = open_shifts[0]
                self.values["shift"][0].config(text="Открыта")
                extra = f" (+{len(open_shifts) - 1})" if len(open_shifts) > 1 else ""
                self.values["shift"][1].config(text=f"{first['cashier_name']} с {first['opened_local'][-5:]}{extra}")
            else:
                self.values["shift"][0].config(text="Нет")
                self.values["shift"][1].config(text="ни одной открытой смены")
        elif err:
            problems.append(err)
        orders, err = data["orders"]
        self.tree.delete(*self.tree.get_children())
        if orders is not None:
            for o in orders:
                self.tree.insert("", "end", values=(
                    o["id"], o["created_at"], o["cashier_name"], fmt_money(o["total"]),
                    "Аннулирован" if o["voided"] else ORDER_STATUSES.get(o["status"], o["status"])),
                    tags=("voided" if o["voided"] else o["status"],))
        elif err:
            problems.append(err)
        if problems:
            self.show_status(" · ".join(dict.fromkeys(problems)), "orange")
        else:
            self.show_status("", "gray")


ALL_CATEGORIES_LABEL = "Все категории"
ARCHIVABLE = ("on_sale", "out_of_stock")  # архивировать можно только подтверждённые блюда


class MenuSectionPanel(Panel):
    """Управление меню: вкладки «Активные блюда» и «Архив»; редактор позиций — окно MenuAdminWindow.

    Все действия вызывают методы db.py (archive_menu_item, restore_menu_item, purge_menu_item), права и
    правила проверяет сервер (sql/15_menu_item_archive.sql). Панель ничего не решает сама: после отказа
    сервера список перечитывается.
    """

    def __init__(self, master, db, on_auth_error, on_menu_changed=None):
        super().__init__(master, db, on_auth_error)
        self.on_menu_changed = on_menu_changed
        self.items = []
        self.by_id = {}
        head, right = theme.header(self, "Управление меню", "Блюда, архив, цены, станции и решения по предложениям")
        head.pack(fill="x")
        self.edit_btn = ttk.Button(right, text="Редактор позиций", style="Primary.TButton", command=self.open_editor)
        self.edit_btn.pack(side="right")
        self.reload_btn = ttk.Button(right, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=8)
        self.busy_widgets += [self.edit_btn, self.reload_btn]

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=theme.GAP)
        ttk.Label(bar, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._render())
        ttk.Entry(bar, textvariable=self.search_var, width=22).pack(side="left", padx=8)
        ttk.Label(bar, text="Категория:").pack(side="left", padx=(8, 0))
        self.category_box = ttk.Combobox(bar, values=[ALL_CATEGORIES_LABEL], state="readonly", width=20)
        self.category_box.set(ALL_CATEGORIES_LABEL)
        self.category_box.pack(side="left", padx=8)
        self.category_box.bind("<<ComboboxSelected>>", lambda _e: self._render())
        self.counts_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self.counts_label.pack(side="right")
        self.pending_label = ttk.Label(bar, text="", foreground=theme.WARNING)
        self.pending_label.pack(side="right", padx=12)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        columns = [("code", "Код", 70, "center"), ("name", "Позиция", 240, "w"), ("category", "Категория", 140, "w"),
                   ("station", "Станция", 90, "center"), ("price", "Цена", 110, "e")]
        self.active_tree, self.active_empty, active_actions = self._tab(
            "Активные блюда", columns + [("status", "Статус", 130, "center")], {**theme.MENU_STATUS_COLORS})
        self.archive_tree, self.archive_empty, archive_actions = self._tab(
            "Архив", columns + [("archived", "В архиве с", 150, "center")], {})
        self.archive_btn = self._button(active_actions, "Архивировать", self.archive, "Danger.TButton")
        self.restore_btn = self._button(archive_actions, "Восстановить", self.restore, "Success.TButton")
        self.purge_btn = self._button(archive_actions, "Удалить навсегда", self.purge, "Danger.TButton")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._update_buttons())
        for tree in (self.active_tree, self.archive_tree):
            tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.build_status(self).pack(fill="x", pady=(8, 0))
        self._update_buttons()
        self.reload()

    # ---------- Построение ----------

    def _tab(self, title, columns, tags):
        """Вкладка: таблица, подпись «пусто» и строка кнопок. Возвращает (таблица, подпись, строка кнопок)."""
        frame = ttk.Frame(self.notebook, padding=(0, theme.GAP, 0, 0))
        self.notebook.add(frame, text=title)
        actions = ttk.Frame(frame)
        actions.pack(side="bottom", fill="x", pady=(theme.GAP, 0))
        empty = ttk.Label(frame, text="", style="Muted.TLabel")
        empty.pack(side="bottom", anchor="w", pady=(4, 0))
        table, tree = theme.make_table(frame, columns, height=11, tags=tags)
        table.pack(fill="both", expand=True)
        return tree, empty, actions

    def _button(self, actions, text, command, style):
        btn = ttk.Button(actions, text=text, style=style, command=command)
        btn.pack(side="left", padx=(0, 8))
        self.busy_widgets.append(btn)
        return btn

    # ---------- Данные ----------

    def reload(self, message=None, color="gray"):
        if self.busy:
            return
        self.show_status("Загрузка меню...", "gray")
        self.run(lambda: (self.db.load_all_menu(), self.db.load_categories(lenient=True)),
                 lambda result: self._loaded(*result, message, color))

    def _loaded(self, items, known_categories, message, color):
        self.items = items
        self.by_id = {item["id"]: item for item in items}
        categories = sort_categories([*known_categories, *(item["category"] for item in items)])
        self.category_box["values"] = [ALL_CATEGORIES_LABEL, *categories]
        if self.category_box.get() not in self.category_box["values"]:
            self.category_box.set(ALL_CATEGORIES_LABEL)
        self._render()
        active = sum(1 for i in items if not i.get("archived"))
        pending = sum(1 for i in items if not i.get("archived") and menu_status(i) == "pending")
        self.counts_label.config(text=f"Активных: {active} · в архиве: {len(items) - active}")
        self.pending_label.config(text=f"Ждут решения: {pending}" if pending else "")
        self.show_status(message or "", color)

    def visible(self, archived):
        """Блюда вкладки с учётом поиска по коду и названию и выбранной категории (локально)."""
        from search import search
        category = self.category_box.get()
        items = [i for i in self.items if bool(i.get("archived")) == archived
                 and category in (ALL_CATEGORIES_LABEL, i["category"])]
        return search(items, self.search_var.get())

    def _render(self):
        station = {"kitchen": "Кухня", "bar": "Бар"}
        filtered = bool(self.search_var.get().strip()) or self.category_box.get() != ALL_CATEGORIES_LABEL
        for archived, tree, empty in ((False, self.active_tree, self.active_empty),
                                      (True, self.archive_tree, self.archive_empty)):
            selected = tree.selection()
            tree.delete(*tree.get_children())
            rows = self.visible(archived)
            for item in rows:
                status = menu_status(item)
                values = [item.get("code") or "", item["name"], item["category"],
                          station.get(item.get("station"), "Кухня"), fmt_money(item["price"])]
                if archived:
                    values.append(to_local_time(item.get("archived_at")))
                else:
                    values.append(MENU_STATUSES[status])
                tree.insert("", "end", iid=str(item["id"]), values=values, tags=() if archived else (status,))
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])
            if rows:
                empty.config(text="")
            elif filtered:
                empty.config(text="Ничего не найдено — измените поиск или категорию.")
            else:
                empty.config(text="В архиве пока ничего нет." if archived else "Активных блюд нет.")
        self._update_buttons()

    # ---------- Выбор и кнопки ----------

    def _current(self):
        """(блюдо, в архиве ли вкладка) выбранной строки текущей вкладки; блюдо None, если ничего не выбрано."""
        archived = self.notebook.index(self.notebook.select()) == 1
        tree = self.archive_tree if archived else self.active_tree
        selected = tree.selection()
        return (self.by_id.get(int(selected[0])) if selected else None), archived

    def set_busy(self, busy):
        super().set_busy(busy)
        if not busy:
            self._update_buttons()

    def _update_buttons(self):
        if not hasattr(self, "purge_btn"):
            return
        item, archived = self._current()
        free = not self.busy
        self.archive_btn.config(state="normal" if free and item and not archived
                                and menu_status(item) in ARCHIVABLE else "disabled")
        self.restore_btn.config(state="normal" if free and item and archived else "disabled")
        self.purge_btn.config(state="normal" if free and item and archived else "disabled")
        if item and not archived and menu_status(item) not in ARCHIVABLE:
            self.show_status("Блюдо ждёт решения или отклонено: подтвердите или отклоните его в редакторе позиций; "
                             "в архив можно отправить только подтверждённые блюда.", "gray")

    # ---------- Действия (всё через db.py; решает сервер) ----------

    def _on_error(self, error):
        """Отказ сервера: показать причину, при устаревших данных перечитать список и сохранить сообщение."""
        if error.code in ("invalid", "forbidden", None) and not self.closed:
            self.show_status(str(error), "red")
            self.reload(str(error), "red")
            return
        self.handle_error(error)

    def _done(self, text):
        self.reload(text, "green")
        if self.on_menu_changed:
            self.on_menu_changed()

    def archive(self):
        item, archived = self._current()
        if self.busy or item is None or archived:
            return
        if not messagebox.askyesno(
                "Архивировать блюдо",
                f"Архивировать «{item['name']}»?\n\nБлюдо исчезнет из меню кассы и с экранов кухни и бара. "
                "Старые заказы и отчёты не изменятся. Из архива блюдо можно восстановить.", parent=self):
            return
        self.show_status("Архивация...", "gray")
        self.run(lambda: self.db.archive_menu_item(item["id"]),
                 lambda r: self._done(f"«{item['name']}» перенесено в архив." if r["changed"]
                                      else f"«{item['name']}» уже было в архиве."), self._on_error)

    def restore(self):
        item, archived = self._current()
        if self.busy or item is None or not archived:
            return
        self.show_status("Восстановление...", "gray")
        self.run(lambda: self.db.restore_menu_item(item["id"]),
                 lambda r: self._done(f"«{item['name']}» восстановлено и снова в продаже." if r["changed"]
                                      else f"«{item['name']}» не было в архиве."), self._on_error)

    def purge(self):
        """Удалить навсегда: сначала число заказов с блюдом, затем подтверждение, затем вызов сервера."""
        item, archived = self._current()
        if self.busy or item is None or not archived:
            return
        self.show_status("Проверка заказов...", "gray")
        self.run(lambda: self.db.count_menu_item_orders(item["id"]),
                 lambda count: self._confirm_purge(item, count), self._on_error)

    def _confirm_purge(self, item, orders_count):
        self.show_status("", "gray")
        if orders_count:
            history = (f"Блюдо есть в заказах: {orders_count}. В них название, цену и категорию блюда сохранят сами "
                       "заказы — история и отчёты не изменятся.")
        else:
            history = "В заказах это блюдо не встречается."
        if not messagebox.askyesno(
                "Удалить навсегда",
                f"Удалить навсегда «{item['name']}»?\n\n{history}\n\n"
                "Действие НЕОБРАТИМО: блюдо исчезнет из архива, восстановить его будет нельзя. "
                "Название и код освободятся.", icon="warning", default="no", parent=self):
            return
        self.show_status("Удаление...", "gray")
        self.run(lambda: self.db.purge_menu_item(item["id"]),
                 lambda r: self._done(f"«{item['name']}» удалено навсегда. Заказов с ним: {r['orders_count']}."),
                 self._on_error)

    def open_editor(self):
        from ui_admin import MenuAdminWindow
        MenuAdminWindow(self, self.db, on_close=self._editor_closed, on_auth_error=self.on_auth_error)

    def _editor_closed(self):
        if self.closed:
            return
        self.reload()
        if self.on_menu_changed:
            self.on_menu_changed()


class CategoriesPanel(Panel):
    """Категории меню (справочник menu_categories, sql/16): число блюд, добавление, переименование,
    объединение и удаление пустой категории."""

    def __init__(self, master, db, on_auth_error, on_menu_changed=None):
        super().__init__(master, db, on_auth_error)
        self.on_menu_changed = on_menu_changed
        self.counts = {}   # название -> [активных, в продаже, в архиве]
        head, right = theme.header(self, "Категории меню", "Справочник категорий")
        head.pack(fill="x")
        self.add_btn = ttk.Button(right, text="Добавить", style="Primary.TButton", command=self.add)
        self.add_btn.pack(side="right")
        self.delete_btn = ttk.Button(right, text="Удалить", command=self.delete)
        self.delete_btn.pack(side="right", padx=(8, 0))
        self.rename_btn = ttk.Button(right, text="Переименовать", command=self.rename)
        self.rename_btn.pack(side="right", padx=(8, 0))
        self.reload_btn = ttk.Button(right, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=(8, 0))
        self.busy_widgets += [self.add_btn, self.delete_btn, self.rename_btn, self.reload_btn]
        ttk.Label(self, text="Удалить можно только пустую категорию: блюда, в том числе архивные, сначала переносят "
                             "в другую категорию. Если новое имя при переименовании уже есть, категории объединяются "
                             "(это и есть перенос всех блюд сразу). Категории можно добавлять и напрямую в базе — "
                             "после «Обновить» они появятся здесь.",
                  style="Muted.TLabel", wraplength=800, justify="left").pack(anchor="w", pady=theme.GAP)
        frame, self.tree = theme.make_table(self, [("name", "Категория", 280, "w"), ("active", "Активных", 100, "center"),
                                                   ("sale", "В продаже", 100, "center"),
                                                   ("archived", "В архиве", 100, "center")], height=12)
        frame.pack(fill="both", expand=True)
        self.build_status(self).pack(fill="x", pady=(8, 0))
        self.reload()

    def reload(self, done_text=None):
        if self.busy:
            return
        self.show_status("Загрузка...", "gray")
        self.run(lambda: (self.db.load_all_menu(), self.db.load_categories()),
                 lambda result: self._loaded(*result, done_text))

    def _loaded(self, items, categories, done_text):
        # [активных, из них в продаже, в архиве]: архивные блюда считаются отдельно и в «активных» не участвуют.
        # Пустые категории справочника попадают в список с нулями.
        self.counts = {name: [0, 0, 0] for name in categories}
        for item in items:
            row = self.counts.setdefault(item["category"], [0, 0, 0])
            if item.get("archived"):
                row[2] += 1
            else:
                row[0] += 1
                row[1] += 1 if menu_status(item) == "on_sale" else 0
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for name in sort_categories(self.counts):
            self.tree.insert("", "end", iid=name, values=(name, *self.counts[name]))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self.show_status(done_text or f"Категорий: {len(self.counts)}", "green" if done_text else "gray")

    def _changed(self, done_text):
        if self.on_menu_changed:
            self.on_menu_changed()
        self.reload(done_text)

    def _selected(self):
        selected = self.tree.selection()
        if not selected or self.busy:
            self.show_status("Выберите категорию в списке.", "orange")
            return None
        return selected[0]

    def add(self):
        if self.busy:
            return

        def submit(values):
            name = values["name"].strip()
            if not name or len(name) > 50:
                return "Категория — от 1 до 50 символов."
            if name.casefold() in {n.casefold() for n in self.counts}:
                return "Категория с таким названием уже есть."
            self.show_status("Сохранение...", "gray")
            self.run(lambda: self.db.add_category(name), lambda r: self._changed(f"Категория «{r['name']}» добавлена."))
            return None

        FormDialog(self, "Новая категория", [("name", "Название:", "", False)], submit, ok_text="Добавить")

    def rename(self):
        old = self._selected()
        if old is None:
            return

        def submit(values):
            new = values["name"].strip()
            if not new or len(new) > 50:
                return "Категория — от 1 до 50 символов."
            if new == old:
                return "Имя не изменилось."
            self.show_status("Сохранение...", "gray")
            self.run(lambda: self.db.rename_category(old, new), lambda r: self._renamed(old, r))
            return None

        FormDialog(self, "Переименовать категорию", [("name", "Новое название:", old, False)], submit,
                   ok_text="Переименовать")

    def _renamed(self, old, result):
        self._changed(f"Категория «{old}» → «{result.get('category', '')}»: блюд изменено {result['updated']}.")

    def delete(self):
        name = self._selected()
        if name is None:
            return
        active, _sale, archived = self.counts.get(name, [0, 0, 0])
        if active + archived:
            # Сервер всё равно проверит; здесь — понятное объяснение до запроса
            messagebox.showwarning("Категорию удалить нельзя", category_not_empty_text(f"{active + archived};{archived}"),
                                   parent=self)
            return
        if not messagebox.askyesno("Удалить категорию", f"Удалить пустую категорию «{name}»?", parent=self):
            return
        self.show_status("Удаление...", "gray")
        self.run(lambda: self.db.delete_category(name), lambda r: self._changed(f"Категория «{r['name']}» удалена."),
                 lambda error: self._delete_failed(error))

    def _delete_failed(self, error):
        self.handle_error(error)
        if error.code == "invalid":   # например, блюдо добавили, пока диалог был открыт
            messagebox.showwarning("Категорию удалить нельзя", str(error), parent=self)
            self.reload()


class SettingsPanel(Panel):
    """Настройки: сведения о подключении и проверка применённых миграций (только чтение)."""

    def __init__(self, master, db, on_auth_error, profile, server_host=""):
        super().__init__(master, db, on_auth_error)
        head, _right = theme.header(self, "Настройки", "Параметры приложения и состояние базы данных")
        head.pack(fill="x")
        info = theme.card(self)
        info.pack(fill="x", pady=theme.GAP)
        for title, value in (("Вы вошли как", f"{profile['full_name']} (администратор)"),
                             ("Сервер", server_host or "—"),
                             ("Надбавка за обслуживание", f"{SERVICE_PERCENT:.0f}% — фиксированная, считается на сервере"),
                             ("Валюта", "тенге (₸), округление надбавки до целого тенге")):
            row = ttk.Frame(info, style="Card.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=title, style="CardMuted.TLabel", width=28).pack(side="left")
            ttk.Label(row, text=value, style="Card.TLabel").pack(side="left")

        box = theme.card(self)
        box.pack(fill="x")
        top = ttk.Frame(box, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="Миграции базы данных", style="CardH2.TLabel").pack(side="left")
        self.check_btn = ttk.Button(top, text="Проверить", style="Primary.TButton", command=self.check)
        self.check_btn.pack(side="right")
        self.busy_widgets.append(self.check_btn)
        self.result_box = ttk.Frame(box, style="Card.TFrame")
        self.result_box.pack(fill="x", pady=(8, 0))
        ttk.Label(self.result_box, text="Нажмите «Проверить»: приложение только читает схему.",
                  style="CardMuted.TLabel").pack(anchor="w")
        self.build_status(self).pack(fill="x", pady=(8, 0))

    def check(self):
        if self.busy:
            return
        self.show_status("Проверка...", "gray")
        self.run(self.db.probe_features, self._checked)

    def _checked(self, result):
        for child in self.result_box.winfo_children():
            child.destroy()
        for name, ok in result.items():
            ttk.Label(self.result_box, text=("✔  " if ok else "✖  ") + name + ("" if ok else " — не применена"),
                      style="Card.TLabel", foreground=theme.SUCCESS if ok else theme.DANGER).pack(anchor="w", pady=1)
        missing = [n for n, ok in result.items() if not ok]
        self.show_status("Все миграции применены." if not missing else
                         "Часть функций недоступна: выполните недостающие файлы из папки sql/.",
                         "green" if not missing else "orange")
