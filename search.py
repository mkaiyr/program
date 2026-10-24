"""Поиск позиций по коду и названию — одинаковый во всех панелях.

Работает локально по уже загруженному списку, без запросов к Supabase:
на 1000 позиций это доли миллисекунды.

Правила:
* пробелы по краям не важны, регистр не важен;
* запрос из цифр совпадает с кодом полностью или по началу (1001, 100),
  а также с цифрами в названии;
* иначе — подстрока названия;
* точное совпадение кода идёт первым.
"""


def normalize(query):
    return " ".join(str(query).split()).casefold()


def search(items, query, name_key="name"):
    """Отфильтрованный список; порядок исходный, точный код — первым."""
    q = normalize(query)
    if not q:
        return list(items)
    exact, rest = [], []
    for item in items:
        code = str(item.get("code") or "")
        if q.isdigit() and code == q:
            exact.append(item)
        elif (q.isdigit() and code.startswith(q)) or q in item[name_key].casefold():
            rest.append(item)
    return exact + rest


def find_by_code(items, query):
    """Позиция с точно таким кодом или None."""
    q = normalize(query)
    if not q.isdigit():
        return None
    return next((item for item in items if str(item.get("code") or "") == q), None)
