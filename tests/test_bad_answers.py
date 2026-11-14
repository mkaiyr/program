"""Ответы сервера неверной формы и поздний ответ на экране входа.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Без подключения к Supabase: у Database подменён клиент (MagicMock), поток —
синхронным или «задержанным» вызовом. Проверяется видимый результат: текст
ошибки, отсутствие исключений в обработчиках Tk и отложенных вызовов на
удалённые команды.
"""

import sys
import tkinter
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import ui_kitchen  # noqa: E402
import ui_login  # noqa: E402
import ui_orders  # noqa: E402
from _tk import get_root  # noqa: E402

UNEXPECTED = "Сервер вернул неожиданный ответ"


def make_db():
    db = dbm.Database.__new__(dbm.Database)
    db.profile = None
    db.client = mock.MagicMock()
    return db


def station_answer(items):
    return [{"id": 7, "created_at": "2026-10-23T09:05:00+00:00", "status": "accepted", "items": items}]


def orders_answer(items):
    return [{"id": 3, "created_at": "2026-10-23T09:05:00+00:00", "total": "900.00", "status": "accepted",
             "cashier": {"full_name": "Кассир"}, "order_items": items}]


GOOD_ITEM = {"id": 1, "item_name": "Суп", "unit_price": "900.00", "quantity": 1, "line_total": "900.00"}
BAD_STATION_ITEMS = {
    "нет quantity": [{"code": 1001, "item_name": "Суп"}],
    "нет item_name": [{"code": 1001, "quantity": 2}],
    "позиция не объект": ["Суп"],
    "items не список": {"item_name": "Суп", "quantity": 1},
    "quantity строкой": [{"item_name": "Суп", "quantity": "2"}],
}
BAD_ORDER_ITEMS = {
    "нет quantity": [{k: v for k, v in GOOD_ITEM.items() if k != "quantity"}],
    "нет item_name": [{k: v for k, v in GOOD_ITEM.items() if k != "item_name"}],
    "позиция не объект": ["Суп"],
}


class DatabaseShapeTest(unittest.TestCase):
    def test_station_items_bad_shape(self):
        for name, items in BAD_STATION_ITEMS.items():
            with self.subTest(name):
                db = make_db()
                db.client.rpc.return_value.execute.return_value.data = station_answer(items)
                with self.assertRaises(dbm.AppError) as ctx:
                    db.load_station_orders("kitchen")
                self.assertIn(UNEXPECTED, str(ctx.exception))

    def test_station_items_good_shape(self):
        """Регрессия: код позиции необязателен, пустой список и null допустимы."""
        for items, expected in (([{"code": 1001, "item_name": "Суп", "quantity": 2}],
                                 [{"code": 1001, "item_name": "Суп", "quantity": 2}]),
                                ([{"item_name": "Суп", "quantity": 1}], [{"item_name": "Суп", "quantity": 1}]),
                                ([], []), (None, [])):
            with self.subTest(items=items):
                db = make_db()
                db.client.rpc.return_value.execute.return_value.data = station_answer(items)
                self.assertEqual(db.load_station_orders("kitchen")[0]["items"], expected)

    def orders_db(self, items):
        db = make_db()
        query = db.client.table.return_value
        query.select.return_value = query.order.return_value = query.limit.return_value = query
        query.execute.return_value.data = orders_answer(items)
        return db

    def test_order_items_bad_shape(self):
        for name, items in BAD_ORDER_ITEMS.items():
            with self.subTest(name):
                with self.assertRaises(dbm.AppError) as ctx:
                    self.orders_db(items).load_orders()
                self.assertIn(UNEXPECTED, str(ctx.exception))

    def test_order_items_good_shape(self):
        orders = self.orders_db([GOOD_ITEM]).load_orders()
        self.assertEqual(orders[0]["items"][0]["item_name"], "Суп")
        self.assertEqual(orders[0]["items"][0]["quantity"], 1)


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class UiShapeTest(unittest.TestCase):
    """Неверная позиция не роняет отрисовку: экран показывает ошибку и остаётся рабочим."""

    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        self.shown = []
        self.callback_errors = []
        patches = [mock.patch.object(ui_kitchen.threading, "Thread", SyncThread),
                   mock.patch.object(ui_orders.threading, "Thread", SyncThread),
                   mock.patch.object(ui_kitchen, "play_alert")]
        for module in (ui_kitchen, ui_orders):
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

    def test_kitchen_bad_item(self):
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = station_answer([{"item_name": "Суп"}])
        frame = ui_kitchen.KitchenFrame(self.root, db, {"full_name": "Повар", "role": "kitchen"},
                                        on_logout=mock.Mock())
        self.addCleanup(lambda: frame.closed or frame.destroy())
        self.pump()
        self.assertEqual(self.callback_errors, [])
        self.assertIn(UNEXPECTED, frame.status["text"])
        self.assertFalse(frame.busy)
        self.assertEqual(str(frame.reload_btn["state"]), "normal")

    def test_orders_window_bad_item(self):
        db = make_db()
        query = db.client.table.return_value
        query.select.return_value = query.order.return_value = query.limit.return_value = query
        query.execute.return_value.data = orders_answer([{k: v for k, v in GOOD_ITEM.items() if k != "item_name"}])
        screen = tkinter.Frame(self.root)
        self.addCleanup(lambda: screen.winfo_exists() and screen.destroy())
        window = ui_orders.OrdersWindow(screen, db, role="cashier", on_auth_error=mock.Mock())
        self.pump()
        self.assertEqual(self.callback_errors, [])
        self.assertIn(UNEXPECTED, window.status["text"])
        self.assertFalse(window.busy)


# ---------- Экран входа: ответ после закрытия ----------

class HeldThread:
    started = []

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        HeldThread.started.append(lambda: self.target(*self.args))


class FakeLoginDB:
    def __init__(self, fail=None):
        self.fail = fail

    def sign_in(self, code, password):
        if self.fail:
            raise self.fail
        return {"id": "u", "full_name": "Кассир", "role": "cashier"}


class LoginLateAnswerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        HeldThread.started.clear()
        self.callback_errors = []
        patches = [mock.patch.object(ui_login.threading, "Thread", HeldThread),
                   mock.patch.object(self.root, "report_callback_exception",
                                     lambda *exc: self.callback_errors.append(exc[1]))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def pump(self):
        for _ in range(5):
            self.root.update()

    def assert_no_dangling_after(self):
        tk = self.root.tk
        for after_id in tk.splitlist(tk.call("after", "info")):
            script = tk.splitlist(tk.call("after", "info", after_id))[0]
            command = tk.splitlist(script)[0] if script else ""
            if command:
                self.assertTrue(tk.call("info", "commands", command),
                                f"отложенный вызов {after_id} ссылается на удалённую команду {command}")

    def login_frame(self, db):
        on_success = mock.Mock()
        frame = ui_login.LoginFrame(self.root, db, on_success=on_success)
        self.addCleanup(lambda: frame.winfo_exists() and frame.destroy())
        frame.code.insert(0, "1002")
        frame.password.insert(0, "pw")
        frame.submit()
        self.assertEqual(len(HeldThread.started), 1)
        return frame, on_success

    def test_answer_after_frame_closed(self):
        for fail in (None, dbm.AppError(dbm.BAD_LOGIN), RuntimeError("boom")):
            with self.subTest(fail=fail):
                HeldThread.started.clear()
                frame, on_success = self.login_frame(FakeLoginDB(fail))
                frame.destroy()                 # экран входа закрыт, пока идёт вход
                HeldThread.started.pop()()
                self.assert_no_dangling_after()
                self.pump()
                self.assertEqual(self.callback_errors, [])
                on_success.assert_not_called()  # закрытый экран не открывает экран роли

    def test_answer_queued_before_frame_closed(self):
        frame, on_success = self.login_frame(FakeLoginDB())
        HeldThread.started.pop()()     # ответ уже в очереди Tk...
        frame.destroy()                # ...а экран закрыт раньше, чем Tk его обработал
        self.assert_no_dangling_after()
        self.pump()
        self.assertEqual(self.callback_errors, [])
        on_success.assert_not_called()

    def test_answer_after_application_closed(self):
        """Приложение закрыто: передача ответа в Tk из потока падает — поток завершается тихо."""
        _frame, on_success = self.login_frame(FakeLoginDB())
        closed = RuntimeError("main thread is not in main loop")
        with mock.patch.object(tkinter.Misc, "after", side_effect=closed):
            HeldThread.started.pop()()  # не должно выбрасывать исключение из фонового потока
        on_success.assert_not_called()

    def test_answer_applied_while_open(self):
        """Регрессия: пока экран открыт, успешный вход открывает экран роли, ошибка — показывается."""
        frame, on_success = self.login_frame(FakeLoginDB())
        HeldThread.started.pop()()
        self.pump()
        on_success.assert_called_once_with({"id": "u", "full_name": "Кассир", "role": "cashier"})

        HeldThread.started.clear()
        frame, _ = self.login_frame(FakeLoginDB(dbm.AppError(dbm.BAD_LOGIN)))
        HeldThread.started.pop()()
        self.pump()
        self.assertEqual(frame.status["text"], dbm.BAD_LOGIN)
        self.assertEqual(str(frame.button["state"]), "normal")
        self.assertEqual(self.callback_errors, [])


if __name__ == "__main__":
    unittest.main()
