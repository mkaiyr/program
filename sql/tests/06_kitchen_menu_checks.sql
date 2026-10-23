-- =====================================================================
-- Проверка прав после 06_kitchen_menu.sql
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
--
-- НИЧЕГО НЕ СОХРАНЯЕТ: всё выполняется в одной транзакции, в конце rollback.
-- Нужны: один активный cashier и хотя бы одна доступная позиция меню.
-- Роль kitchen проверяется на временно изменённом профиле кассира (тоже откатится).
--
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- =====================================================================

begin;

-- ---------- Подготовка (от имени postgres) ----------

select set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true),
       set_config('test.menu_id',
         (select id::text from public.menu_items where is_available order by id limit 1), true);
select set_config('test.price', (select price::text from public.menu_items
                                  where id = nullif(current_setting('test.menu_id'), '')::bigint), true);

do $$
begin
  if nullif(current_setting('test.cashier_id'), '') is null or nullif(current_setting('test.menu_id'), '') is null then
    raise exception 'FAIL: нужны активный cashier и хотя бы одна доступная позиция меню';
  end if;
end;
$$;

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);

-- ---------- 1. Кассир не может пользоваться функциями кухни ----------

set local role authenticated;

do $$
begin
  perform public.kitchen_set_menu_item_available(current_setting('test.menu_id')::bigint, false);
  raise exception 'FAIL: кассир изменил наличие через функцию кухни';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

do $$
begin
  perform public.kitchen_menu();
  raise exception 'FAIL: кассиру доступно kitchen_menu';
exception when others then
  if sqlerrm <> 'NOT_ALLOWED' then raise; end if;
end;
$$;

-- ---------- 2. Кухня ----------

reset role;
update public.profiles set role = 'kitchen' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
declare
  v jsonb := public.kitchen_menu();
  v_item jsonb;
begin
  assert (select count(*) from public.menu_items) = 0, 'FAIL: кухня читает menu_items напрямую (с ценами)';
  select e into v_item from jsonb_array_elements(v) e
   where (e ->> 'id')::bigint = current_setting('test.menu_id')::bigint;
  assert v_item is not null, 'FAIL: блюдо не видно в kitchen_menu';
  assert not (v_item ? 'price'), 'FAIL: kitchen_menu отдаёт цену';
  assert (v_item ->> 'is_available')::boolean, 'FAIL: неверная доступность';

  v := public.kitchen_set_menu_item_available(current_setting('test.menu_id')::bigint, false);
  assert not (v ->> 'is_available')::boolean and not (v ? 'price'), 'FAIL: неверный ответ переключения';
end;
$$;

do $$
begin
  perform public.kitchen_set_menu_item_available(-1, true);
  raise exception 'FAIL: несуществующее блюдо';
exception when others then
  if sqlerrm <> 'NOT_FOUND' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_update_menu_item(current_setting('test.menu_id')::bigint, 'X', 'Y', 1);
  raise exception 'FAIL: кухня изменила название и цену';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  perform public.admin_set_menu_item_available(current_setting('test.menu_id')::bigint, true);
  raise exception 'FAIL: кухне доступна функция администратора';
exception when others then
  if sqlerrm <> 'ADMIN_REQUIRED' then raise; end if;
end;
$$;

do $$
begin
  update public.menu_items set price = 1, is_available = true where id = current_setting('test.menu_id')::bigint;
  raise exception 'FAIL: кухня изменила меню напрямую';
exception when insufficient_privilege then null;
end;
$$;

-- ---------- 3. Кассир: блюдо скрыто и не продаётся ----------

reset role;
update public.profiles set role = 'cashier' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;

do $$
begin
  assert not exists (select 1 from public.menu_items where id = current_setting('test.menu_id')::bigint),
    'FAIL: кассир видит блюдо «Нет в наличии»';
end;
$$;

do $$
begin
  perform public.create_order(gen_random_uuid(), jsonb_build_array(jsonb_build_object(
    'menu_item_id', current_setting('test.menu_id')::bigint, 'quantity', 1,
    'expected_price', current_setting('test.price'))));
  raise exception 'FAIL: продано блюдо «Нет в наличии»';
exception when others then
  if sqlerrm <> 'MENU_CHANGED' then raise; end if;
end;
$$;

-- Цена не изменилась
reset role;
do $$
begin
  assert (select price::text from public.menu_items where id = current_setting('test.menu_id')::bigint)
         = current_setting('test.price'), 'FAIL: цена изменилась';
end;
$$;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;

rollback;
