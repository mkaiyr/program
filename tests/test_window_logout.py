"""Выход из аккаунта во время запроса в окнах «Заказы» и «Управление меню».

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Выход из аккаунта уничтожает кассу вместе с дочерними окнами. Ответ сервера,
пришедший после этого, не должен обращаться к уничтоженным виджетам (TclError,
invalid command name) и не должен показывать окна ошибок. Пока окно открыто,
ошибки показываются как раньше. Без подключения к Supabase.
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from tkinter import ttk
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import ui_admin  # noqa: E402
import ui_orders  # noqa: E402
from _tk import get_root  # noqa: E402


class HeldThread:
    """Поток, который не стартует сам: тест решает, когда «ответит сервер»."""
    started = []

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        HeldThread.started.append(self.target)


class FakeDB:
    def __init__(self):
        self.fail = None

    def _answer(self, value):
        if self.fail:
            raise self.fail
        return value

    def load_orders(self):
        return self._answer([{"id": 1, "created_at": "", "cashier_name": "К", "total": Decimal("1.00"),
                              "status": "ready", "items": []}])

    def load_all_menu(self):
        return self._answer([dict(id=1, code=1001, name="Капучино", category="Напитки", price=Decimal("1100.00"),
                                  is_available=True, station="kitchen")])


WINDOWS = {
    "Заказы": lambda master, db: ui_orders.OrdersWindow(master, db, role="cashier", on_auth_error=mock.Mock()),
    "Управление меню": lambda master, db: ui_admin.MenuAdminWindow(master, db, on_close=mock.Mock(),
                                                                   on_auth_error=mock.Mock()),
}


class LogoutDuringRequestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        HeldThread.started.clear()
        self.shown = []
        self.callback_errors = []
        patches = [mock.patch.object(m.threading, "Thread", HeldThread) for m in (ui_orders, ui_admin)]
        for module in (ui_orders, ui_admin):
            for name in ("showinfo", "showwarning", "showerror"):
                patches.append(mock.patch.object(module.messagebox, name,
                                                 lambda *a, _n=name, **k: self.shown.append((_n, a[0]))))
        patches.append(mock.patch.object(self.root, "report_callback_exception",
                                         lambda *exc: self.callback_errors.append(exc[1])))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def pump(self):
        for _ in range(5):
            self.root.update()

    def open_window(self, name, db):
        """Окно поверх «кассы» — фрейма, который уничтожается при выходе из аккаунта."""
        cashier_screen = ttk.Frame(self.root)
        self.addCleanup(lambda: cashier_screen.winfo_exists() and cashier_screen.destroy())
        window = WINDOWS[name](cashier_screen, db)
        self.assertEqual(len(HeldThread.started), 1, "окно должно сразу начать загрузку")
        return cashier_screen, window

    def answer(self):
        """Сервер отвечает: фоновый поток доходит до конца."""
        HeldThread.started.pop()()

    def assert_no_dangling_after(self):
        """У каждого отложенного вызова after должна существовать его Tcl-команда.

        Если ответ поставлен через after на окно, а окно уничтожено, Tk удаляет
        команду, и при срабатывании Tcl пишет «invalid command name» через bgerror —
        мимо report_callback_exception. Поэтому проверяется сама очередь.
        """
        tk = self.root.tk
        for after_id in tk.splitlist(tk.call("after", "info")):
            script = tk.splitlist(tk.call("after", "info", after_id))[0]
            command = tk.splitlist(script)[0] if script else ""
            if command:
                self.assertTrue(tk.call("info", "commands", command),
                                f"отложенный вызов {after_id} ссылается на удалённую команду {command}")

    def test_answer_after_logout(self):
        for name in WINDOWS:
            for fail in (None, dbm.AppError(dbm.NO_NETWORK, "network"), dbm.AppError("Ошибка сервера")):
                with self.subTest(window=name, fail=fail):
                    db = FakeDB()
                    db.fail = fail
                    cashier_screen, window = self.open_window(name, db)
                    cashier_screen.destroy()        # выход из аккаунта, пока идёт запрос
                    self.answer()                   # ответ пришёл после выхода
                    self.assert_no_dangling_after()
                    self.pump()
                    self.assertEqual(self.callback_errors, [])  # нет TclError / invalid command name
                    self.assertEqual(self.shown, [])            # и всплывающих окон

    def test_answer_queued_before_logout(self):
        for name in WINDOWS:
            with self.subTest(window=name):
                cashier_screen, window = self.open_window(name, FakeDB())
                self.answer()               # ответ уже стоит в очереди Tk...
                cashier_screen.destroy()    # ...а кассу закрыли раньше, чем Tk его обработал
                self.assert_no_dangling_after()
                self.pump()
                self.assertEqual(self.callback_errors, [])
                self.assertEqual(self.shown, [])

    def test_errors_still_shown_while_open(self):
        for name in WINDOWS:
            with self.subTest(window=name):
                self.shown.clear()
                db = FakeDB()
                db.fail = dbm.AppError(dbm.NO_NETWORK, "network")
                _cashier_screen, window = self.open_window(name, db)
                self.answer()
                self.pump()
                self.assertEqual(self.shown, [("showerror", "Нет связи")])
                self.assertTrue(window.winfo_exists())
                self.assertFalse(window.busy)
                self.assertEqual(self.callback_errors, [])

    def test_answer_applied_while_open(self):
        for name in WINDOWS:
            with self.subTest(window=name):
                _cashier_screen, window = self.open_window(name, FakeDB())
                self.answer()
                self.pump()
                self.assertFalse(window.busy)
                self.assertEqual(window.tree.get_children(), ("1",))


if __name__ == "__main__":
    unittest.main()
