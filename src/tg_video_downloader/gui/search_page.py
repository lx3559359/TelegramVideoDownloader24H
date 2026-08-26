from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, tzinfo

from tg_video_downloader.models import VideoSearchResult
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
    zone = local_timezone or datetime.now().astimezone().tzinfo or UTC
    return normalized.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def queue_state_text(state: SearchQueueState) -> str:
    return {
        SearchQueueState.AVAILABLE: "可加入",
        SearchQueueState.QUEUED: "已在队列",
        SearchQueueState.COMPLETED: "已完成",
        SearchQueueState.RETRYABLE: "可重新排队",
    }[state]
