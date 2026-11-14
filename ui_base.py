"""Общая основа новых панелей: фоновые запросы, блокировка кнопок, строка состояния, ошибки.

Сетевые запросы выполняются только в фоновом потоке (run), виджеты меняются только в потоке Tk.
Результат передаётся через корневое окно: панель может быть уничтожена (выход из аккаунта,
переключение раздела) до ответа сервера — такой ответ игнорируется.
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import ui_theme as theme
from db import AppError, unexpected_error


class Panel(ttk.Frame):
    """Панель раздела. Наследник создаёт виджеты и добавляет кнопки в self.busy_widgets."""

    def __init__(self, master, db, on_auth_error):
        super().__init__(master)
        self.db = db
        self.on_auth_error = on_auth_error
        self.busy = False
        self.closed = False
        self.busy_widgets = []
        self.status = None

    # ---------- Фоновые запросы ----------

    def run(self, task, on_ok, on_err=None):
        """task() — в потоке; on_ok(result) / on_err(AppError) — в потоке Tk."""
        self.set_busy(True)
        root = self._root()
        on_err = on_err or self.handle_error

        def deliver(callback, value):
            try:
                root.after(0, self._finish, callback, value)
            except (RuntimeError, tk.TclError):
                pass  # приложение закрыто

        def worker():
            try:
                result = task()
            except AppError as e:
                deliver(on_err, e)
            except Exception as e:  # noqa: BLE001 — непредвиденная ошибка не должна молча ронять поток
                deliver(on_err, unexpected_error(e))
            else:
                deliver(on_ok, result)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, callback, value):
        if self.closed:
            return
        self.set_busy(False)
        callback(value)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in self.busy_widgets:
            try:
                widget.config(state=state)
            except tk.TclError:
                pass

    # ---------- Состояние и ошибки ----------

    def build_status(self, master):
        self.status = ttk.Label(master, text="", style="Status.TLabel", wraplength=900, justify="left")
        return self.status

    def show_status(self, text, color="black"):
        if self.status is not None:
            self.status.config(text=text, foreground=theme.message_color(color))

    def handle_error(self, error):
        self.show_status(str(error), "red")
        if error.code == "auth":
            messagebox.showerror("Доступ", str(error), parent=self)
            self.on_auth_error()
        elif error.code == "network":
            messagebox.showerror("Нет связи", str(error), parent=self)

    def destroy(self):
        self.closed = True
        super().destroy()
