import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.coordinator import ScannerCoordinator
from tg_video_downloader.gateway import DOWNLOAD_CHUNK_SIZE, TransientTelegramError
from tg_video_downloader.gui.controller import GuiController
from tg_video_downloader.models import (
    AppConfig,
    Credentials,
    GroupTarget,
    JobSource,
    JobStatus,
    MessageInfo,
    VideoSearchResult,
)
from tg_video_downloader.naming import build_final_path
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.service import DownloaderService
from tg_video_downloader.state import StateStore
from tg_video_downloader.storage import build_part_path
from tg_video_downloader.windows import request_stop
from tg_video_downloader.worker import DownloadWorker
from tests.fakes import FakeTelegramGateway


GROUP_A = GroupTarget(-1001, "A 群")
GROUP_B = GroupTarget(-1002, "B 群")


def video(chat_id: int, message_id: int, payload: bytes | None = None) -> MessageInfo:
    content = payload if payload is not None else f"video-{chat_id}-{message_id}".encode()
    return MessageInfo(
        chat_id=chat_id,
        message_id=message_id,
        date=datetime(2026, 8, 24, tzinfo=UTC) + timedelta(minutes=message_id),
        mime_type="video/mp4",
        original_name=f"{message_id}.mp4",
        extension=".mp4",
        size=len(content),
        is_video=True,
        is_animated=False,
        is_round=False,
    )


def gateway_with(messages: dict[int, list[MessageInfo]]) -> FakeTelegramGateway:
    gateway = FakeTelegramGateway(messages)
    for group_messages in messages.values():
        for message in group_messages:
            gateway.download_payloads[(message.chat_id, message.message_id)] = (
                f"video-{message.chat_id}-{message.message_id}".encode()
            )
    return gateway


def search_result(message: MessageInfo) -> VideoSearchResult:
    return VideoSearchResult(
        message=message,
        duration_seconds=65,
        caption="课程",
    )


@pytest.mark.asyncio
async def test_selected_search_result_uses_existing_worker_and_current_root(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    selected = video(GROUP_A.chat_id, 77, payload=b"video")
    gateway = FakeTelegramGateway()
    gateway.search_results = (search_result(selected),)
    gateway.download_payloads[(selected.chat_id, selected.message_id)] = b"video"
    controller = GuiController(paths, lambda *_: gateway)
    controller.save_credentials(Credentials(123, "hash"))
    controller.save_selected_groups((GROUP_A,))
    state = StateStore(paths.database)
    try:
        found = await controller.search_videos(
            GROUP_A.chat_id,
            "",
            "",
            "",
            20,
            local_timezone=UTC,
        )
        summary = controller.enqueue_selected_videos(
            GROUP_A.chat_id,
            tuple(item.result for item in found),
        )
        worker = DownloadWorker(
            paths,
            state,
            gateway,
            download_root=lambda: paths.downloads,
        )

        assert await worker.run_one() == "completed"
        job = state.get_job(GROUP_A.chat_id, selected.message_id)
        assert summary.added == 1
        assert job is not None and job.status is JobStatus.COMPLETED
        assert gateway.downloaded_keys == [
            (GROUP_A.chat_id, selected.message_id)
        ]
    finally:
        state.close()


def test_selected_job_waits_for_current_file_then_precedes_history(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    state = StateStore(paths.database)
    current = video(GROUP_A.chat_id, 90)
    selected = video(GROUP_A.chat_id, 80)
    history = video(GROUP_A.chat_id, 100)
    try:
        state.reconcile_targets((GROUP_A,))
        state.upsert_job(current, GROUP_A.title, JobSource.LIVE)
        active = state.claim_next()
        assert active is not None and active.message_id == current.message_id
        state.upsert_job(history, GROUP_A.title, JobSource.HISTORY)

        state.enqueue_manual_results(GROUP_A, (selected,))

        still_active = state.get_job(GROUP_A.chat_id, current.message_id)
        assert still_active is not None
        assert still_active.status is JobStatus.DOWNLOADING
        state.mark_completed(active, tmp_path / "current.mp4")
        next_job = state.claim_next()
        assert next_job is not None
        assert next_job.message_id == selected.message_id
        assert next_job.source is JobSource.LIVE
    finally:
        state.close()


@pytest.mark.asyncio
async def test_requeued_selected_failure_preserves_root_and_partial(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths.from_root(tmp_path / "project")
    paths.ensure_directories()
    external = (tmp_path / "external").resolve()
    external.mkdir(parents=True)
    payload = b"a" * DOWNLOAD_CHUNK_SIZE + b"rest"
    selected = video(GROUP_A.chat_id, 78, payload=payload)
    gateway = FakeTelegramGateway()
    gateway.download_payloads[(selected.chat_id, selected.message_id)] = payload
    controller = GuiController(paths, lambda *_: gateway)
    controller.save_selected_groups((GROUP_A,))
    state = StateStore(paths.database)
    try:
        state.reconcile_targets((GROUP_A,))
        state.upsert_job(selected, GROUP_A.title, JobSource.HISTORY)
        failed = state.get_job(selected.chat_id, selected.message_id)
        assert failed is not None
        bound = state.bind_output_root(failed, external)
        state.mark_permanent_error(bound, "deleted")
        partial = build_part_path(
            external,
            selected.chat_id,
            selected.message_id,
        )
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(payload[:DOWNLOAD_CHUNK_SIZE])

        summary = controller.enqueue_selected_videos(
            GROUP_A.chat_id,
            (search_result(selected),),
        )
        worker = DownloadWorker(
            paths,
            state,
            gateway,
            download_root=lambda: tmp_path / "new-root",
        )

        assert summary.requeued == 1
        assert await worker.run_one() == "completed"
        completed = state.get_job(selected.chat_id, selected.message_id)
        assert completed is not None
        assert completed.output_root == external
        assert gateway.download_offsets == [DOWNLOAD_CHUNK_SIZE]
        final_path = build_final_path(
            paths,
            GROUP_A.title,
            selected,
            download_root=external,
        )
        assert final_path.read_bytes() == payload
        assert not partial.exists()
    finally:
        state.close()


@pytest.mark.asyncio
async def test_whitelist_only_scans_and_downloads_selected_group(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    selected = video(GROUP_A.chat_id, 1)
    unselected = video(GROUP_B.chat_id, 2)
    gateway = gateway_with({GROUP_A.chat_id: [selected], GROUP_B.chat_id: [unselected]})
    state = StateStore(paths.database)
    coordinator = ScannerCoordinator(state, gateway)
    worker = DownloadWorker(paths, state, gateway)
    try:
        await coordinator.start((GROUP_A,))
        await coordinator.scan_once(GROUP_A.chat_id)
        assert await worker.run_one() == "completed"

        assert set(gateway.iterated_chat_ids) == {GROUP_A.chat_id}
        assert gateway.downloaded_keys == [(GROUP_A.chat_id, 1)]
        assert build_final_path(paths, GROUP_A.title, selected).is_file()
        assert not build_final_path(paths, GROUP_B.title, unselected).exists()
    finally:
        state.close()


@pytest.mark.asyncio
async def test_restart_resumes_history_cursor_without_duplicates(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    history = [video(GROUP_A.chat_id, message_id) for message_id in (1, 2, 3)]
    gateway = gateway_with({GROUP_A.chat_id: history})

    first_state = StateStore(paths.database)
    first = ScannerCoordinator(first_state, gateway)
    await first.start((GROUP_A,))
    assert await first.scan_once(GROUP_A.chat_id, batch_size=1)
    assert first_state.get_group(GROUP_A.chat_id).history_cursor_id == 3
    first_state.close()

    state = StateStore(paths.database)
    coordinator = ScannerCoordinator(state, gateway)
    worker = DownloadWorker(paths, state, gateway)
    try:
        await coordinator.start((GROUP_A,))
        while not state.get_group(GROUP_A.chat_id).history_complete:
            await coordinator.scan_once(GROUP_A.chat_id, batch_size=1)

        while await worker.run_one() != "idle":
            pass

        assert state.job_count() == 3
        assert state.counts()["completed"] == 3
        assert len(list(paths.downloads.rglob("*.mp4"))) == 3
    finally:
        state.close()


@pytest.mark.asyncio
async def test_live_video_jumps_ahead_of_waiting_history(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    history = [video(GROUP_A.chat_id, message_id) for message_id in (1, 2)]
    live = video(GROUP_A.chat_id, 10)
    gateway = gateway_with({GROUP_A.chat_id: history})
    gateway.download_payloads[(live.chat_id, live.message_id)] = b"video--1001-10"
    state = StateStore(paths.database)
    coordinator = ScannerCoordinator(state, gateway)
    worker = DownloadWorker(paths, state, gateway)
    try:
        await coordinator.start((GROUP_A,))
        await coordinator.scan_once(GROUP_A.chat_id)
        assert await worker.run_one() == "completed"

        await gateway.emit(live)
        assert await worker.run_one() == "completed"

        assert gateway.downloaded_keys[:2] == [
            (GROUP_A.chat_id, 2),
            (GROUP_A.chat_id, 10),
        ]
    finally:
        state.close()


@pytest.mark.asyncio
async def test_transient_download_retries_to_one_clean_final_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    message = video(GROUP_A.chat_id, 1)
    gateway = gateway_with({GROUP_A.chat_id: [message]})
    gateway.download_failures[(message.chat_id, message.message_id)] = [
        TransientTelegramError("temporary")
    ]
    state = StateStore(paths.database)
    coordinator = ScannerCoordinator(state, gateway)
    worker = DownloadWorker(paths, state, gateway)
    monkeypatch.setattr("tg_video_downloader.worker.QUICK_RETRY_DELAYS", (0,))
    try:
        await coordinator.start((GROUP_A,))
        await coordinator.scan_once(GROUP_A.chat_id)
        assert await worker.run_one() == "retry_wait"
        assert await worker.run_one() == "completed"

        final_path = build_final_path(paths, GROUP_A.title, message)
        assert final_path.read_bytes() == gateway.download_payloads[(message.chat_id, message.message_id)]
        assert len(list(final_path.parent.glob(f"{message.message_id}_*"))) == 1
        assert not list(paths.temp.glob("*.part"))
    finally:
        state.close()


@pytest.mark.asyncio
async def test_hot_target_switch_keeps_existing_files_and_ignores_removed_group(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    old_a = video(GROUP_A.chat_id, 1)
    new_a = video(GROUP_A.chat_id, 2)
    history_b = video(GROUP_B.chat_id, 5)
    gateway = gateway_with({GROUP_A.chat_id: [old_a], GROUP_B.chat_id: [history_b]})
    state = StateStore(paths.database)
    coordinator = ScannerCoordinator(state, gateway)
    worker = DownloadWorker(paths, state, gateway)
    try:
        await coordinator.start((GROUP_A,))
        await coordinator.scan_once(GROUP_A.chat_id)
        assert await worker.run_one() == "completed"
        a_path = build_final_path(paths, GROUP_A.title, old_a)
        original_a = a_path.read_bytes()

        await coordinator.apply_targets((GROUP_B,))
        await coordinator.scan_once(GROUP_B.chat_id)
        await gateway.emit(new_a)
        while await worker.run_one() != "idle":
            pass

        assert a_path.read_bytes() == original_a
        assert not build_final_path(paths, GROUP_A.title, new_a).exists()
        assert build_final_path(paths, GROUP_B.title, history_b).is_file()
        assert GROUP_B.chat_id in gateway.iterated_chat_ids
    finally:
        state.close()


@pytest.mark.asyncio
async def test_reenabled_group_catches_up_only_the_missed_video(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    first = video(GROUP_A.chat_id, 1)
    missed_payload = b"missed-video"
    missed = video(GROUP_A.chat_id, 2, payload=missed_payload)
    gateway = gateway_with({GROUP_A.chat_id: [first]})
    state = StateStore(paths.database)
    coordinator = ScannerCoordinator(state, gateway)
    worker = DownloadWorker(paths, state, gateway)
    try:
        await coordinator.start((GROUP_A,))
        await coordinator.scan_once(GROUP_A.chat_id)
        assert await worker.run_one() == "completed"

        await coordinator.apply_targets(())
        gateway.messages[GROUP_A.chat_id].append(missed)
        gateway.download_payloads[(missed.chat_id, missed.message_id)] = missed_payload

        await coordinator.apply_targets((GROUP_A,))
        assert await worker.run_one() == "completed"
        assert await worker.run_one() == "idle"

        assert build_final_path(paths, GROUP_A.title, missed).read_bytes() == missed_payload
        assert gateway.downloaded_keys.count((GROUP_A.chat_id, missed.message_id)) == 1
        assert state.counts()["completed"] == 2
    finally:
        state.close()


@pytest.mark.asyncio
async def test_service_hot_reload_resumes_paused_history_after_live_download(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    paused_target = GroupTarget(GROUP_A.chat_id, GROUP_A.title, False)
    history = video(GROUP_A.chat_id, 1)
    live = video(GROUP_A.chat_id, 2)
    config_store = ConfigStore(paths)
    config_store.save_credentials(Credentials(12345, "hash"))
    config_store.save_config(
        AppConfig(groups=(paused_target,), config_poll_seconds=1)
    )
    state = StateStore(paths.database)
    state.reconcile_targets((paused_target,))
    state.upsert_job(history, GROUP_A.title, JobSource.HISTORY)
    state.upsert_job(live, GROUP_A.title, JobSource.LIVE)
    state.close()
    live_completed = asyncio.Event()
    history_completed = asyncio.Event()

    class RecordingGateway(FakeTelegramGateway):
        async def download_message(self, chat_id, message_id, destination, **kwargs):
            result = await super().download_message(
                chat_id,
                message_id,
                destination,
                **kwargs,
            )
            if message_id == live.message_id:
                live_completed.set()
            if message_id == history.message_id:
                history_completed.set()
            return result

    gateway = RecordingGateway()
    gateway.download_payloads[(live.chat_id, live.message_id)] = b"video--1001-2"
    gateway.download_payloads[(history.chat_id, history.message_id)] = b"video--1001-1"
    service_task = asyncio.create_task(
        DownloaderService(paths, lambda *_: gateway).run()
    )
    try:
        await asyncio.wait_for(live_completed.wait(), timeout=2)
        live_final_path = build_final_path(paths, GROUP_A.title, live)
        await _wait_until(live_final_path.is_file, timeout=2)
        observer = StateStore(paths.database)
        try:
            assert observer.counts()["paused_history"] == 1
        finally:
            observer.close()
        assert live_final_path.is_file()

        config_store.save_config(
            AppConfig(
                groups=(GroupTarget(GROUP_A.chat_id, GROUP_A.title, True),),
                config_poll_seconds=1,
            )
        )

        # Config reload and an idle worker wake-up can each consume one second.
        await asyncio.wait_for(history_completed.wait(), timeout=3)
        history_final_path = build_final_path(paths, GROUP_A.title, history)
        await _wait_until(history_final_path.is_file, timeout=2)
        assert history_final_path.is_file()
    finally:
        request_stop(paths)
        assert await asyncio.wait_for(service_task, timeout=2) == 0


@pytest.mark.asyncio
async def test_service_stop_cancels_active_download_and_preserves_partial(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    target = GroupTarget(GROUP_A.chat_id, GROUP_A.title, True)
    message = video(
        GROUP_A.chat_id,
        1,
        payload=b"a" * (DOWNLOAD_CHUNK_SIZE * 2),
    )
    config_store = ConfigStore(paths)
    config_store.save_credentials(Credentials(12345, "hash"))
    config_store.save_config(AppConfig(groups=(target,), config_poll_seconds=1))
    state = StateStore(paths.database)
    state.reconcile_targets((target,))
    state.upsert_job(message, GROUP_A.title, JobSource.LIVE)
    state.close()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingGateway(FakeTelegramGateway):
        async def download_message(
            self,
            _chat_id,
            _message_id,
            destination,
            *,
            offset=0,
            progress_callback=None,
        ):
            with destination.open("ab" if offset else "wb") as handle:
                handle.write(b"a" * DOWNLOAD_CHUNK_SIZE)
            if progress_callback is not None:
                progress_callback(offset + DOWNLOAD_CHUNK_SIZE, message.size)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    gateway = BlockingGateway()
    service_task = asyncio.create_task(
        DownloaderService(paths, lambda *_: gateway).run()
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    request_stop(paths)

    assert await asyncio.wait_for(service_task, timeout=2) == 0
    assert cancelled.is_set()
    part_path = build_part_path(
        paths.downloads,
        message.chat_id,
        message.message_id,
    )
    assert part_path.exists()
    assert part_path.stat().st_size == DOWNLOAD_CHUNK_SIZE
    reopened = StateStore(paths.database)
    try:
        assert reopened.claim_next() is not None
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_service_hot_reload_changes_root_only_for_future_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path / "project")
    paths.ensure_directories()
    root_a = (tmp_path / "root-a").resolve()
    root_b = (tmp_path / "root-b").resolve()
    first = video(GROUP_A.chat_id, 30)
    second = video(GROUP_A.chat_id, 31)
    config_store = ConfigStore(paths)
    config_store.save_credentials(Credentials(12345, "hash"))
    config_store.save_config(
        AppConfig(
            groups=(GROUP_A,),
            config_poll_seconds=1,
            download_root=root_a,
        )
    )
    state = StateStore(paths.database)
    state.reconcile_targets((GROUP_A,))
    state.upsert_job(first, GROUP_A.title, JobSource.LIVE)
    state.close()

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_completed = asyncio.Event()
    config_reloaded = asyncio.Event()

    class BlockingFirstGateway(FakeTelegramGateway):
        async def download_message(self, chat_id, message_id, destination, **kwargs):
            if message_id == first.message_id:
                first_started.set()
                await release_first.wait()
            result = await super().download_message(
                chat_id,
                message_id,
                destination,
                **kwargs,
            )
            if message_id == second.message_id:
                second_completed.set()
            return result

    original_apply_targets = ScannerCoordinator.apply_targets
    apply_count = 0

    async def recording_apply_targets(self, targets):
        nonlocal apply_count
        result = await original_apply_targets(self, targets)
        apply_count += 1
        if apply_count >= 2:
            config_reloaded.set()
        return result

    monkeypatch.setattr(
        ScannerCoordinator,
        "apply_targets",
        recording_apply_targets,
    )
    gateway = BlockingFirstGateway()
    gateway.download_payloads[(first.chat_id, first.message_id)] = b"video--1001-30"
    gateway.download_payloads[(second.chat_id, second.message_id)] = b"video--1001-31"
    service_task = asyncio.create_task(
        DownloaderService(paths, lambda *_: gateway).run()
    )
    try:
        await asyncio.wait_for(first_started.wait(), timeout=2)
        config_store.save_config(
            AppConfig(
                groups=(GROUP_A,),
                config_poll_seconds=1,
                download_root=root_b,
            )
        )
        await asyncio.wait_for(config_reloaded.wait(), timeout=3)
        observer = StateStore(paths.database)
        try:
            observer.upsert_job(second, GROUP_A.title, JobSource.LIVE)
        finally:
            observer.close()
        release_first.set()
        await asyncio.wait_for(second_completed.wait(), timeout=3)
        first_final = build_final_path(
            paths,
            GROUP_A.title,
            first,
            download_root=root_a,
        )
        second_final = build_final_path(
            paths,
            GROUP_A.title,
            second,
            download_root=root_b,
        )
        await _wait_until(first_final.is_file, timeout=2)
        await _wait_until(second_final.is_file, timeout=2)
        observer = StateStore(paths.database)
        try:
            first_job = observer.get_job(first.chat_id, first.message_id)
            second_job = observer.get_job(second.chat_id, second.message_id)
            assert first_job is not None and first_job.output_root == root_a
            assert second_job is not None and second_job.output_root == root_b
        finally:
            observer.close()
    finally:
        release_first.set()
        request_stop(paths)
        assert await asyncio.wait_for(service_task, timeout=3) == 0
def test_all_generated_paths_stay_inside_project_root(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    state = StateStore(paths.database)
    state.close()

    for path in tmp_path.rglob("*"):
        assert path.resolve().is_relative_to(paths.root)
    for path in paths.writable_directories:
        assert path.resolve().is_relative_to(paths.root)


async def _wait_until(predicate, timeout: float) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=timeout)
