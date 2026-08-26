import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.gateway import AuthenticationRequiredError
from tg_video_downloader.models import (
    AppConfig,
    Credentials,
    GroupTarget,
    JobSource,
    MessageInfo,
    VideoSearchResult,
)
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.service import DownloaderService
from tg_video_downloader.search_ipc import (
    SearchChannelError,
    SearchIpcClient,
    SearchRequest,
)
from tg_video_downloader.selective import SearchQueueState
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


def _install_quiet_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    class QuietCoordinator:
        def __init__(self, state, telegram_gateway) -> None:
            pass

        async def start(self, targets) -> None:
            return None

        async def apply_targets(self, targets):
            return set(), set()

        async def run_scans(self, stop: asyncio.Event) -> None:
            await stop.wait()

        async def run_catchups(self, stop: asyncio.Event) -> None:
            await stop.wait()

    class QuietWorker:
        current_file = None
        progress = None

        def __init__(self, paths, state, telegram_gateway, **_kwargs) -> None:
            pass

        def recover(self) -> int:
            return 0

        async def run(self, stop: asyncio.Event) -> None:
            await stop.wait()

    monkeypatch.setattr(
        "tg_video_downloader.service.ScannerCoordinator",
        QuietCoordinator,
    )
    monkeypatch.setattr("tg_video_downloader.service.DownloadWorker", QuietWorker)


def _video(message_id: int) -> MessageInfo:
    return MessageInfo(
        chat_id=-1001,
        message_id=message_id,
        date=datetime(2026, 8, 26, tzinfo=UTC),
        mime_type="video/mp4",
        original_name=f"video-{message_id}.mp4",
        extension=".mp4",
        size=1000 + message_id,
        is_video=True,
        is_animated=False,
        is_round=False,
    )


def _search_request(chat_id: int = -1001) -> SearchRequest:
    return SearchRequest(
        chat_id=chat_id,
        keyword="课程",
        start_utc=None,
        end_utc=None,
        limit=20,
    )


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
async def test_service_search_reuses_gateway_and_returns_queue_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_quiet_runtime(monkeypatch)
    group = GroupTarget(-1001, "课程群")
    paths, _ = configure(tmp_path, (group,))
    queued = _video(1)
    completed = _video(2)
    available = _video(3)
    state = StateStore(paths.database)
    state.reconcile_targets((group,))
    state.upsert_job(queued, group.title, JobSource.LIVE)
    state.upsert_job(completed, group.title, JobSource.LIVE)
    completed_job = state.get_job(completed.chat_id, completed.message_id)
    assert completed_job is not None
    state.mark_completed(completed_job, tmp_path / "video-2.mp4")
    state.close()

    gateway = FakeTelegramGateway()
    gateway.search_results = tuple(
        VideoSearchResult(message, 60, f"说明 {message.message_id}")
        for message in (queued, completed, available)
    )
    factory_calls = 0

    def factory(*_args, **_kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return gateway

    service_task = asyncio.create_task(DownloaderService(paths, factory).run())
    try:
        await _wait_until(paths.search_endpoint.is_file)
        heartbeat = HeartbeatWriter(paths.heartbeat).read()
        assert heartbeat["status"] == "running"
        result = await SearchIpcClient(paths).search_videos(
            _search_request(),
            expected_pid=int(heartbeat["pid"]),
        )

        assert [item.queue_state for item in result] == [
            SearchQueueState.QUEUED,
            SearchQueueState.COMPLETED,
            SearchQueueState.AVAILABLE,
        ]
        assert factory_calls == 1
        assert gateway.search_calls == [(-1001, "课程", None, None, 20)]

        with pytest.raises(SearchChannelError, match="当前已监听"):
            await SearchIpcClient(paths).search_videos(
                _search_request(-1002),
                expected_pid=int(heartbeat["pid"]),
            )
    finally:
        request_stop(paths)
        assert await asyncio.wait_for(service_task, timeout=4) == 0

    assert gateway.disconnect_calls == 1
    assert not paths.search_endpoint.exists()


@pytest.mark.asyncio
async def test_service_stop_cancels_search_before_gateway_disconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_quiet_runtime(monkeypatch)
    paths, _ = configure(tmp_path, (GroupTarget(-1001, "课程群"),))
    started = asyncio.Event()
    cancelled = asyncio.Event()
    events: list[str] = []

    class BlockingGateway(FakeTelegramGateway):
        async def search_videos(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("search_cancelled")
                cancelled.set()
                raise

        async def disconnect(self) -> None:
            events.append("gateway_disconnected")
            await super().disconnect()

    gateway = BlockingGateway()
    service_task = asyncio.create_task(
        DownloaderService(paths, lambda *_: gateway).run()
    )
    await _wait_until(paths.search_endpoint.is_file)
    heartbeat = HeartbeatWriter(paths.heartbeat).read()
    client_task = asyncio.create_task(
        SearchIpcClient(paths).search_videos(
            _search_request(),
            expected_pid=int(heartbeat["pid"]),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    request_stop(paths)

    with pytest.raises(SearchChannelError, match="后台已停止"):
        await asyncio.wait_for(client_task, timeout=4)
    await asyncio.wait_for(cancelled.wait(), timeout=2)
    assert await asyncio.wait_for(service_task, timeout=4) == 0
    assert events == ["search_cancelled", "gateway_disconnected"]


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

        def __init__(self, paths, state, telegram_gateway, **_kwargs) -> None:
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
