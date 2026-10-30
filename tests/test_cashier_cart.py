"""Касса: корзина, сумма, повторные нажатия и ввод по коду во время запроса.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Без подключения к Supabase: Database заменён заглушкой, сетевой поток —
синхронным вызовом или «задержанным» потоком, которым управляет тест.
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import ui_order  # noqa: E402
from _tk import get_root  # noqa: E402

MENU = [{"id": 1, "code": 1001, "name": "Капучино", "category": "Напитки", "price": Decimal("1100.00")},
        {"id": 2, "code": 1002, "name": "Круассан", "category": "Выпечка", "price": Decimal("700.50")}]


class HeldThread:
    """Поток, который не стартует сам: тест решает, когда «ответит сервер»."""
    started = []

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        HeldThread.started.append(self.target)


class FakeDB:
    def __init__(self):
        self.orders = []          # (request_id, items) каждого вызова create_order
        self.order_fail = None

    def load_menu(self):
        return [dict(item) for item in MENU]

    def create_order(self, request_id, items):
        self.orders.append((request_id, list(items)))
        if self.order_fail:
            raise self.order_fail
        total = sum(price * qty for _id, qty, price in items)
        return {"order_id": len(self.orders), "total": total, "duplicate": False}


class CartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        HeldThread.started.clear()
        self.shown = []
        self.callback_errors = []
        patches = [mock.patch.object(ui_order.threading, "Thread", HeldThread),
                   mock.patch.object(ui_order.messagebox, "askyesno", return_value=True)]
        for name in ("showinfo", "showwarning", "showerror"):
            patches.append(mock.patch.object(ui_order.messagebox, name,
                                             lambda *a, _n=name, **k: self.shown.append((_n, a[0]))))
        patches.append(mock.patch.object(self.root, "report_callback_exception",
                                         lambda *exc: self.callback_errors.append(exc[1])))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.db = FakeDB()
        self.frame = ui_order.OrderFrame(self.root, self.db, {"full_name": "Кассир", "role": "cashier"},
                                         on_logout=mock.Mock())
        self.addCleanup(lambda: self.frame.winfo_exists() and self.frame.destroy())
        self.answer()  # меню загружено

    def pump(self):
        for _ in range(5):
            self.root.update()

    def answer(self):
        HeldThread.started.pop(0)()
        self.pump()

    def qty(self):
        return {item_id: line["qty"] for item_id, line in self.frame.cart.items()}

    def total_text(self):
        return self.frame.total_label["text"]

    def select(self, item_id):
        self.frame.cart_tree.selection_set(str(item_id))

    # ---------- Корзина и сумма ----------

    def test_add_change_remove_and_total(self):
        f = self.frame
        f.add_item(1)
        f.add_item(1)
        f.add_item(2)
        self.assertEqual(self.qty(), {1: 2, 2: 1})
        self.assertEqual(self.total_text(), ui_order.fmt_money(Decimal("2900.50")))
        self.select(1)
        f.change_qty(-1)
        f.change_qty(-1)                 # 0 шт. — строка удаляется
        self.assertEqual(self.qty(), {2: 1})
        self.assertEqual(self.total_text(), ui_order.fmt_money(Decimal("700.50")))
        self.select(2)
        f.remove_item()
        self.assertEqual(self.qty(), {})
        self.assertEqual(self.total_text(), ui_order.fmt_money(Decimal("0")))
        self.assertEqual(self.callback_errors, [])

    def test_quantity_limit(self):
        f = self.frame
        f.add_item(1)
        f.cart[1]["qty"] = ui_order.MAX_QTY
        f.add_item(1)
        self.assertEqual(self.qty(), {1: ui_order.MAX_QTY})
        self.assertIn(("showwarning", "Количество"), self.shown)

    def test_clear_cart(self):
        f = self.frame
        f.add_item(1)
        f.clear_cart()
        self.assertEqual(self.qty(), {})
        self.assertEqual(self.total_text(), ui_order.fmt_money(Decimal("0")))

    def test_empty_order_not_sent(self):
        self.frame.submit()
        self.assertEqual(HeldThread.started, [])
        self.assertEqual(self.frame.status["text"], "Заказ пуст.")

    # ---------- Повторные нажатия и повтор после ошибки связи ----------

    def test_double_submit_sends_once(self):
        f = self.frame
        f.add_item(1)
        f.submit()
        f.submit()                       # второе нажатие, пока ждём ответа
        self.assertEqual(len(HeldThread.started), 1)
        self.answer()
        self.assertEqual(len(self.db.orders), 1)
        self.assertEqual(self.qty(), {})

    def test_cart_locked_while_submitting(self):
        f = self.frame
        f.add_item(1)
        f.submit()
        f.add_item(2)
        self.select(1)
        f.change_qty(1)
        f.remove_item()
        self.assertEqual(self.qty(), {1: 1})      # отправляется ровно то, что было в корзине
        self.answer()
        self.assertEqual(self.db.orders[0][1], [(1, 1, Decimal("1100.00"))])

    def test_retry_after_network_error_reuses_request_id(self):
        f = self.frame
        f.add_item(1)
        self.db.order_fail = dbm.AppError("Нет связи", "network")
        f.submit()
        self.answer()
        self.assertEqual(self.qty(), {1: 1})      # корзина не потеряна
        self.db.order_fail = None
        f.submit()
        self.answer()
        first, second = self.db.orders
        self.assertEqual(first[0], second[0])     # тот же ID: сервер не создаст второй заказ

    def test_changed_cart_gets_new_request_id(self):
        f = self.frame
        f.add_item(1)
        self.db.order_fail = dbm.AppError("Нет связи", "network")
        f.submit()
        self.answer()
        f.add_item(2)                             # заказ изменён — это новый запрос
        self.db.order_fail = None
        f.submit()
        self.answer()
        self.assertNotEqual(self.db.orders[0][0], self.db.orders[1][0])

    # ---------- Заказ без ответа сервера и изменённая корзина (возможный дубль) ----------

    def network_failure(self):
        """Заказ из позиции 1 отправлен, ответа нет: он мог сохраниться на сервере."""
        self.frame.add_item(1)
        self.db.order_fail = dbm.AppError("Нет связи", "network")
        self.frame.submit()
        self.answer()
        self.db.order_fail = None
        ui_order.messagebox.askyesno.reset_mock()  # вопрос «Очистить…» и т. п. не в счёт

    def asked_unconfirmed(self):
        return [c for c in ui_order.messagebox.askyesno.call_args_list
                if c.args[0] == "Предыдущий заказ не подтверждён"]

    def test_changed_cart_after_network_error_asks_first(self):
        self.network_failure()
        self.frame.add_item(2)                    # корзина изменена — это был бы второй заказ
        ui_order.messagebox.askyesno.return_value = False
        self.frame.submit()
        self.assertEqual(len(self.asked_unconfirmed()), 1)
        self.assertEqual(HeldThread.started, [])  # отказ — ничего не отправлено
        self.assertEqual(len(self.db.orders), 1)

        ui_order.messagebox.askyesno.return_value = True
        self.frame.submit()                       # кассир проверил «Заказы» и подтвердил
        self.answer()
        self.assertEqual(len(self.db.orders), 2)
        self.frame.add_item(1)
        self.frame.submit()                       # после успешного заказа вопрос не повторяется
        self.answer()
        self.assertEqual(len(self.asked_unconfirmed()), 2)  # отказ и подтверждение — и всё

    def test_unchanged_retry_does_not_ask(self):
        self.network_failure()
        self.frame.submit()                       # тот же ID: сервер вернёт уже сохранённый заказ
        self.answer()
        self.assertEqual(self.asked_unconfirmed(), [])
        self.assertEqual(self.db.orders[0][0], self.db.orders[1][0])

    def test_server_refusal_for_same_id_clears_warning(self):
        """Сервер ответил отказом на повтор с тем же ID — заказа с этим ID нет, предупреждать не о чем."""
        self.network_failure()
        self.db.order_fail = dbm.AppError("Меню изменилось", "menu_changed")
        self.frame.submit()
        self.answer()
        self.answer()                             # касса сама перезагрузила меню
        self.db.order_fail = None
        self.frame.add_item(2)
        self.frame.submit()
        self.answer()
        self.assertEqual(self.asked_unconfirmed(), [])

    # ---------- Ввод по коду (Enter) ----------

    def test_enter_by_code_adds(self):
        f = self.frame
        f.search_var.set("1002")
        f.add_from_search()
        self.assertEqual(self.qty(), {2: 1})
        self.assertEqual(f.search_var.get(), "")
        self.assertEqual(f.status["text"], "Добавлено: Круассан")

    def test_enter_by_code_while_submitting(self):
        """Пока оформляется заказ, Enter не меняет корзину и не сообщает «Добавлено»."""
        f = self.frame
        f.add_item(1)
        f.submit()
        self.assertTrue(f.busy)
        f.search_var.set("1001")
        f.add_from_search()
        self.assertEqual(self.qty(), {1: 1})
        self.assertNotIn("Добавлено", f.status["text"])
        self.assertEqual(f.search_var.get(), "1001")  # введённый код не потерян — Enter после ответа
        self.answer()
        f.add_from_search()
        self.assertEqual(self.qty(), {1: 1})          # новый заказ: позиция добавлена в пустую корзину
        self.assertEqual(f.status["text"], "Добавлено: Капучино")


if __name__ == "__main__":
    unittest.main()
