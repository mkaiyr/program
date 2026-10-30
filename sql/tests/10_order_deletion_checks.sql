-- =====================================================================
-- Проверка 10_order_deletion.sql: удалённые заказы, восстановление, окончательное удаление, журнал
-- Выполнять целиком на ТЕСТОВОЙ базе (docs/MIGRATION_TEST_PLAN.md).
--
-- НИЧЕГО НЕ СОХРАНЯЕТ: всё выполняется в одной транзакции, в конце rollback.
-- (Номера последовательностей заказов при этом расходуются.)
-- Нужны: активные admin и cashier, хотя бы две доступные позиции меню.
-- Тестовые заказы создаются здесь же через create_order; существующие заказы не удаляются
-- и не меняются (проверяется сравнением снимков). Роль kitchen проверяется на временно
-- изменённом профиле кассира — всё откатится.
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- Проверки написаны через if … raise, а не assert: assert молча пропускается при
-- plpgsql.check_asserts = off.
-- =====================================================================

begin;

-- ---------- 0. Схема и права (от имени postgres) ----------

do $$
declare
  f text;
begin
  if (select count(*) from pg_attribute where attrelid = 'public.orders'::regclass
       and attname in ('deleted_at', 'deleted_by') and not attisdropped) <> 2 then
    raise exception 'FAIL: нет столбцов orders.deleted_at / deleted_by';
  end if;
  if not (select relrowsecurity from pg_class where oid = 'public.order_purge_log'::regclass) then
    raise exception 'FAIL: у order_purge_log выключен RLS';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'order_purge_log') then
    raise exception 'FAIL: у order_purge_log есть политики — журнал должен быть закрыт';
  end if;
  if has_table_privilege('authenticated', 'public.order_purge_log', 'select, insert, update, delete')
     or has_table_privilege('anon', 'public.order_purge_log', 'select, insert, update, delete') then
    raise exception 'FAIL: у клиентов есть права на order_purge_log';
  end if;
  if has_table_privilege('authenticated', 'public.orders', 'update, delete')
     or has_table_privilege('authenticated', 'public.order_items', 'delete') then
    raise exception 'FAIL: клиент может менять или удалять заказы напрямую';
  end if;
  foreach f in array array['delete_order(bigint)', 'admin_deleted_orders()', 'admin_restore_order(bigint)',
                           'admin_purge_order(bigint)', 'admin_purge_log()'] loop
    if not exists (select 1 from pg_proc p where p.oid = to_regprocedure('public.' || f)
                    and p.prosecdef and p.proconfig @> array['search_path=""']) then
      raise exception 'FAIL: % — нет, не SECURITY DEFINER или без search_path = ''''', f;
    end if;
    if has_function_privilege('anon', 'public.' || f, 'execute') then
      raise exception 'FAIL: % доступна anon', f;
    end if;
    if not has_function_privilege('authenticated', 'public.' || f, 'execute') then
      raise exception 'FAIL: % недоступна authenticated', f;
    end if;
  end loop;
end;
$$;

-- ---------- Подготовка (от имени postgres) ----------

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true),
       set_config('test.item1', (select id::text from public.menu_items where is_available order by id limit 1), true),
       set_config('test.item2', (select id::text from public.menu_items where is_available order by id offset 1 limit 1), true),
       -- снимки всех существующих заказов: проверка в конце, что их не затронуло ничего
       set_config('test.max_order', (select coalesce(max(id), 0)::text from public.orders), true),
       set_config('test.old_orders_md5',
         (select md5(coalesce(string_agg(t::text, ',' order by t.id), '')) from public.orders t), true),
       set_config('test.old_items_md5',
         (select md5(coalesce(string_agg(t::text, ',' order by t.id), '')) from public.order_items t), true),
       set_config('test.old_stations_md5',
         (select md5(coalesce(string_agg(t::text, ',' order by t.order_id, t.station), '')) from public.order_stations t), true);

do $$
begin
  if nullif(current_setting('test.admin_id'), '') is null or nullif(current_setting('test.cashier_id'), '') is null
     or nullif(current_setting('test.item1'), '') is null or nullif(current_setting('test.item2'), '') is null then
    raise exception 'FAIL: нужны активные admin и cashier и две доступные позиции меню';
  end if;
end;
$$;

-- Три тестовых заказа кассира: A (две позиции), B (одна), C (останется «Принят»)
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
select set_config('test.order_a', (public.create_order(gen_random_uuid(), (select jsonb_agg(jsonb_build_object(
         'menu_item_id', m.id, 'quantity', 2, 'expected_price', m.price)) from public.menu_items m
          where m.id in (current_setting('test.item1')::bigint, current_setting('test.item2')::bigint))) ->> 'order_id'), true),
       set_config('test.order_b', (public.create_order(gen_random_uuid(), (select jsonb_agg(jsonb_build_object(
         'menu_item_id', m.id, 'quantity', 1, 'expected_price', m.price)) from public.menu_items m
          where m.id = current_setting('test.item1')::bigint)) ->> 'order_id'), true),
       set_config('test.order_c', (public.create_order(gen_random_uuid(), (select jsonb_agg(jsonb_build_object(
         'menu_item_id', m.id, 'quantity', 1, 'expected_price', m.price)) from public.menu_items m
          where m.id = current_setting('test.item2')::bigint)) ->> 'order_id'), true);
reset role;

-- A и B выданы (как после «Готово» станций и выдачи кассиром), C — принят
update public.order_stations set status = 'ready'
 where order_id in (current_setting('test.order_a')::bigint, current_setting('test.order_b')::bigint);
update public.orders set status = 'completed'
 where id in (current_setting('test.order_a')::bigint, current_setting('test.order_b')::bigint);
select set_config('test.a_total', (select total::text from public.orders where id = current_setting('test.order_a')::bigint), true),
       set_config('test.a_items', (select count(*)::text from public.order_items where order_id = current_setting('test.order_a')::bigint), true),
       set_config('test.bc_md5', (select md5(string_agg(t::text, ',' order by t.id)) from public.orders t
                                   where t.id in (current_setting('test.order_b')::bigint, current_setting('test.order_c')::bigint)), true),
       -- экраны кухни и бара до удаления (C «Принят» — на экране, A и B выданы — нет)
       set_config('test.kitchen_screen_md5', md5(public._station_orders('kitchen')::text), true),
       set_config('test.bar_screen_md5', md5(public._station_orders('bar')::text), true);

-- ---------- 1. Кухня не удаляет заказы ----------

update public.profiles set role = 'kitchen' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;
do $$
begin
  begin
    perform public.delete_order(current_setting('test.order_a')::bigint);
    raise exception 'FAIL: кухня отправила заказ в удалённые';
  exception when others then
    if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
  end;
end;
$$;
reset role;
update public.profiles set role = 'cashier' where id = current_setting('test.cashier_id')::uuid;

-- ---------- 2. Кассир: удаление своего выданного заказа (сценарии 1, 2, 6, 9, 10) ----------

set local role authenticated;
do $$
declare
  v jsonb;
  a bigint := current_setting('test.order_a')::bigint;
begin
  -- невыданный заказ удалить нельзя
  begin
    perform public.delete_order(current_setting('test.order_c')::bigint);
    raise exception 'FAIL: удалён невыданный заказ';
  exception when others then
    if sqlerrm <> 'ORDER_NOT_COMPLETED' then raise; end if;
  end;
  -- несуществующий заказ
  begin
    perform public.delete_order(-1);
    raise exception 'FAIL: удалён несуществующий заказ';
  exception when others then
    if sqlerrm <> 'ORDER_NOT_FOUND' then raise; end if;
  end;

  if (select count(*) from public.orders where id = a) <> 1 then
    raise exception 'FAIL: кассир не видит свой выданный заказ до удаления';
  end if;
  v := public.delete_order(a);
  if v <> jsonb_build_object('order_id', a, 'deleted', true, 'changed', true) then
    raise exception 'FAIL: delete_order вернул %', v;
  end if;
  -- сценарий 10: повтор — без ошибки и без изменений
  v := public.delete_order(a);
  if (v ->> 'changed')::boolean then
    raise exception 'FAIL: повторное удаление что-то изменило: %', v;
  end if;
  -- сценарий 2: удалённый заказ и его позиции не видны в обычной истории
  if exists (select 1 from public.orders where id = a) or exists (select 1 from public.order_items where order_id = a) then
    raise exception 'FAIL: удалённый заказ виден кассиру в истории';
  end if;

  -- сценарий 6: кассиру недоступны раздел удалённых, восстановление и окончательное удаление
  begin
    perform public.admin_deleted_orders();
    raise exception 'FAIL: кассир открыл удалённые заказы';
  exception when others then
    if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
  end;
  begin
    perform public.admin_restore_order(a);
    raise exception 'FAIL: кассир восстановил заказ';
  exception when others then
    if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
  end;
  begin
    perform public.admin_purge_order(a);
    raise exception 'FAIL: кассир удалил заказ навсегда';
  exception when others then
    if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
  end;
  begin
    perform public.admin_purge_log();
    raise exception 'FAIL: кассир открыл журнал удалений';
  exception when others then
    if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
  end;
  begin
    perform 1 from public.order_purge_log;
    raise exception 'FAIL: кассир читает журнал напрямую';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.orders set deleted_at = null where id = a;
    raise exception 'FAIL: кассир изменил заказ напрямую';
  exception when insufficient_privilege then null;
  end;
end;
$$;
reset role;

-- сценарий 9: чужой заказ (временно принадлежит администратору) кассир не удалит
update public.orders set cashier_id = current_setting('test.admin_id')::uuid
 where id = current_setting('test.order_b')::bigint;
set local role authenticated;
do $$
begin
  begin
    perform public.delete_order(current_setting('test.order_b')::bigint);
    raise exception 'FAIL: кассир удалил чужой заказ';
  exception when others then
    if sqlerrm <> 'ORDER_NOT_FOUND' then raise; end if;
  end;
end;
$$;
reset role;
update public.orders set cashier_id = current_setting('test.cashier_id')::uuid
 where id = current_setting('test.order_b')::bigint;

do $$
begin
  if (select deleted_at from public.orders where id = current_setting('test.order_b')::bigint) is not null then
    raise exception 'FAIL: чужой заказ отмечен удалённым';
  end if;
  if (select deleted_by from public.orders where id = current_setting('test.order_a')::bigint)
     is distinct from current_setting('test.cashier_id')::uuid then
    raise exception 'FAIL: не записано, кто удалил заказ';
  end if;
  -- экраны кухни и бара не изменились: выданный заказ на них и так не показывается
  if md5(public._station_orders('kitchen')::text) <> current_setting('test.kitchen_screen_md5')
     or md5(public._station_orders('bar')::text) <> current_setting('test.bar_screen_md5') then
    raise exception 'FAIL: удаление выданного заказа изменило экраны кухни или бара';
  end if;
  -- невыданный заказ нельзя отметить удалённым даже напрямую
  begin
    update public.orders set deleted_at = now() where id = current_setting('test.order_c')::bigint;
    raise exception 'FAIL: невыданный заказ отмечен удалённым напрямую';
  exception when check_violation then null;
  end;
end;
$$;

-- ---------- 3. Администратор: раздел удалённых и восстановление (сценарии 3, 4, 5, 10) ----------

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare
  v    jsonb;
  row  jsonb;
  a    bigint := current_setting('test.order_a')::bigint;
begin
  -- в обычной истории администратора удалённого заказа тоже нет
  if exists (select 1 from public.orders where id = a) or exists (select 1 from public.order_items where order_id = a) then
    raise exception 'FAIL: удалённый заказ виден администратору в обычной истории';
  end if;
  -- сценарий 3: заказ в разделе «Удалённые заказы» с составом, суммой и кассиром
  select e into row from jsonb_array_elements(public.admin_deleted_orders()) e where (e ->> 'id')::bigint = a;
  if row is null then
    raise exception 'FAIL: удалённого заказа нет в admin_deleted_orders';
  end if;
  if (row ->> 'total')::numeric <> current_setting('test.a_total')::numeric
     or jsonb_array_length(row -> 'items') <> current_setting('test.a_items')::int
     or row ->> 'cashier_name' is null or row ->> 'deleted_by_name' is null or row ->> 'deleted_at' is null
     or row ->> 'created_at' is null then
    raise exception 'FAIL: неполная запись удалённого заказа: %', row;
  end if;
  if exists (select 1 from jsonb_array_elements(public.admin_deleted_orders()) e
              where (e ->> 'id')::bigint <> a and (e ->> 'id')::bigint > current_setting('test.max_order')::bigint) then
    raise exception 'FAIL: в удалённых оказались другие тестовые заказы';
  end if;

  -- окончательно удалить можно только удалённый: B не удалён
  begin
    perform public.admin_purge_order(current_setting('test.order_b')::bigint);
    raise exception 'FAIL: стёрт заказ, не отправленный в удалённые';
  exception when others then
    if sqlerrm <> 'ORDER_NOT_DELETED' then raise; end if;
  end;

  -- сценарий 4: восстановление; сценарий 10: повтор без ошибки
  v := public.admin_restore_order(a);
  if not (v ->> 'changed')::boolean then
    raise exception 'FAIL: заказ не восстановлен: %', v;
  end if;
  v := public.admin_restore_order(a);
  if (v ->> 'changed')::boolean then
    raise exception 'FAIL: повторное восстановление что-то изменило: %', v;
  end if;
  -- сценарий 5: заказ снова в обычной истории вместе с позициями
  if (select count(*) from public.order_items where order_id = a) <> current_setting('test.a_items')::int then
    raise exception 'FAIL: восстановленный заказ не виден администратору';
  end if;
  begin
    perform public.admin_restore_order(-1);
    raise exception 'FAIL: восстановлен несуществующий заказ';
  exception when others then
    if sqlerrm <> 'ORDER_NOT_FOUND' then raise; end if;
  end;
end;
$$;
reset role;

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  if (select count(*) from public.order_items where order_id = current_setting('test.order_a')::bigint)
     <> current_setting('test.a_items')::int then
    raise exception 'FAIL: восстановленный заказ не виден кассиру';
  end if;
  perform public.delete_order(current_setting('test.order_a')::bigint);  -- снова в удалённые для шага 4
end;
$$;
reset role;

-- ---------- 4. Администратор: окончательное удаление и журнал (сценарии 7, 8, 9, 10) ----------

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare
  v jsonb;
  a bigint := current_setting('test.order_a')::bigint;
begin
  v := public.admin_purge_order(a);
  if not (v ->> 'changed')::boolean or (v ->> 'items_deleted')::int <> current_setting('test.a_items')::int then
    raise exception 'FAIL: admin_purge_order вернул %', v;
  end if;
  -- сценарий 10: повтор — без ошибки, ничего не меняет
  v := public.admin_purge_order(a);
  if (v ->> 'changed')::boolean then
    raise exception 'FAIL: повторное окончательное удаление что-то изменило: %', v;
  end if;
  begin
    perform public.admin_purge_order(-1);
    raise exception 'FAIL: «стёрт» несуществующий заказ';
  exception when others then
    if sqlerrm <> 'ORDER_NOT_FOUND' then raise; end if;
  end;
  -- журнал виден администратору функцией
  if not exists (select 1 from jsonb_array_elements(public.admin_purge_log()) e
                  where (e ->> 'order_id')::bigint = a
                    and (e ->> 'total')::numeric = current_setting('test.a_total')::numeric) then
    raise exception 'FAIL: стёртого заказа нет в admin_purge_log';
  end if;
  begin
    perform 1 from public.order_purge_log;
    raise exception 'FAIL: администратор читает журнал в обход функции';
  exception when insufficient_privilege then null;
  end;
end;
$$;
reset role;

do $$
declare
  a bigint := current_setting('test.order_a')::bigint;
  l public.order_purge_log;
begin
  -- сценарий 8: заказа, позиций и частей станций нет в базе (проверка от postgres, без RLS)
  if exists (select 1 from public.orders where id = a) or exists (select 1 from public.order_items where order_id = a)
     or exists (select 1 from public.order_stations where order_id = a) then
    raise exception 'FAIL: после окончательного удаления остались данные заказа';
  end if;
  -- журнал: номер, сумма, кассир, кто отправил в удалённые и кто стёр, когда
  select * into l from public.order_purge_log where order_id = a;
  if l.id is null or l.total <> current_setting('test.a_total')::numeric
     or l.cashier_id <> current_setting('test.cashier_id')::uuid or l.cashier_name is null
     or l.deleted_by <> current_setting('test.cashier_id')::uuid
     or l.purged_by <> current_setting('test.admin_id')::uuid or l.purged_by_name is null or l.purged_at is null then
    raise exception 'FAIL: неполная запись журнала: %', to_jsonb(l);
  end if;
  -- журнал нельзя изменить или удалить даже владельцем таблицы
  begin
    update public.order_purge_log set total = 0 where order_id = a;
    raise exception 'FAIL: запись журнала изменена';
  exception when others then
    if sqlerrm <> 'PURGE_LOG_READONLY' then raise; end if;
  end;
  begin
    delete from public.order_purge_log where order_id = a;
    raise exception 'FAIL: запись журнала удалена';
  exception when others then
    if sqlerrm <> 'PURGE_LOG_READONLY' then raise; end if;
  end;
  begin
    truncate public.order_purge_log;
    raise exception 'FAIL: журнал очищен';
  exception when others then
    if sqlerrm <> 'PURGE_LOG_READONLY' then raise; end if;
  end;
  -- сценарий 9: другие заказы не затронуты — тестовые B и C и все заказы, существовавшие до проверки
  if (select md5(string_agg(t::text, ',' order by t.id)) from public.orders t
       where t.id in (current_setting('test.order_b')::bigint, current_setting('test.order_c')::bigint))
     <> current_setting('test.bc_md5') then
    raise exception 'FAIL: изменились другие тестовые заказы';
  end if;
  if (select md5(coalesce(string_agg(t::text, ',' order by t.id), '')) from public.orders t
       where t.id <= current_setting('test.max_order')::bigint) <> current_setting('test.old_orders_md5')
     or (select md5(coalesce(string_agg(t::text, ',' order by t.id), '')) from public.order_items t
          where t.order_id <= current_setting('test.max_order')::bigint) <> current_setting('test.old_items_md5')
     or (select md5(coalesce(string_agg(t::text, ',' order by t.order_id, t.station), '')) from public.order_stations t
          where t.order_id <= current_setting('test.max_order')::bigint) <> current_setting('test.old_stations_md5') then
    raise exception 'FAIL: изменились существующие заказы, их позиции или части станций';
  end if;
  if md5(public._station_orders('kitchen')::text) is null then
    raise exception 'FAIL: экран кухни не строится после удаления';
  end if;
end;
$$;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;

rollback;
