from datetime import UTC, datetime
from pathlib import Path

import pytest

from tg_video_downloader.coordinator import ScannerCoordinator
from tg_video_downloader.models import GroupTarget, MessageInfo
from tg_video_downloader.state import StateStore
from tests.fakes import FakeTelegramGateway


def make_video(chat_id: int, message_id: int) -> MessageInfo:
    return MessageInfo(
        chat_id=chat_id,
        message_id=message_id,
        date=datetime(2026, 8, 24, message_id % 24, tzinfo=UTC),
        mime_type="video/mp4",
        original_name=f"{message_id}.mp4",
        extension=".mp4",
        size=message_id * 10,
        is_video=True,
        is_animated=False,
        is_round=False,
    )


@pytest.mark.asyncio
async def test_only_selected_groups_are_scanned_and_live_is_upserted(tmp_path: Path) -> None:
    selected = GroupTarget(-1001, "选中群")
    unselected_message = make_video(-1002, 8)
    selected_history = make_video(-1001, 5)
    gateway = FakeTelegramGateway(
        {-1001: [selected_history], -1002: [unselected_message]}
    )
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = ScannerCoordinator(store, gateway)
    try:
        await coordinator.start((selected,))
        gateway.iterated_chat_ids.clear()
        await coordinator.scan_once(-1001)
        await gateway.emit(make_video(-1001, 6))
        await gateway.emit(unselected_message)

        assert gateway.iterated_chat_ids == [-1001]
        assert store.job_count() == 2
        claimed = store.claim_next()
        assert claimed is not None
        assert claimed.message_id == 6
    finally:
        store.close()


@pytest.mark.asyncio
async def test_catch_up_only_enqueues_messages_after_latest_seen(tmp_path: Path) -> None:
    gateway = FakeTelegramGateway(
        {-1001: [make_video(-1001, message_id) for message_id in (4, 5, 6, 7)]}
    )
    store = StateStore(tmp_path / "state.sqlite3")
    store.reconcile_targets((GroupTarget(-1001, "群"),))
    store.set_latest_seen(-1001, 5)
    coordinator = ScannerCoordinator(store, gateway)
    try:
        await coordinator.catch_up_once(-1001)

        assert store.job_count() == 2
        assert store.get_group(-1001).latest_seen_id == 7
        assert gateway.iterated_chat_ids == [-1001]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_reenabled_group_catches_up_messages_seen_while_disabled(
    tmp_path: Path,
) -> None:
    group = GroupTarget(-1001, "群")
    gateway = FakeTelegramGateway(
        {-1001: [make_video(-1001, message_id) for message_id in (5, 6, 7)]}
    )
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = ScannerCoordinator(store, gateway)
    try:
        store.reconcile_targets((group,))
        store.set_latest_seen(group.chat_id, 5)
        store.reconcile_targets(())

        await coordinator.apply_targets((group,))

        assert store.job_count() == 2
        assert store.get_group(group.chat_id).latest_seen_id == 7
    finally:
        store.close()


@pytest.mark.asyncio
async def test_catch_up_enabled_once_only_queries_enabled_groups(tmp_path: Path) -> None:
    enabled = GroupTarget(-1001, "启用群")
    disabled = GroupTarget(-1002, "禁用群")
    gateway = FakeTelegramGateway(
        {
            enabled.chat_id: [make_video(enabled.chat_id, 6)],
            disabled.chat_id: [make_video(disabled.chat_id, 6)],
        }
    )
    store = StateStore(tmp_path / "state.sqlite3")
    store.reconcile_targets((enabled, disabled))
    store.set_latest_seen(enabled.chat_id, 5)
    store.set_latest_seen(disabled.chat_id, 5)
    store.reconcile_targets((enabled,))
    coordinator = ScannerCoordinator(store, gateway)
    try:
        await coordinator.catch_up_enabled_once()

        assert gateway.iterated_chat_ids == [enabled.chat_id]
        assert store.job_count() == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_history_continues_below_saved_cursor(tmp_path: Path) -> None:
    gateway = FakeTelegramGateway(
        {-1001: [make_video(-1001, message_id) for message_id in (2, 4, 5, 6)]}
    )
    store = StateStore(tmp_path / "state.sqlite3")
    store.reconcile_targets((GroupTarget(-1001, "群"),))
    store.set_history_cursor(-1001, 5, complete=False)
    coordinator = ScannerCoordinator(store, gateway)
    try:
        assert await coordinator.scan_once(-1001, batch_size=100)

        assert store.job_count() == 2
        group = store.get_group(-1001)
        assert group.history_cursor_id == 2
        assert group.history_complete is True
    finally:
        store.close()


@pytest.mark.asyncio
async def test_removed_group_ignores_new_events(tmp_path: Path) -> None:
    selected = GroupTarget(-1001, "群")
    gateway = FakeTelegramGateway()
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = ScannerCoordinator(store, gateway)
    try:
        await coordinator.start((selected,))
        await coordinator.apply_targets(())
        await gateway.emit(make_video(-1001, 10))

        assert store.job_count() == 0
        assert store.enabled_chat_ids() == set()
    finally:
        store.close()
