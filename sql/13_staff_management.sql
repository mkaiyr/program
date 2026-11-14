-- =====================================================================
-- Этап 15. Управление сотрудниками из приложения
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–12. Одна транзакция: при ошибке или остановке не меняется ничего.
-- Повторный запуск безопасен: данные не меняются, определения те же.
--
-- Вход остаётся прежним: код сотрудника -> employee_login_email -> Supabase Auth (email + пароль).
-- Второй системы входа нет. Функции только добавляют и меняют записи в той же паре
-- auth.users + profiles, которую раньше администратор создавал вручную в SQL Editor.
--
--   admin_staff_list()                                          список сотрудников
--   admin_create_staff(p_full_name, p_role, p_code, p_password) новая учётная запись + профиль
--   admin_update_staff(p_id, p_full_name, p_role, p_code, p_is_active)
--   admin_set_staff_password(p_id, p_password)
--
-- Email служебный: <uuid>@bistro.local (в приложении не показывается; письма не отправляются).
-- Пароли хранятся как bcrypt-хеш (extensions.crypt) — так же, как у GoTrue.
-- Удаления сотрудников нет: заказы и смены ссылаются на профиль; используется деактивация.
-- Защита: администратор не может отключить или понизить себя и нельзя остаться без активного администратора.
--
-- ВАЖНО: прямая запись в auth.users зависит от версии GoTrue в проекте Supabase. Функция написана по
-- схеме auth последних версий (identities.provider_id) и проверялась только чтением схемы в
-- документации — сначала запускать на ТЕСТОВОМ проекте (sql/tests/13_staff_checks.sql).
-- =====================================================================

begin;

do $$
declare
  v_missing text[] := '{}';
begin
  if to_regclass('public.cash_shifts') is null
  then v_missing := array_append(v_missing, 'cash_shifts (12)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_admin')
  then v_missing := array_append(v_missing, 'is_active_admin() (02)'); end if;
  if not exists (select 1 from pg_attribute where attrelid = to_regclass('public.profiles') and attname = 'employee_code' and not attisdropped)
  then v_missing := array_append(v_missing, 'profiles.employee_code (03)'); end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.profiles'::regclass and contype = 'c'
                  and pg_get_constraintdef(oid) ~ '''bar''::text')
  then v_missing := array_append(v_missing, 'profiles.role: bar (08)'); end if;
  if not exists (select 1 from pg_extension where extname = 'pgcrypto')
  then v_missing := array_append(v_missing, 'расширение pgcrypto (Supabase включает его по умолчанию)'); end if;
  if cardinality(v_missing) > 0 then
    raise exception 'ОСТАНОВЛЕНО: 13_staff_management.sql требует выполненных 01–12, не найдено: %',
      array_to_string(v_missing, ', ');
  end if;
end;
$$;

-- Общие проверки администратора
create or replace function public._staff_admin_only()
returns void
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
end;
$$;

revoke all on function public._staff_admin_only() from public, anon, authenticated;

create or replace function public._staff_check_fields(p_full_name text, p_role text, p_code text)
returns void
language plpgsql
immutable
set search_path = ''
as $$
begin
  if p_full_name is null or length(trim(p_full_name)) not between 1 and 100 then
    raise exception using errcode = '22023', message = 'INVALID_NAME';
  end if;
  if p_role is null or p_role not in ('admin', 'cashier', 'kitchen', 'bar') then
    raise exception using errcode = '22023', message = 'INVALID_ROLE';
  end if;
  if p_code is null or p_code !~ '^[0-9]{4,6}$' then
    raise exception using errcode = '22023', message = 'INVALID_EMPLOYEE_CODE';
  end if;
end;
$$;

revoke all on function public._staff_check_fields(text, text, text) from public, anon, authenticated;

-- ---------- Список ----------

create or replace function public.admin_staff_list()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  perform public._staff_admin_only();
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'id', p.id, 'full_name', p.full_name, 'role', p.role, 'employee_code', p.employee_code,
            'is_active', p.is_active, 'created_at', p.created_at)
            order by p.is_active desc, p.full_name, p.id), '[]'::jsonb)
            from public.profiles p);
end;
$$;

revoke all on function public.admin_staff_list() from public, anon;
grant execute on function public.admin_staff_list() to authenticated;

-- ---------- Создание ----------
-- Ошибки: ADMIN_REQUIRED, INVALID_NAME, INVALID_ROLE, INVALID_EMPLOYEE_CODE, WEAK_PASSWORD,
--   EMPLOYEE_CODE_TAKEN

create or replace function public.admin_create_staff(p_full_name text, p_role text, p_code text, p_password text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id    uuid := gen_random_uuid();
  v_email text;
  v_name  text := trim(coalesce(p_full_name, ''));
begin
  perform public._staff_admin_only();
  perform public._staff_check_fields(v_name, p_role, p_code);
  if p_password is null or length(p_password) < 8 or length(p_password) > 72 then
    raise exception using errcode = '22023', message = 'WEAK_PASSWORD';
  end if;
  if exists (select 1 from public.profiles p where p.employee_code = p_code) then
    raise exception using errcode = '23505', message = 'EMPLOYEE_CODE_TAKEN';
  end if;

  v_email := v_id::text || '@bistro.local';

  insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
                          raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
                          confirmation_token, recovery_token, email_change_token_new, email_change)
  values ('00000000-0000-0000-0000-000000000000', v_id, 'authenticated', 'authenticated', v_email,
          extensions.crypt(p_password, extensions.gen_salt('bf')), now(),
          '{"provider": "email", "providers": ["email"]}'::jsonb, '{}'::jsonb, now(), now(),
          '', '', '', '');

  insert into auth.identities (id, user_id, provider_id, identity_data, provider,
                               last_sign_in_at, created_at, updated_at)
  values (gen_random_uuid(), v_id, v_id::text,
          jsonb_build_object('sub', v_id::text, 'email', v_email, 'email_verified', true),
          'email', now(), now(), now());

  insert into public.profiles (id, full_name, role, employee_code, is_active)
  values (v_id, v_name, p_role, p_code, true);

  return jsonb_build_object('id', v_id, 'full_name', v_name, 'role', p_role,
                            'employee_code', p_code, 'is_active', true);
end;
$$;

revoke all on function public.admin_create_staff(text, text, text, text) from public, anon;
grant execute on function public.admin_create_staff(text, text, text, text) to authenticated;

-- ---------- Изменение ----------
-- Ошибки: ADMIN_REQUIRED, INVALID_NAME, INVALID_ROLE, INVALID_EMPLOYEE_CODE, EMPLOYEE_CODE_TAKEN,
--   STAFF_NOT_FOUND, SELF_LOCKOUT (отключить или понизить себя), LAST_ADMIN

create or replace function public.admin_update_staff(p_id uuid, p_full_name text, p_role text,
                                                     p_code text, p_is_active boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name   text := trim(coalesce(p_full_name, ''));
  v_old    record;
begin
  perform public._staff_admin_only();
  perform public._staff_check_fields(v_name, p_role, p_code);
  if p_is_active is null then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;

  select p.role, p.is_active into v_old from public.profiles p where p.id = p_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'STAFF_NOT_FOUND';
  end if;

  if p_id = auth.uid() and (not p_is_active or p_role <> 'admin') then
    raise exception using errcode = 'P0001', message = 'SELF_LOCKOUT';
  end if;
  -- Последний активный администратор остаётся администратором
  if v_old.role = 'admin' and v_old.is_active and (p_role <> 'admin' or not p_is_active)
     and not exists (select 1 from public.profiles p
                      where p.role = 'admin' and p.is_active and p.id <> p_id) then
    raise exception using errcode = 'P0001', message = 'LAST_ADMIN';
  end if;
  if exists (select 1 from public.profiles p where p.employee_code = p_code and p.id <> p_id) then
    raise exception using errcode = '23505', message = 'EMPLOYEE_CODE_TAKEN';
  end if;

  update public.profiles
     set full_name = v_name, role = p_role, employee_code = p_code, is_active = p_is_active
   where id = p_id;
  return jsonb_build_object('id', p_id, 'full_name', v_name, 'role', p_role,
                            'employee_code', p_code, 'is_active', p_is_active);
end;
$$;

revoke all on function public.admin_update_staff(uuid, text, text, text, boolean) from public, anon;
grant execute on function public.admin_update_staff(uuid, text, text, text, boolean) to authenticated;

-- ---------- Смена пароля ----------
-- Ошибки: ADMIN_REQUIRED, WEAK_PASSWORD, STAFF_NOT_FOUND

create or replace function public.admin_set_staff_password(p_id uuid, p_password text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform public._staff_admin_only();
  if p_password is null or length(p_password) < 8 or length(p_password) > 72 then
    raise exception using errcode = '22023', message = 'WEAK_PASSWORD';
  end if;
  if not exists (select 1 from public.profiles p where p.id = p_id) then
    raise exception using errcode = 'P0002', message = 'STAFF_NOT_FOUND';
  end if;
  update auth.users
     set encrypted_password = extensions.crypt(p_password, extensions.gen_salt('bf')), updated_at = now()
   where id = p_id;
  if not found then
    raise exception using errcode = 'P0002', message = 'STAFF_NOT_FOUND';
  end if;
  return jsonb_build_object('id', p_id, 'password_changed', true);
end;
$$;

revoke all on function public.admin_set_staff_password(uuid, text) from public, anon;
grant execute on function public.admin_set_staff_password(uuid, text) to authenticated;

notify pgrst, 'reload schema';

commit;
