-- =====================================================================
-- ПРЕДВАРИТЕЛЬНАЯ проверка перед миграцией 16 (справочник категорий меню)
-- ТОЛЬКО ЧТЕНИЕ: один SELECT, ничего не создаёт и не меняет. Безопасно запускать на рабочем проекте.
-- Выполнять в Supabase: SQL Editor -> New query -> Run. Редактор показывает результат последнего оператора,
-- поэтому здесь один запрос. Колонка status: OK — можно продолжать; STOP — не применять 16, пока не исправлено;
-- INFO — запишите значение (сравните с проверкой после миграции); WARN — прочитать и принять решение.
-- Скопируйте ВЕСЬ результат в заметки: строки INFO нужны для сравнения «до / после».
-- =====================================================================
with
req_tables(name) as (values ('menu_items'), ('orders'), ('order_items'), ('profiles'), ('menu_item_purge_log'),
                            ('menu_item_reviews'), ('cash_shifts')),
req_funcs(name) as (values ('is_active_admin'), ('is_active_staff'), ('place_order'), ('station_menu'), ('station_add_menu_item'),
                           ('admin_add_menu_item'), ('admin_update_menu_item'), ('admin_archive_menu_item'),
                           ('admin_restore_menu_item'), ('admin_purge_menu_item'), ('admin_rename_category'), ('admin_report')),
dishes as (select id, category, btrim(category) as clean, lower(btrim(category)) as key from public.menu_items),
variants as (select key, count(distinct clean) as n_spellings, count(distinct category) as n_raw,
                    string_agg(distinct '«' || category || '»', ', ') as spellings
               from dishes group by key),
canon as (select distinct on (key) key, clean as canon_name
            from (select lower(btrim(category)) as key, btrim(category) as clean, count(*) as n
                    from public.menu_items group by btrim(category)) q order by key, n desc, clean)
select * from (
  -- 1. Предусловия: применены 01–15
  select 10 as ord, 'Версия PostgreSQL' as check_name, 'INFO' as status, version() as detail
  union all select 11, 'Текущая роль', 'INFO', current_user || ' (суперпользователь: ' || (select rolsuper::text from pg_roles where rolname = current_user) || ')'
  union all select 20, 'Таблица ' || t.name, case when to_regclass('public.' || t.name) is not null then 'OK' else 'STOP' end,
                      case when to_regclass('public.' || t.name) is not null then 'есть' else 'нет — сначала выполните миграции 01–15' end
              from req_tables t
  union all select 30, 'Функция ' || f.name || '()', case when exists (select 1 from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = f.name) then 'OK' else 'STOP' end,
                      case when exists (select 1 from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = f.name) then 'есть' else 'нет — выполните предыдущие миграции' end
              from req_funcs f
  union all select 40, 'Колонка menu_items.archived_at (миграция 15)',
                      case when exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'menu_items' and column_name = 'archived_at') then 'OK' else 'STOP' end, ''
  union all select 41, 'Колонка order_items.category — снимок (миграция 15)',
                      case when exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'order_items' and column_name = 'category') then 'OK' else 'STOP' end, ''
  union all select 42, 'admin_report из миграции 15 (снимок категории в отчёте)',
                      case when exists (select 1 from pg_proc p where p.proname = 'admin_report' and pg_get_functiondef(p.oid) like '%i.category%') then 'OK' else 'WARN' end,
                      'не OK — после повторного запуска 14 нужно заново выполнить 15'
  union all select 43, 'station_menu из миграции 15 (поле archived_at)',
                      case when exists (select 1 from pg_proc p where p.proname = 'station_menu' and pg_get_functiondef(p.oid) like '%archived_at%') then 'OK' else 'WARN' end,
                      'не OK — после повторного запуска 09 нужно заново выполнить 15'
  -- 2. Миграция 16 уже применена?
  union all select 50, 'Миграция 16 уже применена?', 'INFO',
                      case when to_regclass('public.menu_categories') is null then 'нет (ожидаемо)' else 'ДА — таблица menu_categories уже есть; повторный запуск безопасен, но проверьте причину' end
  union all select 51, 'Нет постороннего объекта с таким же именем', case when to_regclass('public.menu_categories') is null
                      or exists (select 1 from pg_attribute where attrelid = to_regclass('public.menu_categories') and attname = 'created_by') then 'OK' else 'STOP' end,
                      'не OK — в public уже есть чужая таблица menu_categories'
  union all select 52, 'Нет функций admin_add_category / admin_delete_category с другой сигнатурой',
                      case when not exists (select 1 from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname in ('admin_add_category', 'admin_delete_category')
                                            and pg_get_function_identity_arguments(p.oid) <> 'p_name text') then 'OK' else 'STOP' end, ''
  -- 3. Данные: конфликты при переносе категорий
  union all select 60, 'Блюд в меню (всего)', 'INFO', count(*)::text from public.menu_items
  union all select 61, 'Категорий (точные написания)', 'INFO', count(distinct category)::text from public.menu_items
  union all select 62, 'Категорий (без учёта регистра и пробелов по краям) — столько будет в справочнике', 'INFO', count(distinct key)::text from dishes
  union all select 63, 'Блюд с пустой категорией', case when count(*) = 0 then 'OK' else 'STOP' end, count(*)::text from dishes where clean is null or clean = ''
  union all select 64, 'Категорий длиннее 50 символов после обрезки пробелов', case when count(*) = 0 then 'OK' else 'STOP' end, count(*)::text from dishes where length(clean) > 50
  union all select 65, 'Блюд, чьё написание категории миграция приведёт к единому (регистр / пробелы)',
                      case when count(*) = 0 then 'OK' else 'WARN' end,
                      count(*)::text || ' — это нормально, но запишите число: после миграции NOTICE должен показать то же'
              from public.menu_items m join canon c on c.key = lower(btrim(m.category)) where m.category <> c.canon_name
  union all select 66, 'Категория, написанная по-разному: ' || v.key, 'WARN', v.spellings || ' → будет «' || c.canon_name || '»'
              from variants v join canon c using (key) where v.n_raw > 1
  -- 4. Контроль сохранности: запишите и сравните после миграции
  union all select 70, 'Контроль: заказов', 'INFO', count(*)::text from public.orders
  union all select 71, 'Контроль: позиций заказов', 'INFO', count(*)::text from public.order_items
  union all select 72, 'Контроль: блюд в архиве', 'INFO', count(*)::text from public.menu_items where archived_at is not null
  union all select 73, 'Контроль: сотрудников / активных администраторов', case when count(*) filter (where role = 'admin' and is_active) >= 1 then 'OK' else 'STOP' end,
                      count(*)::text || ' / ' || count(*) filter (where role = 'admin' and is_active)::text from public.profiles
  union all select 74, 'Контроль: хеш блюд (кроме категории)', 'INFO',
                      md5(coalesce((select string_agg(concat_ws('|', id, name, price, is_available, archived_at is not null, station, code), ',' order by id) from public.menu_items), ''))
  union all select 75, 'Контроль: хеш заказов и позиций (с категорией-снимком)', 'INFO',
                      md5(coalesce((select string_agg(t::text, ',' order by id) from public.orders t), '') ||
                          coalesce((select string_agg(concat_ws('|', i.id, i.item_name, i.unit_price, i.quantity, i.line_total, i.category, i.menu_item_id), ',' order by i.id) from public.order_items i), ''))
  -- 5. Окружение
  union all select 80, 'Триггеры на menu_items сейчас', 'INFO', coalesce((select string_agg(tgname, ', ') from pg_trigger where tgrelid = 'public.menu_items'::regclass and not tgisinternal), 'нет')
  union all select 81, 'Открытые транзакции других сессий (pg_stat_activity, не idle)', 'INFO',
                      (select count(*)::text from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid() and state in ('active', 'idle in transaction'))
) r
order by ord, check_name;
