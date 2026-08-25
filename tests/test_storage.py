import ctypes
import os
from pathlib import Path

import pytest

from tg_video_downloader.models import AppConfig
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.storage import (
    assert_download_path,
    build_part_path,
    effective_download_root,
    ensure_partial_directory,
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


def test_part_path_stays_in_destination_local_private_directory(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "selected").resolve()

    part_path = build_part_path(root, -1001, 7)

    assert part_path == root / ".tg-video-downloader" / "partial" / "-1001_7.part"
    assert part_path.resolve().is_relative_to(root)


def test_download_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / ".tg-video-downloader"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 配置不允许创建目录符号链接")

    with pytest.raises(ValueError, match="下载目录之外"):
        build_part_path(root, -1001, 7)


def test_assert_download_path_accepts_root_itself(tmp_path: Path) -> None:
    root = (tmp_path / "selected").resolve()

    assert assert_download_path(root, root) == root


@pytest.mark.skipif(os.name != "nt", reason="Windows hidden attribute")
def test_partial_directory_has_windows_hidden_attribute(tmp_path: Path) -> None:
    parent = ensure_partial_directory(tmp_path / "selected")

    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(parent))

    assert attributes != 0xFFFFFFFF
    assert attributes & 0x2
