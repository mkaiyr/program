-- =====================================================================
-- Этап 9. Постоянные числовые коды позиций меню (1001, 1002, ...)
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–06. Повторный запуск безопасен.
--
-- * menu_items.code integer NOT NULL UNIQUE — отдельно от первичного ключа id.
--   Заказы (order_items.menu_item_id) по-прежнему ссылаются на id.
-- * Коды выдаёт последовательность menu_item_code_seq: безопасно при одновременных
--   вставках, номер никогда не выдаётся повторно (MAX(code)+1 не используется).
-- * Существующим позициям коды назначаются по порядку id; названия, цены,
--   категории и доступность не меняются.
-- * Код постоянный: изменить его нельзя (триггер), ручной код — только при создании.
-- * Тип integer: коды начинаются с 1001, ведущих нулей нет.
-- =====================================================================

begin;

-- ---------- Предусловие: колонка code, если уже есть, должна быть integer ----------

do $$
declare
  v_type text;
begin
  select data_type into v_type
    from information_schema.columns
   where table_schema = 'public' and table_name = 'menu_items' and column_name = 'code';
  if v_type is not null and v_type <> 'integer' then
    raise exception 'ОСТАНОВЛЕНО: menu_items.code уже существует с типом %, ожидается integer', v_type;
  end if;
  -- После 08 функция admin_add_menu_item имеет другой набор параметров: повторный
  -- запуск 07 создал бы вторую версию рядом с ней. Транзакция откатится целиком.
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'menu_items' and column_name = 'station') then
    raise exception 'ОСТАНОВЛЕНО: уже применена 08_bar_stations.sql, повторный запуск 07 не нужен';
  end if;
end;
$$;

-- ---------- Последовательность и выдача следующего свободного кода ----------

create sequence if not exists public.menu_item_code_seq as integer start with 1001 minvalue 1001;
revoke all on sequence public.menu_item_code_seq from public, anon, authenticated;

alter table public.menu_items add column if not exists code integer;
alter sequence public.menu_item_code_seq owned by public.menu_items.code;

-- Следующий код из последовательности, пропуская номера, занятые вручную.
-- Номера из последовательности не повторяются, поэтому параллельные вызовы
-- получают разные коды; от совпадения с ручным кодом защищает UNIQUE.
create or replace function public._next_menu_item_code()
returns integer
language plpgsql
volatile
set search_path = ''
as $$
declare
  v_code integer;
begin
  loop
    v_code := nextval('public.menu_item_code_seq');
    exit when not exists (select 1 from public.menu_items m where m.code = v_code);
  end loop;
  return v_code;
end;
$$;

revoke all on function public._next_menu_item_code() from public, anon, authenticated;

-- Если коды уже были (повторный запуск или ручное заполнение) — последовательность не отстаёт
do $$
declare
  v_max  integer;
  v_next integer;
begin
  select max(code) into v_max from public.menu_items;
  select case when is_called then last_value + 1 else last_value end into v_next
    from public.menu_item_code_seq;
  if v_max is not null and v_max >= v_next then
    perform setval('public.menu_item_code_seq', v_max, true);
  end if;
end;
$$;

-- ---------- Коды существующим позициям (по порядку id) ----------
-- Триггер неизменности создаётся ниже, после заполнения.

do $$
declare
  r record;
begin
  for r in select id from public.menu_items where code is null order by id loop
    update public.menu_items set code = public._next_menu_item_code() where id = r.id;
  end loop;
end;
$$;

alter table public.menu_items alter column code set default public._next_menu_item_code();
alter table public.menu_items alter column code set not null;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.menu_items'::regclass and conname = 'menu_items_code_key') then
    alter table public.menu_items add constraint menu_items_code_key unique (code);
  end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.menu_items'::regclass and conname = 'menu_items_code_check') then
    alter table public.menu_items add constraint menu_items_code_check check (code between 1 and 99999999);
  end if;
end;
$$;

-- ---------- Код постоянный ----------

create or replace function public._menu_item_code_immutable()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.code is distinct from old.code then
    raise exception using errcode = '55000', message = 'CODE_IMMUTABLE';
  end if;
  return new;
end;
$$;

revoke all on function public._menu_item_code_immutable() from public, anon, authenticated;

drop trigger if exists menu_items_code_immutable on public.menu_items;
create trigger menu_items_code_immutable
  before update of code on public.menu_items
  for each row execute function public._menu_item_code_immutable();

-- ---------- Добавление позиции: необязательный ручной код ----------
-- Набор параметров меняется, поэтому старая версия удаляется (иначе PostgREST
-- увидит две функции с одним именем). Права выдаются заново ниже.
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_*, INVALID_CODE, NAME_TAKEN, CODE_TAKEN

drop function if exists public.admin_add_menu_item(text, text, numeric);

create or replace function public.admin_add_menu_item(p_name text, p_category text, p_price numeric,
                                                      p_code integer default null)
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

  -- Та же блокировка, что в admin_update_menu_item: проверки имени и кода без гонок
  perform pg_advisory_xact_lock(hashtext('public.menu_items.name'));
  if exists (select 1 from public.menu_items m where lower(m.name) = lower(trim(p_name))) then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
  end if;
  if p_code is not null and exists (select 1 from public.menu_items m where m.code = p_code) then
    raise exception using errcode = '23505', message = 'CODE_TAKEN';
  end if;

  insert into public.menu_items (name, category, price, code)
  values (trim(p_name), trim(p_category), p_price, coalesce(p_code, public._next_menu_item_code()))
  returning * into v_row;

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available);
exception
  when unique_violation then
    get stacked diagnostics v_constraint = constraint_name;
    if sqlerrm = 'CODE_TAKEN' or v_constraint = 'menu_items_code_key' then
      raise exception using errcode = '23505', message = 'CODE_TAKEN';
    end if;
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
end;
$$;

revoke all on function public.admin_add_menu_item(text, text, numeric, integer) from public, anon;
grant execute on function public.admin_add_menu_item(text, text, numeric, integer) to authenticated;

-- ---------- Изменение позиции: код не меняется, возвращается в ответе ----------
-- Сигнатура та же, что в 02.

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

  return jsonb_build_object('id', v_row.id, 'code', v_row.code, 'name', v_row.name, 'category', v_row.category,
                            'price', v_row.price, 'is_available', v_row.is_available);
exception
  when unique_violation then
    raise exception using errcode = '23505', message = 'NAME_TAKEN';
end;
$$;

revoke all on function public.admin_update_menu_item(bigint, text, text, numeric) from public, anon;
grant execute on function public.admin_update_menu_item(bigint, text, text, numeric) to authenticated;

-- ---------- Доступность (админ): код в ответе ----------
-- Сигнатура та же, что в 02.

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

-- ---------- Проверка после установки ----------
-- select id, code, name, category, price, is_available from public.menu_items order by code;
-- select count(*) = count(distinct code) and count(*) = count(code) as codes_ok from public.menu_items;
-- Полная проверка: sql/tests/07_menu_codes_checks.sql
