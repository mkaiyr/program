-- =====================================================================
-- Этап 4. Управление меню администратором
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненного 01_schema.sql. Одна транзакция: при ошибке не меняется ничего.
-- Повторный запуск безопасен только до 07_menu_codes.sql: после 07/08 миграция
-- останавливается до изменений. См. docs/DATABASE.md.
--
-- Принцип тот же, что в этапе 1: прямой записи в таблицы у клиента нет,
-- изменения меню — только через функции ниже, которые сами проверяют роль.
-- Позиции не удаляются, а снимаются с продажи (is_available = false).
-- Заказы не затрагиваются: order_items хранит снимок названия и цены.
-- =====================================================================

begin;

-- ---------- Предусловие: база ещё без 07_menu_codes.sql и 08_bar_stations.sql ----------
-- 02 создаёт admin_add_menu_item(text, text, numeric) и admin_update_menu_item с 4 параметрами.
-- После 07/08 это вторые версии рядом с новыми: PostgREST не сможет выбрать функцию
-- (добавление и изменение позиций перестанут работать), а ответы потеряют code.
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
    raise exception 'ОСТАНОВЛЕНО: 02_admin_menu.sql нельзя запускать после 07/08, найдено: %',
      array_to_string(v_found, ', ');
  end if;
end;
$$;

-- ---------- Проверка роли администратора ----------

create or replace function public.is_active_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.profiles p
    where p.id = auth.uid()
      and p.is_active
      and p.role = 'admin'
  );
$$;

revoke all on function public.is_active_admin() from public, anon;
grant execute on function public.is_active_admin() to authenticated;

-- ---------- Чтение: администратор видит все позиции, включая недоступные ----------
-- Политики SELECT объединяются через OR: кассир по-прежнему видит только доступные.

drop policy if exists menu_select_admin on public.menu_items;
create policy menu_select_admin on public.menu_items
  for select to authenticated
  using ((select public.is_active_admin()));

-- ---------- Общая проверка данных позиции ----------
-- Ошибки (поле message): INVALID_NAME, INVALID_CATEGORY, INVALID_PRICE

create or replace function public._check_menu_item_input(p_name text, p_category text, p_price numeric)
returns void
language plpgsql
immutable
set search_path = ''
as $$
begin
  if p_name is null or length(trim(p_name)) not between 1 and 100 then
    raise exception using errcode = '22023', message = 'INVALID_NAME';
  end if;
  if p_category is null or length(trim(p_category)) not between 1 and 50 then
    raise exception using errcode = '22023', message = 'INVALID_CATEGORY';
  end if;
  -- numeric(10, 2) молча округлил бы 1.239 до 1.24, поэтому проверяем явно
  if p_price is null or p_price <= 0 or p_price <> round(p_price, 2) or p_price > 99999999.99 then
    raise exception using errcode = '22023', message = 'INVALID_PRICE';
  end if;
end;
$$;

revoke all on function public._check_menu_item_input(text, text, numeric) from public, anon, authenticated;

-- ---------- Добавление позиции ----------
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_*, NAME_TAKEN

create or replace function public.admin_add_menu_item(p_name text, p_category text, p_price numeric)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row public.menu_items;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  perform public._check_menu_item_input(p_name, p_category, p_price);

  -- Названия, отличающиеся только регистром, тоже считаем дублем.
  -- Блокировка исключает гонку двух одновременных сохранений.
  perform pg_advisory_xact_lock(hashtext('public.menu_items.name'));
  if exists (select 1 from public.menu_items m where lower(m.name) = lower(trim(p_name))) then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
  end if;

  insert into public.menu_items (name, category, price)
  values (trim(p_name), trim(p_category), p_price)
  returning * into v_row;

  return jsonb_build_object('id', v_row.id, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available);
exception
  when unique_violation then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
end;
$$;

revoke all on function public.admin_add_menu_item(text, text, numeric) from public, anon;
grant execute on function public.admin_add_menu_item(text, text, numeric) to authenticated;

-- ---------- Изменение названия, категории и цены ----------
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_*, NAME_TAKEN, NOT_FOUND

create or replace function public.admin_update_menu_item(p_id bigint, p_name text, p_category text, p_price numeric)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row public.menu_items;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  perform public._check_menu_item_input(p_name, p_category, p_price);

  perform pg_advisory_xact_lock(hashtext('public.menu_items.name'));
  if exists (select 1 from public.menu_items m
              where lower(m.name) = lower(trim(p_name)) and m.id <> p_id) then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
  end if;

  update public.menu_items
     set name = trim(p_name), category = trim(p_category), price = p_price
   where id = p_id
  returning * into v_row;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;

  return jsonb_build_object('id', v_row.id, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available);
exception
  when unique_violation then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
end;
$$;

revoke all on function public.admin_update_menu_item(bigint, text, text, numeric) from public, anon;
grant execute on function public.admin_update_menu_item(bigint, text, text, numeric) to authenticated;

-- ---------- Доступность «Есть» / «Нет» ----------
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_REQUEST, NOT_FOUND

create or replace function public.admin_set_menu_item_available(p_id bigint, p_available boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row public.menu_items;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  if p_available is null then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;

  update public.menu_items set is_available = p_available
   where id = p_id
  returning * into v_row;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;

  return jsonb_build_object('id', v_row.id, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available);
end;
$$;

revoke all on function public.admin_set_menu_item_available(bigint, boolean) from public, anon;
grant execute on function public.admin_set_menu_item_available(bigint, boolean) to authenticated;

commit;

-- ---------- Назначение администратора (выполнить отдельно, подставив email) ----------
-- Пользователь уже есть в profiles:
-- update public.profiles set role = 'admin'
--  where id = (select id from auth.users where email = 'admin@example.com');
-- Пользователя ещё нет в profiles (сначала создать в Authentication -> Users):
-- insert into public.profiles (id, full_name, role)
-- select id, 'Администратор', 'admin' from auth.users where email = 'admin@example.com';

-- ---------- Проверки после установки ----------
-- 1) Прямой записи у клиента по-прежнему нет (ожидается пустой результат):
-- select table_name, privilege_type from information_schema.role_table_grants
--  where table_schema = 'public' and grantee in ('anon', 'authenticated')
--    and privilege_type <> 'SELECT';
-- 2) Функции не доступны anon (ожидается false во всех строках):
-- select p.proname, has_function_privilege('anon', p.oid, 'execute') as anon_exec
--   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
--  where n.nspname = 'public' and p.proname like any (array['admin_%', 'is_active_admin', '_check_menu_item_input']);
