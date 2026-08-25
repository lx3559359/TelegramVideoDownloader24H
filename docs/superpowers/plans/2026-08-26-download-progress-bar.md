# Download Progress Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a low-overhead visual progress bar for the current file on the configurator run page.

**Architecture:** Keep heartbeat production and the existing two-second GUI refresh unchanged. Add one pure presentation function that converts untrusted status snapshots into a bounded value and label, then bind its output to a determinate ttk progress bar.

**Tech Stack:** Python 3.11+, Tkinter/ttk, pytest.

---

## File map

- Modify `src/tg_video_downloader/gui/app.py`: pure progress presentation and ttk widgets.
- Modify `tests/test_gui_app.py`: malformed data, status states, and refresh integration.
- Modify `README.md` and `docs/verification.md`: behavior and Windows evidence.

### Task 1: Pure progress-bar presentation

**Files:**
- Modify: `src/tg_video_downloader/gui/app.py`
- Modify: `tests/test_gui_app.py`

- [ ] **Step 1: Write failing pure-function tests**

```python
@pytest.mark.parametrize(
    ("snapshot", "value", "label"),
    [
        ({"status": "running", "progress": {"percent": 0}}, 0.0, "0.0%"),
        ({"status": "running", "progress": {"percent": 52.34}}, 52.34, "52.3%"),
        ({"status": "running", "progress": {"percent": 120}}, 100.0, "100.0%"),
        ({"status": "running", "current_file": "x.mp4", "progress": {}}, 0.0, "正在准备"),
        ({"status": "running"}, 0.0, "等待任务"),
        ({"status": "stopped"}, 0.0, "后台已停止"),
        ({"status": "stale", "progress": {"percent": 50}}, 50.0, "心跳异常"),
    ],
)
def test_progress_bar_presentation(snapshot, value, label) -> None:
    assert progress_bar_presentation(snapshot) == (value, label)


@pytest.mark.parametrize("percent", [True, "50", -1, float("nan"), float("inf")])
def test_progress_bar_rejects_malformed_percent(percent) -> None:
    value, label = progress_bar_presentation(
        {"status": "running", "current_file": "x.mp4", "progress": {"percent": percent}}
    )
    assert value == 0.0
    assert label == "正在准备"
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_app.py -q
```

Expected: collection fails because `progress_bar_presentation` is missing.

- [ ] **Step 3: Implement the pure function**

```python
def progress_bar_presentation(snapshot: object) -> tuple[float, str]:
    safe = snapshot if isinstance(snapshot, dict) else {}
    status = safe.get("status", "stopped")
    progress = safe.get("progress")
    details = progress if isinstance(progress, dict) else {}
    percent = details.get("percent")
    valid = (
        not isinstance(percent, bool)
        and isinstance(percent, (int, float))
        and isfinite(percent)
        and percent >= 0
    )
    value = min(100.0, float(percent)) if valid else 0.0
    if status == "stale":
        return value, "心跳异常"
    if status != "running":
        labels = {
            "stopped": "后台已停止",
            "needs_login": "需要重新登录",
            "needs_config": "配置无效",
            "error": "后台错误",
            "starting": "正在启动",
        }
        return value, labels.get(str(status), "状态不可用")
    if valid:
        return value, f"{value:.1f}%"
    if safe.get("current_file"):
        return 0.0, "正在准备"
    return 0.0, "等待任务"
```

- [ ] **Step 4: Run tests and verify GREEN**

Run Step 2.

Expected: all GUI app tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/gui/app.py tests/test_gui_app.py
git commit -m "feat: model GUI download progress"
```

### Task 2: Bind the presentation to ttk widgets

**Files:**
- Modify: `src/tg_video_downloader/gui/app.py`
- Modify: `tests/test_gui_app.py`

- [ ] **Step 1: Add a failing refresh integration test**

Extend the fake app in `test_refresh_status_shows_progress_paused_history_and_group_policy`:

```python
app.progress_bar_var = FakeVar()
app.progress_bar_label_var = FakeVar()

app._refresh_status()

assert app.progress_bar_var.get() == 50.0
assert app.progress_bar_label_var.get() == "50.0%"
```

Make `FakeVar` accept and return `str | float`:

```python
class FakeVar:
    def __init__(self, value: str | float = "") -> None:
        self.value = value

    def get(self) -> str | float:
        return self.value

    def set(self, value: str | float) -> None:
        self.value = value
```

Extend the existing `test_status_read_error_is_published_for_tray_recovery` setup and assertions:

```python
app.progress_bar_var = FakeVar(37.5)
app.progress_bar_label_var = FakeVar("37.5%")

app._refresh_status()

assert app.progress_bar_var.get() == 37.5
assert app.progress_bar_label_var.get() == "状态读取失败"
assert published == [{"status": "error", "error": "heartbeat broken"}]
```

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_app.py::test_refresh_status_shows_progress_paused_history_and_group_policy -q
```

Expected: the fake app has no progress variables or they remain unchanged.

- [ ] **Step 3: Create and update the progress widgets**

In `_build_run_page`, immediately below the current-file/status grid, create:

```python
self.progress_bar_var = tk.DoubleVar(value=0.0)
self.progress_bar_label_var = tk.StringVar(value="等待任务")
progress_row = ttk.Frame(page)
progress_row.pack(fill="x", pady=(12, 0))
ttk.Progressbar(
    progress_row,
    orient="horizontal",
    mode="determinate",
    maximum=100.0,
    variable=self.progress_bar_var,
).pack(side="left", fill="x", expand=True)
ttk.Label(
    progress_row,
    textvariable=self.progress_bar_label_var,
    width=12,
    anchor="e",
).pack(side="left", padx=(12, 0))
```

In `_refresh_status`, add the presentation update inside the existing successful `try` branch after textual progress formatting:

```python
bar_value, bar_label = progress_bar_presentation(snapshot)
self.progress_bar_var.set(bar_value)
self.progress_bar_label_var.set(bar_label)
```

Add this line to the existing broad exception branch before publishing the tray error:

```python
self.progress_bar_label_var.set("状态读取失败")
```

The numeric variable is deliberately not changed by that branch. Keep the existing final scheduling line unchanged:

```python
self._status_after = self.after(2000, self._refresh_status)
```

- [ ] **Step 4: Run all GUI and tray tests**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_app.py tests\test_gui_runtime.py tests\test_tray.py -q
```

Expected: all pass; tests still observe one 2000 ms refresh callback.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/gui/app.py tests/test_gui_app.py
git commit -m "feat: show current file progress in the GUI"
```

### Task 3: Documentation and Windows acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/verification.md`

- [ ] **Step 1: Document exact progress behavior**

Add this paragraph to the run-status section of `README.md`:

```markdown
“当前文件进度”只表示正在下载的单个文件。进度条复用现有的 2 秒心跳刷新；百分比暂不可用时显示“正在准备”或“等待任务”。此功能不增加下载线程、动画计时器或额外轮询。
```

Add the measured heartbeat percentage, GUI label, tray accessible name, and 2000 ms callback evidence to `docs/verification.md` after the Windows check.

- [ ] **Step 2: Run full verification**

```powershell
& .\scripts\check.ps1
```

Expected: all tests, compileall, and path checks pass.

- [ ] **Step 3: Perform a real Windows progress check**

During an active download, record heartbeat percent before and after one refresh, and verify the GUI bar label and tray accessible name match the same rounded percentage. Hide the GUI and confirm downloader bytes continue advancing.

- [ ] **Step 4: Commit**

```powershell
git add README.md docs/verification.md
git commit -m "docs: verify the download progress bar"
```
