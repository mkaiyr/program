"""Заключительный аудит печати чеков: чек из сохранённого заказа без пересчёта, неизменность заказа, ошибки принтера, повторная печать,
крайние случаи вёрстки, Unicode, отсутствие Pillow / принтера / команды печати, ветка Windows (подставной winspool), локальный журнал,
«не фискальный» и самопроверка принтера. Печать на физическом принтере этими тестами НЕ проверяется."""

import copy
import ctypes
import socket
import sys
import tempfile
import unicodedata
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import print_log  # noqa: E402
import print_test  # noqa: E402
import printing  # noqa: E402
import receipt as rc  # noqa: E402
import receipt_print as rp  # noqa: E402
import receipt_render as rr  # noqa: E402
from _tk import get_root  # noqa: E402

KZ = "құүғңіәөҚҰҮҒҢІӘӨ"
needs_pil = unittest.skipUnless(rr.available(), "нет Pillow")


def item(name, qty, price):
    return {"item_name": name, "quantity": qty, "unit_price": D(str(price)), "line_total": D(str(price)) * qty}


def order(items=None, **kw):
    items = items or [item("Қуырылған тауық еті", 2, 1190), item("Чай", 1, 1190)]
    sub = sum(i["line_total"] for i in items)
    o = {"id": 17, "created_at": "25.11.2026 14:30", "cashier_name": "Айгерім Қасымова", "subtotal": sub, "service_percent": D(10),
         "service_amount": (sub / 10).quantize(D(1)), "status": "completed", "voided": False, "items": items}
    o["total"] = o["subtotal"] + o["service_amount"]
    o.update(kw)
    return o


class FromStoredOrderTest(unittest.TestCase):
    def test_receipt_prints_stored_numbers_not_recalculated_ones(self):
        """Надбавка 7% (а не 10%), хотя приложение считает 10%: чек берёт сохранённые значения заказа."""
        o = order([item("Чай", 1, 1000)], service_percent=D(7), service_amount=D(70), total=D(1070))
        text = rc.build_receipt(o).text()
        self.assertIn("Надбавка за обслуживание 7%", text)
        self.assertIn("70 ₸", text)
        self.assertIn("1 070 ₸", text)
        self.assertNotIn("100 ₸", text)

    def test_inconsistent_order_is_refused_not_silently_fixed(self):
        o = order(total=D("1"))
        with self.assertRaises(rc.ReceiptError):
            rc.build_receipt(o)

    def test_old_schema_order_without_service_charge(self):
        o = order([item("Чай", 1, 500)], service_percent=D(0), service_amount=D(0))
        o["total"] = D(500)
        text = rc.build_receipt(o).text()
        self.assertNotIn("Надбавка", text)
        self.assertIn("500 ₸", text)

    def test_building_and_printing_never_modify_the_order(self):
        o = order()
        before = copy.deepcopy(o)
        rc.build_receipt(o, copy_no=2, printed_at="25.11.2026 15:00", payment_method="Наличные")
        if rr.available():
            d = Path(tempfile.mkdtemp())
            cfg = printing.PrinterConfig(mode="file", path=str(d / "o.bin"), log_path=str(d / "l.jsonl"))
            rp.print_order(o, cfg)
            rp.print_order(o, cfg)
        self.assertEqual(o, before)


@needs_pil
class PrinterFailureTest(unittest.TestCase):
    def test_printer_error_loses_nothing_and_printing_can_be_retried(self):
        d = Path(tempfile.mkdtemp())
        o = order()
        before = copy.deepcopy(o)
        log = print_log.PrintLog(d / "l.jsonl")
        bad = printing.PrinterConfig(mode="file", path=str(d))                       # путь — каталог: запись невозможна
        with self.assertRaises(printing.PrintError):
            rp.print_order(o, bad, log)
        self.assertEqual(o, before)                                                  # заказ цел
        self.assertEqual(log.count_ok(17), 0)                                        # копией не считается
        good = printing.PrinterConfig(mode="file", path=str(d / "ok.bin"))
        res = rp.print_order(o, good, log)                                           # принтер «вернулся» — печать проходит как первая
        self.assertEqual(res.copy_no, 0)
        self.assertEqual([r["ok"] for r in log.history(17)], [False, True])

    def test_two_windows_share_the_local_log_for_copy_marking(self):
        d = Path(tempfile.mkdtemp())
        cfg = printing.PrinterConfig(mode="file", path=str(d / "o.bin"))
        log = print_log.PrintLog(d / "l.jsonl")
        rp.print_order(order(), cfg, log)
        self.assertEqual(rp.prepare(order(), cfg, log).copy_no, 1)
        self.assertIn("ПОВТОРНАЯ ПЕЧАТЬ (копия 1)", rp.prepare(order(), cfg, log).receipt.text())
        other = print_log.PrintLog(d / "другой_компьютер.jsonl")                    # журнал другого компьютера пуст — там чек «первый»
        self.assertEqual(rp.prepare(order(), cfg, other).copy_no, 0)


@needs_pil
class LayoutStressTest(unittest.TestCase):
    def check_fit(self, o, width=576):
        cols = rc.cols_for_width(width)
        r = rc.build_receipt(o, cols=cols)
        self.assertTrue(all(len(line) <= cols for line in r.text().split("\n")), [ln for ln in r.text().split("\n") if len(ln) > cols])
        img, lost = rr.render_receipt(r, width_dots=width)
        self.assertEqual((img.size[0], lost), (width, ""))
        ink = img.convert("L").point(lambda v: 255 - v).getbbox()
        self.assertGreaterEqual(ink[0], 0)
        self.assertLessEqual(ink[2], width)                                         # ничего не выходит за край бумаги
        self.assertEqual(rr.decode_escpos_raster(rr.escpos_job(img)).tobytes(), img.tobytes())
        return r, img

    def test_very_long_names_and_one_long_word(self):
        for width in (576, 512):
            self.check_fit(order([item("Қуырылған тауық еті картофельмен және ерекше соуспен (үлкен порция) " * 4, 1, 1190),
                                  item("Оченьдлинноенаzваниеблюдабезпробелов" * 6, 1, 100)]), width)

    def test_many_items_and_huge_amounts(self):
        items = [item(f"Блюдо № {i} с длинным названием для проверки переноса строк", 99, D("1234567.50")) for i in range(50)]
        r, img = self.check_fit(order(items))
        self.assertGreater(img.size[1], 3000)
        self.assertIn("6 722 220 037 ₸", r.text())
        self.assertGreater(rr.escpos_job(img).count(b"\x1dv0\x00"), 30)             # длинный чек уходит несколькими кусками

    def test_big_order_id_and_fractional_money(self):
        self.check_fit(order([item("Чай", 3, D("199.99"))], id=9223372036854775807))

    def test_unicode_decomposed_text_is_normalized(self):
        composed = "Йогурт Ёжик Қазақ"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(len(composed), len(decomposed))
        a = rr.render_receipt(rc.build_receipt(order([item(composed, 1, 1000)])))[0]
        b = rr.render_receipt(rc.build_receipt(order([item(decomposed, 1, 1000)])))[0]
        self.assertEqual(a.tobytes(), b.tobytes())                                    # «й» и «ё» рисуются одинаково при любой записи

    def test_control_and_invisible_characters_are_removed(self):
        text = rc.build_receipt(order([item("Чай\nс\tлимоном\x07​‮тёплый", 1, 500)])).text()
        self.assertIn("Чай с лимоном", text)
        self.assertNotIn("\x07", text)
        self.assertNotIn("​", text)
        self.assertNotIn("‮", text)
        with self.assertRaises(rc.ReceiptError):
            rc.build_receipt(order([{"item_name": "\x07​ ", "quantity": 1, "unit_price": D(1), "line_total": D(1)}]))

    def test_kazakh_and_russian_survive_cleaning(self):
        self.assertEqual(rc.clean(KZ + " абвгдеёжз ₸ №"), KZ + " абвгдеёжз ₸ №")
        self.assertEqual(rc.clean(None), "")


@needs_pil
class MissingPiecesTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.log = print_log.PrintLog(self.d / "l.jsonl")

    def test_lp_command_missing_or_slow(self):
        cfg = printing.PrinterConfig(mode="system", name="POS-80")
        with mock.patch.object(printing, "list_system_printers", return_value=["POS-80"]), mock.patch.object(printing.sys, "platform", "darwin"):
            with mock.patch.object(printing.subprocess, "run", side_effect=FileNotFoundError):
                with self.assertRaises(printing.PrintError) as cm:
                    rp.print_order(order(), cfg, self.log)
            self.assertEqual(cm.exception.code, "failed")
            self.assertIn("lp", str(cm.exception))
            with mock.patch.object(printing.subprocess, "run", side_effect=printing.subprocess.TimeoutExpired("lp", 15)):
                with self.assertRaises(printing.PrintError) as cm:
                    rp.print_order(order(), cfg, self.log)
            self.assertEqual(cm.exception.code, "timeout")
        self.assertEqual(self.log.count_ok(17), 0)

    def test_lpstat_failure_is_reported(self):
        with mock.patch.object(printing.sys, "platform", "darwin"), mock.patch.object(printing.shutil, "which", return_value="/usr/bin/lpstat"), \
                mock.patch.object(printing.subprocess, "run", side_effect=OSError):
            with self.assertRaises(printing.PrintError):
                printing.list_system_printers()

    def test_font_missing_gives_clear_error_and_no_crash(self):
        with mock.patch.object(rr, "FONT_DIR", self.d / "нет_шрифтов"):
            with self.assertRaises(printing.PrintError) as cm:
                rp.print_order(order(), printing.PrinterConfig(mode="file", path=str(self.d / "o.bin")), self.log)
        self.assertIn("шрифт", str(cm.exception))
        self.assertFalse((self.d / "o.bin").exists())

    def test_window_with_bad_config_shows_message_and_cannot_print(self):
        import ui_receipt
        root = get_root()
        err = printing.PrintError("В config.ini неверный режим принтера «laser».", "bad_config")
        sync = type("T", (), {"__init__": lambda s, target, daemon=None: setattr(s, "t", target), "start": lambda s: s.t()})
        with mock.patch.object(ui_receipt, "load_printer_config", side_effect=err), mock.patch.object(ui_receipt.messagebox, "showerror") as shown, \
                mock.patch.object(ui_receipt.threading, "Thread", sync):
            win = ui_receipt.ReceiptWindow(root, order(), log=self.log)
            self.addCleanup(lambda: win.winfo_exists() and win.destroy())
            self.assertIn("неверный режим", win.status["text"])
            win.print_btn.invoke()
            for _ in range(5):
                root.update()
            self.assertIn("не настроен", shown.call_args[0][1])
        self.assertEqual(self.log.count_ok(17), 0)


def fake_winspool(open_ok=True, start_ok=True, written=None):
    calls = []
    w = mock.Mock()
    w.OpenPrinterW.side_effect = lambda *a: (calls.append("open"), int(open_ok))[1]
    w.StartDocPrinterW.side_effect = lambda *a: (calls.append("startdoc"), int(start_ok))[1]
    w.StartPagePrinter.side_effect = lambda *a: (calls.append("startpage"), 1)[1]

    def write(h, data, n, byref_written):
        calls.append("write")
        byref_written._obj.value = n if written is None else written
        return 1
    w.WritePrinter.side_effect = write
    w.EndPagePrinter.side_effect = lambda *a: (calls.append("endpage"), 1)[1]
    w.EndDocPrinter.side_effect = lambda *a: (calls.append("enddoc"), 1)[1]
    w.ClosePrinter.side_effect = lambda *a: (calls.append("close"), 1)[1]
    return w, calls


class WindowsBranchTest(unittest.TestCase):
    """Ветка Windows проверена на подставном winspool: порядок вызовов и обработка отказов. Настоящий Windows и принтер не использовались."""
    cfg = printing.PrinterConfig(mode="system", name="POS-80")

    def run_send(self, winspool, printers=("POS-80",)):
        fake = mock.Mock(winspool=winspool)
        with mock.patch.object(printing.sys, "platform", "win32"), mock.patch.object(printing, "_windows_printers", return_value=list(printers)), \
                mock.patch.object(ctypes, "windll", fake, create=True):
            printing.send(self.cfg, b"\x1b@DATA")

    def test_success_calls_in_order_and_closes(self):
        w, calls = fake_winspool()
        self.run_send(w)
        self.assertEqual(calls, ["open", "startdoc", "startpage", "write", "endpage", "enddoc", "close"])

    def test_printer_not_in_list(self):
        w, calls = fake_winspool()
        with self.assertRaises(printing.PrintError) as cm:
            self.run_send(w, printers=("Другой принтер",))
        self.assertEqual((cm.exception.code, calls), ("not_found", []))

    def test_open_fails(self):
        w, calls = fake_winspool(open_ok=False)
        with self.assertRaises(printing.PrintError) as cm:
            self.run_send(w)
        self.assertEqual((cm.exception.code, calls), ("not_found", ["open"]))

    def test_start_document_refused_still_closes_handle(self):
        w, calls = fake_winspool(start_ok=False)
        with self.assertRaises(printing.PrintError) as cm:
            self.run_send(w)
        self.assertEqual(cm.exception.code, "failed")
        self.assertEqual(calls[-1], "close")

    def test_partial_write_is_an_error_and_document_is_ended(self):
        w, calls = fake_winspool(written=3)
        with self.assertRaises(printing.PrintError) as cm:
            self.run_send(w)
        self.assertIn("3 из", str(cm.exception))
        self.assertEqual(calls[-2:], ["enddoc", "close"])


class WordingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def test_local_log_is_not_presented_as_shared(self):
        import ui_receipt
        d = Path(tempfile.mkdtemp())
        log = print_log.PrintLog(d / "l.jsonl")
        log.record(17, True, "print")
        with mock.patch.object(ui_receipt.messagebox, "askyesno", return_value=False) as ask:
            win = ui_receipt.ReceiptWindow(self.root, order(), printing.PrinterConfig(mode="file", path=str(d / "o.bin")), log)
            self.addCleanup(lambda: win.winfo_exists() and win.destroy())
            info = win.info["text"]
            self.assertIn("На этом компьютере", info)
            self.assertIn("локальный", info)
            self.assertIn("между кассами он не общий", info)
            self.assertIn("из списка «Заказы»", info)
            if rr.available():
                win.print_btn.invoke()
                self.assertIn("На этом компьютере", ask.call_args[0][1])
        self.assertTrue(str(print_log.DEFAULT_PATH).startswith(str(Path.home())))          # журнал в домашней папке пользователя, не в базе и не в репозитории
        self.assertNotIn("supabase", (ROOT / "print_log.py").read_text(encoding="utf-8").lower())

    def test_receipt_is_never_called_fiscal(self):
        text = rc.build_receipt(order()).text()
        self.assertEqual(text.count("фискал"), 1)
        self.assertIn("Чек не является фискальным документом", text)
        for name in ("ui_receipt.py", "receipt_print.py", "printing.py", "print_log.py", "receipt_render.py", "print_test.py"):
            self.assertNotIn("фискал", (ROOT / name).read_text(encoding="utf-8").lower(), name)
        lines = [ln for ln in (ROOT / "receipt.py").read_text(encoding="utf-8").splitlines() if "фискал" in ln.lower()]
        self.assertTrue(all(ln.startswith("FISCAL_NOTE") or ln.startswith("Это обычный") for ln in lines), lines)   # только пометка и пояснение в докстроке

    def test_orders_panel_has_receipt_button_too(self):
        import ui_orders

        class Db:
            def load_orders(self, **kw):
                return [order()]
        with mock.patch.object(ui_orders.threading, "Thread", type("T", (), {"__init__": lambda s, target, daemon=None: setattr(s, "t", target), "start": lambda s: s.t()})):
            frame = ui_orders.OrdersPanel(self.root, Db(), on_auth_error=mock.Mock())
            self.addCleanup(frame.destroy)
            self.assertEqual(str(frame.receipt_btn["state"]), "disabled")


@needs_pil
class SelfTestScriptTest(unittest.TestCase):
    def test_file_mode_list_and_preview(self):
        d = Path(tempfile.mkdtemp())
        out = d / "t.bin"
        with mock.patch("builtins.print"):
            self.assertEqual(print_test.main(["--file", str(out)]), 0)
            self.assertEqual(print_test.main(["--preview", str(d / "t.png")]), 0)
        data = out.read_bytes()
        self.assertTrue(data.startswith(b"\x1b@") and data.endswith(rr.CUT_PARTIAL))
        self.assertEqual(rr.decode_escpos_raster(data).size[0], 576)
        self.assertTrue((d / "t.png").stat().st_size > 1000)
        with mock.patch("builtins.print"), mock.patch.object(print_test, "list_system_printers", return_value=["POS-80"]):
            self.assertEqual(print_test.main(["--list"]), 0)

    def test_width_option_and_errors_are_clean(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch("builtins.print"):
            self.assertEqual(print_test.main(["--file", str(d / "t.bin"), "--width", "512"]), 0)
            self.assertEqual(rr.decode_escpos_raster((d / "t.bin").read_bytes()).size[0], 512)
            self.assertEqual(print_test.main(["--file", str(d / "t2.bin"), "--width", "500"]), 1)       # не кратно 8: ошибка без исключения
            with mock.patch.object(print_test, "load_printer_config", return_value=printing.PrinterConfig()):
                self.assertEqual(print_test.main([]), 1)                                                  # принтер не настроен

    def test_self_test_does_not_touch_the_real_print_log(self):
        d = Path(tempfile.mkdtemp())
        real = Path(tempfile.mkdtemp()) / "real.jsonl"
        with mock.patch("builtins.print"), mock.patch.object(print_log, "DEFAULT_PATH", real):
            print_test.main(["--file", str(d / "t.bin")])
        self.assertFalse(real.exists())


if __name__ == "__main__":
    unittest.main()
