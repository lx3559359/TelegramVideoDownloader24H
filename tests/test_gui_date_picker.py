from datetime import date
import tkinter as tk

import pytest

from tg_video_downloader.gui.date_picker import (
    DatePicker,
    month_cells,
    shift_month,
)


@pytest.mark.parametrize(
    ("year", "month", "expected_days"),
    [
        (2025, 2, 28),
        (2024, 2, 29),
        (2026, 4, 30),
        (2026, 7, 31),
    ],
)
def test_month_cells_are_monday_first_and_always_six_weeks(
    year: int,
    month: int,
    expected_days: int,
) -> None:
    cells = month_cells(year, month)

    assert len(cells) == 42
    dates = tuple(value for value in cells if value is not None)
    assert len(dates) == expected_days
    assert dates[0] == date(year, month, 1)
    assert dates[-1] == date(year, month, expected_days)
    assert cells.index(dates[0]) == dates[0].weekday()


def test_shift_month_crosses_year_boundaries() -> None:
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 1, -1) == (2025, 12)


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.geometry("900x720")
    root.update_idletasks()
    try:
        yield root
    finally:
        root.update_idletasks()
        root.destroy()


def make_picker(root: tk.Tk) -> DatePicker:
    picker = DatePicker(
        root,
        label="开始日期",
        today=lambda: date(2026, 8, 26),
    )
    picker.pack()
    root.update_idletasks()
    return picker


def test_date_picker_defaults_to_unlimited_and_is_readonly(tk_root: tk.Tk) -> None:
    picker = make_picker(tk_root)

    assert picker.get() == ""
    assert picker.display_var.get() == "不限"
    assert str(picker.entry.cget("state")) == "readonly"
    assert picker.popup is None
    picker.destroy()


def test_date_picker_sets_and_clears_iso_value(tk_root: tk.Tk) -> None:
    picker = make_picker(tk_root)

    picker.set_date(date(2026, 8, 9))
    assert picker.get() == "2026-08-09"
    assert picker.display_var.get() == "2026-08-09"

    picker.clear()
    assert picker.get() == ""
    assert picker.display_var.get() == "不限"
    picker.destroy()


def test_date_picker_reuses_popup_navigates_and_selects(tk_root: tk.Tk) -> None:
    picker = make_picker(tk_root)
    picker.set_date(date(2026, 12, 15))

    picker.open_popup()
    popup = picker.popup
    picker.open_popup()
    assert picker.popup is popup

    picker.move_month(1)
    assert (picker.view_year, picker.view_month) == (2027, 1)
    picker.select_date(date(2027, 1, 3))
    assert picker.get() == "2027-01-03"
    assert picker.popup is None
    picker.destroy()


def test_date_picker_close_keeps_value_and_today_selects(tk_root: tk.Tk) -> None:
    picker = make_picker(tk_root)
    picker.set_date(date(2026, 8, 9))

    picker.open_popup()
    picker.close_popup()
    assert picker.get() == "2026-08-09"

    picker.open_popup()
    picker.select_today()
    assert picker.get() == "2026-08-26"
    assert picker.popup is None
    picker.destroy()


def test_date_picker_unlimited_action_and_destroy_close_popup(
    tk_root: tk.Tk,
) -> None:
    picker = make_picker(tk_root)
    picker.set_date(date(2026, 8, 9))
    picker.open_popup()
    picker.select_unlimited()
    assert picker.get() == ""
    assert picker.popup is None

    picker.open_popup()
    popup = picker.popup
    picker.destroy()
    assert popup is not None
    assert not bool(popup.winfo_exists())
