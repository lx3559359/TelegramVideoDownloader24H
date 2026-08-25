from __future__ import annotations

import mimetypes
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from telethon import TelegramClient, errors, events

from tg_video_downloader.models import Credentials, GroupTarget, MessageInfo
from tg_video_downloader.paths import ProjectPaths


class AuthenticationRequiredError(RuntimeError):
    pass


class InvalidApiCredentialsError(ValueError):
    pass


class QrLoginExpiredError(RuntimeError):
    pass


class GroupAccessError(RuntimeError):
    pass


class PermanentMessageError(RuntimeError):
    pass


class TransientTelegramError(RuntimeError):
    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class QrLoginChallenge:
    url: str
    expires_at: datetime


MessageHandler = Callable[[MessageInfo], Awaitable[None]]
DownloadProgressCallback = Callable[[int, int | None], None]

DOWNLOAD_CHUNK_SIZE = 512 * 1024
SYNC_BYTES = 8 * 1024 * 1024
SYNC_SECONDS = 5.0


class TelegramGateway(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def is_authorized(self) -> bool: ...

    async def send_login_code(self, phone: str) -> None: ...

    async def complete_login(
        self,
        phone: str,
        code: str,
        password: str | None = None,
    ) -> None: ...

    async def start_qr_login(self) -> QrLoginChallenge: ...

    async def refresh_qr_login(self) -> QrLoginChallenge: ...

    async def wait_qr_login(self) -> None: ...

    async def complete_password(self, password: str) -> None: ...

    async def log_out(self) -> None: ...

    async def list_groups(self) -> tuple[GroupTarget, ...]: ...

    def set_new_message_handler(self, handler: MessageHandler) -> None: ...

    async def latest_message_id(self, chat_id: int) -> int: ...

    def iter_newer_messages(
        self,
        chat_id: int,
        min_id: int,
    ) -> AsyncIterator[MessageInfo]: ...

    def iter_older_messages(
        self,
        chat_id: int,
        offset_id: int | None,
    ) -> AsyncIterator[MessageInfo]: ...

    async def download_message(
        self,
        chat_id: int,
        message_id: int,
        destination: Path,
        *,
        offset: int = 0,
        progress_callback: DownloadProgressCallback | None = None,
    ) -> Path: ...


def normalize_message(message: Any, chat_id: int) -> MessageInfo:
    document = getattr(message, "document", None)
    file_info = getattr(message, "file", None)
    attributes = getattr(document, "attributes", ()) if document is not None else ()

    original_name: str | None = None
    is_video_attribute = False
    is_animated = False
    is_round = False
    for attribute in attributes:
        attribute_name = type(attribute).__name__
        if attribute_name == "DocumentAttributeFilename":
            original_name = getattr(attribute, "file_name", None)
        elif attribute_name == "DocumentAttributeAnimated":
            is_animated = True
        elif attribute_name == "DocumentAttributeVideo":
            is_video_attribute = True
            is_round = bool(getattr(attribute, "round_message", False))

    if original_name is None and file_info is not None:
        original_name = getattr(file_info, "name", None)

    mime_type = getattr(document, "mime_type", None)
    if mime_type is None and file_info is not None:
        mime_type = getattr(file_info, "mime_type", None)

    extension = getattr(file_info, "ext", None) if file_info is not None else None
    if not extension and original_name:
        extension = Path(original_name).suffix
    if not extension and mime_type:
        extension = mimetypes.guess_extension(mime_type, strict=False)

    size = getattr(document, "size", None)
    if size is None and file_info is not None:
        size = getattr(file_info, "size", None)

    is_video = is_video_attribute or getattr(message, "video", None) is not None
    is_animated = is_animated or getattr(message, "gif", None) is not None
    is_round = is_round or getattr(message, "video_note", None) is not None

    return MessageInfo(
        chat_id=int(chat_id),
        message_id=int(message.id),
        date=message.date,
        mime_type=mime_type,
        original_name=original_name,
        extension=extension or "",
        size=size,
        is_video=is_video,
        is_animated=is_animated,
        is_round=is_round,
    )


class TelethonGateway:
    def __init__(
        self,
        paths: ProjectPaths,
        credentials: Credentials,
        *,
        client_factory: Callable[..., Any] = TelegramClient,
    ) -> None:
        paths.ensure_directories()
        credentials.validate()
        self._client = client_factory(
            str(paths.session),
            credentials.api_id,
            credentials.api_hash,
            auto_reconnect=True,
            connection_retries=-1,
            retry_delay=5,
            flood_sleep_threshold=60,
        )
        self._event_callback: Callable[[Any], Awaitable[None]] | None = None
        self._password_required = False
        self._qr_login: Any | None = None

    async def connect(self) -> None:
        try:
            await self._client.connect()
        except Exception as error:
            raise _mapped_error(error) from error

    async def disconnect(self) -> None:
        try:
            await self._client.disconnect()
        except Exception as error:
            raise _mapped_error(error) from error
        finally:
            self._qr_login = None
            self._password_required = False

    async def is_authorized(self) -> bool:
        try:
            return bool(await self._client.is_user_authorized())
        except Exception as error:
            raise _mapped_error(error) from error

    async def send_login_code(self, phone: str) -> None:
        try:
            await self._client.send_code_request(phone)
            self._password_required = False
        except Exception as error:
            raise _mapped_error(error) from error

    async def complete_login(
        self,
        phone: str,
        code: str,
        password: str | None = None,
    ) -> None:
        if self._password_required:
            await self.complete_password(password or "")
            return
        try:
            await self._client.sign_in(phone=phone, code=code)
        except errors.SessionPasswordNeededError as error:
            self._password_required = True
            if not password:
                raise AuthenticationRequiredError("需要二步验证密码") from error
            await self.complete_password(password)
        except Exception as error:
            raise _mapped_error(error) from error

    async def start_qr_login(self) -> QrLoginChallenge:
        try:
            self._qr_login = await self._client.qr_login()
            return self._qr_challenge()
        except Exception as error:
            raise _mapped_error(error) from error

    async def refresh_qr_login(self) -> QrLoginChallenge:
        if self._qr_login is None:
            raise ValueError("请先开始二维码登录")
        try:
            await self._qr_login.recreate()
            return self._qr_challenge()
        except Exception as error:
            raise _mapped_error(error) from error

    async def wait_qr_login(self) -> None:
        if self._qr_login is None:
            raise ValueError("请先开始二维码登录")
        try:
            await self._qr_login.wait()
        except TimeoutError as error:
            raise QrLoginExpiredError("二维码已过期") from error
        except errors.SessionPasswordNeededError as error:
            self._password_required = True
            raise AuthenticationRequiredError("需要二步验证密码") from error
        except Exception as error:
            raise _mapped_error(error) from error

    async def complete_password(self, password: str) -> None:
        if not password:
            raise AuthenticationRequiredError("需要二步验证密码")
        try:
            await self._client.sign_in(password=password)
        except Exception as error:
            raise _mapped_error(error) from error
        self._password_required = False

    async def log_out(self) -> None:
        try:
            await self._client.log_out()
        except Exception as error:
            raise _mapped_error(error) from error
        self._qr_login = None
        self._password_required = False

    def _qr_challenge(self) -> QrLoginChallenge:
        if self._qr_login is None:
            raise ValueError("请先开始二维码登录")
        return QrLoginChallenge(
            url=str(self._qr_login.url),
            expires_at=self._qr_login.expires,
        )

    async def list_groups(self) -> tuple[GroupTarget, ...]:
        groups: list[GroupTarget] = []
        try:
            async for dialog in self._client.iter_dialogs():
                if dialog.is_group is True or dialog.is_channel is True:
                    groups.append(
                        GroupTarget(
                            int(dialog.id),
                            str(dialog.name),
                            download_history=False,
                        )
                    )
        except Exception as error:
            raise _mapped_error(error) from error
        return tuple(sorted(groups, key=lambda group: group.title.casefold()))

    def set_new_message_handler(self, handler: MessageHandler) -> None:
        if self._event_callback is not None:
            self._client.remove_event_handler(self._event_callback)

        async def callback(event: Any) -> None:
            await handler(normalize_message(event.message, int(event.chat_id)))

        self._event_callback = callback
        self._client.add_event_handler(callback, events.NewMessage())

    async def latest_message_id(self, chat_id: int) -> int:
        try:
            async for message in self._client.iter_messages(chat_id, limit=1):
                return int(message.id)
            return 0
        except Exception as error:
            raise _mapped_error(error) from error

    async def iter_newer_messages(
        self,
        chat_id: int,
        min_id: int,
    ) -> AsyncIterator[MessageInfo]:
        try:
            async for message in self._client.iter_messages(
                chat_id,
                min_id=min_id,
                reverse=True,
            ):
                yield normalize_message(message, chat_id)
        except Exception as error:
            raise _mapped_error(error) from error

    async def iter_older_messages(
        self,
        chat_id: int,
        offset_id: int | None,
    ) -> AsyncIterator[MessageInfo]:
        try:
            async for message in self._client.iter_messages(
                chat_id,
                offset_id=offset_id or 0,
            ):
                yield normalize_message(message, chat_id)
        except Exception as error:
            raise _mapped_error(error) from error

    async def download_message(
        self,
        chat_id: int,
        message_id: int,
        destination: Path,
        *,
        offset: int = 0,
        progress_callback: DownloadProgressCallback | None = None,
    ) -> Path:
        try:
            message = await self._client.get_messages(chat_id, ids=message_id)
            if message is None:
                raise PermanentMessageError("消息不存在或已被删除")
            media = getattr(message, "media", None)
            if media is None:
                raise PermanentMessageError("消息没有可下载的媒体")

            total = getattr(getattr(message, "document", None), "size", None)
            destination.parent.mkdir(parents=True, exist_ok=True)
            mode = "ab" if offset else "wb"
            downloaded = offset
            unsynced = 0
            last_sync = time.monotonic()
            stream = self._client.iter_download(
                media,
                offset=offset,
                request_size=DOWNLOAD_CHUNK_SIZE,
                chunk_size=DOWNLOAD_CHUNK_SIZE,
            )
            try:
                with destination.open(mode, buffering=0) as handle:
                    async for chunk in stream:
                        handle.write(chunk)
                        downloaded += len(chunk)
                        unsynced += len(chunk)
                        if progress_callback is not None:
                            progress_callback(downloaded, total)
                        now = time.monotonic()
                        if (
                            unsynced >= SYNC_BYTES
                            or now - last_sync >= SYNC_SECONDS
                        ):
                            os.fsync(handle.fileno())
                            unsynced = 0
                            last_sync = now
                    os.fsync(handle.fileno())
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    await close()
            return destination
        except (
            AuthenticationRequiredError,
            GroupAccessError,
            PermanentMessageError,
            TransientTelegramError,
        ):
            raise
        except Exception as error:
            raise _mapped_error(error) from error


def _mapped_error(error: Exception) -> Exception:
    if isinstance(error, errors.ApiIdInvalidError):
        return InvalidApiCredentialsError("API ID 或 API Hash 无效")
    if isinstance(error, errors.PasswordHashInvalidError):
        return AuthenticationRequiredError("二步验证密码错误")
    if isinstance(error, errors.FloodWaitError):
        seconds = int(getattr(error, "seconds", 0))
        return TransientTelegramError(
            f"Telegram 要求等待 {seconds} 秒",
            retry_after=seconds,
        )
    if isinstance(
        error,
        (
            errors.AuthKeyError,
            errors.UnauthorizedError,
            errors.AuthKeyUnregisteredError,
            errors.SessionRevokedError,
            errors.UserDeactivatedBanError,
        ),
    ):
        return AuthenticationRequiredError("Telegram 登录已失效，需要重新登录")
    if isinstance(
        error,
        (
            errors.ChannelPrivateError,
            errors.ChatAdminRequiredError,
            errors.ChatWriteForbiddenError,
        ),
    ):
        return GroupAccessError("无法访问该群组或频道")
    if isinstance(error, errors.MessageIdInvalidError):
        return PermanentMessageError("消息不存在、已删除或不可下载")
    if isinstance(
        error,
        (
            errors.ServerError,
            errors.RpcCallFailError,
            errors.TimedOutError,
            ConnectionError,
            TimeoutError,
            OSError,
        ),
    ):
        return TransientTelegramError(str(error) or "Telegram 临时网络错误")
    if isinstance(error, errors.RPCError):
        return TransientTelegramError(str(error) or "Telegram 请求暂时失败")
    return TransientTelegramError(str(error) or type(error).__name__)
