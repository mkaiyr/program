"""Единый стиль приложения: цвета, шрифты, стили ttk и общие виджеты.

Вызывается один раз: apply(root) в main.py. Остальные модули используют
константы цветов и стили по имени ("Primary.TButton", "Card.TLabel" и т. д.).
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---------- Цвета ----------
BG = "#F3F4F6"        # фон окна
SURFACE = "#FFFFFF"   # карточки, таблицы
BORDER = "#E5E7EB"
TEXT = "#111827"
MUTED = "#6B7280"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
ACCENT_LIGHT = "#DBEAFE"
SUCCESS = "#16A34A"
SUCCESS_DARK = "#15803D"
WARNING = "#D97706"
DANGER = "#DC2626"
DANGER_DARK = "#B91C1C"
DISABLED_BG = "#E5E7EB"
DISABLED_FG = "#9CA3AF"

# Цвета статусов заказа (бейджи, текст в таблицах)
STATUS_COLORS = {"accepted": ACCENT, "preparing": WARNING, "ready": SUCCESS, "completed": MUTED}

# Цвета строки состояния: в коде экранов используются простые имена
MESSAGE_COLORS = {"gray": MUTED, "green": SUCCESS, "red": DANGER, "orange": WARNING, "black": TEXT}


def message_color(name):
    return MESSAGE_COLORS.get(name, name)


# ---------- Шрифты ----------
FONT_H1 = ("", 22, "bold")
FONT_H2 = ("", 16, "bold")
FONT_BODY = ("", 13)
FONT_BOLD = ("", 13, "bold")
FONT_BIG = ("", 16, "bold")
FONT_TOTAL = ("", 20, "bold")


def apply(root):
    """Настраивает тему и стили. Повторный вызов безопасен."""
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        tkfont.nametofont(name).configure(size=13)
    tkfont.nametofont("TkHeadingFont").configure(size=12, weight="bold")

    root.configure(bg=BG)
    root.option_add("*Toplevel.background", BG)

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER,
                    focuscolor=ACCENT_LIGHT, lightcolor=SURFACE, darkcolor=BORDER)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)

    # Подписи
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("H1.TLabel", font=FONT_H1)
    style.configure("H2.TLabel", font=FONT_H2)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Status.TLabel", foreground=MUTED)
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("CardH2.TLabel", background=SURFACE, font=FONT_H2)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("CardBold.TLabel", background=SURFACE, font=FONT_BOLD)
    style.configure("Total.TLabel", background=SURFACE, font=FONT_TOTAL)
    for status, color in STATUS_COLORS.items():
        style.configure(f"Badge.{status}.TLabel", background=color, foreground="white",
                        font=FONT_BOLD, padding=(10, 3))
    style.configure("Badge.on.TLabel", background=SUCCESS, foreground="white", font=FONT_BOLD, padding=(10, 3))
    style.configure("Badge.off.TLabel", background=DANGER, foreground="white", font=FONT_BOLD, padding=(10, 3))

    # Кнопки: обычная, основная, «успех», «опасно», крупные варианты
    style.configure("TButton", padding=(14, 8), background=SURFACE, foreground=TEXT, relief="flat")
    style.map("TButton", background=[("disabled", DISABLED_BG), ("pressed", BORDER), ("active", "#F9FAFB")],
              foreground=[("disabled", DISABLED_FG)])
    for name, color, dark in (("Primary", ACCENT, ACCENT_DARK), ("Success", SUCCESS, SUCCESS_DARK),
                              ("Danger", DANGER, DANGER_DARK)):
        style.configure(f"{name}.TButton", background=color, foreground="white", bordercolor=color,
                        font=FONT_BOLD)
        style.map(f"{name}.TButton", background=[("disabled", DISABLED_BG), ("pressed", dark), ("active", dark)],
                  foreground=[("disabled", DISABLED_FG)], bordercolor=[("disabled", DISABLED_BG)])
        style.configure(f"Big.{name}.TButton", font=FONT_BIG, padding=(18, 14))
    style.configure("Big.TButton", font=FONT_BIG, padding=(18, 14))
    # Клавиша экранной клавиатуры на входе: крупный шрифт, умеренная высота
    style.configure("Key.TButton", font=FONT_BIG, padding=(10, 9))
    # Плитка блюда в каталоге кассы
    style.configure("Tile.TButton", font=FONT_BOLD, padding=(10, 16), justify="center", wraplength=150)
    style.map("Tile.TButton", background=[("pressed", ACCENT_LIGHT), ("active", "#EFF6FF")])
    # Фильтр категорий
    style.configure("Chip.Toolbutton", padding=(14, 6), background=SURFACE, font=FONT_BODY)
    style.map("Chip.Toolbutton", background=[("selected", ACCENT), ("active", ACCENT_LIGHT)],
              foreground=[("selected", "white")])

    # Поля ввода
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(widget, padding=6, fieldbackground=SURFACE, bordercolor=BORDER)
        style.map(widget, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])

    # Таблицы
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                    rowheight=32, bordercolor=BORDER)
    style.map("Treeview", background=[("selected", ACCENT_LIGHT)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=BG, foreground=MUTED, relief="flat", padding=(8, 6))
    style.map("Treeview.Heading", background=[("active", BORDER)])

    # Вкладки
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(20, 10), font=FONT_BOLD, background=BG, foreground=MUTED)
    style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", TEXT)])

    style.configure("TLabelframe", background=BG, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED, font=FONT_BOLD)


def card(master):
    """Белая карточка с тонкой рамкой."""
    return tk.Frame(master, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, padx=14, pady=12)


def header(master, title, subtitle=""):
    """Шапка экрана: заголовок слева, подзаголовок под ним, место для кнопок справа.

    Возвращает (frame, right) — в right добавляются кнопки (pack side="right").
    """
    frame = ttk.Frame(master)
    left = ttk.Frame(frame)
    left.pack(side="left")
    ttk.Label(left, text=title, style="H1.TLabel").pack(anchor="w")
    if subtitle:
        ttk.Label(left, text=subtitle, style="Muted.TLabel").pack(anchor="w")
    right = ttk.Frame(frame)
    right.pack(side="right")
    return frame, right


class ScrollFrame(ttk.Frame):
    """Прокручиваемая область: виджеты добавляются в self.inner."""

    def __init__(self, master, bg=BG):
        super().__init__(master)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        # Колесо мыши работает, пока курсор над областью
        self.bind("<Enter>", lambda _e: self.bind_all("<MouseWheel>", self._on_wheel))
        self.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event):
        if self.tk.call("tk", "windowingsystem") == "aqua":
            step = -event.delta
        else:
            step = -event.delta // 120
        self.canvas.yview_scroll(step, "units")

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)
