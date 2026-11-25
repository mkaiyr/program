#!/usr/bin/env python3
"""Проверка контраста пар цветов по WCAG 2.1 (AA). Цвета берутся из docs/assets/css/tokens.css, чтобы проверка не расходилась с сайтом.
Запуск из корня репозитория:  python3 tools/check_contrast.py"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
css = (ROOT / "docs" / "assets" / "css" / "tokens.css").read_text(encoding="utf-8")
V = {m.group(1): m.group(2).upper() for m in re.finditer(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", css)}
# переменные-ссылки: --text: var(--espresso-900)
for m in re.finditer(r"--([a-z0-9-]+):\s*var\(--([a-z0-9-]+)\)", css):
    V.setdefault(m.group(1), V.get(m.group(2)))

def lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)

def blend(fg, bg, a):
    """Цвет fg с непрозрачностью a поверх bg."""
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]; b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a * x + (1 - a) * y):02X}" for x, y in zip(f, b))

# (описание, цвет, фон, минимум): 4.5 — обычный текст, 3.0 — крупный текст и элементы интерфейса
PAIRS = [
    ("основной текст на фоне", V["text"], V["bg"], 4.5),
    ("основной текст на карточке", V["text"], V["surface"], 4.5),
    ("основной текст на втором фоне", V["text"], V["cream-100"], 4.5),
    ("приглушённый текст на фоне", V["text-muted"], V["bg"], 4.5),
    ("приглушённый текст на карточке", V["text-muted"], V["surface"], 4.5),
    ("приглушённый текст на втором фоне", V["text-muted"], V["cream-100"], 4.5),
    ("белый текст на основной кнопке", V["on-accent"], V["accent"], 4.5),
    ("белый текст на кнопке при наведении", V["on-accent"], V["accent-hover"], 4.5),
    ("ссылка на фоне", V["accent-hover"], V["bg"], 4.5),
    ("ссылка на карточке", V["accent-hover"], V["surface"], 4.5),
    ("слово «Бистро» в логотипе (крупный текст)", V["terracotta-600"], V["bg"], 3.0),
    ("«в наличии»", V["olive-700"], V["olive-100"], 4.5),
    ("предупреждение об аллергене", V["honey-700"], V["honey-100"], 4.5),
    ("ошибка на светлом фоне", V["danger-700"], V["danger-100"], 4.5),
    ("текст ошибки поля на фоне страницы", V["danger-700"], V["bg"], 4.5),
    ("плашка: текст", "#F5ECDD", V["espresso-900"], 4.5),
    ("плашка: выделенный текст", "#FFE2CF", V["espresso-900"], 4.5),
    ("подвал: текст", "#E9DCCB", V["espresso-900"], 4.5),
    ("подвал: ссылки", "#FFD9C2", V["espresso-900"], 4.5),
    ("текущий пункт меню (белый на эспрессо)", "#FFFFFF", V["espresso-900"], 4.5),
    ("вторичная кнопка при наведении", "#FFFFFF", V["espresso-900"], 4.5),
    ("метка: текст на кремовом", V["espresso-700"], V["cream-100"], 4.5),
    ("кольцо фокуса на фоне страницы (элемент интерфейса)", V["focus"], V["bg"], 3.0),
    ("кольцо фокуса на карточке", V["focus"], V["surface"], 3.0),
    ("граница поля ввода на белом (элемент интерфейса)", V["field-border"], "#FFFFFF", 3.0),
    ("граница поля ввода на фоне страницы", V["field-border"], V["bg"], 3.0),
    ("граница поля ввода на карточке", V["field-border"], V["surface"], 3.0),
    ("граница кнопки «Подробнее» на фоне", V["espresso-900"], V["bg"], 3.0),
    ("уведомление-предупреждение: белый на меду", "#FFFFFF", V["honey-700"], 4.5),
    ("уведомление: белый на эспрессо", "#FFFFFF", V["espresso-900"], 4.5),
    ("подпись плитки категории: белый на затемнении 78% поверх БЕЛОГО фото (худший случай)", "#FFFFFF", blend(V["espresso-900"], "#FFFFFF", 0.78), 4.5),
    ("вторичный текст на второй фон (герой)", V["espresso-700"], V["cream-100"], 4.5),
    ("счётчик корзины: белый на терракоте", V["on-accent"], V["accent"], 4.5),
    ("счётчик корзины на текущем пункте: эспрессо на белом", V["espresso-900"], "#FFFFFF", 4.5),
    ("прогресс доставки (оливковый) на дорожке (элемент интерфейса)", V["olive-700"], V["cream-200"], 3.0),
]
bad = 0
for name, fg, bg, need in PAIRS:
    r = ratio(fg, bg)
    ok = r >= need
    bad += not ok
    print(f"{'✔' if ok else '✘'} {r:5.2f} (нужно ≥ {need}) {name}: {fg} на {bg}")
print("ИТОГ:", "контраст достаточный" if not bad else f"недостаточный контраст: {bad}")
sys.exit(1 if bad else 0)
