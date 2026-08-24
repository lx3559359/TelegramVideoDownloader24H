from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tg_video_downloader.models import GroupTarget, JobSource, JobStatus, MessageInfo
from tg_video_downloader.state import StateStore


@pytest.fixture
def history_message() -> MessageInfo:
    return MessageInfo(
        chat_id=-1001,
        message_id=10,
        date=datetime(2026, 8, 23, 1, tzinfo=UTC),
        mime_type="video/mp4",
        original_name="history.mp4",
        extension=".mp4",
        size=100,
        is_video=True,
        is_animated=False,
        is_round=False,
    )


@pytest.fixture
def live_message(history_message: MessageInfo) -> MessageInfo:
    return MessageInfo(
        chat_id=history_message.chat_id,
        message_id=11,
        date=history_message.date + timedelta(days=1),
        mime_type="video/mp4",
        original_name="live.mp4",
        extension=".mp4",
        size=200,
        is_video=True,
        is_animated=False,
        is_round=False,
    )


@pytest.fixture
def store(tmp_path: Path):
    database = tmp_path / ".runtime" / "state.sqlite3"
    state = StateStore(database)
    state.reconcile_targets((GroupTarget(-1001, "群"),))
    try:
        yield state
    finally:
        state.close()


def test_jobs_are_deduplicated_and_live_is_claimed_first(
    store: StateStore,
    history_message: MessageInfo,
    live_message: MessageInfo,
) -> None:
    store.upsert_job(history_message, "群", JobSource.HISTORY)
    store.upsert_job(history_message, "群", JobSource.LIVE)
    store.upsert_job(live_message, "群", JobSource.LIVE)

    assert store.job_count() == 2
    claimed = store.claim_next()
    assert claimed is not None
    assert claimed.source == JobSource.LIVE


def test_recover_inflight_and_preserve_cursors(
    store: StateStore,
    history_message: MessageInfo,
) -> None:
    store.reconcile_targets((GroupTarget(-1001, "群"),))
    store.set_latest_seen(-1001, 50)
    store.set_history_cursor(-1001, 20, complete=False)
    store.upsert_job(history_message, "群", JobSource.HISTORY)
    claimed = store.claim_next()

    assert claimed is not None
    assert store.recover_inflight() == ((-1001, history_message.message_id),)
    group = store.get_group(-1001)
    assert (group.latest_seen_id, group.history_cursor_id, group.history_complete) == (
        50,
        20,
        False,
    )


def test_reconcile_status_transitions_and_counts(
    store: StateStore,
    history_message: MessageInfo,
    live_message: MessageInfo,
    tmp_path: Path,
) -> None:
    added, removed = store.reconcile_targets((GroupTarget(-1001, "新群名"), GroupTarget(-1002, "B 群")))
    assert added == {-1002}
    assert removed == set()

    store.upsert_job(history_message, "新群名", JobSource.HISTORY)
    store.upsert_job(live_message, "新群名", JobSource.CATCHUP)
    live_job = store.claim_next()
    assert live_job is not None
    assert live_job.source == JobSource.CATCHUP
    store.mark_retry(live_job, "temporary", delay_seconds=60)

    history_job = store.claim_next()
    assert history_job is not None
    assert history_job.source == JobSource.HISTORY
    final_path = tmp_path / "downloads" / "history.mp4"
    store.mark_completed(history_job, final_path)

    assert store.counts() == {
        "pending_live": 0,
        "pending_history": 0,
        "retry_wait": 1,
        "completed": 1,
        "permanent_error": 0,
    }

    added, removed = store.reconcile_targets((GroupTarget(-1002, "B 群"),))
    assert added == set()
    assert removed == {-1001}
    assert store.enabled_chat_ids() == {-1002}


def test_mark_permanent_error(store: StateStore, live_message: MessageInfo) -> None:
    store.upsert_job(live_message, "群", JobSource.LIVE)
    job = store.claim_next()
    assert job is not None
    assert job.status == JobStatus.DOWNLOADING
    assert job.attempts == 1

    store.mark_permanent_error(job, "forbidden")
    assert store.counts()["permanent_error"] == 1
