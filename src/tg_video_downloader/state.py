from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tg_video_downloader.models import (
    DownloadJob,
    GroupTarget,
    JobSource,
    JobStatus,
    MessageInfo,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    download_history INTEGER NOT NULL DEFAULT 1,
    latest_seen_id INTEGER,
    history_cursor_id INTEGER,
    history_complete INTEGER NOT NULL DEFAULT 0,
    access_error TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    group_title TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('live','catchup','history')),
    priority INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','downloading','retry_wait','completed','permanent_error')),
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
    output_root TEXT,
    final_path TEXT,
    error TEXT,
    PRIMARY KEY(chat_id, message_id),
    FOREIGN KEY(chat_id) REFERENCES groups(chat_id)
);

CREATE INDEX IF NOT EXISTS jobs_next_idx
ON jobs(status, priority, next_attempt_at, message_date DESC);
"""

PRIORITY = {
    JobSource.LIVE: 0,
    JobSource.CATCHUP: 0,
    JobSource.HISTORY: 10,
}


@dataclass(frozen=True)
class GroupState:
    chat_id: int
    title: str
    enabled: bool
    download_history: bool
    latest_seen_id: int | None
    history_cursor_id: int | None
    history_complete: bool
    access_error: str | None


class StateStore:
    def __init__(self, database: Path) -> None:
        self.database = database.resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(SCHEMA)
        group_columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(groups)").fetchall()
        }
        if "download_history" not in group_columns:
            self._connection.execute(
                "ALTER TABLE groups ADD COLUMN download_history "
                "INTEGER NOT NULL DEFAULT 1"
            )
        job_columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "output_root" not in job_columns:
            self._connection.execute("ALTER TABLE jobs ADD COLUMN output_root TEXT")
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def reconcile_targets(
        self,
        targets: tuple[GroupTarget, ...],
    ) -> tuple[set[int], set[int]]:
        current = self.enabled_chat_ids()
        incoming = {target.chat_id for target in targets}
        added = incoming - current
        removed = current - incoming

        with self._connection:
            self._connection.execute("UPDATE groups SET enabled = 0")
            self._connection.executemany(
                """
                INSERT INTO groups(chat_id, title, enabled, download_history)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    enabled = 1,
                    download_history = excluded.download_history
                """,
                (
                    (target.chat_id, target.title, int(target.download_history))
                    for target in targets
                ),
            )
        return added, removed

    def enabled_chat_ids(self) -> set[int]:
        rows = self._connection.execute(
            "SELECT chat_id FROM groups WHERE enabled = 1"
        ).fetchall()
        return {int(row["chat_id"]) for row in rows}

    def group_states(self) -> tuple[GroupState, ...]:
        rows = self._connection.execute(
            "SELECT * FROM groups ORDER BY chat_id"
        ).fetchall()
        return tuple(self._group_from_row(row) for row in rows)

    def get_group(self, chat_id: int) -> GroupState:
        row = self._connection.execute(
            "SELECT * FROM groups WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"未知群组: {chat_id}")
        return self._group_from_row(row)

    def set_latest_seen(self, chat_id: int, message_id: int) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE groups
                SET latest_seen_id = CASE
                    WHEN latest_seen_id IS NULL OR latest_seen_id < ? THEN ?
                    ELSE latest_seen_id
                END
                WHERE chat_id = ?
                """,
                (message_id, message_id, chat_id),
            )

    def set_history_cursor(
        self,
        chat_id: int,
        message_id: int | None,
        complete: bool,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE groups
                SET history_cursor_id = ?, history_complete = ?
                WHERE chat_id = ?
                """,
                (message_id, int(complete), chat_id),
            )

    def set_access_error(self, chat_id: int, error: str | None) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE groups SET access_error = ? WHERE chat_id = ?",
                (error, chat_id),
            )

    def upsert_job(
        self,
        message: MessageInfo,
        group_title: str,
        source: JobSource,
    ) -> None:
        priority = PRIORITY[source]
        values = (
            message.chat_id,
            message.message_id,
            group_title,
            source.value,
            priority,
            JobStatus.PENDING.value,
            _as_utc(message.date).isoformat(),
            message.mime_type,
            message.original_name,
            message.extension,
            message.size,
            int(message.is_video),
            int(message.is_animated),
            int(message.is_round),
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO jobs(
                    chat_id, message_id, group_title, source, priority, status,
                    message_date, mime_type, original_name, extension,
                    expected_size, is_video, is_animated, is_round
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    group_title = excluded.group_title,
                    source = CASE
                        WHEN jobs.status <> 'completed' AND excluded.priority < jobs.priority
                        THEN excluded.source ELSE jobs.source
                    END,
                    priority = CASE
                        WHEN jobs.status <> 'completed' AND excluded.priority < jobs.priority
                        THEN excluded.priority ELSE jobs.priority
                    END,
                    message_date = CASE WHEN jobs.status <> 'completed'
                        THEN excluded.message_date ELSE jobs.message_date END,
                    mime_type = CASE WHEN jobs.status <> 'completed'
                        THEN excluded.mime_type ELSE jobs.mime_type END,
                    original_name = CASE WHEN jobs.status <> 'completed'
                        THEN excluded.original_name ELSE jobs.original_name END,
                    extension = CASE WHEN jobs.status <> 'completed'
                        THEN excluded.extension ELSE jobs.extension END,
                    expected_size = CASE WHEN jobs.status <> 'completed'
                        THEN excluded.expected_size ELSE jobs.expected_size END,
                    is_video = CASE WHEN jobs.status <> 'completed'
                        THEN excluded.is_video ELSE jobs.is_video END,
                    is_animated = CASE WHEN jobs.status <> 'completed'
                        THEN excluded.is_animated ELSE jobs.is_animated END,
                    is_round = CASE WHEN jobs.status <> 'completed'
                        THEN excluded.is_round ELSE jobs.is_round END
                """,
                values,
            )

    def claim_next(self, now: datetime | None = None) -> DownloadJob | None:
        current_time = _as_utc(now or datetime.now(UTC)).isoformat()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                """
                SELECT jobs.*
                FROM jobs
                JOIN groups ON groups.chat_id = jobs.chat_id
                WHERE groups.enabled = 1
                  AND (jobs.source <> 'history' OR groups.download_history = 1)
                  AND (
                    jobs.status = 'pending'
                    OR (
                        jobs.status = 'retry_wait'
                        AND jobs.next_attempt_at <= ?
                    )
                  )
                ORDER BY jobs.priority ASC, jobs.message_date DESC
                LIMIT 1
                """,
                (current_time,),
            ).fetchone()
            if row is None:
                self._connection.commit()
                return None

            attempts = int(row["attempts"]) + 1
            self._connection.execute(
                """
                UPDATE jobs
                SET status = ?, attempts = ?, next_attempt_at = NULL
                WHERE chat_id = ? AND message_id = ?
                """,
                (
                    JobStatus.DOWNLOADING.value,
                    attempts,
                    row["chat_id"],
                    row["message_id"],
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

        return self._job_from_row(
            row,
            status=JobStatus.DOWNLOADING,
            attempts=attempts,
        )

    def release(self, job: DownloadJob) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'pending', next_attempt_at = NULL, error = NULL
                WHERE chat_id = ? AND message_id = ? AND status = 'downloading'
                """,
                (job.chat_id, job.message_id),
            )

    def bind_output_root(self, job: DownloadJob, root: Path) -> DownloadJob:
        resolved = root.resolve()
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET output_root = ?
                WHERE chat_id = ? AND message_id = ? AND output_root IS NULL
                """,
                (str(resolved), job.chat_id, job.message_id),
            )
            row = self._connection.execute(
                """
                SELECT output_root FROM jobs
                WHERE chat_id = ? AND message_id = ?
                """,
                (job.chat_id, job.message_id),
            ).fetchone()
        if row is None or row["output_root"] is None:
            raise RuntimeError("下载任务输出目录绑定失败")
        return replace(
            job,
            output_root=Path(str(row["output_root"])).resolve(),
        )

    def get_job(self, chat_id: int, message_id: int) -> DownloadJob | None:
        row = self._connection.execute(
            "SELECT * FROM jobs WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        if row is None:
            return None
        return self._job_from_row(
            row,
            status=JobStatus(str(row["status"])),
            attempts=int(row["attempts"]),
        )

    def mark_completed(self, job: DownloadJob, final_path: Path) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = ?, final_path = ?, error = NULL, next_attempt_at = NULL
                WHERE chat_id = ? AND message_id = ?
                """,
                (
                    JobStatus.COMPLETED.value,
                    str(final_path),
                    job.chat_id,
                    job.message_id,
                ),
            )

    def mark_retry(
        self,
        job: DownloadJob,
        error: str,
        delay_seconds: float,
    ) -> None:
        next_attempt = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, next_attempt_at = ?
                WHERE chat_id = ? AND message_id = ?
                """,
                (
                    JobStatus.RETRY_WAIT.value,
                    error,
                    next_attempt.isoformat(),
                    job.chat_id,
                    job.message_id,
                ),
            )

    def mark_permanent_error(self, job: DownloadJob, error: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, next_attempt_at = NULL
                WHERE chat_id = ? AND message_id = ?
                """,
                (
                    JobStatus.PERMANENT_ERROR.value,
                    error,
                    job.chat_id,
                    job.message_id,
                ),
            )

    def recover_inflight(self) -> tuple[tuple[int, int], ...]:
        with self._connection:
            rows = self._connection.execute(
                """
                SELECT chat_id, message_id
                FROM jobs
                WHERE status = ?
                ORDER BY chat_id, message_id
                """,
                (JobStatus.DOWNLOADING.value,),
            ).fetchall()
            self._connection.execute(
                """
                UPDATE jobs
                SET status = ?, next_attempt_at = NULL
                WHERE status = ?
                """,
                (JobStatus.PENDING.value, JobStatus.DOWNLOADING.value),
            )
        return tuple((int(row["chat_id"]), int(row["message_id"])) for row in rows)

    def job_count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()
        return int(row["count"])

    def counts(self) -> dict[str, int]:
        row = self._connection.execute(
            """
            SELECT
                SUM(CASE WHEN jobs.status = 'pending' AND jobs.priority = 0
                              AND groups.enabled = 1
                    THEN 1 ELSE 0 END) AS pending_live,
                SUM(CASE WHEN jobs.status = 'pending' AND jobs.priority = 10
                              AND groups.enabled = 1
                              AND groups.download_history = 1
                    THEN 1 ELSE 0 END) AS pending_history,
                SUM(CASE WHEN jobs.source = 'history'
                              AND jobs.status IN ('pending', 'retry_wait')
                              AND groups.enabled = 1
                              AND groups.download_history = 0
                    THEN 1 ELSE 0 END) AS paused_history,
                SUM(CASE WHEN jobs.status = 'retry_wait'
                              AND groups.enabled = 1
                              AND (
                                  jobs.source <> 'history'
                                  OR groups.download_history = 1
                              )
                    THEN 1 ELSE 0 END) AS retry_wait,
                SUM(CASE WHEN jobs.status = 'completed'
                    THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN jobs.status = 'permanent_error'
                    THEN 1 ELSE 0 END) AS permanent_error
            FROM jobs
            JOIN groups ON groups.chat_id = jobs.chat_id
            """
        ).fetchone()
        return {
            "pending_live": int(row["pending_live"] or 0),
            "pending_history": int(row["pending_history"] or 0),
            "paused_history": int(row["paused_history"] or 0),
            "retry_wait": int(row["retry_wait"] or 0),
            "completed": int(row["completed"] or 0),
            "permanent_error": int(row["permanent_error"] or 0),
        }

    @staticmethod
    def _group_from_row(row: sqlite3.Row) -> GroupState:
        return GroupState(
            chat_id=int(row["chat_id"]),
            title=str(row["title"]),
            enabled=bool(row["enabled"]),
            download_history=bool(row["download_history"]),
            latest_seen_id=row["latest_seen_id"],
            history_cursor_id=row["history_cursor_id"],
            history_complete=bool(row["history_complete"]),
            access_error=row["access_error"],
        )

    @staticmethod
    def _job_from_row(
        row: sqlite3.Row,
        *,
        status: JobStatus,
        attempts: int,
    ) -> DownloadJob:
        message = MessageInfo(
            chat_id=int(row["chat_id"]),
            message_id=int(row["message_id"]),
            date=datetime.fromisoformat(row["message_date"]),
            mime_type=row["mime_type"],
            original_name=row["original_name"],
            extension=str(row["extension"]),
            size=row["expected_size"],
            is_video=bool(row["is_video"]),
            is_animated=bool(row["is_animated"]),
            is_round=bool(row["is_round"]),
        )
        return DownloadJob(
            chat_id=int(row["chat_id"]),
            message_id=int(row["message_id"]),
            group_title=str(row["group_title"]),
            source=JobSource(row["source"]),
            status=status,
            message=message,
            attempts=attempts,
            output_root=(
                None
                if row["output_root"] is None
                else Path(str(row["output_root"])).resolve()
            ),
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
