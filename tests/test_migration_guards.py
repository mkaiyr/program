"""Защита старых миграций 01, 02, 04, 05, 06 от запуска поверх более новой схемы.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

SQL здесь НЕ выполняется. Тест разбирает блок «Предусловие» каждой миграции
(строки «if <условие> then v_found := array_append(v_found, '<признак>'); end if;»),
переводит условия в проверки над моделью схемы и проверяет на состояниях базы после
01…08, а также на частичных состояниях (вручную созданные объекты, недоприменённая
миграция). Модель описывает только объекты, по которым срабатывают предусловия;
поведение PostgreSQL проверяется запуском на тестовом проекте Supabase (docs/DATABASE.md).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL = ROOT / "sql"


def norm(sql):
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", sql)).strip()


def any_check(column, values):
    """Определение CHECK в виде, который возвращает pg_get_constraintdef для «col in (...)»."""
    items = ", ".join(f"'{v}'::text" for v in values)
    return f"CHECK (({column} = ANY (ARRAY[{items}])))"


# ---------- Модель схемы ----------

def empty_state():
    return {"rels": set(), "cols": set(), "procs": {}, "triggers": set(), "checks": {}}


def add_proc(state, name, nargs, replace=None):
    overloads = state["procs"].setdefault(name, [])
    if replace is not None and replace in overloads:
        overloads.remove(replace)
    overloads.append(nargs)


def set_check(state, table, key, definition):
    state["checks"].setdefault(table, {})[key] = definition


# Каждый шаг — набор «признаков»: функций, меняющих модель. Частичные состояния
# строятся применением одного признака более новой миграции.
FEATURES = {
    1: {
        "tables": lambda s: (s["rels"].update({"profiles", "menu_items", "orders", "order_items"}),
                             set_check(s, "profiles", "role", any_check("role", ["cashier", "admin"]))),
        "is_active_staff": lambda s: add_proc(s, "is_active_staff", 0),
        "create_order": lambda s: add_proc(s, "create_order", 2),
    },
    2: {
        "is_active_admin": lambda s: add_proc(s, "is_active_admin", 0),
        "_check_menu_item_input": lambda s: add_proc(s, "_check_menu_item_input", 3),
        "admin_add_menu_item/3": lambda s: add_proc(s, "admin_add_menu_item", 3),
        "admin_update_menu_item/4": lambda s: add_proc(s, "admin_update_menu_item", 4),
        "admin_set_menu_item_available": lambda s: add_proc(s, "admin_set_menu_item_available", 2),
    },
    3: {
        "profiles.employee_code": lambda s: s["cols"].add(("profiles", "employee_code")),
        "employee_login_email": lambda s: add_proc(s, "employee_login_email", 1),
    },
    4: {
        "orders.status": lambda s: (s["cols"].add(("orders", "status")),
                                    set_check(s, "orders", "status",
                                              any_check("status", ["accepted", "ready", "completed"]))),
        "set_order_status": lambda s: add_proc(s, "set_order_status", 2),
    },
    5: {
        "role: kitchen": lambda s: set_check(s, "profiles", "role",
                                             any_check("role", ["cashier", "admin", "kitchen"])),
        "status: preparing": lambda s: set_check(s, "orders", "status", any_check(
            "status", ["accepted", "preparing", "ready", "completed"])),
        "is_active_kitchen": lambda s: add_proc(s, "is_active_kitchen", 0),
        "kitchen_orders": lambda s: add_proc(s, "kitchen_orders", 0),
    },
    6: {
        "kitchen_menu": lambda s: add_proc(s, "kitchen_menu", 0),
        "kitchen_set_menu_item_available": lambda s: add_proc(s, "kitchen_set_menu_item_available", 2),
    },
    7: {
        "menu_items.code": lambda s: s["cols"].add(("menu_items", "code")),
        "menu_item_code_seq": lambda s: s["rels"].add("menu_item_code_seq"),
        "_next_menu_item_code": lambda s: add_proc(s, "_next_menu_item_code", 0),
        "_menu_item_code_immutable": lambda s: add_proc(s, "_menu_item_code_immutable", 0),
        "trigger code_immutable": lambda s: s["triggers"].add(("menu_items", "menu_items_code_immutable")),
        "admin_add_menu_item/4": lambda s: add_proc(s, "admin_add_menu_item", 4, replace=3),
    },
    8: {
        "menu_items.station": lambda s: s["cols"].add(("menu_items", "station")),
        "order_items.station": lambda s: s["cols"].add(("order_items", "station")),
        "order_items.item_code": lambda s: s["cols"].add(("order_items", "item_code")),
        "order_stations": lambda s: s["rels"].add("order_stations"),
        "role: bar": lambda s: set_check(s, "profiles", "role",
                                         any_check("role", ["cashier", "admin", "kitchen", "bar"])),
        "is_active_bar": lambda s: add_proc(s, "is_active_bar", 0),
        "bar_orders": lambda s: add_proc(s, "bar_orders", 0),
        "set_station_status": lambda s: add_proc(s, "set_station_status", 3),
        "admin_force_order_ready": lambda s: add_proc(s, "admin_force_order_ready", 1),
        "_station_orders": lambda s: add_proc(s, "_station_orders", 1),
        "_refresh_order_status": lambda s: add_proc(s, "_refresh_order_status", 1),
        "admin_add_menu_item/5": lambda s: add_proc(s, "admin_add_menu_item", 5, replace=4),
        "admin_update_menu_item/5": lambda s: add_proc(s, "admin_update_menu_item", 5, replace=4),
    },
}


def state_after(step):
    """Модель базы после миграций 01…step (0 — пустая база)."""
    state = empty_state()
    for n in range(1, step + 1):
        for feature in FEATURES.get(n, {}).values():
            feature(state)
    return state


# ---------- Разбор предусловий ----------

PATTERNS = [
    (r"to_regclass\('public\.(\w+)'\) is not null",
     lambda s, rel: rel in s["rels"]),
    (r"exists \(select 1 from pg_attribute where attrelid = to_regclass\('public\.(\w+)'\) "
     r"and attname = '(\w+)' and not attisdropped\)",
     lambda s, table, col: table in s["rels"] and (table, col) in s["cols"]),
    (r"exists \(select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = '(\w+)'"
     r"(?: and pronargs > (\d+))?\)",
     lambda s, name, more: any(n > int(more or -1) for n in s["procs"].get(name, []))),
    (r"exists \(select 1 from pg_trigger where tgrelid = to_regclass\('public\.(\w+)'\) and tgname = '(\w+)'\)",
     lambda s, table, name: table in s["rels"] and (table, name) in s["triggers"]),
    (r"exists \(select 1 from pg_constraint where conrelid = to_regclass\('public\.(\w+)'\) and contype = 'c' "
     r"and pg_get_constraintdef\(oid\) ~ '((?:[^']|'')*)'\)",
     lambda s, table, pattern: table in s["rels"] and any(
         re.search(pattern.replace("''", "'"), d) for d in s["checks"].get(table, {}).values())),
]

GUARD_LINE = re.compile(r"if (.+?) then v_found := array_append\(v_found, '([^']+)'\); end if;")


def parse_guard(sql):
    """[(признак, предикат)] из блока предусловия; неизвестный вид условия — ошибка."""
    block = re.search(r"do \$\$ declare v_found text\[\] := '\{\}'; begin (.*?) end; \$\$;", norm(sql))
    assert block, "нет блока предусловия с v_found"
    checks = []
    for cond, label in GUARD_LINE.findall(block.group(1)):
        for pattern, predicate in PATTERNS:
            m = re.fullmatch(pattern, cond)
            if m:
                checks.append((label, lambda s, p=predicate, g=m.groups(): bool(p(s, *g))))
                break
        else:
            raise AssertionError(f"условие не распознано моделью: {cond}")
    return checks


def fires(checks, state):
    return [label for label, predicate in checks if predicate(state)]


# Файл: (последний шаг, после которого повторный запуск ещё допустим; шаги, признаки которых
# должны остановить миграцию)
GUARDED = {
    "01_schema.sql": (7, [8]),
    "02_admin_menu.sql": (6, [7, 8]),
    "04_order_status.sql": (4, [5, 6, 7, 8]),
    "05_kitchen_status.sql": (7, [8]),
    "06_kitchen_menu.sql": (6, [7, 8]),
}

OWN_STEP = {"01_schema.sql": 1, "02_admin_menu.sql": 2, "04_order_status.sql": 4,
            "05_kitchen_status.sql": 5, "06_kitchen_menu.sql": 6}


class GuardModelTest(unittest.TestCase):
    def setUp(self):
        self.guards = {name: parse_guard((SQL / name).read_text(encoding="utf-8")) for name in GUARDED}

    def test_no_stop_on_supported_states(self):
        """Первый запуск и повторный запуск до более новой миграции не блокируются."""
        for name, (last_ok, _) in GUARDED.items():
            for step in range(OWN_STEP[name] - 1, last_ok + 1):
                with self.subTest(file=name, state=f"после {step:02d}"):
                    self.assertEqual(fires(self.guards[name], state_after(step)), [])

    def test_stop_on_each_single_newer_feature(self):
        """Частичная схема: достаточно одного признака более новой миграции."""
        for name, (last_ok, newer) in GUARDED.items():
            for step in newer:
                for feature_name, feature in FEATURES[step].items():
                    state = state_after(last_ok)
                    feature(state)
                    with self.subTest(file=name, feature=f"{step:02d}: {feature_name}"):
                        self.assertTrue(fires(self.guards[name], state))

    def test_stop_on_partial_newer_migrations(self):
        """Недоприменённая новая миграция: только её столбцы, без функций, и наоборот."""
        for name, (last_ok, newer) in GUARDED.items():
            for step in newer:
                features = list(FEATURES[step].items())
                for part in (features[: len(features) // 2], features[len(features) // 2:]):
                    state = state_after(last_ok)
                    for _, feature in part:
                        feature(state)
                    with self.subTest(file=name, step=step, part=[n for n, _ in part]):
                        self.assertTrue(fires(self.guards[name], state))

    def test_stop_on_full_newer_schema(self):
        for name, (last_ok, _) in GUARDED.items():
            for step in range(last_ok + 1, 9):
                with self.subTest(file=name, state=f"после {step:02d}"):
                    self.assertTrue(fires(self.guards[name], state_after(step)))

    def test_every_newer_feature_is_covered(self):
        """Каждый шаг из списка «новее» распознаётся хотя бы одним признаком своего номера."""
        for name, (_, newer) in GUARDED.items():
            labels = [label for label, _ in self.guards[name]]
            for step in newer:
                with self.subTest(file=name, step=step):
                    self.assertTrue(any(label.endswith(f"({step:02d})") for label in labels), labels)

    def test_unknown_condition_is_rejected(self):
        bad = "do $$ declare v_found text[] := '{}'; begin if true then v_found := array_append(v_found, 'x'); end if; end; $$;"
        with self.assertRaises(AssertionError):
            parse_guard(bad)


class GuardPlacementTest(unittest.TestCase):
    """Предусловие — первое действие внутри явной транзакции, до любых изменений."""

    CHANGE = re.compile(r"\b(create|alter|drop|insert|update|delete|revoke|grant)\b", re.I)

    def test_guard_first_in_transaction(self):
        for name in GUARDED:
            text = norm((SQL / name).read_text(encoding="utf-8"))
            with self.subTest(file=name):
                self.assertEqual(text.count("begin;"), 1)
                self.assertTrue(text.rstrip().endswith("commit;") or "commit;" in text)
                begin = text.index("begin;")
                guard = text.index("v_found text[]")
                end_guard = text.index("end; $$;", guard)
                self.assertTrue(text.startswith("begin;"), "до begin; есть команды")
                self.assertLess(begin, guard)
                first_change = self.CHANGE.search(text, begin)
                self.assertGreater(first_change.start(), end_guard,
                                   f"изменение до предусловия: {text[first_change.start():first_change.start() + 60]}")
                self.assertLess(end_guard, text.index("commit;"))
                self.assertIn("raise exception 'ОСТАНОВЛЕНО:", text[guard:end_guard])

    def test_array_append_not_concat(self):
        """`text[] || 'литерал'` в PostgreSQL разбирает литерал как массив — только array_append."""
        for name in GUARDED:
            self.assertNotRegex(norm((SQL / name).read_text(encoding="utf-8")), r"v_found \|\|")

    def test_headers_no_unconditional_rerun_claim(self):
        for name in GUARDED:
            header = (SQL / name).read_text(encoding="utf-8").split("\n-- ====", 1)[0]
            with self.subTest(file=name):
                self.assertNotRegex(header, r"Повторный запуск безопасен\.")
                self.assertIn("docs/DATABASE.md", header)


class KitchenRolesGuardTest(unittest.TestCase):
    """05: удаление CHECK на роль только когда оно не сужает набор ролей."""

    TEXT = (SQL / "05_kitchen_status.sql").read_text(encoding="utf-8")
    NORM = norm(TEXT)

    def test_role_guard_before_drop_loop(self):
        guard = self.NORM.index("ограничение на profiles.role допускает роли вне")
        drop = self.NORM.index("alter table public.profiles drop constraint")
        self.assertLess(guard, drop)
        self.assertLess(self.NORM.index("в profiles есть роли вне"), drop)
        self.assertLess(self.NORM.index("'profiles.role: bar (08)'"), drop)

    def _patterns(self):
        shape = re.search(r"if v_def !~ '((?:[^']|'')*)' then", self.NORM).group(1).replace("''", "'")
        roles = re.search(r"regexp_matches\(v_def, '((?:[^']|'')*)', 'g'\)", self.NORM).group(1).replace("''", "'")
        allowed = re.search(r"where m\[1\] not in \(([^)]*)\)", self.NORM).group(1)
        return shape, roles, {r.strip(" '") for r in allowed.split(",")}

    def _stops(self, definition):
        shape, roles, allowed = self._patterns()
        if not re.search(shape, definition):
            return True
        return bool(set(re.findall(roles, definition)) - allowed)

    def test_role_definitions(self):
        # Регулярные выражения взяты из SQL и выполнены в Python: для этих простых
        # выражений синтаксис POSIX ARE и re совпадает. Сам PostgreSQL здесь не вызывается.
        self.assertEqual(self._patterns()[2], {"cashier", "admin", "kitchen"})
        cases = {
            any_check("role", ["cashier", "admin"]): False,             # первый запуск после 01
            any_check("role", ["cashier", "admin", "kitchen"]): False,  # повторный до 08
            any_check("role", ["cashier", "admin", "kitchen", "bar"]): True,
            any_check("role", ["cashier", "admin", "manager"]): True,   # роль, добавленная вручную
            "CHECK ((role <> 'guest'::text))": True,                    # другой вид — разбор вручную
            "CHECK ((length(role) > 0))": True,
        }
        for definition, stops in cases.items():
            with self.subTest(definition=definition):
                self.assertEqual(self._stops(definition), stops)


class RecoveryIn08Test(unittest.TestCase):
    """08 восстанавливает определения меню после случайного запуска старых 02/06."""

    S07 = (SQL / "07_menu_codes.sql").read_text(encoding="utf-8")
    S08 = (SQL / "08_bar_stations.sql").read_text(encoding="utf-8")
    N08 = norm(S08)

    @staticmethod
    def body(sql, name):
        found = re.findall(rf"create or replace function public\.{name}\(.*?\n\$\$;", sql, re.S)
        assert len(found) == 1, (name, len(found))
        return found[0]

    def test_old_overloads_dropped(self):
        for sig in ("admin_add_menu_item(text, text, numeric)", "admin_add_menu_item(text, text, numeric, integer)",
                    "admin_update_menu_item(bigint, text, text, numeric)"):
            self.assertIn(f"drop function if exists public.{sig};", self.N08, sig)

    def test_single_definition_per_menu_function(self):
        for name in ("admin_add_menu_item", "admin_update_menu_item", "admin_set_menu_item_available",
                     "kitchen_menu"):
            self.body(self.S08, name)

    def test_restored_definitions_match_07(self):
        for name in ("admin_set_menu_item_available", "kitchen_menu"):
            with self.subTest(name=name):
                self.assertEqual(self.body(self.S08, name), self.body(self.S07, name))
                self.assertIn("'code'", self.body(self.S08, name))

    def test_restored_functions_rights(self):
        for sig in ("admin_set_menu_item_available(bigint, boolean)", "kitchen_menu()"):
            self.assertIn(f"revoke all on function public.{sig} from public, anon;", self.N08)
            self.assertIn(f"grant execute on function public.{sig} to authenticated;", self.N08)

    def test_restore_does_not_touch_data(self):
        section = self.S08[self.S08.index("Восстановление определений из 07"):self.S08.index("notify pgrst")]
        sql_only = re.sub(r"--[^\n]*", "", section)
        # единственный update — внутри тела admin_set_menu_item_available (как в 07)
        self.assertEqual(re.findall(r"(?i)\b(update|insert|delete|truncate|alter table|drop)\b", sql_only),
                         ["update"])


class SeedSeparationTest(unittest.TestCase):
    """Тестовое меню — отдельный файл; миграция 01 данные меню не добавляет."""

    SEED = (SQL / "seed" / "demo_menu.sql").read_text(encoding="utf-8")
    N_SEED = norm(SEED)
    NAMES = {"Американо", "Капучино", "Чай чёрный", "Круассан", "Сэндвич с курицей"}

    def test_01_inserts_no_data(self):
        sql_only = norm((SQL / "01_schema.sql").read_text(encoding="utf-8"))
        top_level = re.sub(r"\$\$.*?\$\$", "$$…$$", sql_only)  # без тел функций (create_order вставляет заказы)
        self.assertIn("create or replace function public.create_order", top_level)
        self.assertNotRegex(top_level, r"(?i)\b(insert\s+into|update|delete\s+from|copy)\b")
        for name in self.NAMES:
            self.assertNotIn(name, sql_only)

    def test_seed_guard_before_insert(self):
        self.assertTrue(self.N_SEED.startswith("begin;"))
        self.assertTrue(self.N_SEED.endswith("commit;"))
        guard = self.N_SEED.index("if exists (select 1 from public.menu_items) then raise exception 'ОСТАНОВЛЕНО:")
        table = self.N_SEED.index("if to_regclass('public.menu_items') is null then raise exception 'ОСТАНОВЛЕНО:")
        insert = self.N_SEED.index("insert into public.menu_items")
        self.assertLess(table, guard)
        self.assertLess(guard, insert)

    def test_seed_only_inserts_demo_items(self):
        changes = re.findall(r"(?i)\b(insert into|update|delete|truncate|alter|drop|create|grant|revoke)\b\s*(\S*)",
                             self.N_SEED)
        self.assertEqual(changes, [("insert into", "public.menu_items")])
        values = re.search(r"insert into public\.menu_items \(name, category, price\) values (.*?);", self.N_SEED)
        rows = re.findall(r"\('([^']+)', *'([^']+)', *(\d+\.\d{2})\)", values.group(1))
        self.assertEqual({r[0] for r in rows}, self.NAMES)
        self.assertEqual(len(rows), 5)

    def test_install_order_documented(self):
        db = (ROOT / "docs" / "DATABASE.md").read_text(encoding="utf-8")
        section = db[db.index("### Первая установка"):]
        section = section[:section.index("\n### ", 5)]
        order = ["sql/01_schema.sql", "sql/08_bar_stations.sql", "Пользователи", "sql/seed/demo_menu.sql",
                 "sql/tests/07_menu_codes_checks.sql", "config.ini"]
        positions = [section.index(item) for item in order]
        self.assertEqual(positions, sorted(positions), "шаги первой установки не по порядку")
        self.assertIn("seed не запускать", section)  # рабочий проект
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        install = readme[readme.index("Первая установка базы"):]
        self.assertLess(install.index("sql/08_bar_stations.sql"), install.index("sql/seed/demo_menu.sql"))


if __name__ == "__main__":
    unittest.main()
