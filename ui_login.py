"""Окно входа сотрудника по коду и паролю."""

import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import ui_theme as theme
from db import AppError, unexpected_error

FONT = ("", 18)
FORM_WIDTH = 320  # одинаковая ширина полей, клавиатуры, кнопки и сообщения
MAX_CODE_LEN = 6  # совпадает с profiles_employee_code_format


class LoginFrame(ttk.Frame):
    def __init__(self, master, db, on_success):
        super().__init__(master)
        self.db = db
        self.on_success = on_success
        self.closed = False  # экран уничтожен: ответ на вход игнорируется

        # Блок входа одной колонкой фиксированной ширины, всегда по центру окна
        box = ttk.Frame(self, padding=24)
        box.place(relx=0.5, rely=0.5, anchor="center")
        box.columnconfigure(0, minsize=FORM_WIDTH)

        ttk.Label(box, text="Кафе Бистро", style="H1.TLabel").grid(row=0, column=0)
        ttk.Label(box, text="Вход сотрудника", style="Muted.TLabel").grid(row=1, column=0, pady=(2, 20))

        ttk.Label(box, text="Код сотрудника", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
        only_digits = (self.register(lambda v: v.isdigit() and len(v) <= MAX_CODE_LEN or v == ""), "%P")
        self.code = ttk.Entry(box, font=FONT, justify="center", validate="key", validatecommand=only_digits)
        self.code.grid(row=3, column=0, sticky="ew", pady=(4, 12))

        ttk.Label(box, text="Пароль", style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        self.password = ttk.Entry(box, font=FONT, justify="center", show="•")
        self.password.grid(row=5, column=0, sticky="ew", pady=(4, 12))

        self.target = self.code  # поле, в которое вводит экранная клавиатура
        for entry in (self.code, self.password):
            entry.bind("<FocusIn>", lambda e: setattr(self, "target", e.widget))

        self._build_keypad(box).grid(row=6, column=0, sticky="ew")

        self.button = ttk.Button(box, text="Войти", style="Big.Primary.TButton", command=self.submit)
        self.button.grid(row=7, column=0, sticky="ew", pady=(14, 8))

        # Место под сообщение (до трёх строк) зарезервировано: блок не сдвигается при ошибке
        lines = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        status_area = ttk.Frame(box, height=lines * 3 + 4)
        status_area.grid(row=8, column=0, sticky="ew")
        status_area.grid_propagate(False)
        status_area.columnconfigure(0, weight=1)
        self.status = ttk.Label(status_area, text="", foreground=theme.DANGER, wraplength=FORM_WIDTH,
                                justify="center", anchor="n")
        self.status.grid(row=0, column=0, sticky="new")

        self.code.bind("<Return>", lambda _e: self.password.focus_set())
        self.password.bind("<Return>", lambda _e: self.submit())
        self.code.focus_set()

    def _build_keypad(self, master):
        """Цифровая клавиатура для сенсорного экрана."""
        pad = ttk.Frame(master)
        keys = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "⌫")
        for i, key in enumerate(keys):
            ttk.Button(pad, text=key, style="Key.TButton", width=3,
                       command=lambda k=key: self._press(k)).grid(row=i // 3, column=i % 3, sticky="nsew",
                                                                  padx=3, pady=3)
        for col in range(3):
            pad.columnconfigure(col, weight=1, uniform="keys")
        return pad

    def _press(self, key):
        entry = self.target
        if key == "C":
            entry.delete(0, tk.END)
        elif key == "⌫":
            entry.delete(len(entry.get()) - 1, tk.END)
        else:
            entry.insert(tk.END, key)  # для поля кода сработает проверка «только цифры»
        entry.focus_set()

    def submit(self):
        if str(self.button["state"]) == "disabled":
            return  # запрос уже выполняется
        code, password = self.code.get(), self.password.get()
        self.button.config(state="disabled")
        self.status.config(text="Подключение...", foreground=theme.MUTED)
        # Результат передаётся через корневое окно, как на остальных экранах: after на
        # уничтоженном экране исчез бы, а после закрытия приложения вызов из потока падает
        root = self._root()

        def deliver(callback, value):
            try:
                root.after(0, self._finish, callback, value)
            except (RuntimeError, tk.TclError):
                pass  # приложение уже закрыто

        # Сетевой запрос в отдельном потоке, чтобы окно не зависало
        threading.Thread(target=self._worker, args=(code, password, deliver), daemon=True).start()

    def _worker(self, code, password, deliver):
        try:
            profile = self.db.sign_in(code, password)
        except AppError as e:
            deliver(self._fail, str(e))
        except Exception as e:  # непредвиденная ошибка не должна молча ронять поток
            deliver(self._fail, str(unexpected_error(e)))
        else:
            deliver(self.on_success, profile)

    def _finish(self, callback, value):
        if self.closed:
            return  # экран входа закрыли, пока шёл вход
        callback(value)

    def destroy(self):
        self.closed = True
        super().destroy()

    def _fail(self, message):
        self.status.config(text=message, foreground=theme.DANGER)
        self.button.config(state="normal")
        self.password.delete(0, tk.END)
        self.password.focus_set()
