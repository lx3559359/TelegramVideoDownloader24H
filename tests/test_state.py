import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tg_video_downloader.models import GroupTarget, JobSource, JobStatus, MessageInfo
from tg_video_downloader.selective import ManualQueueSummary
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


def test_legacy_groups_table_migrates_history_policy(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE groups (chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
        "enabled INTEGER NOT NULL DEFAULT 1, latest_seen_id INTEGER, "
        "history_cursor_id INTEGER, history_complete INTEGER NOT NULL DEFAULT 0, "
        "access_error TEXT)"
    )
    connection.execute("INSERT INTO groups(chat_id, title) VALUES(-1001, '旧频道')")
    connection.commit()
    connection.close()

    state = StateStore(database)
    try:
        assert state.get_group(-1001).download_history is True
    finally:
        state.close()


def test_paused_history_does_not_block_live_and_can_resume(
    store: StateStore,
    history_message: MessageInfo,
    live_message: MessageInfo,
) -> None:
    store.reconcile_targets((GroupTarget(-1001, "群", False),))
    store.upsert_job(history_message, "群", JobSource.HISTORY)
    store.upsert_job(live_message, "群", JobSource.LIVE)

    live_job = store.claim_next()
    assert live_job is not None
    assert live_job.source == JobSource.LIVE
    assert store.claim_next() is None
    assert store.counts()["paused_history"] == 1

    store.reconcile_targets((GroupTarget(-1001, "群", True),))
    history_job = store.claim_next()
    assert history_job is not None
    assert history_job.source == JobSource.HISTORY


def test_release_returns_downloading_job_to_pending(
    store: StateStore,
    live_message: MessageInfo,
) -> None:
    store.upsert_job(live_message, "群", JobSource.LIVE)
    job = store.claim_next()
    assert job is not None

    store.release(job)

    assert store.claim_next() is not None


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
        "paused_history": 0,
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


def test_existing_database_adds_output_root_column(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE jobs (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            group_title TEXT NOT NULL,
            source TEXT NOT NULL,
            priority INTEGER NOT NULL,
            status TEXT NOT NULL,
            message_date TEXT NOT NULL,
            mime_type TEXT,
            original_name TEXT,
            extension TEXT NOT NULL,
            expected_size INTEGER,
            is_video INTEGER NOT NULL,
            is_animated INTEGER NOT NULL,
            is_round INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            final_path TEXT,
            error TEXT,
            PRIMARY KEY(chat_id, message_id)
        )
        """
    )
    connection.commit()
    connection.close()

    state = StateStore(database)
    try:
        columns = {
            str(row["name"])
            for row in state._connection.execute("PRAGMA table_info(jobs)")
        }
        assert "output_root" in columns
    finally:
        state.close()


def test_bind_output_root_is_first_writer_wins_and_survives_release(
    tmp_path: Path,
    store: StateStore,
    live_message: MessageInfo,
) -> None:
    store.upsert_job(live_message, "群", JobSource.LIVE)
    job = store.claim_next()
    assert job is not None

    first = store.bind_output_root(job, tmp_path / "first")
    second = store.bind_output_root(first, tmp_path / "second")
    store.release(second)
    claimed_again = store.claim_next()

    assert first.output_root == (tmp_path / "first").resolve()
    assert second.output_root == first.output_root
    assert claimed_again is not None
    assert claimed_again.output_root == first.output_root
    assert store.get_job(job.chat_id, job.message_id) == claimed_again


def test_get_job_returns_none_for_unknown_key(store: StateStore) -> None:
    assert store.get_job(-9999, 123) is None


def test_enqueue_manual_results_is_atomic_idempotent_and_requeues_failure(
    store: StateStore,
    live_message: MessageInfo,
    tmp_path: Path,
) -> None:
    group = GroupTarget(live_message.chat_id, "课程群", download_history=False)
    pending = replace(live_message, message_id=20, original_name="pending.mp4")
    completed = replace(live_message, message_id=21, original_name="done.mp4")
    failed = replace(live_message, message_id=22, original_name="failed.mp4")
    fresh = replace(live_message, message_id=23, original_name="fresh.mp4")
    store.upsert_job(pending, group.title, JobSource.HISTORY)
    store.upsert_job(completed, group.title, JobSource.HISTORY)
    completed_job = store.get_job(group.chat_id, completed.message_id)
    assert completed_job is not None
    final_path = tmp_path / "done.mp4"
    store.mark_completed(completed_job, final_path)
    store.upsert_job(failed, group.title, JobSource.HISTORY)
    failed_job = store.get_job(group.chat_id, failed.message_id)
    assert failed_job is not None
    bound = store.bind_output_root(failed_job, tmp_path / "external")
    store.mark_permanent_error(bound, "deleted")

    summary = store.enqueue_manual_results(
        group,
        (pending, completed, failed, fresh),
    )

    assert summary == ManualQueueSummary(
        added=1,
        requeued=1,
        already_queued=1,
        completed=1,
    )
    retried = store.get_job(group.chat_id, failed.message_id)
    assert retried is not None
    assert retried.status is JobStatus.PENDING
    assert retried.source is JobSource.LIVE
    assert retried.attempts == 0
    assert retried.output_root == (tmp_path / "external").resolve()
    protected = store.get_job(group.chat_id, completed.message_id)
    assert protected is not None
    assert protected.status is JobStatus.COMPLETED
    assert protected.source is JobSource.HISTORY
    assert protected.output_root is None
    unchanged = store.get_job(group.chat_id, pending.message_id)
    assert unchanged is not None
    assert unchanged.status is JobStatus.PENDING
    assert unchanged.source is JobSource.HISTORY
    row = store._connection.execute(
        "SELECT final_path FROM jobs WHERE chat_id = ? AND message_id = ?",
        (group.chat_id, completed.message_id),
    ).fetchone()
    assert row is not None
    assert row["final_path"] == str(final_path)


def test_job_statuses_returns_only_existing_keys(
    store: StateStore,
    live_message: MessageInfo,
) -> None:
    store.upsert_job(live_message, "群", JobSource.LIVE)

    statuses = store.job_statuses(
        (
            (live_message.chat_id, live_message.message_id),
            (live_message.chat_id, 999),
        )
    )

    assert statuses == {
        (live_message.chat_id, live_message.message_id): JobStatus.PENDING
    }


@pytest.mark.parametrize(
    "messages_factory",
    [
        lambda message: (),
        lambda message: (message, message),
        lambda message: (replace(message, chat_id=-2002),),
        lambda message: (replace(message, is_animated=True),),
        lambda message: (
            replace(message, is_video=False, mime_type="application/pdf"),
        ),
    ],
)
def test_enqueue_manual_results_validates_before_writing(
    store: StateStore,
    live_message: MessageInfo,
    messages_factory,
) -> None:
    before = store.job_count()

    with pytest.raises(ValueError):
        store.enqueue_manual_results(
            GroupTarget(live_message.chat_id, "群", False),
            tuple(messages_factory(live_message)),
        )

    assert store.job_count() == before


def test_enqueue_manual_results_rolls_back_the_whole_batch_on_sql_error(
    store: StateStore,
    live_message: MessageInfo,
) -> None:
    first = replace(live_message, message_id=31)
    second = replace(live_message, message_id=32)
    store._connection.execute(
        f"""
        CREATE TRIGGER abort_selected_batch
        BEFORE INSERT ON jobs
        WHEN NEW.message_id = {second.message_id}
        BEGIN
            SELECT RAISE(ABORT, 'forced selected-batch failure');
        END
        """
    )
    store._connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced selected-batch failure"):
        store.enqueue_manual_results(
            GroupTarget(live_message.chat_id, "新群名", False),
            (first, second),
        )

    assert store.get_job(first.chat_id, first.message_id) is None
    assert store.get_job(second.chat_id, second.message_id) is None
    assert store.get_group(live_message.chat_id).title == "群"
