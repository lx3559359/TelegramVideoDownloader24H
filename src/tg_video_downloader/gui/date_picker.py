from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from datetime import date
import tkinter as tk
from tkinter import ttk


CALENDAR_CELL_COUNT = 42
WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")


def month_cells(year: int, month: int) -> tuple[date | None, ...]:
    first = date(year, month, 1)
    day_count = monthrange(year, month)[1]
    values: list[date | None] = [None] * first.weekday()
    values.extend(date(year, month, day) for day in range(1, day_count + 1))
    values.extend([None] * (CALENDAR_CELL_COUNT - len(values)))
    return tuple(values)


def shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    shifted_year, shifted_month = divmod(absolute, 12)
    if not 1 <= shifted_year <= 9999:
        raise ValueError("日期超出支持范围")
    return shifted_year, shifted_month + 1


class DatePicker(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        label: str,
        today: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(master)
        self.label = label
        self._today = today
        self._value: date | None = None
        self.display_var = tk.StringVar(self, value="不限")
        self.popup: tk.Toplevel | None = None
        self.view_year = today().year
        self.view_month = today().month

        self.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(
            self,
            textvariable=self.display_var,
            state="readonly",
            width=12,
        )
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Button-1>", self._open_from_event)
        self.button = ttk.Button(self, text="选择", command=self.open_popup)
        self.button.grid(row=0, column=1, padx=(6, 0))

    def get(self) -> str:
        return "" if self._value is None else self._value.isoformat()

    def set_date(self, value: date | None) -> None:
        self._value = value
        self.display_var.set("不限" if value is None else value.isoformat())

    def clear(self) -> None:
        self.set_date(None)

    def _open_from_event(self, _event: tk.Event[tk.Misc]) -> str:
        self.open_popup()
        return "break"

    def open_popup(self) -> None:
        if self.popup is not None and bool(self.popup.winfo_exists()):
            self.popup.lift()
            self.popup.focus_set()
            return

        anchor = self._value or self._today()
        self.view_year, self.view_month = anchor.year, anchor.month
        popup = tk.Toplevel(self)
        self.popup = popup
        popup.title(self.label)
        popup.transient(self.winfo_toplevel())
        popup.resizable(False, False)
        popup.protocol("WM_DELETE_WINDOW", self.close_popup)
        popup.bind("<Escape>", lambda _event: self.close_popup())
        self._render_popup()
        popup.update_idletasks()
        popup.geometry(
            f"+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height()}"
        )
        popup.grab_set()

    def _render_popup(self) -> None:
        popup = self.popup
        if popup is None:
            return
        for child in popup.winfo_children():
            child.destroy()

        header = ttk.Frame(popup, padding=(8, 8, 8, 4))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Button(header, text="上月", command=lambda: self.move_month(-1)).grid(
            row=0,
            column=0,
        )
        ttk.Label(
            header,
            text=f"{self.view_year}年{self.view_month}月",
            anchor="center",
        ).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(header, text="今天", command=self.select_today).grid(
            row=0,
            column=2,
            padx=(0, 6),
        )
        ttk.Button(header, text="下月", command=lambda: self.move_month(1)).grid(
            row=0,
            column=3,
        )

        calendar = ttk.Frame(popup, padding=(8, 4))
        calendar.grid(row=1, column=0)
        for column, label in enumerate(WEEKDAY_LABELS):
            ttk.Label(calendar, text=label, anchor="center", width=4).grid(
                row=0,
                column=column,
            )
        for index, value in enumerate(month_cells(self.view_year, self.view_month)):
            row, column = divmod(index, 7)
            if value is None:
                ttk.Label(calendar, text="", width=4).grid(
                    row=row + 1,
                    column=column,
                )
            else:
                ttk.Button(
                    calendar,
                    text=str(value.day),
                    width=3,
                    command=lambda selected=value: self.select_date(selected),
                ).grid(row=row + 1, column=column, padx=1, pady=1)

        footer = ttk.Frame(popup, padding=(8, 4, 8, 8))
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Button(footer, text="不限", command=self.select_unlimited).pack(
            side="left",
        )
        ttk.Button(footer, text="关闭", command=self.close_popup).pack(
            side="right",
        )

    def move_month(self, offset: int) -> None:
        self.view_year, self.view_month = shift_month(
            self.view_year,
            self.view_month,
            offset,
        )
        self._render_popup()

    def select_date(self, value: date) -> None:
        self.set_date(value)
        self.close_popup()

    def select_today(self) -> None:
        self.select_date(self._today())

    def select_unlimited(self) -> None:
        self.clear()
        self.close_popup()

    def close_popup(self) -> None:
        popup, self.popup = self.popup, None
        if popup is None:
            return
        try:
            if popup.grab_current() is popup:
                popup.grab_release()
        except tk.TclError:
            pass
        try:
            popup.destroy()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        self.close_popup()
        super().destroy()
