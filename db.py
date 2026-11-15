"""Работа с Supabase: подключение, вход сотрудника, меню и заказы."""

import logging
import re
import threading
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from supabase import AuthApiError, AuthError, PostgrestAPIError, create_client

ALLOWED_ROLES = ("cashier", "admin", "kitchen", "bar")
CENT = Decimal("0.01")
MAX_PRICE = Decimal("99999999.99")  # предел numeric(10, 2)
PRICE_RE = re.compile(r"\d+([.,]\d{1,2})?")
EMPLOYEE_CODE_RE = re.compile(r"[0-9]{4,6}")  # совпадает с profiles_employee_code_format
MENU_CODE_RE = re.compile(r"[0-9]{1,8}")      # совпадает с menu_items_code_check (1..99999999)
BAD_LOGIN = "Неверный код сотрудника или пароль."
NO_NETWORK = "Нет связи с сервером. Проверьте интернет."
SIGN_OUT_WAIT = 15  # с: сколько новый вход ждёт отзыва прежней сессии на сервере (таймауты HTTP — 5 с)

# Статусы заказа и допустимые переходы (совпадают с sql/05_kitchen_status.sql)
ORDER_STATUSES = {"accepted": "Принят", "preparing": "Готовится", "ready": "Готов", "completed": "Выдан"}
NEXT_STATUS = {"accepted": "preparing", "preparing": "ready", "ready": "completed"}
# Какие переходы показывать каждой роли (окончательно права проверяет сервер).
# Кухня и бар двигают только свою часть заказа (set_station_status, sql/08_bar_stations.sql);
# общий статус «Готовится»/«Готов» сервер вычисляет из станций, поэтому set_order_status
# допускает только выдачу (ready -> completed). Администратор может явно отметить заказ
# готовым целиком — force_order_ready, отдельной кнопкой с подтверждением.
STATION_TRANSITIONS = {"accepted": "preparing", "preparing": "ready"}
ROLE_TRANSITIONS = {
    "admin": {"ready": "completed"},
    "kitchen": STATION_TRANSITIONS,
    "bar": STATION_TRANSITIONS,
    "cashier": {"ready": "completed"},
}
STATIONS = {"kitchen": "Кухня", "bar": "Бар"}
STATION_ORDERS_RPC = {"kitchen": "kitchen_orders", "bar": "bar_orders"}
# Состояния позиции меню (sql/09_station_menu.sql, функция _menu_item_status)
MENU_STATUSES = {"on_sale": "В продаже", "out_of_stock": "Нет в наличии",
                 "pending": "Ждёт решения", "rejected": "Отклонена"}
MAX_REJECT_REASON = 200  # совпадает с проверкой в admin_reject_menu_item
ORDERS_LIMIT = 100  # сколько последних заказов показывать

# Ошибки серверной функции create_order (поле message) -> (код для UI, текст)
RPC_ERRORS = {
    "MENU_CHANGED": ("menu_changed", "Меню изменилось: {details}. Меню обновлено, проверьте заказ."),
    "NOT_AUTHENTICATED": ("auth", "Сессия истекла. Войдите снова."),
    # Вызывают и касса, и кухня с баром: учётную запись деактивировали или сменили роль
    "NOT_ALLOWED": ("auth", "У этой учётной записи больше нет доступа к этому действию. Войдите снова."),
    "INVALID_REQUEST": ("invalid", "Некорректный запрос."),
    "INVALID_ITEMS": ("invalid", "Некорректный состав заказа."),
    "DUPLICATE_ITEM": ("invalid", "Позиция повторяется в заказе."),
    "REQUEST_ID_CONFLICT": ("invalid", "Конфликт идентификатора запроса. Попробуйте ещё раз."),
    # Функции управления меню (sql/02_admin_menu.sql)
    "ADMIN_REQUIRED": ("forbidden", "Это действие доступно только администратору."),
    "INVALID_NAME": ("invalid", "Название должно содержать от 1 до 100 символов."),
    "INVALID_CATEGORY": ("invalid", "Категория должна содержать от 1 до 50 символов."),
    "INVALID_PRICE": ("invalid", "Цена должна быть больше нуля, не более двух знаков после запятой."),
    "NAME_TAKEN": ("invalid", "Позиция с таким названием уже есть в меню."),
    "NOT_FOUND": ("invalid", "Позиция или категория не найдена. Обновите список."),
    # Коды позиций меню (sql/07_menu_codes.sql)
    "CODE_TAKEN": ("invalid", "Этот код уже занят другой позицией. Укажите другой или оставьте поле пустым."),
    "INVALID_CODE": ("invalid", "Код — целое число от 1 до 99999999."),
    "CODE_IMMUTABLE": ("invalid", "Код позиции изменить нельзя."),
    # Статусы заказов (sql/04_order_status.sql)
    "INVALID_STATUS": ("invalid", "Недопустимый статус заказа."),
    "ORDER_NOT_FOUND": ("order_changed", "Заказ не найден или недоступен. Список обновлён."),
    "INVALID_TRANSITION": ("order_changed", "Статус заказа уже изменён. Список обновлён."),
    "TRANSITION_NOT_ALLOWED": ("forbidden", "Этот переход статуса недоступен для вашей роли."),
    # Станции (sql/08_bar_stations.sql)
    "STATION_NOT_ALLOWED": ("forbidden", "Эта часть заказа готовится на другой станции."),
    "INVALID_STATION": ("invalid", "Неизвестная станция приготовления."),
    # Меню станций и решения администратора (sql/09_station_menu.sql)
    "ITEM_REJECTED": ("invalid", "Позиция отклонена администратором — изменить её нельзя. Обновите список."),
    "NOT_APPROVED": ("invalid", "Позиция ещё ждёт решения администратора. Обновите список."),
    "ALREADY_APPROVED": ("invalid", "Решение по позиции уже принято. Обновите список."),
    # Удаление заказов (sql/10_order_deletion.sql)
    "ORDER_NOT_COMPLETED": ("order_changed", "Удалить можно только выданный заказ. Список обновлён."),
    "ORDER_NOT_DELETED": ("order_changed", "Навсегда можно удалить только заказ из «Удалённых заказов». "
                                           "Список обновлён."),
    "PURGE_FAILED": (None, "Заказ не удалён: сервер отменил операцию, данные не изменены. Попробуйте ещё раз."),
    # Надбавка и аннулирование (sql/11_service_charge_void.sql)
    "TOTAL_MISMATCH": ("invalid", "Итоговая сумма на сервере отличается от показанной ({details}). "
                                  "Заказ не сохранён — обновите меню и проверьте заказ."),
    "INVALID_REASON": ("invalid", "Причина аннулирования — от 3 до 200 символов."),
    # Кассовые смены (sql/12_cash_shifts.sql)
    "SHIFT_ALREADY_OPEN": ("invalid", "Смена уже открыта."),
    "NO_OPEN_SHIFT": ("invalid", "Нет открытой смены. Откройте смену."),
    "INVALID_AMOUNT": ("invalid", "Сумма — число не меньше нуля, не более двух знаков после запятой."),
    "INVALID_KIND": ("invalid", "Неизвестный вид операции."),
    "INVALID_NOTE": ("invalid", "Комментарий — не длиннее 200 символов."),
    "INSUFFICIENT_CASH": ("invalid", "В кассе недостаточно наличных для изъятия (ожидается {details} ₸)."),
    # Сотрудники (sql/13_staff_management.sql)
    "INVALID_ROLE": ("invalid", "Неизвестная роль."),
    "INVALID_EMPLOYEE_CODE": ("invalid", "Код сотрудника — от 4 до 6 цифр."),
    "EMPLOYEE_CODE_TAKEN": ("invalid", "Этот код сотрудника уже занят."),
    "WEAK_PASSWORD": ("invalid", "Пароль — от 8 до 72 символов."),
    "STAFF_NOT_FOUND": ("invalid", "Сотрудник не найден. Обновите список."),
    "SELF_LOCKOUT": ("invalid", "Нельзя отключить или понизить собственную учётную запись."),
    "LAST_ADMIN": ("invalid", "Нельзя оставить систему без активного администратора."),
    # Архив блюд (sql/15_menu_item_archive.sql); NOT_FOUND, NOT_APPROVED, ITEM_REJECTED, ADMIN_REQUIRED — выше
    "NOT_ARCHIVED": ("invalid", "Удалить навсегда можно только блюдо из архива. Обновите список."),
    # Справочник категорий (sql/16_menu_categories.sql); CATEGORY_NOT_EMPTY разбирает _api_error (в details — счётчики)
    "CATEGORY_EXISTS": ("invalid", "Категория с таким названием уже есть."),
    "CATEGORY_NOT_FOUND": ("invalid", "Такой категории нет. Выберите существующую — новые категории добавляет администратор."),
    # Отчёты (sql/14_reports_categories.sql)
    "INVALID_PERIOD": ("invalid", "Некорректный период (не больше 366 дней)."),
}

# Ошибки PostgreSQL без своего кода в message (например, нарушение ограничения таблицы).
# Текст сервера сотруднику не показывается: в нём имена таблиц и ограничений.
PG_ERRORS = {
    "23514": ("invalid", "Изменение противоречит правилам базы данных. Обновите список и попробуйте снова."),
    "42501": ("forbidden", "Недостаточно прав для этого действия."),
    "22P02": ("invalid", "Некорректные данные в запросе."),
    "57014": (None, "Сервер не успел ответить. Попробуйте ещё раз."),
}
UNEXPECTED_ERROR = "Непредвиденная ошибка. Попробуйте ещё раз; если повторится — обратитесь к администратору."

log = logging.getLogger(__name__)


class AppError(Exception):
    """Ошибка с текстом, который можно показать пользователю."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code  # "network", "auth", "forbidden", "menu_changed", "invalid" или None


def to_money(value):
    """Число из JSON -> Decimal с двумя знаками (без ошибок float)."""
    return Decimal(str(value)).quantize(CENT)


def parse_price(text):
    """Цена из поля ввода -> Decimal. Допускает запятую и пробелы между разрядами."""
    text = text.replace(" ", "").replace("\u00a0", "").strip()
    if not text:
        raise AppError("Введите цену.", "invalid")
    if not PRICE_RE.fullmatch(text):
        raise AppError("Цена — положительное число, не более двух знаков после запятой. "
                       "Пример: 450 или 450.50", "invalid")
    price = Decimal(text.replace(",", "."))
    if price <= 0:
        raise AppError("Цена должна быть больше нуля.", "invalid")
    if price > MAX_PRICE:
        raise AppError("Слишком большая цена.", "invalid")
    return price.quantize(CENT)


def parse_amount(text, allow_zero=False):
    """Денежная сумма кассы из поля ввода (наличные): >= 0, до двух знаков, пробелы и запятая допустимы."""
    text = (text or "").replace(" ", "").replace("\u00a0", "").strip()
    if not text:
        raise AppError("Введите сумму.", "invalid")
    if not PRICE_RE.fullmatch(text):
        raise AppError("Сумма — число, не более двух знаков после запятой. Пример: 20000 или 150.50", "invalid")
    amount = Decimal(text.replace(",", "."))
    if amount == 0 and not allow_zero:
        raise AppError("Сумма должна быть больше нуля.", "invalid")
    if amount > Decimal("9999999999.99"):
        raise AppError("Слишком большая сумма.", "invalid")
    return amount.quantize(CENT)


def check_staff(full_name, role, code):
    """Поля сотрудника: (имя, роль, код) после проверки."""
    full_name, code = (full_name or "").strip(), (code or "").strip()
    if not full_name:
        raise AppError("Введите имя сотрудника.", "invalid")
    if len(full_name) > 100:
        raise AppError("Имя не длиннее 100 символов.", "invalid")
    if role not in ALLOWED_ROLES:
        raise AppError("Выберите роль.", "invalid")
    if not EMPLOYEE_CODE_RE.fullmatch(code):
        raise AppError("Код сотрудника — от 4 до 6 цифр.", "invalid")
    return full_name, role, code


def check_password(password):
    if not password or len(password) < 8:
        raise AppError("Пароль — не менее 8 символов.", "invalid")
    if len(password) > 72:
        raise AppError("Пароль — не более 72 символов.", "invalid")


def check_menu_item(name, category, price_text):
    """Проверка полей позиции меню. Возвращает (name, category, price: Decimal)."""
    name, category = name.strip(), category.strip()
    if not name:
        raise AppError("Введите название.", "invalid")
    if len(name) > 100:
        raise AppError("Название не длиннее 100 символов.", "invalid")
    if not category:
        raise AppError("Введите категорию.", "invalid")
    if len(category) > 50:
        raise AppError("Категория не длиннее 50 символов.", "invalid")
    return name, category, parse_price(price_text)


def to_local_time(value, fmt="%d.%m.%Y %H:%M"):
    """Время из Supabase (ISO, UTC) -> строка в местном времени."""
    try:
        return datetime.fromisoformat(value).astimezone().strftime(fmt)
    except (TypeError, ValueError):
        return str(value or "")


def parse_menu_code(text):
    """Код позиции из поля ввода: None — назначить автоматически."""
    text = text.strip()
    if not text:
        return None
    if not MENU_CODE_RE.fullmatch(text) or int(text) == 0:
        raise AppError("Код — целое число от 1 до 99999999 (или оставьте поле пустым).", "invalid")
    return int(text)


def _check_station(station):
    if station not in STATIONS:
        raise AppError("Неизвестная станция приготовления.", "invalid")
    return station


def menu_status(item):
    """Состояние позиции. До миграции 09 сервер его не присылает — тогда по наличию."""
    status = item.get("status")
    if status in MENU_STATUSES:
        return status
    return "on_sale" if item.get("is_available") else "out_of_stock"


def _check_menu_rows(rows):
    """Позиции меню из ответа сервера: экраны берут эти поля без проверок."""
    if not isinstance(rows, list) or not all(
            isinstance(row, dict) and type(row.get("id")) is int and isinstance(row.get("name"), str)
            and isinstance(row.get("category"), str) and isinstance(row.get("is_available"), bool)
            and row.get("status", "on_sale") in MENU_STATUSES
            and (row.get("archived_at") is None or isinstance(row.get("archived_at"), str))
            for row in rows):
        raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
    return rows


def menu_archived(item):
    """Блюдо в архиве? До миграции 15 сервер archived_at не присылает — тогда все блюда активные."""
    return bool(item.get("archived_at"))


def _menu_rows_with_prices(rows):
    """Строки меню администратора: цена -> Decimal, флаг archived (до миграции 15 всегда False)."""
    return [{**row, "price": to_money(row["price"]), "archived": menu_archived(row)} for row in rows]


def _check_items(items, keys=()):
    """Позиции заказа из ответа сервера: список объектов с названием и количеством (и полями keys).

    Экраны берут item_name и quantity без проверок, поэтому ответ другой формы —
    «неожиданный ответ», а не KeyError при отрисовке.
    """
    if items is None:
        return []
    if not isinstance(items, list) or not all(
            isinstance(item, dict) and isinstance(item.get("item_name"), str)
            and type(item.get("quantity")) is int and all(key in item for key in keys)
            for item in items):
        raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
    return items


def sort_categories(names):
    """Категории по алфавиту без учёта регистра (Ё и Е сортируются как в словаре Python)."""
    return sorted({n for n in names if n}, key=lambda n: (n.casefold(), n))


def category_not_empty_text(details):
    """Текст отказа в удалении категории с блюдами; details сервера — «всего;в архиве»."""
    try:
        total, archived = (int(x) for x in str(details).split(";"))
    except ValueError:
        total = archived = None
    what = "В категории есть блюда"
    if total is not None:
        what = f"В категории {total} блюд" + (f" (из них в архиве — {archived})" if archived else "")
    return (f"{what}: удалить её нельзя. Сначала перенесите блюда в другую категорию (измените категорию блюда "
            "в «Управлении меню» или объедините категории переименованием); архивные блюда удалите навсегда.")


def _api_error(e, default_text):
    """PostgrestAPIError -> AppError."""
    if e.code in ("PGRST301", "PGRST303") or "JWT" in (e.message or ""):
        return AppError("Сессия истекла. Войдите снова.", "auth")
    if e.code == "42703":  # нет новой колонки: миграция ещё не выполнена
        return AppError("База данных ещё не обновлена (не выполнена SQL-миграция). Обратитесь к администратору.")
    if e.message == "CATEGORY_NOT_EMPTY":
        return AppError(category_not_empty_text(e.details), "invalid")
    if e.message in RPC_ERRORS:
        code, text = RPC_ERRORS[e.message]
        return AppError(text.format(details=e.details or ""), code)
    if e.code == "23503" and "menu_items_category_fkey" in (e.message or ""):
        # Категорию удалили, пока блюдо сохранялось (гонка двух администраторов): сырую ошибку ключа не показываем
        return AppError("Категория блюда была удалена или изменена. Обновите список и выберите категорию снова.", "invalid")
    if e.code == "23514" and "menu_items_archived_state" in (e.message or ""):
        # Включить наличие архивного блюда нельзя (ограничение миграции 15): сначала восстановить
        return AppError("Блюдо в архиве — сначала восстановите его. Список обновлён.", "invalid")
    # Подробности — в журнал для разработчика, сотруднику — понятный текст и код ошибки
    log.warning("Ошибка сервера %s: %s", e.code, e.message)
    if e.code in PG_ERRORS:
        code, text = PG_ERRORS[e.code]
        return AppError(text, code)
    return AppError(f"{default_text}. Код ошибки: {e.code}." if e.code else f"{default_text}.")


def unexpected_error(e):
    """Непредвиденное исключение в фоновом запросе -> AppError без технических подробностей."""
    log.exception("Непредвиденная ошибка в фоновом запросе", exc_info=e)
    return AppError(UNEXPECTED_ERROR)


class Database:
    profile = None         # профиль вошедшего сотрудника (sign_in), None — никто не вошёл
    _access_token = None   # токен текущей сессии (из событий Auth): отзыв при выходе без get_session()
    _revoke_thread = None  # фоновый отзыв прежней сессии на сервере (выход без ожидания)

    def __init__(self, url, key):
        self.client = create_client(url, key)
        self.profile = None
        self.client.auth.on_auth_state_change(self._on_auth_event)

    def _on_auth_event(self, _event, session):
        # SIGNED_IN, TOKEN_REFRESHED — новая сессия; SIGNED_OUT — None
        self._access_token = session.access_token if session else None

    def _login_email(self, code):
        """Email учётной записи Supabase Auth по коду сотрудника (RPC employee_login_email)."""
        try:
            result = self.client.rpc("employee_login_email", {"p_code": code}).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":  # функции нет: не выполнена миграция
                raise AppError("Вход по коду не настроен на сервере. Обратитесь к администратору.") from e
            raise AppError("Ошибка сервера при входе. Попробуйте ещё раз.") from e
        except httpx.HTTPError as e:
            raise AppError(NO_NETWORK) from e
        return result.data if isinstance(result.data, str) and result.data else None

    def sign_in(self, code, password):
        """Вход по коду сотрудника и паролю. Возвращает профиль сотрудника.

        Код только находит учётную запись; пароль проверяет Supabase Auth,
        роль и статус читаются из profiles после входа.
        """
        code = code.strip()
        if not code or not password:
            raise AppError("Введите код сотрудника и пароль.")
        if not EMPLOYEE_CODE_RE.fullmatch(code):
            raise AppError("Код сотрудника — от 4 до 6 цифр.")

        pending = self._revoke_thread
        if pending is not None:
            # Прежняя сессия ещё отзывается на сервере (выход того же сотрудника со scope=global
            # отозвал бы и новую): вход продолжается после отзыва или по таймауту
            pending.join(SIGN_OUT_WAIT)

        email = self._login_email(code)
        if email is None:  # неизвестный код или неактивный сотрудник — ответ одинаковый
            raise AppError(BAD_LOGIN)

        try:
            response = self.client.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
        except AuthApiError as e:
            if e.code == "invalid_credentials" or e.status == 400:
                raise AppError(BAD_LOGIN) from e
            if e.status == 429:
                raise AppError("Слишком много попыток входа. Подождите и попробуйте снова.") from e
            raise AppError("Ошибка входа. Попробуйте ещё раз.") from e
        except (AuthError, httpx.HTTPError) as e:
            raise AppError(NO_NETWORK) from e

        try:
            result = (
                self.client.table("profiles")
                .select("id, full_name, role, is_active, employee_code")
                .eq("id", response.user.id)
                .limit(1)
                .execute()
            )
        except (PostgrestAPIError, httpx.HTTPError) as e:
            self.sign_out()
            raise AppError("Не удалось загрузить профиль сотрудника.") from e

        profile = result.data[0] if result.data else None
        if not profile or not profile["is_active"] or profile["role"] not in ALLOWED_ROLES:
            self.sign_out()
            raise AppError("У этой учётной записи нет доступа к кассе.")
        if profile["employee_code"] != code:  # код переназначили между запросами
            self.sign_out()
            raise AppError(BAD_LOGIN)

        self.profile = profile
        return profile

    def _check_still_allowed(self):
        """Сотрудника могли деактивировать или сменить ему роль во время смены.

        Тогда RLS молча скрывает меню и заказы (касса показала бы пустое меню без объяснений),
        поэтому профиль перечитывается: такая сессия — ошибка «auth», экран входа.
        Свой профиль виден всегда (политика profiles_select_own).
        """
        if self.profile is None:
            return
        result = (
            self.client.table("profiles")
            .select("is_active, role")
            .eq("id", self.profile["id"])
            .limit(1)
            .execute()
        )
        row = result.data[0] if result.data else None
        if not row or not row.get("is_active") or row.get("role") != self.profile["role"]:
            raise AppError("Доступ этой учётной записи изменён администратором. Войдите снова.", "auth")

    def load_menu(self):
        """Доступные позиции меню: [{id, code, name, category, price: Decimal}]."""
        try:
            self._check_still_allowed()
            result = (
                self.client.table("menu_items")
                .select("id, code, name, category, price")
                .eq("is_available", True)
                .order("category")
                .order("name")
                .execute()
            )
        except PostgrestAPIError as e:
            raise _api_error(e, "Не удалось загрузить меню") from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Проверьте интернет.", "network") from e
        return [{**row, "price": to_money(row["price"])} for row in result.data]

    def create_order(self, request_id, items, expected_total=None):
        """Атомарное оформление заказа через RPC place_order (sql/11_service_charge_void.sql).

        items: [(menu_item_id, quantity, expected_price: Decimal)]
        expected_total: итог с надбавкой, который видит кассир; при расхождении сервер откажет.
        Возвращает {order_id, subtotal, service_percent, service_amount, total: Decimal, created_at, duplicate}.
        """
        payload = [
            {"menu_item_id": item_id, "quantity": qty, "expected_price": str(price)}
            for item_id, qty, price in items
        ]
        params = {"p_client_request_id": str(request_id), "p_items": payload}
        if expected_total is not None:
            params["p_expected_total"] = str(expected_total)
        try:
            result = self.client.rpc("place_order", params).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":  # функции нет: миграция 11 не выполнена, надбавку считать негде
                raise AppError("Надбавка 10% не настроена на сервере (не выполнена миграция 11). "
                               "Обратитесь к администратору.") from e
            raise _api_error(e, "Заказ не сохранён") from e
        except httpx.HTTPError as e:
            # Ответ не получен: заказ мог сохраниться. Повтор с тем же request_id безопасен.
            raise AppError("Нет связи с сервером. Заказ не подтверждён — нажмите «Оформить» ещё раз.",
                           "network") from e

        data = result.data
        if (not isinstance(data, dict) or "order_id" not in data
                or any(k not in data for k in ("subtotal", "service_amount", "total"))):
            raise AppError("Сервер вернул неожиданный ответ. Заказ не подтверждён.")
        return {**data, "subtotal": to_money(data["subtotal"]),
                "service_amount": to_money(data["service_amount"]), "total": to_money(data["total"])}

    # ---------- Управление меню (только администратор) ----------
    # Права проверяются на сервере: RLS для чтения и функции admin_* для записи.

    def load_all_menu(self):
        """Все позиции меню для администратора: [{id, code, name, category, price, is_available,
        station, status, reject_reason, archived}] — активные и архивные вместе, различает `archived`.

        После миграции 09 — RPC station_menu (со статусами «ждёт решения» и «отклонена»; после 15 — с
        archived_at), до неё — чтение таблицы, статус по наличию (menu_status). До миграции 15 все
        блюда имеют archived = False.
        """
        try:
            result = self.client.rpc("station_menu", {}).execute()
        except PostgrestAPIError as e:
            if e.code != "PGRST202":  # PGRST202 — функции нет: миграция 09 ещё не выполнена
                raise _api_error(e, "Не удалось загрузить меню") from e
            return self._load_menu_table()
        except httpx.HTTPError as e:
            raise AppError(NO_NETWORK, "network") from e
        return _menu_rows_with_prices(_check_menu_rows(result.data))

    def _load_menu_table(self):
        """Все позиции меню до миграции 09 (RLS: администратор видит и недоступные)."""
        try:
            result = (
                self.client.table("menu_items")
                .select("id, code, name, category, price, is_available, station")
                .order("category")
                .order("name")
                .execute()
            )
        except PostgrestAPIError as e:
            raise _api_error(e, "Не удалось загрузить меню") from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Проверьте интернет.", "network") from e
        return _menu_rows_with_prices(result.data)

    def _admin_rpc(self, name, params, default_text):
        try:
            result = self.client.rpc(name, params).execute()
        except PostgrestAPIError as e:
            raise _api_error(e, default_text) from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Изменение не подтверждено — обновите список.",
                           "network") from e
        data = result.data
        if not isinstance(data, dict) or "id" not in data:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return {**data, "price": to_money(data["price"])}

    def add_menu_item(self, name, category, price_text, code_text="", station="kitchen"):
        """Новая позиция; пустой code_text — код назначит сервер (виден в ответе)."""
        name, category, price = check_menu_item(name, category, price_text)
        params = {"p_name": name, "p_category": category, "p_price": str(price)}
        code = parse_menu_code(code_text)
        if code is not None:
            params["p_code"] = code
        if _check_station(station) != "kitchen":  # 'kitchen' — значение по умолчанию на сервере
            params["p_station"] = station
        return self._admin_rpc("admin_add_menu_item", params, "Позиция не добавлена")

    def update_menu_item(self, item_id, name, category, price_text, station=None):
        """station=None — не менять станцию; новая станция действует для новых заказов."""
        name, category, price = check_menu_item(name, category, price_text)
        params = {"p_id": item_id, "p_name": name, "p_category": category, "p_price": str(price)}
        if station is not None:
            params["p_station"] = _check_station(station)
        return self._admin_rpc("admin_update_menu_item", params, "Изменения не сохранены")

    def set_menu_item_available(self, item_id, available):
        return self._admin_rpc(
            "admin_set_menu_item_available",
            {"p_id": item_id, "p_available": bool(available)},
            "Доступность не изменена",
        )

    # ---------- Архив блюд (миграция 15) ----------
    # Только администратор; права проверяет сервер. Блюдо физически не удаляется, пока оно не в архиве;
    # «удалить навсегда» не меняет заказы (названия, цены и суммы хранятся в самих заказах).

    ARCHIVE_MISSING = ("Архив блюд не настроен на сервере (не выполнена миграция 15). "
                       "Обратитесь к администратору.")

    def _archive_rpc(self, name, item_id, default_text, expected):
        """RPC архива: проверка номера до запроса и формы ответа; expected — {поле: значение} ответа."""
        if type(item_id) is not int:
            raise AppError("Не выбрано блюдо.", "invalid")
        data = self._rpc(name, {"p_id": item_id}, default_text, self.ARCHIVE_MISSING)
        if (not isinstance(data, dict) or data.get("id") != item_id or not isinstance(data.get("name"), str)
                or any(data.get(key) is not value for key, value in expected.items())):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def archive_menu_item(self, item_id):
        """В архив (блюдо снимается с продажи). {id, name, archived: True, changed}; changed = False — уже в архиве."""
        data = self._archive_rpc("admin_archive_menu_item", item_id, "Блюдо не архивировано", {"archived": True})
        if type(data.get("changed")) is not bool:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def restore_menu_item(self, item_id):
        """Из архива обратно в продажу. {id, name, archived: False, changed}; changed = False — и не было в архиве."""
        data = self._archive_rpc("admin_restore_menu_item", item_id, "Блюдо не восстановлено", {"archived": False})
        if type(data.get("changed")) is not bool:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def purge_menu_item(self, item_id):
        """Удалить навсегда блюдо из архива. {id, name, purged: True, orders_count} — сколько заказов его содержали
        (в них название, цена и категория сохраняются). Необратимо."""
        data = self._archive_rpc("admin_purge_menu_item", item_id, "Блюдо не удалено", {"purged": True})
        if type(data.get("orders_count")) is not int or data["orders_count"] < 0:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def count_menu_item_orders(self, item_id):
        """Сколько заказов содержат блюдо — для подтверждения «Удалить навсегда» (только чтение, RLS администратора).

        В позиции заказа блюдо встречается один раз (unique order_id + menu_item_id), поэтому число строк —
        число заказов. Заказы, отправленные в «Удалённые заказы», RLS скрывает: число может быть меньше
        итогового `orders_count`, который вернёт сервер при удалении. Только для показа, решает сервер.
        """
        if type(item_id) is not int:
            raise AppError("Не выбрано блюдо.", "invalid")
        try:
            result = (self.client.table("order_items").select("order_id", count="exact")
                      .eq("menu_item_id", item_id).limit(1).execute())
        except PostgrestAPIError as e:
            raise _api_error(e, "Не удалось определить число заказов") from e
        except httpx.HTTPError as e:
            raise AppError(NO_NETWORK, "network") from e
        if type(result.count) is not int or result.count < 0:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return result.count

    # ---------- Решения по позициям станций (миграция 09) ----------

    def approve_menu_item(self, item_id, available):
        """Подтвердить ожидающую позицию: available — сразу в продажу или пока «нет в наличии»."""
        return self._admin_rpc("admin_approve_menu_item", {"p_id": item_id, "p_available": bool(available)},
                               "Позиция не подтверждена")

    def reject_menu_item(self, item_id, reason=""):
        """Отклонить ожидающую позицию. Причина необязательна, до 200 символов."""
        reason = (reason or "").strip()
        if len(reason) > MAX_REJECT_REASON:
            raise AppError(f"Причина — не длиннее {MAX_REJECT_REASON} символов.", "invalid")
        params = {"p_id": item_id}
        if reason:
            params["p_reason"] = reason
        return self._admin_rpc("admin_reject_menu_item", params, "Позиция не отклонена")

    # ---------- Меню станции: кухня и бар (миграция 09) ----------

    def load_station_menu(self, station):
        """Меню своей станции: {"items": [...], "can_propose": bool, "available": bool}.

        items — [{id, code, name, category, is_available, status, reject_reason}]; станцию сервер
        определяет по роли. До миграции 09 (нет station_menu) кухня получает прежний kitchen_menu
        без предложений, а у бара меню нет (available = False): kitchen_menu бару не разрешён.
        """
        try:
            result = self.client.rpc("station_menu", {}).execute()
        except PostgrestAPIError as e:
            if e.code != "PGRST202":
                raise _api_error(e, "Не удалось загрузить меню") from e
            if _check_station(station) == "bar":
                return {"items": [], "can_propose": False, "available": False}
            return {"items": self.load_kitchen_menu(), "can_propose": False, "available": True}
        except httpx.HTTPError as e:
            raise AppError(NO_NETWORK, "network") from e
        return {"items": _check_menu_rows(result.data), "can_propose": True, "available": True}

    def propose_menu_item(self, name, category, price_text, code_text=""):
        """Кухня или бар предлагает позицию своей станции: она ждёт решения администратора."""
        name, category, price = check_menu_item(name, category, price_text)
        params = {"p_name": name, "p_category": category, "p_price": str(price)}
        code = parse_menu_code(code_text)
        if code is not None:
            params["p_code"] = code
        return self._admin_rpc("station_add_menu_item", params, "Позиция не предложена")

    def station_set_available(self, station, item_id, available):
        """Наличие подтверждённой позиции своей станции.

        Кухня — kitchen_set_menu_item_available (есть и до 09; после 09 проверяет станцию
        и подтверждение), бар — station_set_menu_item_available (только после 09).
        """
        if _check_station(station) == "kitchen":
            return self.kitchen_set_available(item_id, available)
        try:
            result = self.client.rpc(
                "station_set_menu_item_available", {"p_id": item_id, "p_available": bool(available)}
            ).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":
                raise AppError("Меню бара не настроено на сервере. Обратитесь к администратору.") from e
            raise _api_error(e, "Наличие не изменено") from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Изменение не подтверждено — обновите список.",
                           "network") from e
        data = result.data
        if not isinstance(data, dict) or data.get("is_available") is not bool(available):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    # ---------- Заказы ----------
    # Видимость задаёт RLS: кассир видит свои заказы, администратор — все.

    ORDER_COLUMNS_OLD = ("id, created_at, total, status, cashier:profiles!orders_cashier_id_fkey(full_name), "
                         "order_items(id, item_name, unit_price, quantity, line_total)")
    ORDER_COLUMNS = ("id, created_at, total, status, subtotal, service_percent, service_amount, "
                     "voided_at, void_reason, cashier:profiles!orders_cashier_id_fkey(full_name), "
                     "voider:profiles!orders_voided_by_fkey(full_name), "
                     "order_items(id, item_name, unit_price, quantity, line_total)")

    def load_orders(self, order_id=None, date_from=None, date_to=None, status=None, voided=None,
                    limit=ORDERS_LIMIT):
        """Заказы с позициями, новые сначала. Фильтры выполняет сервер:
        order_id — номер; date_from/date_to — datetime (включительно / не включая конец);
        status — статус заказа; voided: True — только аннулированные, False — без них, None — все.

        [{id, created_at, cashier_name, subtotal, service_percent, service_amount, total, status,
          voided, void_reason, voided_by_name, items}]. До миграции 11 подытог равен итогу, надбавки нет.
        """
        def query(columns, new_schema):
            q = self.client.table("orders").select(columns)
            if order_id is not None:
                q = q.eq("id", order_id)
            if date_from is not None:
                q = q.gte("created_at", date_from.astimezone().isoformat())
            if date_to is not None:
                q = q.lt("created_at", date_to.astimezone().isoformat())
            if status:
                q = q.eq("status", status)
            if new_schema and voided is True:
                q = q.not_.is_("voided_at", "null")
            elif new_schema and voided is False:
                q = q.is_("voided_at", "null")
            return q.order("created_at", desc=True).order("id", desc=True).limit(limit).execute()

        new_schema = True
        try:
            try:
                result = query(self.ORDER_COLUMNS, True)
            except PostgrestAPIError as e:
                if e.code != "42703":
                    raise
                new_schema = False  # нет колонок миграции 11 — прежний набор
                result = query(self.ORDER_COLUMNS_OLD, False)
        except PostgrestAPIError as e:
            if e.code == "42703":  # нет колонки status: не выполнена миграция 04
                raise AppError("Раздел «Заказы» не настроен на сервере. Обратитесь к администратору.") from e
            raise _api_error(e, "Не удалось загрузить заказы") from e
        except httpx.HTTPError as e:
            raise AppError(NO_NETWORK, "network") from e
        if not isinstance(result.data, list):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")

        orders = []
        for row in result.data:
            items = sorted(_check_items(row.get("order_items"), ("id", "unit_price", "line_total")),
                           key=lambda i: i["id"])
            total = to_money(row["total"])
            orders.append({
                "id": row["id"],
                "created_at": to_local_time(row["created_at"]),
                "created_raw": row["created_at"],
                "cashier_name": (row.get("cashier") or {}).get("full_name") or "—",
                "subtotal": to_money(row["subtotal"]) if new_schema and row.get("subtotal") is not None else total,
                "service_percent": to_money(row.get("service_percent") or 0),
                "service_amount": to_money(row.get("service_amount") or 0),
                "total": total,
                "status": row["status"],
                "voided": bool(row.get("voided_at")),
                "voided_at": to_local_time(row["voided_at"]) if row.get("voided_at") else "",
                "void_reason": row.get("void_reason") or "",
                "voided_by_name": (row.get("voider") or {}).get("full_name") or "—",
                "items": [{**i, "unit_price": to_money(i["unit_price"]),
                           "line_total": to_money(i["line_total"])} for i in items],
            })
        return orders

    # ---------- Общий вызов RPC для новых функций (миграции 11–14) ----------

    def _rpc(self, name, params, default_text, missing_text):
        """RPC без повторов: нет функции (PGRST202) — missing_text, нет связи — понятная ошибка."""
        try:
            return self.client.rpc(name, params).execute().data
        except PostgrestAPIError as e:
            if e.code == "PGRST202":
                raise AppError(missing_text) from e
            raise _api_error(e, default_text) from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Действие не подтверждено — обновите данные.",
                           "network") from e

    # ---------- Аннулирование заказов (миграция 11) ----------

    def void_order(self, order_id, reason):
        """Аннулировать заказ с причиной (3–200 символов). changed = False — уже был аннулирован."""
        reason = (reason or "").strip()
        if not 3 <= len(reason) <= 200:
            raise AppError("Причина аннулирования — от 3 до 200 символов.", "invalid")
        data = self._rpc("void_order", {"p_order_id": order_id, "p_reason": reason}, "Заказ не аннулирован",
                         "Аннулирование не настроено на сервере (миграция 11). Обратитесь к администратору.")
        if (not isinstance(data, dict) or data.get("order_id") != order_id or data.get("voided") is not True
                or type(data.get("changed")) is not bool):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    # ---------- Кассовые смены (миграция 12) ----------

    SHIFT_MISSING = "Кассовые смены не настроены на сервере (миграция 12). Обратитесь к администратору."

    @staticmethod
    def _shift(data):
        """Смена из ответа сервера: суммы -> Decimal, время -> местное."""
        if (not isinstance(data, dict) or type(data.get("id")) is not int
                or any(data.get(k) is None for k in ("opening_cash", "sales_total", "expected_cash"))):
            raise AppError("Сервер вернул неожиданный ответ. Обновите данные.")
        out = dict(data)
        for key in ("opening_cash", "actual_cash", "sales_total", "deposits_total", "withdrawals_total",
                    "expected_cash", "difference"):
            out[key] = to_money(data[key]) if data.get(key) is not None else None
        out["opened_local"] = to_local_time(data.get("opened_at"))
        out["closed_local"] = to_local_time(data["closed_at"]) if data.get("closed_at") else ""
        out["cashier_name"] = data.get("cashier_name") or "—"
        out["orders_count"] = int(data.get("orders_count") or 0)
        out["movements"] = [{**m, "amount": to_money(m["amount"]), "time": to_local_time(m.get("created_at"), "%H:%M")}
                            for m in data.get("movements") or []]
        return out

    def current_shift(self):
        """Открытая смена сотрудника или None."""
        data = self._rpc("shift_current", {}, "Не удалось загрузить смену", self.SHIFT_MISSING)
        return None if data is None else self._shift(data)

    def open_shift(self, opening_text):
        amount = parse_amount(opening_text, allow_zero=True)
        return self._shift(self._rpc("shift_open", {"p_opening_cash": str(amount)}, "Смена не открыта",
                                     self.SHIFT_MISSING))

    def cash_move(self, kind, amount_text, note=""):
        """kind: 'deposit' (внесение) или 'withdrawal' (изъятие)."""
        if kind not in ("deposit", "withdrawal"):
            raise AppError("Неизвестный вид операции.", "invalid")
        amount = parse_amount(amount_text)
        note = (note or "").strip()
        if len(note) > 200:
            raise AppError("Комментарий — не длиннее 200 символов.", "invalid")
        params = {"p_kind": kind, "p_amount": str(amount)}
        if note:
            params["p_note"] = note
        return self._shift(self._rpc("shift_cash_move", params, "Операция не проведена", self.SHIFT_MISSING))

    def close_shift(self, actual_text):
        amount = parse_amount(actual_text, allow_zero=True)
        data = self._shift(self._rpc("shift_close", {"p_actual_cash": str(amount)}, "Смена не закрыта",
                                     self.SHIFT_MISSING))
        if data["difference"] is None:
            raise AppError("Сервер вернул неожиданный ответ. Обновите данные.")
        return data

    def shift_history(self, limit=100):
        data = self._rpc("shift_history", {"p_limit": limit}, "Не удалось загрузить смены", self.SHIFT_MISSING)
        if not isinstance(data, list):
            raise AppError("Сервер вернул неожиданный ответ. Обновите данные.")
        return [self._shift(row) for row in data]

    # ---------- Сотрудники (миграция 13) ----------

    STAFF_MISSING = "Управление сотрудниками не настроено на сервере (миграция 13). Обратитесь к администратору."

    @staticmethod
    def _staff_row(row):
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or row.get("role") not in ALLOWED_ROLES or not isinstance(row.get("full_name"), str)
                or type(row.get("is_active")) is not bool):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return {**row, "employee_code": row.get("employee_code") or ""}

    def load_staff(self):
        data = self._rpc("admin_staff_list", {}, "Не удалось загрузить сотрудников", self.STAFF_MISSING)
        if not isinstance(data, list):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return [self._staff_row(row) for row in data]

    def create_staff(self, full_name, role, code, password):
        full_name, role, code = check_staff(full_name, role, code)
        check_password(password)
        return self._staff_row(self._rpc(
            "admin_create_staff", {"p_full_name": full_name, "p_role": role, "p_code": code, "p_password": password},
            "Сотрудник не создан", self.STAFF_MISSING))

    def update_staff(self, staff_id, full_name, role, code, is_active):
        full_name, role, code = check_staff(full_name, role, code)
        return self._staff_row(self._rpc(
            "admin_update_staff", {"p_id": staff_id, "p_full_name": full_name, "p_role": role, "p_code": code,
                                   "p_is_active": bool(is_active)}, "Изменения не сохранены", self.STAFF_MISSING))

    def set_staff_password(self, staff_id, password):
        check_password(password)
        data = self._rpc("admin_set_staff_password", {"p_id": staff_id, "p_password": password},
                         "Пароль не изменён", self.STAFF_MISSING)
        if not isinstance(data, dict) or data.get("password_changed") is not True:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    # ---------- Отчёты и категории (миграция 14) ----------

    def load_report(self, date_from, date_to):
        """Сводка за период [date_from, date_to) — datetime. Суммы -> Decimal."""
        data = self._rpc("admin_report", {"p_from": date_from.astimezone().isoformat(),
                                          "p_to": date_to.astimezone().isoformat()},
                         "Не удалось загрузить отчёт",
                         "Отчёты не настроены на сервере (миграция 14). Обратитесь к администратору.")
        if (not isinstance(data, dict) or type(data.get("orders_count")) is not int
                or any(not isinstance(data.get(k), list) for k in ("top_items", "categories", "cashiers", "daily"))):
            raise AppError("Сервер вернул неожиданный ответ. Обновите отчёт.")
        money = lambda v: to_money(v if v is not None else 0)  # noqa: E731
        return {
            **data,
            "subtotal_total": money(data.get("subtotal_total")), "service_total": money(data.get("service_total")),
            "revenue": money(data.get("revenue")), "average_check": money(data.get("average_check")),
            "voided_total": money(data.get("voided_total")), "voided_count": int(data.get("voided_count") or 0),
            "top_items": [{**r, "revenue": money(r.get("revenue"))} for r in data["top_items"]],
            "categories": [{**r, "revenue": money(r.get("revenue"))} for r in data["categories"]],
            "cashiers": [{**r, "revenue": money(r.get("revenue")), "service": money(r.get("service"))}
                         for r in data["cashiers"]],
            "daily": [{**r, "revenue": money(r.get("revenue"))} for r in data["daily"]],
        }

    def probe_features(self):
        """Какие миграции применены на сервере: {название: True/False}. Только чтение, без побочных действий."""
        def exists(call):
            try:
                call()
            except PostgrestAPIError as e:
                if e.code in ("PGRST202", "PGRST205", "42703", "42P01"):
                    return False
                # Любая другая ошибка (например, нет прав) — функция на сервере есть
            except httpx.HTTPError as e:
                raise AppError(NO_NETWORK, "network") from e
            return True

        now = datetime.now().astimezone()
        rpc = lambda name, params: (lambda: self.client.rpc(name, params).execute())  # noqa: E731
        return {
            "10 Удаление заказов": exists(rpc("admin_purge_log", {})),
            "11 Надбавка 10% и аннулирование": exists(
                lambda: self.client.table("orders").select("subtotal, voided_at").limit(1).execute()),
            "12 Кассовые смены": exists(rpc("shift_current", {})),
            "13 Управление сотрудниками": exists(rpc("admin_staff_list", {})),
            "14 Отчёты и категории": exists(rpc("admin_report", {
                "p_from": (now - timedelta(hours=1)).isoformat(), "p_to": now.isoformat()})),
            "16 Категории меню": exists(lambda: self.client.table("menu_categories").select("name").limit(1).execute()),
        }

    CATEGORIES_MISSING = ("Справочник категорий не настроен на сервере (не выполнена миграция 16). "
                          "Обратитесь к администратору.")

    def load_categories(self, lenient=False):
        """Названия категорий из справочника (миграция 16) по алфавиту. До миграции 16 — пустой список:
        вызывающий берёт категории из самих блюд. lenient — подсказки в списках не должны ломать экран:
        любая ошибка, кроме потери сессии, даёт пустой список."""
        try:
            return self._load_categories()
        except AppError as e:
            if lenient and e.code != "auth":
                return []
            raise

    def _load_categories(self):
        try:
            result = self.client.table("menu_categories").select("name").execute()
        except PostgrestAPIError as e:
            if e.code in ("PGRST205", "42P01"):  # таблицы нет: миграция 16 ещё не выполнена
                return []
            raise _api_error(e, "Не удалось загрузить категории") from e
        except httpx.HTTPError as e:
            raise AppError(NO_NETWORK, "network") from e
        return sort_categories(row["name"] for row in result.data if isinstance(row.get("name"), str))

    @staticmethod
    def _check_category_name(name):
        name = (name or "").strip()
        if not name or len(name) > 50:
            raise AppError("Категория должна содержать от 1 до 50 символов.", "invalid")
        return name

    def add_category(self, name):
        name = self._check_category_name(name)
        data = self._rpc("admin_add_category", {"p_name": name}, "Категория не добавлена", self.CATEGORIES_MISSING)
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def delete_category(self, name):
        name = self._check_category_name(name)
        data = self._rpc("admin_delete_category", {"p_name": name}, "Категория не удалена", self.CATEGORIES_MISSING)
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def rename_category(self, old, new):
        old, new = (old or "").strip(), (new or "").strip()
        if not new or len(new) > 50:
            raise AppError("Категория должна содержать от 1 до 50 символов.", "invalid")
        data = self._rpc("admin_rename_category", {"p_old": old, "p_new": new}, "Категория не изменена",
                         "Переименование категорий не настроено на сервере (миграции 14 и 16).")
        if not isinstance(data, dict) or type(data.get("updated")) is not int:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    # ---------- Удаление заказов (миграция 10) ----------
    # Удалённые заказы обычная история не показывает: их скрывает RLS на сервере.
    # Права (кассир — только свои выданные, остальное — администратор) проверяют функции.

    def _order_rpc(self, name, params, default_text):
        try:
            result = self.client.rpc(name, params).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":  # функции нет: миграция 10 ещё не выполнена
                raise AppError("Удаление заказов не настроено на сервере. Обратитесь к администратору.") from e
            raise _api_error(e, default_text) from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Действие не подтверждено — обновите список.", "network") from e
        return result.data

    @staticmethod
    def _check_order_answer(data, order_id, **expected):
        if (not isinstance(data, dict) or data.get("order_id") != order_id
                or type(data.get("changed")) is not bool
                or any(data.get(key) is not value for key, value in expected.items())):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def delete_order(self, order_id):
        """Отправить выданный заказ в «Удалённые заказы». changed = False — уже был там."""
        data = self._order_rpc("delete_order", {"p_order_id": order_id}, "Заказ не удалён")
        return self._check_order_answer(data, order_id, deleted=True)

    def restore_order(self, order_id):
        """Администратор: вернуть заказ в обычную историю. changed = False — он и не был удалён."""
        data = self._order_rpc("admin_restore_order", {"p_order_id": order_id}, "Заказ не восстановлен")
        return self._check_order_answer(data, order_id, deleted=False)

    def purge_order(self, order_id):
        """Администратор: удалить навсегда заказ из «Удалённых заказов» (с позициями).

        changed = False — заказ уже был стёрт раньше (есть в журнале).
        """
        data = self._order_rpc("admin_purge_order", {"p_order_id": order_id}, "Заказ не удалён")
        data = self._check_order_answer(data, order_id, purged=True)
        if type(data.get("items_deleted")) is not int:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def load_deleted_orders(self):
        """Администратор: удалённые заказы [{id, created_at, cashier_name, total, deleted_at,
        deleted_by_name, items}], последние удалённые сначала."""
        data = self._order_rpc("admin_deleted_orders", {}, "Не удалось загрузить удалённые заказы")
        if not isinstance(data, list) or not all(
                isinstance(row, dict) and type(row.get("id")) is int and row.get("total") is not None
                for row in data):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return [{
            "id": row["id"],
            "created_at": to_local_time(row.get("created_at")),
            "cashier_name": row.get("cashier_name") or "—",
            "total": to_money(row["total"]),
            "deleted_at": to_local_time(row.get("deleted_at")),
            "deleted_by_name": row.get("deleted_by_name") or "—",
            "items": [{**i, "unit_price": to_money(i["unit_price"]), "line_total": to_money(i["line_total"])}
                      for i in sorted(_check_items(row.get("items"), ("id", "unit_price", "line_total")),
                                      key=lambda i: i["id"])],
        } for row in data]

    def load_purge_log(self):
        """Администратор: журнал окончательных удалений [{order_id, total, cashier_name,
        order_created_at, purged_by_name, purged_at}], новые сначала."""
        data = self._order_rpc("admin_purge_log", {}, "Не удалось загрузить журнал удалений")
        if not isinstance(data, list) or not all(
                isinstance(row, dict) and type(row.get("order_id")) is int and row.get("total") is not None
                for row in data):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return [{
            "order_id": row["order_id"],
            "total": to_money(row["total"]),
            "cashier_name": row.get("cashier_name") or "—",
            "order_created_at": to_local_time(row.get("order_created_at")),
            "purged_by_name": row.get("purged_by_name") or "—",
            "purged_at": to_local_time(row.get("purged_at")),
        } for row in data]

    def load_kitchen_orders(self):
        """Активные заказы для кухни — см. load_station_orders."""
        return self.load_station_orders("kitchen")

    def load_station_orders(self, station):
        """Активные заказы станции (RPC kitchen_orders / bar_orders): [{id, time, status, items}].

        status — статус части заказа этой станции; items — только её позиции
        [{code, item_name, quantity}]. Таблицы заказов станции напрямую не читают:
        функции отдают названия, коды и количество, без цен, сумм и кассира.
        """
        try:
            result = self.client.rpc(STATION_ORDERS_RPC[station], {}).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":  # функции нет: не выполнена миграция 05/08
                raise AppError(f"Экран «{STATIONS[station]}» не настроен на сервере. "
                               "Обратитесь к администратору.") from e
            raise _api_error(e, "Не удалось загрузить заказы") from e
        except httpx.HTTPError as e:
            raise AppError(NO_NETWORK, "network") from e
        if not isinstance(result.data, list):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return [{
            "id": row["id"],
            "time": to_local_time(row["created_at"], "%H:%M"),
            "created_raw": row["created_at"],  # для времени ожидания на карточке
            "status": row["status"],
            "items": _check_items(row.get("items")),
        } for row in result.data]

    def load_kitchen_menu(self):
        """Меню для кухни (RPC kitchen_menu): [{id, name, category, is_available}] — без цен."""
        try:
            result = self.client.rpc("kitchen_menu", {}).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":  # функции нет: не выполнена миграция 06
                raise AppError("Меню кухни не настроено на сервере. Обратитесь к администратору.") from e
            raise _api_error(e, "Не удалось загрузить меню") from e
        except httpx.HTTPError as e:
            raise AppError(NO_NETWORK, "network") from e
        if not isinstance(result.data, list):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return result.data

    def kitchen_set_available(self, item_id, available):
        """Кухня отмечает наличие блюда (RPC kitchen_set_menu_item_available)."""
        try:
            result = self.client.rpc(
                "kitchen_set_menu_item_available", {"p_id": item_id, "p_available": bool(available)}
            ).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":
                raise AppError("Меню кухни не настроено на сервере. Обратитесь к администратору.") from e
            raise _api_error(e, "Наличие не изменено") from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Изменение не подтверждено — обновите список.",
                           "network") from e
        data = result.data
        if not isinstance(data, dict) or data.get("is_available") is not bool(available):
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def set_station_status(self, order_id, station, status):
        """Кухня/бар: статус своей части заказа (RPC set_station_status)."""
        try:
            result = self.client.rpc(
                "set_station_status", {"p_order_id": order_id, "p_station": station, "p_status": status}
            ).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":  # функции нет: не выполнена миграция 08
                raise AppError("Смена статуса станции не настроена на сервере. Обратитесь к администратору.") from e
            raise _api_error(e, "Статус не изменён") from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Статус не подтверждён — обновите список.",
                           "network") from e
        data = result.data
        if not isinstance(data, dict) or data.get("status") != status or data.get("station") != station:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def force_order_ready(self, order_id):
        """Администратор: все станции и заказ — «Готов» (RPC admin_force_order_ready)."""
        try:
            result = self.client.rpc("admin_force_order_ready", {"p_order_id": order_id}).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":  # функции нет: не выполнена миграция 08
                raise AppError("Принудительная готовность не настроена на сервере.") from e
            raise _api_error(e, "Статус не изменён") from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Статус не подтверждён — обновите список.",
                           "network") from e
        data = result.data
        if (not isinstance(data, dict) or data.get("status") != "ready" or data.get("forced") is not True
                or type(data.get("stations_forced")) is not int):  # число показывается в окне «Заказы»
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def set_order_status(self, order_id, status):
        """Смена статуса через RPC set_order_status (переход проверяет сервер)."""
        try:
            result = self.client.rpc(
                "set_order_status", {"p_order_id": order_id, "p_status": status}
            ).execute()
        except PostgrestAPIError as e:
            if e.code == "PGRST202":  # функции нет: не выполнена миграция 04
                raise AppError("Смена статуса не настроена на сервере. Обратитесь к администратору.") from e
            raise _api_error(e, "Статус не изменён") from e
        except httpx.HTTPError as e:
            raise AppError("Нет связи с сервером. Статус не подтверждён — обновите список.",
                           "network") from e
        data = result.data
        if not isinstance(data, dict) or data.get("status") != status:
            raise AppError("Сервер вернул неожиданный ответ. Обновите список.")
        return data

    def sign_out(self, background=False):
        """Выход. Сессия на этом компьютере сбрасывается сразу, без ожидания сервера.

        Сервер отзывает сессию, если доступен: при background=True — в фоновом потоке,
        чтобы окно не зависало до таймаута. Следующий sign_in дождётся отзыва.
        """
        self.profile = None
        auth = self.client.auth
        if not background:
            try:
                auth.sign_out()
            except (AuthError, httpx.HTTPError):
                # supabase-py удаляет локальную сессию только после ответа сервера: без связи или
                # при 5xx токен сотрудника остался бы в клиенте (и в заголовках запросов), а таймер
                # продолжал бы его обновлять — удаляем сами.
                self._drop_local_session()
            return

        # В фоне: сначала локально, затем отзыв сохранённого токена. client.auth.sign_out()
        # в потоке не подходит: его удаление сессии после ответа сервера стёрло бы сессию
        # сотрудника, который успел войти следующим.
        token = self._access_token
        self._drop_local_session()
        if not token:
            return

        def revoke():
            try:
                auth.admin.sign_out(token)  # scope="global", как у client.auth.sign_out()
            except (AuthError, httpx.HTTPError):
                pass  # сервер недоступен или токен уже недействителен: локально сессии уже нет

        # Не daemon: закрытие приложения сразу после выхода не обрывает отзыв (не дольше таймаута)
        self._revoke_thread = threading.Thread(target=revoke)
        self._revoke_thread.start()

    def _drop_local_session(self):
        """Сессия на этом компьютере — без запроса к серверу (supabase-py 2.33, внутренние методы
        клиента): удаление сессии с остановкой таймера обновления и событие SIGNED_OUT, по которому
        клиент возвращает заголовкам запросов публичный ключ. Проверяется tests/test_logout_safety.py."""
        self.client.auth._remove_session()
        self.client.auth._notify_all_subscribers("SIGNED_OUT", None)
