-- =====================================================================
-- Этап 8. Кухня отмечает наличие блюд («В продаже» / «Нет в наличии»)
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–05. Повторный запуск безопасен. Данные не меняются.
--
-- Используется существующее поле menu_items.is_available — новых колонок нет.
-- Права кухни на таблицу menu_items не выдаются и RLS не меняется:
--   * kitchen_menu() отдаёт только id, название, категорию и доступность (без цен);
--   * kitchen_set_menu_item_available() меняет только is_available.
-- Название, цену и категорию по-прежнему меняет только администратор (02).
-- Касса видит изменения после «Обновить меню»; create_order (01) не примет
-- недоступное блюдо (MENU_CHANGED). Старые заказы не затрагиваются.
-- =====================================================================

begin;

-- ---------- Меню для кухни (без цен) ----------
-- Ошибки (поле message): NOT_AUTHENTICATED, NOT_ALLOWED
-- Возвращает [{id, name, category, is_available}], по категории и названию.

create or replace function public.kitchen_menu()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_result jsonb;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_kitchen() then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
           'id', m.id, 'name', m.name, 'category', m.category, 'is_available', m.is_available)
         order by m.category, m.name), '[]'::jsonb)
    into v_result
    from public.menu_items m;

  return v_result;
end;
$$;

revoke all on function public.kitchen_menu() from public, anon;
grant execute on function public.kitchen_menu() to authenticated;

-- ---------- Наличие блюда ----------
-- Ошибки: NOT_AUTHENTICATED, NOT_ALLOWED, INVALID_REQUEST, NOT_FOUND
-- Возвращает {id, name, is_available} — без цены.

create or replace function public.kitchen_set_menu_item_available(p_id bigint, p_available boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name text;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_kitchen() then
    raise exception using errcode = '42501', message = 'NOT_ALLOWED';
  end if;
  if p_id is null or p_available is null then
    raise exception using errcode = '22023', message = 'INVALID_REQUEST';
  end if;

  update public.menu_items set is_available = p_available
   where id = p_id
  returning name into v_name;
  if not found then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;

  return jsonb_build_object('id', p_id, 'name', v_name, 'is_available', p_available);
end;
$$;

revoke all on function public.kitchen_set_menu_item_available(bigint, boolean) from public, anon;
grant execute on function public.kitchen_set_menu_item_available(bigint, boolean) to authenticated;

-- Чтобы PostgREST сразу увидел новые функции
notify pgrst, 'reload schema';

commit;

-- ---------- Проверка после установки ----------
-- select has_function_privilege('anon', 'public.kitchen_menu()', 'execute');   -- ожидается false
-- select has_function_privilege('anon', 'public.kitchen_set_menu_item_available(bigint, boolean)', 'execute');  -- false
-- Полная проверка прав: sql/tests/06_kitchen_menu_checks.sql
