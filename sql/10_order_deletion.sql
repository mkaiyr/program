-- =====================================================================
-- Этап 12. Удаление выданных заказов: «Удалённые заказы» и окончательное удаление с журналом
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–09. Одна транзакция: при ошибке или остановке не меняется ничего.
-- Повторный запуск безопасен: данные не меняются, определения те же.
--
-- Модель:
--   * Удалять можно только выданный заказ (orders.status = 'completed' — статус «Выдан» из 04/05).
--     Выданный заказ уже не виден кухне и бару (_station_orders: o.status <> 'completed'), а функции
--     смены статуса его не меняют (08) — поэтому экраны станций и функции 01–09 не меняются.
--   * Обычное удаление (delete_order) — отметка orders.deleted_at / deleted_by. Заказ и его позиции
--     остаются в базе. Политики RLS скрывают удалённый заказ из обычной истории у всех.
--   * Администратор видит удалённые заказы (admin_deleted_orders), восстанавливает
--     (admin_restore_order) или удаляет навсегда (admin_purge_order).
--   * Удалить навсегда можно только ранее удалённый выданный заказ. Заказ, его order_items и
--     order_stations удаляются одной командой (ON DELETE CASCADE из 01/08) в транзакции функции;
--     другие заказы не затрагиваются. Остаётся запись в журнале order_purge_log без состава.
--   * Журнал order_purge_log: RLS без политик, прав у клиентов нет; читается только функцией
--     admin_purge_log(). Триггеры запрещают изменение и удаление записей.
--
-- Права (проверяются здесь, не в приложении):
--   * delete_order — кассир: только свои заказы; администратор: любые. Кухня и бар — нет.
--   * admin_* — только активный администратор.
--   * Повторное удаление и восстановление — без ошибки (ответ changed = false); повторное
--     окончательное удаление уже стёртого заказа — без ошибки, если он есть в журнале.
--
-- Перезаписываются политики orders_select_own, orders_select_admin, order_items_select_own,
-- order_items_select_admin (01, 04): + условие «заказ не удалён». 01 и 04 повторно уже не
-- запускаются — их предусловия останавливают их после 08 и 05.
-- =====================================================================

begin;

-- ---------- Предусловие: применены 01–09 ----------

do $$
declare
  v_missing text[] := '{}';
begin
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.orders') and attname = 'status' and not attisdropped)
  then v_missing := array_append(v_missing, 'orders.status (04)'); end if;
  if to_regclass('public.order_stations') is null
  then v_missing := array_append(v_missing, 'order_stations (08)'); end if;
  if to_regclass('public.menu_item_reviews') is null
  then v_missing := array_append(v_missing, 'menu_item_reviews (09)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_admin')
  then v_missing := array_append(v_missing, 'is_active_admin() (02)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_staff')
  then v_missing := array_append(v_missing, 'is_active_staff() (01)'); end if;

  if cardinality(v_missing) > 0 then
    raise exception 'ОСТАНОВЛЕНО: 10_order_deletion.sql требует выполненных 01–09, не найдено: %',
      array_to_string(v_missing, ', ');
  end if;

  -- «Выдан» — статус completed (04/05); другой набор статусов требует разбора вручную
  if not exists (select 1 from pg_constraint where conrelid = 'public.orders'::regclass and conname = 'orders_status_check'
                  and pg_get_constraintdef(oid) ~ '''completed''::text') then
    raise exception 'ОСТАНОВЛЕНО: orders_status_check не содержит статус completed («Выдан»)';
  end if;
end;
$$;

-- ---------- Отметка обычного удаления ----------

alter table public.orders add column if not exists deleted_at timestamptz;
alter table public.orders add column if not exists deleted_by uuid references public.profiles (id) on delete set null;

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.orders'::regclass
                  and conname = 'orders_deleted_only_completed') then
    alter table public.orders add constraint orders_deleted_only_completed
      check (deleted_at is null or status = 'completed');
  end if;
end;
$$;

create index if not exists orders_deleted_idx on public.orders (deleted_at desc) where deleted_at is not null;

-- ---------- Журнал окончательного удаления (без состава заказа) ----------

-- Без внешних ключей: это история. Внешний ключ с ON DELETE SET NULL менял бы записи при удалении
-- сотрудника, а триггер ниже это запрещает — удалить сотрудника стало бы нельзя. Имена — снимки.
create table if not exists public.order_purge_log (
  id               bigint generated always as identity primary key,
  order_id         bigint not null unique,  -- номер стёртого заказа
  total            numeric(12, 2) not null,
  cashier_id       uuid,
  cashier_name     text,                    -- снимок имени на момент удаления
  order_created_at timestamptz not null,
  deleted_at       timestamptz not null,    -- когда отправлен в удалённые
  deleted_by       uuid,
  purged_by        uuid not null,
  purged_by_name   text,
  purged_at        timestamptz not null default now()
);

alter table public.order_purge_log enable row level security;
revoke all on public.order_purge_log from public, anon, authenticated;

-- Записи журнала не меняются и не удаляются (в том числе функциями проекта)
create or replace function public._order_purge_log_readonly()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception using errcode = '55000', message = 'PURGE_LOG_READONLY';
end;
$$;

revoke all on function public._order_purge_log_readonly() from public, anon, authenticated;

drop trigger if exists order_purge_log_readonly on public.order_purge_log;
create trigger order_purge_log_readonly
  before update or delete on public.order_purge_log
  for each row execute function public._order_purge_log_readonly();

drop trigger if exists order_purge_log_no_truncate on public.order_purge_log;
create trigger order_purge_log_no_truncate
  before truncate on public.order_purge_log
  for each statement execute function public._order_purge_log_readonly();

-- ---------- Обычная история: удалённые заказы не видны никому ----------
-- Тексты политик из 01 и 04 + условие deleted_at is null.

drop policy if exists orders_select_own on public.orders;
create policy orders_select_own on public.orders
  for select to authenticated
  using (cashier_id = (select auth.uid()) and deleted_at is null and (select public.is_active_staff()));

drop policy if exists orders_select_admin on public.orders;
create policy orders_select_admin on public.orders
  for select to authenticated
  using (deleted_at is null and (select public.is_active_admin()));

drop policy if exists order_items_select_own on public.order_items;
create policy order_items_select_own on public.order_items
  for select to authenticated
  using (exists (
    select 1 from public.orders o
    where o.id = order_items.order_id
      and o.cashier_id = (select auth.uid())
      and o.deleted_at is null
  ) and (select public.is_active_staff()));

drop policy if exists order_items_select_admin on public.order_items;
create policy order_items_select_admin on public.order_items
  for select to authenticated
  using ((select public.is_active_admin()) and exists (
    select 1 from public.orders o
    where o.id = order_items.order_id
      and o.deleted_at is null
  ));

-- ---------- Отправить в удалённые ----------
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, ORDER_NOT_FOUND (нет или чужой заказ кассира),
--   ORDER_NOT_COMPLETED (detail = текущий статус)
-- Возвращает {order_id, deleted: true, changed}; changed = false — заказ уже был удалён.

create or replace function public.delete_order(p_order_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid     uuid := auth.uid();
  v_role    text;
  v_status  text;
  v_owner   uuid;
  v_deleted timestamptz;
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

  select o.status, o.cashier_id, o.deleted_at into v_status, v_owner, v_deleted
    from public.orders o
   where o.id = p_order_id
   for update;
  -- Чужой заказ для кассира «не найден» — как в RLS и set_order_status
  if not found or (v_role = 'cashier' and v_owner <> v_uid) then
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;
  if v_status <> 'completed' then
    raise exception using errcode = 'P0001', message = 'ORDER_NOT_COMPLETED', detail = v_status;
  end if;
  if v_deleted is not null then
    return jsonb_build_object('order_id', p_order_id, 'deleted', true, 'changed', false);
  end if;

  update public.orders set deleted_at = now(), deleted_by = v_uid where id = p_order_id;
  return jsonb_build_object('order_id', p_order_id, 'deleted', true, 'changed', true);
end;
$$;

revoke all on function public.delete_order(bigint) from public, anon;
grant execute on function public.delete_order(bigint) to authenticated;

-- ---------- Удалённые заказы (администратор) ----------
-- Возвращает [{id, created_at, total, status, cashier_name, deleted_at, deleted_by_name,
--   items: [{id, item_name, unit_price, quantity, line_total}]}], последние удалённые сначала.

create or replace function public.admin_deleted_orders()
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

  select coalesce(jsonb_agg(jsonb_build_object(
           'id', o.id, 'created_at', o.created_at, 'total', o.total, 'status', o.status,
           'cashier_name', pc.full_name, 'deleted_at', o.deleted_at, 'deleted_by_name', pd.full_name,
           'items', (select coalesce(jsonb_agg(jsonb_build_object(
                              'id', i.id, 'item_name', i.item_name, 'unit_price', i.unit_price,
                              'quantity', i.quantity, 'line_total', i.line_total) order by i.id), '[]'::jsonb)
                       from public.order_items i where i.order_id = o.id))
         order by o.deleted_at desc, o.id desc), '[]'::jsonb)
    into v_result
    from (select * from public.orders o2 where o2.deleted_at is not null
           order by o2.deleted_at desc, o2.id desc limit 500) o
    left join public.profiles pc on pc.id = o.cashier_id
    left join public.profiles pd on pd.id = o.deleted_by;

  return v_result;
end;
$$;

revoke all on function public.admin_deleted_orders() from public, anon;
grant execute on function public.admin_deleted_orders() to authenticated;

-- ---------- Восстановить ----------
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, ORDER_NOT_FOUND
-- Возвращает {order_id, deleted: false, changed}; changed = false — заказ и не был удалён.

create or replace function public.admin_restore_order(p_order_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_deleted timestamptz;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;

  select o.deleted_at into v_deleted from public.orders o where o.id = p_order_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;
  if v_deleted is null then
    return jsonb_build_object('order_id', p_order_id, 'deleted', false, 'changed', false);
  end if;

  update public.orders set deleted_at = null, deleted_by = null where id = p_order_id;
  return jsonb_build_object('order_id', p_order_id, 'deleted', false, 'changed', true);
end;
$$;

revoke all on function public.admin_restore_order(bigint) from public, anon;
grant execute on function public.admin_restore_order(bigint) to authenticated;

-- ---------- Удалить навсегда ----------
-- Только ранее удалённый выданный заказ. Запись в журнал и удаление — в одной транзакции:
-- либо удалены заказ, его позиции и части станций и есть запись журнала, либо не изменено ничего.
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, ORDER_NOT_FOUND, ORDER_NOT_DELETED, ORDER_NOT_COMPLETED
-- Возвращает {order_id, purged: true, changed, items_deleted}; changed = false — заказ уже стёрт
-- (есть в журнале).

create or replace function public.admin_purge_order(p_order_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid     uuid := auth.uid();
  v_order   public.orders;
  v_items   integer;
  v_removed integer;
begin
  if v_uid is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;

  select * into v_order from public.orders o where o.id = p_order_id for update;
  if not found then
    if exists (select 1 from public.order_purge_log l where l.order_id = p_order_id) then
      return jsonb_build_object('order_id', p_order_id, 'purged', true, 'changed', false, 'items_deleted', 0);
    end if;
    raise exception using errcode = 'P0002', message = 'ORDER_NOT_FOUND';
  end if;
  if v_order.deleted_at is null then
    raise exception using errcode = 'P0001', message = 'ORDER_NOT_DELETED';
  end if;
  if v_order.status <> 'completed' then
    raise exception using errcode = 'P0001', message = 'ORDER_NOT_COMPLETED', detail = v_order.status;
  end if;

  insert into public.order_purge_log (order_id, total, cashier_id, cashier_name, order_created_at,
                                      deleted_at, deleted_by, purged_by, purged_by_name)
  values (v_order.id, v_order.total, v_order.cashier_id,
          (select p.full_name from public.profiles p where p.id = v_order.cashier_id),
          v_order.created_at, v_order.deleted_at, v_order.deleted_by, v_uid,
          (select p.full_name from public.profiles p where p.id = v_uid));

  select count(*) into v_items from public.order_items i where i.order_id = p_order_id;

  -- Позиции (order_items) и части станций (order_stations) удаляются каскадом вместе с заказом
  delete from public.orders where id = p_order_id;
  get diagnostics v_removed = row_count;
  if v_removed <> 1
     or exists (select 1 from public.order_items i where i.order_id = p_order_id)
     or exists (select 1 from public.order_stations s where s.order_id = p_order_id) then
    raise exception using errcode = 'P0001', message = 'PURGE_FAILED';  -- откатит всю транзакцию
  end if;

  return jsonb_build_object('order_id', p_order_id, 'purged', true, 'changed', true, 'items_deleted', v_items);
end;
$$;

revoke all on function public.admin_purge_order(bigint) from public, anon;
grant execute on function public.admin_purge_order(bigint) to authenticated;

-- ---------- Журнал окончательных удалений (администратор) ----------
-- Возвращает [{order_id, total, cashier_name, order_created_at, deleted_at, purged_by_name, purged_at}],
-- новые сначала (не больше 500).

create or replace function public.admin_purge_log()
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

  select coalesce(jsonb_agg(jsonb_build_object(
           'order_id', l.order_id, 'total', l.total, 'cashier_name', l.cashier_name,
           'order_created_at', l.order_created_at, 'deleted_at', l.deleted_at,
           'purged_by_name', l.purged_by_name, 'purged_at', l.purged_at)
         order by l.purged_at desc, l.id desc), '[]'::jsonb)
    into v_result
    from (select * from public.order_purge_log order by purged_at desc, id desc limit 500) l;

  return v_result;
end;
$$;

revoke all on function public.admin_purge_log() from public, anon;
grant execute on function public.admin_purge_log() to authenticated;

notify pgrst, 'reload schema';

commit;

-- ---------- Проверка после установки (только чтение) ----------
-- select count(*) from public.orders where deleted_at is not null and status <> 'completed';   -- 0
-- select has_table_privilege('authenticated', 'public.order_purge_log', 'select');              -- false
-- Полная проверка: sql/tests/10_order_deletion_checks.sql
