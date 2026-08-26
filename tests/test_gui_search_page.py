from datetime import UTC, datetime, timedelta, timezone

import pytest

from tg_video_downloader.gui.search_page import (
    SearchSelectionModel,
    format_search_date,
    format_search_duration,
    format_search_size,
    queue_state_text,
)
from tg_video_downloader.models import MessageInfo, VideoSearchResult
from tg_video_downloader.selective import SearchQueueState, SelectableVideo


def make_selectable_item(
    state: SearchQueueState,
    *,
    message_id: int,
) -> SelectableVideo:
    message = MessageInfo(
        chat_id=-1001,
        message_id=message_id,
        date=datetime(2026, 8, 24, 1, 2, tzinfo=UTC),
        mime_type="video/mp4",
        original_name=f"video-{message_id}.mp4",
        extension=".mp4",
        size=1024,
        is_video=True,
        is_animated=False,
        is_round=False,
    )
    return SelectableVideo(
        VideoSearchResult(message, duration_seconds=65, caption="课程"),
        state,
    )


def make_selectable_items(
    *states: SearchQueueState,
) -> tuple[SelectableVideo, ...]:
    return tuple(
        make_selectable_item(state, message_id=index)
        for index, state in enumerate(states, start=1)
    )


def test_selection_model_only_selects_available_and_retryable_items() -> None:
    items = make_selectable_items(
        SearchQueueState.AVAILABLE,
        SearchQueueState.RETRYABLE,
        SearchQueueState.QUEUED,
        SearchQueueState.COMPLETED,
    )
    model = SearchSelectionModel()
    model.replace(items)

    model.select_eligible()

    assert model.selected_keys == {
        items[0].result.message.message_id,
        items[1].result.message.message_id,
    }
    assert model.selected_results() == (
        items[0].result,
        items[1].result,
    )


def test_selection_model_toggles_available_row_and_preserves_result_order() -> None:
    first, second = make_selectable_items(
        SearchQueueState.AVAILABLE,
        SearchQueueState.RETRYABLE,
    )
    model = SearchSelectionModel()
    model.replace((first, second))

    model.toggle(second.result.message.message_id)
    model.toggle(first.result.message.message_id)

    assert model.selected_results() == (first.result, second.result)
    model.toggle(first.result.message.message_id)
    assert model.selected_results() == (second.result,)


def test_selection_model_rejects_toggle_for_non_selectable_or_unknown_row() -> None:
    item = make_selectable_item(SearchQueueState.COMPLETED, message_id=7)
    model = SearchSelectionModel()
    model.replace((item,))

    model.toggle(7)
    model.toggle(999)

    assert model.selected_keys == set()


def test_selection_model_clear_and_replace_drop_previous_selection() -> None:
    item = make_selectable_item(SearchQueueState.AVAILABLE, message_id=7)
    model = SearchSelectionModel()
    model.replace((item,))
    model.toggle(7)

    model.clear_selection()
    assert model.selected_keys == set()

    model.toggle(7)
    model.clear()
    assert model.items == ()
    assert model.selected_results() == ()


def test_selection_model_marks_selected_rows_queued_after_enqueue() -> None:
    first, second = make_selectable_items(
        SearchQueueState.AVAILABLE,
        SearchQueueState.RETRYABLE,
    )
    model = SearchSelectionModel()
    model.replace((first, second))
    model.toggle(first.result.message.message_id)

    model.mark_selected_queued()

    assert model.items[0].queue_state is SearchQueueState.QUEUED
    assert model.items[1].queue_state is SearchQueueState.RETRYABLE
    assert model.selected_keys == set()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "-"),
        (True, "-"),
        (-1, "-"),
        (float("nan"), "-"),
        (float("inf"), "-"),
        (0, "0 B"),
        (1024, "1.0 KiB"),
        (5 * 1024**2, "5.0 MiB"),
    ],
)
def test_format_search_size_handles_binary_units_and_bad_values(
    value,
    expected: str,
) -> None:
    assert format_search_size(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "-"),
        (True, "-"),
        (-1, "-"),
        (float("inf"), "-"),
        (65, "01:05"),
        (3661, "1:01:01"),
    ],
)
def test_format_search_duration_handles_missing_and_long_values(
    value,
    expected: str,
) -> None:
    assert format_search_duration(value) == expected


def test_format_search_date_uses_requested_timezone() -> None:
    china = timezone(timedelta(hours=8))
    assert format_search_date(
        datetime(2026, 8, 24, 1, 2, tzinfo=UTC),
        china,
    ) == "2026-08-24 09:02"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (SearchQueueState.AVAILABLE, "可加入"),
        (SearchQueueState.QUEUED, "已在队列"),
        (SearchQueueState.COMPLETED, "已完成"),
        (SearchQueueState.RETRYABLE, "可重新排队"),
    ],
)
def test_queue_state_text_is_user_facing(
    state: SearchQueueState,
    expected: str,
) -> None:
    assert queue_state_text(state) == expected
