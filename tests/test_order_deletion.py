"""Удаление заказов: «Удалить», «Удалённые заказы», восстановление, окончательное удаление.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Без подключения к Supabase: у Database подменён клиент (MagicMock), окна работают с
заглушкой, которая ведёт себя как функции sql/10_order_deletion.sql (права, только
выданные, повторы без изменений). Сами функции и RLS на PostgreSQL проверяет
sql/tests/10_order_deletion_checks.sql; здесь — текст миграции.
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
import ui_deleted_orders  # noqa: E402
import ui_orders  # noqa: E402
from _tk import get_root  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

MIGRATION = (ROOT / "sql" / "10_order_deletion.sql").read_text(encoding="utf-8")


def api_error(message, code="P0001"):
    return APIError({"message": message, "code": code, "details": "", "hint": ""})


# ---------- Database ----------

class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db = dbm.Database.__new__(dbm.Database)
        self.db.client = mock.MagicMock()
        self.rpc = self.db.client.rpc

    def answer(self, data):
        self.rpc.return_value.execute.side_effect = None
        self.rpc.return_value.execute.return_value.data = data

    def test_rpc_names_and_params(self):
        self.answer({"order_id": 5, "deleted": True, "changed": True})
        self.assertTrue(self.db.delete_order(5)["changed"])
        self.rpc.assert_called_with("delete_order", {"p_order_id": 5})
        self.answer({"order_id": 5, "deleted": False, "changed": False})
        self.assertFalse(self.db.restore_order(5)["changed"])
        self.rpc.assert_called_with("admin_restore_order", {"p_order_id": 5})
        self.answer({"order_id": 5, "purged": True, "changed": True, "items_deleted": 2})
        self.assertEqual(self.db.purge_order(5)["items_deleted"], 2)
        self.rpc.assert_called_with("admin_purge_order", {"p_order_id": 5})

    def test_unexpected_answers(self):
        cases = [
            (self.db.delete_order, {"order_id": 6, "deleted": True, "changed": True}),   # чужой номер
            (self.db.delete_order, {"order_id": 5, "deleted": False, "changed": True}),
            (self.db.restore_order, {"order_id": 5, "deleted": True, "changed": True}),
            (self.db.purge_order, {"order_id": 5, "purged": True, "changed": True}),     # нет items_deleted
            (self.db.purge_order, {"order_id": 5, "purged": True, "changed": "yes", "items_deleted": 1}),
            (self.db.delete_order, None),
        ]
        for call, data in cases:
            with self.subTest(data=data):
                self.answer(data)
                with self.assertRaises(dbm.AppError):
                    call(5)

    def test_deleted_orders_and_log(self):
        self.answer([{"id": 7, "created_at": "2026-10-30T09:00:00+00:00", "total": 3600, "status": "completed",
                      "cashier_name": "Кассир", "deleted_at": "2026-10-30T10:00:00+00:00", "deleted_by_name": "Кассир",
                      "items": [{"id": 2, "item_name": "Круассан", "unit_price": 700, "quantity": 2, "line_total": 1400},
                                {"id": 1, "item_name": "Капучино", "unit_price": 1100, "quantity": 2,
                                 "line_total": 2200}]}])
        (order,) = self.db.load_deleted_orders()
        self.rpc.assert_called_with("admin_deleted_orders", {})
        self.assertEqual(order["total"], Decimal("3600.00"))
        self.assertEqual([i["item_name"] for i in order["items"]], ["Капучино", "Круассан"])
        self.answer([{"order_id": 7, "total": "3600.00", "cashier_name": "Кассир", "order_created_at": None,
                      "purged_by_name": "Админ", "purged_at": "2026-10-30T11:00:00+00:00"}])
        (row,) = self.db.load_purge_log()
        self.rpc.assert_called_with("admin_purge_log", {})
        self.assertEqual(row["purged_by_name"], "Админ")
        self.answer([{"id": 7, "total": 1, "items": [{"item_name": "Суп"}]}])  # позиция без количества
        with self.assertRaises(dbm.AppError):
            self.db.load_deleted_orders()

    def test_server_errors(self):
        for message, code in (("ORDER_NOT_COMPLETED", "order_changed"), ("ORDER_NOT_DELETED", "order_changed"),
                              ("ORDER_NOT_FOUND", "order_changed"), ("ADMIN_REQUIRED", "forbidden"),
                              ("NOT_ALLOWED", "auth")):
            with self.subTest(message):
                self.rpc.return_value.execute.side_effect = api_error(message)
                with self.assertRaises(dbm.AppError) as ctx:
                    self.db.purge_order(5)
                self.assertEqual(ctx.exception.code, code)
                self.assertNotIn(message, str(ctx.exception))

    def test_before_migration_10(self):
        self.rpc.return_value.execute.side_effect = api_error("Could not find the function", "PGRST202")
        for call in (lambda: self.db.delete_order(5), self.db.load_deleted_orders, self.db.load_purge_log):
            with self.assertRaises(dbm.AppError) as ctx:
                call()
            self.assertIn("не настроено на сервере", str(ctx.exception))


# ---------- Заглушка сервера ----------

class FakeServer:
    """Как функции миграции 10: история без удалённых, права по роли, повторы без изменений."""

    def __init__(self, role="cashier", user="cashier"):
        self.role, self.user = role, user
        self.orders = {}
        for oid, status, owner in ((1, "completed", "cashier"), (2, "ready", "cashier"),
                                   (3, "completed", "other"), (4, "completed", "cashier")):
            self.orders[oid] = {"id": oid, "created_at": "30.10.2026 09:00", "cashier_name": owner,
                                "total": Decimal("1400.00"), "status": status, "owner": owner, "deleted": False,
                                "items": [{"id": oid, "item_name": "Круассан", "unit_price": Decimal("700.00"),
                                           "quantity": 2, "line_total": Decimal("1400.00")}]}
        self.purge_log = []
        self.calls = []

    def _admin(self):
        if self.role != "admin":
            raise dbm.AppError(dbm.RPC_ERRORS["ADMIN_REQUIRED"][1], "forbidden")

    def load_orders(self):  # RLS: удалённые не видны, кассиру — только свои
        return [dict(o) for o in self.orders.values()
                if not o["deleted"] and (self.role == "admin" or o["owner"] == self.user)]

    def delete_order(self, order_id):
        self.calls.append(("delete", order_id))
        order = self.orders.get(order_id)
        if order is None or (self.role == "cashier" and order["owner"] != self.user):
            raise dbm.AppError(dbm.RPC_ERRORS["ORDER_NOT_FOUND"][1], "order_changed")
        if order["status"] != "completed":
            raise dbm.AppError(dbm.RPC_ERRORS["ORDER_NOT_COMPLETED"][1], "order_changed")
        changed, order["deleted"] = not order["deleted"], True
        return {"order_id": order_id, "deleted": True, "changed": changed}

    def load_deleted_orders(self):
        self._admin()
        return [dict(o, deleted_at="30.10.2026 10:00", deleted_by_name="Кассир")
                for o in self.orders.values() if o["deleted"]]

    def load_purge_log(self):
        self._admin()
        return list(self.purge_log)

    def restore_order(self, order_id):
        self._admin()
        self.calls.append(("restore", order_id))
        changed, self.orders[order_id]["deleted"] = self.orders[order_id]["deleted"], False
        return {"order_id": order_id, "deleted": False, "changed": changed}

    def purge_order(self, order_id):
        self._admin()
        self.calls.append(("purge", order_id))
        if order_id not in self.orders:
            return {"order_id": order_id, "purged": True, "changed": False, "items_deleted": 0}
        if not self.orders[order_id]["deleted"]:
            raise dbm.AppError(dbm.RPC_ERRORS["ORDER_NOT_DELETED"][1], "order_changed")
        order = self.orders.pop(order_id)
        self.purge_log.append({"order_id": order_id, "total": order["total"], "cashier_name": order["cashier_name"],
                               "order_created_at": order["created_at"], "purged_by_name": "Админ",
                               "purged_at": "30.10.2026 11:00"})
        return {"order_id": order_id, "purged": True, "changed": True, "items_deleted": len(order["items"])}


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


# ---------- Окна ----------

class WindowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        self.shown = []
        self.yes = mock.Mock(return_value=True)
        self.typed = mock.Mock(return_value=None)
        for module in (ui_orders, ui_deleted_orders):
            mock.patch.object(module.threading, "Thread", SyncThread).start()
            mock.patch.object(module.messagebox, "askyesno", self.yes).start()
            for name in ("showinfo", "showwarning", "showerror"):
                mock.patch.object(module.messagebox, name,
                                  lambda *a, _n=name, **k: self.shown.append((_n, a[0]))).start()
        mock.patch.object(ui_deleted_orders.simpledialog, "askstring", self.typed).start()
        self.addCleanup(mock.patch.stopall)

    def pump(self):
        for _ in range(5):
            self.root.update()

    def orders_window(self, server):
        win = ui_orders.OrdersWindow(self.root, server, role=server.role, on_auth_error=mock.Mock())
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        self.pump()
        return win

    def select(self, win, order_id):
        win.tree.selection_set(str(order_id))
        self.pump()

    def history(self, win):
        return sorted(int(i) for i in win.tree.get_children())

    def test_cashier_deletes_own_completed_order(self):
        """Сценарии 1, 2: кассир отправляет свой выданный заказ в удалённые — он исчезает из истории."""
        server = FakeServer()
        win = self.orders_window(server)
        self.assertIsNone(win.deleted_btn)                # раздела «Удалённые заказы» у кассира нет
        self.assertEqual(self.history(win), [1, 2, 4])    # чужой заказ 3 кассиру не виден
        self.select(win, 2)
        self.assertEqual(str(win.delete_btn["state"]), "disabled")  # не выдан — удалять нельзя
        self.select(win, 1)
        self.assertEqual(str(win.delete_btn["state"]), "normal")
        self.yes.return_value = False                     # «Нет» в подтверждении
        win.delete_btn.invoke()
        self.assertEqual(server.calls, [])
        self.yes.return_value = True
        win.delete_btn.invoke()
        self.pump()
        self.assertEqual(server.calls, [("delete", 1)])
        self.assertEqual(self.history(win), [2, 4])
        self.assertIn("«Удалённые заказы»", win.status["text"])

    def test_cashier_cannot_restore_or_purge(self):
        """Сценарий 6: даже прямой вызов окна — отказ сервера, данные не меняются."""
        server = FakeServer()
        server.orders[1]["deleted"] = True
        win = ui_deleted_orders.DeletedOrdersWindow(self.root, server, on_change=mock.Mock(),
                                                    on_auth_error=mock.Mock())
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        self.pump()
        self.assertEqual(self.shown, [("showerror", "Нет прав")])  # один отказ, без повторов по кругу
        self.assertFalse(win.winfo_exists())                       # окно закрыто
        self.assertTrue(server.orders[1]["deleted"])

    def test_admin_restore_and_purge(self):
        """Сценарии 3, 4, 5, 7, 8, 10 на стороне окна."""
        server = FakeServer(role="admin", user="admin")
        orders = self.orders_window(server)
        self.select(orders, 3)                            # администратор удаляет любой выданный
        orders.delete_btn.invoke()
        self.pump()
        self.select(orders, 4)
        orders.delete_btn.invoke()
        self.pump()
        self.assertEqual(self.history(orders), [1, 2])

        orders.deleted_btn.invoke()
        self.pump()
        deleted = orders.deleted_window
        self.assertEqual(sorted(deleted.orders), [3, 4])  # сценарий 3
        deleted.tree.selection_set("3")
        self.pump()
        texts = [deleted.items_tree.item(i)["values"][0] for i in deleted.items_tree.get_children()]
        self.assertEqual(texts, ["Круассан"])             # состав виден

        deleted.restore()                                 # сценарии 4, 5
        self.pump()
        self.assertEqual(sorted(deleted.orders), [4])
        self.assertEqual(self.history(orders), [1, 2, 3])  # окно «Заказы» обновилось само

        deleted.tree.selection_set("4")
        self.pump()
        self.yes.return_value = False                     # предупреждение: «Нет»
        deleted.purge()
        self.typed.return_value = "3"                     # «Да», но номер не тот
        self.yes.return_value = True
        deleted.purge()
        self.assertEqual([c for c in server.calls if c[0] == "purge"], [])
        self.assertIn(("showwarning", "Не удалено"), self.shown)
        self.typed.return_value = " 4 "                   # сценарий 7: верный номер
        deleted.purge()
        self.pump()
        self.assertNotIn(4, server.orders)                # сценарий 8: стёрт
        self.assertEqual(deleted.orders, {})
        log = [deleted.log_tree.item(i)["values"][0] for i in deleted.log_tree.get_children()]
        self.assertEqual(log, [4])
        self.assertIn("удалён навсегда", deleted.status["text"])
        self.assertEqual(self.history(orders), [1, 2, 3])  # другие заказы на месте

    def test_repeats_do_not_break(self):
        """Сценарий 10: повторное удаление и восстановление — сообщение, а не ошибка."""
        server = FakeServer(role="admin", user="admin")
        self.assertTrue(server.delete_order(1)["changed"])
        self.assertFalse(server.delete_order(1)["changed"])
        win = ui_deleted_orders.DeletedOrdersWindow(self.root, server, on_change=mock.Mock(),
                                                    on_auth_error=mock.Mock())
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        self.pump()
        win._restored(1, server.restore_order(1))
        self.pump()
        win._restored(1, server.restore_order(1))
        self.pump()
        self.assertIn("уже был в истории", win.status["text"])
        self.assertEqual([s for s in self.shown if s[0] == "showerror"], [])

    def test_purge_of_not_deleted_refused(self):
        """Сценарий 9: стереть можно только заказ из удалённых — не случайный из истории."""
        server = FakeServer(role="admin", user="admin")
        with self.assertRaises(dbm.AppError) as ctx:
            server.purge_order(1)
        self.assertEqual(ctx.exception.code, "order_changed")
        self.assertIn(1, server.orders)


# ---------- Текст миграции 10 ----------

class MigrationTextTest(unittest.TestCase):
    FUNCTIONS = ("delete_order", "admin_deleted_orders", "admin_restore_order", "admin_purge_order",
                 "admin_purge_log")

    def body(self, name):
        match = re.search(rf"create or replace function public\.{name}\(.*?\n\$\$;", MIGRATION, re.S)
        self.assertIsNotNone(match, name)
        return match.group(0)

    def test_functions_are_protected(self):
        for name in self.FUNCTIONS:
            with self.subTest(name):
                body = self.body(name)
                self.assertIn("security definer", body)
                self.assertIn("set search_path = ''", body)
                self.assertRegex(MIGRATION, rf"revoke all on function public\.{name}\([^)]*\) from public, anon;")
                if name.startswith("admin_"):
                    self.assertIn("is_active_admin()", body)

    def test_delete_rules(self):
        delete = self.body("delete_order")
        self.assertIn("p.role in ('cashier', 'admin')", delete)          # кухня и бар — нет
        self.assertIn("v_role = 'cashier' and v_owner <> v_uid", delete)  # чужой заказ кассира
        self.assertIn("v_status <> 'completed'", delete)                 # только выданный
        purge = self.body("admin_purge_order")
        self.assertIn("v_order.deleted_at is null", purge)               # только из удалённых
        self.assertIn("delete from public.orders where id = p_order_id", purge)
        self.assertIn("insert into public.order_purge_log", purge)
        self.assertIn("check (deleted_at is null or status = 'completed')", MIGRATION)

    def test_purge_log_closed_and_readonly(self):
        self.assertIn("alter table public.order_purge_log enable row level security", MIGRATION)
        self.assertIn("revoke all on public.order_purge_log from public, anon, authenticated", MIGRATION)
        self.assertNotRegex(MIGRATION, r"create policy \w+ on public\.order_purge_log")
        self.assertIn("before update or delete on public.order_purge_log", MIGRATION)
        self.assertIn("before truncate on public.order_purge_log", MIGRATION)

    def test_history_policies_hide_deleted(self):
        for policy in ("orders_select_own", "orders_select_admin", "order_items_select_own",
                       "order_items_select_admin"):
            with self.subTest(policy):
                match = re.search(rf"create policy {policy} on .*?;", MIGRATION, re.S)
                self.assertIsNotNone(match)
                self.assertIn("deleted_at is null", match.group(0))

    def test_does_not_redefine_older_functions(self):
        """Функции 01–09 не переопределяются: повторный запуск 08/09 не откатит миграцию 10."""
        older = "".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "sql").glob("0[1-9]_*.sql")))
        defined_before = set(re.findall(r"create or replace function public\.(\w+)\(", older, re.I))
        defined_here = set(re.findall(r"create or replace function public\.(\w+)\(", MIGRATION, re.I))
        self.assertEqual(defined_before & defined_here, set())


if __name__ == "__main__":
    unittest.main()
