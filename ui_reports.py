"""Раздел «Статистика и отчёты» (sql/14_reports_categories.sql): период, показатели, диаграммы, CSV."""

from datetime import date, timedelta
from tkinter import filedialog, ttk

import ui_theme as theme
from db import AppError
from money import fmt_money
from reports import PERIODS, export_report_csv, parse_date, period_range
from ui_base import Panel
from ui_charts import ColumnChart, HBarChart


class ReportsPanel(Panel):
    def __init__(self, master, db, on_auth_error):
        super().__init__(master, db, on_auth_error)
        self.report = None
        self.label = ""
        self._build()
        self.load()

    def _build(self):
        head, right = theme.header(self, "Статистика и отчёты", "Выручка, блюда, кассиры; цены — из заказов")
        head.pack(fill="x")
        self.export_btn = ttk.Button(right, text="Экспорт в CSV", command=self.export, state="disabled")
        self.export_btn.pack(side="right")

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=theme.GAP)
        ttk.Label(bar, text="Период:").pack(side="left")
        self.period_box = ttk.Combobox(bar, values=PERIODS, state="readonly", width=12)
        self.period_box.set(PERIODS[0])
        self.period_box.pack(side="left", padx=8)
        self.period_box.bind("<<ComboboxSelected>>", lambda _e: self._period_changed())
        self.from_entry = ttk.Entry(bar, width=12)
        self.to_entry = ttk.Entry(bar, width=12)
        self.dash = ttk.Label(bar, text="—")
        self.show_btn = ttk.Button(bar, text="Показать", style="Primary.TButton", command=self.load)
        self.show_btn.pack(side="left", padx=8)
        self.busy_widgets += [self.show_btn, self.period_box]

        self.scroll = theme.ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        body = self.scroll.inner
        self.cards = ttk.Frame(body)
        self.cards.pack(fill="x")
        self.card_labels = {}
        specs = (("revenue", "Выручка"), ("orders", "Заказов"), ("avg", "Средний чек"),
                 ("service", "Надбавки 10%"), ("voided", "Аннулировано"))
        for col, (key, title) in enumerate(specs):
            self.cards.columnconfigure(col, weight=1, uniform="stat")
            box, value, hint = theme.stat_card(self.cards, title)
            box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
            self.card_labels[key] = (value, hint)

        self.items_chart = self._chart_card(body, "Самые продаваемые блюда (шт.)", HBarChart, lambda v: f"{v:g} шт.")
        self.cat_chart = self._chart_card(body, "Категории по выручке", HBarChart, lambda v: fmt_money(v))
        self.day_chart = self._chart_card(body, "Выручка по дням", ColumnChart, lambda v: f"{v / 1000:.1f}к")

        box = theme.card(body)
        box.pack(fill="x", pady=(theme.GAP, 0))
        ttk.Label(box, text="Продажи по кассирам", style="CardH2.TLabel").pack(anchor="w", pady=(0, 6))
        frame, self.cashier_tree = theme.make_table(
            box, [("name", "Кассир", 220, "w"), ("orders", "Заказов", 80, "center"),
                  ("revenue", "Выручка", 130, "e"), ("service", "Надбавка", 110, "e")], height=4)
        frame.pack(fill="x")
        self.build_status(self).pack(fill="x", pady=(8, 0))

    def _chart_card(self, master, title, chart_class, fmt):
        box = theme.card(master)
        box.pack(fill="x", pady=(theme.GAP, 0))
        ttk.Label(box, text=title, style="CardH2.TLabel").pack(anchor="w", pady=(0, 6))
        chart = chart_class(box, fmt=fmt)
        chart.pack(fill="x")
        return chart

    def _period_changed(self):
        custom = self.period_box.get() == "Свой период"
        for widget in (self.from_entry, self.dash, self.to_entry):
            widget.pack_forget()
        if custom:
            today = date.today().strftime("%d.%m.%Y")
            for entry in (self.from_entry, self.to_entry):
                if not entry.get():
                    entry.insert(0, today)
            self.from_entry.pack(side="left", before=self.show_btn)
            self.dash.pack(side="left", padx=4, before=self.show_btn)
            self.to_entry.pack(side="left", before=self.show_btn)
        else:
            self.load()

    def current_range(self):
        kind = self.period_box.get()
        if kind == "Свой период":
            start, end = parse_date(self.from_entry.get()), parse_date(self.to_entry.get())
            return period_range(kind, custom_from=start, custom_to=end)
        return period_range(kind)

    def load(self):
        if self.busy:
            return
        try:
            start, end = self.current_range()
        except AppError as e:
            self.show_status(str(e), "red")
            return
        last_day = end - timedelta(days=1)
        self.label = f"{start:%d.%m.%Y} — {last_day:%d.%m.%Y}"
        self.show_status("Загрузка отчёта...", "gray")
        self.run(lambda: self.db.load_report(start, end), self._loaded)

    def _loaded(self, report):
        self.report = report
        self.export_btn.config(state="normal")
        values = {"revenue": (fmt_money(report["revenue"]), f"подытог {fmt_money(report['subtotal_total'])}"),
                  "orders": (str(report["orders_count"]), self.label),
                  "avg": (fmt_money(report["average_check"]), "выручка / заказы"),
                  "service": (fmt_money(report["service_total"]), "входит в выручку"),
                  "voided": (str(report["voided_count"]), f"на {fmt_money(report['voided_total'])}")}
        for key, (value, hint) in values.items():
            self.card_labels[key][0].config(text=value)
            self.card_labels[key][1].config(text=hint)
        self.items_chart.set_rows([(r["name"], r["quantity"]) for r in report["top_items"]])
        self.cat_chart.set_rows([(r["category"], r["revenue"]) for r in report["categories"]])
        self.day_chart.set_rows([(r["day"][8:10] + "." + r["day"][5:7], r["revenue"]) for r in report["daily"]])
        self.cashier_tree.delete(*self.cashier_tree.get_children())
        for r in report["cashiers"]:
            self.cashier_tree.insert("", "end", values=(r["name"], r["orders"], fmt_money(r["revenue"]),
                                                        fmt_money(r["service"])))
        self.show_status("Отчёт обновлён." if report["orders_count"] else "За период заказов нет.", "gray")

    def export(self):
        if self.report is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile="otchet.csv",
                                            filetypes=[("CSV (Excel)", "*.csv")])
        if not path:
            return
        try:
            rows = export_report_csv(path, self.report, self.label)
        except OSError as e:
            self.show_status(f"Файл не сохранён: {e.strerror or 'ошибка записи'}.", "red")
            return
        self.show_status(f"Отчёт сохранён: {path} ({rows} строк).", "green")
