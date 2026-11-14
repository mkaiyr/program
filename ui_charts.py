"""Простые графики на Canvas (без внешних библиотек): горизонтальные и столбчатые диаграммы."""

import tkinter as tk

import ui_theme as theme

PALETTE = (theme.ACCENT, "#0EA5E9", theme.SUCCESS, theme.WARNING, "#8B5CF6", "#EC4899", "#14B8A6", "#64748B")


class Chart(tk.Canvas):
    """Холст, перерисовывающийся при изменении размера. Данные — rows: [(подпись, число Decimal/int)]."""

    def __init__(self, master, height=220, fmt=str):
        super().__init__(master, height=height, bg=theme.SURFACE, highlightthickness=0)
        self.rows = []
        self.fmt = fmt
        self.bind("<Configure>", lambda _e: self.redraw())

    def set_rows(self, rows):
        self.rows = [(str(label), float(value)) for label, value in rows]
        self.redraw()

    def _empty(self):
        self.create_text(self.winfo_width() / 2, self.winfo_height() / 2, text="Нет данных за период",
                         fill=theme.MUTED, font=theme.FONT_BODY)

    def redraw(self):
        self.delete("all")


class HBarChart(Chart):
    """Горизонтальные полосы: подпись слева, значение справа от полосы."""

    def redraw(self):
        super().redraw()
        if not self.rows:
            return self._empty()
        width, height = max(self.winfo_width(), 300), max(self.winfo_height(), 120)
        top = max(value for _l, value in self.rows) or 1
        row_h = min(34, (height - 8) / len(self.rows))
        label_w = min(200, width * 0.35)
        value_w = 110
        for index, (label, value) in enumerate(self.rows):
            y = 4 + index * row_h
            text = label if len(label) <= 24 else label[:23] + "…"
            self.create_text(label_w - 8, y + row_h / 2, text=text, anchor="e", fill=theme.TEXT, font=theme.FONT_SMALL)
            bar_w = max(2, (width - label_w - value_w) * value / top) if value > 0 else 0
            self.create_rectangle(label_w, y + 5, label_w + bar_w, y + row_h - 5, fill=PALETTE[index % len(PALETTE)],
                                  outline="")
            self.create_text(label_w + bar_w + 8, y + row_h / 2, text=self.fmt(value), anchor="w",
                             fill=theme.MUTED, font=theme.FONT_SMALL)


class ColumnChart(Chart):
    """Столбцы по дням (подпись снизу, значение над столбцом, если хватает места)."""

    def redraw(self):
        super().redraw()
        if not self.rows:
            return self._empty()
        width, height = max(self.winfo_width(), 300), max(self.winfo_height(), 140)
        top = max(value for _l, value in self.rows) or 1
        n = len(self.rows)
        slot = (width - 20) / n
        bar_w = max(4, min(60, slot * 0.6))
        base = height - 24
        for index, (label, value) in enumerate(self.rows):
            x = 10 + index * slot + (slot - bar_w) / 2
            h = (base - 22) * value / top if value > 0 else 0
            self.create_rectangle(x, base - h, x + bar_w, base, fill=theme.ACCENT, outline="")
            if slot >= 60:
                self.create_text(x + bar_w / 2, base - h - 8, text=self.fmt(value), fill=theme.MUTED,
                                 font=theme.FONT_SMALL)
            if slot >= 34 or index % max(1, int(34 / slot) + 1) == 0:
                self.create_text(x + bar_w / 2, base + 12, text=label, fill=theme.MUTED, font=theme.FONT_SMALL)
