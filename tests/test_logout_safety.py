"""Выход из аккаунта: локальная сессия, ответы после выхода и некорректные ответы сервера.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Без подключения к Supabase. Клиент supabase-py настоящий, но его HTTP-запросы
подменены: обрыв связи — исключением httpx, ответ сервера — httpx.Response.
Адрес проекта и ключ — заглушки, запросы никуда не отправляются.
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from tkinter import ttk
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import threading  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402
import supabase_auth._sync.gotrue_client as gotrue_client  # noqa: E402
from supabase_auth.types import Session, User  # noqa: E402

import db as dbm  # noqa: E402
import main  # noqa: E402
import ui_kitchen  # noqa: E402
import ui_order  # noqa: E402
import ui_orders  # noqa: E402
from _tk import get_root  # noqa: E402

FAKE_URL = "https://example-test-project.supabase.co"
FAKE_KEY = "sb_publishable_test_only_not_a_real_key"


def employee_session(user_id):
    """Сессия на час вперёд: get_session() возвращает её без обновления токена (без сети)."""
    user = User(id=user_id, app_metadata={}, user_metadata={}, aud="authenticated",
                created_at="2026-01-01T00:00:00Z")
    return Session(access_token=f"jwt-of-{user_id}", refresh_token="refresh", expires_in=3600,
                   token_type="bearer", user=user)


def signed_in_db(user_id="cashier-1"):
    """Database с настоящим клиентом supabase-py в состоянии «сотрудник вошёл»."""
    db = dbm.Database(FAKE_URL, FAKE_KEY)
    # Без таймера автообновления токена: он запускается при сохранении сессии и позже ходил бы в сеть
    db.client.auth._auto_refresh_token = False
    session = employee_session(user_id)
    db.client.auth._save_session(session)
    db.client.auth._notify_all_subscribers("SIGNED_IN", session)  # как после sign_in_with_password
    db.profile = {"id": user_id, "role": "cashier"}
    return db


def server_answer(status):
    def answer(*_args, **_kwargs):
        return httpx.Response(status, request=httpx.Request("POST", FAKE_URL), json={})
    return answer


class SignOutTest(unittest.TestCase):
    """Выход сбрасывает сессию на этом компьютере, даже если сервер недоступен."""

    def assert_signed_out(self, db, user_id="cashier-1"):
        self.assertIsNone(db.profile)
        self.assertIsNone(db.client.auth.get_session(), "сессия сотрудника осталась в клиенте")
        self.assertNotIn(f"jwt-of-{user_id}", db.client.options.headers["Authorization"])
        # Запросы к таблицам и RPC после выхода идут с публичным ключом, а не с токеном сотрудника
        self.assertNotIn(f"jwt-of-{user_id}", db.client.postgrest.session.headers["Authorization"])

    def test_sign_out_without_network(self):
        failures = {
            "нет связи": httpx.ConnectError("offline"),
            "таймаут": httpx.ReadTimeout("timeout"),
        }
        for name, error in failures.items():
            with self.subTest(name):
                db = signed_in_db()
                with mock.patch.object(db.client.auth.admin._http_client, "request", side_effect=error):
                    db.sign_out()  # не должно выбрасывать исключение
                self.assert_signed_out(db)

    def test_sign_out_server_unavailable(self):
        for status in (502, 503, 504):
            with self.subTest(status=status):
                db = signed_in_db()
                with mock.patch.object(db.client.auth.admin._http_client, "request",
                                       side_effect=server_answer(status)):
                    db.sign_out()
                self.assert_signed_out(db)

    def test_sign_out_normal_and_expired(self):
        """Регрессия: обычный выход (204) и истёкший токен (401) по-прежнему сбрасывают сессию."""
        for status in (204, 401):
            with self.subTest(status=status):
                db = signed_in_db()
                with mock.patch.object(db.client.auth.admin._http_client, "request",
                                       side_effect=server_answer(status)) as request:
                    db.sign_out()
                request.assert_called_once()  # сервер уведомляется, если доступен
                self.assert_signed_out(db)

    def test_next_employee_after_offline_sign_out(self):
        """Регрессия: после выхода без связи следующий вход получает свою сессию и свой токен."""
        db = signed_in_db("cashier-1")
        with mock.patch.object(db.client.auth.admin._http_client, "request",
                               side_effect=httpx.ConnectError("offline")):
            db.sign_out()
        session = employee_session("kitchen-2")
        db.client.auth._save_session(session)
        db.client.auth._notify_all_subscribers("SIGNED_IN", session)
        self.assertIn("jwt-of-kitchen-2", db.client.options.headers["Authorization"])
        self.assertEqual(db.client.auth.get_session().user.id, "kitchen-2")


class FakeTimer:
    """Вместо threading.Timer клиента supabase-py: не запускает поток, запоминает отмену."""
    created = []

    def __init__(self, interval, function):
        self.interval, self.function, self.cancelled = interval, function, False
        FakeTimer.created.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class SignOutEffectsTest(unittest.TestCase):
    """Последствия выхода, которые не видны по одной только сессии."""

    def setUp(self):
        FakeTimer.created.clear()
        patch = mock.patch.object(gotrue_client, "Timer", FakeTimer)
        patch.start()
        self.addCleanup(patch.stop)

    def signed_in_with_refresh_timer(self):
        db = dbm.Database(FAKE_URL, FAKE_KEY)  # автообновление токена включено, как в приложении
        session = employee_session("cashier-1")
        db.client.auth._save_session(session)
        db.client.auth._notify_all_subscribers("SIGNED_IN", session)
        self.assertEqual(len(FakeTimer.created), 1, "клиент должен запланировать обновление токена")
        return db

    def test_refresh_timer_stopped(self):
        cases = {"нет связи": httpx.ConnectError("offline"), "503": server_answer(503), "204": server_answer(204)}
        for name, effect in cases.items():
            with self.subTest(name):
                FakeTimer.created.clear()
                db = self.signed_in_with_refresh_timer()
                timer = FakeTimer.created[0]
                with mock.patch.object(db.client.auth.admin._http_client, "request", side_effect=effect):
                    db.sign_out()
                self.assertTrue(timer.cancelled, "таймер обновления токена прежнего сотрудника не остановлен")
                self.assertIsNone(db.client.auth._refresh_token_timer)

    def test_signed_out_event_once(self):
        cases = {"нет связи": httpx.ConnectError("offline"), "503": server_answer(503), "204": server_answer(204),
                 "401": server_answer(401)}
        for name, effect in cases.items():
            with self.subTest(name):
                FakeTimer.created.clear()
                db = self.signed_in_with_refresh_timer()
                events = []
                db.client.auth.on_auth_state_change(lambda event, _session: events.append(event))
                with mock.patch.object(db.client.auth.admin._http_client, "request", side_effect=effect):
                    db.sign_out()
                self.assertEqual(events, ["SIGNED_OUT"])


class SlowServer:
    """HTTP-клиент авторизации: ответ задерживается до сигнала теста (или таймаута)."""

    def __init__(self, error=None):
        self.release = threading.Event()
        self.calls = []
        self.error = error or httpx.ConnectError("offline")

    def request(self, method, url, headers=None, **_kwargs):
        self.calls.append((method, url, (headers or {}).get("Authorization")))
        self.release.wait(5)
        raise self.error


class NonBlockingLogoutTest(unittest.TestCase):
    """Выход не ждёт сервер в потоке Tk; следующий вход не теряет свою сессию."""

    def setUp(self):
        self.server = SlowServer()

    def db_with_slow_server(self, user_id="cashier-1"):
        db = signed_in_db(user_id)
        patch = mock.patch.object(db.client.auth.admin._http_client, "request", side_effect=self.server.request)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.server.release.set)  # не оставлять потоки ждать
        return db

    def test_app_logout_returns_without_waiting_for_server(self):
        db = self.db_with_slow_server()
        app = mock.Mock()
        app.db = db
        started = time.monotonic()
        main.App.logout(app)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.5, f"выход ждал сервер {elapsed:.1f} с в потоке Tk")
        app.show_login.assert_called_once_with()
        self.assertIsNone(db.client.auth.get_session())          # локально сессии уже нет
        self.assertNotIn("jwt-of-cashier-1", db.client.options.headers["Authorization"])
        # Сервер всё равно получает отзыв именно старого токена (в фоне)
        deadline = time.monotonic() + 3
        while not self.server.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(self.server.calls[0][2], "Bearer jwt-of-cashier-1")
        self.server.release.set()
        time.sleep(0.1)
        # Ответ сервера после выхода ничего не делает с интерфейсом: никаких поздних вызовов App
        self.assertEqual(app.method_calls, [mock.call.show_login()])

    def test_revoke_uses_refreshed_token(self):
        """Токен обновился (TOKEN_REFRESHED) — отзывается текущий токен, а не первый."""
        db = self.db_with_slow_server()
        refreshed = employee_session("cashier-1-refreshed")
        db.client.auth._save_session(refreshed)
        db.client.auth._notify_all_subscribers("TOKEN_REFRESHED", refreshed)
        main.App.logout(mock.Mock(db=db))
        deadline = time.monotonic() + 3
        while not self.server.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual([call[2] for call in self.server.calls], ["Bearer jwt-of-cashier-1-refreshed"])

    def test_logout_without_session_makes_no_request(self):
        """Выход без сессии (например, после истёкшего входа): только локальный сброс, без запроса."""
        db = dbm.Database(FAKE_URL, FAKE_KEY)
        db.client.auth._auto_refresh_token = False
        with mock.patch.object(db.client.auth.admin._http_client, "request") as request:
            main.App.logout(mock.Mock(db=db))
            time.sleep(0.1)
        request.assert_not_called()
        self.assertIsNone(db.client.auth.get_session())

    def test_next_sign_in_waits_for_revoke_and_keeps_new_session(self):
        db = self.db_with_slow_server("cashier-1")
        main.App.logout(mock.Mock(db=db))
        deadline = time.monotonic() + 3
        while not self.server.calls and time.monotonic() < deadline:
            time.sleep(0.01)

        entered = threading.Event()

        def fake_login_email(_code):
            entered.set()
            return None  # неизвестный код: sign_in выбросит BAD_LOGIN, это не важно для теста

        with mock.patch.object(db, "_login_email", side_effect=fake_login_email):
            worker = threading.Thread(target=lambda: self.assertRaises(dbm.AppError, db.sign_in, "1002", "pw"))
            worker.start()
            self.assertFalse(entered.wait(0.3), "вход начался, пока сервер ещё отзывает прежнюю сессию")
            self.server.release.set()                 # отзыв завершился (ошибкой связи)
            self.assertTrue(entered.wait(3), "вход не продолжился после отзыва")
            worker.join(3)

        # Новый вход того же сотрудника после отзыва: его сессия остаётся
        session = employee_session("cashier-1-new")
        db.client.auth._save_session(session)
        db.client.auth._notify_all_subscribers("SIGNED_IN", session)
        time.sleep(0.1)
        self.assertEqual(db.client.auth.get_session().access_token, "jwt-of-cashier-1-new")
        self.assertIn("jwt-of-cashier-1-new", db.client.options.headers["Authorization"])


class ForceReadyAnswerTest(unittest.TestCase):
    """Некорректный ответ admin_force_order_ready не принимается за успех."""

    def make_db(self, data):
        db = dbm.Database.__new__(dbm.Database)
        db.profile = None
        db.client = mock.MagicMock()
        db.client.rpc.return_value.execute.return_value.data = data
        return db

    def test_missing_or_bad_stations_forced(self):
        for data in ({"order_id": 1, "status": "ready", "forced": True},
                     {"order_id": 1, "status": "ready", "forced": True, "stations_forced": "2"},
                     {"order_id": 1, "status": "ready", "forced": True, "stations_forced": None}):
            with self.subTest(data=data), self.assertRaises(dbm.AppError):
                self.make_db(data).force_order_ready(1)

    def test_valid_answer(self):
        data = {"order_id": 1, "status": "ready", "forced": True, "stations_forced": 2}
        self.assertEqual(self.make_db(data).force_order_ready(1), data)


# ---------- Ответы фоновых запросов после выхода из аккаунта ----------

class HeldThread:
    """Поток, который не стартует сам: тест решает, когда «ответит сервер»."""
    started = []

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        HeldThread.started.append(self.target)


ORDER = {"id": 1, "created_at": "", "cashier_name": "К", "total": Decimal("1.00"),
         "status": "accepted", "items": []}
MENU_ITEM = {"id": 1, "code": 1001, "name": "Капучино", "category": "Напитки", "price": Decimal("1100.00")}


class FakeDB:
    def __init__(self):
        self.fail = None
        self.calls = []

    def _answer(self, name, value):
        self.calls.append(name)
        if self.fail:
            raise self.fail
        return value

    def load_orders(self):
        return self._answer("load_orders", [dict(ORDER)])

    def load_menu(self):
        return self._answer("load_menu", [dict(MENU_ITEM)])

    def create_order(self, request_id, items):
        return self._answer("create_order", {"order_id": 5, "total": Decimal("1100.00"), "duplicate": False})

    def load_station_orders(self, station):
        return self._answer("load_station_orders", [{"id": 1, "time": "10:00", "status": "accepted",
                                                     "items": [{"code": 1001, "item_name": "Суп", "quantity": 1}]}])

    def load_kitchen_menu(self):
        return self._answer("load_kitchen_menu", [{"id": 1, "code": 1001, "name": "Суп", "category": "Еда",
                                                   "is_available": True}])


AUTH_ERROR = dbm.AppError("Сессия истекла. Войдите снова.", "auth")


class AnswerAfterLogoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        HeldThread.started.clear()
        self.shown = []
        self.callback_errors = []
        modules = (ui_order, ui_orders, ui_kitchen)
        patches = [mock.patch.object(m.threading, "Thread", HeldThread) for m in modules]
        patches.append(mock.patch.object(ui_kitchen, "play_alert"))
        for module in modules:
            for name in ("showinfo", "showwarning", "showerror"):
                patches.append(mock.patch.object(module.messagebox, name,
                                                 lambda *a, _n=name, **k: self.shown.append((_n, a[0]))))
            patches.append(mock.patch.object(module.messagebox, "askyesno", return_value=True))
        patches.append(mock.patch.object(self.root, "report_callback_exception",
                                         lambda *exc: self.callback_errors.append(exc[1])))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def pump(self):
        for _ in range(5):
            self.root.update()

    def answer(self):
        """Сервер отвечает на самый старый ожидающий запрос."""
        HeldThread.started.pop(0)()

    def assert_no_dangling_after(self):
        """Каждый отложенный вызов after ссылается на существующую Tcl-команду (см. test_window_logout)."""
        tk = self.root.tk
        for after_id in tk.splitlist(tk.call("after", "info")):
            script = tk.splitlist(tk.call("after", "info", after_id))[0]
            command = tk.splitlist(script)[0] if script else ""
            if command:
                self.assertTrue(tk.call("info", "commands", command),
                                f"отложенный вызов {after_id} ссылается на удалённую команду {command}")

    def logout_by_destroying(self, screen):
        """Как App.logout: экран роли уничтожается (вместе с дочерними окнами)."""
        logout = mock.Mock(side_effect=lambda: screen.winfo_exists() and screen.destroy())
        return logout

    # --- Окно «Заказы»: обновление, запрошенное во время запроса, и истёкшая сессия ---

    def test_orders_window_pending_reload_after_auth_error(self):
        screen = ttk.Frame(self.root)
        self.addCleanup(lambda: screen.winfo_exists() and screen.destroy())
        db = FakeDB()
        window = ui_orders.OrdersWindow(screen, db, role="cashier", on_auth_error=None)
        window.on_auth_error = self.logout_by_destroying(screen)
        window.reload()                      # касса оформила заказ и просит обновить список
        self.assertTrue(window.reload_pending)
        db.fail = AUTH_ERROR                 # сессия истекла
        self.answer()
        self.pump()
        self.assertEqual(self.callback_errors, [])          # нет TclError на уничтоженном окне
        window.on_auth_error.assert_called_once()
        self.assertEqual(HeldThread.started, [])            # после выхода запросов нет
        self.assertEqual(db.calls, ["load_orders"])
        self.assert_no_dangling_after()

    # --- Экран кухни: меню, запрошенное во время запроса, и истёкшая сессия ---

    def test_kitchen_pending_menu_after_auth_error(self):
        db = FakeDB()
        frame = ui_kitchen.KitchenFrame(self.root, db, {"full_name": "Повар", "role": "kitchen"},
                                        on_logout=None)
        self.addCleanup(lambda: frame.closed or frame.destroy())
        frame.on_logout = self.logout_by_destroying(frame)
        frame.tabs.select(frame.menu_panel)  # повар открыл вкладку «Меню», пока грузятся заказы
        self.pump()
        self.assertTrue(frame.menu_pending)
        db.fail = AUTH_ERROR
        self.answer()
        self.pump()
        self.assertEqual(self.callback_errors, [])
        frame.on_logout.assert_called_once()
        self.assertEqual(HeldThread.started, [])
        self.assertEqual(db.calls, ["load_station_orders"])
        self.assert_no_dangling_after()

    def test_kitchen_pending_menu_still_loads_while_open(self):
        """Регрессия: без выхода отложенная загрузка меню выполняется."""
        db = FakeDB()
        frame = ui_kitchen.KitchenFrame(self.root, db, {"full_name": "Повар", "role": "kitchen"},
                                        on_logout=mock.Mock())
        self.addCleanup(lambda: frame.closed or frame.destroy())
        frame.tabs.select(frame.menu_panel)
        self.pump()
        self.answer()                        # заказы загружены
        self.pump()
        self.assertEqual(len(HeldThread.started), 1)        # началась отложенная загрузка меню
        self.answer()
        self.pump()
        self.assertTrue(frame.menu_loaded)
        self.assertEqual(self.callback_errors, [])

    # --- Касса: выход (из окна «Заказы»), пока идёт её собственный запрос ---

    def cashier_screen(self, db):
        frame = ui_order.OrderFrame(self.root, db, {"full_name": "Кассир", "role": "cashier"},
                                    on_logout=mock.Mock())
        self.addCleanup(lambda: frame.winfo_exists() and frame.destroy())
        return frame

    def test_cashier_answer_after_logout(self):
        for fail in (None, dbm.AppError(dbm.NO_NETWORK, "network"), AUTH_ERROR):
            with self.subTest(fail=fail):
                HeldThread.started.clear()
                self.shown.clear()
                db = FakeDB()
                db.fail = fail
                frame = self.cashier_screen(db)     # загрузка меню при открытии
                frame.destroy()                     # выход из аккаунта, пока идёт запрос
                self.answer()
                self.assert_no_dangling_after()
                self.pump()
                self.assertEqual(self.callback_errors, [])
                self.assertEqual(self.shown, [])
                frame.on_logout.assert_not_called()  # повторного выхода нет

    def test_cashier_answer_queued_before_logout(self):
        frame = self.cashier_screen(FakeDB())
        self.answer()            # ответ уже в очереди Tk...
        frame.destroy()          # ...а экран кассы закрыт раньше, чем Tk его обработал
        self.assert_no_dangling_after()
        self.pump()
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.shown, [])

    def test_cashier_order_answer_after_logout_from_orders_window(self):
        """Сквозной сценарий: касса оформляет заказ, окно «Заказы» получает «сессия истекла»."""
        db = FakeDB()
        frame = self.cashier_screen(db)
        self.answer()                                # меню загружено
        self.pump()
        frame.on_logout.side_effect = lambda: frame.destroy()  # как App.logout
        frame.add_item(1)
        frame.open_orders()                          # окно «Заказы» начинает загрузку
        frame.submit()                               # касса ждёт ответа create_order
        self.assertTrue(frame.busy)
        orders_request, order_request = HeldThread.started
        HeldThread.started.clear()
        db.fail = AUTH_ERROR
        orders_request()                             # окно «Заказы»: сессия истекла -> выход
        self.pump()
        frame.on_logout.assert_called_once()
        self.assertFalse(frame.winfo_exists())
        db.fail = None
        order_request()                              # ответ кассе пришёл после выхода
        self.assert_no_dangling_after()
        self.pump()
        self.assertEqual(self.callback_errors, [])
        self.assertNotIn(("showinfo", "Заказ оформлен"), self.shown)

    def test_cashier_answer_applied_while_open(self):
        """Регрессия: пока касса открыта, ответ применяется."""
        frame = self.cashier_screen(FakeDB())
        self.answer()
        self.pump()
        self.assertFalse(frame.busy)
        self.assertIn(1, frame.menu)
        self.assertEqual(self.callback_errors, [])

    # --- «Готов принудительно»: некорректный ответ сервера ---

    def test_force_ready_bad_answer_shows_error(self):
        real = dbm.Database.__new__(dbm.Database)
        real.profile = None
        real.client = mock.MagicMock()
        real.client.rpc.return_value.execute.return_value.data = {"order_id": 1, "status": "ready", "forced": True}
        db = FakeDB()
        db.force_order_ready = real.force_order_ready   # настоящая проверка ответа из db.py
        screen = ttk.Frame(self.root)
        self.addCleanup(lambda: screen.winfo_exists() and screen.destroy())
        window = ui_orders.OrdersWindow(screen, db, role="admin", on_auth_error=mock.Mock())
        self.answer()
        self.pump()
        window.tree.selection_set("1")
        self.pump()
        window.force_ready()
        self.answer()
        self.pump()
        self.assertEqual(self.callback_errors, [])          # нет KeyError в обработчике Tk
        self.assertIn(("showerror", "Ошибка"), self.shown)   # пользователь видит, что не подтверждено
        self.assertFalse(window.busy)


if __name__ == "__main__":
    unittest.main()
