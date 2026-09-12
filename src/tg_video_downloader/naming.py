from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from tg_video_downloader.models import MessageInfo
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.storage import assert_download_path


WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_windows_name(value: str, max_length: int = 120) -> str:
    cleaned = FORBIDDEN.sub("_", value).rstrip(" .") or "_"
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(" .") or "_"


def collection_directory(message: MessageInfo) -> str:
    collection = message.collection_title
    if not collection:
        return message.date.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")
    directory = sanitize_windows_name(collection)
    # Distinct titles must not collapse after Windows character cleanup.
    if directory != collection or re.fullmatch(r"\d{4}-\d{2}", directory):
        digest = sha256(collection.encode("utf-8")).hexdigest()[:10]
        directory = f"{directory}_{digest}"
    return directory


def build_final_path(
    paths: ProjectPaths,
    group_title: str,
    message: MessageInfo,
    *,
    download_root: Path | None = None,
) -> Path:
    group_dir = sanitize_windows_name(f"{group_title}_{message.chat_id}")
    root = (download_root or paths.downloads).resolve()
    parent = root / group_dir / collection_directory(message)
    filename_limit = min(180, max(32, 240 - len(str(parent)) - 1))
    if message.original_name:
        original = sanitize_windows_name(message.original_name, 150)
        filename = sanitize_windows_name(
            f"{message.message_id}_{original}",
            filename_limit,
        )
    else:
        filename = f"{message.message_id}_video{message.extension or '.mp4'}"
    return assert_download_path(root, parent / filename)
