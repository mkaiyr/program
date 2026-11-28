-- =====================================================================
-- Проверки миграции 18 (единый каталог). НИЧЕГО НЕ СОХРАНЯЕТ: одна транзакция, в конце rollback.
-- Роли имитируются: claims (request.jwt.claims) + set local role anon/authenticated, как в Supabase.
-- Успех: последняя строка 'ВСЕ ПРОВЕРКИ МИГРАЦИИ 18 ПРОЙДЕНЫ'; ошибка: исключение 'FAIL: ...'.
-- =====================================================================
\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned
begin;

-- ---------- Помощники (временные, исчезают с транзакцией) ----------
create function pg_temp.try_as(p_role text, p_uid uuid, p_sql text) returns text language plpgsql as $$
declare v text; d text;
begin
  perform set_config('request.jwt.claims', case when p_uid is null then json_build_object('role', p_role)::text
                                                 else json_build_object('sub', p_uid::text, 'role', p_role)::text end, true);
  execute format('set local role %I', p_role);
  begin
    execute p_sql into v;
    execute 'reset role';
    return 'OK|' || coalesce(v, '');
  exception when others then
    get stacked diagnostics d = pg_exception_detail;
    execute 'reset role';
    return 'ERR|' || sqlstate || '|' || sqlerrm || '|' || coalesce(d, '');
  end;
end $$;

create temp table ctx (k text primary key, v text);
create function pg_temp.c(p_k text) returns text language sql as $$ select v from ctx where k = p_k $$;
create function pg_temp.u(p_k text) returns uuid language sql as $$ select v::uuid from ctx where k = p_k $$;
create function pg_temp.id(p_k text) returns bigint language sql as $$ select v::bigint from ctx where k = p_k $$;
create function pg_temp.adm(p_sql text) returns text language sql as $$ select pg_temp.try_as('authenticated', pg_temp.u('admin'), p_sql) $$;
create function pg_temp.anon(p_sql text) returns text language sql as $$ select pg_temp.try_as('anon', null, p_sql) $$;
create function pg_temp.ok(p_cond boolean, p_msg text) returns void language plpgsql as $$
begin if p_cond is not true then raise exception 'FAIL: %', p_msg; end if; end $$;
create function pg_temp.good(p_res text, p_msg text) returns jsonb language plpgsql as $$
begin
  if p_res not like 'OK|%' then raise exception 'FAIL: % — ожидался успех, получено %', p_msg, p_res; end if;
  return nullif(substr(p_res, 4), '')::jsonb;
end $$;
create function pg_temp.bad(p_res text, p_code text, p_msg text) returns void language plpgsql as $$
begin
  if p_res not like 'ERR|%' or split_part(p_res, '|', 3) <> p_code then raise exception 'FAIL: % — ожидалась ошибка %, получено %', p_msg, p_code, p_res; end if;
end $$;
create function pg_temp.denied(p_res text, p_msg text) returns void language plpgsql as $$
begin
  if p_res not like 'ERR|42501|%' then raise exception 'FAIL: % — ожидался отказ в доступе (42501), получено %', p_msg, p_res; end if;
end $$;
-- Массив блюд публичного меню, прочитанный под ролью anon
create function pg_temp.anon_json() returns jsonb language sql as $$
  select pg_temp.good(pg_temp.anon('select public.public_menu()::text'), 'public_menu под anon') -> 'dishes' $$;
create function pg_temp.reg(p_json text) returns bigint language plpgsql as $$
begin
  return (pg_temp.good(pg_temp.adm(format('select public.admin_register_media(%L::jsonb)::text', p_json)), 'регистрация медиа') ->> 'id')::bigint;
end $$;

-- ---------- Подготовка: пользователи и блюда (всё откатится) ----------
insert into auth.users (id, aud, role, email, encrypted_password, email_confirmed_at, created_at, updated_at, confirmation_token, recovery_token, email_change_token_new, email_change)
select x.id::uuid, 'authenticated', 'authenticated', x.email, 'x', now(), now(), now(), '', '', '', ''
  from (values ('00000000-0000-4000-8000-0000000000a1', 't-admin@bistro.local'), ('00000000-0000-4000-8000-0000000000c1', 't-cashier@bistro.local'),
               ('00000000-0000-4000-8000-0000000000d1', 't-kitchen@bistro.local'), ('00000000-0000-4000-8000-0000000000e1', 't-bar@bistro.local'),
               ('00000000-0000-4000-8000-0000000000f1', 't-inactive@bistro.local')) x(id, email);
insert into public.profiles (id, full_name, role, is_active, employee_code) values
  ('00000000-0000-4000-8000-0000000000a1', 'Т Админ', 'admin', true, '8001'), ('00000000-0000-4000-8000-0000000000c1', 'Т Кассир', 'cashier', true, '8002'),
  ('00000000-0000-4000-8000-0000000000d1', 'Т Кухня', 'kitchen', true, '8003'), ('00000000-0000-4000-8000-0000000000e1', 'Т Бар', 'bar', true, '8004'),
  ('00000000-0000-4000-8000-0000000000f1', 'Т Неактивный', 'admin', false, '8005');
insert into ctx values ('admin', '00000000-0000-4000-8000-0000000000a1'), ('cashier', '00000000-0000-4000-8000-0000000000c1'), ('kitchen', '00000000-0000-4000-8000-0000000000d1'),
                       ('bar', '00000000-0000-4000-8000-0000000000e1'), ('inactive', '00000000-0000-4000-8000-0000000000f1');
insert into public.menu_categories (name) values ('Т-Бургеры'), ('Т-Напитки'), ('Т-Пусто'), ('Т-БезСлага');
insert into ctx select case name when 'Т-БезСлага' then 'cat_noslug' when 'Т-Бургеры' then 'cat_burgers' when 'Т-Напитки' then 'cat_drinks' when 'Т-Пусто' then 'cat_empty' end, id::text
  from public.menu_categories where name like 'Т-%';
insert into public.menu_items (name, category, price, station, updated_at) values
  ('Т-Чизбургер', 'Т-Бургеры', 2290, 'kitchen', '2000-01-01'), ('Т-Кола', 'Т-Напитки', 1000, 'bar', now()), ('Т-Копейки', 'Т-Бургеры', 999.90, 'kitchen', now()),
  ('Т-БезСлага-блюдо', 'Т-БезСлага', 500, 'kitchen', now()), ('Т-Кола2', 'Т-Напитки', 1100, 'bar', now()), ('Т-Салат', 'Т-Бургеры', 1500, 'kitchen', now());
insert into public.menu_items (name, category, price, station, approved_at, is_available) values ('Т-НеОдобрено', 'Т-Бургеры', 700, 'kitchen', null, false);
insert into public.menu_items (name, category, price, station, approved_at, rejected_at, is_available) values ('Т-Отклонено', 'Т-Бургеры', 800, 'kitchen', null, now(), false);
insert into ctx select 'it_' || case name when 'Т-Чизбургер' then 'cheese' when 'Т-Кола' then 'cola' when 'Т-Копейки' then 'kop' when 'Т-БезСлага-блюдо' then 'noslug'
                                           when 'Т-Кола2' then 'cola2' when 'Т-Салат' then 'salad' when 'Т-НеОдобрено' then 'unapproved' when 'Т-Отклонено' then 'rejected' end, id::text
  from public.menu_items where name like 'Т-%';

-- =====================================================================
-- 1. Матрица прав по каталогу PostgreSQL
-- =====================================================================
do $t$
declare
  t text; v_anon text; v_pub text; v_n integer;
begin
  -- функции, доступные anon: ровно employee_login_email (существует) и public_menu (новая)
  select string_agg(p.proname, ',' order by p.proname) into v_anon from pg_proc p where p.pronamespace = 'public'::regnamespace and has_function_privilege('anon', p.oid, 'execute');
  perform pg_temp.ok(v_anon = 'employee_login_email,public_menu', 'anon выполняет только employee_login_email и public_menu, получено: ' || v_anon);
  -- ни одна функция проекта не доступна PUBLIC
  select string_agg(p.proname, ',') into v_pub from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
   where p.pronamespace = 'public'::regnamespace and a.grantee = 0;
  perform pg_temp.ok(v_pub is null, 'PUBLIC не имеет EXECUTE на функции проекта, найдено: ' || coalesce(v_pub, ''));
  -- authenticated: 46 прежних + 15 admin_* + public_menu
  select count(*) into v_n from pg_proc p where p.pronamespace = 'public'::regnamespace and has_function_privilege('authenticated', p.oid, 'execute');
  perform pg_temp.ok(v_n = 46 + 16, 'число функций, доступных authenticated, = 62, получено ' || v_n);
  -- внутренние функции недоступны клиентам
  select count(*) into v_n from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname in ('_touch_updated_at', '_slug_guard', '_slug_reserve', '_require_admin', '_json_text', '_json_int',
         '_json_bool', '_public_blockers', '_save_item_public', '_set_item_ingredients', '_confirm_price', '_publish', '_set_photo')
         and (has_function_privilege('anon', p.oid, 'execute') or has_function_privilege('authenticated', p.oid, 'execute'));
  perform pg_temp.ok(v_n = 0, 'внутренние функции недоступны anon и authenticated');
  -- таблицы: нет прав у anon и authenticated, RLS включён, политик нет
  foreach t in array array['media_assets', 'menu_category_public', 'menu_item_public', 'public_slug_reservations', 'allergens', 'ingredients', 'ingredient_allergens', 'menu_item_ingredients'] loop
    perform pg_temp.ok(not has_table_privilege('anon', 'public.' || t, 'select,insert,update,delete,truncate,references,trigger'), t || ': права anon');
    perform pg_temp.ok(not has_table_privilege('authenticated', 'public.' || t, 'select,insert,update,delete,truncate,references,trigger'), t || ': права authenticated');
    perform pg_temp.ok((select relrowsecurity from pg_class where oid = ('public.' || t)::regclass), t || ': RLS выключен');
    perform pg_temp.ok(not exists (select 1 from pg_policies where schemaname = 'public' and tablename = t), t || ': есть политики');
    perform pg_temp.denied(pg_temp.anon('select count(*)::text from public.' || t), 'anon читает ' || t);
    perform pg_temp.denied(pg_temp.adm('select count(*)::text from public.' || t), 'admin (authenticated) читает ' || t || ' напрямую');
    perform pg_temp.denied(pg_temp.adm('delete from public.' || t), 'admin (authenticated) пишет ' || t || ' напрямую');
  end loop;
  perform pg_temp.ok(not has_sequence_privilege('anon', 'public.media_assets_id_seq', 'usage,select,update') and not has_sequence_privilege('authenticated', 'public.media_assets_id_seq', 'usage,select,update'), 'права на последовательность');
  -- таблицы-расширения не появляются в публикации Realtime
  perform pg_temp.ok(not exists (select 1 from pg_publication_tables where tablename in ('media_assets', 'menu_item_public', 'menu_category_public')), 'новые таблицы в публикации Realtime');
  -- справочно: политика service_role (не отзывается, как в 01–16)
  raise notice 'service_role: EXECUTE на public_menu = %, на admin_catalog_diagnostics = % (политика 01–16, ключ только на сервере)',
    has_function_privilege('service_role', 'public.public_menu()', 'execute'), has_function_privilege('service_role', 'public.admin_catalog_diagnostics()', 'execute');
  raise notice 'ТЕСТ 1 (матрица прав по каталогу): OK';
end $t$;

-- =====================================================================
-- 2. Реальные вызовы под ролями: anon, не авторизован, кассир, кухня, бар, неактивный админ, админ
-- =====================================================================
do $t$
declare
  v_calls text[]; q text; r text; w text;
  v_cat text := pg_temp.c('cat_burgers'); v_it text := pg_temp.c('it_cheese');
begin
  v_calls := array[
    format($q$select public.admin_set_category_public(%s, '{"slug":"zz"}'::jsonb)::text$q$, v_cat), 'select public.admin_list_category_public()::text',
    format($q$select public.admin_set_menu_item_public(%s, '{"slug":"zz"}'::jsonb)::text$q$, v_it), format($q$select public.admin_set_menu_item_ingredients(%s, '[]'::jsonb)::text$q$, v_it),
    $q$select public.admin_save_allergen('zz', 'ZZ', 0)::text$q$, $q$select public.admin_save_ingredient('zz', 'ZZ', '{}'::jsonb)::text$q$,
    format('select public.admin_confirm_menu_price(%s)::text', v_it), format('select public.admin_publish_menu_item(%s, false)::text', v_it),
    format($q$select public.admin_save_dish_public(%s, '{"slug":"zz"}'::jsonb, false)::text$q$, v_it), $q$select public.admin_release_slug('dish', 'zz')::text$q$,
    $q$select public.admin_register_media('{}'::jsonb)::text$q$, format('select public.admin_set_dish_photo(%s, null, null)::text', v_it),
    'select public.admin_media_list(false)::text', 'select public.admin_delete_media(1)::text', 'select public.admin_catalog_diagnostics()::text'];
  perform pg_temp.ok(cardinality(v_calls) = 15, 'в списке проверяемых функций 15 вызовов');
  foreach q in array v_calls loop
    perform pg_temp.denied(pg_temp.anon(q), 'anon вызывает: ' || left(q, 60));
    perform pg_temp.bad(pg_temp.try_as('authenticated', null, q), 'NOT_AUTHENTICATED', 'без пользователя: ' || left(q, 60));
    foreach w in array array['cashier', 'kitchen', 'bar', 'inactive'] loop
      perform pg_temp.bad(pg_temp.try_as('authenticated', pg_temp.u(w), q), 'ADMIN_REQUIRED', w || ': ' || left(q, 60));
    end loop;
  end loop;
  -- внутренние функции недоступны даже администратору через API
  foreach q in array array['select count(*)::text from public._public_blockers()', $q$select public._publish(1, true)::text$q$, 'select public._require_admin()::text',
                           $q$select public._json_int('{}'::jsonb, 'a', 1, 2)::text$q$] loop
    perform pg_temp.denied(pg_temp.adm(q), 'admin вызывает внутреннюю: ' || left(q, 50));
    perform pg_temp.denied(pg_temp.anon(q), 'anon вызывает внутреннюю: ' || left(q, 50));
  end loop;
  -- public_menu доступна всем
  r := pg_temp.anon('select public.public_menu()::text');
  perform pg_temp.ok(pg_temp.good(r, 'anon public_menu') ->> 'schema' = '1', 'public_menu вернула schema = 1');
  perform pg_temp.good(pg_temp.try_as('authenticated', pg_temp.u('cashier'), 'select public.public_menu()::text'), 'cashier public_menu');
  raise notice 'ТЕСТ 2 (роли: anon / без пользователя / кассир / кухня / бар / неактивный админ / внутренние функции): OK';
end $t$;

-- =====================================================================
-- 3. Категории: слаги, стабильность, резервирование
-- =====================================================================
do $t$
declare
  c1 text := pg_temp.c('cat_burgers'); c2 text := pg_temp.c('cat_drinks'); c3 text := pg_temp.c('cat_empty'); r jsonb; slug_before text; v_new bigint;
begin
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"sort":1}'::jsonb)::text$q$, c1)), 'SLUG_REQUIRED', 'нет слага при создании');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"Бургеры"}'::jsonb)::text$q$, c1)), 'INVALID_SLUG', 'кириллица в слаге');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"Bad_Slug"}'::jsonb)::text$q$, c1)), 'INVALID_SLUG', 'заглавные и подчёркивание');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"a--b"}'::jsonb)::text$q$, c1)), 'INVALID_SLUG', 'двойной дефис');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"x","sort":"1"}'::jsonb)::text$q$, c1)), 'INVALID_DATA', 'порядок строкой');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_set_category_public(999999, '{"slug":"x"}'::jsonb)::text$q$), 'CATEGORY_NOT_FOUND', 'нет такой категории');
  r := pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"burgers","sort":1}'::jsonb)::text$q$, c1)), 'создание слага burgers');
  perform pg_temp.ok(r ->> 'slug' = 'burgers' and (r ->> 'sort')::int = 1 and (r ->> 'archived')::boolean = false, 'ответ создания слага категории');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"burgers"}'::jsonb)::text$q$, c2)), 'SLUG_TAKEN', 'дубль слага у другой категории');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"drinks","sort":2}'::jsonb)::text$q$, c2)), 'слаг drinks');
  -- неизменяемость
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"burgers-2"}'::jsonb)::text$q$, c1)), 'SLUG_IMMUTABLE', 'смена слага');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"burgers","sort":5}'::jsonb)::text$q$, c1)), 'тот же слаг и новый порядок');
  perform pg_temp.ok((select sort from public.menu_category_public where category_id = c1::bigint) = 5, 'порядок обновился');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"sort":1}'::jsonb)::text$q$, c1)), 'вернули порядок 1');
  -- переименование категории (существующая функция из 16) не меняет слаг
  select slug into slug_before from public.menu_category_public where category_id = c1::bigint;
  perform pg_temp.good(pg_temp.adm($q$select public.admin_rename_category('Т-Бургеры', 'Т-Бургеры и сэндвичи')::text$q$), 'переименование категории');
  perform pg_temp.ok((select slug from public.menu_category_public where category_id = c1::bigint) = slug_before, 'слаг после переименования неизменен');
  perform pg_temp.ok((select count(*) from public.menu_items where category = 'Т-Бургеры и сэндвичи') >= 3, 'блюда переехали в переименованную категорию');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_rename_category('Т-Бургеры и сэндвичи', 'Т-Бургеры')::text$q$), 'переименование обратно');
  -- архив категории
  r := pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"archived":true}'::jsonb)::text$q$, c2)), 'архив категории');
  perform pg_temp.ok((r ->> 'archived')::boolean, 'категория в архиве');
  r := pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"archived":false}'::jsonb)::text$q$, c2)), 'возврат из архива');
  perform pg_temp.ok(not (r ->> 'archived')::boolean, 'категория не в архиве');
  -- список
  r := pg_temp.good(pg_temp.adm('select public.admin_list_category_public()::text'), 'список категорий');
  perform pg_temp.ok(jsonb_array_length(r) >= 4, 'в списке категорий не меньше 4');
  perform pg_temp.ok((select (e ->> 'has_public_data')::boolean from jsonb_array_elements(r) e where e ->> 'name' = 'Т-БезСлага') = false, 'категория без слага помечена');
  -- резервирование: удалённая пустая категория оставляет зарезервированный слаг
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"empty"}'::jsonb)::text$q$, c3)), 'слаг empty');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_delete_category('Т-Пусто')::text$q$), 'удаление пустой категории (функция из 16)');
  perform pg_temp.ok(not exists (select 1 from public.menu_category_public where slug = 'empty'), 'строка слага ушла вместе с категорией');
  perform pg_temp.ok(exists (select 1 from public.public_slug_reservations where kind = 'category' and slug = 'empty'), 'слаг остался зарезервированным');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_add_category('Т-Пусто2')::text$q$), 'новая категория');
  select id into v_new from public.menu_categories where name = 'Т-Пусто2';
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"empty"}'::jsonb)::text$q$, v_new)), 'SLUG_RESERVED', 'повторное использование слага');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_release_slug('category', 'burgers')::text$q$), 'SLUG_IN_USE', 'освобождение слага, который используется');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_release_slug('menu', 'empty')::text$q$), 'INVALID_DATA', 'неверный вид слага');
  r := pg_temp.good(pg_temp.adm($q$select public.admin_release_slug('category', 'empty')::text$q$), 'освобождение');
  perform pg_temp.ok((r ->> 'released')::boolean, 'слаг освобождён');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"slug":"empty"}'::jsonb)::text$q$, v_new)), 'слаг после явного освобождения');
  r := pg_temp.good(pg_temp.adm($q$select public.admin_release_slug('category', 'never-used')::text$q$), 'освобождение неиспользованного');
  perform pg_temp.ok(not (r ->> 'released')::boolean, 'неиспользованный слаг: released = false');
  -- прямое изменение слага мимо функций тоже запрещено триггером
  begin
    update public.menu_category_public set slug = 'hacked' where category_id = c1::bigint;
    raise exception 'FAIL: прямая смена слага прошла';
  exception when others then
    perform pg_temp.ok(sqlerrm = 'SLUG_IMMUTABLE', 'прямая смена слага отклонена триггером: ' || sqlerrm);
  end;
  raise notice 'ТЕСТ 3 (категории, стабильность и резервирование слагов): OK';
end $t$;

-- =====================================================================
-- 4. Блюда: данные, подтверждение цены, публикация, диагностика
-- =====================================================================
do $t$
declare
  it text := pg_temp.c('it_cheese'); r jsonb; d jsonb; dishes jsonb; n integer;
begin
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', it)), 'PUBLIC_DATA_MISSING', 'публикация без публичных данных');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_confirm_menu_price(%s)::text', it)), 'PUBLIC_DATA_MISSING', 'подтверждение цены без публичных данных');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"description":"x"}'::jsonb)::text$q$, it)), 'SLUG_REQUIRED', 'создание без слага');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_set_menu_item_public(999999, '{"slug":"x"}'::jsonb)::text$q$), 'ITEM_NOT_FOUND', 'нет блюда');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"Чизбургер"}'::jsonb)::text$q$, it)), 'INVALID_SLUG', 'кириллица в слаге блюда');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"x","weight_value":5}'::jsonb)::text$q$, it)), 'WEIGHT_INCOMPLETE', 'вес без единицы');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"x","weight_value":5,"weight_unit":"кг"}'::jsonb)::text$q$, it)), 'INVALID_DATA', 'единица веса вне списка');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, %L::jsonb)::text$q$, it, json_build_object('slug', 'x', 'description', repeat('я', 501))::text)), 'INVALID_DATA', 'описание длиннее 500');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"x","recommended":"да"}'::jsonb)::text$q$, it)), 'INVALID_DATA', 'recommended не boolean');
  perform pg_temp.ok(not exists (select 1 from public.menu_item_public where item_id = it::bigint), 'неудачные попытки не оставили строк');
  r := pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"cheeseburger","description":"Сочная котлета","weight_value":260,"weight_unit":"г","recommended":true,"sort":1}'::jsonb)::text$q$, it)), 'данные блюда');
  perform pg_temp.ok(r ->> 'slug' = 'cheeseburger', 'слаг блюда создан');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"cheeseburger-2"}'::jsonb)::text$q$, it)), 'SLUG_IMMUTABLE', 'смена слага блюда');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"cheeseburger"}'::jsonb)::text$q$, pg_temp.c('it_cola'))), 'SLUG_TAKEN', 'дубль слага блюда');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"weight_unit":null}'::jsonb)::text$q$, it)), 'WEIGHT_INCOMPLETE', 'очистка только единицы');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"weight_value":null,"weight_unit":null}'::jsonb)::text$q$, it)), 'очистка веса целиком');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"weight_value":260,"weight_unit":"г"}'::jsonb)::text$q$, it)), 'вес возвращён');
  -- публикация шаг за шагом
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', it)), 'PRICE_NOT_CONFIRMED', 'цена не подтверждена');
  d := (select e from jsonb_array_elements(pg_temp.good(pg_temp.adm('select public.admin_catalog_diagnostics()::text'), 'диагностика')->'items') e where (e ->> 'id')::bigint = it::bigint);
  perform pg_temp.ok(d -> 'reasons' = '["NOT_PUBLISHED","PRICE_NOT_CONFIRMED"]'::jsonb, 'диагностика до подтверждения: ' || (d -> 'reasons')::text);
  r := pg_temp.good(pg_temp.adm(format('select public.admin_confirm_menu_price(%s)::text', it)), 'подтверждение цены');
  perform pg_temp.ok((r ->> 'confirmed_price')::numeric = 2290, 'подтверждена цена 2290');
  r := pg_temp.good(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', it)), 'публикация');
  perform pg_temp.ok((r ->> 'published')::boolean, 'опубликовано');
  dishes := pg_temp.good(pg_temp.anon('select public.public_menu()::text'), 'публичное меню') -> 'dishes';
  d := (select e from jsonb_array_elements(dishes) e where e ->> 'id' = 'cheeseburger');
  perform pg_temp.ok(d is not null, 'блюдо видно анониму');
  perform pg_temp.ok(d ->> 'name' = 'Т-Чизбургер' and d ->> 'category' = 'burgers' and jsonb_typeof(d -> 'price') = 'number' and (d ->> 'price') = '2290'
                     and d ->> 'description' = 'Сочная котлета' and d -> 'weight' = '{"value":260,"unit":"г"}'::jsonb and (d ->> 'available')::boolean and (d ->> 'recommended')::boolean
                     and d -> 'photo' = 'null'::jsonb and d -> 'ingredients' = '[]'::jsonb, 'поля блюда в публичном меню: ' || d::text);
  -- изменение цены существующей функцией скрывает блюдо до нового подтверждения
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_update_menu_item(%s, 'Т-Чизбургер', 'Т-Бургеры', 2490, 'kitchen')::text$q$, it)), 'изменение цены (функция из 02)');
  perform pg_temp.ok(not exists (select 1 from jsonb_array_elements(pg_temp.good(pg_temp.anon('select public.public_menu()::text'), 'меню после смены цены') -> 'dishes') e where e ->> 'id' = 'cheeseburger'), 'блюдо скрыто после смены цены');
  d := (select e from jsonb_array_elements(pg_temp.good(pg_temp.adm('select public.admin_catalog_diagnostics()::text'), 'диагностика') -> 'items') e where (e ->> 'id')::bigint = it::bigint);
  perform pg_temp.ok(d -> 'reasons' = '["PRICE_NOT_CONFIRMED"]'::jsonb and not (d ->> 'on_site')::boolean, 'диагностика: PRICE_NOT_CONFIRMED, ' || (d -> 'reasons')::text);
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', it)), 'PRICE_NOT_CONFIRMED', 'повторная публикация без подтверждения');
  r := pg_temp.good(pg_temp.adm(format($q$select public.admin_save_dish_public(%s, '{"published":true}'::jsonb, true)::text$q$, it)), 'составное сохранение с подтверждением цены');
  perform pg_temp.ok((r ->> 'on_site')::boolean and r -> 'reasons' = '[]'::jsonb, 'после составного сохранения блюдо на сайте');
  d := (select e from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger');
  perform pg_temp.ok((d ->> 'price') = '2490', 'на сайте новая цена 2490');
  -- снятие с публикации
  perform pg_temp.good(pg_temp.adm(format('select public.admin_publish_menu_item(%s, false)::text', it)), 'снятие с публикации');
  perform pg_temp.ok(not exists (select 1 from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger'), 'снятое блюдо скрыто');
  perform pg_temp.good(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', it)), 'публикация снова (цена всё ещё подтверждена)');
  -- цена с копейками
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"kopeiki"}'::jsonb)::text$q$, pg_temp.c('it_kop'))), 'данные блюда с копейками');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_confirm_menu_price(%s)::text', pg_temp.c('it_kop'))), 'PRICE_NOT_WHOLE', 'подтверждение цены с копейками');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', pg_temp.c('it_kop'))), 'PRICE_NOT_WHOLE', 'публикация цены с копейками');
  d := (select e from jsonb_array_elements(pg_temp.good(pg_temp.adm('select public.admin_catalog_diagnostics()::text'), 'диагностика') -> 'items') e where (e ->> 'id')::bigint = pg_temp.id('it_kop'));
  perform pg_temp.ok(d -> 'reasons' = '["NOT_PUBLISHED","PRICE_NOT_WHOLE","PRICE_NOT_CONFIRMED"]'::jsonb, 'диагностика копеек: ' || (d -> 'reasons')::text);
  -- категория без слага
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"bez-sluga"}'::jsonb)::text$q$, pg_temp.c('it_noslug'))), 'данные блюда в категории без слага');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', pg_temp.c('it_noslug'))), 'CATEGORY_SLUG_MISSING', 'категория без слага');
  -- не одобрено, отклонено
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"neodobreno"}'::jsonb)::text$q$, pg_temp.c('it_unapproved'))), 'данные неодобренного блюда');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', pg_temp.c('it_unapproved'))), 'ITEM_NOT_APPROVED', 'неодобренное блюдо');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"otklonyon"}'::jsonb)::text$q$, pg_temp.c('it_rejected'))), 'данные отклонённого блюда');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', pg_temp.c('it_rejected'))), 'ITEM_REJECTED', 'отклонённое блюдо');
  -- недоступное блюдо остаётся в меню с available = false
  perform pg_temp.good(pg_temp.adm(format('select public.admin_set_menu_item_available(%s, false)::text', it)), 'блюдо недоступно (функция из 02)');
  d := (select e from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger');
  perform pg_temp.ok(d is not null and not (d ->> 'available')::boolean, 'недоступное блюдо показывается как недоступное');
  perform pg_temp.good(pg_temp.adm(format('select public.admin_set_menu_item_available(%s, true)::text', it)), 'снова доступно');
  -- архив блюда
  perform pg_temp.good(pg_temp.adm(format('select public.admin_archive_menu_item(%s)::text', it)), 'архив блюда (функция из 15)');
  perform pg_temp.ok(not exists (select 1 from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger'), 'архивное блюдо скрыто');
  d := (select e from jsonb_array_elements(pg_temp.good(pg_temp.adm('select public.admin_catalog_diagnostics()::text'), 'диагностика') -> 'items') e where (e ->> 'id')::bigint = it::bigint);
  perform pg_temp.ok(d -> 'reasons' ? 'ITEM_ARCHIVED', 'диагностика: ITEM_ARCHIVED');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', it)), 'ITEM_ARCHIVED', 'публикация архивного');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_confirm_menu_price(%s)::text', it)), 'ITEM_ARCHIVED', 'подтверждение цены архивного');
  perform pg_temp.good(pg_temp.adm(format('select public.admin_restore_menu_item(%s)::text', it)), 'восстановление блюда');
  perform pg_temp.ok(exists (select 1 from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger'), 'восстановленное опубликованное блюдо снова на сайте');
  -- архив категории скрывает её блюда
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"archived":true}'::jsonb)::text$q$, pg_temp.c('cat_burgers'))), 'архив категории бургеров');
  perform pg_temp.ok(not exists (select 1 from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger'), 'блюдо архивной категории скрыто');
  d := (select e from jsonb_array_elements(pg_temp.good(pg_temp.adm('select public.admin_catalog_diagnostics()::text'), 'диагностика') -> 'items') e where (e ->> 'id')::bigint = it::bigint);
  perform pg_temp.ok(d -> 'reasons' ? 'CATEGORY_ARCHIVED', 'диагностика: CATEGORY_ARCHIVED');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_publish_menu_item(%s, true)::text', it)), 'CATEGORY_ARCHIVED', 'публикация в архивной категории');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_category_public(%s, '{"archived":false}'::jsonb)::text$q$, pg_temp.c('cat_burgers'))), 'категория возвращена');
  perform pg_temp.ok(exists (select 1 from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger'), 'блюдо вернулось вместе с категорией');
  -- итоги диагностики
  r := pg_temp.good(pg_temp.adm('select public.admin_catalog_diagnostics()::text'), 'итоги диагностики');
  select count(*) into n from public.menu_items where name like 'Т-%';
  perform pg_temp.ok((r ->> 'on_site')::int + (r ->> 'blocked')::int = n and jsonb_array_length(r -> 'items') = n, 'on_site + blocked = число блюд');
  raise notice 'ТЕСТ 4 (блюда: публикация, подтверждение цены, диагностика, архив): OK';
end $t$;

-- =====================================================================
-- 5. Медиа и фотографии блюд
-- =====================================================================
do $t$
declare
  it text := pg_temp.c('it_cheese'); r jsonb; repo_id bigint; st_id bigint; d jsonb; v_sha text := repeat('a', 64); v_state text;
  v_ok constant text := '{"storage":"repo","path":"cheeseburger.webp","thumb_path":"cheeseburger-thumb.webp","mime":"image/webp","size_bytes":81000,"width":1200,"height":900,"alt":"Чизбургер на тарелке","license":"CC0","source_url":"https://commons.wikimedia.org/x","author":"Автор"}';
begin
  -- неверные регистрации
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"Cheese.webp","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":100,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'заглавные буквы в пути');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"../x.webp","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":100,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'выход из каталога в пути');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"a//b.webp","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":100,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'двойной слэш');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"a.gif","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":100,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'расширение gif');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"a.webp","thumb_path":"c-thumb.webp","mime":"image/gif","size_bytes":100,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'тип gif');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"a.webp","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":0,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'размер 0');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"a.webp","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":6000000,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'размер больше 5 МиБ');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"a.webp","mime":"image/webp","size_bytes":100,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'нет миниатюры');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"ftp","path":"a.webp","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":100,"width":10,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'неизвестное хранилище');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"a.webp","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":100,"width":10,"height":10,"sha256":"xyz"}'::jsonb)::text$q$), 'INVALID_DATA', 'плохая контрольная сумма');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_register_media('{"storage":"repo","path":"a.webp","thumb_path":"c-thumb.webp","mime":"image/webp","size_bytes":100,"width":0,"height":10}'::jsonb)::text$q$), 'INVALID_DATA', 'ширина 0');
  perform pg_temp.ok((select count(*) from public.media_assets) = 0, 'неверные регистрации не оставили записей');
  -- регистрация и повтор
  r := pg_temp.good(pg_temp.adm(format('select public.admin_register_media(%L::jsonb)::text', v_ok)), 'регистрация repo');
  repo_id := (r ->> 'id')::bigint;
  perform pg_temp.ok(not (r ->> 'duplicate')::boolean, 'первая регистрация не дубль');
  r := pg_temp.good(pg_temp.adm(format('select public.admin_register_media(%L::jsonb)::text', v_ok)), 'повторная регистрация');
  perform pg_temp.ok((r ->> 'duplicate')::boolean and (r ->> 'id')::bigint = repo_id, 'повтор возвращает ту же запись');
  st_id := pg_temp.reg(json_build_object('storage', 'storage', 'path', 'dishes/t-1.webp', 'thumb_path', 'dishes/t-1-thumb.webp', 'mime', 'image/webp', 'size_bytes', 50000, 'width', 800, 'height', 600, 'sha256', v_sha)::text);
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_register_media(%L::jsonb)::text', json_build_object('storage', 'storage', 'path', 'dishes/t-1.webp', 'thumb_path', 'dishes/t-1-thumb.webp',
        'mime', 'image/webp', 'size_bytes', 50000, 'width', 800, 'height', 600, 'sha256', repeat('b', 64))::text)), 'MEDIA_CONFLICT', 'тот же путь с другой контрольной суммой');
  insert into ctx values ('media_repo', repo_id::text), ('media_storage', st_id::text);
  -- привязка к блюду
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_set_dish_photo(%s, 999999, null)::text', it)), 'MEDIA_NOT_FOUND', 'нет такого медиа');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_set_dish_photo(%s, %s, null)::text', pg_temp.c('it_salad'), repo_id)), 'PUBLIC_DATA_MISSING', 'фото блюду без публичных данных');
  perform pg_temp.good(pg_temp.adm(format('select public.admin_set_dish_photo(%s, %s, null)::text', it, repo_id)), 'привязка фото');
  d := (select e -> 'photo' from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger');
  perform pg_temp.ok(d = '{"source":"repo","file":"cheeseburger.webp","thumb":"cheeseburger-thumb.webp","alt":"Чизбургер на тарелке","width":1200,"height":900}'::jsonb, 'объект фото в публичном меню: ' || d::text);
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_dish_photo(%s, %s, 'Свой alt')::text$q$, it, repo_id)), 'свой alt');
  perform pg_temp.ok((select e -> 'photo' ->> 'alt' from jsonb_array_elements(pg_temp.anon_json()) e where e ->> 'id' = 'cheeseburger') = 'Свой alt', 'alt блюда переопределяет alt медиа');
  -- список и использование
  r := pg_temp.good(pg_temp.adm('select public.admin_media_list(false)::text'), 'список медиа');
  perform pg_temp.ok(jsonb_array_length(r) = 2 and (select (e ->> 'used')::boolean from jsonb_array_elements(r) e where (e ->> 'id')::bigint = repo_id)
                     and (select jsonb_array_length(e -> 'used_by') from jsonb_array_elements(r) e where (e ->> 'id')::bigint = repo_id) = 1, 'repo-медиа используется блюдом');
  r := pg_temp.good(pg_temp.adm('select public.admin_media_list(true)::text'), 'список неиспользуемых');
  perform pg_temp.ok(jsonb_array_length(r) = 1 and (r -> 0 ->> 'id')::bigint = st_id, 'среди неиспользуемых только storage-медиа');
  -- удаление
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_delete_media(%s)::text', repo_id)), 'REPO_MEDIA_PROTECTED', 'запись repo защищена от удаления');
  perform pg_temp.bad(pg_temp.adm('select public.admin_delete_media(999999)::text'), 'MEDIA_NOT_FOUND', 'удаление несуществующего');
  perform pg_temp.good(pg_temp.adm(format('select public.admin_set_dish_photo(%s, %s, null)::text', it, st_id)), 'storage-фото блюду');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_delete_media(%s)::text', st_id)), 'MEDIA_IN_USE', 'удаление используемого медиа');
  begin
    delete from public.media_assets where id = st_id;
    raise exception 'FAIL: прямое удаление используемого медиа прошло';
  exception when foreign_key_violation then null;
  end;
  perform pg_temp.good(pg_temp.adm(format('select public.admin_set_dish_photo(%s, null, null)::text', it)), 'снятие фото');
  r := pg_temp.good(pg_temp.adm(format('select public.admin_delete_media(%s)::text', st_id)), 'удаление неиспользуемого storage-медиа');
  perform pg_temp.ok((r ->> 'deleted')::boolean and r ->> 'path' = 'dishes/t-1.webp' and r ->> 'thumb_path' = 'dishes/t-1-thumb.webp', 'ответ удаления возвращает пути файлов для удаления в бакете');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_delete_media(%s)::text', st_id)), 'MEDIA_NOT_FOUND', 'повторное удаление');
  -- для следующих тестов: у блюда фото repo, создаём снова storage-медиа
  st_id := pg_temp.reg(json_build_object('storage', 'storage', 'path', 'dishes/t-2.webp', 'thumb_path', 'dishes/t-2-thumb.webp', 'mime', 'image/jpeg', 'size_bytes', 60000, 'width', 640, 'height', 480)::text);
  update ctx set v = st_id::text where k = 'media_storage';
  perform pg_temp.good(pg_temp.adm(format('select public.admin_set_dish_photo(%s, %s, null)::text', it, repo_id)), 'вернули фото repo');
  raise notice 'ТЕСТ 5 (медиа: регистрация, привязка, использование, защита удаления): OK';
end $t$;

-- =====================================================================
-- 6. Справочники состава и аллергенов
-- =====================================================================
do $t$
declare
  it text := pg_temp.c('it_cheese'); r jsonb; v_ids text;
begin
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_save_allergen('Gluten', 'Глютен', 0)::text$q$), 'INVALID_DATA', 'заглавные в id аллергена');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_save_allergen('gluten', '  ', 0)::text$q$), 'INVALID_DATA', 'пустое название аллергена');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_allergen('gluten', 'Глютенсодержащие злаки', 1)::text$q$), 'аллерген gluten');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_allergen('milk', 'Молоко', 2)::text$q$), 'аллерген milk');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_allergen('eggs', 'Яйца', 3)::text$q$), 'аллерген eggs');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_allergen('milk', 'Молоко и молочные продукты', 2)::text$q$), 'обновление аллергена');
  perform pg_temp.ok((select name from public.allergens where id = 'milk') = 'Молоко и молочные продукты', 'название аллергена обновлено');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_save_ingredient('Bad Id', 'X', '{}'::jsonb)::text$q$), 'INVALID_DATA', 'неверный id состава');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_save_ingredient('bun', 'булочка', '[]'::jsonb)::text$q$), 'INVALID_DATA', 'аллергены не объектом');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_save_ingredient('bun', 'булочка', '{"nuts":"contains"}'::jsonb)::text$q$), 'ALLERGEN_NOT_FOUND', 'неизвестный аллерген');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_save_ingredient('bun', 'булочка', '{"gluten":"maybe"}'::jsonb)::text$q$), 'INVALID_DATA', 'неверный статус');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_save_ingredient('bun', 'булочка', '{"gluten":1}'::jsonb)::text$q$), 'INVALID_DATA', 'статус числом');
  perform pg_temp.ok(not exists (select 1 from public.ingredients), 'неудачные попытки не создали состав');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_ingredient('bun', 'булочка', '{"gluten":"contains","milk":"may_contain"}'::jsonb)::text$q$), 'состав bun');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_ingredient('beef', 'говяжья котлета', '{"milk":"confirmed_free"}'::jsonb)::text$q$), 'состав beef');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_ingredient('unused_ing', 'не используется', '{}'::jsonb)::text$q$), 'неиспользуемый состав');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_ingredient('secret_ing', 'только в неопубликованном', '{"eggs":"contains"}'::jsonb)::text$q$), 'состав неопубликованного блюда');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_save_ingredient('bun', 'булочка', '{"gluten":"contains","eggs":"may_contain"}'::jsonb)::text$q$), 'замена набора аллергенов состава');
  perform pg_temp.ok((select string_agg(allergen_id || ':' || status, ',' order by allergen_id) from public.ingredient_allergens where ingredient_id = 'bun') = 'eggs:may_contain,gluten:contains', 'набор аллергенов заменён целиком');
  -- состав блюда
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_ingredients(%s, '["bun","zzz"]'::jsonb)::text$q$, it)), 'INGREDIENT_NOT_FOUND', 'неизвестный состав у блюда');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_ingredients(%s, '["bun",5]'::jsonb)::text$q$, it)), 'INVALID_DATA', 'не строка в составе');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_ingredients(%s, '{"a":1}'::jsonb)::text$q$, it)), 'INVALID_DATA', 'состав не массивом');
  perform pg_temp.bad(pg_temp.adm(format('select public.admin_set_menu_item_ingredients(%s, %L::jsonb)::text', it, (select jsonb_agg('bun'::text)::text from generate_series(1, 41)))), 'INVALID_DATA', 'больше 40 позиций состава');
  perform pg_temp.bad(pg_temp.adm($q$select public.admin_set_menu_item_ingredients(999999, '[]'::jsonb)::text$q$), 'ITEM_NOT_FOUND', 'состав у несуществующего блюда');
  r := pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_ingredients(%s, '["beef","bun","bun"]'::jsonb)::text$q$, it)), 'состав блюда с дублем');
  perform pg_temp.ok((r ->> 'count')::int = 2, 'дубль в составе схлопнут');
  select string_agg(ingredient_id, ',' order by sort) into v_ids from public.menu_item_ingredients where item_id = it::bigint;
  perform pg_temp.ok(v_ids = 'beef,bun', 'порядок состава сохранён: ' || v_ids);
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"kopeiki"}'::jsonb)::text$q$, pg_temp.c('it_kop'))), 'данные блюда с копейками (повтор)');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_ingredients(%s, '["secret_ing"]'::jsonb)::text$q$, pg_temp.c('it_kop'))), 'состав неопубликованного блюда привязан');
  raise notice 'ТЕСТ 6 (справочники состава и аллергенов): OK';
end $t$;

-- =====================================================================
-- 7. Составное сохранение блюда: всё или ничего
-- =====================================================================
do $t$
declare
  salad text := pg_temp.c('it_salad'); cola2 text := pg_temp.c('it_cola2'); cola text := pg_temp.c('it_cola'); r jsonb;
begin
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_save_dish_public(%s, '{"slug":"salat","ingredients":["nope"]}'::jsonb, false)::text$q$, salad)), 'INGREDIENT_NOT_FOUND', 'неверный состав в составном сохранении');
  perform pg_temp.ok(not exists (select 1 from public.menu_item_public where item_id = salad::bigint), 'после ошибки состава публичная запись не создана (откат)');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_save_dish_public(%s, '{"slug":"salat","photo_asset_id":999999}'::jsonb, false)::text$q$, salad)), 'MEDIA_NOT_FOUND', 'неверное фото в составном сохранении');
  perform pg_temp.ok(not exists (select 1 from public.menu_item_public where item_id = salad::bigint), 'после ошибки фото публичная запись не создана (откат)');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_save_dish_public(%s, '{"slug":"cola-2","published":true}'::jsonb, false)::text$q$, cola2)), 'PRICE_NOT_CONFIRMED', 'публикация без подтверждения в составном сохранении');
  perform pg_temp.ok(not exists (select 1 from public.menu_item_public where item_id = cola2::bigint), 'после отказа публикации данные блюда не сохранены (откат)');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_save_dish_public(%s, '{"slug":"cola-2","published":"да"}'::jsonb, true)::text$q$, cola2)), 'INVALID_DATA', 'published не boolean');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_save_dish_public(%s, '[1]'::jsonb, false)::text$q$, cola2)), 'INVALID_DATA', 'данные не объектом');
  r := pg_temp.good(pg_temp.adm(format($q$select public.admin_save_dish_public(%s, %L::jsonb, true)::text$q$, salad,
        json_build_object('slug', 'salat', 'description', 'Свежий салат', 'weight_value', 200, 'weight_unit', 'г', 'sort', 3, 'recommended', false, 'ingredients', json_build_array('bun'),
                          'photo_asset_id', pg_temp.id('media_storage'), 'photo_alt', 'Салат', 'published', true)::text)), 'составное сохранение целиком');
  perform pg_temp.ok((r ->> 'on_site')::boolean and r -> 'reasons' = '[]'::jsonb and (r ->> 'published')::boolean, 'салат на сайте после одного вызова');
  perform pg_temp.ok((select price_confirmed_price from public.menu_item_public where item_id = salad::bigint) = 1500, 'цена подтверждена в том же вызове');
  perform pg_temp.ok((select photo_asset_id from public.menu_item_public where item_id = salad::bigint) = pg_temp.id('media_storage'), 'фото привязано');
  perform pg_temp.ok((select count(*) from public.menu_item_ingredients where item_id = salad::bigint) = 1, 'состав привязан');
  -- напиток для проверки категорий и порядка
  r := pg_temp.good(pg_temp.adm(format($q$select public.admin_save_dish_public(%s, '{"slug":"cola","description":"Холодный напиток","weight_value":330,"weight_unit":"мл","published":true}'::jsonb, true)::text$q$, cola)), 'кола');
  perform pg_temp.ok((r ->> 'on_site')::boolean, 'кола на сайте');
  raise notice 'ТЕСТ 7 (составное сохранение блюда, откат при ошибке): OK';
end $t$;

-- =====================================================================
-- 8. Формат public_menu() и отсутствие внутренних данных
-- =====================================================================
do $t$
declare
  m jsonb; txt text; d jsonb; k text; cat_ids text[]; m2 jsonb;
  v_dish_keys constant text[] := array['available', 'category', 'description', 'id', 'ingredients', 'name', 'photo', 'price', 'recommended', 'sort', 'weight'];
begin
  m := pg_temp.good(pg_temp.anon('select public.public_menu()::text'), 'публичное меню');
  txt := m::text;
  perform pg_temp.ok((select array_agg(x order by x) from jsonb_object_keys(m) x) = array['allergens', 'categories', 'dishes', 'generated_at', 'ingredients', 'schema'], 'ключи верхнего уровня');
  perform pg_temp.ok((m ->> 'schema') = '1', 'schema = 1');
  select array_agg(c ->> 'id' order by (c ->> 'sort')::int) into cat_ids from jsonb_array_elements(m -> 'categories') c;
  perform pg_temp.ok(cat_ids = array['burgers', 'drinks'], 'в категориях только непустые, по порядку: ' || array_to_string(cat_ids, ','));
  perform pg_temp.ok(not exists (select 1 from jsonb_array_elements(m -> 'categories') c where (select array_agg(x order by x) from jsonb_object_keys(c) x) <> array['id', 'name', 'sort']), 'ключи категорий: id, name, sort');
  perform pg_temp.ok(jsonb_array_length(m -> 'dishes') = 3, 'на сайте три блюда, получено ' || jsonb_array_length(m -> 'dishes'));
  perform pg_temp.ok((select array_agg(e ->> 'id' order by ord) from jsonb_array_elements(m -> 'dishes') with ordinality t(e, ord)) = array['cola', 'cheeseburger', 'salat'], 'порядок блюд по sort, затем названию');
  for d in select e from jsonb_array_elements(m -> 'dishes') e loop
    perform pg_temp.ok((select array_agg(x order by x) from jsonb_object_keys(d) x) = v_dish_keys, 'ключи блюда ' || (d ->> 'id'));
    perform pg_temp.ok((d ->> 'id') ~ '^[a-z0-9]+(-[a-z0-9]+)*$' and (d ->> 'category') = any (cat_ids), 'слаг и категория блюда ' || (d ->> 'id'));
    perform pg_temp.ok(jsonb_typeof(d -> 'price') = 'number' and (d ->> 'price') ~ '^[0-9]+$', 'цена — целое число: ' || (d ->> 'price'));
    perform pg_temp.ok(jsonb_typeof(d -> 'ingredients') = 'array' and jsonb_typeof(d -> 'available') = 'boolean' and jsonb_typeof(d -> 'recommended') = 'boolean', 'типы полей блюда');
    perform pg_temp.ok((d -> 'photo') = 'null'::jsonb or (select array_agg(x order by x) from jsonb_object_keys(d -> 'photo') x) = array['alt', 'file', 'height', 'source', 'thumb', 'width'], 'ключи фото блюда ' || (d ->> 'id'));
  end loop;
  d := (select e from jsonb_array_elements(m -> 'dishes') e where e ->> 'id' = 'salat');
  perform pg_temp.ok(d -> 'photo' ->> 'source' = 'storage' and d -> 'photo' ->> 'file' = 'dishes/t-2.webp' and d -> 'photo' ->> 'alt' = 'Салат', 'фото из storage');
  d := (select e from jsonb_array_elements(m -> 'dishes') e where e ->> 'id' = 'cheeseburger');
  perform pg_temp.ok(d -> 'ingredients' = '["beef","bun"]'::jsonb, 'состав блюда по порядку: ' || (d -> 'ingredients')::text);
  -- словарь состава: только используемое опубликованными блюдами
  perform pg_temp.ok((select array_agg(x order by x) from jsonb_object_keys(m -> 'ingredients') x) = array['beef', 'bun'], 'в словаре состава только используемые: ' || (select string_agg(x, ',') from jsonb_object_keys(m -> 'ingredients') x));
  perform pg_temp.ok(m -> 'ingredients' -> 'bun' = '{"name":"булочка","allergens":{"gluten":"contains","eggs":"may_contain"},"confirmed_free":[]}'::jsonb, 'аллергены bun: ' || (m -> 'ingredients' -> 'bun')::text);
  perform pg_temp.ok(m -> 'ingredients' -> 'beef' = '{"name":"говяжья котлета","allergens":{},"confirmed_free":["milk"]}'::jsonb, 'аллергены beef: ' || (m -> 'ingredients' -> 'beef')::text);
  perform pg_temp.ok(not (m -> 'ingredients' ? 'secret_ing') and not (m -> 'ingredients' ? 'unused_ing'), 'состав неопубликованных и неиспользуемых не раскрывается');
  perform pg_temp.ok((select array_agg(a ->> 'id' order by ord) from jsonb_array_elements(m -> 'allergens') with ordinality t(a, ord)) = array['gluten', 'milk', 'eggs'], 'список аллергенов по порядку');
  -- нет внутренних полей
  foreach k in array array['"station"', '"code"', '"price_confirmed', '"confirmed_by"', '"uploaded_by"', '"purchase', '"is_available"', '"archived', '"published"', '"approved', '"cashier', '"sha256"', '"license"', '"source_url"', '"note"'] loop
    perform pg_temp.ok(position(k in txt) = 0, 'в публичном меню нет поля ' || k);
  end loop;
  perform pg_temp.ok(not exists (select 1 from jsonb_array_elements(m -> 'dishes') e where e ->> 'name' in ('Т-Копейки', 'Т-НеОдобрено', 'Т-Отклонено', 'Т-БезСлага-блюдо', 'Т-Кола2')), 'неопубликованные блюда отсутствуют');
  -- тот же ответ для авторизованных (кроме времени генерации)
  m2 := pg_temp.good(pg_temp.try_as('authenticated', pg_temp.u('cashier'), 'select public.public_menu()::text'), 'меню для кассира');
  perform pg_temp.ok((m - 'generated_at') = (m2 - 'generated_at'), 'для кассира то же меню');
  raise notice 'ТЕСТ 8 (формат public_menu, отсутствие внутренних полей и чужого состава): OK';
end $t$;

-- =====================================================================
-- 9. Блюдо удалено окончательно: слаг зарезервирован; диагностика совпадает с публичным меню; отметка времени
-- =====================================================================
do $t$
declare
  cola text := pg_temp.c('it_cola'); cola2 text := pg_temp.c('it_cola2'); r jsonb; n integer;
begin
  perform pg_temp.good(pg_temp.adm(format('select public.admin_archive_menu_item(%s)::text', cola)), 'архив колы');
  perform pg_temp.good(pg_temp.adm(format('select public.admin_purge_menu_item(%s)::text', cola)), 'окончательное удаление колы (функция из 15)');
  perform pg_temp.ok(not exists (select 1 from public.menu_item_public where slug = 'cola'), 'публичная запись удалена вместе с блюдом');
  perform pg_temp.ok(exists (select 1 from public.public_slug_reservations where kind = 'dish' and slug = 'cola'), 'слаг блюда остался зарезервированным');
  perform pg_temp.bad(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"cola"}'::jsonb)::text$q$, cola2)), 'SLUG_RESERVED', 'повторное использование слага удалённого блюда');
  perform pg_temp.good(pg_temp.adm($q$select public.admin_release_slug('dish', 'cola')::text$q$), 'освобождение слага блюда');
  perform pg_temp.good(pg_temp.adm(format($q$select public.admin_set_menu_item_public(%s, '{"slug":"cola"}'::jsonb)::text$q$, cola2)), 'слаг после освобождения');
  perform pg_temp.ok(exists (select 1 from public.media_assets where storage = 'repo'), 'медиа не удаляются вместе с блюдом');
  -- диагностика и public_menu() построены на одном источнике: для каждого блюда «нет причин» ⇔ «есть в публичном меню»
  select count(*) into n from public._public_blockers() b
   where (cardinality(b.reasons) = 0) <> exists (select 1 from jsonb_array_elements(public.public_menu() -> 'dishes') e join public.menu_item_public p on p.slug = e ->> 'id' where p.item_id = b.item_id);
  perform pg_temp.ok(n = 0, 'диагностика расходится с публичным меню по ' || n || ' блюдам');
  r := pg_temp.good(pg_temp.adm('select public.admin_catalog_diagnostics()::text'), 'диагностика');
  perform pg_temp.ok((r ->> 'on_site')::int = jsonb_array_length(public.public_menu() -> 'dishes'), 'число блюд на сайте совпадает с диагностикой');
  -- отметка времени menu_items: строка создана с updated_at = 2000-01-01, после изменения цены стала текущей
  perform pg_temp.ok((select updated_at from public.menu_items where id = pg_temp.id('it_cheese')) > '2020-01-01', 'menu_items.updated_at обновляется триггером');
  raise notice 'ТЕСТ 9 (окончательное удаление и резервирование слага, согласованность диагностики, updated_at): OK';
end $t$;

-- ---------- Образец публичного меню для проверки валидатором сайта (до отката) ----------
\o <HOME>/cashier_catalog_dev_pg/work/v2/tests/public_menu_sample.json
select public.public_menu();
\o

select 'ВСЕ ПРОВЕРКИ МИГРАЦИИ 18 ПРОЙДЕНЫ';
rollback;
