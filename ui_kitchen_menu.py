"""Вкладка «Меню» на экране кухни: наличие блюд.

Только отображение и фильтры. Данные и запросы к Supabase остаются в
KitchenFrame (ui_kitchen.py): панель получает блюда через set_items() и
сообщает о нажатии кнопки через on_toggle(item_id). Цены здесь не показываются.
"""

import tkinter as tk
from tkinter import ttk

import ui_theme as theme
from search import search

ALL = "Все"
AVAILABILITY = {"Все": None, "В продаже": True, "Нет в наличии": False}
BUTTON_WIDTH = 18  # в символах: «Снять с продажи» и «Вернуть в продажу» одной ширины
NAME_WRAP = 460    # перенос длинных названий, чтобы не вытеснить статус и кнопку
CARD_LIMIT = 100   # больше карточек не рисуется: при 500–1000 блюдах вкладка остаётся быстрой


class KitchenMenuPanel(ttk.Frame):
    def __init__(self, master, on_toggle):
        super().__init__(master, padding=(4, 8, 4, 0))
        self.on_toggle = on_toggle
        self.items = []     # [{id, name, category, is_available}] из kitchen_menu()
        self.buttons = {}   # id блюда -> кнопка «Снять с продажи» / «Вернуть в продажу»
        self.busy = False
        self.reflow_job = None
        self.render_job = None

        self.search_var = tk.StringVar()
        self.category_var = tk.StringVar(value=ALL)
        self.availability_var = tk.StringVar(value=ALL)
        # Ввод в поиск: несколько нажатий подряд — одна перерисовка; фильтры — сразу
        self.search_var.trace_add("write", lambda *_a: self._schedule_render())
        for var in (self.category_var, self.availability_var):
            var.trace_add("write", lambda *_a: self.render())

        self._build_header()
        self._build_filters()
        self.list = theme.ScrollFrame(self)
        self.list.pack(fill="both", expand=True, pady=(6, 0))
        # Перенос чипов считается от ширины панели (она зависит только от окна) и после
        # того, как Tk разложил виджеты: при изменении размера и при показе вкладки
        self.bind("<Configure>", lambda _e: self._schedule_reflow(), add="+")
        self.bind("<Map>", lambda _e: self._schedule_reflow(), add="+")

    # ---------- Разметка ----------

    def _build_header(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=4)
        titles = ttk.Frame(top)
        titles.pack(side="left", anchor="w")
        ttk.Label(titles, text="Управление меню", style="H2.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Управляйте наличием блюд", style="Muted.TLabel").pack(anchor="w")

        search = ttk.Frame(top)
        search.pack(side="right", anchor="e")
        ttk.Label(search, text="Поиск по коду или названию", style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(search)
        row.pack(anchor="w")
        self.search_entry = ttk.Entry(row, textvariable=self.search_var, width=26)
        self.search_entry.pack(side="left")
        ttk.Button(row, text="✕", width=2, command=lambda: self.search_var.set("")).pack(side="left", padx=(6, 0))

    def _build_filters(self):
        box = theme.card(self)
        box.configure(pady=8)
        box.pack(fill="x", padx=4, pady=(8, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Категория", style="CardMuted.TLabel").grid(row=0, column=0, sticky="nw", pady=6)
        # Кнопки категорий переносятся на новую строку, а не обрезаются (см. _reflow)
        self.category_chips = tk.Frame(box, bg=theme.SURFACE)
        self.category_chips.grid(row=0, column=1, sticky="ew", padx=(12, 0))

        ttk.Label(box, text="Наличие", style="CardMuted.TLabel").grid(row=1, column=0, sticky="nw", pady=(8, 2))
        availability = tk.Frame(box, bg=theme.SURFACE)
        availability.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(4, 0))
        for label in AVAILABILITY:
            self._chip(availability, label, self.availability_var).pack(side="left", padx=(0, 6), pady=2)
        self.reset_btn = ttk.Button(availability, text="Сбросить фильтры", command=self.reset_filters)
        self.reset_btn.pack(side="right")
        self.counts = ttk.Label(availability, text="", style="CardMuted.TLabel")
        self.counts.pack(side="right", padx=(12, 12))

    def _chip(self, master, label, variable):
        return ttk.Radiobutton(master, text=label, value=label, variable=variable, style="Chip.Toolbutton")

    def _schedule_reflow(self):
        if self.reflow_job is None:
            self.reflow_job = self.after_idle(self._run_reflow)

    def _run_reflow(self):
        self.reflow_job = None
        self._reflow(self.category_chips)

    def _reflow(self, frame):
        """Раскладывает чипы по строкам. place, а не grid: у grid ширина колонки
        общая для всех строк, и широкий чип во второй строке сдвигал бы первую."""
        # Доступно: от левого края блока чипов до правого края панели минус отступы карточки
        width = self.winfo_width() - (frame.winfo_rootx() - self.winfo_rootx()) - 24
        gap = 6
        x = y = row_height = 0
        for chip in frame.winfo_children():
            w, h = chip.winfo_reqwidth(), chip.winfo_reqheight()
            if x and x + w > width:
                x, y = 0, y + row_height + gap
                row_height = 0
            chip.place(x=x, y=y + 3)
            x += w + gap
            row_height = max(row_height, h)
        frame.configure(height=y + row_height + 6)

    # ---------- Данные ----------

    def set_items(self, items):
        """Новые данные с сервера. Фильтры и поиск сохраняются."""
        self.items = list(items)
        categories = sorted({item["category"] for item in self.items})
        if self.category_var.get() not in categories:
            self.category_var.set(ALL)
        for chip in self.category_chips.winfo_children():
            chip.destroy()
        for label in [ALL] + categories:
            self._chip(self.category_chips, label, self.category_var)
        self._reflow(self.category_chips)   # сразу — чтобы чипы были видны
        self._schedule_reflow()             # и уточнить, когда известна ширина
        self.render(keep_position=True)

    def filtered(self):
        """Блюда с учётом поиска (код или название), категории и наличия одновременно."""
        category = self.category_var.get()
        wanted = AVAILABILITY.get(self.availability_var.get())
        items = [item for item in sorted(self.items, key=lambda i: (i["category"], i["name"]))
                 if category in (ALL, item["category"]) and wanted in (None, item["is_available"])]
        return search(items, self.search_var.get())

    def reset_filters(self):
        self.search_var.set("")
        self.category_var.set(ALL)
        self.availability_var.set(ALL)

    def _schedule_render(self):
        if self.render_job is None:
            self.render_job = self.after_idle(self._run_render)

    def _run_render(self):
        self.render_job = None
        self.render()

    def destroy(self):
        for job in (self.reflow_job, self.render_job):
            if job is not None:
                self.after_cancel(job)
        self.reflow_job = self.render_job = None
        super().destroy()

    def set_busy(self, busy):
        self.busy = busy
        for btn in self.buttons.values():
            btn.config(state="disabled" if busy else "normal")

    # ---------- Список ----------

    def render(self, keep_position=False):
        if not hasattr(self, "list"):
            return  # trace сработал до создания списка
        self.list.clear(keep_position=keep_position)
        self.buttons.clear()
        items = self.filtered()
        on_sale = sum(1 for item in self.items if item["is_available"])
        self.counts.config(text=f"Показано {len(items)} из {len(self.items)}  ·  "
                                f"в продаже {on_sale}, нет {len(self.items) - on_sale}")
        filters_set = (self.search_var.get().strip() or self.category_var.get() != ALL
                       or self.availability_var.get() != ALL)
        self.reset_btn.config(state="normal" if filters_set else "disabled")

        if not items:
            self._empty(filters_set)
            return
        for item in items[:CARD_LIMIT]:
            self._card(item).pack(fill="x", padx=4, pady=4)
        if len(items) > CARD_LIMIT:
            ttk.Label(self.list.inner, text=f"Показаны первые {CARD_LIMIT} из {len(items)} — уточните поиск",
                      style="Muted.TLabel").pack(pady=8)

    def _empty(self, filters_set):
        box = tk.Frame(self.list.inner, bg=theme.BG)
        box.pack(fill="x", pady=48)
        if filters_set:
            ttk.Label(box, text="Блюда не найдены", style="H2.TLabel").pack()
            ttk.Label(box, text="Измените поиск или фильтры", style="Muted.TLabel").pack(pady=(4, 12))
            ttk.Button(box, text="Сбросить фильтры", command=self.reset_filters).pack()
        else:
            ttk.Label(box, text="В меню пока нет блюд", style="H2.TLabel").pack()

    def _card(self, item):
        available = item["is_available"]
        box = theme.card(self.list.inner)
        box.columnconfigure(0, weight=1)

        ttk.Label(box, text=item["name"], style="CardH2.TLabel", wraplength=NAME_WRAP,
                  foreground=theme.TEXT if available else theme.MUTED).grid(row=0, column=0, sticky="w")
        secondary = f"Код {item['code']}  ·  {item['category']}" if item.get("code") else item["category"]
        ttk.Label(box, text=secondary, style="CardMuted.TLabel").grid(row=1, column=0, sticky="w")

        ttk.Label(box, text="●  В продаже" if available else "●  Нет в наличии", style="CardBold.TLabel",
                  foreground=theme.SUCCESS if available else theme.DANGER).grid(
            row=0, column=1, rowspan=2, padx=(16, 16))

        btn = ttk.Button(box, width=BUTTON_WIDTH,
                         text="Снять с продажи" if available else "Вернуть в продажу",
                         style="Danger.TButton" if available else "Success.TButton",
                         command=lambda: self.on_toggle(item["id"]))
        btn.grid(row=0, column=2, rowspan=2, sticky="e")
        if self.busy:
            btn.config(state="disabled")
        self.buttons[item["id"]] = btn
        return box
