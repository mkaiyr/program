"""Категории меню (миграция 16): слой db.py, раздел «Категории меню», касса, структура миграции.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v
Без подключения к Supabase: у Database подменён клиент, экраны работают с заглушкой. Права, перенос блюд,
архив, отчёты, кухню и бар проверяет сервер: sql/tests/16_menu_categories_checks.sql.
"""

import re
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
import ui_admin  # noqa: E402
import ui_admin_panels as panels  # noqa: E402
import ui_base  # noqa: E402
import ui_order  # noqa: E402
from _tk import get_root  # noqa: E402

D = Decimal
SQL = (ROOT / "sql" / "16_menu_categories.sql").read_text(encoding="utf-8")


def api_error(message, code="P0001", details=""):
    return APIError({"message": message, "code": code, "details": details, "hint": ""})


def make_db():
    db = dbm.Database.__new__(dbm.Database)
    db.client = mock.MagicMock()
    return db


class CategoryMethodsTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.table = self.db.client.table.return_value
        self.table.select.return_value = self.table
        self.table.limit.return_value = self.table
        self.rpc = self.db.client.rpc

    def test_load_categories_sorted_case_insensitive(self):
        self.table.execute.return_value.data = [{"name": "супы"}, {"name": "Напитки"}, {"name": "Выпечка"},
                                                {"name": "Ёлка"}, {"name": "Горячее"}]
        self.assertEqual(self.db.load_categories(), ["Выпечка", "Горячее", "Напитки", "супы", "Ёлка"])
        self.db.client.table.assert_called_with("menu_categories")

    def test_before_migration_16_returns_empty_list(self):
        for code in ("PGRST205", "42P01"):
            self.table.execute.side_effect = api_error("no table", code)
            self.assertEqual(self.db.load_categories(), [])

    def test_other_errors_raise_unless_lenient(self):
        self.table.execute.side_effect = api_error("boom", "XX000")
        with self.assertRaises(dbm.AppError):
            self.db.load_categories()
        self.assertEqual(self.db.load_categories(lenient=True), [])
        self.table.execute.side_effect = httpx.ConnectError("x")
        with self.assertRaises(dbm.AppError):
            self.db.load_categories()
        self.assertEqual(self.db.load_categories(lenient=True), [])

    def test_lost_session_is_never_swallowed(self):
        self.table.execute.side_effect = api_error("JWT expired", "PGRST301")
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.load_categories(lenient=True)
        self.assertEqual(ctx.exception.code, "auth")

    def test_add_and_delete_call_server_functions(self):
        self.rpc.return_value.execute.return_value.data = {"id": 7, "name": "Салаты"}
        self.assertEqual(self.db.add_category("  Салаты ")["name"], "Салаты")
        self.rpc.assert_called_with("admin_add_category", {"p_name": "Салаты"})
        self.rpc.return_value.execute.return_value.data = {"name": "Салаты"}
        self.db.delete_category("Салаты")
        self.rpc.assert_called_with("admin_delete_category", {"p_name": "Салаты"})

    def test_input_checked_before_request(self):
        for call in (self.db.add_category, self.db.delete_category):
            for bad in ("", "   ", None, "я" * 51):
                with self.assertRaises(dbm.AppError):
                    call(bad)
        self.rpc.assert_not_called()

    def test_unexpected_answer_is_error(self):
        self.rpc.return_value.execute.return_value.data = None
        with self.assertRaises(dbm.AppError):
            self.db.add_category("Салаты")
        with self.assertRaises(dbm.AppError):
            self.db.delete_category("Салаты")

    def test_missing_functions_explain_migration_16(self):
        self.rpc.return_value.execute.side_effect = api_error("no fn", "PGRST202")
        for call in (self.db.add_category, self.db.delete_category):
            with self.assertRaises(dbm.AppError) as ctx:
                call("Салаты")
            self.assertIn("миграция 16", str(ctx.exception))

    def test_server_error_texts(self):
        cases = {"CATEGORY_EXISTS": "уже есть", "CATEGORY_NOT_FOUND": "Такой категории нет",
                 "ADMIN_REQUIRED": "только администратору", "INVALID_CATEGORY": "от 1 до 50", "NOT_FOUND": "не найдена"}
        for message, fragment in cases.items():
            self.rpc.return_value.execute.side_effect = api_error(message)
            with self.assertRaises(dbm.AppError) as ctx:
                self.db.add_category("Салаты")
            self.assertIn(fragment, str(ctx.exception), message)
            self.assertEqual(ctx.exception.code, "forbidden" if message == "ADMIN_REQUIRED" else "invalid")

    def test_not_empty_text_uses_counts_and_suggests_moving(self):
        self.rpc.return_value.execute.side_effect = api_error("CATEGORY_NOT_EMPTY", "23503", "5;2")
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.delete_category("Супы")
        text = str(ctx.exception)
        self.assertIn("5 блюд", text)
        self.assertIn("в архиве — 2", text)
        self.assertIn("перенесите блюда в другую категорию", text)
        self.assertNotIn("23503", text)
        self.assertNotIn("1;", dbm.category_not_empty_text("garbage"))
        self.assertIn("есть блюда", dbm.category_not_empty_text("garbage"))
        self.assertNotIn("архиве", dbm.category_not_empty_text("3;0"))

    def test_raw_category_foreign_key_error_is_friendly(self):
        # категорию удалили в тот момент, когда блюдо сохранялось: без технических подробностей
        self.rpc.return_value.execute.side_effect = api_error(
            'insert or update on table "menu_items" violates foreign key constraint "menu_items_category_fkey"', "23503")
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.add_menu_item("Суп", "Супы", "500")
        self.assertIn("Категория блюда была удалена", str(ctx.exception))
        self.assertNotIn("23503", str(ctx.exception))
        self.assertNotIn("menu_items_category_fkey", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "invalid")

    def test_rename_unchanged_contract(self):
        self.rpc.return_value.execute.return_value.data = {"updated": 2, "category": "Новое"}
        self.assertEqual(self.db.rename_category("Старое", "Новое")["updated"], 2)
        self.rpc.assert_called_with("admin_rename_category", {"p_old": "Старое", "p_new": "Новое"})

    def test_probe_lists_migration_16(self):
        result = self.db.probe_features()
        self.assertIn("16 Категории меню", result)
        self.assertTrue(result["16 Категории меню"])
        self.table.execute.side_effect = api_error("no table", "PGRST205")
        self.assertFalse(self.db.probe_features()["16 Категории меню"])

    def test_sort_categories(self):
        self.assertEqual(dbm.sort_categories(["б", "А", "а", "Б", "", None, "А"]), ["А", "а", "Б", "б"])


# ---------- Интерфейс ----------

class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def patch_threads():
    for module in (ui_base, ui_admin, ui_order):
        mock.patch.object(module.threading, "Thread", SyncThread).start()


def dish(item_id, category, archived=False, status="on_sale"):
    return {"id": item_id, "code": 1000 + item_id, "name": f"Блюдо {item_id}", "category": category,
            "price": D("500.00"), "is_available": status == "on_sale" and not archived,
            "status": "out_of_stock" if archived else status, "station": "kitchen", "reject_reason": None,
            "archived": archived, "archived_at": "2026-11-14T10:00:00+00:00" if archived else None}


class CategoryDB:
    """Справочник + меню администратора; методы повторяют правила серверных функций в нужном экрану объёме."""

    def __init__(self):
        self.categories = ["Супы", "Напитки", "Пустая"]
        self.menu = [dish(1, "Супы"), dish(2, "Супы", archived=True), dish(3, "Напитки"), dish(4, "Напитки", status="pending")]
        self.calls = []
        self.fail = {}

    def load_categories(self, lenient=False):
        self.calls.append("load_categories")
        return sorted(self.categories, key=str.casefold)

    def load_all_menu(self):
        return [dict(i) for i in self.menu]

    def _maybe_fail(self, name):
        if name in self.fail:
            raise self.fail[name]

    def add_category(self, name):
        self.calls.append(("add", name))
        self._maybe_fail("add")
        self.categories.append(name)
        return {"id": 1, "name": name}

    def rename_category(self, old, new):
        self.calls.append(("rename", old, new))
        self._maybe_fail("rename")
        self.categories = [new if c == old else c for c in self.categories]
        moved = 0
        for row in self.menu:
            if row["category"] == old:
                row["category"] = new
                moved += 1
        return {"updated": moved, "category": new}

    def delete_category(self, name):
        self.calls.append(("delete", name))
        self._maybe_fail("delete")
        self.categories.remove(name)
        return {"name": name}


class CategoriesPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        patch_threads()
        self.addCleanup(mock.patch.stopall)
        self.db = CategoryDB()
        self.changed = mock.Mock()
        self.panel = panels.CategoriesPanel(self.root, self.db, on_auth_error=mock.Mock(), on_menu_changed=self.changed)
        self.panel.pack(fill="both", expand=True)
        self.addCleanup(self.panel.destroy)
        self.pump()

    def pump(self):
        for _ in range(5):
            self.root.update()

    def rows(self):
        return {iid: self.panel.tree.item(iid, "values") for iid in self.panel.tree.get_children()}

    def pick(self, name):
        self.panel.tree.selection_set(name)
        self.pump()

    def submit_dialog(self, method, value):
        with mock.patch.object(panels, "FormDialog") as dialog:
            method()
        submit = dialog.call_args.args[3]
        result = submit({"name": value})
        self.pump()
        return result

    def test_lists_all_categories_with_counts_including_empty_and_alphabetical(self):
        self.assertEqual(list(self.rows()), ["Напитки", "Пустая", "Супы"])
        self.assertEqual(self.rows()["Пустая"], ("Пустая", "0", "0", "0"))
        self.assertEqual(self.rows()["Супы"], ("Супы", "1", "1", "1"))          # активных, в продаже, в архиве
        self.assertEqual(self.rows()["Напитки"], ("Напитки", "2", "1", "0"))    # «ждёт решения» не в продаже

    def test_category_missing_from_registry_still_shown_from_dishes(self):
        self.db.categories.remove("Супы")                                        # до миграции 16 справочника нет
        self.panel.reload()
        self.pump()
        self.assertIn("Супы", self.rows())

    def test_add_category(self):
        self.assertIsNone(self.submit_dialog(self.panel.add, "  Салаты "))
        self.assertIn(("add", "Салаты"), self.db.calls)
        self.assertEqual(self.rows()["Салаты"], ("Салаты", "0", "0", "0"))
        self.assertIn("добавлена", self.panel.status["text"])
        self.changed.assert_called()

    def test_add_checks_input_locally(self):
        for bad in ("", "   ", "я" * 51):
            self.assertIn("1 до 50", self.submit_dialog(self.panel.add, bad))
        self.assertIn("уже есть", self.submit_dialog(self.panel.add, "супы"))   # без учёта регистра
        self.assertFalse([c for c in self.db.calls if isinstance(c, tuple)])

    def test_add_server_error_is_shown(self):
        self.db.fail["add"] = dbm.AppError("Категория с таким названием уже есть.", "invalid")
        self.submit_dialog(self.panel.add, "Салаты")
        self.assertIn("уже есть", self.panel.status["text"])
        self.assertNotIn("Салаты", self.rows())

    def test_rename_and_merge(self):
        self.pick("Супы")
        self.assertIsNone(self.submit_dialog(self.panel.rename, "Первые блюда"))
        self.assertIn(("rename", "Супы", "Первые блюда"), self.db.calls)
        self.assertEqual(self.rows()["Первые блюда"], ("Первые блюда", "1", "1", "1"))
        self.assertIn("блюд изменено 2", self.panel.status["text"])
        self.pick("Пустая")                                                      # имя занято — категории объединяются
        self.assertIsNone(self.submit_dialog(self.panel.rename, "Напитки"))
        self.assertIn(("rename", "Пустая", "Напитки"), self.db.calls)

    def test_rename_requires_selection_and_new_name(self):
        self.panel.rename()
        self.assertIn("Выберите категорию", self.panel.status["text"])
        self.pick("Супы")
        self.assertIn("не изменилось", self.submit_dialog(self.panel.rename, "Супы"))

    def test_delete_empty_category_after_confirmation(self):
        self.pick("Пустая")
        with mock.patch.object(panels.messagebox, "askyesno", return_value=True) as ask:
            self.panel.delete()
        self.pump()
        self.assertIn("Пустая", ask.call_args.args[1])
        self.assertIn(("delete", "Пустая"), self.db.calls)
        self.assertNotIn("Пустая", self.rows())
        self.assertIn("удалена", self.panel.status["text"])

    def test_delete_declined_does_nothing(self):
        self.pick("Пустая")
        with mock.patch.object(panels.messagebox, "askyesno", return_value=False):
            self.panel.delete()
        self.assertNotIn(("delete", "Пустая"), self.db.calls)
        self.assertIn("Пустая", self.rows())

    def test_delete_category_with_dishes_explains_and_makes_no_request(self):
        self.pick("Супы")                                                        # 1 активное + 1 в архиве
        with mock.patch.object(panels.messagebox, "showwarning") as warn, \
                mock.patch.object(panels.messagebox, "askyesno") as ask:
            self.panel.delete()
        ask.assert_not_called()
        text = warn.call_args.args[1]
        self.assertIn("2 блюд", text)
        self.assertIn("в архиве — 1", text)
        self.assertIn("перенесите блюда в другую категорию", text)
        self.assertFalse([c for c in self.db.calls if isinstance(c, tuple)])
        self.assertIn("Супы", self.rows())

    def test_delete_refused_by_server_shows_message_and_refreshes(self):
        # блюдо добавили, пока диалог был открыт: локальные счётчики устарели, отказ даёт сервер
        self.pick("Пустая")
        self.db.fail["delete"] = dbm.AppError(dbm.category_not_empty_text("1;0"), "invalid")
        with mock.patch.object(panels.messagebox, "askyesno", return_value=True), \
                mock.patch.object(panels.messagebox, "showwarning") as warn:
            self.panel.delete()
            self.pump()          # ответ сервера приходит через очередь Tk — внутри подмены диалога
        warn.assert_called_once()
        self.assertIn("Пустая", self.rows())
        self.assertIn("удалить её нельзя", warn.call_args.args[1])

    def test_delete_requires_selection(self):
        with mock.patch.object(panels.messagebox, "askyesno") as ask:
            self.panel.delete()
        ask.assert_not_called()
        self.assertIn("Выберите категорию", self.panel.status["text"])

    def test_buttons_present(self):
        self.assertEqual([str(self.panel.add_btn["text"]), str(self.panel.rename_btn["text"]),
                          str(self.panel.delete_btn["text"])], ["Добавить", "Переименовать", "Удалить"])


class CashDB:
    def __init__(self):
        self.menu = [{"id": 1, "code": 1, "name": "Борщ", "category": "Супы", "price": D("500.00")},
                     {"id": 2, "code": 2, "name": "Кола", "category": "Напитки", "price": D("400.00")}]
        self.categories = ["Супы", "Напитки", "Десерты", "Бар"]
        self.fail_categories = None

    def load_menu(self):
        return [dict(i) for i in self.menu]

    def load_categories(self, lenient=False):
        if self.fail_categories and not lenient:
            raise self.fail_categories
        return [] if self.fail_categories else sorted(self.categories)


class CashierCategoriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        patch_threads()
        self.addCleanup(mock.patch.stopall)
        self.db = CashDB()
        self.frame = ui_order.OrderFrame(self.root, self.db, {"full_name": "К", "role": "cashier"}, on_logout=mock.Mock())
        self.frame.pack(fill="both", expand=True)
        self.addCleanup(self.frame.destroy)
        self.pump()

    def pump(self):
        for _ in range(4):
            self.root.update()

    def chips(self):
        return [w["text"] for w in self.frame.chips.winfo_children()]

    def reload(self):
        self.frame.reload_menu()
        self.pump()

    def test_chips_alphabetical_and_include_empty_categories(self):
        self.assertEqual(self.chips(), ["Все", "Бар", "Десерты", "Напитки", "Супы"])

    def test_new_category_appears_without_code_changes_after_reload(self):
        self.db.categories.append("Авторские салаты")
        self.reload()
        self.assertEqual(self.chips()[1], "Авторские салаты")

    def test_empty_category_shows_message_and_does_not_break_catalog(self):
        self.frame.category_var.set("Десерты")
        self.frame._render_catalog()
        self.pump()
        self.assertEqual(self.frame.tiles, {})
        self.assertIn("Десерты", self.frame._empty_text())
        self.assertIn("нет доступных блюд", self.frame._empty_text())
        self.frame.category_var.set("Все")
        self.frame._render_catalog()
        self.assertEqual(sorted(self.frame.tiles), [1, 2])

    def test_removed_category_selection_falls_back_to_all(self):
        self.frame.category_var.set("Десерты")
        self.db.categories.remove("Десерты")
        self.reload()
        self.assertEqual(self.frame.category_var.get(), "Все")

    def test_cart_and_search_survive_reload_with_new_categories(self):
        f = self.frame
        f.add_item(1)
        f.add_item(1)
        f.search_var.set("кола")
        self.pump()
        self.db.categories.append("Новая")
        self.reload()
        self.assertEqual(f.cart[1]["qty"], 2)
        self.assertEqual(f.total_label["text"], "1 100 ₸")
        self.assertEqual(f.search_var.get(), "кола")
        self.assertIn("Новая", self.chips())

    def test_menu_works_if_registry_is_unavailable(self):
        # справочник не загрузился (или миграция 16 не выполнена): категории берутся из самих блюд
        self.db.fail_categories = dbm.AppError("x")
        self.reload()
        self.assertEqual(self.chips(), ["Все", "Напитки", "Супы"])
        self.assertEqual(sorted(self.frame.tiles), [1, 2])


class EditorCategoriesTest(unittest.TestCase):
    """Редактор меню: выпадающий список категорий включает пустые категории справочника."""

    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def test_editor_list_includes_registry(self):
        patch_threads()
        self.addCleanup(mock.patch.stopall)
        db = CategoryDB()
        db.categories.append("Авторские")
        panel = panels.MenuSectionPanel(self.root, db, on_auth_error=mock.Mock(), on_menu_changed=mock.Mock())
        panel.pack()
        self.addCleanup(panel.destroy)
        for _ in range(4):
            self.root.update()
        self.assertEqual(list(panel.category_box["values"]), ["Все категории", "Авторские", "Напитки", "Пустая", "Супы"])


# ---------- Структура миграции ----------

def strip_comments(text):
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


class CategoryMigrationTest(unittest.TestCase):
    CODE = strip_comments(SQL)

    def test_single_transaction_and_idempotent_definitions(self):
        self.assertEqual(self.CODE.count("begin;"), 1)
        self.assertTrue(self.CODE.rstrip().endswith("commit;"))
        self.assertIn("create table if not exists public.menu_categories", self.CODE)
        self.assertIn("on conflict do nothing", self.CODE)
        self.assertIn("if not exists (select 1 from pg_constraint", self.CODE)
        self.assertIn("drop trigger if exists menu_items_category_ref", self.CODE)

    def test_does_not_destroy_data(self):
        for forbidden in ("drop table", "truncate", "delete from", "drop column", "alter column category"):
            self.assertNotIn(forbidden, self.CODE.replace("delete from public.menu_categories where id", ""))
        self.assertNotIn("add column", self.CODE)   # menu_items.category остаётся текстовой колонкой

    def test_foreign_key_protects_dishes(self):
        self.assertRegex(self.CODE, r"foreign key \(category\)\s+references public\.menu_categories \(name\) "
                                    r"on update cascade on delete restrict")
        self.assertIn("create unique index if not exists menu_categories_name_ci_key", self.CODE)
        self.assertIn("lower(name)", self.CODE)

    def test_requires_previous_migrations(self):
        self.assertIn("Сначала выполните миграции 01–15", self.CODE)
        self.assertIn("archived_at", self.CODE)

    def test_functions_are_admin_only_security_definer(self):
        for fn in ("admin_add_category", "admin_rename_category", "admin_delete_category"):
            m = re.search(rf"create or replace function public\.{fn}\(.*?\n\$\$;", self.CODE, re.S)
            self.assertIsNotNone(m, fn)
            body = m.group(0)
            self.assertIn("security definer", body, fn)
            self.assertIn("set search_path = ''", body, fn)
            self.assertIn("public.is_active_admin()", body, fn)
            self.assertIn("'ADMIN_REQUIRED'", body, fn)
            self.assertIn(f"revoke all on function public.{fn}", self.CODE)
            self.assertRegex(self.CODE, rf"grant execute on function public\.{fn}\(.*?\) to authenticated;")

    def test_clients_cannot_write_table(self):
        self.assertIn("revoke all on public.menu_categories from anon, authenticated;", self.CODE)
        self.assertIn("grant select on public.menu_categories to authenticated;", self.CODE)
        self.assertIn("alter table public.menu_categories enable row level security;", self.CODE)
        self.assertNotRegex(self.CODE, r"grant (insert|update|delete|all)[^;]*menu_categories")
        self.assertIn("revoke all on function public.menu_items_category_ref() from public, anon, authenticated;",
                      self.CODE)

    def test_staff_cannot_create_categories_through_dishes(self):
        trigger = re.search(r"create or replace function public\.menu_items_category_ref\(\).*?\n\$\$;", self.CODE, re.S).group(0)
        self.assertIn("auth.uid() is not null and not public.is_active_admin()", trigger)
        self.assertIn("'CATEGORY_NOT_FOUND'", trigger)

    def test_delete_blocked_by_dishes_including_archived(self):
        m = re.search(r"create or replace function public\.admin_delete_category.*?\n\$\$;", self.CODE, re.S).group(0)
        self.assertIn("from public.menu_items where category = v_name", m)
        self.assertNotIn("archived_at is null", m)    # архивные блюда считаются
        self.assertIn("'CATEGORY_NOT_EMPTY'", m)
        self.assertIn("foreign_key_violation", m)

    def test_does_not_touch_order_history(self):
        self.assertNotIn("order_items", self.CODE)
        self.assertNotIn("public.orders", self.CODE)

    def test_original_migrations_untouched(self):
        # 16 переопределяет только admin_rename_category из 14; миграции 01–15 не редактируются
        text14 = (ROOT / "sql" / "14_reports_categories.sql").read_text(encoding="utf-8")
        self.assertIn("create or replace function public.admin_rename_category", text14)
        self.assertEqual(self.CODE.count("create or replace function public.admin_rename_category"), 1)

    def test_error_codes_known_to_app(self):
        for code in ("CATEGORY_EXISTS", "CATEGORY_NOT_FOUND", "CATEGORY_NOT_EMPTY", "ADMIN_REQUIRED", "INVALID_CATEGORY"):
            self.assertIn(f"'{code}'", self.CODE)
        for code in ("CATEGORY_EXISTS", "CATEGORY_NOT_FOUND"):
            self.assertIn(code, dbm.RPC_ERRORS)


if __name__ == "__main__":
    unittest.main()
