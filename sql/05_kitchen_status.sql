-- =====================================================================
-- Этап 7. Кухня: статус «Готовится» и роль kitchen
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–04. Повторный запуск безопасен только до 08_bar_stations.sql:
-- после неё (или если ограничение ролей допускает другие роли) миграция останавливается
-- до изменений. См. docs/DATABASE.md.
-- Существующие заказы и их статусы не меняются (все старые значения допустимы).
--
-- Статусы: accepted (Принят) -> preparing (Готовится) -> ready (Готов) -> completed (Выдан)
-- Кто какой переход выполняет (проверяет set_order_status):
--   kitchen: accepted -> preparing, preparing -> ready   (любой заказ)
--   cashier: ready -> completed                          (только свои заказы)
--   admin:   все переходы                                (любой заказ)
--
-- Права кухни:
--   * is_active_staff() НЕ включает kitchen: кухня не может оформлять заказы
--     (create_order), не видит меню и цены через RLS и не читает таблицы заказов.
--   * Заказы для приготовления кухня получает только функцией kitchen_orders():
--     номер, время, статус, названия блюд и количество — без сумм, цен и кассира.
--   * Прав INSERT/UPDATE/DELETE на таблицы по-прежнему нет ни у одной роли клиента.
-- =====================================================================

begin;

-- ---------- Предусловие: база ещё без 08_bar_stations.sql ----------
-- 05 заменяет ограничение ролей на {cashier, admin, kitchen} и перезаписывает
-- employee_login_email, kitchen_orders и set_order_status версиями без бара и станций.
-- После 08 это удалило бы роль bar и вернуло старое управление общим статусом.
-- Каждый признак проверяется отдельно: остановка и при частично применённой более
-- новой миграции или объектах, созданных вручную. Строки «if … then v_found := array_append(…)»
-- разбирает tests/test_migration_guards.py — формат менять вместе с тестом.

do $$
declare
  v_found text[] := '{}';
begin
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
    raise exception 'ОСТАНОВЛЕНО: 05_kitchen_status.sql нельзя запускать после 08_bar_stations.sql, найдено: %',
      array_to_string(v_found, ', ');
  end if;
end;
$$;

-- ---------- Предусловие: ограничения ролей ----------
-- Ниже удаляются ВСЕ CHECK на profiles.role и создаётся одно на {cashier, admin, kitchen}.
-- Это безопасно, только если каждое из них имеет вид «role = ANY (ARRAY[...])» и не
-- допускает других ролей (bar из 08, роли, добавленные вручную), а в данных таких ролей нет.
-- Иначе остановка: удаление сузило бы набор ролей. Регулярное выражение — то же, что в 08.

do $$
declare
  v_def   text;
  v_extra text;
begin
  for v_def in select pg_get_constraintdef(oid) from pg_constraint
                where conrelid = 'public.profiles'::regclass
                  and contype = 'c'
                  and pg_get_constraintdef(oid) ~ '\mrole\M'
  loop
    if v_def !~ '^CHECK \(\(role = ANY \(ARRAY\[''[a-z_]+''::text(, ''[a-z_]+''::text)*\]\)\)\)$' then
      raise exception 'ОСТАНОВЛЕНО: ограничение на profiles.role имеет неожиданный вид: %', v_def;
    end if;
    select string_agg(m[1], ', ') into v_extra
      from regexp_matches(v_def, '''([a-z_]+)''::text', 'g') as m
     where m[1] not in ('cashier', 'admin', 'kitchen');
    if v_extra is not null then
      raise exception 'ОСТАНОВЛЕНО: ограничение на profiles.role допускает роли вне {cashier, admin, kitchen}: %', v_extra;
    end if;
  end loop;

  select string_agg(distinct role, ', ') into v_extra
    from public.profiles
   where role not in ('cashier', 'admin', 'kitchen');
  if v_extra is not null then
    raise exception 'ОСТАНОВЛЕНО: в profiles есть роли вне {cashier, admin, kitchen}: %', v_extra;
  end if;
end;
$$;

-- ---------- Роль kitchen в profiles ----------
-- Ограничение роли из 01 создано без имени, поэтому ищем его по определению.

do $$
declare
  r record;
begin
  for r in select conname from pg_constraint
            where conrelid = 'public.profiles'::regclass
              and contype = 'c'
              and pg_get_constraintdef(oid) ~ '\mrole\M'
  loop
    execute format('alter table public.profiles drop constraint %I', r.conname);
  end loop;
  alter table public.profiles add constraint profiles_role_check
    check (role in ('cashier', 'admin', 'kitchen'));
end;
$$;

-- ---------- Статус preparing ----------

alter table public.orders drop constraint if exists orders_status_check;
alter table public.orders add constraint orders_status_check
  check (status in ('accepted', 'preparing', 'ready', 'completed'));

-- Активные заказы для экрана кухни
create index if not exists orders_active_idx on public.orders (created_at)
  where status in ('accepted', 'preparing');

-- ---------- Проверка роли кухни ----------

create or replace function public.is_active_kitchen()
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
      and p.role = 'kitchen'
  );
$$;

revoke all on function public.is_active_kitchen() from public, anon;
grant execute on function public.is_active_kitchen() to authenticated;

-- ---------- Вход по коду: разрешить роль kitchen ----------
-- То же определение, что в 03_employee_code.sql, добавлена только роль kitchen.

create or replace function public.employee_login_email(p_code text)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select u.email::text
    from public.profiles p
    join auth.users u on u.id = p.id
   where p_code ~ '^[0-9]{4,6}$'
     and p.employee_code = p_code
     and p.is_active
     and p.role in ('cashier', 'admin', 'kitchen');
$$;

revoke all on function public.employee_login_email(text) from public;
grant execute on function public.employee_login_email(text) to anon, authenticated;

-- ---------- Активные заказы для кухни ----------
-- Ошибки (поле message): NOT_AUTHENTICATED, NOT_ALLOWED
-- Возвращает [{id, created_at, status, items: [{item_name, quantity}]}], старые сначала.

create or replace function public.kitchen_orders()
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
  if not (public.is_active_kitchen() or public.is_active_admin()) then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
           'id', o.id,
           'created_at', o.created_at,
           'status', o.status,
           'items', (select coalesce(jsonb_agg(jsonb_build_object(
                              'item_name', i.item_name, 'quantity', i.quantity) order by i.id), '[]'::jsonb)
                       from public.order_items i
                      where i.order_id = o.id))
         order by o.created_at, o.id), '[]'::jsonb)
    into v_result
    from (select * from public.orders
           where status in ('accepted', 'preparing')
           order by created_at, id
           limit 200) o;

  return v_result;
end;
$$;

revoke all on function public.kitchen_orders() from public, anon;
grant execute on function public.kitchen_orders() to authenticated;

-- ---------- Смена статуса с учётом роли ----------
-- Сигнатура та же, что в 04, поэтому вызов из приложения не меняется.
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_STATUS, ORDER_NOT_FOUND,
--   INVALID_TRANSITION (detail = текущий статус), TRANSITION_NOT_ALLOWED

create or replace function public.set_order_status(p_order_id bigint, p_status text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid     uuid := auth.uid();
  v_role    text;
  v_current text;
  v_owner   uuid;
begin
  if v_uid is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;

  select p.role into v_role
    from public.profiles p
   where p.id = v_uid and p.is_active and p.role in ('cashier', 'admin', 'kitchen');
  if v_role is null then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;

  if p_status is null or p_status not in ('accepted', 'preparing', 'ready', 'completed') then
    raise exception using errcode = '22023', message = 'INVALID_STATUS';
  end if;

  -- Блокировка строки: два одновременных нажатия не проведут заказ дважды
  select o.status, o.cashier_id into v_current, v_owner
    from public.orders o
   where o.id = p_order_id
   for update;
  -- Кассир работает только со своими заказами (как в RLS); чужой заказ для него «не найден»
  if not found or (v_role = 'cashier' and v_owner <> v_uid) then
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;

  if not ((v_current = 'accepted'  and p_status = 'preparing')
       or (v_current = 'preparing' and p_status = 'ready')
       or (v_current = 'ready'     and p_status = 'completed')) then
    raise exception using errcode = 'P0001', message = 'INVALID_TRANSITION', detail = v_current;
  end if;

  if not (v_role = 'admin'
       or (v_role = 'kitchen' and p_status in ('preparing', 'ready'))
       or (v_role = 'cashier' and p_status = 'completed')) then
    raise exception using errcode = '42501', message = 'TRANSITION_NOT_ALLOWED';
  end if;

  update public.orders set status = p_status where id = p_order_id;

  return jsonb_build_object('order_id', p_order_id, 'status', p_status);
end;
$$;

revoke all on function public.set_order_status(bigint, text) from public, anon;
grant execute on function public.set_order_status(bigint, text) to authenticated;

-- Чтобы PostgREST сразу увидел новую функцию
notify pgrst, 'reload schema';

commit;

-- ---------- Назначение повара (выполнить отдельно) ----------
-- 1) Authentication -> Users -> Add user (служебный email, например 2001@bistro.local).
-- 2) insert into public.profiles (id, full_name, role, employee_code)
--    select id, 'Повар 1', 'kitchen', '2001' from auth.users where email = '2001@bistro.local';

-- ---------- Проверка после установки ----------
-- select status, count(*) from public.orders group by status;          -- статусы не изменились
-- select conname, pg_get_constraintdef(oid) from pg_constraint
--  where conrelid in ('public.profiles'::regclass, 'public.orders'::regclass) and contype = 'c';
-- select has_function_privilege('anon', 'public.kitchen_orders()', 'execute');   -- ожидается false
-- Полная проверка прав: sql/tests/05_kitchen_checks.sql
