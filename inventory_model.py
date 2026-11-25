"""Эталонная модель склада: правила, которым должна подчиняться будущая серверная реализация (SQL-функции в базе).

ЭТО НЕ ПОДКЛЮЧЕНО К ПРИЛОЖЕНИЮ И НЕ ЗАМЕНЯЕТ БАЗУ. Модель — исполняемая спецификация: каждая операция описана на чистом Python с Decimal, а
docs/contracts/inventory_vectors.json содержит сценарии, которые обязана проходить и эта модель, и будущие SQL-функции (одни и те же входы — одни и те же результаты).
Реальный склад живёт в общей базе; приложение и сайт будут вызывать только серверные функции. Здесь нет рецептур и цен кафе — только механика.
"""

from decimal import ROUND_HALF_UP, Decimal

Q = Decimal("0.001")
# единица -> (измерение, коэффициент к базовой единице: г, мл, шт)
UNITS = {"кг": ("mass", Decimal(1000)), "г": ("mass", Decimal(1)), "л": ("volume", Decimal(1000)), "мл": ("volume", Decimal(1)), "шт": ("count", Decimal(1)), "шт.": ("count", Decimal(1))}


class StockError(Exception):
    def __init__(self, code, message="", data=None):
        super().__init__(message or code)
        self.code, self.data = code, data or {}


def dec(v):
    return Decimal(str(v)).quantize(Q, rounding=ROUND_HALF_UP)


def convert(qty, from_unit, to_unit):
    """Количество из одной единицы в другую той же величины (кг↔г, л↔мл). Разные величины (кг→шт) — ошибка."""
    if from_unit not in UNITS or to_unit not in UNITS:
        raise StockError("UNKNOWN_UNIT", f"{from_unit}→{to_unit}")
    (d1, k1), (d2, k2) = UNITS[from_unit], UNITS[to_unit]
    if d1 != d2:
        raise StockError("UNIT_MISMATCH", f"{from_unit}→{to_unit}")
    return dec(Decimal(str(qty)) * k1 / k2)


def consumption(order_items, recipes, products):
    """Расход продуктов по заказу. order_items: [{dish_id, qty}], recipes: {dish_id: [{product_id, qty, unit}]}, products: {id: {unit, ...}}.
    Возвращает (расход {product_id: Decimal в единице склада}, блюда без технологической карты). Блюдо без карты ничего не списывает."""
    need, missing = {}, []
    for it in order_items:
        recipe = recipes.get(it["dish_id"]) or []
        if not recipe:
            if it["dish_id"] not in missing:
                missing.append(it["dish_id"])
            continue
        for line in recipe:
            p = products.get(line["product_id"])
            if p is None:
                raise StockError("UNKNOWN_PRODUCT", str(line["product_id"]))
            need[line["product_id"]] = need.get(line["product_id"], Decimal(0)) + convert(Decimal(str(line["qty"])) * int(it["qty"]), line["unit"], p["unit"])
    return {k: dec(v) for k, v in need.items()}, missing


class Warehouse:
    """Остатки + журнал движений. Остаток всегда равен сумме движений; отрицательным он не бывает; повторная операция с тем же ключом ничего не меняет."""

    def __init__(self, products):
        self.products = {k: dict(v) for k, v in products.items()}
        self.stock = {k: dec(v.get("stock", 0)) for k, v in products.items()}
        self.ledger = []                       # {product_id, delta, kind, ref}
        self.done = {}                         # (kind, ref) -> результат: защита от повторов (в базе — уникальный ключ)

    def _post(self, product_id, delta, kind, ref):
        self.stock[product_id] = dec(self.stock[product_id] + delta)
        self.ledger.append({"product_id": product_id, "delta": dec(delta), "kind": kind, "ref": ref})

    def verify(self):
        """Журнал сходится с остатками, отрицательных остатков нет."""
        for pid, qty in self.stock.items():
            total = dec(sum((m["delta"] for m in self.ledger if m["product_id"] == pid), Decimal(0)) + dec(self.products[pid].get("stock", 0)))
            if total != qty or qty < 0:
                return False
        return True

    def sell(self, order_id, order_items, recipes):
        """Списание по продаже. Всё или ничего: при нехватке любого продукта не меняется ничего. Повтор того же заказа — без изменений."""
        key = ("sale", order_id)
        if key in self.done:
            return {**self.done[key], "duplicate": True}
        need, missing = consumption(order_items, recipes, self.products)
        short = [{"product_id": p, "need": n, "have": self.stock[p]} for p, n in sorted(need.items()) if self.stock[p] < n]
        if short:
            raise StockError("INSUFFICIENT_STOCK", "не хватает продуктов", {"short": short})
        for p, n in sorted(need.items()):
            self._post(p, -n, "sale", order_id)
        res = {"duplicate": False, "deducted": dict(need), "missing_recipe": missing}
        self.done[key] = res
        return res

    def dish_status(self, dish_id, recipes, qty=1):
        """Складской статус блюда для кассы: 'ok' (карта есть, хватает), 'insufficient' (карта есть, не хватает — продажа блокируется), 'no_recipe' (карты нет:
        ничего не списывается, а наличие НЕ подтверждено складом — это предупреждение, а не «в наличии»)."""
        if not recipes.get(dish_id):
            return {"status": "no_recipe", "short": []}
        try:
            need, _ = consumption([{"dish_id": dish_id, "qty": qty}], recipes, self.products)
        except StockError:
            return {"status": "no_recipe", "short": []}
        short = [{"product_id": p, "need": n, "have": self.stock[p]} for p, n in sorted(need.items()) if self.stock[p] < n]
        return {"status": "insufficient" if short else "ok", "short": short}

    def _net_sold(self, order_id):
        """Сколько каждого продукта заказ списал и ещё не вернул (продажа минус любые возвраты по нему)."""
        net = {}
        for m in self.ledger:
            if m["ref"] == order_id and m["kind"] in ("sale", "cancel", "return"):
                net[m["product_id"]] = net.get(m["product_id"], Decimal(0)) - m["delta"]
        return {pid: dec(v) for pid, v in net.items() if v > 0}

    def cancel_order(self, order_id, cooking_started):
        """Отмена заказа. До начала приготовления — автоматически возвращает всё списанное (один раз). После начала — склад НЕ меняется:
        нужна отдельная операция return_selected (что реально пригодно к возврату) и/или write_off (потери)."""
        key = ("cancel", order_id)
        if key in self.done:
            return {**self.done[key], "duplicate": True}
        if cooking_started:
            res = {"duplicate": False, "restored": {}, "manual_required": bool(self._net_sold(order_id))}
        else:
            restored = self._net_sold(order_id)
            for pid, qty in restored.items():
                self._post(pid, qty, "cancel", order_id)
            res = {"duplicate": False, "restored": restored, "manual_required": False}
        self.done[key] = res
        return res

    def return_selected(self, order_id, ref, lines, reason):
        """Возврат пригодных продуктов по заказу (после начала приготовления или возврат готового заказа): причина обязательна, количество по продукту
        не больше списанного и ещё не возвращённого. Повтор с тем же ref ничего не меняет."""
        key = ("return", order_id, ref)
        if key in self.done:
            return {**self.done[key], "duplicate": True}
        if not str(reason).strip():
            raise StockError("REASON_REQUIRED")
        left = self._net_sold(order_id)
        for ln in lines:
            q = dec(ln["qty"])
            if q <= 0:
                raise StockError("INVALID_LINE")
            if q > left.get(ln["product_id"], Decimal(0)):
                raise StockError("RETURN_EXCEEDS", data={"product_id": ln["product_id"], "left": left.get(ln["product_id"], Decimal(0))})
            left[ln["product_id"]] -= q
        restored = {}
        for ln in lines:
            self._post(ln["product_id"], dec(ln["qty"]), "return", order_id)
            restored[ln["product_id"]] = restored.get(ln["product_id"], Decimal(0)) + dec(ln["qty"])
        res = {"duplicate": False, "restored": {k: dec(v) for k, v in restored.items()}}
        self.done[key] = res
        return res

    def write_off(self, ref, product_id, qty, reason):
        """Списание потерь (порча, брак, использованные в отменённом заказе продукты): причина обязательна, больше остатка списать нельзя, повтор по ref не меняет."""
        key = ("loss", ref)
        if key in self.done:
            return {**self.done[key], "duplicate": True}
        if not str(reason).strip():
            raise StockError("REASON_REQUIRED")
        q = dec(qty)
        if q <= 0:
            raise StockError("INVALID_LINE")
        if q > self.stock[product_id]:
            raise StockError("INSUFFICIENT_STOCK", data={"short": [{"product_id": product_id, "need": q, "have": self.stock[product_id]}]})
        self._post(product_id, -q, "loss", ref)
        res = {"duplicate": False, "written_off": q}
        self.done[key] = res
        return res

    def confirm_receipt(self, receipt_id, lines):
        """Подтверждение поступления: остаток растёт на количество (в единице склада); повторное подтверждение не меняет остаток."""
        key = ("receipt", receipt_id)
        if key in self.done:
            return {**self.done[key], "duplicate": True}
        total = Decimal(0)
        for ln in lines:
            if Decimal(str(ln["qty"])) <= 0 or Decimal(str(ln["unit_price"])) < 0:
                raise StockError("INVALID_LINE")
        for ln in lines:
            qty = convert(ln["qty"], ln["unit"], self.products[ln["product_id"]]["unit"])
            self._post(ln["product_id"], qty, "receipt", receipt_id)
            total += Decimal(str(ln["qty"])) * Decimal(str(ln["unit_price"]))
        res = {"duplicate": False, "total": total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)}
        self.done[key] = res
        return res

    def confirm_count(self, count_id, lines):
        """Подтверждение инвентаризации: для каждой позиции движение «корректировка» = факт − текущий остаток; причина обязательна при расхождении."""
        key = ("count", count_id)
        if key in self.done:
            return {**self.done[key], "duplicate": True}
        for ln in lines:
            if Decimal(str(ln["actual"])) < 0:
                raise StockError("NEGATIVE_ACTUAL")
            if dec(ln["actual"]) != self.stock[ln["product_id"]] and not str(ln.get("reason", "")).strip():
                raise StockError("REASON_REQUIRED", data={"product_id": ln["product_id"]})
        diffs = {}
        for ln in lines:
            delta = dec(ln["actual"]) - self.stock[ln["product_id"]]
            if delta != 0:
                self._post(ln["product_id"], delta, "count", count_id)
            diffs[ln["product_id"]] = delta
        res = {"duplicate": False, "diffs": diffs}
        self.done[key] = res
        return res
