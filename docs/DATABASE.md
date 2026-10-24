# База данных (Supabase / PostgreSQL)

> **Статус проверки.** Миграции 07 и 08 и все SQL-проверки `sql/tests/` ещё не выполнялись
> ни на одном сервере Supabase. Supabase MCP настроен в режиме только чтения, но в рабочих
> сессиях пока не подключён (нужна авторизация через `/mcp`). Сведения ниже получены из
> исходников миграций.

## Таблицы

| Таблица | Основные поля | Важные ограничения |
|---|---|---|
| `profiles` | `id` (= `auth.users.id`), `full_name`, `role`, `is_active`, `employee_code` | `profiles_role_check`: `cashier`, `admin`, `kitchen`, `bar` (после 08); `employee_code` — 4–6 цифр, уникален |
| `menu_items` | `id` (PK), `code`, `name`, `category`, `price`, `is_available`, `station` | `name` уникально; `price > 0`; `code` `NOT NULL UNIQUE`, 1…99999999, неизменяем (триггер); `station` ∈ `kitchen`, `bar` |
| `orders` | `id`, `cashier_id`, `client_request_id`, `total`, `status`, `created_at` | `client_request_id` уникален (защита от повторной отправки); `status` ∈ `accepted`, `preparing`, `ready`, `completed` |
| `order_items` | `order_id`, `menu_item_id` → `menu_items.id`, `item_name`, `unit_price`, `quantity`, `line_total`, `station`, `item_code` | снимки названия, цены, станции и кода на момент продажи; `menu_item_id` — ссылка на `id`, не на код |
| `order_stations` | `order_id`, `station`, `status` | PK (`order_id`, `station`); `status` ∈ `accepted`, `preparing`, `ready`; RLS включён, политик и прав для клиентов нет |

Позиции меню физически не удаляются (нет прав, внешний ключ `on delete restrict`) — снимаются
с продажи через `is_available`.

## RLS (чтение)

- `profiles`: свой профиль; администратор — все.
- `menu_items`: кассир и администратор — доступные позиции; администратор — все.
- `orders`, `order_items`: кассир — свои заказы; администратор — все.
- Кухня и бар таблицы не читают: `is_active_staff()` их не включает. Данные — только через RPC.

## Серверные функции

| Функция | Кто вызывает | Назначение |
|---|---|---|
| `employee_login_email(code)` | `anon`, `authenticated` | email для входа по коду активного сотрудника |
| `create_order(request_id, items)` | кассир, администратор | атомарный заказ: проверка доступности и цен, сумма на сервере, станции |
| `set_order_status(id, status)` | кассир (свои), администратор | только `ready → completed` |
| `set_station_status(id, station, status)` | кухня — `kitchen`, бар — `bar`, администратор — любая | `accepted → preparing → ready`, пересчёт общего статуса |
| `admin_force_order_ready(id)` | администратор | явная готовность всех станций и заказа |
| `kitchen_orders()` / `bar_orders()` | кухня / бар (и администратор) | позиции своей станции: код, название, количество — без цен |
| `kitchen_menu()`, `kitchen_set_menu_item_available(id, flag)` | кухня | меню без цен, только наличие |
| `admin_add_menu_item`, `admin_update_menu_item`, `admin_set_menu_item_available` | администратор | меню; код автоматически или вручную при создании |
| `is_active_staff/admin/kitchen/bar()` | `authenticated` | проверки роли текущего пользователя |
| `_next_menu_item_code`, `_menu_item_code_immutable`, `_station_orders`, `_refresh_order_status`, `_check_menu_item_input` | только из других функций | внутренние; `EXECUTE` отозван у клиентов |

Все клиентские RPC — `SECURITY DEFINER` с `search_path = ''`, `EXECUTE` отозван у `PUBLIC` и `anon`
(кроме `employee_login_email`).

## Миграции и порядок применения

| Файл | Содержание |
|---|---|
| `01_schema.sql` | таблицы, RLS, `create_order` |
| `02_admin_menu.sql` | управление меню администратором |
| `03_employee_code.sql` | код сотрудника, `employee_login_email` |
| `04_order_status.sql` | статус заказа |
| `05_kitchen_status.sql` | роль `kitchen`, статус `preparing` |
| `06_kitchen_menu.sql` | наличие блюд для кухни |
| `07_menu_codes.sql` | коды позиций меню (последовательность, триггер неизменности) |
| `08_bar_stations.sql` | роль `bar`, станции, снимки `station`/`item_code`, новые правила статусов |

Применять строго по порядку, каждую — целиком (SQL Editor → New query → Run). Каждая миграция —
одна транзакция: при ошибке не меняется ничего.

Предусловия, при которых миграция останавливается до изменений:

- **07**: колонка `code` уже есть, но не `integer`; уже применена 08.
- **08**: не применена 07; в `orders.status` значения вне `accepted`, `preparing`, `ready`,
  `completed`; в `profiles.role` неизвестные роли; набор ролей в `profiles_role_check` не равен
  `{cashier, admin, kitchen}` и не равен `{cashier, admin, kitchen, bar}`, или ограничение имеет
  неожиданный вид; на `profiles.role` есть другие `CHECK`.

**Совместимость приложения:** текущая версия приложения требует 07 и 08. Устанавливать её на все
рабочие места одновременно с миграциями: после 08 кухня со старой версией не сможет менять статусы.

## Безопасный порядок проверки миграций

1. **Только чтение на production** (SQL Editor или MCP в режиме `read_only`) — убедиться, что
   предусловия выполнены:
   ```sql
   select pg_get_constraintdef(oid) from pg_constraint
    where conrelid = 'public.profiles'::regclass and conname = 'profiles_role_check';
   select status, count(*) from public.orders group by status;
   select role, count(*) from public.profiles group by role;
   ```
2. **Тестовый проект Supabase** (копия схемы, тестовые данные): применить 01→08, затем
   `sql/tests/07_menu_codes_checks.sql` и `sql/tests/08_bar_stations_checks.sql`.
   Ожидается строка `ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ`; проверки выполняются в транзакции и откатываются
   (номера последовательности кодов при этом расходуются — это нормально).
3. **Production** — только после отдельного подтверждения владельца и резервной копии:
   07, затем 08, затем те же SQL-проверки и ручные шаги из конца `08_bar_stations.sql`
   (перевести напитки на станцию `bar`, завести барменов).

`sql/tests/05_kitchen_checks.sql` относится к состоянию до 08 и после неё сразу останавливается
с пояснением.

## Известные вопросы

### `sql/03_employee_code.sql` и ограничения ролей
В рабочей копии `employee_login_email` в 03 перечисляет роли `cashier`, `admin`, `kitchen`, `bar`,
хотя на этапе 03 ограничение ролей допускает только `cashier`, `admin` (роль `kitchen` вводит 05,
`bar` — 08). Анализ:

- итоговое состояние после 01→08 не зависит от этой правки — функцию переопределяют 05 и 08;
- на базе без 05/08 лишние роли ни на что не влияют: таких профилей ещё не может быть;
- прежний текст 03 при повторном запуске после 05/08 отключил бы вход кухне и бару — новый текст
  этот риск устраняет;
- если 03 уже применена в production со старым текстом, файл больше не совпадает с выполненной
  историей.

Предложение: правку оставить; при желании добавить в шапку 03 комментарий о том, что список
ролей синхронизирован с 08. Миграцию повторно на production не применять.

### Формат `pg_get_constraintdef`
08 определяет набор ролей по выводу `pg_get_constraintdef` вида
`CHECK ((role = ANY (ARRAY['cashier'::text, …])))`. Формат на реальной базе не проверен; при
другом виде миграция безопасно остановится и покажет фактическое определение.

### Регистр кириллицы
Проверка уникальности имени позиции (02) использует `lower()`. Если локаль базы не меняет регистр
кириллицы, «Капучино» и «капучино» будут считаться разными; это покажет `07_menu_codes_checks.sql`.
