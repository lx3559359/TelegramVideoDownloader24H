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
