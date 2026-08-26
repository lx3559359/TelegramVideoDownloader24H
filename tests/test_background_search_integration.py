import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.gui.controller import GuiController
from tg_video_downloader.models import (
    AppConfig,
    Credentials,
    GroupTarget,
    MessageInfo,
    VideoSearchResult,
)
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.selective import SearchQueueState, SelectableVideo
from tg_video_downloader.service import DownloaderService
from tg_video_downloader.windows import request_stop
from tests.fakes import FakeTelegramGateway


def _configure(tmp_path: Path) -> tuple[ProjectPaths, GroupTarget]:
    paths = ProjectPaths.from_root(tmp_path)
    group = GroupTarget(-1001, "课程群", download_history=False)
    store = ConfigStore(paths)
    store.save_credentials(Credentials(12345, "secret-hash"))
    store.save_config(AppConfig(groups=(group,), config_poll_seconds=1))
    return paths, group


def _install_quiet_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    class QuietCoordinator:
        def __init__(self, state, gateway) -> None:
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

        def __init__(self, paths, state, gateway, **_options) -> None:
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


def _result(
    *,
    caption: str,
    filename: str,
) -> VideoSearchResult:
    return VideoSearchResult(
        message=MessageInfo(
            chat_id=-1001,
            message_id=77,
            date=datetime(2026, 8, 26, tzinfo=UTC),
            mime_type="video/mp4",
            original_name=filename,
            extension=".mp4",
            size=123456,
            is_video=True,
            is_animated=False,
            is_round=False,
        ),
        duration_seconds=60,
        caption=caption,
    )


async def _wait_until(predicate, timeout: float = 2) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=timeout)


@pytest.mark.asyncio
async def test_running_controller_uses_one_gateway_and_keeps_search_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install_quiet_runtime(monkeypatch)
    paths, group = _configure(tmp_path)
    keyword = "private-keyword-91d9d7"
    caption = "private-caption-bf7a21"
    filename = "private-filename-6c42a8.mp4"
    gateway = FakeTelegramGateway()
    result = _result(caption=caption, filename=filename)
    gateway.search_results = (result,)
    factory_calls = 0

    def factory(*_args, **_kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return gateway

    service_task = asyncio.create_task(DownloaderService(paths, factory).run())
    try:
        await _wait_until(paths.search_endpoint.is_file)
        controller = GuiController(paths, factory)

        items = await controller.search_videos(
            group.chat_id,
            keyword,
            "",
            "",
            20,
            local_timezone=UTC,
        )

        assert items == (SelectableVideo(result, SearchQueueState.AVAILABLE),)
        assert factory_calls == 1
        assert gateway.connected is True
        assert gateway.disconnect_calls == 0

        private_values = tuple(
            value.encode("utf-8") for value in (keyword, caption, filename)
        )
        runtime_files = tuple(
            path
            for path in paths.runtime.rglob("*")
            if path.is_file() and path.suffix != ".lock"
        )
        for path in runtime_files:
            contents = path.read_bytes()
            assert all(value not in contents for value in private_values), path
        for path in paths.logs.rglob("*"):
            if path.is_file():
                contents = path.read_bytes()
                assert all(value not in contents for value in private_values), path
        assert keyword not in caplog.text
        assert caption not in caplog.text
        assert filename not in caplog.text
        heartbeat = paths.heartbeat.read_bytes()
        assert all(value not in heartbeat for value in private_values)
    finally:
        request_stop(paths)
        assert await asyncio.wait_for(service_task, timeout=4) == 0


@pytest.mark.asyncio
async def test_controller_cancel_reaches_service_without_stopping_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_quiet_runtime(monkeypatch)
    paths, group = _configure(tmp_path)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingGateway(FakeTelegramGateway):
        async def search_videos(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    gateway = BlockingGateway()
    factory_calls = 0

    def factory(*_args, **_kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return gateway

    service_task = asyncio.create_task(DownloaderService(paths, factory).run())
    try:
        await _wait_until(paths.search_endpoint.is_file)
        controller = GuiController(paths, factory)
        search_task = asyncio.create_task(
            controller.search_videos(group.chat_id, "", "", "", 20)
        )
        await asyncio.wait_for(started.wait(), timeout=2)

        search_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await search_task
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        assert service_task.done() is False
        assert gateway.connected is True
        assert gateway.disconnect_calls == 0
        assert factory_calls == 1
    finally:
        request_stop(paths)
        assert await asyncio.wait_for(service_task, timeout=4) == 0

    assert gateway.disconnect_calls == 1
    assert HeartbeatWriter(paths.heartbeat).read()["status"] == "stopped"
