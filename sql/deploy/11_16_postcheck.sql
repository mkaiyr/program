-- =====================================================================
-- ПРОВЕРКА ПОСЛЕ миграций 11–16. ТОЛЬКО ЧТЕНИЕ: один SELECT, ничего не создаёт, не меняет и не вызывает функций приложения.
-- Безопасно для рабочего проекта. Supabase: SQL Editor -> New query -> Run (нужны права владельца, роль postgres).
-- status: OK — норма; FAIL — проблема (см. docs/DEPLOY_11_16_SUPABASE.md, раздел «Если что-то пошло не так»); INFO — сравните со
-- значениями предварительной проверки (строки «Контроль» 70–78: должны СОВПАСТЬ, кроме строки 74 при нормализации категорий в 16).
-- Права ролей проверяются по каталогу PostgreSQL (has_*_privilege, ACL) без подмены ролей и без записи.
-- =====================================================================
with
markers(mig, kind, a, b) as (values
  (11,'col','orders','subtotal'),(11,'col','orders','service_percent'),(11,'col','orders','service_amount'),(11,'col','orders','voided_at'),
  (11,'col','orders','voided_by'),(11,'col','orders','void_reason'),(11,'func','place_order',null),(11,'func','void_order',null),(11,'con','orders_total_parts',null),
  (12,'table','cash_shifts',null),(12,'table','cash_movements',null),(12,'col','orders','shift_id'),(12,'func','shift_open',null),(12,'func','shift_current',null),
  (12,'func','shift_cash_move',null),(12,'func','shift_close',null),(12,'func','shift_history',null),(12,'trigger','orders_attach_shift',null),
  (13,'func','admin_staff_list',null),(13,'func','admin_create_staff',null),(13,'func','admin_update_staff',null),(13,'func','admin_set_staff_password',null),
  (13,'func','_staff_admin_only',null),(13,'func','_staff_check_fields',null),
  (14,'func','admin_report',null),(14,'func','admin_rename_category',null),
  (15,'col','menu_items','archived_at'),(15,'col','menu_items','archived_by'),(15,'col','order_items','category'),(15,'table','menu_item_purge_log',null),
  (15,'func','admin_archive_menu_item',null),(15,'func','admin_restore_menu_item',null),(15,'func','admin_purge_menu_item',null),(15,'func','admin_menu_purge_log',null),
  (15,'con','menu_items_archived_state',null),(15,'trigger','order_items_snapshot_category',null),
  (16,'table','menu_categories',null),(16,'func','admin_add_category',null),(16,'func','admin_delete_category',null),
  (16,'trigger','menu_items_category_ref',null),(16,'con','menu_items_category_fkey',null)
),
marker_state as (
  select m.*, case m.kind
      when 'col' then exists (select 1 from pg_attribute where attrelid = to_regclass('public.' || m.a) and attname = m.b and not attisdropped)
      when 'func' then exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = m.a)
      when 'table' then to_regclass('public.' || m.a) is not null
      when 'trigger' then exists (select 1 from pg_trigger t where t.tgname = m.a and not t.tgisinternal)
      when 'con' then exists (select 1 from pg_constraint where conname = m.a and connamespace = 'public'::regnamespace)
    end as ok
  from markers m),
mig as (select mig, count(*) filter (where ok) as present, count(*) as total,
               string_agg(coalesce(a || coalesce('.' || b, ''), ''), ', ' order by a, b) filter (where not ok) as missing
          from marker_state group by mig),
fn as (select p.oid, p.proname, p.prosecdef, p.proconfig, p.prosrc, pg_get_userbyid(p.proowner) as owner,
              (select count(*) from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where a.grantee = 0) as public_exec
         from pg_proc p where p.pronamespace = 'public'::regnamespace and p.prokind = 'f'),
tb as (select c.oid, c.relname, c.relrowsecurity as rls, pg_get_userbyid(c.relowner) as owner
         from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind = 'r')
select * from (
  select 10 as ord, 'Миграция ' || mig || ': все объекты на месте' as check_name, case when present = total then 'OK' else 'FAIL' end as status,
         present || ' из ' || total || coalesce(' — нет: ' || missing, '') as detail from mig
  -- Версии функций (повторный запуск 08, 09 или 14 после более новых откатывает определения)
  union all select 20, 'create_order делегирует в place_order (11)', case when (select pg_get_functiondef(oid) from pg_proc where pronamespace = 'public'::regnamespace and proname = 'create_order') like '%place_order%' then 'OK' else 'FAIL' end, 'не OK — запускали 08 после 11: выполните 09, 11, 15'
  union all select 21, '_station_orders без аннулированных заказов (11)', case when (select pg_get_functiondef(oid) from pg_proc where pronamespace = 'public'::regnamespace and proname = '_station_orders') like '%voided_at%' then 'OK' else 'FAIL' end, 'не OK — запускали 08 после 11: выполните 09, 11, 15'
  union all select 22, 'station_menu с archived_at (15)', case when (select pg_get_functiondef(oid) from pg_proc where pronamespace = 'public'::regnamespace and proname = 'station_menu') like '%archived_at%' then 'OK' else 'FAIL' end, 'не OK — запускали 09 после 15: выполните 15'
  union all select 23, 'admin_report со снимком категории заказа (15)', case when (select pg_get_functiondef(oid) from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_report') like '%i.category%' then 'OK' else 'FAIL' end, 'не OK — запускали 14 после 15: выполните 15, затем 16'
  union all select 24, 'admin_rename_category со справочником (16)', case when (select prosrc from fn where proname = 'admin_rename_category') like '%menu_categories%' then 'OK' else 'FAIL' end, 'не OK — запускали 14 после 16: выполните 15, затем 16'
  union all select 25, 'Политика menu_select_staff скрывает архивные блюда (15)', case when exists (select 1 from pg_policies where tablename = 'menu_items' and policyname = 'menu_select_staff' and qual like '%archived_at%') then 'OK' else 'FAIL' end, ''
  union all select 26, 'admin_add_menu_item / admin_update_menu_item принимают станцию (09)', case when (select count(*) from pg_proc where pronamespace = 'public'::regnamespace and proname in ('admin_add_menu_item', 'admin_update_menu_item') and pg_get_function_identity_arguments(oid) like '%p_station%') = 2 then 'OK' else 'FAIL' end, 'не OK — запускали 08 после 09: выполните 09, 11, 15'
  -- Ограничения и ключевые свойства схемы
  union all select 30, 'orders_total_parts (total = subtotal + service_amount) проверен', case when exists (select 1 from pg_constraint where conname = 'orders_total_parts' and convalidated) then 'OK' else 'FAIL' end, ''
  union all select 31, 'Не больше одной открытой смены на кассира (уникальный индекс)', case when exists (select 1 from pg_indexes where indexname = 'cash_shifts_one_open_idx' and indexdef ilike '%unique%') then 'OK' else 'FAIL' end, ''
  union all select 32, 'order_items.menu_item_id необязателен, ON DELETE SET NULL (15)',
         case when exists (select 1 from pg_attribute where attrelid = 'public.order_items'::regclass and attname = 'menu_item_id' and not attnotnull)
                   and exists (select 1 from pg_constraint where conrelid = 'public.order_items'::regclass and contype = 'f' and confrelid = 'public.menu_items'::regclass and confdeltype = 'n') then 'OK' else 'FAIL' end, ''
  union all select 33, 'Внешний ключ блюд на категории: ON UPDATE CASCADE, ON DELETE RESTRICT (16)', case when exists (select 1 from pg_constraint where conname = 'menu_items_category_fkey' and confupdtype = 'c' and confdeltype = 'r' and convalidated) then 'OK' else 'FAIL' end, ''
  union all select 34, 'Уникальность категорий без учёта регистра (16)', case when exists (select 1 from pg_indexes where indexname = 'menu_categories_name_ci_key' and indexdef ilike '%unique%' and indexdef ilike '%lower%') then 'OK' else 'FAIL' end, ''
  -- Безопасность: функции
  union all select 40, 'SECURITY DEFINER-функции без пустого search_path', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(proname, ', '), 'нет') from fn where prosecdef and not coalesce(proconfig @> array['search_path=""'], false)
  union all select 41, 'Функции, доступные псевдороли PUBLIC', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(proname, ', '), 'нет') from fn where public_exec > 0
  union all select 42, 'Функции, доступные anon (допустима только employee_login_email — вход по коду)', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(proname, ', '), 'нет') from fn where has_function_privilege('anon', oid, 'EXECUTE') and proname <> 'employee_login_email'
  union all select 43, 'Функции admin_* без проверки администратора внутри (is_active_admin / _staff_admin_only)', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(proname, ', '), 'нет')
         from fn where proname like 'admin\_%' and prosrc !~ 'is_active_admin\(\)' and prosrc !~ '_staff_admin_only\(\)'
  union all select 44, 'Функции admin_* недоступны authenticated без права EXECUTE (должны быть доступны, проверка роли внутри)', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(proname, ', '), 'нет')
         from fn where proname like 'admin\_%' and not has_function_privilege('authenticated', oid, 'EXECUTE')
  union all select 45, 'Владельцы функций (должен быть один владелец проекта)', case when count(distinct owner) = 1 then 'OK' else 'WARN' end, string_agg(distinct owner, ', ') from fn
  union all select 46, 'Триггерные и служебные функции, доступные клиентским ролям (_* и триггеры)', 'INFO',
         coalesce((select string_agg(proname, ', ' order by proname) from fn where proname like '\_%' and has_function_privilege('authenticated', oid, 'EXECUTE')), 'нет')
  -- Безопасность: таблицы
  union all select 50, 'Таблицы public без включённой RLS', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(relname, ', '), 'нет') from tb where not rls
  union all select 51, 'Таблицы public, на которые у anon есть любые права', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(relname, ', '), 'нет')
         from tb where has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
  union all select 52, 'Таблицы public, в которые authenticated может писать напрямую', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(relname, ', '), 'нет')
         from tb where has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
  union all select 53, 'Таблицы public, доступные псевдороли PUBLIC', case when count(*) = 0 then 'OK' else 'FAIL' end, coalesce(string_agg(relname, ', '), 'нет')
         from tb where exists (select 1 from pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a where c.oid = tb.oid and a.grantee = 0)
  union all select 54, 'Таблицы без политик, но читаемые authenticated (RLS закроет, но проверьте)', 'INFO',
         coalesce((select string_agg(relname, ', ') from tb where has_table_privilege('authenticated', oid, 'SELECT') and not exists (select 1 from pg_policies p where p.tablename = tb.relname)), 'нет')
  union all select 55, 'Закрытые таблицы (RLS без политик, нет прав у клиентов)', 'INFO',
         coalesce((select string_agg(relname, ', ' order by relname) from tb where rls and not has_table_privilege('authenticated', oid, 'SELECT') and not exists (select 1 from pg_policies p where p.tablename = tb.relname)), 'нет')
  union all select 56, 'menu_categories: чтение только у authenticated через политику; запись только через RPC', case when
         exists (select 1 from pg_policies where tablename = 'menu_categories' and cmd = 'SELECT' and roles = '{authenticated}')
         and not has_table_privilege('authenticated', 'public.menu_categories', 'INSERT,UPDATE,DELETE') then 'OK' else 'FAIL' end, ''
  union all select 57, 'service_role (серверный ключ; в приложении запрещён) имеет доступ — ожидаемо', 'INFO',
         case when has_table_privilege('service_role', 'public.orders', 'SELECT') then 'да (обходит RLS)' else 'нет' end
  -- Целостность данных
  union all select 60, 'Заказы, где total <> subtotal + service_amount', case when count(*) = 0 then 'OK' else 'FAIL' end, count(*)::text from public.orders where total <> subtotal + service_amount
  union all select 61, 'Заказы без subtotal', case when count(*) = 0 then 'OK' else 'FAIL' end, count(*)::text from public.orders where subtotal is null
  union all select 62, 'Позиции заказов без снимка категории (15)', case when count(*) = 0 then 'OK' else 'FAIL' end, count(*)::text from public.order_items where category is null
  union all select 63, 'Блюда с категорией, которой нет в справочнике (сироты)', case when count(*) = 0 then 'OK' else 'FAIL' end, count(*)::text
         from public.menu_items m where not exists (select 1 from public.menu_categories c where c.name = m.category)
  union all select 64, 'Названия категорий не уникальны без учёта регистра', case when (select count(*) from public.menu_categories) = (select count(distinct lower(name)) from public.menu_categories) then 'OK' else 'FAIL' end, ''
  union all select 65, 'Сотрудники без записи в auth.users', case when count(*) = 0 then 'OK' else 'WARN' end, count(*)::text from public.profiles p where not exists (select 1 from auth.users u where u.id = p.id)
  union all select 66, 'Активных администраторов', case when count(*) >= 1 then 'OK' else 'FAIL' end, count(*)::text from public.profiles where role = 'admin' and is_active
  -- Контроль сохранности: те же формулы, что в предварительной проверке
  union all select 70, 'Контроль: сотрудников', 'INFO', count(*)::text from public.profiles
  union all select 71, 'Контроль: блюд / заказов / позиций заказов', 'INFO',
         (select count(*) from public.menu_items)::text || ' / ' || (select count(*) from public.orders)::text || ' / ' || (select count(*) from public.order_items)::text
  union all select 72, 'Контроль: хеш сотрудников', 'INFO', md5(coalesce((select string_agg(to_jsonb(p)::text, ',' order by id) from public.profiles p), ''))
  union all select 73, 'Контроль: хеш блюд (без category и колонок архива)', 'INFO',
         md5(coalesce((select string_agg((to_jsonb(m) - 'category' - 'archived_at' - 'archived_by')::text, ',' order by id) from public.menu_items m), ''))
  union all select 74, 'Контроль: хеш категорий блюд (меняется только нормализацией 16)', 'INFO',
         md5(coalesce((select string_agg(id || '=' || category, ',' order by id) from public.menu_items), ''))
  union all select 75, 'Контроль: хеш заказов (старые колонки)', 'INFO',
         md5(coalesce((select string_agg((to_jsonb(o) - 'subtotal' - 'service_percent' - 'service_amount' - 'voided_at' - 'voided_by' - 'void_reason' - 'shift_id')::text, ',' order by id) from public.orders o), ''))
  union all select 76, 'Контроль: хеш позиций заказов (старые колонки)', 'INFO',
         md5(coalesce((select string_agg((to_jsonb(i) - 'category')::text, ',' order by id) from public.order_items i), ''))
  union all select 77, 'Контроль: сумма total по заказам', 'INFO', coalesce(sum(total), 0)::text from public.orders
  union all select 78, 'Контроль: удалённых заказов / записей в order_stations', 'INFO',
         (select count(*) from public.orders o where to_jsonb(o) ->> 'deleted_at' is not null)::text || ' / ' ||
         (xpath('/row/c/text()', query_to_xml('select count(*) c from public.order_stations', false, true, '')))[1]::text
) r
order by ord, check_name;
