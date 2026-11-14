-- =====================================================================
-- Проверка 13_staff_management.sql: создание, изменение, пароль, защита администратора
-- Выполнять на ТЕСТОВОЙ базе (Supabase). Ничего не сохраняет (rollback). Нужен активный admin.
-- Успех: последняя строка вывода — 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ'.
-- =====================================================================

begin;

select set_config('test.admin_id',
         (select id::text from public.profiles where role = 'admin' and is_active order by created_at limit 1), true),
       set_config('test.cashier_id',
         (select id::text from public.profiles where role = 'cashier' and is_active order by created_at limit 1), true);

-- Не администратор: отказ
select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.cashier_id'), 'role', 'authenticated')::text, true);
set local role authenticated;
do $$
begin
  begin perform public.admin_staff_list(); raise exception 'FAIL: кассир получил список';
  exception when others then if sqlerrm <> 'ADMIN_REQUIRED' then raise exception 'FAIL: %', sqlerrm; end if; end;
end;
$$;
reset role;

select set_config('request.jwt.claims',
         json_build_object('sub', current_setting('test.admin_id'), 'role', 'authenticated')::text, true);
set local role authenticated;

do $$
declare r jsonb; v_id uuid; v_email text;
begin
  r := public.admin_create_staff('ТЕСТ Бармен', 'bar', '9876', 'password123');
  v_id := (r ->> 'id')::uuid;
  perform set_config('test.new_id', v_id::text, true);
  if not exists (select 1 from public.profiles where id = v_id and role = 'bar' and employee_code = '9876' and is_active) then
    raise exception 'FAIL: профиль не создан';
  end if;
  begin perform public.admin_create_staff('Дубль', 'bar', '9876', 'password123'); raise exception 'FAIL: дубль кода';
  exception when others then if sqlerrm <> 'EMPLOYEE_CODE_TAKEN' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_create_staff('Слабый', 'bar', '9877', '123'); raise exception 'FAIL: слабый пароль';
  exception when others then if sqlerrm <> 'WEAK_PASSWORD' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_create_staff('Роль', 'owner', '9878', 'password123'); raise exception 'FAIL: неизвестная роль';
  exception when others then if sqlerrm <> 'INVALID_ROLE' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_create_staff('Код', 'bar', '12', 'password123'); raise exception 'FAIL: короткий код';
  exception when others then if sqlerrm <> 'INVALID_EMPLOYEE_CODE' then raise exception 'FAIL: %', sqlerrm; end if; end;

  r := public.admin_update_staff(v_id, 'ТЕСТ Повар', 'kitchen', '9879', true);
  if (r ->> 'role') <> 'kitchen' or (r ->> 'employee_code') <> '9879' then raise exception 'FAIL: изменение %', r; end if;
  r := public.admin_update_staff(v_id, 'ТЕСТ Повар', 'kitchen', '9879', false);
  if (r ->> 'is_active')::boolean then raise exception 'FAIL: не отключён'; end if;
  perform public.admin_set_staff_password(v_id, 'newpassword9');
  if jsonb_array_length(public.admin_staff_list()) < 2 then raise exception 'FAIL: список'; end if;

  -- защита администратора
  begin perform public.admin_update_staff(current_setting('test.admin_id')::uuid, 'А', 'cashier', '1001', true);
    raise exception 'FAIL: администратор понизил себя';
  exception when others then if sqlerrm <> 'SELF_LOCKOUT' then raise exception 'FAIL: %', sqlerrm; end if; end;
  begin perform public.admin_update_staff(gen_random_uuid(), 'Нет', 'bar', '9880', true); raise exception 'FAIL: нет сотрудника';
  exception when others then if sqlerrm <> 'STAFF_NOT_FOUND' then raise exception 'FAIL: %', sqlerrm; end if; end;
end;
$$;
reset role;

-- Вход прежним способом: код -> email -> проверка пароля (bcrypt) в auth.users
update public.profiles set is_active = true where id = current_setting('test.new_id')::uuid;
do $$
declare v_email text;
begin
  v_email := public.employee_login_email('9879');
  if v_email is distinct from (select email from auth.users where id = current_setting('test.new_id')::uuid) then
    raise exception 'FAIL: employee_login_email не нашёл нового сотрудника';
  end if;
  if not exists (select 1 from auth.users where id = current_setting('test.new_id')::uuid
                  and encrypted_password = extensions.crypt('newpassword9', encrypted_password)) then
    raise exception 'FAIL: пароль не сохранён как bcrypt-хеш нового пароля';
  end if;
  if not exists (select 1 from auth.identities where user_id = current_setting('test.new_id')::uuid and provider = 'email') then
    raise exception 'FAIL: нет записи identities';
  end if;
end;
$$;

select 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' as result;
rollback;
