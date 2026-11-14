-- =====================================================================
-- Этап 13. Надбавка 10% и аннулирование заказов
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–10. Одна транзакция: при ошибке или остановке не меняется ничего.
-- Повторный запуск безопасен: данные не меняются, определения те же.
--
-- Надбавка:
--   * Ставка фиксированная — 10% от подытога, округление до целого тенге (половина — вверх).
--   * orders.subtotal — сумма позиций, orders.service_percent — ставка, orders.service_amount — надбавка,
--     orders.total = subtotal + service_amount (проверяется ограничением orders_total_parts).
--   * Старые заказы: subtotal = total, надбавка 0 (их итог не меняется).
--   * Цены позиций (order_items.unit_price) надбавку не содержат: кухня и бар её не видят.
--   * Расчёт только на сервере (place_order). Приложение передаёт ожидаемый итог (p_expected_total):
--     если он не совпал с серверным — TOTAL_MISMATCH, заказ не сохраняется.
--   * create_order(uuid, jsonb) остаётся, но теперь вызывает place_order: любой клиент получает надбавку.
--
-- Аннулирование:
--   * void_order(p_order_id, p_reason) — отметка orders.voided_at / voided_by / void_reason.
--     Заказ, его позиции, количество и стоимость остаются в базе; в выручку не входят.
--   * Кассир аннулирует только свои заказы, администратор — любые. Причина обязательна (3–200 символов).
--   * Аннулированный заказ исчезает с экранов кухни и бара (_station_orders).
--
-- Повторный запуск 08 пересоздаёт create_order и _station_orders прежними текстами (без надбавки и
-- без фильтра аннулированных): после него запустить эту миграцию снова (docs/DATABASE.md).
-- =====================================================================

begin;

-- ---------- Предусловие: применены 01–10 ----------

do $$
declare
  v_missing text[] := '{}';
begin
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.orders') and attname = 'deleted_at' and not attisdropped)
  then v_missing := array_append(v_missing, 'orders.deleted_at (10)'); end if;
  if to_regclass('public.order_stations') is null
  then v_missing := array_append(v_missing, 'order_stations (08)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_admin')
  then v_missing := array_append(v_missing, 'is_active_admin() (02)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = '_station_orders')
  then v_missing := array_append(v_missing, '_station_orders() (08)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'create_order')
  then v_missing := array_append(v_missing, 'create_order() (01/08)'); end if;

  if cardinality(v_missing) > 0 then
    raise exception 'ОСТАНОВЛЕНО: 11_service_charge_void.sql требует выполненных 01–10, не найдено: %',
      array_to_string(v_missing, ', ');
  end if;
end;
$$;

-- ---------- Столбцы: подытог, надбавка, аннулирование ----------

alter table public.orders add column if not exists subtotal        numeric(12, 2);
alter table public.orders add column if not exists service_percent numeric(5, 2)  not null default 0;
alter table public.orders add column if not exists service_amount  numeric(12, 2) not null default 0;

-- Старые заказы: подытог равен итогу, надбавки не было
update public.orders set subtotal = total where subtotal is null;
alter table public.orders alter column subtotal set not null;

alter table public.orders add column if not exists voided_at  timestamptz;
alter table public.orders add column if not exists voided_by  uuid references public.profiles (id) on delete set null;
alter table public.orders add column if not exists void_reason text;

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.orders'::regclass and conname = 'orders_total_parts') then
    alter table public.orders add constraint orders_total_parts
      check (subtotal > 0 and service_amount >= 0 and service_percent between 0 and 100
             and total = subtotal + service_amount);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.orders'::regclass and conname = 'orders_void_consistent') then
    alter table public.orders add constraint orders_void_consistent
      check ((voided_at is null) = (void_reason is null)
             and (void_reason is null or length(trim(void_reason)) between 3 and 200));
  end if;
end;
$$;

create index if not exists orders_voided_idx on public.orders (voided_at desc) where voided_at is not null;
create index if not exists orders_created_idx on public.orders (created_at desc);

-- ---------- Оформление заказа с надбавкой ----------
-- Тело — create_order из 08 (проверки состава, актуальности меню, повторной отправки) плюс расчёт
-- подытога, надбавки и итога и сверка с ожидаемым итогом клиента.
-- Ошибки: те же, что у create_order, и TOTAL_MISMATCH (detail = итог сервера).

create or replace function public.place_order(p_client_request_id uuid, p_items jsonb,
                                              p_expected_total numeric default null)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  c_percent    constant numeric := 10;   -- фиксированная надбавка, %
  v_uid        uuid := auth.uid();
  v_count      integer;
  v_invalid    integer;
  v_distinct   integer;
  v_changed    text;
  v_subtotal   numeric(12, 2);
  v_service    numeric(12, 2);
  v_total      numeric(12, 2);
  v_order_id   bigint;
  v_created_at timestamptz;
  v_existing   record;
begin
  if v_uid is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_staff() then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  if p_client_request_id is null then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' then
    raise exception using errcode = '22023', message = 'INVALID_ITEMS';
  end if;

  v_count := jsonb_array_length(p_items);
  if v_count < 1 or v_count > 50 then
    raise exception using errcode = '22023', message = 'INVALID_ITEMS';
  end if;

  -- Повторный запрос: вернуть уже сохранённый заказ, не создавая новый
  select o.id, o.cashier_id, o.subtotal, o.service_percent, o.service_amount, o.total, o.created_at
    into v_existing
    from public.orders o where o.client_request_id = p_client_request_id;
  if found then
    if v_existing.cashier_id <> v_uid then
      raise exception using errcode = '23505', message = 'REQUEST_ID_CONFLICT';
    end if;
    return jsonb_build_object('order_id', v_existing.id, 'subtotal', v_existing.subtotal,
                              'service_percent', v_existing.service_percent,
                              'service_amount', v_existing.service_amount, 'total', v_existing.total,
                              'created_at', v_existing.created_at, 'duplicate', true);
  end if;

  select count(*) filter (where x.menu_item_id is null or x.quantity is null
                            or x.expected_price is null or x.quantity not between 1 and 100),
         count(distinct x.menu_item_id)
    into v_invalid, v_distinct
    from jsonb_to_recordset(p_items) as x(menu_item_id bigint, quantity integer, expected_price numeric);
  if v_invalid > 0 then
    raise exception using errcode = '22023', message = 'INVALID_ITEMS';
  end if;
  if v_distinct <> v_count then
    raise exception using errcode = '22023', message = 'DUPLICATE_ITEM';
  end if;

  perform 1 from public.menu_items m
   where m.id in (select x.menu_item_id
                    from jsonb_to_recordset(p_items) as x(menu_item_id bigint))
   order by m.id
   for share;

  select string_agg(coalesce(m.name, '#' || x.menu_item_id::text), ', ')
    into v_changed
    from jsonb_to_recordset(p_items) as x(menu_item_id bigint, quantity integer, expected_price numeric)
    left join public.menu_items m on m.id = x.menu_item_id
   where m.id is null or not m.is_available or m.price <> x.expected_price;
  if v_changed is not null then
    raise exception using errcode = 'P0001', message = 'MENU_CHANGED', detail = v_changed;
  end if;

  -- Подытог, надбавка и итог считаются только на сервере; round(numeric, 0) — половина вверх
  select sum(m.price * x.quantity) into v_subtotal
    from jsonb_to_recordset(p_items) as x(menu_item_id bigint, quantity integer)
    join public.menu_items m on m.id = x.menu_item_id;
  v_service := round(v_subtotal * c_percent / 100, 0);
  v_total := v_subtotal + v_service;

  if p_expected_total is not null and p_expected_total <> v_total then
    raise exception using errcode = 'P0001', message = 'TOTAL_MISMATCH', detail = v_total::text;
  end if;

  insert into public.orders (cashier_id, client_request_id, subtotal, service_percent, service_amount, total)
  values (v_uid, p_client_request_id, v_subtotal, c_percent, v_service, v_total)
  on conflict (client_request_id) do nothing
  returning id, created_at into v_order_id, v_created_at;

  if v_order_id is null then
    select o.id, o.cashier_id, o.subtotal, o.service_percent, o.service_amount, o.total, o.created_at
      into v_existing
      from public.orders o where o.client_request_id = p_client_request_id;
    if v_existing.cashier_id <> v_uid then
      raise exception using errcode = '23505', message = 'REQUEST_ID_CONFLICT';
    end if;
    return jsonb_build_object('order_id', v_existing.id, 'subtotal', v_existing.subtotal,
                              'service_percent', v_existing.service_percent,
                              'service_amount', v_existing.service_amount, 'total', v_existing.total,
                              'created_at', v_existing.created_at, 'duplicate', true);
  end if;

  -- Позиции хранят цену без надбавки: кухня и бар надбавку не видят
  insert into public.order_items (order_id, menu_item_id, item_name, unit_price, quantity, station, item_code)
  select v_order_id, m.id, m.name, m.price, x.quantity, m.station, m.code
    from jsonb_to_recordset(p_items) as x(menu_item_id bigint, quantity integer)
    join public.menu_items m on m.id = x.menu_item_id;

  insert into public.order_stations (order_id, station)
  select distinct v_order_id, i.station from public.order_items i where i.order_id = v_order_id;

  return jsonb_build_object('order_id', v_order_id, 'subtotal', v_subtotal, 'service_percent', c_percent,
                            'service_amount', v_service, 'total', v_total,
                            'created_at', v_created_at, 'duplicate', false);
end;
$$;

revoke all on function public.place_order(uuid, jsonb, numeric) from public, anon;
grant execute on function public.place_order(uuid, jsonb, numeric) to authenticated;

-- create_order — прежнее имя и параметры, но с надбавкой
create or replace function public.create_order(p_client_request_id uuid, p_items jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  return public.place_order(p_client_request_id, p_items, null);
end;
$$;

revoke all on function public.create_order(uuid, jsonb) from public, anon;
grant execute on function public.create_order(uuid, jsonb) to authenticated;

-- ---------- Аннулирование ----------
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_REASON, ORDER_NOT_FOUND (нет, удалён или чужой заказ кассира)
-- Возвращает {order_id, voided: true, changed, void_reason}; changed = false — заказ уже аннулирован.

create or replace function public.void_order(p_order_id bigint, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid     uuid := auth.uid();
  v_role    text;
  v_reason  text := trim(coalesce(p_reason, ''));
  v_owner   uuid;
  v_deleted timestamptz;
  v_voided  timestamptz;
  v_prev    text;
begin
  if v_uid is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  select p.role into v_role
    from public.profiles p
   where p.id = v_uid and p.is_active and p.role in ('cashier', 'admin');
  if v_role is null then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  if length(v_reason) not between 3 and 200 then
    raise exception using errcode = '22023', message = 'INVALID_REASON';
  end if;

  select o.cashier_id, o.deleted_at, o.voided_at, o.void_reason into v_owner, v_deleted, v_voided, v_prev
    from public.orders o where o.id = p_order_id for update;
  if not found or v_deleted is not null or (v_role = 'cashier' and v_owner <> v_uid) then
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;
  if v_voided is not null then
    return jsonb_build_object('order_id', p_order_id, 'voided', true, 'changed', false, 'void_reason', v_prev);
  end if;

  update public.orders set voided_at = now(), voided_by = v_uid, void_reason = v_reason
   where id = p_order_id;
  return jsonb_build_object('order_id', p_order_id, 'voided', true, 'changed', true, 'void_reason', v_reason);
end;
$$;

revoke all on function public.void_order(bigint, text) from public, anon;
grant execute on function public.void_order(bigint, text) to authenticated;

-- ---------- Заказы станции: без аннулированных ----------
-- Текст из 08 + условие voided_at is null. Надбавки и цен здесь нет и не будет.

create or replace function public._station_orders(p_station text)
returns jsonb
language sql
stable
set search_path = ''
as $$
  select coalesce(jsonb_agg(jsonb_build_object(
           'id', o.id,
           'created_at', o.created_at,
           'status', s.status,
           'items', (select coalesce(jsonb_agg(jsonb_build_object(
                              'code', i.item_code, 'item_name', i.item_name, 'quantity', i.quantity) order by i.id),
                            '[]'::jsonb)
                       from public.order_items i
                      where i.order_id = o.id and i.station = p_station))
         order by o.created_at, o.id), '[]'::jsonb)
    from (select o.* from public.orders o
            join public.order_stations s on s.order_id = o.id
           where s.station = p_station
             and s.status in ('accepted', 'preparing')
             and o.status <> 'completed'
             and o.voided_at is null
           order by o.created_at, o.id
           limit 200) o
    join public.order_stations s on s.order_id = o.id and s.station = p_station;
$$;

revoke all on function public._station_orders(text) from public, anon, authenticated;

notify pgrst, 'reload schema';

commit;
