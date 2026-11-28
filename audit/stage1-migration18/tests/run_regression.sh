#!/bin/bash
# Прогон старых проверок sql/tests/05–16 (оригиналы только читаются) внутри транзакции с временным заполнением. Аргумент: каталог для журналов.
Q=<SCRATCH>/q.sh
OUT="$1"; mkdir -p "$OUT"; SEED="$HOME/cashier_catalog_dev_pg/work/v2/tests/regression_seed.sql"
for f in <HOME>/cashier_app/sql/tests/*.sql; do
  n=$(basename "$f" .sql); tmp=$(mktemp)
  { echo "begin;"; cat "$SEED"; cat "$f"; } > "$tmp"
  $Q -q -f "$tmp" > "$OUT/$n.log" 2>&1; rc=$?; rm -f "$tmp"
  last=$(grep -v '^$' "$OUT/$n.log" | grep -v '^WARNING\|^NOTICE' | tail -1 | cut -c1-100)
  echo "$n rc=$rc | $last"
done
