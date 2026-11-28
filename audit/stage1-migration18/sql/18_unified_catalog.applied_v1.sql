-- =====================================================================
-- 18. Единый каталог: публичные данные блюд и категорий, стабильные слаги, медиа, состав и аллергены, публичное меню
-- Рабочая копия для изолированной базы cashier_catalog_dev (НЕ для защищённых баз и НЕ в репозиторий без отдельного разрешения).
-- Зависимости: 01–16 (menu_items, menu_categories, is_active_admin). Миграции 01–16 не меняются.
-- Область: каталог для сайта. Склад, заказы, персональные данные, Storage-бакеты сюда НЕ входят.
-- Принципы: единственный источник блюда — menu_items; таблицы расширения без прав у клиентов (RLS без политик, REVOKE);
--   чтение и запись только функциями SECURITY DEFINER с search_path = ''; анонимно доступна только public_menu();
--   публикация только при подтверждённой цене; слаги неизменяемы и резервируются навсегда (повторно — только через admin_release_slug).
-- Права: REVOKE для PUBLIC, anon, authenticated на все новые таблицы, последовательности и функции, затем точечный GRANT.
--   service_role не отзывается (платформенный контур, как в 01–16; ключ только на сервере).
-- =====================================================================
begin;

-- ---------- Предусловия ----------
do $$
declare
  v_missing text[] := '{}';
  v_taken text[];
begin
  if to_regclass('public.menu_items') is null then v_missing := v_missing || 'menu_items'; end if;
  if to_regclass('public.menu_categories') is null then v_missing := v_missing || 'menu_categories (16)'; end if;
  if to_regclass('public.menu_item_purge_log') is null then v_missing := v_missing || 'menu_item_purge_log (15)'; end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'is_active_admin') then v_missing := v_missing || 'is_active_admin()'; end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_purge_menu_item') then v_missing := v_missing || 'admin_purge_menu_item (15)'; end if;
  if not exists (select 1 from pg_trigger where tgname = 'menu_items_category_ref') then v_missing := v_missing || 'триггер menu_items_category_ref (16)'; end if;
  if to_regnamespace('auth') is null or to_regprocedure('auth.uid()') is null then v_missing := v_missing || 'auth.uid()'; end if;
  if v_missing <> '{}' then
    raise exception 'ОСТАНОВЛЕНО: нет зависимостей 01–16: %', array_to_string(v_missing, ', ');
  end if;

  select array_agg(x) into v_taken from unnest(array['media_assets', 'menu_category_public', 'menu_item_public', 'public_slug_reservations', 'allergens',
                                                       'ingredients', 'ingredient_allergens', 'menu_item_ingredients']) x where to_regclass('public.' || x) is not null;
  if v_taken is not null then
    raise exception 'ОСТАНОВЛЕНО: миграция 18 уже применена (или применена частично): таблицы %', array_to_string(v_taken, ', ');
  end if;
  select array_agg(proname::text) into v_taken from pg_proc
   where pronamespace = 'public'::regnamespace
     and proname in ('_touch_updated_at', '_slug_guard', '_slug_reserve', '_require_admin', '_json_text', '_json_int', '_json_bool', '_public_blockers',
                     '_save_item_public', '_set_item_ingredients', '_confirm_price', '_publish', '_set_photo', 'public_menu',
                     'admin_set_category_public', 'admin_list_category_public', 'admin_set_menu_item_public', 'admin_set_menu_item_ingredients',
                     'admin_save_allergen', 'admin_save_ingredient', 'admin_confirm_menu_price', 'admin_publish_menu_item', 'admin_save_dish_public',
                     'admin_release_slug', 'admin_register_media', 'admin_set_dish_photo', 'admin_media_list', 'admin_delete_media', 'admin_catalog_diagnostics');
  if v_taken is not null then
    raise exception 'ОСТАНОВЛЕНО: имена функций уже заняты: %', array_to_string(v_taken, ', ');
  end if;
  if exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'menu_items' and column_name = 'updated_at') then
    raise exception 'ОСТАНОВЛЕНО: menu_items.updated_at уже существует';
  end if;
end;
$$;

-- ---------- menu_items: отметка времени изменения ----------
alter table public.menu_items add column updated_at timestamptz not null default now();

create function public._touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at := now();
  return new;
end;
$$;
create trigger menu_items_touch before update on public.menu_items
  for each row execute function public._touch_updated_at();

-- ---------- Медиа ----------
-- storage = 'repo': файл лежит в репозитории сайта (существующие фотографии, не перемещаются и не удаляются);
-- storage = 'storage': файл в бакете Storage (загрузка администратором; бакет и его политики — только на staging, вне этой миграции).
create table public.media_assets (
  id          bigint generated always as identity primary key,
  storage     text not null check (storage in ('repo', 'storage')),
  path        text not null check (length(path) <= 200 and path ~ '^[a-z0-9][a-z0-9_./-]*\.(webp|jpg|jpeg|png)$' and position('..' in path) = 0 and position('//' in path) = 0),
  thumb_path  text not null check (length(thumb_path) <= 200 and thumb_path ~ '^[a-z0-9][a-z0-9_./-]*\.(webp|jpg|jpeg|png)$' and position('..' in thumb_path) = 0 and position('//' in thumb_path) = 0),
  mime        text not null check (mime in ('image/webp', 'image/jpeg', 'image/png')),
  size_bytes  integer not null check (size_bytes between 1 and 5242880),
  width       integer not null check (width between 1 and 10000),
  height      integer not null check (height between 1 and 10000),
  alt         text not null default '' check (length(alt) <= 200),
  sha256      text check (sha256 ~ '^[0-9a-f]{64}$'),
  source_url  text check (length(source_url) <= 500),
  license     text check (length(license) <= 200),
  author      text check (length(author) <= 200),
  note        text check (length(note) <= 500),
  uploaded_by uuid references auth.users (id) on delete set null,
  created_at  timestamptz not null default now(),
  unique (storage, path)
);

-- ---------- Публичные данные категорий ----------
create table public.menu_category_public (
  category_id bigint primary key references public.menu_categories (id) on delete cascade,
  slug        text not null unique check (length(slug) <= 50 and slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  sort        integer not null default 0 check (sort between -100000 and 100000),
  archived_at timestamptz,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- ---------- Публичные данные блюд ----------
create table public.menu_item_public (
  item_id               bigint primary key references public.menu_items (id) on delete cascade,
  slug                  text not null unique check (length(slug) <= 80 and slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  description           text not null default '' check (length(description) <= 500),
  weight_value          integer check (weight_value between 1 and 100000),
  weight_unit           text check (weight_unit in ('г', 'мл', 'шт.')),
  recommended           boolean not null default false,
  sort                  integer not null default 0 check (sort between -100000 and 100000),
  photo_asset_id        bigint references public.media_assets (id) on delete restrict,
  photo_alt             text check (length(photo_alt) <= 200),
  published             boolean not null default false,
  price_confirmed_price numeric(10, 2) check (price_confirmed_price > 0),
  price_confirmed_by    uuid references auth.users (id) on delete set null,
  price_confirmed_at    timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint menu_item_public_weight_pair check ((weight_value is null) = (weight_unit is null)),
  constraint menu_item_public_confirm_pair check ((price_confirmed_price is null) = (price_confirmed_at is null))
);
create index menu_item_public_photo_idx on public.menu_item_public (photo_asset_id) where photo_asset_id is not null;

-- ---------- Резервирование слагов (F25): использованный слаг нельзя отдать другой сущности ----------
create table public.public_slug_reservations (
  kind        text not null check (kind in ('category', 'dish')),
  slug        text not null,
  reserved_at timestamptz not null default now(),
  primary key (kind, slug)
);

-- ---------- Состав и аллергены (описательные справочники для сайта; это НЕ технологическая карта склада) ----------
create table public.allergens (
  id   text primary key check (length(id) <= 40 and id ~ '^[a-z0-9]+(_[a-z0-9]+)*$'),
  name text not null check (length(btrim(name)) between 1 and 80),
  sort integer not null default 0 check (sort between -100000 and 100000)
);
create table public.ingredients (
  id   text primary key check (length(id) <= 40 and id ~ '^[a-z0-9]+(_[a-z0-9]+)*$'),
  name text not null check (length(btrim(name)) between 1 and 100)
);
create table public.ingredient_allergens (
  ingredient_id text not null references public.ingredients (id) on delete cascade,
  allergen_id   text not null references public.allergens (id) on delete restrict,
  status        text not null check (status in ('contains', 'may_contain', 'confirmed_free')),   -- confirmed_free: производитель подтвердил отсутствие
  primary key (ingredient_id, allergen_id)
);
create table public.menu_item_ingredients (
  item_id       bigint not null references public.menu_items (id) on delete cascade,
  ingredient_id text not null references public.ingredients (id) on delete restrict,
  sort          integer not null default 0,
  primary key (item_id, ingredient_id)
);

alter table public.media_assets             enable row level security;
alter table public.menu_category_public     enable row level security;
alter table public.menu_item_public         enable row level security;
alter table public.public_slug_reservations enable row level security;
alter table public.allergens                enable row level security;
alter table public.ingredients              enable row level security;
alter table public.ingredient_allergens     enable row level security;
alter table public.menu_item_ingredients    enable row level security;
-- Политик нет намеренно: клиентские роли не читают и не пишут эти таблицы напрямую.

revoke all on table public.media_assets, public.menu_category_public, public.menu_item_public, public.public_slug_reservations,
                    public.allergens, public.ingredients, public.ingredient_allergens, public.menu_item_ingredients
  from public, anon, authenticated;
revoke all on sequence public.media_assets_id_seq from public, anon, authenticated;

-- ---------- Триггеры слагов и отметок времени ----------
create function public._slug_guard() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_kind text := case tg_table_name when 'menu_category_public' then 'category' else 'dish' end;
begin
  if tg_op = 'UPDATE' then
    if new.slug is distinct from old.slug then
      raise exception using errcode = 'P0001', message = 'SLUG_IMMUTABLE', detail = old.slug;
    end if;
    return new;
  end if;
  if exists (select 1 from public.public_slug_reservations r where r.kind = v_kind and r.slug = new.slug) then
    raise exception using errcode = 'P0001', message = 'SLUG_RESERVED', detail = new.slug;
  end if;
  return new;
end;
$$;

create function public._slug_reserve() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.public_slug_reservations (kind, slug)
  values (case tg_table_name when 'menu_category_public' then 'category' else 'dish' end, new.slug)
  on conflict do nothing;
  return null;
end;
$$;

create trigger menu_category_public_a_slug_guard before insert or update of slug on public.menu_category_public
  for each row execute function public._slug_guard();
create trigger menu_category_public_b_touch before update on public.menu_category_public
  for each row execute function public._touch_updated_at();
create trigger menu_category_public_c_slug_reserve after insert on public.menu_category_public
  for each row execute function public._slug_reserve();
create trigger menu_item_public_a_slug_guard before insert or update of slug on public.menu_item_public
  for each row execute function public._slug_guard();
create trigger menu_item_public_b_touch before update on public.menu_item_public
  for each row execute function public._touch_updated_at();
create trigger menu_item_public_c_slug_reserve after insert on public.menu_item_public
  for each row execute function public._slug_reserve();

-- ---------- Внутренние помощники ----------
create function public._require_admin() returns void
language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
end;
$$;

-- Разбор значений JSON: отсутствие или null → NULL; неверный тип/формат/диапазон → INVALID_DATA (detail — имя поля)
create function public._json_text(p jsonb, k text, p_max integer) returns text
language plpgsql immutable set search_path = '' as $$
declare
  v jsonb;
  t text;
begin
  if p is null or jsonb_typeof(p) <> 'object' then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  v := p -> k;
  if v is null or jsonb_typeof(v) = 'null' then
    return null;
  end if;
  if jsonb_typeof(v) <> 'string' then
    raise exception using errcode = '22023', message = 'INVALID_DATA', detail = k;
  end if;
  t := btrim(p ->> k);
  if length(t) > p_max then
    raise exception using errcode = '22023', message = 'INVALID_DATA', detail = k;
  end if;
  return t;
end;
$$;

create function public._json_int(p jsonb, k text, p_lo integer, p_hi integer) returns integer
language plpgsql immutable set search_path = '' as $$
declare
  v jsonb;
  n bigint;
begin
  if p is null or jsonb_typeof(p) <> 'object' then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  v := p -> k;
  if v is null or jsonb_typeof(v) = 'null' then
    return null;
  end if;
  if jsonb_typeof(v) <> 'number' or (p ->> k) !~ '^-?[0-9]{1,9}$' then
    raise exception using errcode = '22023', message = 'INVALID_DATA', detail = k;
  end if;
  n := (p ->> k)::bigint;
  if n < p_lo or n > p_hi then
    raise exception using errcode = '22023', message = 'INVALID_DATA', detail = k;
  end if;
  return n::integer;
end;
$$;

create function public._json_bool(p jsonb, k text) returns boolean
language plpgsql immutable set search_path = '' as $$
declare
  v jsonb;
begin
  if p is null or jsonb_typeof(p) <> 'object' then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  v := p -> k;
  if v is null or jsonb_typeof(v) = 'null' then
    return null;
  end if;
  if jsonb_typeof(v) <> 'boolean' then
    raise exception using errcode = '22023', message = 'INVALID_DATA', detail = k;
  end if;
  return (p ->> k)::boolean;
end;
$$;

-- ЕДИНСТВЕННОЕ определение «почему блюда нет на сайте»: его используют public_menu() и диагностика, расхождение невозможно (F27).
-- Пустой массив причин = блюдо опубликовано. Недоступность (is_available = false) НЕ причина скрытия: блюдо показывается как «недоступно».
create function public._public_blockers() returns table (item_id bigint, reasons text[])
language sql stable security definer set search_path = '' as $$
  select m.id,
         array_remove(array[
           case when m.archived_at is not null then 'ITEM_ARCHIVED' end,
           case when m.rejected_at is not null then 'ITEM_REJECTED' end,
           case when m.approved_at is null and m.rejected_at is null then 'ITEM_NOT_APPROVED' end,
           case when p.item_id is null then 'PUBLIC_DATA_MISSING' end,
           case when p.item_id is not null and not p.published then 'NOT_PUBLISHED' end,
           case when cp.category_id is null then 'CATEGORY_SLUG_MISSING' end,
           case when cp.archived_at is not null then 'CATEGORY_ARCHIVED' end,
           case when p.item_id is not null and m.price <> round(m.price) then 'PRICE_NOT_WHOLE' end,
           case when p.item_id is not null and p.price_confirmed_price is distinct from m.price then 'PRICE_NOT_CONFIRMED' end
         ], null)
    from public.menu_items m
    left join public.menu_item_public p on p.item_id = m.id
    left join public.menu_categories c on c.name = m.category
    left join public.menu_category_public cp on cp.category_id = c.id;
$$;

-- Сохранение публичных данных блюда (создание: слаг обязателен; изменение: только переданные поля; слаг менять нельзя)
create function public._save_item_public(p_item_id bigint, p_data jsonb) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_slug text;
  v_found boolean;
  v_cname text;
begin
  if p_data is null or jsonb_typeof(p_data) <> 'object' then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  if not exists (select 1 from public.menu_items where id = p_item_id) then
    raise exception using errcode = 'P0002', message = 'ITEM_NOT_FOUND';
  end if;
  v_slug := nullif(public._json_text(p_data, 'slug', 80), '');
  if v_slug is not null and v_slug !~ '^[a-z0-9]+(-[a-z0-9]+)*$' then
    raise exception using errcode = '22023', message = 'INVALID_SLUG', detail = v_slug;
  end if;
  perform 1 from public.menu_item_public where item_id = p_item_id for update;
  v_found := found;
  if not v_found then
    if v_slug is null then
      raise exception using errcode = '22023', message = 'SLUG_REQUIRED';
    end if;
    insert into public.menu_item_public (item_id, slug, description, weight_value, weight_unit, recommended, sort)
    values (p_item_id, v_slug, coalesce(public._json_text(p_data, 'description', 500), ''), public._json_int(p_data, 'weight_value', 1, 100000),
            nullif(public._json_text(p_data, 'weight_unit', 10), ''), coalesce(public._json_bool(p_data, 'recommended'), false),
            coalesce(public._json_int(p_data, 'sort', -100000, 100000), 0));
  else
    update public.menu_item_public
       set slug = coalesce(v_slug, slug),
           description = case when p_data ? 'description' then coalesce(public._json_text(p_data, 'description', 500), '') else description end,
           weight_value = case when p_data ? 'weight_value' then public._json_int(p_data, 'weight_value', 1, 100000) else weight_value end,
           weight_unit = case when p_data ? 'weight_unit' then nullif(public._json_text(p_data, 'weight_unit', 10), '') else weight_unit end,
           recommended = coalesce(public._json_bool(p_data, 'recommended'), recommended),
           sort = coalesce(public._json_int(p_data, 'sort', -100000, 100000), sort)
     where item_id = p_item_id;
  end if;
exception
  when unique_violation then
    raise exception using errcode = '23505', message = 'SLUG_TAKEN';
  when check_violation then
    get stacked diagnostics v_cname = constraint_name;
    raise exception using errcode = '22023', message = case when v_cname = 'menu_item_public_weight_pair' then 'WEIGHT_INCOMPLETE' else 'INVALID_DATA' end, detail = v_cname;
end;
$$;

create function public._set_item_ingredients(p_item_id bigint, p_ids jsonb) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_missing text;
begin
  if not exists (select 1 from public.menu_items where id = p_item_id) then
    raise exception using errcode = 'P0002', message = 'ITEM_NOT_FOUND';
  end if;
  if p_ids is null or jsonb_typeof(p_ids) <> 'array' or jsonb_array_length(p_ids) > 40 then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  if exists (select 1 from jsonb_array_elements(p_ids) e where jsonb_typeof(e) <> 'string') then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  select x.id into v_missing from jsonb_array_elements_text(p_ids) as x(id)
   where not exists (select 1 from public.ingredients i where i.id = x.id) limit 1;
  if v_missing is not null then
    raise exception using errcode = 'P0002', message = 'INGREDIENT_NOT_FOUND', detail = v_missing;
  end if;
  delete from public.menu_item_ingredients where item_id = p_item_id;
  insert into public.menu_item_ingredients (item_id, ingredient_id, sort)
  select p_item_id, x.id, min(x.ord)::integer from jsonb_array_elements_text(p_ids) with ordinality as x(id, ord) group by x.id;
end;
$$;

-- Подтверждение ТЕКУЩЕЙ цены блюда для сайта (изменение цены в menu_items подтверждение не сохраняет: блюдо скрывается до нового подтверждения)
create function public._confirm_price(p_item_id bigint) returns numeric
language plpgsql security definer set search_path = '' as $$
declare
  v_price numeric;
  v_archived timestamptz;
begin
  select m.price, m.archived_at into v_price, v_archived from public.menu_items m where m.id = p_item_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'ITEM_NOT_FOUND';
  end if;
  if v_archived is not null then
    raise exception using errcode = 'P0001', message = 'ITEM_ARCHIVED';
  end if;
  if v_price <> round(v_price) then
    raise exception using errcode = 'P0001', message = 'PRICE_NOT_WHOLE';
  end if;
  update public.menu_item_public
     set price_confirmed_price = v_price, price_confirmed_by = auth.uid(), price_confirmed_at = now()
   where item_id = p_item_id;
  if not found then
    raise exception using errcode = 'P0002', message = 'PUBLIC_DATA_MISSING';
  end if;
  return v_price;
end;
$$;

create function public._publish(p_item_id bigint, p_published boolean) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_reasons text[];
begin
  if p_published is null then
    raise exception using errcode = '22023', message = 'INVALID_DATA', detail = 'published';
  end if;
  perform 1 from public.menu_items where id = p_item_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'ITEM_NOT_FOUND';
  end if;
  if not exists (select 1 from public.menu_item_public where item_id = p_item_id) then
    raise exception using errcode = 'P0002', message = 'PUBLIC_DATA_MISSING';
  end if;
  if p_published then
    select array_remove(b.reasons, 'NOT_PUBLISHED') into v_reasons from public._public_blockers() b where b.item_id = p_item_id;
    if cardinality(v_reasons) > 0 then
      raise exception using errcode = 'P0001', message = v_reasons[1], detail = array_to_string(v_reasons, ',');
    end if;
  end if;
  update public.menu_item_public set published = p_published where item_id = p_item_id;
end;
$$;

create function public._set_photo(p_item_id bigint, p_asset_id bigint, p_alt text, p_set_alt boolean) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists (select 1 from public.menu_item_public where item_id = p_item_id) then
    raise exception using errcode = 'P0002', message = 'PUBLIC_DATA_MISSING';
  end if;
  if p_asset_id is not null and not exists (select 1 from public.media_assets where id = p_asset_id) then
    raise exception using errcode = 'P0002', message = 'MEDIA_NOT_FOUND';
  end if;
  if p_set_alt and length(btrim(coalesce(p_alt, ''))) > 200 then
    raise exception using errcode = '22023', message = 'INVALID_DATA', detail = 'photo_alt';
  end if;
  update public.menu_item_public
     set photo_asset_id = p_asset_id,
         photo_alt = case when p_set_alt then nullif(btrim(coalesce(p_alt, '')), '') else photo_alt end
   where item_id = p_item_id;
end;
$$;

-- ---------- Анонимно: публичное меню (формат docs/contracts/public_catalog.md; дополнительно, обратно совместимо: allergens, ingredients, photo.source) ----------
create function public.public_menu() returns jsonb
language sql stable security definer set search_path = '' as $$
  with dishes as (
    select m.id, m.name, m.price, m.is_available,
           p.slug as dslug, p.description, p.weight_value, p.weight_unit, p.recommended, p.sort as dsort,
           c.name as cname, cp.slug as cslug, cp.sort as csort,
           a.id as asset_id, a.storage, a.path, a.thumb_path, a.width, a.height,
           coalesce(p.photo_alt, nullif(a.alt, ''), m.name) as photo_alt
      from public._public_blockers() b
      join public.menu_items m on m.id = b.item_id
      join public.menu_item_public p on p.item_id = m.id
      join public.menu_categories c on c.name = m.category
      join public.menu_category_public cp on cp.category_id = c.id
      left join public.media_assets a on a.id = p.photo_asset_id
     where cardinality(b.reasons) = 0
  )
  select jsonb_build_object(
    'schema', 1,
    'generated_at', now(),
    'categories', coalesce((select jsonb_agg(jsonb_build_object('id', x.cslug, 'name', x.cname, 'sort', x.csort) order by x.csort, x.cname)
                              from (select distinct d.cslug, d.cname, d.csort from dishes d) x), '[]'::jsonb),
    'dishes', coalesce((select jsonb_agg(jsonb_build_object(
                  'id', d.dslug, 'name', d.name, 'category', d.cslug, 'price', d.price::integer, 'description', d.description,
                  'weight', case when d.weight_value is null then null else jsonb_build_object('value', d.weight_value, 'unit', d.weight_unit) end,
                  'ingredients', coalesce((select jsonb_agg(i.ingredient_id order by i.sort, i.ingredient_id) from public.menu_item_ingredients i where i.item_id = d.id), '[]'::jsonb),
                  'recommended', d.recommended, 'sort', d.dsort, 'available', d.is_available,
                  'photo', case when d.asset_id is null then null
                                else jsonb_build_object('source', d.storage, 'file', d.path, 'thumb', d.thumb_path, 'alt', d.photo_alt, 'width', d.width, 'height', d.height) end)
                  order by d.dsort, d.name) from dishes d), '[]'::jsonb),
    'allergens', coalesce((select jsonb_agg(jsonb_build_object('id', al.id, 'name', al.name) order by al.sort, al.id) from public.allergens al), '[]'::jsonb),
    'ingredients', coalesce((select jsonb_object_agg(g.id, g.obj)
                               from (select i.id, jsonb_build_object(
                                       'name', i.name,
                                       'allergens', coalesce((select jsonb_object_agg(ia.allergen_id, ia.status) from public.ingredient_allergens ia
                                                               where ia.ingredient_id = i.id and ia.status <> 'confirmed_free'), '{}'::jsonb),
                                       'confirmed_free', coalesce((select jsonb_agg(ia.allergen_id order by ia.allergen_id) from public.ingredient_allergens ia
                                                                    where ia.ingredient_id = i.id and ia.status = 'confirmed_free'), '[]'::jsonb)) as obj
                                       from public.ingredients i
                                      where i.id in (select mi.ingredient_id from public.menu_item_ingredients mi join dishes d on d.id = mi.item_id)) g), '{}'::jsonb)
  );
$$;

-- ---------- Администратор ----------
create function public.admin_set_category_public(p_category_id bigint, p_data jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_slug text;
  v_sort integer;
  v_arch boolean;
  v_row public.menu_category_public;
begin
  perform public._require_admin();
  if p_data is null or jsonb_typeof(p_data) <> 'object' then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  if not exists (select 1 from public.menu_categories where id = p_category_id) then
    raise exception using errcode = 'P0002', message = 'CATEGORY_NOT_FOUND';
  end if;
  v_slug := nullif(public._json_text(p_data, 'slug', 50), '');
  v_sort := public._json_int(p_data, 'sort', -100000, 100000);
  v_arch := public._json_bool(p_data, 'archived');
  if v_slug is not null and v_slug !~ '^[a-z0-9]+(-[a-z0-9]+)*$' then
    raise exception using errcode = '22023', message = 'INVALID_SLUG', detail = v_slug;
  end if;
  select * into v_row from public.menu_category_public where category_id = p_category_id for update;
  if not found then
    if v_slug is null then
      raise exception using errcode = '22023', message = 'SLUG_REQUIRED';
    end if;
    begin
      insert into public.menu_category_public (category_id, slug, sort, archived_at)
      values (p_category_id, v_slug, coalesce(v_sort, 0), case when coalesce(v_arch, false) then now() end);
    exception when unique_violation then
      raise exception using errcode = '23505', message = 'SLUG_TAKEN';
    end;
  else
    update public.menu_category_public
       set slug = coalesce(v_slug, slug),
           sort = coalesce(v_sort, sort),
           archived_at = case when v_arch is null then archived_at when v_arch then coalesce(archived_at, now()) else null end
     where category_id = p_category_id;
  end if;
  select * into v_row from public.menu_category_public where category_id = p_category_id;
  return jsonb_build_object('category_id', p_category_id, 'slug', v_row.slug, 'sort', v_row.sort, 'archived', v_row.archived_at is not null);
end;
$$;

create function public.admin_list_category_public() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  perform public._require_admin();
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'id', c.id, 'name', c.name, 'slug', cp.slug, 'sort', cp.sort, 'archived', cp.archived_at is not null, 'has_public_data', cp.category_id is not null,
            'dishes_total', (select count(*) from public.menu_items m where m.category = c.name),
            'dishes_on_site', (select count(*) from public._public_blockers() b join public.menu_items m on m.id = b.item_id
                                where m.category = c.name and cardinality(b.reasons) = 0))
            order by coalesce(cp.sort, 0), c.name), '[]'::jsonb)
            from public.menu_categories c left join public.menu_category_public cp on cp.category_id = c.id);
end;
$$;

create function public.admin_set_menu_item_public(p_item_id bigint, p_data jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  perform public._require_admin();
  perform public._save_item_public(p_item_id, p_data);
  return jsonb_build_object('item_id', p_item_id, 'slug', (select slug from public.menu_item_public where item_id = p_item_id));
end;
$$;

create function public.admin_set_menu_item_ingredients(p_item_id bigint, p_ids jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  perform public._require_admin();
  perform public._set_item_ingredients(p_item_id, p_ids);
  return jsonb_build_object('item_id', p_item_id, 'count', (select count(*) from public.menu_item_ingredients where item_id = p_item_id));
end;
$$;

create function public.admin_save_allergen(p_id text, p_name text, p_sort integer default 0) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  perform public._require_admin();
  if p_id is null or length(p_id) > 40 or p_id !~ '^[a-z0-9]+(_[a-z0-9]+)*$' or length(btrim(coalesce(p_name, ''))) not between 1 and 80
     or p_sort is null or p_sort not between -100000 and 100000 then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  insert into public.allergens (id, name, sort) values (p_id, btrim(p_name), p_sort)
  on conflict (id) do update set name = excluded.name, sort = excluded.sort;
  return jsonb_build_object('allergen_id', p_id);
end;
$$;

create function public.admin_save_ingredient(p_id text, p_name text, p_allergens jsonb default '{}'::jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  r record;
begin
  perform public._require_admin();
  if p_id is null or length(p_id) > 40 or p_id !~ '^[a-z0-9]+(_[a-z0-9]+)*$' or length(btrim(coalesce(p_name, ''))) not between 1 and 100 then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  if p_allergens is null or jsonb_typeof(p_allergens) <> 'object' then
    raise exception using errcode = '22023', message = 'INVALID_DATA', detail = 'allergens';
  end if;
  for r in select e.key, e.value from jsonb_each(p_allergens) e loop
    if jsonb_typeof(r.value) <> 'string' or (r.value #>> '{}') not in ('contains', 'may_contain', 'confirmed_free') then
      raise exception using errcode = '22023', message = 'INVALID_DATA', detail = r.key;
    end if;
    if not exists (select 1 from public.allergens a where a.id = r.key) then
      raise exception using errcode = 'P0002', message = 'ALLERGEN_NOT_FOUND', detail = r.key;
    end if;
  end loop;
  insert into public.ingredients (id, name) values (p_id, btrim(p_name))
  on conflict (id) do update set name = excluded.name;
  delete from public.ingredient_allergens where ingredient_id = p_id;
  insert into public.ingredient_allergens (ingredient_id, allergen_id, status)
  select p_id, e.key, e.value #>> '{}' from jsonb_each(p_allergens) e;
  return jsonb_build_object('ingredient_id', p_id);
end;
$$;

create function public.admin_confirm_menu_price(p_item_id bigint) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_price numeric;
begin
  perform public._require_admin();
  v_price := public._confirm_price(p_item_id);
  return jsonb_build_object('item_id', p_item_id, 'confirmed_price', v_price);
end;
$$;

create function public.admin_publish_menu_item(p_item_id bigint, p_published boolean) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  perform public._require_admin();
  perform public._publish(p_item_id, p_published);
  return jsonb_build_object('item_id', p_item_id, 'published', p_published);
end;
$$;

-- Сохранение блюда для сайта ОДНОЙ транзакцией: публичные данные, состав, фото, (необязательно) подтверждение цены и публикация.
-- Цену блюда меняет существующая admin_update_menu_item; сюда передаётся p_confirm_price = true, чтобы блюдо не пропало с сайта молча.
create function public.admin_save_dish_public(p_item_id bigint, p_data jsonb, p_confirm_price boolean default false) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_reasons text[];
begin
  perform public._require_admin();
  perform public._save_item_public(p_item_id, p_data);
  if p_data ? 'ingredients' then
    perform public._set_item_ingredients(p_item_id, p_data -> 'ingredients');
  end if;
  if p_data ? 'photo_asset_id' or p_data ? 'photo_alt' then
    perform public._set_photo(p_item_id, public._json_int(p_data, 'photo_asset_id', 1, 2000000000)::bigint,
                              public._json_text(p_data, 'photo_alt', 200), p_data ? 'photo_alt');
  end if;
  if coalesce(p_confirm_price, false) then
    perform public._confirm_price(p_item_id);
  end if;
  if p_data ? 'published' then
    perform public._publish(p_item_id, public._json_bool(p_data, 'published'));
  end if;
  select b.reasons into v_reasons from public._public_blockers() b where b.item_id = p_item_id;
  return jsonb_build_object('item_id', p_item_id, 'published', (select published from public.menu_item_public where item_id = p_item_id),
                            'on_site', cardinality(v_reasons) = 0, 'reasons', to_jsonb(v_reasons));
end;
$$;

create function public.admin_release_slug(p_kind text, p_slug text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_count integer;
begin
  perform public._require_admin();
  if p_kind is null or p_kind not in ('category', 'dish') or p_slug is null or p_slug !~ '^[a-z0-9]+(-[a-z0-9]+)*$' then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  if (p_kind = 'category' and exists (select 1 from public.menu_category_public where slug = p_slug))
     or (p_kind = 'dish' and exists (select 1 from public.menu_item_public where slug = p_slug)) then
    raise exception using errcode = 'P0001', message = 'SLUG_IN_USE', detail = p_slug;
  end if;
  delete from public.public_slug_reservations where kind = p_kind and slug = p_slug;
  get diagnostics v_count = row_count;
  return jsonb_build_object('kind', p_kind, 'slug', p_slug, 'released', v_count > 0);
end;
$$;

create function public.admin_register_media(p_data jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_storage text; v_path text; v_thumb text; v_mime text; v_sha text; v_alt text;
  v_size integer; v_w integer; v_h integer;
  v_id bigint; v_old record;
  c_path constant text := '^[a-z0-9][a-z0-9_./-]*\.(webp|jpg|jpeg|png)$';
begin
  perform public._require_admin();
  if p_data is null or jsonb_typeof(p_data) <> 'object' then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  v_storage := public._json_text(p_data, 'storage', 10);
  v_path := public._json_text(p_data, 'path', 200);
  v_thumb := public._json_text(p_data, 'thumb_path', 200);
  v_mime := public._json_text(p_data, 'mime', 20);
  v_sha := lower(public._json_text(p_data, 'sha256', 64));
  v_alt := coalesce(public._json_text(p_data, 'alt', 200), '');
  v_size := public._json_int(p_data, 'size_bytes', 1, 5242880);
  v_w := public._json_int(p_data, 'width', 1, 10000);
  v_h := public._json_int(p_data, 'height', 1, 10000);
  if v_storage is null or v_storage not in ('repo', 'storage')
     or v_path is null or v_path !~ c_path or position('..' in v_path) > 0 or position('//' in v_path) > 0
     or v_thumb is null or v_thumb !~ c_path or position('..' in v_thumb) > 0 or position('//' in v_thumb) > 0
     or v_mime is null or v_mime not in ('image/webp', 'image/jpeg', 'image/png')
     or v_size is null or v_w is null or v_h is null
     or (v_sha is not null and v_sha !~ '^[0-9a-f]{64}$') then
    raise exception using errcode = '22023', message = 'INVALID_DATA';
  end if;
  select * into v_old from public.media_assets where storage = v_storage and path = v_path;
  if found then
    if v_sha is not null and v_old.sha256 is not null and v_sha <> v_old.sha256 then
      raise exception using errcode = 'P0001', message = 'MEDIA_CONFLICT';
    end if;
    return jsonb_build_object('id', v_old.id, 'duplicate', true);
  end if;
  insert into public.media_assets (storage, path, thumb_path, mime, size_bytes, width, height, alt, sha256, source_url, license, author, note, uploaded_by)
  values (v_storage, v_path, v_thumb, v_mime, v_size, v_w, v_h, v_alt, v_sha, public._json_text(p_data, 'source_url', 500), public._json_text(p_data, 'license', 200),
          public._json_text(p_data, 'author', 200), public._json_text(p_data, 'note', 500), auth.uid())
  returning id into v_id;
  return jsonb_build_object('id', v_id, 'duplicate', false);
end;
$$;

create function public.admin_set_dish_photo(p_item_id bigint, p_asset_id bigint, p_alt text default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  perform public._require_admin();
  perform public._set_photo(p_item_id, p_asset_id, p_alt, p_alt is not null);
  return jsonb_build_object('item_id', p_item_id, 'photo_asset_id', p_asset_id);
end;
$$;

create function public.admin_media_list(p_only_unused boolean default false) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  perform public._require_admin();
  return (select coalesce(jsonb_agg(row_data order by (row_data ->> 'id')::bigint desc), '[]'::jsonb)
            from (select jsonb_build_object('id', a.id, 'storage', a.storage, 'path', a.path, 'thumb_path', a.thumb_path, 'mime', a.mime, 'size_bytes', a.size_bytes,
                           'width', a.width, 'height', a.height, 'alt', a.alt, 'sha256', a.sha256, 'source_url', a.source_url, 'license', a.license, 'author', a.author,
                           'created_at', a.created_at, 'used', u.items is not null, 'used_by', coalesce(u.items, '[]'::jsonb)) as row_data
                    from public.media_assets a
                    left join lateral (select jsonb_agg(jsonb_build_object('item_id', p.item_id, 'name', m.name, 'slug', p.slug)) as items
                                         from public.menu_item_public p join public.menu_items m on m.id = p.item_id
                                        where p.photo_asset_id = a.id) u on true
                   where not coalesce(p_only_unused, false) or u.items is null
                   order by a.id desc limit 1000) t);
end;
$$;

create function public.admin_delete_media(p_id bigint) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v public.media_assets;
  v_used jsonb;
begin
  perform public._require_admin();
  select * into v from public.media_assets where id = p_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'MEDIA_NOT_FOUND';
  end if;
  if v.storage = 'repo' then
    raise exception using errcode = 'P0001', message = 'REPO_MEDIA_PROTECTED';
  end if;
  select jsonb_agg(p.item_id) into v_used from public.menu_item_public p where p.photo_asset_id = p_id;
  if v_used is not null then
    raise exception using errcode = 'P0001', message = 'MEDIA_IN_USE', detail = v_used::text;
  end if;
  delete from public.media_assets where id = p_id;
  return jsonb_build_object('deleted', true, 'storage', v.storage, 'path', v.path, 'thumb_path', v.thumb_path);
end;
$$;

-- Диагностика: каждое блюдо с причинами, по которым его нет на сайте (пустой список = на сайте). Тот же источник, что у public_menu().
create function public.admin_catalog_diagnostics() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  perform public._require_admin();
  return (select jsonb_build_object(
            'on_site', count(*) filter (where cardinality(b.reasons) = 0),
            'blocked', count(*) filter (where cardinality(b.reasons) > 0),
            'items', coalesce(jsonb_agg(jsonb_build_object('id', m.id, 'name', m.name, 'category', m.category, 'price', m.price, 'is_available', m.is_available,
                       'slug', p.slug, 'published', coalesce(p.published, false), 'on_site', cardinality(b.reasons) = 0, 'reasons', to_jsonb(b.reasons))
                       order by m.name), '[]'::jsonb))
            from public._public_blockers() b
            join public.menu_items m on m.id = b.item_id
            left join public.menu_item_public p on p.item_id = m.id);
end;
$$;

-- ---------- Права: сначала отзыв у PUBLIC, anon, authenticated; затем точечный GRANT ----------
revoke all on function
  public._touch_updated_at(), public._slug_guard(), public._slug_reserve(), public._require_admin(),
  public._json_text(jsonb, text, integer), public._json_int(jsonb, text, integer, integer), public._json_bool(jsonb, text),
  public._public_blockers(), public._save_item_public(bigint, jsonb), public._set_item_ingredients(bigint, jsonb),
  public._confirm_price(bigint), public._publish(bigint, boolean), public._set_photo(bigint, bigint, text, boolean),
  public.public_menu(),
  public.admin_set_category_public(bigint, jsonb), public.admin_list_category_public(), public.admin_set_menu_item_public(bigint, jsonb),
  public.admin_set_menu_item_ingredients(bigint, jsonb), public.admin_save_allergen(text, text, integer), public.admin_save_ingredient(text, text, jsonb),
  public.admin_confirm_menu_price(bigint), public.admin_publish_menu_item(bigint, boolean), public.admin_save_dish_public(bigint, jsonb, boolean),
  public.admin_release_slug(text, text), public.admin_register_media(jsonb), public.admin_set_dish_photo(bigint, bigint, text),
  public.admin_media_list(boolean), public.admin_delete_media(bigint), public.admin_catalog_diagnostics()
  from public, anon, authenticated;

grant execute on function public.public_menu() to anon, authenticated;
grant execute on function
  public.admin_set_category_public(bigint, jsonb), public.admin_list_category_public(), public.admin_set_menu_item_public(bigint, jsonb),
  public.admin_set_menu_item_ingredients(bigint, jsonb), public.admin_save_allergen(text, text, integer), public.admin_save_ingredient(text, text, jsonb),
  public.admin_confirm_menu_price(bigint), public.admin_publish_menu_item(bigint, boolean), public.admin_save_dish_public(bigint, jsonb, boolean),
  public.admin_release_slug(text, text), public.admin_register_media(jsonb), public.admin_set_dish_photo(bigint, bigint, text),
  public.admin_media_list(boolean), public.admin_delete_media(bigint), public.admin_catalog_diagnostics()
  to authenticated;

-- ---------- Проверка результата внутри транзакции: при любом несоответствии миграция откатывается ----------
do $$
declare
  t text;
  v_bad text[] := '{}';
  f record;
  v_internal text[] := array['_touch_updated_at', '_slug_guard', '_slug_reserve', '_require_admin', '_json_text', '_json_int', '_json_bool', '_public_blockers',
                             '_save_item_public', '_set_item_ingredients', '_confirm_price', '_publish', '_set_photo'];
  v_found integer := 0;
  v_pub_exec boolean;
begin
  foreach t in array array['media_assets', 'menu_category_public', 'menu_item_public', 'public_slug_reservations', 'allergens', 'ingredients', 'ingredient_allergens', 'menu_item_ingredients'] loop
    if not (select relrowsecurity from pg_class where oid = ('public.' || t)::regclass) then v_bad := v_bad || (t || ': RLS выключен'); end if;
    if exists (select 1 from pg_policies where schemaname = 'public' and tablename = t) then v_bad := v_bad || (t || ': есть политики'); end if;
    if has_table_privilege('anon', 'public.' || t, 'select,insert,update,delete,truncate,references,trigger') then v_bad := v_bad || (t || ': права у anon'); end if;
    if has_table_privilege('authenticated', 'public.' || t, 'select,insert,update,delete,truncate,references,trigger') then v_bad := v_bad || (t || ': права у authenticated'); end if;
    if exists (select 1 from pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a where c.oid = ('public.' || t)::regclass and a.grantee = 0) then
      v_bad := v_bad || (t || ': права у PUBLIC');
    end if;
  end loop;
  if has_sequence_privilege('anon', 'public.media_assets_id_seq', 'usage,select,update') or has_sequence_privilege('authenticated', 'public.media_assets_id_seq', 'usage,select,update') then
    v_bad := v_bad || 'media_assets_id_seq: права у клиентов';
  end if;
  for f in select p.oid, p.proname, p.proacl, p.proowner from pg_proc p
            where p.pronamespace = 'public'::regnamespace
              and (p.proname = any (v_internal) or p.proname = 'public_menu' or p.proname like 'admin\_%' escape '\')
              and p.proname in ('_touch_updated_at', '_slug_guard', '_slug_reserve', '_require_admin', '_json_text', '_json_int', '_json_bool', '_public_blockers',
                                '_save_item_public', '_set_item_ingredients', '_confirm_price', '_publish', '_set_photo', 'public_menu',
                                'admin_set_category_public', 'admin_list_category_public', 'admin_set_menu_item_public', 'admin_set_menu_item_ingredients',
                                'admin_save_allergen', 'admin_save_ingredient', 'admin_confirm_menu_price', 'admin_publish_menu_item', 'admin_save_dish_public',
                                'admin_release_slug', 'admin_register_media', 'admin_set_dish_photo', 'admin_media_list', 'admin_delete_media', 'admin_catalog_diagnostics') loop
    v_found := v_found + 1;
    v_pub_exec := exists (select 1 from aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) a where a.grantee = 0);
    if v_pub_exec then v_bad := v_bad || (f.proname || ': EXECUTE у PUBLIC'); end if;
    if f.proname = any (v_internal) then
      if has_function_privilege('anon', f.oid, 'execute') or has_function_privilege('authenticated', f.oid, 'execute') then v_bad := v_bad || (f.proname || ': внутренняя функция доступна клиентам'); end if;
    elsif f.proname = 'public_menu' then
      if not has_function_privilege('anon', f.oid, 'execute') or not has_function_privilege('authenticated', f.oid, 'execute') then v_bad := v_bad || 'public_menu: нет доступа'; end if;
    else
      if has_function_privilege('anon', f.oid, 'execute') then v_bad := v_bad || (f.proname || ': доступна anon'); end if;
      if not has_function_privilege('authenticated', f.oid, 'execute') then v_bad := v_bad || (f.proname || ': недоступна authenticated'); end if;
    end if;
  end loop;
  if v_found <> 29 then v_bad := v_bad || ('создано функций ' || v_found || ' вместо 29'); end if;
  if not exists (select 1 from pg_trigger where tgname = 'menu_items_touch' and tgrelid = 'public.menu_items'::regclass) then v_bad := v_bad || 'нет триггера menu_items_touch'; end if;
  if (select count(*) from pg_trigger where tgrelid in ('public.menu_category_public'::regclass, 'public.menu_item_public'::regclass) and not tgisinternal) <> 6 then v_bad := v_bad || 'триггеров слагов не 6'; end if;
  if v_bad <> '{}' then
    raise exception 'ОСТАНОВЛЕНО (post-check внутри миграции): %', array_to_string(v_bad, '; ');
  end if;
end;
$$;

notify pgrst, 'reload schema';
commit;
