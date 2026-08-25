# Windows System Tray Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-instance Windows tray companion to the existing Tk configurator so users can see live download status and progress, open project directories, and start or stop the independent downloader.

**Architecture:** Keep the supervisor and downloader service unchanged. Add a focused tray module for pure status presentation plus the `pystray` adapter, a focused GUI-instance coordinator for the project-local lock and activation request, and a runtime module that wires those units to Tk without moving business logic into the tray thread.

**Tech Stack:** Python 3.11+, Tkinter, pystray 0.19.5, Pillow 12.x, Windows `msvcrt` byte locks, pytest, PowerShell launch scripts.

---

## File map

- Create `src/tg_video_downloader/gui/tray.py`: status-to-presentation mapping, in-memory icon drawing, tray menu adapter, thread-to-Tk callback scheduling.
- Create `tests/test_tray.py`: pure formatting, icon, menu state, callback dispatch, notification, and idempotent shutdown tests.
- Create `src/tg_video_downloader/gui/instance.py`: GUI lock ownership and activation-token coordination.
- Create `tests/test_gui_instance.py`: duplicate launch, activation, stale request, and lock release tests.
- Create `src/tg_video_downloader/gui/runtime.py`: compose Tk, `DownloaderApp`, tray, and GUI-instance lifetime.
- Create `tests/test_gui_runtime.py`: runtime behavior with fake Tk, app, tray, and instance objects.
- Modify `src/tg_video_downloader/paths.py`: explicit project-local GUI lock and activation-request paths.
- Modify `src/tg_video_downloader/windows.py`: configurable `SingleInstance` error text while preserving downloader behavior.
- Modify `src/tg_video_downloader/gui/app.py`: publish status snapshots to the tray, expose idempotent UI cleanup, and remove process composition from this already-large view module.
- Modify `src/tg_video_downloader/cli.py`: route the `gui` command to the new runtime module.
- Modify `src/tg_video_downloader/diagnostics.py`: validate tray packages, icon creation, and Windows menu/default-action support without showing an icon.
- Modify `pyproject.toml`: add bounded tray dependencies.
- Modify `tests/test_paths.py`, `tests/test_windows.py`, `tests/test_gui_app.py`, and `tests/test_diagnostics.py`: focused regression coverage.
- Modify `README.md`: document colors, hover progress, menu actions, single-instance behavior, and the difference between stopping the downloader and exiting the tray.
- Modify `docs/verification.md`: record the final automated and real-Windows evidence.

### Task 1: Tray presentation model, icon, and dependencies

**Files:**
- Create: `src/tg_video_downloader/gui/tray.py`
- Create: `tests/test_tray.py`
- Modify: `pyproject.toml:10-17`

- [ ] **Step 1: Write failing presentation and icon tests**

Create `tests/test_tray.py` with the initial tests:

```python
from math import nan

import pytest

from tg_video_downloader.gui.tray import (
    ERROR_COLOR,
    RUNNING_COLOR,
    STARTING_COLOR,
    STOPPED_COLOR,
    build_tray_presentation,
    create_status_icon,
)


def test_running_download_presentation_includes_progress_and_speed() -> None:
    presentation = build_tray_presentation(
        {
            "status": "running",
            "current_file": "1359_弱点水印.mp4",
            "progress": {
                "percent": 67.04,
                "bytes_per_second": 215_732,
            },
            "counts": {"pending_history": 777, "pending_live": 0, "completed": 11},
        }
    )

    assert presentation.color == RUNNING_COLOR
    assert presentation.title == "正在下载｜1359_弱点水印.mp4｜67.0%｜210.68 KiB/s"
    assert presentation.summary == "运行中｜1359_弱点水印.mp4｜67.0%"
    assert presentation.can_start is False
    assert presentation.can_stop is True


@pytest.mark.parametrize(
    ("status", "color", "can_start", "can_stop"),
    [
        ("starting", STARTING_COLOR, False, True),
        ("stale", STARTING_COLOR, False, True),
        ("needs_login", ERROR_COLOR, False, True),
        ("needs_config", ERROR_COLOR, False, True),
        ("error", ERROR_COLOR, False, True),
        ("stopped", STOPPED_COLOR, True, False),
    ],
)
def test_status_controls_color_and_menu_actions(
    status: str,
    color: tuple[int, int, int, int],
    can_start: bool,
    can_stop: bool,
) -> None:
    presentation = build_tray_presentation({"status": status})

    assert presentation.color == color
    assert presentation.can_start is can_start
    assert presentation.can_stop is can_stop


def test_idle_running_presentation_uses_queue_counts() -> None:
    presentation = build_tray_presentation(
        {
            "status": "running",
            "counts": {"pending_history": 777, "pending_live": 2, "completed": 11},
        }
    )

    assert presentation.title == "后台正常｜等待 779｜已完成 11"
    assert presentation.summary == "运行中｜等待 779｜已完成 11"


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"status": "running", "progress": {"percent": nan, "bytes_per_second": 1}},
        {"status": "running", "progress": {"percent": 2, "bytes_per_second": "bad"}},
        {"status": object(), "counts": "bad"},
    ],
)
def test_malformed_snapshot_degrades_without_raising(snapshot: dict[str, object]) -> None:
    presentation = build_tray_presentation(snapshot)

    assert presentation.title
    assert len(presentation.title) <= 127


def test_long_file_name_is_truncated_to_windows_title_limit() -> None:
    presentation = build_tray_presentation(
        {
            "status": "running",
            "current_file": "很长" * 100 + ".mp4",
            "progress": {"percent": 50, "bytes_per_second": 1024},
        }
    )

    assert "…" in presentation.title
    assert len(presentation.title) <= 127


def test_status_icon_is_rgba_and_uses_requested_color() -> None:
    image = create_status_icon(RUNNING_COLOR)

    assert image.mode == "RGBA"
    assert image.size == (64, 64)
    assert image.getpixel((8, 32)) == RUNNING_COLOR
    assert image.getpixel((32, 24)) == (255, 255, 255, 255)
```

- [ ] **Step 2: Run the new test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_tray.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'tg_video_downloader.gui.tray'`.

- [ ] **Step 3: Add bounded runtime dependencies and install them project-locally**

Add these entries to `[project].dependencies` in `pyproject.toml`:

```toml
  "pillow>=12.3,<13",
  "pystray>=0.19.5,<0.20",
```

Install through the existing project-local bootstrap path:

```powershell
& .\scripts\bootstrap.ps1
```

Expected: exit code `0`; packages install only into `.venv`, with cache and temporary data under `.cache` and `.tmp`.

- [ ] **Step 4: Implement the pure presentation model and icon drawing**

Create `src/tg_video_downloader/gui/tray.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


RUNNING_COLOR = (35, 165, 82, 255)
STARTING_COLOR = (230, 170, 20, 255)
ERROR_COLOR = (210, 55, 55, 255)
STOPPED_COLOR = (125, 125, 125, 255)
WINDOWS_TITLE_LIMIT = 127
FILE_NAME_LIMIT = 38


@dataclass(frozen=True)
class TrayPresentation:
    color: tuple[int, int, int, int]
    title: str
    summary: str
    can_start: bool
    can_stop: bool


def _count(counts: object, key: str) -> int:
    if not isinstance(counts, dict):
        return 0
    value = counts.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) and number >= 0 else None


def _rate(value: float) -> str:
    size = value
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if size < 1024 or unit == "GiB/s":
            return f"{size:.2f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def _short_file_name(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    name = value.strip()
    if len(name) <= FILE_NAME_LIMIT:
        return name
    return name[: FILE_NAME_LIMIT - 1] + "…"


def _limited(value: str) -> str:
    return value if len(value) <= WINDOWS_TITLE_LIMIT else value[:126] + "…"


def build_tray_presentation(snapshot: object) -> TrayPresentation:
    safe = snapshot if isinstance(snapshot, dict) else {}
    status_value = safe.get("status", "stopped")
    status = status_value if isinstance(status_value, str) else "error"
    if status == "running":
        color = RUNNING_COLOR
    elif status in {"starting", "stale"}:
        color = STARTING_COLOR
    elif status == "stopped":
        color = STOPPED_COLOR
    else:
        color = ERROR_COLOR

    can_start = status == "stopped"
    can_stop = not can_start
    progress = safe.get("progress")
    progress_dict = progress if isinstance(progress, dict) else {}
    file_name = _short_file_name(
        progress_dict.get("file_name") or safe.get("current_file")
    )
    percent = _finite_number(progress_dict.get("percent"))
    speed = _finite_number(progress_dict.get("bytes_per_second"))
    counts = safe.get("counts")
    waiting = _count(counts, "pending_live") + _count(counts, "pending_history")
    completed = _count(counts, "completed")

    if status == "running" and file_name and percent is not None and speed is not None:
        title = f"正在下载｜{file_name}｜{percent:.1f}%｜{_rate(speed)}"
        summary = f"运行中｜{file_name}｜{percent:.1f}%"
    elif status == "running":
        title = f"后台正常｜等待 {waiting}｜已完成 {completed}"
        summary = f"运行中｜等待 {waiting}｜已完成 {completed}"
    elif status == "starting":
        title = summary = "正在启动｜等待后台心跳"
    elif status == "stale":
        title = summary = "心跳异常｜请打开配置器检查"
    elif status == "stopped":
        title = summary = "后台已停止｜右键可启动"
    elif status == "needs_login":
        title = summary = "需要重新登录｜请打开配置器"
    elif status == "needs_config":
        title = summary = "配置无效｜请打开配置器"
    else:
        title = summary = "后台错误｜请打开配置器检查"

    return TrayPresentation(color, _limited(title), _limited(summary), can_start, can_stop)


def create_status_icon(color: tuple[int, int, int, int]) -> Any:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=color)
    draw.rounded_rectangle((29, 13, 35, 38), radius=2, fill=(255, 255, 255, 255))
    draw.polygon(((20, 34), (44, 34), (32, 50)), fill=(255, 255, 255, 255))
    return image
```

- [ ] **Step 5: Run the focused tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_tray.py -q
```

Expected: all tests in `tests/test_tray.py` pass.

- [ ] **Step 6: Commit the presentation unit**

```powershell
git add pyproject.toml src/tg_video_downloader/gui/tray.py tests/test_tray.py
git commit -m "feat: model Windows tray status"
```

### Task 2: pystray adapter and Tk-safe menu callbacks

**Files:**
- Modify: `src/tg_video_downloader/gui/tray.py`
- Modify: `tests/test_tray.py`

- [ ] **Step 1: Add failing adapter tests**

Append to `tests/test_tray.py`:

```python
from tg_video_downloader.gui.tray import TrayActions, TrayController


class FakeIcon:
    HAS_NOTIFICATION = True
    latest = None

    def __init__(self, name, icon, title, menu) -> None:
        type(self).latest = self
        self.name = name
        self.icon = icon
        self.title = title
        self.menu = menu
        self.visible = False
        self.update_calls = 0
        self.stop_calls = 0
        self.notifications: list[tuple[str, str]] = []

    def run_detached(self, setup) -> None:
        setup(self)

    def update_menu(self) -> None:
        self.update_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def notify(self, message: str, title: str) -> None:
        self.notifications.append((message, title))


def make_actions(calls: list[str], errors: list[Exception]) -> TrayActions:
    return TrayActions(
        show_window=lambda: calls.append("show"),
        start_service=lambda: calls.append("start"),
        stop_service=lambda: calls.append("stop"),
        open_downloads=lambda: calls.append("downloads"),
        open_logs=lambda: calls.append("logs"),
        exit_ui=lambda: calls.append("exit"),
        report_error=errors.append,
    )


def test_controller_starts_updates_and_stops_idempotently() -> None:
    scheduled: list[object] = []
    controller = TrayController(
        schedule=scheduled.append,
        actions=make_actions([], []),
        icon_factory=FakeIcon,
    )

    controller.start()
    controller.update({"status": "running", "counts": {"completed": 3}})
    controller.stop()
    controller.stop()

    icon = FakeIcon.latest
    assert controller.available is False
    assert icon.visible is True
    assert icon.title == "后台正常｜等待 0｜已完成 3"
    assert icon.update_calls == 1
    assert icon.stop_calls == 1


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("show_window", "show"),
        ("start_service", "start"),
        ("stop_service", "stop"),
        ("open_downloads", "downloads"),
        ("open_logs", "logs"),
        ("exit_ui", "exit"),
    ],
)
def test_menu_callbacks_are_marshaled_to_tk(
    field: str,
    expected: str,
) -> None:
    calls: list[str] = []
    scheduled: list[object] = []
    actions = make_actions(calls, [])
    controller = TrayController(
        schedule=scheduled.append,
        actions=actions,
        icon_factory=FakeIcon,
    )
    controller.start()

    controller._dispatch(getattr(actions, field))(None, None)

    assert calls == []
    scheduled.pop()()
    assert calls == [expected]


def test_action_failure_notifies_and_reports_without_stopping_tray() -> None:
    scheduled: list[object] = []
    errors: list[Exception] = []

    def fail() -> None:
        raise RuntimeError("cannot open")

    actions = make_actions([], errors)
    actions = TrayActions(**{**actions.__dict__, "open_downloads": fail})
    controller = TrayController(
        schedule=scheduled.append,
        actions=actions,
        icon_factory=FakeIcon,
    )
    controller.start()

    controller._dispatch(actions.open_downloads)(None, None)
    scheduled.pop()()

    assert str(errors[0]) == "cannot open"
    assert FakeIcon.latest.notifications == [
        ("cannot open", "Telegram 视频自动下载器")
    ]
    assert controller.available is True


def test_scheduled_callback_after_tray_stop_is_ignored() -> None:
    calls: list[str] = []
    scheduled: list[object] = []
    actions = make_actions(calls, [])
    controller = TrayController(
        schedule=scheduled.append,
        actions=actions,
        icon_factory=FakeIcon,
    )
    controller.start()

    controller._dispatch(actions.start_service)(None, None)
    controller.stop()
    scheduled.pop()()

    assert calls == []


def test_callback_arriving_after_tray_stop_is_not_scheduled() -> None:
    calls: list[str] = []
    scheduled: list[object] = []
    actions = make_actions(calls, [])
    controller = TrayController(
        schedule=scheduled.append,
        actions=actions,
        icon_factory=FakeIcon,
    )
    controller.start()
    controller.stop()

    controller._dispatch(actions.start_service)(None, None)

    assert scheduled == []
    assert calls == []
```

- [ ] **Step 2: Run the adapter tests to verify they fail**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_tray.py -q
```

Expected: import fails because `TrayActions` and `TrayController` do not exist.

- [ ] **Step 3: Implement the tray adapter**

Add imports to `src/tg_video_downloader/gui/tray.py`:

```python
from collections.abc import Callable
from threading import Event, RLock
```

Append:

```python
@dataclass(frozen=True)
class TrayActions:
    show_window: Callable[[], None]
    start_service: Callable[[], None]
    stop_service: Callable[[], None]
    open_downloads: Callable[[], None]
    open_logs: Callable[[], None]
    exit_ui: Callable[[], None]
    report_error: Callable[[Exception], None]


class TrayController:
    def __init__(
        self,
        *,
        schedule: Callable[[Callable[[], None]], object],
        actions: TrayActions,
        icon_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._schedule = schedule
        self._actions = actions
        self._icon_factory = icon_factory
        self._icon: Any | None = None
        self._presentation = build_tray_presentation({"status": "stopped"})
        self._lock = RLock()
        self._available = False
        self._stopped = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def presentation(self) -> TrayPresentation:
        with self._lock:
            return self._presentation

    def start(self) -> None:
        if self._available:
            return
        import pystray

        ready = Event()
        menu = pystray.Menu(
            pystray.MenuItem(
                lambda _item: self.presentation.summary,
                lambda _icon, _item: None,
                enabled=False,
            ),
            pystray.MenuItem(
                "打开配置器",
                self._dispatch(self._actions.show_window),
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "启动后台",
                self._dispatch(self._actions.start_service),
                enabled=lambda _item: self.presentation.can_start,
            ),
            pystray.MenuItem(
                "停止后台",
                self._dispatch(self._actions.stop_service),
                enabled=lambda _item: self.presentation.can_stop,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开下载目录", self._dispatch(self._actions.open_downloads)),
            pystray.MenuItem("打开日志目录", self._dispatch(self._actions.open_logs)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出托盘", self._dispatch(self._actions.exit_ui)),
        )
        factory = self._icon_factory or pystray.Icon
        current = self.presentation
        icon = factory(
            "telegram-video-downloader",
            icon=create_status_icon(current.color),
            title=current.title,
            menu=menu,
        )

        def setup(started_icon: Any) -> None:
            started_icon.visible = True
            ready.set()

        try:
            icon.run_detached(setup)
            if not ready.wait(timeout=2):
                raise RuntimeError("Windows 托盘图标启动超时")
        except Exception:
            icon.stop()
            raise
        self._icon = icon
        self._stopped = False
        self._available = True

    def update(self, snapshot: object) -> None:
        presentation = build_tray_presentation(snapshot)
        with self._lock:
            self._presentation = presentation
        icon = self._icon
        if icon is None or not self._available:
            return
        icon.icon = create_status_icon(presentation.color)
        icon.title = presentation.title
        icon.update_menu()

    def notify_error(self, message: str) -> None:
        icon = self._icon
        if (
            icon is not None
            and self._available
            and getattr(icon, "HAS_NOTIFICATION", False)
        ):
            icon.notify(_limited(message), "Telegram 视频自动下载器")

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._available = False
        icon, self._icon = self._icon, None
        if icon is not None:
            icon.stop()

    def _dispatch(self, action: Callable[[], None]) -> Callable[[Any, Any], None]:
        def callback(_icon: Any, _item: Any) -> None:
            if self._stopped:
                return
            self._schedule(lambda: self._run_action(action))

        return callback

    def _run_action(self, action: Callable[[], None]) -> None:
        if self._stopped:
            return
        try:
            action()
        except Exception as error:
            self.notify_error(str(error) or type(error).__name__)
            self._actions.report_error(error)
```

- [ ] **Step 4: Run tray tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_tray.py -q
```

Expected: all tray tests pass.

- [ ] **Step 5: Commit the adapter**

```powershell
git add src/tg_video_downloader/gui/tray.py tests/test_tray.py
git commit -m "feat: control Windows tray menu"
```

### Task 3: Project-local GUI single instance and activation request

**Files:**
- Create: `src/tg_video_downloader/gui/instance.py`
- Create: `tests/test_gui_instance.py`
- Modify: `src/tg_video_downloader/paths.py:7-39`
- Modify: `src/tg_video_downloader/windows.py:17-42`
- Modify: `tests/test_paths.py`
- Modify: `tests/test_windows.py`

- [ ] **Step 1: Write failing path and custom-lock tests**

Append to `tests/test_paths.py`:

```python
def test_gui_control_files_stay_inside_runtime(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)

    assert paths.gui_lock == paths.runtime / "gui.lock"
    assert paths.gui_activation == paths.runtime / "gui-activate.request"
    assert paths.gui_lock.is_relative_to(paths.root)
    assert paths.gui_activation.is_relative_to(paths.root)
```

Append to `tests/test_windows.py`:

```python
def test_single_instance_supports_context_specific_error_message(tmp_path: Path) -> None:
    lock_path = tmp_path / "gui.lock"

    with windows.SingleInstance(lock_path):
        with pytest.raises(RuntimeError, match="配置器已经在运行"):
            with windows.SingleInstance(
                lock_path,
                already_running_message="配置器已经在运行",
            ):
                pass
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_paths.py tests\test_windows.py -q
```

Expected: failures report missing `gui_lock`, `gui_activation`, and unsupported `already_running_message`.

- [ ] **Step 3: Add paths and configurable lock text**

Add fields to `ProjectPaths` after `stop_flag`:

```python
    gui_lock: Path
    gui_activation: Path
```

Add values in `ProjectPaths.from_root`:

```python
            gui_lock=runtime / "gui.lock",
            gui_activation=runtime / "gui-activate.request",
```

Change `SingleInstance.__init__` and the lock failure in `windows.py`:

```python
    def __init__(
        self,
        lock_path: Path,
        *,
        already_running_message: str = "下载器已经在运行",
    ) -> None:
        self.lock_path = lock_path
        self.already_running_message = already_running_message
        self._handle = None
```

```python
        except OSError as error:
            handle.close()
            raise RuntimeError(self.already_running_message) from error
```

- [ ] **Step 4: Run path and Windows lock tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_paths.py tests\test_windows.py -q
```

Expected: all focused tests pass.

- [ ] **Step 5: Write failing GUI coordinator tests**

Create `tests/test_gui_instance.py`:

```python
from pathlib import Path

from tg_video_downloader.gui.instance import GuiInstanceCoordinator
from tg_video_downloader.paths import ProjectPaths


def test_duplicate_instance_signals_first_and_releases_cleanly(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    first = GuiInstanceCoordinator(paths)
    second = GuiInstanceCoordinator(paths)

    assert first.acquire_or_signal() is True
    assert first.activation_requested() is False
    assert second.acquire_or_signal() is False
    assert first.activation_requested() is True
    assert first.activation_requested() is False

    first.close()
    third = GuiInstanceCoordinator(paths)
    assert third.acquire_or_signal() is True
    third.close()


def test_stale_activation_token_is_baseline_not_a_new_request(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.runtime.mkdir(parents=True)
    paths.gui_activation.write_text("stale-token\n", encoding="ascii")
    instance = GuiInstanceCoordinator(paths)

    assert instance.acquire_or_signal() is True
    assert instance.activation_requested() is False

    instance.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    instance = GuiInstanceCoordinator(ProjectPaths.from_root(tmp_path))
    assert instance.acquire_or_signal() is True

    instance.close()
    instance.close()
```

- [ ] **Step 6: Run the coordinator tests to verify they fail**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_instance.py -q
```

Expected: collection fails because `tg_video_downloader.gui.instance` does not exist.

- [ ] **Step 7: Implement the GUI instance coordinator**

Create `src/tg_video_downloader/gui/instance.py`:

```python
from __future__ import annotations

import os
from uuid import uuid4

from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.windows import SingleInstance


class GuiInstanceCoordinator:
    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self._lock = SingleInstance(
            paths.assert_within_root(paths.gui_lock),
            already_running_message="配置器已经在运行",
        )
        self._active = False
        self._last_token = self._read_token()

    def acquire_or_signal(self) -> bool:
        try:
            self._lock.__enter__()
        except RuntimeError:
            self._write_token(uuid4().hex)
            return False
        self._active = True
        self._last_token = self._read_token()
        return True

    def activation_requested(self) -> bool:
        token = self._read_token()
        if not token or token == self._last_token:
            return False
        self._last_token = token
        return True

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        self._lock.__exit__(None, None, None)

    def _read_token(self) -> str | None:
        try:
            token = self.paths.gui_activation.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        return token or None

    def _write_token(self, token: str) -> None:
        path = self.paths.assert_within_root(self.paths.gui_activation)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{token}.new")
        try:
            with temporary.open("w", encoding="ascii", newline="\n") as handle:
                handle.write(token + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
```

- [ ] **Step 8: Run all instance tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_paths.py tests\test_windows.py tests\test_gui_instance.py -q
```

Expected: all focused tests pass.

- [ ] **Step 9: Commit single-instance coordination**

```powershell
git add src/tg_video_downloader/paths.py src/tg_video_downloader/windows.py src/tg_video_downloader/gui/instance.py tests/test_paths.py tests/test_windows.py tests/test_gui_instance.py
git commit -m "feat: activate a single tray configurator"
```

### Task 4: Publish GUI state and make cleanup idempotent

**Files:**
- Modify: `src/tg_video_downloader/gui/app.py:3-9,87-117,855-860,903-987`
- Modify: `tests/test_gui_app.py`

- [ ] **Step 1: Add failing status-publication and cleanup tests**

Update `test_start_service_sets_starting_status` in `tests/test_gui_app.py`:

```python
def test_start_service_sets_and_publishes_starting_status() -> None:
    app = object.__new__(DownloaderApp)
    started: list[bool] = []
    published: list[dict[str, object]] = []
    app.controller = SimpleNamespace(start=lambda: started.append(True))
    app.status_vars = {"status": FakeVar("stopped")}
    app._status_listener = published.append

    app._start_service()

    assert started == [True]
    assert app.status_vars["status"].get() == "starting"
    assert published == [{"status": "starting"}]
```

In `test_refresh_status_shows_progress_paused_history_and_group_policy`, add:

```python
    published: list[dict[str, object]] = []
    app._status_listener = published.append
```

and append this assertion:

```python
    assert published[0]["status"] == "running"
```

Add:

```python
def test_status_read_error_is_published_for_tray_recovery() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app.controller = SimpleNamespace(
        read_status=lambda: (_ for _ in ()).throw(RuntimeError("heartbeat broken"))
    )
    app.status_vars = {"status": FakeVar()}
    app.api_hash_var = FakeVar("")
    app.phone_var = FakeVar("")
    app.code_var = FakeVar("")
    app.password_var = FakeVar("")
    app.qr_password_var = FakeVar("")
    app._status_listener = published = []
    app._status_listener = published.append
    app.after = lambda *_args: "after-status"

    app._refresh_status()

    assert published == [{"status": "error", "error": "heartbeat broken"}]


def test_close_returns_immediately_after_first_cleanup() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = True

    app.close()
```

- [ ] **Step 2: Run GUI app tests to verify they fail**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_app.py -q
```

Expected: publication assertions fail and `close()` accesses attributes even when `_closed` is already true.

- [ ] **Step 3: Add a status listener and publish every state transition**

Add this import to `src/tg_video_downloader/gui/app.py`:

```python
from collections.abc import Callable
```

Then initialize the listener in `DownloaderApp.__init__` before the first `_refresh_status()` call:

```python
        self._status_listener: Callable[[dict[str, object]], None] = lambda _snapshot: None
```

Add methods near `_start_service`:

```python
    def set_status_listener(
        self,
        listener: Callable[[dict[str, object]], None],
    ) -> None:
        self._status_listener = listener

    def _publish_status(self, snapshot: dict[str, object]) -> None:
        if not self._closed:
            self._status_listener(snapshot)
```

Change start and stop methods to:

```python
    def _start_service(self) -> None:
        self.controller.start()
        snapshot: dict[str, object] = {"status": "starting"}
        self.status_vars["status"].set("starting")
        self._publish_status(snapshot)

    def _stop_service(self) -> None:
        self.controller.stop()
```

In `_refresh_status`, publish the successful snapshot after updating the page:

```python
            self._publish_status(snapshot)
```

Replace the exception branch with:

```python
        except Exception as error:
            message = self._safe_error(error)
            self.status_vars["status"].set(f"状态读取失败：{message}")
            self._publish_status({"status": "error", "error": message})
```

Make `close` idempotent:

```python
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
```

Keep the existing cleanup body after those lines unchanged.

- [ ] **Step 4: Run GUI app tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_app.py -q
```

Expected: all GUI app tests pass.

- [ ] **Step 5: Commit GUI publication behavior**

```powershell
git add src/tg_video_downloader/gui/app.py tests/test_gui_app.py
git commit -m "feat: publish GUI status to tray"
```

### Task 5: Compose Tk, tray, and single-instance lifetime

**Files:**
- Create: `src/tg_video_downloader/gui/runtime.py`
- Create: `tests/test_gui_runtime.py`
- Modify: `src/tg_video_downloader/gui/app.py:999-1012`
- Modify: `src/tg_video_downloader/cli.py:18-23`

- [ ] **Step 1: Write failing runtime tests with fakes**

Create `tests/test_gui_runtime.py`:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader.gui.runtime import run_gui
from tg_video_downloader.gui.tray import TrayActions
from tg_video_downloader.paths import ProjectPaths


class FakeRoot:
    def __init__(self) -> None:
        self.protocols = {}
        self.after_calls: list[tuple[int, object]] = []
        self.withdraw_calls = 0
        self.show_calls: list[str] = []
        self.destroy_calls = 0
        self.mainloop_action = lambda: None

    def title(self, _value: str) -> None: pass
    def geometry(self, _value: str) -> None: pass
    def minsize(self, _width: int, _height: int) -> None: pass
    def protocol(self, name: str, callback) -> None: self.protocols[name] = callback
    def withdraw(self) -> None: self.withdraw_calls += 1
    def deiconify(self) -> None: self.show_calls.append("deiconify")
    def lift(self) -> None: self.show_calls.append("lift")
    def focus_force(self) -> None: self.show_calls.append("focus")
    def after(self, delay: int, callback):
        self.after_calls.append((delay, callback))
        return f"after-{len(self.after_calls)}"
    def after_cancel(self, _identifier: str) -> None: pass
    def destroy(self) -> None: self.destroy_calls += 1
    def mainloop(self) -> None: self.mainloop_action()


class FakeInstance:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.closed = 0
        self.activate = False

    def acquire_or_signal(self) -> bool: return self.acquired
    def activation_requested(self) -> bool:
        result, self.activate = self.activate, False
        return result
    def close(self) -> None: self.closed += 1


class FakeApp:
    def __init__(self, root, controller) -> None:
        self.root = root
        self.controller = controller
        self.closed = 0
        self.listener = lambda _snapshot: None

    def set_status_listener(self, listener) -> None: self.listener = listener
    def _start_service(self) -> None: self.controller.start()
    def _stop_service(self) -> None: self.controller.stop()
    def _safe_error(self, error: Exception) -> str: return str(error)
    def close(self) -> None: self.closed += 1


class FakeTray:
    def __init__(self, *, schedule, actions: TrayActions) -> None:
        self.schedule = schedule
        self.actions = actions
        self.started = 0
        self.stopped = 0
        self.snapshots = []

    def start(self) -> None: self.started += 1
    def stop(self) -> None: self.stopped += 1
    def update(self, snapshot) -> None: self.snapshots.append(snapshot)


def test_duplicate_launch_signals_existing_instance_without_creating_tk(tmp_path: Path) -> None:
    created: list[bool] = []
    instance = FakeInstance(acquired=False)

    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: created.append(True),
        instance_factory=lambda _paths: instance,
    )

    assert created == []
    assert instance.closed == 0


def test_close_hides_to_tray_and_tray_exit_does_not_stop_service(tmp_path: Path) -> None:
    root = FakeRoot()
    instance = FakeInstance()
    controller = SimpleNamespace(
        start=lambda: None,
        stop_calls=0,
        stop=lambda: setattr(controller, "stop_calls", controller.stop_calls + 1),
        open_downloads=lambda: None,
        open_logs=lambda: None,
        read_status=lambda: {"status": "running"},
    )
    captured = {}

    def app_factory(created_root, created_controller):
        app = FakeApp(created_root, created_controller)
        captured["app"] = app
        return app

    def tray_factory(**kwargs):
        tray = FakeTray(**kwargs)
        captured["tray"] = tray
        return tray

    def mainloop_action() -> None:
        root.protocols["WM_DELETE_WINDOW"]()
        captured["tray"].actions.exit_ui()
        captured["tray"].actions.exit_ui()
        captured["tray"].actions.show_window()

    root.mainloop_action = mainloop_action
    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: root,
        controller_factory=lambda _paths: controller,
        app_factory=app_factory,
        tray_factory=tray_factory,
        instance_factory=lambda _paths: instance,
    )

    assert root.withdraw_calls == 1
    assert root.destroy_calls == 1
    assert root.show_calls == []
    assert captured["app"].closed == 1
    assert captured["tray"].stopped == 1
    assert controller.stop_calls == 0
    assert instance.closed == 1


def test_activation_request_restores_hidden_window(tmp_path: Path) -> None:
    root = FakeRoot()
    instance = FakeInstance()
    instance.activate = True

    def mainloop_action() -> None:
        poll = next(callback for delay, callback in root.after_calls if delay == 500)
        poll()
        created_tray.actions.exit_ui()

    created_tray = None

    def tray_factory(**kwargs):
        nonlocal created_tray
        created_tray = FakeTray(**kwargs)
        return created_tray

    root.mainloop_action = mainloop_action
    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: root,
        controller_factory=lambda _paths: SimpleNamespace(
            start=lambda: None,
            stop=lambda: None,
            open_downloads=lambda: None,
            open_logs=lambda: None,
            read_status=lambda: {"status": "stopped"},
        ),
        app_factory=FakeApp,
        tray_factory=tray_factory,
        instance_factory=lambda _paths: instance,
    )

    assert root.show_calls == ["deiconify", "lift", "focus"]


def test_tray_start_failure_keeps_close_as_real_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    instance = FakeInstance()
    captured = {}
    monkeypatch.setattr(
        "tg_video_downloader.gui.runtime.messagebox.showerror",
        lambda *_args, **_kwargs: None,
    )

    class FailingTray(FakeTray):
        def start(self) -> None:
            raise RuntimeError("tray unavailable")

    def app_factory(created_root, controller):
        app = FakeApp(created_root, controller)
        captured["app"] = app
        return app

    def tray_factory(**kwargs):
        tray = FailingTray(**kwargs)
        captured["tray"] = tray
        return tray

    root.mainloop_action = lambda: root.protocols["WM_DELETE_WINDOW"]()
    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: root,
        controller_factory=lambda _paths: SimpleNamespace(
            start=lambda: None,
            stop=lambda: None,
            open_downloads=lambda: None,
            open_logs=lambda: None,
            read_status=lambda: {"status": "stopped"},
        ),
        app_factory=app_factory,
        tray_factory=tray_factory,
        instance_factory=lambda _paths: instance,
    )

    assert root.withdraw_calls == 0
    assert root.destroy_calls == 1
    assert captured["app"].closed == 1
    assert captured["tray"].stopped == 1
    assert instance.closed == 1


def test_tray_actions_route_to_existing_controller_methods(tmp_path: Path) -> None:
    root = FakeRoot()
    instance = FakeInstance()
    calls: list[str] = []
    controller = SimpleNamespace(
        start=lambda: calls.append("start"),
        stop=lambda: calls.append("stop"),
        open_downloads=lambda: calls.append("downloads"),
        open_logs=lambda: calls.append("logs"),
        read_status=lambda: {"status": "stopped"},
    )
    captured = {}

    def tray_factory(**kwargs):
        tray = FakeTray(**kwargs)
        captured["tray"] = tray
        return tray

    def mainloop_action() -> None:
        actions = captured["tray"].actions
        actions.start_service()
        actions.stop_service()
        actions.open_downloads()
        actions.open_logs()
        actions.exit_ui()

    root.mainloop_action = mainloop_action
    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: root,
        controller_factory=lambda _paths: controller,
        app_factory=FakeApp,
        tray_factory=tray_factory,
        instance_factory=lambda _paths: instance,
    )

    assert calls == ["start", "stop", "downloads", "logs"]
```

- [ ] **Step 2: Run runtime tests to verify they fail**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_runtime.py -q
```

Expected: collection fails because `tg_video_downloader.gui.runtime` does not exist.

- [ ] **Step 3: Move composition into the runtime module**

Delete the existing `run_gui` function from the bottom of `gui/app.py` and create `src/tg_video_downloader/gui/runtime.py`:

```python
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox
from typing import Any

from tg_video_downloader.gateway import TelethonGateway
from tg_video_downloader.gui.app import DownloaderApp
from tg_video_downloader.gui.controller import GuiController
from tg_video_downloader.gui.instance import GuiInstanceCoordinator
from tg_video_downloader.gui.tray import TrayActions, TrayController
from tg_video_downloader.paths import ProjectPaths


def run_gui(
    paths: ProjectPaths,
    *,
    root_factory: Callable[[], Any] = tk.Tk,
    controller_factory: Callable[[ProjectPaths], Any] | None = None,
    app_factory: Callable[[Any, Any], Any] = DownloaderApp,
    tray_factory: Callable[..., Any] = TrayController,
    instance_factory: Callable[[ProjectPaths], Any] = GuiInstanceCoordinator,
) -> None:
    instance = instance_factory(paths)
    if not instance.acquire_or_signal():
        return

    root: Any | None = None
    app: Any | None = None
    tray: Any | None = None
    activation_after: object | None = None
    closing = False
    tray_ready = False

    try:
        root = root_factory()
        root.title("Telegram 视频自动下载器")
        root.geometry("900x720")
        root.minsize(800, 620)
        make_controller = controller_factory or (
            lambda current_paths: GuiController(current_paths, TelethonGateway)
        )
        app = app_factory(root, make_controller(paths))

        def show_window() -> None:
            if closing:
                return
            root.deiconify()
            root.lift()
            root.focus_force()

        def report_error(error: Exception) -> None:
            show_window()
            messagebox.showerror("操作失败", app._safe_error(error), parent=root)

        def quit_ui() -> None:
            nonlocal closing
            if closing:
                return
            closing = True
            if activation_after is not None:
                try:
                    root.after_cancel(activation_after)
                except tk.TclError:
                    pass
            if tray is not None:
                tray.stop()
            app.close()
            root.destroy()

        actions = TrayActions(
            show_window=show_window,
            start_service=app._start_service,
            stop_service=app._stop_service,
            open_downloads=app.controller.open_downloads,
            open_logs=app.controller.open_logs,
            exit_ui=quit_ui,
            report_error=report_error,
        )
        tray = tray_factory(
            schedule=lambda callback: root.after(0, callback),
            actions=actions,
        )
        try:
            tray.start()
            tray_ready = True
            app.set_status_listener(tray.update)
            tray.update(app.controller.read_status())
        except Exception as error:
            messagebox.showerror(
                "托盘不可用",
                app._safe_error(error),
                parent=root,
            )

        def close_window() -> None:
            if tray_ready:
                root.withdraw()
            else:
                quit_ui()

        def poll_activation() -> None:
            nonlocal activation_after
            if closing:
                return
            if instance.activation_requested():
                show_window()
            activation_after = root.after(500, poll_activation)

        root.protocol("WM_DELETE_WINDOW", close_window)
        activation_after = root.after(500, poll_activation)
        root.mainloop()
    finally:
        if not closing:
            if tray is not None:
                tray.stop()
            if app is not None:
                app.close()
        instance.close()
```

- [ ] **Step 4: Route the CLI to the runtime module**

Change the GUI branch in `src/tg_video_downloader/cli.py`:

```python
    if args.command == "gui":
        from tg_video_downloader.gui.runtime import run_gui

        run_gui(paths)
        return 0
```

- [ ] **Step 5: Run runtime and GUI tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_runtime.py tests\test_gui_app.py tests\test_cli.py -q
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit runtime composition**

```powershell
git add src/tg_video_downloader/gui/app.py src/tg_video_downloader/gui/runtime.py src/tg_video_downloader/cli.py tests/test_gui_runtime.py tests/test_gui_app.py
git commit -m "feat: keep configurator in Windows tray"
```

### Task 6: Tray diagnostics and user documentation

**Files:**
- Modify: `src/tg_video_downloader/diagnostics.py:81-89,188-203`
- Modify: `tests/test_diagnostics.py:59-112,157-196`
- Modify: `README.md:26-66,117-141`

- [ ] **Step 1: Write failing tray diagnostic tests**

Update the expected key set in `test_doctor_runs_local_and_online_checks_and_saves_inside_project` to include:

```python
        "tray_icon",
```

Update the check count assertion in `test_project_path_failure_is_reported_without_aborting_other_checks`:

```python
    assert len(report.checks) == 12
```

Add:

```python
def test_dependency_check_includes_tray_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        "tg_video_downloader.diagnostics.version",
        lambda name: seen.append(name) or "1.0",
    )
    doctor = Doctor(
        ProjectPaths.from_root(tmp_path),
        gateway_factory=lambda *_: FakeTelegramGateway(),
    )

    assert doctor._check_dependencies().status == "pass"
    assert {"pillow", "pystray"}.issubset(seen)


def test_tray_icon_check_builds_an_in_memory_windows_icon(tmp_path: Path) -> None:
    doctor = Doctor(
        ProjectPaths.from_root(tmp_path),
        gateway_factory=lambda *_: FakeTelegramGateway(),
    )

    check = doctor._check_tray_icon()

    assert check.status == "pass"
    assert check.message == "Windows 托盘图标、菜单和默认动作可用"
```

- [ ] **Step 2: Run diagnostics tests to verify they fail**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_diagnostics.py -q
```

Expected: failures report the missing `tray_icon` check and missing package names.

- [ ] **Step 3: Implement the non-visual tray self-check**

Add this local check in `Doctor.run` after `qr_code`:

```python
            self._run_local("tray_icon", self._check_tray_icon),
```

Expand the dependency tuple:

```python
        for distribution in (
            "telethon",
            "cryptg",
            "tzdata",
            "qrcode",
            "pillow",
            "pystray",
        ):
```

Add after `_check_qr_code`:

```python
    def _check_tray_icon(self) -> DiagnosticCheck:
        import pystray

        from tg_video_downloader.gui.tray import RUNNING_COLOR, create_status_icon

        image = create_status_icon(RUNNING_COLOR)
        if image.mode != "RGBA" or image.size != (64, 64):
            return DiagnosticCheck("tray_icon", "fail", "托盘图标内存绘制失败")
        if not getattr(pystray.Icon, "HAS_MENU", False):
            return DiagnosticCheck("tray_icon", "fail", "Windows 托盘菜单不可用")
        if not getattr(pystray.Icon, "HAS_DEFAULT_ACTION", False):
            return DiagnosticCheck("tray_icon", "fail", "Windows 托盘默认动作不可用")
        return DiagnosticCheck(
            "tray_icon",
            "pass",
            "Windows 托盘图标、菜单和默认动作可用",
        )
```

- [ ] **Step 4: Run diagnostics tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_diagnostics.py -q
```

Expected: all diagnostic tests pass.

- [ ] **Step 5: Update README with exact tray behavior**

Make these concrete documentation changes:

```markdown
- 点击配置器右上角“×”会隐藏到 Windows 系统托盘，不会停止下载；双击托盘图标或再次双击 `打开配置器.cmd` 可恢复已有窗口。
- 托盘悬停显示当前文件、百分比和平均速度；绿色表示正常运行，黄色表示正在启动或心跳异常，红色表示需要登录、配置无效或后台错误，灰色表示后台已停止。
- 托盘菜单可启动/停止后台、打开下载目录、打开日志目录和退出托盘。“退出托盘”只关闭配置器；只有“停止后台”才停止下载服务。
```

Update the project-file list with:

```markdown
- `.runtime/gui.lock`、`.runtime/gui-activate.request`：配置器单实例和窗口唤醒控制文件，不包含账号或下载数据。
```

Correct the status refresh sentence from “每 5 秒” to the implemented “每 2 秒”. Add the tray fallback under “后台没有运行”: if no icon appears, open the configurator and run self-check rather than assuming the downloader stopped.

- [ ] **Step 6: Run focused documentation and diagnostics regressions**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_diagnostics.py tests\test_windows_scripts.py -q
```

Expected: all focused tests pass; scripts still keep runtime data inside the project and do not create startup tasks.

- [ ] **Step 7: Commit diagnostics and documentation**

```powershell
git add src/tg_video_downloader/diagnostics.py tests/test_diagnostics.py README.md
git commit -m "docs: explain Windows tray controls"
```

### Task 7: Full verification and real Windows acceptance

**Files:**
- Modify: `docs/verification.md`

- [ ] **Step 1: Run the complete automated verification**

Run:

```powershell
& .\scripts\check.ps1
```

Expected: pytest reaches `100%` with zero failures, compileall exits `0`, and path containment checks exit `0`.

- [ ] **Step 2: Verify the dependency resolver and imports**

Run:

```powershell
& .\.venv\Scripts\python.exe -c "from importlib.metadata import version; print(version('pillow')); print(version('pystray'))"
```

Expected: Pillow reports a `12.x` version and pystray reports `0.19.5`.

- [ ] **Step 3: Capture the live downloader baseline without changing it**

Run:

```powershell
$heartbeat = Get-Content -Raw -LiteralPath '.runtime\heartbeat.json' | ConvertFrom-Json
Get-Process -Id $heartbeat.pid
$heartbeat | Select-Object status,current_file,progress,counts,updated_at | Format-List
```

Expected: if the service is currently running, its PID exists and the heartbeat is fresh. Record the PID and downloaded byte count before GUI/tray testing.

- [ ] **Step 4: Perform the real Windows tray smoke test**

Run:

```powershell
& '.\打开配置器.cmd'
```

Then verify in the Windows notification area:

1. The icon color agrees with the “运行” page.
2. Hover text shows the same current file, percentage, and speed as the heartbeat.
3. Clicking “×” hides the window but leaves the icon.
4. “打开下载目录” opens the project `downloads` directory.
5. Double-clicking the icon restores and focuses the existing window.
6. Double-clicking `打开配置器.cmd` again restores the same window and does not create a second icon.
7. “停止后台” creates the normal stop request and eventually changes the icon to gray; “启动后台” clears the request and returns to a healthy state.
8. “退出托盘” removes only the tray/configurator process.

Expected: all eight observations pass. If any observation fails, stop acceptance, record the exact state, and use systematic debugging before changing code.

- [ ] **Step 5: Confirm tray exit did not stop or corrupt downloads**

If the acceptance test leaves the downloader running, wait for one new heartbeat refresh and run:

```powershell
$after = Get-Content -Raw -LiteralPath '.runtime\heartbeat.json' | ConvertFrom-Json
Get-Process -Id $after.pid
$after | Select-Object status,current_file,progress,counts,updated_at | Format-List
```

Expected: downloader PID is alive, heartbeat timestamp advanced, and current progress either advanced or moved safely to the next queued item. If acceptance intentionally leaves it stopped, expect `stopped` plus no active service PID and preserve the stop request.

- [ ] **Step 6: Record verification evidence**

Append a dated “Windows 系统托盘” section to `docs/verification.md` containing:

```markdown
## Windows 系统托盘（2026-08-25）

- 自动化：`scripts/check.ps1` 通过；记录实际 pytest 通过数量和耗时。
- 依赖：记录 Pillow 与 pystray 的实际版本。
- 托盘：记录颜色、悬停进度、隐藏/恢复、目录打开、重复启动、启动/停止和退出八项结果。
- 独立性：记录退出托盘前后的 downloader PID、心跳时间和进度，证明退出托盘没有停止或损坏下载。
- 数据位置：确认新增控制文件仅为项目内 `.runtime/gui.lock` 与 `.runtime/gui-activate.request`。
```

Replace “记录实际…” wording with the observed values before committing; do not leave template language in the finished verification document.

- [ ] **Step 7: Run final diff and repository checks**

Run:

```powershell
git diff --check
git status --short
git log --oneline -8
```

Expected: `git diff --check` has no output; status lists only `docs/verification.md` before its commit; recent commits correspond to the task commits above.

- [ ] **Step 8: Commit verification evidence**

```powershell
git add docs/verification.md
git commit -m "docs: verify Windows tray workflow"
```

- [ ] **Step 9: Re-run completion gate after the evidence commit**

Run:

```powershell
& .\scripts\check.ps1
git status --short
```

Expected: complete verification exits `0`, all tests pass, and `git status --short` is empty.
