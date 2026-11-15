-- =====================================================================
-- Проверка 14_reports_categories.sql: отчёт за период, категории
-- Выполнять на ТЕСТОВОЙ базе. Ничего не сохраняет (rollback). Нужны активные admin и cashier.
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- =====================================================================

begin;

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true);
insert into public.menu_items (name, category, price, station) values
  ('ТЕСТ Отчёт А', 'ТЕСТ-кат', 3000, 'kitchen'), ('ТЕСТ Отчёт Б', 'ТЕСТ-кат', 1000, 'bar');
select set_config('test.a', (select id::text from public.menu_items where name = 'ТЕСТ Отчёт А'), true),
       set_config('test.b', (select id::text from public.menu_items where name = 'ТЕСТ Отчёт Б'), true),
       set_config('test.t0', now()::text, true);

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
select set_config('test.o1', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.a')::bigint, 'quantity', 1, 'expected_price', 3000),
         jsonb_build_object('menu_item_id', current_setting('test.b')::bigint, 'quantity', 1, 'expected_price', 1000)), 4400)::text, true),
       set_config('test.o2', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.a')::bigint, 'quantity', 2, 'expected_price', 3000)), 6600)::text, true);
-- Третий заказ аннулируем: в выручку он не входит
select set_config('test.o3', public.place_order(gen_random_uuid(), jsonb_build_array(
         jsonb_build_object('menu_item_id', current_setting('test.b')::bigint, 'quantity', 1, 'expected_price', 1000)), 1100)::text, true);
select public.void_order((current_setting('test.o3')::jsonb ->> 'order_id')::bigint, 'Тест отчёта');

-- Кассир отчёт не получает
do $$
begin
  begin perform public.admin_report(now() - interval '1 day', now() + interval '1 day'); raise exception 'FAIL: кассир получил отчёт';
  exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: %', sqlerrm; end if; end;
end;
$$;
reset role;

-- Меняем цену в меню: отчёт должен брать цену из заказов
update public.menu_items set price = 9999 where id = current_setting('test.a')::bigint;

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
declare r jsonb := public.admin_report(current_setting('test.t0')::timestamptz, now() + interval '1 minute');
begin
  -- заказы: 4000 + 6000 = 10000 -> надбавки 400 + 600 = 1000 -> выручка 11000
  if (r ->> 'orders_count')::int <> 2 or (r ->> 'subtotal_total')::numeric <> 10000
     or (r ->> 'service_total')::numeric <> 1000 or (r ->> 'revenue')::numeric <> 11000
     or (r ->> 'average_check')::numeric <> 5500 then
    raise exception 'FAIL: сводка неверна: %', r;
  end if;
  if (r ->> 'voided_count')::int <> 1 or (r ->> 'voided_total')::numeric <> 1100 then
    raise exception 'FAIL: аннулированные: %', r;
  end if;
  -- блюдо А: 3 шт., 9000 по цене из заказов (а не 3 × 9999 из меню)
  if not (r -> 'top_items' @> '[{"name": "ТЕСТ Отчёт А", "quantity": 3, "revenue": 9000}]'::jsonb) then
    raise exception 'FAIL: топ блюд / историческая цена: %', r -> 'top_items';
  end if;
  if not (r -> 'categories' @> '[{"category": "ТЕСТ-кат", "quantity": 4, "revenue": 10000}]'::jsonb) then
    raise exception 'FAIL: категории: %', r -> 'categories';
  end if;
  if jsonb_array_length(r -> 'daily') < 1 or jsonb_array_length(r -> 'cashiers') < 1 then
    raise exception 'FAIL: нет разбивки по дням или кассирам';
  end if;
  begin perform public.admin_report(now(), now() - interval '1 day'); raise exception 'FAIL: перевёрнутый период';
  exception when others then if sqlerrm <> 'INVALID_PERIOD' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_report(now() - interval '400 days', now()); raise exception 'FAIL: период больше года';
  exception when others then if sqlerrm <> 'INVALID_PERIOD' then raise exception 'FAIL: %', sqlerrm; end if; end;
  -- категории
  if (public.admin_rename_category('ТЕСТ-кат', 'ТЕСТ-кат2') ->> 'updated')::int <> 2 then raise exception 'FAIL: переименование'; end if;
  begin perform public.admin_rename_category('нет такой', 'Х'); raise exception 'FAIL: несуществующая категория';
  exception when others then if sqlerrm <> 'NOT_FOUND' then raise exception 'FAIL: %', sqlerrm; end if; end;
end;
$$;
reset role;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;
rollback;
