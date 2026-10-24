-- =====================================================================
-- Проверка 07_menu_codes.sql
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
--
-- НИЧЕГО НЕ СОХРАНЯЕТ: всё выполняется в одной транзакции, в конце rollback.
-- (Номера последовательности, выданные в тесте, не возвращаются — это свойство
--  последовательностей PostgreSQL: в реальном меню появится пропуск в нумерации.)
-- Нужны: активные admin и cashier, хотя бы одна доступная позиция меню.
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- =====================================================================

begin;

-- ---------- Существующие позиции (от имени postgres) ----------

do $$
begin
  assert (select count(*) = count(code) from public.menu_items), 'FAIL: есть позиции без кода';
  assert (select count(*) = count(distinct code) from public.menu_items), 'FAIL: коды повторяются';
  assert (select is_nullable = 'NO' and data_type = 'integer' from information_schema.columns
           where table_schema = 'public' and table_name = 'menu_items' and column_name = 'code'),
    'FAIL: code должен быть integer NOT NULL';
  assert exists (select 1 from pg_constraint where conname = 'menu_items_code_key' and contype = 'u'),
    'FAIL: нет UNIQUE на code';
end;
$$;

-- Код нельзя изменить даже напрямую (триггер)
do $$
begin
  update public.menu_items set code = code + 1000000 where id = (select min(id) from public.menu_items);
  raise exception 'FAIL: код изменён';
exception when others then
  if sqlerrm <> 'CODE_IMMUTABLE' then raise; end if;
end;
$$;

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true),
       set_config('test.busy_code', (select min(code)::text from public.menu_items), true);

do $$
begin
  if nullif(current_setting('test.admin_id'), '') is null or nullif(current_setting('test.cashier_id'), '') is null then
    raise exception 'FAIL: нужны активные admin и cashier';
  end if;
end;
$$;

-- ---------- Администратор ----------

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
declare
  a jsonb;
  b jsonb;
  m jsonb;
  v_max integer;
begin
  select max(code) into v_max from public.menu_items;

  -- Автокод: две позиции подряд получают разные новые коды
  a := public.admin_add_menu_item('Тест-код А', 'Тест', 100);
  b := public.admin_add_menu_item('Тест-код Б', 'Тест', 100);
  assert (a ->> 'code') is not null and (b ->> 'code') is not null, 'FAIL: автокод не назначен';
  assert (a ->> 'code')::int <> (b ->> 'code')::int, 'FAIL: одинаковые автокоды';
  assert (a ->> 'code')::int > v_max and (b ->> 'code')::int > v_max, 'FAIL: автокод не новый';

  -- Ручной свободный код
  m := public.admin_add_menu_item('Тест-код ручной', 'Тест', 100, 77777777);
  assert (m ->> 'code')::int = 77777777, 'FAIL: ручной код не сохранён';

  -- Переименование и смена категории не меняют код
  v_max := (a ->> 'code')::int;   -- код до переименования
  a := public.admin_update_menu_item((a ->> 'id')::bigint, 'Тест-код А2', 'Тест2', 150);
  assert (a ->> 'code')::int = v_max, 'FAIL: код в ответе изменился при переименовании';
  assert (select code from public.menu_items where id = (a ->> 'id')::bigint) = v_max,
    'FAIL: код в базе изменился при переименовании';

  perform set_config('test.new_id', a ->> 'id', true);
  perform set_config('test.new_code', a ->> 'code', true);
end;
$$;

do $$
begin
  perform public.admin_add_menu_item('Тест-код дубль', 'Тест', 100, current_setting('test.busy_code')::int);
  raise exception 'FAIL: назначен занятый код';
exception when others then
  if sqlerrm <> 'CODE_TAKEN' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_add_menu_item('Тест-код плохой', 'Тест', 100, 0);
  raise exception 'FAIL: принят код 0';
exception when others then
  if sqlerrm <> 'INVALID_CODE' then raise; end if;
end;
$$;

-- Имя без учёта регистра (проверка из 02 через lower()). Если здесь FAIL —
-- локаль базы не меняет регистр кириллицы (например, collation "C"), и имена
-- «Капучино» и «капучино» считаются разными: нужен отдельный разбор.
do $$
begin
  perform public.admin_add_menu_item('  тест-КОД ручной ', 'Тест', 100);
  raise exception 'FAIL: принято имя, отличающееся только регистром и пробелами';
exception when others then
  if sqlerrm <> 'NAME_TAKEN' then raise; end if;
end;
$$;

-- Заказ ссылается на id позиции, а не на код (админ — сотрудник кассы);
-- повторная отправка того же запроса не создаёт второй заказ
do $$
declare
  v_request uuid := gen_random_uuid();
  v_items   jsonb := jsonb_build_array(jsonb_build_object(
    'menu_item_id', current_setting('test.new_id')::bigint, 'quantity', 1, 'expected_price', '150.00'));
  v_first   jsonb;
  v_second  jsonb;
begin
  v_first := public.create_order(v_request, v_items);
  assert (select menu_item_id from public.order_items
           where order_id = (v_first ->> 'order_id')::bigint) = current_setting('test.new_id')::bigint,
    'FAIL: order_items.menu_item_id не равен id позиции';
  v_second := public.create_order(v_request, v_items);
  assert (v_second ->> 'duplicate')::boolean and v_second ->> 'order_id' = v_first ->> 'order_id',
    'FAIL: повторная отправка создала новый заказ';
  assert (select count(*) from public.order_items where order_id = (v_first ->> 'order_id')::bigint) = 1,
    'FAIL: повторная отправка добавила позиции';
end;
$$;

-- ---------- Кассир не может создавать позиции ----------

reset role;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
begin
  perform public.admin_add_menu_item('Тест-код кассир', 'Тест', 100, 88888888);
  raise exception 'FAIL: кассир создал позицию';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  assert (select count(*) from public.menu_items where code = current_setting('test.new_code')::int) = 1,
    'FAIL: кассир не видит код доступной позиции';
end;
$$;

-- Функция выдачи кодов и последовательность закрыты для клиентов
do $$
begin
  perform public._next_menu_item_code();
  raise exception 'FAIL: клиенту доступна выдача кодов';
exception when insufficient_privilege then null;
end;
$$;

do $$
begin
  perform nextval('public.menu_item_code_seq');
  raise exception 'FAIL: клиенту доступна последовательность кодов';
exception when insufficient_privilege then null;
end;
$$;

reset role;

-- Права и контекст выполнения (от имени postgres)
do $$
begin
  assert not (select bool_or(has_function_privilege('anon', p.oid, 'execute'))
                from pg_proc p where p.pronamespace = 'public'::regnamespace
                 and p.proname in ('admin_add_menu_item', 'admin_update_menu_item', 'kitchen_menu',
                                   '_next_menu_item_code', '_menu_item_code_immutable')),
    'FAIL: anon может вызывать функции меню';
  assert not (select bool_or(has_function_privilege('authenticated', p.oid, 'execute'))
                from pg_proc p where p.pronamespace = 'public'::regnamespace
                 and p.proname in ('_next_menu_item_code', '_menu_item_code_immutable')),
    'FAIL: authenticated может вызывать внутренние функции кодов';
  assert (select count(*) from pg_proc p where p.pronamespace = 'public'::regnamespace
           and p.proname = 'admin_add_menu_item') = 1, 'FAIL: несколько версий admin_add_menu_item';
  assert (select bool_and(p.prosecdef and p.proconfig @> array['search_path=""'])
            from pg_proc p where p.pronamespace = 'public'::regnamespace
             and p.proname in ('admin_add_menu_item', 'admin_update_menu_item', 'admin_set_menu_item_available',
                               'kitchen_menu')),
    'FAIL: функции меню должны быть SECURITY DEFINER с пустым search_path';
  assert not has_sequence_privilege('authenticated', 'public.menu_item_code_seq', 'usage'),
    'FAIL: authenticated имеет права на последовательность';
end;
$$;
select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;

rollback;
