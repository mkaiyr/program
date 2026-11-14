"""Окно «Удалённые заказы» (только администратор): восстановление и окончательное удаление.

Список удалённых, восстановление, окончательное удаление и журнал окончательных удалений
дают только функции admin_* (sql/10_order_deletion.sql) — они проверяют, что вызывает
активный администратор. Скрытая кнопка у кассира — только удобство.
"""

import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import ui_theme as theme
from db import AppError, unexpected_error
from ui_order import fmt_money


class DeletedOrdersWindow(tk.Toplevel):
    def __init__(self, master, db, on_change, on_auth_error):
        super().__init__(master)
        self.db = db
        self.on_change = on_change          # заказ восстановлен — обновить окно «Заказы»
        self.on_auth_error = on_auth_error
        self.orders = {}                    # id -> удалённый заказ из db.load_deleted_orders()
        self.busy = False
        self.closed = False                 # окно уничтожено: результаты фоновых запросов игнорируются

        self.title("Удалённые заказы")
        self.minsize(820, 560)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._build()
        self.reload()

    # ---------- Разметка ----------

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Удалённые заказы", style="H1.TLabel").pack(side="left")
        self.close_btn = ttk.Button(top, text="Закрыть", command=self.close)
        self.close_btn.pack(side="right")
        self.reload_btn = ttk.Button(top, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right", padx=6)

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)
        self.tabs.add(self._build_deleted_tab(), text="Удалённые")
        self.tabs.add(self._build_log_tab(), text="Журнал окончательных удалений")

        self.status = ttk.Label(root, text="", style="Status.TLabel", wraplength=760)
        self.status.pack(fill="x", pady=(6, 0))
        self._update_buttons()

    def _build_deleted_tab(self):
        tab = ttk.Frame(self.tabs, padding=(0, 10, 0, 0))
        cols = ("id", "date", "cashier", "total", "deleted", "deleted_by")
        self.tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse", height=8)
        for col, title, width, anchor in (("id", "№", 60, "center"), ("date", "Оформлен", 130, "w"),
                                          ("cashier", "Кассир", 150, "w"), ("total", "Сумма", 110, "e"),
                                          ("deleted", "Удалён", 130, "w"), ("deleted_by", "Кто удалил", 150, "w")):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_items())

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=6)
        self.restore_btn = ttk.Button(actions, text="Восстановить", style="Success.TButton", command=self.restore)
        self.restore_btn.pack(side="left")
        self.purge_btn = ttk.Button(actions, text="Удалить навсегда", style="Danger.TButton", command=self.purge)
        self.purge_btn.pack(side="right")

        ttk.Label(tab, text="Состав заказа", style="H2.TLabel").pack(anchor="w", pady=(4, 4))
        cols = ("name", "price", "qty", "sum")
        self.items_tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="none", height=5)
        for col, title, width, anchor in (("name", "Позиция", 220, "w"), ("price", "Цена", 100, "e"),
                                          ("qty", "Кол-во", 70, "center"), ("sum", "Сумма", 110, "e")):
            self.items_tree.heading(col, text=title)
            self.items_tree.column(col, width=width, anchor=anchor)
        self.items_tree.pack(fill="both", expand=True)
        return tab

    def _build_log_tab(self):
        tab = ttk.Frame(self.tabs, padding=(0, 10, 0, 0))
        ttk.Label(tab, text="Состав стёртых заказов не хранится — только номер, сумма, кассир, кто и когда удалил.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        cols = ("id", "total", "cashier", "created", "purged_by", "purged_at")
        self.log_tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="none", height=10)
        for col, title, width, anchor in (("id", "№", 60, "center"), ("total", "Сумма", 110, "e"),
                                          ("cashier", "Кассир", 150, "w"), ("created", "Оформлен", 130, "w"),
                                          ("purged_by", "Удалил навсегда", 150, "w"), ("purged_at", "Когда", 130, "w")):
            self.log_tree.heading(col, text=title)
            self.log_tree.column(col, width=width, anchor=anchor)
        self.log_tree.pack(fill="both", expand=True)
        return tab

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
        for btn in (self.reload_btn, self.close_btn):
            btn.config(state="disabled" if busy else "normal")
        self._update_buttons()

    def _show_status(self, text, color="black"):
        self.status.config(text=text, foreground=theme.message_color(color))

    # ---------- Списки ----------

    def reload(self, done_text=None):
        if self.busy:
            return
        self._show_status("Загрузка...", "gray")
        self._run(lambda: (self.db.load_deleted_orders(), self.db.load_purge_log()),
                  lambda result: self._loaded(*result, done_text), self._handle_error)

    def _loaded(self, orders, log, done_text):
        selected = self._selected_id()
        self.orders = {order["id"]: order for order in orders}
        self.tree.delete(*self.tree.get_children())
        for order in orders:
            self.tree.insert("", "end", iid=str(order["id"]), values=(
                order["id"], order["created_at"], order["cashier_name"], fmt_money(order["total"]),
                order["deleted_at"], order["deleted_by_name"]))
        if selected in self.orders:
            self.tree.selection_set(str(selected))
        self._show_items()

        self.log_tree.delete(*self.log_tree.get_children())
        for row in log:
            self.log_tree.insert("", "end", values=(
                row["order_id"], fmt_money(row["total"]), row["cashier_name"], row["order_created_at"],
                row["purged_by_name"], row["purged_at"]))

        if done_text:
            self._show_status(done_text, "green")
        else:
            self._show_status(f"Удалённых заказов: {len(orders)}" if orders else "Удалённых заказов нет.", "gray")

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
        self._update_buttons()

    def _update_buttons(self):
        state = "normal" if self._selected_id() in self.orders and not self.busy else "disabled"
        self.restore_btn.config(state=state)
        self.purge_btn.config(state=state)

    # ---------- Действия ----------

    def restore(self):
        if self.busy:
            return
        order = self.orders.get(self._selected_id())
        if order is None:
            return
        if not messagebox.askyesno("Восстановить заказ",
                                   f"Вернуть заказ № {order['id']} в обычную историю заказов?", parent=self):
            return
        order_id = order["id"]
        self._show_status("Восстановление...", "gray")
        self._run(lambda: self.db.restore_order(order_id), lambda r: self._restored(order_id, r), self._handle_error)

    def _restored(self, order_id, result):
        self.on_change()
        self.reload(done_text=f"Заказ № {order_id} восстановлен." if result["changed"]
                    else f"Заказ № {order_id} уже был в истории.")

    def purge(self):
        """Удалить навсегда: предупреждение, затем ввод номера заказа для подтверждения."""
        if self.busy:
            return
        order = self.orders.get(self._selected_id())
        if order is None:
            return
        if not messagebox.askyesno(
                "Удалить навсегда",
                f"Заказ № {order['id']} на сумму {fmt_money(order['total'])} будет удалён навсегда вместе "
                "с составом. Это действие необратимо — восстановить заказ будет нельзя.\n\n"
                "В журнале останутся только номер, сумма, кассир, кто и когда удалил.\n\nПродолжить?",
                icon="warning", parent=self):
            return
        typed = simpledialog.askstring(
            "Подтверждение", f"Чтобы удалить навсегда, введите номер заказа ({order['id']}):", parent=self)
        if typed is None:
            return
        if typed.strip() != str(order["id"]):
            messagebox.showwarning("Не удалено", "Номер не совпал — заказ не удалён.", parent=self)
            return
        order_id = order["id"]
        self._show_status("Удаление навсегда...", "gray")
        self._run(lambda: self.db.purge_order(order_id),
                  lambda r: self.reload(done_text=(
                      f"Заказ № {order_id} удалён навсегда (позиций: {r['items_deleted']}). Запись — в журнале."
                      if r["changed"] else f"Заказ № {order_id} уже был удалён навсегда.")),
                  self._handle_error)

    # ---------- Ошибки и закрытие ----------

    def _handle_error(self, error):
        self._show_status(str(error), "red")
        if error.code == "auth":
            messagebox.showerror("Доступ", str(error), parent=self)
            self.destroy()
            self.on_auth_error()
        elif error.code == "forbidden":
            # Нет прав администратора (например, роль сменили): повторная загрузка дала бы тот же
            # отказ по кругу — окно закрывается, как «Управление меню»
            messagebox.showerror("Нет прав", str(error), parent=self)
            self.close()
        elif error.code == "order_changed":
            messagebox.showwarning("Не выполнено", str(error), parent=self)
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
