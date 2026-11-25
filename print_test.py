"""Проверка принтера чеков без приложения и без базы данных: печатает тестовый чек с русским и казахским текстом.

  python3 print_test.py                 печать на принтер из config.ini ([printer])
  python3 print_test.py --list          показать принтеры, установленные в системе (для mode = system)
  python3 print_test.py --file out.bin  записать команды принтера в файл (для ручной проверки: lp -d ИМЯ -o raw out.bin)
  python3 print_test.py --preview t.png сохранить картинку тестового чека (что именно будет напечатано)
  python3 print_test.py --width 512     ширина печати в точках (80 мм: 576, у части моделей 512)

Заказ не создаётся, база данных не используется, журнал печати этого компьютера не изменяется (тест пишет во временный журнал).
Код возврата 0 — успех, 1 — ошибка (текст ошибки понятный, на русском).
"""

import sys
import tempfile
from pathlib import Path

import receipt as rc
import receipt_print as rp
import receipt_render as rr
from print_log import PrintLog
from printing import PrintError, PrinterConfig, list_system_printers, load_printer_config

TEST_ORDER = {
    "id": 1, "created_at": "ТЕСТ ПЕЧАТИ", "cashier_name": "Проверка принтера", "subtotal": 3570, "service_percent": 10, "service_amount": 357, "total": 3927, "voided": False,
    "items": [
        {"item_name": "Қазақша: қ ұ ү ғ ң і ә ө Қ Ұ Ү Ғ Ң І Ә Ө", "quantity": 1, "unit_price": 1190, "line_total": 1190},
        {"item_name": "Русский: абвгдеёжзийклмн опрстуфхцчшщъыьэюя АБВ ₸ №", "quantity": 2, "unit_price": 1190, "line_total": 2380},
    ],
}


def _arg(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    try:
        if "--list" in argv:
            names = list_system_printers()
            print("Принтеры системы:" if names else "Принтеры в системе не найдены.")
            for n in names:
                print("  ", n)
            return 0
        if not rr.available():
            raise PrintError("Нужна библиотека Pillow: pip install Pillow", "no_pillow")
        cfg = load_printer_config()
        width = int(_arg(argv, "--width", cfg.width_dots))
        cfg.width_dots = width
        tmp = Path(tempfile.mkdtemp())
        cfg.log_path = str(tmp / "test_print_log.jsonl")           # журнал этого компьютера не трогаем
        cfg.cafe_name = "ТЕСТ ПЕЧАТИ ЧЕКА"
        if _arg(argv, "--preview"):
            image = rp.prepare(TEST_ORDER, cfg, PrintLog(cfg.log_path)).image
            image.convert("L").save(_arg(argv, "--preview"), "PNG")
            print(f"Картинка тестового чека сохранена: {_arg(argv, '--preview')}")
            return 0
        if _arg(argv, "--file"):
            cfg = PrinterConfig(mode="file", path=_arg(argv, "--file"), width_dots=width, log_path=cfg.log_path, cafe_name=cfg.cafe_name)
        result = rp.print_order(TEST_ORDER, cfg)
        print(f"Тестовый чек отправлен: {cfg.describe()}.")
        if result.lost:
            print("Внимание: символы, которых нет в шрифте, заменены на «?»:", result.lost)
        print("Проверьте на бумаге: все строки целиком (ничего не обрезано по краям), казахские и русские буквы, знаки ₸ и №, обрезка бумаги.")
        return 0
    except (PrintError, rc.ReceiptError, rr.RenderError, ValueError) as e:
        print("ОШИБКА:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
