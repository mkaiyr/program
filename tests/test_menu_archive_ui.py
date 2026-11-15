"""Архив блюд в интерфейсе: «Управление меню» (вкладки, подтверждения), редактор, категории, касса.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v
Без подключения к Supabase: экраны работают с заглушкой Database. Права и правила архива проверяет сервер
(sql/tests/15_menu_item_archive_checks.sql); здесь проверяется, что интерфейс вызывает методы db.py и
правильно реагирует на ответы и отказы.
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import ui_admin  # noqa: E402
import ui_admin_panels as panels  # noqa: E402
import ui_base  # noqa: E402
import ui_order  # noqa: E402
from _tk import get_root  # noqa: E402

D = Decimal


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def item(item_id, name, category, status="on_sale", archived=False, code=None):
    return {"id": item_id, "code": code or 1000 + item_id, "name": name, "category": category, "price": D("500.00"),
            "is_available": status == "on_sale", "status": status, "station": "kitchen", "reject_reason": None,
            "archived": archived, "archived_at": "2026-11-14T10:00:00+00:00" if archived else None}


class FakeDB:
    """Меню администратора + поведение серверных функций архива (в объёме, нужном экрану)."""

    def __init__(self):
        self.menu = [item(1, "Борщ", "Супы"), item(2, "Солянка", "Супы", "out_of_stock"),
                     item(3, "Стейк", "Горячие блюда"), item(4, "Новинка", "Горячие блюда", "pending"),
                     item(5, "Окрошка", "Супы", "out_of_stock", archived=True),
                     item(6, "Тирамису", "Десерты", "out_of_stock", archived=True)]
        self.calls = []
        self.fail = {}          # метод -> AppError
        self.orders_count = 3

    def load_categories(self, lenient=False):  # справочник категорий (миграция 16)
        return []

    def load_all_menu(self):
        self.calls.append("load_all_menu")
        return [dict(i) for i in self.menu]

    def _find(self, item_id):
        return next(i for i in self.menu if i["id"] == item_id)

    def archive_menu_item(self, item_id):
        self.calls.append(("archive", item_id))
        if "archive" in self.fail:
            raise self.fail["archive"]
        row = self._find(item_id)
        row.update(archived=True, archived_at="2026-11-14T11:00:00+00:00", is_available=False, status="out_of_stock")
        return {"id": item_id, "name": row["name"], "archived": True, "changed": True}

    def restore_menu_item(self, item_id):
        self.calls.append(("restore", item_id))
        if "restore" in self.fail:
            raise self.fail["restore"]
        row = self._find(item_id)
        row.update(archived=False, archived_at=None, is_available=True, status="on_sale")
        return {"id": item_id, "name": row["name"], "archived": False, "changed": True}

    def count_menu_item_orders(self, item_id):
        self.calls.append(("count", item_id))
        if "count" in self.fail:
            raise self.fail["count"]
        return self.orders_count

    def purge_menu_item(self, item_id):
        self.calls.append(("purge", item_id))
        if "purge" in self.fail:
            raise self.fail["purge"]
        row = self._find(item_id)
        self.menu.remove(row)
        return {"id": item_id, "name": row["name"], "purged": True, "orders_count": self.orders_count}

    def rename_category(self, old, new):
        return {"updated": 1}


def patch_threads():
    for module in (ui_base, ui_admin, ui_order):
        mock.patch.object(module.threading, "Thread", SyncThread).start()


class MenuSectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        patch_threads()
        self.addCleanup(mock.patch.stopall)
        self.db = FakeDB()
        self.changed = mock.Mock()
        self.panel = panels.MenuSectionPanel(self.root, self.db, on_auth_error=mock.Mock(), on_menu_changed=self.changed)
        self.panel.pack(fill="both", expand=True)
        self.addCleanup(self.panel.destroy)
        self.pump()

    def pump(self):
        for _ in range(5):
            self.root.update()

    def ids(self, tree):
        return sorted(int(i) for i in tree.get_children())

    def select(self, tab, item_id):
        self.panel.notebook.select(tab)
        self.pump()
        tree = self.panel.archive_tree if tab == 1 else self.panel.active_tree
        tree.selection_set(str(item_id))
        self.pump()

    def states(self):
        return {n: str(getattr(self.panel, f"{n}_btn")["state"]) for n in ("archive", "restore", "purge")}

    def test_two_tabs_split_active_and_archive(self):
        tabs = [self.panel.notebook.tab(i, "text") for i in range(2)]
        self.assertEqual(tabs, ["Активные блюда", "Архив"])
        self.assertEqual(self.ids(self.panel.active_tree), [1, 2, 3, 4])
        self.assertEqual(self.ids(self.panel.archive_tree), [5, 6])
        self.assertIn("Активных: 4", self.panel.counts_label["text"])
        self.assertIn("в архиве: 2", self.panel.counts_label["text"])

    def test_search_and_category_filters_work_in_both_tabs(self):
        self.panel.category_box.set("Супы")
        self.panel._render()
        self.assertEqual(self.ids(self.panel.active_tree), [1, 2])
        self.assertEqual(self.ids(self.panel.archive_tree), [5])
        self.panel.search_var.set("борщ")
        self.pump()
        self.assertEqual(self.ids(self.panel.active_tree), [1])
        self.assertEqual(self.ids(self.panel.archive_tree), [])
        self.assertIn("Ничего не найдено", self.panel.archive_empty["text"])
        self.panel.category_box.set("Все категории")
        self.panel.search_var.set("1001")                                   # поиск по коду
        self.pump()
        self.assertEqual(self.ids(self.panel.active_tree), [1])

    def test_empty_archive_message(self):
        self.db.menu = [i for i in self.db.menu if not i["archived"]]
        self.panel.reload()
        self.pump()
        self.assertEqual(self.ids(self.panel.archive_tree), [])
        self.assertIn("пока ничего нет", self.panel.archive_empty["text"])

    def test_buttons_follow_tab_and_selection(self):
        self.assertEqual(self.states(), {"archive": "disabled", "restore": "disabled", "purge": "disabled"})
        self.select(0, 1)
        self.assertEqual(self.states()["archive"], "normal")
        self.select(0, 4)                                                   # ждёт решения — архивировать нельзя
        self.assertEqual(self.states()["archive"], "disabled")
        self.select(1, 5)
        self.assertEqual(self.states(), {"archive": "disabled", "restore": "normal", "purge": "normal"})

    def test_archive_asks_first_and_calls_db_once(self):
        self.select(0, 1)
        with mock.patch.object(panels.messagebox, "askyesno", return_value=False) as ask:
            self.panel.archive()
        self.assertIn("Борщ", ask.call_args.args[1])
        self.assertNotIn(("archive", 1), self.db.calls)
        with mock.patch.object(panels.messagebox, "askyesno", return_value=True):
            self.panel.archive()
        self.pump()
        self.assertEqual(self.db.calls.count(("archive", 1)), 1)
        self.assertEqual(self.ids(self.panel.active_tree), [2, 3, 4])      # исчез из активных
        self.assertEqual(self.ids(self.panel.archive_tree), [1, 5, 6])     # появился в архиве
        self.assertIn("перенесено в архив", self.panel.status["text"])
        self.changed.assert_called()

    def test_restore_returns_item_to_active_menu(self):
        self.select(1, 5)
        self.panel.restore()
        self.pump()
        self.assertIn(("restore", 5), self.db.calls)
        self.assertIn(5, self.ids(self.panel.active_tree))
        self.assertNotIn(5, self.ids(self.panel.archive_tree))
        self.assertIn("снова в продаже", self.panel.status["text"])

    def test_purge_confirmation_shows_name_count_and_warning(self):
        self.select(1, 5)
        with mock.patch.object(panels.messagebox, "askyesno", return_value=False) as ask:
            self.panel.purge()
            self.pump()
        title, text = ask.call_args.args[:2]
        self.assertIn("Окрошка", text)
        self.assertIn("заказах: 3", text)
        self.assertIn("НЕОБРАТИМО", text)
        self.assertEqual(ask.call_args.kwargs["default"], "no")            # по умолчанию — «Нет»
        self.assertEqual(ask.call_args.kwargs["icon"], "warning")
        self.assertIn(("count", 5), self.db.calls)
        self.assertNotIn(("purge", 5), self.db.calls)                       # отказ — блюдо не тронуто
        self.assertIn(5, self.ids(self.panel.archive_tree))

    def test_purge_confirmed_removes_from_archive(self):
        self.select(1, 5)
        with mock.patch.object(panels.messagebox, "askyesno", return_value=True):
            self.panel.purge()
            self.pump()
        self.assertEqual(self.db.calls.count(("purge", 5)), 1)
        self.assertEqual(self.ids(self.panel.archive_tree), [6])
        self.assertNotIn(5, self.ids(self.panel.active_tree))
        self.assertIn("удалено навсегда", self.panel.status["text"])
        self.assertIn("Заказов с ним: 3", self.panel.status["text"])

    def test_purge_of_unsold_item_says_so(self):
        self.db.orders_count = 0
        self.select(1, 6)
        with mock.patch.object(panels.messagebox, "askyesno", return_value=False) as ask:
            self.panel.purge()
            self.pump()
        self.assertIn("не встречается", ask.call_args.args[1])

    def test_purge_is_aborted_if_order_count_unknown(self):
        self.db.fail["count"] = dbm.AppError("Нет связи с сервером. Проверьте интернет.", "network")
        self.select(1, 5)
        with mock.patch.object(panels.messagebox, "askyesno") as ask, \
                mock.patch.object(ui_base.messagebox, "showerror"):
            self.panel.purge()
            self.pump()
        ask.assert_not_called()
        self.assertNotIn(("purge", 5), self.db.calls)

    def test_server_refusal_is_shown_and_list_reloaded(self):
        self.db.fail["archive"] = dbm.AppError("Изменять меню может только администратор.", "forbidden")
        self.select(0, 1)
        loads = self.db.calls.count("load_all_menu")
        with mock.patch.object(panels.messagebox, "askyesno", return_value=True):
            self.panel.archive()
        self.pump()
        self.assertIn("только администратор", self.panel.status["text"])    # сообщение не затёрто перезагрузкой
        self.assertEqual(self.db.calls.count("load_all_menu"), loads + 1)
        self.assertEqual(self.ids(self.panel.active_tree), [1, 2, 3, 4])

    def test_before_migration_15_shows_message_and_app_survives(self):
        self.db.fail["archive"] = dbm.AppError("Архив блюд не настроен на сервере (не выполнена миграция 15). "
                                               "Обратитесь к администратору.")
        self.select(0, 1)
        with mock.patch.object(panels.messagebox, "askyesno", return_value=True):
            self.panel.archive()
        self.pump()
        self.assertIn("миграция 15", self.panel.status["text"])
        self.assertTrue(self.panel.winfo_exists())
        self.assertEqual(self.ids(self.panel.active_tree), [1, 2, 3, 4])

    def test_auth_error_logs_out(self):
        self.db.fail["restore"] = dbm.AppError("Сессия истекла. Войдите снова.", "auth")
        self.select(1, 5)
        with mock.patch.object(ui_base.messagebox, "showerror"):
            self.panel.restore()
            self.pump()
        self.panel.on_auth_error.assert_called_once()


class EditorHidesArchiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        patch_threads()
        self.addCleanup(mock.patch.stopall)
        self.db = FakeDB()
        self.win = ui_admin.MenuAdminWindow(self.root, self.db, on_close=lambda: None, on_auth_error=lambda: None)
        self.addCleanup(lambda: self.win.winfo_exists() and self.win.destroy())
        for _ in range(5):
            self.root.update()

    def test_archived_items_are_not_editable_or_sellable_here(self):
        self.assertEqual(sorted(self.win.items), [1, 2, 3, 4])
        self.assertEqual(sorted(int(i) for i in self.win.tree.get_children()), [1, 2, 3, 4])
        self.assertNotIn("Окрошка", " ".join(str(self.win.tree.item(i, "values")) for i in self.win.tree.get_children()))
        self.assertIn("Позиций: 4", self.win.status["text"])            # счёт без архивных

    def test_search_cannot_find_archived_item(self):
        self.win.search_var.set("Окрошка")
        for _ in range(3):
            self.root.update()
        self.assertEqual(self.win.tree.get_children(), ())


class CategoriesCountersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        patch_threads()
        self.addCleanup(mock.patch.stopall)
        self.db = FakeDB()
        self.panel = panels.CategoriesPanel(self.root, self.db, on_auth_error=mock.Mock())
        self.panel.pack()
        self.addCleanup(self.panel.destroy)
        for _ in range(3):
            self.root.update()

    def rows(self):
        return {self.panel.tree.item(i, "values")[0]: tuple(int(v) for v in self.panel.tree.item(i, "values")[1:])
                for i in self.panel.tree.get_children()}

    def test_active_sale_and_archived_counts_are_separate(self):
        # (активных, в продаже, в архиве)
        self.assertEqual(self.panel.tree["columns"], ("name", "active", "sale", "archived"))
        self.assertEqual(self.rows(), {"Супы": (2, 1, 1), "Горячие блюда": (2, 1, 0), "Десерты": (0, 0, 1)})

    def test_category_with_only_archived_items_is_still_listed_and_renamable(self):
        self.assertIn("Десерты", self.rows())
        self.panel.tree.selection_set("Десерты")
        with mock.patch.object(panels, "FormDialog") as dialog:
            self.panel.rename()
        submit = dialog.call_args.args[3]
        self.assertIsNone(submit({"name": "Сладкое"}))                     # переименование доступно и для архивных
        for _ in range(3):
            self.root.update()
        self.assertIn("Десерты", self.panel.status["text"])


# ---------- Касса ----------

CASH_MENU = [{"id": 1, "code": 1, "name": "Борщ", "category": "Супы", "price": D("500.00")},
             {"id": 2, "code": 2, "name": "Солянка", "category": "Супы", "price": D("600.00")},
             {"id": 3, "code": 3, "name": "Стейк", "category": "Горячие блюда", "price": D("2500.00")},
             {"id": 4, "code": 4, "name": "Кола", "category": "Напитки", "price": D("400.00")}]


class CashDB:
    def __init__(self):
        self.menu = [dict(i) for i in CASH_MENU]

    def load_categories(self, lenient=False):  # справочник категорий (миграция 16)
        return []

    def load_menu(self):
        return [dict(i) for i in self.menu]


class CashCategoriesTest(unittest.TestCase):
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

    def tile_ids(self):
        return sorted(self.frame.tiles)

    def chips(self):
        return [w["text"] for w in self.frame.chips.winfo_children()]

    def choose(self, category):
        self.frame.category_var.set(category)
        self.frame._render_catalog()
        self.pump()

    def test_chips_come_from_real_menu_data(self):
        self.assertEqual(self.chips(), ["Все", "Горячие блюда", "Напитки", "Супы"])
        self.db.menu = [i for i in self.db.menu if i["category"] != "Напитки"]
        self.frame.reload_menu()
        self.pump()
        self.assertEqual(self.chips(), ["Все", "Горячие блюда", "Супы"])   # список не зашит в код

    def test_category_shows_only_its_dishes(self):
        self.assertEqual(self.tile_ids(), [1, 2, 3, 4])
        self.choose("Супы")
        self.assertEqual(self.tile_ids(), [1, 2])
        self.choose("Все")
        self.assertEqual(self.tile_ids(), [1, 2, 3, 4])

    def test_switching_category_keeps_cart_untouched(self):
        f = self.frame
        f.add_item(1)
        f.add_item(1)
        f.add_item(3)
        before = ({k: dict(v) for k, v in f.cart.items()}, f.total_label["text"], f.request_id)
        for category in ("Напитки", "Супы", "Горячие блюда", "Все"):
            self.choose(category)
        after = ({k: dict(v) for k, v in f.cart.items()}, f.total_label["text"], f.request_id)
        self.assertEqual(before, after)
        self.assertEqual(f.cart[1]["qty"], 2)
        self.assertEqual(f.subtotal_label["text"], "3 500 ₸")               # 2×500 + 2500
        self.assertEqual(f.total_label["text"], "3 850 ₸")                  # + надбавка 10%

    def test_search_does_not_clear_cart_and_works_with_category(self):
        f = self.frame
        f.add_item(4)
        f.search_var.set("о")                                               # любой поиск — корзина не затрагивается
        self.pump()
        self.assertEqual(f.cart[4]["qty"], 1)
        self.choose("Супы")
        f.search_var.set("соля")
        self.pump()
        self.assertEqual(self.tile_ids(), [2])                              # поиск внутри выбранной категории
        f.search_var.set("стейк")
        self.pump()
        self.assertEqual(self.tile_ids(), [])                               # «Стейк» не из «Супы»
        self.assertEqual(f.cart[4]["qty"], 1)                               # корзина цела

    def test_empty_category_message(self):
        self.choose("Десерты")                                              # категории нет среди доступных блюд
        labels = [w["text"] for w in self.frame.catalog.inner.winfo_children()]
        self.assertEqual(labels, ["В категории «Десерты» нет доступных блюд"])
        self.choose("Супы")
        self.frame.search_var.set("пицца")
        self.pump()
        labels = [w["text"] for w in self.frame.catalog.inner.winfo_children()]
        self.assertEqual(labels, ["В категории «Супы» ничего не найдено"])
        self.choose("Все")
        labels = [w["text"] for w in self.frame.catalog.inner.winfo_children()]
        self.assertEqual(labels, ["Ничего не найдено"])                     # поиск без категории — прежний текст

    def test_many_categories_wrap_instead_of_overflowing(self):
        """Восемь и более категорий не должны уходить за край колонки каталога: кнопки переносятся на строки."""
        self.db.menu = [{"id": i, "code": i, "name": f"Блюдо {i}", "category": name, "price": D("100.00")}
                        for i, name in enumerate(["Супы", "Горячие блюда", "Салаты", "Гарниры", "Выпечка", "Напитки",
                                                  "Десерты"], 1)]
        self.frame.reload_menu()
        self.pump()
        buttons = self.frame.chips.winfo_children()
        self.assertEqual(len(buttons), 8)                                    # «Все» + 7 категорий
        for width, min_rows in ((300, 3), (450, 2), (5000, 1)):
            with mock.patch.object(self.frame.chips, "winfo_width", return_value=width):
                self.frame._reflow_chips()
            rows = {}
            for b in buttons:
                info = b.grid_info()
                rows.setdefault(int(info["row"]), []).append(b.winfo_reqwidth() + 6)
            self.assertGreaterEqual(len(rows), min_rows)
            for row, widths in rows.items():
                self.assertTrue(len(widths) == 1 or sum(widths) <= width, (width, row, widths))
        # перенос не ломает выбор категории
        self.choose("Супы")
        self.assertEqual(self.tile_ids(), [1])

    def test_dish_archived_after_adding_to_cart_keeps_other_lines(self):
        """Блюдо архивировали, пока оно лежало в корзине: сервер ответит MENU_CHANGED, касса уберёт только его."""
        f = self.frame
        f.add_item(1)
        f.add_item(4)
        self.db.menu = [i for i in self.db.menu if i["id"] != 1]            # архивное блюдо больше не приходит с сервера
        f.reload_menu()
        self.pump()
        self.assertEqual(sorted(f.cart), [4])                               # остальное в корзине сохранилось
        self.assertEqual(f.total_label["text"], "440 ₸")
        self.assertIn("снята с продажи", f.status["text"])


if __name__ == "__main__":
    unittest.main()
