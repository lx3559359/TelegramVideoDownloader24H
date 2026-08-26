from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum

from tg_video_downloader.models import JobStatus, VideoSearchResult


SEARCH_LIMITS = (20, 50, 100)
MAX_SEARCH_CANDIDATES = 500


class SearchQueueState(StrEnum):
    AVAILABLE = "available"
    QUEUED = "queued"
    COMPLETED = "completed"
    RETRYABLE = "retryable"


@dataclass(frozen=True)
class SelectableVideo:
    result: VideoSearchResult
    queue_state: SearchQueueState


@dataclass(frozen=True)
class ManualQueueSummary:
    added: int = 0
    requeued: int = 0
    already_queued: int = 0
    completed: int = 0


def _optional_date(value: str, label: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{label}必须使用 YYYY-MM-DD 格式") from error


def parse_search_dates(
    start_text: str,
    end_text: str,
    local_timezone: tzinfo | None,
) -> tuple[datetime | None, datetime | None]:
    start_date = _optional_date(start_text, "开始日期")
    end_date = _optional_date(end_text, "结束日期")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    start = (
        datetime.combine(start_date, time.min, tzinfo=local_timezone).astimezone(UTC)
        if start_date is not None
        else None
    )
    end = (
        datetime.combine(
            end_date + timedelta(days=1),
            time.min,
            tzinfo=local_timezone,
        ).astimezone(UTC)
        if end_date is not None
        else None
    )
    return start, end


def validate_search_limit(value: int) -> int:
    if isinstance(value, bool) or value not in SEARCH_LIMITS:
        raise ValueError("结果数量必须是 20、50 或 100")
    return value


def normalize_search_caption(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text[:120].rstrip()


def queue_state_for(status: JobStatus | None) -> SearchQueueState:
    if status is None:
        return SearchQueueState.AVAILABLE
    if status is JobStatus.COMPLETED:
        return SearchQueueState.COMPLETED
    if status is JobStatus.PERMANENT_ERROR:
        return SearchQueueState.RETRYABLE
    return SearchQueueState.QUEUED


def is_selectable(item: SelectableVideo) -> bool:
    return item.queue_state in {
        SearchQueueState.AVAILABLE,
        SearchQueueState.RETRYABLE,
    }
