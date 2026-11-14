"""Административная оболочка: тёмная боковая панель и рабочая область с разделами.

Разделы строятся при открытии (данные всегда свежие); касса сохраняется между переключениями,
чтобы не потерять собранный заказ. Права на каждое действие проверяет сервер.
"""

from tkinter import messagebox, ttk

import ui_theme as theme
from ui_admin_panels import CategoriesPanel, DashboardPanel, MenuSectionPanel, SettingsPanel
from ui_order import OrderFrame
from ui_orders import OrdersPanel
from ui_reports import ReportsPanel
from ui_shift import ShiftPanel
from ui_staff import StaffPanel

SECTIONS = (("dashboard", "Главная"), ("cash", "Касса"), ("menu", "Управление меню"), ("categories", "Категории"),
            ("staff", "Сотрудники"), ("shifts", "Кассовые смены"), ("orders", "История заказов"),
            ("stats", "Статистика"), ("settings", "Настройки"))


class AdminShell(ttk.Frame):
    def __init__(self, master, db, profile, on_logout, server_host=""):
        super().__init__(master)
        self.db = db
        self.profile = profile
        self.on_logout = on_logout
        self.server_host = server_host
        self.nav = {}
        self.panel = None       # текущий раздел
        self.current = None
        self.cash = None        # экран кассы живёт всё время работы оболочки
        self.closed = False
        self._build()
        self.show("dashboard")

    def _build(self):
        side = ttk.Frame(self, style="Side.TFrame", width=230)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ttk.Label(side, text="Кафе Бистро", style="Side.TLabel").pack(anchor="w", padx=18, pady=(20, 0))
        ttk.Label(side, text="Панель администратора", style="SideMuted.TLabel").pack(anchor="w", padx=18, pady=(0, 18))
        for key, title in SECTIONS:
            btn = ttk.Button(side, text=title, style="Nav.TButton", command=lambda k=key: self.show(k))
            btn.pack(fill="x", pady=1)
            self.nav[key] = btn
        bottom = ttk.Frame(side, style="Side.TFrame")
        bottom.pack(side="bottom", fill="x", padx=14, pady=16)
        ttk.Label(bottom, text=self.profile["full_name"], style="SideMuted.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Button(bottom, text="Выйти", style="Logout.TButton", command=self.logout).pack(fill="x")

        self.content = ttk.Frame(self, padding=theme.PAD)
        self.content.pack(side="left", fill="both", expand=True)

    # ---------- Разделы ----------

    def _factory(self, key):
        auth = self.on_logout
        return {
            "dashboard": lambda: DashboardPanel(self.content, self.db, auth),
            "menu": lambda: MenuSectionPanel(self.content, self.db, auth, on_menu_changed=self._menu_changed),
            "categories": lambda: CategoriesPanel(self.content, self.db, auth, on_menu_changed=self._menu_changed),
            "staff": lambda: StaffPanel(self.content, self.db, auth, current_user_id=self.profile["id"]),
            "shifts": lambda: ShiftPanel(self.content, self.db, auth, show_cashier=True),
            "orders": lambda: OrdersPanel(self.content, self.db, auth),
            "stats": lambda: ReportsPanel(self.content, self.db, auth),
            "settings": lambda: SettingsPanel(self.content, self.db, auth, self.profile, self.server_host),
        }[key]

    def show(self, key):
        if key == self.current:
            if key != "cash" and self.panel is not None:
                reload = getattr(self.panel, "reload", None)
                if reload:
                    reload()
            return
        if self.panel is not None:
            if self.panel is self.cash:
                self.panel.pack_forget()
            else:
                self.panel.destroy()
            self.panel = None
        if key == "cash":
            if self.cash is None or not self.cash.winfo_exists():
                self.cash = OrderFrame(self.content, self.db, self.profile, on_logout=self.on_logout, embedded=True)
            else:
                self.cash.reload_menu()
            self.panel = self.cash
        else:
            self.panel = self._factory(key)()
        self.panel.pack(fill="both", expand=True)
        self.current = key
        for name, btn in self.nav.items():
            btn.config(style="NavActive.TButton" if name == key else "Nav.TButton")

    def _menu_changed(self):
        """Меню изменили в редакторе или переименовали категорию: касса перечитает его при открытии."""

    # ---------- Выход ----------

    def logout(self):
        cash = self.cash if self.cash is not None and self.cash.winfo_exists() else None
        if cash is not None and cash.busy:
            return
        if cash is not None and cash.cart and not messagebox.askyesno(
                "Выход", "Заказ в кассе не оформлен. Всё равно выйти?", parent=self):
            return
        self.on_logout()

    def destroy(self):
        self.closed = True
        super().destroy()
