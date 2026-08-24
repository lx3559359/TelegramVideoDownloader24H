from datetime import UTC, datetime
from pathlib import Path

from tg_video_downloader.models import MessageInfo
from tg_video_downloader.naming import build_final_path, sanitize_windows_name
from tg_video_downloader.paths import ProjectPaths


def test_sanitize_reserved_and_forbidden_names() -> None:
    assert sanitize_windows_name("CON") == "_CON"
    assert sanitize_windows_name('bad<>:"/\\|?* .') == "bad_________"


def test_build_path_is_unique_and_inside_downloads(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    message = MessageInfo(
        -1001, 42, datetime(2026, 8, 24, 1, tzinfo=UTC), "video/mp4",
        "same:name.mp4", ".mp4", 100, True, False, False,
    )
    result = build_final_path(paths, "测试群", message)

    assert result == paths.downloads / "测试群_-1001" / "2026-08" / "42_same_name.mp4"
    assert result.resolve().is_relative_to(paths.downloads.resolve())
