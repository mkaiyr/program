"""Запуск: ошибки файла настроек показываются окном, а не падением приложения.

Запуск из папки проекта:  python3.12 -m unittest discover -s tests -v

Используются временные файлы; настоящий config.ini не читается.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import main  # noqa: E402

GOOD = "[supabase]\nurl = https://abc.supabase.co\nkey = sb_publishable_test_only\n"
BROKEN = {
    "нет заголовка секции": "url = https://abc.supabase.co\nkey = sb_publishable_test_only\n".encode(),
    "повторяющийся параметр": (GOOD + "url = https://abc.supabase.co\n").encode(),
    "кодировка не UTF-8": "[supabase]\nurl = https://abc.supabase.co\nkey = ключ\n".encode("cp1251"),
}


class ConfigFileTest(unittest.TestCase):
    def config_file(self, data):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "config.ini"
        path.write_bytes(data)
        patch = mock.patch.object(config, "CONFIG_PATH", path)
        patch.start()
        self.addCleanup(patch.stop)
        return path

    def test_broken_file_is_config_error(self):
        for name, data in BROKEN.items():
            with self.subTest(name):
                self.config_file(data)
                with self.assertRaises(config.ConfigError) as ctx:
                    config.load_config()
                self.assertIn("config.ini", str(ctx.exception))

    def test_message_does_not_repeat_file_contents(self):
        """Текст ошибки configparser содержит строку файла — ключ не должен попасть в окно."""
        self.config_file(b"key = sb_publishable_must_not_be_shown\nurl = https://abc.supabase.co\n")
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config()
        self.assertNotIn("must_not_be_shown", str(ctx.exception))

    def test_percent_read_literally(self):
        """Знак % в значении — обычный символ: ни ошибки разбора, ни подстановки configparser."""
        self.config_file(b"[supabase]\nurl = https://abc.supabase.co\nkey = sb_publishable_ab%cd\n")
        self.assertEqual(config.load_config(), ("https://abc.supabase.co", "sb_publishable_ab%cd"))

    def test_interpolation_syntax_not_leaked(self):
        """%(…)s в значении не приводит к исключению configparser с этим значением в тексте."""
        self.config_file(b"[supabase]\nurl = https://abc.supabase.co\nkey = sb_publishable_%(secret_part)s\n")
        try:
            _url, key = config.load_config()
        except config.ConfigError as e:
            self.assertNotIn("secret_part", str(e))
        else:
            self.assertEqual(key, "sb_publishable_%(secret_part)s")

    def test_valid_file(self):
        self.config_file(GOOD.encode())
        self.assertEqual(config.load_config(), ("https://abc.supabase.co", "sb_publishable_test_only"))

    def test_missing_file(self):
        """Регрессия: отсутствующий файл — прежнее понятное сообщение."""
        path = self.config_file(GOOD.encode())
        path.unlink()
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config()
        self.assertIn("Не найден файл настроек", str(ctx.exception))

    def test_secret_key_still_rejected(self):
        """Регрессия: секретный ключ по-прежнему отклоняется."""
        self.config_file(b"[supabase]\nurl = https://abc.supabase.co\nkey = sb_secret_test_only\n")
        with self.assertRaises(config.ConfigError):
            config.load_config()

    def test_main_shows_dialog_for_broken_file(self):
        """main() показывает окно с ошибкой и не запускает приложение."""
        self.config_file(BROKEN["нет заголовка секции"])
        with mock.patch.object(main.tk, "Tk") as tk_root, \
                mock.patch.object(main.messagebox, "showerror") as showerror, \
                mock.patch.object(main, "Database") as database, \
                mock.patch.object(main, "App") as app:
            main.main()
        showerror.assert_called_once()
        self.assertEqual(showerror.call_args[0][0], "Ошибка настроек")
        tk_root.return_value.withdraw.assert_called_once()
        database.assert_not_called()
        app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
