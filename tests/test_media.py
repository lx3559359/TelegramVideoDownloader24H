from dataclasses import replace
from datetime import UTC, datetime

from tg_video_downloader.media import is_downloadable_video
from tg_video_downloader.models import MessageInfo


BASE = MessageInfo(-1001, 7, datetime.now(UTC), "video/mp4", "a.mp4", ".mp4", 10, True, False, False)


def test_video_rules() -> None:
    assert is_downloadable_video(BASE)
    assert is_downloadable_video(replace(BASE, is_video=False, mime_type="video/x-matroska"))
    assert not is_downloadable_video(replace(BASE, is_animated=True))
    assert not is_downloadable_video(replace(BASE, is_round=True))
    assert not is_downloadable_video(replace(BASE, is_video=False, mime_type="image/jpeg"))
    assert not is_downloadable_video(replace(BASE, is_video=False, mime_type="application/pdf"))
