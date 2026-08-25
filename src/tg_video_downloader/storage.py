from __future__ import annotations

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
