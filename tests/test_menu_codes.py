"""Тесты кодов позиций меню и общего поиска (без подключения к Supabase).

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Уникальность и неизменность кодов в базе проверяет sql/tests/07_menu_codes_checks.sql.
"""

import re
import sys
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from postgrest.exceptions import APIError  # noqa: E402

import db as dbm  # noqa: E402
import search  # noqa: E402
import ui_admin  # noqa: E402
from _tk import get_root  # noqa: E402

MIGRATION = (ROOT / "sql" / "07_menu_codes.sql").read_text(encoding="utf-8")
SQL_ONLY = re.sub(r"--[^\n]*", "", MIGRATION)  # без комментариев: проверяется только код

MENU = [
    {"id": 10, "code": 1001, "name": "Капучино", "category": "Напитки"},
    {"id": 11, "code": 1002, "name": "Американо", "category": "Напитки"},
    {"id": 12, "code": 1010, "name": "Круассан", "category": "Выпечка"},
    {"id": 13, "code": 2001, "name": "Сок 100% апельсин", "category": "Напитки"},
]


def ids(items):
    return [item["id"] for item in items]


class SearchTest(unittest.TestCase):
    def test_full_code(self):
        self.assertEqual(ids(search.search(MENU, "1001")), [10])

    def test_code_prefix_exact_first(self):
        # «100» — начало кодов 1001 и 1002 и цифры в названии «Сок 100%»; 1010 не подходит
        self.assertEqual(ids(search.search(MENU, "100")), [10, 11, 13])
        self.assertEqual(ids(search.search(MENU, "101")), [12])
        self.assertEqual(ids(search.search(MENU, "2001")), [13])

    def test_name_part_case_and_spaces(self):
        self.assertEqual(ids(search.search(MENU, "  КАП ")), [10])
        self.assertEqual(ids(search.search(MENU, "ано")), [11])

    def test_empty_and_missing(self):
        self.assertEqual(ids(search.search(MENU, "   ")), [10, 11, 12, 13])
        self.assertEqual(search.search(MENU, "9999"), [])
        self.assertEqual(search.search(MENU, "пицца"), [])

    def test_find_by_code(self):
        self.assertEqual(search.find_by_code(MENU, " 1010 ")["id"], 12)
        self.assertIsNone(search.find_by_code(MENU, "101"))
        self.assertIsNone(search.find_by_code(MENU, "Капучино"))

    def test_performance_1000_items(self):
        items = [{"id": i, "code": 1000 + i, "name": f"Позиция номер {i}", "category": "X"} for i in range(1000)]
        start = time.perf_counter()
        for query in ("1", "1500", "номер 99", "zzz"):
            search.search(items, query)
        self.assertLess(time.perf_counter() - start, 0.05)


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db = dbm.Database.__new__(dbm.Database)
        self.db.client = mock.MagicMock()
        self.db.client.rpc.return_value.execute.return_value.data = {
            "id": 5, "code": 1006, "name": "Латте", "category": "Напитки", "price": 900, "is_available": True}

    def test_auto_code_not_sent(self):
        row = self.db.add_menu_item("Латте", "Напитки", "900")
        name, params = self.db.client.rpc.call_args.args
        self.assertEqual(name, "admin_add_menu_item")
        self.assertNotIn("p_code", params)
        self.assertEqual(row["code"], 1006)

    def test_manual_code_sent(self):
        self.db.add_menu_item("Латте", "Напитки", "900", " 1500 ")
        self.assertEqual(self.db.client.rpc.call_args.args[1]["p_code"], 1500)

    def test_bad_code_rejected_before_request(self):
        for text in ("0", "12a", "-5", "123456789", "1.5"):
            self.db.client.rpc.reset_mock()
            with self.assertRaises(dbm.AppError):
                self.db.add_menu_item("Латте", "Напитки", "900", text)
            self.db.client.rpc.assert_not_called()

    def test_code_taken_message(self):
        self.db.client.rpc.return_value.execute.side_effect = APIError(
            {"message": "CODE_TAKEN", "code": "23505", "details": "", "hint": ""})
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.add_menu_item("Латте", "Напитки", "900", "1001")
        self.assertEqual(ctx.exception.code, "invalid")
        self.assertIn("занят", str(ctx.exception))

    def test_menu_queries_include_code(self):
        query = self.db.client.table.return_value
        query.select.return_value = query.eq.return_value = query.order.return_value = query
        query.execute.return_value.data = []
        self.db.load_menu()
        self.assertIn("code", query.select.call_args.args[0])
        self.db.load_all_menu()
        self.assertIn("code", query.select.call_args.args[0])

    def test_missing_migration_message(self):
        query = self.db.client.table.return_value
        query.select.return_value = query.eq.return_value = query.order.return_value = query
        query.execute.side_effect = APIError({"message": "column menu_items.code does not exist", "code": "42703",
                                              "details": "", "hint": ""})
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.load_menu()
        self.assertIn("миграция", str(ctx.exception))


class MigrationTest(unittest.TestCase):
    def test_sequence_not_max_plus_one(self):
        self.assertIn("create sequence if not exists public.menu_item_code_seq", MIGRATION)
        self.assertIn("nextval('public.menu_item_code_seq')", MIGRATION)
        self.assertNotRegex(SQL_ONLY, r"(?i)max\(\s*code\s*\)\s*\+\s*1")

    def test_constraints(self):
        self.assertIn("alter column code set not null", MIGRATION)
        self.assertIn("unique (code)", MIGRATION)
        self.assertIn("before update of code on public.menu_items", MIGRATION)
        self.assertNotRegex(MIGRATION, r"(?i)alter table public\.menu_items\s+drop|drop table|primary key")

    def test_existing_data_untouched(self):
        # Однострочные update: заполнение кодов и admin_set_menu_item_available (как в 02)
        updates = re.findall(r"update public\.menu_items set (\w+)", SQL_ONLY)
        self.assertEqual(sorted(updates), ["code", "is_available"])
        self.assertNotRegex(SQL_ONLY, r"(?i)set\s+price\s*=\s*(?!p_price)")
        backfill = re.search(r"for r in select id from public\.menu_items where code is null order by id loop\s+"
                             r"update public\.menu_items set code = ", MIGRATION)
        self.assertIsNotNone(backfill)

    def test_rights(self):
        self.assertIn("drop function if exists public.admin_add_menu_item(text, text, numeric);", MIGRATION)
        self.assertIn("revoke all on sequence public.menu_item_code_seq from public, anon, authenticated;", MIGRATION)
        self.assertIn("revoke all on function public._next_menu_item_code() from public, anon, authenticated;",
                      MIGRATION)
        self.assertIn("grant execute on function public.admin_add_menu_item(text, text, numeric, integer) "
                      "to authenticated;", MIGRATION)
        self.assertNotRegex(MIGRATION, r"(?i)grant\s+(select|insert|update|delete|all)\s+on\s+(table\s+)?public\.")


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class FakeAdminDB:
    def __init__(self):
        self.rows = {
            1: dict(id=1, code=1001, name="Капучино", category="Напитки", price=Decimal("1100.00"), is_available=True),
            2: dict(id=2, code=1002, name="Круассан", category="Выпечка", price=Decimal("700.00"), is_available=False),
        }
        self.calls = []
        self.fail = None

    def load_all_menu(self):
        return [dict(r) for r in self.rows.values()]

    def add_menu_item(self, name, category, price_text, code_text="", station="kitchen"):
        self.calls.append(("add", name, code_text))
        self.station = station
        if self.fail:
            error, self.fail = self.fail, None
            raise error
        name, category, price = dbm.check_menu_item(name, category, price_text)
        code = dbm.parse_menu_code(code_text) or 1003
        self.rows[3] = dict(id=3, code=code, name=name, category=category, price=price, is_available=True,
                            station=station)
        return dict(self.rows[3])

    def update_menu_item(self, item_id, name, category, price_text, station=None):
        self.calls.append(("update", item_id, name))
        name, category, price = dbm.check_menu_item(name, category, price_text)
        self.rows[item_id].update(name=name, category=category, price=price)
        if station is not None:
            self.rows[item_id]["station"] = station
        return dict(self.rows[item_id])


class AdminUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        self.shown = []
        patches = [mock.patch.object(ui_admin.threading, "Thread", SyncThread)]
        for name in ("showinfo", "showwarning", "showerror"):
            patches.append(mock.patch.object(ui_admin.messagebox, name,
                                             lambda *a, _n=name, **k: self.shown.append((_n, a[0], a[1]))))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.db = FakeAdminDB()
        self.win = ui_admin.MenuAdminWindow(self.root, self.db, on_close=lambda: None, on_auth_error=lambda: None)
        self.addCleanup(lambda: self.win.winfo_exists() and self.win.destroy())
        self.pump()

    def pump(self):
        for _ in range(5):
            self.root.update()

    def test_code_column_and_search(self):
        self.assertEqual(self.win.tree.item("1")["values"][0], 1001)
        self.win.search_var.set("1002")
        self.assertEqual(self.win.tree.get_children(), ("2",))
        self.win.search_var.set("кап")
        self.assertEqual(self.win.tree.get_children(), ("1",))
        self.win.search_var.set("9999")
        self.assertEqual(self.win.tree.get_children(), ())
        self.assertEqual(self.win.found_label["text"], "Ничего не найдено")

    def test_create_shows_auto_code(self):
        self.win._fill_form("Латте", "Напитки", "900")
        self.win.save()
        self.pump()
        self.assertEqual(self.db.calls[-1], ("add", "Латте", ""))
        self.assertIn("(код 1003)", self.shown[-1][2])

    def test_manual_code_and_taken_error(self):
        self.win._fill_form("Латте", "Напитки", "900")
        self.win.code_entry.insert(0, "1001")
        self.db.fail = dbm.AppError(dbm.RPC_ERRORS["CODE_TAKEN"][1], "invalid")
        self.win.save()
        self.pump()
        self.assertEqual(self.db.calls[-1], ("add", "Латте", "1001"))
        self.assertEqual(self.shown[-1][0], "showwarning")
        self.assertNotIn(3, self.db.rows)

    def test_edit_keeps_code_readonly(self):
        self.win.tree.selection_set("1")
        self.pump()
        self.assertEqual(self.win.code_entry.get(), "1001")
        self.assertEqual(str(self.win.code_entry["state"]), "readonly")
        self.win._fill_form("Капучино большой", "Кофе", "1300")
        self.win.save()
        self.pump()
        self.assertEqual(self.db.calls[-1], ("update", 1, "Капучино большой"))
        self.assertEqual(self.db.rows[1]["code"], 1001)
        self.assertEqual(self.win.tree.item("1")["values"][0], 1001)

    def test_search_keeps_unsaved_input(self):
        self.win.tree.selection_set("1")
        self.pump()
        self.win._fill_form("Новое имя", "Напитки", "1100")
        self.win.search_var.set("кап")
        self.pump()
        self.assertEqual(self.win.name_entry.get(), "Новое имя")


if __name__ == "__main__":
    unittest.main()
