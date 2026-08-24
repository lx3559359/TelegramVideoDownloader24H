from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tg_video_downloader.coordinator import ScannerCoordinator
from tg_video_downloader.gateway import TransientTelegramError
from tg_video_downloader.models import GroupTarget, MessageInfo
from tg_video_downloader.naming import build_final_path
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.state import StateStore
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


def test_all_generated_paths_stay_inside_project_root(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    state = StateStore(paths.database)
    state.close()

    for path in tmp_path.rglob("*"):
        assert path.resolve().is_relative_to(paths.root)
    for path in paths.writable_directories:
        assert path.resolve().is_relative_to(paths.root)
