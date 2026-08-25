import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.gateway import AuthenticationRequiredError
from tg_video_downloader.models import AppConfig, Credentials, GroupTarget
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.service import DownloaderService
from tg_video_downloader.state import StateStore
from tg_video_downloader.windows import request_stop
from tg_video_downloader.worker import DownloadProgress
from tests.fakes import FakeTelegramGateway


def configure(
    tmp_path: Path,
    groups: tuple[GroupTarget, ...],
    *,
    poll_seconds: int = 1,
) -> tuple[ProjectPaths, ConfigStore]:
    paths = ProjectPaths.from_root(tmp_path)
    store = ConfigStore(paths)
    store.save_credentials(Credentials(12345, "secret-hash", "+8613800000000"))
    store.save_config(AppConfig(groups=groups, config_poll_seconds=poll_seconds))
    return paths, store


@pytest.mark.asyncio
async def test_empty_whitelist_writes_needs_config_before_connecting(tmp_path: Path) -> None:
    paths, _ = configure(tmp_path, ())
    calls = 0

    def factory(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeTelegramGateway()

    assert await DownloaderService(paths, factory).run() == 2
    assert calls == 0
    heartbeat = HeartbeatWriter(paths.heartbeat).read()
    assert heartbeat["status"] == "needs_config"
    assert "至少选择一个群" in str(heartbeat["error"])


@pytest.mark.asyncio
async def test_stop_flag_stops_service_and_disconnects(tmp_path: Path) -> None:
    paths, _ = configure(tmp_path, (GroupTarget(-1001, "群"),))
    gateway = FakeTelegramGateway()
    service_task = asyncio.create_task(
        DownloaderService(paths, lambda *_: gateway).run()
    )
    await _wait_until(lambda: gateway.connected)

    request_stop(paths)

    assert await asyncio.wait_for(service_task, timeout=4) == 0
    assert gateway.connected is False
    assert HeartbeatWriter(paths.heartbeat).read()["status"] == "stopped"


@pytest.mark.asyncio
async def test_hot_reload_applies_valid_config_and_ignores_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = GroupTarget(-1001, "A 群")
    second = GroupTarget(-1002, "B 群")
    paths, config_store = configure(tmp_path, (first,))
    gateway = FakeTelegramGateway()
    applied: list[tuple[GroupTarget, ...]] = []
    catchups_started = asyncio.Event()

    class RecordingCoordinator:
        def __init__(self, state, telegram_gateway) -> None:
            pass

        async def start(self, targets) -> None:
            applied.append(targets)

        async def apply_targets(self, targets):
            applied.append(targets)
            return set(), set()

        async def run_scans(self, stop: asyncio.Event) -> None:
            await stop.wait()

        async def run_catchups(self, stop: asyncio.Event) -> None:
            catchups_started.set()
            await stop.wait()

    class WaitingWorker:
        current_file = None

        def __init__(self, paths, state, telegram_gateway) -> None:
            pass

        def recover(self) -> int:
            return 0

        async def run(self, stop: asyncio.Event) -> None:
            await stop.wait()

    monkeypatch.setattr("tg_video_downloader.service.ScannerCoordinator", RecordingCoordinator)
    monkeypatch.setattr("tg_video_downloader.service.DownloadWorker", WaitingWorker)
    service_task = asyncio.create_task(
        DownloaderService(paths, lambda *_: gateway).run()
    )
    await _wait_until(lambda: applied == [(first,)] and catchups_started.is_set())

    config_store.save_config(AppConfig(groups=(first, second), config_poll_seconds=1))
    await _wait_until(lambda: applied[-1] == (first, second), timeout=3)
    calls_after_valid = list(applied)

    paths.config.write_text("[[groups]\n", encoding="utf-8")
    await asyncio.sleep(1.2)
    assert applied == calls_after_valid

    request_stop(paths)
    assert await asyncio.wait_for(service_task, timeout=4) == 0


@pytest.mark.asyncio
async def test_authentication_error_writes_needs_login_once(tmp_path: Path) -> None:
    paths, _ = configure(tmp_path, (GroupTarget(-1001, "群"),))

    class AuthenticationFailureGateway(FakeTelegramGateway):
        def __init__(self) -> None:
            super().__init__()
            self.connect_calls = 0

        async def connect(self) -> None:
            self.connect_calls += 1
            raise AuthenticationRequiredError("expired")

    gateway = AuthenticationFailureGateway()

    assert await DownloaderService(paths, lambda *_: gateway).run() == 0
    heartbeat = HeartbeatWriter(paths.heartbeat).read()
    assert heartbeat["status"] == "needs_login"
    assert gateway.connect_calls == 1


def test_running_snapshot_includes_current_file(tmp_path: Path) -> None:
    paths, _ = configure(tmp_path, (GroupTarget(-1001, "群"),))
    state = StateStore(paths.database)
    service = DownloaderService(paths, lambda *_: FakeTelegramGateway())
    worker = SimpleNamespace(current_file="7_video.mp4")
    try:
        snapshot = service._snapshot("running", state, worker=worker)
        assert snapshot["current_file"] == "7_video.mp4"
    finally:
        state.close()


def test_snapshot_includes_download_progress_and_history_policy(
    tmp_path: Path,
) -> None:
    paths, _ = configure(tmp_path, (GroupTarget(-1001, "群", False),))
    state = StateStore(paths.database)
    state.reconcile_targets((GroupTarget(-1001, "群", False),))
    service = DownloaderService(paths, lambda *_: FakeTelegramGateway())
    worker = SimpleNamespace(
        current_file="video.mp4",
        progress=DownloadProgress(
            "video.mp4",
            5 * 1024**2,
            10 * 1024**2,
            50.0,
            2 * 1024**2,
            True,
        ),
    )
    try:
        snapshot = service._snapshot("running", state, worker=worker)

        assert snapshot["progress"]["downloaded_bytes"] == 5 * 1024**2
        assert snapshot["progress"]["percent"] == 50.0
        assert snapshot["groups"][0]["download_history"] is False
    finally:
        state.close()


async def _wait_until(predicate, timeout: float = 2) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=timeout)
