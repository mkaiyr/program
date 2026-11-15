-- =====================================================================
-- Проверка 16_menu_categories.sql: справочник категорий, права, перенос блюд, архив, отчёты, кухня и бар
-- Выполнять целиком на ТЕСТОВОЙ базе. Ничего не сохраняет: одна транзакция, в конце rollback.
-- Нужны: активные admin, cashier, kitchen и bar. Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- =====================================================================

begin;

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles p where role = 'cashier' and is_active
             and not exists (select 1 from public.cash_shifts s where s.cashier_id = p.id and s.closed_at is null)
           order by created_at limit 1), true),
       set_config('test.kitchen_id',
         (select id::text from public.profiles where role = 'kitchen' and is_active order by created_at limit 1), true),
       set_config('test.bar_id',
         (select id::text from public.profiles where role = 'bar' and is_active order by created_at limit 1), true),
       set_config('test.t0', now()::text, true);

-- ---------- 0. Схема, права, перенос существующих данных ----------
do $$
declare f text;
begin
  foreach f in array array['admin_add_category(text)', 'admin_rename_category(text,text)', 'admin_delete_category(text)'] loop
    if not exists (select 1 from pg_proc p where p.oid = to_regprocedure('public.' || f)
                    and p.prosecdef and p.proconfig @> array['search_path=""']) then
      raise exception 'FAIL: % — нет, не SECURITY DEFINER или без search_path', f;
    end if;
    if has_function_privilege('anon', 'public.' || f, 'execute') then raise exception 'FAIL: % доступна anon', f; end if;
    if not has_function_privilege('authenticated', 'public.' || f, 'execute') then raise exception 'FAIL: % недоступна authenticated', f; end if;
  end loop;
  if has_table_privilege('authenticated', 'public.menu_categories', 'insert, update, delete')
     or has_table_privilege('anon', 'public.menu_categories', 'select, insert, update, delete') then
    raise exception 'FAIL: у клиентов есть лишние права на menu_categories';
  end if;
  if not (select relrowsecurity from pg_class where oid = 'public.menu_categories'::regclass) then
    raise exception 'FAIL: у menu_categories не включена RLS';
  end if;
  if not exists (select 1 from pg_constraint where conname = 'menu_items_category_fkey'
                  and confdeltype = 'r' and confupdtype = 'c') then
    raise exception 'FAIL: внешний ключ блюд на категории должен быть ON UPDATE CASCADE ON DELETE RESTRICT';
  end if;
  if has_function_privilege('authenticated', 'public.menu_items_category_ref()', 'execute') then
    raise exception 'FAIL: триггерная функция доступна клиентам';
  end if;
  if exists (select 1 from public.menu_items m where not exists (select 1 from public.menu_categories c where c.name = m.category)) then
    raise exception 'FAIL: есть блюда с категорией, которой нет в справочнике';
  end if;
  if (select count(*) from public.menu_categories) <> (select count(distinct lower(name)) from public.menu_categories) then
    raise exception 'FAIL: названия категорий не уникальны без учёта регистра';
  end if;
end;
$$;

-- ---------- 1. Прямой SQL (роль владельца, без пользователя) ----------
insert into public.menu_categories (name) values ('ТЕСТ-кат-SQL');
do $$
begin
  begin insert into public.menu_categories (name) values ('тест-КАТ-sql'); raise exception 'FAIL: дубль без учёта регистра принят';
  exception when unique_violation then null; end;
  begin insert into public.menu_categories (name) values (' с пробелом '); raise exception 'FAIL: имя с пробелами по краям принято';
  exception when check_violation then null; end;
  begin insert into public.menu_categories (name) values (''); raise exception 'FAIL: пустое имя принято';
  exception when check_violation then null; end;
end;
$$;
-- новое блюдо с новой категорией создаёт категорию; другое написание приводится к справочному
insert into public.menu_items (name, category, price, station) values
  ('ТЕСТ Кат А', 'ТЕСТ-кат-А', 1000, 'kitchen'),
  ('ТЕСТ Кат Б', 'тест-кат-sql', 500, 'bar');
do $$
begin
  if not exists (select 1 from public.menu_categories where name = 'ТЕСТ-кат-А') then
    raise exception 'FAIL: категория не создана при добавлении блюда прямым SQL';
  end if;
  if (select category from public.menu_items where name = 'ТЕСТ Кат Б') <> 'ТЕСТ-кат-SQL' then
    raise exception 'FAIL: написание категории не приведено к справочнику';
  end if;
end;
$$;
select set_config('test.a', (select id::text from public.menu_items where name = 'ТЕСТ Кат А'), true),
       set_config('test.b', (select id::text from public.menu_items where name = 'ТЕСТ Кат Б'), true);

-- ---------- 2. Администратор: добавление, дубли, ошибки ввода ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare r jsonb;
begin
  r := public.admin_add_category('  ТЕСТ-кат-новая  ');
  if r ->> 'name' <> 'ТЕСТ-кат-новая' then raise exception 'FAIL: имя не обрезано: %', r; end if;
  begin perform public.admin_add_category('тест-КАТ-новая'); raise exception 'FAIL: дубль принят';
  exception when others then if sqlerrm <> 'CATEGORY_EXISTS' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_add_category('   '); raise exception 'FAIL: пустое имя принято';
  exception when others then if sqlerrm <> 'INVALID_CATEGORY' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_add_category(repeat('я', 51)); raise exception 'FAIL: слишком длинное имя принято';
  exception when others then if sqlerrm <> 'INVALID_CATEGORY' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_add_category(null); raise exception 'FAIL: null принят';
  exception when others then if sqlerrm <> 'INVALID_CATEGORY' then raise exception 'FAIL: %', sqlerrm; end if; end;
  -- администратор видит справочник
  if (select count(*) from public.menu_categories where name like 'ТЕСТ-кат-%') <> 3 then
    raise exception 'FAIL: администратор не видит добавленные категории';
  end if;
end;
$$;
reset role;

-- ---------- 3. Кассир, кухня, бар: изменять категории нельзя ----------
do $$
declare who text;
begin
  foreach who in array array['cashier', 'kitchen', 'bar'] loop
    perform set_config('request.jwt.claims',
              json_build_object('sub', current_setting('test.' || who || '_id'), 'role', 'authenticated')::text, true);
    set local role authenticated;
    begin perform public.admin_add_category('ТЕСТ-хак'); raise exception 'FAIL: % добавил категорию', who;
    exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: % — %', who, sqlerrm; end if; end;
    begin perform public.admin_rename_category('ТЕСТ-кат-SQL', 'ТЕСТ-хак'); raise exception 'FAIL: % переименовал', who;
    exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: % — %', who, sqlerrm; end if; end;
    begin perform public.admin_delete_category('ТЕСТ-кат-новая'); raise exception 'FAIL: % удалил', who;
    exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: % — %', who, sqlerrm; end if; end;
    begin insert into public.menu_categories (name) values ('ТЕСТ-хак'); raise exception 'FAIL: % — прямой INSERT', who;
    exception when insufficient_privilege then null; end;
    begin update public.menu_categories set name = 'ТЕСТ-хак'; raise exception 'FAIL: % — прямой UPDATE', who;
    exception when insufficient_privilege then null; end;
    begin delete from public.menu_categories; raise exception 'FAIL: % — прямой DELETE', who;
    exception when insufficient_privilege then null; end;
    reset role;
  end loop;
  if exists (select 1 from public.menu_categories where name in ('ТЕСТ-хак')) then raise exception 'FAIL: следы чужих правок'; end if;
end;
$$;

-- чтение справочника: кассир может, анонимный клиент нет
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  if (select count(*) from public.menu_categories where name = 'ТЕСТ-кат-SQL') <> 1 then
    raise exception 'FAIL: кассир не видит категории';
  end if;
end;
$$;
reset role;
set local role anon;
do $$
begin
  begin perform 1 from public.menu_categories; raise exception 'FAIL: anon читает категории';
  exception when insufficient_privilege then null; end;
end;
$$;
reset role;

-- ---------- 4. Предложение блюда станцией: только в существующей категории ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.kitchen_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare r jsonb;
begin
  begin perform public.station_add_menu_item('ТЕСТ Предложение 1', 'ТЕСТ-кат-несуществующая', 700);
    raise exception 'FAIL: предложение в несуществующей категории принято';
  exception when others then if sqlerrm <> 'CATEGORY_NOT_FOUND' then raise exception 'FAIL: %', sqlerrm; end if; end;
  if exists (select 1 from public.menu_categories where name = 'ТЕСТ-кат-несуществующая') then
    raise exception 'FAIL: повар создал категорию';
  end if;
  r := public.station_add_menu_item('ТЕСТ Предложение 2', 'тест-кат-sql', 700);   -- написание приводится к справочнику
  if (select category from public.menu_items where name = 'ТЕСТ Предложение 2') <> 'ТЕСТ-кат-SQL' then
    raise exception 'FAIL: категория предложения не приведена к справочнику';
  end if;
end;
$$;
reset role;

-- ---------- 5. Заказ до переименования; снимки для сравнения ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
select public.shift_open(1000);
select set_config('test.o1', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.a')::bigint, 'quantity', 2, 'expected_price', 1000),
         jsonb_build_object('menu_item_id', current_setting('test.b')::bigint, 'quantity', 1, 'expected_price', 500)),
         2750)::text, true);   -- 2500 + 250
reset role;
select set_config('test.items_md5', (select md5(string_agg(concat_ws('|', i.id, i.order_id, i.item_name, i.unit_price, i.quantity,
         i.line_total, i.station, i.item_code, i.category), ',' order by i.id)) from public.order_items i
         where i.order_id = (current_setting('test.o1')::jsonb ->> 'order_id')::bigint), true),
       set_config('test.order_md5', (select md5(t::text) from public.orders t
         where t.id = (current_setting('test.o1')::jsonb ->> 'order_id')::bigint), true);

-- ---------- 6. Переименование: блюда (в том числе архивное) следуют за категорией, история не меняется ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
select public.admin_archive_menu_item(current_setting('test.b')::bigint);
do $$
declare r jsonb;
begin
  r := public.admin_rename_category('ТЕСТ-кат-SQL', 'ТЕСТ-кат-Переименована');
  if (r ->> 'updated')::int <> 2 or r ->> 'category' <> 'ТЕСТ-кат-Переименована' then raise exception 'FAIL: результат переименования: %', r; end if;
  if exists (select 1 from public.menu_items where name in ('ТЕСТ Кат Б', 'ТЕСТ Предложение 2') and category <> 'ТЕСТ-кат-Переименована') then
    raise exception 'FAIL: блюда (в том числе архивное) не перенесены в переименованную категорию';
  end if;
  if exists (select 1 from public.menu_categories where name = 'ТЕСТ-кат-SQL') then raise exception 'FAIL: старое имя осталось'; end if;
  begin perform public.admin_rename_category('ТЕСТ-кат-SQL', 'ТЕСТ-кат-Другая'); raise exception 'FAIL: переименована несуществующая';
  exception when others then if sqlerrm <> 'NOT_FOUND' then raise exception 'FAIL: %', sqlerrm; end if; end;
  -- только смена регистра — обычное переименование, а не объединение
  r := public.admin_rename_category('ТЕСТ-кат-Переименована', 'тест-кат-переименована');
  if r ->> 'category' <> 'тест-кат-переименована' or (r ->> 'updated')::int <> 2 then raise exception 'FAIL: смена регистра: %', r; end if;
  r := public.admin_rename_category('тест-кат-переименована', 'ТЕСТ-кат-Переименована');
  -- то же имя — ничего не меняется
  r := public.admin_rename_category('ТЕСТ-кат-Переименована', 'ТЕСТ-кат-Переименована');
  if (r ->> 'updated')::int <> 0 then raise exception 'FAIL: то же имя: %', r; end if;
end;
$$;
reset role;

do $$
begin
  if md5((select string_agg(concat_ws('|', i.id, i.order_id, i.item_name, i.unit_price, i.quantity,
         i.line_total, i.station, i.item_code, i.category), ',' order by i.id) from public.order_items i
         where i.order_id = (current_setting('test.o1')::jsonb ->> 'order_id')::bigint)) <> current_setting('test.items_md5') then
    raise exception 'FAIL: переименование изменило позиции старого заказа (снимок категории)';
  end if;
  if (select md5(t::text) from public.orders t where t.id = (current_setting('test.o1')::jsonb ->> 'order_id')::bigint)
       <> current_setting('test.order_md5') then
    raise exception 'FAIL: переименование изменило заказ';
  end if;
end;
$$;

-- ---------- 7. Перенос блюда между категориями, объединение, восстановление ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare r jsonb;
begin
  -- блюдо переносится в другую (существующую) категорию, в новую (создаётся), и регистр приводится
  perform public.admin_update_menu_item(current_setting('test.a')::bigint, 'ТЕСТ Кат А', 'ТЕСТ-кат-новая', 1000);
  if (select category from public.menu_items where id = current_setting('test.a')::bigint) <> 'ТЕСТ-кат-новая' then
    raise exception 'FAIL: блюдо не перенесено в другую категорию';
  end if;
  perform public.admin_update_menu_item(current_setting('test.a')::bigint, 'ТЕСТ Кат А', 'тест-кат-ЖДЁТ-создания', 1000);
  if not exists (select 1 from public.menu_categories where name = 'тест-кат-ЖДЁТ-создания') then
    raise exception 'FAIL: администратор не создал категорию при переносе блюда';
  end if;
  perform public.admin_update_menu_item(current_setting('test.a')::bigint, 'ТЕСТ Кат А', 'ТЕСТ-кат-Новая', 1000);
  if (select category from public.menu_items where id = current_setting('test.a')::bigint) <> 'ТЕСТ-кат-новая' then
    raise exception 'FAIL: написание при переносе не приведено к справочнику';
  end if;
  -- объединение: блюда уходят в существующую категорию, старая удаляется
  r := public.admin_rename_category('ТЕСТ-кат-Переименована', 'ТЕСТ-КАТ-НОВАЯ');
  if r ->> 'category' <> 'ТЕСТ-кат-новая' or (r ->> 'updated')::int <> 2 then raise exception 'FAIL: объединение: %', r; end if;
  if exists (select 1 from public.menu_categories where name = 'ТЕСТ-кат-Переименована') then raise exception 'FAIL: старая категория после объединения'; end if;
  if (select count(*) from public.menu_items where category = 'ТЕСТ-кат-новая') <> 3 then raise exception 'FAIL: блюда после объединения'; end if;
  -- восстановление архивного блюда в категории, которая была переименована и объединена
  perform public.admin_restore_menu_item(current_setting('test.b')::bigint);
  if (select category from public.menu_items where id = current_setting('test.b')::bigint) <> 'ТЕСТ-кат-новая'
     or not (select is_available and archived_at is null from public.menu_items where id = current_setting('test.b')::bigint) then
    raise exception 'FAIL: восстановление блюда после переименования';
  end if;
end;
$$;
reset role;

-- ---------- 8. Отчёты, кухня и бар видят актуальное имя ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare rep jsonb;
begin
  rep := public.admin_report(current_setting('test.t0')::timestamptz, now() + interval '1 minute');
  if not exists (select 1 from jsonb_array_elements(rep -> 'categories') c
                  where c ->> 'category' = 'ТЕСТ-кат-новая' and (c ->> 'revenue')::numeric = 2500) then
    raise exception 'FAIL: отчёт не показывает новое имя категории с прежней выручкой: %', rep -> 'categories';
  end if;
  if (rep -> 'summary' ->> 'total') is not null and (rep -> 'summary' ->> 'total')::numeric <> 2750 then
    raise exception 'FAIL: итог отчёта изменился';
  end if;
  if not exists (select 1 from jsonb_array_elements(public.station_menu()) m where m ->> 'category' = 'ТЕСТ-кат-новая') then
    raise exception 'FAIL: station_menu не показывает новое имя категории';
  end if;
end;
$$;
reset role;

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.kitchen_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  if not exists (select 1 from jsonb_array_elements(public.station_menu()) m where m ->> 'category' = 'ТЕСТ-кат-новая') then
    raise exception 'FAIL: кухня не видит новое имя категории';
  end if;
end;
$$;
reset role;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.bar_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  if not exists (select 1 from jsonb_array_elements(public.station_menu()) m where m ->> 'category' = 'ТЕСТ-кат-новая') then
    raise exception 'FAIL: бар не видит новое имя категории';
  end if;
end;
$$;
reset role;

-- кассир видит блюдо в новой категории (RLS не изменилась), заказ по-прежнему оформляется
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  if not exists (select 1 from public.menu_items where id = current_setting('test.a')::bigint and category = 'ТЕСТ-кат-новая') then
    raise exception 'FAIL: кассир не видит блюдо в новой категории';
  end if;
  perform public.place_order(gen_random_uuid(), jsonb_build_array(
    jsonb_build_object('menu_item_id', current_setting('test.a')::bigint, 'quantity', 1, 'expected_price', 1000)), 1100);
end;
$$;
reset role;

-- ---------- 9. Удаление: пустая — можно, с блюдами (в том числе архивными) — нельзя ----------
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare v_detail text;
begin
  begin perform public.admin_delete_category('ТЕСТ-кат-новая'); raise exception 'FAIL: удалена категория с блюдами';
  exception when others then
    get stacked diagnostics v_detail = pg_exception_detail;
    if sqlerrm <> 'CATEGORY_NOT_EMPTY' or v_detail <> '3;0' then raise exception 'FAIL: % / %', sqlerrm, v_detail; end if;
  end;
  perform public.admin_archive_menu_item(current_setting('test.b')::bigint);
  begin perform public.admin_delete_category('ТЕСТ-кат-новая'); raise exception 'FAIL: удалена категория с архивным блюдом';
  exception when others then
    get stacked diagnostics v_detail = pg_exception_detail;
    if sqlerrm <> 'CATEGORY_NOT_EMPTY' or v_detail <> '3;1' then raise exception 'FAIL: % / %', sqlerrm, v_detail; end if;
  end;
  begin perform public.admin_delete_category('ТЕСТ-нет-такой'); raise exception 'FAIL: удалена несуществующая';
  exception when others then if sqlerrm <> 'NOT_FOUND' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_delete_category(''); raise exception 'FAIL: пустое имя';
  exception when others then if sqlerrm <> 'INVALID_CATEGORY' then raise exception 'FAIL: %', sqlerrm; end if; end;
  -- пустая категория удаляется; повторно — NOT_FOUND
  perform public.admin_delete_category('тест-кат-ЖДЁТ-создания');
  if exists (select 1 from public.menu_categories where name = 'тест-кат-ЖДЁТ-создания') then raise exception 'FAIL: категория не удалена'; end if;
  begin perform public.admin_delete_category('тест-кат-ЖДЁТ-создания'); raise exception 'FAIL: повторное удаление';
  exception when others then if sqlerrm <> 'NOT_FOUND' then raise exception 'FAIL: %', sqlerrm; end if; end;
end;
$$;
reset role;

-- прямое удаление категории с блюдами блокирует внешний ключ (в том числе для владельца)
do $$
begin
  begin delete from public.menu_categories where name = 'ТЕСТ-кат-новая'; raise exception 'FAIL: прямой DELETE категории с блюдами';
  exception when foreign_key_violation then null; end;
end;
$$;

-- ---------- 10. После окончательного удаления блюд категория удаляется, история остаётся ----------
-- предложение станции, которое ждёт решения (архивировать его нельзя), убираем прямым SQL
delete from public.menu_item_reviews where item_id = (select id from public.menu_items where name = 'ТЕСТ Предложение 2');
delete from public.menu_items where name = 'ТЕСТ Предложение 2';
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare v_id bigint;
begin
  perform public.admin_archive_menu_item(current_setting('test.a')::bigint);
  for v_id in select id from public.menu_items where category = 'ТЕСТ-кат-новая' and archived_at is null and approved_at is not null loop
    perform public.admin_archive_menu_item(v_id);
  end loop;
  for v_id in select id from public.menu_items where category = 'ТЕСТ-кат-новая' and archived_at is not null loop
    perform public.admin_purge_menu_item(v_id);
  end loop;
  perform public.admin_delete_category('ТЕСТ-кат-новая');
  if exists (select 1 from public.menu_categories where name = 'ТЕСТ-кат-новая') then raise exception 'FAIL: категория не удалена после очистки'; end if;
  if (select count(*) from public.order_items i where i.order_id = (current_setting('test.o1')::jsonb ->> 'order_id')::bigint) <> 2 then
    raise exception 'FAIL: позиции заказа потеряны';
  end if;
end;
$$;
reset role;

do $$
begin
  if (select string_agg(i.category, ',' order by i.category collate "C") from public.order_items i
       where i.order_id = (current_setting('test.o1')::jsonb ->> 'order_id')::bigint) <> 'ТЕСТ-кат-SQL,ТЕСТ-кат-А' then
    raise exception 'FAIL: снимок категории в старом заказе изменился';
  end if;
end;
$$;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;
rollback;
