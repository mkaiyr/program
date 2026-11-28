-- 18a: поправка к применённой миграции 18 (найдено тестами после применения): занятый слаг даёт SLUG_TAKEN, а не SLUG_RESERVED.
-- Меняется ТОЛЬКО тело функции public._slug_guard(); права (ACL) при CREATE OR REPLACE сохраняются. Итоговый файл 18_unified_catalog.sql содержит то же определение.
begin;
create or replace function public._slug_guard() returns trigger
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
  -- «зарезервирован» = использован раньше и сейчас НИКЕМ не занят; занятый слаг даст SLUG_TAKEN от уникального ограничения
  if exists (select 1 from public.public_slug_reservations r where r.kind = v_kind and r.slug = new.slug)
     and not (case v_kind when 'category' then exists (select 1 from public.menu_category_public x where x.slug = new.slug)
                          else exists (select 1 from public.menu_item_public x where x.slug = new.slug) end) then
    raise exception using errcode = 'P0001', message = 'SLUG_RESERVED', detail = new.slug;
  end if;
  return new;
end;
$$;

do $$
begin
  if has_function_privilege('anon', 'public._slug_guard()', 'execute') or has_function_privilege('authenticated', 'public._slug_guard()', 'execute')
     or exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a where p.oid = 'public._slug_guard()'::regprocedure and a.grantee = 0) then
    raise exception 'ОСТАНОВЛЕНО: права функции _slug_guard() изменились';
  end if;
end;
$$;
commit;
