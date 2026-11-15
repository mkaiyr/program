"""Архив блюд, слой db.py (миграция 15): методы, тексты ошибок, работа до миграции 15.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v
Без подключения к Supabase: у Database подменён клиент. Серверные функции на PostgreSQL проверяет
sql/tests/15_menu_item_archive_checks.sql.
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

import db as dbm  # noqa: E402

D = Decimal


def api_error(message, code="P0001", details=""):
    return APIError({"message": message, "code": code, "details": details, "hint": ""})


def make_db():
    db = dbm.Database.__new__(dbm.Database)
    db.client = mock.MagicMock()
    return db


class ArchiveMethodsTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.rpc = self.db.client.rpc

    def answer(self, data):
        self.rpc.return_value.execute.side_effect = None
        self.rpc.return_value.execute.return_value.data = data

    def fail(self, error):
        self.rpc.return_value.execute.side_effect = error

    def test_rpc_names_and_params(self):
        self.answer({"id": 7, "name": "Суп", "archived": True, "changed": True})
        self.assertTrue(self.db.archive_menu_item(7)["changed"])
        self.rpc.assert_called_with("admin_archive_menu_item", {"p_id": 7})
        self.answer({"id": 7, "name": "Суп", "archived": False, "changed": True})
        self.assertFalse(self.db.restore_menu_item(7)["archived"])
        self.rpc.assert_called_with("admin_restore_menu_item", {"p_id": 7})
        self.answer({"id": 7, "name": "Суп", "purged": True, "orders_count": 3})
        self.assertEqual(self.db.purge_menu_item(7)["orders_count"], 3)
        self.rpc.assert_called_with("admin_purge_menu_item", {"p_id": 7})

    def test_repeated_actions_report_no_change(self):
        self.answer({"id": 7, "name": "Суп", "archived": True, "changed": False})
        self.assertFalse(self.db.archive_menu_item(7)["changed"])
        self.answer({"id": 7, "name": "Суп", "archived": False, "changed": False})
        self.assertFalse(self.db.restore_menu_item(7)["changed"])

    def test_bad_item_id_is_rejected_before_any_request(self):
        for bad in (None, "7", 7.0, True):
            for call in (self.db.archive_menu_item, self.db.restore_menu_item, self.db.purge_menu_item):
                with self.subTest(bad=bad, call=call.__name__), self.assertRaises(dbm.AppError):
                    call(bad)
        self.rpc.assert_not_called()

    def test_unexpected_answers(self):
        cases = [
            (self.db.archive_menu_item, {"id": 8, "name": "Суп", "archived": True, "changed": True}),     # чужой номер
            (self.db.archive_menu_item, {"id": 7, "name": "Суп", "archived": False, "changed": True}),
            (self.db.archive_menu_item, {"id": 7, "name": "Суп", "archived": True, "changed": "yes"}),
            (self.db.archive_menu_item, {"id": 7, "archived": True, "changed": True}),                     # нет названия
            (self.db.restore_menu_item, {"id": 7, "name": "Суп", "archived": True, "changed": True}),
            (self.db.restore_menu_item, {"id": 7, "name": "Суп", "archived": False}),
            (self.db.purge_menu_item, {"id": 7, "name": "Суп", "purged": True}),                          # нет orders_count
            (self.db.purge_menu_item, {"id": 7, "name": "Суп", "purged": False, "orders_count": 0}),
            (self.db.purge_menu_item, {"id": 7, "name": "Суп", "purged": True, "orders_count": -1}),
            (self.db.purge_menu_item, {"id": 7, "name": "Суп", "purged": True, "orders_count": "2"}),
            (self.db.archive_menu_item, None),
            (self.db.purge_menu_item, []),
        ]
        for call, data in cases:
            with self.subTest(call=call.__name__, data=data):
                self.answer(data)
                with self.assertRaises(dbm.AppError) as ctx:
                    call(7)
                self.assertIn("неожиданный ответ", str(ctx.exception))

    def test_server_errors_have_clear_texts(self):
        cases = {
            "ADMIN_REQUIRED": ("forbidden", "только администратор"),
            "NOT_FOUND": ("invalid", "не найдена"),
            "NOT_APPROVED": ("invalid", "ждёт решения"),
            "ITEM_REJECTED": ("invalid", "отклонена"),
            "NOT_ARCHIVED": ("invalid", "только блюдо из архива"),
            "NOT_AUTHENTICATED": ("auth", "Сессия истекла"),
        }
        for message, (code, fragment) in cases.items():
            for call in (self.db.archive_menu_item, self.db.restore_menu_item, self.db.purge_menu_item):
                with self.subTest(message=message, call=call.__name__):
                    self.fail(api_error(message))
                    with self.assertRaises(dbm.AppError) as ctx:
                        call(7)
                    self.assertEqual(ctx.exception.code, code)
                    self.assertIn(fragment, str(ctx.exception))
                    self.assertNotIn(message, str(ctx.exception))   # внутренний код сотруднику не показывается

    def test_unknown_error_shows_code_not_server_text(self):
        self.fail(api_error('relation "menu_items" secret detail', "XX000"))
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.purge_menu_item(7)
        self.assertEqual(str(ctx.exception), "Блюдо не удалено. Код ошибки: XX000.")

    def test_before_migration_15_every_method_explains_what_to_do(self):
        self.fail(api_error("Could not find the function", "PGRST202"))
        for call in (self.db.archive_menu_item, self.db.restore_menu_item, self.db.purge_menu_item):
            with self.subTest(call=call.__name__), self.assertRaises(dbm.AppError) as ctx:
                call(7)
            self.assertIn("миграция 15", str(ctx.exception))
            self.assertIsNone(ctx.exception.code)    # не «auth»: приложение не выбрасывает на экран входа

    def test_no_connection(self):
        self.fail(httpx.ConnectError("down"))
        for call in (self.db.archive_menu_item, self.db.restore_menu_item, self.db.purge_menu_item):
            with self.subTest(call=call.__name__), self.assertRaises(dbm.AppError) as ctx:
                call(7)
            self.assertEqual(ctx.exception.code, "network")

    def test_archived_item_cannot_be_put_on_sale_message(self):
        # admin_set_menu_item_available(архивное, true) -> нарушение ограничения menu_items_archived_state
        self.fail(api_error('new row for relation "menu_items" violates check constraint "menu_items_archived_state"',
                            "23514"))
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.set_menu_item_available(7, True)
        self.assertIn("в архиве", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "invalid")
        # другое нарушение ограничения — прежний общий текст
        self.fail(api_error('violates check constraint "menu_items_price_check"', "23514"))
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.set_menu_item_available(7, True)
        self.assertIn("противоречит правилам", str(ctx.exception))


class CountOrdersTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.table = self.db.client.table.return_value
        for name in ("select", "eq", "limit"):
            getattr(self.table, name).return_value = self.table

    def test_counts_rows_of_the_item(self):
        self.table.execute.return_value.count = 4
        self.assertEqual(self.db.count_menu_item_orders(7), 4)
        self.db.client.table.assert_called_with("order_items")
        self.table.select.assert_called_with("order_id", count="exact")
        self.table.eq.assert_called_with("menu_item_id", 7)

    def test_bad_answers_and_ids(self):
        for bad in (None, -1, "3", 2.0):
            self.table.execute.return_value.count = bad
            with self.subTest(count=bad), self.assertRaises(dbm.AppError):
                self.db.count_menu_item_orders(7)
        for bad_id in (None, "7", True):
            with self.subTest(item_id=bad_id), self.assertRaises(dbm.AppError):
                self.db.count_menu_item_orders(bad_id)

    def test_errors(self):
        self.table.execute.side_effect = httpx.ConnectError("down")
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.count_menu_item_orders(7)
        self.assertEqual(ctx.exception.code, "network")
        self.table.execute.side_effect = api_error("x", "PGRST301")
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.count_menu_item_orders(7)
        self.assertEqual(ctx.exception.code, "auth")


MENU_ROW = {"id": 1, "code": 1001, "name": "Суп", "category": "Супы", "price": 450, "is_available": True,
            "status": "on_sale", "station": "kitchen", "reject_reason": None}


class LoadMenuModesTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.rpc = self.db.client.rpc

    def test_after_migration_15_flag_comes_from_archived_at(self):
        self.rpc.return_value.execute.return_value.data = [
            {**MENU_ROW, "archived_at": None},
            {**MENU_ROW, "id": 2, "name": "Борщ", "is_available": False, "status": "out_of_stock",
             "archived_at": "2026-11-14T10:00:00+00:00"}]
        rows = self.db.load_all_menu()
        self.assertEqual([r["archived"] for r in rows], [False, True])
        self.assertEqual(rows[0]["price"], D("450.00"))
        self.assertEqual([r["id"] for r in rows if not r["archived"]], [1])      # «Активные блюда»
        self.assertEqual([r["id"] for r in rows if r["archived"]], [2])          # «Архив»
        self.assertTrue(dbm.menu_archived(rows[1]))

    def test_before_migration_15_all_items_are_active(self):
        # station_menu без archived_at (09 без 15): приложение работает, архива просто нет
        self.rpc.return_value.execute.return_value.data = [dict(MENU_ROW)]
        (row,) = self.db.load_all_menu()
        self.assertFalse(row["archived"])

    def test_before_migration_09_table_fallback_still_works(self):
        self.rpc.return_value.execute.side_effect = api_error("x", "PGRST202")
        table = self.db.client.table.return_value
        for name in ("select", "order"):
            getattr(table, name).return_value = table
        table.execute.return_value.data = [{"id": 1, "code": 1001, "name": "Суп", "category": "Супы",
                                            "price": 450, "is_available": True, "station": "kitchen"}]
        (row,) = self.db.load_all_menu()
        self.assertFalse(row["archived"])
        self.assertEqual(row["price"], D("450.00"))
        self.assertEqual(dbm.menu_status(row), "on_sale")

    def test_bad_archived_at_is_unexpected_answer(self):
        self.rpc.return_value.execute.return_value.data = [{**MENU_ROW, "archived_at": 5}]
        with self.assertRaises(dbm.AppError):
            self.db.load_all_menu()

    def test_station_menu_keeps_working(self):
        # кухня и бар получают меню без архивных блюд (их фильтрует сервер) и без поля archived_at
        self.rpc.return_value.execute.return_value.data = [dict(MENU_ROW)]
        result = self.db.load_station_menu("kitchen")
        self.assertEqual([i["id"] for i in result["items"]], [1])
        self.assertTrue(result["can_propose"])


if __name__ == "__main__":
    unittest.main()
