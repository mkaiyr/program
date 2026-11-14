"""Приложение и миграция 09: меню станций, предложения позиций, решения администратора.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Без подключения к Supabase: у Database подменён клиент (MagicMock), ошибки сервера —
настоящие postgrest.exceptions.APIError. Окно администратора — с заглушкой базы.
Сами функции 09 на PostgreSQL проверяет sql/tests/09_station_menu_checks.sql.
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import ui_admin  # noqa: E402
from _tk import get_root  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

NO_FUNCTION = APIError({"message": "Could not find the function", "code": "PGRST202", "details": "", "hint": ""})


def make_db():
    db = dbm.Database.__new__(dbm.Database)
    db.client = mock.MagicMock()
    return db


def menu_row(**fields):
    row = {"id": 1, "code": 1001, "name": "Суп", "category": "Супы", "price": 900, "is_available": True,
           "status": "on_sale", "station": "kitchen", "reject_reason": None}
    row.update(fields)
    return row


class DatabaseStationMenuTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.rpc = self.db.client.rpc

    def answer(self, data):
        self.rpc.return_value.execute.side_effect = None
        self.rpc.return_value.execute.return_value.data = data

    def test_admin_menu_from_station_menu(self):
        self.answer([menu_row(), menu_row(id=2, code=1002, name="Лимонад", is_available=False, status="pending")])
        rows = self.db.load_all_menu()
        self.rpc.assert_called_with("station_menu", {})
        self.assertEqual([r["status"] for r in rows], ["on_sale", "pending"])
        self.assertEqual(rows[0]["price"], Decimal("900.00"))

    def test_bad_menu_shape(self):
        for name, data in (("не список", {"id": 1}), ("неизвестный статус", [menu_row(status="archived")]),
                           ("нет названия", [{k: v for k, v in menu_row().items() if k != "name"}]),
                           ("наличие строкой", [menu_row(is_available="true")])):
            with self.subTest(name):
                self.answer(data)
                with self.assertRaises(dbm.AppError):
                    self.db.load_all_menu()

    def test_station_menu_after_09(self):
        self.answer([menu_row(status="pending", is_available=False)])
        result = self.db.load_station_menu("bar")
        self.rpc.assert_called_once_with("station_menu", {})  # станцию сервер берёт из роли
        self.assertEqual(result["can_propose"], True)
        self.assertEqual(result["items"][0]["status"], "pending")

    def test_kitchen_before_09_uses_kitchen_menu(self):
        legacy = [{"id": 1, "code": 1001, "name": "Суп", "category": "Супы", "is_available": False}]
        self.rpc.return_value.execute.side_effect = [NO_FUNCTION, mock.Mock(data=legacy)]
        result = self.db.load_station_menu("kitchen")
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list], ["station_menu", "kitchen_menu"])
        self.assertEqual(result, {"items": legacy, "can_propose": False, "available": True})
        self.assertEqual(dbm.menu_status(result["items"][0]), "out_of_stock")

    def test_bar_before_09_has_no_menu(self):
        self.rpc.return_value.execute.side_effect = [NO_FUNCTION]
        result = self.db.load_station_menu("bar")
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list], ["station_menu"])  # не kitchen_menu
        self.assertEqual(result, {"items": [], "can_propose": False, "available": False})

    def test_station_set_available_rpc_by_station(self):
        self.answer({"id": 1, "name": "Суп", "is_available": False})
        self.db.station_set_available("kitchen", 1, False)
        self.rpc.assert_called_with("kitchen_set_menu_item_available", {"p_id": 1, "p_available": False})
        self.db.station_set_available("bar", 1, False)
        self.rpc.assert_called_with("station_set_menu_item_available", {"p_id": 1, "p_available": False})
        with self.assertRaises(dbm.AppError):      # сервер ответил другим наличием
            self.db.station_set_available("bar", 1, True)

    def test_propose_menu_item(self):
        self.answer(menu_row(status="pending", is_available=False))
        self.db.propose_menu_item(" Борщ ", "Супы", "1 200,50", " 2500 ")
        self.rpc.assert_called_with("station_add_menu_item", {"p_name": "Борщ", "p_category": "Супы",
                                                              "p_price": "1200.50", "p_code": 2500})
        self.rpc.reset_mock()
        for args in (("", "Супы", "100"), ("Борщ", "Супы", "-1"), ("Борщ", "Супы", "100", "0")):
            with self.subTest(args=args), self.assertRaises(dbm.AppError):
                self.db.propose_menu_item(*args)
        self.rpc.assert_not_called()               # ошибки ввода — без запроса к серверу

    def test_approve_and_reject(self):
        self.answer(menu_row())
        self.db.approve_menu_item(5, False)
        self.rpc.assert_called_with("admin_approve_menu_item", {"p_id": 5, "p_available": False})
        self.answer(menu_row(status="rejected", is_available=False))
        self.db.reject_menu_item(5, "  Нет в концепции  ")
        self.rpc.assert_called_with("admin_reject_menu_item", {"p_id": 5, "p_reason": "Нет в концепции"})
        self.db.reject_menu_item(5, "   ")
        self.rpc.assert_called_with("admin_reject_menu_item", {"p_id": 5})  # пустая причина не передаётся
        self.rpc.reset_mock()
        with self.assertRaises(dbm.AppError):
            self.db.reject_menu_item(5, "x" * (dbm.MAX_REJECT_REASON + 1))
        self.rpc.assert_not_called()

    def test_decision_errors_have_texts(self):
        self.rpc.return_value.execute.side_effect = APIError(
            {"message": "ALREADY_APPROVED", "code": "P0001", "details": "", "hint": ""})
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.approve_menu_item(5, True)
        self.assertEqual(ctx.exception.code, "invalid")
        self.assertIn("уже принято", str(ctx.exception))


class FakeAdminDB:
    def __init__(self):
        self.rows = {
            1: dict(id=1, code=1001, name="Капучино", category="Напитки", price=Decimal("1100.00"),
                    is_available=True, station="bar", status="on_sale"),
            2: dict(id=2, code=1002, name="Борщ", category="Супы", price=Decimal("1200.00"),
                    is_available=False, station="kitchen", status="pending"),
            3: dict(id=3, code=2500, name="Пельмени", category="Горячее", price=Decimal("1800.00"),
                    is_available=False, station="kitchen", status="rejected", reject_reason="Нет в концепции"),
        }
        self.calls = []

    def load_all_menu(self):
        return [dict(r) for r in self.rows.values()]

    def approve_menu_item(self, item_id, available):
        self.calls.append(("approve", item_id, available))
        self.rows[item_id].update(is_available=available, status="on_sale" if available else "out_of_stock")
        return dict(self.rows[item_id])

    def reject_menu_item(self, item_id, reason=""):
        self.calls.append(("reject", item_id, reason))
        self.rows[item_id].update(status="rejected", reject_reason=reason.strip() or None)
        return dict(self.rows[item_id])

    def set_menu_item_available(self, item_id, available):
        self.calls.append(("available", item_id, available))
        return dict(self.rows[item_id])


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class AdminDecisionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        patches = [mock.patch.object(ui_admin.threading, "Thread", SyncThread)]
        for name in ("showinfo", "showwarning", "showerror"):
            patches.append(mock.patch.object(ui_admin.messagebox, name))
        self.ask = mock.patch.object(ui_admin.messagebox, "askyesnocancel").start()
        self.reason = mock.patch.object(ui_admin.simpledialog, "askstring").start()
        self.addCleanup(mock.patch.stopall)
        for p in patches:
            p.start()
        self.db = FakeAdminDB()
        self.win = ui_admin.MenuAdminWindow(self.root, self.db, on_close=lambda: None, on_auth_error=lambda: None)
        self.addCleanup(lambda: self.win.winfo_exists() and self.win.destroy())
        self.pump()

    def pump(self):
        for _ in range(5):
            self.root.update()

    def select(self, item_id):
        self.win.tree.selection_set(str(item_id))
        self.pump()

    def states(self):
        return {name: str(getattr(self.win, f"{name}_btn")["state"]) for name in ("toggle", "approve", "reject", "save")}

    def test_status_column_and_pending_hint(self):
        self.assertEqual(self.win.tree.item("2")["values"][5], "Ждёт решения")
        self.assertEqual(self.win.tree.item("3")["values"][5], "Отклонена")
        self.assertIn("Ждут решения: 1", self.win.status["text"])
        self.win.pending_only.set(True)
        self.win._render_tree()
        self.assertEqual(self.win.tree.get_children(), ("2",))

    def test_buttons_by_state(self):
        self.select(1)
        self.assertEqual(self.states(), {"toggle": "normal", "approve": "disabled", "reject": "disabled",
                                         "save": "normal"})
        self.select(2)
        self.assertEqual(self.states(), {"toggle": "disabled", "approve": "normal", "reject": "normal",
                                         "save": "normal"})
        self.select(3)
        self.assertEqual(self.states(), {"toggle": "disabled", "approve": "disabled", "reject": "disabled",
                                         "save": "disabled"})  # отклонённую изменить нельзя
        self.assertIn("изменить нельзя", str(self.win.form["text"]))
        self.win.toggle_available()                 # прямой вызов тоже ничего не отправляет
        self.assertEqual(self.db.calls, [])

    def test_approve_choices(self):
        self.select(2)
        self.ask.return_value = None                # «Отмена»
        self.win.approve()
        self.assertEqual(self.db.calls, [])
        self.ask.return_value = False               # подтвердить, но пока не продавать
        self.win.approve()
        self.pump()
        self.assertEqual(self.db.calls, [("approve", 2, False)])
        self.assertEqual(self.win.tree.item("2")["values"][5], "Нет в наличии")

    def test_reject_with_reason(self):
        self.select(2)
        self.reason.return_value = None             # «Отмена»
        self.win.reject()
        self.assertEqual(self.db.calls, [])
        self.reason.return_value = "x" * (dbm.MAX_REJECT_REASON + 1)
        self.win.reject()
        self.assertEqual(self.db.calls, [])         # длинная причина — без запроса
        self.reason.return_value = " Нет в концепции "
        self.win.reject()
        self.pump()
        self.assertEqual(self.db.calls, [("reject", 2, " Нет в концепции ")])
        self.assertEqual(self.win.tree.item("2")["values"][5], "Отклонена")

    def test_decisions_only_for_pending(self):
        self.select(1)
        self.win.approve()
        self.win.reject()
        self.assertEqual(self.db.calls, [])
        self.ask.assert_not_called()
        self.reason.assert_not_called()


if __name__ == "__main__":
    unittest.main()
