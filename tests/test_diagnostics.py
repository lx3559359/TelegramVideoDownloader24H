import json
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
    assert json.loads(saved.read_text(encoding="utf-8"))["exit_code"] == 0
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
