from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic as monotonic_clock

from tg_video_downloader.gateway import GroupAccessError, TelegramGateway
from tg_video_downloader.media import is_downloadable_video
from tg_video_downloader.models import GroupTarget, JobSource, MessageInfo
from tg_video_downloader.state import StateStore


CATCHUP_INTERVAL_SECONDS = 5 * 60
ACCESS_RETRY_DELAYS = (60, 5 * 60, 30 * 60)


class ScannerCoordinator:
    def __init__(
        self,
        state: StateStore,
        gateway: TelegramGateway,
        *,
        monotonic: Callable[[], float] = monotonic_clock,
    ) -> None:
        self.state = state
        self.gateway = gateway
        self._monotonic = monotonic
        self._access_failures: dict[int, int] = {}
        self._access_retry_at: dict[int, float] = {}

    async def start(self, targets: tuple[GroupTarget, ...]) -> None:
        self.gateway.set_new_message_handler(self.handle_live)
        await self.apply_targets(targets)
        for chat_id in sorted(self.state.enabled_chat_ids()):
            await self.catch_up_once(chat_id)

    async def apply_targets(
        self,
        targets: tuple[GroupTarget, ...],
    ) -> tuple[set[int], set[int]]:
        known_groups = {group.chat_id: group for group in self.state.group_states()}
        added, removed = self.state.reconcile_targets(targets)
        for chat_id in sorted(added):
            group = self.state.get_group(chat_id)
            if group.latest_seen_id is not None:
                previous = known_groups.get(chat_id)
                if previous is not None and not previous.enabled:
                    await self.catch_up_once(chat_id)
                continue
            if not self._can_access(chat_id):
                continue
            try:
                latest_id = await self.gateway.latest_message_id(chat_id)
            except GroupAccessError as error:
                self.state.set_access_error(chat_id, str(error))
                self._record_access_failure(chat_id)
                continue
            self.state.set_latest_seen(chat_id, latest_id)
            self.state.set_access_error(chat_id, None)
            self._record_access_success(chat_id)
        return added, removed

    async def handle_live(self, message: MessageInfo) -> None:
        if message.chat_id not in self.state.enabled_chat_ids():
            return
        group = self.state.get_group(message.chat_id)
        self._record_access_success(message.chat_id)
        self.state.set_access_error(message.chat_id, None)
        self.state.set_latest_seen(message.chat_id, message.message_id)
        if is_downloadable_video(message):
            self.state.upsert_job(message, group.title, JobSource.LIVE)

    async def catch_up_once(self, chat_id: int) -> None:
        if chat_id not in self.state.enabled_chat_ids():
            return
        if not self._can_access(chat_id):
            return
        group = self.state.get_group(chat_id)
        if group.latest_seen_id is None:
            try:
                latest_id = await self.gateway.latest_message_id(chat_id)
            except GroupAccessError as error:
                self.state.set_access_error(chat_id, str(error))
                self._record_access_failure(chat_id)
                return
            self.state.set_latest_seen(chat_id, latest_id)
            self.state.set_access_error(chat_id, None)
            self._record_access_success(chat_id)
            return

        try:
            async for message in self.gateway.iter_newer_messages(
                chat_id,
                min_id=group.latest_seen_id,
            ):
                self.state.set_latest_seen(chat_id, message.message_id)
                if is_downloadable_video(message):
                    self.state.upsert_job(message, group.title, JobSource.CATCHUP)
        except GroupAccessError as error:
            self.state.set_access_error(chat_id, str(error))
            self._record_access_failure(chat_id)
            return
        self.state.set_access_error(chat_id, None)
        self._record_access_success(chat_id)

    async def catch_up_enabled_once(self) -> None:
        for chat_id in sorted(self.state.enabled_chat_ids()):
            await self.catch_up_once(chat_id)
            await asyncio.sleep(0)

    async def run_catchups(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await _wait_or_stop(stop, CATCHUP_INTERVAL_SECONDS)
            if not stop.is_set():
                await self.catch_up_enabled_once()

    async def scan_once(self, chat_id: int, batch_size: int = 100) -> bool:
        group = self.state.get_group(chat_id)
        if not group.enabled or not group.download_history or group.history_complete:
            return False
        if not self._can_access(chat_id):
            return False

        processed = 0
        cursor = group.history_cursor_id
        exhausted = True
        try:
            async for message in self.gateway.iter_older_messages(chat_id, cursor):
                if processed >= batch_size:
                    exhausted = False
                    break
                cursor = message.message_id
                if is_downloadable_video(message):
                    self.state.upsert_job(message, group.title, JobSource.HISTORY)
                self.state.set_history_cursor(chat_id, cursor, complete=False)
                processed += 1
        except GroupAccessError as error:
            self.state.set_access_error(chat_id, str(error))
            self._record_access_failure(chat_id)
            return False

        if exhausted:
            self.state.set_history_cursor(chat_id, cursor, complete=True)
        self.state.set_access_error(chat_id, None)
        self._record_access_success(chat_id)
        return processed > 0

    def _can_access(self, chat_id: int) -> bool:
        return self._monotonic() >= self._access_retry_at.get(chat_id, 0.0)

    def _record_access_failure(self, chat_id: int) -> None:
        failures = self._access_failures.get(chat_id, 0) + 1
        delay = ACCESS_RETRY_DELAYS[min(failures - 1, len(ACCESS_RETRY_DELAYS) - 1)]
        self._access_failures[chat_id] = failures
        self._access_retry_at[chat_id] = self._monotonic() + delay

    def _record_access_success(self, chat_id: int) -> None:
        self._access_failures.pop(chat_id, None)
        self._access_retry_at.pop(chat_id, None)

    async def run_scans(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            worked = False
            for group in self.state.group_states():
                if (
                    not group.enabled
                    or not group.download_history
                    or group.history_complete
                ):
                    continue
                worked = await self.scan_once(group.chat_id) or worked
                await asyncio.sleep(0)

            if worked:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass


async def _wait_or_stop(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass
