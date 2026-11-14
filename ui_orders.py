"""«Заказы» — история: поиск, фильтры, карточка заказа, смена статуса, аннулирование, удаление.

Логика в OrdersMixin; OrdersWindow — окно кассира, OrdersPanel — раздел административной оболочки.
Аннулирование (void_order, sql/11_service_charge_void.sql) сохраняет заказ, состав и причину;
в выручку такой заказ не входит.

Какие заказы видны, решает RLS в Supabase; переход статуса проверяет
функция set_order_status (sql/04_order_status.sql). «Удалить» отправляет выданный
заказ в «Удалённые заказы» (delete_order, sql/10_order_deletion.sql): кассир — свои,
администратор — любые; раздел «Удалённые заказы» — только у администратора.
"""

import threading
import tkinter as tk
from datetime import datetime, time, timedelta
from tkinter import filedialog, messagebox, ttk

import ui_theme as theme
from db import ORDER_STATUSES, ORDERS_LIMIT, ROLE_TRANSITIONS, AppError, unexpected_error
from money import SERVICE_PERCENT, fmt_money
from reports import export_orders_csv, parse_date
from ui_dialogs import FormDialog

ANY_STATUS = "Все статусы"
VOID_FILTERS = {"Все заказы": None, "Без аннулированных": False, "Только аннулированные": True}
STATUS_BY_LABEL = {label: key for key, label in ORDER_STATUSES.items()}


class OrdersMixin:
    """Общая логика окна и панели «Заказы». Класс-наследник задаёт _setup_window() и _end()."""

    def _init_orders(self, db, role, on_auth_error):
        self.db = db
        self.transitions = ROLE_TRANSITIONS.get(role, {})
        self.is_admin = role == "admin"
        self.on_auth_error = on_auth_error
        self.orders = {}  # id -> заказ из db.load_orders()
        self.busy = False
        self.closed = False          # окно уничтожено: результаты фоновых запросов игнорируются
        self.reload_pending = False  # обновление, запрошенное во время другого запроса
        self.deleted_window = None   # окно «Удалённые заказы» (администратор)

        self._setup_window()
        self._build()
        self.reload()

    # ---------- Разметка ----------

    def _build(self):
        root = ttk.Frame(self, padding=16 if self.WINDOW else 0)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="История заказов" if not self.WINDOW else "Заказы", style="H1.TLabel").pack(side="left")
        self.close_btn = None
        if self.WINDOW:
            self.close_btn = ttk.Button(top, text="Закрыть", command=self.close)
            self.close_btn.pack(side="right")
        self.reload_btn = ttk.Button(top, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=6)
        self.export_btn = ttk.Button(top, text="Экспорт CSV", command=self.export_csv)
        self.export_btn.pack(side="right")
        # Кнопка — только удобство: список удалённых сервер отдаёт только администратору
        self.deleted_btn = None
        if self.is_admin:
            self.deleted_btn = ttk.Button(top, text="Удалённые заказы", command=self.open_deleted)
            self.deleted_btn.pack(side="right", padx=6)

        # Фильтры: номер, дата, статус, аннулированные. Отбор выполняет сервер.
        filters = ttk.Frame(root)
        filters.pack(fill="x", pady=(0, 4))
        filters2 = ttk.Frame(root)
        filters2.pack(fill="x", pady=(0, 8))
        ttk.Label(filters, text="№:").pack(side="left")
        self.number_entry = ttk.Entry(filters, width=7)
        self.number_entry.pack(side="left", padx=(4, 10))
        ttk.Label(filters, text="Дата (ДД.ММ.ГГГГ):").pack(side="left")
        self.date_entry = ttk.Entry(filters, width=12)
        self.date_entry.pack(side="left", padx=(4, 10))
        self.status_box = ttk.Combobox(filters2, values=[ANY_STATUS, *ORDER_STATUSES.values()], state="readonly",
                                       width=14)
        self.status_box.set(ANY_STATUS)
        self.status_box.pack(side="left", padx=(0, 10))
        self.void_box = ttk.Combobox(filters2, values=list(VOID_FILTERS), state="readonly", width=21)
        self.void_box.set("Все заказы")
        self.void_box.pack(side="left", padx=(0, 10))
        self.find_btn = ttk.Button(filters2, text="Найти", style="Primary.TButton", command=self.reload)
        self.find_btn.pack(side="left")
        ttk.Button(filters2, text="Сбросить", command=self.reset_filters).pack(side="left", padx=6)
        for widget in (self.number_entry, self.date_entry):
            widget.bind("<Return>", lambda _e: self.reload())

        cols = ("id", "date", "cashier", "total", "status")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="browse", height=10)
        for col, title, width, anchor in (("id", "№", 60, "center"), ("date", "Дата", 130, "w"),
                                          ("cashier", "Кассир", 180, "w"), ("total", "Сумма", 110, "e"),
                                          ("status", "Статус", 90, "center")):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=anchor)
        for status, color in theme.STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)
        self.tree.tag_configure("voided", foreground=theme.DISABLED_FG)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_items())

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=6)
        self.next_btn = ttk.Button(actions, text="Сменить статус", style="Big.Primary.TButton",
                                   command=self.advance_status)
        self.next_btn.pack(side="left")
        # Только администратор и только явно: все станции и заказ — «Готов»
        self.force_btn = None
        if self.is_admin:
            self.force_btn = ttk.Button(actions, text="Готов принудительно", style="Danger.TButton",
                                        command=self.force_ready)
            self.force_btn.pack(side="left", padx=12)
        # Только выданный заказ; права (свои — кассиру, любые — администратору) проверяет сервер
        self.delete_btn = ttk.Button(actions, text="Удалить", command=self.delete_order)
        self.delete_btn.pack(side="right")
        # Аннулирование — основной способ убрать ошибочный заказ: состав и причина сохраняются
        self.void_btn = ttk.Button(actions, text="Аннулировать", style="Danger.TButton", command=self.void_order)
        self.void_btn.pack(side="right", padx=8)

        ttk.Label(root, text="Карточка заказа", style="H2.TLabel").pack(anchor="w", pady=(4, 4))
        detail = ttk.Frame(root)
        detail.pack(fill="both", expand=True)
        detail.columnconfigure(0, weight=3)
        detail.columnconfigure(1, weight=2, minsize=280)
        detail.rowconfigure(0, weight=1)
        side = theme.card(detail)
        side.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.detail_title = ttk.Label(side, text="Выберите заказ", style="CardH2.TLabel")
        self.detail_title.pack(anchor="w")
        self.detail_info = ttk.Label(side, text="", style="CardMuted.TLabel", justify="left", wraplength=260)
        self.detail_info.pack(anchor="w", pady=(2, 8))
        self.detail_sums = {}
        for key, title in (("subtotal", "Подытог"), ("service", f"Надбавка {SERVICE_PERCENT:.0f}%"),
                           ("total", "Итого")):
            row = ttk.Frame(side, style="Card.TFrame")
            row.pack(fill="x")
            ttk.Label(row, text=title, style="CardMuted.TLabel" if key != "total" else "CardBold.TLabel").pack(side="left")
            label = ttk.Label(row, text="", style="CardBold.TLabel")
            label.pack(side="right")
            self.detail_sums[key] = label
        self.detail_void = ttk.Label(side, text="", foreground=theme.DANGER, wraplength=260, justify="left")
        self.detail_void.pack(anchor="w", pady=(8, 0))
        items_box = ttk.Frame(detail)
        items_box.grid(row=0, column=0, sticky="nsew")
        cols = ("name", "price", "qty", "sum")
        self.items_tree = ttk.Treeview(items_box, columns=cols, show="headings", selectmode="none", height=6)
        for col, title, width, anchor in (("name", "Позиция", 220, "w"), ("price", "Цена", 100, "e"),
                                          ("qty", "Кол-во", 70, "center"), ("sum", "Сумма", 110, "e")):
            self.items_tree.heading(col, text=title)
            self.items_tree.column(col, width=width, anchor=anchor)
        self.items_tree.pack(fill="both", expand=True)

        self.status = ttk.Label(root, text="", style="Status.TLabel", wraplength=900)
        self.status.pack(fill="x", pady=(6, 0))

        self._update_next_btn()

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
        if self.closed:
            return  # обработчик закрыл окно (истёкшая сессия -> выход): отложенное обновление не нужно
        if self.reload_pending and not self.busy:
            self.reload_pending = False
            self.reload()

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.reload_btn, self.close_btn, self.deleted_btn, self.find_btn, self.export_btn):
            if btn is not None:
                btn.config(state=state)
        self._update_next_btn()

    def _show_status(self, text, color="black"):
        self.status.config(text=text, foreground=theme.message_color(color))

    # ---------- Список ----------

    def reload(self, select_id=None, done_text=None):
        if self.busy:
            self.reload_pending = True
            return
        if select_id is None:
            select_id = self._selected_id()
        try:
            filters = self._filters()
        except AppError as e:
            self._show_status(str(e), "red")
            return
        self._show_status("Загрузка заказов...", "gray")
        self._run(lambda: self.db.load_orders(**filters), lambda orders: self._loaded(orders, select_id, done_text),
                  self._handle_error)

    def _filters(self):
        """Фильтры из полей -> параметры db.load_orders (пустые не передаются)."""
        out = {}
        number = self.number_entry.get().strip()
        if number:
            if not number.isdigit() or int(number) > 2**62:
                raise AppError("Номер заказа — целое число.", "invalid")
            out["order_id"] = int(number)
        if self.date_entry.get().strip():
            day = parse_date(self.date_entry.get())
            out["date_from"] = datetime.combine(day, time.min).astimezone()
            out["date_to"] = datetime.combine(day + timedelta(days=1), time.min).astimezone()
        status = STATUS_BY_LABEL.get(self.status_box.get())
        if status:
            out["status"] = status
        voided = VOID_FILTERS[self.void_box.get()]
        if voided is not None:
            out["voided"] = voided
        return out

    def reset_filters(self):
        if self.busy:
            return
        self.number_entry.delete(0, "end")
        self.date_entry.delete(0, "end")
        self.status_box.set(ANY_STATUS)
        self.void_box.set("Все заказы")
        self.reload()

    def export_csv(self):
        if not self.orders:
            self._show_status("Нечего экспортировать: список пуст.", "orange")
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile="zakazy.csv",
                                            filetypes=[("CSV (Excel)", "*.csv")])
        if not path:
            return
        try:
            count = export_orders_csv(path, list(self.orders.values()))
        except OSError as e:
            self._show_status(f"Файл не сохранён: {e.strerror or 'ошибка записи'}.", "red")
            return
        self._show_status(f"Сохранено заказов: {count} — {path}", "green")

    def _loaded(self, orders, select_id, done_text):
        self.orders = {order["id"]: order for order in orders}
        self.tree.delete(*self.tree.get_children())
        for order in orders:
            voided = order.get("voided", False)
            self.tree.insert("", "end", iid=str(order["id"]), tags=("voided" if voided else order["status"],),
                             values=(order["id"], order["created_at"], order["cashier_name"],
                                     fmt_money(order["total"]),
                                     "Аннулирован" if voided else ORDER_STATUSES.get(order["status"], order["status"])))

        if select_id in self.orders:
            self.tree.selection_set(str(select_id))
            self.tree.see(str(select_id))
        self._show_items()

        if done_text:
            self._show_status(done_text, "green")
        elif not orders:
            self._show_status("Заказов пока нет.", "gray")
        else:
            text = f"Заказов: {len(orders)}"
            if len(orders) >= ORDERS_LIMIT:
                text += f" (показаны последние {ORDERS_LIMIT})"
            self._show_status(text, "gray")

    def _selected_id(self):
        selected = self.tree.selection()
        return int(selected[0]) if selected else None

    def _show_items(self):
        self.items_tree.delete(*self.items_tree.get_children())
        order = self.orders.get(self._selected_id())
        self._show_detail(order)
        if order is not None:
            for item in order["items"]:
                self.items_tree.insert("", "end", values=(
                    item["item_name"], fmt_money(item["unit_price"]), item["quantity"],
                    fmt_money(item["line_total"])))
        self._update_next_btn()

    def _show_detail(self, order):
        """Карточка выбранного заказа: кассир, время, подытог, надбавка, итог, аннулирование."""
        if order is None:
            self.detail_title.config(text="Выберите заказ")
            self.detail_info.config(text="")
            self.detail_void.config(text="")
            for label in self.detail_sums.values():
                label.config(text="")
            return
        self.detail_title.config(text=f"Заказ № {order['id']}")
        self.detail_info.config(text=f"Создан: {order['created_at']}\nКассир: {order['cashier_name']}\n"
                                     f"Статус: {ORDER_STATUSES.get(order['status'], order['status'])}")
        sub, service = order.get("subtotal", order["total"]), order.get("service_amount", 0)
        self.detail_sums["subtotal"].config(text=fmt_money(sub))
        self.detail_sums["service"].config(text=fmt_money(service))
        self.detail_sums["total"].config(text=fmt_money(order["total"]))
        if order.get("voided"):
            self.detail_void.config(text=f"Аннулирован {order['voided_at']} ({order['voided_by_name']}).\n"
                                         f"Причина: {order['void_reason']}")
        else:
            self.detail_void.config(text="")

    def _update_next_btn(self):
        order = self.orders.get(self._selected_id())
        next_status = self.transitions.get(order["status"]) if order else None
        voided = bool(order and order.get("voided"))
        can_void = order is not None and not voided and not self.busy
        self.void_btn.config(state="normal" if can_void else "disabled")
        if voided:
            next_status = None
        if self.busy or next_status is None:
            waiting = {"completed": "Заказ выдан", "accepted": "Ожидает приготовления", "preparing": "Готовится"}
            text = waiting.get(order["status"], "Сменить статус") if order else "Сменить статус"
            if voided:
                text = "Заказ аннулирован"
            self.next_btn.config(text=text, state="disabled")
        else:
            self.next_btn.config(text=f"Отметить «{ORDER_STATUSES[next_status]}»", state="normal")
        if self.force_btn is not None:
            can_force = (order is not None and order["status"] in ("accepted", "preparing") and not self.busy
                         and not voided)
            self.force_btn.config(state="normal" if can_force else "disabled")
        can_delete = order is not None and order["status"] == "completed" and not self.busy and not voided
        self.delete_btn.config(state="normal" if can_delete else "disabled")

    # ---------- Смена статуса ----------

    def advance_status(self):
        if self.busy:
            return
        order = self.orders.get(self._selected_id())
        if order is None:
            self._show_status("Выберите заказ в списке.", "orange")
            return
        next_status = self.transitions.get(order["status"])
        if next_status is None:
            return
        label = ORDER_STATUSES[next_status]
        if not messagebox.askyesno("Статус заказа", f"Отметить заказ № {order['id']} как «{label}»?",
                                   parent=self):
            return
        order_id = order["id"]
        self._show_status("Сохранение статуса...", "gray")
        self._run(lambda: self.db.set_order_status(order_id, next_status),
                  lambda _r: self.reload(select_id=order_id,
                                         done_text=f"Заказ № {order_id}: статус «{label}»."),
                  self._handle_error)

    def force_ready(self):
        if self.busy or not self.is_admin:
            return
        order = self.orders.get(self._selected_id())
        if order is None or order["status"] not in ("accepted", "preparing"):
            return
        if not messagebox.askyesno(
                "Готов принудительно",
                f"Отметить заказ № {order['id']} готовым целиком?\n"
                "Все станции (кухня и бар) будут отмечены «Готов», даже если ещё готовят.", parent=self):
            return
        order_id = order["id"]
        self._show_status("Сохранение статуса...", "gray")
        self._run(lambda: self.db.force_order_ready(order_id),
                  lambda r: self.reload(select_id=order_id, done_text=(
                      f"Заказ № {order_id}: «Готов» принудительно (станций отмечено: {r['stations_forced']}).")),
                  self._handle_error)

    # ---------- Аннулирование ----------

    def void_order(self):
        """Аннулировать выбранный заказ с обязательной причиной (в выручку не входит)."""
        if self.busy:
            return
        order = self.orders.get(self._selected_id())
        if order is None or order.get("voided"):
            return
        order_id = order["id"]

        def submit(values):
            reason = values["reason"].strip()
            if not 3 <= len(reason) <= 200:
                return "Причина — от 3 до 200 символов."
            self._show_status("Аннулирование...", "gray")
            self._run(lambda: self.db.void_order(order_id, reason),
                      lambda r: self.reload(select_id=order_id, done_text=(
                          f"Заказ № {order_id} аннулирован." if r["changed"] else f"Заказ № {order_id} уже аннулирован.")),
                      self._handle_error)
            return None

        FormDialog(self, f"Аннулировать заказ № {order_id}", [("reason", "Причина:", "", False)], submit,
                   ok_text="Аннулировать", ok_style="Danger.TButton",
                   hint=f"Заказ на {fmt_money(order['total'])} останется в истории с причиной, "
                        "но не войдёт в выручку и пропадёт с экранов кухни и бара.")

    # ---------- Удаление ----------

    def delete_order(self):
        """Выданный заказ — в «Удалённые заказы». Восстановить его сможет администратор."""
        if self.busy:
            return
        order = self.orders.get(self._selected_id())
        if order is None or order["status"] != "completed":
            return
        if not messagebox.askyesno(
                "Удалить заказ",
                f"Удалить заказ № {order['id']} на сумму {fmt_money(order['total'])}?\n"
                "Он пропадёт из истории и попадёт в «Удалённые заказы» — администратор сможет его "
                "восстановить.", parent=self):
            return
        order_id = order["id"]
        self._show_status("Удаление...", "gray")
        self._run(lambda: self.db.delete_order(order_id),
                  lambda r: self.reload(done_text=(f"Заказ № {order_id} перенесён в «Удалённые заказы»." if r["changed"]
                                                   else f"Заказ № {order_id} уже был удалён.")),
                  self._handle_error)

    def open_deleted(self):
        if self.busy or not self.is_admin:
            return
        if self.deleted_window is not None and self.deleted_window.winfo_exists():
            self.deleted_window.deiconify()
            self.deleted_window.lift()
            self.deleted_window.reload()
            return
        from ui_deleted_orders import DeletedOrdersWindow
        self.deleted_window = DeletedOrdersWindow(self, self.db, on_change=self.reload,
                                                  on_auth_error=self._auth_error_from_child)

    def _auth_error_from_child(self):
        # Окно «Удалённые заказы» уже закрылось; сессия истекла — выход, как при своей ошибке
        if not self.closed:
            self._end()
            self.on_auth_error()

    # ---------- Ошибки и закрытие ----------

    def _handle_error(self, error):
        self._show_status(str(error), "red")
        if error.code == "auth":
            messagebox.showerror("Доступ", str(error), parent=self)
            self._end()
            self.on_auth_error()
        elif error.code in ("order_changed", "forbidden"):
            messagebox.showwarning("Статус не изменён", str(error), parent=self)
            self.reload()
        elif error.code == "network":
            messagebox.showerror("Нет связи", str(error), parent=self)
        else:
            messagebox.showerror("Ошибка", str(error), parent=self)


class OrdersWindow(OrdersMixin, tk.Toplevel):
    """Окно «Заказы» кассира (и администратора на экране кассы)."""

    WINDOW = True

    def __init__(self, master, db, role, on_auth_error):
        tk.Toplevel.__init__(self, master)
        self._init_orders(db, role, on_auth_error)

    def _setup_window(self):
        self.title("Заказы")
        self.minsize(900, 600)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _end(self):
        self.destroy()

    def close(self):
        if self.busy:
            return
        self.destroy()

    def destroy(self):
        self.closed = True
        super().destroy()


class OrdersPanel(OrdersMixin, ttk.Frame):
    """Раздел «История заказов» административной оболочки."""

    WINDOW = False

    def __init__(self, master, db, on_auth_error):
        ttk.Frame.__init__(self, master)
        self._init_orders(db, "admin", on_auth_error)

    def _setup_window(self):
        pass

    def _end(self):
        pass  # панель закрывает оболочка при выходе из аккаунта

    def destroy(self):
        self.closed = True
        super().destroy()
