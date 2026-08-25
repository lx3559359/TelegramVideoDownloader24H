import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.diagnostics import Doctor
from tg_video_downloader.gateway import TransientTelegramError
from tg_video_downloader.models import AppConfig, Credentials, GroupTarget
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.state import StateStore
from tests.fakes import FakeTelegramGateway


def configure_valid_project(tmp_path: Path) -> tuple[ProjectPaths, Credentials, GroupTarget]:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    credentials = Credentials(12345, "secret-hash", "+8613800000000")
    group = GroupTarget(-1001, "诊断群")
    store = ConfigStore(paths)
    store.save_credentials(credentials)
    store.save_config(AppConfig(groups=(group,)))
    state = StateStore(paths.database)
    state.close()
    HeartbeatWriter(paths.heartbeat).write(
        {"status": "stopped", "updated_at": datetime.now(UTC).isoformat()}
    )
    return paths, credentials, group


def test_diagnostics_module_loads_when_qrcode_import_is_unavailable() -> None:
    probe = """
import sys

class BlockQrcode:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "qrcode" or fullname.startswith("qrcode."):
            raise ModuleNotFoundError("blocked qrcode")
        return None

sys.meta_path.insert(0, BlockQrcode())
import tg_video_downloader.diagnostics
"""

    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_dependency_check_includes_cryptg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_version(name: str) -> str:
        seen.append(name)
        return "1.0"

    monkeypatch.setattr("tg_video_downloader.diagnostics.version", fake_version)
    doctor = Doctor(
        ProjectPaths.from_root(tmp_path),
        gateway_factory=lambda *_: FakeTelegramGateway(),
    )

    assert doctor._check_dependencies().status == "pass"
    assert "cryptg" in seen


@pytest.mark.asyncio
async def test_doctor_runs_local_and_online_checks_and_saves_inside_project(
    tmp_path: Path,
) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    gateway = FakeTelegramGateway({group.chat_id: []})
    doctor = Doctor(paths, gateway_factory=lambda *_: gateway)

    report = await doctor.run()
    saved = doctor.save(report)

    assert {item.key for item in report.checks} == {
        "project_paths",
        "python",
        "dependencies",
        "qr_code",
        "login_task",
        "config",
        "credentials",
        "disk",
        "database",
        "heartbeat",
        "telegram",
    }
    assert report.exit_code == 0
    assert saved.resolve().is_relative_to(paths.root)
    assert saved.parent == paths.logs / "diagnostics"
    serialized = saved.read_text(encoding="utf-8")
    assert json.loads(serialized)["exit_code"] == 0
    assert group.title not in serialized
    assert str(group.chat_id) not in serialized
    assert "doctor-probe" not in serialized
    assert "tg://login" not in serialized
    assert not saved.with_suffix(saved.suffix + ".tmp").exists()
    assert gateway.connected is False


@pytest.mark.asyncio
async def test_doctor_redacts_credentials_from_errors_and_saved_report(
    tmp_path: Path,
) -> None:
    paths, credentials, _ = configure_valid_project(tmp_path)

    class SecretFailureGateway(FakeTelegramGateway):
        async def connect(self) -> None:
            raise TransientTelegramError(
                f"network {credentials.api_hash} {credentials.phone}"
            )

    doctor = Doctor(paths, gateway_factory=lambda *_: SecretFailureGateway())

    report = await doctor.run()
    saved = doctor.save(report)
    serialized = saved.read_text(encoding="utf-8")

    assert report.exit_code == 1
    assert credentials.api_hash not in serialized
    assert credentials.phone not in serialized
    assert "***" in serialized


@pytest.mark.asyncio
async def test_database_failure_does_not_stop_remaining_checks(tmp_path: Path) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    paths.database.write_bytes(b"not a sqlite database")
    doctor = Doctor(
        paths,
        gateway_factory=lambda *_: FakeTelegramGateway({group.chat_id: []}),
    )

    report = await doctor.run()
    checks = {item.key: item for item in report.checks}

    assert checks["database"].status == "fail"
    assert checks["telegram"].status == "pass"
    assert report.exit_code == 2


@pytest.mark.asyncio
async def test_project_path_failure_is_reported_without_aborting_other_checks(
    tmp_path: Path,
) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    paths.downloads.rmdir()
    paths.downloads.write_text("occupied", encoding="utf-8")
    doctor = Doctor(
        paths,
        gateway_factory=lambda *_: FakeTelegramGateway({group.chat_id: []}),
    )

    report = await doctor.run()
    checks = {item.key: item for item in report.checks}

    assert checks["project_paths"].status == "fail"
    assert checks["telegram"].status == "pass"
    assert len(report.checks) == 11


@pytest.mark.asyncio
async def test_doctor_accepts_empty_phone_and_checks_qr_component(
    tmp_path: Path,
) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    ConfigStore(paths).save_credentials(Credentials(12345, "secret-hash"))
    doctor = Doctor(
        paths,
        gateway_factory=lambda *_: FakeTelegramGateway({group.chat_id: []}),
        login_active=lambda: False,
    )

    report = await doctor.run()
    checks = {item.key: item for item in report.checks}

    assert checks["credentials"].status == "pass"
    assert checks["qr_code"].status == "pass"
    assert checks["qr_code"].message == "二维码组件可用且无需图片文件"
    assert checks["login_task"].status == "pass"
    assert checks["login_task"].message == "没有未清理的登录任务"


@pytest.mark.asyncio
async def test_doctor_warns_when_login_is_active_without_redaction_corruption(
    tmp_path: Path,
) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    ConfigStore(paths).save_credentials(Credentials(12345, "secret-hash"))
    doctor = Doctor(
        paths,
        gateway_factory=lambda *_: FakeTelegramGateway({group.chat_id: []}),
        login_active=lambda: True,
    )

    report = await doctor.run()
    login_task = next(item for item in report.checks if item.key == "login_task")

    assert login_task.status == "warning"
    assert login_task.message == "图形界面存在进行中的登录任务"


@pytest.mark.asyncio
async def test_running_heartbeat_with_stop_request_is_a_warning(tmp_path: Path) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    HeartbeatWriter(paths.heartbeat).write(
        {"status": "running", "updated_at": datetime.now(UTC).isoformat()}
    )
    paths.stop_flag.write_text("stop\n", encoding="ascii")
    doctor = Doctor(
        paths,
        gateway_factory=lambda *_: FakeTelegramGateway({group.chat_id: []}),
    )

    report = await doctor.run()
    heartbeat = next(item for item in report.checks if item.key == "heartbeat")

    assert heartbeat.status == "warning"
    assert "停止" in heartbeat.message


@pytest.mark.asyncio
async def test_invisible_whitelisted_group_fails_online_check(tmp_path: Path) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    doctor = Doctor(paths, gateway_factory=lambda *_: FakeTelegramGateway())

    report = await doctor.run()
    telegram = next(item for item in report.checks if item.key == "telegram")

    assert telegram.status == "fail"
    assert "1 个白名单目标" in telegram.message
    assert group.title not in telegram.message
    assert str(group.chat_id) not in telegram.message
