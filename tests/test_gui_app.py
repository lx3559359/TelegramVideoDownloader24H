from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader.gateway import QrLoginExpiredError, TransientTelegramError
from tg_video_downloader.gui import app as app_module
from tg_video_downloader.gui.app import DownloaderApp


class FakeFrame:
    def __init__(self) -> None:
        self.visible = False

    def grid(self) -> None:
        self.visible = True

    def grid_remove(self) -> None:
        self.visible = False


class FakeButton:
    def __init__(self) -> None:
        self.text = ""
        self.states: list[str] = []

    def configure(self, **values: str) -> None:
        self.text = values["text"]

    def state(self, values: list[str]) -> None:
        self.states = values


class FakeVar:
    def __init__(self, value: str | float = "") -> None:
        self.value = value

    def get(self) -> str | float:
        return self.value

    def set(self, value: str | float) -> None:
        self.value = value


class FakeText:
    def __init__(self) -> None:
        self.value = ""

    def configure(self, **_values) -> None:
        return None

    def delete(self, *_args) -> None:
        self.value = ""

    def insert(self, _index: str, value: str) -> None:
        self.value = value


def test_phone_login_panel_toggles_without_tk_window() -> None:
    app = object.__new__(DownloaderApp)
    app.phone_login_visible = False
    app.phone_login_frame = FakeFrame()
    app.phone_toggle_button = FakeButton()

    app._toggle_phone_login()
    assert app.phone_login_frame.visible is True
    assert app.phone_toggle_button.text == "收起手机号验证码登录"

    app._toggle_phone_login()
    assert app.phone_login_frame.visible is False
    assert app.phone_toggle_button.text == "使用手机号验证码登录"


def test_safe_error_ignores_empty_phone_and_redacts_sensitive_fields() -> None:
    app = object.__new__(DownloaderApp)
    app.api_hash_var = FakeVar("secret-hash")
    app.phone_var = FakeVar("")
    app.code_var = FakeVar("123456")
    app.password_var = FakeVar("phone-password")
    app.qr_password_var = FakeVar("qr-password")

    message = app._safe_error(
        RuntimeError("secret-hash 123456 phone-password qr-password remains")
    )

    assert message == "*** *** *** *** remains"


def test_stale_qr_generation_is_ignored() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4

    assert app._is_current_qr_generation(4) is True
    assert app._is_current_qr_generation(3) is False


def test_saved_session_status_restores_authorized_account_without_qr() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4
    finished: list[str] = []
    app._finish_qr_login = finished.append

    app._handle_saved_session_status(True, 4)

    assert finished == ["登录成功"]


def test_saved_session_status_leaves_manual_login_available_when_unauthorized() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4
    finished: list[str] = []
    app._finish_qr_login = finished.append

    app._handle_saved_session_status(False, 4)

    assert finished == ["尚未登录"]


def test_saved_session_probe_error_keeps_session_and_shows_generic_status() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4
    app.account_status_var = FakeVar()
    finished: list[str] = []
    app._finish_qr_login = finished.append

    app._handle_saved_session_error(RuntimeError("private network detail"), 4)

    assert finished == ["尚未登录"]
    assert app.account_status_var.get() == "暂时无法检查已有会话，可稍后重试"


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


def test_history_column_enables_target_and_history() -> None:
    app = object.__new__(DownloaderApp)
    app._selected_ids = set()
    app._history_ids = set()
    app.group_tree = SimpleNamespace(
        identify_row=lambda _y: "-1001",
        identify_column=lambda _x: "#2",
    )
    app._render_groups = lambda: None

    app._toggle_group(SimpleNamespace(x=80, y=10))

    assert app._selected_ids == {-1001}
    assert app._history_ids == {-1001}


def test_turning_monitoring_off_also_turns_history_off() -> None:
    app = object.__new__(DownloaderApp)
    app._selected_ids = {-1001}
    app._history_ids = {-1001}
    app.group_tree = SimpleNamespace(
        identify_row=lambda _y: "-1001",
        identify_column=lambda _x: "#1",
    )
    app._render_groups = lambda: None

    app._toggle_group(SimpleNamespace(x=20, y=10))

    assert app._selected_ids == set()
    assert app._history_ids == set()


def test_format_progress_uses_binary_units() -> None:
    text, speed = app_module.format_download_progress(
        {
            "downloaded_bytes": 5 * 1024**2,
            "total_bytes": 10 * 1024**2,
            "percent": 50.0,
            "bytes_per_second": 2 * 1024**2,
            "resumed": True,
        }
    )

    assert text == "5.00 MiB / 10.00 MiB（50.0%，断点续传）"
    assert speed == "2.00 MiB/s"


@pytest.mark.parametrize("progress", [None, {}, {"downloaded_bytes": "bad"}])
def test_format_progress_tolerates_missing_or_malformed_data(progress) -> None:
    assert app_module.format_download_progress(progress) == ("-", "-")


@pytest.mark.parametrize(
    ("snapshot", "value", "label"),
    [
        ({"status": "running", "progress": {"percent": 0}}, 0.0, "0.0%"),
        (
            {"status": "running", "progress": {"percent": 52.34}},
            52.34,
            "52.3%",
        ),
        (
            {"status": "running", "progress": {"percent": 120}},
            100.0,
            "100.0%",
        ),
        (
            {"status": "running", "current_file": "x.mp4", "progress": {}},
            0.0,
            "正在准备",
        ),
        ({"status": "running"}, 0.0, "等待任务"),
        ({"status": "stopped"}, 0.0, "后台已停止"),
        (
            {"status": "stale", "progress": {"percent": 50}},
            50.0,
            "心跳异常",
        ),
    ],
)
def test_progress_bar_presentation(snapshot, value, label) -> None:
    assert app_module.progress_bar_presentation(snapshot) == (value, label)


@pytest.mark.parametrize("percent", [True, "50", -1, float("nan"), float("inf")])
def test_progress_bar_rejects_malformed_percent(percent) -> None:
    value, label = app_module.progress_bar_presentation(
        {
            "status": "running",
            "current_file": "x.mp4",
            "progress": {"percent": percent},
        }
    )

    assert value == 0.0
    assert label == "正在准备"


def test_choose_download_root_saves_and_displays_normalized_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = (tmp_path / "external").resolve()
    saved: list[Path] = []
    app = object.__new__(DownloaderApp)
    app.download_root_var = FakeVar(str(tmp_path))
    app._call_sync = lambda function: function()
    app.controller = SimpleNamespace(
        current_download_root=lambda: tmp_path,
        save_download_root=lambda value: saved.append(Path(value)) or selected,
    )
    monkeypatch.setattr(
        "tg_video_downloader.gui.app.filedialog.askdirectory",
        lambda **_kwargs: str(selected),
    )

    app._choose_download_root()

    assert saved == [selected]
    assert app.download_root_var.get() == str(selected)


def test_refresh_status_shows_progress_paused_history_and_group_policy() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    published: list[dict[str, object]] = []
    app._status_listener = published.append
    app.controller = SimpleNamespace(
        read_status=lambda: {
            "status": "running",
            "updated_at": "2026-08-25T12:00:00+00:00",
            "current_file": "video.mp4",
            "progress": {
                "downloaded_bytes": 5 * 1024**2,
                "total_bytes": 10 * 1024**2,
                "percent": 50.0,
                "bytes_per_second": 2 * 1024**2,
                "resumed": False,
            },
            "counts": {"paused_history": 3},
            "groups": [
                {
                    "chat_id": -1001,
                    "title": "频道",
                    "download_history": False,
                    "history_complete": False,
                    "access_error": None,
                }
            ],
        }
    )
    app.status_vars = {
        key: FakeVar()
        for key in (
            "status",
            "updated_at",
            "current_file",
            "download_progress",
            "download_speed",
            "pending_live",
            "pending_history",
            "paused_history",
            "completed",
            "retry_wait",
            "permanent_error",
            "last_error",
        )
    }
    app.progress_bar_var = FakeVar()
    app.progress_bar_label_var = FakeVar()
    app.group_status = FakeText()
    app.after = lambda *_args: "after-status"

    app._refresh_status()

    assert app.status_vars["download_progress"].get() == (
        "5.00 MiB / 10.00 MiB（50.0%）"
    )
    assert app.status_vars["download_speed"].get() == "2.00 MiB/s"
    assert app.progress_bar_var.get() == 50.0
    assert app.progress_bar_label_var.get() == "50.0%"
    assert app.status_vars["paused_history"].get() == "3"
    assert app.group_status.value == "频道：监听新内容；历史下载已暂停"
    assert published[0]["status"] == "running"


def test_status_read_error_is_published_for_tray_recovery() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app.controller = SimpleNamespace(
        read_status=lambda: (_ for _ in ()).throw(RuntimeError("heartbeat broken"))
    )
    app.status_vars = {"status": FakeVar()}
    app.progress_bar_var = FakeVar(37.5)
    app.progress_bar_label_var = FakeVar("37.5%")
    app.api_hash_var = FakeVar("")
    app.phone_var = FakeVar("")
    app.code_var = FakeVar("")
    app.password_var = FakeVar("")
    app.qr_password_var = FakeVar("")
    published: list[dict[str, object]] = []
    app._status_listener = published.append
    app.after = lambda *_args: "after-status"

    app._refresh_status()

    assert app.progress_bar_var.get() == 37.5
    assert app.progress_bar_label_var.get() == "状态读取失败"
    assert published == [{"status": "error", "error": "heartbeat broken"}]


def test_close_returns_immediately_after_first_cleanup() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = True

    app.close()


def test_status_listener_failure_does_not_corrupt_running_page() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app.controller = SimpleNamespace(
        read_status=lambda: {"status": "running", "counts": {}, "groups": []}
    )
    app.status_vars = {
        key: FakeVar()
        for key in (
            "status",
            "updated_at",
            "current_file",
            "download_progress",
            "download_speed",
            "pending_live",
            "pending_history",
            "paused_history",
            "completed",
            "retry_wait",
            "permanent_error",
            "last_error",
        )
    }
    app.progress_bar_var = FakeVar()
    app.progress_bar_label_var = FakeVar()
    app.group_status = FakeText()

    def fail(_snapshot: dict[str, object]) -> None:
        raise RuntimeError("tray update failed")

    app._status_listener = fail
    app.after = lambda *_args: "after-status"

    app._refresh_status()

    assert app.status_vars["status"].get() == "running"


def test_expired_qr_refreshes_current_generation() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4
    refreshed: list[int] = []
    app._refresh_qr_login = refreshed.append

    app._handle_qr_wait_error(QrLoginExpiredError("expired"), 4)

    assert refreshed == [4]


def test_transient_qr_error_schedules_server_retry_time() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4
    app._qr_retry_attempt = 0
    app._qr_retry_after = None
    app.account_status_var = FakeVar()
    scheduled: list[tuple[int, object]] = []
    app.after = lambda delay, callback: scheduled.append((delay, callback)) or "after-1"

    app._handle_qr_wait_error(
        TransientTelegramError("wait", retry_after=73),
        4,
    )

    assert app.account_status_var.get() == "等待网络恢复，73 秒后重试"
    assert app._qr_retry_attempt == 1
    assert app._qr_retry_after == "after-1"
    assert scheduled[0][0] == 73_000


def test_qr_password_error_is_redacted_before_password_is_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4
    app.api_hash_var = FakeVar("")
    app.phone_var = FakeVar("")
    app.code_var = FakeVar("")
    app.password_var = FakeVar("")
    app.qr_password_var = FakeVar("private-password")
    app.account_status_var = FakeVar()
    app.qr_password_button = FakeButton()
    shown: list[str] = []
    monkeypatch.setattr(
        "tg_video_downloader.gui.app.messagebox.showerror",
        lambda _title, message: shown.append(message),
    )

    app._handle_qr_password_error(
        RuntimeError("private-password was rejected"),
        4,
    )

    assert shown == ["*** was rejected"]
    assert app.qr_password_var.get() == ""
