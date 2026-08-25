from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from tg_video_downloader.paths import ProjectPaths

if TYPE_CHECKING:
    from tg_video_downloader.models import AppConfig


PROTECTED_PROJECT_DIRECTORIES = (
    ".git",
    ".venv",
    ".runtime",
    ".cache",
    ".tmp",
    "logs",
)
WINDOWS_HIDDEN_ATTRIBUTE = 0x2
WINDOWS_INVALID_ATTRIBUTES = 0xFFFFFFFF


def parse_download_root(paths: ProjectPaths, value: str | Path) -> Path:
    raw = str(value).strip()
    if raw.startswith(("\\\\", "//")):
        raise ValueError("下载目录不支持 UNC 网络共享路径")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError("下载目录必须是本地磁盘绝对路径")
    resolved = candidate.resolve()
    protected = tuple(
        (paths.root / name).resolve()
        for name in PROTECTED_PROJECT_DIRECTORIES
    )
    if any(
        resolved == directory or resolved.is_relative_to(directory)
        for directory in protected
    ):
        raise ValueError("下载目录不能位于项目运行目录中")
    return resolved


def effective_download_root(paths: ProjectPaths, config: "AppConfig") -> Path:
    return config.download_root or paths.downloads


def assert_download_path(root: Path, path: Path) -> Path:
    checked_root = root.resolve()
    checked = path.resolve()
    if not checked.is_relative_to(checked_root):
        raise ValueError(f"任务路径位于下载目录之外: {checked}")
    return checked


def build_part_path(root: Path, chat_id: int, message_id: int) -> Path:
    parent = assert_download_path(
        root,
        root / ".tg-video-downloader" / "partial",
    )
    return assert_download_path(
        parent,
        parent / f"{chat_id}_{message_id}.part",
    )


def ensure_partial_directory(root: Path) -> Path:
    private_root = assert_download_path(root, root / ".tg-video-downloader")
    parent = assert_download_path(root, private_root / "partial")
    parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        _ensure_windows_hidden(private_root)
        _ensure_windows_hidden(parent)
    return parent


def _ensure_windows_hidden(path: Path) -> None:
    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attributes == WINDOWS_INVALID_ATTRIBUTES:
        raise ctypes.WinError()
    if attributes & WINDOWS_HIDDEN_ATTRIBUTE:
        return
    if not ctypes.windll.kernel32.SetFileAttributesW(
        str(path),
        attributes | WINDOWS_HIDDEN_ATTRIBUTE,
    ):
        raise ctypes.WinError()


def require_writable_download_root(
    paths: ProjectPaths,
    value: str | Path,
) -> Path:
    root = parse_download_root(paths, value)
    if root.exists() and not root.is_dir():
        raise ValueError("下载保存位置不是文件夹")
    root.mkdir(parents=True, exist_ok=True)
    probe = root / f".tg-video-downloader-write-{os.getpid()}-{uuid4().hex}"
    try:
        with probe.open("x", encoding="ascii") as handle:
            handle.write("ok")
    finally:
        probe.unlink(missing_ok=True)
    return root
