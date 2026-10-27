"""Окно «Заказы»: список заказов, состав выбранного заказа, смена статуса.

Какие заказы видны, решает RLS в Supabase; переход статуса проверяет
функция set_order_status (sql/04_order_status.sql).
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import ui_theme as theme
from db import ORDER_STATUSES, ORDERS_LIMIT, ROLE_TRANSITIONS, AppError
from ui_order import fmt_money


class OrdersWindow(tk.Toplevel):
    def __init__(self, master, db, role, on_auth_error):
        super().__init__(master)
        self.db = db
        self.transitions = ROLE_TRANSITIONS.get(role, {})
        self.is_admin = role == "admin"
        self.on_auth_error = on_auth_error
        self.orders = {}  # id -> заказ из db.load_orders()
        self.busy = False
        self.closed = False          # окно уничтожено: результаты фоновых запросов игнорируются
        self.reload_pending = False  # обновление, запрошенное во время другого запроса

        self.title("Заказы")
        self.minsize(760, 520)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._build()
        self.reload()

    # ---------- Разметка ----------

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Заказы", style="H1.TLabel").pack(side="left")
        self.close_btn = ttk.Button(top, text="Закрыть", command=self.close)
        self.close_btn.pack(side="right")
        self.reload_btn = ttk.Button(top, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=6)

        cols = ("id", "date", "cashier", "total", "status")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="browse", height=10)
        for col, title, width, anchor in (("id", "№", 60, "center"), ("date", "Дата", 130, "w"),
                                          ("cashier", "Кассир", 180, "w"), ("total", "Сумма", 110, "e"),
                                          ("status", "Статус", 90, "center")):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=anchor)
        for status, color in theme.STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)
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

        ttk.Label(root, text="Состав заказа", style="H2.TLabel").pack(anchor="w", pady=(4, 4))
        cols = ("name", "price", "qty", "sum")
        self.items_tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="none", height=6)
        for col, title, width, anchor in (("name", "Позиция", 220, "w"), ("price", "Цена", 100, "e"),
                                          ("qty", "Кол-во", 70, "center"), ("sum", "Сумма", 110, "e")):
            self.items_tree.heading(col, text=title)
            self.items_tree.column(col, width=width, anchor=anchor)
        self.items_tree.pack(fill="both", expand=True)

        self.status = ttk.Label(root, text="", style="Status.TLabel", wraplength=700)
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
                deliver(on_err, AppError(f"Непредвиденная ошибка: {e}"))
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
        for btn in (self.reload_btn, self.close_btn):
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
        self._show_status("Загрузка заказов...", "gray")
        self._run(self.db.load_orders, lambda orders: self._loaded(orders, select_id, done_text),
                  self._handle_error)

    def _loaded(self, orders, select_id, done_text):
        self.orders = {order["id"]: order for order in orders}
        self.tree.delete(*self.tree.get_children())
        for order in orders:
            self.tree.insert("", "end", iid=str(order["id"]), tags=(order["status"],), values=(
                order["id"], order["created_at"], order["cashier_name"], fmt_money(order["total"]),
                ORDER_STATUSES.get(order["status"], order["status"])))

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
        if order is not None:
            for item in order["items"]:
                self.items_tree.insert("", "end", values=(
                    item["item_name"], fmt_money(item["unit_price"]), item["quantity"],
                    fmt_money(item["line_total"])))
        self._update_next_btn()

    def _update_next_btn(self):
        order = self.orders.get(self._selected_id())
        next_status = self.transitions.get(order["status"]) if order else None
        if self.busy or next_status is None:
            waiting = {"completed": "Заказ выдан", "accepted": "Ожидает приготовления", "preparing": "Готовится"}
            text = waiting.get(order["status"], "Сменить статус") if order else "Сменить статус"
            self.next_btn.config(text=text, state="disabled")
        else:
            self.next_btn.config(text=f"Отметить «{ORDER_STATUSES[next_status]}»", state="normal")
        if self.force_btn is not None:
            can_force = order is not None and order["status"] in ("accepted", "preparing") and not self.busy
            self.force_btn.config(state="normal" if can_force else "disabled")

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

    # ---------- Ошибки и закрытие ----------

    def _handle_error(self, error):
        self._show_status(str(error), "red")
        if error.code == "auth":
            messagebox.showerror("Доступ", str(error), parent=self)
            self.destroy()
            self.on_auth_error()
        elif error.code in ("order_changed", "forbidden"):
            messagebox.showwarning("Статус не изменён", str(error), parent=self)
            self.reload()
        elif error.code == "network":
            messagebox.showerror("Нет связи", str(error), parent=self)
        else:
            messagebox.showerror("Ошибка", str(error), parent=self)

    def close(self):
        if self.busy:
            return
        self.destroy()

    def destroy(self):
        self.closed = True
        super().destroy()
