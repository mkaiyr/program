"""Эталонная модель склада проходит общие векторы (docs/contracts/inventory_vectors.json) и сохраняет инварианты на случайных последовательностях операций.
Те же векторы должны будут проходить SQL-функции склада — их ещё нет (нужно отдельное разрешение на схему)."""

import json
import random
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import inventory_model as im  # noqa: E402

V = json.loads((ROOT / "docs" / "contracts" / "inventory_vectors.json").read_text(encoding="utf-8"))


def warehouse(sc):
    return im.Warehouse(sc.get("products") or V["common"]["products"])


class VectorTest(unittest.TestCase):
    def test_units(self):
        for u in V["units"]:
            with self.subTest(u=u):
                if "error" in u:
                    with self.assertRaises(im.StockError) as cm:
                        im.convert(u["qty"], u["from"], u["to"])
                    self.assertEqual(cm.exception.code, u["error"])
                else:
                    self.assertEqual(str(im.convert(u["qty"], u["from"], u["to"])), u["expect"])

    def test_scenarios(self):
        recipes = V["common"]["recipes"]
        for sc in V["scenarios"]:
            with self.subTest(sc["name"]):
                w = warehouse(sc)
                for st in sc["steps"]:
                    exp = st["expect"]
                    call = {"sell": lambda: w.sell(st["order_id"], st["items"], recipes), "cancel": lambda: w.cancel_order(st["order_id"], st["cooking"]),
                            "return": lambda: w.return_selected(st["order_id"], st["ref"], st["lines"], st["reason"]),
                            "writeoff": lambda: w.write_off(st["ref"], st["product_id"], st["qty"], st["reason"]),
                            "status": lambda: w.dish_status(st["dish_id"], recipes, st["qty"]),
                            "receipt": lambda: w.confirm_receipt(st["id"], st["lines"]), "count": lambda: w.confirm_count(st["id"], st["lines"])}[st["op"]]
                    if "error" in exp:
                        with self.assertRaises(im.StockError) as cm:
                            call()
                        self.assertEqual(cm.exception.code, exp["error"])
                        if "short" in exp:
                            self.assertEqual([s["product_id"] for s in cm.exception.data["short"]], exp["short"])
                        continue
                    res = call()
                    for k, v in exp.items():
                        got = res[k]
                        if k == "short" and st["op"] == "status":
                            self.assertEqual([s["product_id"] for s in got], v)
                        elif isinstance(v, dict):
                            self.assertEqual({a: str(b) for a, b in got.items()}, v, k)
                        elif k == "total":
                            self.assertEqual(str(got), v)
                        else:
                            self.assertEqual(str(got) if isinstance(got, D) else got, v, k)
                self.assertEqual({k: str(v) for k, v in w.stock.items()}, sc["stock"])
                self.assertEqual(len(w.ledger), sc["ledger_len"])
                self.assertTrue(w.verify())


class InvariantTest(unittest.TestCase):
    def test_random_operations_keep_ledger_equal_to_stock_and_never_negative(self):
        rnd = random.Random(20261125)
        recipes = {"burger": V["common"]["recipes"]["burger"], "tea": []}
        for run in range(40):
            w = im.Warehouse(V["common"]["products"])
            for n in range(60):
                op = rnd.choice(["sell", "sell", "reverse", "receipt", "count", "sell_dup"])
                oid = rnd.randint(1, 15)
                try:
                    if op in ("sell", "sell_dup"):
                        w.sell(oid, [{"dish_id": rnd.choice(["burger", "tea"]), "qty": rnd.randint(1, 30)}], recipes)
                    elif op == "reverse":
                        w.cancel_order(oid, rnd.random() < 0.3)
                    elif op == "receipt":
                        w.confirm_receipt(rnd.randint(1, 8), [{"product_id": rnd.choice(list(w.products)), "qty": str(rnd.randint(1, 500)), "unit": w.products[rnd.choice(list(w.products))]["unit"], "unit_price": "1"}])
                    else:
                        pid = rnd.choice(list(w.products))
                        w.confirm_count(rnd.randint(1, 8), [{"product_id": pid, "actual": str(rnd.randint(0, 120)), "reason": "пересчёт"}])
                except (im.StockError, KeyError):
                    pass                                                   # отказ допустим — главное, что он ничего не испортил
                self.assertTrue(w.verify(), f"прогон {run}, шаг {n}")
                self.assertTrue(all(v >= 0 for v in w.stock.values()))

    def test_sale_then_cancel_before_cooking_restores_exactly_for_any_quantity(self):
        for q in (1, 2, 7, 33):
            w = im.Warehouse(V["common"]["products"])
            before = dict(w.stock)
            w.sell(1, [{"dish_id": "burger", "qty": q}], V["common"]["recipes"])
            w.cancel_order(1, False)
            self.assertEqual(w.stock, before)

    def test_returns_and_cancels_never_exceed_sales_per_order_in_random_runs(self):
        rnd = random.Random(7)
        recipes = {"burger": V["common"]["recipes"]["burger"]}
        for _ in range(40):
            w = im.Warehouse(V["common"]["products"])
            for n in range(50):
                oid = rnd.randint(1, 6)
                try:
                    r = rnd.random()
                    if r < 0.4:
                        w.sell(oid, [{"dish_id": "burger", "qty": rnd.randint(1, 5)}], recipes)
                    elif r < 0.6:
                        w.cancel_order(oid, rnd.random() < 0.5)
                    elif r < 0.85:
                        w.return_selected(oid, str(rnd.randint(1, 4)), [{"product_id": rnd.choice(list(w.products)), "qty": str(rnd.randint(1, 60))}], "пригодно")
                    else:
                        w.write_off(str(rnd.randint(1, 9)), rnd.choice(list(w.products)), str(rnd.randint(1, 40)), "потери")
                except im.StockError:
                    pass
            for oid in range(1, 7):
                for pid in w.products:
                    sold = -sum((m["delta"] for m in w.ledger if m["ref"] == oid and m["kind"] == "sale" and m["product_id"] == pid), D(0))
                    back = sum((m["delta"] for m in w.ledger if m["ref"] == oid and m["kind"] in ("cancel", "return") and m["product_id"] == pid), D(0))
                    self.assertLessEqual(back, sold)
            self.assertTrue(w.verify())
            self.assertTrue(all(v >= 0 for v in w.stock.values()))


if __name__ == "__main__":
    unittest.main()
