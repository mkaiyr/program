-- Временное заполнение для старых проверок sql/tests/05–16 (они требуют активных admin и cashier и позиции меню).
-- Пароли — настоящие bcrypt-хеши: проверки вроде 13 сравнивают crypt() по всем строкам auth.users (порядок вычисления зависит от плана запроса).
-- Выполняется ВНУТРИ транзакции, которую откатывает rollback самой проверки: в базе ничего не остаётся.
insert into auth.users (id, aud, role, email, encrypted_password, email_confirmed_at, created_at, updated_at, confirmation_token, recovery_token, email_change_token_new, email_change)
values ('00000000-0000-4000-8000-0000000000a1', 'authenticated', 'authenticated', 'reg-admin@bistro.local', extensions.crypt('x', extensions.gen_salt('bf')), now(), now(), now(), '', '', '', ''),
       ('00000000-0000-4000-8000-0000000000c1', 'authenticated', 'authenticated', 'reg-cashier@bistro.local', extensions.crypt('x', extensions.gen_salt('bf')), now(), now(), now(), '', '', '', ''),
       ('00000000-0000-4000-8000-0000000000d1', 'authenticated', 'authenticated', 'reg-kitchen@bistro.local', extensions.crypt('x', extensions.gen_salt('bf')), now(), now(), now(), '', '', '', ''),
       ('00000000-0000-4000-8000-0000000000e1', 'authenticated', 'authenticated', 'reg-bar@bistro.local', extensions.crypt('x', extensions.gen_salt('bf')), now(), now(), now(), '', '', '', '');
insert into public.profiles (id, full_name, role, is_active, employee_code) values
  ('00000000-0000-4000-8000-0000000000a1', 'Рег Админ', 'admin', true, '9001'),
  ('00000000-0000-4000-8000-0000000000c1', 'Рег Кассир', 'cashier', true, '9002'),
  ('00000000-0000-4000-8000-0000000000d1', 'Рег Кухня', 'kitchen', true, '9003'),
  ('00000000-0000-4000-8000-0000000000e1', 'Рег Бар', 'bar', true, '9004');
insert into public.menu_categories (name) values ('РЕГ') on conflict do nothing;
insert into public.menu_items (name, category, price, station) values ('РЕГ Блюдо 1', 'РЕГ', 1000, 'kitchen'), ('РЕГ Блюдо 2', 'РЕГ', 500, 'bar');
