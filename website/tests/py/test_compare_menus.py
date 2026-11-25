"""Проверка инструмента сверки меню сайта и кассы (tools/compare_menus.py)."""
import json
import sys
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import compare_menus as cm  # noqa: E402

SEED = "insert into public.menu_items (name, category, price) values\n  ('Американо', 'Напитки', 800.00),\n  ('Чай чёрный', 'Напитки', 500.00),\n  ('Круассан', 'Выпечка', 700.00);\n"
S = lambda n, c, p, a=True, i="x": {"name": n, "category": c, "price": D(p), "available": a, "id": i}


class CompareTest(unittest.TestCase):
    def test_seed_parsing(self):
        rows = cm.parse_seed(SEED)
        self.assertEqual([(r["name"], str(r["price"])) for r in rows], [("Американо", "800.00"), ("Чай чёрный", "500.00"), ("Круассан", "700.00")])

    def test_price_category_availability_differences(self):
        r = cm.compare([S("Американо", "Напитки", 890), S("Чай", "Напитки", 500, False), S("Борщ", "Супы", 1000)],
                       [S("американо", "Напитки", 800), S("Чай", "Горячее", 500, True), S("Круассан", "Выпечка", 700)])
        d = {m[0]["name"]: m[2] for m in r["matched"]}
        self.assertEqual(len(d["Американо"]), 1)
        self.assertIn("цена", d["Американо"][0])
        self.assertEqual(len(d["Чай"]), 2)
        self.assertEqual([s["name"] for s in r["only_site"]], ["Борщ"])
        self.assertEqual([c["name"] for c in r["only_cash"]], ["Круассан"])
        self.assertTrue(cm.differs(r))

    def test_yo_and_case_do_not_matter_and_identical_menus_match(self):
        r = cm.compare([S("Чай чёрный", "Напитки", 500)], [S("ЧАЙ ЧЕРНЫЙ", "напитки", 500)])
        self.assertFalse(cm.differs(r))

    def test_similar_names_are_suggestions_not_matches(self):
        r = cm.compare([S("Сырники со сметаной", "Десерты", 1690)], [S("Сырники со сметаной!", "Десерты", 1690), S("Совсем другое", "Х", 1)])
        self.assertEqual(len(r["matched"]), 1)
        r2 = cm.compare([S("Сырники", "Десерты", 1690)], [S("Сырникы", "Десерты", 1690)])
        self.assertEqual((len(r2["matched"]), len(r2["suggestions"])), (0, 1))

    def test_real_site_menu_vs_demo_seed_reports_known_gaps(self):
        site = cm.load_site(ROOT / "docs" / "data" / "menu.json")
        r = cm.compare(site, cm.parse_seed((ROOT.parent / "cashier_app" / "sql" / "seed" / "demo_menu.sql").read_text(encoding="utf-8")) if (ROOT.parent / "cashier_app" / "sql" / "seed" / "demo_menu.sql").exists() else cm.parse_seed(SEED))
        self.assertTrue(cm.differs(r))

    def test_cash_json_and_strict_exit_code(self):
        d = Path(tempfile.mkdtemp())
        (d / "cash.json").write_text(json.dumps([{"id": 1, "code": 1, "name": "Чизбургер", "category": "Бургеры", "price": 2290, "is_available": True}]), encoding="utf-8")
        self.assertEqual(cm.main(["--cash", str(d / "cash.json")]), 0)               # без --strict расхождения не меняют код возврата
        self.assertEqual(cm.main(["--cash", str(d / "cash.json"), "--strict"]), 1)    # остальные блюда сайта в кассе отсутствуют

    def test_full_table_covers_every_item_once_and_leaves_decision_to_owner(self):
        site = [S("Американо", "Напитки", 890, i="americano"), S("Борщ", "Супы", 1000, i="borsch"), S("Сырники", "Десерты", 1690, i="syrniki"), S("Чай", "Напитки", 500, i="tea")]
        cash = [S("Американо", "Напитки", 800, i=1), S("Круассан", "Выпечка", 700, i=2), S("Сырникы", "Десерты", 1700, i=3), S("Чай", "Напитки", 500, i=4)]
        table, cnt, n = cm.full_table(cm.compare(site, cash))
        self.assertEqual(n, 5)                                                         # 4 блюда сайта + «Круассан»; «Сырники» и «Сырникы» — одна пара, а не две позиции
        self.assertEqual(cnt, {"отличаются цены": 1, "отличаются названия (возможная пара)": 1, "только касса": 1, "только сайт": 1, "совпадают": 1})
        self.assertEqual(table.count("требует утверждения владельцем"), 5)             # ничего не считается подтверждённым
        self.assertIn("касса «Сырникы» ~ сайт «Сырники»", table)


if __name__ == "__main__":
    unittest.main()
