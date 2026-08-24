from __future__ import annotations

import asyncio

from tg_video_downloader.gateway import GroupAccessError, TelegramGateway
from tg_video_downloader.media import is_downloadable_video
from tg_video_downloader.models import GroupTarget, JobSource, MessageInfo
from tg_video_downloader.state import StateStore


class ScannerCoordinator:
    def __init__(self, state: StateStore, gateway: TelegramGateway) -> None:
        self.state = state
        self.gateway = gateway

    async def start(self, targets: tuple[GroupTarget, ...]) -> None:
        self.gateway.set_new_message_handler(self.handle_live)
        await self.apply_targets(targets)
        for chat_id in sorted(self.state.enabled_chat_ids()):
            await self.catch_up_once(chat_id)

    async def apply_targets(
        self,
        targets: tuple[GroupTarget, ...],
    ) -> tuple[set[int], set[int]]:
        added, removed = self.state.reconcile_targets(targets)
        for chat_id in sorted(added):
            group = self.state.get_group(chat_id)
            if group.latest_seen_id is not None:
                continue
            try:
                latest_id = await self.gateway.latest_message_id(chat_id)
            except GroupAccessError as error:
                self.state.set_access_error(chat_id, str(error))
                continue
            self.state.set_latest_seen(chat_id, latest_id)
            self.state.set_access_error(chat_id, None)
        return added, removed

    async def handle_live(self, message: MessageInfo) -> None:
        if message.chat_id not in self.state.enabled_chat_ids():
            return
        group = self.state.get_group(message.chat_id)
        self.state.set_latest_seen(message.chat_id, message.message_id)
        if is_downloadable_video(message):
            self.state.upsert_job(message, group.title, JobSource.LIVE)

    async def catch_up_once(self, chat_id: int) -> None:
        if chat_id not in self.state.enabled_chat_ids():
            return
        group = self.state.get_group(chat_id)
        if group.latest_seen_id is None:
            try:
                latest_id = await self.gateway.latest_message_id(chat_id)
            except GroupAccessError as error:
                self.state.set_access_error(chat_id, str(error))
                return
            self.state.set_latest_seen(chat_id, latest_id)
            self.state.set_access_error(chat_id, None)
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
            return
        self.state.set_access_error(chat_id, None)

    async def scan_once(self, chat_id: int, batch_size: int = 100) -> bool:
        group = self.state.get_group(chat_id)
        if not group.enabled or group.history_complete:
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
            return False

        if exhausted:
            self.state.set_history_cursor(chat_id, cursor, complete=True)
        self.state.set_access_error(chat_id, None)
        return processed > 0

    async def run_scans(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            worked = False
            for group in self.state.group_states():
                if not group.enabled or group.history_complete:
                    continue
                worked = await self.scan_once(group.chat_id) or worked
                await asyncio.sleep(0)

            if worked:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
