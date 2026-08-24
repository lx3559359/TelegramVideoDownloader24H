from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tg_video_downloader.gateway import AuthenticationRequiredError
from tg_video_downloader.diagnostics import DiagnosticCheck, DiagnosticReport
from tg_video_downloader.gui.app import format_doctor_summary
from tg_video_downloader.gui.controller import GuiController
from tg_video_downloader.models import Credentials, GroupTarget
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths


class LoginGateway:
    def __init__(self) -> None:
        self.connected = False
        self.sent_phone = None
        self.login_calls = []
        self.groups = (GroupTarget(-1001, "A 群"), GroupTarget(-1002, "B 群"))

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def is_authorized(self) -> bool:
        return True

    async def send_login_code(self, phone: str) -> None:
        self.sent_phone = phone

    async def complete_login(self, phone: str, code: str, password=None) -> None:
        self.login_calls.append((phone, code, password))
        if password is None:
            raise AuthenticationRequiredError("需要二步验证密码")

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
        def __init__(self, doctor_paths, gateway_factory) -> None:
            assert doctor_paths is paths

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
