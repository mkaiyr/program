# Материалы для независимой проверки: этап 1, миграция 18 (единый каталог)

Дата подготовки: 2026-11-28. Это **документы и SQL-файлы для проверки**, а не часть приложения или сайта. В базу из этой папки ничего не применялось и не применяется автоматически; миграции 01–16 и черновик 17 не затронуты. Миграция 18 была применена автором **только к изолированной локальной базе** `cashier_catalog_dev` (порт 54323); защищённые базы не затрагивались.

## Что где лежит
| Каталог | Содержимое |
|---|---|
| `report/` | `STAGE_1_MIGRATION_18_REPORT.md` — отчёт этапа: что сделано, результаты проверок, отклонения |
| `plan/` | `FINAL_INTEGRATION_PLAN.md` (редакция 2, этап 1 отмечен выполненным) и `FINAL_PLAN_REVIEW.md` (заключительная проверка плана, замечания F01–F66) |
| `sql/` | `18_unified_catalog.sql` — **итоговая** миграция; `18_unified_catalog.applied_v1.sql` — **фактически применённый первым** вариант; `18a_slug_guard_fix.sql` — поправка (одна функция `_slug_guard`), применённая сразу после; `18_postcheck.sql` — проверка результата (только чтение, 22 проверки); `18_rehearsal_rollback.sql` — репетиция (тот же файл с `rollback`) |
| `tests/` | `18_checks.sql` — автотесты (9 групп, транзакция с откатом); `regression_seed.sql` и `run_regression.sh` — прогон старых проверок `sql/tests/05–16` в обёртке с временным заполнением; `validate_sample.py` — проверка образца `public_menu()` валидатором сайта |
| `contract/` | `public_menu_sample.json` — образец ответа `public_menu()` (из теста, вымышленные данные `Т-…`). Сам контракт — `docs/contracts/public_catalog.md` в этом репозитории (расширения ответа описаны в отчёте, п. 8) |
| `logs/` | `stage1_commands.sanitized.log` (журнал команд, очищенная копия), `stage1_apply_18.log`, `stage1_tests_18.log`, `stage1_postcheck_18.log`, `stage1_validate_sample.log`, `regression_before_18_summary.txt`, `regression_after_18_summary.txt`. В репозитории действует правило `.gitignore: *.log`, поэтому файлы `*.log` добавлены принудительно (`git add -f`) |
| `checksums/` | Контрольные суммы: файлы миграций 01–16 в репозитории, применённого файла 18 и файлов этапа, черновика 17 |

## Как читать
1. `report/STAGE_1_MIGRATION_18_REPORT.md` — общая картина и отклонения (раздел 6: поправка 18a, причина падения проверки 13 из-за временных данных).
2. `sql/18_unified_catalog.sql` — что создаёт миграция; `sql/18a_slug_guard_fix.sql` — чем итоговый файл отличается от применённого (`diff sql/18_unified_catalog.applied_v1.sql sql/18_unified_catalog.sql` показывает только функцию `_slug_guard`).
3. `tests/18_checks.sql` и `sql/18_postcheck.sql` — что именно проверено.
4. `plan/` — контекст: зачем нужны слаги, диагностика, права, что остаётся на следующие этапы.

Состояние базы после этапа = применённый первым вариант + `18a`. `sql/18_unified_catalog.sql` содержит то же определение функции, что и поправка, но применение одним итоговым файлом на пустой базе автор не проверял (отчёт, п. 6.1).

## ⚠ Перед запуском: замените плейсхолдеры и пути
Пакет очищен от локальных путей автора. **Три файла нельзя запускать «как есть»** — сначала подставьте свои значения (строки указаны по файлам пакета):

| Файл | Строка | Что там | Чем заменить |
|---|---|---|---|
| `tests/18_checks.sql` | 524 | `\o <HOME>/cashier_catalog_dev_pg/work/v2/tests/public_menu_sample.json` — куда тест записывает образец ответа `public_menu()` | `<HOME>` — абсолютный путь к вашему каталогу, **в который можно писать** (лучше не `contract/`, чтобы не затереть образец). Либо удалите три строки блока (`\o …`, `select public.public_menu();`, `\o`) — тесты 1–9 от него не зависят |
| `tests/run_regression.sh` | 3 | `Q=<SCRATCH>/q.sh` | `<SCRATCH>/q.sh` — путь к вашему скрипту-обёртке над `psql` (в пакет не входит, см. ниже) |
| `tests/run_regression.sh` | 4 | `SEED="$HOME/cashier_catalog_dev_pg/work/v2/tests/regression_seed.sql"` — локальный путь автора (не плейсхолдер) | путь к `tests/regression_seed.sql` из пакета |
| `tests/run_regression.sh` | 5 | `for f in <HOME>/cashier_app/sql/tests/*.sql` | `<HOME>/cashier_app` — корень вашей копии репозитория, т.е. `<корень>/sql/tests/*.sql` (оригиналы проверок только читаются) |
| `tests/validate_sample.py` | 7 | `SITE_JS = Path("<HOME>/cashier_app/website/docs/assets/js")` | `<корень репозитория>/website/docs/assets/js` (файлы сайта только читаются) |
| `tests/validate_sample.py` | 8 | образец по умолчанию ищется **рядом со скриптом** | в пакете образец лежит в `contract/`: передайте путь аргументом (ниже) |

`<HOME>` в отчёте, журналах и `checksums/`, а также `<PRODUCTION_PROJECT_REF>` в отчёте и журнале команд — просто следы очистки, подставлять их не нужно. `<SCRATCH>` встречается только в `tests/run_regression.sh` (его нужно заменить, см. выше). Скрипт-обёртка `q.sh` — это вызов `psql` к изолированной базе с остановкой на первой ошибке:
```bash
#!/bin/bash
exec psql -h <каталог_сокета> -p <порт> -U <суперпользователь_изолированного_кластера> -d cashier_catalog_dev -X -v ON_ERROR_STOP=1 "$@"
```

## Как запускать (только на изолированной базе, не на защищённых)
Нужна пустая изолированная база с эмуляцией Supabase (схема `auth`, роли `anon`, `authenticated`, `service_role`, расширение `pgcrypto` в схеме `extensions`) и применёнными миграциями 01–16 (`sql/01…16` репозитория).
```bash
# 1. Миграция 18 (одна транзакция; при ошибке или повторном применении останавливается на pre-check, ничего не меняя)
q.sh -f sql/18_unified_catalog.sql
# 2. Post-check: только чтение, 22 проверки, последняя строка «ИТОГ: …»
q.sh -At -f sql/18_postcheck.sql
# 3. Автотесты миграции (транзакция с откатом); последняя строка «ВСЕ ПРОВЕРКИ МИГРАЦИИ 18 ПРОЙДЕНЫ»
q.sh -q -f tests/18_checks.sql
# 4. Образец public_menu() — валидатором сайта (нужен macOS: JavaScriptCore через osascript)
python3 tests/validate_sample.py contract/public_menu_sample.json     # ожидается «ИТОГ: образец public_menu() принят валидатором сайта»
# 5. Регрессия старых проверок 05–16 в обёртке с временным заполнением (всё откатывается)
bash tests/run_regression.sh <каталог_для_журналов>                   # ожидается: 06–16 → «ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ»; 05 пропускается по замыслу после 08
```
Шаги 2–5 выполняются **после** шага 1. До шага 1 запускать их бессмысленно: `18_postcheck.sql` и `18_checks.sql` обращаются к таблицам миграции и до её применения завершатся ошибкой «relation does not exist». Файлы `sql/18_rehearsal_rollback.sql` (репетиция с откатом) и `sql/18a_slug_guard_fix.sql` (поправка к варианту `applied_v1`) в обычную проверку не входят.

## Подготовка пакета: что очищено и что исключено
**Очищенные копии** (оригиналы в рабочей папке автора не изменялись): в тексте заменены идентификатор Production-проекта на `<PRODUCTION_PROJECT_REF>`, локальные пути на `<HOME>` и `<SCRATCH>`. Затронуты только: отчёт, `tests/18_checks.sql` (одна строка `\o` с путём), `tests/run_regression.sh`, `tests/validate_sample.py`, очищенный журнал команд. Файлы `sql/*`, `plan/*`, `tests/regression_seed.sql`, образец JSON и журналы результатов совпадают с оригиналами побайтно (таблица ниже). В `tests/18_checks.sql`, `tests/run_regression.sh` и `tests/validate_sample.py` остались плейсхолдеры `<HOME>` и `<SCRATCH>`, которые нужно заменить — см. раздел «⚠ Перед запуском».

**Исключено намеренно:** резервные копии базы (`.dump`, plain-дампы схемы и данных, дамп ролей кластера), полные дампы и снимки схемы (`schema_before_18.sql`, `schema_after_18*.sql`, `schema_diff_18.txt`), подробные журналы регрессии по каждому файлу, рабочая копия миграций 01–16 (они есть в `sql/` репозитория), скрипты эмуляции Supabase (скопированы из другого рабочего каталога), модель склада v2 и векторы (отдельный этап 0), аналитические документы `ANALYSIS_BEFORE_18_23.md`, `OWNER_DECISIONS.md`, `IMPLEMENTATION_PLAN_18_23.md` (не запрашивались), ключи, пароли и конфигурации (в рабочей папке их нет; проверено сканированием).

## Манифест (SHA-256 оригинала и копии)
| Файл в пакете | Исходный файл (в `work/`) | SHA-256 оригинала | SHA-256 копии | Статус |
|---|---|---|---|---|
| `report/STAGE_1_MIGRATION_18_REPORT.md` | `STAGE_1_MIGRATION_18_REPORT.md` | `2a939a2618967c22…` | `20679342b0d7b89e…` | изменён (санитизация) |
| `plan/FINAL_INTEGRATION_PLAN.md` | `FINAL_INTEGRATION_PLAN.md` | `61e013311d1a83cb…` | `61e013311d1a83cb…` | побайтно совпадает с оригиналом |
| `plan/FINAL_PLAN_REVIEW.md` | `FINAL_PLAN_REVIEW.md` | `97135a02d03bcd8b…` | `97135a02d03bcd8b…` | побайтно совпадает с оригиналом |
| `sql/18_unified_catalog.sql` | `v2/18_unified_catalog.sql` | `0f5c4cb3f44fbd76…` | `0f5c4cb3f44fbd76…` | побайтно совпадает с оригиналом |
| `sql/18_unified_catalog.applied_v1.sql` | `v2/18_unified_catalog.applied_v1.sql` | `edf91bcacbb90d22…` | `edf91bcacbb90d22…` | побайтно совпадает с оригиналом |
| `sql/18a_slug_guard_fix.sql` | `v2/18a_slug_guard_fix.sql` | `95d8b374fb4eee12…` | `95d8b374fb4eee12…` | побайтно совпадает с оригиналом |
| `sql/18_postcheck.sql` | `v2/18_postcheck.sql` | `6d7018077577c0df…` | `6d7018077577c0df…` | побайтно совпадает с оригиналом |
| `sql/18_rehearsal_rollback.sql` | `v2/18_rehearsal_rollback.sql` | `3e0b465675029141…` | `3e0b465675029141…` | побайтно совпадает с оригиналом |
| `tests/18_checks.sql` | `v2/tests/18_checks.sql` | `042a006306cc15de…` | `2b106f3a57482265…` | изменён (санитизация) |
| `tests/regression_seed.sql` | `v2/tests/regression_seed.sql` | `84a7d74592d812d0…` | `84a7d74592d812d0…` | побайтно совпадает с оригиналом |
| `tests/run_regression.sh` | `v2/tests/run_regression.sh` | `8d1bf1b43f4a77f3…` | `a62329a86be0a16a…` | изменён (санитизация) |
| `tests/validate_sample.py` | `v2/tests/validate_sample.py` | `2ef585a17cde492a…` | `3098c863a60dd589…` | изменён (санитизация) |
| `contract/public_menu_sample.json` | `v2/tests/public_menu_sample.json` | `3e8d2ac10ca7d649…` | `3e8d2ac10ca7d649…` | побайтно совпадает с оригиналом |
| `logs/stage1_commands.sanitized.log` | `log/stage1_commands.log` | `763890645aca76f5…` | `917d3a8ce3c50e7f…` | изменён (санитизация) |
| `logs/stage1_apply_18.log` | `log/stage1_apply_18.log` | `c3291b7d82553a3d…` | `c3291b7d82553a3d…` | побайтно совпадает с оригиналом |
| `logs/stage1_tests_18.log` | `log/stage1_tests_18.log` | `f3e2f6aa744b48c8…` | `f3e2f6aa744b48c8…` | побайтно совпадает с оригиналом |
| `logs/stage1_postcheck_18.log` | `log/stage1_postcheck_18.log` | `8a73e4527a16a670…` | `8a73e4527a16a670…` | побайтно совпадает с оригиналом |
| `logs/stage1_validate_sample.log` | `log/stage1_validate_sample.log` | `635fde30d0f376b6…` | `635fde30d0f376b6…` | побайтно совпадает с оригиналом |
| `logs/regression_before_18_summary.txt` | `log/stage1_baseline_seeded_summary.txt` | `c5476dffe3ca343a…` | `c5476dffe3ca343a…` | побайтно совпадает с оригиналом |
| `logs/regression_after_18_summary.txt` | `log/stage1_after18_regression_summary.txt` | `c5476dffe3ca343a…` | `c5476dffe3ca343a…` | побайтно совпадает с оригиналом |

## Ревизия 2 (после независимого ревью)
- В первой версии пакета пять журналов были перечислены в README и манифесте, но не попали в коммит: их молча пропустило правило `.gitignore: *.log`. Теперь они добавлены принудительно (`git add -f`), `.gitignore` не менялся. Копии прошли повторную проверку на секреты и персональные данные; резервные копии, дампы и снимки схемы по-прежнему исключены.
- README дополнен: явное предупреждение о плейсхолдерах (`<HOME>`, `<SCRATCH>`) с номерами строк, описание скрипта-обёртки `q.sh`, точные команды запуска и ожидаемые результаты.
- Манифест выше не менялся: его хеши журналов совпали с фактическими файлами. SHA-256 **добавленных** файлов (полные):

| Файл | SHA-256 |
|---|---|
| `logs/stage1_commands.sanitized.log` | `917d3a8ce3c50e7fc3ef9408d7847ae011cb3c4338ff4fc886adb84c455f1e86` |
| `logs/stage1_apply_18.log` | `c3291b7d82553a3dc73efc6aa67109a031fc8418c73476b21296cfdcaf31eff5` |
| `logs/stage1_tests_18.log` | `f3e2f6aa744b48c8303361957061507cca7097377d3c7ea1a5e71fd403e62d20` |
| `logs/stage1_postcheck_18.log` | `8a73e4527a16a670d8393d47e895b2fea9d42b7ea72458c8418180437e5b1ee1` |
| `logs/stage1_validate_sample.log` | `635fde30d0f376b634c9d5ff4b959e1d93b5c915c423589988c801c246fc95a3` |
