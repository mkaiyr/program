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
| `logs/` | Журнал команд (очищенная копия) и результаты шагов: применение, тесты, post-check, валидатор, сводки регрессии до/после |
| `checksums/` | Контрольные суммы: файлы миграций 01–16 в репозитории, применённого файла 18 и файлов этапа, черновика 17 |

## Как читать
1. `report/STAGE_1_MIGRATION_18_REPORT.md` — общая картина и отклонения (раздел 6: поправка 18a, причина падения проверки 13 из-за временных данных).
2. `sql/18_unified_catalog.sql` — что создаёт миграция; `sql/18a_slug_guard_fix.sql` — чем итоговый файл отличается от применённого (`diff sql/18_unified_catalog.applied_v1.sql sql/18_unified_catalog.sql` показывает только функцию `_slug_guard`).
3. `tests/18_checks.sql` и `sql/18_postcheck.sql` — что именно проверено.
4. `plan/` — контекст: зачем нужны слаги, диагностика, права, что остаётся на следующие этапы.

Состояние базы после этапа = применённый первым вариант + `18a`. Чтобы воспроизвести на **пустой изолированной базе** с эмуляцией Supabase (схема `auth`, роли `anon`, `authenticated`, `service_role`) и применёнными миграциями 01–16: сначала `sql/18_unified_catalog.sql`, затем `sql/18_postcheck.sql` и `tests/18_checks.sql` (последний сам откатывает транзакцию). Не применять к защищённым базам.

## Подготовка пакета: что очищено и что исключено
**Очищенные копии** (оригиналы в рабочей папке автора не изменялись): в тексте заменены идентификатор Production-проекта на `<PRODUCTION_PROJECT_REF>`, локальные пути на `<HOME>`, `<SCRATCH>`, `<TMP>`. Затронуты только: отчёт, `tests/18_checks.sql` (одна строка `\o` с путём), `tests/run_regression.sh`, `tests/validate_sample.py`, очищенный журнал команд. Файлы `sql/*`, `plan/*`, `tests/regression_seed.sql`, образец JSON и журналы результатов совпадают с оригиналами побайтно (таблица ниже). Пути в `run_regression.sh` и `validate_sample.py` — локальные для автора, их нужно подставить своими.

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

