"""Тесты наличия блюд (кухня) и каталога кассы (без подключения к Supabase).

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Права на стороне базы проверяет sql/tests/06_kitchen_menu_checks.sql.
"""

import re
import sys
import tkinter as tk
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

import db as dbm  # noqa: E402
import ui_kitchen  # noqa: E402
import ui_order  # noqa: E402
import ui_theme  # noqa: E402
from _tk import get_root  # noqa: E402

MIGRATION = (ROOT / "sql" / "06_kitchen_menu.sql").read_text(encoding="utf-8")


def api_error(message, code="P0001"):
    return APIError({"message": message, "code": code, "details": "", "hint": ""})


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class MigrationTest(unittest.TestCase):
    def test_no_table_rights_or_policies(self):
        """Кухне не выдаются права на таблицы и не меняется RLS."""
        self.assertNotRegex(MIGRATION, r"(?i)grant\s+(select|insert|update|delete|all)\s+on\s+(table\s+)?public\.")
        self.assertNotRegex(MIGRATION, r"(?i)create policy|alter policy|disable row level security")

    def test_functions_check_kitchen_role(self):
        for name in ("kitchen_menu", "kitchen_set_menu_item_available"):
            body = re.search(rf"function public\.{name}\(.*?\n\$\$;", MIGRATION, re.S).group(0)
            self.assertIn("security definer", body)
            self.assertIn("set search_path = ''", body)
            self.assertIn("public.is_active_kitchen()", body)
            self.assertNotIn("price", body.split("as $$", 1)[1], f"{name} не должна касаться цены")

    def test_only_availability_is_updated(self):
        updates = re.findall(r"update public\.menu_items set (.*?)\n", MIGRATION)
        self.assertEqual(updates, ["is_available = p_available"])

    def test_execute_not_for_anon(self):
        for sig in ("kitchen_menu()", "kitchen_set_menu_item_available(bigint, boolean)"):
            self.assertIn(f"revoke all on function public.{sig} from public, anon;", MIGRATION)
            self.assertIn(f"grant execute on function public.{sig} to authenticated;", MIGRATION)


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db = dbm.Database.__new__(dbm.Database)
        self.db.client = mock.MagicMock()
        self.rpc = self.db.client.rpc

    def test_load_kitchen_menu(self):
        rows = [{"id": 1, "name": "Суп", "category": "Еда", "is_available": False}]
        self.rpc.return_value.execute.return_value.data = rows
        self.assertEqual(self.db.load_kitchen_menu(), rows)
        self.rpc.assert_called_with("kitchen_menu", {})

    def test_kitchen_set_available(self):
        self.rpc.return_value.execute.return_value.data = {"id": 1, "name": "Суп", "is_available": False}
        self.db.kitchen_set_available(1, False)
        self.rpc.assert_called_with("kitchen_set_menu_item_available", {"p_id": 1, "p_available": False})

    def test_wrong_answer_is_not_success(self):
        self.rpc.return_value.execute.return_value.data = {"id": 1, "name": "Суп", "is_available": True}
        with self.assertRaises(dbm.AppError):
            self.db.kitchen_set_available(1, False)

    def test_errors(self):
        cases = [(api_error("NOT_ALLOWED", "42501"), "auth"), (api_error("NOT_FOUND", "P0002"), "invalid"),
                 (httpx.ConnectError("x"), "network"), (api_error("x", "PGRST202"), None)]
        for error, code in cases:
            self.rpc.return_value.execute.side_effect = error
            for call in (self.db.load_kitchen_menu, lambda: self.db.kitchen_set_available(1, True)):
                with self.assertRaises(dbm.AppError) as ctx:
                    call()
                self.assertEqual(ctx.exception.code, code, error)


class FakeDB:
    """Заглушка: общее меню для кассы и кухни, как в Supabase после RLS/RPC."""

    def __init__(self):
        self.items = {1: dict(id=1, name="Капучино", category="Напитки", price=Decimal("1100.00"), is_available=True),
                      2: dict(id=2, name="Круассан", category="Выпечка", price=Decimal("700.00"), is_available=True)}
        self.orders = []
        self.fail = None

    def _maybe_fail(self):
        if self.fail:
            error, self.fail = self.fail, None
            raise error

    def load_menu(self):  # касса: только доступные (так фильтрует сервер)
        self._maybe_fail()
        return [{k: v for k, v in i.items() if k != "is_available"} for i in self.items.values() if i["is_available"]]

    def load_kitchen_menu(self):  # кухня: без цен
        self._maybe_fail()
        return [{k: v for k, v in i.items() if k != "price"} for i in self.items.values()]

    def kitchen_set_available(self, item_id, available):
        self._maybe_fail()
        self.items[item_id]["is_available"] = available
        return {"id": item_id, "name": self.items[item_id]["name"], "is_available": available}

    def load_station_orders(self, station):
        return []

    def create_order(self, request_id, items):
        self._maybe_fail()
        for item_id, _qty, price in items:
            item = self.items[item_id]
            if not item["is_available"] or item["price"] != price:
                raise dbm.AppError("Меню изменилось: " + item["name"], "menu_changed")
        self.orders.append(items)
        total = sum(price * qty for _i, qty, price in items)
        return {"order_id": len(self.orders), "total": total, "duplicate": False}


class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        self.shown = []
        self.confirm = True
        patches = [mock.patch.object(m.threading, "Thread", SyncThread) for m in (ui_kitchen, ui_order)]
        for name in ("showinfo", "showwarning", "showerror"):
            patches.append(mock.patch.object(ui_order.messagebox, name,
                                             lambda *a, _n=name, **k: self.shown.append((_n, a[0]))))
        patches.append(mock.patch.object(ui_order.messagebox, "askyesno", lambda *a, **k: self.confirm))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.db = FakeDB()

    def pump(self):
        for _ in range(5):
            self.root.update()

    def kitchen_menu(self):
        frame = ui_kitchen.KitchenFrame(self.root, self.db, {"full_name": "Повар", "role": "kitchen"},
                                        on_logout=mock.Mock())
        self.addCleanup(frame.destroy)
        frame.tabs.select(1)
        # В скрытом окне Tk не присылает <<NotebookTabChanged>>. Вызов сразу после
        # создания попадает на загрузку заказов и должен выполниться после неё.
        frame.reload_menu()
        self.assertTrue(frame.menu_pending)
        self.pump()
        self.assertEqual(sorted(frame.menu_buttons), sorted(self.db.items))
        return frame

    def cashier(self):
        frame = ui_order.OrderFrame(self.root, self.db, {"full_name": "Кассир", "role": "cashier"},
                                    on_logout=mock.Mock())
        self.addCleanup(frame.destroy)
        self.pump()
        return frame

    # ---------- Кухня ----------

    def test_kitchen_menu_toggle(self):
        frame = self.kitchen_menu()
        self.assertEqual(frame.menu_buttons[1]["text"], "Снять с продажи")
        frame.menu_buttons[1].invoke()
        self.pump()
        self.assertFalse(self.db.items[1]["is_available"])
        self.assertEqual(frame.menu_buttons[1]["text"], "Вернуть в продажу")
        self.assertIn("нет в наличии", frame.status["text"])
        frame.toggle_available(1)
        self.pump()
        self.assertTrue(self.db.items[1]["is_available"])

    def test_kitchen_menu_cancel_and_errors(self):
        frame = self.kitchen_menu()
        self.confirm = False
        frame.toggle_available(1)
        self.pump()
        self.assertTrue(self.db.items[1]["is_available"])
        self.confirm = True
        for error, title in [(dbm.AppError(dbm.NO_NETWORK, "network"), "showerror"),
                             (dbm.AppError("Позиция не найдена. Обновите список.", "invalid"), "showwarning")]:
            self.db.fail = error
            frame.toggle_available(1)
            self.pump()
            self.assertEqual(self.shown[-1][0], title)
            self.assertTrue(self.db.items[1]["is_available"])
            self.assertNotEqual(frame.status["foreground"], ui_theme.SUCCESS)
            self.assertFalse(frame.busy)

    def test_kitchen_menu_has_no_prices(self):
        frame = self.kitchen_menu()
        texts = [str(w["text"]) for w in _all_widgets(frame.menu_panel) if "text" in w.keys()]
        self.assertIn("Капучино", texts)
        self.assertFalse(any("₸" in t or "1100" in t for t in texts), texts)

    def test_kitchen_menu_filters_combine(self):
        self.db.items[3] = dict(id=3, name="Капучино большой", category="Напитки", price=Decimal("1300.00"),
                                is_available=False)
        frame = self.kitchen_menu()
        panel = frame.menu_panel
        self.assertEqual(sorted(frame.menu_buttons), [1, 2, 3])
        panel.category_var.set("Напитки")
        self.assertEqual(sorted(frame.menu_buttons), [1, 3])
        panel.availability_var.set("Нет в наличии")
        self.assertEqual(sorted(frame.menu_buttons), [3])
        panel.availability_var.set("В продаже")
        panel.search_var.set("КАП")
        self.pump()  # поиск перерисовывается отложенно (after_idle)
        self.assertEqual(sorted(frame.menu_buttons), [1])
        panel.search_var.set("круассан")  # есть, но не в категории «Напитки»
        self.pump()
        self.assertEqual(frame.menu_buttons, {})
        texts = [str(w["text"]) for w in _all_widgets(panel.list.inner) if "text" in w.keys()]
        self.assertIn("Блюда не найдены", texts)
        panel.reset_filters()
        self.assertEqual(sorted(frame.menu_buttons), [1, 2, 3])
        self.assertEqual(str(panel.reset_btn["state"]), "disabled")

    def test_kitchen_menu_card_updates_and_filters_kept(self):
        frame = self.kitchen_menu()
        panel = frame.menu_panel
        panel.availability_var.set("В продаже")
        frame.menu_buttons[1].invoke()   # снять с продажи (подтверждение — True)
        self.pump()
        self.assertFalse(self.db.items[1]["is_available"])
        self.assertEqual(panel.availability_var.get(), "В продаже")  # фильтр сохранён после обновления
        self.assertEqual(sorted(frame.menu_buttons), [2])            # карточка ушла из «В продаже»
        panel.availability_var.set("Нет в наличии")
        self.assertEqual(frame.menu_buttons[1]["text"], "Вернуть в продажу")
        self.assertIn("Показано 1 из 2", panel.counts["text"])

    def test_kitchen_menu_categories_from_data(self):
        frame = self.kitchen_menu()
        chips = [str(c["text"]) for c in frame.menu_panel.category_chips.winfo_children()]
        self.assertEqual(chips, ["Все", "Выпечка", "Напитки"])

    # ---------- Касса ----------

    def test_catalog_add_and_quantity(self):
        frame = self.cashier()
        self.assertEqual(sorted(frame.tiles), [1, 2])
        frame.tiles[1].invoke()
        frame.tiles[1].invoke()
        frame.tiles[2].invoke()
        self.assertEqual(frame.cart[1]["qty"], 2)
        self.assertEqual(frame.total_label["text"], ui_order.fmt_money(Decimal("2900.00")))
        frame.cart_tree.selection_set("1")
        frame.change_qty(-1)
        frame.change_qty(-1)  # 0 шт. — строка удаляется
        self.assertNotIn(1, frame.cart)
        self.assertEqual(frame.total_label["text"], ui_order.fmt_money(Decimal("700.00")))

    def test_category_filter(self):
        frame = self.cashier()
        frame.category_var.set("Выпечка")
        frame._render_catalog()
        self.assertEqual(list(frame.tiles), [2])

    def test_unavailable_dish_hidden_and_removed_from_cart(self):
        frame = self.cashier()
        frame.tiles[1].invoke()
        self.db.kitchen_set_available(1, False)   # кухня отметила «Нет в наличии»
        frame.reload_menu()
        self.pump()
        self.assertEqual(list(frame.tiles), [2])
        self.assertNotIn(1, frame.cart)
        self.assertIn("снята с продажи", frame.status["text"])

    def test_server_rejects_unavailable_dish(self):
        frame = self.cashier()
        frame.tiles[1].invoke()
        self.db.items[1]["is_available"] = False  # изменилось, пока касса не обновила меню
        frame.submit()
        self.pump()
        self.assertEqual(self.db.orders, [])
        self.assertEqual(self.shown[0][0], "showwarning")
        self.assertNotIn(1, frame.tiles)          # меню перезагружено
        self.assertNotIn(1, frame.cart)

    def test_submit_and_network_error(self):
        frame = self.cashier()
        frame.tiles[2].invoke()
        self.db.fail = dbm.AppError("Нет связи с сервером.", "network")
        frame.submit()
        self.pump()
        self.assertEqual(self.shown[-1][0], "showerror")
        self.assertIn(2, frame.cart)              # заказ не потерян
        frame.submit()
        self.pump()
        self.assertEqual(self.db.orders, [[(2, 1, Decimal("700.00"))]])
        self.assertEqual(frame.cart, {})


def _all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from _all_widgets(child)


if __name__ == "__main__":
    unittest.main()
