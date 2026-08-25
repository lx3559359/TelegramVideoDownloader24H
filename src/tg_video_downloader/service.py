from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from tg_video_downloader.config import ConfigReloader, ConfigStore
from tg_video_downloader.coordinator import ScannerCoordinator
from tg_video_downloader.gateway import (
    AuthenticationRequiredError,
    TelegramGateway,
)
from tg_video_downloader.models import AppConfig, Credentials
from tg_video_downloader.observability import HeartbeatWriter, configure_logging
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.state import StateStore
from tg_video_downloader.windows import (
    PreventIdleSleep,
    SingleInstance,
    is_stop_requested,
)
from tg_video_downloader.worker import DownloadWorker


class DownloaderService:
    def __init__(
        self,
        paths: ProjectPaths,
        gateway_factory: Callable[[ProjectPaths, Credentials], TelegramGateway],
    ) -> None:
        self.paths = paths
        self.gateway_factory = gateway_factory
        self._config_error: str | None = None

    async def run(self) -> int:
        self.paths.ensure_directories()
        heartbeat = HeartbeatWriter(self.paths.heartbeat)
        logger = configure_logging(self.paths.logs, ())
        config_store = ConfigStore(self.paths)
        try:
            config = config_store.load_config().require_targets()
            credentials = config_store.load_credentials().validate()
        except (OSError, KeyError, TypeError, ValueError) as error:
            logger.warning("启动配置无效: %s", error)
            heartbeat.write({**_base_snapshot("needs_config"), "error": str(error)})
            return 2
        logger = configure_logging(
            self.paths.logs,
            (credentials.api_hash, credentials.phone),
        )

        with SingleInstance(self.paths.runtime / "downloader.lock"):
            if is_stop_requested(self.paths):
                heartbeat.write(_base_snapshot("stopped"))
                return 0
            sleep_context = PreventIdleSleep() if config.prevent_sleep else nullcontext()
            with sleep_context:
                return await self._run_connected(
                    config_store,
                    config,
                    credentials,
                    heartbeat,
                    logger,
                )

    async def _run_connected(
        self,
        config_store: ConfigStore,
        config: AppConfig,
        credentials: Credentials,
        heartbeat: HeartbeatWriter,
        logger: logging.Logger,
    ) -> int:
        state = StateStore(self.paths.database)
        gateway = self.gateway_factory(self.paths, credentials)
        stop = asyncio.Event()
        tasks: list[asyncio.Task[None]] = []
        status = "stopped"
        worker: DownloadWorker | None = None
        try:
            worker = DownloadWorker(self.paths, state, gateway)
            coordinator = ScannerCoordinator(state, gateway)
            recovered = worker.recover()
            logger.info("恢复了 %d 个中断下载任务", recovered)

            await gateway.connect()
            if not await gateway.is_authorized():
                raise AuthenticationRequiredError("Telegram 账号需要登录")
            await coordinator.start(config.groups)

            reloader = config_store.reloader()
            reloader.load_if_changed()
            config_holder = [config]
            heartbeat.write(self._snapshot("running", state, worker=worker))
            tasks = [
                asyncio.create_task(coordinator.run_scans(stop), name="history-scans"),
                asyncio.create_task(coordinator.run_catchups(stop), name="catch-up-scans"),
                asyncio.create_task(worker.run(stop), name="downloads"),
                asyncio.create_task(
                    self._watch_config(reloader, coordinator, config_holder, stop),
                    name="config-reload",
                ),
                asyncio.create_task(
                    self._write_heartbeat(heartbeat, state, worker, stop),
                    name="heartbeat",
                ),
                asyncio.create_task(self._watch_stop(stop), name="stop-flag"),
            ]
            await asyncio.gather(*tasks)
            status = "stopped"
            return 0
        except AuthenticationRequiredError as error:
            status = "needs_login"
            logger.warning("Telegram 认证失效: %s", error)
            heartbeat.write(self._snapshot(status, state, worker=worker, error=str(error)))
            return 0
        except Exception:
            status = "error"
            logger.exception("后台服务异常退出")
            heartbeat.write(self._snapshot(status, state, worker=worker))
            raise
        finally:
            stop.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await gateway.disconnect()
            except Exception:
                logger.exception("断开 Telegram 连接时发生错误")
            if status == "stopped":
                heartbeat.write(self._snapshot("stopped", state, worker=worker))
            state.close()

    async def _watch_config(
        self,
        reloader: ConfigReloader,
        coordinator: ScannerCoordinator,
        config_holder: list[AppConfig],
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            await _wait_or_stop(stop, config_holder[0].config_poll_seconds)
            if stop.is_set():
                return
            candidate = reloader.load_if_changed()
            if reloader.last_error is not None:
                self._config_error = reloader.last_error
                continue
            if candidate is None:
                continue
            try:
                candidate.require_targets()
            except ValueError as error:
                self._config_error = str(error)
                continue
            await coordinator.apply_targets(candidate.groups)
            config_holder[0] = candidate
            self._config_error = None

    async def _write_heartbeat(
        self,
        writer: HeartbeatWriter,
        state: StateStore,
        worker: DownloadWorker,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            await _wait_or_stop(stop, 5)
            if not stop.is_set():
                writer.write(self._snapshot("running", state, worker=worker))

    async def _watch_stop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if is_stop_requested(self.paths):
                stop.set()
                return
            await _wait_or_stop(stop, 1)

    def _snapshot(
        self,
        status: str,
        state: StateStore,
        *,
        worker: DownloadWorker | Any | None = None,
        error: str | None = None,
    ) -> dict[str, object]:
        snapshot = _base_snapshot(status)
        snapshot["counts"] = state.counts()
        snapshot["groups"] = [
            {
                "chat_id": group.chat_id,
                "title": group.title,
                "enabled": group.enabled,
                "download_history": group.download_history,
                "history_complete": group.history_complete,
                "access_error": group.access_error,
            }
            for group in state.group_states()
        ]
        current_file = getattr(worker, "current_file", None)
        if current_file:
            snapshot["current_file"] = str(current_file)
        progress = getattr(worker, "progress", None)
        if progress is not None:
            snapshot["progress"] = asdict(progress)
        if self._config_error:
            snapshot["config_error"] = self._config_error
        if error:
            snapshot["error"] = error
        return snapshot


def _base_snapshot(status: str) -> dict[str, object]:
    return {
        "status": status,
        "pid": os.getpid(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


async def _wait_or_stop(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass
