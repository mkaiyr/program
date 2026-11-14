-- =====================================================================
-- Проверка 08_bar_stations.sql: роль bar, станции, независимое приготовление
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
--
-- НИЧЕГО НЕ СОХРАНЯЕТ: всё выполняется в одной транзакции, в конце rollback.
-- Нужны: активные admin и cashier, хотя бы две доступные позиции меню.
-- Роли kitchen и bar проверяются на временно изменённом профиле кассира,
-- одна позиция временно переводится в бар — всё откатится.
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
--
-- Проверяется: перенос старых заказов, метаданные функций (SECURITY DEFINER,
-- search_path, владелец, права), смешанный заказ «еда + напиток», повторная отправка,
-- права бармена и повара (включая прямые вызовы внутренних функций), неактивный
-- бармен, выдача кассиром, явная принудительная готовность администратора.
-- =====================================================================

begin;

-- ---------- 0. Состояние после миграции (от имени postgres, до тестовых данных) ----------

do $$
begin
  assert (select count(*) from public.orders o
           where not exists (select 1 from public.order_stations s where s.order_id = o.id)
             and exists (select 1 from public.order_items i where i.order_id = o.id)) = 0,
    'FAIL: у существующего заказа с позициями нет частей по станциям';
  assert not exists (select 1 from public.order_stations s join public.orders o on o.id = s.order_id
                      where (o.status = 'completed' and s.status <> 'ready')
                         or (o.status in ('accepted', 'preparing', 'ready') and o.status <> s.status
                             and not exists (select 1 from public.order_stations x
                                              where x.order_id = o.id and x.station <> s.station))),
    'FAIL: статус перенесённой части не совпадает со статусом старого заказа';
  assert not exists (select 1 from public.order_items i join public.menu_items m on m.id = i.menu_item_id
                      where i.item_code is distinct from m.code),
    'FAIL: снимок кода в позициях заказов не заполнен или не совпадает с кодом позиции';
  assert (select column_default from information_schema.columns where table_schema = 'public'
           and table_name = 'order_items' and column_name = 'station') is null,
    'FAIL: у order_items.station осталось значение по умолчанию';
  assert exists (select 1 from pg_constraint where conrelid = 'public.profiles'::regclass
                  and conname = 'profiles_role_check' and pg_get_constraintdef(oid) like '%bar%'),
    'FAIL: profiles_role_check не допускает роль bar';
end;
$$;

-- Нет старых перегрузок меню (02/07) и определений без кода (02/06): иначе PostgREST
-- не выберет функцию, а кухня и админ не увидят код. Исправляет повторный запуск 08.
do $$
begin
  assert to_regprocedure('public.admin_add_menu_item(text, text, numeric)') is null
     and to_regprocedure('public.admin_add_menu_item(text, text, numeric, integer)') is null
     and to_regprocedure('public.admin_update_menu_item(bigint, text, text, numeric)') is null,
    'FAIL: остались старые перегрузки admin_add_menu_item / admin_update_menu_item';
  assert (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
           and proname in ('admin_add_menu_item', 'admin_update_menu_item')) = 2,
    'FAIL: ожидается ровно по одной версии admin_add_menu_item и admin_update_menu_item';
  assert pg_get_functiondef('public.kitchen_menu()'::regprocedure) ~ '''code'', m\.code'
     and pg_get_functiondef('public.admin_set_menu_item_available(bigint, boolean)'::regprocedure) ~ '''code'', v_row\.code',
    'FAIL: kitchen_menu или admin_set_menu_item_available без кода позиции (запускалась старая 02 или 06?)';
end;
$$;

-- Метаданные функций: публичные RPC — SECURITY DEFINER с пустым search_path,
-- внутренние — без SECURITY DEFINER, закрыты для клиентов; владелец — владелец таблиц
do $$
declare
  v_table_owner name := (select tableowner from pg_tables where schemaname = 'public' and tablename = 'order_stations');
begin
  assert (select bool_and(p.prosecdef and p.proconfig @> array['search_path=""'])
            from pg_proc p where p.pronamespace = 'public'::regnamespace
             and p.proname in ('create_order', 'kitchen_orders', 'bar_orders', 'set_station_status',
                               'set_order_status', 'admin_force_order_ready', 'is_active_bar',
                               'employee_login_email', 'admin_add_menu_item', 'admin_update_menu_item')),
    'FAIL: RPC должны быть SECURITY DEFINER с пустым search_path';
  assert (select bool_and(not p.prosecdef and p.proconfig @> array['search_path=""'])
            from pg_proc p where p.pronamespace = 'public'::regnamespace
             and p.proname in ('_station_orders', '_refresh_order_status')),
    'FAIL: внутренние функции не должны быть SECURITY DEFINER';
  assert (select bool_and(pg_get_userbyid(p.proowner) = v_table_owner)
            from pg_proc p where p.pronamespace = 'public'::regnamespace
             and p.proname in ('create_order', 'kitchen_orders', 'bar_orders', 'set_station_status',
                               'set_order_status', 'admin_force_order_ready', '_station_orders',
                               '_refresh_order_status')),
    'FAIL: владелец функций отличается от владельца таблиц (RLS order_stations может мешать)';
  assert not (select bool_or(has_function_privilege('anon', p.oid, 'execute'))
                from pg_proc p where p.pronamespace = 'public'::regnamespace
                 and p.proname in ('kitchen_orders', 'bar_orders', 'set_station_status', 'set_order_status',
                                   'admin_force_order_ready', 'create_order', '_station_orders',
                                   '_refresh_order_status')),
    'FAIL: anon может вызывать функции заказов';
  assert not (select bool_or(has_function_privilege('authenticated', p.oid, 'execute'))
                from pg_proc p where p.pronamespace = 'public'::regnamespace
                 and p.proname in ('_station_orders', '_refresh_order_status')),
    'FAIL: authenticated может вызывать внутренние функции станций';
  assert (select count(*) from pg_proc p where p.pronamespace = 'public'::regnamespace
           and p.proname in ('admin_add_menu_item', 'admin_update_menu_item')) = 2,
    'FAIL: остались старые версии функций меню';
  assert not has_table_privilege('authenticated', 'public.order_stations', 'select')
     and not has_table_privilege('anon', 'public.order_stations', 'select'),
    'FAIL: клиентам выданы права на order_stations';
end;
$$;

-- ---------- Подготовка (от имени postgres) ----------

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true),
       set_config('test.food_id', (select id::text from public.menu_items where is_available order by id limit 1), true),
       set_config('test.drink_id',
         (select id::text from public.menu_items where is_available order by id offset 1 limit 1), true);

do $$
begin
  if nullif(current_setting('test.admin_id'), '') is null or nullif(current_setting('test.cashier_id'), '') is null
     or nullif(current_setting('test.food_id'), '') is null or nullif(current_setting('test.drink_id'), '') is null then
    raise exception 'FAIL: нужны активные admin и cashier и две доступные позиции меню';
  end if;
end;
$$;

update public.menu_items set station = 'kitchen' where id = current_setting('test.food_id')::bigint;
update public.menu_items set station = 'bar' where id = current_setting('test.drink_id')::bigint;
select set_config('test.food_price', (select price::text from public.menu_items
                                       where id = current_setting('test.food_id')::bigint), true),
       set_config('test.drink_price', (select price::text from public.menu_items
                                        where id = current_setting('test.drink_id')::bigint), true);

-- ---------- 1. Кассир оформляет три заказа ----------

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
declare
  food  jsonb := jsonb_build_object('menu_item_id', current_setting('test.food_id')::bigint, 'quantity', 2,
                                    'expected_price', current_setting('test.food_price'));
  drink jsonb := jsonb_build_object('menu_item_id', current_setting('test.drink_id')::bigint, 'quantity', 1,
                                    'expected_price', current_setting('test.drink_price'));
begin
  perform set_config('test.mixed_request', gen_random_uuid()::text, true);
  perform set_config('test.mixed', public.create_order(current_setting('test.mixed_request')::uuid,
                                                       jsonb_build_array(food, drink)) ->> 'order_id', true);
  perform set_config('test.food_only', public.create_order(gen_random_uuid(), jsonb_build_array(food))
                                         ->> 'order_id', true);
  perform set_config('test.drink_only', public.create_order(gen_random_uuid(), jsonb_build_array(drink))
                                          ->> 'order_id', true);
end;
$$;

-- Повторная отправка того же смешанного заказа: тот же заказ, без новых позиций и частей
do $$
declare
  v jsonb;
begin
  v := public.create_order(current_setting('test.mixed_request')::uuid, jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.food_id')::bigint, 'quantity', 2,
                            'expected_price', current_setting('test.food_price')),
         jsonb_build_object('menu_item_id', current_setting('test.drink_id')::bigint, 'quantity', 1,
                            'expected_price', current_setting('test.drink_price'))));
  assert (v ->> 'duplicate')::boolean and v ->> 'order_id' = current_setting('test.mixed'),
    'FAIL: повторная отправка создала новый заказ';
end;
$$;

do $$
begin
  perform public.admin_force_order_ready(current_setting('test.mixed')::bigint);
  raise exception 'FAIL: кассир принудительно завершил приготовление';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.mixed')::bigint, 'ready');
  raise exception 'FAIL: кассир выставил «Готов» в обход станций';
exception when others then
  if sqlerrm <> 'TRANSITION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_station_status(current_setting('test.mixed')::bigint, 'kitchen', 'preparing');
  raise exception 'FAIL: кассир изменил статус станции';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.bar_orders();
  raise exception 'FAIL: кассиру доступен bar_orders';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform 1 from public.order_stations;
  raise exception 'FAIL: клиент читает order_stations';
exception when insufficient_privilege then null;
end;
$$;

-- Снимок станций и части заказа (от имени postgres)
reset role;
do $$
begin
  assert (select station from public.order_items where order_id = current_setting('test.mixed')::bigint
           and menu_item_id = current_setting('test.drink_id')::bigint) = 'bar', 'FAIL: напиток не помечен баром';
  assert (select string_agg(station || ':' || status, ',' order by station) from public.order_stations
           where order_id = current_setting('test.mixed')::bigint) = 'bar:accepted,kitchen:accepted',
    'FAIL: у смешанного заказа должны быть части bar и kitchen';
  assert (select string_agg(station, ',') from public.order_stations
           where order_id = current_setting('test.food_only')::bigint) = 'kitchen', 'FAIL: кухонный заказ';
  assert (select string_agg(station, ',') from public.order_stations
           where order_id = current_setting('test.drink_only')::bigint) = 'bar', 'FAIL: барный заказ';
  assert (select count(*) from public.order_items where order_id = current_setting('test.mixed')::bigint) = 2
     and (select count(*) from public.order_stations where order_id = current_setting('test.mixed')::bigint) = 2,
    'FAIL: повторная отправка добавила позиции или части станций';
  assert not exists (select 1 from public.order_items i join public.menu_items m on m.id = i.menu_item_id
                      where i.order_id = current_setting('test.mixed')::bigint and i.item_code <> m.code),
    'FAIL: снимок кода в новом заказе не равен коду позиции';
end;
$$;

-- ---------- 2. Бармен ----------

update public.profiles set role = 'bar' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
declare
  v jsonb := public.bar_orders();
  v_mixed jsonb;
begin
  assert (select count(*) from public.orders) = 0, 'FAIL: бармен читает orders (суммы)';
  assert (select count(*) from public.order_items) = 0, 'FAIL: бармен читает order_items (цены)';
  assert (select count(*) from public.menu_items) = 0, 'FAIL: бармен читает меню с ценами';
  assert not exists (select 1 from jsonb_array_elements(v) e
                      where (e ->> 'id')::bigint = current_setting('test.food_only')::bigint),
    'FAIL: бару виден кухонный заказ';
  assert exists (select 1 from jsonb_array_elements(v) e
                  where (e ->> 'id')::bigint = current_setting('test.drink_only')::bigint), 'FAIL: бару не виден напиток';
  select e into v_mixed from jsonb_array_elements(v) e
   where (e ->> 'id')::bigint = current_setting('test.mixed')::bigint;
  assert jsonb_array_length(v_mixed -> 'items') = 1, 'FAIL: бару видна кухонная позиция смешанного заказа';
  assert (v_mixed -> 'items' -> 0 ->> 'quantity')::int = 1 and (v_mixed -> 'items' -> 0 ? 'code'),
    'FAIL: позиция бара без количества или кода';
  assert not (v_mixed ? 'total' or v_mixed ? 'cashier_id' or v_mixed -> 'items' -> 0 ? 'unit_price'),
    'FAIL: бару видны суммы, цены или кассир';
end;
$$;

do $$
begin
  perform public._station_orders('kitchen');
  raise exception 'FAIL: бармен получил кухонные позиции прямым вызовом';
exception when insufficient_privilege then null;
end;
$$;

do $$
begin
  perform public._refresh_order_status(current_setting('test.mixed')::bigint);
  raise exception 'FAIL: бармен вызвал внутренний пересчёт статуса';
exception when insufficient_privilege then null;
end;
$$;

do $$
begin
  perform 1 from public.order_stations;
  raise exception 'FAIL: бармен читает order_stations';
exception when insufficient_privilege then null;
end;
$$;

do $$
begin
  perform public.admin_force_order_ready(current_setting('test.mixed')::bigint);
  raise exception 'FAIL: бармен принудительно завершил заказ';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_station_status(current_setting('test.mixed')::bigint, 'kitchen', 'preparing');
  raise exception 'FAIL: бармен изменил кухонную часть';
exception when others then
  if sqlerrm <> 'STATION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.mixed')::bigint, 'ready');
  raise exception 'FAIL: бармен изменил общий статус заказа';
exception when others then
  if sqlerrm <> 'TRANSITION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.create_order(gen_random_uuid(), jsonb_build_array(jsonb_build_object(
    'menu_item_id', current_setting('test.drink_id')::bigint, 'quantity', 1,
    'expected_price', current_setting('test.drink_price'))));
  raise exception 'FAIL: бармен оформил продажу';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.kitchen_orders();
  raise exception 'FAIL: бармену доступен kitchen_orders';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.kitchen_set_menu_item_available(current_setting('test.drink_id')::bigint, false);
  raise exception 'FAIL: бармену доступна функция кухни';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_update_menu_item(current_setting('test.drink_id')::bigint, 'X', 'Y', 1);
  raise exception 'FAIL: бармен изменил меню';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  update public.menu_items set price = 1 where id = current_setting('test.drink_id')::bigint;
  raise exception 'FAIL: бармен изменил цену напрямую';
exception when insufficient_privilege then null;
end;
$$;

-- Готовый напиток не делает готовым смешанный заказ
do $$
begin
  perform public.set_station_status(current_setting('test.mixed')::bigint, 'bar', 'ready');
  raise exception 'FAIL: пропуск шага «Готовится»';
exception when others then
  if sqlerrm <> 'INVALID_TRANSITION' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_station_status(current_setting('test.mixed')::bigint, 'bar', 'preparing');
  assert public.set_station_status(current_setting('test.mixed')::bigint, 'bar', 'ready') ->> 'order_status'
         = 'preparing', 'FAIL: смешанный заказ стал готовым, пока кухня не готова';
  perform public.set_station_status(current_setting('test.drink_only')::bigint, 'bar', 'preparing');
  assert public.set_station_status(current_setting('test.drink_only')::bigint, 'bar', 'ready') ->> 'order_status'
         = 'ready', 'FAIL: барный заказ не стал готовым';
  assert not exists (select 1 from jsonb_array_elements(public.bar_orders()) e
                      where (e ->> 'id')::bigint = current_setting('test.mixed')::bigint),
    'FAIL: готовая барная часть осталась в списке бара';
end;
$$;

-- Вход по коду для bar (от anon), если у профиля есть код
reset role;
select set_config('test.code',
         coalesce((select employee_code from public.profiles where id = current_setting('test.cashier_id')::uuid), ''),
         true);
set local role anon;
do $$
begin
  if current_setting('test.code') <> '' then
    assert public.employee_login_email(current_setting('test.code')) is not null, 'FAIL: бармен не входит по коду';
  end if;
end;
$$;

-- Неактивный бармен не входит и теряет доступ
reset role;
update public.profiles set is_active = false where id = current_setting('test.cashier_id')::uuid;
set local role anon;
do $$
begin
  if current_setting('test.code') <> '' then
    assert public.employee_login_email(current_setting('test.code')) is null, 'FAIL: неактивный бармен входит';
  end if;
end;
$$;
reset role;
set local role authenticated;
do $$
begin
  perform public.bar_orders();
  raise exception 'FAIL: неактивный бармен видит заказы';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

-- ---------- 3. Повар ----------

reset role;
update public.profiles set role = 'kitchen', is_active = true where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
declare
  v jsonb := public.kitchen_orders();
  v_mixed jsonb;
begin
  assert not exists (select 1 from jsonb_array_elements(v) e
                      where (e ->> 'id')::bigint = current_setting('test.drink_only')::bigint),
    'FAIL: кухне виден барный заказ';
  select e into v_mixed from jsonb_array_elements(v) e
   where (e ->> 'id')::bigint = current_setting('test.mixed')::bigint;
  assert jsonb_array_length(v_mixed -> 'items') = 1 and (v_mixed -> 'items' -> 0 ->> 'quantity')::int = 2,
    'FAIL: кухне видна барная позиция смешанного заказа';
  assert not (v_mixed ? 'total' or v_mixed ? 'cashier_id' or v_mixed -> 'items' -> 0 ? 'unit_price'),
    'FAIL: кухне видны суммы, цены или кассир';
end;
$$;

do $$
begin
  perform public.bar_orders();
  raise exception 'FAIL: кухне доступен bar_orders';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_station_status(current_setting('test.mixed')::bigint, 'bar', 'preparing');
  raise exception 'FAIL: кухня изменила барную часть';
exception when others then
  if sqlerrm <> 'STATION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.food_only')::bigint, 'preparing');
  raise exception 'FAIL: кухня изменила общий статус (обойдя станции)';
exception when others then
  if sqlerrm <> 'TRANSITION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  assert public.set_station_status(current_setting('test.mixed')::bigint, 'kitchen', 'preparing')
         ->> 'order_status' = 'preparing', 'FAIL: кухня не начала готовить';
  assert public.set_station_status(current_setting('test.mixed')::bigint, 'kitchen', 'ready')
         ->> 'order_status' = 'ready', 'FAIL: заказ не готов, когда готовы обе станции';
end;
$$;

-- ---------- 4. Кассир выдаёт; администратор ----------

reset role;
update public.profiles set role = 'cashier' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
begin
  assert public.set_order_status(current_setting('test.mixed')::bigint, 'completed') ->> 'status' = 'completed',
    'FAIL: кассир не выдал готовый заказ';
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.food_only')::bigint, 'completed');
  raise exception 'FAIL: выдан неготовый заказ';
exception when others then
  if sqlerrm <> 'INVALID_TRANSITION' then raise; end if;
end;
$$;

reset role;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

-- Обычный переход не выставляет «Готовится» / «Готов» в обход станций — даже администратору
do $$
begin
  perform public.set_order_status(current_setting('test.food_only')::bigint, 'preparing');
  raise exception 'FAIL: общий статус «Готовится» выставлен в обход станций';
exception when others then
  if sqlerrm <> 'TRANSITION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.food_only')::bigint, 'ready');
  raise exception 'FAIL: общий статус «Готов» выставлен в обход станций';
exception when others then
  if sqlerrm <> 'TRANSITION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.food_only')::bigint, 'completed');
  raise exception 'FAIL: выдан неготовый заказ';
exception when others then
  if sqlerrm <> 'INVALID_TRANSITION' then raise; end if;
end;
$$;

-- Явная принудительная готовность: смешанный заказ админа, кухня готовит, бар не начинал
do $$
declare
  v_order bigint;
  v jsonb;
begin
  perform public.bar_orders();
  v_order := (public.create_order(gen_random_uuid(), jsonb_build_array(
                jsonb_build_object('menu_item_id', current_setting('test.food_id')::bigint, 'quantity', 1,
                                   'expected_price', current_setting('test.food_price')),
                jsonb_build_object('menu_item_id', current_setting('test.drink_id')::bigint, 'quantity', 1,
                                   'expected_price', current_setting('test.drink_price')))) ->> 'order_id')::bigint;
  perform public.set_station_status(v_order, 'kitchen', 'preparing');  -- админ может любую станцию
  v := public.admin_force_order_ready(v_order);
  assert (v ->> 'forced')::boolean and v ->> 'status' = 'ready' and (v ->> 'stations_forced')::int = 2,
    'FAIL: принудительная готовность должна отметить обе станции';
  perform set_config('test.forced', v_order::text, true);
  assert public.set_order_status(v_order, 'completed') ->> 'status' = 'completed',
    'FAIL: администратор не выдал готовый заказ';
end;
$$;

do $$
begin
  perform public.admin_force_order_ready(current_setting('test.forced')::bigint);
  raise exception 'FAIL: повторная принудительная готовность выданного заказа';
exception when others then
  if sqlerrm <> 'INVALID_TRANSITION' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_force_order_ready(current_setting('test.food_only')::bigint);
  assert public.set_order_status(current_setting('test.food_only')::bigint, 'completed') ->> 'status'
         = 'completed', 'FAIL: администратор не выдал заказ после принудительной готовности';
end;
$$;

-- ---------- 5. Смена станции блюда не меняет оформленные заказы ----------

reset role;
update public.menu_items set station = 'kitchen' where id = current_setting('test.drink_id')::bigint;
do $$
begin
  assert (select station from public.order_items where order_id = current_setting('test.mixed')::bigint
           and menu_item_id = current_setting('test.drink_id')::bigint) = 'bar',
    'FAIL: станция в оформленном заказе изменилась';
  assert (select string_agg(status, ',' order by station) from public.order_stations
           where order_id = current_setting('test.forced')::bigint) = 'ready,ready',
    'FAIL: части станций не совпадают с принудительной готовностью';
  assert (select string_agg(status, ',') from public.order_stations
           where order_id = current_setting('test.food_only')::bigint) = 'ready',
    'FAIL: кухонная часть не отмечена при принудительной готовности';
end;
$$;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;

rollback;
