import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader.gateway import (
    DOWNLOAD_CHUNK_SIZE,
    PermanentMessageError,
    TransientTelegramError,
)
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
async def test_worker_resumes_aligned_partial(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"a" * (DOWNLOAD_CHUNK_SIZE * 2)
    message = make_video(1, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    part = paths.temp / "-1001_1.part"
    part.write_bytes(payload[:DOWNLOAD_CHUNK_SIZE])
    gateway.download_payloads[(-1001, 1)] = payload
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert await worker.run_one() == "completed"
        assert gateway.download_offsets == [DOWNLOAD_CHUNK_SIZE]
        assert build_final_path(paths, "群", message).read_bytes() == payload
    finally:
        state.close()


@pytest.mark.asyncio
async def test_unaligned_partial_truncates_to_previous_chunk(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"a" * (DOWNLOAD_CHUNK_SIZE * 2)
    message = make_video(1, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    part = paths.temp / "-1001_1.part"
    part.write_bytes(payload[: DOWNLOAD_CHUNK_SIZE + 17])
    gateway.download_payloads[(-1001, 1)] = payload
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert await worker.run_one() == "completed"
        assert gateway.download_offsets == [DOWNLOAD_CHUNK_SIZE]
        assert build_final_path(paths, "群", message).read_bytes() == payload
    finally:
        state.close()


@pytest.mark.asyncio
async def test_oversized_partial_restarts_from_zero(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"payload"
    message = make_video(1, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    part = paths.temp / "-1001_1.part"
    part.write_bytes(payload + b"corrupt")
    gateway.download_payloads[(-1001, 1)] = payload
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert await worker.run_one() == "completed"
        assert gateway.download_offsets == [0]
        assert build_final_path(paths, "群", message).read_bytes() == payload
    finally:
        state.close()


@pytest.mark.asyncio
async def test_complete_partial_finalizes_without_network(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"payload"
    message = make_video(1, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    part = paths.temp / "-1001_1.part"
    part.write_bytes(payload)
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert await worker.run_one() == "completed"
        assert gateway.downloaded_keys == []
        assert build_final_path(paths, "群", message).read_bytes() == payload
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
        **_kwargs,
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

    async def fail_newer(
        chat_id: int,
        message_id: int,
        destination: Path,
        **kwargs,
    ) -> Path:
        if message_id == 2:
            raise TransientTelegramError("temporary")
        return await original_download(chat_id, message_id, destination, **kwargs)

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


@pytest.mark.asyncio
async def test_disk_guard_receives_only_remaining_bytes(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1, size=DOWNLOAD_CHUNK_SIZE * 2)
    state.upsert_job(message, "群", JobSource.LIVE)
    part = paths.temp / "-1001_1.part"
    part.write_bytes(b"a" * DOWNLOAD_CHUNK_SIZE)
    seen: list[int | None] = []
    worker = DownloadWorker(paths, state, gateway)
    worker.disk_guard = SimpleNamespace(
        has_space=lambda size: seen.append(size) or False
    )
    try:
        assert await worker.run_one() == "disk_paused"
        assert seen == [DOWNLOAD_CHUNK_SIZE]
        assert gateway.downloaded_keys == []
    finally:
        state.close()


@pytest.mark.asyncio
async def test_active_history_finishes_after_policy_is_paused(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    current = make_video(2)
    waiting = make_video(1)
    state.upsert_job(current, "群", JobSource.HISTORY)
    state.upsert_job(waiting, "群", JobSource.HISTORY)
    gateway.download_payloads[(-1001, current.message_id)] = b"payload"
    started = asyncio.Event()
    release = asyncio.Event()
    original_download = gateway.download_message

    async def blocking_download(*args, **kwargs):
        started.set()
        await release.wait()
        return await original_download(*args, **kwargs)

    gateway.download_message = blocking_download
    worker = DownloadWorker(paths, state, gateway, monitor_seconds=0.005)
    try:
        task = asyncio.create_task(worker.run_one())
        await started.wait()
        state.reconcile_targets((GroupTarget(-1001, "群", False),))
        release.set()

        assert await task == "completed"
        assert state.claim_next() is None
        assert state.counts()["paused_history"] == 1
    finally:
        state.close()


@pytest.mark.asyncio
async def test_progress_reports_resume_bytes_percent_and_speed(
    tmp_path: Path,
) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"a" * (DOWNLOAD_CHUNK_SIZE * 2)
    message = make_video(1, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    part = paths.temp / "-1001_1.part"
    part.write_bytes(payload[:DOWNLOAD_CHUNK_SIZE])
    started = asyncio.Event()
    release = asyncio.Event()
    clock = SimpleNamespace(value=100.0)

    async def blocking_download(
        _chat_id: int,
        _message_id: int,
        destination: Path,
        *,
        offset: int,
        progress_callback,
    ) -> Path:
        clock.value = 102.0
        progress_callback(
            offset + DOWNLOAD_CHUNK_SIZE // 2,
            len(payload),
        )
        started.set()
        await release.wait()
        with destination.open("ab") as handle:
            handle.write(payload[offset:])
        progress_callback(len(payload), len(payload))
        return destination

    gateway.download_message = blocking_download
    worker = DownloadWorker(
        paths,
        state,
        gateway,
        monotonic=lambda: clock.value,
        monitor_seconds=0.005,
    )
    try:
        task = asyncio.create_task(worker.run_one())
        await started.wait()

        progress = worker.progress
        assert progress is not None
        assert progress.file_name == build_final_path(paths, "群", message).name
        assert progress.downloaded_bytes == DOWNLOAD_CHUNK_SIZE * 3 // 2
        assert progress.total_bytes == len(payload)
        assert progress.percent == 75.0
        assert progress.bytes_per_second == DOWNLOAD_CHUNK_SIZE / 4
        assert progress.resumed is True

        release.set()
        assert await task == "completed"
        assert worker.progress is None
    finally:
        state.close()


@pytest.mark.asyncio
async def test_stalled_download_is_cancelled_and_retried(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1, size=100)
    state.upsert_job(message, "群", JobSource.LIVE)
    cancelled = asyncio.Event()

    async def stalled(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    gateway.download_message = stalled
    worker = DownloadWorker(
        paths,
        state,
        gateway,
        stall_seconds=0.03,
        monitor_seconds=0.005,
    )
    try:
        assert await worker.run_one() == "retry_wait"
        assert cancelled.is_set()
        assert state.counts()["retry_wait"] == 1
    finally:
        state.close()


@pytest.mark.asyncio
async def test_stop_cancels_download_and_releases_job(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1, size=100)
    state.upsert_job(message, "群", JobSource.LIVE)
    stop = asyncio.Event()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    gateway.download_message = blocked
    worker = DownloadWorker(paths, state, gateway, monitor_seconds=0.005)
    try:
        task = asyncio.create_task(worker.run_one(stop))
        await started.wait()
        stop.set()

        assert await task == "stopped"
        assert cancelled.is_set()
        assert state.claim_next() is not None
    finally:
        state.close()


@pytest.mark.asyncio
async def test_parent_cancellation_cancels_download_and_releases_job(
    tmp_path: Path,
) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1, size=100)
    state.upsert_job(message, "群", JobSource.LIVE)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    active_download: list[asyncio.Task] = []

    async def blocked(*_args, **_kwargs):
        active_download.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    gateway.download_message = blocked
    worker = DownloadWorker(paths, state, gateway, monitor_seconds=0.005)
    task = asyncio.create_task(worker.run_one())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert cancelled.is_set()
        assert state.claim_next() is not None
    finally:
        for download_task in active_download:
            if download_task is not None and not download_task.done():
                download_task.cancel()
                await asyncio.wait_for(
                    asyncio.gather(download_task, return_exceptions=True),
                    timeout=1,
                )
        state.close()


def test_recover_preserves_partial_file(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1)
    state.upsert_job(message, "群", JobSource.LIVE)
    assert state.claim_next() is not None
    matching = paths.temp / "-1001_1.part"
    unrelated = paths.temp / "keep.part"
    matching.write_bytes(b"a" * DOWNLOAD_CHUNK_SIZE)
    unrelated.write_bytes(b"keep")
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert worker.recover() == 1
        assert matching.stat().st_size == DOWNLOAD_CHUNK_SIZE
        assert unrelated.read_bytes() == b"keep"
        assert state.counts()["pending_live"] == 1
    finally:
        state.close()
