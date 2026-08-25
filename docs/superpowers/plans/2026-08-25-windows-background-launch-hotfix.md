# Windows Background Launch Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Windows GUI 点击“启动后台”后隐藏 PowerShell 在执行脚本前退出且界面无反馈的问题。

**Architecture:** 保留现有 GUI → `GuiController` → PowerShell supervisor → Python service 链路，只移除已证实不兼容的 `DETACHED_PROCESS`。启动器在短时间内观察项目内 PID/锁/心跳或子进程退出状态，GUI 则立即显示 `starting`，不改 Telegram、白名单和下载状态机。

**Tech Stack:** Python 3.12、Tkinter/ttk、Windows `subprocess`、PowerShell、pytest。

---

## 文件结构

- 创建 `tests/test_windows.py`：覆盖 Windows 创建标志、启动器提前退出和慢启动行为。
- 修改 `src/tg_video_downloader/windows.py`：使用兼容的隐藏启动标志并观察启动结果。
- 修改 `tests/test_gui_app.py`：覆盖点击启动后的即时状态反馈。
- 修改 `src/tg_video_downloader/gui/app.py`：消除启动方法的双重同步包装并写入 `starting`。
- 修改 `docs/verification.md`：记录新的测试数量和真实 Windows 启停结果。

### Task 1: Windows 隐藏启动器回归测试

**Files:**
- Create: `tests/test_windows.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: 写入创建标志失败测试**

```python
from pathlib import Path
from types import SimpleNamespace

from tg_video_downloader import windows
from tg_video_downloader.paths import ProjectPaths


def test_hidden_supervisor_does_not_use_detached_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    (paths.runtime / "supervisor.pid").write_text("1", encoding="ascii")
    captured = {}
    process = SimpleNamespace(poll=lambda: None)

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(windows.subprocess, "Popen", fake_popen)

    assert windows.start_hidden_supervisor(tmp_path) is process
    flags = captured["creationflags"]
    assert flags & windows.subprocess.CREATE_NO_WINDOW
    assert not flags & windows.subprocess.DETACHED_PROCESS
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_windows.py::test_hidden_supervisor_does_not_use_detached_process -q`

Expected: FAIL，因为当前 `creationflags` 仍包含 `DETACHED_PROCESS`。

- [ ] **Step 3: 写入提前退出和慢启动测试**

```python
import pytest


def test_hidden_supervisor_reports_early_exit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    process = SimpleNamespace(poll=lambda: 0)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(RuntimeError, match="退出码 0"):
        windows.start_hidden_supervisor(tmp_path)


def test_hidden_supervisor_allows_slow_running_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    process = SimpleNamespace(poll=lambda: None)
    times = iter((0.0, 2.0))
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(windows.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(windows.time, "sleep", lambda _seconds: None)

    assert windows.start_hidden_supervisor(tmp_path) is process
```

- [ ] **Step 4: 运行三个测试并确认当前实现至少两项失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_windows.py -q`

Expected: 创建标志测试失败；提前退出测试失败；慢启动测试可能因尚未导入 `time` 而失败，均证明现有代码没有所需行为。

### Task 2: 实现兼容启动标志和启动结果观察

**Files:**
- Modify: `src/tg_video_downloader/windows.py:1-110`
- Test: `tests/test_windows.py`

- [ ] **Step 1: 写入最小实现**

在导入区加入 `time`，并将启动函数改为：

```python
import time


SUPERVISOR_START_OBSERVE_SECONDS = 2.0
SUPERVISOR_START_POLL_SECONDS = 0.05


def start_hidden_supervisor(project_root: Path) -> subprocess.Popen[bytes]:
    root = project_root.resolve()
    paths = ProjectPaths.from_root(root)
    script = root / "scripts" / "run-supervisor.ps1"
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=root,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    ready_files = (
        paths.runtime / "supervisor.pid",
        paths.runtime / "downloader.lock",
        paths.heartbeat,
    )
    deadline = time.monotonic() + SUPERVISOR_START_OBSERVE_SECONDS
    while time.monotonic() < deadline:
        if any(path.exists() for path in ready_files):
            return process
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(f"后台启动器提前退出，退出码 {returncode}")
        time.sleep(SUPERVISOR_START_POLL_SECONDS)
    return process
```

- [ ] **Step 2: 运行启动器测试并确认 GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_windows.py -q`

Expected: `3 passed`。

- [ ] **Step 3: 运行现有控制器启动测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_controller.py::test_start_stop_and_missing_heartbeat -q`

Expected: `1 passed`，现有进程控制协议保持兼容。

- [ ] **Step 4: 提交启动器修复**

```powershell
git add tests/test_windows.py src/tg_video_downloader/windows.py
git commit -m "fix: start Windows supervisor without detached flag"
```

### Task 3: GUI 启动即时反馈

**Files:**
- Modify: `tests/test_gui_app.py`
- Modify: `src/tg_video_downloader/gui/app.py:752-754`

- [ ] **Step 1: 写入 GUI 失败测试**

```python
def test_start_service_sets_starting_status() -> None:
    app = object.__new__(DownloaderApp)
    started = []
    app.controller = SimpleNamespace(start=lambda: started.append(True))
    app.status_vars = {"status": FakeVar("stopped")}

    app._start_service()

    assert started == [True]
    assert app.status_vars["status"].get() == "starting"
```

同时在 `tests/test_gui_app.py` 导入：

```python
from types import SimpleNamespace
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_app.py::test_start_service_sets_starting_status -q`

Expected: FAIL，因为当前方法依赖 `_call_sync` 且不会设置 `starting`。

- [ ] **Step 3: 写入最小 GUI 实现**

```python
def _start_service(self) -> None:
    self.controller.start()
    self.status_vars["status"].set("starting")
```

按钮构造层现有的 `_call_sync(fn)` 继续统一捕获异常并调用脱敏错误框。

- [ ] **Step 4: 运行 GUI 和控制器定向测试并确认 GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_app.py tests/test_gui_controller.py -q`

Expected: 全部通过。

- [ ] **Step 5: 提交 GUI 反馈修复**

```powershell
git add tests/test_gui_app.py src/tg_video_downloader/gui/app.py
git commit -m "fix: show background startup progress"
```

### Task 4: 全量验证和真实 Windows 冒烟

**Files:**
- Modify: `docs/verification.md`

- [ ] **Step 1: 运行全量自动化验证**

Run: `scripts\check.ps1`

Expected: 93 项测试全部通过，语法编译和项目路径守卫通过。

- [ ] **Step 2: 运行依赖完整性检查**

Run: `.venv\Scripts\python.exe -m pip check`

Expected: `No broken requirements found.`

- [ ] **Step 3: 真实 GUI 启动冒烟**

1. 确认诊断实例已按停止标记退出，PID、锁和旧心跳状态可解释。
2. 打开正式 GUI，确认自动复用登录且不显示二维码。
3. 点击“启动后台”，确认状态立即显示 `starting`。
4. 在 15 秒内确认项目 `.runtime` 出现 supervisor PID、下载器锁和 `running` 心跳。
5. 关闭 GUI，确认 supervisor 和服务仍在运行。
6. 写入项目内停止标记，确认当前文件完成后服务和 supervisor 有序退出。

- [ ] **Step 4: 更新验收记录**

在 `docs/verification.md` 记录：

```markdown
- Windows 后台启动回归覆盖不兼容 `DETACHED_PROCESS`、提前退出反馈和慢启动容忍；真实 GUI 点击后生成 PID、锁和运行心跳，关闭 GUI 后后台继续运行。
```

同时把完整测试总数更新为 93，并写入本次实际测试时间和分支。

- [ ] **Step 5: 提交验收记录**

```powershell
git add docs/verification.md
git commit -m "docs: verify Windows background startup"
```

### Task 5: 审查、合并和发布

**Files:**
- Review: `src/tg_video_downloader/windows.py`
- Review: `src/tg_video_downloader/gui/app.py`
- Review: `tests/test_windows.py`
- Review: `tests/test_gui_app.py`
- Review: `docs/verification.md`

- [ ] **Step 1: 检查差异和工作树洁净度**

Run: `git diff --check master...HEAD && git status --short`

Expected: 无空白错误；仅计划内提交，工作树洁净。

- [ ] **Step 2: 提交后再次运行发布门禁**

Run: `scripts\check.ps1; .venv\Scripts\python.exe -m pip check`

Expected: 93 项测试通过，依赖完整。

- [ ] **Step 3: 快进合并到 `master`**

```powershell
git switch master
git merge --ff-only codex/windows-background-launch-hotfix
```

- [ ] **Step 4: 在 `master` 再次验证并推送两个远端**

```powershell
scripts\check.ps1
.venv\Scripts\python.exe -m pip check
git push github master
git push modelscope master
```

Expected: GitHub 和魔塔的 `master` 均指向本地 `HEAD`。
