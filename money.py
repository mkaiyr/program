"""Деньги: надбавка за обслуживание и формат суммы. Только Decimal — без float.

Правило совпадает с серверной функцией place_order (sql/11_service_charge_void.sql):
надбавка = 10% от подытога, округление до целого тенге, половина — вверх.
Приложение считает так же, чтобы показать сумму до оформления и передать её серверу
как ожидаемый итог: расхождение (TOTAL_MISMATCH) заказ не сохраняет.
"""

from decimal import ROUND_HALF_UP, Decimal

SERVICE_PERCENT = Decimal(10)  # фиксированная надбавка, %
CENT = Decimal("0.01")
TENGE = Decimal(1)


def service_amount(subtotal):
    """Надбавка от подытога: 10%, до целого тенге (половина — вверх)."""
    return (Decimal(subtotal) * SERVICE_PERCENT / 100).quantize(TENGE, rounding=ROUND_HALF_UP).quantize(CENT)


def order_totals(subtotal):
    """Подытог -> (подытог, надбавка, итог), все с двумя знаками."""
    subtotal = Decimal(subtotal).quantize(CENT)
    service = service_amount(subtotal)
    return subtotal, service, subtotal + service


def fmt_money(amount):
    """1234.5 -> «1 234.50 ₸» (целые суммы — без копеек: «1 234 ₸»)."""
    amount = Decimal(amount)
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}".replace(",", " ") + " ₸"
    return f"{amount:,.2f}".replace(",", " ") + " ₸"
