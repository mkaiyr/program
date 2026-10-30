"""Окно кассы: меню, текущий заказ, оформление."""

import threading
import tkinter as tk
import uuid
from decimal import Decimal
from tkinter import messagebox, ttk

import ui_theme as theme
from db import AppError, unexpected_error
from search import find_by_code, search

MAX_QTY = 100     # совпадает с ограничением order_items.quantity
MAX_LINES = 50    # совпадает с проверкой в create_order
TILE_COLUMNS = 3
TILE_LIMIT = 60   # больше плиток не рисуется: при 500–1000 позициях окно остаётся быстрым
ALL_CATEGORIES = "Все"


def fmt_money(amount):
    return f"{amount:,.2f}".replace(",", " ") + " ₸"


class OrderFrame(ttk.Frame):
    def __init__(self, master, db, profile, on_logout):
        super().__init__(master, padding=16)
        self.db = db
        self.on_logout = on_logout
        self.menu = {}         # id -> {id, name, category, price}
        self.tiles = {}        # id -> кнопка-плитка в каталоге
        self.catalog_job = None  # отложенная перерисовка каталога при вводе в поиск
        self.cart = {}         # id -> {name, price, qty}
        self.request_id = None  # один ID на неизменённый заказ (защита от дублей)
        # ID заказа, отправленного без ответа сервера: он мог сохраниться. Повтор с тем же ID
        # безопасен, а изменённая корзина — уже новый заказ, поэтому перед ним предупреждение.
        self.unconfirmed_id = None
        self.busy = False
        self.closed = False    # экран уничтожен: результаты фоновых запросов игнорируются
        self.is_admin = profile["role"] == "admin"
        self.orders_window = None

        self._build(profile)
        self.reload_menu()

    # ---------- Разметка ----------

    def _build(self, profile):
        role_title = "Администратор" if self.is_admin else "Кассир"
        top, right = theme.header(self, "Кафе Бистро", f"{role_title}: {profile['full_name']}")
        top.pack(fill="x", pady=(0, 12))
        self.logout_btn = ttk.Button(right, text="Выйти", command=self.logout)
        self.logout_btn.pack(side="right")
        self.reload_btn = ttk.Button(right, text="Обновить меню", command=self.reload_menu)
        self.reload_btn.pack(side="right", padx=8)
        # Кнопка — только удобство; права на изменение меню проверяет сервер
        self.admin_btn = None
        if self.is_admin:
            self.admin_btn = ttk.Button(right, text="Управление меню", command=self.open_menu_admin)
            self.admin_btn.pack(side="right")
        ttk.Button(right, text="Заказы", command=self.open_orders).pack(side="right", padx=8)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2, minsize=380)
        body.rowconfigure(0, weight=1)

        # Каталог: фильтр категорий и плитки блюд (нажатие добавляет 1 шт.)
        catalog = ttk.Frame(body)
        catalog.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.category_var = tk.StringVar(value=ALL_CATEGORIES)
        search_row = ttk.Frame(catalog)
        search_row.pack(fill="x", pady=(0, 8))
        ttk.Label(search_row, text="Код или название:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._schedule_catalog())
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var, font=theme.FONT_BIG, width=18)
        self.search_entry.pack(side="left", padx=8)
        self.search_entry.bind("<Return>", lambda _e: self.add_from_search())
        ttk.Button(search_row, text="✕", width=2, command=lambda: self.search_var.set("")).pack(side="left")
        ttk.Label(search_row, text="Enter — добавить по коду", style="Muted.TLabel").pack(side="left", padx=8)
        self.chips = ttk.Frame(catalog)
        self.chips.pack(fill="x", pady=(0, 8))
        self.catalog = theme.ScrollFrame(catalog)
        self.catalog.pack(fill="both", expand=True)

        # Корзина
        cart = theme.card(body)
        cart.grid(row=0, column=1, sticky="nsew")
        ttk.Label(cart, text="Текущий заказ", style="CardH2.TLabel").pack(anchor="w", pady=(0, 8))
        # Итог и «Оформить» размещаются первыми снизу: при нехватке места сжимается таблица, а не кнопка
        self.submit_btn = ttk.Button(cart, text="Оформить заказ", style="Big.Primary.TButton", command=self.submit)
        self.submit_btn.pack(side="bottom", fill="x", pady=(12, 0))
        total_row = ttk.Frame(cart, style="Card.TFrame")
        total_row.pack(side="bottom", fill="x")
        ttk.Label(total_row, text="Итого", style="CardMuted.TLabel").pack(side="left")
        self.total_label = ttk.Label(total_row, text=fmt_money(Decimal(0)), style="Total.TLabel")
        self.total_label.pack(side="right")
        ttk.Separator(cart).pack(side="bottom", fill="x", pady=8)
        cart_btns = ttk.Frame(cart, style="Card.TFrame")
        cart_btns.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(cart_btns, text="−", width=3, command=lambda: self.change_qty(-1)).pack(side="left")
        ttk.Button(cart_btns, text="+", width=3, command=lambda: self.change_qty(1)).pack(side="left", padx=6)
        ttk.Button(cart_btns, text="Удалить", command=self.remove_item).pack(side="left")
        ttk.Button(cart_btns, text="Очистить", command=self.clear_cart).pack(side="right")

        cols = ("name", "price", "qty", "sum")
        self.cart_tree = ttk.Treeview(cart, columns=cols, show="headings", selectmode="browse", height=5)
        for col, title, width, anchor in (("name", "Позиция", 150, "w"), ("price", "Цена", 90, "e"),
                                          ("qty", "Кол-во", 60, "center"), ("sum", "Сумма", 100, "e")):
            self.cart_tree.heading(col, text=title)
            self.cart_tree.column(col, width=width, anchor=anchor)
        self.cart_tree.pack(fill="both", expand=True)

        self.status = ttk.Label(self, text="", style="Status.TLabel", wraplength=900)
        self.status.pack(fill="x", pady=(8, 0))

    # ---------- Фоновые запросы ----------

    def _run(self, task, on_ok, on_err):
        """Выполняет сетевой запрос в потоке, результат обрабатывает в потоке Tk."""
        self._set_busy(True)
        # Результат передаётся через корневое окно: касса уничтожается при выходе из аккаунта
        # (в том числе из окна «Заказы», пока идёт её запрос), и after на ней исчез бы
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
            return  # экран закрыли (выход из аккаунта), пока шёл запрос: виджетов уже нет
        self._set_busy(False)
        callback(value)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.submit_btn, self.reload_btn, self.logout_btn, self.admin_btn):
            if btn is not None:
                btn.config(state=state)

    def _show_status(self, text, color="black"):
        self.status.config(text=text, foreground=theme.message_color(color))

    # ---------- Меню ----------

    def reload_menu(self):
        if self.busy:
            return
        self._show_status("Загрузка меню...", "gray")
        self._run(self.db.load_menu, self._menu_loaded, self._handle_error)

    def _menu_loaded(self, items):
        self.menu = {item["id"]: item for item in items}
        self._render_chips()
        self._render_catalog()
        changed = self._sync_cart_with_menu()
        if changed:
            self._show_status("Меню изменилось. В заказе обновлено: " + ", ".join(changed), "orange")
        else:
            self._show_status(f"Меню загружено: {len(items)} поз.", "gray")

    def _render_chips(self):
        categories = [ALL_CATEGORIES] + sorted({item["category"] for item in self.menu.values()})
        if self.category_var.get() not in categories:
            self.category_var.set(ALL_CATEGORIES)
        for child in self.chips.winfo_children():
            child.destroy()
        for category in categories:
            ttk.Radiobutton(self.chips, text=category, value=category, variable=self.category_var,
                            style="Chip.Toolbutton", command=self._render_catalog).pack(side="left", padx=(0, 6))

    def _schedule_catalog(self):
        """Несколько нажатий подряд — одна перерисовка (когда Tk освободится)."""
        if self.catalog_job is None:
            self.catalog_job = self.after_idle(self._run_catalog)

    def _run_catalog(self):
        self.catalog_job = None
        self._render_catalog()

    def visible_items(self):
        """Каталог с учётом категории и поиска по коду или названию (локально)."""
        category = self.category_var.get()
        items = [item for item in self.menu.values() if category in (ALL_CATEGORIES, item["category"])]
        return search(items, self.search_var.get())

    def _render_catalog(self):
        self.catalog.clear()
        self.tiles.clear()
        inner = self.catalog.inner
        items = self.visible_items()
        if not items:
            text = "Ничего не найдено" if self.search_var.get().strip() else "Нет доступных блюд"
            ttk.Label(inner, text=text, style="H2.TLabel", foreground=theme.MUTED).grid(row=0, column=0, pady=40)
            return
        for col in range(TILE_COLUMNS):
            inner.columnconfigure(col, weight=1, uniform="tiles")
        for index, item in enumerate(items[:TILE_LIMIT]):
            code = f"{item['code']} · " if item.get("code") else ""
            tile = ttk.Button(inner, text=f"{code}{item['name']}\n{fmt_money(item['price'])}", style="Tile.TButton",
                              command=lambda i=item["id"]: self.add_item(i))
            tile.grid(row=index // TILE_COLUMNS, column=index % TILE_COLUMNS, sticky="nsew", padx=4, pady=4)
            self.tiles[item["id"]] = tile
        if len(items) > TILE_LIMIT:
            ttk.Label(inner, text=f"Показаны первые {TILE_LIMIT} из {len(items)} — уточните поиск",
                      style="Muted.TLabel").grid(row=TILE_LIMIT // TILE_COLUMNS + 1, column=0,
                                                 columnspan=TILE_COLUMNS, pady=8)

    def add_from_search(self):
        """Enter в поиске: точный код или единственное найденное блюдо — в заказ."""
        if self.busy:
            return  # корзина заблокирована, пока идёт запрос; введённый код остаётся в поле
        items = self.visible_items()
        item = find_by_code(self.menu.values(), self.search_var.get()) or (items[0] if len(items) == 1 else None)
        if item is None:
            self._show_status("Введите точный код блюда или уточните название." if items
                              else "Ничего не найдено.", "orange")
            return
        self.add_item(item["id"])
        if item["id"] in self.cart:
            self.search_var.set("")
            self._show_status(f"Добавлено: {item['name']}", "green")
        self.search_entry.focus_set()

    def _sync_cart_with_menu(self):
        """Убирает недоступные позиции и обновляет цены в заказе."""
        changed = []
        for item_id, line in list(self.cart.items()):
            item = self.menu.get(item_id)
            if item is None:
                changed.append(f"{line['name']} (снята с продажи)")
                del self.cart[item_id]
            elif item["price"] != line["price"] or item["name"] != line["name"]:
                changed.append(f"{item['name']} ({fmt_money(item['price'])})")
                line.update(name=item["name"], price=item["price"])
        if changed:
            self._cart_changed()
        return changed

    # ---------- Заказ ----------

    def add_item(self, item_id):
        """Плитка каталога: +1 шт. позиции в заказ."""
        if self.busy:
            return
        item = self.menu.get(item_id)
        if item is None:
            return
        line = self.cart.get(item_id)
        if line is None:
            if len(self.cart) >= MAX_LINES:
                messagebox.showwarning("Заказ", f"Не больше {MAX_LINES} разных позиций.", parent=self)
                return
            self.cart[item_id] = {"name": item["name"], "price": item["price"], "qty": 1}
        else:
            if line["qty"] >= MAX_QTY:
                messagebox.showwarning("Количество", f"Не больше {MAX_QTY} шт. одной позиции.", parent=self)
                return
            line["qty"] += 1
        self._cart_changed(select_id=item_id)

    def _selected_cart_id(self):
        selected = self.cart_tree.selection()
        if not selected:
            self._show_status("Выберите позицию в заказе.", "orange")
            return None
        return int(selected[0])

    def change_qty(self, delta):
        """Кнопки «−» и «+» для выбранной строки заказа; 0 шт. — удалить строку."""
        if self.busy:
            return
        item_id = self._selected_cart_id()
        if item_id is None:
            return
        qty = self.cart[item_id]["qty"] + delta
        if qty > MAX_QTY:
            messagebox.showwarning("Количество", f"Не больше {MAX_QTY} шт. одной позиции.", parent=self)
            return
        if qty < 1:
            del self.cart[item_id]
            self._cart_changed()
        else:
            self.cart[item_id]["qty"] = qty
            self._cart_changed(select_id=item_id)

    def remove_item(self):
        if self.busy:
            return
        item_id = self._selected_cart_id()
        if item_id is not None:
            del self.cart[item_id]
            self._cart_changed()

    def clear_cart(self):
        if self.busy:
            return
        if self.cart and messagebox.askyesno("Заказ", "Очистить текущий заказ?", parent=self):
            self.cart.clear()
            self._cart_changed()

    def _cart_changed(self, select_id=None):
        # Изменённый заказ — это новый запрос, поэтому сбрасываем его ID
        self.request_id = None
        self.cart_tree.delete(*self.cart_tree.get_children())
        total = Decimal(0)
        for item_id, line in self.cart.items():
            line_sum = line["price"] * line["qty"]
            total += line_sum
            self.cart_tree.insert("", "end", iid=str(item_id), values=(
                line["name"], fmt_money(line["price"]), line["qty"], fmt_money(line_sum)))
        self.total_label.config(text=fmt_money(total))
        if select_id in self.cart:
            self.cart_tree.selection_set(str(select_id))
            self.cart_tree.see(str(select_id))

    def submit(self):
        if self.busy:
            return
        if not self.cart:
            self._show_status("Заказ пуст.", "orange")
            return
        if self.unconfirmed_id is not None and self.request_id != self.unconfirmed_id:
            if not messagebox.askyesno(
                    "Предыдущий заказ не подтверждён",
                    "Прошлая попытка оформить заказ прервалась из-за связи — тот заказ мог сохраниться.\n"
                    "Проверьте окно «Заказы», чтобы не оформить его дважды.\n\n"
                    "Оформить текущий заказ как новый?", parent=self):
                return
            self.unconfirmed_id = None
        if self.request_id is None:
            self.request_id = uuid.uuid4()
        items = [(item_id, line["qty"], line["price"]) for item_id, line in self.cart.items()]
        request_id = self.request_id
        self._show_status("Сохранение заказа...", "gray")
        self._run(lambda: self.db.create_order(request_id, items), self._order_saved,
                  lambda error: self._order_failed(request_id, error))

    def _order_failed(self, request_id, error):
        if error.code == "network":
            self.unconfirmed_id = request_id  # ответа нет: заказ мог сохраниться
        elif request_id == self.unconfirmed_id and error.code != "auth":
            # Сервер ответил отказом по этому же ID: заказа с ним нет (повтор вернул бы его)
            self.unconfirmed_id = None
        self._handle_error(error)

    def _order_saved(self, result):
        self.unconfirmed_id = None  # повтор с тем же ID вернул заказ или сохранён новый
        self.cart.clear()
        self._cart_changed()
        text = f"Заказ № {result['order_id']} оформлен. Сумма: {fmt_money(result['total'])}"
        if result.get("duplicate"):
            text += "\n(повторный запрос — заказ был сохранён ранее, новый не создан)"
        self._show_status(text.replace("\n", " "), "green")
        if self.orders_window is not None and self.orders_window.winfo_exists():
            self.orders_window.reload(select_id=result["order_id"])
        messagebox.showinfo("Заказ оформлен", text, parent=self)

    # ---------- Управление меню ----------

    def open_menu_admin(self):
        if self.busy:
            return
        from ui_admin import MenuAdminWindow  # ui_admin импортирует fmt_money отсюда
        MenuAdminWindow(self, self.db, on_close=self.reload_menu, on_auth_error=self.on_logout)

    # ---------- Заказы ----------

    def open_orders(self):
        if self.orders_window is not None and self.orders_window.winfo_exists():
            self.orders_window.deiconify()
            self.orders_window.lift()
            self.orders_window.reload()
            return
        from ui_orders import OrdersWindow  # ui_orders импортирует fmt_money отсюда
        role = "admin" if self.is_admin else "cashier"
        self.orders_window = OrdersWindow(self, self.db, role=role, on_auth_error=self.on_logout)

    # ---------- Ошибки и выход ----------

    def _handle_error(self, error):
        self._show_status(str(error), "red")
        if error.code == "auth":
            messagebox.showerror("Доступ", str(error), parent=self)
            self.on_logout()
        elif error.code == "menu_changed":
            messagebox.showwarning("Меню изменилось", str(error), parent=self)
            self.reload_menu()
        elif error.code == "network":
            messagebox.showerror("Нет связи", str(error), parent=self)

    def destroy(self):
        self.closed = True
        if self.catalog_job is not None:
            self.after_cancel(self.catalog_job)
            self.catalog_job = None
        super().destroy()

    def logout(self):
        if self.busy:
            return
        if self.cart and not messagebox.askyesno("Выход", "Заказ не оформлен. Всё равно выйти?", parent=self):
            return
        self.on_logout()
