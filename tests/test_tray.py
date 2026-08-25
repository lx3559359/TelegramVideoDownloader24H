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
