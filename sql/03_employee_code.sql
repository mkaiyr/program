-- =====================================================================
-- Этап 5. Вход по коду сотрудника
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01_schema.sql и 02_admin_menu.sql. Повторный запуск безопасен.
-- Существующие данные не удаляются: колонка добавляется пустой (NULL).
--
-- Код только находит учётную запись. Пароль по-прежнему проверяет Supabase Auth,
-- роль и статус после входа читаются из profiles под RLS, как раньше.
--
-- Известное ограничение: функция доступна anon, поэтому перебором кодов можно
-- узнать email активных сотрудников. Для новых сотрудников используйте служебные
-- email (например, 1002@bistro.local), а не личные.
-- =====================================================================

begin;

-- ---------- Код сотрудника: 4–6 цифр, уникальный ----------
-- Сотрудник без кода (NULL) войти по коду не сможет, пока код не назначен.
-- Ограничения добавляются отдельно: "add column if not exists" пропустил бы их,
-- если колонка уже существует.

alter table public.profiles add column if not exists employee_code text;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.profiles'::regclass
                    and conname = 'profiles_employee_code_format') then
    alter table public.profiles add constraint profiles_employee_code_format
      check (employee_code ~ '^[0-9]{4,6}$');
  end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.profiles'::regclass
                    and conname = 'profiles_employee_code_key') then
    alter table public.profiles add constraint profiles_employee_code_key
      unique (employee_code);
  end if;
end;
$$;

-- ---------- Код -> email для входа (вызывается до входа, роль anon) ----------
-- Возвращает email только активного сотрудника с допустимой ролью, иначе NULL.
-- Для неизвестного кода и неактивного сотрудника ответ одинаковый (NULL),
-- клиент в обоих случаях показывает «Неверный код или пароль».
-- Роль и пароль функция не раскрывает и прав не даёт.
-- SECURITY DEFINER нужен: у anon нет доступа к profiles и auth.users.

create or replace function public.employee_login_email(p_code text)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select u.email::text
    from public.profiles p
    join auth.users u on u.id = p.id
   where p_code ~ '^[0-9]{4,6}$'
     and p.employee_code = p_code
     and p.is_active
     and p.role in ('cashier', 'admin', 'kitchen', 'bar');
$$;

revoke all on function public.employee_login_email(text) from public;
grant execute on function public.employee_login_email(text) to anon, authenticated;

-- Чтобы PostgREST сразу увидел новую функцию (иначе возможна ошибка PGRST202)
notify pgrst, 'reload schema';

commit;

-- ---------- Если миграция упала на добавлении ограничения ----------
-- Найти дубли и неверный формат в уже существующей колонке:
-- select employee_code, count(*) from public.profiles
--  where employee_code is not null group by employee_code having count(*) > 1;
-- select id, employee_code from public.profiles
--  where employee_code is not null and employee_code !~ '^[0-9]{4,6}$';

-- ---------- Назначение кодов (выполнить отдельно, подставив свои email) ----------
-- update public.profiles set employee_code = '1001'
--  where id = (select id from auth.users where email = 'admin@example.com');
-- update public.profiles set employee_code = '1002'
--  where id = (select id from auth.users where email = 'cashier1@example.com');

-- ---------- Проверка ----------
-- select p.employee_code, p.full_name, p.role, p.is_active, u.email
--   from public.profiles p join auth.users u on u.id = p.id order by p.employee_code;
-- select conname, pg_get_constraintdef(oid) from pg_constraint
--  where conrelid = 'public.profiles'::regclass and conname like 'profiles_employee_code%';
-- select public.employee_login_email('1001');   -- ожидается email администратора
-- select public.employee_login_email('9999');   -- ожидается NULL
-- select has_function_privilege('anon', 'public.employee_login_email(text)', 'execute');  -- ожидается true
