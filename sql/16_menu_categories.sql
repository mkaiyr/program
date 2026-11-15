-- =====================================================================
-- Этап 18. Категории меню как справочник (таблица menu_categories)
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–15. Одна транзакция: при ошибке или остановке не меняется ничего.
-- Повторный запуск безопасен: данные не меняются, определения те же.
--
-- Модель:
--   * menu_categories — справочник: id, name (уникально без учёта регистра), created_at, created_by.
--   * menu_items.category ОСТАЁТСЯ текстовой колонкой. На неё опираются отчёты (admin_report), кухня и бар
--     (station_menu, kitchen_menu), история заказов, архив и все версии RPC 02/07/08/09 — они не меняются.
--     Связь с справочником — внешний ключ menu_items.category -> menu_categories.name:
--       ON UPDATE CASCADE  — переименование категории само обновляет блюда (в том числе архивные);
--       ON DELETE RESTRICT — категорию с блюдами (в том числе архивными) удалить нельзя.
--     order_items.category — снимок на момент заказа, без связи: история не меняется при переименовании.
--   * Существующие категории переносятся из menu_items. Если одна и та же категория написана по-разному
--     (регистр, пробелы по краям), берётся самое частое написание, остальные приводятся к нему:
--     иначе уникальность без учёта регистра невозможна. Число изменённых блюд выводится в NOTICE.
--   * Триггер menu_items_category_ref (BEFORE INSERT/UPDATE OF category) приводит название к написанию
--     из справочника (супы -> Супы). Если категории нет: активный администратор или прямой SQL
--     (auth.uid() не задан) — категория создаётся; кассир, кухня и бар получают CATEGORY_NOT_FOUND
--     (предложить блюдо можно только в существующей категории).
--   * admin_add_category / admin_rename_category / admin_delete_category — только активный администратор;
--     остальные роли получают ADMIN_REQUIRED. У клиентов нет прав INSERT/UPDATE/DELETE на таблицу.
--     Читать справочник могут активные кассир и администратор (политика menu_categories_select).
--   * Прямой SQL (роль postgres / SQL Editor): insert into public.menu_categories (name) values ('Салаты');
--     update public.menu_categories set name = 'Новое' where name = 'Старое'; — приложение показывает
--     изменения после обновления данных (кнопка «Обновить» или повторный вход).
--
-- Эта миграция переопределяет admin_rename_category из 14. Повторный запуск 14 вернёт прежний текст:
-- после него запустить эту миграцию снова.
-- =====================================================================

begin;

-- ---------- Предусловие: применены 01–15 ----------

do $$
declare
  v_missing text[] := '{}';
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'menu_items' and column_name = 'archived_at')
  then v_missing := array_append(v_missing, 'menu_items.archived_at (15)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_archive_menu_item')
  then v_missing := array_append(v_missing, 'admin_archive_menu_item() (15)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_rename_category')
  then v_missing := array_append(v_missing, 'admin_rename_category() (14)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_admin')
  then v_missing := array_append(v_missing, 'is_active_admin() (02)'); end if;
  if array_length(v_missing, 1) > 0 then
    raise exception 'Сначала выполните миграции 01–15. Не найдено: %', array_to_string(v_missing, ', ');
  end if;
end $$;

-- ---------- Таблица ----------

create table if not exists public.menu_categories (
  id         bigint generated always as identity primary key,
  name       text not null,
  created_at timestamptz not null default now(),
  created_by uuid references auth.users (id) on delete set null,
  constraint menu_categories_name_key unique (name),
  constraint menu_categories_name_check check (name = btrim(name) and length(name) between 1 and 50)
);

create unique index if not exists menu_categories_name_ci_key on public.menu_categories (lower(name));

-- ---------- Перенос существующих категорий (без потери блюд) ----------

do $$
declare
  v_fixed integer;
begin
  -- Приводим написание к самому частому в группе (регистр и пробелы по краям); тот же ключ, что у индекса
  with variants as (
    select btrim(category) as name, lower(btrim(category)) as key, count(*) as n
      from public.menu_items group by btrim(category)
  ), canon as (
    select distinct on (key) key, name from variants order by key, n desc, name
  )
  update public.menu_items m set category = c.name
    from canon c
   where lower(btrim(m.category)) = c.key and m.category <> c.name;
  get diagnostics v_fixed = row_count;
  if v_fixed > 0 then
    raise notice 'Категории: приведено к единому написанию блюд — %', v_fixed;
  end if;
end $$;

insert into public.menu_categories (name)
select distinct category from public.menu_items
on conflict do nothing;

-- ---------- Связь блюд со справочником ----------

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'menu_items_category_fkey' and conrelid = 'public.menu_items'::regclass) then
    alter table public.menu_items
      add constraint menu_items_category_fkey foreign key (category)
      references public.menu_categories (name) on update cascade on delete restrict;
  end if;
end $$;

create index if not exists menu_items_category_idx on public.menu_items (category);

-- Название приводится к написанию из справочника; неизвестную категорию создаёт только администратор или прямой SQL
create or replace function public.menu_items_category_ref()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name text;
  v_clean text := btrim(new.category);
begin
  select c.name into v_name from public.menu_categories c where lower(c.name) = lower(v_clean);
  if v_name is not null then
    new.category := v_name;
    return new;
  end if;
  if auth.uid() is not null and not public.is_active_admin() then
    raise exception using errcode = '23503', message = 'CATEGORY_NOT_FOUND';
  end if;
  if length(v_clean) not between 1 and 50 then
    raise exception using errcode = '22023', message = 'INVALID_CATEGORY';
  end if;
  insert into public.menu_categories (name, created_by) values (v_clean, auth.uid());
  new.category := v_clean;
  return new;
end;
$$;

revoke all on function public.menu_items_category_ref() from public, anon, authenticated;

drop trigger if exists menu_items_category_ref on public.menu_items;
create trigger menu_items_category_ref
  before insert or update of category on public.menu_items
  for each row execute function public.menu_items_category_ref();

-- ---------- Права: чтение — кассир и администратор, запись — только функции ----------

revoke all on public.menu_categories from anon, authenticated;
grant select on public.menu_categories to authenticated;
alter table public.menu_categories enable row level security;

drop policy if exists menu_categories_select on public.menu_categories;
create policy menu_categories_select on public.menu_categories
  for select to authenticated
  using ((select public.is_active_staff()));

-- ---------- Добавление ----------
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_CATEGORY, CATEGORY_EXISTS. Возвращает {id, name}.

create or replace function public.admin_add_category(p_name text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name text := btrim(coalesce(p_name, ''));
  v_id   bigint;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  if length(v_name) not between 1 and 50 then
    raise exception using errcode = '22023', message = 'INVALID_CATEGORY';
  end if;
  begin
    insert into public.menu_categories (name, created_by) values (v_name, auth.uid()) returning id into v_id;
  exception when unique_violation then
    raise exception using errcode = '23505', message = 'CATEGORY_EXISTS';
  end;
  return jsonb_build_object('id', v_id, 'name', v_name);
end;
$$;

revoke all on function public.admin_add_category(text) from public, anon;
grant execute on function public.admin_add_category(text) to authenticated;

-- ---------- Переименование и объединение ----------
-- Новое имя свободно — категория переименовывается (блюда обновляет внешний ключ). Такое имя уже есть
-- (без учёта регистра, кроме смены только регистра самой категории) — блюда переносятся в существующую
-- категорию, а старая удаляется. Ошибки: ADMIN_REQUIRED, INVALID_CATEGORY, NOT_FOUND.
-- Возвращает {updated: число блюд, category: итоговое имя}.

create or replace function public.admin_rename_category(p_old text, p_new text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_old   text := btrim(coalesce(p_old, ''));
  v_new   text := btrim(coalesce(p_new, ''));
  v_old_id bigint;
  v_target text;
  v_n     integer;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  if length(v_new) not between 1 and 50 or length(v_old) not between 1 and 50 then
    raise exception using errcode = '22023', message = 'INVALID_CATEGORY';
  end if;
  select id into v_old_id from public.menu_categories where name = v_old for update;
  if v_old_id is null then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  select count(*) into v_n from public.menu_items where category = v_old;
  if v_old = v_new then
    return jsonb_build_object('updated', 0, 'category', v_new);
  end if;

  select name into v_target from public.menu_categories where lower(name) = lower(v_new) and id <> v_old_id;
  if v_target is null then
    update public.menu_categories set name = v_new where id = v_old_id;   -- блюда обновит ON UPDATE CASCADE
    return jsonb_build_object('updated', v_n, 'category', v_new);
  end if;
  update public.menu_items set category = v_target where category = v_old;
  delete from public.menu_categories where id = v_old_id;
  return jsonb_build_object('updated', v_n, 'category', v_target);
end;
$$;

revoke all on function public.admin_rename_category(text, text) from public, anon;
grant execute on function public.admin_rename_category(text, text) to authenticated;

-- ---------- Удаление ----------
-- Только пустая категория: блюда, в том числе архивные, блокируют удаление. Ошибки: ADMIN_REQUIRED,
-- INVALID_CATEGORY, NOT_FOUND, CATEGORY_NOT_EMPTY (в details — «всего;в архиве»). Возвращает {name}.

create or replace function public.admin_delete_category(p_name text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name  text := btrim(coalesce(p_name, ''));
  v_id    bigint;
  v_total integer;
  v_arch  integer;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  if length(v_name) not between 1 and 50 then
    raise exception using errcode = '22023', message = 'INVALID_CATEGORY';
  end if;
  select id into v_id from public.menu_categories where name = v_name for update;
  if v_id is null then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  select count(*)::integer, (count(*) filter (where archived_at is not null))::integer
    into v_total, v_arch from public.menu_items where category = v_name;
  if v_total > 0 then
    raise exception using errcode = '23503', message = 'CATEGORY_NOT_EMPTY', detail = v_total || ';' || v_arch;
  end if;
  begin
    delete from public.menu_categories where id = v_id;
  exception when foreign_key_violation then   -- блюдо добавили между проверкой и удалением
    raise exception using errcode = '23503', message = 'CATEGORY_NOT_EMPTY', detail = '1;0';
  end;
  return jsonb_build_object('name', v_name);
end;
$$;

revoke all on function public.admin_delete_category(text) from public, anon;
grant execute on function public.admin_delete_category(text) to authenticated;

notify pgrst, 'reload schema';

commit;
