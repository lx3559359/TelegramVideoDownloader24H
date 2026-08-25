from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from threading import Event, RLock
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


@dataclass(frozen=True)
class TrayActions:
    show_window: Callable[[], None]
    start_service: Callable[[], None]
    stop_service: Callable[[], None]
    open_downloads: Callable[[], None]
    open_logs: Callable[[], None]
    check_update: Callable[[], None]
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
            pystray.MenuItem("检查更新", self._dispatch(self._actions.check_update)),
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
