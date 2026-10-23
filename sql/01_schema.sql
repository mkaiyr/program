-- =====================================================================
-- Этап 1. Схема, ограничения, RLS и серверная функция оформления заказа
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- =====================================================================

-- ---------- Таблицы ----------

-- Профиль сотрудника. Создаётся и меняется только администратором в SQL Editor
-- (см. конец файла). У клиента нет прав INSERT/UPDATE, поэтому роль себе не назначить.
create table if not exists public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  full_name  text not null check (length(trim(full_name)) between 1 and 100),
  role       text not null default 'cashier' check (role in ('cashier', 'admin')),
  is_active  boolean not null default true,
  created_at timestamptz not null default now()
);
-- На случай, если таблица уже была создана ранней версией файла без роли
alter table public.profiles add column if not exists
  role text not null default 'cashier' check (role in ('cashier', 'admin'));

create table if not exists public.menu_items (
  id           bigint generated always as identity primary key,
  name         text not null unique check (length(trim(name)) between 1 and 100),
  category     text not null default 'Прочее' check (length(trim(category)) between 1 and 50),
  price        numeric(10, 2) not null check (price > 0),
  is_available boolean not null default true,
  created_at   timestamptz not null default now()
);

create table if not exists public.orders (
  id                bigint generated always as identity primary key,
  cashier_id        uuid not null references public.profiles (id) on delete restrict,
  -- UUID, который генерирует клиент; защищает от повторной отправки одного заказа
  client_request_id uuid not null unique,
  total             numeric(12, 2) not null check (total > 0),
  created_at        timestamptz not null default now()
);

create table if not exists public.order_items (
  id           bigint generated always as identity primary key,
  order_id     bigint not null references public.orders (id) on delete cascade,
  menu_item_id bigint not null references public.menu_items (id) on delete restrict,
  item_name    text not null,                       -- снимок названия на момент заказа
  unit_price   numeric(10, 2) not null check (unit_price > 0),  -- снимок цены
  quantity     integer not null check (quantity between 1 and 100),
  line_total   numeric(12, 2) generated always as (unit_price * quantity) stored,
  unique (order_id, menu_item_id)
);

create index if not exists orders_cashier_created_idx on public.orders (cashier_id, created_at desc);
create index if not exists order_items_menu_item_idx  on public.order_items (menu_item_id);

-- ---------- Вспомогательная функция проверки прав ----------

create or replace function public.is_active_staff()
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
      and p.role in ('cashier', 'admin')
  );
$$;

-- ---------- Права доступа (запись только через RPC) ----------

revoke all on public.profiles, public.menu_items, public.orders, public.order_items from anon, authenticated;
grant select on public.profiles, public.menu_items, public.orders, public.order_items to authenticated;

revoke all on function public.is_active_staff() from public, anon;
grant execute on function public.is_active_staff() to authenticated;

-- ---------- RLS ----------

alter table public.profiles    enable row level security;
alter table public.menu_items  enable row level security;
alter table public.orders      enable row level security;
alter table public.order_items enable row level security;

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select to authenticated
  using (id = (select auth.uid()));

drop policy if exists menu_select_staff on public.menu_items;
create policy menu_select_staff on public.menu_items
  for select to authenticated
  using (is_available and (select public.is_active_staff()));

drop policy if exists orders_select_own on public.orders;
create policy orders_select_own on public.orders
  for select to authenticated
  using (cashier_id = (select auth.uid()) and (select public.is_active_staff()));

drop policy if exists order_items_select_own on public.order_items;
create policy order_items_select_own on public.order_items
  for select to authenticated
  using (exists (
    select 1 from public.orders o
    where o.id = order_items.order_id
      and o.cashier_id = (select auth.uid())
  ) and (select public.is_active_staff()));

-- Политик INSERT/UPDATE/DELETE нет намеренно: клиент не может менять данные напрямую.

-- ---------- Оформление заказа (атомарно, одна транзакция) ----------
-- p_items: [{"menu_item_id": 1, "quantity": 2, "expected_price": "450.00"}, ...]
-- Ошибки (поле message): NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_REQUEST,
--   INVALID_ITEMS, DUPLICATE_ITEM, MENU_CHANGED (detail = список позиций), REQUEST_ID_CONFLICT

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

  insert into public.order_items (order_id, menu_item_id, item_name, unit_price, quantity)
  select v_order_id, m.id, m.name, m.price, x.quantity
    from jsonb_to_recordset(p_items) as x(menu_item_id bigint, quantity integer)
    join public.menu_items m on m.id = x.menu_item_id;

  return jsonb_build_object('order_id', v_order_id, 'total', v_total,
                            'created_at', v_created_at, 'duplicate', false);
end;
$$;

revoke all on function public.create_order(uuid, jsonb) from public, anon;
grant execute on function public.create_order(uuid, jsonb) to authenticated;

-- ---------- Тестовое меню ----------

insert into public.menu_items (name, category, price) values
  ('Американо',        'Напитки', 800.00),
  ('Капучино',         'Напитки', 1100.00),
  ('Чай чёрный',       'Напитки', 500.00),
  ('Круассан',         'Выпечка', 700.00),
  ('Сэндвич с курицей','Еда',     1500.00)
on conflict (name) do nothing;

-- ---------- Профиль кассира (выполнить отдельно после создания пользователя) ----------
-- insert into public.profiles (id, full_name, role)
-- select id, 'Кассир 1', 'cashier' from auth.users where email = 'cashier1@example.com';

-- ---------- Проверка прав (выполнить после установки, ожидается пустой результат) ----------
-- select table_name, privilege_type from information_schema.role_table_grants
--  where table_schema = 'public' and grantee in ('anon', 'authenticated')
--    and privilege_type <> 'SELECT';
