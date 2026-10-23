"""Экран кухни: карточки активных заказов и наличие блюд.

Заказы приходят из RPC kitchen_orders (без цен и сумм), статус меняет
RPC set_order_status (sql/05_kitchen_status.sql). Меню — RPC kitchen_menu и
kitchen_set_menu_item_available (sql/06_kitchen_menu.sql): кухня меняет только
наличие блюда. Права и переходы проверяет сервер.
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import ui_theme as theme
from db import ORDER_STATUSES, ROLE_TRANSITIONS, AppError

AUTO_REFRESH_MS = 15000  # автообновление заказов, чтобы новые появлялись сами
CARD_COLUMNS = 3
MENU_CARD_WIDTH = 280  # примерная ширина карточки блюда: по ней считается число колонок
ACTION = {"preparing": ("Начать готовить", "Big.Primary.TButton"),
          "ready": ("Готово", "Big.Success.TButton")}


class KitchenFrame(ttk.Frame):
    def __init__(self, master, db, profile, on_logout):
        super().__init__(master, padding=16)
        self.db = db
        self.on_logout = on_logout
        self.transitions = ROLE_TRANSITIONS.get(profile["role"], {})
        self.orders = {}          # id -> заказ из db.load_kitchen_orders()
        self.shown_orders = None  # что сейчас нарисовано (чтобы не перерисовывать без изменений)
        self.menu = {}            # id -> блюдо из db.load_kitchen_menu()
        self.menu_loaded = False
        self.menu_pending = False  # меню запрошено, пока шёл другой запрос
        self.busy = False
        self.refresh_job = None
        self.action_buttons = {}  # id заказа -> кнопка смены статуса
        self.badges = {}          # id заказа -> бейдж статуса
        self.menu_buttons = {}    # id блюда -> кнопка наличия
        self.menu_columns = 0     # сколько колонок карточек блюд сейчас нарисовано

        self._build(profile)
        self.reload()

    # ---------- Разметка ----------

    def _build(self, profile):
        top, right = theme.header(self, "Кухня", f"Повар: {profile['full_name']}")
        top.pack(fill="x", pady=(0, 12))
        self.logout_btn = ttk.Button(right, text="Выйти", command=self.logout)
        self.logout_btn.pack(side="right")
        self.reload_btn = ttk.Button(right, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=8)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self.orders_area = theme.ScrollFrame(self.tabs)
        self.tabs.add(self.orders_area, text="Заказы")
        self.tabs.add(self._build_menu_tab(), text="Меню")
        self.tabs.bind("<<NotebookTabChanged>>", lambda _e: self._on_tab())

        self.status = ttk.Label(self, text="", style="Status.TLabel", wraplength=900)
        self.status.pack(fill="x", pady=(8, 0))

    def _build_menu_tab(self):
        tab = ttk.Frame(self.tabs, padding=(0, 10, 0, 0))
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 8), padx=6)
        ttk.Label(bar, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._render_menu())
        search = ttk.Entry(bar, textvariable=self.search_var, width=28)
        search.pack(side="left", padx=8)
        ttk.Button(bar, text="Очистить", command=lambda: self.search_var.set("")).pack(side="left")
        self.menu_counts = ttk.Label(bar, text="", style="Muted.TLabel")
        self.menu_counts.pack(side="right")

        self.menu_area = theme.ScrollFrame(tab)
        self.menu_area.pack(fill="both", expand=True)
        # Число колонок зависит от ширины: без горизонтальной прокрутки и обрезанных кнопок
        self.menu_area.canvas.bind("<Configure>", self._on_menu_resize, add="+")
        return tab

    def _menu_tab_active(self):
        return self.tabs.index(self.tabs.select()) == 1

    def _on_tab(self):
        if self._menu_tab_active() and not self.menu_loaded:
            self.reload_menu()

    # ---------- Фоновые запросы ----------

    def _run(self, task, on_ok, on_err):
        """Выполняет сетевой запрос в потоке, результат обрабатывает в потоке Tk."""
        self._set_busy(True)

        def deliver(callback, value):
            try:
                self.after(0, self._finish, callback, value)
            except (RuntimeError, tk.TclError):
                pass  # экран уже закрыт (выход из аккаунта)

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
        self._set_busy(False)
        callback(value)
        if self.menu_pending and not self.busy:
            self.menu_pending = False
            self.reload_menu()

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.reload_btn, self.logout_btn, *self.action_buttons.values(), *self.menu_buttons.values()):
            btn.config(state=state)

    def _show_status(self, text, color="gray"):
        self.status.config(text=text, foreground=theme.message_color(color))

    # ---------- Заказы ----------

    def _schedule_refresh(self):
        if self.refresh_job is not None:
            self.after_cancel(self.refresh_job)
        self.refresh_job = self.after(AUTO_REFRESH_MS, self._auto_refresh)

    def _auto_refresh(self):
        self.refresh_job = None
        if self.busy:
            self._schedule_refresh()
        else:
            self.reload_orders(quiet=True)

    def reload(self):
        """Кнопка «Обновить»: обновляет открытую вкладку."""
        if self._menu_tab_active():
            self.reload_menu()
        else:
            self.reload_orders()

    def reload_orders(self, done_text=None, quiet=False):
        if self.busy:
            return
        if not quiet:
            self._show_status("Загрузка заказов...")
        self._run(self.db.load_kitchen_orders, lambda orders: self._orders_loaded(orders, done_text, quiet),
                  self._handle_error)

    def _orders_loaded(self, orders, done_text, quiet):
        self._schedule_refresh()
        self.orders = {order["id"]: order for order in orders}
        if orders != self.shown_orders:
            self._render_orders(orders)
        if done_text:
            self._show_status(done_text, "green")
        elif not quiet:
            self._show_status(f"Активных заказов: {len(orders)}" if orders else "Активных заказов нет.")

    def _render_orders(self, orders):
        self.shown_orders = orders
        self.orders_area.clear()
        self.action_buttons.clear()
        self.badges.clear()
        inner = self.orders_area.inner
        if not orders:
            ttk.Label(inner, text="Активных заказов нет", style="H2.TLabel", foreground=theme.MUTED).pack(pady=40)
            return
        for col in range(CARD_COLUMNS):
            inner.columnconfigure(col, weight=1, uniform="cards")
        for index, order in enumerate(orders):
            box = self._order_card(inner, order)
            box.grid(row=index // CARD_COLUMNS, column=index % CARD_COLUMNS, sticky="nsew", padx=6, pady=6)

    def _order_card(self, master, order):
        box = theme.card(master)
        top = ttk.Frame(box, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=f"№ {order['id']}", style="CardH2.TLabel").pack(side="left")
        ttk.Label(top, text=order["time"], style="CardMuted.TLabel").pack(side="right")

        badge = ttk.Label(box, text=ORDER_STATUSES.get(order["status"], order["status"]),
                          style=f"Badge.{order['status']}.TLabel")
        badge.pack(anchor="w", pady=(6, 10))
        self.badges[order["id"]] = badge

        items = ttk.Frame(box, style="Card.TFrame")
        items.pack(fill="x")
        for row, item in enumerate(order["items"]):
            ttk.Label(items, text=f"{item['quantity']} ×", style="CardBold.TLabel").grid(
                row=row, column=0, sticky="ne", padx=(0, 8), pady=2)
            ttk.Label(items, text=item["item_name"], style="Card.TLabel", wraplength=220).grid(
                row=row, column=1, sticky="w", pady=2)

        next_status = self.transitions.get(order["status"])
        if next_status in ACTION:
            text, style = ACTION[next_status]
            btn = ttk.Button(box, text=text, style=style,
                             command=lambda oid=order["id"]: self.advance_status(oid))
            btn.pack(fill="x", pady=(12, 0))
            if self.busy:
                btn.config(state="disabled")
            self.action_buttons[order["id"]] = btn
        return box

    def advance_status(self, order_id):
        if self.busy:
            return
        order = self.orders.get(order_id)
        next_status = self.transitions.get(order["status"]) if order else None
        if next_status is None:
            return
        self._show_status("Сохранение статуса...")
        self._run(lambda: self.db.set_order_status(order_id, next_status),
                  lambda _r: self.reload_orders(done_text=(
                      f"Заказ № {order_id}: статус «{ORDER_STATUSES[next_status]}».")),
                  self._handle_error)

    # ---------- Меню (наличие блюд) ----------

    def reload_menu(self, done_text=None):
        if self.busy:
            self.menu_pending = True
            return
        self._show_status("Загрузка меню...")
        self._run(self.db.load_kitchen_menu, lambda items: self._menu_loaded(items, done_text), self._handle_error)

    def _menu_loaded(self, items, done_text):
        self.menu_loaded = True
        self.menu = {item["id"]: item for item in items}
        self._render_menu()
        off = sum(1 for item in items if not item["is_available"])
        self.menu_counts.config(text=f"В продаже: {len(items) - off}  ·  Нет в наличии: {off}")
        if done_text:
            self._show_status(done_text, "green")
        else:
            self._show_status(f"Блюд: {len(items)}, нет в наличии: {off}")

    def _menu_column_count(self):
        width = self.menu_area.canvas.winfo_width()
        return max(1, width // MENU_CARD_WIDTH) if width > 1 else CARD_COLUMNS

    def _on_menu_resize(self, _event):
        if self.menu_loaded and self._menu_column_count() != self.menu_columns:
            self._render_menu()

    def _render_menu(self):
        """Карточки блюд по категориям с учётом поиска (без запроса к серверу)."""
        self.menu_area.clear()
        self.menu_buttons.clear()
        self.menu_columns = columns = self._menu_column_count()
        inner = self.menu_area.inner
        query = self.search_var.get().strip().casefold()
        items = [item for item in self.menu.values() if query in item["name"].casefold()]
        if not items:
            text = "Ничего не найдено" if query else "Меню пусто"
            ttk.Label(inner, text=text, style="H2.TLabel", foreground=theme.MUTED).pack(pady=40)
            return

        by_category = {}
        for item in sorted(items, key=lambda i: (i["category"], i["name"])):
            by_category.setdefault(item["category"], []).append(item)
        for category, dishes in by_category.items():
            ttk.Label(inner, text=f"{category}  ({len(dishes)})", style="H2.TLabel").pack(
                anchor="w", padx=6, pady=(14, 6))
            grid = tk.Frame(inner, bg=theme.BG)
            grid.pack(fill="x")
            for col in range(columns):
                grid.columnconfigure(col, weight=1, uniform="dishes")
            for index, item in enumerate(dishes):
                self._menu_card(grid, item).grid(row=index // columns, column=index % columns,
                                                 sticky="nsew", padx=6, pady=6)

    def _menu_card(self, master, item):
        """Карточка блюда: цветная полоса статуса, название, бейдж и крупная кнопка."""
        available = item["is_available"]
        color = theme.SUCCESS if available else theme.DANGER
        box = tk.Frame(master, bg=theme.SURFACE, highlightbackground=color if not available else theme.BORDER,
                       highlightthickness=1)
        tk.Frame(box, bg=color, width=6).pack(side="left", fill="y")
        body = ttk.Frame(box, style="Card.TFrame", padding=(12, 10))
        body.pack(side="left", fill="both", expand=True)

        ttk.Label(body, text=item["name"], style="CardBold.TLabel" if available else "CardMuted.TLabel",
                  wraplength=MENU_CARD_WIDTH - 60).pack(anchor="w")
        ttk.Label(body, text="В продаже" if available else "Нет в наличии",
                  style="Badge.on.TLabel" if available else "Badge.off.TLabel").pack(anchor="w", pady=(6, 10))
        if available:
            btn = ttk.Button(body, text="Нет в наличии", style="Big.Danger.TButton",
                             command=lambda: self.toggle_available(item["id"]))
        else:
            btn = ttk.Button(body, text="Вернуть в продажу", style="Big.Success.TButton",
                             command=lambda: self.toggle_available(item["id"]))
        btn.pack(fill="x", side="bottom")
        if self.busy:
            btn.config(state="disabled")
        self.menu_buttons[item["id"]] = btn
        return box

    def toggle_available(self, item_id):
        if self.busy:
            return
        item = self.menu.get(item_id)
        if item is None:
            return
        available = not item["is_available"]
        if not available and not messagebox.askyesno(
                "Нет в наличии", f"Отметить «{item['name']}» как «Нет в наличии»?\n"
                                 "Кассиры не смогут добавить его в заказ.", parent=self):
            return
        self._show_status("Сохранение...")
        text = f"«{item['name']}»: " + ("в продаже." if available else "нет в наличии.")
        self._run(lambda: self.db.kitchen_set_available(item_id, available),
                  lambda _r: self.reload_menu(done_text=text), self._handle_error)

    # ---------- Ошибки и выход ----------

    def _handle_error(self, error):
        self._schedule_refresh()
        self._show_status(str(error), "red")
        if error.code == "auth":
            messagebox.showerror("Доступ", str(error), parent=self)
            self.on_logout()
        elif error.code in ("order_changed", "forbidden"):
            messagebox.showwarning("Статус не изменён", str(error), parent=self)
            self.reload_orders()
        elif error.code == "invalid":
            messagebox.showwarning("Не сохранено", str(error), parent=self)
            self.reload_menu()
        elif error.code == "network":
            messagebox.showerror("Нет связи", str(error), parent=self)
        else:
            messagebox.showerror("Ошибка", str(error), parent=self)

    def logout(self):
        if self.busy:
            return
        self.on_logout()

    def destroy(self):
        if self.refresh_job is not None:
            self.after_cancel(self.refresh_job)
            self.refresh_job = None
        super().destroy()
