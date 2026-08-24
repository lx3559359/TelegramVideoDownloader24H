from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tg_video_downloader.gateway import (
    AuthenticationRequiredError,
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
    ) -> None:
        self.paths = paths
        self.state = state
        self.gateway = gateway
        self.disk_guard = DiskGuard(paths.downloads)

    def recover(self) -> int:
        recovered = self.state.recover_inflight()
        for chat_id, message_id in recovered:
            self._part_path(chat_id, message_id).unlink(missing_ok=True)
        return len(recovered)

    async def run_one(self) -> str:
        job = self.state.claim_next()
        if job is None:
            return "idle"

        final_path = build_final_path(self.paths, job.group_title, job.message)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = self._part_path(job.chat_id, job.message_id)

        if _matches_expected_size(final_path, job.message.size):
            part_path.unlink(missing_ok=True)
            self.state.mark_completed(job, final_path)
            return "completed"

        if not self.disk_guard.has_space(job.message.size):
            self.state.mark_retry(job, "磁盘可用空间低于安全阈值", delay_seconds=60)
            return "disk_paused"

        part_path.unlink(missing_ok=True)
        try:
            actual_path = await self.gateway.download_message(
                job.chat_id,
                job.message_id,
                part_path,
            )
            actual_path = self.paths.assert_within_root(Path(actual_path))
            if actual_path != part_path:
                os.replace(actual_path, part_path)
            if not _matches_expected_size(part_path, job.message.size):
                raise TransientTelegramError("下载文件大小与 Telegram 元数据不一致")
            os.replace(part_path, final_path)
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

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            result = await self.run_one()
            if result == "idle":
                await _wait_or_stop(stop, 1)
            elif result == "disk_paused":
                await _wait_or_stop(stop, 60)

    def _part_path(self, chat_id: int, message_id: int) -> Path:
        path = self.paths.temp / f"{chat_id}_{message_id}.part"
        return self.paths.assert_within_root(path)


def _matches_expected_size(path: Path, expected_size: int | None) -> bool:
    return expected_size is not None and path.is_file() and path.stat().st_size == expected_size


def _retry_delay(job: DownloadJob) -> int:
    if 1 <= job.attempts <= len(QUICK_RETRY_DELAYS):
        return QUICK_RETRY_DELAYS[job.attempts - 1]
    return LONG_RETRY_SECONDS


async def _wait_or_stop(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass
