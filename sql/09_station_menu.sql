-- =====================================================================
-- Этап 11. Меню станций: кухня и бар ведут свои позиции; новые — после решения администратора
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–08. Одна транзакция: при ошибке или остановке не меняется ничего.
-- Повторный запуск безопасен: статусы существующих позиций не меняются (см. «Состояния позиции»).
-- После повторного запуска 08 запустите 09 ещё раз: 08 восстанавливает свои admin_add_menu_item
-- и admin_update_menu_item, в которых отклонённые позиции не учитываются (см. docs/DATABASE.md).
--
-- Состояния позиции (menu_items):
--   ожидает решения — approved_at IS NULL, rejected_at IS NULL, не в продаже;
--   в продаже       — approved_at задан, is_available;
--   нет в наличии   — approved_at задан, NOT is_available;
--   отклонена       — rejected_at задан, approved_at IS NULL, не в продаже (конечное состояние).
--   CHECK menu_items_approved_before_sale и menu_items_single_decision: ожидающая и отклонённая
--   позиции не могут быть в продаже ни одной функцией, в том числе версиями из 02/06/07/08 при их
--   повторном запуске. Поэтому касса их не видит (RLS menu_select_staff требует is_available), а
--   create_order не принимает (проверка is_available) — обе не меняются.
--   Существующие позиции подтверждены (approved_at = created_at) — только при первом применении.
--   Значение по умолчанию approved_at = now() (восстанавливается при каждом запуске): позиции
--   администратора подтверждены сразу; станция явно создаёт ожидающую.
--
-- Отклонённая позиция остаётся в menu_items и в журнале. Её название и код можно снова
-- использовать: уникальность (01: name, 07: menu_items_code_key) заменена частичными уникальными
-- индексами с теми же именами menu_items_name_key, menu_items_code_key WHERE rejected_at IS NULL.
-- Отклонить можно только ожидающую позицию — она ни разу не продавалась, поэтому её нет в заказах
-- (order_items.menu_item_id) и её код не встречается в снимках order_items.item_code.
-- Автоматические коды по-прежнему не выдаются повторно (_next_menu_item_code смотрит все строки).
--
-- Журнал menu_item_reviews: кто и когда предложил позицию (снимок полей), кто и как решил,
-- причина отклонения. Закрыт для клиентов (RLS без политик, прав нет); администратор читает его
-- только функцией admin_menu_reviews().
--
-- Права (станция определяется ролью на сервере, клиент её не передаёт):
--   * station_menu() — kitchen/bar: только позиции своей станции, с ценой (только чтение);
--     admin — все позиции.
--   * station_add_menu_item(name, category, price, code) — kitchen/bar: новая ожидающая позиция
--     своей станции. Проверки ввода и уникальности — как у администратора.
--   * station_set_menu_item_available(id, flag) — kitchen/bar: только позиции своей станции;
--     admin — любые. Только подтверждённые позиции.
--   * admin_approve_menu_item(id, available), admin_reject_menu_item(id, reason),
--     admin_menu_reviews(status) — только admin.
--   * kitchen_set_menu_item_available (06) — только кухонные подтверждённые позиции.
--   * kitchen_menu (08) — только позиции кухни; тот же текст, что в 08.
--   * admin_add_menu_item, admin_update_menu_item (08) — дубли считаются среди неотклонённых;
--     отклонённую позицию изменить нельзя.
--   * Название, категория и цена существующих позиций — по-прежнему только admin_update_menu_item.
-- Не меняются: RLS, права на таблицы menu_items и заказов, create_order.
-- =====================================================================

begin;

-- ---------- Предусловие: применены 01–08 ----------
-- Строки «if … then v_missing := array_append(…)» разбирает tests/test_station_menu_migration.py.

do $$
declare
  v_missing text[] := '{}';
  v_type    text;
  v_fk      text;
begin
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.menu_items') and attname = 'code' and not attisdropped)
  then v_missing := array_append(v_missing, 'menu_items.code (07)'); end if;
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.menu_items') and attname = 'station' and not attisdropped)
  then v_missing := array_append(v_missing, 'menu_items.station (08)'); end if;
  if to_regclass('public.order_stations') is null
  then v_missing := array_append(v_missing, 'order_stations (08)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'set_station_status')
  then v_missing := array_append(v_missing, 'set_station_status() (08)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = '_next_menu_item_code')
  then v_missing := array_append(v_missing, '_next_menu_item_code() (07)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = '_check_menu_item_input')
  then v_missing := array_append(v_missing, '_check_menu_item_input() (02)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_kitchen')
  then v_missing := array_append(v_missing, 'is_active_kitchen() (05)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_admin')
  then v_missing := array_append(v_missing, 'is_active_admin() (02)'); end if;

  if cardinality(v_missing) > 0 then
    raise exception 'ОСТАНОВЛЕНО: 09_station_menu.sql требует выполненных 01–08, не найдено: %',
      array_to_string(v_missing, ', ');
  end if;

  select string_agg(a.attname || ': ' || format_type(a.atttypid, a.atttypmod), ', ') into v_type
    from pg_attribute a
   where a.attrelid = 'public.menu_items'::regclass and a.attname in ('approved_at', 'rejected_at')
     and not a.attisdropped and format_type(a.atttypid, a.atttypmod) <> 'timestamp with time zone';
  if v_type is not null then
    raise exception 'ОСТАНОВЛЕНО: столбцы menu_items неожиданного типа (ожидается timestamptz): %', v_type;
  end if;

  -- Уникальность name и code заменяется частичной: внешние ключи на эти столбцы этого не допускают
  select string_agg(c.conrelid::regclass::text || '.' || c.conname, ', ') into v_fk
    from pg_constraint c
   where c.contype = 'f' and c.confrelid = 'public.menu_items'::regclass
     and exists (select 1 from unnest(c.confkey) k
                   join pg_attribute a on a.attrelid = c.confrelid and a.attnum = k
                  where a.attname in ('name', 'code'));
  if v_fk is not null then
    raise exception 'ОСТАНОВЛЕНО: на menu_items.name или menu_items.code ссылаются внешние ключи: %', v_fk;
  end if;
end;
$$;

-- ---------- Состояния позиции ----------
-- Существующие позиции подтверждаются только при первом применении (столбца ещё нет):
-- при повторном запуске ожидающие и отклонённые позиции станций не должны стать подтверждёнными.

do $$
begin
  if not exists (select 1 from pg_attribute where attrelid = 'public.menu_items'::regclass
                  and attname = 'approved_at' and not attisdropped) then
    alter table public.menu_items add column approved_at timestamptz;
    update public.menu_items set approved_at = created_at;
  end if;
end;
$$;

-- При каждом запуске: без значения по умолчанию admin_add_menu_item создавал бы позицию
-- в продаже без подтверждения, и CHECK ниже не дал бы её сохранить
alter table public.menu_items alter column approved_at set default now();
alter table public.menu_items add column if not exists rejected_at timestamptz;

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.menu_items'::regclass
                  and conname = 'menu_items_approved_before_sale') then
    alter table public.menu_items add constraint menu_items_approved_before_sale
      check (approved_at is not null or not is_available);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.menu_items'::regclass
                  and conname = 'menu_items_single_decision') then
    alter table public.menu_items add constraint menu_items_single_decision
      check (approved_at is null or rejected_at is null);
  end if;
end;
$$;

-- ---------- Журнал решений (закрыт для клиентов) ----------

create table if not exists public.menu_item_reviews (
  item_id       bigint primary key references public.menu_items (id) on delete restrict,
  submitted_by  uuid references public.profiles (id) on delete set null,
  submitted_at  timestamptz not null default now(),
  submitted     jsonb not null,  -- название, категория, цена, код, станция на момент предложения
  decision      text check (decision in ('approved', 'rejected')),
  decided_by    uuid references public.profiles (id) on delete set null,
  decided_at    timestamptz,
  reject_reason text check (reject_reason is null or length(reject_reason) between 1 and 200),
  check ((decision is null) = (decided_at is null)),
  check (reject_reason is null or decision = 'rejected')
);

alter table public.menu_item_reviews enable row level security;
revoke all on public.menu_item_reviews from public, anon, authenticated;

-- ---------- Уникальность названия и кода — среди неотклонённых ----------
-- Ограничения UNIQUE (01: name, 07: menu_items_code_key) заменяются частичными уникальными
-- индексами с теми же именами: сопоставление ошибки CODE_TAKEN по имени menu_items_code_key
-- в функциях 07/08/09 продолжает работать. Данные не меняются.

do $$
declare
  r record;
begin
  for r in
    select c.conname
      from pg_constraint c
     where c.conrelid = 'public.menu_items'::regclass and c.contype = 'u'
       and array_length(c.conkey, 1) = 1
       and (select a.attname from pg_attribute a
             where a.attrelid = c.conrelid and a.attnum = c.conkey[1]) in ('name', 'code')
  loop
    execute format('alter table public.menu_items drop constraint %I', r.conname);
  end loop;
end;
$$;

create unique index if not exists menu_items_name_key on public.menu_items (name) where rejected_at is null;
create unique index if not exists menu_items_code_key on public.menu_items (code) where rejected_at is null;

-- Индексы с этими именами должны быть именно частичными уникальными (иначе — остановка)
do $$
declare
  v_bad text;
begin
  select string_agg(n, ', ') into v_bad
    from unnest(array['menu_items_name_key', 'menu_items_code_key']) as n
   where not exists (select 1 from pg_index i
                      where i.indexrelid = to_regclass('public.' || n)
                        and i.indrelid = 'public.menu_items'::regclass
                        and i.indisunique
                        and pg_get_expr(i.indpred, i.indrelid) ~ 'rejected_at IS NULL');
  if v_bad is not null then
    raise exception 'ОСТАНОВЛЕНО: % — не частичный уникальный индекс menu_items WHERE rejected_at IS NULL', v_bad;
  end if;
end;
$$;

-- ---------- Внутренние функции ----------
-- Вызываются только из функций ниже (SECURITY DEFINER), клиентам закрыты.

-- 'kitchen' / 'bar' для активного повара или бармена, иначе NULL (в том числе для администратора)
create or replace function public._staff_station()
returns text
language sql
stable
set search_path = ''
as $$
  select p.role
    from public.profiles p
   where p.id = auth.uid()
     and p.is_active
     and p.role in ('kitchen', 'bar');
$$;

revoke all on function public._staff_station() from public, anon, authenticated;

create or replace function public._menu_item_status(p_approved_at timestamptz, p_rejected_at timestamptz,
                                                    p_available boolean)
returns text
language sql
immutable
set search_path = ''
as $$
  select case
           when p_rejected_at is not null then 'rejected'
           when p_approved_at is null then 'pending'
           when p_available then 'on_sale'
           else 'out_of_stock'
         end;
$$;

revoke all on function public._menu_item_status(timestamptz, timestamptz, boolean) from public, anon, authenticated;

-- ---------- Меню станции (с ценой, только чтение) ----------
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED
-- Возвращает [{id, code, name, category, price, is_available, status, station, reject_reason}],
-- status: pending | on_sale | out_of_stock | rejected.

create or replace function public.station_menu()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_station text;
  v_admin   boolean;
  v_result  jsonb;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  v_station := public._staff_station();
  v_admin := v_station is null and public.is_active_admin();
  if v_station is null and not v_admin then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
           'id', m.id, 'code', m.code, 'name', m.name, 'category', m.category, 'price', m.price,
           'is_available', m.is_available,
           'status', public._menu_item_status(m.approved_at, m.rejected_at, m.is_available),
           'station', m.station, 'reject_reason', r.reject_reason)
         order by m.category, m.name, m.id), '[]'::jsonb)
    into v_result
    from public.menu_items m
    left join public.menu_item_reviews r on r.item_id = m.id
   where v_admin or m.station = v_station;

  return v_result;
end;
$$;

revoke all on function public.station_menu() from public, anon;
grant execute on function public.station_menu() to authenticated;

-- ---------- Новая позиция станции ----------
-- Станция — по роли; позиция создаётся ожидающей решения (не в продаже). Название и код
-- отклонённой позиции можно использовать снова.
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_NAME, INVALID_CATEGORY, INVALID_PRICE,
--   INVALID_CODE, NAME_TAKEN, CODE_TAKEN

create or replace function public.station_add_menu_item(p_name text, p_category text, p_price numeric,
                                                        p_code integer default null)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_station    text;
  v_row        public.menu_items;
  v_constraint text;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  v_station := public._staff_station();
  if v_station is null then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  perform public._check_menu_item_input(p_name, p_category, p_price);
  if p_code is not null and p_code not between 1 and 99999999 then
    raise exception using errcode = '22023', message = 'INVALID_CODE';
  end if;

  -- Та же блокировка, что у функций администратора: проверки имени и кода без гонок
  perform pg_advisory_xact_lock(hashtext('public.menu_items.name'));
  if exists (select 1 from public.menu_items m
              where lower(m.name) = lower(trim(p_name)) and m.rejected_at is null) then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
  end if;
  if p_code is not null and exists (select 1 from public.menu_items m
                                     where m.code = p_code and m.rejected_at is null) then
    raise exception using errcode = '23505', message = 'CODE_TAKEN';
  end if;

  insert into public.menu_items (name, category, price, code, station, is_available, approved_at)
  values (trim(p_name), trim(p_category), p_price, coalesce(p_code, public._next_menu_item_code()),
          v_station, false, null)
  returning * into v_row;

  insert into public.menu_item_reviews (item_id, submitted_by, submitted)
  values (v_row.id, auth.uid(),
          jsonb_build_object('name', v_row.name, 'category', v_row.category, 'price', v_row.price,
                             'code', v_row.code, 'station', v_row.station));

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available,
                            'status', 'pending', 'station', v_row.station);
exception
  when unique_violation then
    get stacked diagnostics v_constraint = constraint_name;
    if sqlerrm = 'CODE_TAKEN' or v_constraint = 'menu_items_code_key' then
      raise exception using errcode = '23505', message = 'CODE_TAKEN';
    end if;
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
end;
$$;

revoke all on function public.station_add_menu_item(text, text, numeric, integer) from public, anon;
grant execute on function public.station_add_menu_item(text, text, numeric, integer) to authenticated;

-- ---------- Наличие позиции (станция — свои, администратор — любые) ----------
-- Только подтверждённые позиции: «в продаже» <-> «нет в наличии».
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_REQUEST, NOT_FOUND, STATION_NOT_ALLOWED,
--   ITEM_REJECTED, NOT_APPROVED

create or replace function public.station_set_menu_item_available(p_id bigint, p_available boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_station text;
  v_admin   boolean;
  v_row     public.menu_items;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  v_station := public._staff_station();
  v_admin := v_station is null and public.is_active_admin();
  if v_station is null and not v_admin then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  if p_id is null or p_available is null then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;

  select * into v_row from public.menu_items m where m.id = p_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  if not v_admin and v_row.station <> v_station then
    raise exception using errcode = '42501', message = 'STATION_NOT_ALLOWED';
  end if;
  if v_row.rejected_at is not null then
    raise exception using errcode = 'P0001', message = 'ITEM_REJECTED';
  end if;
  if v_row.approved_at is null then
    raise exception using errcode = 'P0001', message = 'NOT_APPROVED';
  end if;

  update public.menu_items set is_available = p_available
   where id = p_id
  returning * into v_row;

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available,
                            'status', public._menu_item_status(v_row.approved_at, v_row.rejected_at, v_row.is_available),
                            'station', v_row.station);
end;
$$;

revoke all on function public.station_set_menu_item_available(bigint, boolean) from public, anon;
grant execute on function public.station_set_menu_item_available(bigint, boolean) to authenticated;

-- ---------- Решение администратора: подтвердить ----------
-- Только ожидающую позицию. p_available = false — подтвердить, но пока не продавать.
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_REQUEST, NOT_FOUND, ITEM_REJECTED, ALREADY_APPROVED

create or replace function public.admin_approve_menu_item(p_id bigint, p_available boolean default true)
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
  if p_id is null or p_available is null then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;

  select * into v_row from public.menu_items m where m.id = p_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  if v_row.rejected_at is not null then
    raise exception using errcode = 'P0001', message = 'ITEM_REJECTED';
  end if;
  if v_row.approved_at is not null then
    raise exception using errcode = 'P0001', message = 'ALREADY_APPROVED';
  end if;

  update public.menu_items set approved_at = now(), is_available = p_available
   where id = p_id
  returning * into v_row;

  insert into public.menu_item_reviews (item_id, submitted_at, submitted, decision, decided_by, decided_at)
  values (v_row.id, v_row.created_at,
          jsonb_build_object('name', v_row.name, 'category', v_row.category, 'price', v_row.price,
                             'code', v_row.code, 'station', v_row.station),
          'approved', auth.uid(), now())
  on conflict (item_id) do update
     set decision = excluded.decision, decided_by = excluded.decided_by, decided_at = excluded.decided_at;

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available,
                            'status', public._menu_item_status(v_row.approved_at, v_row.rejected_at, v_row.is_available),
                            'station', v_row.station);
end;
$$;

revoke all on function public.admin_approve_menu_item(bigint, boolean) from public, anon;
grant execute on function public.admin_approve_menu_item(bigint, boolean) to authenticated;

-- ---------- Решение администратора: отклонить ----------
-- Только ожидающую позицию. Запись остаётся (аудит), в продажу больше не попадает; её название
-- и код освобождаются для новых позиций. Причина — до 200 символов (необязательна).
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_REQUEST, NOT_FOUND, ITEM_REJECTED, ALREADY_APPROVED

create or replace function public.admin_reject_menu_item(p_id bigint, p_reason text default null)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row    public.menu_items;
  v_reason text := nullif(trim(p_reason), '');
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  if p_id is null or (v_reason is not null and length(v_reason) > 200) then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;

  select * into v_row from public.menu_items m where m.id = p_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  if v_row.rejected_at is not null then
    raise exception using errcode = 'P0001', message = 'ITEM_REJECTED';
  end if;
  if v_row.approved_at is not null then
    raise exception using errcode = 'P0001', message = 'ALREADY_APPROVED';
  end if;

  update public.menu_items set rejected_at = now(), is_available = false
   where id = p_id
  returning * into v_row;

  insert into public.menu_item_reviews (item_id, submitted_at, submitted, decision, decided_by, decided_at,
                                        reject_reason)
  values (v_row.id, v_row.created_at,
          jsonb_build_object('name', v_row.name, 'category', v_row.category, 'price', v_row.price,
                             'code', v_row.code, 'station', v_row.station),
          'rejected', auth.uid(), now(), v_reason)
  on conflict (item_id) do update
     set decision = excluded.decision, decided_by = excluded.decided_by, decided_at = excluded.decided_at,
         reject_reason = excluded.reject_reason;

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available,
                            'status', 'rejected', 'station', v_row.station, 'reject_reason', v_reason);
end;
$$;

revoke all on function public.admin_reject_menu_item(bigint, text) from public, anon;
grant execute on function public.admin_reject_menu_item(bigint, text) to authenticated;

-- ---------- Журнал для администратора ----------
-- p_status: pending (ожидают решения), rejected, approved (предложены станцией и подтверждены), all.
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_REQUEST
-- Возвращает [{id, code, name, category, price, station, status, submitted_by_name, submitted_at,
--   submitted, decided_by_name, decided_at, reject_reason}], новые сначала.

create or replace function public.admin_menu_reviews(p_status text default 'pending')
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
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  if p_status is null or p_status not in ('pending', 'rejected', 'approved', 'all') then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
           'id', m.id, 'code', m.code, 'name', m.name, 'category', m.category, 'price', m.price,
           'station', m.station,
           'status', public._menu_item_status(m.approved_at, m.rejected_at, m.is_available),
           'submitted_by_name', ps.full_name, 'submitted_at', r.submitted_at, 'submitted', r.submitted,
           'decided_by_name', pd.full_name, 'decided_at', r.decided_at, 'reject_reason', r.reject_reason)
         order by coalesce(r.submitted_at, m.created_at) desc, m.id desc), '[]'::jsonb)
    into v_result
    from public.menu_items m
    left join public.menu_item_reviews r on r.item_id = m.id
    left join public.profiles ps on ps.id = r.submitted_by
    left join public.profiles pd on pd.id = r.decided_by
   where p_status = 'all'
      or (p_status = 'pending' and m.approved_at is null and m.rejected_at is null)
      or (p_status = 'rejected' and m.rejected_at is not null)
      or (p_status = 'approved' and m.approved_at is not null and r.item_id is not null);

  return v_result;
end;
$$;

revoke all on function public.admin_menu_reviews(text) from public, anon;
grant execute on function public.admin_menu_reviews(text) to authenticated;

-- ---------- Наличие для кухни (06): только кухонные подтверждённые ----------
-- Сигнатура и ответ те же, что в 06: прежняя версия приложения продолжает работать.
-- До 09 кухня могла снять с продажи и барную позицию.
-- Ошибки: как в 06 + STATION_NOT_ALLOWED, ITEM_REJECTED, NOT_APPROVED

create or replace function public.kitchen_set_menu_item_available(p_id bigint, p_available boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name     text;
  v_station  text;
  v_approved boolean;
  v_rejected boolean;
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

  select m.station, m.approved_at is not null, m.rejected_at is not null
    into v_station, v_approved, v_rejected
    from public.menu_items m
   where m.id = p_id
     for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  if v_station <> 'kitchen' then
    raise exception using errcode = '42501', message = 'STATION_NOT_ALLOWED';
  end if;
  if v_rejected then
    raise exception using errcode = 'P0001', message = 'ITEM_REJECTED';
  end if;
  if not v_approved then
    raise exception using errcode = 'P0001', message = 'NOT_APPROVED';
  end if;

  update public.menu_items set is_available = p_available
   where id = p_id
  returning name into v_name;

  return jsonb_build_object('id', p_id, 'name', v_name, 'is_available', p_available);
end;
$$;

revoke all on function public.kitchen_set_menu_item_available(bigint, boolean) from public, anon;
grant execute on function public.kitchen_set_menu_item_available(bigint, boolean) to authenticated;

-- ---------- Меню кухни: только позиции кухни (тот же текст, что в 08) ----------
-- Сигнатура та же, что в 06. Если 08 ещё содержит прежний текст без фильтра, эта версия
-- его исправляет; повторный запуск 08 с текущим файлом устанавливает тот же текст.

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
    from public.menu_items m
   where m.station = 'kitchen';

  return v_result;
end;
$$;

revoke all on function public.kitchen_menu() from public, anon;
grant execute on function public.kitchen_menu() to authenticated;

-- ---------- Функции администратора (08): дубли — среди неотклонённых ----------
-- Тексты из 08 с двумя отличиями: проверки NAME_TAKEN / CODE_TAKEN не учитывают отклонённые
-- позиции (их название и код можно использовать снова), отклонённую позицию изменить нельзя.
-- Повторный запуск 08 возвращает прежние тексты (строже: отклонённые снова занимают название и
-- код) — после него запустите 09 ещё раз. Сигнатуры, параметры по умолчанию и ответы те же.

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
  if exists (select 1 from public.menu_items m where lower(m.name) = lower(trim(p_name)) and m.rejected_at is null) then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
  end if;
  if p_code is not null and exists (select 1 from public.menu_items m where m.code = p_code and m.rejected_at is null) then
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
  if exists (select 1 from public.menu_items m where m.id = p_id and m.rejected_at is not null) then
    raise exception using errcode = 'P0001', message = 'ITEM_REJECTED';
  end if;
  if exists (select 1 from public.menu_items m
              where lower(m.name) = lower(trim(p_name)) and m.id <> p_id and m.rejected_at is null) then
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

notify pgrst, 'reload schema';

commit;

-- ---------- Ручные шаги после установки (только чтение) ----------
-- Позиции, ожидающие решения:
--   select id, code, name, category, price, station, created_at
--     from public.menu_items where approved_at is null and rejected_at is null order by created_at;
-- Решения — функциями admin_approve_menu_item / admin_reject_menu_item от имени администратора,
-- а не прямым update.

-- ---------- Проверка после установки ----------
-- select count(*) from public.menu_items
--  where is_available and (approved_at is null or rejected_at is not null);          -- 0
-- Полная проверка: sql/tests/09_station_menu_checks.sql
