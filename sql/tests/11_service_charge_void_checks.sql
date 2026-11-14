-- =====================================================================
-- Проверка 11_service_charge_void.sql: надбавка 10%, итог, аннулирование, экраны станций
-- Выполнять целиком на ТЕСТОВОЙ базе. Ничего не сохраняет: одна транзакция, в конце rollback.
-- Нужны: активные admin и cashier, две доступные позиции меню.
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- =====================================================================

begin;

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true);

-- Тестовые позиции с известными ценами (откатятся)
insert into public.menu_items (name, category, price, station) values
  ('ТЕСТ Бургер 3000', 'ТЕСТ', 3000, 'kitchen'),
  ('ТЕСТ Кола 1000', 'ТЕСТ', 1000, 'bar'),
  ('ТЕСТ Копейки 999.90', 'ТЕСТ', 999.90, 'kitchen'),
  ('ТЕСТ Полтинник 5', 'ТЕСТ', 5, 'kitchen');
select set_config('test.burger', (select id::text from public.menu_items where name = 'ТЕСТ Бургер 3000'), true),
       set_config('test.cola', (select id::text from public.menu_items where name = 'ТЕСТ Кола 1000'), true),
       set_config('test.kop', (select id::text from public.menu_items where name = 'ТЕСТ Копейки 999.90'), true),
       set_config('test.five', (select id::text from public.menu_items where name = 'ТЕСТ Полтинник 5'), true);

do $$
declare
  f text;
begin
  foreach f in array array['place_order(uuid, jsonb, numeric)', 'create_order(uuid, jsonb)', 'void_order(bigint, text)'] loop
    if has_function_privilege('anon', 'public.' || f, 'execute') then
      raise exception 'FAIL: % доступна anon', f;
    end if;
    if not has_function_privilege('authenticated', 'public.' || f, 'execute') then
      raise exception 'FAIL: % недоступна authenticated', f;
    end if;
  end loop;
end;
$$;

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

-- ---------- 1. Надбавка: бургер 3000 + кола 1000 = 4000 -> 400 -> 4400 ----------
select set_config('test.r1', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.burger')::bigint, 'quantity', 1, 'expected_price', 3000),
         jsonb_build_object('menu_item_id', current_setting('test.cola')::bigint, 'quantity', 1, 'expected_price', 1000)),
         4400)::text, true);
do $$
declare r jsonb := current_setting('test.r1')::jsonb;
begin
  if (r ->> 'subtotal')::numeric <> 4000 or (r ->> 'service_amount')::numeric <> 400
     or (r ->> 'total')::numeric <> 4400 or (r ->> 'service_percent')::numeric <> 10 then
    raise exception 'FAIL: 4000 -> надбавка 400 -> 4400, получено %', r;
  end if;
  if not exists (select 1 from public.orders o where o.id = (r ->> 'order_id')::bigint
                  and o.subtotal = 4000 and o.service_amount = 400 and o.total = 4400 and o.service_percent = 10) then
    raise exception 'FAIL: заказ сохранён с другими суммами';
  end if;
  -- позиции хранят цену без надбавки
  if (select sum(line_total) from public.order_items where order_id = (r ->> 'order_id')::bigint) <> 4000 then
    raise exception 'FAIL: сумма позиций должна быть 4000 (без надбавки)';
  end if;
end;
$$;

-- ---------- 2. Округление до целого тенге: 999.90 -> 100 (99.99 -> 100); 5 -> 0.5 -> 1 ----------
select set_config('test.r2', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.kop')::bigint, 'quantity', 10, 'expected_price', 999.90)),
         null)::text, true),
       set_config('test.r3', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.five')::bigint, 'quantity', 1, 'expected_price', 5)),
         null)::text, true);
do $$
begin
  -- 10 × 999.90 = 9999.00 -> 999.90 -> 1000 -> 10999
  if (current_setting('test.r2')::jsonb ->> 'service_amount')::numeric <> 1000
     or (current_setting('test.r2')::jsonb ->> 'total')::numeric <> 10999 then
    raise exception 'FAIL: 9999 -> надбавка 1000 -> 10999, получено %', current_setting('test.r2');
  end if;
  if (current_setting('test.r3')::jsonb ->> 'service_amount')::numeric <> 1
     or (current_setting('test.r3')::jsonb ->> 'total')::numeric <> 6 then
    raise exception 'FAIL: 5 -> 0.5 округляется вверх до 1, итог 6, получено %', current_setting('test.r3');
  end if;
end;
$$;

-- ---------- 3. Неверный ожидаемый итог ----------
do $$
begin
  begin
    perform public.place_order(gen_random_uuid(), jsonb_build_array(
      jsonb_build_object('menu_item_id', current_setting('test.burger')::bigint, 'quantity', 1, 'expected_price', 3000)), 3000);
    raise exception 'FAIL: итог без надбавки принят';
  exception when others then
    if sqlerrm <> 'TOTAL_MISMATCH' then raise exception 'FAIL: ожидался TOTAL_MISMATCH, получено %', sqlerrm; end if;
  end;
end;
$$;

-- ---------- 4. Старое имя create_order тоже начисляет надбавку; повтор возвращает тот же заказ ----------
select set_config('test.req', gen_random_uuid()::text, true);
select set_config('test.r4', public.create_order(current_setting('test.req')::uuid, jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.burger')::bigint, 'quantity', 2, 'expected_price', 3000)))::text, true);
do $$
declare again jsonb;
begin
  if (current_setting('test.r4')::jsonb ->> 'total')::numeric <> 6600 then
    raise exception 'FAIL: create_order: 6000 + 10%% = 6600, получено %', current_setting('test.r4');
  end if;
  again := public.place_order(current_setting('test.req')::uuid, jsonb_build_array(
             jsonb_build_object('menu_item_id', current_setting('test.burger')::bigint, 'quantity', 2, 'expected_price', 3000)), 6600);
  if (again ->> 'duplicate')::boolean is not true or again ->> 'order_id' <> current_setting('test.r4')::jsonb ->> 'order_id'
     or (again ->> 'total')::numeric <> 6600 then
    raise exception 'FAIL: повторная отправка создала другой заказ или другую сумму: %', again;
  end if;
end;
$$;

-- ---------- 5. Экран кухни и бара: позиции есть, надбавки и цен нет ----------
reset role;
do $$
declare
  k jsonb := public._station_orders('kitchen');
  b jsonb := public._station_orders('bar');
  oid bigint := (current_setting('test.r1')::jsonb ->> 'order_id')::bigint;
begin
  if k::text ~* '(price|total|service|subtotal|надбавк)' or b::text ~* '(price|total|service|subtotal|надбавк)' then
    raise exception 'FAIL: экраны станций содержат цены или надбавку';
  end if;
  if not exists (select 1 from jsonb_array_elements(k) e where (e ->> 'id')::bigint = oid
                  and e -> 'items' @> '[{"item_name": "ТЕСТ Бургер 3000"}]'::jsonb) then
    raise exception 'FAIL: бургер не на кухне';
  end if;
  if not exists (select 1 from jsonb_array_elements(b) e where (e ->> 'id')::bigint = oid
                  and e -> 'items' @> '[{"item_name": "ТЕСТ Кола 1000"}]'::jsonb) then
    raise exception 'FAIL: кола не в баре';
  end if;
  if exists (select 1 from jsonb_array_elements(k) e, jsonb_array_elements(e -> 'items') i
              where (e ->> 'id')::bigint = oid and i ->> 'item_name' = 'ТЕСТ Кола 1000') then
    raise exception 'FAIL: напиток попал на кухню';
  end if;
end;
$$;

-- ---------- 6. Аннулирование ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare
  oid bigint := (current_setting('test.r1')::jsonb ->> 'order_id')::bigint;
  r jsonb;
begin
  begin
    perform public.void_order(oid, 'x');
    raise exception 'FAIL: слишком короткая причина принята';
  exception when others then
    if sqlerrm <> 'INVALID_REASON' then raise exception 'FAIL: ожидался INVALID_REASON, получено %', sqlerrm; end if;
  end;
  r := public.void_order(oid, 'Клиент передумал');
  if (r ->> 'changed')::boolean is not true then raise exception 'FAIL: аннулирование не выполнено'; end if;
  r := public.void_order(oid, 'Клиент передумал');
  if (r ->> 'changed')::boolean is not false then raise exception 'FAIL: повтор должен быть без изменений'; end if;
  -- заказ и состав остались в истории кассира
  if not exists (select 1 from public.orders o where o.id = oid and o.voided_at is not null
                  and o.void_reason = 'Клиент передумал' and o.voided_by = current_setting('test.cashier_id')::uuid
                  and o.total = 4400) then
    raise exception 'FAIL: заказ не отмечен или потерял сумму';
  end if;
  if (select count(*) from public.order_items where order_id = oid) <> 2 then
    raise exception 'FAIL: после аннулирования пропали позиции';
  end if;
end;
$$;
reset role;

do $$
declare oid bigint := (current_setting('test.r1')::jsonb ->> 'order_id')::bigint;
begin
  if exists (select 1 from jsonb_array_elements(public._station_orders('kitchen')) e where (e ->> 'id')::bigint = oid)
     or exists (select 1 from jsonb_array_elements(public._station_orders('bar')) e where (e ->> 'id')::bigint = oid) then
    raise exception 'FAIL: аннулированный заказ остался на экране кухни или бара';
  end if;
end;
$$;

-- Чужой заказ кассир аннулировать не может; администратор — может
do $$
declare other uuid; oid bigint := (current_setting('test.r4')::jsonb ->> 'order_id')::bigint;
begin
  select id into other from public.profiles
   where role = 'cashier' and is_active and id <> current_setting('test.cashier_id')::uuid limit 1;
  if other is not null then
    perform set_config('request.jwt.claims', json_build_object('sub', other, 'role', 'authenticated')::text, true);
    set local role authenticated;
    begin
      perform public.void_order(oid, 'Чужой заказ');
      raise exception 'FAIL: кассир аннулировал чужой заказ';
    exception when others then
      if sqlerrm <> 'ORDER_NOT_FOUND' then raise exception 'FAIL: ожидался ORDER_NOT_FOUND, получено %', sqlerrm; end if;
    end;
    reset role;
  end if;
end;
$$;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  if (public.void_order((current_setting('test.r4')::jsonb ->> 'order_id')::bigint, 'Ошибка администратора') ->> 'changed')::boolean
     is not true then
    raise exception 'FAIL: администратор не смог аннулировать заказ';
  end if;
end;
$$;
reset role;

-- Старые заказы: подытог равен итогу, надбавки нет
do $$
begin
  if exists (select 1 from public.orders where id < (current_setting('test.r1')::jsonb ->> 'order_id')::bigint
              and service_percent = 0 and (service_amount <> 0 or subtotal <> total)) then
    raise exception 'FAIL: у старых заказов нарушено subtotal = total';
  end if;
end;
$$;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;
rollback;
