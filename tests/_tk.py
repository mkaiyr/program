"""Общее окно Tk для всех UI-тестов.

На macOS второй tk.Tk() в одном процессе после уничтожения первого вешает
тесты, поэтому все файлы тестов используют одно корневое окно.
"""

import tkinter as tk
import unittest

import ui_theme

_root = None


def get_root():
    global _root
    if _root is None:
        try:
            _root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("нет графического дисплея")
        _root.withdraw()
        ui_theme.apply(_root)
    return _root
