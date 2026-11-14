-- =====================================================================
-- Этап 6. Раздел «Заказы»: статус заказа
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01_schema.sql, 02_admin_menu.sql и 03_employee_code.sql.
-- Существующие заказы получают статус 'accepted'.
-- Повторный запуск безопасен только до 05_kitchen_status.sql: после 05–08 миграция
-- останавливается до изменений. См. docs/DATABASE.md.
--
-- Статусы и переходы (только вперёд):
--   accepted (Принят) -> ready (Готов) -> completed (Выдан)
-- Статус меняется только функцией set_order_status. Прямых прав INSERT/UPDATE/DELETE
-- на orders у клиента по-прежнему нет, поэтому удалить заказ нельзя.
-- create_order не меняется: новый заказ получает статус по умолчанию.
--
-- Видимость:
--   кассир — только свои заказы (политики из 01 не меняются);
--   администратор — все заказы, их позиции и имена кассиров (новые политики ниже).
-- =====================================================================

begin;

-- ---------- Предусловие: база ещё без 05–08 ----------
-- 04 перезаписывает set_order_status версией без кухни и станций: касса снова
-- переводит заказ в «Готов» в обход приготовления. После 05 это откат правил статусов.
-- Каждый признак проверяется отдельно: остановка и при частично применённой более
-- новой миграции или объектах, созданных вручную. Строки «if … then v_found := array_append(…)»
-- разбирает tests/test_migration_guards.py — формат менять вместе с тестом.

do $$
declare
  v_found text[] := '{}';
begin
  if exists (select 1 from pg_constraint where conrelid = to_regclass('public.orders') and contype = 'c' and pg_get_constraintdef(oid) ~ '''preparing''::text')
  then v_found := array_append(v_found, 'orders.status: preparing (05)'); end if;
  if exists (select 1 from pg_constraint where conrelid = to_regclass('public.profiles') and contype = 'c' and pg_get_constraintdef(oid) ~ '''kitchen''::text')
  then v_found := array_append(v_found, 'profiles.role: kitchen (05)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_kitchen')
  then v_found := array_append(v_found, 'is_active_kitchen() (05)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'kitchen_orders')
  then v_found := array_append(v_found, 'kitchen_orders() (05)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'kitchen_menu')
  then v_found := array_append(v_found, 'kitchen_menu() (06)'); end if;
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'kitchen_set_menu_item_available')
  then v_found := array_append(v_found, 'kitchen_set_menu_item_available() (06)'); end if;
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
    raise exception 'ОСТАНОВЛЕНО: 04_order_status.sql нельзя запускать после 05–08, найдено: %',
      array_to_string(v_found, ', ');
  end if;
end;
$$;

-- ---------- Колонка статуса ----------

alter table public.orders add column if not exists status text not null default 'accepted';

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.orders'::regclass
                    and conname = 'orders_status_check') then
    alter table public.orders add constraint orders_status_check
      check (status in ('accepted', 'ready', 'completed'));
  end if;
end;
$$;

-- Список заказов администратора сортируется по дате
create index if not exists orders_created_idx on public.orders (created_at desc);

-- ---------- Чтение для администратора ----------
-- Политики SELECT объединяются через OR: права кассира не расширяются.

drop policy if exists orders_select_admin on public.orders;
create policy orders_select_admin on public.orders
  for select to authenticated
  using ((select public.is_active_admin()));

drop policy if exists order_items_select_admin on public.order_items;
create policy order_items_select_admin on public.order_items
  for select to authenticated
  using ((select public.is_active_admin()));

-- Нужна, чтобы администратор видел имена кассиров в списке заказов
drop policy if exists profiles_select_admin on public.profiles;
create policy profiles_select_admin on public.profiles
  for select to authenticated
  using ((select public.is_active_admin()));

-- ---------- Смена статуса ----------
-- Ошибки (поле message): NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_STATUS,
--   ORDER_NOT_FOUND, INVALID_TRANSITION (detail = текущий статус)

create or replace function public.set_order_status(p_order_id bigint, p_status text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid     uuid := auth.uid();
  v_current text;
begin
  if v_uid is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_staff() then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  if p_status is null or p_status not in ('accepted', 'ready', 'completed') then
    raise exception using errcode = '22023', message = 'INVALID_STATUS';
  end if;

  -- Та же видимость, что в RLS: кассир — свои заказы, администратор — любые.
  -- Блокировка строки: два одновременных нажатия не проведут заказ дважды.
  select o.status into v_current
    from public.orders o
   where o.id = p_order_id
     and (o.cashier_id = v_uid or public.is_active_admin())
   for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;

  if not ((v_current = 'accepted' and p_status = 'ready')
       or (v_current = 'ready' and p_status = 'completed')) then
    raise exception using errcode = 'P0001', message = 'INVALID_TRANSITION', detail = v_current;
  end if;

  update public.orders set status = p_status where id = p_order_id;

  return jsonb_build_object('order_id', p_order_id, 'status', p_status);
end;
$$;

revoke all on function public.set_order_status(bigint, text) from public, anon;
grant execute on function public.set_order_status(bigint, text) to authenticated;

-- Чтобы PostgREST сразу увидел новую колонку и функцию
notify pgrst, 'reload schema';

commit;

-- ---------- Проверка после установки ----------
-- 1) Статус у существующих заказов (ожидается 'accepted'):
-- select id, cashier_id, total, status, created_at from public.orders order by id;
-- 2) Прямой записи у клиента по-прежнему нет (ожидается пустой результат):
-- select table_name, privilege_type from information_schema.role_table_grants
--  where table_schema = 'public' and grantee in ('anon', 'authenticated')
--    and privilege_type <> 'SELECT';
-- 3) Функция не доступна anon (ожидается false):
-- select has_function_privilege('anon', 'public.set_order_status(bigint, text)', 'execute');
