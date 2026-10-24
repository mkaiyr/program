-- =====================================================================
-- Проверка прав и переходов статусов после 05_kitchen_status.sql
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
--
-- НИЧЕГО НЕ СОХРАНЯЕТ: всё выполняется в одной транзакции, в конце rollback.
-- Нужны: один активный admin, один активный cashier, хотя бы одна позиция меню.
-- Роль kitchen проверяется на временно изменённом профиле кассира (тоже откатится).
--
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- Ошибка: выполнение остановится с текстом 'FAIL: ...' или с неожиданной ошибкой.
--
-- Актуально только ДО 08_bar_stations.sql: после неё кухня меняет статус своей
-- части заказа (set_station_status), а не общий статус. Проверка после 08 —
-- sql/tests/08_bar_stations_checks.sql.
-- =====================================================================

begin;

do $$
begin
  if to_regclass('public.order_stations') is not null then
    raise exception 'ПРОПУСК: уже применена 08_bar_stations.sql — запустите sql/tests/08_bar_stations_checks.sql';
  end if;
end;
$$;

-- ---------- Подготовка (от имени postgres) ----------

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true),
       set_config('test.menu_id', (select id::text from public.menu_items order by id limit 1), true);

do $$
begin
  if nullif(current_setting('test.admin_id'), '') is null
     or nullif(current_setting('test.cashier_id'), '') is null
     or nullif(current_setting('test.menu_id'), '') is null then
    raise exception 'FAIL: нужны активные admin и cashier и хотя бы одна позиция меню';
  end if;
end;
$$;

-- Заказ кассира и заказ администратора
with o as (
  insert into public.orders (cashier_id, client_request_id, total)
  values (current_setting('test.cashier_id')::uuid, gen_random_uuid(), 200) returning id
) select set_config('test.order_c', id::text, true) from o;
with o as (
  insert into public.orders (cashier_id, client_request_id, total)
  values (current_setting('test.admin_id')::uuid, gen_random_uuid(), 100) returning id
) select set_config('test.order_a', id::text, true) from o;
insert into public.order_items (order_id, menu_item_id, item_name, unit_price, quantity)
values (current_setting('test.order_c')::bigint, current_setting('test.menu_id')::bigint, 'Тест-блюдо', 100, 2),
       (current_setting('test.order_a')::bigint, current_setting('test.menu_id')::bigint, 'Тест-блюдо', 100, 1);

do $$
begin
  assert (select status from public.orders where id = current_setting('test.order_c')::bigint) = 'accepted',
    'FAIL: новый заказ должен получать статус accepted';
end;
$$;

-- ---------- 1. Кассир ----------

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
begin
  perform public.set_order_status(current_setting('test.order_c')::bigint, 'preparing');
  raise exception 'FAIL: кассир смог начать готовить';
exception when others then
  if sqlerrm <> 'TRANSITION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.order_a')::bigint, 'preparing');
  raise exception 'FAIL: кассир изменил чужой заказ';
exception when others then
  if sqlerrm <> 'ORDER_NOT_FOUND' then raise; end if;
end;
$$;

do $$
begin
  perform public.kitchen_orders();
  raise exception 'FAIL: кассиру доступен kitchen_orders';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  update public.orders set status = 'ready' where id = current_setting('test.order_c')::bigint;
  raise exception 'FAIL: кассир изменил заказ напрямую';
exception when insufficient_privilege then null;
end;
$$;

do $$
begin
  delete from public.orders where id = current_setting('test.order_c')::bigint;
  raise exception 'FAIL: кассир удалил заказ';
exception when insufficient_privilege then null;
end;
$$;

-- ---------- 2. Кухня (профиль кассира временно получает роль kitchen) ----------

reset role;
update public.profiles set role = 'kitchen' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
declare
  v jsonb;
  v_order jsonb;
begin
  assert (select count(*) from public.orders) = 0, 'FAIL: кухня читает таблицу orders напрямую';
  assert (select count(*) from public.order_items) = 0, 'FAIL: кухня читает order_items напрямую';
  assert (select count(*) from public.menu_items) = 0, 'FAIL: кухня видит меню с ценами';

  v := public.kitchen_orders();
  select e into v_order from jsonb_array_elements(v) e
   where (e ->> 'id')::bigint = current_setting('test.order_c')::bigint;
  assert v_order is not null, 'FAIL: заказ не виден на кухне';
  assert v_order -> 'items' = '[{"item_name": "Тест-блюдо", "quantity": 2}]'::jsonb,
    'FAIL: неверный состав заказа на кухне';
  assert not (v_order ? 'total' or v_order ? 'cashier_id' or v_order -> 'items' -> 0 ? 'unit_price'),
    'FAIL: кухня получает суммы, цены или кассира';
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.order_c')::bigint, 'ready');
  raise exception 'FAIL: переход accepted -> ready (пропуск preparing) разрешён';
exception when others then
  if sqlerrm <> 'INVALID_TRANSITION' then raise; end if;
end;
$$;

do $$
begin
  assert public.set_order_status(current_setting('test.order_c')::bigint, 'preparing') ->> 'status' = 'preparing',
    'FAIL: кухня не смогла начать готовить';
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.order_c')::bigint, 'preparing');
  raise exception 'FAIL: повторное «Начать готовить» разрешено';
exception when others then
  if sqlerrm <> 'INVALID_TRANSITION' then raise; end if;
end;
$$;

do $$
begin
  assert public.set_order_status(current_setting('test.order_c')::bigint, 'ready') ->> 'status' = 'ready',
    'FAIL: кухня не смогла отметить «Готов»';
  assert not exists (select 1 from jsonb_array_elements(public.kitchen_orders()) e
                      where (e ->> 'id')::bigint = current_setting('test.order_c')::bigint),
    'FAIL: готовый заказ остался в списке кухни';
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.order_c')::bigint, 'completed');
  raise exception 'FAIL: кухня выдала заказ';
exception when others then
  if sqlerrm <> 'TRANSITION_NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.create_order(gen_random_uuid(), jsonb_build_array(jsonb_build_object(
    'menu_item_id', current_setting('test.menu_id')::bigint, 'quantity', 1, 'expected_price', '1')));
  raise exception 'FAIL: кухня оформила продажу';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_update_menu_item(current_setting('test.menu_id')::bigint, 'X', 'Y', 1);
  raise exception 'FAIL: кухня изменила меню';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  update public.menu_items set price = 1 where id = current_setting('test.menu_id')::bigint;
  raise exception 'FAIL: кухня изменила цену напрямую';
exception when insufficient_privilege then null;
end;
$$;

do $$
begin
  update public.profiles set role = 'admin' where id = auth.uid();
  raise exception 'FAIL: кухня изменила свою роль';
exception when insufficient_privilege then null;
end;
$$;

-- Вход по коду работает для kitchen (вызов от anon), если у профиля есть код.
-- Код читаем от postgres: anon профили не видит.
reset role;
select set_config('test.cashier_code',
         coalesce((select employee_code from public.profiles where id = current_setting('test.cashier_id')::uuid), ''),
         true);
set local role anon;
do $$
begin
  if current_setting('test.cashier_code') <> '' then
    assert public.employee_login_email(current_setting('test.cashier_code')) is not null,
      'FAIL: сотрудник kitchen не может войти по коду';
  end if;
end;
$$;

-- Неактивная кухня теряет доступ
reset role;
update public.profiles set is_active = false where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;
do $$
begin
  perform public.kitchen_orders();
  raise exception 'FAIL: неактивный сотрудник кухни видит заказы';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

-- ---------- 3. Кассир выдаёт готовый заказ ----------

reset role;
update public.profiles set role = 'cashier', is_active = true where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
begin
  assert public.set_order_status(current_setting('test.order_c')::bigint, 'completed') ->> 'status' = 'completed',
    'FAIL: кассир не смог выдать свой готовый заказ';
end;
$$;

do $$
begin
  perform public.set_order_status(current_setting('test.order_c')::bigint, 'accepted');
  raise exception 'FAIL: переход назад разрешён';
exception when others then
  if sqlerrm <> 'INVALID_TRANSITION' then raise; end if;
end;
$$;

-- ---------- 4. Администратор: все переходы, любой заказ ----------

reset role;
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
begin
  perform public.kitchen_orders();
  perform public.set_order_status(current_setting('test.order_a')::bigint, 'preparing');
  perform public.set_order_status(current_setting('test.order_a')::bigint, 'ready');
  assert public.set_order_status(current_setting('test.order_a')::bigint, 'completed') ->> 'status' = 'completed',
    'FAIL: администратор не прошёл все переходы';
end;
$$;

reset role;
select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;

rollback;
