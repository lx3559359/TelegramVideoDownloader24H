from datetime import UTC, datetime, timedelta, timezone

import pytest

from tg_video_downloader.models import JobStatus, MessageInfo, VideoSearchResult
from tg_video_downloader.selective import (
    SEARCH_LIMITS,
    ManualQueueSummary,
    SearchQueueState,
    SelectableVideo,
    is_selectable,
    normalize_search_caption,
    parse_search_dates,
    queue_state_for,
    validate_search_limit,
)


def test_parse_search_dates_uses_inclusive_local_days() -> None:
    china = timezone(timedelta(hours=8))

    start, end = parse_search_dates("2026-08-01", "2026-08-02", china)

    assert start is not None
    assert end is not None
    assert start.isoformat() == "2026-07-31T16:00:00+00:00"
    assert end.isoformat() == "2026-08-02T16:00:00+00:00"
    assert start.tzinfo is UTC
    assert end.tzinfo is UTC


def test_parse_search_dates_accepts_open_bounds() -> None:
    assert parse_search_dates("", "", UTC) == (None, None)


def test_parse_search_dates_can_use_operating_system_local_rules() -> None:
    start, end = parse_search_dates("2026-08-01", "2026-08-01", None)

    assert start == datetime(2026, 8, 1).astimezone(UTC)
    assert end == datetime(2026, 8, 2).astimezone(UTC)


@pytest.mark.parametrize("value", ["2026/08/01", "not-a-date"])
def test_parse_search_dates_rejects_invalid_format(value: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_search_dates(value, "", UTC)


def test_parse_search_dates_rejects_reverse_range() -> None:
    with pytest.raises(ValueError, match="开始日期不能晚于结束日期"):
        parse_search_dates("2026-08-03", "2026-08-02", timezone.utc)


@pytest.mark.parametrize("value", [20, 50, 100])
def test_validate_search_limit_accepts_only_supported_values(value: int) -> None:
    assert validate_search_limit(value) == value


@pytest.mark.parametrize("value", [True, 0, 19, 21, 500])
def test_validate_search_limit_rejects_unsupported_values(value: int) -> None:
    with pytest.raises(ValueError, match="20、50 或 100"):
        validate_search_limit(value)


def test_search_limits_are_stable_for_the_gui() -> None:
    assert SEARCH_LIMITS == (20, 50, 100)


def test_caption_is_single_line_and_bounded() -> None:
    assert normalize_search_caption(" a\n b " + "字" * 200) == (
        "a b " + "字" * 116
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, SearchQueueState.AVAILABLE),
        (JobStatus.PENDING, SearchQueueState.QUEUED),
        (JobStatus.DOWNLOADING, SearchQueueState.QUEUED),
        (JobStatus.RETRY_WAIT, SearchQueueState.QUEUED),
        (JobStatus.COMPLETED, SearchQueueState.COMPLETED),
        (JobStatus.PERMANENT_ERROR, SearchQueueState.RETRYABLE),
    ],
)
def test_queue_state_mapping(
    status: JobStatus | None,
    expected: SearchQueueState,
) -> None:
    assert queue_state_for(status) is expected


def test_selectability_and_summary_defaults() -> None:
    message = MessageInfo(
        chat_id=-1001,
        message_id=1,
        date=datetime(2026, 8, 1, tzinfo=UTC),
        mime_type="video/mp4",
        original_name="lesson.mp4",
        extension=".mp4",
        size=10,
        is_video=True,
        is_animated=False,
        is_round=False,
    )
    result = VideoSearchResult(message, 60, "lesson")
    available = SelectableVideo(result, SearchQueueState.AVAILABLE)
    completed = SelectableVideo(result, SearchQueueState.COMPLETED)

    assert is_selectable(available) is True
    assert is_selectable(completed) is False
    assert ManualQueueSummary() == ManualQueueSummary(0, 0, 0, 0)
