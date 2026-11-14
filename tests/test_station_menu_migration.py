"""Миграция 09: меню станций, решения администратора по новым позициям, повторное использование кода.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

SQL здесь НЕ выполняется. Проверяются текст миграции, её предусловия на модели схемы (та же
модель, что в test_migration_guards.py), согласованность с 08 и модель переходов четырёх
состояний позиции. Права и поведение на PostgreSQL проверяет sql/tests/09_station_menu_checks.sql
на тестовом проекте Supabase (docs/MIGRATION_TEST_PLAN.md).
"""

import itertools
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import test_migration_guards as guards  # noqa: E402

SQL = ROOT / "sql"
TEXT = (SQL / "09_station_menu.sql").read_text(encoding="utf-8")
NORM = guards.norm(TEXT)
S08 = (SQL / "08_bar_stations.sql").read_text(encoding="utf-8")
CHECKS = (SQL / "tests" / "09_station_menu_checks.sql").read_text(encoding="utf-8")
NORM_CHECKS = guards.norm(CHECKS)

NEW_FUNCTIONS = ("station_menu", "station_add_menu_item", "station_set_menu_item_available",
                 "admin_approve_menu_item", "admin_reject_menu_item", "admin_menu_reviews")
INTERNAL = ("_staff_station", "_menu_item_status")
REDEFINED_OLD = ("kitchen_set_menu_item_available", "kitchen_menu", "admin_add_menu_item", "admin_update_menu_item")

REJECTED_FILTER = " and m.rejected_at is null"
REJECTED_GUARD = ("  if exists (select 1 from public.menu_items m where m.id = p_id and m.rejected_at is not null) then\n"
                  "    raise exception using errcode = 'P0001', message = 'ITEM_REJECTED';\n"
                  "  end if;\n")


def raw_body(name, text):
    found = re.findall(rf"create or replace function public\.{name}\(.*?\n\$\$;", text, re.S)
    assert len(found) == 1, (name, len(found))
    return found[0]


def body(name, text=None):
    """Нормализованное тело функции; текст миграции читается при вызове (для мутационных проверок)."""
    return guards.norm(raw_body(name, TEXT if text is None else text))


def defined_functions(text):
    return set(re.findall(r"create or replace function public\.(\w+)\(", text))


# ---------- Модель схемы: признаки 09 ----------

FEATURES_09 = {
    "menu_items.approved_at": lambda s: s["cols"].add(("menu_items", "approved_at")),
    "menu_items.rejected_at": lambda s: s["cols"].add(("menu_items", "rejected_at")),
    "menu_item_reviews": lambda s: s["rels"].add("menu_item_reviews"),
    "approved_before_sale": lambda s: guards.set_check(
        s, "menu_items", "approved", "CHECK (((approved_at IS NOT NULL) OR (NOT is_available)))"),
    "_staff_station": lambda s: guards.add_proc(s, "_staff_station", 0),
    "station_menu": lambda s: guards.add_proc(s, "station_menu", 0),
    "station_add_menu_item": lambda s: guards.add_proc(s, "station_add_menu_item", 4),
    "station_set_menu_item_available": lambda s: guards.add_proc(s, "station_set_menu_item_available", 2),
    "admin_approve_menu_item": lambda s: guards.add_proc(s, "admin_approve_menu_item", 2),
    "admin_reject_menu_item": lambda s: guards.add_proc(s, "admin_reject_menu_item", 2),
}


def state_after_09():
    state = guards.state_after(8)
    for feature in FEATURES_09.values():
        feature(state)
    return state


MISSING_LINE = re.compile(r"if (.+?) then v_missing := array_append\(v_missing, '([^']+)'\); end if;")


def parse_requirements(sql):
    """[(признак, предикат «объект есть»)] из блока предусловия 09."""
    block = re.search(r"do \$\$ declare v_missing text\[\] := '\{\}';.*?begin (.*?) if cardinality", guards.norm(sql))
    assert block, "нет блока предусловия с v_missing"
    checks = []
    for cond, label in MISSING_LINE.findall(block.group(1)):
        m = re.fullmatch(r"not (.+)", cond) or re.fullmatch(r"(to_regclass\('public\.\w+'\)) is null", cond)
        assert m, f"условие не распознано: {cond}"
        positive = m.group(1) if cond.startswith("not ") else m.group(1) + " is not null"
        for pattern, predicate in guards.PATTERNS:
            pm = re.fullmatch(pattern, positive)
            if pm:
                checks.append((label, lambda s, p=predicate, g=pm.groups(): bool(p(s, *g))))
                break
        else:
            raise AssertionError(f"условие не распознано моделью: {positive}")
    return checks


def missing(checks, state):
    return [label for label, present in checks if not present(state)]


class PreconditionTest(unittest.TestCase):
    REQUIRED = parse_requirements(TEXT)

    def test_stops_before_08(self):
        for step in range(0, 8):
            with self.subTest(state=f"после {step:02d}"):
                self.assertTrue(missing(self.REQUIRED, guards.state_after(step)))

    def test_stops_when_part_of_08_missing(self):
        for name in ("menu_items.station", "order_stations", "set_station_status"):
            state = guards.state_after(7)
            for feature_name, feature in guards.FEATURES[8].items():
                if feature_name != name:
                    feature(state)
            with self.subTest(without=name):
                self.assertEqual(len(missing(self.REQUIRED, state)), 1)

    def test_runs_after_08_and_on_rerun(self):
        self.assertEqual(missing(self.REQUIRED, guards.state_after(8)), [])
        self.assertEqual(missing(self.REQUIRED, state_after_09()), [])

    def test_preconditions_before_first_change(self):
        self.assertTrue(NORM.startswith("begin;"))
        self.assertTrue(NORM.endswith("commit;"))
        first_change = re.search(r"\b(create|alter|drop|insert|update|delete|revoke|grant)\b", NORM)
        before = NORM[:first_change.start()]
        for message in ("ОСТАНОВЛЕНО: 09_station_menu.sql требует", "ожидается timestamptz",
                        "ссылаются внешние ключи"):
            self.assertIn(message, before)

    def test_foreign_keys_on_name_or_code_stop(self):
        self.assertIn("where c.contype = 'f' and c.confrelid = 'public.menu_items'::regclass", NORM)
        self.assertIn("where a.attname in ('name', 'code')", NORM)


class OlderMigrationsTest(unittest.TestCase):
    """09 не открывает старым файлам путь назад; общие с 08 функции согласованы."""

    def test_older_files_still_stop_after_09(self):
        for name in guards.GUARDED:
            with self.subTest(file=name):
                self.assertTrue(guards.fires(guards.parse_guard((SQL / name).read_text(encoding="utf-8")),
                                             state_after_09()))

    def test_redefined_old_functions(self):
        older = set()
        for path in sorted(SQL.glob("0[1-8]_*.sql")):
            older |= defined_functions(path.read_text(encoding="utf-8"))
        self.assertEqual(defined_functions(TEXT) & older, set(REDEFINED_OLD))
        self.assertEqual(defined_functions(TEXT) & defined_functions(S08),
                         {"kitchen_menu", "admin_add_menu_item", "admin_update_menu_item"})

    def test_kitchen_menu_same_in_08_and_09(self):
        """Повторный запуск 08 устанавливает тот же kitchen_menu, что 09: фильтр не теряется."""
        self.assertEqual(raw_body("kitchen_menu", TEXT), raw_body("kitchen_menu", S08))
        self.assertIn("where m.station = 'kitchen';", body("kitchen_menu"))

    def test_admin_functions_are_08_plus_rejected_rules(self):
        """09 = текст 08 + учёт отклонённых; других отличий нет."""
        add09 = raw_body("admin_add_menu_item", TEXT)
        self.assertEqual(add09.count(REJECTED_FILTER), 2)
        self.assertEqual(add09.replace(REJECTED_FILTER, ""), raw_body("admin_add_menu_item", S08))
        upd09 = raw_body("admin_update_menu_item", TEXT)
        self.assertEqual(upd09.count(REJECTED_FILTER), 1)
        self.assertEqual(upd09.count(REJECTED_GUARD), 1)
        self.assertEqual(upd09.replace(REJECTED_GUARD, "").replace(REJECTED_FILTER, ""),
                         raw_body("admin_update_menu_item", S08))

    def test_kitchen_availability_not_redefined_after_06(self):
        redefined = [p.name for p in sorted(SQL.glob("0[7-8]_*.sql"))
                     if "kitchen_set_menu_item_available" in defined_functions(p.read_text(encoding="utf-8"))]
        self.assertEqual(redefined, [])
        self.assertIn("06_kitchen_menu.sql", guards.GUARDED)

    def test_code_generator_untouched(self):
        """Автоматические коды по-прежнему не выдаются повторно: _next_menu_item_code (07) не меняется."""
        self.assertNotIn("_next_menu_item_code", defined_functions(TEXT))
        s07 = guards.norm((SQL / "07_menu_codes.sql").read_text(encoding="utf-8"))
        self.assertIn("exit when not exists (select 1 from public.menu_items m where m.code = v_code);", s07)


class StatesTest(unittest.TestCase):
    """Столбцы, ограничения и повторный запуск: статусы существующих позиций не меняются."""

    def test_constraints(self):
        self.assertIn("add constraint menu_items_approved_before_sale check (approved_at is not null or not is_available)",
                      NORM)
        self.assertIn("add constraint menu_items_single_decision check (approved_at is null or rejected_at is null)",
                      NORM)
        self.assertIn("alter table public.menu_items add column if not exists rejected_at timestamptz;", NORM)
        self.assertNotRegex(NORM, r"not valid")  # ограничения проверяются на всех строках

    def test_first_run_only_backfill(self):
        block = re.search(r"if not exists \(select 1 from pg_attribute where attrelid = 'public\.menu_items'::regclass "
                          r"and attname = 'approved_at' and not attisdropped\) then (.*?) end if;", NORM)
        self.assertIsNotNone(block)
        self.assertEqual(block.group(1).strip(), "alter table public.menu_items add column approved_at timestamptz; "
                                                 "update public.menu_items set approved_at = created_at;")
        self.assertEqual(NORM.count("set approved_at = created_at"), 1)
        # Ни одного массового update по menu_items вне блока первого применения
        outside = NORM.replace(block.group(0), "")
        functions_removed = re.sub(r"create or replace function .*? \$\$;", "", outside)
        self.assertNotRegex(functions_removed, r"\bupdate public\.menu_items\b")

    def test_default_restored_every_run(self):
        block = re.search(r"attname = 'approved_at' and not attisdropped\) then (.*?) end if;", NORM).group(1)
        self.assertNotIn("set default", block)
        statement = "alter table public.menu_items alter column approved_at set default now();"
        self.assertIn(statement, NORM)
        self.assertLess(NORM.index(statement), NORM.index("add constraint menu_items_approved_before_sale"))
        self.assertNotIn(statement, re.sub(r"do \$\$.*?\$\$;", "", NORM).replace(statement, "", 1))

    def test_rerun_model(self):
        """Модель шагов 09 с данными: повторный запуск не меняет ни одного статуса."""
        def run_09(rows, column_exists):
            if not column_exists:  # блок первого применения
                for row in rows:
                    row["approved"] = True
            return True  # столбец теперь есть; остальные шаги данные не меняют

        rows = [{"id": 1, "approved": None, "rejected": False, "available": True}]
        exists = run_09(rows, column_exists=False)
        rows += [{"id": 2, "approved": False, "rejected": False, "available": False},   # ожидает
                 {"id": 3, "approved": False, "rejected": True, "available": False},    # отклонена
                 {"id": 4, "approved": True, "rejected": False, "available": False}]    # нет в наличии
        before = [dict(r) for r in rows]
        run_09(rows, column_exists=exists)
        self.assertEqual(rows, before)


class UniquenessTest(unittest.TestCase):
    """Название и код уникальны среди неотклонённых; отклонённая запись сохраняется."""

    def test_constraints_replaced_by_partial_indexes(self):
        drop = NORM.index("execute format('alter table public.menu_items drop constraint %I', r.conname);")
        for name, column in (("menu_items_name_key", "name"), ("menu_items_code_key", "code")):
            with self.subTest(name):
                create = (f"create unique index if not exists {name} on public.menu_items ({column}) "
                          f"where rejected_at is null;")
                self.assertIn(create, NORM)
                self.assertLess(drop, NORM.index(create))
        self.assertIn("and c.contype = 'u' and array_length(c.conkey, 1) = 1", NORM)
        self.assertIn("in ('name', 'code') loop", NORM)
        self.assertIn("pg_get_expr(i.indpred, i.indrelid) ~ 'rejected_at IS NULL'", NORM)
        self.assertLess(NORM.index("add column if not exists rejected_at"), NORM.index("create unique index"))

    def test_no_physical_delete(self):
        self.assertNotRegex(NORM, r"(?i)\bdelete from\b|\btruncate\b|drop table")

    def test_duplicate_checks_skip_rejected(self):
        b = body("station_add_menu_item")
        self.assertIn("where lower(m.name) = lower(trim(p_name)) and m.rejected_at is null)", b)
        self.assertIn("where m.code = p_code and m.rejected_at is null)", b)
        for name in ("station_add_menu_item", "admin_add_menu_item"):
            self.assertIn("v_constraint = 'menu_items_code_key'", body(name))

    def test_reject_only_pending_never_sold(self):
        """Отклоняется только ожидающая позиция — она не продавалась, её код не встречается в заказах."""
        b = body("admin_reject_menu_item")
        self.assertLess(b.index("message = 'ALREADY_APPROVED'"), b.index("update public.menu_items"))
        self.assertIn("set rejected_at = now(), is_available = false", b)


class JournalTest(unittest.TestCase):
    """Автор и решение — в menu_item_reviews, закрытом для клиентов; администратор — через RPC."""

    def test_table_closed(self):
        self.assertIn("create table if not exists public.menu_item_reviews (", NORM)
        self.assertIn("alter table public.menu_item_reviews enable row level security;", NORM)
        self.assertIn("revoke all on public.menu_item_reviews from public, anon, authenticated;", NORM)
        self.assertNotRegex(NORM, r"(?i)\b(create|alter|drop) policy\b")
        self.assertNotRegex(NORM, r"(?i)grant\s+(select|insert|update|delete|all)\s+on\s+(table\s+)?public\.")

    def test_no_author_columns_in_menu_items(self):
        self.assertNotRegex(NORM, r"menu_items add column if not exists (created_by|approved_by)")

    def test_journal_written_and_read_only_by_functions(self):
        self.assertIn("insert into public.menu_item_reviews (item_id, submitted_by, submitted)",
                      body("station_add_menu_item"))
        for name in ("admin_approve_menu_item", "admin_reject_menu_item"):
            self.assertIn("on conflict (item_id) do update", body(name))
        readers = [n for n in defined_functions(TEXT) if "join public.menu_item_reviews" in body(n)]
        self.assertEqual(sorted(readers), ["admin_menu_reviews", "station_menu"])
        b = body("admin_menu_reviews")
        self.assertLess(b.index("message = 'ADMIN_REQUIRED'"), b.index("from public.menu_items m"))
        self.assertIn("'submitted_by_name', ps.full_name", b)
        # станция видит только причину отклонения своей позиции, не автора
        self.assertNotIn("submitted_by", body("station_menu"))


class StationRightsTest(unittest.TestCase):
    """Станция определяется ролью на сервере; чужая станция — STATION_NOT_ALLOWED."""

    def test_no_station_parameter(self):
        for name in NEW_FUNCTIONS:
            with self.subTest(name):
                signature = re.search(rf"function public\.{name}\(([^)]*)\)", NORM).group(1)
                self.assertNotIn("station", signature)

    def test_internal_functions_closed(self):
        self.assertIn("where p.id = auth.uid() and p.is_active and p.role in ('kitchen', 'bar')", body("_staff_station"))
        for name in INTERNAL:
            with self.subTest(name):
                self.assertNotIn("security definer", body(name))
                self.assertRegex(NORM, rf"revoke all on function public\.{name}\([^)]*\) from public, anon, authenticated;")

    def test_station_checks_before_update(self):
        b = body("station_set_menu_item_available")
        self.assertIn("if not v_admin and v_row.station <> v_station then", b)
        k = body("kitchen_set_menu_item_available")
        self.assertIn("if v_station <> 'kitchen' then", k)
        for text in (b, k):
            for message in ("STATION_NOT_ALLOWED", "ITEM_REJECTED", "NOT_APPROVED"):
                self.assertLess(text.index(f"message = '{message}'"), text.index("update public.menu_items"))

    def test_station_menu_filtered(self):
        b = body("station_menu")
        self.assertIn("where v_admin or m.station = v_station", b)
        self.assertIn("'price', m.price", b)
        self.assertIn("v_admin := v_station is null and public.is_active_admin();", b)

    def test_add_uses_role_station_and_same_validation(self):
        b = body("station_add_menu_item")
        self.assertIn("v_station := public._staff_station();", b)
        self.assertIn("perform public._check_menu_item_input(p_name, p_category, p_price);", b)
        self.assertIn("perform pg_advisory_xact_lock(hashtext('public.menu_items.name'));", b)
        self.assertIn("values (trim(p_name), trim(p_category), p_price, coalesce(p_code, public._next_menu_item_code()), "
                      "v_station, false, null)", b)

    def test_admin_only_decisions(self):
        for name in ("admin_approve_menu_item", "admin_reject_menu_item", "admin_menu_reviews"):
            with self.subTest(name):
                b = body(name)
                self.assertIn("if not public.is_active_admin() then", b)
                self.assertLess(b.index("ADMIN_REQUIRED"), b.index("from public.menu_items"))

    def test_rights_and_metadata(self):
        for name in NEW_FUNCTIONS + REDEFINED_OLD:
            with self.subTest(name):
                b = body(name)
                self.assertIn("security definer", b)
                self.assertIn("set search_path = ''", b)
                self.assertIn("if auth.uid() is null then", b)
        for sig in ("station_menu()", "station_add_menu_item(text, text, numeric, integer)",
                    "station_set_menu_item_available(bigint, boolean)", "admin_approve_menu_item(bigint, boolean)",
                    "admin_reject_menu_item(bigint, text)", "admin_menu_reviews(text)",
                    "kitchen_set_menu_item_available(bigint, boolean)", "kitchen_menu()",
                    "admin_add_menu_item(text, text, numeric, integer, text)",
                    "admin_update_menu_item(bigint, text, text, numeric, text)"):
            with self.subTest(sig):
                self.assertIn(f"revoke all on function public.{sig} from public, anon;", NORM)
                self.assertIn(f"grant execute on function public.{sig} to authenticated;", NORM)

    def test_cashier_path_untouched(self):
        self.assertNotIn("create_order", defined_functions(TEXT))
        self.assertNotRegex(NORM, r"(?i)disable row level security|menu_select_staff")

    def test_kitchen_availability_answer_unchanged(self):
        old = guards.norm((SQL / "06_kitchen_menu.sql").read_text(encoding="utf-8"))
        answer = "return jsonb_build_object('id', p_id, 'name', v_name, 'is_available', p_available);"
        self.assertIn(answer, old)
        self.assertIn(answer, body("kitchen_set_menu_item_available"))


# ---------- Модель переходов четырёх состояний ----------
# Правила функций (порядок проверок как в SQL) и ограничения таблицы. Связь модели с текстом SQL
# проверяют StationRightsTest / UniquenessTest; здесь — что из этих правил следуют инварианты.

class Refused(Exception):
    pass


def check_table(item):
    """CHECK menu_items_approved_before_sale и menu_items_single_decision."""
    if item["available"] and not item["approved"]:
        raise Refused("check_violation")
    if item["approved"] and item["rejected"]:
        raise Refused("check_violation")


def apply(op, item, role, flag):
    item = dict(item)
    if op == "station_set":
        if role not in ("kitchen", "bar", "admin"):
            raise Refused("NOT_ALLOWED")
        if role != "admin" and item["station"] != role:
            raise Refused("STATION_NOT_ALLOWED")
        if item["rejected"]:
            raise Refused("ITEM_REJECTED")
        if not item["approved"]:
            raise Refused("NOT_APPROVED")
        item["available"] = flag
    elif op == "kitchen_set":
        if role != "kitchen":
            raise Refused("NOT_ALLOWED")
        if item["station"] != "kitchen":
            raise Refused("STATION_NOT_ALLOWED")
        if item["rejected"]:
            raise Refused("ITEM_REJECTED")
        if not item["approved"]:
            raise Refused("NOT_APPROVED")
        item["available"] = flag
    elif op == "admin_set":  # текст 07/08 без проверок состояния — защищает только ограничение таблицы
        if role != "admin":
            raise Refused("ADMIN_REQUIRED")
        item["available"] = flag
    elif op == "approve":
        if role != "admin":
            raise Refused("ADMIN_REQUIRED")
        if item["rejected"]:
            raise Refused("ITEM_REJECTED")
        if item["approved"]:
            raise Refused("ALREADY_APPROVED")
        item.update(approved=True, available=flag)
    elif op == "reject":
        if role != "admin":
            raise Refused("ADMIN_REQUIRED")
        if item["rejected"]:
            raise Refused("ITEM_REJECTED")
        if item["approved"]:
            raise Refused("ALREADY_APPROVED")
        item.update(rejected=True, available=False)
    check_table(item)
    return item


def status(item):
    if item["rejected"]:
        return "rejected"
    if not item["approved"]:
        return "pending"
    return "on_sale" if item["available"] else "out_of_stock"


class TransitionsModelTest(unittest.TestCase):
    OPS = ("station_set", "kitchen_set", "admin_set", "approve", "reject")
    ROLES = ("kitchen", "bar", "admin", "cashier")

    def explore(self, station):
        start = {"station": station, "approved": False, "rejected": False, "available": False}
        seen, edges, frontier = {status(start): start}, set(), [start]
        while frontier:
            item = frontier.pop()
            for op, role, flag in itertools.product(self.OPS, self.ROLES, (True, False)):
                try:
                    new = apply(op, item, role, flag)
                except Refused:
                    continue
                edges.add((status(item), op, role, flag, status(new)))
                if status(new) not in seen:
                    seen[status(new)] = new
                    frontier.append(new)
        return seen, edges

    def test_reachable_states_and_invariants(self):
        for station in ("kitchen", "bar"):
            with self.subTest(station=station):
                seen, edges = self.explore(station)
                self.assertEqual(set(seen), {"pending", "on_sale", "out_of_stock", "rejected"})
                for item in seen.values():
                    # касса видит только is_available; продаётся только подтверждённая и неотклонённая
                    self.assertFalse(item["available"] and (not item["approved"] or item["rejected"]))
                # отклонённая — конечное состояние
                self.assertFalse([e for e in edges if e[0] == "rejected" and e[4] != "rejected"])
                # из ожидающей выйти может только администратор, и только решением
                self.assertEqual({(e[1], e[2]) for e in edges if e[0] == "pending" and e[4] != "pending"},
                                 {("approve", "admin"), ("reject", "admin")})
                # подтверждённая не отклоняется
                self.assertFalse([e for e in edges if e[0] in ("on_sale", "out_of_stock") and e[4] == "rejected"])

    def test_station_cannot_touch_other_station(self):
        for station, other in (("kitchen", "bar"), ("bar", "kitchen")):
            for state in ({"approved": True, "rejected": False, "available": True},
                          {"approved": False, "rejected": False, "available": False}):
                item = {"station": station, **state}
                with self.subTest(item=item, role=other):
                    for op, flag in itertools.product(("station_set", "kitchen_set", "approve", "reject"), (True, False)):
                        with self.assertRaises(Refused):
                            apply(op, item, other, flag)

    def test_old_admin_function_blocked_by_table(self):
        """admin_set_menu_item_available из 08 (повторный запуск) не может продать ожидающую или отклонённую."""
        for state in ({"approved": False, "rejected": False}, {"approved": False, "rejected": True}):
            item = {"station": "kitchen", "available": False, **state}
            with self.subTest(state=state), self.assertRaises(Refused):
                apply("admin_set", item, "admin", True)

    def test_model_matches_sql_guards(self):
        """Порядок проверок модели совпадает с SQL: станция -> отклонена -> не подтверждена."""
        for name in ("station_set_menu_item_available", "kitchen_set_menu_item_available"):
            b = body(name)
            order = [b.index(f"message = '{m}'") for m in ("STATION_NOT_ALLOWED", "ITEM_REJECTED", "NOT_APPROVED")]
            self.assertEqual(order, sorted(order), name)
        for name in ("admin_approve_menu_item", "admin_reject_menu_item"):
            b = body(name)
            self.assertLess(b.index("message = 'ITEM_REJECTED'"), b.index("message = 'ALREADY_APPROVED'"))


class ChecksFileTest(unittest.TestCase):
    """sql/tests/09_station_menu_checks.sql покрывает согласованные правила (выполняется на тестовом проекте)."""

    def test_transaction_and_result(self):
        self.assertTrue(NORM_CHECKS.startswith("begin;"))
        self.assertTrue(NORM_CHECKS.endswith("rollback;"))
        self.assertIn("select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;", NORM_CHECKS)

    def test_expected_errors_covered(self):
        for message in ("NOT_APPROVED", "ITEM_REJECTED", "ALREADY_APPROVED", "STATION_NOT_ALLOWED", "CODE_TAKEN",
                        "NAME_TAKEN", "INVALID_PRICE", "INVALID_CODE", "INVALID_REQUEST", "ADMIN_REQUIRED",
                        "MENU_CHANGED", "NOT_ALLOWED"):
            with self.subTest(message):
                self.assertIn(f"if sqlerrm <> '{message}' then raise;", NORM_CHECKS)
        self.assertEqual(NORM_CHECKS.count("exception when check_violation then null;"), 2)
        self.assertEqual(NORM_CHECKS.count("exception when insufficient_privilege then null;"), 2)

    def test_scenarios_covered(self):
        for fragment in ("'FAIL: позиции до 09 (или администратора) не подтверждены с approved_at = created_at'",
                         "'FAIL: kitchen_menu показывает кухне барную позицию'",
                         # норма схлопывает пробелы; в файле причина передаётся с пробелами по краям
                         "public.admin_reject_menu_item(current_setting('test.to_reject')::bigint, ' Нет в концепции меню ')",
                         "'FAIL: не удалось повторно использовать название и код отклонённой позиции'",
                         "'FAIL: код занят действующей позицией, но принят'",
                         "'FAIL: create_order принял отклонённую позицию'",
                         "'FAIL: кассир видит отклонённую или ожидающую позицию'",
                         "'FAIL: отклонённая позиция удалена или код занят дважды среди действующих'",
                         "'FAIL: кухня читает журнал напрямую'",
                         "'FAIL: изменились позиции исторических заказов'"):
            with self.subTest(fragment):
                self.assertIn(fragment, NORM_CHECKS)
        self.assertIn("'  Нет в концепции меню  '", CHECKS)  # trim причины проверяется
        self.assertIn("шаг 9.4 плана", CHECKS)               # повторный запуск — шаг плана со снимками


if __name__ == "__main__":
    unittest.main()
