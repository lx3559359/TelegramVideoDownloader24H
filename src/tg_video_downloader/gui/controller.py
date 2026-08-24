from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Protocol, TypeVar

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.gateway import AuthenticationRequiredError, TelegramGateway
from tg_video_downloader.models import AppConfig, Credentials, GroupTarget
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.windows import (
    clear_stop,
    request_stop,
    start_hidden_supervisor,
)


T = TypeVar("T")


class AsyncBridge:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run_loop,
            name="telegram-gui-async",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    def submit(self, coroutine: Coroutine[Any, Any, T]) -> Future[T]:
        if self._closed:
            coroutine.close()
            raise RuntimeError("异步桥已经关闭")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.close()


class ProcessControl(Protocol):
    def clear_stop(self, paths: ProjectPaths) -> None: ...

    def start(self, project_root: Path) -> object: ...

    def request_stop(self, paths: ProjectPaths) -> None: ...


class WindowsProcessControl:
    def clear_stop(self, paths: ProjectPaths) -> None:
        clear_stop(paths)

    def start(self, project_root: Path) -> object:
        return start_hidden_supervisor(project_root)

    def request_stop(self, paths: ProjectPaths) -> None:
        request_stop(paths)


class GuiController:
    def __init__(
        self,
        paths: ProjectPaths,
        gateway_factory: Callable[[ProjectPaths, Credentials], TelegramGateway],
        *,
        process_control: ProcessControl | None = None,
    ) -> None:
        self.paths = paths
        self.paths.ensure_directories()
        self.config_store = ConfigStore(paths)
        self.gateway_factory = gateway_factory
        self.process_control = process_control or WindowsProcessControl()
        self._login_gateway: TelegramGateway | None = None
        self._login_credentials: Credentials | None = None

    def load_credentials(self) -> Credentials | None:
        try:
            return self.config_store.load_credentials()
        except FileNotFoundError:
            return None

    def save_credentials(self, credentials: Credentials) -> None:
        self.config_store.save_credentials(credentials.validate())

    async def send_code(self, credentials: Credentials) -> None:
        credentials.validate()
        self.save_credentials(credentials)
        if self._login_gateway is not None:
            await self._login_gateway.disconnect()
        gateway = self.gateway_factory(self.paths, credentials)
        try:
            await gateway.connect()
            await gateway.send_login_code(credentials.phone)
        except Exception:
            await gateway.disconnect()
            raise
        self._login_gateway = gateway
        self._login_credentials = credentials

    async def complete_login(self, code: str, password: str) -> str:
        if self._login_gateway is None or self._login_credentials is None:
            raise ValueError("请先发送验证码")
        try:
            await self._login_gateway.complete_login(
                self._login_credentials.phone,
                code.strip(),
                password or None,
            )
        except AuthenticationRequiredError as error:
            if "二步验证密码" in str(error):
                return "需要二步验证密码"
            raise
        await self._login_gateway.disconnect()
        self._login_gateway = None
        self._login_credentials = None
        return "登录成功"

    async def list_groups(self) -> tuple[GroupTarget, ...]:
        credentials = self.load_credentials()
        if credentials is None:
            raise ValueError("请先填写并保存账号信息")
        gateway = self.gateway_factory(self.paths, credentials)
        try:
            await gateway.connect()
            if not await gateway.is_authorized():
                raise AuthenticationRequiredError("请先完成 Telegram 登录")
            return await gateway.list_groups()
        finally:
            await gateway.disconnect()

    def selected_chat_ids(self) -> set[int]:
        try:
            config = self.config_store.load_config()
        except FileNotFoundError:
            return set()
        return {group.chat_id for group in config.groups}

    def save_selected_groups(self, groups: tuple[GroupTarget, ...]) -> None:
        try:
            current = self.config_store.load_config()
        except FileNotFoundError:
            current = AppConfig()
        updated = AppConfig(
            groups=groups,
            config_poll_seconds=current.config_poll_seconds,
            prevent_sleep=current.prevent_sleep,
        ).require_targets()
        self.config_store.save_config(updated)

    def start(self) -> object:
        credentials = self.load_credentials()
        if credentials is None:
            raise ValueError("请先保存账号信息")
        credentials.validate()
        self.config_store.load_config().require_targets()
        self.process_control.clear_stop(self.paths)
        return self.process_control.start(self.paths.root)

    def stop(self) -> None:
        self.process_control.request_stop(self.paths)

    def read_status(self) -> dict[str, object]:
        snapshot = HeartbeatWriter(self.paths.heartbeat).read()
        return snapshot or {"status": "stopped"}

    def open_downloads(self) -> None:
        self.paths.downloads.mkdir(parents=True, exist_ok=True)
        os.startfile(self.paths.downloads)

    def open_logs(self) -> None:
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        os.startfile(self.paths.logs)
