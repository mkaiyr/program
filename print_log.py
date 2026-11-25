"""Журнал печати чеков на этом компьютере: какой заказ, когда, успешно ли. Локальный файл (по одной JSON-строке), не база данных.

Нужен, чтобы отличать первую печать от повторной (на повторной чек помечен) и показывать историю. Запись в журнал не влияет на заказ:
при ошибке записи печать не прерывается, а кассиру сообщается, что журнал не сохранён. Журнал только дописывается.
"""

import json
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = Path.home() / ".bistro_cashier" / "print_log.jsonl"


class PrintLog:
    def __init__(self, path=None):
        self.path = Path(path) if path else DEFAULT_PATH

    def _rows(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        rows = []
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue                         # повреждённая строка не мешает остальным
            if isinstance(row, dict) and isinstance(row.get("order_id"), int):
                rows.append(row)
        return rows

    def history(self, order_id):
        return [r for r in self._rows() if r["order_id"] == int(order_id)]

    def count_ok(self, order_id):
        """Сколько раз чек заказа уже УСПЕШНО отправлен на печать (ошибки не считаются)."""
        return sum(1 for r in self.history(order_id) if r.get("ok") is True)

    def record(self, order_id, ok, kind, printer="", error="", now=None):
        """Дописывает запись. Возвращает True, если записано (False — журнал недоступен, печать от этого не зависит)."""
        row = {"order_id": int(order_id), "at": (now or datetime.now()).isoformat(timespec="seconds"), "ok": bool(ok), "kind": kind,
               "printer": printer, "error": error}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return True
        except OSError:
            return False
