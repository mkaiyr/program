-- =====================================================================
-- ПРОВЕРКА ПОСЛЕ миграции 16 (справочник категорий меню)
-- ТОЛЬКО ЧТЕНИЕ: один SELECT, ничего не создаёт и не меняет. Безопасно запускать на рабочем проекте.
-- Supabase: SQL Editor -> New query -> Run. Колонка status: OK — норма; FAIL — проблема (см. docs/DEPLOY_16_SUPABASE.md,
-- раздел «Если что-то пошло не так»); INFO — сравните со значениями предварительной проверки (строки «Контроль»).
-- Права ролей проверяются по каталогу PostgreSQL (has_*_privilege, ACL), без подмены ролей и без записи.
-- =====================================================================
with
t as (select c.oid, pg_get_userbyid(c.relowner) as owner, c.relrowsecurity as rls from pg_class c
       where c.oid = to_regclass('public.menu_categories')),
mi as (select pg_get_userbyid(c.relowner) as owner from pg_class c where c.oid = 'public.menu_items'::regclass),
rpc(name, args) as (values ('admin_add_category', 'p_name text'), ('admin_delete_category', 'p_name text'),
                           ('admin_rename_category', 'p_old text, p_new text')),
fn as (select r.name, p.oid, p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner) as owner, p.prosrc,
              (select count(*) from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where a.grantee = 0) as public_exec
         from rpc r left join pg_proc p on p.pronamespace = 'public'::regnamespace and p.proname = r.name
                                       and pg_get_function_identity_arguments(p.oid) = r.args),
tf as (select p.oid, p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner) as owner,
              (select count(*) from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where a.grantee = 0) as public_exec
         from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = 'menu_items_category_ref'),
fk as (select * from pg_constraint where conname = 'menu_items_category_fkey' and conrelid = 'public.menu_items'::regclass)
select * from (
  select 10 as ord, 'Таблица menu_categories существует' as check_name, case when (select count(*) from t) = 1 then 'OK' else 'FAIL' end as status,
         coalesce((select 'владелец ' || owner from t), 'нет') as detail
  union all select 11, 'Владелец таблицы = владелец menu_items (как у остальных таблиц)', case when (select owner from t) = (select owner from mi) then 'OK' else 'FAIL' end,
         coalesce((select owner from t), '') || ' / ' || (select owner from mi)
  union all select 12, 'RLS включена', case when (select rls from t) then 'OK' else 'FAIL' end, ''
  union all select 13, 'Политика чтения: только SELECT для authenticated',
         case when exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'menu_categories' and policyname = 'menu_categories_select'
                           and cmd = 'SELECT' and roles = '{authenticated}') and (select count(*) from pg_policies where tablename = 'menu_categories') = 1 then 'OK' else 'FAIL' end,
         coalesce((select qual from pg_policies where tablename = 'menu_categories' and policyname = 'menu_categories_select'), 'нет')
  union all select 14, 'Колонки id, name, created_at, created_by',
         case when (select count(*) from pg_attribute where attrelid = to_regclass('public.menu_categories') and attname in ('id', 'name', 'created_at', 'created_by') and not attisdropped) = 4 then 'OK' else 'FAIL' end, ''
  union all select 15, 'Ограничения: PK, UNIQUE(name), CHECK(имя 1–50 без пробелов по краям)',
         case when (select count(*) from pg_constraint where conrelid = to_regclass('public.menu_categories') and conname in
                    ('menu_categories_pkey', 'menu_categories_name_key', 'menu_categories_name_check')) = 3 then 'OK' else 'FAIL' end, ''
  union all select 16, 'Уникальность без учёта регистра (индекс по lower(name))',
         case when exists (select 1 from pg_indexes where schemaname = 'public' and tablename = 'menu_categories' and indexname = 'menu_categories_name_ci_key'
                           and indexdef ilike '%unique%' and indexdef ilike '%lower%') then 'OK' else 'FAIL' end, ''
  union all select 17, 'Внешний ключ блюд на категории: ON UPDATE CASCADE, ON DELETE RESTRICT, проверен',
         case when exists (select 1 from fk where confupdtype = 'c' and confdeltype = 'r' and convalidated and confrelid = to_regclass('public.menu_categories')) then 'OK' else 'FAIL' end, ''
  union all select 18, 'Триггер menu_items_category_ref: BEFORE INSERT OR UPDATE OF category, включён',
         case when exists (select 1 from pg_trigger g where g.tgrelid = 'public.menu_items'::regclass and g.tgname = 'menu_items_category_ref' and g.tgenabled = 'O'
                           and (g.tgtype & 2) = 2 and (g.tgtype & 4) = 4 and (g.tgtype & 16) = 16) then 'OK' else 'FAIL' end, ''
  -- Права на таблицу
  union all select 20, 'anon: никаких прав на menu_categories',
         case when not has_table_privilege('anon', to_regclass('public.menu_categories'), 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') then 'OK' else 'FAIL' end, ''
  union all select 21, 'authenticated: только SELECT (нет INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER)',
         case when has_table_privilege('authenticated', to_regclass('public.menu_categories'), 'SELECT')
                   and not has_table_privilege('authenticated', to_regclass('public.menu_categories'), 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') then 'OK' else 'FAIL' end, ''
  union all select 22, 'Нет прав для псевдороли PUBLIC на таблицу',
         case when not exists (select 1 from pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
                               where c.oid = to_regclass('public.menu_categories') and a.grantee = 0) then 'OK' else 'FAIL' end, ''
  union all select 23, 'service_role (серверный ключ; в приложении не используется): права на таблицу', 'INFO',
         case when has_table_privilege('service_role', to_regclass('public.menu_categories'), 'SELECT,INSERT,UPDATE,DELETE') then 'есть (обходит RLS; ключ не должен попадать в приложение)' else 'нет' end
  -- RPC
  union all select 30, 'Функция ' || name || ': существует, SECURITY DEFINER', case when oid is not null and prosecdef then 'OK' else 'FAIL' end, coalesce('владелец ' || owner, 'нет') from fn
  union all select 31, 'Функция ' || name || ': search_path пустой', case when proconfig @> array['search_path=""'] then 'OK' else 'FAIL' end, coalesce(array_to_string(proconfig, ','), '') from fn
  union all select 32, 'Функция ' || name || ': владелец = владелец таблицы', case when owner = (select owner from mi) then 'OK' else 'FAIL' end, coalesce(owner, '') from fn
  union all select 33, 'Функция ' || name || ': EXECUTE у authenticated, нет у anon и PUBLIC',
         case when oid is not null and has_function_privilege('authenticated', oid, 'EXECUTE') and not has_function_privilege('anon', oid, 'EXECUTE') and public_exec = 0 then 'OK' else 'FAIL' end, '' from fn
  union all select 34, 'Функция ' || name || ': проверка администратора внутри (is_active_admin, ADMIN_REQUIRED, NOT_AUTHENTICATED)',
         case when prosrc like '%public.is_active_admin()%' and prosrc like '%ADMIN_REQUIRED%' and prosrc like '%NOT_AUTHENTICATED%' then 'OK' else 'FAIL' end, '' from fn
  union all select 35, 'Триггерная функция menu_items_category_ref: SECURITY DEFINER, search_path пустой, недоступна клиентам',
         case when (select count(*) from tf) = 1 and (select prosecdef from tf) and (select proconfig from tf) @> array['search_path=""']
                   and not has_function_privilege('authenticated', (select oid from tf), 'EXECUTE') and not has_function_privilege('anon', (select oid from tf), 'EXECUTE')
                   and (select public_exec from tf) = 0 then 'OK' else 'FAIL' end, ''
  union all select 36, 'admin_rename_category — версия из 16 (работает со справочником), а не из 14',
         case when (select prosrc from fn where name = 'admin_rename_category') like '%menu_categories%' then 'OK' else 'FAIL' end,
         'не OK — значит, 14 запускали после 16: выполните 16 ещё раз'
  -- Совместимость с 09 и 15
  union all select 40, 'admin_report из 15 (категория из снимка заказа) на месте',
         case when exists (select 1 from pg_proc p where p.proname = 'admin_report' and pg_get_functiondef(p.oid) like '%i.category%') then 'OK' else 'FAIL' end, ''
  union all select 41, 'station_menu из 15 (archived_at) на месте',
         case when exists (select 1 from pg_proc p where p.proname = 'station_menu' and pg_get_functiondef(p.oid) like '%archived_at%') then 'OK' else 'FAIL' end, ''
  union all select 42, 'Политика menu_select_staff из 15 (архивные блюда не видны кассиру)',
         case when exists (select 1 from pg_policies where tablename = 'menu_items' and policyname = 'menu_select_staff' and qual like '%archived_at%') then 'OK' else 'FAIL' end, ''
  union all select 43, 'У клиентов нет прямых прав записи в menu_items',
         case when not has_table_privilege('authenticated', 'public.menu_items', 'INSERT,UPDATE,DELETE') and not has_table_privilege('anon', 'public.menu_items', 'SELECT,INSERT,UPDATE,DELETE') then 'OK' else 'FAIL' end, ''
  -- Данные
  union all select 50, 'Каждое блюдо ссылается на категорию справочника (сирот нет)',
         case when not exists (select 1 from public.menu_items m where not exists (select 1 from public.menu_categories c where c.name = m.category)) then 'OK' else 'FAIL' end, ''
  union all select 51, 'Названия категорий уникальны без учёта регистра',
         case when (select count(*) from public.menu_categories) = (select count(distinct lower(name)) from public.menu_categories) then 'OK' else 'FAIL' end, ''
  union all select 52, 'Категорий в справочнике', 'INFO', (select count(*)::text from public.menu_categories)
  union all select 53, 'Из них пустых (нет ни одного блюда)', 'INFO', (select count(*)::text from public.menu_categories c where not exists (select 1 from public.menu_items m where m.category = c.name))
  union all select 54, 'Категорий — ожидалось по предварительной проверке (строка 62)', 'INFO', 'должно совпасть со значением «столько будет в справочнике»'
  -- Контроль сохранности: те же значения, что в предварительной проверке
  union all select 70, 'Контроль: заказов', 'INFO', count(*)::text from public.orders
  union all select 71, 'Контроль: позиций заказов', 'INFO', count(*)::text from public.order_items
  union all select 72, 'Контроль: блюд в архиве', 'INFO', count(*)::text from public.menu_items where archived_at is not null
  union all select 73, 'Контроль: сотрудников / активных администраторов', 'INFO', count(*)::text || ' / ' || count(*) filter (where role = 'admin' and is_active)::text from public.profiles
  union all select 74, 'Контроль: блюд (всего)', 'INFO', count(*)::text from public.menu_items
  union all select 75, 'Контроль: хеш блюд (кроме категории) — ДОЛЖЕН СОВПАСТЬ с предварительной проверкой', 'INFO',
         md5(coalesce((select string_agg(concat_ws('|', id, name, price, is_available, archived_at is not null, station, code), ',' order by id) from public.menu_items), ''))
  union all select 76, 'Контроль: хеш заказов и позиций — ДОЛЖЕН СОВПАСТЬ с предварительной проверкой', 'INFO',
         md5(coalesce((select string_agg(t::text, ',' order by id) from public.orders t), '') ||
             coalesce((select string_agg(concat_ws('|', i.id, i.item_name, i.unit_price, i.quantity, i.line_total, i.category, i.menu_item_id), ',' order by i.id) from public.order_items i), ''))
) r
order by ord, check_name;
