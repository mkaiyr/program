-- =====================================================================
-- Этап 14. Кассовые смены (демонстрационный модуль)
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–11. Одна транзакция: при ошибке или остановке не меняется ничего.
-- Повторный запуск безопасен: данные не меняются, определения те же.
--
-- Модель:
--   * Смена принадлежит кассиру (или администратору, работающему на кассе). У сотрудника не больше
--     одной открытой смены (частичный уникальный индекс).
--   * Заказ привязывается к открытой смене кассира триггером orders_attach_shift. Открытая смена для
--     оформления заказа не обязательна — это демонстрационный модуль.
--   * Ожидаемая сумма = начальная сумма + выручка по заказам смены (с надбавкой, без аннулированных)
--     + внесения − изъятия. Считается сервером; при закрытии значения сохраняются в смене.
--   * Таблицы закрыты для клиентов (RLS без политик); чтение и запись — функциями shift_*.
-- =====================================================================

begin;

do $$
declare
  v_missing text[] := '{}';
begin
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.orders') and attname = 'voided_at' and not attisdropped)
  then v_missing := array_append(v_missing, 'orders.voided_at (11)'); end if;
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.orders') and attname = 'service_amount' and not attisdropped)
  then v_missing := array_append(v_missing, 'orders.service_amount (11)'); end if;
  if cardinality(v_missing) > 0 then
    raise exception 'ОСТАНОВЛЕНО: 12_cash_shifts.sql требует выполненных 01–11, не найдено: %',
      array_to_string(v_missing, ', ');
  end if;
end;
$$;

-- ---------- Таблицы ----------

create table if not exists public.cash_shifts (
  id                bigint generated always as identity primary key,
  cashier_id        uuid not null references public.profiles (id) on delete restrict,
  opened_at         timestamptz not null default now(),
  opening_cash      numeric(12, 2) not null check (opening_cash >= 0),
  closed_at         timestamptz,
  actual_cash       numeric(12, 2) check (actual_cash >= 0),
  -- Снимок при закрытии (у открытой смены — NULL, значения считаются на лету)
  orders_count      integer,
  sales_total       numeric(12, 2),
  deposits_total    numeric(12, 2),
  withdrawals_total numeric(12, 2),
  expected_cash     numeric(12, 2),
  difference        numeric(12, 2),
  check ((closed_at is null) = (actual_cash is null)),
  check (closed_at is null or closed_at >= opened_at)
);

create unique index if not exists cash_shifts_one_open_idx on public.cash_shifts (cashier_id) where closed_at is null;
create index if not exists cash_shifts_opened_idx on public.cash_shifts (opened_at desc);

create table if not exists public.cash_movements (
  id         bigint generated always as identity primary key,
  shift_id   bigint not null references public.cash_shifts (id) on delete restrict,
  kind       text not null check (kind in ('deposit', 'withdrawal')),
  amount     numeric(12, 2) not null check (amount > 0),
  note       text check (note is null or length(note) <= 200),
  created_at timestamptz not null default now(),
  created_by uuid not null references public.profiles (id) on delete restrict
);
create index if not exists cash_movements_shift_idx on public.cash_movements (shift_id, created_at);

alter table public.cash_shifts enable row level security;
alter table public.cash_movements enable row level security;
revoke all on public.cash_shifts, public.cash_movements from public, anon, authenticated;

alter table public.orders add column if not exists shift_id bigint references public.cash_shifts (id) on delete restrict;
create index if not exists orders_shift_idx on public.orders (shift_id) where shift_id is not null;

-- ---------- Привязка заказа к открытой смене ----------
-- for share: заказ, оформляемый в момент закрытия смены, дожидается закрытия и в неё не попадает.

create or replace function public._orders_attach_shift()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.shift_id is null then
    select s.id into new.shift_id
      from public.cash_shifts s
     where s.cashier_id = new.cashier_id and s.closed_at is null
     for share;
  end if;
  return new;
end;
$$;

revoke all on function public._orders_attach_shift() from public, anon, authenticated;

drop trigger if exists orders_attach_shift on public.orders;
create trigger orders_attach_shift
  before insert on public.orders
  for each row execute function public._orders_attach_shift();

-- ---------- Права и расчёт ----------

-- Кассир или администратор; возвращает роль или вызывает ошибку
create or replace function public._shift_actor()
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_role text;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  select p.role into v_role
    from public.profiles p
   where p.id = auth.uid() and p.is_active and p.role in ('cashier', 'admin');
  if v_role is null then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  return v_role;
end;
$$;

revoke all on function public._shift_actor() from public, anon, authenticated;

-- Итоги смены на текущий момент (для закрытой смены — тот же расчёт: заказы смены не меняются)
create or replace function public._shift_json(p_shift_id bigint)
returns jsonb
language sql
stable
set search_path = ''
as $$
  select jsonb_build_object(
           'id', s.id, 'cashier_id', s.cashier_id, 'cashier_name', p.full_name,
           'opened_at', s.opened_at, 'closed_at', s.closed_at,
           'opening_cash', s.opening_cash, 'actual_cash', s.actual_cash,
           'orders_count', coalesce(s.orders_count, t.orders_count),
           'sales_total', coalesce(s.sales_total, t.sales_total),
           'deposits_total', coalesce(s.deposits_total, t.deposits_total),
           'withdrawals_total', coalesce(s.withdrawals_total, t.withdrawals_total),
           'expected_cash', coalesce(s.expected_cash,
                              s.opening_cash + t.sales_total + t.deposits_total - t.withdrawals_total),
           'difference', s.difference,
           'movements', (select coalesce(jsonb_agg(jsonb_build_object(
                                  'id', m.id, 'kind', m.kind, 'amount', m.amount, 'note', m.note,
                                  'created_at', m.created_at) order by m.id), '[]'::jsonb)
                           from public.cash_movements m where m.shift_id = s.id))
    from public.cash_shifts s
    join public.profiles p on p.id = s.cashier_id
    cross join lateral (
      select (select count(*) from public.orders o where o.shift_id = s.id and o.voided_at is null)::integer as orders_count,
             (select coalesce(sum(o.total), 0) from public.orders o where o.shift_id = s.id and o.voided_at is null) as sales_total,
             (select coalesce(sum(m.amount), 0) from public.cash_movements m where m.shift_id = s.id and m.kind = 'deposit') as deposits_total,
             (select coalesce(sum(m.amount), 0) from public.cash_movements m where m.shift_id = s.id and m.kind = 'withdrawal') as withdrawals_total
    ) t
   where s.id = p_shift_id;
$$;

revoke all on function public._shift_json(bigint) from public, anon, authenticated;

-- Сумма: неотрицательная, не более 2 знаков после запятой, не больше 9 999 999 999.99
create or replace function public._check_cash_amount(p_amount numeric, p_allow_zero boolean)
returns void
language plpgsql
immutable
set search_path = ''
as $$
begin
  if p_amount is null or p_amount < 0 or (p_amount = 0 and not p_allow_zero)
     or p_amount <> round(p_amount, 2) or p_amount > 9999999999.99 then
    raise exception using errcode = '22023', message = 'INVALID_AMOUNT';
  end if;
end;
$$;

revoke all on function public._check_cash_amount(numeric, boolean) from public, anon, authenticated;

-- ---------- Текущая смена ----------
-- Возвращает объект смены или null, если у сотрудника нет открытой смены.

create or replace function public.shift_current()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_id bigint;
begin
  perform public._shift_actor();
  select s.id into v_id from public.cash_shifts s where s.cashier_id = auth.uid() and s.closed_at is null;
  if v_id is null then
    return null;
  end if;
  return public._shift_json(v_id);
end;
$$;

revoke all on function public.shift_current() from public, anon;
grant execute on function public.shift_current() to authenticated;

-- ---------- Открытие ----------
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_AMOUNT, SHIFT_ALREADY_OPEN

create or replace function public.shift_open(p_opening_cash numeric)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id bigint;
begin
  perform public._shift_actor();
  perform public._check_cash_amount(p_opening_cash, true);
  begin
    insert into public.cash_shifts (cashier_id, opening_cash) values (auth.uid(), p_opening_cash)
    returning id into v_id;
  exception when unique_violation then
    raise exception using errcode = 'P0001', message = 'SHIFT_ALREADY_OPEN';
  end;
  return public._shift_json(v_id);
end;
$$;

revoke all on function public.shift_open(numeric) from public, anon;
grant execute on function public.shift_open(numeric) to authenticated;

-- ---------- Внесение и изъятие наличных ----------
-- p_kind: 'deposit' | 'withdrawal'. Ошибки: INVALID_KIND, INVALID_AMOUNT, NO_OPEN_SHIFT,
--   INSUFFICIENT_CASH (изъятие больше ожидаемой суммы; detail = ожидаемая сумма), INVALID_NOTE

create or replace function public.shift_cash_move(p_kind text, p_amount numeric, p_note text default null)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id       bigint;
  v_note     text := nullif(trim(coalesce(p_note, '')), '');
  v_expected numeric;
begin
  perform public._shift_actor();
  if p_kind not in ('deposit', 'withdrawal') then
    raise exception using errcode = '22023', message = 'INVALID_KIND';
  end if;
  perform public._check_cash_amount(p_amount, false);
  if v_note is not null and length(v_note) > 200 then
    raise exception using errcode = '22023', message = 'INVALID_NOTE';
  end if;

  select s.id into v_id from public.cash_shifts s
   where s.cashier_id = auth.uid() and s.closed_at is null for update;
  if v_id is null then
    raise exception using errcode = 'P0001', message = 'NO_OPEN_SHIFT';
  end if;

  if p_kind = 'withdrawal' then
    v_expected := (public._shift_json(v_id) ->> 'expected_cash')::numeric;
    if p_amount > v_expected then
      raise exception using errcode = 'P0001', message = 'INSUFFICIENT_CASH', detail = v_expected::text;
    end if;
  end if;

  insert into public.cash_movements (shift_id, kind, amount, note, created_by)
  values (v_id, p_kind, p_amount, v_note, auth.uid());
  return public._shift_json(v_id);
end;
$$;

revoke all on function public.shift_cash_move(text, numeric, text) from public, anon;
grant execute on function public.shift_cash_move(text, numeric, text) to authenticated;

-- ---------- Закрытие ----------
-- Ошибки: INVALID_AMOUNT, NO_OPEN_SHIFT. Возвращает смену с expected_cash и difference (факт − ожидание).

create or replace function public.shift_close(p_actual_cash numeric)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id   bigint;
  v_calc jsonb;
begin
  perform public._shift_actor();
  perform public._check_cash_amount(p_actual_cash, true);

  select s.id into v_id from public.cash_shifts s
   where s.cashier_id = auth.uid() and s.closed_at is null for update;
  if v_id is null then
    raise exception using errcode = 'P0001', message = 'NO_OPEN_SHIFT';
  end if;

  v_calc := public._shift_json(v_id);
  update public.cash_shifts
     set closed_at = now(), actual_cash = p_actual_cash,
         orders_count = (v_calc ->> 'orders_count')::integer,
         sales_total = (v_calc ->> 'sales_total')::numeric,
         deposits_total = (v_calc ->> 'deposits_total')::numeric,
         withdrawals_total = (v_calc ->> 'withdrawals_total')::numeric,
         expected_cash = (v_calc ->> 'expected_cash')::numeric,
         difference = p_actual_cash - (v_calc ->> 'expected_cash')::numeric
   where id = v_id;
  return public._shift_json(v_id);
end;
$$;

revoke all on function public.shift_close(numeric) from public, anon;
grant execute on function public.shift_close(numeric) to authenticated;

-- ---------- История смен ----------
-- Кассир видит свои смены, администратор — все. Новые сверху; p_limit от 1 до 500.

create or replace function public.shift_history(p_limit integer default 100)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_role text;
begin
  v_role := public._shift_actor();
  return (select coalesce(jsonb_agg(public._shift_json(t.id) order by t.opened_at desc, t.id desc), '[]'::jsonb)
            from (select s.id, s.opened_at from public.cash_shifts s
                   where v_role = 'admin' or s.cashier_id = auth.uid()
                   order by s.opened_at desc, s.id desc
                   limit greatest(1, least(coalesce(p_limit, 100), 500))) t);
end;
$$;

revoke all on function public.shift_history(integer) from public, anon;
grant execute on function public.shift_history(integer) to authenticated;

notify pgrst, 'reload schema';

commit;
