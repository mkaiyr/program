#!/usr/bin/env python3
"""Сверка меню сайта (docs/data/menu.json) с меню кассы: названия, цены, категории, доступность.
Меню кассы можно задать двумя способами (Production Supabase эта программа НЕ читает):
  --cash файл.json   выгрузка меню кассы: список {id, code, name, category, price, is_available} (её делает сотрудник через приложение/SQL Editor)
  --seed файл.sql    демонстрационный набор кассы sql/seed/demo_menu.sql из репозитория приложения
Блюда сопоставляются по названию (без регистра и «ё»); похожие названия предлагаются как возможные пары, но не считаются совпадением.
Запуск:  python3 tools/compare_menus.py (--cash menu_cash.json | --seed demo_menu.sql) [--site docs/data/menu.json] [--strict] [--full]
--full: полная таблица «каждая позиция — статус — что решить владельцу» (Markdown); неподтверждённые позиции помечаются «требует утверждения владельцем».
--strict: код возврата 1, если есть любое расхождение (для проверок, когда меню должно совпадать полностью)."""
import difflib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

def norm(s): return re.sub(r"[^a-zа-я0-9]+", " ", str(s).lower().replace("ё", "е")).strip()

def load_site(path):
    m = json.loads(Path(path).read_text(encoding="utf-8")); cats = {c["id"]: c["name"] for c in m["categories"]}
    return [{"name": d["name"], "category": cats.get(d["category"], d["category"]), "price": Decimal(str(d["price"])), "available": bool(d["available"]), "id": d["id"]} for d in m["dishes"]]

def parse_seed(text):
    body = text.split("insert into public.menu_items", 1)[1].split(";", 1)[0]
    return [{"name": n, "category": c, "price": Decimal(p), "available": True, "id": None}
            for n, c, p in re.findall(r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*([0-9.]+)\s*\)", body)]

def load_cash(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{"name": r["name"], "category": r["category"], "price": Decimal(str(r["price"])), "available": bool(r.get("is_available", True)), "id": r.get("id")} for r in rows]

def compare(site, cash):
    """Возвращает словарь: matched [(site, cash, [расхождения])], only_site, only_cash, suggestions."""
    by_cash, used = {norm(c["name"]): c for c in cash}, set()
    matched, only_site = [], []
    for s in site:
        c = by_cash.get(norm(s["name"]))
        if not c: only_site.append(s); continue
        used.add(norm(c["name"])); diffs = []
        if s["price"] != c["price"]: diffs.append(f"цена: сайт {s['price']} ₸, касса {c['price']} ₸")
        if norm(s["category"]) != norm(c["category"]): diffs.append(f"категория: сайт «{s['category']}», касса «{c['category']}»")
        if s["available"] != c["available"]: diffs.append(f"доступность: сайт {'да' if s['available'] else 'нет'}, касса {'да' if c['available'] else 'нет'}")
        matched.append((s, c, diffs))
    only_cash = [c for c in cash if norm(c["name"]) not in used]
    sugg = []
    for c in only_cash:
        best = max(only_site, key=lambda s: difflib.SequenceMatcher(None, norm(s["name"]), norm(c["name"])).ratio(), default=None)
        if best and difflib.SequenceMatcher(None, norm(best["name"]), norm(c["name"])).ratio() >= 0.8: sugg.append((c, best))
    return {"matched": matched, "only_site": only_site, "only_cash": only_cash, "suggestions": sugg}

def report(r):
    out = []
    bad = [m for m in r["matched"] if m[2]]
    out.append(f"Совпали по названию: {len(r['matched'])} (с расхождениями: {len(bad)}); только на сайте: {len(r['only_site'])}; только в кассе: {len(r['only_cash'])}")
    if r["matched"]:
        out += ["", "| Блюдо | Сайт: id | Касса: id | Расхождения |", "|---|---|---|---|"]
        for s, c, d in r["matched"]: out.append(f"| {s['name']} | {s['id']} | {c['id'] if c['id'] is not None else '—'} | {'; '.join(d) or 'нет'} |")
    if r["only_cash"]:
        out += ["", "Есть в кассе, нет на сайте: " + ", ".join(f"{c['name']} ({c['price']} ₸, {c['category']})" for c in r["only_cash"])]
    if r["only_site"]:
        out += ["", "Есть на сайте, нет в кассе: " + ", ".join(s["name"] for s in r["only_site"])]
    for c, s in r["suggestions"]: out.append(f"Возможная пара (не совпадение!): касса «{c['name']}» ~ сайт «{s['name']}»")
    return "\n".join(out)

def full_table(r):
    """Полная таблица сверки. Статусы: совпадают, отличаются цены, отличается категория, отличаются названия (возможная пара), только касса, только сайт.
    Ничего не считается подтверждённым автоматически: любая позиция требует утверждения владельцем (цены демонстрационные)."""
    rows, sugg = [], {c["name"]: s for c, s in r["suggestions"]}
    for s, c, d in r["matched"]:
        st = "совпадают" if not d else ("отличаются цены" if any(x.startswith("цена") for x in d) else "отличается категория/доступность")
        if d and any(x.startswith("цена") for x in d) and any(x.startswith("категория") for x in d): st = "отличаются цены и категория"
        rows.append((st, s["name"], s["id"], f"{s['price']} ₸ / {s['category']}", f"{c['price']} ₸ / {c['category']}", "; ".join(d) or "—"))
    for c in r["only_cash"]:
        pair = sugg.get(c["name"])
        rows.append(("отличаются названия (возможная пара)" if pair else "только касса", pair["name"] if pair else "—", pair["id"] if pair else "—", f"{pair['price']} ₸ / {pair['category']}" if pair else "—", f"{c['price']} ₸ / {c['category']}", (f"касса «{c['name']}» ~ сайт «{pair['name']}»" if pair else "нет на сайте")))
    paired = {s["name"] for _, s in r["suggestions"]}
    for s in r["only_site"]:
        if s["name"] not in paired: rows.append(("только сайт", s["name"], s["id"], f"{s['price']} ₸ / {s['category']}", "—", "нет в кассе"))
    order = {"отличаются цены": 0, "отличаются цены и категория": 0, "отличается категория/доступность": 1, "отличаются названия (возможная пара)": 2, "только касса": 3, "только сайт": 4, "совпадают": 5}
    rows.sort(key=lambda x: (order.get(x[0], 9), x[1]))
    out = ["| № | Статус | Блюдо на сайте | id сайта | Сайт: цена / категория | Касса: цена / категория | Расхождение | Решение владельца |", "|---|---|---|---|---|---|---|---|"]
    for i, (st, name, sid, sv, cv, note) in enumerate(rows, 1):
        out.append(f"| {i} | {st} | {name} | {sid} | {sv} | {cv} | {note} | требует утверждения владельцем |")
    cnt = {}
    for row in rows: cnt[row[0]] = cnt.get(row[0], 0) + 1
    return "\n".join(out), cnt, len(rows)


def differs(r): return bool(r["only_site"] or r["only_cash"] or any(m[2] for m in r["matched"]))

def main(argv):
    a = lambda k: argv[argv.index(k) + 1] if k in argv else None
    if not (a("--cash") or a("--seed")): raise SystemExit(__doc__)
    site = load_site(a("--site") or Path(__file__).resolve().parent.parent / "docs" / "data" / "menu.json")
    cash = load_cash(a("--cash")) if a("--cash") else parse_seed(Path(a("--seed")).read_text(encoding="utf-8"))
    r = compare(site, cash)
    if "--full" in argv:
        table, cnt, n = full_table(r); print(table); print(); print("Итого позиций:", n, "|", "; ".join(f"{k}: {v}" for k, v in cnt.items()))
    else: print(report(r))
    return 1 if "--strict" in argv and differs(r) else 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
