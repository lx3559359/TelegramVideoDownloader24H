from __future__ import annotations

import asyncio
import math
import mimetypes
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from telethon import TelegramClient, errors, events
from telethon.tl.types import InputMessagesFilterVideo

from tg_video_downloader.media import is_downloadable_video
from tg_video_downloader.models import (
    Credentials,
    GroupTarget,
    MessageInfo,
    VideoSearchResult,
)
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.selective import (
    MAX_SEARCH_CANDIDATES,
    normalize_search_caption,
)
from tg_video_downloader.windows import SingleInstance


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


class TelegramSessionInUseError(RuntimeError):
    pass


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

    async def search_videos(
        self,
        chat_id: int,
        keyword: str,
        start_utc: datetime | None,
        end_utc: datetime | None,
        result_limit: int,
    ) -> tuple[VideoSearchResult, ...]: ...

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


def _video_duration(message: Any) -> int | None:
    document = getattr(message, "document", None)
    attributes = getattr(document, "attributes", ()) if document else ()
    for attribute in attributes:
        if type(attribute).__name__ != "DocumentAttributeVideo":
            continue
        value = getattr(attribute, "duration", None)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            return int(value)
    return None


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
        self._paths = paths
        self._credentials = credentials
        self._client_factory = client_factory
        self._client: Any | None = None
        self._session_guard: SingleInstance | None = None
        self._event_callback: Callable[[Any], Awaitable[None]] | None = None
        self._password_required = False
        self._qr_login: Any | None = None

    async def connect(self) -> None:
        if self._client is not None:
            return
        guard = SingleInstance(
            self._paths.telegram_client_lock,
            already_running_message="Telegram 会话正在由后台使用",
        )
        try:
            guard.__enter__()
        except RuntimeError as error:
            raise TelegramSessionInUseError(
                "Telegram 会话正在由后台使用"
            ) from error
        self._session_guard = guard
        client: Any | None = None
        try:
            client = self._client_factory(
                str(self._paths.session),
                self._credentials.api_id,
                self._credentials.api_hash,
                auto_reconnect=True,
                connection_retries=-1,
                retry_delay=5,
                flood_sleep_threshold=60,
            )
            self._client = client
            await client.connect()
        except BaseException as error:
            await self._release_client(client)
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, Exception):
                raise _mapped_error(error) from error
            raise

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        mapped: Exception | None = None
        try:
            if client is not None:
                await client.disconnect()
        except Exception as error:
            mapped = _mapped_error(error)
        finally:
            self._release_session_guard()
            self._qr_login = None
            self._password_required = False
        if mapped is not None:
            raise mapped

    async def _release_client(self, client: Any | None) -> None:
        self._client = None
        try:
            if client is not None:
                await client.disconnect()
        except Exception:
            pass
        finally:
            self._release_session_guard()
            self._qr_login = None
            self._password_required = False

    def _release_session_guard(self) -> None:
        guard = self._session_guard
        self._session_guard = None
        if guard is not None:
            guard.__exit__(None, None, None)

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Telegram 客户端尚未连接")
        return self._client

    async def is_authorized(self) -> bool:
        client = self._require_client()
        try:
            return bool(await client.is_user_authorized())
        except Exception as error:
            raise _mapped_error(error) from error

    async def send_login_code(self, phone: str) -> None:
        client = self._require_client()
        try:
            await client.send_code_request(phone)
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
        client = self._require_client()
        try:
            await client.sign_in(phone=phone, code=code)
        except errors.SessionPasswordNeededError as error:
            self._password_required = True
            if not password:
                raise AuthenticationRequiredError("需要二步验证密码") from error
            await self.complete_password(password)
        except Exception as error:
            raise _mapped_error(error) from error

    async def start_qr_login(self) -> QrLoginChallenge:
        client = self._require_client()
        try:
            self._qr_login = await client.qr_login()
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
        client = self._require_client()
        try:
            await client.sign_in(password=password)
        except Exception as error:
            raise _mapped_error(error) from error
        self._password_required = False

    async def log_out(self) -> None:
        client = self._require_client()
        try:
            await client.log_out()
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
        client = self._require_client()
        groups: list[GroupTarget] = []
        try:
            async for dialog in client.iter_dialogs():
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

    async def search_videos(
        self,
        chat_id: int,
        keyword: str,
        start_utc: datetime | None,
        end_utc: datetime | None,
        result_limit: int,
    ) -> tuple[VideoSearchResult, ...]:
        client = self._require_client()
        results: list[VideoSearchResult] = []
        try:
            async for raw in client.iter_messages(
                chat_id,
                limit=MAX_SEARCH_CANDIDATES,
                search=keyword.strip() or None,
                filter=InputMessagesFilterVideo,
                offset_date=end_utc,
            ):
                message = normalize_message(raw, chat_id)
                message_date = message.date
                if message_date.tzinfo is None:
                    message_date = message_date.replace(tzinfo=UTC)
                message_date = message_date.astimezone(UTC)
                if start_utc is not None and message_date < start_utc:
                    break
                if end_utc is not None and message_date >= end_utc:
                    continue
                if not is_downloadable_video(message):
                    continue
                results.append(
                    VideoSearchResult(
                        message=message,
                        duration_seconds=_video_duration(raw),
                        caption=normalize_search_caption(
                            getattr(raw, "message", "")
                        ),
                    )
                )
                if len(results) >= result_limit:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _mapped_error(error) from error
        return tuple(results)

    def set_new_message_handler(self, handler: MessageHandler) -> None:
        client = self._require_client()
        if self._event_callback is not None:
            client.remove_event_handler(self._event_callback)

        async def callback(event: Any) -> None:
            await handler(normalize_message(event.message, int(event.chat_id)))

        self._event_callback = callback
        client.add_event_handler(callback, events.NewMessage())

    async def latest_message_id(self, chat_id: int) -> int:
        client = self._require_client()
        try:
            async for message in client.iter_messages(chat_id, limit=1):
                return int(message.id)
            return 0
        except Exception as error:
            raise _mapped_error(error) from error

    async def iter_newer_messages(
        self,
        chat_id: int,
        min_id: int,
    ) -> AsyncIterator[MessageInfo]:
        client = self._require_client()
        try:
            async for message in client.iter_messages(
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
        client = self._require_client()
        try:
            async for message in client.iter_messages(
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
        client = self._require_client()
        try:
            message = await client.get_messages(chat_id, ids=message_id)
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
            stream = client.iter_download(
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
