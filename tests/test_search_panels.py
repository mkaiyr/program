"""Поиск по коду и названию в кассе и в меню кухни (без подключения к Supabase).

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v
"""

import sys
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import ui_kitchen  # noqa: E402
import ui_order  # noqa: E402
from _tk import get_root  # noqa: E402


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def make_items(count):
    names = ["Капучино", "Американо", "Круассан", "Сэндвич", "Чай чёрный"]
    cats = ["Напитки", "Напитки", "Выпечка", "Еда", "Напитки"]
    return {i: dict(id=i, code=1000 + i, name=f"{names[i % 5]} {i}" if i > 5 else names[i - 1],
                    category=cats[(i - 1) % 5], price=Decimal("500.00") + i, is_available=True)
            for i in range(1, count + 1)}


class FakeDB:
    def __init__(self, count=5):
        self.items = make_items(count)
        self.items[3]["is_available"] = False  # «Круассан», код 1003 — нет в наличии
        self.orders = []

    def load_menu(self):  # касса: сервер отдаёт только доступные (RLS)
        return [{k: v for k, v in i.items() if k != "is_available"} for i in self.items.values() if i["is_available"]]

    def load_kitchen_menu(self):  # кухня: без цен
        return [{k: v for k, v in i.items() if k != "price"} for i in self.items.values()]

    def load_station_orders(self, station):
        return []

    def create_order(self, request_id, items):
        for item_id, _qty, price in items:
            item = self.items[item_id]
            if not item["is_available"] or item["price"] != price:
                raise dbm.AppError("Меню изменилось: " + item["name"], "menu_changed")
        self.orders.append(items)
        return {"order_id": len(self.orders), "total": sum(p * q for _i, q, p in items), "duplicate": False}


class PanelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        self.shown = []
        patches = [mock.patch.object(m.threading, "Thread", SyncThread) for m in (ui_order, ui_kitchen)]
        for name in ("showinfo", "showwarning", "showerror"):
            patches.append(mock.patch.object(ui_order.messagebox, name,
                                             lambda *a, _n=name, **k: self.shown.append((_n, a[0]))))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def pump(self):
        for _ in range(5):
            self.root.update()

    def cashier(self, db):
        frame = ui_order.OrderFrame(self.root, db, {"full_name": "Кассир", "role": "cashier"}, on_logout=mock.Mock())
        self.addCleanup(frame.destroy)
        self.pump()
        return frame

    def kitchen_panel(self, db):
        frame = ui_kitchen.KitchenFrame(self.root, db, {"full_name": "Повар", "role": "kitchen"},
                                        on_logout=mock.Mock())
        self.addCleanup(frame.destroy)
        self.pump()
        frame.reload_menu()
        self.pump()
        return frame.menu_panel

    # ---------- Касса ----------

    def test_cashier_tile_shows_code_and_search(self):
        frame = self.cashier(FakeDB())
        self.assertTrue(str(frame.tiles[1]["text"]).startswith("1001 · Капучино"))
        frame.search_var.set(" 1002 ")
        self.pump()
        self.assertEqual(list(frame.tiles), [2])
        frame.search_var.set("ЧАЙ")
        self.pump()
        self.assertEqual(list(frame.tiles), [5])

    def test_cashier_search_with_category(self):
        frame = self.cashier(FakeDB())
        frame.category_var.set("Напитки")
        frame.search_var.set("ано")  # «Американо» — напиток
        self.pump()
        self.assertEqual(list(frame.tiles), [2])
        frame.search_var.set("сэндвич")  # есть, но в категории «Еда»
        self.pump()
        self.assertEqual(frame.tiles, {})

    def test_cashier_enter_adds_by_exact_code(self):
        frame = self.cashier(FakeDB())
        frame.search_var.set("1004")
        frame.add_from_search()
        self.assertEqual(frame.cart[4]["qty"], 1)
        self.assertEqual(frame.search_var.get(), "")
        frame.search_var.set("1004")
        frame.add_from_search()
        self.assertEqual(frame.cart[4]["qty"], 2)  # прежняя логика количества

    def test_cashier_enter_single_name_match_and_ambiguous(self):
        frame = self.cashier(FakeDB())
        frame.search_var.set("америк")
        frame.add_from_search()
        self.assertIn(2, frame.cart)
        frame.search_var.set("100")  # несколько совпадений, точного кода нет
        frame.add_from_search()
        self.assertEqual(list(frame.cart), [2])
        self.assertIn("точный код", frame.status["text"])

    def test_cashier_missing_and_unavailable_code(self):
        frame = self.cashier(FakeDB())
        frame.search_var.set("9999")
        self.pump()
        self.assertEqual(frame.tiles, {})
        frame.add_from_search()
        self.assertEqual(frame.cart, {})
        self.assertEqual(frame.status["text"], "Ничего не найдено.")
        frame.search_var.set("1003")  # «Круассан» нет в наличии: сервер не прислал его кассе
        frame.add_from_search()
        self.assertEqual(frame.cart, {})

    def test_cashier_stale_catalog_rejected_by_server(self):
        db = FakeDB()
        frame = self.cashier(db)
        frame.search_var.set("1001")
        frame.add_from_search()
        db.items[1]["is_available"] = False  # сняли с продажи, каталог кассы устарел
        frame.submit()
        self.pump()
        self.assertEqual(db.orders, [])
        self.assertNotIn(1, frame.cart)

    def test_cashier_tile_limit_and_speed(self):
        frame = self.cashier(FakeDB(1000))
        self.assertEqual(len(frame.tiles), ui_order.TILE_LIMIT)
        start = time.perf_counter()
        frame.search_var.set("капучино")
        self.pump()
        elapsed = time.perf_counter() - start
        self.assertEqual(len(frame.tiles), ui_order.TILE_LIMIT)  # 200 капучино — показаны первые 60
        frame.search_var.set("1500")
        self.pump()
        self.assertEqual(list(frame.tiles), [500])
        self.assertLess(elapsed, 2.0, f"перерисовка {elapsed:.2f} с")

    # ---------- Кухня ----------

    def test_kitchen_search_by_code(self):
        panel = self.kitchen_panel(FakeDB())
        panel.search_var.set("1003")
        self.pump()
        self.assertEqual(list(panel.buttons), [3])
        texts = [str(w["text"]) for w in panel.list.inner.winfo_children()[0].winfo_children()]
        self.assertIn("Код 1003  ·  Выпечка", texts)
        panel.search_var.set("1003")
        panel.availability_var.set("В продаже")  # 1003 нет в наличии — вместе с фильтром ничего
        self.assertEqual(panel.buttons, {})

    def test_kitchen_card_limit(self):
        panel = self.kitchen_panel(FakeDB(300))
        self.assertEqual(len(panel.buttons), 100)
        panel.search_var.set("1250")
        self.pump()
        self.assertEqual(list(panel.buttons), [250])


if __name__ == "__main__":
    unittest.main()
