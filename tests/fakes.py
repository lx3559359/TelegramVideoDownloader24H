from __future__ import annotations

from pathlib import Path

from tg_video_downloader.models import GroupTarget, MessageInfo


class FakeTelegramGateway:
    def __init__(self, messages: dict[int, list[MessageInfo]] | None = None) -> None:
        self.messages = messages or {}
        self.download_payloads: dict[tuple[int, int], bytes] = {}
        self.download_failures: dict[tuple[int, int], list[Exception]] = {}
        self.downloaded_keys: list[tuple[int, int]] = []
        self.iterated_chat_ids: list[int] = []
        self.handler = None
        self.authorized = True
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def is_authorized(self) -> bool:
        return self.authorized

    async def send_login_code(self, phone: str) -> None:
        return None

    async def complete_login(
        self,
        phone: str,
        code: str,
        password: str | None = None,
    ) -> None:
        self.authorized = True

    async def start_qr_login(self):
        raise RuntimeError("fake QR login is not configured")

    async def refresh_qr_login(self):
        raise RuntimeError("fake QR login is not configured")

    async def wait_qr_login(self) -> None:
        return None

    async def complete_password(self, password: str) -> None:
        self.authorized = True

    async def log_out(self) -> None:
        self.authorized = False

    async def list_groups(self) -> tuple[GroupTarget, ...]:
        return tuple(
            GroupTarget(chat_id, f"群 {chat_id}")
            for chat_id in sorted(self.messages)
        )

    def set_new_message_handler(self, handler) -> None:
        self.handler = handler

    async def latest_message_id(self, chat_id: int) -> int:
        return max((message.message_id for message in self.messages.get(chat_id, [])), default=0)

    async def iter_newer_messages(self, chat_id: int, min_id: int):
        self.iterated_chat_ids.append(chat_id)
        messages = sorted(self.messages.get(chat_id, []), key=lambda message: message.message_id)
        for message in messages:
            if message.message_id > min_id:
                yield message

    async def iter_older_messages(self, chat_id: int, offset_id: int | None):
        self.iterated_chat_ids.append(chat_id)
        messages = sorted(
            self.messages.get(chat_id, []),
            key=lambda message: message.message_id,
            reverse=True,
        )
        for message in messages:
            if not offset_id or message.message_id < offset_id:
                yield message

    async def emit(self, message: MessageInfo) -> None:
        if self.handler is None:
            raise RuntimeError("new-message handler is not registered")
        await self.handler(message)

    async def download_message(
        self,
        chat_id: int,
        message_id: int,
        destination: Path,
    ) -> Path:
        self.downloaded_keys.append((chat_id, message_id))
        failures = self.download_failures.get((chat_id, message_id), [])
        if failures:
            raise failures.pop(0)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.download_payloads[(chat_id, message_id)])
        return destination
