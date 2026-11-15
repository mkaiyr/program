-- =====================================================================
-- Проверка 15_menu_item_archive.sql: архив, восстановление, окончательное удаление, сохранность истории
-- Выполнять целиком на ТЕСТОВОЙ базе. Ничего не сохраняет: одна транзакция, в конце rollback.
-- Нужны: активные admin и cashier. Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- =====================================================================

begin;

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles p where role = 'cashier' and is_active
             and not exists (select 1 from public.cash_shifts s where s.cashier_id = p.id and s.closed_at is null)
           order by created_at limit 1), true),
       set_config('test.t0', now()::text, true);

insert into public.menu_items (name, category, price, station) values
  ('ТЕСТ Архив А', 'ТЕСТ-арх', 3000, 'kitchen'),
  ('ТЕСТ Архив Б', 'ТЕСТ-арх', 1000, 'bar'),
  ('ТЕСТ Без продаж', 'ТЕСТ-арх', 500, 'kitchen');
insert into public.menu_items (name, category, price, station, approved_at, is_available) values ('ТЕСТ Ждёт решения', 'ТЕСТ-арх', 100, 'kitchen', null, false);
insert into public.menu_items (name, category, price, station, approved_at, rejected_at, is_available)
  values ('ТЕСТ Отклонено', 'ТЕСТ-арх', 100, 'kitchen', null, now(), false);
select set_config('test.a', (select id::text from public.menu_items where name = 'ТЕСТ Архив А'), true),
       set_config('test.b', (select id::text from public.menu_items where name = 'ТЕСТ Архив Б'), true),
       set_config('test.c', (select id::text from public.menu_items where name = 'ТЕСТ Без продаж'), true),
       set_config('test.pending', (select id::text from public.menu_items where name = 'ТЕСТ Ждёт решения'), true),
       set_config('test.rejected', (select id::text from public.menu_items where name = 'ТЕСТ Отклонено'), true);

-- ---------- 0. Схема и права ----------
do $$
declare f text;
begin
  foreach f in array array['admin_archive_menu_item(bigint)', 'admin_restore_menu_item(bigint)',
                           'admin_purge_menu_item(bigint)', 'admin_menu_purge_log()', 'station_menu()'] loop
    if not exists (select 1 from pg_proc p where p.oid = to_regprocedure('public.' || f)
                    and p.prosecdef and p.proconfig @> array['search_path=""']) then
      raise exception 'FAIL: % — нет, не SECURITY DEFINER или без search_path', f;
    end if;
    if has_function_privilege('anon', 'public.' || f, 'execute') then raise exception 'FAIL: % доступна anon', f; end if;
    if not has_function_privilege('authenticated', 'public.' || f, 'execute') then raise exception 'FAIL: % недоступна authenticated', f; end if;
  end loop;
  if has_table_privilege('authenticated', 'public.menu_item_purge_log', 'select, insert, update, delete')
     or has_table_privilege('authenticated', 'public.menu_items', 'insert, update, delete') then
    raise exception 'FAIL: у клиентов есть прямые права на menu_items или журнал удалений';
  end if;
  if (select confdeltype from pg_constraint where conrelid = 'public.order_items'::regclass and contype = 'f'
         and confrelid = 'public.menu_items'::regclass) <> 'n' then
    raise exception 'FAIL: order_items.menu_item_id должен быть ON DELETE SET NULL';
  end if;
end;
$$;

-- ---------- 1. Заказы до архивации (кассир, со сменой) ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
select public.shift_open(1000);
select set_config('test.o1', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.a')::bigint, 'quantity', 2, 'expected_price', 3000),
         jsonb_build_object('menu_item_id', current_setting('test.b')::bigint, 'quantity', 1, 'expected_price', 1000)),
         7700)::text, true);   -- 7000 + 700
select set_config('test.o2', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.a')::bigint, 'quantity', 1, 'expected_price', 3000)),
         3300)::text, true);
reset role;

do $$
begin
  -- снимок категории записан триггером
  if (select count(*) from public.order_items i join public.orders o on o.id = i.order_id
       where o.id in ((current_setting('test.o1')::jsonb ->> 'order_id')::bigint, (current_setting('test.o2')::jsonb ->> 'order_id')::bigint)
         and i.category = 'ТЕСТ-арх') <> 3 then
    raise exception 'FAIL: категория не записана в order_items при заказе';
  end if;
end;
$$;

-- Снимки: заказы, позиции (без menu_item_id), смена, отчёт
select set_config('test.orders_md5', (select md5(string_agg(t::text, ',' order by t.id)) from public.orders t
         where t.id in ((current_setting('test.o1')::jsonb ->> 'order_id')::bigint, (current_setting('test.o2')::jsonb ->> 'order_id')::bigint)), true),
       set_config('test.items_md5', (select md5(string_agg(concat_ws('|', i.id, i.order_id, i.item_name, i.unit_price, i.quantity,
         i.line_total, i.station, i.item_code, i.category), ',' order by i.id)) from public.order_items i
         where i.order_id in ((current_setting('test.o1')::jsonb ->> 'order_id')::bigint, (current_setting('test.o2')::jsonb ->> 'order_id')::bigint)), true);

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
select set_config('test.report0', public.admin_report(current_setting('test.t0')::timestamptz, now() + interval '1 minute')::text, true);
reset role;
select set_config('test.shift0', (select public._shift_json(s.id)::text from public.cash_shifts s
         where s.cashier_id = current_setting('test.cashier_id')::uuid and s.closed_at is null), true);

-- ---------- 2. Кассир не может архивировать, восстанавливать и удалять ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  begin perform public.admin_archive_menu_item(current_setting('test.a')::bigint); raise exception 'FAIL: кассир архивировал';
  exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_restore_menu_item(current_setting('test.a')::bigint); raise exception 'FAIL: кассир восстановил';
  exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_purge_menu_item(current_setting('test.a')::bigint); raise exception 'FAIL: кассир удалил';
  exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_menu_purge_log(); raise exception 'FAIL: кассир прочитал журнал';
  exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin update public.menu_items set archived_at = now() where id = current_setting('test.a')::bigint;
    raise exception 'FAIL: прямой UPDATE меню кассиром';
  exception when insufficient_privilege then null; end;
end;
$$;
reset role;

-- ---------- 3. Архивация администратором ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare r jsonb;
begin
  r := public.admin_archive_menu_item(current_setting('test.a')::bigint);
  if (r ->> 'changed')::boolean is not true then raise exception 'FAIL: архивация %', r; end if;
  if (public.admin_archive_menu_item(current_setting('test.a')::bigint) ->> 'changed')::boolean is not false then
    raise exception 'FAIL: повторная архивация должна быть без изменений';
  end if;
  begin perform public.admin_archive_menu_item(current_setting('test.pending')::bigint); raise exception 'FAIL: архивирована ждущая позиция';
  exception when others then if sqlerrm <> 'NOT_APPROVED' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_archive_menu_item(current_setting('test.rejected')::bigint); raise exception 'FAIL: архивирована отклонённая позиция';
  exception when others then if sqlerrm <> 'ITEM_REJECTED' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_archive_menu_item(-1); raise exception 'FAIL: несуществующее блюдо';
  exception when others then if sqlerrm <> 'NOT_FOUND' then raise exception 'FAIL: %', sqlerrm; end if; end;
  -- наличие архивного блюда включить нельзя ни одной функцией
  begin perform public.admin_set_menu_item_available(current_setting('test.a')::bigint, true);
    raise exception 'FAIL: архивное блюдо включено в наличие';
  exception when check_violation then null; end;
  -- в списке администратора оно есть с флагом archived_at
  if not exists (select 1 from jsonb_array_elements(public.station_menu()) e
                  where (e ->> 'id')::bigint = current_setting('test.a')::bigint and e ->> 'archived_at' is not null) then
    raise exception 'FAIL: администратор не видит архивное блюдо с archived_at';
  end if;
  -- категории: переименование затрагивает и архивные блюда
  perform public.admin_rename_category('ТЕСТ-арх', 'ТЕСТ-арх2');
  if (select category from public.menu_items where id = current_setting('test.a')::bigint) <> 'ТЕСТ-арх2' then
    raise exception 'FAIL: переименование категории не затронуло архивное блюдо';
  end if;
  perform public.admin_rename_category('ТЕСТ-арх2', 'ТЕСТ-арх');
end;
$$;
reset role;

-- ---------- 4. Кассир: архивное блюдо не видно и не продаётся; остальное продаётся ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare r jsonb;
begin
  if exists (select 1 from public.menu_items where id = current_setting('test.a')::bigint) then
    raise exception 'FAIL: кассир видит архивное блюдо';
  end if;
  begin
    perform public.place_order(gen_random_uuid(), jsonb_build_array(
      jsonb_build_object('menu_item_id', current_setting('test.a')::bigint, 'quantity', 1, 'expected_price', 3000),
      jsonb_build_object('menu_item_id', current_setting('test.b')::bigint, 'quantity', 1, 'expected_price', 1000)), 4400);
    raise exception 'FAIL: заказ с архивным блюдом принят';
  exception when others then
    if sqlerrm <> 'MENU_CHANGED' then raise exception 'FAIL: ожидался MENU_CHANGED, получено %', sqlerrm; end if;
  end;
  -- остальные позиции корзины продаются отдельно: надбавка по-прежнему 10%
  r := public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.b')::bigint, 'quantity', 1, 'expected_price', 1000)), 1100);
  if (r ->> 'total')::numeric <> 1100 or (r ->> 'service_amount')::numeric <> 100 then raise exception 'FAIL: расчёт нарушен: %', r; end if;
end;
$$;
reset role;

-- кухня архивного блюда не видит
update public.profiles set role = 'kitchen' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;
do $$
begin
  if exists (select 1 from jsonb_array_elements(public.station_menu()) e where (e ->> 'id')::bigint = current_setting('test.a')::bigint) then
    raise exception 'FAIL: кухня видит архивное блюдо';
  end if;
  if not exists (select 1 from jsonb_array_elements(public.station_menu()) e where e ->> 'name' = 'ТЕСТ Без продаж') then
    raise exception 'FAIL: кухня не видит обычное блюдо';
  end if;
end;
$$;
reset role;
update public.profiles set role = 'cashier' where id = current_setting('test.cashier_id')::uuid;

-- ---------- 5. Архивация не меняет заказы, смену и отчёт ----------
do $$
begin
  if (select md5(string_agg(t::text, ',' order by t.id)) from public.orders t
       where t.id in ((current_setting('test.o1')::jsonb ->> 'order_id')::bigint, (current_setting('test.o2')::jsonb ->> 'order_id')::bigint))
     <> current_setting('test.orders_md5') then raise exception 'FAIL: архивация изменила заказы'; end if;
  if (select md5(string_agg(concat_ws('|', i.id, i.order_id, i.item_name, i.unit_price, i.quantity, i.line_total, i.station,
        i.item_code, i.category), ',' order by i.id)) from public.order_items i
       where i.order_id in ((current_setting('test.o1')::jsonb ->> 'order_id')::bigint, (current_setting('test.o2')::jsonb ->> 'order_id')::bigint))
     <> current_setting('test.items_md5') then raise exception 'FAIL: архивация изменила позиции заказов'; end if;
end;
$$;

-- ---------- 6. Восстановление ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  if (public.admin_restore_menu_item(current_setting('test.a')::bigint) ->> 'changed')::boolean is not true then raise exception 'FAIL: восстановление'; end if;
  if (public.admin_restore_menu_item(current_setting('test.a')::bigint) ->> 'changed')::boolean is not false then raise exception 'FAIL: повтор восстановления'; end if;
  if not (select is_available and archived_at is null from public.menu_items where id = current_setting('test.a')::bigint) then
    raise exception 'FAIL: восстановленное блюдо не в продаже';
  end if;
  begin perform public.admin_purge_menu_item(current_setting('test.a')::bigint); raise exception 'FAIL: удалено активное блюдо';
  exception when others then if sqlerrm <> 'NOT_ARCHIVED' then raise exception 'FAIL: %', sqlerrm; end if; end;
end;
$$;
reset role;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  if not exists (select 1 from public.menu_items where id = current_setting('test.a')::bigint) then raise exception 'FAIL: кассир не видит восстановленное блюдо'; end if;
  perform public.place_order(gen_random_uuid(), jsonb_build_array(
    jsonb_build_object('menu_item_id', current_setting('test.a')::bigint, 'quantity', 1, 'expected_price', 3000)), 3300);
end;
$$;
reset role;

-- ---------- 7. Окончательное удаление блюда без продаж ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare r jsonb;
begin
  perform public.admin_archive_menu_item(current_setting('test.c')::bigint);
  r := public.admin_purge_menu_item(current_setting('test.c')::bigint);
  if (r ->> 'orders_count')::int <> 0 or (r ->> 'purged')::boolean is not true then raise exception 'FAIL: удаление без продаж %', r; end if;
  if exists (select 1 from public.menu_items where name = 'ТЕСТ Без продаж') then raise exception 'FAIL: блюдо осталось в menu_items'; end if;
  begin perform public.admin_purge_menu_item(current_setting('test.c')::bigint); raise exception 'FAIL: повторное удаление';
  exception when others then if sqlerrm <> 'NOT_FOUND' then raise exception 'FAIL: %', sqlerrm; end if; end;
  -- название освободилось
  perform public.admin_add_menu_item('ТЕСТ Без продаж', 'ТЕСТ-арх', 500);
end;
$$;
reset role;

-- ---------- 8. Окончательное удаление ПРОДАННОГО блюда: история и отчёты не меняются ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
select set_config('test.report1', public.admin_report(current_setting('test.t0')::timestamptz, now() + interval '1 minute')::text, true);
select public.admin_archive_menu_item(current_setting('test.a')::bigint);
select set_config('test.purge_a', public.admin_purge_menu_item(current_setting('test.a')::bigint)::text, true);
reset role;

do $$
declare
  o1 bigint := (current_setting('test.o1')::jsonb ->> 'order_id')::bigint;
  o2 bigint := (current_setting('test.o2')::jsonb ->> 'order_id')::bigint;
  rep jsonb;
begin
  if (current_setting('test.purge_a')::jsonb ->> 'orders_count')::int < 2 then
    raise exception 'FAIL: блюдо было минимум в двух заказах: %', current_setting('test.purge_a');
  end if;
  if exists (select 1 from public.menu_items where name = 'ТЕСТ Архив А') then raise exception 'FAIL: блюдо не удалено'; end if;
  -- у позиций заказов menu_item_id = NULL, всё остальное прежнее
  if (select count(*) from public.order_items where order_id in (o1, o2) and item_name = 'ТЕСТ Архив А' and menu_item_id is null) <> 2 then
    raise exception 'FAIL: у проданных позиций menu_item_id должен стать NULL';
  end if;
  if (select md5(string_agg(t::text, ',' order by t.id)) from public.orders t where t.id in (o1, o2)) <> current_setting('test.orders_md5') then
    raise exception 'FAIL: удаление блюда изменило заказы';
  end if;
  if (select md5(string_agg(concat_ws('|', i.id, i.order_id, i.item_name, i.unit_price, i.quantity, i.line_total, i.station,
        i.item_code, i.category), ',' order by i.id)) from public.order_items i where i.order_id in (o1, o2))
     <> current_setting('test.items_md5') then raise exception 'FAIL: удаление блюда изменило названия, цены, количества или категории в заказах'; end if;
  -- итоги заказов и надбавка
  if not exists (select 1 from public.orders where id = o1 and subtotal = 7000 and service_amount = 700 and total = 7700) then
    raise exception 'FAIL: суммы заказа изменились';
  end if;
  -- смена считает так же
  if (select public._shift_json(s.id) ->> 'sales_total' from public.cash_shifts s
       where s.cashier_id = current_setting('test.cashier_id')::uuid and s.closed_at is null)::numeric
     < (current_setting('test.shift0')::jsonb ->> 'sales_total')::numeric then
    raise exception 'FAIL: выручка смены уменьшилась после удаления блюда';
  end if;
  -- кухня и бар видят состав заказа по снимкам
  if not exists (select 1 from jsonb_array_elements(public._station_orders('kitchen')) e, jsonb_array_elements(e -> 'items') i
                  where (e ->> 'id')::bigint = o1 and i ->> 'item_name' = 'ТЕСТ Архив А') then
    raise exception 'FAIL: кухня потеряла состав заказа после удаления блюда';
  end if;
  -- журнал
  if not exists (select 1 from public.menu_item_purge_log where name = 'ТЕСТ Архив А' and category = 'ТЕСТ-арх' and price = 3000
                  and orders_count >= 2 and purged_by = current_setting('test.admin_id')::uuid) then
    raise exception 'FAIL: нет записи в журнале удалений';
  end if;
  begin update public.menu_item_purge_log set name = 'x'; raise exception 'FAIL: журнал изменяем';
  exception when others then if sqlerrm <> 'PURGE_LOG_READONLY' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin delete from public.menu_item_purge_log; raise exception 'FAIL: журнал удаляется';
  exception when others then if sqlerrm <> 'PURGE_LOG_READONLY' then raise exception 'FAIL: %', sqlerrm; end if; end;
end;
$$;

-- Отчёт после удаления: те же блюда, категории, суммы (кроме добавленных нами заказов после снимка report1)
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare
  r1 jsonb := current_setting('test.report1')::jsonb;
  r2 jsonb := public.admin_report(current_setting('test.t0')::timestamptz, now() + interval '1 minute');
begin
  if r1 -> 'top_items' <> r2 -> 'top_items' or r1 -> 'categories' <> r2 -> 'categories'
     or r1 -> 'cashiers' <> r2 -> 'cashiers' or r1 -> 'daily' <> r2 -> 'daily'
     or r1 ->> 'revenue' <> r2 ->> 'revenue' or r1 ->> 'orders_count' <> r2 ->> 'orders_count' then
    raise exception 'FAIL: отчёт изменился после удаления блюда: до % после %', r1, r2;
  end if;
  if not (r2 -> 'categories' @> '[{"category": "ТЕСТ-арх"}]'::jsonb) then raise exception 'FAIL: категория проданного блюда пропала из отчёта'; end if;
  if not (r2 -> 'top_items' @> '[{"name": "ТЕСТ Архив А"}]'::jsonb) then raise exception 'FAIL: блюдо пропало из топа'; end if;
  if jsonb_array_length(public.admin_menu_purge_log()) < 2 then raise exception 'FAIL: журнал для администратора'; end if;
end;
$$;
reset role;

-- Заказ с удалённым блюдом отклоняется; кассир по-прежнему видит свои заказы и состав
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  begin
    perform public.place_order(gen_random_uuid(), jsonb_build_array(
      jsonb_build_object('menu_item_id', (current_setting('test.purge_a')::jsonb ->> 'id')::bigint, 'quantity', 1, 'expected_price', 3000)), 3300);
    raise exception 'FAIL: заказ с удалённым блюдом принят';
  exception when others then if sqlerrm <> 'MENU_CHANGED' then raise exception 'FAIL: %', sqlerrm; end if; end;
  if (select count(*) from public.order_items i where i.order_id = (current_setting('test.o1')::jsonb ->> 'order_id')::bigint) <> 2 then
    raise exception 'FAIL: кассир не видит состав своего заказа';
  end if;
end;
$$;
reset role;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;
rollback;
