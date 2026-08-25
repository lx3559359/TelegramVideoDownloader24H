import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader.gateway import AuthenticationRequiredError, QrLoginChallenge
from tg_video_downloader.diagnostics import DiagnosticCheck, DiagnosticReport
from tg_video_downloader.gui.app import format_doctor_summary
from tg_video_downloader.gui.controller import GuiController
from tg_video_downloader.models import Credentials, GroupTarget
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.update import UpdateResult, write_update_result


class LoginGateway:
    def __init__(self) -> None:
        self.connected = False
        self.authorized = True
        self.sent_phone = None
        self.login_calls = []
        self.groups = (GroupTarget(-1001, "A 群"), GroupTarget(-1002, "B 群"))
        self.challenge = QrLoginChallenge(
            "tg://login?token=first",
            datetime(2026, 8, 25, 1, 0, tzinfo=UTC),
        )
        self.refreshed_challenge = QrLoginChallenge(
            "tg://login?token=second",
            datetime(2026, 8, 25, 1, 1, tzinfo=UTC),
        )
        self.password_required = False
        self.passwords: list[str] = []
        self.logged_out = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def is_authorized(self) -> bool:
        return self.authorized

    async def send_login_code(self, phone: str) -> None:
        self.sent_phone = phone

    async def complete_login(self, phone: str, code: str, password=None) -> None:
        self.login_calls.append((phone, code, password))
        if password is None:
            raise AuthenticationRequiredError("需要二步验证密码")

    async def start_qr_login(self) -> QrLoginChallenge:
        return self.challenge

    async def refresh_qr_login(self) -> QrLoginChallenge:
        return self.refreshed_challenge

    async def wait_qr_login(self) -> None:
        if self.password_required:
            raise AuthenticationRequiredError("需要二步验证密码")
        self.authorized = True

    async def complete_password(self, password: str) -> None:
        self.passwords.append(password)
        self.authorized = True

    async def log_out(self) -> None:
        self.logged_out = True
        self.authorized = False

    async def list_groups(self) -> tuple[GroupTarget, ...]:
        return self.groups


class FakeProcessControl:
    def __init__(self) -> None:
        self.actions = []

    def clear_stop(self, paths: ProjectPaths) -> None:
        self.actions.append("clear")

    def start(self, project_root: Path):
        self.actions.append(("start", project_root))
        return object()

    def request_stop(self, paths: ProjectPaths) -> None:
        self.actions.append("stop")


def make_controller(tmp_path: Path):
    paths = ProjectPaths.from_root(tmp_path)
    gateway = LoginGateway()
    process = FakeProcessControl()
    controller = GuiController(
        paths,
        lambda *_: gateway,
        process_control=process,
    )
    return controller, paths, gateway, process


def test_save_credentials_and_selected_groups(tmp_path: Path) -> None:
    controller, _, _, _ = make_controller(tmp_path)
    credentials = Credentials(12345, "secret-hash", "+8613800000000")

    controller.save_credentials(credentials)
    controller.save_selected_groups((GroupTarget(-1001, "选中群"),))

    assert controller.load_credentials() == credentials
    assert controller.config_store.load_config().groups == (
        GroupTarget(-1001, "选中群"),
    )
    assert controller.selected_chat_ids() == {-1001}

    with pytest.raises(ValueError, match="至少选择一个群"):
        controller.save_selected_groups(())


def test_controller_preserves_selected_history_policy(tmp_path: Path) -> None:
    controller, _, _, _ = make_controller(tmp_path)
    groups = (
        GroupTarget(-1001, "只监听新内容", False),
        GroupTarget(-1002, "包含历史", True),
    )

    controller.save_selected_groups(groups)

    assert controller.selected_groups() == groups
    assert controller.selected_chat_ids() == {-1001, -1002}


def test_save_download_root_preserves_groups(tmp_path: Path) -> None:
    controller, _, _, _ = make_controller(tmp_path / "project")
    groups = (GroupTarget(-1001, "群", False),)
    controller.save_selected_groups(groups)

    selected = controller.save_download_root(tmp_path / "external")
    config = controller.config_store.load_config()

    assert selected == (tmp_path / "external").resolve()
    assert config.groups == groups
    assert config.download_root == selected


def test_save_groups_preserves_download_root(tmp_path: Path) -> None:
    controller, _, _, _ = make_controller(tmp_path / "project")
    selected = controller.save_download_root(tmp_path / "external")
    groups = (GroupTarget(-1001, "群", True),)

    controller.save_selected_groups(groups)

    config = controller.config_store.load_config()
    assert config.groups == groups
    assert config.download_root == selected


def test_open_downloads_uses_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _, _, _ = make_controller(tmp_path / "project")
    selected = controller.save_download_root(tmp_path / "external")
    opened: list[Path] = []
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.os.startfile",
        lambda path: opened.append(Path(path)),
    )

    controller.open_downloads()

    assert opened == [selected]


def test_current_download_root_defaults_to_project_downloads(tmp_path: Path) -> None:
    controller, paths, _, _ = make_controller(tmp_path)

    assert controller.current_download_root() == paths.downloads


@pytest.mark.asyncio
async def test_prepare_update_stops_only_a_previously_running_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, paths, _, process = make_controller(tmp_path)
    prepared = SimpleNamespace(tag="v0.2.0")
    installs: list[tuple[object, bool]] = []
    controller.update_manager = SimpleNamespace(
        validate_prepared=lambda _value: None,
        validate_install_environment=lambda: None,
        prepare_install=lambda value, restore: installs.append((value, restore)),
    )
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.downloader_is_running",
        lambda _paths: True,
    )
    waited: list[Path] = []
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.wait_for_downloader_stop",
        lambda value, timeout_seconds=30: waited.append(value),
    )

    await controller.prepare_update_install(prepared)

    assert process.actions == ["stop"]
    assert waited == [paths]
    assert installs == [(prepared, True)]


@pytest.mark.asyncio
async def test_prepare_update_does_not_stop_an_already_stopped_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _, _, process = make_controller(tmp_path)
    prepared = SimpleNamespace(tag="v0.2.0")
    installs: list[tuple[object, bool]] = []
    controller.update_manager = SimpleNamespace(
        validate_prepared=lambda _value: None,
        validate_install_environment=lambda: None,
        prepare_install=lambda value, restore: installs.append((value, restore)),
    )
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.downloader_is_running",
        lambda _paths: False,
    )

    await controller.prepare_update_install(prepared)

    assert process.actions == []
    assert installs == [(prepared, False)]


@pytest.mark.asyncio
async def test_prepare_update_rejects_active_login_before_service_control(
    tmp_path: Path,
) -> None:
    controller, _, gateway, process = make_controller(tmp_path)
    controller._login_gateway = gateway
    controller.update_manager = SimpleNamespace(
        validate_prepared=lambda _value: (_ for _ in ()).throw(
            AssertionError("must not validate during login")
        ),
        validate_install_environment=lambda: None,
    )

    with pytest.raises(ValueError, match="登录"):
        await controller.prepare_update_install(SimpleNamespace(tag="v0.2.0"))

    assert process.actions == []


def test_update_result_is_consumed_only_once(tmp_path: Path) -> None:
    controller, paths, _, _ = make_controller(tmp_path)
    result = UpdateResult(
        token="a" * 32,
        tag="v0.2.0",
        status="success",
        message="更新完成",
        completed_at="2026-08-26T12:00:00+00:00",
    )
    write_update_result(paths, result)

    assert controller.consume_update_result() == result
    assert controller.consume_update_result() is None


@pytest.mark.asyncio
async def test_update_preparation_finishes_transaction_after_gui_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _, _, process = make_controller(tmp_path)
    validation_started = threading.Event()
    allow_validation = threading.Event()
    install_finished = threading.Event()
    installs: list[tuple[object, bool]] = []
    prepared = SimpleNamespace(tag="v0.2.0")

    def validate(_value: object) -> None:
        validation_started.set()
        assert allow_validation.wait(timeout=5)

    def install(value: object, restore: bool) -> None:
        installs.append((value, restore))
        install_finished.set()

    controller.update_manager = SimpleNamespace(
        validate_prepared=validate,
        validate_install_environment=lambda: None,
        prepare_install=install,
    )
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.downloader_is_running",
        lambda _paths: False,
    )
    task = asyncio.create_task(controller.prepare_update_install(prepared))
    assert await asyncio.to_thread(validation_started.wait, 5)

    task.cancel()
    allow_validation.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(install_finished.wait, 5)

    assert installs == [(prepared, False)]
    assert process.actions == []


@pytest.mark.asyncio
async def test_missing_update_environment_does_not_stop_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _, _, process = make_controller(tmp_path)
    controller.update_manager = SimpleNamespace(
        validate_prepared=lambda _value: None,
        validate_install_environment=lambda: (_ for _ in ()).throw(
            RuntimeError("PowerShell missing")
        ),
    )
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.downloader_is_running",
        lambda _paths: (_ for _ in ()).throw(
            AssertionError("must not inspect service before environment validation")
        ),
    )

    with pytest.raises(RuntimeError, match="PowerShell missing"):
        await controller.prepare_update_install(SimpleNamespace(tag="v0.2.0"))

    assert process.actions == []


@pytest.mark.asyncio
async def test_login_flow_and_group_listing(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    credentials = Credentials(12345, "secret-hash", "+8613800000000")

    await controller.send_code(credentials)
    assert gateway.sent_phone == credentials.phone
    assert await controller.complete_login("123456", "") == "需要二步验证密码"
    assert await controller.complete_login("123456", "two-factor") == "登录成功"
    assert await controller.list_groups() == gateway.groups

    assert not hasattr(controller, "code")
    assert not hasattr(controller, "password")


@pytest.mark.asyncio
async def test_phone_login_still_requires_phone(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)

    with pytest.raises(ValueError, match="手机号"):
        await controller.send_code(Credentials(12345, "secret-hash"))

    assert gateway.connected is False
    assert gateway.sent_phone is None


@pytest.mark.asyncio
async def test_qr_login_reuses_authorized_session(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    gateway.authorized = True

    challenge = await controller.start_qr_login(Credentials(12345, "hash"))

    assert challenge is None
    assert gateway.connected is False
    assert controller.login_active is False


@pytest.mark.asyncio
async def test_saved_session_probe_reuses_authorization_without_starting_login(
    tmp_path: Path,
) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    controller.save_credentials(Credentials(12345, "hash"))
    gateway.authorized = True

    assert await controller.saved_session_authorized() is True
    assert gateway.connected is False
    assert controller.login_active is False


@pytest.mark.asyncio
async def test_qr_login_refresh_password_and_cleanup(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    gateway.authorized = False

    first = await controller.start_qr_login(Credentials(12345, "hash"))
    refreshed = await controller.refresh_qr_login()
    gateway.password_required = True

    assert first == gateway.challenge
    assert refreshed == gateway.refreshed_challenge
    assert await controller.wait_qr_login() == "需要二步验证密码"
    assert await controller.complete_qr_password("two-factor") == "登录成功"
    assert gateway.passwords == ["two-factor"]
    assert gateway.connected is False
    assert controller.login_active is False


@pytest.mark.asyncio
async def test_cancel_login_is_idempotent(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    gateway.authorized = False
    await controller.start_qr_login(Credentials(12345, "hash"))

    await controller.cancel_login()
    await controller.cancel_login()

    assert controller.login_active is False
    assert gateway.connected is False


@pytest.mark.asyncio
async def test_cancelled_qr_start_disconnects_unregistered_gateway(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()

    class SlowConnectGateway(LoginGateway):
        async def connect(self) -> None:
            self.connected = True
            entered.set()
            await asyncio.Event().wait()

    paths = ProjectPaths.from_root(tmp_path)
    gateway = SlowConnectGateway()
    controller = GuiController(paths, lambda *_: gateway)
    task = asyncio.create_task(
        controller.start_qr_login(Credentials(12345, "hash"))
    )
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert gateway.connected is False
    assert controller.login_active is False


@pytest.mark.asyncio
async def test_cancelled_phone_code_request_disconnects_unregistered_gateway(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()

    class SlowCodeGateway(LoginGateway):
        async def send_login_code(self, phone: str) -> None:
            entered.set()
            await asyncio.Event().wait()

    paths = ProjectPaths.from_root(tmp_path)
    gateway = SlowCodeGateway()
    controller = GuiController(paths, lambda *_: gateway)
    task = asyncio.create_task(
        controller.send_code(Credentials(12345, "hash", "+8613800000000"))
    )
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert gateway.connected is False
    assert controller.login_active is False


@pytest.mark.asyncio
async def test_logout_releases_login_gateway(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    controller.save_credentials(Credentials(12345, "hash"))
    gateway.authorized = True

    assert await controller.log_out() == "已退出当前账号"
    assert gateway.logged_out is True
    assert gateway.connected is False
    assert controller.login_active is False


def test_start_stop_and_missing_heartbeat(tmp_path: Path) -> None:
    controller, paths, _, process = make_controller(tmp_path)
    controller.save_credentials(Credentials(12345, "hash", "+8613800000000"))
    controller.save_selected_groups((GroupTarget(-1001, "群"),))

    controller.start()
    controller.stop()

    assert process.actions == ["clear", ("start", paths.root), "stop"]
    assert controller.read_status() == {"status": "stopped"}


def test_stale_running_heartbeat_is_not_reported_as_healthy(tmp_path: Path) -> None:
    controller, paths, _, _ = make_controller(tmp_path)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    HeartbeatWriter(paths.heartbeat).write(
        {
            "status": "running",
            "updated_at": (now - timedelta(seconds=16)).isoformat(),
        }
    )

    snapshot = controller.read_status(now=now)

    assert snapshot["status"] == "stale"
    assert snapshot["reported_status"] == "running"
    assert "心跳" in str(snapshot["error"])


@pytest.mark.asyncio
async def test_run_doctor_returns_report_and_project_local_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, paths, _, _ = make_controller(tmp_path)
    report = DiagnosticReport(
        generated_at="2026-08-24T12:00:00+00:00",
        checks=(DiagnosticCheck("paths", "pass", "ok"),),
    )

    class FakeDoctor:
        def __init__(self, doctor_paths, gateway_factory, *, login_active) -> None:
            assert doctor_paths is paths
            assert login_active() is False

        async def run(self) -> DiagnosticReport:
            return report

        def save(self, value: DiagnosticReport) -> Path:
            assert value is report
            return paths.logs / "diagnostics" / "doctor.json"

    monkeypatch.setattr("tg_video_downloader.gui.controller.Doctor", FakeDoctor)

    result, saved = await controller.run_doctor()

    assert result is report
    assert saved.resolve().is_relative_to(paths.root)


def test_format_doctor_summary_includes_all_outcome_counts() -> None:
    report = DiagnosticReport(
        generated_at="2026-08-24T12:00:00+00:00",
        checks=(
            DiagnosticCheck("a", "pass", "ok"),
            DiagnosticCheck("b", "warning", "warn"),
            DiagnosticCheck("c", "fail", "bad"),
        ),
    )

    summary = format_doctor_summary(report, Path("logs/diagnostics/doctor.json"))

    assert "通过：1" in summary
    assert "警告：1" in summary
    assert "失败：1" in summary
    assert "doctor.json" in summary
