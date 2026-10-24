"""Автообновление экранов кухни и бара и звуковой сигнал о новых заказах.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Без подключения к Supabase: источник заказов — заглушка, звук подменён.
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402
import ui_kitchen  # noqa: E402
from _tk import get_root  # noqa: E402


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class HeldThread:
    """Поток, который не стартует сам: тест решает, когда «ответит сервер»."""
    started = []

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        HeldThread.started.append(self.target)


def order(order_id, status="accepted", name="Капучино"):
    return {"id": order_id, "time": "09:00", "status": status,
            "items": [{"code": 1000 + order_id, "item_name": name, "quantity": 1}]}


class FakeStationDB:
    def __init__(self, orders):
        self.orders = list(orders)
        self.loads = []
        self.fail = None

    def load_station_orders(self, station):
        self.loads.append(station)
        if self.fail:
            error, self.fail = self.fail, None
            raise error
        return [dict(o) for o in self.orders]


class SeenOrdersTest(unittest.TestCase):
    def test_only_new_ids_returned(self):
        seen = ui_kitchen.SeenOrders()
        self.assertEqual(seen.add_new([3, 1, 2]), [3, 1, 2])
        self.assertEqual(seen.add_new([1, 2, 4, 4]), [4])
        self.assertEqual(seen.add_new([]), [])
        self.assertEqual(seen.add_new([3]), [])  # исчез и вернулся — не новый

    def test_memory_is_bounded(self):
        seen = ui_kitchen.SeenOrders(limit=3)
        seen.add_new([1, 2, 3, 4, 5])
        self.assertEqual(len(seen), 3)
        self.assertNotIn(1, seen)
        self.assertIn(5, seen)


class PlayAlertTest(unittest.TestCase):
    def test_sound_failure_is_ignored(self):
        widget = mock.Mock()
        widget.bell.side_effect = RuntimeError("нет звука")
        with mock.patch.object(ui_kitchen.sys, "platform", "darwin"):
            ui_kitchen.play_alert(widget)  # не падает
        widget.bell.assert_called_once()

    def test_windows_uses_winsound(self):
        fake = types.SimpleNamespace(MB_ICONASTERISK=64, MessageBeep=mock.Mock())
        widget = mock.Mock()
        with mock.patch.object(ui_kitchen.sys, "platform", "win32"), mock.patch.dict(sys.modules, {"winsound": fake}):
            ui_kitchen.play_alert(widget)
        fake.MessageBeep.assert_called_once_with(64)
        widget.bell.assert_not_called()


class RefreshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        self.shown = []
        self.callback_errors = []
        patches = [mock.patch.object(ui_kitchen.threading, "Thread", SyncThread),
                   mock.patch.object(ui_kitchen, "play_alert")]
        for name in ("showinfo", "showwarning", "showerror"):
            patches.append(mock.patch.object(ui_kitchen.messagebox, name,
                                             lambda *a, _n=name, **k: self.shown.append((_n, a[0]))))
        patches.append(mock.patch.object(self.root, "report_callback_exception",
                                         lambda *exc: self.callback_errors.append(exc[1])))
        self.alerts = None
        for p in patches:
            started = p.start()
            if p.attribute == "play_alert":
                self.alerts = started
            self.addCleanup(p.stop)

    def pump(self):
        for _ in range(5):
            self.root.update()

    def screen(self, db, role="kitchen"):
        cls = ui_kitchen.BarFrame if role == "bar" else ui_kitchen.KitchenFrame
        frame = cls(self.root, db, {"full_name": "Сотрудник", "role": role}, on_logout=mock.Mock())
        self.addCleanup(lambda: frame.closed or frame.destroy())
        self.pump()
        return frame

    @staticmethod
    def fire_timer(frame):
        """Срабатывание таймера автообновления: сработавший таймер уходит из очереди Tk.

        Прямой вызов _auto_refresh без этого оставил бы настоящий таймер ждать в очереди,
        а после закрытия экрана он ссылался бы на удалённую команду.
        """
        if frame.refresh_job is not None:
            frame.after_cancel(frame.refresh_job)
        frame._auto_refresh()

    def refresh(self, frame):
        self.fire_timer(frame)
        self.pump()

    def test_interval_is_7_seconds(self):
        self.assertEqual(ui_kitchen.AUTO_REFRESH_MS, 7000)
        frame = self.screen(FakeStationDB([]))
        with mock.patch.object(frame, "after", wraps=frame.after) as after:
            self.refresh(frame)
        after.assert_any_call(7000, frame._auto_refresh)
        self.assertIsNotNone(frame.refresh_job)

    def test_first_load_is_silent(self):
        frame = self.screen(FakeStationDB([order(1), order(2)]))
        self.assertEqual(sorted(frame.badges), [1, 2])
        self.alerts.assert_not_called()

    def test_new_order_beeps_once(self):
        db = FakeStationDB([order(1)])
        frame = self.screen(db)
        db.orders.append(order(2))
        self.refresh(frame)
        self.alerts.assert_called_once_with(frame)
        self.assertIn("№ 2", frame.status["text"])
        self.refresh(frame)  # тот же список ещё раз
        self.refresh(frame)
        self.alerts.assert_called_once()

    def test_status_change_is_not_new(self):
        db = FakeStationDB([order(1), order(2)])
        frame = self.screen(db)
        db.orders[0]["status"] = "preparing"
        self.refresh(frame)
        self.alerts.assert_not_called()
        self.assertEqual(frame.badges[1]["text"], "Готовится")  # список обновился

    def test_disappeared_and_returned_is_not_new(self):
        db = FakeStationDB([order(1), order(2)])
        frame = self.screen(db)
        db.orders = [order(1)]
        self.refresh(frame)
        db.orders = [order(1), order(2, status="preparing")]
        self.refresh(frame)
        self.alerts.assert_not_called()

    def test_several_new_orders_single_sound(self):
        db = FakeStationDB([order(1)])
        frame = self.screen(db)
        db.orders += [order(4), order(5)]
        self.refresh(frame)
        self.alerts.assert_called_once()
        self.assertEqual(frame.status["text"], "Новые заказы: № 4, № 5")

    def test_empty_first_load_then_first_order_beeps(self):
        db = FakeStationDB([])
        frame = self.screen(db)
        db.orders = [order(7)]
        self.refresh(frame)
        self.alerts.assert_called_once()

    def test_network_error_during_auto_refresh(self):
        db = FakeStationDB([order(1)])
        frame = self.screen(db)
        frame.search_var.set("капуч")
        db.fail = dbm.AppError(dbm.NO_NETWORK, "network")
        self.refresh(frame)
        self.assertEqual(self.shown, [])                    # без всплывающих окон
        self.assertIn("Повторим автоматически", frame.status["text"])
        self.assertIsNotNone(frame.refresh_job)            # следующая попытка запланирована
        self.assertFalse(frame.busy)
        self.assertEqual(sorted(frame.badges), [1])        # прежний список на месте
        self.assertEqual(frame.search_var.get(), "капуч")  # ввод пользователя не сброшен
        db.orders.append(order(2))
        self.refresh(frame)
        self.alerts.assert_called_once()                   # после восстановления — сигнал о новом
        self.assertNotIn("Повторим", frame.status["text"])

    def test_error_before_first_success_keeps_silent_baseline(self):
        db = FakeStationDB([order(1), order(2)])
        db.fail = dbm.AppError(dbm.NO_NETWORK, "network")
        frame = self.screen(db)  # первая загрузка (ручная) не удалась
        self.refresh(frame)      # первая успешная — без сигналов
        self.alerts.assert_not_called()
        self.assertEqual(sorted(frame.badges), [1, 2])

    def test_manual_reload_error_still_shows_dialog(self):
        db = FakeStationDB([order(1)])
        frame = self.screen(db)
        db.fail = dbm.AppError(dbm.NO_NETWORK, "network")
        frame.reload()
        self.pump()
        self.assertEqual(self.shown, [("showerror", "Нет связи")])

    def test_auth_error_during_auto_refresh_logs_out(self):
        db = FakeStationDB([order(1)])
        frame = self.screen(db)
        db.fail = dbm.AppError("Сессия истекла. Войдите снова.", "auth")
        self.refresh(frame)
        frame.on_logout.assert_called_once()

    def test_no_parallel_requests(self):
        db = FakeStationDB([order(1)])
        frame = self.screen(db)
        HeldThread.started.clear()
        with mock.patch.object(ui_kitchen.threading, "Thread", HeldThread):
            self.fire_timer(frame)        # запрос ушёл, ответа ещё нет
            self.fire_timer(frame)        # таймер сработал снова
            frame.reload()               # и ручное обновление
            self.assertEqual(len(HeldThread.started), 1)
            self.assertIsNotNone(frame.refresh_job)  # отложено до следующего срабатывания
            HeldThread.started.pop()()
            self.pump()
        self.assertFalse(frame.busy)

    def test_closed_during_request(self):
        db = FakeStationDB([order(1)])
        frame = self.screen(db)
        db.orders.append(order(2))
        HeldThread.started.clear()
        with mock.patch.object(ui_kitchen.threading, "Thread", HeldThread):
            self.fire_timer(frame)
            frame.destroy()              # выход из аккаунта, пока идёт запрос
            self.assertIsNone(frame.refresh_job)
            HeldThread.started.pop()()   # ответ пришёл после закрытия
            self.pump()
        self.assertEqual(self.callback_errors, [])  # нет invalid command name и т. п.
        self.alerts.assert_not_called()

    def test_closed_after_answer_queued(self):
        db = FakeStationDB([order(1)])
        frame = self.screen(db)
        HeldThread.started.clear()
        with mock.patch.object(ui_kitchen.threading, "Thread", HeldThread):
            self.fire_timer(frame)
            HeldThread.started.pop()()   # ответ поставлен в очередь Tk...
            frame.destroy()              # ...а экран закрыли раньше, чем Tk его обработал
            self.pump()
        self.assertEqual(self.callback_errors, [])

    def test_bar_uses_bar_source(self):
        db = FakeStationDB([order(1, name="Латте")])
        frame = self.screen(db, role="bar")
        db.orders.append(order(2, name="Мохито б/а"))
        self.refresh(frame)
        self.assertEqual(set(db.loads), {"bar"})
        self.alerts.assert_called_once()


if __name__ == "__main__":
    unittest.main()
