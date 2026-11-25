"""Печать чека: настройки принтера, отправка данных и понятные ошибки.

Ни одна функция здесь не получает объект базы данных: печать, в том числе повторная, технически не может создать заказ, провести оплату
или списать товар. Данные чека берутся из уже загруженного заказа (receipt.py).

Способы отправки (config.ini, секция [printer], образец — config.example.ini):
  network — сетевой термопринтер по адресу host:port (обычно 9100, «RAW»);
  system  — принтер, установленный в системе, по имени: Windows (RAW через winspool) или macOS/Linux (CUPS, команда lp -o raw);
  file    — запись команд в файл (проверка без принтера, «виртуальный» принтер).
Если секции нет или режим не задан, печать недоступна, а предпросмотр работает.
"""

import configparser
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from config import CONFIG_PATH

MODES = ("network", "system", "file")


class PrintError(Exception):
    """Ошибка печати. code: not_configured, bad_config, not_found, unreachable, timeout, failed, voided, no_pillow."""

    def __init__(self, message, code="failed"):
        super().__init__(message)
        self.code = code


@dataclass
class PrinterConfig:
    mode: str = ""
    name: str = ""
    host: str = ""
    port: int = 9100
    path: str = ""
    width_dots: int = 576
    timeout: float = 5.0
    cut: bool = True
    cafe_name: str = ""
    log_path: str = ""

    @property
    def configured(self):
        return self.mode in MODES

    def describe(self):
        if self.mode == "network":
            return f"сетевой принтер {self.host}:{self.port}"
        if self.mode == "system":
            return f"принтер «{self.name}»"
        if self.mode == "file":
            return f"файл {self.path}"
        return "принтер не настроен"


def load_printer_config(path=CONFIG_PATH):
    """Читает [printer] и [receipt] из config.ini. Нет файла или секции — печать не настроена (это не ошибка). Неверные значения — PrintError."""
    cfg = PrinterConfig()
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return cfg                             # повреждённый файл настроек диагностирует load_config, не печать
    if parser.has_section("receipt"):
        cfg.cafe_name = parser.get("receipt", "cafe_name", fallback="").strip()
    if not parser.has_section("printer"):
        return cfg
    s = parser["printer"]
    mode = s.get("mode", "").strip().lower()
    if mode and mode not in MODES:
        raise PrintError(f"В config.ini неверный режим принтера «{mode}». Допустимо: network, system, file.", "bad_config")
    cfg.mode, cfg.name, cfg.host, cfg.path = mode, s.get("name", "").strip(), s.get("host", "").strip(), s.get("path", "").strip()
    cfg.log_path = s.get("log", "").strip()
    try:
        cfg.port = int(s.get("port", "9100"))
        cfg.width_dots = int(s.get("width_dots", "576"))
        cfg.timeout = float(s.get("timeout", "5"))
    except ValueError as e:
        raise PrintError("В config.ini [printer]: port, width_dots и timeout должны быть числами.", "bad_config") from e
    cfg.cut = s.get("cut", "yes").strip().lower() not in ("no", "false", "0")
    if not 1 <= cfg.port <= 65535 or cfg.width_dots % 8 or not 256 <= cfg.width_dots <= 832 or not 0.5 <= cfg.timeout <= 60:
        raise PrintError("В config.ini [printer]: port 1–65535, width_dots кратно 8 (для 80 мм — 576 или 512), timeout 0.5–60.", "bad_config")
    if mode == "network" and not cfg.host:
        raise PrintError("В config.ini [printer] для режима network не указан host.", "bad_config")
    if mode == "system" and not cfg.name:
        raise PrintError("В config.ini [printer] для режима system не указано name (имя принтера в системе).", "bad_config")
    if mode == "file" and not cfg.path:
        raise PrintError("В config.ini [printer] для режима file не указан path.", "bad_config")
    return cfg


# ---------- принтеры системы ----------

def list_system_printers():
    """Имена установленных принтеров. macOS/Linux — lpstat; Windows — EnumPrinters (на Windows не проверялось: нет такой машины)."""
    if sys.platform.startswith("win"):
        return _windows_printers()
    if not shutil.which("lpstat"):
        raise PrintError("В системе нет службы печати CUPS (команда lpstat). Используйте сетевой принтер или режим file.", "not_found")
    try:
        out = subprocess.run(["lpstat", "-e"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise PrintError("Не удалось получить список принтеров системы.", "failed") from e
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _windows_printers():
    import ctypes
    from ctypes import wintypes

    class PRINTER_INFO_4(ctypes.Structure):
        _fields_ = [("pPrinterName", wintypes.LPWSTR), ("pServerName", wintypes.LPWSTR), ("Attributes", wintypes.DWORD)]

    flags = 0x2 | 0x4                           # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    need, count = wintypes.DWORD(), wintypes.DWORD()
    ctypes.windll.winspool.EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(need), ctypes.byref(count))
    if not need.value:
        return []
    buf = ctypes.create_string_buffer(need.value)
    if not ctypes.windll.winspool.EnumPrintersW(flags, None, 4, buf, need, ctypes.byref(need), ctypes.byref(count)):
        raise PrintError("Не удалось получить список принтеров Windows.", "failed")
    arr = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_4))
    return [arr[i].pPrinterName for i in range(count.value)]


# ---------- отправка ----------

def _send_network(cfg, data):
    try:
        with socket.create_connection((cfg.host, cfg.port), timeout=cfg.timeout) as s:
            s.settimeout(cfg.timeout)
            s.sendall(data)
    except socket.timeout as e:
        raise PrintError(f"Принтер {cfg.host}:{cfg.port} не отвечает (тайм-аут {cfg.timeout:g} с). Проверьте питание и сеть.", "timeout") from e
    except socket.gaierror as e:
        raise PrintError(f"Адрес принтера «{cfg.host}» не найден. Проверьте host в config.ini.", "unreachable") from e
    except OSError as e:
        raise PrintError(f"Не удалось подключиться к принтеру {cfg.host}:{cfg.port}. Проверьте, что он включён и в той же сети.", "unreachable") from e


def _send_system(cfg, data):
    names = list_system_printers()
    if cfg.name not in names:
        raise PrintError(f"Принтер «{cfg.name}» не найден в системе. Проверьте имя в config.ini и подключение принтера.", "not_found")
    if sys.platform.startswith("win"):
        _send_windows(cfg, data)
        return
    try:
        r = subprocess.run(["lp", "-d", cfg.name, "-o", "raw"], input=data, capture_output=True, timeout=max(cfg.timeout, 15))
    except subprocess.TimeoutExpired as e:
        raise PrintError(f"Принтер «{cfg.name}» не принял задание вовремя.", "timeout") from e
    except OSError as e:
        raise PrintError("Не удалось запустить печать (команда lp).", "failed") from e
    if r.returncode != 0:
        raise PrintError(f"Система отклонила печать на «{cfg.name}». Проверьте, что принтер включён и не на паузе.", "failed")


def _send_windows(cfg, data):  # pragma: no cover — выполняется только на Windows, на этой машине не проверялось
    import ctypes
    from ctypes import wintypes

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR), ("pDatatype", wintypes.LPWSTR)]

    w = ctypes.windll.winspool
    h = wintypes.HANDLE()
    if not w.OpenPrinterW(cfg.name, ctypes.byref(h), None):
        raise PrintError(f"Принтер «{cfg.name}» не найден или недоступен.", "not_found")
    try:
        if not w.StartDocPrinterW(h, 1, ctypes.byref(DOC_INFO_1("Чек", None, "RAW"))):
            raise PrintError(f"Принтер «{cfg.name}» не принял документ.", "failed")
        try:
            w.StartPagePrinter(h)
            written = wintypes.DWORD()
            if not w.WritePrinter(h, data, len(data), ctypes.byref(written)) or written.value != len(data):
                raise PrintError(f"Не удалось передать чек принтеру «{cfg.name}» (отправлено {written.value} из {len(data)} байт).", "failed")
            w.EndPagePrinter(h)
        finally:
            w.EndDocPrinter(h)
    finally:
        w.ClosePrinter(h)


def _send_file(cfg, data):
    try:
        Path(cfg.path).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.path).write_bytes(data)
    except OSError as e:
        raise PrintError(f"Не удалось записать файл печати «{cfg.path}».", "failed") from e


def send(cfg, data):
    """Отправляет готовые команды принтеру. Любая неудача — PrintError с понятным текстом."""
    if not cfg.configured:
        raise PrintError("Принтер не настроен. Добавьте раздел [printer] в config.ini (образец — config.example.ini).", "not_configured")
    {"network": _send_network, "system": _send_system, "file": _send_file}[cfg.mode](cfg, data)
