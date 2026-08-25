from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic as monotonic_clock
from typing import Any

from tg_video_downloader.gateway import (
    AuthenticationRequiredError,
    DOWNLOAD_CHUNK_SIZE,
    GroupAccessError,
    PermanentMessageError,
    TelegramGateway,
    TransientTelegramError,
)
from tg_video_downloader.models import DownloadJob
from tg_video_downloader.naming import build_final_path
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.state import StateStore


SAFETY_FREE_BYTES = 512 * 1024 * 1024
QUICK_RETRY_DELAYS = (5, 15, 30, 60, 120)
LONG_RETRY_SECONDS = 15 * 60


@dataclass(frozen=True)
class DownloadProgress:
    file_name: str
    downloaded_bytes: int
    total_bytes: int | None
    percent: float | None
    bytes_per_second: float
    resumed: bool


class DiskGuard:
    def __init__(
        self,
        downloads: Path,
        usage: Callable[[Path], Any] = shutil.disk_usage,
    ) -> None:
        self.downloads = downloads
        self._usage = usage

    def has_space(self, expected_size: int | None) -> bool:
        required = max(0, expected_size or 0) + SAFETY_FREE_BYTES
        return int(self._usage(self.downloads).free) >= required


class DownloadWorker:
    def __init__(
        self,
        paths: ProjectPaths,
        state: StateStore,
        gateway: TelegramGateway,
        *,
        monotonic: Callable[[], float] = monotonic_clock,
        stall_seconds: float = 120.0,
        monitor_seconds: float = 1.0,
    ) -> None:
        self.paths = paths
        self.state = state
        self.gateway = gateway
        self.disk_guard = DiskGuard(paths.downloads)
        self._monotonic = monotonic
        self._stall_seconds = stall_seconds
        self._monitor_seconds = monitor_seconds
        self._current_file: str | None = None
        self._progress: DownloadProgress | None = None

    @property
    def current_file(self) -> str | None:
        return self._current_file

    @property
    def progress(self) -> DownloadProgress | None:
        return self._progress

    def recover(self) -> int:
        recovered = self.state.recover_inflight()
        return len(recovered)

    async def run_one(self, stop: asyncio.Event | None = None) -> str:
        job = self.state.claim_next()
        if job is None:
            return "idle"

        final_path = build_final_path(self.paths, job.group_title, job.message)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = self._part_path(job.chat_id, job.message_id)
        self._current_file = final_path.name

        try:
            if _matches_expected_size(final_path, job.message.size):
                part_path.unlink(missing_ok=True)
                self.state.mark_completed(job, final_path)
                return "completed"

            download_task: asyncio.Task[Path] | None = None
            try:
                offset = _resume_offset(part_path, job.message.size)
                if job.message.size is not None and offset == job.message.size:
                    os.replace(part_path, final_path)
                    self.state.mark_completed(job, final_path)
                    return "completed"

                remaining = (
                    None
                    if job.message.size is None
                    else max(0, job.message.size - offset)
                )
                if not self.disk_guard.has_space(remaining):
                    self.state.mark_retry(
                        job,
                        "磁盘可用空间低于安全阈值",
                        delay_seconds=60,
                    )
                    return "disk_paused"

                started_at = self._monotonic()
                last_progress = started_at
                last_downloaded = offset
                resumed = offset > 0
                self._progress = DownloadProgress(
                    file_name=final_path.name,
                    downloaded_bytes=offset,
                    total_bytes=job.message.size,
                    percent=_percent(offset, job.message.size),
                    bytes_per_second=0.0,
                    resumed=resumed,
                )

                def on_progress(downloaded: int, total: int | None) -> None:
                    nonlocal last_downloaded, last_progress
                    now = self._monotonic()
                    if downloaded > last_downloaded:
                        last_downloaded = downloaded
                        last_progress = now
                    effective_total = (
                        total if isinstance(total, int) else job.message.size
                    )
                    elapsed = max(0.0, now - started_at)
                    received = max(0, downloaded - offset)
                    speed = received / elapsed if elapsed > 0 else 0.0
                    self._progress = DownloadProgress(
                        file_name=final_path.name,
                        downloaded_bytes=downloaded,
                        total_bytes=effective_total,
                        percent=_percent(downloaded, effective_total),
                        bytes_per_second=speed,
                        resumed=resumed,
                    )

                download_task = asyncio.create_task(
                    self.gateway.download_message(
                        job.chat_id,
                        job.message_id,
                        part_path,
                        offset=offset,
                        progress_callback=on_progress,
                    )
                )
                while not download_task.done():
                    if stop is not None and stop.is_set():
                        download_task.cancel()
                        await asyncio.gather(download_task, return_exceptions=True)
                        self.state.release(job)
                        return "stopped"
                    if self._monotonic() - last_progress >= self._stall_seconds:
                        download_task.cancel()
                        await asyncio.gather(download_task, return_exceptions=True)
                        raise TransientTelegramError(
                            "下载连续 120 秒没有进度"
                        )
                    await asyncio.sleep(self._monitor_seconds)

                actual_path = await download_task
                actual_path = self.paths.assert_within_root(Path(actual_path))
                if actual_path != part_path:
                    os.replace(actual_path, part_path)
                if not _matches_expected_size(part_path, job.message.size):
                    raise TransientTelegramError("下载文件大小与 Telegram 元数据不一致")
                os.replace(part_path, final_path)
            except asyncio.CancelledError:
                if download_task is not None and not download_task.done():
                    download_task.cancel()
                    await asyncio.gather(download_task, return_exceptions=True)
                self.state.release(job)
                raise
            except AuthenticationRequiredError:
                raise
            except PermanentMessageError as error:
                part_path.unlink(missing_ok=True)
                self.state.mark_permanent_error(job, str(error))
                return "permanent_error"
            except GroupAccessError as error:
                self.state.set_access_error(job.chat_id, str(error))
                self.state.mark_retry(job, str(error), delay_seconds=LONG_RETRY_SECONDS)
                return "retry_wait"
            except (TransientTelegramError, OSError) as error:
                self.state.mark_retry(
                    job,
                    str(error),
                    delay_seconds=_retry_delay(job),
                )
                return "retry_wait"

            self.state.set_access_error(job.chat_id, None)
            self.state.mark_completed(job, final_path)
            return "completed"
        finally:
            self._current_file = None
            self._progress = None

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            result = await self.run_one(stop)
            if result == "stopped":
                return
            if result == "idle":
                await _wait_or_stop(stop, 1)
            elif result == "disk_paused":
                await _wait_or_stop(stop, 60)

    def _part_path(self, chat_id: int, message_id: int) -> Path:
        path = self.paths.temp / f"{chat_id}_{message_id}.part"
        return self.paths.assert_within_root(path)


def _matches_expected_size(path: Path, expected_size: int | None) -> bool:
    return expected_size is not None and path.is_file() and path.stat().st_size == expected_size


def _resume_offset(part_path: Path, expected_size: int | None) -> int:
    if not part_path.is_file():
        return 0
    if expected_size is None:
        part_path.unlink(missing_ok=True)
        return 0
    size = part_path.stat().st_size
    if size > expected_size:
        part_path.unlink(missing_ok=True)
        return 0
    if size == expected_size:
        return size
    aligned = size - (size % DOWNLOAD_CHUNK_SIZE)
    if aligned != size:
        with part_path.open("r+b") as handle:
            handle.truncate(aligned)
    return aligned


def _percent(downloaded: int, total: int | None) -> float | None:
    if total is None or total <= 0:
        return None
    return min(100.0, max(0.0, downloaded * 100.0 / total))


def _retry_delay(job: DownloadJob) -> int:
    if 1 <= job.attempts <= len(QUICK_RETRY_DELAYS):
        return QUICK_RETRY_DELAYS[job.attempts - 1]
    return LONG_RETRY_SECONDS


async def _wait_or_stop(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass
