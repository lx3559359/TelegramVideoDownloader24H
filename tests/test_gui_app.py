from tg_video_downloader.gateway import QrLoginExpiredError, TransientTelegramError
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

    def configure(self, **values: str) -> None:
        self.text = values["text"]


class FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
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
