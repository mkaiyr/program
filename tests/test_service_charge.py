"""Надбавка 10%: расчёт (Decimal), корзина кассы, передача итога серверу, текст миграции 11.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v
Без подключения к Supabase. Серверную функцию place_order на PostgreSQL проверяет
sql/tests/11_service_charge_void_checks.sql.
"""

import re
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import money  # noqa: E402
import ui_order  # noqa: E402
from _tk import get_root  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

MIGRATION = (ROOT / "sql" / "11_service_charge_void.sql").read_text(encoding="utf-8")
D = Decimal


class MoneyTest(unittest.TestCase):
    def test_required_examples(self):
        # (подытог, надбавка, итого) — числа из задания, записаны вручную, а не вычислены функцией
        cases = {
            "1000": ("100.00", "1100.00"),
            "2500": ("250.00", "2750.00"),
            "4000": ("400.00", "4400.00"),
            "9999": ("1000.00", "10999.00"),   # 999.90 -> 1000 тенге
        }
        for subtotal, (service, total) in cases.items():
            with self.subTest(subtotal=subtotal):
                self.assertEqual(money.order_totals(D(subtotal)), (D(subtotal), D(service), D(total)))

    def test_rounding_half_up_to_whole_tenge(self):
        cases = {"5": "1.00", "4": "0.00", "1005": "101.00", "1004": "100.00", "700.50": "70.00",
                 "2900.50": "290.00", "0.01": "0.00", "15": "2.00", "25": "3.00"}  # 1.5 -> 2, 2.5 -> 3 (не «банковское»)
        for subtotal, service in cases.items():
            with self.subTest(subtotal=subtotal):
                self.assertEqual(money.service_amount(D(subtotal)), D(service))

    def test_total_is_always_subtotal_plus_service_and_exact(self):
        for kopecks in range(1, 5000, 37):
            subtotal = D(kopecks) / 100
            sub, service, total = money.order_totals(subtotal)
            self.assertEqual(total, sub + service)
            self.assertEqual(service, service.to_integral_value())   # целые тенге
            self.assertIsInstance(total, Decimal)

    def test_example_from_task(self):
        sub, service, total = money.order_totals(D(3000) + D(1000))
        self.assertEqual((sub, service, total), (D("4000.00"), D("400.00"), D("4400.00")))
        self.assertEqual(money.fmt_money(total), "4 400 ₸")
        self.assertEqual(money.fmt_money(D("2900.5")), "2 900.50 ₸")


MENU = [{"id": 1, "code": 1, "name": "Бургер", "category": "Еда", "price": D("3000.00")},
        {"id": 2, "code": 2, "name": "Кола", "category": "Напитки", "price": D("1000.00")}]


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class FakeDB:
    def __init__(self):
        self.calls = []

    def load_categories(self, lenient=False):  # справочник категорий (миграция 16)
        return []

    def load_menu(self):
        return [dict(i) for i in MENU]

    def create_order(self, request_id, items, expected_total=None):
        self.calls.append((items, expected_total))
        sub, service, total = money.order_totals(sum(p * q for _i, q, p in items))
        return {"order_id": 1, "subtotal": sub, "service_amount": service, "total": total, "duplicate": False}


class CartChargeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        for patcher in (mock.patch.object(ui_order.threading, "Thread", SyncThread),
                        mock.patch.object(ui_order.messagebox, "showinfo"),
                        mock.patch.object(ui_order.messagebox, "askyesno", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.db = FakeDB()
        self.frame = ui_order.OrderFrame(self.root, self.db, {"full_name": "К", "role": "cashier"}, on_logout=mock.Mock())
        self.frame.pack()
        self.root.update()
        self.addCleanup(self.frame.destroy)

    def test_burger_and_cola(self):
        f = self.frame
        f.add_item(1)
        f.add_item(2)
        self.assertEqual((f.subtotal_label["text"], f.service_label["text"], f.total_label["text"]),
                         ("4 000 ₸", "400 ₸", "4 400 ₸"))
        self.assertIn("10%", f.service_label.master.winfo_children()[0]["text"])

    def test_recalculates_on_quantity_and_removal(self):
        f = self.frame
        f.add_item(1)
        f.add_item(2)
        f.add_item(2)                          # 3000 + 2×1000 = 5000 -> 5500
        self.assertEqual(f.total_label["text"], "5 500 ₸")
        f.cart_tree.selection_set("2")
        f.change_qty(-1)                       # 4000 -> 4400
        self.assertEqual(f.total_label["text"], "4 400 ₸")
        f.remove_item()                        # 3000 -> 3300
        self.assertEqual(f.total_label["text"], "3 300 ₸")
        f.cart_tree.selection_set("1")
        f.remove_item()
        self.assertEqual((f.subtotal_label["text"], f.service_label["text"], f.total_label["text"]),
                         ("0 ₸", "0 ₸", "0 ₸"))

    def test_expected_total_is_sent_to_server(self):
        f = self.frame
        f.add_item(1)
        f.add_item(2)
        f.submit()
        self.root.update()                     # результат фонового запроса приходит через after
        (items, expected), = self.db.calls
        self.assertEqual(expected, D("4400.00"))
        # цены строк — без надбавки: на кухню и в бар уходят позиции, а не итог
        self.assertEqual(sorted((i, q, p) for i, q, p in items), [(1, 1, D("3000.00")), (2, 1, D("1000.00"))])
        self.assertIn("4 400 ₸", f.status["text"])
        self.assertIn("400 ₸", f.status["text"])


class DatabaseCreateOrderTest(unittest.TestCase):
    def setUp(self):
        self.db = dbm.Database.__new__(dbm.Database)
        self.db.client = mock.MagicMock()
        self.rpc = self.db.client.rpc

    def test_calls_place_order_with_expected_total(self):
        self.rpc.return_value.execute.return_value.data = {
            "order_id": 9, "subtotal": 4000, "service_percent": 10, "service_amount": 400, "total": 4400,
            "created_at": "2026-11-11T10:00:00+00:00", "duplicate": False}
        result = self.db.create_order("req-1", [(1, 1, D("3000.00")), (2, 1, D("1000.00"))], D("4400.00"))
        name, params = self.rpc.call_args.args
        self.assertEqual(name, "place_order")
        self.assertEqual(params["p_expected_total"], "4400.00")
        self.assertEqual(params["p_items"][0], {"menu_item_id": 1, "quantity": 1, "expected_price": "3000.00"})
        self.assertEqual((result["subtotal"], result["service_amount"], result["total"]),
                         (D("4000.00"), D("400.00"), D("4400.00")))

    def test_old_server_answer_is_rejected(self):
        self.rpc.return_value.execute.return_value.data = {"order_id": 9, "total": 4000}
        with self.assertRaises(dbm.AppError):
            self.db.create_order("req-1", [(1, 1, D("3000.00"))], D("3300.00"))

    def test_missing_function_explains_migration(self):
        self.rpc.return_value.execute.side_effect = APIError({"message": "x", "code": "PGRST202", "details": "", "hint": ""})
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.create_order("req-1", [(1, 1, D("3000.00"))], D("3300.00"))
        self.assertIn("миграция 11", str(ctx.exception))

    def test_total_mismatch_text(self):
        self.rpc.return_value.execute.side_effect = APIError(
            {"message": "TOTAL_MISMATCH", "code": "P0001", "details": "4500.00", "hint": ""})
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.create_order("req-1", [(1, 1, D("3000.00"))], D("3300.00"))
        self.assertIn("4500.00", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "invalid")


class MigrationTextTest(unittest.TestCase):
    def test_fixed_rate_and_rounding_on_server(self):
        self.assertRegex(MIGRATION, r"c_percent\s+constant numeric := 10;")
        self.assertIn("round(v_subtotal * c_percent / 100, 0)", MIGRATION)
        self.assertIn("v_total := v_subtotal + v_service;", MIGRATION)
        self.assertIn("TOTAL_MISMATCH", MIGRATION)

    def test_items_keep_price_without_charge(self):
        insert = re.search(r"insert into public\.order_items.*?;", MIGRATION, re.S).group(0)
        self.assertIn("m.price", insert)
        self.assertNotIn("service", insert)

    def test_stations_do_not_see_charge_or_prices(self):
        func = re.search(r"create or replace function public\._station_orders.*?\$\$;", MIGRATION, re.S).group(0)
        for word in ("service", "subtotal", "total", "price"):
            self.assertNotIn(word, func)
        self.assertIn("voided_at is null", func)

    def test_totals_constraint_and_backfill(self):
        self.assertIn("total = subtotal + service_amount", MIGRATION)
        self.assertIn("update public.orders set subtotal = total where subtotal is null;", MIGRATION)


if __name__ == "__main__":
    unittest.main()
