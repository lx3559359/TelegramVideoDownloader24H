from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors

from tg_video_downloader.gateway import (
    AuthenticationRequiredError,
    InvalidApiCredentialsError,
    QrLoginExpiredError,
    TelethonGateway,
    TransientTelegramError,
    _mapped_error,
    normalize_message,
)
from tg_video_downloader.models import Credentials, GroupTarget
from tg_video_downloader.paths import ProjectPaths


def _attribute(name: str, **values):
    attribute_type = type(name, (), {})
    attribute = attribute_type()
    for key, value in values.items():
        setattr(attribute, key, value)
    return attribute


def _message(*attributes):
    document = SimpleNamespace(
        mime_type="video/mp4",
        size=123,
        attributes=list(attributes),
    )
    return SimpleNamespace(
        id=9,
        date=datetime(2026, 8, 24, tzinfo=UTC),
        document=document,
        video=None,
        video_note=None,
        gif=None,
        file=SimpleNamespace(name="movie.mp4", ext=".mp4", size=123),
    )


def test_normalize_message_attributes() -> None:
    message = _message(
        _attribute("DocumentAttributeFilename", file_name="movie.mp4"),
        _attribute("DocumentAttributeVideo", round_message=False),
    )

    info = normalize_message(message, chat_id=-1001)

    assert info.chat_id == -1001
    assert info.message_id == 9
    assert info.original_name == "movie.mp4"
    assert info.mime_type == "video/mp4"
    assert info.extension == ".mp4"
    assert info.is_video is True
    assert info.is_animated is False
    assert info.is_round is False


def test_normalize_animated_and_round_attributes() -> None:
    animated = normalize_message(
        _message(_attribute("DocumentAttributeAnimated")),
        chat_id=-1001,
    )
    round_video = normalize_message(
        _message(_attribute("DocumentAttributeVideo", round_message=True)),
        chat_id=-1001,
    )

    assert animated.is_animated is True
    assert round_video.is_video is True
    assert round_video.is_round is True


@pytest.mark.asyncio
async def test_list_groups_and_channels_excludes_private_chats_and_sorts(
    tmp_path: Path,
) -> None:
    dialogs = (
        SimpleNamespace(id=-1002, name="beta", is_group=True, is_channel=True),
        SimpleNamespace(id=7, name="Private", is_group=False, is_channel=False),
        SimpleNamespace(id=-1003, name="News", is_group=False, is_channel=True),
        SimpleNamespace(id=-1001, name="Alpha", is_group=True, is_channel=False),
    )

    class FakeClient:
        async def iter_dialogs(self):
            for dialog in dialogs:
                yield dialog

    captured = {}

    def client_factory(session, api_id, api_hash, **options):
        captured.update(
            session=session,
            api_id=api_id,
            api_hash=api_hash,
            options=options,
        )
        return FakeClient()

    paths = ProjectPaths.from_root(tmp_path)
    gateway = TelethonGateway(
        paths,
        Credentials(12345, "hash", "+8613800000000"),
        client_factory=client_factory,
    )

    assert await gateway.list_groups() == (
        GroupTarget(-1001, "Alpha", False),
        GroupTarget(-1002, "beta", False),
        GroupTarget(-1003, "News", False),
    )
    assert captured["session"] == str(paths.session)
    assert captured["options"] == {
        "auto_reconnect": True,
        "connection_retries": -1,
        "retry_delay": 5,
        "flood_sleep_threshold": 60,
    }


class DownloadStream:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._chunks)
        except StopIteration as error:
            raise StopAsyncIteration from error

    async def close(self) -> None:
        self.closed = True


class DownloadClient:
    def __init__(self, chunks: tuple[bytes, ...], media_size: int) -> None:
        self.chunks = chunks
        self.media_size = media_size
        self.download_offsets: list[int] = []
        self.download_options: list[dict[str, int]] = []
        self.stream: DownloadStream | None = None

    async def get_messages(self, chat_id: int, *, ids: int):
        return SimpleNamespace(
            media=object(),
            document=SimpleNamespace(size=self.media_size),
        )

    def iter_download(self, _media, **options):
        self.download_offsets.append(options["offset"])
        self.download_options.append(options)
        self.stream = DownloadStream(self.chunks)
        return self.stream


@pytest.mark.asyncio
async def test_download_appends_from_offset_and_reports_progress(
    tmp_path: Path,
) -> None:
    client = DownloadClient(chunks=(b"def", b"ghi"), media_size=9)
    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(123, "hash"),
        client_factory=lambda *_args, **_kwargs: client,
    )
    destination = tmp_path / ".tmp" / "job.part"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"abc")
    progress: list[tuple[int, int | None]] = []

    result = await gateway.download_message(
        -1001,
        1,
        destination,
        offset=3,
        progress_callback=lambda current, total: progress.append((current, total)),
    )

    assert result == destination
    assert destination.read_bytes() == b"abcdefghi"
    assert client.download_offsets == [3]
    assert client.download_options == [
        {
            "offset": 3,
            "request_size": 512 * 1024,
            "chunk_size": 512 * 1024,
        }
    ]
    assert progress == [(6, 9), (9, 9)]
    assert client.stream is not None
    assert client.stream.closed is True


@pytest.mark.asyncio
async def test_two_step_password_retry_does_not_resubmit_code(tmp_path: Path) -> None:
    class PasswordClient:
        def __init__(self) -> None:
            self.sign_in_calls = []

        async def sign_in(self, **values) -> None:
            self.sign_in_calls.append(values)
            if "phone" in values:
                raise errors.SessionPasswordNeededError(request=None)

    client = PasswordClient()
    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(12345, "hash", "+8613800000000"),
        client_factory=lambda *args, **kwargs: client,
    )

    with pytest.raises(AuthenticationRequiredError, match="二步验证密码"):
        await gateway.complete_login("+8613800000000", "123456")
    await gateway.complete_login("+8613800000000", "123456", "two-factor")

    assert client.sign_in_calls == [
        {"phone": "+8613800000000", "code": "123456"},
        {"password": "two-factor"},
    ]


class FakeQrLogin:
    def __init__(self) -> None:
        self.url = "tg://login?token=first"
        self.expires = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
        self.wait_error: Exception | None = None

    async def recreate(self) -> None:
        self.url = "tg://login?token=second"

    async def wait(self) -> None:
        if self.wait_error is not None:
            raise self.wait_error


@pytest.mark.asyncio
async def test_qr_login_create_refresh_and_wait(tmp_path: Path) -> None:
    qr = FakeQrLogin()

    class QrClient:
        async def qr_login(self) -> FakeQrLogin:
            return qr

    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(12345, "hash"),
        client_factory=lambda *args, **kwargs: QrClient(),
    )

    first = await gateway.start_qr_login()
    second = await gateway.refresh_qr_login()
    await gateway.wait_qr_login()

    assert first.url.endswith("first")
    assert first.expires_at == qr.expires
    assert second.url.endswith("second")


@pytest.mark.asyncio
async def test_qr_wait_maps_expiry_and_two_step_password(tmp_path: Path) -> None:
    qr = FakeQrLogin()

    class QrClient:
        def __init__(self) -> None:
            self.passwords: list[str] = []

        async def qr_login(self) -> FakeQrLogin:
            return qr

        async def sign_in(self, *, password: str) -> None:
            self.passwords.append(password)

    client = QrClient()
    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(12345, "hash"),
        client_factory=lambda *args, **kwargs: client,
    )
    await gateway.start_qr_login()

    qr.wait_error = TimeoutError()
    with pytest.raises(QrLoginExpiredError):
        await gateway.wait_qr_login()

    qr.wait_error = errors.SessionPasswordNeededError(request=None)
    with pytest.raises(AuthenticationRequiredError, match="二步验证密码"):
        await gateway.wait_qr_login()
    await gateway.complete_password("two-factor")

    assert client.passwords == ["two-factor"]


def test_qr_login_error_mapping_preserves_user_action_and_retry_time() -> None:
    invalid_api = _mapped_error(errors.ApiIdInvalidError(request=None))
    flood_wait = _mapped_error(errors.FloodWaitError(request=None, capture=73))

    assert isinstance(invalid_api, InvalidApiCredentialsError)
    assert "API ID" in str(invalid_api)
    assert isinstance(flood_wait, TransientTelegramError)
    assert flood_wait.retry_after == 73


@pytest.mark.asyncio
async def test_gateway_logs_out_current_session(tmp_path: Path) -> None:
    class LogOutClient:
        def __init__(self) -> None:
            self.logged_out = False

        async def log_out(self) -> None:
            self.logged_out = True

    client = LogOutClient()
    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(12345, "hash"),
        client_factory=lambda *args, **kwargs: client,
    )

    await gateway.log_out()

    assert client.logged_out is True
