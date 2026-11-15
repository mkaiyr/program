-- =====================================================================
-- ПРЕДВАРИТЕЛЬНАЯ проверка перед миграциями 11–16 (надбавка и аннулирование, смены, сотрудники, отчёты и категории,
-- архив блюд, справочник категорий). ТОЛЬКО ЧТЕНИЕ: один SELECT, ничего не создаёт, не меняет и не вызывает функций приложения.
-- Безопасно для рабочего проекта; блокирующих операций нет (читает каталог PostgreSQL и несколько агрегатов).
-- Выполнять в Supabase: SQL Editor -> New query -> Run (редактор показывает результат последнего оператора — поэтому один запрос).
--
-- status: OK — норма; INFO — запишите значение; WARN — прочитать и принять решение; STOP — не применять миграции, пока не выяснена причина.
-- Нужны права владельца (роль postgres в SQL Editor): запрос читает схему auth и статистику сессий.
-- Строки 70–78 («Контроль») сохраните: после миграций 11_16_postcheck.sql покажет те же значения для сравнения.
-- =====================================================================
with
-- маркеры объектов по миграциям: kind = col (таблица, колонка) | func | table | trigger | con
markers(mig, kind, a, b) as (values
  (1,'table','profiles',null),(1,'table','menu_items',null),(1,'table','orders',null),(1,'table','order_items',null),(1,'func','is_active_staff',null),
  (2,'func','is_active_admin',null),(2,'func','admin_add_menu_item',null),
  (3,'col','profiles','employee_code'),(3,'func','employee_login_email',null),
  (4,'func','set_order_status',null),
  (5,'func','is_active_kitchen',null),(5,'func','kitchen_orders',null),
  (6,'func','kitchen_menu',null),
  (7,'col','menu_items','code'),
  (8,'table','order_stations',null),(8,'col','menu_items','station'),(8,'func','bar_orders',null),(8,'func','set_station_status',null),
  (9,'table','menu_item_reviews',null),(9,'col','menu_items','approved_at'),(9,'func','station_menu',null),(9,'func','admin_approve_menu_item',null),
  (10,'col','orders','deleted_at'),(10,'func','delete_order',null),(10,'func','admin_purge_order',null),(10,'table','order_purge_log',null),
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
mig as (
  select mig, count(*) as total, count(*) filter (where ok) as present,
         case when count(*) filter (where ok) = count(*) then 'применена'
              when count(*) filter (where ok) = 0 then 'не применена' else 'ЧАСТИЧНО' end as state,
         string_agg(coalesce(a || coalesce('.' || b, ''), ''), ', ' order by a, b) filter (where not ok) as missing
    from marker_state group by mig),
mig_chain as (select mig, state, missing, lag(state) over (order by mig) as prev_state from mig),
-- Auth: что вставляет миграция 13
u_provided(c) as (values ('instance_id'),('id'),('aud'),('role'),('email'),('encrypted_password'),('email_confirmed_at'),('raw_app_meta_data'),
                         ('raw_user_meta_data'),('created_at'),('updated_at'),('confirmation_token'),('recovery_token'),('email_change_token_new'),('email_change')),
i_provided(c) as (values ('id'),('user_id'),('provider_id'),('identity_data'),('provider'),('last_sign_in_at'),('created_at'),('updated_at')),
u_cols as (select * from information_schema.columns where table_schema = 'auth' and table_name = 'users'),
i_cols as (select * from information_schema.columns where table_schema = 'auth' and table_name = 'identities'),
dishes as (select category, btrim(category) as clean, lower(btrim(category)) as key from public.menu_items),
canon as (select distinct on (key) key, clean as canon_name
            from (select lower(btrim(category)) as key, btrim(category) as clean, count(*) as n from public.menu_items group by btrim(category)) q
           order by key, n desc, clean)
select * from (
  select 10 as ord, 'Версия PostgreSQL' as check_name, 'INFO' as status, version() as detail
  union all select 11, 'Текущая роль (нужен владелец проекта, postgres)', 'INFO',
         current_user || ' | суперпользователь: ' || (select rolsuper::text from pg_roles where rolname = current_user)
  union all select 12, 'Расширение pgcrypto в схеме extensions (нужно миграции 13: crypt, gen_salt)',
         case when exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'extensions' and p.proname = 'crypt')
                   and exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'extensions' and p.proname = 'gen_salt') then 'OK'
              when (select state from mig where mig = 13) = 'применена' then 'WARN' else 'STOP' end, ''
  -- Предусловия 01–10
  union all select 20, 'Миграция ' || lpad(mig::text, 2, '0') || ' (предусловие)', case when state = 'применена' then 'OK' else 'STOP' end,
         state || coalesce(' — нет: ' || missing, '') from mig where mig <= 10
  -- Состояние 11–16 и согласованность
  union all select 30, 'Миграция ' || lpad(mig::text, 2, '0') || ' — состояние',
         case when state = 'ЧАСТИЧНО' then 'STOP'
              when state <> 'не применена' and prev_state is distinct from 'применена' then 'STOP' else 'INFO' end,
         state || coalesce(' — нет: ' || missing, '') ||
         case when state <> 'не применена' and prev_state is distinct from 'применена' then ' | применена без предыдущей (' || coalesce(prev_state, '—') || ')' else '' end
    from mig_chain where mig >= 11
  union all select 40, 'create_order делегирует в place_order (11)', 'INFO',
         coalesce((select case when pg_get_functiondef(p.oid) like '%place_order%' then 'да (11 применена)' else 'нет (до 11)' end from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = 'create_order' limit 1), 'нет функции')
  union all select 41, 'admin_report со снимком категории (15)', 'INFO',
         coalesce((select case when pg_get_functiondef(p.oid) like '%i.category%' then 'да' else 'нет' end from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = 'admin_report' limit 1), 'нет функции')
  union all select 42, 'admin_rename_category со справочником (16)', 'INFO',
         coalesce((select case when pg_get_functiondef(p.oid) like '%menu_categories%' then 'да' else 'нет' end from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = 'admin_rename_category' limit 1), 'нет функции')
  -- Схема auth: что нужно миграции 13 (прямая запись в auth.users и auth.identities)
  union all select 50, 'auth.users: все колонки, которые заполняет 13, существуют', case when (select count(*) from u_provided where c not in (select column_name from u_cols)) = 0 then 'OK' else 'STOP' end,
         coalesce((select string_agg(c, ', ') from u_provided where c not in (select column_name from u_cols)), 'все есть')
  union all select 51, 'auth.users: нет обязательных колонок без значения по умолчанию, которых 13 не заполняет',
         case when (select count(*) from u_cols where is_nullable = 'NO' and column_default is null and is_generated = 'NEVER' and is_identity = 'NO' and column_name not in (select c from u_provided)) = 0 then 'OK' else 'STOP' end,
         coalesce((select string_agg(column_name, ', ') from u_cols where is_nullable = 'NO' and column_default is null and is_generated = 'NEVER' and is_identity = 'NO' and column_name not in (select c from u_provided)), 'нет')
  union all select 52, 'auth.users: колонки-токены, допускающие NULL и не заполняемые миграцией 13 (риск для входа созданного сотрудника)',
         case when (select count(*) from u_cols where is_nullable = 'YES' and column_default is null and column_name ~ '(token|change)' and column_name not in (select c from u_provided)) = 0 then 'OK' else 'WARN' end,
         coalesce((select string_agg(column_name, ', ') from u_cols where is_nullable = 'YES' and column_default is null and column_name ~ '(token|change)' and column_name not in (select c from u_provided)), 'нет')
         || ' — обязательно проверьте на тестовом проекте вход сотрудника, созданного через admin_create_staff'
  union all select 53, 'auth.identities: все колонки, которые заполняет 13, существуют', case when (select count(*) from i_provided where c not in (select column_name from i_cols)) = 0 then 'OK' else 'STOP' end,
         coalesce((select string_agg(c, ', ') from i_provided where c not in (select column_name from i_cols)), 'все есть')
  union all select 54, 'auth.identities: нет обязательных колонок без значения по умолчанию, которых 13 не заполняет',
         case when (select count(*) from i_cols where is_nullable = 'NO' and column_default is null and is_generated = 'NEVER' and is_identity = 'NO' and column_name not in (select c from i_provided)) = 0 then 'OK' else 'STOP' end,
         coalesce((select string_agg(column_name, ', ') from i_cols where is_nullable = 'NO' and column_default is null and is_generated = 'NEVER' and is_identity = 'NO' and column_name not in (select c from i_provided)), 'нет')
  union all select 55, 'Сотрудники без записи в auth.users (сироты)', case when count(*) = 0 then 'OK' else 'WARN' end, count(*)::text
         from public.profiles p where not exists (select 1 from auth.users u where u.id = p.id)
  union all select 56, 'Активных администраторов (нужен минимум один)', case when count(*) >= 1 then 'OK' else 'STOP' end, count(*)::text from public.profiles where role = 'admin' and is_active
  union all select 57, 'Сотрудники по ролям', 'INFO',
         coalesce((select string_agg(role || '=' || n, ', ' order by role) from (select role, count(*) n from public.profiles group by role) r), 'нет сотрудников')
  -- Данные: возможные конфликты
  union all select 60, 'Старые заказы без надбавки: после 11 будут иметь subtotal = total и надбавку 0', 'INFO',
         case when (select state from mig where mig = 11) = 'не применена' then (select count(*)::text from public.orders) || ' заказов' else 'миграция 11 уже применена' end
  union all select 61, 'Блюд: категорий (точные написания) / (без регистра и пробелов по краям)', 'INFO',
         count(distinct category)::text || ' / ' || count(distinct key)::text from dishes
  union all select 62, 'Блюд с пустой категорией или длиннее 50 символов (16 не сможет перенести)', case when count(*) = 0 then 'OK' else 'STOP' end, count(*)::text
         from dishes where clean is null or clean = '' or length(clean) > 50
  union all select 63, 'Блюд, чьё написание категории миграция 16 приведёт к единому (регистр / пробелы)', case when count(*) = 0 then 'OK' else 'WARN' end, count(*)::text
         from public.menu_items m join canon c on c.key = lower(btrim(m.category)) where m.category <> c.canon_name
  union all select 64, 'Категория, написанная по-разному: ' || v.key, 'WARN', v.spellings || ' → будет «' || c.canon_name || '»'
         from (select key, string_agg(distinct '«' || category || '»', ', ') as spellings, count(distinct category) as n_raw from dishes group by key) v
         join canon c using (key) where v.n_raw > 1
  -- Контроль сохранности (после миграций те же запросы дадут те же значения)
  union all select 70, 'Контроль: сотрудников', 'INFO', count(*)::text from public.profiles
  union all select 71, 'Контроль: блюд / заказов / позиций заказов', 'INFO',
         (select count(*) from public.menu_items)::text || ' / ' || (select count(*) from public.orders)::text || ' / ' || (select count(*) from public.order_items)::text
  union all select 72, 'Контроль: хеш сотрудников', 'INFO', md5(coalesce((select string_agg(to_jsonb(p)::text, ',' order by id) from public.profiles p), ''))
  union all select 73, 'Контроль: хеш блюд (без category и колонок архива)', 'INFO',
         md5(coalesce((select string_agg((to_jsonb(m) - 'category' - 'archived_at' - 'archived_by')::text, ',' order by id) from public.menu_items m), ''))
  union all select 74, 'Контроль: хеш категорий блюд (изменится только при нормализации 16)', 'INFO',
         md5(coalesce((select string_agg(id || '=' || category, ',' order by id) from public.menu_items), ''))
  union all select 75, 'Контроль: хеш заказов (старые колонки)', 'INFO',
         md5(coalesce((select string_agg((to_jsonb(o) - 'subtotal' - 'service_percent' - 'service_amount' - 'voided_at' - 'voided_by' - 'void_reason' - 'shift_id')::text, ',' order by id) from public.orders o), ''))
  union all select 76, 'Контроль: хеш позиций заказов (старые колонки)', 'INFO',
         md5(coalesce((select string_agg((to_jsonb(i) - 'category')::text, ',' order by id) from public.order_items i), ''))
  union all select 77, 'Контроль: сумма total по заказам', 'INFO', coalesce(sum(total), 0)::text from public.orders
  union all select 78, 'Контроль: удалённых заказов / записей в order_stations', 'INFO',
         (select count(*) from public.orders o where to_jsonb(o) ->> 'deleted_at' is not null)::text || ' / ' ||
         case when to_regclass('public.order_stations') is null then 'нет таблицы'
              else (xpath('/row/c/text()', query_to_xml('select count(*) c from public.order_stations', false, true, '')))[1]::text end
  -- Окружение
  union all select 80, 'Открытые транзакции других сессий (не idle)', 'INFO',
         (select count(*)::text from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid() and state in ('active', 'idle in transaction'))
) r
order by ord, check_name;
