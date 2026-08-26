import asyncio
import importlib
import json
import os
import secrets
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tg_video_downloader.models import MessageInfo, VideoSearchResult
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.selective import (
    SearchQueueState,
    SelectableVideo,
    normalize_search_caption,
)


def _ipc():
    return importlib.import_module("tg_video_downloader.search_ipc")


def _item(
    *,
    message_id: int = 7,
    caption: str = "课程说明",
    original_name: str = "lesson.mp4",
    queue_state: SearchQueueState = SearchQueueState.AVAILABLE,
) -> SelectableVideo:
    message = MessageInfo(
        chat_id=-1001,
        message_id=message_id,
        date=datetime(2026, 8, 26, 2, 3, 4, tzinfo=UTC),
        mime_type="video/mp4",
        original_name=original_name,
        extension=".mp4",
        size=123456,
        is_video=True,
        is_animated=False,
        is_round=False,
    )
    return SelectableVideo(
        result=VideoSearchResult(
            message=message,
            duration_seconds=95,
            caption=caption,
        ),
        queue_state=queue_state,
    )


def _request_payload(token: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "token": token,
        "operation": "search_videos",
        "chat_id": -1001,
        "keyword": "课程",
        "start_utc": "2026-08-01T00:00:00+00:00",
        "end_utc": "2026-09-01T00:00:00+00:00",
        "limit": 20,
    }


def test_request_json_round_trip_preserves_typed_fields() -> None:
    ipc = _ipc()
    request = ipc.SearchRequest(
        chat_id=-1001,
        keyword="课程",
        start_utc=datetime(2026, 8, 1, tzinfo=UTC),
        end_utc=datetime(2026, 9, 1, tzinfo=UTC),
        limit=20,
    )

    raw = ipc.encode_request(request, "token-value")

    assert not raw.endswith(b"\n")
    assert ipc.decode_request(raw, "token-value") == request


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda value: value.update(extra=True), "字段"),
        (lambda value: value.update(schema_version=2), "版本"),
        (lambda value: value.update(operation="other"), "操作"),
        (lambda value: value.update(token="wrong"), "失效"),
        (lambda value: value.update(chat_id=True), "chat_id"),
        (lambda value: value.update(start_utc="2026-08-01T00:00:00"), "时区"),
        (lambda value: value.update(limit=21), "20、50 或 100"),
    ],
)
def test_request_rejects_invalid_protocol_fields(mutate, expected: str) -> None:
    ipc = _ipc()
    payload = _request_payload("token-value")
    mutate(payload)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    with pytest.raises(ipc.SearchChannelError, match=expected):
        ipc.decode_request(raw, "token-value")


def test_request_enforces_byte_limit() -> None:
    ipc = _ipc()
    request = ipc.SearchRequest(
        chat_id=-1001,
        keyword="x" * ipc.MAX_REQUEST_BYTES,
        start_utc=None,
        end_utc=None,
        limit=20,
    )

    with pytest.raises(ipc.SearchChannelError, match="过大"):
        ipc.encode_request(request, "token-value")


def test_response_json_round_trip_preserves_message_and_queue_state() -> None:
    ipc = _ipc()
    items = (
        _item(queue_state=SearchQueueState.QUEUED),
        _item(message_id=8, queue_state=SearchQueueState.COMPLETED),
    )

    raw = ipc.encode_success(items)

    assert not raw.endswith(b"\n")
    assert ipc.decode_response(raw) == items


def test_response_round_trip_accepts_caption_truncated_at_word_boundary() -> None:
    ipc = _ipc()
    item = _item(
        caption=normalize_search_caption("a" * 119 + " " + "b")
    )

    raw = ipc.encode_success((item,))

    assert ipc.decode_response(raw) == (item,)


def test_response_error_becomes_stable_channel_error() -> None:
    ipc = _ipc()

    with pytest.raises(ipc.SearchChannelError, match="请稍后重试"):
        ipc.decode_response(ipc.encode_error("请稍后重试"))


def test_response_rejects_more_than_100_results() -> None:
    ipc = _ipc()
    items = tuple(_item(message_id=index + 1) for index in range(101))

    with pytest.raises(ipc.SearchChannelError, match="100"):
        ipc.encode_success(items)


@pytest.mark.parametrize(
    "item",
    [
        _item(caption="line one\nline two"),
        _item(caption="x" * 121),
    ],
)
def test_response_rejects_non_normalized_caption(item: SelectableVideo) -> None:
    ipc = _ipc()

    with pytest.raises(ipc.SearchChannelError, match="说明"):
        ipc.encode_success((item,))


def test_response_rejects_oversized_payload() -> None:
    ipc = _ipc()
    item = _item(original_name="x" * ipc.MAX_RESPONSE_BYTES)

    with pytest.raises(ipc.SearchChannelError, match="过大"):
        ipc.encode_success((item,))


def _endpoint(ipc, *, token: str | None = None, pid: int = 1234):
    return ipc.SearchEndpoint(
        schema_version=ipc.SCHEMA_VERSION,
        host=ipc.LOOPBACK_HOST,
        port=43210,
        token=token or secrets.token_urlsafe(32),
        pid=pid,
        started_at=datetime(2026, 8, 26, 2, 3, 4, tzinfo=UTC),
    )


def test_endpoint_round_trip_is_atomic_and_private(tmp_path: Path) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    endpoint = _endpoint(ipc)

    ipc.write_search_endpoint(paths, endpoint)

    assert ipc.read_search_endpoint(paths) == endpoint
    payload = json.loads(paths.search_endpoint.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "host",
        "port",
        "token",
        "pid",
        "started_at",
    }
    serialized = paths.search_endpoint.read_text(encoding="utf-8")
    assert "ipc-private-keyword-4f692d" not in serialized
    assert "lesson.mp4" not in serialized
    assert list(paths.runtime.glob("search-endpoint.json.*.tmp")) == []


def test_endpoint_write_replaces_stale_record(tmp_path: Path) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    first = _endpoint(ipc, pid=100)
    second = _endpoint(ipc, pid=200)

    ipc.write_search_endpoint(paths, first)
    ipc.write_search_endpoint(paths, second)

    assert ipc.read_search_endpoint(paths) == second


@pytest.mark.parametrize(
    "updates",
    [
        {"host": "0.0.0.0"},
        {"port": 0},
        {"pid": True},
        {"schema_version": 2},
        {"started_at": "2026-08-26T02:03:04"},
        {"extra": "field"},
    ],
)
def test_endpoint_rejects_invalid_or_extra_fields(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    endpoint = _endpoint(ipc)
    payload = {
        "schema_version": endpoint.schema_version,
        "host": endpoint.host,
        "port": endpoint.port,
        "token": endpoint.token,
        "pid": endpoint.pid,
        "started_at": endpoint.started_at.isoformat(),
    }
    payload.update(updates)
    paths.ensure_directories()
    paths.search_endpoint.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ipc.SearchChannelError):
        ipc.read_search_endpoint(paths)


def test_endpoint_delete_requires_matching_token(tmp_path: Path) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    endpoint = _endpoint(ipc)
    ipc.write_search_endpoint(paths, endpoint)

    ipc.remove_search_endpoint(paths, "wrong-token")
    assert paths.search_endpoint.is_file()

    ipc.remove_search_endpoint(paths, endpoint.token)
    assert not paths.search_endpoint.exists()


def _request(ipc):
    return ipc.SearchRequest(
        chat_id=-1001,
        keyword="课程",
        start_utc=datetime(2026, 8, 1, tzinfo=UTC),
        end_utc=datetime(2026, 9, 1, tzinfo=UTC),
        limit=20,
    )


async def _raw_exchange(endpoint, raw: bytes) -> bytes:
    reader, writer = await asyncio.open_connection(
        endpoint.host,
        endpoint.port,
        limit=2 * 1024 * 1024,
    )
    try:
        writer.write(raw + b"\n")
        await writer.drain()
        return await asyncio.wait_for(reader.readline(), timeout=2)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_server_publishes_loopback_endpoint_and_client_round_trips(
    tmp_path: Path,
) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    calls = []

    async def search(request):
        calls.append(request)
        return (_item(),)

    server = ipc.SearchIpcServer(paths, search)
    endpoint = await server.start()
    try:
        assert endpoint.host == "127.0.0.1"
        assert endpoint.port > 0
        assert endpoint.pid == os.getpid()
        assert len(endpoint.token) >= 32
        assert ipc.read_search_endpoint(paths) == endpoint

        result = await ipc.SearchIpcClient(paths).search_videos(
            _request(ipc),
            expected_pid=os.getpid(),
        )

        assert result == (_item(),)
        assert calls == [_request(ipc)]
    finally:
        await server.close()
    assert not paths.search_endpoint.exists()


@pytest.mark.asyncio
async def test_client_rejects_endpoint_pid_mismatch_before_connecting(
    tmp_path: Path,
) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    ipc.write_search_endpoint(paths, _endpoint(ipc, pid=111))

    with pytest.raises(ipc.SearchChannelError, match="失效"):
        await ipc.SearchIpcClient(paths).search_videos(
            _request(ipc),
            expected_pid=222,
        )


@pytest.mark.asyncio
async def test_client_reports_missing_and_refused_endpoints(tmp_path: Path) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    client = ipc.SearchIpcClient(paths)

    with pytest.raises(ipc.SearchChannelError, match="尚未就绪"):
        await client.search_videos(_request(ipc), expected_pid=None)

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((ipc.LOOPBACK_HOST, 0))
    port = probe.getsockname()[1]
    probe.close()
    endpoint = _endpoint(ipc)
    ipc.write_search_endpoint(
        paths,
        ipc.SearchEndpoint(
            endpoint.schema_version,
            endpoint.host,
            port,
            endpoint.token,
            endpoint.pid,
            endpoint.started_at,
        ),
    )

    with pytest.raises(ipc.SearchChannelError, match="失效"):
        await client.search_videos(_request(ipc), expected_pid=None)


@pytest.mark.asyncio
async def test_client_reports_invalid_token_as_stale_endpoint(tmp_path: Path) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)

    async def search(_request_value):
        return (_item(),)

    server = ipc.SearchIpcServer(paths, search)
    endpoint = await server.start()
    ipc.write_search_endpoint(
        paths,
        ipc.SearchEndpoint(
            endpoint.schema_version,
            endpoint.host,
            endpoint.port,
            secrets.token_urlsafe(32),
            endpoint.pid,
            endpoint.started_at,
        ),
    )
    try:
        with pytest.raises(ipc.SearchChannelError, match="失效"):
            await ipc.SearchIpcClient(paths).search_videos(
                _request(ipc),
                expected_pid=endpoint.pid,
            )
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_server_allows_only_one_active_search(tmp_path: Path) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def search(_request_value):
        started.set()
        await release.wait()
        return (_item(),)

    server = ipc.SearchIpcServer(paths, search)
    await server.start()
    first = asyncio.create_task(
        ipc.SearchIpcClient(paths).search_videos(
            _request(ipc),
            expected_pid=os.getpid(),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        with pytest.raises(ipc.SearchChannelBusyError, match="已有检索"):
            await ipc.SearchIpcClient(paths).search_videos(
                _request(ipc),
                expected_pid=os.getpid(),
            )
        release.set()
        assert await asyncio.wait_for(first, timeout=2) == (_item(),)
    finally:
        release.set()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_client_cancellation_closes_socket_and_cancels_only_search(
    tmp_path: Path,
) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def search(_request_value):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    server = ipc.SearchIpcServer(paths, search)
    await server.start()
    task = asyncio.create_task(
        ipc.SearchIpcClient(paths).search_videos(
            _request(ipc),
            expected_pid=os.getpid(),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        assert paths.search_endpoint.is_file()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_server_close_cancels_search_and_reports_stopped(tmp_path: Path) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    started = asyncio.Event()

    async def search(_request_value):
        started.set()
        await asyncio.Event().wait()

    server = ipc.SearchIpcServer(paths, search)
    await server.start()
    task = asyncio.create_task(
        ipc.SearchIpcClient(paths).search_videos(
            _request(ipc),
            expected_pid=os.getpid(),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    await asyncio.wait_for(server.close(), timeout=2)

    with pytest.raises(ipc.SearchChannelError, match="后台已停止"):
        await asyncio.wait_for(task, timeout=2)
    assert not paths.search_endpoint.exists()


@pytest.mark.asyncio
async def test_server_close_does_not_wait_for_an_incomplete_request(
    tmp_path: Path,
) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)

    async def search(_request_value):
        return ()

    server = ipc.SearchIpcServer(paths, search)
    endpoint = await server.start()
    reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(server.close(), timeout=0.5)
        assert await asyncio.wait_for(reader.read(), timeout=0.5) != b""
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_old_server_close_cannot_remove_new_server_endpoint(
    tmp_path: Path,
) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)

    async def search(_request_value):
        return ()

    first = ipc.SearchIpcServer(paths, search)
    second = ipc.SearchIpcServer(paths, search)
    first_endpoint = await first.start()
    second_endpoint = await second.start()
    assert first_endpoint.token != second_endpoint.token

    await first.close()
    try:
        assert ipc.read_search_endpoint(paths) == second_endpoint
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_server_rejects_malformed_extra_and_oversized_requests(
    tmp_path: Path,
) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)

    async def search(_request_value):
        raise AssertionError("invalid requests must not reach search handler")

    server = ipc.SearchIpcServer(paths, search)
    endpoint = await server.start()
    try:
        extra = _request_payload(endpoint.token)
        extra["extra"] = True
        extra_response = await _raw_exchange(
            endpoint,
            json.dumps(extra).encode("utf-8"),
        )
        with pytest.raises(ipc.SearchChannelError):
            ipc.decode_response(extra_response.rstrip(b"\n"))

        oversized_response = await _raw_exchange(
            endpoint,
            b"x" * (ipc.MAX_REQUEST_BYTES + 1),
        )
        with pytest.raises(ipc.SearchChannelError):
            ipc.decode_response(oversized_response.rstrip(b"\n"))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_server_internal_error_is_generic_and_never_leaks_token(
    tmp_path: Path,
) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)

    async def search(_request_value):
        raise RuntimeError("internal-secret")

    server = ipc.SearchIpcServer(paths, search)
    endpoint = await server.start()
    try:
        with pytest.raises(ipc.SearchChannelError) as captured:
            await ipc.SearchIpcClient(paths).search_videos(
                _request(ipc),
                expected_pid=endpoint.pid,
            )
        assert str(captured.value) == ipc.INTERNAL_ERROR
        assert "internal-secret" not in str(captured.value)
        assert endpoint.token not in str(captured.value)
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_client_does_not_timeout_a_valid_long_search(tmp_path: Path) -> None:
    ipc = _ipc()
    paths = ProjectPaths.from_root(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def search(_request_value):
        started.set()
        await release.wait()
        return (_item(),)

    server = ipc.SearchIpcServer(paths, search)
    await server.start()
    task = asyncio.create_task(
        ipc.SearchIpcClient(paths).search_videos(
            _request(ipc),
            expected_pid=os.getpid(),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        done, _pending = await asyncio.wait({task}, timeout=0.05)
        assert done == set()
        release.set()
        assert await asyncio.wait_for(task, timeout=2) == (_item(),)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()
