from pathlib import Path

import pytest

from tg_video_downloader.models import AppConfig
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.storage import (
    effective_download_root,
    parse_download_root,
    require_writable_download_root,
)


def test_missing_download_root_uses_project_downloads(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)

    assert effective_download_root(paths, AppConfig()) == paths.downloads


@pytest.mark.parametrize("value", [r"relative\folder", r"\\server\share", "//server/share"])
def test_non_local_absolute_download_root_is_rejected(
    tmp_path: Path,
    value: str,
) -> None:
    with pytest.raises(ValueError, match="本地磁盘绝对路径|UNC"):
        parse_download_root(ProjectPaths.from_root(tmp_path), value)


@pytest.mark.parametrize(
    "directory",
    [".git", ".venv", ".runtime", ".cache", ".tmp", "logs"],
)
def test_project_control_directory_is_rejected(
    tmp_path: Path,
    directory: str,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)

    with pytest.raises(ValueError, match="运行目录"):
        parse_download_root(paths, paths.root / directory / "nested")


def test_writable_download_root_is_created_and_probe_is_removed(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path / "project")
    selected = (tmp_path / "media").resolve()

    assert require_writable_download_root(paths, selected) == selected
    assert selected.is_dir()
    assert list(selected.glob(".tg-video-downloader-write-*")) == []


def test_file_download_root_is_rejected(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path / "project")
    selected = tmp_path / "occupied"
    selected.write_text("file", encoding="utf-8")

    with pytest.raises(ValueError, match="文件夹"):
        require_writable_download_root(paths, selected)
