"""Разделы административной оболочки: главная панель, меню, категории, настройки."""

import tkinter as tk
from datetime import date
from tkinter import ttk

import ui_theme as theme
from db import MENU_STATUSES, ORDER_STATUSES, AppError, menu_status
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


class MenuSectionPanel(Panel):
    """Управление меню: список позиций со статусами; редактор позиций — прежнее окно MenuAdminWindow."""

    def __init__(self, master, db, on_auth_error, on_menu_changed=None):
        super().__init__(master, db, on_auth_error)
        self.on_menu_changed = on_menu_changed
        self.items = []
        head, right = theme.header(self, "Управление меню", "Позиции, цены, станции и решения по предложениям")
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
        ttk.Entry(bar, textvariable=self.search_var, width=26).pack(side="left", padx=8)
        self.pending_label = ttk.Label(bar, text="", foreground=theme.WARNING)
        self.pending_label.pack(side="right")

        frame, self.tree = theme.make_table(
            self, [("code", "Код", 70, "center"), ("name", "Позиция", 240, "w"), ("category", "Категория", 140, "w"),
                   ("station", "Станция", 90, "center"), ("price", "Цена", 110, "e"), ("status", "Статус", 130, "center")],
            height=12, tags={**theme.MENU_STATUS_COLORS})
        frame.pack(fill="both", expand=True)
        self.build_status(self).pack(fill="x", pady=(8, 0))
        self.reload()

    def reload(self):
        if self.busy:
            return
        self.show_status("Загрузка меню...", "gray")
        self.run(self.db.load_all_menu, self._loaded)

    def _loaded(self, items):
        self.items = items
        self._render()
        pending = sum(1 for i in items if menu_status(i) == "pending")
        self.pending_label.config(text=f"Ждут решения: {pending}" if pending else "")
        self.show_status(f"Позиций: {len(items)}", "gray")

    def _render(self):
        from search import search
        self.tree.delete(*self.tree.get_children())
        for item in search(self.items, self.search_var.get()):
            status = menu_status(item)
            self.tree.insert("", "end", tags=(status,), values=(
                item.get("code") or "", item["name"], item["category"],
                {"kitchen": "Кухня", "bar": "Бар"}.get(item.get("station"), "Кухня"), fmt_money(item["price"]),
                MENU_STATUSES[status]))

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
    """Категории: число позиций, переименование и объединение (admin_rename_category)."""

    def __init__(self, master, db, on_auth_error, on_menu_changed=None):
        super().__init__(master, db, on_auth_error)
        self.on_menu_changed = on_menu_changed
        self.counts = {}
        head, right = theme.header(self, "Категории", "Категория — общее поле позиций меню")
        head.pack(fill="x")
        self.rename_btn = ttk.Button(right, text="Переименовать", style="Primary.TButton", command=self.rename)
        self.rename_btn.pack(side="right")
        self.reload_btn = ttk.Button(right, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=8)
        self.busy_widgets += [self.rename_btn, self.reload_btn]
        ttk.Label(self, text="Новая категория появляется, когда её указывают у позиции в редакторе меню. "
                             "Если новое имя уже есть, категории объединяются.", style="Muted.TLabel",
                  wraplength=800, justify="left").pack(anchor="w", pady=theme.GAP)
        frame, self.tree = theme.make_table(self, [("name", "Категория", 300, "w"), ("count", "Позиций", 100, "center"),
                                                   ("sale", "В продаже", 100, "center")], height=12)
        frame.pack(fill="both", expand=True)
        self.build_status(self).pack(fill="x", pady=(8, 0))
        self.reload()

    def reload(self, done_text=None):
        if self.busy:
            return
        self.show_status("Загрузка...", "gray")
        self.run(self.db.load_all_menu, lambda items: self._loaded(items, done_text))

    def _loaded(self, items, done_text):
        self.counts = {}
        for item in items:
            row = self.counts.setdefault(item["category"], [0, 0])
            row[0] += 1
            row[1] += 1 if menu_status(item) == "on_sale" else 0
        self.tree.delete(*self.tree.get_children())
        for name in sorted(self.counts):
            self.tree.insert("", "end", iid=name, values=(name, *self.counts[name]))
        self.show_status(done_text or f"Категорий: {len(self.counts)}", "green" if done_text else "gray")

    def rename(self):
        selected = self.tree.selection()
        if not selected or self.busy:
            self.show_status("Выберите категорию в списке.", "orange")
            return
        old = selected[0]

        def submit(values):
            new = values["name"].strip()
            if not new or len(new) > 50:
                return "Категория — от 1 до 50 символов."
            if new == old:
                return "Имя не изменилось."
            self.show_status("Сохранение...", "gray")
            self.run(lambda: self.db.rename_category(old, new), lambda r: self._renamed(old, new, r))
            return None

        FormDialog(self, "Переименовать категорию", [("name", "Новое название:", old, False)], submit,
                   ok_text="Переименовать")

    def _renamed(self, old, new, result):
        if self.on_menu_changed:
            self.on_menu_changed()
        self.reload(f"Категория «{old}» → «{new}»: позиций изменено {result['updated']}.")


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
