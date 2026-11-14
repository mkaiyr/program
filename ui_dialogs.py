"""Небольшие модальные диалоги: сумма с комментарием, текст (причина), пароль."""

import tkinter as tk
from tkinter import ttk

import ui_theme as theme


class FormDialog(tk.Toplevel):
    """Диалог из подписанных полей. fields: [(ключ, подпись, значение по умолчанию, скрытый ввод[, варианты])].

    on_submit(values: dict) вызывается по «ОК»; если вернул строку — это ошибка, диалог остаётся открытым.
    """

    def __init__(self, master, title, fields, on_submit, ok_text="ОК", hint="", ok_style="Primary.TButton"):
        super().__init__(master)
        self.on_submit = on_submit
        self.entries = {}
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        body = ttk.Frame(self, padding=theme.PAD)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=title, style="H2.TLabel").pack(anchor="w")
        if hint:
            ttk.Label(body, text=hint, style="Muted.TLabel", wraplength=380, justify="left").pack(anchor="w", pady=(2, 0))
        form = ttk.Frame(body)
        form.pack(fill="x", pady=theme.GAP)
        form.columnconfigure(1, weight=1)
        for row, field in enumerate(fields):
            key, label, default, secret = field[:4]
            choices = field[4] if len(field) > 4 else None  # список вариантов — выпадающий список
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            if choices:
                entry = ttk.Combobox(form, values=list(choices), state="readonly", width=28, font=theme.FONT_BODY)
                entry.set(default if default in choices else choices[0])
            else:
                entry = ttk.Entry(form, width=30, show="•" if secret else "", font=theme.FONT_BODY)
                entry.insert(0, default or "")
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            entry.bind("<Return>", lambda _e: self.submit())
            self.entries[key] = entry
        self.error = ttk.Label(body, text="", foreground=theme.DANGER, wraplength=380, justify="left")
        self.error.pack(fill="x")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(theme.GAP, 0))
        self.ok_btn = ttk.Button(buttons, text=ok_text, style=ok_style, command=self.submit)
        self.ok_btn.pack(side="right")
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Escape>", lambda _e: self.destroy())
        first = next(iter(self.entries.values()))
        self.after(50, first.focus_set)
        self.grab_set()

    def values(self):
        return {key: entry.get() for key, entry in self.entries.items()}

    def submit(self):
        problem = self.on_submit(self.values())
        if problem:
            self.error.config(text=problem)
        else:
            self.destroy()
