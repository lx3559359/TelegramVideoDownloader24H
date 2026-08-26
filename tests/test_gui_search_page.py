import tkinter as tk
from concurrent.futures import Future
from datetime import UTC, date, datetime, timedelta, timezone
from tkinter import ttk
from types import SimpleNamespace

import pytest

from tg_video_downloader.gui.search_page import (
    SearchSelectionModel,
    VideoSearchPage,
    format_search_date,
    format_search_duration,
    format_search_size,
    queue_state_text,
)
from tg_video_downloader.models import GroupTarget, MessageInfo, VideoSearchResult
from tg_video_downloader.selective import (
    ManualQueueSummary,
    SearchQueueState,
    SelectableVideo,
)


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


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    try:
        yield root
    finally:
        root.update_idletasks()
        root.destroy()


class FakeSearchController:
    def __init__(self) -> None:
        self.groups = (GroupTarget(-1001, "课程群", False),)
        self.search_calls: list[tuple[object, ...]] = []
        self.enqueue_calls: list[tuple[int, tuple[VideoSearchResult, ...]]] = []
        self.summary = ManualQueueSummary(added=1)

    def selected_groups(self) -> tuple[GroupTarget, ...]:
        return self.groups

    def search_videos(self, *arguments):
        self.search_calls.append(arguments)
        return object()

    def enqueue_selected_videos(
        self,
        chat_id: int,
        results: tuple[VideoSearchResult, ...],
    ) -> ManualQueueSummary:
        self.enqueue_calls.append((chat_id, results))
        return self.summary


class FakeSearchBridge:
    def __init__(self) -> None:
        self.future: Future[tuple[SelectableVideo, ...]] = Future()
        self.submitted: list[object] = []
        self.cancel_requested = False

    def submit_cancellable(self, operation: object):
        self.submitted.append(operation)

        def cancel() -> None:
            self.cancel_requested = True
            self.future.cancel()

        return SimpleNamespace(future=self.future, cancel=cancel)


def install_fake_scheduler(page: VideoSearchPage):
    callbacks: dict[str, object] = {}
    cancelled: list[str] = []

    def schedule(_delay: int, callback) -> str:
        identifier = f"after-{len(callbacks) + 1}"
        callbacks[identifier] = callback
        return identifier

    def cancel(identifier: str) -> None:
        cancelled.append(identifier)
        callbacks.pop(identifier, None)

    page.after = schedule
    page.after_cancel = cancel
    return callbacks, cancelled


def test_search_page_builds_lightweight_controls_and_no_timer_until_search(
    tk_root: tk.Tk,
) -> None:
    notebook = ttk.Notebook(tk_root)
    page = VideoSearchPage(
        notebook,
        controller=FakeSearchController(),
        bridge=FakeSearchBridge(),
        show_error=lambda _error: None,
    )

    assert notebook.tab(page, "text") == "视频检索"
    assert page.limit_var.get() == "100"
    assert page.start_date_picker.get() == ""
    assert page.end_date_picker.get() == ""
    assert page.start_date_picker.display_var.get() == "不限"
    assert page.end_date_picker.display_var.get() == "不限"
    assert str(page.start_date_picker.entry.cget("state")) == "readonly"
    assert str(page.end_date_picker.entry.cget("state")) == "readonly"
    assert page.start_date_picker.popup is None
    assert page.end_date_picker.popup is None
    assert page.search_future is None
    assert page.poll_after is None
    assert tuple(str(value) for value in page.result_tree["show"]) == ("headings",)
    assert tuple(str(value) for value in page.result_tree["columns"]) == (
        "selected",
        "date",
        "name",
        "size",
        "duration",
        "caption",
        "state",
    )
    assert page.cancel_button.instate(["disabled"])
    assert page.enqueue_button.instate(["disabled"])
    page.close()


def test_search_page_passes_selected_iso_dates_to_controller(
    tk_root: tk.Tk,
) -> None:
    notebook = ttk.Notebook(tk_root)
    controller = FakeSearchController()
    page = VideoSearchPage(
        notebook,
        controller=controller,
        bridge=FakeSearchBridge(),
        show_error=lambda _error: None,
    )
    install_fake_scheduler(page)
    page.start_date_picker.set_date(date(2026, 8, 1))
    page.end_date_picker.set_date(date(2026, 8, 26))

    page.start_search()

    assert controller.search_calls == [
        (-1001, "", "2026-08-01", "2026-08-26", 100)
    ]
    page.close()


def test_search_page_date_pickers_fit_900x720(tk_root: tk.Tk) -> None:
    tk_root.deiconify()
    tk_root.geometry("900x720")
    notebook = ttk.Notebook(tk_root)
    notebook.pack(fill="both", expand=True)
    page = VideoSearchPage(
        notebook,
        controller=FakeSearchController(),
        bridge=FakeSearchBridge(),
        show_error=lambda _error: None,
    )
    try:
        tk_root.update()
        assert page.start_date_picker.winfo_viewable()
        assert page.end_date_picker.winfo_viewable()
        assert page.search_button.winfo_viewable()
        assert page.result_tree.winfo_viewable()
        assert page.enqueue_button.winfo_viewable()
        assert page.start_date_picker.popup is None
        assert page.end_date_picker.popup is None

        page.start_date_picker.open_popup()
        assert page.start_date_picker.popup is not None
        page.start_date_picker.close_popup()
        assert page.start_date_picker.popup is None
    finally:
        page.close()
        notebook.destroy()
        tk_root.withdraw()


def test_search_page_completes_one_future_and_returns_to_idle(
    tk_root: tk.Tk,
) -> None:
    notebook = ttk.Notebook(tk_root)
    controller = FakeSearchController()
    bridge = FakeSearchBridge()
    item = make_selectable_item(SearchQueueState.AVAILABLE, message_id=7)
    bridge.future.set_result((item,))
    page = VideoSearchPage(
        notebook,
        controller=controller,
        bridge=bridge,
        show_error=lambda _error: None,
    )
    callbacks, _ = install_fake_scheduler(page)

    page.start_search()

    assert str(page.target_box.cget("state")) == "disabled"
    assert len(callbacks) == 1
    callback = next(iter(callbacks.values()))
    callback()
    assert controller.search_calls == [(-1001, "", "", "", 100)]
    assert page.search_future is None
    assert page.poll_after is None
    assert str(page.target_box.cget("state")) == "readonly"
    assert page.result_tree.get_children() == ("7",)
    assert page.status_var.get() == "检索完成，共 1 条"
    assert len(callbacks) == 1
    page.close()


def test_search_page_cancellation_runs_cleanup_callback_without_partial_rows(
    tk_root: tk.Tk,
) -> None:
    notebook = ttk.Notebook(tk_root)
    bridge = FakeSearchBridge()
    page = VideoSearchPage(
        notebook,
        controller=FakeSearchController(),
        bridge=bridge,
        show_error=lambda _error: None,
    )
    callbacks, _ = install_fake_scheduler(page)
    finished: list[bool] = []
    page.start_search()

    page.cancel_search(on_finished=lambda: finished.append(True))
    callback = next(iter(callbacks.values()))
    callback()

    assert bridge.future.cancelled() is True
    assert bridge.cancel_requested is True
    assert page.status_var.get() == "已取消"
    assert page.result_tree.get_children() == ()
    assert finished == [True]
    assert page.poll_after is None
    page.close()


def test_search_page_discards_result_when_running_future_cannot_cancel(
    tk_root: tk.Tk,
) -> None:
    notebook = ttk.Notebook(tk_root)
    bridge = FakeSearchBridge()
    assert bridge.future.set_running_or_notify_cancel() is True
    page = VideoSearchPage(
        notebook,
        controller=FakeSearchController(),
        bridge=bridge,
        show_error=lambda _error: None,
    )
    callbacks, _ = install_fake_scheduler(page)
    finished: list[bool] = []
    page.start_search()
    page.cancel_search(on_finished=lambda: finished.append(True))
    bridge.future.set_result(
        (make_selectable_item(SearchQueueState.AVAILABLE, message_id=8),)
    )

    callback = next(iter(callbacks.values()))
    callback()

    assert page.result_tree.get_children() == ()
    assert page.status_var.get() == "已取消"
    assert bridge.cancel_requested is True
    assert finished == [True]
    assert page.poll_after is None
    page.close()


def test_search_page_selects_and_enqueues_without_starting_service(
    tk_root: tk.Tk,
) -> None:
    notebook = ttk.Notebook(tk_root)
    controller = FakeSearchController()
    controller.summary = ManualQueueSummary(
        added=1,
        requeued=2,
        already_queued=3,
        completed=4,
    )
    page = VideoSearchPage(
        notebook,
        controller=controller,
        bridge=FakeSearchBridge(),
        show_error=lambda _error: None,
    )
    item = make_selectable_item(SearchQueueState.AVAILABLE, message_id=7)
    page.model.replace((item,))
    page.model.toggle(7)
    page._render_results()

    page.enqueue_selected()

    assert controller.enqueue_calls == [(-1001, (item.result,))]
    assert page.model.items[0].queue_state is SearchQueueState.QUEUED
    assert page.status_var.get() == (
        "新增 1，重新排队 2，已在队列 3，已完成 4"
    )
    assert page.enqueue_button.instate(["disabled"])
    page.close()


def test_search_page_target_refresh_clears_stale_results(tk_root: tk.Tk) -> None:
    notebook = ttk.Notebook(tk_root)
    controller = FakeSearchController()
    page = VideoSearchPage(
        notebook,
        controller=controller,
        bridge=FakeSearchBridge(),
        show_error=lambda _error: None,
    )
    page.model.replace(
        (make_selectable_item(SearchQueueState.AVAILABLE, message_id=7),)
    )
    page._render_results()
    controller.groups = (GroupTarget(-2002, "新目标", False),)

    page.refresh_targets()

    assert page.target_var.get() == "新目标 (-2002)"
    assert page.model.items == ()
    assert page.status_var.get() == "尚未检索"
    page.close()


def test_search_page_close_cancels_future_and_poll_callback(tk_root: tk.Tk) -> None:
    notebook = ttk.Notebook(tk_root)
    bridge = FakeSearchBridge()
    page = VideoSearchPage(
        notebook,
        controller=FakeSearchController(),
        bridge=bridge,
        show_error=lambda _error: None,
    )
    _, cancelled = install_fake_scheduler(page)
    page.start_search()
    poll_after = page.poll_after

    page.close()

    assert bridge.future.cancelled() is True
    assert bridge.cancel_requested is True
    assert cancelled == [poll_after]
    assert page.poll_after is None
    assert page.search_future is None
