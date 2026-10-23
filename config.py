"""Чтение настроек подключения из config.ini."""

import base64
import configparser
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.ini")


class ConfigError(Exception):
    pass


def _is_privileged_key(key):
    """True, если ключ обходит RLS (secret или legacy service_role)."""
    if key.startswith("sb_secret_"):
        return True
    parts = key.split(".")
    if len(parts) == 3:  # legacy JWT-ключ: проверяем роль в payload
        try:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            role = json.loads(base64.urlsafe_b64decode(payload)).get("role")
        except (ValueError, UnicodeDecodeError):
            return False
        return role == "service_role"
    return False


def load_config():
    """Возвращает (url, key) или выбрасывает ConfigError с понятным текстом."""
    parser = configparser.ConfigParser()
    if not parser.read(CONFIG_PATH, encoding="utf-8"):
        raise ConfigError(f"Не найден файл настроек: {CONFIG_PATH}")

    url = parser.get("supabase", "url", fallback="").strip()
    key = parser.get("supabase", "key", fallback="").strip()

    if not url.startswith("https://") or ".supabase.co" not in url:
        raise ConfigError("В config.ini неверный url. Пример: https://xxxx.supabase.co")
    if not key:
        raise ConfigError("В config.ini не указан key.")
    if _is_privileged_key(key):
        raise ConfigError("В config.ini указан секретный ключ. Нужен публичный (publishable/anon).")
    return url.rstrip("/"), key
