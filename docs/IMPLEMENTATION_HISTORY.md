# История реализации: пакеты 11–15

Документ для разработчика, который продолжает проект: в каком порядке сделаны пакет «надбавка 10%, аннулирование, смены,
сотрудники, отчёты, оболочка администратора» (миграции 11–14, этапы 0–9) и пакет «архив блюд» (миграция 15, этап 10), как миграции связаны с Python-кодом и что осталось.
Пакеты реализованы в рабочих сессиях (Claude Code по заданию владельца проекта). Пакет 11–14 закоммичен владельцем (`19cf8b9`, 2026-11-14); пакет 15 на момент записи **не закоммичен**;
по истории Git отдельные шаги не восстановить, порядок ниже — порядок работы. Подробности по функциям —
[CHANGELOG.md](../CHANGELOG.md), по ошибкам — [BUGFIXES.md](BUGFIXES.md).

## Этап 0. Исходное состояние (`HEAD` = `2067800`)

- Миграции 01–10; `create_order` (последняя версия — в 08) считал итог как сумму позиций; в `orders` — `total`.
- Роли `admin`, `cashier`, `kitchen`, `bar`; администратор после входа попадал на экран кассы (`OrderFrame`) с кнопками,
  открывавшими отдельные окна: «Управление меню» (`MenuAdminWindow`), «Заказы» (`OrdersWindow`).
- Удаление: выданный заказ в «Удалённые» и окончательное удаление администратором (миграция 10).
- Нет: надбавки, аннулирования, смен, управления сотрудниками из приложения, отчётов.
- 270 тестов на заглушках `Database`; SQL-проверки 05–10 выполнялись на локальном PostgreSQL 16.2.
- Единый стиль был в `ui_theme.py`; повторяющийся шаблон фонового запроса (`_run`/`_finish`/`_set_busy`) был скопирован в каждом окне.

Решения, принятые до реализации (после чтения кода, не угадывания):
1. Новые изменения — только новыми файлами 11–14; старые миграции не менялись (правило [DEVELOPMENT.md](DEVELOPMENT.md#правила)).
2. Серверная функция с новой сигнатурой получила **новое имя** `place_order`, а `create_order(uuid, jsonb)` осталась
   обёрткой: перегрузка с тем же именем и разным набором параметров сделала бы выбор функции в PostgREST неоднозначным.
3. Ставка 10% — константа функции, а не настройка: по заданию она фиксирована.
4. Деньги — `Decimal` в Python и `numeric` в PostgreSQL; `float` не используется.

## Этап 1. Надбавка 10% (миграция 11, часть 1)

- SQL: столбцы `subtotal`, `service_percent`, `service_amount`, ограничение `orders_total_parts`, заполнение подытога у старых
  заказов, функция `place_order`, обёртка `create_order`.
- Python: `money.py` (расчёт, формат суммы), `db.create_order(request_id, items, expected_total)`, корзина в `ui_order.py`
  (`cart_totals`, три строки итога).
- Сверка суммы: `p_expected_total` → `TOTAL_MISMATCH` (текст — `db.RPC_ERRORS`).
- Тесты: `tests/test_service_charge.py`; заглушки `create_order` в четырёх старых файлах тестов обновлены; SQL: `sql/tests/11_…`.

## Этап 2. Аннулирование (миграция 11, часть 2)

- SQL: `voided_at`, `voided_by`, `void_reason`, `orders_void_consistent`, `void_order`; `_station_orders` пересоздана с
  фильтром `voided_at is null` (текст из 08 плюс одно условие).
- Python: `db.void_order`; в `ui_orders.py` кнопка «Аннулировать» и диалог причины (`ui_dialogs.FormDialog`).
- Удаление из миграции 10 **сохранено** — оно остаётся для выданных заказов рядом с аннулированием.

## Этап 3. История заказов

- `ui_orders.py` разделён: `OrdersMixin` (вся логика) и две оболочки — `OrdersWindow` (`Toplevel`, кассир) и `OrdersPanel`
  (`ttk.Frame`, раздел администратора); имена атрибутов окна не менялись, поэтому старые тесты прошли без правок.
- `db.load_orders` принимает фильтры (номер, диапазон дат, статус, аннулирование) и выполняет отбор на сервере;
  при `42703` (нет колонок миграции 11) повторяет запрос с прежним набором колонок. Здесь же указан явный внешний ключ
  кассира (см. [BUGFIXES.md](BUGFIXES.md#1-неоднозначное-встраивание-кассира-в-load_orders)).
- `reports.export_orders_csv` — экспорт.

## Этап 4. Кассовые смены (миграция 12)

- SQL: таблицы смен и операций (закрыты для клиентов), `orders.shift_id`, триггер `orders_attach_shift` (заказ привязывается к
  открытой смене кассира; `for share` защищает от гонки с закрытием), `_shift_json` (общий расчёт), функции `shift_*`.
- Python: методы смен в `db.py`, `parse_amount`, `ui_shift.py` (`ShiftPanel` — общая панель; `ShiftWindow` — окно кассира);
  кнопка «Смена» в `ui_order.py`.
- Зависит от 11: выручка смены считается по `orders.total` без аннулированных (`voided_at`).

## Этап 5. Сотрудники (миграция 13)

- SQL: `admin_staff_list`, `admin_create_staff`, `admin_update_staff`, `admin_set_staff_password`; защита `SELF_LOCKOUT` и `LAST_ADMIN`.
- Python: `db.load_staff`, `create_staff`, `update_staff`, `set_staff_password`, `check_staff`, `check_password`; `ui_staff.py`.
- Вход не менялся (`employee_login_email`, Supabase Auth). Создание записи в `auth.users` — самое хрупкое место пакета (см. ограничения).

## Этап 6. Отчёты и категории (миграция 14)

- SQL: `admin_report` (цены из `order_items`, без аннулированных), `admin_rename_category`.
- Python: `db.load_report`, `db.rename_category`; `reports.py` (периоды, `export_report_csv`), `ui_reports.py`, `ui_charts.py`
  (диаграммы на Canvas без внешних библиотек), `CategoriesPanel` в `ui_admin_panels.py`.

## Этап 7. Улучшения интерфейса

- `ui_theme.py`: цвета боковой панели, `FONT_TOTAL`/`FONT_STAT`, стили `Side.*`, `Nav*`, `Stat`, `Touch.TButton`, `Cart.Treeview`,
  `CardH1`, `CardWarn`, `CardDanger`; помощники `stat_card`, `make_table`.
- `ui_base.Panel` — общий шаблон фонового запроса для новых панелей (старые окна не переводились на него, чтобы не трогать
  проверенную логику выхода из аккаунта).
- `ui_shell.AdminShell` и `ui_admin_panels.py`; `OrderFrame(embedded=True)` — касса внутри оболочки без собственной шапки.
- Кухня и бар: время ожидания и цветовая индикация задержки (`ui_kitchen.py`).
- `main.py`: администратор → `AdminShell`.

## Этап 8. Исправления ошибок

См. [BUGFIXES.md](BUGFIXES.md): неоднозначное встраивание кассира (до пакета), обрезанные элементы новых экранов (внутри пакета).
Устаревшая SQL-проверка 09 обновлена — это следствие намеренного изменения `create_order`, не ошибка.

## Этап 9. Тестирование

Результаты — в таблице [DEVELOPMENT.md](DEVELOPMENT.md#статус-проверки-пакета-1115). Порядок локальной проверки SQL: 01–10 на пустой базе,
вставка пользователей, тестового меню и двух заказов **до** миграции 11, затем 11–14, повторный запуск каждой, затем проверки
11–14 и старые 07–10. Заказы до миграции сравнивались по хешу до/после — не изменились.

## Связи: миграция → функция → Python → экран → тест

| Миграция | SQL-функция / объект | `db.py` | Экран | Тесты |
|---|---|---|---|---|
| 11 | `place_order`, `create_order` | `create_order` | `ui_order.OrderFrame` | `test_service_charge.py`, SQL 11 |
| 11 | `void_order`, `orders.voided_*` | `void_order`, `load_orders` | `ui_orders` | `test_new_modules.py`, SQL 11 |
| 11 | `_station_orders` | `load_station_orders` (+`created_raw`) | `ui_kitchen` | `test_new_modules.py::KitchenWaitTest`, SQL 11 |
| 12 | `shift_current/open/cash_move/close/history` | `current_shift`, `open_shift`, `cash_move`, `close_shift`, `shift_history` | `ui_shift`, `ui_order` («Смена»), раздел «Кассовые смены» | `test_new_modules.py`, SQL 12 |
| 13 | `admin_staff_list/create/update/set_staff_password` | `load_staff`, `create_staff`, `update_staff`, `set_staff_password` | `ui_staff` | `test_new_modules.py`, SQL 13 |
| 14 | `admin_report` | `load_report` | `ui_reports`, главная | `test_new_modules.py`, SQL 14 |
| 14 | `admin_rename_category` | `rename_category` | `CategoriesPanel` | SQL 14 |
| 11–14 | — | `probe_features` (только чтение) | `SettingsPanel` | `test_new_modules.py` |
| 11–14 | — | — | — | `test_new_migrations.py` (статический разбор файлов) |

Зависимости миграций: 11 ← 01–10; 12 ← 11; 13 ← 12 (+ `employee_code`, роль `bar`, `pgcrypto`); 14 ← 12, 13.
Приложение без 11 не оформляет заказы; без 12–14 соответствующие разделы сообщают «не настроено на сервере»
(`PGRST202` → понятный текст, как у `load_station_menu` для 09).

## Этап 10. Архив блюд (миграция 15, пакет 15)

Реализован по согласованному плану в четыре шага; каждый шаг проверялся отдельно.

1. **Миграция 15 и SQL-проверки** (`sql/15_menu_item_archive.sql`, `sql/tests/15_menu_item_archive_checks.sql`): `menu_items.archived_at/by` и ограничение `menu_items_archived_state`,
   `admin_archive_menu_item`, `admin_restore_menu_item`, `admin_purge_menu_item`, `admin_menu_purge_log`, журнал `menu_item_purge_log`, снимок `order_items.category`,
   `order_items.menu_item_id` nullable (`on delete set null`), пересозданные `station_menu`, `menu_select_staff`, `admin_report`. `place_order` не менялась: архив закрыт ограничением и RLS.
   Проверено на двух локальных базах, в том числе удалением блюда, проданного в двух заказах, со сравнением заказов, смены и отчёта до и после.
2. **Python-слой** (`db.py`): три метода архива, `PGRST202` → «не выполнена миграция 15», флаг `archived` в `load_all_menu`, тексты ошибок; `tests/test_menu_archive.py`.
3. **Интерфейс**: вкладки «Активные блюда» и «Архив» в `MenuSectionPanel`, подтверждения, редактор без архивных блюд, счётчики категорий, сообщение пустой категории и перенос кнопок категорий на кассе;
   `db.count_menu_item_orders` для подтверждения удаления; `tests/test_menu_archive_ui.py`. На этом шаге найдены и исправлены переполнение кнопок категорий и цикл `<Configure>` ([BUGFIXES.md](BUGFIXES.md)).
4. **Тесты и документация**: статические проверки миграции 15 в `tests/test_new_migrations.py`, документы, итоговая проверка.

Связи: `15` → `admin_archive_menu_item` / `admin_restore_menu_item` / `admin_purge_menu_item` → `db.archive_menu_item` / `restore_menu_item` / `purge_menu_item` → `ui_admin_panels.MenuSectionPanel`;
`15` → `station_menu` (поле `archived_at`) → `db.load_all_menu` (флаг `archived`) → `MenuSectionPanel`, `CategoriesPanel`, `ui_admin.MenuAdminWindow`;
`15` → `order_items.category` → `admin_report` → `ui_reports`. Кассир: RLS `menu_select_staff` → `db.load_menu` → `ui_order.OrderFrame`.

## Этап 11. Справочник категорий меню (миграция 16)

Перед выбором способа связи изучены все обращения к `menu_items.category` (отчёт 14/15, `station_menu`, `kitchen_menu`, пять версий RPC добавления и изменения блюда, `place_order`, снимок `order_items.category`,
UI кассы, редактора, «Управления меню», кухни и бара). Выбрано: текстовая колонка остаётся, добавлен справочник и внешний ключ `ON UPDATE CASCADE ON DELETE RESTRICT` — ни один существующий запрос не менялся.
Остальные варианты (числовой `category_id` вместо текста) потребовали бы переписать отчёт, обе станции, снимки заказов и пять функций; от них отказались.

1. **Миграция и SQL-проверки** (`sql/16_menu_categories.sql`, `sql/tests/16_menu_categories_checks.sql`): таблица, перенос категорий, внешний ключ, триггер приведения названия, `admin_add_category`,
   `admin_delete_category`, переопределённая `admin_rename_category`, права. Выполнено на локальной `cashier_local` дважды (повторный запуск без изменений данных).
2. **Python-слой** (`db.py`): `load_categories` (до миграции 16 — пустой список), `add_category`, `delete_category`, тексты `CATEGORY_EXISTS`, `CATEGORY_NOT_FOUND`, `CATEGORY_NOT_EMPTY` (со счётчиками), строка в «Настройках».
3. **Интерфейс**: `CategoriesPanel` («Добавить», «Переименовать», «Удалить», пустые категории в списке), касса, редактор и «Управление меню» берут список категорий из справочника.
4. **Тесты и документация**: `tests/test_menu_categories.py` (db, экраны, структура миграции), проверки 07–15 после 16, 63 сценария настоящего приложения через PostgREST.

Найденное по пути: проверка `09_station_menu_checks.sql` предлагала блюдо от станции в несуществующей категории — это противоречит требованию «повар и бармен категории не меняют». Правило оставлено строгим,
проверка 09 получила подготовку (категория «Тест»); изменение поведения описано в CHANGELOG и DATABASE.

## Известные ограничения и что осталось

**Не проверено на целевом Supabase** (локально проверено на PostgreSQL 17 с эмуляцией):
- применение миграций 11–15 и SQL-проверок 11–15 на целевом проекте (их нужно выполнить там отдельно: сначала тестовый проект,
  на рабочем — после резервной копии и подтверждения владельца);
- работа PostgREST с функциями 11–15 и встраиванием `profiles!…_fkey`;
- вход сотрудника, созданного `admin_create_staff`, через GoTrue (функция пишет в `auth.users` и `auth.identities` напрямую;
  схема `auth` зависит от версии GoTrue);
- права и владельцы функций при роли `postgres` Supabase без прав суперпользователя.

**Не реализовано:** фото блюд (в `menu_items` нет столбца), типы заказов, номер стола, комментарии к заказу, скидки, способы оплаты,
печать и предпросмотр чека; экспорт в Excel (только CSV); тест интерфейса раздела «Категории».

**Решения, требующие подтверждения владельца:**
- повторный запуск 08 откатывает `create_order` и `_station_orders` — предусловия против этого в старых файлах не добавлены
  (порядок восстановления — [DATABASE.md](DATABASE.md#повторный-запуск));
- удаление (миграция 10) сохранено рядом с аннулированием;
- отчёт берёт категорию из текущего меню (заказ её не хранит), дни считаются в поясе `Asia/Almaty`;
- заказы, отправленные в «Удалённые», остаются в выручке (деньги приняты).

**Другое:** вид интерфейса проверялся программно (обход виджетов), снимков экрана нет; редактор позиций меню остался
отдельным модальным окном.
