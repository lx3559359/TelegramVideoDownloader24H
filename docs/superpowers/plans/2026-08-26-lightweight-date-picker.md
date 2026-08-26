# Lightweight Video Search Date Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual start/end date text entry on the video search page with dependency-free, read-only popup calendars that support an unlimited state.

**Architecture:** Add a focused `DatePicker` ttk component backed by pure month-grid helpers. The component stores `date | None`, renders a transient on-demand `Toplevel`, and exposes the existing empty-string/ISO-string boundary so controller and Telegram search logic remain unchanged. Integrate two instances into `VideoSearchPage` and prepare a v0.3.2 release candidate without adding dependencies or idle work.

**Tech Stack:** Python 3.12 standard library, Tkinter/ttk, pytest, PowerShell, Git

---

### Task 1: Create an isolated verified workspace

**Files:**
- Use: `.worktrees/lightweight-date-picker-v032/`
- Verify: `scripts/bootstrap.ps1`
- Verify: `scripts/check.ps1`

- [ ] **Step 1: Verify the main checkout and worktree location**

Run:

```powershell
git status --short --branch
git check-ignore .worktrees
git worktree list --porcelain
```

Expected: clean `master`, ignored `.worktrees`, and no existing `lightweight-date-picker-v032` worktree.

- [ ] **Step 2: Create the feature worktree**

Run:

```powershell
git worktree add '.worktrees/lightweight-date-picker-v032' -b 'codex/lightweight-date-picker-v032'
```

Expected: a new worktree at the approved design commit.

- [ ] **Step 3: Install dependencies in the worktree-local environment**

Run from `.worktrees/lightweight-date-picker-v032`:

```powershell
& .\scripts\bootstrap.ps1
```

Expected: editable version 0.3.1 installs with the existing dependency set; no calendar package is installed.

- [ ] **Step 4: Verify the baseline**

Run:

```powershell
& .\scripts\check.ps1
```

Expected: 365 tests pass and compileall exits 0.

### Task 2: Build the pure calendar model with TDD

**Files:**
- Create: `src/tg_video_downloader/gui/date_picker.py`
- Create: `tests/test_gui_date_picker.py`

- [ ] **Step 1: Write failing month-grid and navigation tests**

Create `tests/test_gui_date_picker.py` with:

```python
import tkinter as tk
from datetime import date

import pytest

from tg_video_downloader.gui.date_picker import (
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
def test_month_cells_are_monday_aligned_and_fixed_size(
    year: int,
    month: int,
    expected_days: int,
) -> None:
    cells = month_cells(year, month)
    values = tuple(value for value in cells if value is not None)

    assert len(cells) == 42
    assert values == tuple(date(year, month, day) for day in range(1, expected_days + 1))
    assert cells.index(date(year, month, 1)) == date(year, month, 1).weekday()


def test_shift_month_crosses_year_boundaries() -> None:
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 1, -1) == (2025, 12)
```

- [ ] **Step 2: Run the pure tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_date_picker.py
```

Expected: collection fails because `tg_video_downloader.gui.date_picker` does not exist.

- [ ] **Step 3: Implement the pure helpers**

Create `src/tg_video_downloader/gui/date_picker.py` with this initial content:

```python
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
```

- [ ] **Step 4: Run the pure tests and verify GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_date_picker.py
```

Expected: 5 parameterized test cases pass.

### Task 3: Build the on-demand DatePicker widget with TDD

**Files:**
- Modify: `src/tg_video_downloader/gui/date_picker.py`
- Modify: `tests/test_gui_date_picker.py`

- [ ] **Step 1: Add failing widget behavior tests**

Add `DatePicker` to the existing import from `tg_video_downloader.gui.date_picker`, then append to `tests/test_gui_date_picker.py`:

```python
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
```

- [ ] **Step 2: Run widget tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_date_picker.py
```

Expected: FAIL because `DatePicker` and its behavior are not implemented.

- [ ] **Step 3: Implement the DatePicker component**

Append this class to `src/tg_video_downloader/gui/date_picker.py`:

```python
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
            row=0, column=0
        )
        ttk.Label(
            header,
            text=f"{self.view_year}年{self.view_month}月",
            anchor="center",
        ).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(header, text="今天", command=self.select_today).grid(
            row=0, column=2, padx=(0, 6)
        )
        ttk.Button(header, text="下月", command=lambda: self.move_month(1)).grid(
            row=0, column=3
        )

        calendar = ttk.Frame(popup, padding=(8, 4))
        calendar.grid(row=1, column=0)
        for column, label in enumerate(WEEKDAY_LABELS):
            ttk.Label(calendar, text=label, anchor="center", width=4).grid(
                row=0, column=column
            )
        for index, value in enumerate(month_cells(self.view_year, self.view_month)):
            row, column = divmod(index, 7)
            if value is None:
                ttk.Label(calendar, text="", width=4).grid(row=row + 1, column=column)
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
            side="left"
        )
        ttk.Button(footer, text="关闭", command=self.close_popup).pack(side="right")

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
```

- [ ] **Step 4: Run widget tests and verify GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_date_picker.py
```

Expected: all 10 calendar and widget cases pass with no Tcl errors.

- [ ] **Step 5: Commit the standalone component**

Run:

```powershell
git add src/tg_video_downloader/gui/date_picker.py tests/test_gui_date_picker.py
git commit -m "feat: add lightweight popup date picker"
```

### Task 4: Integrate date pickers into video search

**Files:**
- Modify: `src/tg_video_downloader/gui/search_page.py`
- Modify: `tests/test_gui_search_page.py`

- [ ] **Step 1: Update search-page tests first**

Add `date` to the datetime import in `tests/test_gui_search_page.py`, then extend `test_search_page_builds_lightweight_controls_and_no_timer_until_search` with:

```python
assert page.start_date_picker.get() == ""
assert page.end_date_picker.get() == ""
assert page.start_date_picker.display_var.get() == "不限"
assert page.end_date_picker.display_var.get() == "不限"
assert str(page.start_date_picker.entry.cget("state")) == "readonly"
assert str(page.end_date_picker.entry.cget("state")) == "readonly"
assert page.start_date_picker.popup is None
assert page.end_date_picker.popup is None
```

Add this test after the lightweight-controls test:

```python
def test_search_page_passes_selected_iso_dates_to_controller(tk_root: tk.Tk) -> None:
    notebook = ttk.Notebook(tk_root)
    controller = FakeSearchController()
    page = VideoSearchPage(
        notebook,
        controller=controller,
        bridge=FakeSearchBridge(),
        show_error=lambda _error: None,
    )
    install_fake_scheduler(page)
    page.start_date_picker.set_date(date(2026, 8, 1))
    page.end_date_picker.set_date(date(2026, 8, 26))

    page.start_search()

    assert controller.search_calls == [
        (-1001, "", "2026-08-01", "2026-08-26", 100)
    ]
    page.close()
```

- [ ] **Step 2: Run the page tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_search_page.py
```

Expected: FAIL because `VideoSearchPage` has no date-picker attributes.

- [ ] **Step 3: Replace manual Entry controls**

In `src/tg_video_downloader/gui/search_page.py`:

```python
from tg_video_downloader.gui.date_picker import DatePicker
```

Remove creation of `start_date_var` and `end_date_var`. Replace the two date `ttk.Entry` blocks with:

```python
self.start_date_picker = DatePicker(toolbar, label="开始日期")
self.start_date_picker.grid(
    row=1,
    column=1,
    sticky="ew",
    padx=(0, 10),
    pady=(8, 0),
)

self.end_date_picker = DatePicker(toolbar, label="结束日期")
self.end_date_picker.grid(
    row=1,
    column=3,
    sticky="ew",
    padx=(0, 10),
    pady=(8, 0),
)
```

Change the controller call to:

```python
operation = self.controller.search_videos(
    group.chat_id,
    self.keyword_var.get(),
    self.start_date_picker.get(),
    self.end_date_picker.get(),
    int(self.limit_var.get()),
)
```

At the start of `close()`, add:

```python
self.start_date_picker.close_popup()
self.end_date_picker.close_popup()
```

- [ ] **Step 4: Run page and date-picker tests and verify GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_date_picker.py tests/test_gui_search_page.py tests/test_selective.py
```

Expected: all date, page, open-bound and reverse-range tests pass.

- [ ] **Step 5: Commit the page integration**

Run:

```powershell
git add src/tg_video_downloader/gui/search_page.py tests/test_gui_search_page.py
git commit -m "feat: select video search dates from calendars"
```

### Task 5: Prepare v0.3.2 release metadata

**Files:**
- Modify: `tests/test_release_metadata.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/verification.md`

- [ ] **Step 1: Change release metadata expectations first**

Rename the release test to `test_v032_docs_explain_release_boundaries`, change the version assertion to:

```python
assert pyproject["project"]["version"] == "0.3.2"
```

Keep all existing assertions and add:

```python
assert "弹出月历" in readme
assert "不限" in readme
assert "不增加第三方日历依赖" in readme
```

- [ ] **Step 2: Run the release test and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_release_metadata.py
```

Expected: FAIL because the package is still 0.3.1 and README lacks the date-picker explanation.

- [ ] **Step 3: Bump version and document the interaction**

Set:

```toml
version = "0.3.2"
```

Add this paragraph under README’s “选择性下载” section:

```markdown
开始日期和结束日期使用内置弹出月历，默认均为“不限”；可选择上月、下月、今天或清除日期。日期框为只读，不需要手动输入格式，也不增加第三方日历依赖、常驻线程或后台轮询。
```

- [ ] **Step 4: Run release metadata test and reinstall candidate**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_release_metadata.py
& .\scripts\bootstrap.ps1
```

Expected: release metadata passes and editable version 0.3.2 installs without a new dependency.

- [ ] **Step 5: Commit release metadata**

Run:

```powershell
git add pyproject.toml README.md tests/test_release_metadata.py
git commit -m "docs: prepare the v0.3.2 date picker release"
```

### Task 6: Verify layout, lifecycle, and release candidate

**Files:**
- Review: `src/tg_video_downloader/gui/date_picker.py`
- Review: `src/tg_video_downloader/gui/search_page.py`
- Review: `tests/test_gui_date_picker.py`
- Review: `tests/test_gui_search_page.py`
- Modify: `docs/verification.md`

- [ ] **Step 1: Run scoped tests and inspect the complete diff**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_date_picker.py tests/test_gui_search_page.py tests/test_selective.py tests/test_gui_app.py
git diff --check master...HEAD
git diff --stat master...HEAD
```

Expected: all scoped tests pass; changes are limited to the picker, search integration, tests and release metadata.

- [ ] **Step 2: Perform a 900×720 Tk acceptance check**

Add this test to `tests/test_gui_search_page.py`:

```python
def test_search_page_date_pickers_fit_900x720(tk_root: tk.Tk) -> None:
    tk_root.deiconify()
    tk_root.geometry("900x720")
    notebook = ttk.Notebook(tk_root)
    notebook.pack(fill="both", expand=True)
    page = VideoSearchPage(
        notebook,
        controller=FakeSearchController(),
        bridge=FakeSearchBridge(),
        show_error=lambda _error: None,
    )
    try:
        tk_root.update_idletasks()
        assert page.start_date_picker.winfo_viewable()
        assert page.end_date_picker.winfo_viewable()
        assert page.search_button.winfo_viewable()
        assert page.result_tree.winfo_viewable()
        assert page.enqueue_button.winfo_viewable()
        assert page.start_date_picker.popup is None
        assert page.end_date_picker.popup is None

        page.start_date_picker.open_popup()
        assert page.start_date_picker.popup is not None
        page.start_date_picker.close_popup()
        assert page.start_date_picker.popup is None
    finally:
        page.close()
        notebook.destroy()
        tk_root.withdraw()
```

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_search_page.py::test_search_page_date_pickers_fit_900x720
```

Expected: PASS with all controls viewable and no popup left behind.

- [ ] **Step 3: Run dependency and complete project checks**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
```

Expected: no broken requirements, all tests pass, and compileall exits 0.

- [ ] **Step 4: Verify no idle work or dependency growth**

Run:

```powershell
rg -n "tkcalendar|after\(|Thread|Future" src/tg_video_downloader/gui/date_picker.py pyproject.toml
& .\.venv\Scripts\python.exe -m pip show telegram-video-downloader
```

Expected: no `tkcalendar`, thread, Future or `after()` use in the picker; package dependencies are unchanged.

- [ ] **Step 5: Verify the real background remains uninterrupted**

Using the worktree Python against `D:\Codex Project\Telegram自动化脚本`, read the downloader/supervisor locks and heartbeat twice eight seconds apart.

Expected: both locks report running, heartbeat advances, and no service stop or GUI restart occurs.

- [ ] **Step 6: Record measured evidence**

Append this section to `docs/verification.md`:

```markdown
## v0.3.2 轻量日期选择器证据（2026-08-26）

- 日期交互：开始和结束日期均为只读控件，默认“不限”；弹出月历支持上月、下月、今天、清除和关闭，产生的值仅为空字符串或 ISO 日期。
- 日历正确性：纯函数覆盖周一对齐、闰年和 28/29/30/31 天月份，以及跨年导航；开始晚于结束仍由现有范围校验拒绝。
- 轻量边界：没有新增第三方依赖、线程、Future、永久 `after()`、后台进程或网络请求；弹窗按需创建并在关闭后销毁。
- 900×720 验收：两个日期选择器、检索按钮、结果表和下载按钮均可见，选择、今天、不限和重复打开行为正常。
- 发布候选：完整自动化测试通过，`python -m pip check` 无损坏依赖，源码编译检查通过。
- 实机只读核验：原后台下载器与监督器持续运行，心跳推进；验收没有停止服务、重启真实 GUI 或修改真实下载数据。
```

- [ ] **Step 7: Commit verification evidence**

Run:

```powershell
git add docs/verification.md
git commit -m "docs: verify the v0.3.2 date picker"
```

- [ ] **Step 8: Run the final clean release-candidate gate**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
git diff --check master...HEAD
git status --short --branch
git log --oneline master..HEAD
```

Expected: dependencies and all tests pass, branch is clean, and commits contain only the approved v0.3.2 date-picker work.
