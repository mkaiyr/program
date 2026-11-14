"""Точка входа: python main.py"""

import tkinter as tk
from tkinter import messagebox

import ui_theme
from config import ConfigError, load_config
from db import Database
from ui_login import LoginFrame
from ui_kitchen import BarFrame, KitchenFrame
from ui_order import OrderFrame
from ui_shell import AdminShell
from ui_shell import AdminShell


class App(tk.Tk):
    def __init__(self, db, server_host=""):
        super().__init__()
        self.db = db
        self.server_host = server_host
        ui_theme.apply(self)
        self.title("Кафе Бистро — касса")
        self.minsize(420, 620)
        self.current = None
        self.show_login()

    def _show(self, frame):
        if self.current is not None:
            self.current.destroy()
        self.current = frame
        frame.pack(fill="both", expand=True)

    def show_login(self):
        self.minsize(420, 620)
        self._show(LoginFrame(self, self.db, on_success=self.show_main))

    def show_main(self, profile):
        self.minsize(960, 600)
        if profile["role"] == "kitchen":
            self._show(KitchenFrame(self, self.db, profile, on_logout=self.logout))
        elif profile["role"] == "bar":
            self._show(BarFrame(self, self.db, profile, on_logout=self.logout))
        elif profile["role"] == "admin":
            self.minsize(1100, 680)
            self._show(AdminShell(self, self.db, profile, on_logout=self.logout, server_host=self.server_host))
        elif profile["role"] == "admin":
            self.minsize(1100, 680)
            self._show(AdminShell(self, self.db, profile, on_logout=self.logout, server_host=self.server_host))
        else:
            self._show(OrderFrame(self, self.db, profile, on_logout=self.logout))

    def logout(self):
        # Сессия на этом компьютере сбрасывается сразу; сервер уведомляется в фоновом потоке,
        # чтобы окно не зависало, если он недоступен (таймаут — секунды)
        self.db.sign_out(background=True)
        self.show_login()


def main():
    try:
        url, key = load_config()
        db = Database(url, key)
    except ConfigError as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Ошибка настроек", str(e))
        return
    host = url.split('//', 1)[-1].split('/', 1)[0]
    App(db, server_host=host).mainloop()


if __name__ == "__main__":
    main()
