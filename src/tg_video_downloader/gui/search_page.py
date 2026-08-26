from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from dataclasses import replace
from datetime import UTC, datetime, tzinfo
from tkinter import ttk
from typing import Any

from tg_video_downloader.gui.controller import AsyncBridge, GuiController
from tg_video_downloader.gui.date_picker import DatePicker
from tg_video_downloader.models import GroupTarget, VideoSearchResult
from tg_video_downloader.selective import (
    SearchQueueState,
    SelectableVideo,
    is_selectable,
)


class SearchSelectionModel:
    def __init__(self) -> None:
        self.items: tuple[SelectableVideo, ...] = ()
        self._by_id: dict[int, SelectableVideo] = {}
        self.selected_keys: set[int] = set()

    def replace(self, items: tuple[SelectableVideo, ...]) -> None:
        self.items = items
        self._by_id = {
            item.result.message.message_id: item
            for item in items
        }
        self.selected_keys.clear()

    def clear(self) -> None:
        self.replace(())

    def toggle(self, message_id: int) -> None:
        item = self._by_id.get(message_id)
        if item is None or not is_selectable(item):
            return
        if message_id in self.selected_keys:
            self.selected_keys.remove(message_id)
        else:
            self.selected_keys.add(message_id)

    def select_eligible(self) -> None:
        self.selected_keys = {
            item.result.message.message_id
            for item in self.items
            if is_selectable(item)
        }

    def clear_selection(self) -> None:
        self.selected_keys.clear()

    def selected_results(self) -> tuple[VideoSearchResult, ...]:
        return tuple(
            item.result
            for item in self.items
            if item.result.message.message_id in self.selected_keys
        )

    def mark_selected_queued(self) -> None:
        selected = set(self.selected_keys)
        self.items = tuple(
            replace(item, queue_state=SearchQueueState.QUEUED)
            if item.result.message.message_id in selected
            else item
            for item in self.items
        )
        self._by_id = {
            item.result.message.message_id: item
            for item in self.items
        }
        self.selected_keys.clear()


def format_search_size(value: int | float | None) -> str:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        return "-"
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"


def format_search_duration(value: int | float | None) -> str:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        return "-"
    total = int(value)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_search_date(
    value: datetime,
    local_timezone: tzinfo | None = None,
) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    localized = (
        normalized.astimezone(local_timezone)
        if local_timezone is not None
        else normalized.astimezone()
    )
    return localized.strftime("%Y-%m-%d %H:%M")


def queue_state_text(state: SearchQueueState) -> str:
    return {
        SearchQueueState.AVAILABLE: "可加入",
        SearchQueueState.QUEUED: "已在队列",
        SearchQueueState.COMPLETED: "已完成",
        SearchQueueState.RETRYABLE: "可重新排队",
    }[state]


class VideoSearchPage(ttk.Frame):
    def __init__(
        self,
        notebook: ttk.Notebook,
        controller: GuiController,
        bridge: AsyncBridge,
        show_error: Callable[[Exception], None],
    ) -> None:
        super().__init__(notebook, padding=12)
        self.controller = controller
        self.bridge = bridge
        self.show_error = show_error
        self.model = SearchSelectionModel()
        self.search_future: Future[tuple[SelectableVideo, ...]] | None = None
        self._cancel_search_request: Callable[[], None] | None = None
        self.poll_after: str | None = None
        self.generation = 0
        self._after_cancel: Callable[[], None] | None = None
        self._groups_by_label: dict[str, GroupTarget] = {}

        notebook.add(self, text="视频检索")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=2)
        toolbar.columnconfigure(3, weight=2)

        self.target_var = tk.StringVar()
        self.keyword_var = tk.StringVar()
        self.limit_var = tk.StringVar(value="100")
        self.status_var = tk.StringVar(value="尚未检索")
        self.count_var = tk.StringVar(value="结果 0，已选 0")

        ttk.Label(toolbar, text="目标").grid(row=0, column=0, padx=(0, 6))
        self.target_box = ttk.Combobox(
            toolbar,
            textvariable=self.target_var,
            state="readonly",
        )
        self.target_box.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.target_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.clear_results("目标已变化，请重新检索"),
        )
        ttk.Label(toolbar, text="关键词").grid(row=0, column=2, padx=(0, 6))
        ttk.Entry(toolbar, textvariable=self.keyword_var).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(0, 10),
        )
        ttk.Label(toolbar, text="数量").grid(row=0, column=4, padx=(0, 6))
        ttk.Combobox(
            toolbar,
            textvariable=self.limit_var,
            values=("20", "50", "100"),
            state="readonly",
            width=6,
        ).grid(row=0, column=5, padx=(0, 10))

        ttk.Label(toolbar, text="开始日期").grid(
            row=1,
            column=0,
            padx=(0, 6),
            pady=(8, 0),
        )
        self.start_date_picker = DatePicker(toolbar, label="开始日期")
        self.start_date_picker.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(0, 10),
            pady=(8, 0),
        )
        ttk.Label(toolbar, text="结束日期").grid(
            row=1,
            column=2,
            padx=(0, 6),
            pady=(8, 0),
        )
        self.end_date_picker = DatePicker(toolbar, label="结束日期")
        self.end_date_picker.grid(
            row=1,
            column=3,
            sticky="ew",
            padx=(0, 10),
            pady=(8, 0),
        )
        self.search_button = ttk.Button(
            toolbar,
            text="检索",
            command=self.start_search,
        )
        self.search_button.grid(row=1, column=4, pady=(8, 0), padx=(0, 6))
        self.cancel_button = ttk.Button(
            toolbar,
            text="取消检索",
            command=self.cancel_search,
        )
        self.cancel_button.grid(row=1, column=5, pady=(8, 0))
        self.cancel_button.state(["disabled"])

        self.result_tree = ttk.Treeview(
            self,
            columns=(
                "selected",
                "date",
                "name",
                "size",
                "duration",
                "caption",
                "state",
            ),
            show="headings",
            selectmode="browse",
        )
        headings = {
            "selected": "选择",
            "date": "消息日期",
            "name": "文件名",
            "size": "大小",
            "duration": "时长",
            "caption": "说明",
            "state": "队列状态",
        }
        widths = {
            "selected": 54,
            "date": 125,
            "name": 180,
            "size": 82,
            "duration": 68,
            "caption": 230,
            "state": 92,
        }
        for column, heading in headings.items():
            self.result_tree.heading(column, text=heading)
            self.result_tree.column(
                column,
                width=widths[column],
                anchor=(
                    "center"
                    if column in {"selected", "size", "duration", "state"}
                    else "w"
                ),
                stretch=column in {"name", "caption"},
            )
        self.result_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.result_tree.yview,
        )
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.result_tree.configure(yscrollcommand=scrollbar.set)
        self.result_tree.bind("<Double-1>", self._toggle_row)
        self.result_tree.bind("<space>", self._toggle_focused_row)

        footer = ttk.Frame(self)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(footer, textvariable=self.status_var).pack(side="left")
        ttk.Label(footer, textvariable=self.count_var).pack(
            side="left",
            padx=(16, 0),
        )
        self.enqueue_button = ttk.Button(
            footer,
            text="下载选中项",
            command=self.enqueue_selected,
        )
        self.enqueue_button.pack(side="right")
        self.clear_button = ttk.Button(
            footer,
            text="清除选择",
            command=self._clear_selection,
        )
        self.clear_button.pack(side="right", padx=(0, 8))
        self.select_all_button = ttk.Button(
            footer,
            text="选择当前结果",
            command=self._select_eligible,
        )
        self.select_all_button.pack(side="right", padx=(0, 8))
        self.enqueue_button.state(["disabled"])
        self.clear_button.state(["disabled"])
        self.select_all_button.state(["disabled"])
        self.refresh_targets()

    def refresh_targets(self) -> None:
        groups = self.controller.selected_groups()
        self._groups_by_label = {
            f"{group.title} ({group.chat_id})": group for group in groups
        }
        self.target_box.configure(values=tuple(self._groups_by_label))
        if self.target_var.get() not in self._groups_by_label:
            self.target_var.set(next(iter(self._groups_by_label), ""))
            self.clear_results(
                "请选择一个已监听目标" if not groups else "尚未检索"
            )

    def start_search(self) -> None:
        if self.search_future is not None and not self.search_future.done():
            return
        group = self._groups_by_label.get(self.target_var.get())
        if group is None:
            self.show_error(ValueError("请选择一个已监听的群组或频道"))
            return
        self.clear_results("正在检索")
        self.generation += 1
        generation = self.generation
        self.target_box.configure(state="disabled")
        self.search_button.state(["disabled"])
        self.cancel_button.state(["!disabled"])
        try:
            operation = self.controller.search_videos(
                group.chat_id,
                self.keyword_var.get(),
                self.start_date_picker.get(),
                self.end_date_picker.get(),
                int(self.limit_var.get()),
            )
            submission = self.bridge.submit_cancellable(operation)
            self.search_future = submission.future
            self._cancel_search_request = submission.cancel
        except Exception as error:
            self._cancel_search_request = None
            self._finish_search_controls()
            self.show_error(error)
            return
        self.poll_after = self.after(
            100,
            lambda: self._poll_search(generation),
        )

    def cancel_search(
        self,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self.generation += 1
        if self.search_future is not None and not self.search_future.done():
            self._after_cancel = on_finished
            if self._cancel_search_request is not None:
                self._cancel_search_request()
            return
        self.clear_results("已取消")
        self._finish_search_controls()
        if on_finished is not None:
            on_finished()

    def close(self) -> None:
        self.start_date_picker.close_popup()
        self.end_date_picker.close_popup()
        self.generation += 1
        if self.poll_after is not None:
            try:
                self.after_cancel(self.poll_after)
            except tk.TclError:
                pass
            self.poll_after = None
        if self.search_future is not None and not self.search_future.done():
            if self._cancel_search_request is not None:
                self._cancel_search_request()
        self.search_future = None
        self._cancel_search_request = None
        self._after_cancel = None
        self.model.clear()

    def clear_results(self, status: str) -> None:
        self.model.clear()
        self.result_tree.delete(*self.result_tree.get_children())
        self.status_var.set(status)
        self.count_var.set("结果 0，已选 0")
        self.enqueue_button.state(["disabled"])
        self.clear_button.state(["disabled"])
        self.select_all_button.state(["disabled"])

    def _finish_search_controls(self) -> None:
        self.target_box.configure(state="readonly")
        self.search_button.state(["!disabled"])
        self.cancel_button.state(["disabled"])
        self.poll_after = None

    def _poll_search(self, generation: int) -> None:
        future = self.search_future
        if future is None:
            self._finish_search_controls()
            return
        if not future.done():
            self.poll_after = self.after(
                100,
                lambda: self._poll_search(generation),
            )
            return
        self.search_future = None
        self._cancel_search_request = None
        try:
            items = future.result()
        except CancelledError:
            self.clear_results("已取消")
        except Exception as error:
            self.clear_results("检索失败")
            self.show_error(error)
        else:
            if generation == self.generation:
                self.model.replace(items)
                self._render_results()
                self.status_var.set(f"检索完成，共 {len(items)} 条")
            else:
                self.clear_results("已取消")
        finally:
            self._finish_search_controls()
            callback, self._after_cancel = self._after_cancel, None
            if callback is not None:
                callback()

    def _toggle_message(self, message_id: int) -> None:
        self.model.toggle(message_id)
        self._render_results()

    def _toggle_row(self, event: tk.Event[Any]) -> str:
        item_id = self.result_tree.identify_row(event.y)
        if item_id:
            self._toggle_message(int(item_id))
        return "break"

    def _toggle_focused_row(self, _event: tk.Event[Any]) -> str:
        item_id = self.result_tree.focus()
        if item_id:
            self._toggle_message(int(item_id))
        return "break"

    def _select_eligible(self) -> None:
        self.model.select_eligible()
        self._render_results()

    def _clear_selection(self) -> None:
        self.model.clear_selection()
        self._render_results()

    def _render_results(self) -> None:
        self.result_tree.delete(*self.result_tree.get_children())
        for item in self.model.items:
            message = item.result.message
            selected = message.message_id in self.model.selected_keys
            marker = "☑" if selected else ("☐" if is_selectable(item) else "")
            fallback_name = f"video_{message.message_id}{message.extension or ''}"
            self.result_tree.insert(
                "",
                "end",
                iid=str(message.message_id),
                values=(
                    marker,
                    format_search_date(message.date),
                    message.original_name or fallback_name,
                    format_search_size(message.size),
                    format_search_duration(item.result.duration_seconds),
                    item.result.caption or "-",
                    queue_state_text(item.queue_state),
                ),
            )
        selected_count = len(self.model.selected_keys)
        self.count_var.set(f"结果 {len(self.model.items)}，已选 {selected_count}")
        self.enqueue_button.state(
            ["!disabled"] if selected_count else ["disabled"]
        )
        self.clear_button.state(
            ["!disabled"] if selected_count else ["disabled"]
        )
        self.select_all_button.state(
            ["!disabled"]
            if any(is_selectable(item) for item in self.model.items)
            else ["disabled"]
        )

    def enqueue_selected(self) -> None:
        group = self._groups_by_label.get(self.target_var.get())
        if group is None:
            self.show_error(ValueError("请选择一个已监听的群组或频道"))
            return
        selected = self.model.selected_results()
        try:
            summary = self.controller.enqueue_selected_videos(
                group.chat_id,
                selected,
            )
        except Exception as error:
            self.show_error(error)
            return
        self.model.mark_selected_queued()
        self._render_results()
        self.status_var.set(
            f"新增 {summary.added}，重新排队 {summary.requeued}，"
            f"已在队列 {summary.already_queued}，已完成 {summary.completed}"
        )
