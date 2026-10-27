-- =====================================================================
-- Этап 10. Роль bar (бармен) и раздельное приготовление на кухне и в баре
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–07. Повторный запуск безопасен.
--
-- Модель:
--   * menu_items.station ('kitchen' | 'bar') — где готовится позиция. Все существующие
--     позиции получают 'kitchen' (поведение не меняется); напитки переводятся в бар
--     вручную (см. конец файла) или в «Управлении меню».
--   * order_items.station — снимок станции на момент заказа (как снимок названия и цены).
--     Старые позиции заказов получают 'kitchen'; названия, цены, количества не меняются.
--   * order_stations (order_id, station, status) — часть заказа для каждой станции.
--     Кухня и бар двигают только свою часть: accepted -> preparing -> ready.
--   * orders.status вычисляет сервер: «Готов» только когда готовы ВСЕ станции,
--     «Готовится» — если хотя бы одна начала. Выдаёт заказ по-прежнему кассир
--     (ready -> completed). Готовый напиток не завершает заказ.
--   * Администратор может явно отметить заказ готовым целиком: admin_force_order_ready.
--     Обычный set_order_status больше не выставляет «Готовится» / «Готов».
--   * order_items.item_code — снимок кода позиции (как название и цена).
--
-- Права:
--   * bar НЕ входит в is_active_staff(): не оформляет продажи, не читает меню с ценами
--     и таблицы заказов. Заказы — только bar_orders(): барные позиции без цен.
--   * Кухня и бар меняют статус только set_station_status() и только своей станции;
--     глобальный set_order_status им больше не разрешён.
--   * order_stations закрыта для клиентов полностью (RLS без политик, прав нет).
-- =====================================================================

begin;

-- ---------- Предусловия (до любых изменений) ----------
-- Ожидаемые значения по миграциям проекта:
--   orders.status: 04 — accepted, ready, completed; 05 — + preparing.
--   profiles.role: profiles_role_check из 05 — ровно {cashier, admin, kitchen}
--                  (01 создала его с {cashier, admin}, 05 заменила).
-- Набор ролей извлекается из фактического определения ограничения
-- (pg_get_constraintdef) и сравнивается как множество:
--   {cashier, admin, kitchen}       -> ограничение заменяется на {cashier, admin, kitchen, bar};
--   {cashier, admin, kitchen, bar}  -> уже применено (повторный запуск), ничего не меняется;
--   иное (нет ограничения, другой набор, другое выражение, ещё CHECK на роль) -> остановка.
-- Решение передаётся следующему блоку через migration08.role_action (только в этой транзакции).
-- При любой остановке транзакция откатывается целиком, база не меняется.

DO $$
DECLARE
    v_unknown text;
    v_current text;
    v_other   text;
    v_roles   text[];
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'menu_items'
          AND column_name = 'code'
    ) THEN
        RAISE EXCEPTION
            'ОСТАНОВЛЕНО: сначала выполните 07_menu_codes.sql';
    END IF;

    SELECT string_agg(DISTINCT status, ', ')
    INTO v_unknown
    FROM public.orders
    WHERE status IS NULL
       OR status NOT IN ('accepted', 'preparing', 'ready', 'completed');

    IF v_unknown IS NOT NULL THEN
        RAISE EXCEPTION
            'ОСТАНОВЛЕНО: в orders есть неизвестные статусы: %',
            v_unknown;
    END IF;

    SELECT string_agg(DISTINCT role, ', ')
    INTO v_unknown
    FROM public.profiles
    WHERE role IS NULL
       OR role NOT IN ('cashier', 'admin', 'kitchen', 'bar');

    IF v_unknown IS NOT NULL THEN
        RAISE EXCEPTION
            'ОСТАНОВЛЕНО: в profiles есть неизвестные роли: %',
            v_unknown;
    END IF;

    SELECT pg_get_constraintdef(oid)
    INTO v_current
    FROM pg_constraint
    WHERE conrelid = 'public.profiles'::regclass
      AND conname = 'profiles_role_check'
      AND contype = 'c';

    -- Ограничение должно быть ровно «role = ANY (ARRAY[...])» без других условий:
    -- иначе набор ролей нельзя определить однозначно
    IF v_current IS NULL
       OR v_current !~ '^CHECK \(\(role = ANY \(ARRAY\[''[a-z_]+''::text(, ''[a-z_]+''::text)*\]\)\)\)$' THEN
        RAISE EXCEPTION
            'ОСТАНОВЛЕНО: profiles_role_check отсутствует или имеет неожиданный вид: %',
            coalesce(v_current, 'ограничения нет');
    END IF;

    SELECT array_agg(DISTINCT m[1] ORDER BY m[1])
    INTO v_roles
    FROM regexp_matches(v_current, '''([a-z_]+)''::text', 'g') AS m;

    IF v_roles = ARRAY['admin', 'cashier', 'kitchen'] THEN
        PERFORM set_config('migration08.role_action', 'add_bar', true);
    ELSIF v_roles = ARRAY['admin', 'bar', 'cashier', 'kitchen'] THEN
        PERFORM set_config('migration08.role_action', 'none', true);
    ELSE
        RAISE EXCEPTION
            'ОСТАНОВЛЕНО: profiles_role_check допускает роли %, ожидаются {cashier, admin, kitchen} (из 05) или с bar',
            v_roles;
    END IF;

    -- Другие CHECK на роль не удаляются и не правятся: их появление требует разбора вручную
    SELECT string_agg(conname, ', ')
    INTO v_other
    FROM pg_constraint
    WHERE conrelid = 'public.profiles'::regclass
      AND contype = 'c'
      AND conname <> 'profiles_role_check'
      AND pg_get_constraintdef(oid) ~ '\mrole\M';

    IF v_other IS NOT NULL THEN
        RAISE EXCEPTION
            'ОСТАНОВЛЕНО: на profiles.role есть другие CHECK-ограничения: %',
            v_other;
    END IF;
END;
$$;

-- ---------- Роль bar ----------
-- Заменяется только известное profiles_role_check (имя то же — дубликата нет);
-- при повторном запуске ограничение уже с bar и не трогается.

DO $$
BEGIN
    IF current_setting('migration08.role_action') = 'add_bar' THEN
        ALTER TABLE public.profiles DROP CONSTRAINT profiles_role_check;
        ALTER TABLE public.profiles ADD CONSTRAINT profiles_role_check
            CHECK (role IN ('cashier', 'admin', 'kitchen', 'bar'));
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.is_active_bar()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.profiles p
        WHERE p.id = auth.uid()
          AND p.is_active
          AND p.role = 'bar'
    );
$$;

REVOKE ALL ON FUNCTION public.is_active_bar() FROM public, anon;
GRANT EXECUTE ON FUNCTION public.is_active_bar() TO authenticated;

-- Вход по коду сотрудника с поддержкой роли bar
CREATE OR REPLACE FUNCTION public.employee_login_email(p_code text)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT u.email::text
    FROM public.profiles p
    JOIN auth.users u ON u.id = p.id
    WHERE p_code ~ '^[0-9]{4,6}$'
      AND p.employee_code = p_code
      AND p.is_active
      AND p.role IN ('cashier', 'admin', 'kitchen', 'bar');
$$;

REVOKE ALL ON FUNCTION public.employee_login_email(text) FROM public;
GRANT EXECUTE ON FUNCTION public.employee_login_email(text) TO anon, authenticated;

-- ---------- Станция позиции меню и снимок в позициях заказа ----------

alter table public.menu_items add column if not exists station text not null default 'kitchen';
-- Значение по умолчанию нужно только чтобы заполнить существующие строки ('kitchen');
-- затем оно снимается: новые позиции заказа получают станцию явно из create_order,
-- и другой путь вставки не сможет молча отправить напиток на кухню.
alter table public.order_items add column if not exists station text not null default 'kitchen';
alter table public.order_items alter column station drop default;

-- ---------- Снимок кода позиции в заказе ----------
-- Как название и цена: код хранится в заказе на момент продажи. Код в меню постоянный
-- (07), поэтому для старых строк он берётся из menu_items по menu_item_id. Связь
-- order_items.menu_item_id -> menu_items.id не меняется.
alter table public.order_items add column if not exists item_code integer;
update public.order_items i
   set item_code = m.code
  from public.menu_items m
 where m.id = i.menu_item_id and i.item_code is null;
alter table public.order_items alter column item_code set not null;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.menu_items'::regclass and conname = 'menu_items_station_check') then
    alter table public.menu_items add constraint menu_items_station_check check (station in ('kitchen', 'bar'));
  end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.order_items'::regclass and conname = 'order_items_station_check') then
    alter table public.order_items add constraint order_items_station_check check (station in ('kitchen', 'bar'));
  end if;
end;
$$;

-- ---------- Части заказа по станциям ----------

create table if not exists public.order_stations (
  order_id bigint not null references public.orders (id) on delete cascade,
  station  text   not null check (station in ('kitchen', 'bar')),
  status   text   not null default 'accepted' check (status in ('accepted', 'preparing', 'ready')),
  primary key (order_id, station)
);
create index if not exists order_stations_active_idx on public.order_stations (station, status);

-- Закрыта для клиентов: читается и меняется только серверными функциями
alter table public.order_stations enable row level security;
revoke all on public.order_stations from public, anon, authenticated;

-- Существующие заказы: одна кухонная часть со статусом заказа (выданные — «готово»)
insert into public.order_stations (order_id, station, status)
select distinct o.id, i.station,
       case o.status when 'completed' then 'ready' else o.status end
  from public.orders o
  join public.order_items i on i.order_id = o.id
on conflict (order_id, station) do nothing;

-- ---------- Общий статус заказа из статусов станций ----------

create or replace function public._refresh_order_status(p_order_id bigint)
returns void
language plpgsql
set search_path = ''
as $$
declare
  v_all_ready boolean;
  v_started   boolean;
  v_status    text;
begin
  select bool_and(s.status = 'ready'), bool_or(s.status in ('preparing', 'ready'))
    into v_all_ready, v_started
    from public.order_stations s
   where s.order_id = p_order_id;
  if v_all_ready is null then
    return;  -- у заказа нет частей по станциям
  end if;
  v_status := case when v_all_ready then 'ready' when v_started then 'preparing' else 'accepted' end;
  update public.orders set status = v_status
   where id = p_order_id and status <> 'completed' and status <> v_status;
end;
$$;

revoke all on function public._refresh_order_status(bigint) from public, anon, authenticated;

-- ---------- Оформление заказа: + станции ----------
-- Определение из 01_schema.sql без изменений, кроме вставки позиций (station, item_code
-- и order_stations). Проверки доступности, цены, суммы и повторной отправки те же.

create or replace function public.create_order(p_client_request_id uuid, p_items jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid        uuid := auth.uid();
  v_count      integer;
  v_invalid    integer;
  v_distinct   integer;
  v_changed    text;
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
  select o.id, o.cashier_id, o.total, o.created_at into v_existing
    from public.orders o where o.client_request_id = p_client_request_id;
  if found then
    if v_existing.cashier_id <> v_uid then
      raise exception using errcode = '23505', message = 'REQUEST_ID_CONFLICT';
    end if;
    return jsonb_build_object('order_id', v_existing.id, 'total', v_existing.total,
                              'created_at', v_existing.created_at, 'duplicate', true);
  end if;

  -- Проверка состава
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

  -- Блокируем позиции меню до конца транзакции (цена не изменится в процессе)
  perform 1 from public.menu_items m
   where m.id in (select x.menu_item_id
                    from jsonb_to_recordset(p_items) as x(menu_item_id bigint))
   order by m.id
   for share;

  -- Актуальность меню: позиция существует, доступна, цена совпадает
  select string_agg(coalesce(m.name, '#' || x.menu_item_id::text), ', ')
    into v_changed
    from jsonb_to_recordset(p_items) as x(menu_item_id bigint, quantity integer, expected_price numeric)
    left join public.menu_items m on m.id = x.menu_item_id
   where m.id is null or not m.is_available or m.price <> x.expected_price;
  if v_changed is not null then
    raise exception using errcode = 'P0001', message = 'MENU_CHANGED', detail = v_changed;
  end if;

  -- Сумма считается только на сервере
  select sum(m.price * x.quantity) into v_total
    from jsonb_to_recordset(p_items) as x(menu_item_id bigint, quantity integer)
    join public.menu_items m on m.id = x.menu_item_id;

  insert into public.orders (cashier_id, client_request_id, total)
  values (v_uid, p_client_request_id, v_total)
  on conflict (client_request_id) do nothing
  returning id, created_at into v_order_id, v_created_at;

  -- Параллельный запрос с тем же ID успел сохранить заказ первым
  if v_order_id is null then
    select o.id, o.cashier_id, o.total, o.created_at into v_existing
      from public.orders o where o.client_request_id = p_client_request_id;
    if v_existing.cashier_id <> v_uid then
      raise exception using errcode = '23505', message = 'REQUEST_ID_CONFLICT';
    end if;
    return jsonb_build_object('order_id', v_existing.id, 'total', v_existing.total,
                              'created_at', v_existing.created_at, 'duplicate', true);
  end if;

  -- station и item_code — снимки станции и кода на момент заказа (как название и цена)
  insert into public.order_items (order_id, menu_item_id, item_name, unit_price, quantity, station, item_code)
  select v_order_id, m.id, m.name, m.price, x.quantity, m.station, m.code
    from jsonb_to_recordset(p_items) as x(menu_item_id bigint, quantity integer)
    join public.menu_items m on m.id = x.menu_item_id;

  -- Части заказа по станциям: кухня и бар готовят свою часть независимо
  insert into public.order_stations (order_id, station)
  select distinct v_order_id, i.station from public.order_items i where i.order_id = v_order_id;

  return jsonb_build_object('order_id', v_order_id, 'total', v_total,
                            'created_at', v_created_at, 'duplicate', false);
end;
$$;

revoke all on function public.create_order(uuid, jsonb) from public, anon;
grant execute on function public.create_order(uuid, jsonb) to authenticated;

-- ---------- Заказы станции (без цен, сумм и кассира) ----------
-- Возвращает [{id, created_at, status, items: [{code, item_name, quantity}]}]:
-- status — статус части этой станции; только позиции этой станции; старые сначала.

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
           order by o.created_at, o.id
           limit 200) o
    join public.order_stations s on s.order_id = o.id and s.station = p_station;
$$;

revoke all on function public._station_orders(text) from public, anon, authenticated;

-- Кухня: тот же контракт, что в 05, но только кухонные позиции (+ код позиции)
create or replace function public.kitchen_orders()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not (public.is_active_kitchen() or public.is_active_admin()) then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  return public._station_orders('kitchen');
end;
$$;

revoke all on function public.kitchen_orders() from public, anon;
grant execute on function public.kitchen_orders() to authenticated;

create or replace function public.bar_orders()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not (public.is_active_bar() or public.is_active_admin()) then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  return public._station_orders('bar');
end;
$$;

revoke all on function public.bar_orders() from public, anon;
grant execute on function public.bar_orders() to authenticated;

-- ---------- Статус части заказа станции ----------
-- kitchen — только 'kitchen', bar — только 'bar', admin — любая.
-- Переходы: accepted -> preparing -> ready. Общий статус заказа пересчитывается.
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_STATION, STATION_NOT_ALLOWED,
--   INVALID_STATUS, ORDER_NOT_FOUND, INVALID_TRANSITION (detail = текущий статус)

create or replace function public.set_station_status(p_order_id bigint, p_station text, p_status text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid     uuid := auth.uid();
  v_role    text;
  v_order   text;
  v_current text;
begin
  if v_uid is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  select p.role into v_role
    from public.profiles p
   where p.id = v_uid and p.is_active and p.role in ('admin', 'kitchen', 'bar');
  if v_role is null then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  if p_station is null or p_station not in ('kitchen', 'bar') then
    raise exception using errcode = '22023', message = 'INVALID_STATION';
  end if;
  if v_role <> 'admin' and v_role <> p_station then
    raise exception using errcode = '42501', message = 'STATION_NOT_ALLOWED';
  end if;
  if p_status is null or p_status not in ('preparing', 'ready') then
    raise exception using errcode = '22023', message = 'INVALID_STATUS';
  end if;

  -- Блокировка заказа: станции одного заказа и выдача не пересчитывают статус одновременно
  select o.status into v_order from public.orders o where o.id = p_order_id for update;
  select s.status into v_current
    from public.order_stations s
   where s.order_id = p_order_id and s.station = p_station
   for update;
  if v_order is null or v_current is null then
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;
  if v_order = 'completed'
     or not ((v_current = 'accepted' and p_status = 'preparing')
          or (v_current = 'preparing' and p_status = 'ready')) then
    raise exception using errcode = 'P0001', message = 'INVALID_TRANSITION', detail = v_current;
  end if;

  update public.order_stations set status = p_status
   where order_id = p_order_id and station = p_station;
  perform public._refresh_order_status(p_order_id);

  return jsonb_build_object('order_id', p_order_id, 'station', p_station, 'status', p_status,
                            'order_status', (select o.status from public.orders o where o.id = p_order_id));
end;
$$;

revoke all on function public.set_station_status(bigint, text, text) from public, anon;
grant execute on function public.set_station_status(bigint, text, text) to authenticated;

-- ---------- Общий статус заказа ----------
-- Сигнатура та же, что в 04/05. «Принят» / «Готовится» / «Готов» теперь вычисляются
-- из станций (_refresh_order_status), поэтому обычным переходом их выставить нельзя:
-- иначе общий статус разошёлся бы со станциями или незаметно обошёл приготовление.
-- Разрешена только выдача ready -> completed:
--   cashier — свои заказы (как раньше), admin — любые.
-- kitchen и bar — TRANSITION_NOT_ALLOWED (меняют только свою часть: set_station_status).
-- Принудительная готовность — только явным admin_force_order_ready (ниже).

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
   where p.id = v_uid and p.is_active and p.role in ('cashier', 'admin', 'kitchen', 'bar');
  if v_role is null then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;

  if v_role in ('kitchen', 'bar') then
    raise exception using errcode = '42501', message = 'TRANSITION_NOT_ALLOWED';
  end if;

  if p_status is null or p_status not in ('accepted', 'preparing', 'ready', 'completed') then
    raise exception using errcode = '22023', message = 'INVALID_STATUS';
  end if;
  if p_status <> 'completed' then
    -- общий статус приготовления выставляют станции, а не прямой переход
    raise exception using errcode = '42501', message = 'TRANSITION_NOT_ALLOWED';
  end if;

  select o.status, o.cashier_id into v_current, v_owner
    from public.orders o
   where o.id = p_order_id
   for update;
  -- Кассир работает только со своими заказами (как в RLS); чужой заказ для него «не найден»
  if not found or (v_role = 'cashier' and v_owner <> v_uid) then
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;

  if v_current <> 'ready' then
    raise exception using errcode = 'P0001', message = 'INVALID_TRANSITION', detail = v_current;
  end if;

  update public.orders set status = 'completed' where id = p_order_id;

  return jsonb_build_object('order_id', p_order_id, 'status', 'completed');
end;
$$;

revoke all on function public.set_order_status(bigint, text) from public, anon;
grant execute on function public.set_order_status(bigint, text) to authenticated;

-- ---------- Принудительная готовность (только администратор, явно) ----------
-- Отмечает «готово» все части станций и заказ целиком — например, если блюдо
-- приготовлено, но станцию забыли отметить. Это отдельное действие с
-- подтверждением в приложении, а не побочный эффект обычного перехода.
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, ORDER_NOT_FOUND,
--   INVALID_TRANSITION (detail = текущий статус: заказ уже готов или выдан)
-- Возвращает {order_id, status: 'ready', forced: true, stations_forced: N}.

create or replace function public.admin_force_order_ready(p_order_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_current text;
  v_forced  integer;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;

  select o.status into v_current from public.orders o where o.id = p_order_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;
  if v_current not in ('accepted', 'preparing') then
    raise exception using errcode = 'P0001', message = 'INVALID_TRANSITION', detail = v_current;
  end if;

  update public.order_stations set status = 'ready'
   where order_id = p_order_id and status <> 'ready';
  get diagnostics v_forced = row_count;
  update public.orders set status = 'ready' where id = p_order_id;

  return jsonb_build_object('order_id', p_order_id, 'status', 'ready', 'forced', true,
                            'stations_forced', v_forced);
end;
$$;

revoke all on function public.admin_force_order_ready(bigint) from public, anon;
grant execute on function public.admin_force_order_ready(bigint) to authenticated;

-- ---------- Управление меню: станция позиции ----------
-- Набор параметров меняется, поэтому прежние версии удаляются (иначе PostgREST
-- увидит две функции с одним именем). Права выдаются заново.
-- Ошибки: как в 07 + INVALID_STATION

drop function if exists public.admin_add_menu_item(text, text, numeric, integer);
-- Версия из 02: удалена в 07, но появляется снова, если 02 запустили повторно после 07/08
-- (до появления предусловий в 02). Без неё вызов с тремя параметрами неоднозначен.
drop function if exists public.admin_add_menu_item(text, text, numeric);

create or replace function public.admin_add_menu_item(p_name text, p_category text, p_price numeric,
                                                      p_code integer default null,
                                                      p_station text default 'kitchen')
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row        public.menu_items;
  v_constraint text;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  perform public._check_menu_item_input(p_name, p_category, p_price);
  if p_code is not null and p_code not between 1 and 99999999 then
    raise exception using errcode = '22023', message = 'INVALID_CODE';
  end if;
  if p_station is null or p_station not in ('kitchen', 'bar') then
    raise exception using errcode = '22023', message = 'INVALID_STATION';
  end if;

  perform pg_advisory_xact_lock(hashtext('public.menu_items.name'));
  if exists (select 1 from public.menu_items m where lower(m.name) = lower(trim(p_name))) then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
  end if;
  if p_code is not null and exists (select 1 from public.menu_items m where m.code = p_code) then
    raise exception using errcode = '23505', message = 'CODE_TAKEN';
  end if;

  insert into public.menu_items (name, category, price, code, station)
  values (trim(p_name), trim(p_category), p_price, coalesce(p_code, public._next_menu_item_code()), p_station)
  returning * into v_row;

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available, 'station', v_row.station);
exception
  when unique_violation then
    get stacked diagnostics v_constraint = constraint_name;
    if sqlerrm = 'CODE_TAKEN' or v_constraint = 'menu_items_code_key' then
      raise exception using errcode = '23505', message = 'CODE_TAKEN';
    end if;
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
end;
$$;

revoke all on function public.admin_add_menu_item(text, text, numeric, integer, text) from public, anon;
grant execute on function public.admin_add_menu_item(text, text, numeric, integer, text) to authenticated;

-- p_station = NULL — станция не меняется. Новая станция действует для новых заказов:
-- уже оформленные позиции хранят свой снимок.
drop function if exists public.admin_update_menu_item(bigint, text, text, numeric);

create or replace function public.admin_update_menu_item(p_id bigint, p_name text, p_category text, p_price numeric,
                                                         p_station text default null)
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
  if p_station is not null and p_station not in ('kitchen', 'bar') then
    raise exception using errcode = '22023', message = 'INVALID_STATION';
  end if;

  perform pg_advisory_xact_lock(hashtext('public.menu_items.name'));
  if exists (select 1 from public.menu_items m
              where lower(m.name) = lower(trim(p_name)) and m.id <> p_id) then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
  end if;

  update public.menu_items
     set name = trim(p_name), category = trim(p_category), price = p_price,
         station = coalesce(p_station, station)
   where id = p_id
  returning * into v_row;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available, 'station', v_row.station);
exception
  when unique_violation then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
end;
$$;

revoke all on function public.admin_update_menu_item(bigint, text, text, numeric, text) from public, anon;
grant execute on function public.admin_update_menu_item(bigint, text, text, numeric, text) to authenticated;

-- ---------- Восстановление определений из 07 ----------
-- Тексты без изменений из 07_menu_codes.sql (сигнатуры те же, новых перегрузок нет,
-- данные не меняются). Нужны, чтобы повторный запуск 08 исправлял последствия
-- случайного повторного запуска старых 02 или 06: 07 после 08 не запускается.

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

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available);
end;
$$;

revoke all on function public.admin_set_menu_item_available(bigint, boolean) from public, anon;
grant execute on function public.admin_set_menu_item_available(bigint, boolean) to authenticated;

-- ---------- Меню кухни: добавлен код (по-прежнему без цен) ----------
-- Сигнатура та же, что в 06.

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
           'id', m.id, 'code', m.code, 'name', m.name, 'category', m.category, 'is_available', m.is_available)
         order by m.category, m.name), '[]'::jsonb)
    into v_result
    from public.menu_items m;

  return v_result;
end;
$$;

revoke all on function public.kitchen_menu() from public, anon;
grant execute on function public.kitchen_menu() to authenticated;

notify pgrst, 'reload schema';

commit;

-- ---------- Ручные шаги после установки (выполнить отдельно) ----------
-- 1) Какие позиции готовит бар (пример — все из категории «Напитки»):
--    update public.menu_items set station = 'bar' where category = 'Напитки';
--    Новые заказы с этими позициями уйдут в бар; уже оформленные не изменятся.
-- 2) Бармен: Authentication -> Users -> Add user (служебный email, например 3001@bistro.local), затем
--    insert into public.profiles (id, full_name, role, employee_code)
--    select id, 'Бармен 1', 'bar', '3001' from auth.users where email = '3001@bistro.local';

-- ---------- Проверка после установки ----------
-- select station, count(*) from public.menu_items group by station;
-- select o.id, o.status, s.station, s.status from public.orders o
--   join public.order_stations s on s.order_id = o.id order by o.id desc limit 20;
-- select has_function_privilege('anon', 'public.bar_orders()', 'execute');            -- false
-- select has_table_privilege('authenticated', 'public.order_stations', 'select');    -- false
-- Полная проверка: sql/tests/08_bar_stations_checks.sql
