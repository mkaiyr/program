-- =====================================================================
-- Этап 16. Отчёты и категории меню
-- Выполнять целиком в Supabase: SQL Editor -> New query -> Run
-- Требует выполненных 01–13. Одна транзакция. Повторный запуск безопасен (только функции).
--
--   admin_report(p_from, p_to)  сводка за период для администратора
--   admin_rename_category(p_old, p_new)  переименование и объединение категорий меню
--
-- Отчёт считается по заказам, а не по текущему меню: названия и цены берутся из order_items
-- (снимок на момент продажи). Единственное исключение — категория: заказ её не хранит, берётся
-- текущая категория позиции (menu_items.category). Аннулированные заказы в выручку не входят,
-- они считаются отдельно. Заказы, отправленные в «Удалённые» (10), в выручке остаются:
-- деньги по ним приняты.
-- Дни группируются по часовому поясу Asia/Almaty.
-- =====================================================================

begin;

do $$
declare
  v_missing text[] := '{}';
begin
  if to_regclass('public.cash_shifts') is null
  then v_missing := array_append(v_missing, 'cash_shifts (12)'); end if;
  if not exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = 'admin_staff_list')
  then v_missing := array_append(v_missing, 'admin_staff_list() (13)'); end if;
  if cardinality(v_missing) > 0 then
    raise exception 'ОСТАНОВЛЕНО: 14_reports_categories.sql требует выполненных 01–13, не найдено: %',
      array_to_string(v_missing, ', ');
  end if;
end;
$$;

-- ---------- Отчёт ----------
-- Ошибки: NOT_AUTHENTICATED, ADMIN_REQUIRED, INVALID_PERIOD (пусто, перевёрнуто или больше 366 дней)
-- Возвращает:
--   {from, to, orders_count, subtotal_total, service_total, revenue, average_check,
--    voided_count, voided_total,
--    top_items: [{name, quantity, revenue}], categories: [{category, quantity, revenue}],
--    cashiers: [{name, orders, revenue, service}], daily: [{day, orders, revenue}]}
-- revenue — итог заказов (с надбавкой); у блюд и категорий revenue — без надбавки (цена × количество).

create or replace function public.admin_report(p_from timestamptz, p_to timestamptz)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  c_tz constant text := 'Asia/Almaty';
  v_sum record;
  v_void record;
begin
  if auth.uid() is null then
    raise exception using errcode = '28000', message = 'NOT_AUTHENTICATED';
  end if;
  if not public.is_active_admin() then
    raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
  end if;
  if p_from is null or p_to is null or p_to <= p_from or p_to - p_from > interval '366 days' then
    raise exception using errcode = '22023', message = 'INVALID_PERIOD';
  end if;

  select count(*) as n, coalesce(sum(o.subtotal), 0) as subtotal, coalesce(sum(o.service_amount), 0) as service,
         coalesce(sum(o.total), 0) as revenue
    into v_sum
    from public.orders o
   where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null;

  select count(*) as n, coalesce(sum(o.total), 0) as total
    into v_void
    from public.orders o
   where o.created_at >= p_from and o.created_at < p_to and o.voided_at is not null;

  return jsonb_build_object(
    'from', p_from, 'to', p_to,
    'orders_count', v_sum.n, 'subtotal_total', v_sum.subtotal, 'service_total', v_sum.service,
    'revenue', v_sum.revenue,
    'average_check', case when v_sum.n > 0 then round(v_sum.revenue / v_sum.n, 2) else 0 end,
    'voided_count', v_void.n, 'voided_total', v_void.total,
    'top_items', (
      select coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'quantity', t.quantity, 'revenue', t.revenue)
                                order by t.quantity desc, t.revenue desc, t.name), '[]'::jsonb)
        from (select i.item_name as name, sum(i.quantity)::integer as quantity, sum(i.line_total) as revenue
                from public.order_items i join public.orders o on o.id = i.order_id
               where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null
               group by i.item_name
               order by sum(i.quantity) desc, sum(i.line_total) desc, i.item_name
               limit 10) t),
    'categories', (
      select coalesce(jsonb_agg(jsonb_build_object('category', t.category, 'quantity', t.quantity, 'revenue', t.revenue)
                                order by t.revenue desc, t.category), '[]'::jsonb)
        from (select coalesce(m.category, 'Без категории') as category, sum(i.quantity)::integer as quantity,
                     sum(i.line_total) as revenue
                from public.order_items i
                join public.orders o on o.id = i.order_id
                left join public.menu_items m on m.id = i.menu_item_id
               where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null
               group by coalesce(m.category, 'Без категории')) t),
    'cashiers', (
      select coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'orders', t.orders, 'revenue', t.revenue,
                                                   'service', t.service) order by t.revenue desc, t.name), '[]'::jsonb)
        from (select coalesce(p.full_name, '—') as name, count(*)::integer as orders,
                     sum(o.total) as revenue, sum(o.service_amount) as service
                from public.orders o left join public.profiles p on p.id = o.cashier_id
               where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null
               group by o.cashier_id, p.full_name) t),
    'daily', (
      select coalesce(jsonb_agg(jsonb_build_object('day', t.day, 'orders', t.orders, 'revenue', t.revenue)
                                order by t.day), '[]'::jsonb)
        from (select (o.created_at at time zone c_tz)::date as day, count(*)::integer as orders,
                     sum(o.total) as revenue
                from public.orders o
               where o.created_at >= p_from and o.created_at < p_to and o.voided_at is null
               group by (o.created_at at time zone c_tz)::date) t)
  );
end;
$$;

revoke all on function public.admin_report(timestamptz, timestamptz) from public, anon;
grant execute on function public.admin_report(timestamptz, timestamptz) to authenticated;

-- ---------- Переименование категории ----------
-- Если новое имя уже есть, категории объединяются. Ошибки: ADMIN_REQUIRED, INVALID_CATEGORY, NOT_FOUND
-- Возвращает {updated: число позиций}.

create or replace function public.admin_rename_category(p_old text, p_new text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_old text := trim(coalesce(p_old, ''));
  v_new text := trim(coalesce(p_new, ''));
  v_n   integer;
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
  update public.menu_items set category = v_new where category = v_old and category <> v_new;
  get diagnostics v_n = row_count;
  if v_n = 0 and not exists (select 1 from public.menu_items where category = v_old) then
    raise exception using errcode = 'P0002', message = 'NOT_FOUND';
  end if;
  return jsonb_build_object('updated', v_n, 'category', v_new);
end;
$$;

revoke all on function public.admin_rename_category(text, text) from public, anon;
grant execute on function public.admin_rename_category(text, text) to authenticated;

notify pgrst, 'reload schema';

commit;
