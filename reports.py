"""Периоды отчётов и экспорт в CSV — без интерфейса, чтобы проверять тестами."""

import csv
from datetime import date, datetime, time, timedelta

from db import AppError

PERIODS = ("Сегодня", "7 дней", "Месяц", "Свой период")


def _start(day):
    return datetime.combine(day, time.min).astimezone()


def period_range(kind, today=None, custom_from=None, custom_to=None):
    """Название периода -> (начало, конец) — datetime в местном поясе, конец не включается.

    «7 дней» — шесть прошлых дней и сегодня; «Месяц» — с первого числа текущего месяца по сегодня;
    «Свой период» — custom_from..custom_to (даты, обе включительно).
    """
    today = today or date.today()
    if kind == "Сегодня":
        return _start(today), _start(today + timedelta(days=1))
    if kind == "7 дней":
        return _start(today - timedelta(days=6)), _start(today + timedelta(days=1))
    if kind == "Месяц":
        return _start(today.replace(day=1)), _start(today + timedelta(days=1))
    if kind == "Свой период":
        if custom_from is None or custom_to is None:
            raise AppError("Укажите даты периода.", "invalid")
        if custom_to < custom_from:
            raise AppError("Конец периода раньше начала.", "invalid")
        if (custom_to - custom_from).days >= 366:
            raise AppError("Период не больше 366 дней.", "invalid")
        return _start(custom_from), _start(custom_to + timedelta(days=1))
    raise AppError("Неизвестный период.", "invalid")


def parse_date(text):
    """«ДД.ММ.ГГГГ» (или ГГГГ-ММ-ДД) -> date."""
    text = (text or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise AppError("Дата — в формате ДД.ММ.ГГГГ, например 05.11.2026.", "invalid")


def _num(value):
    return str(value)


def report_rows(report, label):
    """Отчёт -> список строк CSV (сводка и таблицы друг под другом)."""
    rows = [["Отчёт «Кафе Бистро»", label], [],
            ["Показатель", "Значение"],
            ["Заказов", report["orders_count"]],
            ["Подытог (без надбавки), ₸", _num(report["subtotal_total"])],
            ["Надбавка 10%, ₸", _num(report["service_total"])],
            ["Выручка (с надбавкой), ₸", _num(report["revenue"])],
            ["Средний чек, ₸", _num(report["average_check"])],
            ["Аннулировано заказов", report["voided_count"]],
            ["Сумма аннулированных, ₸", _num(report["voided_total"])], [],
            ["Самые продаваемые блюда"], ["Блюдо", "Количество", "Сумма без надбавки, ₸"]]
    rows += [[r["name"], r["quantity"], _num(r["revenue"])] for r in report["top_items"]]
    rows += [[], ["Категории по выручке"], ["Категория", "Количество", "Сумма без надбавки, ₸"]]
    rows += [[r["category"], r["quantity"], _num(r["revenue"])] for r in report["categories"]]
    rows += [[], ["Продажи по кассирам"], ["Кассир", "Заказов", "Выручка, ₸", "Надбавка, ₸"]]
    rows += [[r["name"], r["orders"], _num(r["revenue"]), _num(r["service"])] for r in report["cashiers"]]
    rows += [[], ["Выручка по дням"], ["День", "Заказов", "Выручка, ₸"]]
    rows += [[r["day"], r["orders"], _num(r["revenue"])] for r in report["daily"]]
    return rows


def export_report_csv(path, report, label):
    """CSV для Excel: UTF-8 с BOM, разделитель «;». Возвращает число записанных строк."""
    rows = report_rows(report, label)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f, delimiter=";").writerows(rows)
    return len(rows)


def export_orders_csv(path, orders):
    """История заказов (из db.load_orders) в CSV."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["№", "Создан", "Кассир", "Статус", "Подытог, ₸", "Надбавка, ₸", "Итого, ₸", "Аннулирован",
                    "Причина", "Состав"])
        for o in orders:
            w.writerow([o["id"], o["created_at"], o["cashier_name"], o["status"], o["subtotal"],
                        o["service_amount"], o["total"], "да" if o["voided"] else "нет", o["void_reason"],
                        "; ".join(f"{i['item_name']} × {i['quantity']}" for i in o["items"])])
    return len(orders)
