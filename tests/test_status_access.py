"""Тесты статусов заказа, ролей и экрана кухни (без подключения к Supabase).

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Здесь проверяется клиентская логика на mock-объектах и согласованность
с текстом миграции. Права на стороне базы проверяет sql/tests/05_kitchen_checks.sql.
"""

import re
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

import db as dbm  # noqa: E402
import ui_kitchen  # noqa: E402
import ui_order  # noqa: E402
import ui_orders  # noqa: E402
import ui_theme  # noqa: E402
from _tk import get_root  # noqa: E402

MIGRATION = (ROOT / "sql" / "05_kitchen_status.sql").read_text(encoding="utf-8")


def api_error(message, code="P0001"):
    return APIError({"message": message, "code": code, "details": "", "hint": ""})


def make_db():
    """Database с mock-клиентом Supabase."""
    db = dbm.Database.__new__(dbm.Database)
    db.profile = None
    db.client = mock.MagicMock()
    return db


class SyncThread:
    """Замена threading.Thread: запрос выполняется сразу, без mainloop."""

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class TransitionsTest(unittest.TestCase):
    def test_sequence(self):
        self.assertEqual(list(dbm.ORDER_STATUSES), ["accepted", "preparing", "ready", "completed"])
        self.assertEqual(dbm.ORDER_STATUSES["preparing"], "Готовится")
        self.assertEqual(dbm.NEXT_STATUS, {"accepted": "preparing", "preparing": "ready", "ready": "completed"})

    def test_roles(self):
        self.assertEqual(dbm.ROLE_TRANSITIONS["kitchen"], {"accepted": "preparing", "preparing": "ready"})
        self.assertEqual(dbm.ROLE_TRANSITIONS["cashier"], {"ready": "completed"})
        # «Готовится»/«Готов» вычисляются из станций (08); админ выдаёт, а готовность — только явно
        self.assertEqual(dbm.ROLE_TRANSITIONS["admin"], {"ready": "completed"})
        for role, transitions in dbm.ROLE_TRANSITIONS.items():
            for old, new in transitions.items():
                self.assertEqual(dbm.NEXT_STATUS[old], new, f"{role}: {old}->{new} нет в последовательности")

    def test_migration_matches_client(self):
        """Статусы и переходы в Python совпадают с ограничением и set_order_status в SQL."""
        check = re.search(r"orders_status_check\s+check \(status in \(([^)]*)\)\)", MIGRATION)
        self.assertIsNotNone(check)
        self.assertEqual(re.findall(r"'(\w+)'", check.group(1)), list(dbm.ORDER_STATUSES))
        sql_pairs = dict(re.findall(r"v_current = '(\w+)'\s+and p_status = '(\w+)'", MIGRATION))
        self.assertEqual(sql_pairs, dbm.NEXT_STATUS)
        self.assertIn("p.role in ('cashier', 'admin', 'kitchen')", MIGRATION)

    def test_migration_keeps_kitchen_out_of_staff(self):
        """Кухня не должна попасть в is_active_staff (иначе сможет оформлять продажи)."""
        self.assertNotIn("function public.is_active_staff", MIGRATION)
        self.assertNotRegex(MIGRATION, r"(?i)grant\s+(insert|update|delete|all)\s+on\s+(table\s+)?public\.")
        self.assertNotRegex(MIGRATION, r"(?i)create policy")


class DatabaseTest(unittest.TestCase):
    def test_load_kitchen_orders(self):
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = [
            {"id": 7, "created_at": "2026-10-23T09:05:00+00:00", "status": "accepted",
             "items": [{"item_name": "Капучино", "quantity": 2}]},
            {"id": 8, "created_at": "2026-10-23T09:06:00+00:00", "status": "preparing", "items": None},
        ]
        orders = db.load_kitchen_orders()
        db.client.rpc.assert_called_with("kitchen_orders", {})
        self.assertEqual(orders[0]["items"], [{"item_name": "Капучино", "quantity": 2}])
        self.assertEqual(orders[1]["items"], [])
        self.assertRegex(orders[0]["time"], r"^\d\d:\d\d$")

    def test_load_kitchen_orders_errors(self):
        db = make_db()
        cases = [(api_error("x", "PGRST202"), None, "не настроен"),
                 (api_error("NOT_ALLOWED", "42501"), "auth", None),
                 (httpx.ConnectError("x"), "network", None)]
        for error, code, text in cases:
            db.client.rpc.return_value.execute.side_effect = error
            with self.assertRaises(dbm.AppError) as ctx:
                db.load_kitchen_orders()
            if code:
                self.assertEqual(ctx.exception.code, code)
            if text:
                self.assertIn(text, str(ctx.exception))

    def test_set_order_status_errors(self):
        db = make_db()
        for message, code in [("TRANSITION_NOT_ALLOWED", "forbidden"), ("INVALID_TRANSITION", "order_changed"),
                              ("ORDER_NOT_FOUND", "order_changed"), ("NOT_ALLOWED", "auth")]:
            db.client.rpc.return_value.execute.side_effect = api_error(message)
            with self.assertRaises(dbm.AppError) as ctx:
                db.set_order_status(1, "preparing")
            self.assertEqual(ctx.exception.code, code, message)

    def test_set_order_status_rejects_wrong_answer(self):
        """Успех не показывается, если сервер вернул не тот статус."""
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = {"order_id": 1, "status": "accepted"}
        with self.assertRaises(dbm.AppError):
            db.set_order_status(1, "preparing")

    def _sign_in(self, role, active=True):
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = "2001@bistro.local"
        db.client.auth.sign_in_with_password.return_value.user.id = "k"
        query = db.client.table.return_value
        query.select.return_value = query.eq.return_value = query.limit.return_value = query
        query.execute.return_value.data = [{"id": "k", "full_name": "Повар", "role": role,
                                            "is_active": active, "employee_code": "2001"}]
        return db, db.sign_in("2001", "secret") if role in dbm.ALLOWED_ROLES and active else None

    def test_kitchen_can_sign_in(self):
        _db, profile = self._sign_in("kitchen")
        self.assertEqual(profile["role"], "kitchen")

    def test_unknown_role_and_inactive_rejected(self):
        for role, active in (("manager", True), ("kitchen", False)):
            db, _ = self._sign_in("cashier")  # заготовка mock-клиента
            db.client.table.return_value.execute.return_value.data[0].update(role=role, is_active=active)
            with self.assertRaises(dbm.AppError):
                db.sign_in("2001", "secret")
            db.client.auth.sign_out.assert_called()


class FakeKitchenDB:
    def __init__(self):
        self.orders = {1: "accepted", 2: "preparing"}
        self.calls = []
        self.stations = []  # какой станции касались вызовы
        self.fail = None

    def load_station_orders(self, station):
        self.stations.append(station)
        return [{"id": oid, "time": "09:0" + str(oid), "status": st,
                 "items": [{"item_name": "Суп", "quantity": oid}]}
                for oid, st in self.orders.items() if st in ("accepted", "preparing")]

    def set_station_status(self, order_id, station, status):
        self.calls.append((order_id, status))
        self.stations.append(station)
        if self.fail:
            error, self.fail = self.fail, None
            raise error
        self.orders[order_id] = status
        return {"order_id": order_id, "status": status}


class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        self.shown = []
        patches = [mock.patch.object(m.threading, "Thread", SyncThread) for m in (ui_kitchen, ui_orders, ui_order)]
        for name in ("showinfo", "showwarning", "showerror"):
            patches.append(mock.patch.object(ui_kitchen.messagebox, name,
                                             lambda *a, _n=name, **k: self.shown.append((_n, a[0]))))
        patches.append(mock.patch.object(ui_orders.messagebox, "askyesno", lambda *a, **k: True))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def pump(self):
        for _ in range(5):
            self.root.update()

    def kitchen(self, db):
        frame = ui_kitchen.KitchenFrame(self.root, db, {"full_name": "Повар", "role": "kitchen"},
                                        on_logout=mock.Mock())
        self.addCleanup(frame.destroy)
        self.pump()
        return frame

    def test_kitchen_start_cooking(self):
        db = FakeKitchenDB()
        frame = self.kitchen(db)
        self.assertEqual(sorted(frame.badges), [1, 2])
        self.assertEqual(frame.badges[1]["text"], "Принят")
        self.assertEqual(frame.action_buttons[1]["text"], "Начать готовить")
        self.assertEqual(frame.action_buttons[2]["text"], "Готово")
        frame.action_buttons[1].invoke()
        self.pump()
        self.assertEqual(db.calls, [(1, "preparing")])
        self.assertEqual(frame.badges[1]["text"], "Готовится")
        self.assertIn("Готовится", frame.status["text"])
        self.assertEqual(frame.action_buttons[1]["text"], "Готово")

    def test_kitchen_ready_leaves_list(self):
        db = FakeKitchenDB()
        frame = self.kitchen(db)
        frame.advance_status(2)
        self.pump()
        self.assertEqual(db.calls, [(2, "ready")])
        self.assertEqual(list(frame.badges), [1])

    def test_kitchen_no_success_on_error(self):
        db = FakeKitchenDB()
        frame = self.kitchen(db)
        for error, title in [(dbm.AppError("Статус заказа уже изменён.", "order_changed"), "showwarning"),
                             (dbm.AppError(dbm.NO_NETWORK, "network"), "showerror")]:
            db.fail = error
            frame.advance_status(1)
            self.pump()
            self.assertEqual(self.shown[-1][0], title)
            self.assertNotIn("статус «", frame.status["text"])
            self.assertEqual(db.orders[1], "accepted")
            self.assertFalse(frame.busy)
            self.assertEqual(str(frame.action_buttons[1]["state"]), "normal")

    def test_kitchen_empty_list(self):
        db = FakeKitchenDB()
        db.orders = {}
        frame = self.kitchen(db)
        self.assertEqual(frame.status["text"], "Активных заказов нет.")
        self.assertEqual(frame.action_buttons, {})

    def test_orders_window_buttons_by_role(self):
        orders = [{"id": oid, "created_at": "", "cashier_name": "К", "total": dbm.Decimal("1.00"),
                   "status": st, "items": []}
                  for oid, st in ((1, "accepted"), (2, "preparing"), (3, "ready"), (4, "completed"))]
        db = mock.Mock(load_orders=mock.Mock(return_value=orders))
        expected = {
            "cashier": {"1": None, "2": None, "3": "Выдан", "4": None},
            "admin": {"1": None, "2": None, "3": "Выдан", "4": None},
        }
        for role, by_order in expected.items():
            window = ui_orders.OrdersWindow(self.root, db, role=role, on_auth_error=mock.Mock())
            self.pump()
            for oid, label in by_order.items():
                window.tree.selection_set(oid)
                self.pump()
                state = str(window.next_btn["state"])
                if label is None:
                    self.assertEqual(state, "disabled", f"{role} #{oid}")
                else:
                    self.assertEqual(state, "normal", f"{role} #{oid}")
                    self.assertIn(label, window.next_btn["text"])
            window.destroy()

    def test_app_routes_by_role(self):
        """Кухня попадает на экран кухни, кассир и администратор — в кассу.

        Второй tk.Tk() в одном процессе на macOS вешает тесты, поэтому App
        не создаётся: show_main вызывается на заглушке, экраны подменены.
        """
        import main
        for role, screen in (("kitchen", "KitchenFrame"), ("cashier", "OrderFrame"), ("admin", "OrderFrame")):
            app = mock.Mock()
            with mock.patch.object(main, "KitchenFrame") as kitchen, mock.patch.object(main, "OrderFrame") as order:
                main.App.show_main(app, {"full_name": "X", "role": role})
            called = kitchen if screen == "KitchenFrame" else order
            other = order if screen == "KitchenFrame" else kitchen
            called.assert_called_once()
            other.assert_not_called()
            app._show.assert_called_once_with(called.return_value)


if __name__ == "__main__":
    unittest.main()
