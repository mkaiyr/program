-- =====================================================================
-- Этап 17. Архив блюд, восстановление и окончательное удаление
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–14. Одна транзакция: при ошибке или остановке не меняется ничего.
-- Повторный запуск безопасен: данные не меняются, определения те же.
--
-- Модель:
--   * Архивное блюдо — menu_items.archived_at is not null. Ограничение menu_items_archived_state:
--     архивное блюдо всегда «нет в наличии» (is_available = false), подтверждённое и не отклонённое.
--     Поэтому существующие механизмы закрывают архивное блюдо без изменения place_order: кассир его не
--     видит (RLS menu_select_staff), оформление отвечает MENU_CHANGED (is_available), а любая функция,
--     пытающаяся включить наличие (в том числе старые admin_/kitchen_/station_ функции), получает
--     нарушение ограничения (23514). Архивировать можно только подтверждённое блюдо.
--   * Название и код архивного блюда остаются занятыми (индексы 09 не менялись): восстановление
--     никогда не конфликтует. Окончательное удаление их освобождает.
--   * admin_archive_menu_item / admin_restore_menu_item / admin_purge_menu_item — только активный
--     администратор; кассир, кухня и бар получают ADMIN_REQUIRED. Таблицы напрямую не меняются
--     (у клиентов нет прав INSERT/UPDATE/DELETE).
--   * Восстановление возвращает блюдо «в продаже» (is_available = true).
--   * Окончательное удаление — только архивных блюд. Заказы НЕ меняются: order_items хранит снимки
--     названия, цены, кода, станции и (с этой миграции) категории. order_items.menu_item_id становится
--     необязательным (ON DELETE SET NULL): после удаления блюда у старых позиций заказов он NULL, а
--     названия, цены, количество, суммы, надбавка, смены, кухня и бар работают как раньше.
--     Отчёт admin_report берёт категорию из меню, а если блюда уже нет — из снимка order_items.category.
--   * Каждое окончательное удаление записывается в неизменяемый журнал menu_item_purge_log
--     (снимок блюда, число заказов, кто и когда).
--   * station_menu(): кухня и бар архивных блюд не видят; администратор получает archived_at.
--
-- Эта миграция переопределяет функции из 09 (station_menu), 14 (admin_report) и политику menu_select_staff.
-- Повторный запуск 09 или 14 возвращает их прежние тексты: после него запустить эту миграцию снова.
-- =====================================================================

begin;

-- ---------- Предусловие: применены 01–14 ----------

do $$
declare
  v_missing text[] := '{}';
begin
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.menu_items') and attname = 'approved_at' and not attisdropped)
  then v_missing := array_append(v_missing, 'menu_items.approved_at (09)'); end if;
  if to_regclass('public.menu_item_reviews') is null
  then v_missing := array_append(v_missing, 'menu_item_reviews (09)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'station_menu')
  then v_missing := array_append(v_missing, 'station_menu() (09)'); end if;
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.orders') and attname = 'voided_at' and not attisdropped)
  then v_missing := array_append(v_missing, 'orders.voided_at (11)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_report')
  then v_missing := array_append(v_missing, 'admin_report() (14)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_rename_category')
  then v_missing := array_append(v_missing, 'admin_rename_category() (14)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_admin')
  then v_missing := array_append(v_missing, 'is_active_admin() (02)'); end if;

  if cardinality(v_missing) > 0 then
    raise exception 'ОСТАНОВЛЕНО: 15_menu_item_archive.sql требует выполненных 01–14, не найдено: %',
      array_to_string(v_missing, ', ');
  end if;
end;
$$;

-- ---------- Архив: столбцы и ограничение ----------

alter table public.menu_items add column if not exists archived_at timestamptz;
alter table public.menu_items add column if not exists archived_by uuid references public.profiles (id) on delete set null;

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.menu_items'::regclass
                  and conname = 'menu_items_archived_state') then
    alter table public.menu_items add constraint menu_items_archived_state
      check (archived_at is null
             or (not is_available and approved_at is not null and rejected_at is null));
  end if;
end;
$$;

create index if not exists menu_items_archived_idx on public.menu_items (archived_at) where archived_at is not null;

-- Кассир не видит архивные блюда и на уровне политики (текст из 01 + archived_at is null)
drop policy if exists menu_select_staff on public.menu_items;
create policy menu_select_staff on public.menu_items
  for select to authenticated
  using (archived_at is null and is_available and (select public.is_active_staff()));

-- ---------- Снимок категории в позициях заказа ----------
-- Нужен, чтобы после окончательного удаления блюда отчёт по категориям не терял его продажи.

alter table public.order_items add column if not exists category text;
update public.order_items i set category = m.category
  from public.menu_items m
 where m.id = i.menu_item_id and i.category is null;

create or replace function public._order_items_snapshot_category()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.category is null and new.menu_item_id is not null then
    select m.category into new.category from public.menu_items m where m.id = new.menu_item_id;
  end if;
  return new;
end;
$$;

revoke all on function public._order_items_snapshot_category() from public, anon, authenticated;

drop trigger if exists order_items_snapshot_category on public.order_items;
create trigger order_items_snapshot_category
  before insert on public.order_items
  for each row execute function public._order_items_snapshot_category();

-- ---------- order_items.menu_item_id: необязателен, ON DELETE SET NULL ----------

alter table public.order_items alter column menu_item_id drop not null;

do $$
declare
  v_name text;
  v_type "char";
begin
  select c.conname, c.confdeltype into v_name, v_type
    from pg_constraint c
   where c.conrelid = 'public.order_items'::regclass and c.contype = 'f'
     and c.confrelid = 'public.menu_items'::regclass;
  if v_name is null or v_type <> 'n' then
    if v_name is not null then
      execute format('alter table public.order_items drop constraint %I', v_name);
    end if;
    alter table public.order_items add constraint order_items_menu_item_id_fkey
      foreign key (menu_item_id) references public.menu_items (id) on delete set null;
  end if;
end;
$$;

-- ---------- Журнал окончательного удаления блюд (закрыт для клиентов, неизменяем) ----------

create table if not exists public.menu_item_purge_log (
  id             bigint generated always as identity primary key,
  item_id        bigint not null,           -- прежний id блюда (в заказах уже NULL)
  code           integer,
  name           text not null,
  category       text,
  price          numeric(10, 2),
  station        text,
  archived_at    timestamptz,
  orders_count   integer not null,          -- в скольких заказах блюдо было
  purged_by      uuid not null,
  purged_by_name text,
  purged_at      timestamptz not null default now()
);

alter table public.menu_item_purge_log enable row level security;
revoke all on public.menu_item_purge_log from public, anon, authenticated;

create or replace function public._menu_item_purge_log_readonly()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception using errcode = '55000', message = 'PURGE_LOG_READONLY';
end;
$$;

revoke all on function public._menu_item_purge_log_readonly() from public, anon, authenticated;

drop trigger if exists menu_item_purge_log_readonly on public.menu_item_purge_log;
create trigger menu_item_purge_log_readonly
  before update or delete on public.menu_item_purge_log
  for each row execute function public._menu_item_purge_log_readonly();

drop trigger if exists menu_item_purge_log_no_truncate on public.menu_item_purge_log;
create trigger menu_item_purge_log_no_truncate
  before truncate on public.menu_item_purge_log
  for each statement execute function public._menu_item_purge_log_readonly();

-- ---------- Архивировать ----------
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, NOT_FOUND, NOT_APPROVED (ждёт решения), ITEM_REJECTED
-- Возвращает {id, name, archived: true, changed}; changed = false — блюдо уже в архиве.

create or replace function public.admin_archive_menu_item(p_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_item record;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;

  select m.name, m.approved_at, m.rejected_at, m.archived_at into v_item
    from public.menu_items m where m.id = p_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  if v_item.archived_at is not null then
    return jsonb_build_object('id', p_id, 'name', v_item.name, 'archived', true, 'changed', false);
  end if;
  if v_item.rejected_at is not null then
    raise exception using errcode = 'P0001', message = 'ITEM_REJECTED';
  end if;
  if v_item.approved_at is null then
    raise exception using errcode = 'P0001', message = 'NOT_APPROVED';
  end if;

  update public.menu_items
     set is_available = false, archived_at = now(), archived_by = auth.uid()
   where id = p_id;
  return jsonb_build_object('id', p_id, 'name', v_item.name, 'archived', true, 'changed', true);
end;
$$;

revoke all on function public.admin_archive_menu_item(bigint) from public, anon;
grant execute on function public.admin_archive_menu_item(bigint) to authenticated;

-- ---------- Восстановить ----------
-- Возвращает {id, name, archived: false, changed}; блюдо снова «в продаже».
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, NOT_FOUND

create or replace function public.admin_restore_menu_item(p_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_item record;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;

  select m.name, m.archived_at into v_item from public.menu_items m where m.id = p_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  if v_item.archived_at is null then
    return jsonb_build_object('id', p_id, 'name', v_item.name, 'archived', false, 'changed', false);
  end if;

  update public.menu_items
     set archived_at = null, archived_by = null, is_available = true
   where id = p_id;
  return jsonb_build_object('id', p_id, 'name', v_item.name, 'archived', false, 'changed', true);
end;
$$;

revoke all on function public.admin_restore_menu_item(bigint) from public, anon;
grant execute on function public.admin_restore_menu_item(bigint) to authenticated;

-- ---------- Удалить навсегда ----------
-- Только архивное блюдо. Заказы не меняются (см. шапку). Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED,
--   NOT_FOUND, NOT_ARCHIVED. Возвращает {id, name, purged: true, orders_count}.

create or replace function public.admin_purge_menu_item(p_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_item   record;
  v_orders integer;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;

  select m.code, m.name, m.category, m.price, m.station, m.archived_at into v_item
    from public.menu_items m where m.id = p_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  if v_item.archived_at is null then
    raise exception using errcode = 'P0001', message = 'NOT_ARCHIVED';
  end if;

  select count(distinct i.order_id)::integer into v_orders from public.order_items i where i.menu_item_id = p_id;

  insert into public.menu_item_purge_log (item_id, code, name, category, price, station, archived_at,
                                          orders_count, purged_by, purged_by_name)
  values (p_id, v_item.code, v_item.name, v_item.category, v_item.price, v_item.station, v_item.archived_at,
          v_orders, auth.uid(), (select p.full_name from public.profiles p where p.id = auth.uid()));

  delete from public.menu_item_reviews where item_id = p_id;
  -- order_items.menu_item_id -> NULL (ON DELETE SET NULL); снимки названия, цены, кода, станции и категории остаются
  delete from public.menu_items where id = p_id;

  return jsonb_build_object('id', p_id, 'name', v_item.name, 'purged', true, 'orders_count', v_orders);
end;
$$;

revoke all on function public.admin_purge_menu_item(bigint) from public, anon;
grant execute on function public.admin_purge_menu_item(bigint) to authenticated;

-- ---------- Журнал удалений (администратор) ----------

create or replace function public.admin_menu_purge_log()
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
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'item_id', l.item_id, 'code', l.code, 'name', l.name, 'category', l.category, 'price', l.price,
            'station', l.station, 'orders_count', l.orders_count, 'purged_by_name', l.purged_by_name,
            'purged_at', l.purged_at) order by l.purged_at desc, l.id desc), '[]'::jsonb)
            from public.menu_item_purge_log l);
end;
$$;

revoke all on function public.admin_menu_purge_log() from public, anon;
grant execute on function public.admin_menu_purge_log() to authenticated;

-- ---------- Меню станции: без архивных блюд (администратору — с флагом) ----------
-- Текст из 09 + условие по archived_at и поле archived_at в ответе.

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
           'station', m.station, 'reject_reason', r.reject_reason, 'archived_at', m.archived_at)
         order by m.category, m.name, m.id), '[]'::jsonb)
    into v_result
    from public.menu_items m
    left join public.menu_item_reviews r on r.item_id = m.id
   where (v_admin or m.station = v_station)
     and (v_admin or m.archived_at is null);

  return v_result;
end;
$$;

revoke all on function public.station_menu() from public, anon;
grant execute on function public.station_menu() to authenticated;

-- ---------- Отчёт: категория из меню, а у удалённого блюда — из снимка заказа ----------
-- Текст из 14, единственное отличие: coalesce(m.category, i.category, 'Без категории').

create or replace function public.admin_report(p_from timestamptz, p_to timestamptz)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  c_tz constant text := 'Asia/Almaty';
  v_sum record;
  v_void record;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  if p_from is null or p_to is null or p_to <= p_from or p_to - p_from > interval '366 days' then
    raise exception using errcode = '22023', message = 'INVALID_PERIOD';
  end if;

  select count(*) as n, coalesce(sum(o.subtotal), 0) as subtotal, coalesce(sum(o.service_amount), 0) as service,
         coalesce(sum(o.total), 0) as revenue
    into v_sum
    from public.orders o
   where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null;

  select count(*) as n, coalesce(sum(o.total), 0) as total
    into v_void
    from public.orders o
   where o.created_at >= p_from and o.created_at < p_to and o.voided_at is not null;

  return jsonb_build_object(
    'from', p_from, 'to', p_to,
    'orders_count', v_sum.n, 'subtotal_total', v_sum.subtotal, 'service_total', v_sum.service,
    'revenue', v_sum.revenue,
    'average_check', case when v_sum.n > 0 then round(v_sum.revenue / v_sum.n, 2) else 0 end,
    'voided_count', v_void.n, 'voided_total', v_void.total,
    'top_items', (
      select coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'quantity', t.quantity, 'revenue', t.revenue)
                                order by t.quantity desc, t.revenue desc, t.name), '[]'::jsonb)
        from (select i.item_name as name, sum(i.quantity)::integer as quantity, sum(i.line_total) as revenue
                from public.order_items i join public.orders o on o.id = i.order_id
               where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null
               group by i.item_name
               order by sum(i.quantity) desc, sum(i.line_total) desc, i.item_name
               limit 10) t),
    'categories', (
      select coalesce(jsonb_agg(jsonb_build_object('category', t.category, 'quantity', t.quantity, 'revenue', t.revenue)
                                order by t.revenue desc, t.category), '[]'::jsonb)
        from (select coalesce(m.category, i.category, 'Без категории') as category, sum(i.quantity)::integer as quantity,
                     sum(i.line_total) as revenue
                from public.order_items i
                join public.orders o on o.id = i.order_id
                left join public.menu_items m on m.id = i.menu_item_id
               where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null
               group by coalesce(m.category, i.category, 'Без категории')) t),
    'cashiers', (
      select coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'orders', t.orders, 'revenue', t.revenue,
                                                   'service', t.service) order by t.revenue desc, t.name), '[]'::jsonb)
        from (select coalesce(p.full_name, '—') as name, count(*)::integer as orders,
                     sum(o.total) as revenue, sum(o.service_amount) as service
                from public.orders o left join public.profiles p on p.id = o.cashier_id
               where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null
               group by o.cashier_id, p.full_name) t),
    'daily', (
      select coalesce(jsonb_agg(jsonb_build_object('day', t.day, 'orders', t.orders, 'revenue', t.revenue)
                                order by t.day), '[]'::jsonb)
        from (select (o.created_at at time zone c_tz)::date as day, count(*)::integer as orders,
                     sum(o.total) as revenue
                from public.orders o
               where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null
               group by (o.created_at at time zone c_tz)::date) t)
  );
end;
$$;

revoke all on function public.admin_report(timestamptz, timestamptz) from public, anon;
grant execute on function public.admin_report(timestamptz, timestamptz) to authenticated;

notify pgrst, 'reload schema';

commit;
