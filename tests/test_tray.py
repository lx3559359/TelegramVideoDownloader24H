from dataclasses import replace
from math import nan

import pytest

from tg_video_downloader.gui.tray import (
    ERROR_COLOR,
    RUNNING_COLOR,
    STARTING_COLOR,
    STOPPED_COLOR,
    TrayActions,
    TrayController,
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
        check_update=lambda: calls.append("update"),
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
        ("check_update", "update"),
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


def test_menu_contains_manual_update_action() -> None:
    controller = TrayController(
        schedule=lambda _callback: None,
        actions=make_actions([], []),
        icon_factory=FakeIcon,
    )

    controller.start()

    assert "检查更新" in str(FakeIcon.latest.menu)


def test_action_failure_notifies_and_reports_without_stopping_tray() -> None:
    scheduled: list[object] = []
    errors: list[Exception] = []

    def fail() -> None:
        raise RuntimeError("cannot open")

    actions = replace(make_actions([], errors), open_downloads=fail)
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
