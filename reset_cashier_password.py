import getpass
from supabase import create_client

URL = input("Вставь Project URL из Supabase: ").strip()
KEY = getpass.getpass("Вставь секретный API key (ввод скрыт): ").strip()

if not URL.startswith("https://") or not KEY:
    print("Ошибка: проверь URL и ключ.")
    raise SystemExit(1)

client = create_client(URL, KEY)

user_id = "..."

password = getpass.getpass("Введи новый пароль кассира (ввод скрыт): ")
confirm = getpass.getpass("Повтори новый пароль: ")

if len(password) < 8:
    print("Пароль должен содержать не менее 8 символов.")
    raise SystemExit(1)

if password != confirm:
    print("Пароли не совпадают.")
    raise SystemExit(1)

confirmation = input(
    "Изменить пароль только для ... (ID кассира)? Напиши YES: "
).strip()

if confirmation != "YES":
    print("Отменено. Пароль не изменён.")
    raise SystemExit(0)

try:
    client.auth.admin.update_user_by_id(
        user_id,
        {"password": password}
    )
    print("Запрос выполнен. Попробуй войти кодом 1002 и новым паролем.")
except Exception as error:
    print("Не удалось изменить пароль. Проверь сообщение об ошибке:")
    print(error)
finally:
    password = ""
    confirm = ""
    KEY = ""
