"""Смены, сотрудники, отчёты, история заказов с аннулированием, кухня (время ожидания), оболочка администратора.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v
Без подключения к Supabase: у Database подменён клиент или экраны работают с заглушкой.
"""

import csv
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import reports  # noqa: E402
import ui_base  # noqa: E402
import ui_kitchen  # noqa: E402
import ui_orders  # noqa: E402
import ui_shell  # noqa: E402
import ui_staff  # noqa: E402
from _tk import get_root  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

D = Decimal


def api_error(message, code="P0001", details=""):
    return APIError({"message": message, "code": code, "details": details, "hint": ""})


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def make_db():
    db = dbm.Database.__new__(dbm.Database)
    db.client = mock.MagicMock()
    return db


def answer(db, data):
    db.client.rpc.return_value.execute.side_effect = None
    db.client.rpc.return_value.execute.return_value.data = data


SHIFT = {"id": 3, "cashier_id": "u1", "cashier_name": "Айгерим", "opened_at": "2026-11-11T04:00:00+00:00",
         "closed_at": None, "opening_cash": 20000, "actual_cash": None, "orders_count": 2, "sales_total": 85000,
         "deposits_total": 5000, "withdrawals_total": 10000, "expected_cash": 100000, "difference": None,
         "movements": [{"id": 1, "kind": "deposit", "amount": 5000, "note": None,
                        "created_at": "2026-11-11T05:00:00+00:00"}]}


class ParseTest(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(dbm.parse_amount("20 000"), D("20000.00"))
        self.assertEqual(dbm.parse_amount("150,5"), D("150.50"))
        self.assertEqual(dbm.parse_amount("0", allow_zero=True), D("0.00"))
        for bad in ("", "abc", "-5", "1.234", "0", "99999999999"):
            with self.subTest(bad=bad), self.assertRaises(dbm.AppError):
                dbm.parse_amount(bad)

    def test_staff_fields(self):
        self.assertEqual(dbm.check_staff(" Мария ", "bar", "1234"), ("Мария", "bar", "1234"))
        for args in (("", "bar", "1234"), ("М", "boss", "1234"), ("М", "bar", "12"), ("М", "bar", "1234567"),
                     ("М", "bar", "12a4"), ("М" * 101, "bar", "1234")):
            with self.subTest(args=args), self.assertRaises(dbm.AppError):
                dbm.check_staff(*args)
        with self.assertRaises(dbm.AppError):
            dbm.check_password("short")
        dbm.check_password("longenough")


class DatabaseNewTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.rpc = self.db.client.rpc

    def test_shift_flow_params_and_decimals(self):
        answer(self.db, SHIFT)
        shift = self.db.open_shift("20 000")
        self.rpc.assert_called_with("shift_open", {"p_opening_cash": "20000.00"})
        # пример из задания: 20 000 + 85 000 + 5 000 − 10 000 = 100 000
        self.assertEqual(shift["expected_cash"], D("100000.00"))
        self.assertEqual(shift["expected_cash"], shift["opening_cash"] + shift["sales_total"]
                         + shift["deposits_total"] - shift["withdrawals_total"])
        self.assertEqual(shift["movements"][0]["amount"], D("5000.00"))
        self.db.cash_move("withdrawal", "10000", " на закупку ")
        self.rpc.assert_called_with("shift_cash_move", {"p_kind": "withdrawal", "p_amount": "10000.00",
                                                       "p_note": "на закупку"})
        closed = {**SHIFT, "closed_at": "2026-11-11T12:00:00+00:00", "actual_cash": 99500, "difference": -500}
        answer(self.db, closed)
        result = self.db.close_shift("99500")
        self.rpc.assert_called_with("shift_close", {"p_actual_cash": "99500.00"})
        self.assertEqual(result["difference"], D("-500.00"))

    def test_shift_validation_before_request(self):
        for call in (lambda: self.db.open_shift("-1"), lambda: self.db.cash_move("steal", "5"),
                     lambda: self.db.cash_move("deposit", "0"), lambda: self.db.close_shift("")):
            with self.assertRaises(dbm.AppError):
                call()
        self.rpc.assert_not_called()

    def test_current_shift_none_and_bad_answer(self):
        answer(self.db, None)
        self.assertIsNone(self.db.current_shift())
        answer(self.db, {"id": "x"})
        with self.assertRaises(dbm.AppError):
            self.db.current_shift()

    def test_shift_server_errors_have_texts(self):
        for code, text in (("NO_OPEN_SHIFT", "Нет открытой смены"), ("SHIFT_ALREADY_OPEN", "уже открыта"),
                           ("INSUFFICIENT_CASH", "недостаточно")):
            self.rpc.return_value.execute.side_effect = api_error(code, details="500.00")
            with self.subTest(code=code), self.assertRaises(dbm.AppError) as ctx:
                self.db.cash_move("withdrawal", "1")
            self.assertIn(text, str(ctx.exception))

    def test_missing_migration_message(self):
        self.rpc.return_value.execute.side_effect = api_error("x", "PGRST202")
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.current_shift()
        self.assertIn("миграция 12", str(ctx.exception))
        with self.assertRaises(dbm.AppError) as ctx:
            self.db.load_staff()
        self.assertIn("миграция 13", str(ctx.exception))

    def test_staff_calls(self):
        row = {"id": "u2", "full_name": "Бота", "role": "bar", "employee_code": "2001", "is_active": True}
        answer(self.db, row)
        self.db.create_staff(" Бота ", "bar", "2001", "password1")
        self.rpc.assert_called_with("admin_create_staff", {"p_full_name": "Бота", "p_role": "bar",
                                                           "p_code": "2001", "p_password": "password1"})
        self.db.update_staff("u2", "Бота", "kitchen", "2001", False)
        self.rpc.assert_called_with("admin_update_staff", {"p_id": "u2", "p_full_name": "Бота", "p_role": "kitchen",
                                                           "p_code": "2001", "p_is_active": False})
        answer(self.db, {"id": "u2", "password_changed": True})
        self.db.set_staff_password("u2", "newpassword")
        self.rpc.assert_called_with("admin_set_staff_password", {"p_id": "u2", "p_password": "newpassword"})
        answer(self.db, [row, {"id": "u3", "full_name": "Без кода", "role": "cashier", "employee_code": None,
                               "is_active": False}])
        staff = self.db.load_staff()
        self.assertEqual(staff[1]["employee_code"], "")
        answer(self.db, [{"id": "u2", "role": "owner"}])
        with self.assertRaises(dbm.AppError):
            self.db.load_staff()
        for text in ("SELF_LOCKOUT", "LAST_ADMIN", "EMPLOYEE_CODE_TAKEN"):
            self.rpc.return_value.execute.side_effect = api_error(text)
            with self.subTest(text=text), self.assertRaises(dbm.AppError):
                self.db.update_staff("u2", "Бота", "bar", "2001", True)

    def test_void_order(self):
        answer(self.db, {"order_id": 4, "voided": True, "changed": True, "void_reason": "Ошибка"})
        self.assertTrue(self.db.void_order(4, " Ошибка кассира ")["changed"])
        self.rpc.assert_called_with("void_order", {"p_order_id": 4, "p_reason": "Ошибка кассира"})
        for reason in ("", "  ", "ab", "x" * 201):
            with self.subTest(reason=reason), self.assertRaises(dbm.AppError):
                self.db.void_order(4, reason)
        answer(self.db, {"order_id": 5, "voided": True, "changed": True})
        with self.assertRaises(dbm.AppError):
            self.db.void_order(4, "Ошибка кассира")

    def test_report_decimals(self):
        answer(self.db, {"orders_count": 2, "subtotal_total": 8000, "service_total": 800, "revenue": 8800,
                         "average_check": 4400, "voided_count": 1, "voided_total": 1100,
                         "top_items": [{"name": "Бургер", "quantity": 3, "revenue": 9000}],
                         "categories": [], "cashiers": [{"name": "А", "orders": 2, "revenue": 8800, "service": 800}],
                         "daily": [{"day": "2026-11-11", "orders": 2, "revenue": 8800}]})
        start = datetime(2026, 11, 11).astimezone()
        report = self.db.load_report(start, start + timedelta(days=1))
        self.assertEqual(report["revenue"], D("8800.00"))
        self.assertEqual(report["revenue"], report["subtotal_total"] + report["service_total"])
        self.assertEqual(self.rpc.call_args.args[0], "admin_report")
        answer(self.db, {"orders_count": 0})
        with self.assertRaises(dbm.AppError):
            self.db.load_report(start, start + timedelta(days=1))

    def test_load_orders_filters_and_old_schema(self):
        table = self.db.client.table.return_value
        for name in ("select", "eq", "gte", "lt", "is_", "order", "limit"):
            getattr(table, name).return_value = table
        table.not_.is_.return_value = table
        row = {"id": 7, "created_at": "2026-11-11T05:00:00+00:00", "total": 4400, "status": "accepted",
               "subtotal": 4000, "service_percent": 10, "service_amount": 400, "voided_at": None,
               "void_reason": None, "cashier": {"full_name": "А"}, "voider": None, "order_items": []}
        table.execute.return_value.data = [row]
        day = datetime(2026, 11, 11).astimezone()
        (order,) = self.db.load_orders(order_id=7, date_from=day, date_to=day + timedelta(days=1),
                                       status="accepted", voided=False)
        table.eq.assert_any_call("id", 7)
        table.eq.assert_any_call("status", "accepted")
        table.is_.assert_any_call("voided_at", "null")
        self.assertEqual((order["subtotal"], order["service_amount"], order["total"]),
                         (D("4000.00"), D("400.00"), D("4400.00")))
        self.assertFalse(order["voided"])
        table.not_.is_.reset_mock()
        self.db.load_orders(voided=True)
        table.not_.is_.assert_called_with("voided_at", "null")

        # до миграции 11: колонок нет — прежний запрос, подытог = итог, надбавки нет
        old_row = {"id": 1, "created_at": "2026-11-01T05:00:00+00:00", "total": 1000, "status": "completed",
                   "cashier": {"full_name": "А"}, "order_items": []}
        table.execute.side_effect = [api_error("col", "42703"), mock.Mock(data=[old_row])]
        (old,) = self.db.load_orders()
        self.assertEqual((old["subtotal"], old["service_amount"], old["voided"]), (D("1000.00"), D("0.00"), False))


class ReportsTest(unittest.TestCase):
    TODAY = date(2026, 11, 11)  # среда

    def test_periods(self):
        start, end = reports.period_range("Сегодня", self.TODAY)
        self.assertEqual((end - start), timedelta(days=1))
        self.assertEqual(start.date(), self.TODAY)
        start, end = reports.period_range("7 дней", self.TODAY)
        self.assertEqual(start.date(), date(2026, 11, 5))
        self.assertEqual(end.date(), date(2026, 11, 12))
        start, end = reports.period_range("Месяц", self.TODAY)
        self.assertEqual(start.date(), date(2026, 11, 1))
        start, end = reports.period_range("Свой период", self.TODAY, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(end.date(), date(2026, 11, 1))   # конец не включается: 31 октября входит
        for args in (("Свой период", self.TODAY, date(2026, 10, 5), date(2026, 10, 1)),
                     ("Свой период", self.TODAY, date(2024, 1, 1), date(2026, 1, 1)),
                     ("Свой период", self.TODAY, None, None), ("Год", self.TODAY)):
            with self.subTest(args=args), self.assertRaises(dbm.AppError):
                reports.period_range(*args)

    def test_parse_date(self):
        self.assertEqual(reports.parse_date("05.11.2026"), date(2026, 11, 5))
        self.assertEqual(reports.parse_date("2026-11-05"), date(2026, 11, 5))
        with self.assertRaises(dbm.AppError):
            reports.parse_date("32.13.2026")

    def test_csv_export_roundtrip(self):
        report = {"orders_count": 2, "subtotal_total": D("8000.00"), "service_total": D("800.00"),
                  "revenue": D("8800.00"), "average_check": D("4400.00"), "voided_count": 1,
                  "voided_total": D("1100.00"),
                  "top_items": [{"name": "Бургер; XL", "quantity": 3, "revenue": D("9000.00")}],
                  "categories": [{"category": "Еда", "quantity": 3, "revenue": D("9000.00")}],
                  "cashiers": [{"name": "Айгерим", "orders": 2, "revenue": D("8800.00"), "service": D("800.00")}],
                  "daily": [{"day": "2026-11-11", "orders": 2, "revenue": D("8800.00")}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.csv"
            reports.export_report_csv(path, report, "11.11.2026 — 11.11.2026")
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))           # BOM: Excel читает кириллицу
            rows = list(csv.reader(raw.decode("utf-8-sig").splitlines(), delimiter=";"))
        flat = {row[0]: row[1:] for row in rows if row}
        self.assertEqual(flat["Выручка (с надбавкой), ₸"], ["8800.00"])
        self.assertEqual(flat["Надбавка 10%, ₸"], ["800.00"])
        self.assertEqual(flat["Бургер; XL"], ["3", "9000.00"])          # «;» в названии экранируется кавычками
        self.assertEqual(flat["Айгерим"], ["2", "8800.00", "800.00"])

    def test_orders_csv(self):
        order = {"id": 7, "created_at": "11.11.2026 10:00", "cashier_name": "А", "status": "completed",
                 "subtotal": D("4000.00"), "service_amount": D("400.00"), "total": D("4400.00"), "voided": True,
                 "void_reason": "Ошибка", "items": [{"item_name": "Кола", "quantity": 2}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "o.csv"
            self.assertEqual(reports.export_orders_csv(path, [order]), 1)
            rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
        self.assertEqual(rows[1], ["7", "11.11.2026 10:00", "А", "completed", "4000.00", "400.00", "4400.00",
                                   "да", "Ошибка", "Кола × 2"])


class KitchenWaitTest(unittest.TestCase):
    def test_waiting_and_levels(self):
        now = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
        created = lambda m: (now - timedelta(minutes=m, seconds=30)).isoformat()  # noqa: E731
        self.assertEqual(ui_kitchen.waiting_minutes(created(4), now), 4)
        self.assertEqual(ui_kitchen.waiting_minutes((now + timedelta(minutes=3)).isoformat(), now), 0)
        self.assertIsNone(ui_kitchen.waiting_minutes("мусор", now))
        self.assertIsNone(ui_kitchen.waiting_minutes(None, now))
        levels = [(0, 0), (9, 0), (10, 1), (19, 1), (20, 2), (90, 2)]
        for minutes, level in levels:
            with self.subTest(minutes=minutes):
                self.assertEqual(ui_kitchen.delay_level(minutes), level)
        self.assertEqual(ui_kitchen.delay_level(None), 0)


# ---------- Экраны ----------

class FakeDB:
    """Заглушка Database для панелей: хранит вызовы и отдаёт заранее заданные данные."""

    def __init__(self):
        self.calls = []
        self.staff = [
            {"id": "a", "full_name": "Админ Иван", "role": "admin", "employee_code": "1001", "is_active": True},
            {"id": "c", "full_name": "Кассир Мария", "role": "cashier", "employee_code": "1002", "is_active": True},
            {"id": "k", "full_name": "Повар Олег", "role": "kitchen", "employee_code": "1003", "is_active": False}]
        self.shift = None
        self.orders = [{"id": 5, "created_at": "11.11.2026 10:00", "created_raw": "2026-11-11T05:00:00+00:00",
                        "cashier_name": "Мария", "subtotal": D("4000.00"), "service_percent": D("10.00"),
                        "service_amount": D("400.00"), "total": D("4400.00"), "status": "accepted", "voided": False,
                        "voided_at": "", "void_reason": "", "voided_by_name": "—",
                        "items": [{"id": 1, "item_name": "Бургер", "unit_price": D("3000.00"), "quantity": 1,
                                   "line_total": D("3000.00")}]}]

    def load_staff(self):
        self.calls.append("load_staff")
        return [dict(s) for s in self.staff]

    def update_staff(self, staff_id, name, role, code, active):
        self.calls.append(("update_staff", staff_id, name, role, code, active))
        for s in self.staff:
            if s["id"] == staff_id:
                s.update(full_name=name, role=role, employee_code=code, is_active=active)
        return {}

    def create_staff(self, name, role, code, password):
        self.calls.append(("create_staff", name, role, code, password))
        self.staff.append({"id": "n", "full_name": name, "role": role, "employee_code": code, "is_active": True})
        return {"full_name": name, "employee_code": code}

    def current_shift(self):
        return self.shift

    def shift_history(self, limit=100):
        return [self.shift] if self.shift else []

    def open_shift(self, text):
        self.calls.append(("open_shift", text))
        self.shift = {**dbm.Database._shift(SHIFT), "closed_at": None}
        return self.shift

    def load_orders(self, **filters):
        self.calls.append(("load_orders", filters))
        return [dict(o) for o in self.orders]

    def void_order(self, order_id, reason):
        self.calls.append(("void_order", order_id, reason))
        for o in self.orders:
            if o["id"] == order_id:
                o.update(voided=True, void_reason=reason, voided_at="11.11.2026 11:00", voided_by_name="Админ")
        return {"order_id": order_id, "voided": True, "changed": True}

    def load_report(self, start, end):
        self.calls.append(("load_report", start, end))
        return {"orders_count": 1, "subtotal_total": D("4000.00"), "service_total": D("400.00"),
                "revenue": D("4400.00"), "average_check": D("4400.00"), "voided_count": 0,
                "voided_total": D("0.00"), "top_items": [{"name": "Бургер", "quantity": 1, "revenue": D("3000.00")}],
                "categories": [{"category": "Еда", "quantity": 1, "revenue": D("3000.00")}],
                "cashiers": [{"name": "Мария", "orders": 1, "revenue": D("4400.00"), "service": D("400.00")}],
                "daily": [{"day": "2026-11-11", "orders": 1, "revenue": D("4400.00")}]}

    def load_menu(self):
        return []

    def load_all_menu(self):
        return [{"id": 1, "code": 1, "name": "Бургер", "category": "Еда", "price": D("3000.00"),
                 "is_available": True, "station": "kitchen", "status": "on_sale"}]

    def probe_features(self):
        return {"11 Надбавка": True, "12 Смены": False}


class ScreensTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        for module in (ui_base, ui_orders):
            patcher = mock.patch.object(module.threading, "Thread", SyncThread)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.db = FakeDB()

    def pump(self):
        self.root.update()

    def test_staff_search_filter_and_actions(self):
        panel = ui_staff.StaffPanel(self.root, self.db, on_auth_error=mock.Mock())
        panel.pack()
        self.addCleanup(panel.destroy)
        self.pump()
        self.assertEqual(len(panel.tree.get_children()), 3)
        panel.search_var.set("мария")
        self.assertEqual([panel.staff[i]["full_name"] for i in panel.tree.get_children()], ["Кассир Мария"])
        panel.search_var.set("")
        panel.role_box.set("Кухня")
        panel._render()
        self.assertEqual(panel.tree.get_children(), ("k",))
        self.assertEqual(str(panel.toggle_btn["state"]), "disabled")      # ничего не выбрано
        panel.tree.selection_set("k")
        self.pump()                                                        # <<TreeviewSelect>> приходит из цикла событий
        self.assertEqual(panel.toggle_btn["text"], "Активировать")
        panel.toggle_active()                                              # активация без вопроса
        self.pump()
        self.assertIn(("update_staff", "k", "Повар Олег", "kitchen", "1003", True), self.db.calls)
        self.assertEqual(panel.staff["k"]["is_active"], True)
        self.assertIn("активирован", panel.status["text"])

    def test_staff_disable_asks_first(self):
        panel = ui_staff.StaffPanel(self.root, self.db, on_auth_error=mock.Mock())
        panel.pack()
        self.addCleanup(panel.destroy)
        self.pump()
        panel.tree.selection_set("c")
        self.pump()
        with mock.patch.object(ui_staff.messagebox, "askyesno", return_value=False) as ask:
            panel.toggle_active()
        ask.assert_called_once()
        self.assertFalse([c for c in self.db.calls if c and c[0] == "update_staff"])
        with mock.patch.object(ui_staff.messagebox, "askyesno", return_value=True):
            panel.toggle_active()
        self.pump()
        self.assertIn(("update_staff", "c", "Кассир Мария", "cashier", "1002", False), self.db.calls)
        self.assertIn("отключён", panel.status["text"])

    def test_shift_open_flow(self):
        from ui_shift import ShiftPanel
        panel = ShiftPanel(self.root, self.db, on_auth_error=mock.Mock())
        panel.pack()
        self.addCleanup(panel.destroy)
        self.pump()
        self.assertIsNone(panel.shift)
        panel.opening_entry.delete(0, "end")
        panel.opening_entry.insert(0, "20000")
        panel.open_shift()
        self.pump()
        self.assertIn(("open_shift", "20000"), self.db.calls)
        self.assertIsNotNone(panel.shift)
        self.assertEqual(len(panel.tree.get_children()), 1)
        self.assertIn("Смена открыта", panel.status["text"])

    def test_orders_filters_and_void(self):
        panel = ui_orders.OrdersPanel(self.root, self.db, on_auth_error=mock.Mock())
        panel.pack()
        self.addCleanup(panel.destroy)
        self.pump()
        self.assertEqual(self.db.calls[-1], ("load_orders", {}))           # без фильтров — без параметров
        panel.number_entry.insert(0, "5")
        panel.date_entry.insert(0, "11.11.2026")
        panel.status_box.set("Принят")
        panel.void_box.set("Без аннулированных")
        panel.reload()
        self.pump()
        _name, filters = self.db.calls[-1]
        self.assertEqual((filters["order_id"], filters["status"], filters["voided"]), (5, "accepted", False))
        self.assertEqual(filters["date_to"] - filters["date_from"], timedelta(days=1))
        panel.number_entry.delete(0, "end")
        panel.number_entry.insert(0, "abc")
        panel.reload()
        self.assertIn("Номер заказа", panel.status["text"])
        panel.reset_filters()
        self.pump()
        # карточка: подытог, надбавка, итог
        panel.tree.selection_set("5")
        self.pump()
        self.assertEqual((panel.detail_sums["subtotal"]["text"], panel.detail_sums["service"]["text"],
                          panel.detail_sums["total"]["text"]), ("4 000 ₸", "400 ₸", "4 400 ₸"))
        self.assertEqual(str(panel.void_btn["state"]), "normal")
        # аннулирование: причина обязательна
        with mock.patch.object(ui_orders, "FormDialog") as dialog:
            panel.void_order()
        submit = dialog.call_args.args[3]
        self.assertIn("Причина", submit({"reason": "  "}))
        self.assertFalse([c for c in self.db.calls if c[0] == "void_order"])
        self.assertIsNone(submit({"reason": "Ошибка кассира"}))
        self.pump()
        self.assertIn(("void_order", 5, "Ошибка кассира"), self.db.calls)
        self.assertEqual(panel.tree.item("5", "values")[-1], "Аннулирован")
        self.assertIn("Ошибка кассира", panel.detail_void["text"])
        self.assertEqual(str(panel.void_btn["state"]), "disabled")          # повторно нельзя
        self.assertEqual(str(panel.next_btn["state"]), "disabled")

    def test_admin_shell_opens_every_section(self):
        profile = {"id": "a", "full_name": "Админ Иван", "role": "admin"}
        shell = ui_shell.AdminShell(self.root, self.db, profile, on_logout=mock.Mock(), server_host="demo.supabase.co")
        shell.pack(fill="both", expand=True)
        self.addCleanup(shell.destroy)
        self.pump()
        errors = []
        self.root.report_callback_exception = lambda *a: errors.append(a)
        for key, _title in ui_shell.SECTIONS:
            shell.show(key)
            self.pump()
            self.assertEqual(shell.current, key)
            self.assertEqual(shell.nav[key]["style"], "NavActive.TButton")
        # касса сохраняется при переключении разделов
        cash = shell.cash
        shell.show("stats")
        shell.show("cash")
        self.assertIs(shell.cash, cash)
        self.assertEqual(errors, [])

    def test_settings_probe_and_dashboard_partial_failure(self):
        shell = ui_shell.AdminShell(self.root, self.db, {"id": "a", "full_name": "А", "role": "admin"},
                                    on_logout=mock.Mock())
        shell.pack(fill="both", expand=True)
        self.addCleanup(shell.destroy)
        self.pump()
        # главная: отчёта нет (миграция 14), остальное показывается
        orig = self.db.load_report
        self.db.load_report = mock.Mock(side_effect=dbm.AppError("Отчёты не настроены"))
        shell.show("settings")
        shell.panel.check()
        self.pump()
        self.assertIn("не применена", " ".join(w["text"] for w in shell.panel.result_box.winfo_children()))
        self.db.load_report = mock.Mock(side_effect=dbm.AppError("Отчёты не настроены"))
        shell.current = None
        shell.show("dashboard")
        self.pump()
        self.assertIn("Отчёты не настроены", shell.panel.status["text"])
        self.assertEqual(len(shell.panel.tree.get_children()), 1)           # последние заказы всё равно есть
        self.db.load_report = orig


if __name__ == "__main__":
    unittest.main()
