"""Тексты ошибок для сотрудника и неактивная учётная запись во время смены.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Без подключения к Supabase: у Database подменён клиент (MagicMock), ошибки сервера —
настоящие postgrest.exceptions.APIError. Проверяется, что сотрудник видит понятный текст
без имён таблиц, ограничений и внутренних сообщений, а подробности уходят в журнал.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import ui_order  # noqa: E402
from _tk import get_root  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

INTERNAL = 'new row for relation "menu_items" violates check constraint "menu_items_approved_before_sale"'


def api_error(message, code):
    return APIError({"message": message, "code": code, "details": "", "hint": ""})


def make_db(profile=None):
    db = dbm.Database.__new__(dbm.Database)
    db.profile = profile
    db.client = mock.MagicMock()
    return db


class ApiErrorTextTest(unittest.TestCase):
    def test_check_violation_hides_server_text(self):
        with self.assertLogs("db", "WARNING") as logs:
            error = dbm._api_error(api_error(INTERNAL, "23514"), "Доступность не изменена")
        self.assertEqual(error.code, "invalid")
        self.assertNotIn("menu_items", str(error))
        self.assertIn("Обновите список", str(error))
        self.assertIn("menu_items_approved_before_sale", "".join(logs.output))  # для разработчика

    def test_unknown_error_shows_only_code(self):
        with self.assertLogs("db", "WARNING"):
            error = dbm._api_error(api_error("internal: relation public.x does not exist", "XX000"),
                                   "Заказ не сохранён")
        self.assertEqual(str(error), "Заказ не сохранён. Код ошибки: XX000.")

    def test_permission_denied_is_forbidden(self):
        with self.assertLogs("db", "WARNING"):
            error = dbm._api_error(api_error("permission denied for table order_stations", "42501"), "Ошибка")
        self.assertEqual(error.code, "forbidden")
        self.assertNotIn("order_stations", str(error))

    def test_own_codes_still_win(self):
        """Коды функций проекта (message) важнее общего кода PostgreSQL: NOT_ALLOWED тоже 42501."""
        error = dbm._api_error(api_error("NOT_ALLOWED", "42501"), "Ошибка")
        self.assertEqual(error.code, "auth")

    def test_station_menu_codes_have_texts(self):
        for message in ("ITEM_REJECTED", "NOT_APPROVED", "ALREADY_APPROVED"):
            with self.subTest(message):
                error = dbm._api_error(api_error(message, "P0001"), "Ошибка")
                self.assertEqual(error.code, "invalid")
                self.assertNotIn(message, str(error))

    def test_unexpected_error_hides_exception_text(self):
        with self.assertLogs("db", "ERROR") as logs:
            error = dbm.unexpected_error(RuntimeError("/Users/dom/secret/path line 42"))
        self.assertNotIn("secret", str(error))
        self.assertEqual(str(error), dbm.UNEXPECTED_ERROR)
        self.assertIn("secret", "".join(logs.output))


class StillAllowedTest(unittest.TestCase):
    """Касса при обновлении меню перечитывает свой профиль."""

    def db_with_profile_row(self, row):
        db = make_db({"id": "cashier-1", "role": "cashier"})
        tables = {"profiles": mock.MagicMock(), "menu_items": mock.MagicMock()}
        for query in tables.values():
            query.select.return_value = query.eq.return_value = query.order.return_value = query
            query.limit.return_value = query
        tables["profiles"].execute.return_value.data = [row] if row else []
        tables["menu_items"].execute.return_value.data = [
            {"id": 1, "code": 1001, "name": "Суп", "category": "Супы", "price": "900.00"}]
        db.client.table.side_effect = lambda name: tables[name]
        return db, tables

    def test_inactive_employee_sent_to_login(self):
        for name, row in (("деактивирован", {"is_active": False, "role": "cashier"}),
                          ("роль сменили", {"is_active": True, "role": "kitchen"}),
                          ("профиль удалён", None)):
            with self.subTest(name):
                db, tables = self.db_with_profile_row(row)
                with self.assertRaises(dbm.AppError) as ctx:
                    db.load_menu()
                self.assertEqual(ctx.exception.code, "auth")
                tables["menu_items"].execute.assert_not_called()

    def test_active_employee_gets_menu(self):
        db, _tables = self.db_with_profile_row({"is_active": True, "role": "cashier"})
        self.assertEqual([item["name"] for item in db.load_menu()], ["Суп"])

    def test_profile_check_network_error(self):
        db, tables = self.db_with_profile_row({"is_active": True, "role": "cashier"})
        import httpx
        tables["profiles"].execute.side_effect = httpx.ConnectError("нет сети")
        with self.assertRaises(dbm.AppError) as ctx:
            db.load_menu()
        self.assertEqual(ctx.exception.code, "network")


class UnexpectedErrorOnScreenTest(unittest.TestCase):
    """Непредвиденное исключение в фоновом запросе кассы: понятный текст, без подробностей."""

    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def test_cashier_status_text(self):
        class BrokenDB:
            def load_menu(self):
                raise KeyError("price — внутреннее поле ответа")

        class SyncThread:
            """Запрос выполняется сразу: без mainloop настоящий поток не может передать результат в Tk."""
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        patches = [mock.patch.object(ui_order.messagebox, name) for name in ("showinfo", "showwarning",
                                                                               "showerror")]
        patches.append(mock.patch.object(ui_order.threading, "Thread", SyncThread))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with self.assertLogs("db", "ERROR"):
            frame = ui_order.OrderFrame(self.root, BrokenDB(), {"full_name": "Кассир", "role": "cashier"},
                                        on_logout=mock.Mock())
        self.addCleanup(frame.destroy)
        for _ in range(5):
            self.root.update()
        self.assertFalse(frame.busy)
        self.assertEqual(frame.status["text"], dbm.UNEXPECTED_ERROR)
        self.assertNotIn("внутреннее", frame.status["text"])


if __name__ == "__main__":
    unittest.main()
