-- =====================================================================
-- POST-CHECK миграции 18. ТОЛЬКО ЧТЕНИЕ: один SELECT, ничего не создаёт и не меняет.
-- status: OK — норма; FAIL — проблема. Последняя строка — итог.
-- =====================================================================
with
tabs(t) as (values ('media_assets'), ('menu_category_public'), ('menu_item_public'), ('public_slug_reservations'), ('allergens'), ('ingredients'), ('ingredient_allergens'), ('menu_item_ingredients')),
internal_fn(n) as (values ('_touch_updated_at'), ('_slug_guard'), ('_slug_reserve'), ('_require_admin'), ('_json_text'), ('_json_int'), ('_json_bool'), ('_public_blockers'),
                          ('_save_item_public'), ('_set_item_ingredients'), ('_confirm_price'), ('_publish'), ('_set_photo')),
admin_fn(n) as (values ('admin_set_category_public'), ('admin_list_category_public'), ('admin_set_menu_item_public'), ('admin_set_menu_item_ingredients'), ('admin_save_allergen'),
                       ('admin_save_ingredient'), ('admin_confirm_menu_price'), ('admin_publish_menu_item'), ('admin_save_dish_public'), ('admin_release_slug'),
                       ('admin_register_media'), ('admin_set_dish_photo'), ('admin_media_list'), ('admin_delete_media'), ('admin_catalog_diagnostics')),
invoker_ok(n) as (values ('_touch_updated_at'), ('_json_text'), ('_json_int'), ('_json_bool')),   -- не SECURITY DEFINER по замыслу
checks as (
  select 1 as ord, 'таблицы созданы (8)' as chk, (select count(*) from tabs where to_regclass('public.' || t) is not null) = 8 as ok, (select count(*) from tabs where to_regclass('public.' || t) is not null)::text as detail
  union all select 2, 'RLS включён на всех новых таблицах', (select count(*) from tabs join pg_class c on c.oid = to_regclass('public.' || t) where c.relrowsecurity) = 8, ''
  union all select 3, 'политик на новых таблицах нет', (select count(*) from pg_policies where schemaname = 'public' and tablename in (select t from tabs)) = 0, ''
  union all select 4, 'нет прав у anon/authenticated/PUBLIC на новых таблицах',
         not exists (select 1 from tabs where has_table_privilege('anon', 'public.' || t, 'select,insert,update,delete,truncate,references,trigger')
                                           or has_table_privilege('authenticated', 'public.' || t, 'select,insert,update,delete,truncate,references,trigger')
                                           or exists (select 1 from pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a where c.oid = to_regclass('public.' || t) and a.grantee = 0)), ''
  union all select 5, 'нет прав на последовательность media_assets_id_seq у клиентов',
         not has_sequence_privilege('anon', 'public.media_assets_id_seq', 'usage,select,update') and not has_sequence_privilege('authenticated', 'public.media_assets_id_seq', 'usage,select,update')
         and not exists (select 1 from pg_class c, aclexplode(coalesce(c.relacl, acldefault('S', c.relowner))) a where c.oid = 'public.media_assets_id_seq'::regclass and a.grantee = 0), ''
  union all select 6, 'menu_items.updated_at (not null, default now())',
         exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'menu_items' and column_name = 'updated_at' and is_nullable = 'NO' and column_default like 'now()%'), ''
  union all select 7, 'триггер menu_items_touch включён', exists (select 1 from pg_trigger where tgname = 'menu_items_touch' and tgrelid = 'public.menu_items'::regclass and tgenabled = 'O'), ''
  union all select 8, 'шесть триггеров слагов включены (по 3 на таблицу: guard, touch, reserve)',
         (select count(*) from pg_trigger where tgrelid in ('public.menu_category_public'::regclass, 'public.menu_item_public'::regclass) and not tgisinternal and tgenabled = 'O') = 6, ''
  union all select 9, 'ограничения: пары веса и подтверждения, уникальность слагов',
         (select count(*) from pg_constraint where conname in ('menu_item_public_weight_pair', 'menu_item_public_confirm_pair')) = 2
         and exists (select 1 from pg_constraint where conrelid = 'public.menu_item_public'::regclass and contype = 'u' and pg_get_constraintdef(oid) = 'UNIQUE (slug)')
         and exists (select 1 from pg_constraint where conrelid = 'public.menu_category_public'::regclass and contype = 'u' and pg_get_constraintdef(oid) = 'UNIQUE (slug)')
         and exists (select 1 from pg_constraint where conrelid = 'public.media_assets'::regclass and contype = 'u' and pg_get_constraintdef(oid) = 'UNIQUE (storage, path)'), ''
  union all select 10, 'внешние ключи: каскады и запрет удаления используемого медиа',
         exists (select 1 from pg_constraint where conrelid = 'public.menu_item_public'::regclass and contype = 'f' and confrelid = 'public.media_assets'::regclass and confdeltype = 'r')
         and exists (select 1 from pg_constraint where conrelid = 'public.menu_item_public'::regclass and contype = 'f' and confrelid = 'public.menu_items'::regclass and confdeltype = 'c')
         and exists (select 1 from pg_constraint where conrelid = 'public.menu_category_public'::regclass and contype = 'f' and confrelid = 'public.menu_categories'::regclass and confdeltype = 'c')
         and exists (select 1 from pg_constraint where conrelid = 'public.menu_item_ingredients'::regclass and contype = 'f' and confrelid = 'public.menu_items'::regclass and confdeltype = 'c'), ''
  union all select 11, 'индекс menu_item_public_photo_idx', to_regclass('public.menu_item_public_photo_idx') is not null, ''
  union all select 12, 'все 29 функций созданы',
         (select count(*) from pg_proc p where p.pronamespace = 'public'::regnamespace and (p.proname in (select n from internal_fn union select n from admin_fn) or p.proname = 'public_menu')) = 29, ''
  union all select 13, 'у всех новых функций search_path = пусто',
         (select count(*) from pg_proc p where p.pronamespace = 'public'::regnamespace and (p.proname in (select n from internal_fn union select n from admin_fn) or p.proname = 'public_menu')
                and exists (select 1 from unnest(coalesce(p.proconfig, '{}')) cfg where cfg in ('search_path=""', 'search_path=')) ) = 29, ''
  union all select 14, 'SECURITY DEFINER у всех новых функций, кроме четырёх чистых помощников',
         (select count(*) from pg_proc p where p.pronamespace = 'public'::regnamespace and (p.proname in (select n from internal_fn union select n from admin_fn) or p.proname = 'public_menu')
                and p.prosecdef = (p.proname not in (select n from invoker_ok))) = 29, ''
  union all select 15, 'внутренние функции недоступны anon/authenticated/PUBLIC',
         not exists (select 1 from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname in (select n from internal_fn)
                       and (has_function_privilege('anon', p.oid, 'execute') or has_function_privilege('authenticated', p.oid, 'execute')
                            or exists (select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where a.grantee = 0))), ''
  union all select 16, 'admin_*: authenticated — да; anon и PUBLIC — нет',
         not exists (select 1 from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname in (select n from admin_fn)
                       and (has_function_privilege('anon', p.oid, 'execute') or not has_function_privilege('authenticated', p.oid, 'execute')
                            or exists (select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where a.grantee = 0))), ''
  union all select 17, 'public_menu: anon и authenticated — да; PUBLIC — нет',
         has_function_privilege('anon', 'public.public_menu()', 'execute') and has_function_privilege('authenticated', 'public.public_menu()', 'execute')
         and not exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where p.oid = 'public.public_menu()'::regprocedure and a.grantee = 0), ''
  union all select 18, 'anon выполняет только employee_login_email и public_menu',
         (select string_agg(p.proname, ',' order by p.proname) from pg_proc p where p.pronamespace = 'public'::regnamespace and has_function_privilege('anon', p.oid, 'execute')) = 'employee_login_email,public_menu', ''
  union all select 19, 'ни одна функция public не доступна PUBLIC',
         not exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where p.pronamespace = 'public'::regnamespace and a.grantee = 0), ''
  union all select 20, 'итого объектов public: таблиц 19, функций 92, триггеров 15',
         (select count(*) from pg_tables where schemaname = 'public') = 19 and (select count(*) from pg_proc where pronamespace = 'public'::regnamespace) = 92
         and (select count(*) from pg_trigger t join pg_class c on c.oid = t.tgrelid where c.relnamespace = 'public'::regnamespace and not t.tgisinternal) = 15,
         (select count(*) from pg_tables where schemaname = 'public') || ' / ' || (select count(*) from pg_proc where pronamespace = 'public'::regnamespace) || ' / '
         || (select count(*) from pg_trigger t join pg_class c on c.oid = t.tgrelid where c.relnamespace = 'public'::regnamespace and not t.tgisinternal)
  union all select 21, 'новые таблицы пусты (данные не создавались)',
         (select count(*) from public.media_assets) + (select count(*) from public.menu_category_public) + (select count(*) from public.menu_item_public) + (select count(*) from public.public_slug_reservations)
         + (select count(*) from public.allergens) + (select count(*) from public.ingredients) + (select count(*) from public.ingredient_allergens) + (select count(*) from public.menu_item_ingredients) = 0, ''
  union all select 22, 'прежние объекты на месте: функции 01–16 (63) и их права не изменились',
         (select count(*) from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname not in (select n from internal_fn union select n from admin_fn) and p.proname <> 'public_menu') = 63, ''
)
select result from (
  select ord, case when ok then 'OK  ' else 'FAIL' end || ' | ' || chk || case when detail <> '' then ' | ' || detail else '' end as result from checks
  union all
  select 99, 'ИТОГ: ' || case when count(*) filter (where not ok) = 0 then 'все проверки post-check пройдены (' || count(*) || ')' else 'ПРОБЛЕМ: ' || count(*) filter (where not ok) end from checks
) x order by ord;
