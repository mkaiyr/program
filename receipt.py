"""Чек заказа: что напечатать. Чистая логика — без Tk, сети, Pillow и базы данных.

Чек строится ТОЛЬКО из уже загруженного заказа (словарь из Database.load_orders). Здесь нет обращений к базе, оплате или складу:
сформировать, показать и напечатать чек (в том числе повторно) нельзя так, чтобы это изменило заказ.
Это обычный внутренний чек кафе, а не фискальный документ — на чеке об этом сказано.
"""

import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from textwrap import wrap

from money import fmt_money

COLS = 40                     # знаков в строке при растровой печати 80 мм (шрифт 22 px, 576 точек, с полями по краям)
CELL_DOTS = 13.25              # ширина знака шрифта чека (DejaVu Sans Mono, 22 px) в точках принтера
FISCAL_NOTE = "Чек не является фискальным документом"
DEFAULT_CAFE = "Кафе Бистро"


def cols_for_width(width_dots):
    """Сколько знаков помещается в строку при данной ширине печати (с полями по краям): 576 точек — 40, 512 — 36."""
    return max(24, min(COLS, int((width_dots - 24) // CELL_DOTS)))


class ReceiptError(Exception):
    """Заказ нельзя превратить в чек (повреждённые или неполные данные)."""


@dataclass(frozen=True)
class Line:
    text: str = ""
    bold: bool = False
    align: str = "left"       # left | center | right
    kind: str = "text"        # text | rule | blank


@dataclass
class Receipt:
    order_id: int
    lines: list = field(default_factory=list)
    copy_no: int = 0          # 0 — первая печать, 1 и больше — повторные
    voided: bool = False
    cols: int = COLS

    def text(self):
        """Обычный текст чека (для предпросмотра без Pillow и для проверок)."""
        out = []
        for ln in self.lines:
            if ln.kind == "rule":
                out.append("-" * self.cols)
            elif ln.kind == "blank":
                out.append("")
            else:
                pad = {"center": ln.text.center(self.cols), "right": ln.text.rjust(self.cols)}.get(ln.align, ln.text)
                out.append(pad.rstrip())
        return "\n".join(out)


def clean(value):
    """Текст для чека: единая (составная) форма Unicode, без управляющих и невидимых знаков, пробелы схлопнуты.
    Без этого «й» или «ё», записанные как буква + отдельный знак, рисуются неверно, а перевод строки или BEL попадают в чек."""
    s = unicodedata.normalize("NFC", str(value if value is not None else ""))
    s = "".join(" " if (c.isspace() or unicodedata.category(c) == "Zs") else c for c in s if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn") or c.isspace())
    return " ".join(s.split())


def _money(value):
    return fmt_money(Decimal(str(value)))


def _two(left, right, cols):
    """Слева текст, справа сумма в одну строку (если не влезает — сумма уходит на следующую)."""
    if len(left) + len(right) + 1 <= cols:
        return [left + " " * (cols - len(left) - len(right)) + right]
    return (wrap(left, cols) or [""]) + [right.rjust(cols)]


def _check_order(order):
    if not isinstance(order, dict) or not order.get("id"):
        raise ReceiptError("У заказа нет номера — чек сформировать нельзя.")
    items = order.get("items")
    if not isinstance(items, list) or not items:
        raise ReceiptError("В заказе нет позиций — чек сформировать нельзя.")
    total = Decimal("0")
    for item in items:
        try:
            qty, price, line = int(item["quantity"]), Decimal(str(item["unit_price"])), Decimal(str(item["line_total"]))
            name = clean(item["item_name"])
        except (KeyError, TypeError, ValueError, ArithmeticError) as e:
            raise ReceiptError("Позиция заказа повреждена — чек сформировать нельзя.") from e
        if qty < 1 or not name or price < 0 or line != price * qty:
            raise ReceiptError("Сумма позиции не сходится с ценой и количеством — чек не сформирован.")
        total += line
    try:
        subtotal = Decimal(str(order.get("subtotal", order["total"])))
        service = Decimal(str(order.get("service_amount") or 0))
        grand = Decimal(str(order["total"]))
    except (KeyError, ArithmeticError) as e:
        raise ReceiptError("В заказе нет итоговой суммы — чек сформировать нельзя.") from e
    if subtotal != total or subtotal + service != grand:
        raise ReceiptError("Итоги заказа не сходятся с позициями — чек не сформирован, обратитесь к администратору.")
    return subtotal, service, grand


def build_receipt(order, *, cafe_name=DEFAULT_CAFE, copy_no=0, printed_at="", payment_method="", cols=COLS):
    """Чек из заказа. copy_no > 0 — повторная печать (на чеке отмечено); payment_method печатается, только если он известен."""
    subtotal, service, grand = _check_order(order)
    voided = bool(order.get("voided"))
    L = []
    def add(text="", **kw):
        for part in (wrap(text, cols) or [""]) if text else [""]:       # любая строка переносится по ширине бумаги
            L.append(Line(part, **kw))
    if voided:
        add("ЗАКАЗ АННУЛИРОВАН", bold=True, align="center")
        add(kind="blank")
    if copy_no > 0:
        add(f"*** ПОВТОРНАЯ ПЕЧАТЬ (копия {copy_no}) ***", bold=True, align="center")
        add(kind="blank")
    for part in wrap(clean(cafe_name) or DEFAULT_CAFE, cols) or [DEFAULT_CAFE]:
        add(part, bold=True, align="center")
    add(f"Чек заказа № {order['id']}", align="center")
    add(kind="rule")
    if order.get("created_at"):
        add(f"Дата: {clean(order['created_at'])}")
    if clean(order.get("cashier_name")) not in ("", "—"):
        add(f"Кассир: {clean(order['cashier_name'])}")
    add(kind="rule")
    for item in order["items"]:
        for part in wrap(clean(item["item_name"]), cols) or [""]:
            add(part)
        qty, price = int(item["quantity"]), _money(item["unit_price"])
        for part in _two(f"  {qty} x {price}", _money(item["line_total"]), cols):
            add(part)
    add(kind="rule")
    discount = Decimal(str(order.get("discount") or 0))
    if service > 0 or discount > 0:
        for part in _two("Подытог", _money(subtotal), cols):
            add(part)
    if discount > 0:
        for part in _two("Скидка", "-" + _money(discount), cols):
            add(part)
    if service > 0:
        pct = Decimal(str(order.get("service_percent") or 0))
        label = f"Надбавка за обслуживание {pct.normalize():f}%" if pct else "Надбавка за обслуживание"
        for part in _two(label, _money(service), cols):
            add(part)
    for part in _two("ИТОГО", _money(grand - discount), cols):
        add(part, bold=True)
    if clean(payment_method):
        add(f"Оплата: {clean(payment_method)}")
    add(kind="rule")
    add("Спасибо за заказ!", align="center")
    add(FISCAL_NOTE, align="center")
    if copy_no > 0 and clean(printed_at):
        add(f"Напечатано: {clean(printed_at)}", align="center")
    return Receipt(order_id=int(order["id"]), lines=L, copy_no=copy_no, voided=voided, cols=cols)
