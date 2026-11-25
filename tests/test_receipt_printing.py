"""Чек 80 мм: содержимое, растровая печать с казахскими буквами, ESC/POS, отправка, ошибки, повторная печать и изоляция от базы.

Принтера в тестах нет: сеть проверяется локальным сервером на 127.0.0.1, система печати — подставной командой lp, файл — настоящим файлом.
Печать на физическом принтере этими тестами НЕ проверяется."""

import ast
import socket
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import print_log  # noqa: E402
import printing  # noqa: E402
import receipt as rc  # noqa: E402
import receipt_print as rp  # noqa: E402
import receipt_render as rr  # noqa: E402
from _tk import get_root  # noqa: E402
KZ = "құүғңіәө" + "ҚҰҮҒҢІӘӨ"
RU = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
BASE = {"қ": "к", "ұ": "у", "ү": "у", "ғ": "г", "ң": "н", "і": "и", "ә": "а", "ө": "о"}


def order(**kw):
    o = {"id": 17, "created_at": "25.11.2026 14:30", "cashier_name": "Айгерім Қасымова", "subtotal": Decimal("3570"), "service_percent": Decimal("10"),
         "service_amount": Decimal("357"), "total": Decimal("3927"), "status": "completed", "voided": False,
         "items": [{"item_name": "Қуырылған тауық еті, картофель және соус (үлкен порция)", "quantity": 2, "unit_price": Decimal("1190"), "line_total": Decimal("2380")},
                   {"item_name": "Чай", "quantity": 1, "unit_price": Decimal("1190"), "line_total": Decimal("1190")}]}
    o.update(kw)
    return o


class ReceiptContentTest(unittest.TestCase):
    def test_totals_and_lines(self):
        text = rc.build_receipt(order()).text()
        for part in ("Чек заказа № 17", "25.11.2026 14:30", "Айгерім Қасымова", "2 x 1 190 ₸", "2 380 ₸", "Подытог", "3 570 ₸",
                     "Надбавка за обслуживание 10%", "357 ₸", "ИТОГО", "3 927 ₸", "Спасибо за заказ!", rc.FISCAL_NOTE):
            self.assertIn(part, text)
        self.assertNotIn("ПОВТОРНАЯ", text)
        self.assertNotIn("Оплата:", text)          # способа оплаты в заказе нет — он не выдумывается
        self.assertNotIn("Скидка", text)           # скидок в заказе нет

    def test_no_line_wider_than_columns_and_amounts_aligned(self):
        r = rc.build_receipt(order())
        self.assertTrue(all(len(line) <= rc.COLS for line in r.text().split("\n")))
        total_line = [ln for ln in r.text().split("\n") if ln.startswith("ИТОГО")][0]
        self.assertEqual(len(total_line), rc.COLS)

    def test_kazakh_text_is_kept(self):
        o = order(items=[{"item_name": "Қ ұ ү ғ ң і ә ө Қ Ұ Ү Ғ Ң І Ә Ө", "quantity": 1, "unit_price": Decimal("3927"), "line_total": Decimal("3927")}],
                  subtotal=Decimal("3927"), service_amount=Decimal(0), total=Decimal("3927"))
        text = rc.build_receipt(o, cafe_name="Бистро Қазақстан").text()
        for ch in KZ:
            self.assertIn(ch, text)
        self.assertIn("Бистро Қазақстан", text)

    def test_columns_follow_paper_width(self):
        self.assertEqual((rc.cols_for_width(576), rc.cols_for_width(512)), (40, 36))
        for width in (576, 512):
            cols = rc.cols_for_width(width)
            self.assertTrue(all(len(line) <= cols for line in rc.build_receipt(order(), cols=cols).text().split("\n")))

    def test_no_service_charge_means_no_subtotal_block(self):
        o = order(subtotal=Decimal("3570"), service_percent=Decimal(0), service_amount=Decimal(0), total=Decimal("3570"))
        text = rc.build_receipt(o).text()
        self.assertNotIn("Подытог", text)
        self.assertNotIn("Надбавка", text)
        self.assertIn("3 570 ₸", text)

    def test_reprint_is_marked_and_dated(self):
        r = rc.build_receipt(order(), copy_no=2, printed_at="25.11.2026 15:00")
        self.assertIn("ПОВТОРНАЯ ПЕЧАТЬ (копия 2)", r.text())
        self.assertIn("Напечатано: 25.11.2026 15:00", r.text())
        self.assertEqual(r.copy_no, 2)

    def test_voided_banner(self):
        self.assertIn("ЗАКАЗ АННУЛИРОВАН", rc.build_receipt(order(voided=True)).text())

    def test_payment_and_discount_only_when_present(self):
        o = order(discount=Decimal("100"), total=Decimal("3927"))
        t = rc.build_receipt(o, payment_method="Наличные").text()
        self.assertIn("Скидка", t)
        self.assertIn("-100 ₸", t)
        self.assertIn("Оплата: Наличные", t)
        self.assertIn("3 827 ₸", t)                # итог = итог заказа − скидка

    def test_broken_orders_are_refused(self):
        bad = [order(id=0), order(items=[]), order(items=None), order(total=Decimal("1")),
               order(items=[{"item_name": "Чай", "quantity": 0, "unit_price": 1, "line_total": 0}]),
               order(items=[{"item_name": "Чай", "quantity": 1, "unit_price": 1190, "line_total": 1}]),
               order(items=[{"item_name": "", "quantity": 1, "unit_price": 1, "line_total": 1}]),
               order(items=[{"quantity": 1}]), None, "заказ"]
        for o in bad:
            with self.assertRaises(rc.ReceiptError, msg=str(o)[:60]):
                rc.build_receipt(o)


@unittest.skipUnless(rr.available(), "нет Pillow")
class RenderTest(unittest.TestCase):
    def test_fonts_have_kazakh_russian_tenge_and_number_sign(self):
        regular, bold = rr.load_fonts()
        for font in (regular, bold):
            self.assertEqual(rr.missing_chars(font, KZ + RU + "₸№0123456789"), [])

    def test_font_files_cover_kazakh_in_their_cmap(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest("нет fontTools")
        for name in ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"):
            cmap = TTFont(rr.FONT_DIR / name).getBestCmap()
            self.assertEqual([c for c in KZ + "₸№" if ord(c) not in cmap], [], name)

    def test_kazakh_letters_are_drawn_not_replaced(self):
        regular, _ = rr.load_fonts()
        for kz, base in BASE.items():
            self.assertNotEqual(rr._ink(regular, kz), rr._ink(regular, base), f"«{kz}» нарисована как «{base}»")
            self.assertNotEqual(rr._ink(regular, kz.upper()), rr._ink(regular, base.upper()))
        # ни одна казахская буква не совпадает с «пустой рамкой» отсутствующего символа
        probe = rr._ink(regular, rr.PROBE)
        self.assertTrue(all(rr._ink(regular, c) != probe for c in KZ))

    def test_missing_symbol_is_reported_and_replaced(self):
        regular, _ = rr.load_fonts()
        self.assertEqual(rr.missing_chars(regular, "Чай 😀 қ"), ["😀"])
        img, lost = rr.render_receipt(rc.build_receipt(order(items=[{"item_name": "Чай 😀", "quantity": 1, "unit_price": 3927, "line_total": 3927}],
                                                            subtotal=3927, service_amount=0, total=3927)))
        self.assertEqual(lost, "😀")

    def test_image_geometry_for_80mm(self):
        for width in (576, 512):
            img, lost = rr.render_receipt(rc.build_receipt(order(), cols=rc.cols_for_width(width)), width_dots=width)
            self.assertEqual((img.mode, img.size[0], lost), ("1", width, ""))
            self.assertEqual(img.size[0] % 8, 0)
            self.assertGreater(img.size[1], 200)
            self.assertEqual(img.getbbox() is not None, True)
            left, _, right, _ = img.convert("L").point(lambda v: 255 - v).getbbox()      # чернила не выходят за край бумаги
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(right, width)
        with self.assertRaises(rr.RenderError):
            rr.render_receipt(rc.build_receipt(order()), width_dots=500)                  # не кратно 8
        with self.assertRaises(rr.RenderError):
            rr.render_receipt(rc.build_receipt(order()), width_dots=512)                  # 40 знаков в 512 точек не помещаются

    def test_whole_pipeline_does_not_replace_kazakh_letters(self):
        """Чек целиком (а не только шрифт): картинка со строкой из казахских букв отличается от картинки с похожими русскими."""
        def pic(text):
            return rr.render_receipt(rc.Receipt(order_id=1, lines=[rc.Line(text, align="center")]))[0].tobytes()
        for kz, base in BASE.items():
            self.assertNotEqual(pic(kz), pic(base), f"в чеке «{kz}» заменена на «{base}»")
            self.assertNotEqual(pic(kz.upper()), pic(base.upper()))
        self.assertNotEqual(pic("қ"), pic("?"))
        self.assertNotEqual(pic(KZ), pic("?" * len(KZ)))

    def test_kazakh_receipt_has_ink_and_differs_from_latin_replacement(self):
        a, _ = rr.render_receipt(rc.build_receipt(order(cashier_name="Қасым Ұлы Ғалым Үсен")))
        b, _ = rr.render_receipt(rc.build_receipt(order(cashier_name="Kasym Uly Galym Usen")))
        self.assertNotEqual(a.tobytes(), b.tobytes())

    def test_escpos_structure_and_round_trip(self):
        img, _ = rr.render_receipt(rc.build_receipt(order()))
        job = rr.escpos_job(img)
        self.assertTrue(job.startswith(b"\x1b@"))
        self.assertTrue(job.endswith(rr.CUT_PARTIAL))
        self.assertIn(b"\x1b\x64\x03", job)                                           # подача бумаги
        bands = job.count(b"\x1dv0\x00")
        self.assertEqual(bands, -(-img.size[1] // rr.BAND))
        self.assertGreater(bands, 1)                                                  # длинный чек идёт кусками
        back = rr.decode_escpos_raster(job)
        self.assertEqual(back.size, img.size)
        self.assertEqual(back.tobytes(), img.tobytes())                               # в байтах именно этот чек
        self.assertNotIn(rr.CUT_PARTIAL, rr.escpos_job(img, cut=False))
        self.assertEqual(rr.escpos_job(img).count(b"\x1b@"), 1)

    def test_escpos_width_bytes(self):
        img, _ = rr.render_receipt(rc.build_receipt(order(), cols=rc.cols_for_width(512)), width_dots=512)
        job = rr.escpos_job(img)
        i = job.index(b"\x1dv0\x00")
        self.assertEqual((job[i + 4], job[i + 5]), (64, 0))                           # 512 точек = 64 байта в строке
        with self.assertRaises(rr.RenderError):
            rr.decode_escpos_raster(b"\x1b@")
        with self.assertRaises(rr.RenderError):
            rr.decode_escpos_raster(job[:i + 20])                                     # оборванная команда

    def test_text_fallback_without_pillow(self):
        with mock.patch.object(rr, "Image", None):
            self.assertFalse(rr.available())
            p = rp.prepare(order(), printing.PrinterConfig(), print_log.PrintLog(tempfile.mkdtemp() + "/l.jsonl"))
            self.assertIsNone(p.image)
            self.assertIn("ИТОГО", p.receipt.text())
            with self.assertRaises(printing.PrintError) as cm:
                rp.print_order(order(), printing.PrinterConfig(mode="file", path="x"), print_log.PrintLog(tempfile.mkdtemp() + "/l.jsonl"))
            self.assertEqual(cm.exception.code, "no_pillow")


class ConfigTest(unittest.TestCase):
    def cfg(self, text):
        f = Path(tempfile.mkdtemp()) / "c.ini"
        f.write_text(text, encoding="utf-8")
        return printing.load_printer_config(f)

    def test_not_configured_is_not_an_error(self):
        for text in ("[supabase]\nurl=x\n", ""):
            c = self.cfg(text)
            self.assertFalse(c.configured)
            self.assertEqual(c.describe(), "принтер не настроен")
        self.assertFalse(printing.load_printer_config(Path("/нет/такого/config.ini")).configured)

    def test_valid_modes(self):
        c = self.cfg("[printer]\nmode=network\nhost=10.0.0.5\nport=9100\nwidth_dots=512\ncut=no\n[receipt]\ncafe_name=Бистро Қазақстан\n")
        self.assertEqual((c.mode, c.host, c.port, c.width_dots, c.cut, c.cafe_name), ("network", "10.0.0.5", 9100, 512, False, "Бистро Қазақстан"))
        self.assertEqual(self.cfg("[printer]\nmode=system\nname=POS-80\n").name, "POS-80")
        self.assertEqual(self.cfg("[printer]\nmode=file\npath=/tmp/x.bin\n").mode, "file")

    def test_bad_values_give_clear_errors(self):
        for text in ("[printer]\nmode=laser\n", "[printer]\nmode=network\n", "[printer]\nmode=system\n", "[printer]\nmode=file\n",
                     "[printer]\nmode=network\nhost=h\nport=abc\n", "[printer]\nmode=network\nhost=h\nport=70000\n",
                     "[printer]\nmode=network\nhost=h\nwidth_dots=500\n", "[printer]\nmode=network\nhost=h\ntimeout=0\n"):
            with self.assertRaises(printing.PrintError, msg=text) as cm:
                self.cfg(text)
            self.assertEqual(cm.exception.code, "bad_config")
            self.assertIn("config.ini", str(cm.exception))


class Collector:
    """Локальный «принтер» на 127.0.0.1: принимает байты и запоминает их."""
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.data = b""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        conn, _ = self.sock.accept()
        with conn:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                self.data += chunk
        self.sock.close()


@unittest.skipUnless(rr.available(), "нет Pillow")
class TransportTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = print_log.PrintLog(self.dir / "log.jsonl")

    def test_file_mode_writes_exact_job(self):
        cfg = printing.PrinterConfig(mode="file", path=str(self.dir / "sub" / "out.bin"))
        res = rp.print_order(order(), cfg, self.log)
        self.assertEqual((res.copy_no, res.logged), (0, True))
        data = (self.dir / "sub" / "out.bin").read_bytes()
        img, _ = rr.render_receipt(rc.build_receipt(order(), copy_no=0, printed_at=""), width_dots=576)
        self.assertEqual(rr.decode_escpos_raster(data).size, img.size)
        self.assertTrue(data.startswith(b"\x1b@") and data.endswith(rr.CUT_PARTIAL))

    def test_network_sends_bytes_to_printer(self):
        srv = Collector()
        cfg = printing.PrinterConfig(mode="network", host="127.0.0.1", port=srv.port, timeout=3)
        rp.print_order(order(), cfg, self.log)
        srv.thread.join(5)
        self.assertTrue(srv.data.startswith(b"\x1b@"))
        self.assertTrue(srv.data.endswith(rr.CUT_PARTIAL))
        self.assertEqual(rr.decode_escpos_raster(srv.data).size[0], 576)
        self.assertEqual(self.log.count_ok(17), 1)

    def test_printer_off_or_unreachable(self):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()   # порт без слушателя
        cfg = printing.PrinterConfig(mode="network", host="127.0.0.1", port=port, timeout=2)
        with self.assertRaises(printing.PrintError) as cm:
            rp.print_order(order(), cfg, self.log)
        self.assertEqual(cm.exception.code, "unreachable")
        self.assertIn("включён", str(cm.exception))
        self.assertEqual(self.log.count_ok(17), 0)                          # неудача — не копия
        hist = self.log.history(17)
        self.assertEqual((len(hist), hist[0]["ok"]), (1, False))

    def test_timeout_and_bad_host(self):
        cfg = printing.PrinterConfig(mode="network", host="10.255.255.1", port=9100, timeout=1)
        with mock.patch.object(printing.socket, "create_connection", side_effect=socket.timeout):
            with self.assertRaises(printing.PrintError) as cm:
                rp.print_order(order(), cfg, self.log)
        self.assertEqual(cm.exception.code, "timeout")
        with mock.patch.object(printing.socket, "create_connection", side_effect=socket.gaierror):
            with self.assertRaises(printing.PrintError) as cm:
                rp.print_order(order(), cfg, self.log)
        self.assertEqual(cm.exception.code, "unreachable")
        self.assertEqual(self.log.count_ok(17), 0)

    def test_not_configured(self):
        with self.assertRaises(printing.PrintError) as cm:
            rp.print_order(order(), printing.PrinterConfig(), self.log)
        self.assertEqual(cm.exception.code, "not_configured")
        self.assertIn("config.example.ini", str(cm.exception))

    def test_system_printer_missing_and_present(self):
        cfg = printing.PrinterConfig(mode="system", name="POS-80")
        with mock.patch.object(printing, "list_system_printers", return_value=["Другой"]):
            with self.assertRaises(printing.PrintError) as cm:
                rp.print_order(order(), cfg, self.log)
        self.assertEqual(cm.exception.code, "not_found")
        self.assertIn("POS-80", str(cm.exception))
        ok = mock.Mock(returncode=0)
        with mock.patch.object(printing, "list_system_printers", return_value=["POS-80"]), \
                mock.patch.object(printing.sys, "platform", "darwin"), mock.patch.object(printing.subprocess, "run", return_value=ok) as run:
            rp.print_order(order(), cfg, self.log)
        args, kw = run.call_args
        self.assertEqual(args[0], ["lp", "-d", "POS-80", "-o", "raw"])
        self.assertTrue(kw["input"].startswith(b"\x1b@"))
        bad = mock.Mock(returncode=1)
        with mock.patch.object(printing, "list_system_printers", return_value=["POS-80"]), \
                mock.patch.object(printing.sys, "platform", "darwin"), mock.patch.object(printing.subprocess, "run", return_value=bad):
            with self.assertRaises(printing.PrintError) as cm:
                rp.print_order(order(), cfg, self.log)
        self.assertEqual(cm.exception.code, "failed")

    def test_no_cups_gives_clear_message(self):
        with mock.patch.object(printing.sys, "platform", "linux"), mock.patch.object(printing.shutil, "which", return_value=None):
            with self.assertRaises(printing.PrintError) as cm:
                printing.list_system_printers()
        self.assertEqual(cm.exception.code, "not_found")

    def test_file_write_error(self):
        cfg = printing.PrinterConfig(mode="file", path=str(self.dir))          # путь — каталог
        with self.assertRaises(printing.PrintError) as cm:
            rp.print_order(order(), cfg, self.log)
        self.assertEqual(cm.exception.code, "failed")

    def test_voided_order_is_not_printed(self):
        cfg = printing.PrinterConfig(mode="file", path=str(self.dir / "o.bin"))
        with self.assertRaises(printing.PrintError) as cm:
            rp.print_order(order(voided=True), cfg, self.log)
        self.assertEqual(cm.exception.code, "voided")
        self.assertFalse((self.dir / "o.bin").exists())
        self.assertEqual(self.log.history(17), [])

    def test_broken_order_is_not_printed(self):
        cfg = printing.PrinterConfig(mode="file", path=str(self.dir / "o.bin"))
        with self.assertRaises(rc.ReceiptError):
            rp.print_order(order(total=Decimal("1")), cfg, self.log)
        self.assertFalse((self.dir / "o.bin").exists())


@unittest.skipUnless(rr.available(), "нет Pillow")
class ReprintTest(unittest.TestCase):
    def test_reprint_is_marked_counted_and_independent_of_database(self):
        d = Path(tempfile.mkdtemp())
        cfg = printing.PrinterConfig(mode="file", path=str(d / "o.bin"))
        log = print_log.PrintLog(d / "log.jsonl")
        first = rp.print_order(order(), cfg, log)
        original = (d / "o.bin").read_bytes()
        second = rp.print_order(order(), cfg, log)
        third = rp.print_order(order(), cfg, log)
        self.assertEqual((first.copy_no, second.copy_no, third.copy_no), (0, 1, 2))
        self.assertNotEqual((d / "o.bin").read_bytes(), original)                      # повторный чек отличается (пометка и время)
        self.assertEqual([r["kind"] for r in log.history(17)], ["print", "reprint", "reprint"])
        self.assertEqual(log.count_ok(17), 3)
        self.assertEqual(log.count_ok(18), 0)                                          # другой заказ не затронут
        self.assertEqual(rp.prepare(order(), cfg, log).copy_no, 3)

    def test_print_path_has_no_access_to_database(self):
        """Печать и повторная печать технически не могут создать заказ, провести оплату или списать товар: модулей базы там нет."""
        forbidden = {"db", "supabase", "httpx", "postgrest"}
        calls = ("create_order", "place_order", "void_order", "set_order_status", "force_order_ready", "delete_order", ".rpc(", ".table(")
        for name in ("receipt.py", "receipt_render.py", "receipt_print.py", "printing.py", "print_log.py", "ui_receipt.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            imported = {n.split(".")[0] for node in ast.walk(ast.parse(src)) for n in
                        ([a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])}
            self.assertFalse(imported & forbidden, f"{name} импортирует {imported & forbidden}")
            for call in calls:
                self.assertNotIn(call, src, f"{name}: упоминание {call}")


class LogTest(unittest.TestCase):
    def test_counts_only_successes_ignores_garbage_and_survives_unwritable(self):
        d = Path(tempfile.mkdtemp())
        log = print_log.PrintLog(d / "l.jsonl")
        self.assertTrue(log.record(5, True, "print"))
        self.assertTrue(log.record(5, False, "reprint", error="нет связи"))
        self.assertTrue(log.record(5, True, "reprint"))
        (d / "l.jsonl").write_text((d / "l.jsonl").read_text(encoding="utf-8") + "мусор\n{\"order_id\": \"x\"}\n[]\n", encoding="utf-8")
        self.assertEqual(log.count_ok(5), 2)
        self.assertEqual(len(log.history(5)), 3)
        blocked = print_log.PrintLog(d / "file.txt" / "l.jsonl")
        (d / "file.txt").write_text("x")
        self.assertFalse(blocked.record(1, True, "print"))                              # журнал недоступен — не исключение
        self.assertEqual(blocked.count_ok(1), 0)
        self.assertEqual(print_log.PrintLog(d / "нет.jsonl").count_ok(5), 0)


class SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


@unittest.skipUnless(rr.available(), "нет Pillow")
class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def setUp(self):
        import ui_receipt
        self.ui = ui_receipt
        self.shown, self.yes = [], mock.Mock(return_value=True)
        mock.patch.object(ui_receipt.threading, "Thread", SyncThread).start()
        mock.patch.object(ui_receipt.messagebox, "askyesno", self.yes).start()
        for name in ("showinfo", "showwarning", "showerror"):
            mock.patch.object(ui_receipt.messagebox, name, lambda *a, _n=name, **k: self.shown.append((_n, a[0], a[1] if len(a) > 1 else ""))).start()
        self.addCleanup(mock.patch.stopall)
        self.dir = Path(tempfile.mkdtemp())
        self.log = print_log.PrintLog(self.dir / "log.jsonl")

    def pump(self):
        for _ in range(8):
            self.root.update()

    def window(self, order_, cfg=None):
        win = self.ui.ReceiptWindow(self.root, order_, cfg or printing.PrinterConfig(mode="file", path=str(self.dir / "o.bin")), self.log)
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        self.pump()
        return win

    def test_preview_shows_the_real_image(self):
        win = self.window(order())
        self.assertEqual(win.photo.width(), 576)
        self.assertGreater(win.photo.height(), 200)
        self.assertEqual(win.canvas.type(win.canvas.find_all()[0]), "image")
        self.assertEqual(win.print_btn["text"], "Печать")
        self.assertIn("файл", win.info["text"])

    def test_not_configured_printer_explains_and_prints_nothing(self):
        win = self.window(order(), printing.PrinterConfig())
        self.assertIn("не настроен", win.status["text"])
        win.print_btn.invoke()
        self.pump()
        self.assertEqual(self.shown[0][0], "showerror")
        self.assertIn("не настроен", self.shown[0][2])
        self.assertEqual(self.log.count_ok(17), 0)
        self.assertEqual(win.print_btn["text"], "Печать")

    def test_print_then_reprint_with_confirmation(self):
        win = self.window(order())
        win.print_btn.invoke()
        self.pump()
        self.assertTrue((self.dir / "o.bin").exists())
        self.assertIn("отправлен на печать", win.status["text"])
        self.assertEqual(win.print_btn["text"], "Повторная печать")
        self.assertIn("повторная", win.info["text"])
        first = (self.dir / "o.bin").read_bytes()
        self.yes.return_value = False
        win.print_btn.invoke()
        self.pump()
        self.assertEqual((self.dir / "o.bin").read_bytes(), first)                      # отказ в подтверждении — ничего не напечатано
        self.assertEqual(self.log.count_ok(17), 1)
        self.yes.return_value = True
        win.print_btn.invoke()
        self.pump()
        self.assertNotEqual((self.dir / "o.bin").read_bytes(), first)
        self.assertEqual(self.log.count_ok(17), 2)
        self.assertIn("копия 1", win.status["text"])
        self.assertEqual(self.shown, [])                                                # без ошибок

    def test_printer_down_shows_error_and_keeps_copy_number(self):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        win = self.window(order(), printing.PrinterConfig(mode="network", host="127.0.0.1", port=port, timeout=1))
        win.print_btn.invoke()
        self.pump()
        self.assertEqual(self.shown[0][0:2], ("showerror", "Печать не выполнена"))
        self.assertIn("Не удалось подключиться", win.status["text"])
        self.assertEqual(win.print_btn["text"], "Печать")                               # копией не считается
        self.assertEqual(str(win.print_btn["state"]), "normal")                         # можно повторить после включения принтера

    def test_voided_order_can_be_viewed_but_not_printed(self):
        win = self.window(order(voided=True))
        self.assertEqual(str(win.print_btn["state"]), "disabled")
        self.assertIn("аннулирован", win.status["text"])
        self.assertIsNotNone(win.photo)

    def test_broken_order_shows_error_instead_of_wrong_receipt(self):
        win = self.window(order(total=Decimal("1")))
        self.assertEqual(str(win.print_btn["state"]), "disabled")
        self.assertIn("не сходятся", win.status["text"])
        self.assertIsNone(win.photo)

    def test_without_pillow_text_preview_and_no_print(self):
        with mock.patch.object(rr, "Image", None):
            win = self.window(order())
        self.assertIsNone(win.photo)
        self.assertEqual(win.canvas.type(win.canvas.find_all()[0]), "text")
        self.assertEqual(str(win.print_btn["state"]), "disabled")
        self.assertIn("Pillow", win.info["text"])

    def test_save_picture(self):
        win = self.window(order())
        target = self.dir / "check.png"
        with mock.patch.object(self.ui.filedialog, "asksaveasfilename", return_value=str(target)):
            win.save_btn.invoke()
        from PIL import Image
        self.assertEqual(Image.open(target).size[0], 576)
        with mock.patch.object(self.ui.filedialog, "asksaveasfilename", return_value=""):
            win.save_btn.invoke()                                                        # отмена — без ошибок
        self.assertEqual(self.shown, [])

    def test_closing_while_printing_is_safe(self):
        win = self.window(order())
        win.closed = True
        win._finish(lambda v: self.fail("после закрытия результат должен игнорироваться"), None)


class OrdersWindowIntegrationTest(unittest.TestCase):
    """Кнопка «Чек…» в «Заказах»: открывает окно, а база не вызывается ничем, кроме чтения списка."""
    @classmethod
    def setUpClass(cls):
        cls.root = get_root()

    def test_button_opens_receipt_and_database_is_only_read(self):
        import ui_orders
        import ui_receipt

        class ReadOnlyDb:
            def __init__(self):
                self.calls = []

            def load_orders(self, **kw):
                self.calls.append("load_orders")
                return [order()]

            def __getattr__(self, name):
                raise AssertionError(f"чек обратился к базе: {name}")

        db = ReadOnlyDb()
        patches = [mock.patch.object(ui_orders.threading, "Thread", SyncThread), mock.patch.object(ui_receipt, "load_printer_config", return_value=printing.PrinterConfig()),
                   mock.patch.object(ui_receipt.messagebox, "showerror"), mock.patch.object(ui_receipt.messagebox, "askyesno", return_value=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        win = ui_orders.OrdersWindow(self.root, db, role="cashier", on_auth_error=mock.Mock())
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        for _ in range(8):
            self.root.update()
        self.assertEqual(str(win.receipt_btn["state"]), "disabled")                      # заказ не выбран
        win.tree.selection_set("17")
        for _ in range(8):
            self.root.update()
        self.assertEqual(str(win.receipt_btn["state"]), "normal")
        win.receipt_btn.invoke()
        for _ in range(8):
            self.root.update()
        self.assertTrue(win.receipt_window.winfo_exists())
        self.assertIn("№ 17", win.receipt_window.title())
        if rr.available():
            win.receipt_window.print_btn.invoke()                                        # принтер не настроен → ошибка, но не база
            for _ in range(8):
                self.root.update()
        self.assertEqual(db.calls, ["load_orders"])
        win.receipt_window.close()


if __name__ == "__main__":
    unittest.main()
