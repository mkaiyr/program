"""Кассовые смены: открытие, внесение и изъятие наличных, закрытие, история (sql/12_cash_shifts.sql).

ShiftPanel — панель (кассир открывает её окном ShiftWindow, администратор — разделом оболочки).
Суммы считает сервер; приложение только показывает и передаёт введённые значения.
"""

import tkinter as tk
from tkinter import messagebox, ttk

import ui_theme as theme
from db import AppError
from money import fmt_money
from ui_base import Panel
from ui_dialogs import FormDialog


class ShiftPanel(Panel):
    def __init__(self, master, db, on_auth_error, show_cashier=False):
        super().__init__(master, db, on_auth_error)
        self.show_cashier = show_cashier  # администратору в истории нужен столбец «Кассир»
        self.shift = None
        self.history = []
        self._build()
        self.reload()

    def _build(self):
        head, right = theme.header(self, "Кассовая смена", "Наличные, выручка и закрытие смены")
        head.pack(fill="x")
        self.reload_btn = ttk.Button(right, text="Обновить", command=self.reload)
        self.reload_btn.pack(side="right")

        self.state_box = ttk.Frame(self)
        self.state_box.pack(fill="x", pady=(theme.GAP, 0))

        ttk.Label(self, text="История смен", style="H2.TLabel").pack(anchor="w", pady=(theme.PAD, 6))
        cols = [("id", "№", 50, "center"), ("opened", "Открыта", 130, "w"), ("closed", "Закрыта", 130, "w"),
                ("orders", "Заказов", 70, "center"), ("sales", "Выручка", 110, "e"),
                ("expected", "Ожидалось", 110, "e"), ("actual", "Факт", 110, "e"), ("diff", "Разница", 100, "e")]
        if self.show_cashier:
            cols.insert(1, ("cashier", "Кассир", 140, "w"))
        frame, self.tree = theme.make_table(self, cols, height=6,
                                            tags={"short": theme.DANGER, "over": theme.WARNING, "open": theme.ACCENT})
        frame.pack(fill="both", expand=True)
        self.columns = [c[0] for c in cols]
        self.build_status(self).pack(fill="x", pady=(8, 0))
        self.busy_widgets.append(self.reload_btn)

    # ---------- Данные ----------

    def reload(self):
        if self.busy:
            return
        self.show_status("Загрузка...", "gray")
        self.run(lambda: (self.db.current_shift(), self.db.shift_history()), self._loaded)

    def _loaded(self, result):
        self.shift, self.history = result
        self._render_state()
        self._render_history()
        self.show_status("", "gray")

    # ---------- Отрисовка ----------

    def _render_state(self):
        for child in self.state_box.winfo_children():
            child.destroy()
        self.busy_widgets = [w for w in self.busy_widgets if w is self.reload_btn]
        if self.shift is None:
            box = theme.card(self.state_box)
            box.pack(fill="x")
            ttk.Label(box, text="Смена не открыта", style="CardH2.TLabel").pack(anchor="w")
            ttk.Label(box, text="Укажите наличные в кассе на начало смены.", style="CardMuted.TLabel").pack(anchor="w")
            row = ttk.Frame(box, style="Card.TFrame")
            row.pack(fill="x", pady=(10, 0))
            ttk.Label(row, text="Начальная сумма, ₸:", style="Card.TLabel").pack(side="left")
            self.opening_entry = ttk.Entry(row, width=14, font=theme.FONT_BIG)
            self.opening_entry.insert(0, "0")
            self.opening_entry.pack(side="left", padx=10)
            self.opening_entry.bind("<Return>", lambda _e: self.open_shift())
            btn = ttk.Button(row, text="Открыть смену", style="Big.Primary.TButton", command=self.open_shift)
            btn.pack(side="left")
            self.busy_widgets.append(btn)
            return

        shift = self.shift
        ttk.Label(self.state_box, text=f"Смена № {shift['id']} открыта {shift['opened_local']} · "
                                       f"кассир: {shift['cashier_name']}", style="Muted.TLabel").pack(anchor="w")
        cards = ttk.Frame(self.state_box)
        cards.pack(fill="x", pady=(6, 0))
        values = (("Начальная сумма", fmt_money(shift["opening_cash"]), ""),
                  ("Заказов", str(shift["orders_count"]), "без аннулированных"),
                  ("Выручка", fmt_money(shift["sales_total"]), "с надбавкой 10%"),
                  ("Внесения", fmt_money(shift["deposits_total"]), ""),
                  ("Изъятия", fmt_money(shift["withdrawals_total"]), ""),
                  ("Ожидается в кассе", fmt_money(shift["expected_cash"]), "начало + выручка + внесения − изъятия"))
        for col, (title, value, hint) in enumerate(values):
            cards.columnconfigure(col, weight=1, uniform="shift")
            box, _v, _h = theme.stat_card(cards, title, value, hint)
            box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
        buttons = ttk.Frame(self.state_box)
        buttons.pack(fill="x", pady=(theme.GAP, 0))
        for text, style, command in (("Внести наличные", "Big.TButton", lambda: self.move("deposit")),
                                     ("Изъять наличные", "Big.TButton", lambda: self.move("withdrawal")),
                                     ("Закрыть смену", "Big.Danger.TButton", self.close_shift)):
            btn = ttk.Button(buttons, text=text, style=style, command=command)
            btn.pack(side="left", padx=(0, 8))
            self.busy_widgets.append(btn)
        if shift["movements"]:
            kinds = {"deposit": "Внесение", "withdrawal": "Изъятие"}
            text = "   ".join(f"{m['time']} {kinds.get(m['kind'], m['kind'])} {fmt_money(m['amount'])}"
                             + (f" ({m['note']})" if m.get("note") else "") for m in shift["movements"][-4:])
            ttk.Label(self.state_box, text="Операции: " + text, style="Muted.TLabel", wraplength=900,
                      justify="left").pack(anchor="w", pady=(6, 0))

    def _render_history(self):
        self.tree.delete(*self.tree.get_children())
        for shift in self.history:
            closed = bool(shift["closed_at"])
            diff = shift["difference"]
            tag = "open" if not closed else ("short" if diff is not None and diff < 0
                                             else "over" if diff is not None and diff > 0 else "")
            values = {"id": shift["id"], "cashier": shift["cashier_name"], "opened": shift["opened_local"],
                      "closed": shift["closed_local"] or "открыта", "orders": shift["orders_count"],
                      "sales": fmt_money(shift["sales_total"]), "expected": fmt_money(shift["expected_cash"]),
                      "actual": fmt_money(shift["actual_cash"]) if closed else "—",
                      "diff": (f"{diff:+,.2f}".replace(",", " ") + " ₸") if closed and diff is not None else "—"}
            self.tree.insert("", "end", iid=str(shift["id"]), values=[values[c] for c in self.columns], tags=(tag,))

    # ---------- Действия ----------

    def open_shift(self):
        if self.busy or self.shift is not None:
            return
        text = self.opening_entry.get()
        self.show_status("Открытие смены...", "gray")
        self.run(lambda: self.db.open_shift(text), lambda _s: self._done("Смена открыта.", reload=True))

    def move(self, kind):
        if self.busy or self.shift is None:
            return
        title = "Внесение наличных" if kind == "deposit" else "Изъятие наличных"

        def submit(values):
            try:
                from db import parse_amount
                parse_amount(values["amount"])
            except AppError as e:
                return str(e)
            self.show_status("Проведение операции...", "gray")
            self.run(lambda: self.db.cash_move(kind, values["amount"], values["note"]),
                     lambda _s: self._done(f"{title}: проведено.", reload=True))
            return None

        FormDialog(self, title, [("amount", "Сумма, ₸:", "", False), ("note", "Комментарий:", "", False)], submit,
                   ok_text="Провести")

    def close_shift(self):
        if self.busy or self.shift is None:
            return
        shift = self.shift

        def submit(values):
            try:
                from db import parse_amount
                actual = parse_amount(values["actual"], allow_zero=True)
            except AppError as e:
                return str(e)
            diff = actual - shift["expected_cash"]
            if not messagebox.askyesno(
                    "Закрыть смену",
                    f"Ожидается: {fmt_money(shift['expected_cash'])}\nФактически: {fmt_money(actual)}\n"
                    f"Разница: {diff:+,.2f} ₸\n\nЗакрыть смену? Это действие нельзя отменить.".replace(",", " "),
                    parent=self):
                return None
            self.show_status("Закрытие смены...", "gray")
            self.run(lambda: self.db.close_shift(values["actual"]), self._closed)
            return None

        FormDialog(self, "Закрытие смены", [("actual", "Фактическая сумма, ₸:", "", False)], submit,
                   hint=f"Ожидается в кассе: {fmt_money(shift['expected_cash'])}. Пересчитайте наличные и введите факт.",
                   ok_text="Закрыть смену", ok_style="Danger.TButton")

    def _closed(self, shift):
        diff = shift["difference"]
        word = "излишек" if diff > 0 else "недостача" if diff < 0 else "расхождений нет"
        self._done(f"Смена № {shift['id']} закрыта. Ожидалось {fmt_money(shift['expected_cash'])}, "
                   f"факт {fmt_money(shift['actual_cash'])}: {word}"
                   + ("" if diff == 0 else f" {fmt_money(abs(diff))}") + ".", reload=True, color="green")

    def _done(self, text, reload=False, color="green"):
        self.show_status(text, color)
        if reload:
            self.run(lambda: (self.db.current_shift(), self.db.shift_history()), self._reloaded_keep(text, color))

    def _reloaded_keep(self, text, color):
        def done(result):
            self._loaded(result)
            self.show_status(text, color)
        return done


class ShiftWindow(tk.Toplevel):
    """Окно смены кассира."""

    def __init__(self, master, db, on_auth_error):
        super().__init__(master)
        self.title("Кассовая смена")
        self.minsize(900, 560)
        self.transient(master.winfo_toplevel())
        self.panel = ShiftPanel(self, db, on_auth_error=lambda: (self.destroy(), on_auth_error()))
        self.panel.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        ttk.Button(self, text="Закрыть", command=self.destroy).pack(anchor="e", padx=theme.PAD, pady=(0, theme.PAD))
