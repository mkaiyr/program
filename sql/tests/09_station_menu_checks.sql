-- =====================================================================
-- Проверка 09_station_menu.sql: меню кухни и бара, решения администратора по новым позициям
-- Выполнять целиком в Supabase SQL Editor на ТЕСТОВОМ проекте (docs/MIGRATION_TEST_PLAN.md)
--
-- НИЧЕГО НЕ СОХРАНЯЕТ: всё выполняется в одной транзакции, в конце rollback.
-- (Номера последовательностей кодов и заказов при этом расходуются.)
-- Нужны: активные admin и cashier, хотя бы две доступные позиции меню.
-- Роли kitchen и bar проверяются на временно изменённом профиле кассира, одна позиция временно
-- переводится в бар — всё откатится.
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
--
-- Проверяется: столбцы, ограничения и частичные уникальные индексы; первоначальное подтверждение
-- существующих позиций; права функций и закрытый журнал; kitchen_menu и station_menu — только своя
-- станция; добавление позиции станцией (ожидает решения); запрет чужой станции (в том числе прямым
-- вызовом RPC и прямым update); переходы четырёх состояний (ожидает, в продаже, нет в наличии,
-- отклонена) и запрет запрещённых; ни ожидающая, ни отклонённая позиция не продаётся (касса, прямой
-- select, create_order, все функции наличия, admin_set_menu_item_available); повторное использование
-- названия и кода отклонённой позиции и уникальность среди действующих; журнал решений через
-- admin_menu_reviews; серверный расчёт суммы заказа; неизменность исторических заказов.
-- Повторный запуск самой миграции здесь не проверяется — это шаг 9.4 плана (снимки до и после).
-- =====================================================================

begin;

-- ---------- 0. Состояние после миграции (от имени postgres) ----------

do $$
begin
  assert (select count(*) from pg_attribute where attrelid = 'public.menu_items'::regclass
           and attname in ('approved_at', 'rejected_at') and not attisdropped
           and format_type(atttypid, atttypmod) = 'timestamp with time zone') = 2,
    'FAIL: нет столбцов approved_at / rejected_at типа timestamptz';
  assert (select column_default from information_schema.columns where table_schema = 'public'
           and table_name = 'menu_items' and column_name = 'approved_at') = 'now()',
    'FAIL: у approved_at нет значения по умолчанию now()';
  assert (select count(*) from pg_constraint where conrelid = 'public.menu_items'::regclass and contype = 'c'
           and conname in ('menu_items_approved_before_sale', 'menu_items_single_decision')) = 2,
    'FAIL: нет ограничений состояний позиции';
  assert not exists (select 1 from pg_constraint where conrelid = 'public.menu_items'::regclass and contype = 'u'),
    'FAIL: осталось ограничение UNIQUE на всю таблицу menu_items';
  assert (select count(*) from pg_index i where i.indrelid = 'public.menu_items'::regclass and i.indisunique
           and i.indexrelid in (to_regclass('public.menu_items_name_key'), to_regclass('public.menu_items_code_key'))
           and pg_get_expr(i.indpred, i.indrelid) ~ 'rejected_at IS NULL') = 2,
    'FAIL: menu_items_name_key / menu_items_code_key должны быть частичными уникальными индексами';
  assert not exists (select 1 from public.menu_items
                      where is_available and (approved_at is null or rejected_at is not null)),
    'FAIL: в продаже есть ожидающие или отклонённые позиции';
  -- Первоначальное подтверждение: позиции без записи в журнале (существовавшие до 09 и добавленные
  -- администратором) подтверждены, approved_at = created_at, не отклонены
  assert not exists (select 1 from public.menu_items m
                      where not exists (select 1 from public.menu_item_reviews r where r.item_id = m.id)
                        and (m.approved_at is distinct from m.created_at or m.rejected_at is not null)),
    'FAIL: позиции до 09 (или администратора) не подтверждены с approved_at = created_at';
  -- Журнал закрыт
  assert (select relrowsecurity from pg_class where oid = 'public.menu_item_reviews'::regclass),
    'FAIL: у menu_item_reviews выключен RLS';
  assert not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'menu_item_reviews'),
    'FAIL: у menu_item_reviews есть политики';
  assert not has_table_privilege('authenticated', 'public.menu_item_reviews', 'select')
     and not has_table_privilege('anon', 'public.menu_item_reviews', 'select')
     and not has_table_privilege('authenticated', 'public.menu_item_reviews', 'insert'),
    'FAIL: клиентам выданы права на menu_item_reviews';
  assert not has_table_privilege('authenticated', 'public.menu_items', 'update')
     and not has_table_privilege('authenticated', 'public.menu_items', 'insert')
     and not has_table_privilege('authenticated', 'public.menu_items', 'delete'),
    'FAIL: клиентам выданы права записи в menu_items';
  -- Функции
  assert (select bool_and(p.prosecdef and p.proconfig @> array['search_path=""'])
            from pg_proc p where p.pronamespace = 'public'::regnamespace
             and p.proname in ('station_menu', 'station_add_menu_item', 'station_set_menu_item_available',
                               'admin_approve_menu_item', 'admin_reject_menu_item', 'admin_menu_reviews',
                               'kitchen_set_menu_item_available', 'kitchen_menu',
                               'admin_add_menu_item', 'admin_update_menu_item')),
    'FAIL: RPC меню должны быть SECURITY DEFINER с пустым search_path';
  assert (select bool_and(not p.prosecdef and p.proconfig @> array['search_path=""'])
            from pg_proc p where p.pronamespace = 'public'::regnamespace
             and p.proname in ('_staff_station', '_menu_item_status')),
    'FAIL: внутренние функции 09 не должны быть SECURITY DEFINER';
  assert not (select bool_or(has_function_privilege('anon', p.oid, 'execute'))
                from pg_proc p where p.pronamespace = 'public'::regnamespace
                 and p.proname in ('station_menu', 'station_add_menu_item', 'station_set_menu_item_available',
                                   'admin_approve_menu_item', 'admin_reject_menu_item', 'admin_menu_reviews',
                                   'kitchen_set_menu_item_available', '_staff_station', '_menu_item_status')),
    'FAIL: anon может вызывать функции меню станций';
  assert not (select bool_or(has_function_privilege('authenticated', p.oid, 'execute'))
                from pg_proc p where p.pronamespace = 'public'::regnamespace
                 and p.proname in ('_staff_station', '_menu_item_status')),
    'FAIL: authenticated может вызывать внутренние функции 09';
  assert (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
           and proname in ('station_menu', 'station_add_menu_item', 'station_set_menu_item_available',
                           'admin_approve_menu_item', 'admin_reject_menu_item', 'admin_menu_reviews',
                           'kitchen_set_menu_item_available', 'kitchen_menu')) = 8,
    'FAIL: ожидается ровно по одной версии функций меню станций';
end;
$$;

-- ---------- Подготовка (от имени postgres) ----------

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true),
       set_config('test.kitchen_item',
         (select id::text from public.menu_items where is_available order by id limit 1), true),
       set_config('test.bar_item',
         (select id::text from public.menu_items where is_available order by id offset 1 limit 1), true),
       set_config('test.max_order_item', (select coalesce(max(id), 0)::text from public.order_items), true),
       set_config('test.history_md5',
         (select md5(coalesce(string_agg(t::text, ',' order by t.id), '')) from public.order_items t), true),
       set_config('test.orders_md5',
         (select md5(coalesce(string_agg(t::text, ',' order by t.id), '')) from public.orders t), true),
       set_config('test.items_before', (select count(*)::text from public.menu_items), true);

do $$
begin
  if nullif(current_setting('test.admin_id'), '') is null or nullif(current_setting('test.cashier_id'), '') is null
     or nullif(current_setting('test.kitchen_item'), '') is null or nullif(current_setting('test.bar_item'), '') is null then
    raise exception 'FAIL: нужны активные admin и cashier и две доступные позиции меню';
  end if;
end;
$$;

update public.menu_items set station = 'kitchen' where id = current_setting('test.kitchen_item')::bigint;
update public.menu_items set station = 'bar' where id = current_setting('test.bar_item')::bigint;
select set_config('test.kitchen_code', (select code::text from public.menu_items
                                         where id = current_setting('test.kitchen_item')::bigint), true),
       set_config('test.kitchen_name', (select name from public.menu_items
                                         where id = current_setting('test.kitchen_item')::bigint), true);

-- ---------- 1. Кухня ----------

update public.profiles set role = 'kitchen' where id = current_setting('test.cashier_id')::uuid;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
declare
  m jsonb;
  v jsonb;
begin
  m := public.station_menu();
  assert exists (select 1 from jsonb_array_elements(m) e
                  where (e ->> 'id')::bigint = current_setting('test.kitchen_item')::bigint
                    and e ? 'price' and e ->> 'status' = 'on_sale'),
    'FAIL: кухня не видит свою позицию с ценой и статусом on_sale';
  assert not exists (select 1 from jsonb_array_elements(m) e where e ->> 'station' <> 'kitchen'),
    'FAIL: station_menu показывает кухне позиции другой станции';

  -- kitchen_menu (08 = 09): только кухня
  m := public.kitchen_menu();
  assert exists (select 1 from jsonb_array_elements(m) e
                  where (e ->> 'id')::bigint = current_setting('test.kitchen_item')::bigint),
    'FAIL: kitchen_menu не показывает кухонную позицию';
  assert not exists (select 1 from jsonb_array_elements(m) e
                      where (e ->> 'id')::bigint = current_setting('test.bar_item')::bigint),
    'FAIL: kitchen_menu показывает кухне барную позицию';

  v := public.station_add_menu_item('Тест-станция суп', 'Тест', 450.50);
  assert v ->> 'station' = 'kitchen' and v ->> 'status' = 'pending' and not (v ->> 'is_available')::boolean,
    'FAIL: новая позиция кухни должна быть кухонной и ожидать решения';
  perform set_config('test.new_kitchen', v ->> 'id', true);
  perform set_config('test.new_kitchen_code', v ->> 'code', true);

  v := public.station_add_menu_item('Тест-станция отклоняемое', 'Тест', 300, 99999001);
  perform set_config('test.to_reject', v ->> 'id', true);

  -- В продаже <-> нет в наличии для своей подтверждённой позиции
  v := public.station_set_menu_item_available(current_setting('test.kitchen_item')::bigint, false);
  assert v ->> 'status' = 'out_of_stock', 'FAIL: кухня не сняла с продажи свою позицию';
  v := public.station_set_menu_item_available(current_setting('test.kitchen_item')::bigint, true);
  assert v ->> 'status' = 'on_sale', 'FAIL: кухня не вернула в продажу свою позицию';
end;
$$;

-- Ожидающая: ни продать, ни «снять» (решение — за администратором)
do $$
declare
  v_flag boolean;
begin
  foreach v_flag in array array[true, false] loop
    begin
      perform public.station_set_menu_item_available(current_setting('test.new_kitchen')::bigint, v_flag);
      raise exception 'FAIL: station_set_menu_item_available изменила ожидающую позицию (%)', v_flag;
    exception when others then
      if sqlerrm <> 'NOT_APPROVED' then raise; end if;
    end;
    begin
      perform public.kitchen_set_menu_item_available(current_setting('test.new_kitchen')::bigint, v_flag);
      raise exception 'FAIL: kitchen_set_menu_item_available изменила ожидающую позицию (%)', v_flag;
    exception when others then
      if sqlerrm <> 'NOT_APPROVED' then raise; end if;
    end;
  end loop;
end;
$$;

do $$
begin
  perform public.station_set_menu_item_available(current_setting('test.bar_item')::bigint, false);
  raise exception 'FAIL: кухня изменила барную позицию';
exception when others then
  if sqlerrm <> 'STATION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.kitchen_set_menu_item_available(current_setting('test.bar_item')::bigint, false);
  raise exception 'FAIL: kitchen_set_menu_item_available изменила барную позицию';
exception when others then
  if sqlerrm <> 'STATION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  update public.menu_items set is_available = true where id = current_setting('test.new_kitchen')::bigint;
  raise exception 'FAIL: кухня изменила menu_items напрямую';
exception when insufficient_privilege then null;
end;
$$;

do $$
begin
  perform 1 from public.menu_item_reviews;
  raise exception 'FAIL: кухня читает журнал напрямую';
exception when insufficient_privilege then null;
end;
$$;

do $$
begin
  perform public.station_add_menu_item('Тест-станция дубль кода', 'Тест', 100, current_setting('test.kitchen_code')::int);
  raise exception 'FAIL: принят занятый код';
exception when others then
  if sqlerrm <> 'CODE_TAKEN' then raise; end if;
end;
$$;

do $$
begin
  perform public.station_add_menu_item(upper(current_setting('test.kitchen_name')), 'Тест', 100);
  raise exception 'FAIL: принято занятое название';
exception when others then
  if sqlerrm <> 'NAME_TAKEN' then raise; end if;
end;
$$;

-- Ожидающая позиция тоже занимает название и код
do $$
begin
  perform public.station_add_menu_item('Тест-станция суп', 'Тест', 100);
  raise exception 'FAIL: принято название ожидающей позиции';
exception when others then
  if sqlerrm <> 'NAME_TAKEN' then raise; end if;
end;
$$;

do $$
declare
  v_price numeric;
begin
  foreach v_price in array array[0, -1, 1.239, 100000000]::numeric[] loop
    begin
      perform public.station_add_menu_item('Тест-станция цена ' || v_price, 'Тест', v_price);
      raise exception 'FAIL: принята некорректная цена %', v_price;
    exception when others then
      if sqlerrm <> 'INVALID_PRICE' then raise; end if;
    end;
  end loop;
end;
$$;

do $$
begin
  perform public.station_add_menu_item('Тест-станция код 0', 'Тест', 100, 0);
  raise exception 'FAIL: принят код 0';
exception when others then
  if sqlerrm <> 'INVALID_CODE' then raise; end if;
end;
$$;

-- Название, категория и цена существующих позиций и решения — только администратор
do $$
begin
  perform public.admin_update_menu_item(current_setting('test.kitchen_item')::bigint, 'X', 'Y', 1);
  raise exception 'FAIL: кухня изменила название и цену позиции';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_approve_menu_item(current_setting('test.new_kitchen')::bigint);
  raise exception 'FAIL: кухня подтвердила позицию';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_reject_menu_item(current_setting('test.to_reject')::bigint);
  raise exception 'FAIL: кухня отклонила позицию';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_menu_reviews();
  raise exception 'FAIL: кухня прочитала журнал решений';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

-- ---------- 2. Бар ----------

reset role;
update public.profiles set role = 'bar' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
declare
  m jsonb;
  v jsonb;
begin
  m := public.station_menu();
  assert exists (select 1 from jsonb_array_elements(m) e
                  where (e ->> 'id')::bigint = current_setting('test.bar_item')::bigint and e ? 'price'),
    'FAIL: бар не видит свою позицию с ценой';
  assert not exists (select 1 from jsonb_array_elements(m) e where e ->> 'station' <> 'bar'),
    'FAIL: бар видит позиции другой станции';

  v := public.station_add_menu_item('Тест-станция лимонад', 'Тест', 600);
  assert v ->> 'station' = 'bar' and v ->> 'status' = 'pending',
    'FAIL: новая позиция бара должна быть барной и ожидать решения';
  perform set_config('test.new_bar', v ->> 'id', true);
end;
$$;

do $$
begin
  perform public.station_set_menu_item_available(current_setting('test.kitchen_item')::bigint, false);
  raise exception 'FAIL: бар изменил кухонную позицию';
exception when others then
  if sqlerrm <> 'STATION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.kitchen_set_menu_item_available(current_setting('test.bar_item')::bigint, false);
  raise exception 'FAIL: бар вызвал функцию наличия кухни';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.kitchen_menu();
  raise exception 'FAIL: бар прочитал меню кухни';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

-- ---------- 3. Кассир: ожидающие позиции недоступны ----------

reset role;
update public.profiles set role = 'cashier' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
begin
  assert not exists (select 1 from public.menu_items
                      where id in (current_setting('test.new_kitchen')::bigint, current_setting('test.new_bar')::bigint,
                                   current_setting('test.to_reject')::bigint)),
    'FAIL: кассир видит ожидающие позиции';
end;
$$;

do $$
begin
  perform public.create_order(gen_random_uuid(), jsonb_build_array(jsonb_build_object(
    'menu_item_id', current_setting('test.new_kitchen')::bigint, 'quantity', 1, 'expected_price', '450.50')));
  raise exception 'FAIL: create_order принял ожидающую позицию';
exception when others then
  if sqlerrm <> 'MENU_CHANGED' then raise; end if;
end;
$$;

do $$
begin
  perform public.station_menu();
  raise exception 'FAIL: кассиру доступно меню станции';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.station_add_menu_item('Тест-станция кассир', 'Тест', 100);
  raise exception 'FAIL: кассир добавил позицию через функцию станции';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.station_set_menu_item_available(current_setting('test.kitchen_item')::bigint, false);
  raise exception 'FAIL: кассир изменил наличие через функцию станции';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

-- ---------- 4. Администратор: решения ----------

reset role;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
declare
  m jsonb;
begin
  m := public.station_menu();
  assert exists (select 1 from jsonb_array_elements(m) e where e ->> 'station' = 'kitchen')
     and exists (select 1 from jsonb_array_elements(m) e where e ->> 'station' = 'bar'),
    'FAIL: администратор должен видеть меню обеих станций';
  m := public.admin_menu_reviews('pending');
  assert (select count(*) from jsonb_array_elements(m) e
           where (e ->> 'id')::bigint in (current_setting('test.new_kitchen')::bigint,
                                          current_setting('test.new_bar')::bigint,
                                          current_setting('test.to_reject')::bigint)
             and e ->> 'submitted_by_name' is not null and e ->> 'status' = 'pending') = 3,
    'FAIL: журнал не показывает три ожидающие позиции с автором';
end;
$$;

-- Обычная функция наличия администратора не обходит решение (ограничение таблицы)
do $$
begin
  perform public.admin_set_menu_item_available(current_setting('test.new_kitchen')::bigint, true);
  raise exception 'FAIL: admin_set_menu_item_available выставила в продажу ожидающую позицию';
exception when check_violation then null;
end;
$$;

do $$
begin
  perform public.admin_reject_menu_item(current_setting('test.to_reject')::bigint, repeat('я', 201));
  raise exception 'FAIL: принята причина длиннее 200 символов';
exception when others then
  if sqlerrm <> 'INVALID_REQUEST' then raise; end if;
end;
$$;

do $$
declare
  v jsonb;
begin
  -- Ожидает -> в продаже
  v := public.admin_approve_menu_item(current_setting('test.new_kitchen')::bigint);
  assert v ->> 'status' = 'on_sale', 'FAIL: подтверждённая позиция кухни должна быть в продаже';
  -- Ожидает -> нет в наличии (подтверждена, но не продаётся)
  v := public.admin_approve_menu_item(current_setting('test.new_bar')::bigint, false);
  assert v ->> 'status' = 'out_of_stock', 'FAIL: позиция бара должна быть подтверждена, но не в продаже';
  -- Ожидает -> отклонена
  v := public.admin_reject_menu_item(current_setting('test.to_reject')::bigint, '  Нет в концепции меню  ');
  assert v ->> 'status' = 'rejected' and v ->> 'reject_reason' = 'Нет в концепции меню',
    'FAIL: позиция не отклонена или причина не сохранена';
end;
$$;

-- Запрещённые переходы
do $$
begin
  perform public.admin_approve_menu_item(current_setting('test.new_kitchen')::bigint);
  raise exception 'FAIL: повторное подтверждение';
exception when others then
  if sqlerrm <> 'ALREADY_APPROVED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_reject_menu_item(current_setting('test.new_kitchen')::bigint);
  raise exception 'FAIL: отклонена подтверждённая позиция';
exception when others then
  if sqlerrm <> 'ALREADY_APPROVED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_approve_menu_item(current_setting('test.to_reject')::bigint);
  raise exception 'FAIL: подтверждена отклонённая позиция';
exception when others then
  if sqlerrm <> 'ITEM_REJECTED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_reject_menu_item(current_setting('test.to_reject')::bigint);
  raise exception 'FAIL: повторное отклонение';
exception when others then
  if sqlerrm <> 'ITEM_REJECTED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_update_menu_item(current_setting('test.to_reject')::bigint, 'Переименовано', 'Тест', 1);
  raise exception 'FAIL: изменена отклонённая позиция';
exception when others then
  if sqlerrm <> 'ITEM_REJECTED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_set_menu_item_available(current_setting('test.to_reject')::bigint, true);
  raise exception 'FAIL: admin_set_menu_item_available выставила в продажу отклонённую позицию';
exception when check_violation then null;
end;
$$;

do $$
begin
  perform public.station_set_menu_item_available(current_setting('test.to_reject')::bigint, true);
  raise exception 'FAIL: station_set_menu_item_available выставила в продажу отклонённую позицию';
exception when others then
  if sqlerrm <> 'ITEM_REJECTED' then raise; end if;
end;
$$;

do $$
declare
  m jsonb;
begin
  m := public.admin_menu_reviews('rejected');
  assert exists (select 1 from jsonb_array_elements(m) e
                  where (e ->> 'id')::bigint = current_setting('test.to_reject')::bigint
                    and e ->> 'reject_reason' = 'Нет в концепции меню' and e ->> 'decided_by_name' is not null
                    and e -> 'submitted' ->> 'name' = 'Тест-станция отклоняемое'),
    'FAIL: журнал не показывает отклонение с причиной, решившим и снимком предложения';
end;
$$;

do $$
begin
  perform public.admin_menu_reviews('unknown');
  raise exception 'FAIL: принят неизвестный фильтр журнала';
exception when others then
  if sqlerrm <> 'INVALID_REQUEST' then raise; end if;
end;
$$;

-- ---------- 5. Станции после решений; повторное использование названия и кода ----------

reset role;
update public.profiles set role = 'kitchen' where id = current_setting('test.cashier_id')::uuid;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
begin
  perform public.kitchen_set_menu_item_available(current_setting('test.to_reject')::bigint, true);
  raise exception 'FAIL: kitchen_set_menu_item_available выставила в продажу отклонённую позицию';
exception when others then
  if sqlerrm <> 'ITEM_REJECTED' then raise; end if;
end;
$$;

do $$
declare
  v jsonb;
begin
  assert exists (select 1 from jsonb_array_elements(public.station_menu()) e
                  where (e ->> 'id')::bigint = current_setting('test.to_reject')::bigint
                    and e ->> 'status' = 'rejected' and e ->> 'reject_reason' = 'Нет в концепции меню'),
    'FAIL: станция не видит отклонение и его причину';
  -- Название и код отклонённой позиции свободны
  v := public.station_add_menu_item('Тест-станция отклоняемое', 'Тест', 350, 99999001);
  assert v ->> 'status' = 'pending' and (v ->> 'code')::int = 99999001,
    'FAIL: не удалось повторно использовать название и код отклонённой позиции';
  perform set_config('test.reused', v ->> 'id', true);
end;
$$;

-- ...но только один раз: новая позиция снова занимает их
do $$
begin
  perform public.station_add_menu_item('Тест-станция другая', 'Тест', 100, 99999001);
  raise exception 'FAIL: код занят действующей позицией, но принят';
exception when others then
  if sqlerrm <> 'CODE_TAKEN' then raise; end if;
end;
$$;

do $$
begin
  -- Точное название: сравнение регистра кириллицы зависит от локали базы (проверяется в 07)
  perform public.station_add_menu_item('Тест-станция отклоняемое', 'Тест', 100);
  raise exception 'FAIL: название занято действующей позицией, но принято';
exception when others then
  if sqlerrm <> 'NAME_TAKEN' then raise; end if;
end;
$$;

-- Бар возвращает в продажу свою подтверждённую позицию
reset role;
update public.profiles set role = 'bar' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
declare
  v jsonb;
begin
  v := public.station_set_menu_item_available(current_setting('test.new_bar')::bigint, true);
  assert v ->> 'status' = 'on_sale', 'FAIL: бар не вернул в продажу подтверждённую позицию';
end;
$$;

-- ---------- 6. Кассир: подтверждённые — в заказе, отклонённая — нет ----------

reset role;
update public.profiles set role = 'cashier' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
declare
  v jsonb;
begin
  assert exists (select 1 from public.menu_items where id = current_setting('test.new_kitchen')::bigint),
    'FAIL: кассир не видит подтверждённую позицию';
  assert not exists (select 1 from public.menu_items
                      where id in (current_setting('test.to_reject')::bigint, current_setting('test.reused')::bigint)),
    'FAIL: кассир видит отклонённую или ожидающую позицию';
  v := public.create_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.new_kitchen')::bigint, 'quantity', 2,
                            'expected_price', '450.50'),
         jsonb_build_object('menu_item_id', current_setting('test.new_bar')::bigint, 'quantity', 1,
                            'expected_price', '600.00')));
  assert (v ->> 'total')::numeric = 1501.00, 'FAIL: сумма заказа должна быть 2 × 450.50 + 600.00';
  perform set_config('test.order', v ->> 'order_id', true);
end;
$$;

do $$
begin
  perform public.create_order(gen_random_uuid(), jsonb_build_array(jsonb_build_object(
    'menu_item_id', current_setting('test.to_reject')::bigint, 'quantity', 1, 'expected_price', '300.00')));
  raise exception 'FAIL: create_order принял отклонённую позицию';
exception when others then
  if sqlerrm <> 'MENU_CHANGED' then raise; end if;
end;
$$;

-- Клиентская цена не влияет на сумму: неверная ожидаемая цена — отказ, а не другая сумма
do $$
begin
  perform public.create_order(gen_random_uuid(), jsonb_build_array(jsonb_build_object(
    'menu_item_id', current_setting('test.new_kitchen')::bigint, 'quantity', 1, 'expected_price', '1.00')));
  raise exception 'FAIL: create_order принял неверную цену';
exception when others then
  if sqlerrm <> 'MENU_CHANGED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_menu_reviews();
  raise exception 'FAIL: кассир прочитал журнал решений';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

-- ---------- 7. Итог (от имени postgres) ----------

reset role;
do $$
begin
  assert (select string_agg(station || ':' || unit_price::text || 'x' || quantity, ',' order by station)
            from public.order_items where order_id = current_setting('test.order')::bigint)
         = 'bar:600.00x1,kitchen:450.50x2',
    'FAIL: снимки цены и станции в позициях заказа не совпадают';
  assert (select count(*) from public.order_stations where order_id = current_setting('test.order')::bigint) = 2,
    'FAIL: у заказа должны быть части кухни и бара';
  -- Отклонённая позиция сохранена (аудит), рядом — новая с тем же кодом
  assert (select count(*) from public.menu_items where code = 99999001) = 2
     and (select count(*) from public.menu_items where code = 99999001 and rejected_at is null) = 1,
    'FAIL: отклонённая позиция удалена или код занят дважды среди действующих';
  assert (select decision = 'rejected' and decided_by = current_setting('test.admin_id')::uuid
                 and submitted_by = current_setting('test.cashier_id')::uuid
            from public.menu_item_reviews where item_id = current_setting('test.to_reject')::bigint),
    'FAIL: журнал отклонения не заполнен';
  assert (select decision = 'approved' and decided_by = current_setting('test.admin_id')::uuid
            from public.menu_item_reviews where item_id = current_setting('test.new_kitchen')::bigint),
    'FAIL: журнал подтверждения не заполнен';
  assert (select count(*) from public.menu_items)
         = current_setting('test.items_before')::int + 4,
    'FAIL: позиции меню удалены или созданы лишние';
  assert (select md5(coalesce(string_agg(t::text, ',' order by t.id), '')) from public.order_items t
           where t.id <= current_setting('test.max_order_item')::bigint) = current_setting('test.history_md5'),
    'FAIL: изменились позиции исторических заказов';
  assert (select md5(coalesce(string_agg(t::text, ',' order by t.id), '')) from public.orders t
           where t.id < current_setting('test.order')::bigint) = current_setting('test.orders_md5'),
    'FAIL: изменились исторические заказы';
  assert not exists (select 1 from public.menu_items
                      where is_available and (approved_at is null or rejected_at is not null)),
    'FAIL: в продаже оказалась ожидающая или отклонённая позиция';
end;
$$;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;

rollback;
