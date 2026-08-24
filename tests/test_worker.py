import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader.gateway import PermanentMessageError, TransientTelegramError
from tg_video_downloader.models import GroupTarget, JobSource, MessageInfo
from tg_video_downloader.naming import build_final_path
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.state import StateStore
from tg_video_downloader.worker import DiskGuard, DownloadWorker, SAFETY_FREE_BYTES
from tests.fakes import FakeTelegramGateway


def make_video(message_id: int, *, size: int = 7) -> MessageInfo:
    return MessageInfo(
        chat_id=-1001,
        message_id=message_id,
        date=datetime(2026, 8, 24, tzinfo=UTC) + timedelta(minutes=message_id),
        mime_type="video/mp4",
        original_name=f"video-{message_id}.mp4",
        extension=".mp4",
        size=size,
        is_video=True,
        is_animated=False,
        is_round=False,
    )


def prepare(tmp_path: Path):
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    state = StateStore(paths.database)
    state.reconcile_targets((GroupTarget(-1001, "群"),))
    gateway = FakeTelegramGateway()
    return paths, state, gateway


@pytest.mark.asyncio
async def test_download_is_atomic_and_marks_completed(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"payload"
    message = make_video(1, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    gateway.download_payloads[(-1001, 1)] = payload
    worker = DownloadWorker(paths, state, gateway)
    try:
        result = await worker.run_one()
        final_path = build_final_path(paths, "群", message)

        assert result == "completed"
        assert final_path.read_bytes() == payload
        assert not (paths.temp / "-1001_1.part").exists()
        assert state.counts()["completed"] == 1
    finally:
        state.close()


@pytest.mark.asyncio
async def test_current_file_is_visible_only_while_job_is_active(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"payload"
    message = make_video(1, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_download(
        chat_id: int,
        message_id: int,
        destination: Path,
    ) -> Path:
        started.set()
        await release.wait()
        destination.write_bytes(payload)
        return destination

    gateway.download_message = blocking_download
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert worker.current_file is None
        task = asyncio.create_task(worker.run_one())
        await started.wait()
        assert worker.current_file == build_final_path(paths, "群", message).name
        release.set()
        assert await task == "completed"
        assert worker.current_file is None
    finally:
        state.close()


@pytest.mark.asyncio
async def test_transient_error_retries_without_blocking_next_job(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    older = make_video(1)
    newer = make_video(2)
    state.upsert_job(older, "群", JobSource.LIVE)
    state.upsert_job(newer, "群", JobSource.LIVE)
    gateway.download_payloads[(-1001, 1)] = b"payload"

    original_download = gateway.download_message

    async def fail_newer(chat_id: int, message_id: int, destination: Path) -> Path:
        if message_id == 2:
            raise TransientTelegramError("temporary")
        return await original_download(chat_id, message_id, destination)

    gateway.download_message = fail_newer
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert await worker.run_one() == "retry_wait"
        assert await worker.run_one() == "completed"
        assert state.counts()["retry_wait"] == 1
        assert state.counts()["completed"] == 1
    finally:
        state.close()


@pytest.mark.asyncio
async def test_permanent_error_is_not_retried(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1)
    state.upsert_job(message, "群", JobSource.LIVE)

    async def fail(*args, **kwargs):
        raise PermanentMessageError("deleted")

    gateway.download_message = fail
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert await worker.run_one() == "permanent_error"
        assert state.counts()["permanent_error"] == 1
    finally:
        state.close()


@pytest.mark.asyncio
async def test_disk_guard_pauses_before_calling_gateway(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1, size=100)
    state.upsert_job(message, "群", JobSource.LIVE)
    worker = DownloadWorker(paths, state, gateway)
    worker.disk_guard = DiskGuard(
        paths.downloads,
        usage=lambda _: SimpleNamespace(free=SAFETY_FREE_BYTES + 99),
    )
    try:
        assert await worker.run_one() == "disk_paused"
        assert gateway.downloaded_keys == []
    finally:
        state.close()


def test_recover_resets_only_matching_inflight_part(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1)
    state.upsert_job(message, "群", JobSource.LIVE)
    assert state.claim_next() is not None
    matching = paths.temp / "-1001_1.part"
    unrelated = paths.temp / "keep.part"
    matching.write_bytes(b"partial")
    unrelated.write_bytes(b"keep")
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert worker.recover() == 1
        assert not matching.exists()
        assert unrelated.read_bytes() == b"keep"
        assert state.counts()["pending_live"] == 1
    finally:
        state.close()
