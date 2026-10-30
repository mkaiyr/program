"""Роль bar, станции приготовления и экран бара (без подключения к Supabase).

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Здесь проверяется клиент и текст миграции. Разделение заказов по станциям и
права на стороне базы проверяет sql/tests/08_bar_stations_checks.sql.
"""

import re
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from postgrest.exceptions import APIError  # noqa: E402

import db as dbm  # noqa: E402
import ui_admin  # noqa: E402
import ui_kitchen  # noqa: E402
from _tk import get_root  # noqa: E402

MIGRATION = (ROOT / "sql" / "08_bar_stations.sql").read_text(encoding="utf-8")
SQL_ONLY = re.sub(r"--[^\n]*", "", MIGRATION)


def norm(sql):
    """SQL без комментариев, в нижнем регистре, с одиночными пробелами: проверки не
    зависят от оформления (регистра ключевых слов и переносов строк)."""
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", sql)).lower().strip()


NORM = norm(MIGRATION)
SCHEMA_01 = (ROOT / "sql" / "01_schema.sql").read_text(encoding="utf-8")


def api_error(message, code="P0001"):
    return APIError({"message": message, "code": code, "details": "", "hint": ""})


def function_body(sql, name):
    match = re.search(rf"create or replace function public\.{name}\(.*?\n\$\$;", sql, re.S | re.I)
    assert match, name
    return match.group(0)


def make_db():
    db = dbm.Database.__new__(dbm.Database)
    db.profile = None
    db.client = mock.MagicMock()
    return db


class ClientTest(unittest.TestCase):
    def test_roles_and_transitions(self):
        self.assertIn("bar", dbm.ALLOWED_ROLES)
        self.assertEqual(dbm.ROLE_TRANSITIONS["bar"], {"accepted": "preparing", "preparing": "ready"})
        self.assertEqual(dbm.ROLE_TRANSITIONS["kitchen"], dbm.ROLE_TRANSITIONS["bar"])
        self.assertEqual(dbm.ROLE_TRANSITIONS["cashier"], {"ready": "completed"})  # кассир — как раньше
        self.assertNotIn("completed", dbm.ROLE_TRANSITIONS["bar"].values())      # бар не выдаёт заказ

    def _sign_in(self, role, active=True):
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = "3001@bistro.local"
        db.client.auth.sign_in_with_password.return_value.user.id = "b"
        query = db.client.table.return_value
        query.select.return_value = query.eq.return_value = query.limit.return_value = query
        query.execute.return_value.data = [{"id": "b", "full_name": "Бармен", "role": role,
                                            "is_active": active, "employee_code": "3001"}]
        return db

    def test_bar_signs_in(self):
        self.assertEqual(self._sign_in("bar").sign_in("3001", "secret")["role"], "bar")

    def test_inactive_bar_rejected(self):
        db = self._sign_in("bar", active=False)
        with self.assertRaises(dbm.AppError):
            db.sign_in("3001", "secret")
        db.client.auth.sign_out.assert_called()

    def test_station_orders_rpc(self):
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = [
            {"id": 9, "created_at": "2026-10-23T09:00:00+00:00", "status": "accepted",
             "items": [{"code": 1001, "item_name": "Капучино", "quantity": 2}]}]
        orders = db.load_station_orders("bar")
        db.client.rpc.assert_called_with("bar_orders", {})
        self.assertEqual(orders[0]["items"][0]["code"], 1001)
        db.load_station_orders("kitchen")
        db.client.rpc.assert_called_with("kitchen_orders", {})
        db.load_kitchen_orders()  # прежний метод — то же, что кухонная станция
        db.client.rpc.assert_called_with("kitchen_orders", {})

    def test_force_order_ready(self):
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = {
            "order_id": 5, "status": "ready", "forced": True, "stations_forced": 2}
        self.assertEqual(db.force_order_ready(5)["stations_forced"], 2)
        db.client.rpc.assert_called_with("admin_force_order_ready", {"p_order_id": 5})
        db.client.rpc.return_value.execute.return_value.data = {"order_id": 5, "status": "ready"}
        with self.assertRaises(dbm.AppError):
            db.force_order_ready(5)  # без признака forced — не успех
        db.client.rpc.return_value.execute.side_effect = api_error("ADMIN_REQUIRED", "42501")
        with self.assertRaises(dbm.AppError) as ctx:
            db.force_order_ready(5)
        self.assertEqual(ctx.exception.code, "forbidden")

    def test_set_station_status(self):
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = {
            "order_id": 9, "station": "bar", "status": "ready", "order_status": "preparing"}
        result = db.set_station_status(9, "bar", "ready")
        db.client.rpc.assert_called_with("set_station_status", {"p_order_id": 9, "p_station": "bar",
                                                                "p_status": "ready"})
        self.assertEqual(result["order_status"], "preparing")  # заказ целиком ещё не готов
        db.client.rpc.return_value.execute.return_value.data = {"order_id": 9, "station": "kitchen", "status": "ready"}
        with self.assertRaises(dbm.AppError):
            db.set_station_status(9, "bar", "ready")  # ответ про другую станцию — не успех

    def test_station_errors(self):
        db = make_db()
        for message, code in [("STATION_NOT_ALLOWED", "forbidden"), ("INVALID_TRANSITION", "order_changed"),
                              ("NOT_ALLOWED", "auth"), ("INVALID_STATION", "invalid")]:
            db.client.rpc.return_value.execute.side_effect = api_error(message)
            with self.assertRaises(dbm.AppError) as ctx:
                db.set_station_status(1, "bar", "preparing")
            self.assertEqual(ctx.exception.code, code, message)

    def test_menu_station_params(self):
        db = make_db()
        db.client.rpc.return_value.execute.return_value.data = {
            "id": 1, "code": 1001, "name": "Латте", "category": "Напитки", "price": 900, "is_available": True}
        db.add_menu_item("Латте", "Напитки", "900")
        self.assertNotIn("p_station", db.client.rpc.call_args.args[1])  # кухня — по умолчанию на сервере
        db.add_menu_item("Латте", "Напитки", "900", "", "bar")
        self.assertEqual(db.client.rpc.call_args.args[1]["p_station"], "bar")
        db.update_menu_item(1, "Латте", "Напитки", "900")
        self.assertNotIn("p_station", db.client.rpc.call_args.args[1])
        db.update_menu_item(1, "Латте", "Напитки", "900", "bar")
        self.assertEqual(db.client.rpc.call_args.args[1]["p_station"], "bar")
        db.client.rpc.reset_mock()
        with self.assertRaises(dbm.AppError):
            db.add_menu_item("Латте", "Напитки", "900", "", "склад")
        db.client.rpc.assert_not_called()


class MigrationTest(unittest.TestCase):
    def test_create_order_unchanged_except_station_insert(self):
        """create_order в 08 = версия из 01, кроме блока вставки позиций."""
        old = function_body(SCHEMA_01, "create_order")
        new = function_body(MIGRATION, "create_order")
        block = re.compile(r"  (?:-- [^\n]*\n  )?insert into public\.order_items.*?;\n(?:\n  -- [^\n]*\n"
                           r"  insert into public\.order_stations.*?;\n)?", re.S)
        self.assertEqual(block.sub("", old), block.sub("", new))
        self.assertIn("m.station", new)
        self.assertIn("insert into public.order_stations (order_id, station)", new)
        for check in ("is_active_staff()", "MENU_CHANGED", "for share", "client_request_id", "sum(m.price"):
            self.assertIn(check, new)

    def test_bar_not_in_staff_and_no_table_rights(self):
        self.assertNotIn("function public.is_active_staff", NORM)
        self.assertIn("p.role = 'bar'", norm(function_body(MIGRATION, "is_active_bar")))
        self.assertIn("public.is_active_bar()", norm(function_body(MIGRATION, "bar_orders")))
        self.assertNotRegex(SQL_ONLY, r"(?i)grant\s+(select|insert|update|delete|all)\s+on\s+(table\s+)?public\.")
        self.assertNotRegex(SQL_ONLY, r"(?i)create policy")
        self.assertIn("revoke all on public.order_stations from public, anon, authenticated;", NORM)
        self.assertIn("alter table public.order_stations enable row level security;", NORM)

    def test_function_rights(self):
        for sig in ("is_active_bar()", "bar_orders()", "kitchen_orders()", "set_station_status(bigint, text, text)",
                    "admin_force_order_ready(bigint)",
                    "set_order_status(bigint, text)", "create_order(uuid, jsonb)",
                    "admin_add_menu_item(text, text, numeric, integer, text)",
                    "admin_update_menu_item(bigint, text, text, numeric, text)"):
            self.assertIn(f"revoke all on function public.{sig} from public, anon;", NORM, sig)
            self.assertIn(f"grant execute on function public.{sig} to authenticated;", NORM, sig)
        for internal in ("_refresh_order_status(bigint)", "_station_orders(text)"):
            self.assertIn(f"revoke all on function public.{internal} from public, anon, authenticated;", NORM)
        self.assertIn("drop function if exists public.admin_add_menu_item(text, text, numeric, integer);", NORM)
        self.assertIn("drop function if exists public.admin_update_menu_item(bigint, text, text, numeric);", NORM)
        self.assertIn("p.role in ('cashier', 'admin', 'kitchen', 'bar')",
                      norm(function_body(MIGRATION, "employee_login_email")))

    def test_status_rules(self):
        refresh = function_body(MIGRATION, "_refresh_order_status")
        self.assertIn("bool_and(s.status = 'ready')", refresh)        # «Готов» — только если готовы все
        self.assertIn("status <> 'completed'", refresh)               # выданный заказ не трогается
        station = function_body(MIGRATION, "set_station_status")
        self.assertIn("v_role <> 'admin' and v_role <> p_station", station)
        self.assertIn("p_status not in ('preparing', 'ready')", station)  # станция не выдаёт заказ
        order = function_body(MIGRATION, "set_order_status")
        self.assertIn("if v_role in ('kitchen', 'bar') then", order)
        station_orders = function_body(MIGRATION, "_station_orders")
        self.assertIn("i.station = p_station", station_orders)
        self.assertNotRegex(station_orders, r"price|total|cashier")

    def test_preconditions_before_changes(self):
        """Все проверки — до первого изменения схемы (иначе остановка была бы частичной)."""
        first_change = NORM.index("alter table public.profiles drop constraint")
        preconditions = NORM[:first_change]
        self.assertIn("status not in ('accepted', 'preparing', 'ready', 'completed')", preconditions)
        self.assertIn("role not in ('cashier', 'admin', 'kitchen', 'bar')", preconditions)
        self.assertIn("pg_get_constraintdef", preconditions)
        self.assertIn("conname = 'profiles_role_check'", preconditions)
        for message in ("неизвестные статусы", "неизвестные роли", "отсутствует или имеет неожиданный вид",
                        "ожидаются {cashier, admin, kitchen}", "другие check-ограничения"):
            self.assertIn(message, NORM, message)
        for change in ("alter table public.orders", "alter table public.menu_items", "alter table public.order_items",
                       "create table if not exists public.order_stations", "create or replace function"):
            self.assertNotIn(change, preconditions, change)

    def test_role_constraint_dropped_only_by_name(self):
        """Заменяется только profiles_role_check, тем же именем (без дубликатов) и только по решению проверки."""
        self.assertNotRegex(NORM, r"for r in select conname")
        self.assertNotRegex(NORM, r"execute format\('alter table public\.profiles drop constraint")
        self.assertEqual(re.findall(r"alter table public\.profiles drop constraint (\w+)", NORM), ["profiles_role_check"])
        self.assertEqual(re.findall(r"alter table public\.profiles add constraint (\w+)", NORM), ["profiles_role_check"])
        self.assertIn("check (role in ('cashier', 'admin', 'kitchen', 'bar'))", NORM)
        guarded = re.search(r"if current_setting\('migration08\.role_action'\) = 'add_bar' then "
                            r"alter table public\.profiles drop constraint profiles_role_check; "
                            r"alter table public\.profiles add constraint profiles_role_check", NORM)
        self.assertIsNotNone(guarded, "замена ограничения должна выполняться только при решении add_bar")
        self.assertNotRegex(NORM, r"like '%bar%'|like '%kitchen%'")  # подстрока — не роль

    def test_role_set_decision(self):
        """Регулярные выражения и наборы ролей из миграции — на образцах pg_get_constraintdef.

        Выражения берутся из файла миграции и выполняются здесь (синтаксис этих
        выражений одинаков в PostgreSQL и Python). Формат вывода PostgreSQL взят
        по документации; на реальной базе он не проверен.
        """
        shape = re.search(r"v_current !~ '((?:[^']|'')+)'", MIGRATION).group(1).replace("''", "'")
        extract = re.search(r"regexp_matches\(v_current, '((?:[^']|'')+)', 'g'\)", MIGRATION).group(1).replace("''", "'")
        add_bar = re.search(r"IF v_roles = ARRAY\[([^\]]+)\] THEN\s+PERFORM set_config\('migration08\.role_action', "
                            r"'add_bar'", MIGRATION, re.I).group(1)
        done = re.search(r"ELSIF v_roles = ARRAY\[([^\]]+)\] THEN\s+PERFORM set_config\('migration08\.role_action', "
                         r"'none'", MIGRATION, re.I).group(1)
        add_bar = re.findall(r"'(\w+)'", add_bar)
        done = re.findall(r"'(\w+)'", done)
        # В SQL массив сравнивается с array_agg(... ORDER BY ...) — литералы должны быть отсортированы
        self.assertEqual(add_bar, sorted(["cashier", "admin", "kitchen"]))
        self.assertEqual(done, sorted(["cashier", "admin", "kitchen", "bar"]))

        def decide(definition):
            if not re.search(shape, definition):
                return "stop"
            roles = sorted(set(re.findall(extract, definition)))
            return "add_bar" if roles == add_bar else "none" if roles == done else "stop"

        def check(*roles):
            return "CHECK ((role = ANY (ARRAY[" + ", ".join(f"'{r}'::text" for r in roles) + "])))"

        self.assertEqual(decide(check("cashier", "admin", "kitchen")), "add_bar")        # после 05
        self.assertEqual(decide(check("cashier", "admin", "kitchen", "bar")), "none")   # повторный запуск
        self.assertEqual(decide(check("bar", "kitchen", "admin", "cashier")), "none")   # порядок не важен
        self.assertEqual(decide(check("cashier", "admin")), "stop")                     # 01 без 05
        self.assertEqual(decide(check("cashier", "admin", "kitchen", "barista")), "stop")  # подстрока bar
        self.assertEqual(decide(check("cashier", "admin", "kitchen", "bar", "manager")), "stop")
        self.assertEqual(decide("CHECK ((role <> 'bar'::text))"), "stop")
        self.assertEqual(decide("CHECK (((role = ANY (ARRAY['cashier'::text, 'admin'::text, 'kitchen'::text])) "
                                "AND (length(role) > 0)))"), "stop")

    def test_admin_force_is_explicit(self):
        order = function_body(MIGRATION, "set_order_status")
        self.assertIn("if p_status <> 'completed' then", order)
        self.assertNotIn("update public.order_stations", order)  # обычный переход не трогает станции
        force = function_body(MIGRATION, "admin_force_order_ready")
        self.assertIn("public.is_active_admin()", force)
        self.assertIn("v_current not in ('accepted', 'preparing')", force)
        self.assertIn("'forced', true", force)

    def test_item_code_snapshot(self):
        self.assertIn("alter table public.order_items add column if not exists item_code integer;", MIGRATION)
        self.assertIn("alter table public.order_items alter column item_code set not null;", MIGRATION)
        self.assertIn("alter table public.order_items alter column station drop default;", MIGRATION)
        self.assertIn("m.station, m.code", function_body(MIGRATION, "create_order"))
        station_orders = function_body(MIGRATION, "_station_orders")
        self.assertIn("'code', i.item_code", station_orders)
        self.assertNotIn("menu_items", station_orders)

    def test_existing_orders_backfilled_as_kitchen(self):
        self.assertIn("alter table public.order_items add column if not exists station text not null default "
                      "'kitchen';", MIGRATION)
        self.assertIn("on conflict (order_id, station) do nothing", MIGRATION)


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class FakeStationDB:
    def __init__(self):
        self.orders = [
            {"id": 7, "time": "09:05", "status": "accepted",
             "items": [{"code": 2001, "item_name": "Латте", "quantity": 2}]},
            {"id": 8, "time": "09:07", "status": "preparing",
             "items": [{"code": 2002, "item_name": "Мохито б/а", "quantity": 1}]},
        ]
        self.loaded = []
        self.menu_loaded = []
        self.calls = []
        self.fail = None
        self.menu = {
            21: dict(id=21, code=2001, name="Латте", category="Кофе", is_available=True, status="on_sale"),
            22: dict(id=22, code=2002, name="Мохито б/а", category="Коктейли", is_available=False,
                     status="out_of_stock"),
            23: dict(id=23, code=2003, name="Раф лавандовый", category="Кофе", is_available=False, status="pending"),
            24: dict(id=24, code=2004, name="Глинтвейн", category="Коктейли", is_available=False, status="rejected",
                     reject_reason="Нет лицензии"),
        }

    def load_station_orders(self, station):
        self.loaded.append(station)
        return [dict(o) for o in self.orders if o["status"] in ("accepted", "preparing")]

    def set_station_status(self, order_id, station, status):
        self.calls.append((order_id, station, status))
        if self.fail:
            error, self.fail = self.fail, None
            raise error
        next(o for o in self.orders if o["id"] == order_id)["status"] = status
        return {"order_id": order_id, "station": station, "status": status, "order_status": "preparing"}

    def load_kitchen_menu(self):
        raise AssertionError("экран бара не должен вызывать kitchen_menu")

    def load_station_menu(self, station):
        """Меню бара после миграции 09: позиции во всех состояниях, только бар."""
        self.menu_loaded.append(station)
        return {"items": [dict(item) for item in self.menu.values()], "can_propose": True, "available": True}

    def station_set_available(self, station, item_id, available):
        self.calls.append(("available", station, item_id, available))
        self.menu[item_id].update(is_available=available, status="on_sale" if available else "out_of_stock")
        return dict(self.menu[item_id])

    def propose_menu_item(self, name, category, price_text, code_text=""):
        self.calls.append(("propose", name, category, price_text, code_text))
        if self.fail:
            error, self.fail = self.fail, None
            raise error
        self.menu[99] = dict(id=99, code=2099, name=name, category=category, is_available=False, status="pending")
        return dict(self.menu[99])


class FakeAdminDB:
    def __init__(self):
        self.rows = {1: dict(id=1, code=1001, name="Капучино", category="Напитки", price=Decimal("1100.00"),
                             is_available=True, station="kitchen")}
        self.calls = []

    def load_all_menu(self):
        return [dict(r) for r in self.rows.values()]

    def add_menu_item(self, name, category, price_text, code_text="", station="kitchen"):
        self.calls.append(("add", name, station))
        self.rows[2] = dict(id=2, code=1002, name=name, category=category, price=Decimal(price_text),
                            is_available=True, station=station)
        return dict(self.rows[2])

    def update_menu_item(self, item_id, name, category, price_text, station=None):
        self.calls.append(("update", item_id, station))
        self.rows[item_id].update(name=name, category=category, station=station or self.rows[item_id]["station"])
        return dict(self.rows[item_id])


class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        self.shown = []
        patches = [mock.patch.object(m.threading, "Thread", SyncThread) for m in (ui_kitchen, ui_admin)]
        for name in ("showinfo", "showwarning", "showerror"):
            patches.append(mock.patch.object(ui_kitchen.messagebox, name,
                                             lambda *a, _n=name, **k: self.shown.append((_n, a[0]))))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def pump(self):
        for _ in range(5):
            self.root.update()

    def bar(self, db):
        frame = ui_kitchen.BarFrame(self.root, db, {"full_name": "Бармен", "role": "bar"}, on_logout=mock.Mock())
        self.addCleanup(frame.destroy)
        self.pump()
        return frame

    def test_bar_screen(self):
        db = FakeStationDB()
        frame = self.bar(db)
        self.assertEqual(db.loaded, ["bar"])
        self.assertEqual(len(frame.tabs.tabs()), 2)  # «Заказы» и меню бара (миграция 09)
        self.assertEqual(db.menu_loaded, [])         # меню — только при открытии вкладки
        self.assertEqual(frame.menu_buttons, {})
        self.assertEqual(sorted(frame.badges), [7, 8])
        texts = [str(w["text"]) for w in _all_widgets(frame.orders_area.inner) if "text" in w.keys()]
        self.assertIn("2001", texts)
        self.assertIn("Латте", texts)
        self.assertFalse(any("₸" in t for t in texts), texts)

    def bar_menu(self, db):
        frame = self.bar(db)
        frame.tabs.select(1)
        frame.reload_menu()  # в скрытом окне Tk не присылает <<NotebookTabChanged>>
        self.pump()
        return frame

    def test_bar_menu_states(self):
        """Меню бара: кнопка наличия только у подтверждённых; ждущие и отклонённые — без неё."""
        db = FakeStationDB()
        frame = self.bar_menu(db)
        self.assertEqual(set(db.menu_loaded), {"bar"})  # только меню бара; kitchen_menu не вызывался
        self.assertEqual(sorted(frame.menu_buttons), [21, 22])
        texts = [str(w["text"]) for w in _all_widgets(frame.menu_panel.list.inner) if "text" in w.keys()]
        for text in ("●  Ждёт решения", "●  Отклонена", "●  В продаже", "●  Нет в наличии"):
            self.assertIn(text, texts)
        self.assertTrue(any("Нет лицензии" in t for t in texts), texts)  # причина отклонения видна
        self.assertFalse(any("₸" in t for t in texts), texts)
        frame.toggle_available(23)                 # ждущая: наличие не меняется даже прямым вызовом
        frame.toggle_available(24)
        self.pump()
        self.assertEqual(db.calls, [])
        frame.menu_buttons[22].invoke()            # «Вернуть в продажу»
        self.pump()
        self.assertEqual(db.calls, [("available", "bar", 22, True)])
        frame.menu_panel.availability_var.set("Ждут решения")
        texts = [str(w["text"]) for w in _all_widgets(frame.menu_panel.list.inner) if "text" in w.keys()]
        self.assertIn("Раф лавандовый", texts)
        self.assertNotIn("Латте", texts)
        self.assertEqual(frame.menu_buttons, {})

    def test_bar_propose_item(self):
        db = FakeStationDB()
        frame = self.bar_menu(db)
        self.assertEqual(str(frame.menu_panel.propose_btn["state"]), "normal")
        frame.propose_item()
        dialog = [w for w in frame.winfo_children() if isinstance(w, ui_kitchen.ProposeDialog)][-1]
        dialog.name_entry.insert(0, "Матча")
        dialog.category_box.insert(0, "Кофе")
        dialog.price_entry.insert(0, "abc")         # ошибка ввода — запрос не уходит, окно остаётся
        dialog.submit()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(db.calls, [])
        dialog.price_entry.delete(0, "end")
        dialog.price_entry.insert(0, "1500")
        dialog.submit()
        self.pump()
        self.assertEqual(db.calls, [("propose", "Матча", "Кофе", "1500", "")])
        self.assertIn("ждёт решения", frame.status["text"])
        self.assertIn(99, frame.menu)

    def test_bar_propose_refused_keeps_input(self):
        db = FakeStationDB()
        frame = self.bar_menu(db)
        db.fail = dbm.AppError(dbm.RPC_ERRORS["NAME_TAKEN"][1], "invalid")
        frame._send_proposal({"name": "Латте", "category": "Кофе", "price": "900", "code": ""})
        self.pump()
        self.assertEqual(frame.propose_draft["name"], "Латте")  # окно откроется с введённым
        self.assertIn(("showwarning", "Не сохранено"), self.shown)

    def test_bar_advances_only_bar_part(self):
        db = FakeStationDB()
        frame = self.bar(db)
        frame.action_buttons[7].invoke()
        self.pump()
        frame.advance_status(8)
        self.pump()
        self.assertEqual(db.calls, [(7, "bar", "preparing"), (8, "bar", "ready")])
        self.assertEqual(sorted(frame.badges), [7])  # готовая барная часть ушла с экрана бара

    def test_bar_search(self):
        frame = self.bar(FakeStationDB())
        frame.search_var.set("2002")
        self.pump()
        self.assertEqual(list(frame.badges), [8])
        frame.search_var.set("ЛАТ")
        self.pump()
        self.assertEqual(list(frame.badges), [7])
        frame.search_var.set("9999")
        self.pump()
        self.assertEqual(frame.badges, {})
        texts = [str(w["text"]) for w in frame.orders_area.inner.winfo_children()]
        self.assertIn("Ничего не найдено", texts)

    def test_bar_errors_do_not_touch_kitchen_menu(self):
        db = FakeStationDB()
        frame = self.bar(db)
        for error in (dbm.AppError("Позиция не найдена.", "invalid"),
                      dbm.AppError(dbm.RPC_ERRORS["STATION_NOT_ALLOWED"][1], "forbidden")):
            db.fail = error
            frame.advance_status(7)
            self.pump()  # FakeStationDB.load_kitchen_menu упал бы, если бы его вызвали
            self.assertFalse(frame.busy)
        frame.logout()
        frame.on_logout.assert_called_once()  # выход только по кнопке, не из-за ошибки

    def test_kitchen_uses_kitchen_station(self):
        db = FakeStationDB()
        frame = ui_kitchen.KitchenFrame(self.root, db, {"full_name": "Повар", "role": "kitchen"},
                                        on_logout=mock.Mock())
        self.addCleanup(frame.destroy)
        self.pump()
        frame.advance_status(7)
        self.pump()
        self.assertEqual(db.loaded[0], "kitchen")
        self.assertEqual(db.calls, [(7, "kitchen", "preparing")])
        self.assertEqual(len(frame.tabs.tabs()), 2)

    def test_routing_bar(self):
        import main
        app = mock.Mock()
        with mock.patch.object(main, "BarFrame") as bar, mock.patch.object(main, "KitchenFrame") as kitchen, \
                mock.patch.object(main, "OrderFrame") as order:
            main.App.show_main(app, {"full_name": "X", "role": "bar"})
        bar.assert_called_once()
        kitchen.assert_not_called()
        order.assert_not_called()

    def test_orders_window_force_button(self):
        import ui_orders
        orders = [{"id": oid, "created_at": "", "cashier_name": "К", "total": Decimal("1.00"),
                   "status": st, "items": []}
                  for oid, st in ((1, "accepted"), (2, "preparing"), (3, "ready"), (4, "completed"))]
        db = mock.Mock(load_orders=mock.Mock(return_value=orders))
        db.force_order_ready.return_value = {"order_id": 2, "status": "ready", "forced": True, "stations_forced": 1}
        with mock.patch.object(ui_orders.threading, "Thread", SyncThread), \
                mock.patch.object(ui_orders.messagebox, "askyesno", return_value=True) as ask:
            cashier = ui_orders.OrdersWindow(self.root, db, role="cashier", on_auth_error=mock.Mock())
            self.addCleanup(cashier.destroy)
            self.assertIsNone(cashier.force_btn)
            admin = ui_orders.OrdersWindow(self.root, db, role="admin", on_auth_error=mock.Mock())
            self.addCleanup(admin.destroy)
            self.pump()
            states = {}
            for oid in ("1", "2", "3", "4"):
                admin.tree.selection_set(oid)
                self.pump()
                states[oid] = str(admin.force_btn["state"])
            self.assertEqual(states, {"1": "normal", "2": "normal", "3": "disabled", "4": "disabled"})
            admin.tree.selection_set("2")
            self.pump()
            admin.force_btn.invoke()
            self.pump()
            ask.assert_called_once()                      # только с подтверждением
            db.force_order_ready.assert_called_once_with(2)
            db.set_order_status.assert_not_called()

    def test_admin_station(self):
        db = FakeAdminDB()
        win = ui_admin.MenuAdminWindow(self.root, db, on_close=lambda: None, on_auth_error=lambda: None)
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        with mock.patch.object(ui_admin.messagebox, "showinfo"):
            self.pump()
            self.assertEqual(win.tree.item("1")["values"][3], "Кухня")
            win._fill_form("Латте", "Напитки", "900")
            win.station_box.set("Бар")
            win.save()
            self.pump()
            self.assertEqual(db.calls[-1], ("add", "Латте", "bar"))
            win.tree.selection_set("1")
            self.pump()
            self.assertEqual(win.station_box.get(), "Кухня")
            win.station_box.set("Бар")
            win.save()  # изменилась только станция — это изменение
            self.pump()
            self.assertEqual(db.calls[-1], ("update", 1, "bar"))


def _all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from _all_widgets(child)


if __name__ == "__main__":
    unittest.main()
