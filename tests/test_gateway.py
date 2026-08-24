from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors

from tg_video_downloader.gateway import (
    AuthenticationRequiredError,
    TelethonGateway,
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
async def test_list_groups_excludes_private_chats_and_sorts(tmp_path: Path) -> None:
    dialogs = (
        SimpleNamespace(id=-1002, name="beta", is_group=True),
        SimpleNamespace(id=7, name="Private", is_group=False),
        SimpleNamespace(id=-1001, name="Alpha", is_group=True),
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
        GroupTarget(-1001, "Alpha"),
        GroupTarget(-1002, "beta"),
    )
    assert captured["session"] == str(paths.session)
    assert captured["options"] == {
        "auto_reconnect": True,
        "connection_retries": -1,
        "retry_delay": 5,
        "flood_sleep_threshold": 60,
    }


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
