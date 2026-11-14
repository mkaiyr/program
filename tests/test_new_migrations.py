"""Статический разбор миграций 11–14 (SQL здесь не выполняется; на PostgreSQL их проверяют sql/tests/1x_*_checks.sql).

Правила проекта (docs/DEVELOPMENT.md): одна явная транзакция, предусловия до изменений, функции с
search_path = '', права только authenticated, таблицы закрыты для клиентов, новые коды ошибок — в db.RPC_ERRORS.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402

FILES = sorted((ROOT / "sql").glob("1[1-4]_*.sql"))
PUBLIC_FUNCTIONS = {
    "11_service_charge_void.sql": ["place_order", "create_order", "void_order"],
    "12_cash_shifts.sql": ["shift_current", "shift_open", "shift_cash_move", "shift_close", "shift_history"],
    "13_staff_management.sql": ["admin_staff_list", "admin_create_staff", "admin_update_staff",
                                "admin_set_staff_password"],
    "14_reports_categories.sql": ["admin_report", "admin_rename_category"],
}


def strip_comments(sql):
    return re.sub(r"--[^\n]*", "", sql)


class MigrationRulesTest(unittest.TestCase):
    def test_files_exist(self):
        self.assertEqual([f.name for f in FILES], sorted(PUBLIC_FUNCTIONS))

    def test_single_transaction_and_precondition_first(self):
        for f in FILES:
            sql = strip_comments(f.read_text(encoding="utf-8"))
            with self.subTest(file=f.name):
                self.assertEqual(len(re.findall(r"^begin;", sql, re.M)), 1)
                self.assertEqual(len(re.findall(r"^commit;", sql, re.M)), 1)
                first_change = min(i for i in (sql.find("alter table"), sql.find("create table"),
                                               sql.find("create or replace function")) if i >= 0)
                self.assertLess(sql.index("ОСТАНОВЛЕНО"), first_change)

    def test_every_function_is_safe(self):
        for f in FILES:
            sql = strip_comments(f.read_text(encoding="utf-8"))
            for match in re.finditer(r"create or replace function public\.(\w+)\(.*?\$\$;", sql, re.S):
                text = match.group(0)
                with self.subTest(file=f.name, function=match.group(1)):
                    self.assertIn("set search_path = ''", text)

    def test_client_functions_grants(self):
        for name, functions in PUBLIC_FUNCTIONS.items():
            sql = strip_comments((ROOT / "sql" / name).read_text(encoding="utf-8"))
            for fn in functions:
                with self.subTest(file=name, function=fn):
                    self.assertRegex(sql, rf"revoke all on function public\.{fn}\([^)]*\) from public, anon;")
                    self.assertRegex(sql, rf"grant execute on function public\.{fn}\([^)]*\) to authenticated;")
                    self.assertIn("security definer", re.search(
                        rf"create or replace function public\.{fn}\(.*?\$\$;", sql, re.S).group(0))

    def test_admin_functions_check_admin(self):
        checks = {"13_staff_management.sql": ("admin_staff_list", "admin_create_staff", "admin_update_staff",
                                               "admin_set_staff_password"),
                  "14_reports_categories.sql": ("admin_report", "admin_rename_category")}
        for name, functions in checks.items():
            sql = strip_comments((ROOT / "sql" / name).read_text(encoding="utf-8"))
            for fn in functions:
                body = re.search(rf"create or replace function public\.{fn}\(.*?\$\$;", sql, re.S).group(0)
                with self.subTest(function=fn):
                    self.assertTrue("is_active_admin()" in body or "_staff_admin_only()" in body)

    def test_new_tables_closed_to_clients(self):
        sql = strip_comments((ROOT / "sql" / "12_cash_shifts.sql").read_text(encoding="utf-8"))
        for table in ("cash_shifts", "cash_movements"):
            self.assertIn(f"alter table public.{table} enable row level security;", sql)
        self.assertIn("revoke all on public.cash_shifts, public.cash_movements from public, anon, authenticated;", sql)
        self.assertNotIn("create policy", sql)

    def test_shift_balance_formula(self):
        sql = strip_comments((ROOT / "sql" / "12_cash_shifts.sql").read_text(encoding="utf-8"))
        self.assertIn("s.opening_cash + t.sales_total + t.deposits_total - t.withdrawals_total", sql)
        self.assertIn("o.voided_at is null", sql)

    def test_staff_cannot_lock_out_admin_and_no_delete(self):
        sql = strip_comments((ROOT / "sql" / "13_staff_management.sql").read_text(encoding="utf-8"))
        for code in ("SELF_LOCKOUT", "LAST_ADMIN", "EMPLOYEE_CODE_TAKEN", "WEAK_PASSWORD"):
            self.assertIn(code, sql)
        self.assertNotRegex(sql, r"delete\s+from\s+(public\.profiles|auth\.users)")

    def test_report_excludes_voided_and_uses_order_prices(self):
        sql = strip_comments((ROOT / "sql" / "14_reports_categories.sql").read_text(encoding="utf-8"))
        self.assertIn("o.voided_at is null", sql)
        self.assertIn("i.line_total", sql)
        self.assertNotIn("m.price", sql)   # цены — из заказов, не из текущего меню


class ErrorCodesTest(unittest.TestCase):
    def test_every_raised_code_has_text(self):
        for f in FILES:
            sql = f.read_text(encoding="utf-8")
            codes = set(re.findall(r"message = '([A-Z_]+)'", sql))
            for code in codes:
                with self.subTest(file=f.name, code=code):
                    self.assertIn(code, dbm.RPC_ERRORS)


if __name__ == "__main__":
    unittest.main()
