"""Разовая смена пароля сотрудника (запускает администратор на своём компьютере).

Нужен секретный ключ Supabase: он вводится скрыто при запуске и нигде не сохраняется.
UUID пользователя — Authentication -> Users в панели Supabase (не код сотрудника).
"""

import getpass
import uuid

from supabase import create_client

URL = input("Вставь Project URL из Supabase: ").strip()
KEY = getpass.getpass("Вставь секретный API key (ввод скрыт): ").strip()

if not URL.startswith("https://") or not KEY:
    print("Ошибка: проверь URL и ключ.")
    raise SystemExit(1)

user_text = input("UUID пользователя (Authentication -> Users): ").strip()
try:
    user_id = str(uuid.UUID(user_text))
except ValueError:
    print("Это не UUID. Пример: 6f1c2b3a-1234-4cde-9abc-0123456789ab")
    raise SystemExit(1)

password = getpass.getpass("Введи новый пароль сотрудника (ввод скрыт): ")
confirm = getpass.getpass("Повтори новый пароль: ")

if len(password) < 8:
    print("Пароль должен содержать не менее 8 символов.")
    raise SystemExit(1)

if password != confirm:
    print("Пароли не совпадают.")
    raise SystemExit(1)

confirmation = input(f"Изменить пароль только для пользователя {user_id}? Напиши YES: ").strip()

if confirmation != "YES":
    print("Отменено. Пароль не изменён.")
    raise SystemExit(0)

client = create_client(URL, KEY)
try:
    client.auth.admin.update_user_by_id(user_id, {"password": password})
    print("Пароль изменён. Сотрудник входит своим кодом и новым паролем.")
except Exception as error:
    # Текст ошибки Auth (например, «User not found») ключа не содержит
    print(f"Не удалось изменить пароль: {type(error).__name__}: {error}")
finally:
    password = ""
    confirm = ""
    KEY = ""
