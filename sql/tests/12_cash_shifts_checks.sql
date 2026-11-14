-- =====================================================================
-- Проверка 12_cash_shifts.sql: смены, внесения, изъятия, закрытие, привязка заказов
-- Выполнять на ТЕСТОВОЙ базе. Ничего не сохраняет (rollback). Нужен активный cashier без открытой смены.
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- =====================================================================

begin;

select set_config('test.cashier_id',
         (select id::text from public.profiles p where role = 'cashier' and is_active
             and not exists (select 1 from public.cash_shifts s where s.cashier_id = p.id and s.closed_at is null)
           order by created_at limit 1), true);
insert into public.menu_items (name, category, price, station) values ('ТЕСТ Смена 1000', 'ТЕСТ', 1000, 'kitchen');
select set_config('test.item', (select id::text from public.menu_items where name = 'ТЕСТ Смена 1000'), true);

do $$
begin
  if has_table_privilege('authenticated', 'public.cash_shifts', 'select, insert, update, delete')
     or has_table_privilege('authenticated', 'public.cash_movements', 'select, insert, update, delete') then
    raise exception 'FAIL: у клиентов есть прямой доступ к таблицам смен';
  end if;
end;
$$;

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
declare s jsonb; oid jsonb;
begin
  if public.shift_current() is not null then raise exception 'FAIL: смена не должна быть открыта'; end if;
  begin perform public.shift_cash_move('deposit', 100); raise exception 'FAIL: операция без смены принята';
  exception when others then if sqlerrm <> 'NO_OPEN_SHIFT' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.shift_open(-1); raise exception 'FAIL: отрицательная сумма принята';
  exception when others then if sqlerrm <> 'INVALID_AMOUNT' then raise exception 'FAIL: %', sqlerrm; end if; end;

  s := public.shift_open(20000);
  if (s ->> 'expected_cash')::numeric <> 20000 then raise exception 'FAIL: ожидалось 20000, %', s; end if;
  begin perform public.shift_open(1); raise exception 'FAIL: вторая смена открыта';
  exception when others then if sqlerrm <> 'SHIFT_ALREADY_OPEN' then raise exception 'FAIL: %', sqlerrm; end if; end;

  -- продажи: заказ на 1000 -> 1100 с надбавкой попадает в смену
  oid := public.place_order(gen_random_uuid(), jsonb_build_array(
           jsonb_build_object('menu_item_id', current_setting('test.item')::bigint, 'quantity', 1, 'expected_price', 1000)), 1100);
  s := public.shift_cash_move('deposit', 5000, 'размен');
  s := public.shift_cash_move('withdrawal', 10000, 'инкассация');
  if (s ->> 'orders_count')::int <> 1 or (s ->> 'sales_total')::numeric <> 1100
     or (s ->> 'deposits_total')::numeric <> 5000 or (s ->> 'withdrawals_total')::numeric <> 10000
     or (s ->> 'expected_cash')::numeric <> 16100 then   -- 20000 + 1100 + 5000 − 10000
    raise exception 'FAIL: расчёт смены неверный: %', s;
  end if;
  begin perform public.shift_cash_move('withdrawal', 99999); raise exception 'FAIL: изъятие больше кассы принято';
  exception when others then if sqlerrm <> 'INSUFFICIENT_CASH' then raise exception 'FAIL: %', sqlerrm; end if; end;

  -- аннулированный заказ из выручки смены уходит
  perform public.void_order((oid ->> 'order_id')::bigint, 'Тест аннулирования');
  s := public.shift_current();
  if (s ->> 'orders_count')::int <> 0 or (s ->> 'sales_total')::numeric <> 0 or (s ->> 'expected_cash')::numeric <> 15000 then
    raise exception 'FAIL: аннулированный заказ остался в смене: %', s;
  end if;

  s := public.shift_close(14900);
  if (s ->> 'expected_cash')::numeric <> 15000 or (s ->> 'difference')::numeric <> -100 or s ->> 'closed_at' is null then
    raise exception 'FAIL: закрытие смены: %', s;
  end if;
  if public.shift_current() is not null then raise exception 'FAIL: закрытая смена всё ещё текущая'; end if;
  begin perform public.shift_close(1); raise exception 'FAIL: повторное закрытие принято';
  exception when others then if sqlerrm <> 'NO_OPEN_SHIFT' then raise exception 'FAIL: %', sqlerrm; end if; end;
  if jsonb_array_length(public.shift_history(10)) < 1 then raise exception 'FAIL: пустая история'; end if;
  -- закрытая смена хранит снимок
  if (public.shift_history(1) -> 0 ->> 'difference')::numeric <> -100 then raise exception 'FAIL: нет снимка разницы'; end if;
end;
$$;
reset role;

-- Кухня не работает со сменами
update public.profiles set role = 'kitchen' where id = current_setting('test.cashier_id')::uuid;
set local role authenticated;
do $$
begin
  begin perform public.shift_open(0); raise exception 'FAIL: кухня открыла смену';
  exception when others then if sqlerrm <> 'NOT_ALLOWED' then raise exception 'FAIL: %', sqlerrm; end if; end;
end;
$$;
reset role;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;
rollback;
