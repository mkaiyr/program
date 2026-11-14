-- =====================================================================
-- Этап 8. Кухня отмечает наличие блюд («В продаже» / «Нет в наличии»)
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–05. Данные не меняются.
-- Повторный запуск безопасен только до 07_menu_codes.sql: после 07/08 миграция
-- останавливается до изменений. См. docs/DATABASE.md.
--
-- Используется существующее поле menu_items.is_available — новых колонок нет.
-- Права кухни на таблицу menu_items не выдаются и RLS не меняется:
--   * kitchen_menu() отдаёт только id, название, категорию и доступность (без цен);
--   * kitchen_set_menu_item_available() меняет только is_available.
-- Название, цену и категорию по-прежнему меняет только администратор (02).
-- Касса видит изменения после «Обновить меню»; create_order (01) не примет
-- недоступное блюдо (MENU_CHANGED). Старые заказы не затрагиваются.
-- =====================================================================

begin;

-- ---------- Предусловие: база ещё без 07_menu_codes.sql и 08_bar_stations.sql ----------
-- 06 перезаписывает kitchen_menu версией без кода позиции (код добавлен в 07).
-- Каждый признак проверяется отдельно: остановка и при частично применённой более
-- новой миграции или объектах, созданных вручную. Строки «if … then v_found := array_append(…)»
-- разбирает tests/test_migration_guards.py — формат менять вместе с тестом.

do $$
declare
  v_found text[] := '{}';
begin
  if exists (select 1 from pg_attribute where attrelid = to_regclass('public.menu_items') and attname = 'code' and not attisdropped)
  then v_found := array_append(v_found, 'menu_items.code (07)'); end if;
  if to_regclass('public.menu_item_code_seq') is not null
  then v_found := array_append(v_found, 'menu_item_code_seq (07)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = '_next_menu_item_code')
  then v_found := array_append(v_found, '_next_menu_item_code() (07)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = '_menu_item_code_immutable')
  then v_found := array_append(v_found, '_menu_item_code_immutable() (07)'); end if;
  if exists (select 1 from pg_trigger where tgrelid = to_regclass('public.menu_items') and tgname = 'menu_items_code_immutable')
  then v_found := array_append(v_found, 'триггер menu_items_code_immutable (07)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_add_menu_item' and pronargs > 3)
  then v_found := array_append(v_found, 'admin_add_menu_item с p_code (07)'); end if;
  if exists (select 1 from pg_attribute where attrelid = to_regclass('public.menu_items') and attname = 'station' and not attisdropped)
  then v_found := array_append(v_found, 'menu_items.station (08)'); end if;
  if exists (select 1 from pg_attribute where attrelid = to_regclass('public.order_items') and attname = 'station' and not attisdropped)
  then v_found := array_append(v_found, 'order_items.station (08)'); end if;
  if exists (select 1 from pg_attribute where attrelid = to_regclass('public.order_items') and attname = 'item_code' and not attisdropped)
  then v_found := array_append(v_found, 'order_items.item_code (08)'); end if;
  if to_regclass('public.order_stations') is not null
  then v_found := array_append(v_found, 'order_stations (08)'); end if;
  if exists (select 1 from pg_constraint where conrelid = to_regclass('public.profiles') and contype = 'c' and pg_get_constraintdef(oid) ~ '''bar''::text')
  then v_found := array_append(v_found, 'profiles.role: bar (08)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_bar')
  then v_found := array_append(v_found, 'is_active_bar() (08)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'bar_orders')
  then v_found := array_append(v_found, 'bar_orders() (08)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'set_station_status')
  then v_found := array_append(v_found, 'set_station_status() (08)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_force_order_ready')
  then v_found := array_append(v_found, 'admin_force_order_ready() (08)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = '_station_orders')
  then v_found := array_append(v_found, '_station_orders() (08)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = '_refresh_order_status')
  then v_found := array_append(v_found, '_refresh_order_status() (08)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_add_menu_item' and pronargs > 4)
  then v_found := array_append(v_found, 'admin_add_menu_item с p_station (08)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_update_menu_item' and pronargs > 4)
  then v_found := array_append(v_found, 'admin_update_menu_item с p_station (08)'); end if;

  if cardinality(v_found) > 0 then
    raise exception 'ОСТАНОВЛЕНО: 06_kitchen_menu.sql нельзя запускать после 07/08, найдено: %',
      array_to_string(v_found, ', ');
  end if;
end;
$$;

-- ---------- Меню для кухни (без цен) ----------
-- Ошибки (поле message): NOT_AUTHENTICATED, NOT_ALLOWED
-- Возвращает [{id, name, category, is_available}], по категории и названию.

create or replace function public.kitchen_menu()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_result jsonb;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_kitchen() then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
           'id', m.id, 'name', m.name, 'category', m.category, 'is_available', m.is_available)
         order by m.category, m.name), '[]'::jsonb)
    into v_result
    from public.menu_items m;

  return v_result;
end;
$$;

revoke all on function public.kitchen_menu() from public, anon;
grant execute on function public.kitchen_menu() to authenticated;

-- ---------- Наличие блюда ----------
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_REQUEST, NOT_FOUND
-- Возвращает {id, name, is_available} — без цены.

create or replace function public.kitchen_set_menu_item_available(p_id bigint, p_available boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name text;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_kitchen() then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  if p_id is null or p_available is null then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;

  update public.menu_items set is_available = p_available
   where id = p_id
  returning name into v_name;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;

  return jsonb_build_object('id', p_id, 'name', v_name, 'is_available', p_available);
end;
$$;

revoke all on function public.kitchen_set_menu_item_available(bigint, boolean) from public, anon;
grant execute on function public.kitchen_set_menu_item_available(bigint, boolean) to authenticated;

-- Чтобы PostgREST сразу увидел новые функции
notify pgrst, 'reload schema';

commit;

-- ---------- Проверка после установки ----------
-- select has_function_privilege('anon', 'public.kitchen_menu()', 'execute');   -- ожидается false
-- select has_function_privilege('anon', 'public.kitchen_set_menu_item_available(bigint, boolean)', 'execute');  -- false
-- Полная проверка прав: sql/tests/06_kitchen_menu_checks.sql
