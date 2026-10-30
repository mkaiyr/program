"""Экран станции — кухни или бара: карточки активных заказов и меню своей станции.

Станция определяется ролью: kitchen — кухня, bar — бар. Заказы приходят из RPC
kitchen_orders / bar_orders — только позиции своей станции, без цен и сумм.
Статус своей части заказа меняет RPC set_station_status (sql/08_bar_stations.sql):
готовый напиток не делает готовым весь заказ. Меню станции — RPC station_menu,
наличие и предложение новых позиций (sql/09_station_menu.sql); до миграции 09 кухня
работает с kitchen_menu, а у бара меню нет (см. Database.load_station_menu).
Права и переходы проверяет сервер.
"""

import logging
import sys
import threading
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

import ui_theme as theme
from db import ORDER_STATUSES, ROLE_TRANSITIONS, STATIONS, AppError, menu_status, unexpected_error
from search import search
from ui_kitchen_menu import TOGGLEABLE, KitchenMenuPanel, ProposeDialog

AUTO_REFRESH_MS = 7000  # автообновление заказов, чтобы новые появлялись сами
SEEN_LIMIT = 5000       # сколько ID заказов помнить для защиты от повторного сигнала
CARD_COLUMNS = 3
ACTION = {"preparing": ("Начать готовить", "Big.Primary.TButton"),
          "ready": ("Готово", "Big.Success.TButton")}
STAFF_TITLE = {"kitchen": "Повар", "bar": "Бармен"}

log = logging.getLogger(__name__)


def play_alert(widget):
    """Короткий сигнал о новом заказе. Ошибка звука не мешает работе экрана."""
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)  # не блокирует, без зависимостей
        else:
            widget.bell()
    except Exception as e:  # нет звуковой системы и т. п.
        log.debug("Звуковой сигнал недоступен: %s", e)


class SeenOrders:
    """ID заказов, уже показанных на экране в этой сессии (с ограничением памяти).

    Заказ, который исчез и снова появился (смена статуса, поиск), повторно
    новым не считается. При переполнении забываются самые старые ID — к тому
    времени эти заказы давно не активны.
    """

    def __init__(self, limit=SEEN_LIMIT):
        self.limit = limit
        self._ids = set()
        self._order = deque()

    def __contains__(self, order_id):
        return order_id in self._ids

    def __len__(self):
        return len(self._ids)

    def add_new(self, order_ids):
        """Запоминает ID и возвращает те, которых раньше не было (в исходном порядке)."""
        new = []
        for order_id in order_ids:
            if order_id in self._ids:
                continue
            self._ids.add(order_id)
            self._order.append(order_id)
            new.append(order_id)
        while len(self._order) > self.limit:
            self._ids.discard(self._order.popleft())
        return new


class KitchenFrame(ttk.Frame):
    def __init__(self, master, db, profile, on_logout):
        super().__init__(master, padding=16)
        self.db = db
        self.on_logout = on_logout
        self.station = "bar" if profile["role"] == "bar" else "kitchen"
        self.transitions = ROLE_TRANSITIONS.get(profile["role"], {})
        self.orders = {}          # id -> заказ из db.load_station_orders()
        self.order_list = []      # заказы в порядке поступления
        self.shown_orders = None  # что сейчас нарисовано (чтобы не перерисовывать без изменений)
        self.search_job = None
        self.menu_panel = None    # вкладка «Меню» своей станции
        self.menu = {}            # id -> позиция из db.load_station_menu()
        self.propose_draft = None  # введённое в «Предложить позицию», если сервер отказал
        self.menu_loaded = False
        self.menu_pending = False  # меню запрошено, пока шёл другой запрос
        self.busy = False
        self.refresh_job = None
        self.closed = False       # экран уничтожен: результаты фоновых запросов игнорируются
        self.seen_orders = SeenOrders()
        self.baseline_loaded = False  # первая успешная загрузка — без сигналов о «новых»
        self.auto_error = False       # в строке статуса — ошибка автообновления
        self.action_buttons = {}  # id заказа -> кнопка смены статуса
        self.badges = {}          # id заказа -> бейдж статуса

        self._build(profile)
        self.reload()

    # ---------- Разметка ----------

    def _build(self, profile):
        top, right = theme.header(self, STATIONS[self.station],
                                  f"{STAFF_TITLE[self.station]}: {profile['full_name']}")
        top.pack(fill="x", pady=(0, 12))
        self.logout_btn = ttk.Button(right, text="Выйти", command=self.logout)
        self.logout_btn.pack(side="right")
        self.reload_btn = ttk.Button(right, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=8)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self.tabs.add(self._build_orders_tab(), text="Заказы")
        # Меню загружается при первом открытии вкладки: экран начинает с заказов
        self.menu_panel = KitchenMenuPanel(self.tabs, on_toggle=self.toggle_available, on_propose=self.propose_item)
        self.tabs.add(self.menu_panel, text="Меню")
        self.tabs.bind("<<NotebookTabChanged>>", lambda _e: self._on_tab())

        self.status = ttk.Label(self, text="", style="Status.TLabel", wraplength=900)
        self.status.pack(fill="x", pady=(8, 0))

    def _build_orders_tab(self):
        tab = ttk.Frame(self.tabs, padding=(0, 10, 0, 0))
        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=6, pady=(0, 6))
        what = "напитка" if self.station == "bar" else "блюда"
        ttk.Label(bar, text=f"Поиск по коду или названию {what}:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._schedule_search())
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=24)
        self.search_entry.pack(side="left", padx=8)
        ttk.Button(bar, text="✕", width=2, command=lambda: self.search_var.set("")).pack(side="left")
        self.orders_area = theme.ScrollFrame(tab)
        self.orders_area.pack(fill="both", expand=True)
        return tab

    def _menu_tab_active(self):
        return self.menu_panel is not None and self.tabs.index(self.tabs.select()) == 1

    def _on_tab(self):
        if self._menu_tab_active() and not self.menu_loaded:
            self.reload_menu()

    # ---------- Фоновые запросы ----------

    def _run(self, task, on_ok, on_err):
        """Выполняет сетевой запрос в потоке, результат обрабатывает в потоке Tk."""
        self._set_busy(True)

        # Результат передаётся через корневое окно: оно живёт дольше экрана (выход из
        # аккаунта уничтожает только экран), и команда after не исчезнет вместе с ним
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
            return  # экран закрыли, пока шёл запрос: виджетов уже нет
        self._set_busy(False)
        callback(value)
        if self.closed:
            return  # обработчик закрыл экран (истёкшая сессия -> выход): отложенное меню не нужно
        if self.menu_pending and not self.busy:
            self.menu_pending = False
            self.reload_menu()

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.reload_btn, self.logout_btn, *self.action_buttons.values()):
            btn.config(state=state)
        if self.menu_panel is not None:
            self.menu_panel.set_busy(busy)

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
        self._run(lambda: self.db.load_station_orders(self.station),
                  lambda orders: self._orders_loaded(orders, done_text, quiet),
                  lambda error: self._handle_error(error, quiet=quiet))

    def _orders_loaded(self, orders, done_text, quiet):
        self._schedule_refresh()
        self.orders = {order["id"]: order for order in orders}
        self.order_list = orders
        new_ids = self._new_order_ids(orders)
        self._render_orders()
        if new_ids:
            play_alert(self)
        if done_text:
            self._show_status(done_text, "green")
        elif new_ids:
            numbers = ", ".join(f"№ {order_id}" for order_id in new_ids)
            self._show_status(("Новый заказ " if len(new_ids) == 1 else "Новые заказы: ") + numbers, "green")
        elif not quiet or self.auto_error:
            self._show_status(f"Активных заказов: {len(orders)}" if orders else "Активных заказов нет.")
        self.auto_error = False

    def _new_order_ids(self, orders):
        """ID заказов, которых не было ни в одной прежней успешной загрузке этого экрана.

        Сравниваются стабильные ID заказов; смена статуса существующего заказа
        новым заказом не считается. Первая успешная загрузка только запоминает
        текущие заказы — без сигнала для каждого.
        """
        new_ids = self.seen_orders.add_new(order["id"] for order in orders)
        if not self.baseline_loaded:
            self.baseline_loaded = True
            return []
        return new_ids

    def _schedule_search(self):
        if self.search_job is None:
            self.search_job = self.after_idle(self._run_search)

    def _run_search(self):
        self.search_job = None
        self._render_orders()

    def visible_orders(self):
        """Заказы, где есть позиция с подходящим кодом или названием (локально)."""
        query = self.search_var.get()
        return [order for order in self.order_list if search(order["items"], query, name_key="item_name")]

    def _render_orders(self):
        orders = self.visible_orders()
        state = (orders, self.search_var.get().strip())
        if state == self.shown_orders:
            return  # ничего не изменилось — не перерисовывать (автообновление)
        self.shown_orders = state
        self.orders_area.clear(keep_position=True)
        self.action_buttons.clear()
        self.badges.clear()
        inner = self.orders_area.inner
        if not orders:
            text = "Ничего не найдено" if self.order_list else "Активных заказов нет"
            ttk.Label(inner, text=text, style="H2.TLabel", foreground=theme.MUTED).pack(pady=40)
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
            ttk.Label(items, text=item["item_name"], style="Card.TLabel", wraplength=180).grid(
                row=row, column=1, sticky="w", pady=2)
            if item.get("code"):
                ttk.Label(items, text=str(item["code"]), style="CardMuted.TLabel").grid(
                    row=row, column=2, sticky="ne", padx=(8, 0), pady=2)
        items.columnconfigure(1, weight=1)

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
        self._run(lambda: self.db.set_station_status(order_id, self.station, next_status),
                  lambda _r: self.reload_orders(done_text=(
                      f"Заказ № {order_id}: статус «{ORDER_STATUSES[next_status]}».")),
                  self._handle_error)

    # ---------- Меню станции (наличие и предложения) ----------

    def reload_menu(self, done_text=None):
        if self.busy:
            self.menu_pending = True
            return
        self._show_status("Загрузка меню...")
        self._run(lambda: self.db.load_station_menu(self.station),
                  lambda result: self._menu_loaded(result, done_text),
                  lambda error: self._handle_error(error, menu=True))

    @property
    def menu_buttons(self):
        """id позиции -> кнопка наличия на вкладке «Меню»."""
        return self.menu_panel.buttons

    def _menu_loaded(self, result, done_text):
        self.menu_loaded = True
        items = result["items"]
        self.menu = {item["id"]: item for item in items}
        self.menu_panel.set_items(items, can_propose=result["can_propose"], available=result["available"])
        statuses = [menu_status(item) for item in items]
        if done_text:
            self._show_status(done_text, "green")
        elif not result["available"]:
            self._show_status("Меню станции на сервере ещё не настроено.", "orange")
        else:
            text = f"Позиций: {len(items)}, нет в наличии: {statuses.count('out_of_stock')}"
            if statuses.count("pending"):
                text += f", ждут решения администратора: {statuses.count('pending')}"
            self._show_status(text)

    def toggle_available(self, item_id):
        if self.busy:
            return
        item = self.menu.get(item_id)
        if item is None or menu_status(item) not in TOGGLEABLE:
            return  # ожидающую и отклонённую позицию в продажу выводит только решение администратора
        available = not item["is_available"]
        if not available and not messagebox.askyesno(
                "Снять с продажи", f"Снять «{item['name']}» с продажи?\n"
                                   "Кассиры не смогут добавить его в заказ.", parent=self):
            return
        self._show_status("Сохранение...")
        text = f"«{item['name']}»: " + ("в продаже." if available else "нет в наличии.")
        self._run(lambda: self.db.station_set_available(self.station, item_id, available),
                  lambda _r: self.reload_menu(done_text=text),
                  lambda error: self._handle_error(error, menu=True))

    def propose_item(self):
        """«Предложить позицию»: новая позиция своей станции ждёт решения администратора."""
        if self.busy:
            return
        categories = {item["category"] for item in self.menu.values()}
        ProposeDialog(self, categories, on_submit=self._send_proposal, draft=self.propose_draft)

    def _send_proposal(self, draft):
        self.propose_draft = None
        self._show_status("Отправка предложения...")
        self._run(lambda: self.db.propose_menu_item(draft["name"], draft["category"], draft["price"], draft["code"]),
                  lambda row: self.reload_menu(done_text=(
                      f"«{row['name']}» (код {row['code']}) отправлена администратору — ждёт решения.")),
                  lambda error: self._proposal_failed(draft, error))

    def _proposal_failed(self, draft, error):
        if error.code in ("invalid", "network"):
            self.propose_draft = draft  # введённое не теряется: окно откроется с ним снова
        self._handle_error(error, menu=True)

    # ---------- Ошибки и выход ----------

    def _handle_error(self, error, quiet=False, menu=False):
        self._schedule_refresh()
        if quiet and error.code != "auth":
            # Ошибка фонового автообновления: без окон, следующая попытка — по таймеру
            log.warning("Автообновление заказов (%s) не удалось: %s", self.station, error)
            self.auto_error = True
            self._show_status(f"{error} Повторим автоматически через {AUTO_REFRESH_MS // 1000} с.", "orange")
            return
        self._show_status(str(error), "red")
        if error.code == "auth":
            messagebox.showerror("Доступ", str(error), parent=self)
            self.on_logout()
        elif error.code in ("order_changed", "forbidden"):
            messagebox.showwarning("Меню не изменено" if menu else "Статус не изменён", str(error), parent=self)
            if menu:
                self.reload_menu()
            else:
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
        self.closed = True
        for job in (self.refresh_job, self.search_job):
            if job is not None:
                self.after_cancel(job)
        self.refresh_job = self.search_job = None
        super().destroy()


class BarFrame(KitchenFrame):
    """Экран бара: тот же экран станции для роли bar (барные позиции заказов и меню бара)."""
