from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from tg_video_downloader.models import MessageInfo, VideoSearchResult
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.selective import (
    SearchQueueState,
    SelectableVideo,
    normalize_search_caption,
    validate_search_limit,
)


SCHEMA_VERSION = 1
LOOPBACK_HOST = "127.0.0.1"
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ENDPOINT_BYTES = 4 * 1024
MAX_RESULTS = 100
CONNECT_TIMEOUT_SECONDS = 3.0
INITIAL_READ_TIMEOUT_SECONDS = 5.0

ENDPOINT_ERROR = "后台检索通道尚未就绪，请稍后重试"
STALE_ENDPOINT_ERROR = "后台检索通道已失效，请稍后重试"
BUSY_ERROR = "已有检索正在进行，请稍后重试"
STOPPED_ERROR = "后台已停止，本次检索未完成"
INVALID_REQUEST_ERROR = "检索请求无效"
INTERNAL_ERROR = "后台检索失败，请稍后重试"


class SearchChannelError(RuntimeError):
    pass


class SearchChannelBusyError(SearchChannelError):
    pass


@dataclass(frozen=True)
class SearchEndpoint:
    schema_version: int
    host: str
    port: int
    token: str
    pid: int
    started_at: datetime


@dataclass(frozen=True)
class SearchRequest:
    chat_id: int
    keyword: str
    start_utc: datetime | None
    end_utc: datetime | None
    limit: int


class SearchClientProtocol(Protocol):
    async def search_videos(
        self,
        request: SearchRequest,
        *,
        expected_pid: int | None,
    ) -> tuple[SelectableVideo, ...]:
        raise NotImplementedError


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON 包含重复字段")
        value[key] = item
    return value


def _decode_json_object(raw: bytes, *, maximum: int, label: str) -> dict[str, Any]:
    if not raw or len(raw) > maximum:
        suffix = "过大" if len(raw) > maximum else "为空"
        raise SearchChannelError(f"{label}{suffix}")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SearchChannelError(f"{label}不是有效 JSON") from error
    if not isinstance(value, dict):
        raise SearchChannelError(f"{label}必须是 JSON 对象")
    return value


def _encode_json(payload: dict[str, Any], *, maximum: int, label: str) -> bytes:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > maximum:
        raise SearchChannelError(f"{label}过大")
    return raw


def _require_exact_keys(
    payload: dict[str, Any],
    expected: set[str],
    *,
    label: str,
) -> None:
    if set(payload) != expected:
        raise SearchChannelError(f"{label}字段无效")


def _require_int(
    value: Any,
    *,
    label: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SearchChannelError(f"{label}必须是整数")
    if minimum is not None and value < minimum:
        raise SearchChannelError(f"{label}超出范围")
    if maximum is not None and value > maximum:
        raise SearchChannelError(f"{label}超出范围")
    return value


def _optional_int(
    value: Any,
    *,
    label: str,
    minimum: int = 0,
) -> int | None:
    if value is None:
        return None
    return _require_int(value, label=label, minimum=minimum)


def _require_string(value: Any, *, label: str, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise SearchChannelError(f"{label}必须是字符串")
    if not allow_empty and not value:
        raise SearchChannelError(f"{label}不能为空")
    return value


def _optional_string(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, label=label)


def _require_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise SearchChannelError(f"{label}必须是布尔值")
    return value


def _parse_datetime(
    value: Any,
    *,
    label: str,
    optional: bool,
) -> datetime | None:
    if value is None and optional:
        return None
    text = _require_string(value, label=label, allow_empty=False)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise SearchChannelError(f"{label}不是有效日期") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SearchChannelError(f"{label}必须包含时区")
    return parsed.astimezone(UTC)


def _request_payload(request: SearchRequest, token: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "token": token,
        "operation": "search_videos",
        "chat_id": request.chat_id,
        "keyword": request.keyword,
        "start_utc": (
            request.start_utc.isoformat() if request.start_utc is not None else None
        ),
        "end_utc": request.end_utc.isoformat() if request.end_utc is not None else None,
        "limit": request.limit,
    }


def _request_from_payload(
    payload: dict[str, Any],
    expected_token: str,
) -> SearchRequest:
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "token",
            "operation",
            "chat_id",
            "keyword",
            "start_utc",
            "end_utc",
            "limit",
        },
        label="请求",
    )
    if _require_int(payload["schema_version"], label="协议版本") != SCHEMA_VERSION:
        raise SearchChannelError("协议版本不受支持")
    operation = _require_string(payload["operation"], label="检索操作")
    if operation != "search_videos":
        raise SearchChannelError("检索操作不受支持")
    token = _require_string(payload["token"], label="令牌", allow_empty=False)
    if not secrets.compare_digest(token, expected_token):
        raise SearchChannelError(STALE_ENDPOINT_ERROR)
    chat_id = _require_int(payload["chat_id"], label="chat_id")
    keyword = _require_string(payload["keyword"], label="关键词")
    start_utc = _parse_datetime(
        payload["start_utc"],
        label="开始日期",
        optional=True,
    )
    end_utc = _parse_datetime(
        payload["end_utc"],
        label="结束日期",
        optional=True,
    )
    if start_utc is not None and end_utc is not None and start_utc > end_utc:
        raise SearchChannelError("开始日期不能晚于结束日期")
    try:
        limit = validate_search_limit(payload["limit"])
    except ValueError as error:
        raise SearchChannelError(str(error)) from error
    return SearchRequest(chat_id, keyword, start_utc, end_utc, limit)


def encode_request(request: SearchRequest, token: str) -> bytes:
    payload = _request_payload(request, token)
    validated = _request_from_payload(payload, token)
    return _encode_json(
        _request_payload(validated, token),
        maximum=MAX_REQUEST_BYTES,
        label="检索请求",
    )


def decode_request(raw: bytes, expected_token: str) -> SearchRequest:
    payload = _decode_json_object(
        raw,
        maximum=MAX_REQUEST_BYTES,
        label="检索请求",
    )
    return _request_from_payload(payload, expected_token)


def _message_payload(message: MessageInfo) -> dict[str, Any]:
    return {
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "date": message.date.isoformat(),
        "mime_type": message.mime_type,
        "original_name": message.original_name,
        "extension": message.extension,
        "size": message.size,
        "is_video": message.is_video,
        "is_animated": message.is_animated,
        "is_round": message.is_round,
    }


def _message_from_payload(payload: Any) -> MessageInfo:
    if not isinstance(payload, dict):
        raise SearchChannelError("消息字段无效")
    _require_exact_keys(
        payload,
        {
            "chat_id",
            "message_id",
            "date",
            "mime_type",
            "original_name",
            "extension",
            "size",
            "is_video",
            "is_animated",
            "is_round",
        },
        label="消息",
    )
    parsed_date = _parse_datetime(payload["date"], label="消息日期", optional=False)
    if parsed_date is None:
        raise SearchChannelError("消息日期不能为空")
    return MessageInfo(
        chat_id=_require_int(payload["chat_id"], label="消息 chat_id"),
        message_id=_require_int(
            payload["message_id"],
            label="消息 message_id",
            minimum=1,
        ),
        date=parsed_date,
        mime_type=_optional_string(payload["mime_type"], label="MIME 类型"),
        original_name=_optional_string(payload["original_name"], label="文件名"),
        extension=_require_string(payload["extension"], label="扩展名"),
        size=_optional_int(payload["size"], label="文件大小"),
        is_video=_require_bool(payload["is_video"], label="视频标记"),
        is_animated=_require_bool(payload["is_animated"], label="动画标记"),
        is_round=_require_bool(payload["is_round"], label="圆形视频标记"),
    )


def _item_payload(item: SelectableVideo) -> dict[str, Any]:
    return {
        "message": _message_payload(item.result.message),
        "duration_seconds": item.result.duration_seconds,
        "caption": item.result.caption,
        "queue_state": item.queue_state.value,
    }


def _item_from_payload(payload: Any) -> SelectableVideo:
    if not isinstance(payload, dict):
        raise SearchChannelError("检索结果字段无效")
    _require_exact_keys(
        payload,
        {"message", "duration_seconds", "caption", "queue_state"},
        label="检索结果",
    )
    caption = _require_string(payload["caption"], label="说明")
    if len(caption) > 120 or normalize_search_caption(caption) != caption:
        raise SearchChannelError("说明必须是最多 120 字符的单行文本")
    state_text = _require_string(payload["queue_state"], label="队列状态")
    try:
        queue_state = SearchQueueState(state_text)
    except ValueError as error:
        raise SearchChannelError("队列状态无效") from error
    return SelectableVideo(
        result=VideoSearchResult(
            message=_message_from_payload(payload["message"]),
            duration_seconds=_optional_int(
                payload["duration_seconds"],
                label="视频时长",
            ),
            caption=caption,
        ),
        queue_state=queue_state,
    )


def encode_success(items: tuple[SelectableVideo, ...]) -> bytes:
    if len(items) > MAX_RESULTS:
        raise SearchChannelError("检索结果不能超过 100 条")
    normalized = tuple(_item_from_payload(_item_payload(item)) for item in items)
    return _encode_json(
        {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "results": [_item_payload(item) for item in normalized],
        },
        maximum=MAX_RESPONSE_BYTES,
        label="检索响应",
    )


def encode_error(message: str) -> bytes:
    safe_message = " ".join(str(message).split())[:500] or INTERNAL_ERROR
    return _encode_json(
        {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "error": safe_message,
        },
        maximum=MAX_RESPONSE_BYTES,
        label="检索响应",
    )


def decode_response(raw: bytes) -> tuple[SelectableVideo, ...]:
    payload = _decode_json_object(
        raw,
        maximum=MAX_RESPONSE_BYTES,
        label="检索响应",
    )
    if _require_int(payload.get("schema_version"), label="协议版本") != SCHEMA_VERSION:
        raise SearchChannelError("协议版本不受支持")
    ok = _require_bool(payload.get("ok"), label="响应状态")
    if ok:
        _require_exact_keys(
            payload,
            {"schema_version", "ok", "results"},
            label="响应",
        )
        raw_results = payload["results"]
        if not isinstance(raw_results, list):
            raise SearchChannelError("检索结果必须是列表")
        if len(raw_results) > MAX_RESULTS:
            raise SearchChannelError("检索结果不能超过 100 条")
        return tuple(_item_from_payload(item) for item in raw_results)
    _require_exact_keys(
        payload,
        {"schema_version", "ok", "error"},
        label="响应",
    )
    message = _require_string(payload["error"], label="错误", allow_empty=False)
    if message == BUSY_ERROR:
        raise SearchChannelBusyError(message)
    raise SearchChannelError(message)


def _endpoint_payload(endpoint: SearchEndpoint) -> dict[str, Any]:
    return {
        "schema_version": endpoint.schema_version,
        "host": endpoint.host,
        "port": endpoint.port,
        "token": endpoint.token,
        "pid": endpoint.pid,
        "started_at": endpoint.started_at.isoformat(),
    }


def _endpoint_from_payload(payload: dict[str, Any]) -> SearchEndpoint:
    _require_exact_keys(
        payload,
        {"schema_version", "host", "port", "token", "pid", "started_at"},
        label="端点",
    )
    version = _require_int(payload["schema_version"], label="协议版本")
    if version != SCHEMA_VERSION:
        raise SearchChannelError("端点协议版本不受支持")
    host = _require_string(payload["host"], label="监听地址", allow_empty=False)
    if host != LOOPBACK_HOST:
        raise SearchChannelError("端点监听地址无效")
    port = _require_int(payload["port"], label="端口", minimum=1, maximum=65535)
    token = _require_string(payload["token"], label="令牌", allow_empty=False)
    if len(token) < 32 or len(token) > 128:
        raise SearchChannelError("端点令牌无效")
    pid = _require_int(payload["pid"], label="PID", minimum=1)
    started_at = _parse_datetime(
        payload["started_at"],
        label="启动时间",
        optional=False,
    )
    if started_at is None:
        raise SearchChannelError("启动时间不能为空")
    return SearchEndpoint(version, host, port, token, pid, started_at)


def write_search_endpoint(paths: ProjectPaths, endpoint: SearchEndpoint) -> None:
    paths.ensure_directories()
    validated = _endpoint_from_payload(_endpoint_payload(endpoint))
    raw = _encode_json(
        _endpoint_payload(validated),
        maximum=MAX_ENDPOINT_BYTES,
        label="端点记录",
    )
    temporary = paths.search_endpoint.with_name(
        f"{paths.search_endpoint.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, paths.search_endpoint)
    finally:
        temporary.unlink(missing_ok=True)


def read_search_endpoint(paths: ProjectPaths) -> SearchEndpoint:
    try:
        if paths.search_endpoint.stat().st_size > MAX_ENDPOINT_BYTES:
            raise SearchChannelError(STALE_ENDPOINT_ERROR)
        raw = paths.search_endpoint.read_bytes()
    except FileNotFoundError as error:
        raise SearchChannelError(ENDPOINT_ERROR) from error
    except OSError as error:
        raise SearchChannelError(STALE_ENDPOINT_ERROR) from error
    try:
        payload = _decode_json_object(
            raw,
            maximum=MAX_ENDPOINT_BYTES,
            label="端点记录",
        )
        return _endpoint_from_payload(payload)
    except SearchChannelError as error:
        raise SearchChannelError(STALE_ENDPOINT_ERROR) from error


def remove_search_endpoint(paths: ProjectPaths, token: str) -> None:
    try:
        endpoint = read_search_endpoint(paths)
    except SearchChannelError:
        return
    if not secrets.compare_digest(endpoint.token, token):
        return
    try:
        confirmation = read_search_endpoint(paths)
    except SearchChannelError:
        return
    if secrets.compare_digest(confirmation.token, token):
        paths.search_endpoint.unlink(missing_ok=True)


SearchHandler = Callable[
    [SearchRequest],
    Awaitable[tuple[SelectableVideo, ...]],
]


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


class SearchIpcClient:
    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths

    async def search_videos(
        self,
        request: SearchRequest,
        *,
        expected_pid: int | None,
    ) -> tuple[SelectableVideo, ...]:
        endpoint = read_search_endpoint(self.paths)
        if expected_pid is not None and endpoint.pid != expected_pid:
            raise SearchChannelError(STALE_ENDPOINT_ERROR)

        writer: asyncio.StreamWriter | None = None
        try:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        endpoint.host,
                        endpoint.port,
                        limit=MAX_RESPONSE_BYTES + 1,
                    ),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                )
            except (ConnectionError, OSError, TimeoutError) as error:
                raise SearchChannelError(STALE_ENDPOINT_ERROR) from error

            writer.write(encode_request(request, endpoint.token) + b"\n")
            await writer.drain()
            try:
                line = await reader.readline()
            except (ValueError, asyncio.LimitOverrunError) as error:
                raise SearchChannelError(STALE_ENDPOINT_ERROR) from error
            if not line:
                raise SearchChannelError(STOPPED_ERROR)
            if not line.endswith(b"\n") or len(line) - 1 > MAX_RESPONSE_BYTES:
                raise SearchChannelError(STALE_ENDPOINT_ERROR)
            return decode_response(line[:-1])
        except asyncio.CancelledError:
            raise
        except SearchChannelError:
            raise
        except (ConnectionError, OSError) as error:
            raise SearchChannelError(STOPPED_ERROR) from error
        finally:
            if writer is not None:
                await _close_writer(writer)


class SearchIpcServer:
    def __init__(self, paths: ProjectPaths, search_handler: SearchHandler) -> None:
        self.paths = paths
        self.search_handler = search_handler
        self._server: asyncio.AbstractServer | None = None
        self._endpoint: SearchEndpoint | None = None
        self._active_search: asyncio.Task[tuple[SelectableVideo, ...]] | None = None
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._closing = False

    async def start(self) -> SearchEndpoint:
        if self._server is not None:
            raise RuntimeError("后台检索通道已经启动")
        self._closing = False
        server = await asyncio.start_server(
            self._handle_client,
            host=LOOPBACK_HOST,
            port=0,
            limit=MAX_REQUEST_BYTES + 1,
        )
        sockets = server.sockets or ()
        if not sockets:
            server.close()
            await server.wait_closed()
            raise SearchChannelError(ENDPOINT_ERROR)
        port = int(sockets[0].getsockname()[1])
        endpoint = SearchEndpoint(
            schema_version=SCHEMA_VERSION,
            host=LOOPBACK_HOST,
            port=port,
            token=secrets.token_urlsafe(32),
            pid=os.getpid(),
            started_at=datetime.now(UTC),
        )
        self._server = server
        self._endpoint = endpoint
        try:
            write_search_endpoint(self.paths, endpoint)
        except BaseException:
            self._server = None
            self._endpoint = None
            server.close()
            await server.wait_closed()
            raise
        return endpoint

    async def close(self) -> None:
        if self._server is None and self._endpoint is None:
            return
        self._closing = True
        server = self._server
        self._server = None
        if server is not None:
            server.close()

        active = self._active_search
        if active is not None and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)

        current = asyncio.current_task()
        clients = tuple(
            task
            for task in self._client_tasks
            if task is not current and not task.done()
        )
        if clients:
            for task in clients:
                task.cancel()
            await asyncio.gather(*clients, return_exceptions=True)
        if server is not None:
            await server.wait_closed()

        endpoint = self._endpoint
        self._endpoint = None
        if endpoint is not None:
            remove_search_endpoint(self.paths, endpoint.token)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        current = asyncio.current_task()
        if current is not None:
            self._client_tasks.add(current)
        response: bytes | None = None
        search_task: asyncio.Task[tuple[SelectableVideo, ...]] | None = None
        disconnect_task: asyncio.Task[bytes] | None = None
        try:
            endpoint = self._endpoint
            if endpoint is None:
                raise SearchChannelError(STALE_ENDPOINT_ERROR)
            try:
                line = await asyncio.wait_for(
                    reader.readline(),
                    timeout=INITIAL_READ_TIMEOUT_SECONDS,
                )
            except (TimeoutError, ValueError, asyncio.LimitOverrunError) as error:
                raise SearchChannelError(INVALID_REQUEST_ERROR) from error
            if not line or not line.endswith(b"\n"):
                raise SearchChannelError(INVALID_REQUEST_ERROR)
            if len(line) - 1 > MAX_REQUEST_BYTES:
                raise SearchChannelError("检索请求过大")
            request = decode_request(line[:-1], endpoint.token)

            active = self._active_search
            if active is not None and not active.done():
                raise SearchChannelBusyError(BUSY_ERROR)
            search_task = asyncio.create_task(
                self.search_handler(request),
                name="manual-video-search",
            )
            self._active_search = search_task
            disconnect_task = asyncio.create_task(
                reader.read(1),
                name="manual-video-search-client",
            )
            done, _pending = await asyncio.wait(
                (search_task, disconnect_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done and search_task not in done:
                search_task.cancel()
                await asyncio.gather(search_task, return_exceptions=True)
                return
            disconnect_task.cancel()
            await asyncio.gather(disconnect_task, return_exceptions=True)
            try:
                items = await search_task
            except asyncio.CancelledError:
                if self._closing:
                    response = encode_error(STOPPED_ERROR)
                else:
                    return
            except SearchChannelError as error:
                response = encode_error(str(error))
            except Exception:
                response = encode_error(INTERNAL_ERROR)
            else:
                response = encode_success(tuple(items))
        except SearchChannelBusyError as error:
            response = encode_error(str(error))
        except SearchChannelError as error:
            response = encode_error(str(error))
        except asyncio.CancelledError:
            if self._closing:
                response = encode_error(STOPPED_ERROR)
            else:
                raise
        except Exception:
            response = encode_error(INTERNAL_ERROR)
        finally:
            if disconnect_task is not None and not disconnect_task.done():
                disconnect_task.cancel()
                await asyncio.gather(disconnect_task, return_exceptions=True)
            if search_task is not None and self._active_search is search_task:
                self._active_search = None
            if response is not None and not writer.is_closing():
                try:
                    writer.write(response + b"\n")
                    await writer.drain()
                except (ConnectionError, OSError):
                    pass
            await _close_writer(writer)
            if current is not None:
                self._client_tasks.discard(current)
