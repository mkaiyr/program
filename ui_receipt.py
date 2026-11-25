"""Окно «Чек заказа»: предпросмотр, печать и повторная печать.

Окно получает только словарь заказа (из списка «Заказы») и ничего не пишет в базу данных: ни создания заказа, ни оплаты, ни списания.
Печать идёт в фоновом потоке (сетевой принтер может не отвечать), результат — в строке состояния; ошибки показываются понятным текстом.
"""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import receipt as rc
import receipt_print as rp
import receipt_render as rr
import ui_theme as theme
from print_log import PrintLog
from printing import PrintError, PrinterConfig, load_printer_config


def pgm_photo(image, master):
    """Картинка PIL (1 бит) -> tk.PhotoImage без ImageTk: формат PGM понимает сам Tk. master обязателен: иначе картинка привяжется к «корневому» окну по умолчанию."""
    g = image.convert("L")
    return tk.PhotoImage(master=master, data=b"P5\n%d %d\n255\n" % g.size + g.tobytes())


class ReceiptWindow(tk.Toplevel):
    def __init__(self, master, order, cfg=None, log=None):
        super().__init__(master)
        self.order = order
        self.cfg_error = ""
        if cfg is None:
            try:
                cfg = load_printer_config()
            except PrintError as e:
                cfg, self.cfg_error = PrinterConfig(), str(e)
        self.cfg = cfg
        self.log = log or PrintLog(cfg.log_path or None)
        self.prepared = None
        self.photo = None
        self.busy = False
        self.closed = False
        self.title(f"Чек заказа № {order.get('id', '')}")
        self.minsize(660, 600)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.refresh()

    # ---------- Разметка ----------

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        top = ttk.Frame(root)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text=f"Чек заказа № {self.order.get('id', '')}", style="H1.TLabel").pack(side="left")
        self.close_btn = ttk.Button(top, text="Закрыть", command=self.close)
        self.close_btn.pack(side="right")
        self.info = ttk.Label(root, text="", style="Muted.TLabel", wraplength=620, justify="left")
        self.info.pack(fill="x")
        box = ttk.Frame(root)
        box.pack(fill="both", expand=True, pady=8)
        self.canvas = tk.Canvas(box, bg=theme.BORDER, highlightthickness=0, width=600, height=420)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        actions = ttk.Frame(root)
        actions.pack(fill="x")
        self.print_btn = ttk.Button(actions, text="Печать", style="Big.Primary.TButton", command=self.print_clicked)
        self.print_btn.pack(side="left")
        self.save_btn = ttk.Button(actions, text="Сохранить картинкой…", command=self.save_image)
        self.save_btn.pack(side="left", padx=8)
        self.status = ttk.Label(root, text="", style="Status.TLabel", wraplength=620, justify="left")
        self.status.pack(fill="x", pady=(8, 0))

    def _say(self, text, color="gray"):
        self.status.config(text=text, foreground=theme.message_color(color))

    # ---------- Предпросмотр ----------

    def refresh(self):
        """Пересобирает чек (номер копии берётся из журнала) и обновляет предпросмотр и кнопки."""
        self.canvas.delete("all")
        try:
            self.prepared = rp.prepare(self.order, self.cfg, self.log)
        except (rc.ReceiptError, rr.RenderError) as e:
            self.prepared = None
            self.info.config(text="")
            self._say(str(e), "red")
            self.print_btn.config(state="disabled", text="Печать")
            self.save_btn.config(state="disabled")
            return
        p = self.prepared
        if p.image is not None:
            self.photo = pgm_photo(p.image, self)
            self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
            self.canvas.configure(scrollregion=(0, 0, p.image.size[0], p.image.size[1]))
            self.canvas.config(width=min(p.image.size[0], 600))
        else:                                              # без Pillow — текстовый вид тех же строк
            self.photo = None
            self.canvas.create_text(10, 10, anchor="nw", text=p.receipt.text(), font=("Courier", 12), fill=theme.TEXT)
            self.canvas.configure(scrollregion=self.canvas.bbox("all") or (0, 0, 10, 10))
        voided = bool(self.order.get("voided"))
        printed = p.copy_no
        parts = [f"Принтер: {self.cfg.describe()}.", "Данные заказа взяты из списка «Заказы» (на момент его последнего обновления)."]
        if printed:
            parts.append(f"На этом компьютере чек этого заказа уже печатался: {printed} раз(а). Новая печать будет помечена как повторная.")
        parts.append("Учёт печати локальный: его ведёт только этот компьютер, между кассами он не общий.")
        if p.lost:
            parts.append(f"В названиях есть символы, которых нет в шрифте чека, они заменены на «?»: {p.lost}")
        if p.image is None:
            parts.append("Pillow не установлен: показан текстовый вид, печать недоступна (pip install Pillow).")
        self.info.config(text=" ".join(parts))
        can_print = not voided and p.image is not None and not self.busy
        self.print_btn.config(text="Повторная печать" if printed else "Печать", state="normal" if can_print else "disabled")
        self.save_btn.config(state="normal" if p.image is not None else "disabled")
        if voided:
            self._say("Заказ аннулирован: чек можно посмотреть, но не напечатать.", "orange")
        elif not self.cfg.configured and not self.status.cget("text"):
            self._say(self.cfg_error or "Принтер не настроен: предпросмотр доступен, печать — после настройки [printer] в config.ini.", "orange")

    # ---------- Печать ----------

    def print_clicked(self):
        if self.busy or self.prepared is None:
            return
        n = self.prepared.copy_no
        if n and not messagebox.askyesno("Повторная печать",
                                         f"На этом компьютере чек заказа № {self.order['id']} уже печатался ({n} раз(а)).\nНапечатать ещё одну копию?\n\n"
                                         "Новый заказ не создаётся, оплата и списание не выполняются.", parent=self):
            return
        self.busy = True
        self.print_btn.config(state="disabled")
        self._say("Печать…", "gray")
        order, cfg, log = self.order, self.cfg, self.log

        def worker():
            try:
                result = rp.print_order(order, cfg, log)
            except PrintError as e:
                self._deliver(self._failed, e)
            except Exception as e:  # непредвиденная ошибка не должна молча ронять поток
                self._deliver(self._failed, PrintError(f"Непредвиденная ошибка печати: {type(e).__name__}", "failed"))
            else:
                self._deliver(self._printed, result)

        threading.Thread(target=worker, daemon=True).start()

    def _deliver(self, callback, value):
        try:
            self.after(0, self._finish, callback, value)
        except (RuntimeError, tk.TclError):
            pass                                          # окно уже закрыто

    def _finish(self, callback, value):
        if self.closed:
            return
        self.busy = False
        callback(value)

    def _printed(self, result):
        text = "Чек отправлен на печать." if not result.copy_no else f"Повторная печать отправлена (копия {result.copy_no})."
        if not result.logged:
            text += " Локальный журнал печати записать не удалось — следующий чек на этом компьютере не будет помечен как повторный."
        self.refresh()
        self._say(text, "green" if result.logged else "orange")

    def _failed(self, error):
        self.refresh()
        self._say(str(error), "red")
        messagebox.showerror("Печать не выполнена", str(error), parent=self)

    # ---------- Сохранение картинкой ----------

    def save_image(self):
        if self.prepared is None or self.prepared.image is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".png", filetypes=[("PNG", "*.png")],
                                            initialfile=f"check_{self.order['id']}.png")
        if not path:
            return
        try:
            self.prepared.image.convert("L").save(path, "PNG")
        except OSError:
            messagebox.showerror("Сохранение", "Не удалось сохранить картинку чека.", parent=self)
            return
        self._say("Картинка чека сохранена.", "green")

    def close(self):
        self.closed = True
        self.destroy()
