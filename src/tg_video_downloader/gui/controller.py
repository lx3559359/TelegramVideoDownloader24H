from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.diagnostics import DiagnosticReport, Doctor
from tg_video_downloader.gateway import (
    AuthenticationRequiredError,
    QrLoginChallenge,
    TelegramGateway,
)
from tg_video_downloader.models import AppConfig, Credentials, GroupTarget
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.storage import (
    effective_download_root,
    require_writable_download_root,
)
from tg_video_downloader.windows import (
    clear_stop,
    request_stop,
    start_hidden_supervisor,
)


T = TypeVar("T")
STALE_HEARTBEAT_SECONDS = 15


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
        self.config_store.save_credentials(credentials.validate_api())

    @property
    def login_active(self) -> bool:
        return self._login_gateway is not None

    async def saved_session_authorized(self) -> bool:
        credentials = self.load_credentials()
        if credentials is None:
            return False
        gateway = self.gateway_factory(self.paths, credentials)
        try:
            await gateway.connect()
            return await gateway.is_authorized()
        finally:
            await gateway.disconnect()

    async def start_qr_login(
        self,
        credentials: Credentials,
    ) -> QrLoginChallenge | None:
        credentials.validate_api()
        self.save_credentials(credentials)
        await self.cancel_login()
        gateway = self.gateway_factory(self.paths, credentials)
        try:
            await gateway.connect()
            authorized = await gateway.is_authorized()
            if authorized:
                challenge = None
            else:
                challenge = await gateway.start_qr_login()
        except asyncio.CancelledError:
            try:
                await gateway.disconnect()
            except Exception:
                pass
            raise
        except Exception:
            try:
                await gateway.disconnect()
            except Exception:
                pass
            raise
        if authorized:
            await gateway.disconnect()
            return None
        self._login_gateway = gateway
        self._login_credentials = credentials
        return challenge

    async def refresh_qr_login(self) -> QrLoginChallenge:
        if self._login_gateway is None:
            raise ValueError("请先开始二维码登录")
        return await self._login_gateway.refresh_qr_login()

    async def wait_qr_login(self) -> str:
        if self._login_gateway is None:
            raise ValueError("请先开始二维码登录")
        try:
            await self._login_gateway.wait_qr_login()
        except AuthenticationRequiredError as error:
            if "二步验证" in str(error):
                return "需要二步验证密码"
            raise
        await self._clear_login()
        return "登录成功"

    async def complete_qr_password(self, password: str) -> str:
        if self._login_gateway is None:
            raise ValueError("请先扫码登录")
        await self._login_gateway.complete_password(password)
        await self._clear_login()
        return "登录成功"

    async def cancel_login(self) -> None:
        await self._clear_login()

    async def _clear_login(self) -> None:
        gateway = self._login_gateway
        self._login_gateway = None
        self._login_credentials = None
        if gateway is not None:
            await gateway.disconnect()

    async def send_code(self, credentials: Credentials) -> None:
        credentials.validate_phone_login()
        self.save_credentials(credentials)
        await self.cancel_login()
        gateway = self.gateway_factory(self.paths, credentials)
        try:
            await gateway.connect()
            await gateway.send_login_code(credentials.phone)
        except asyncio.CancelledError:
            try:
                await gateway.disconnect()
            except Exception:
                pass
            raise
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
        await self._clear_login()
        return "登录成功"

    async def log_out(self) -> str:
        await self.cancel_login()
        credentials = self.load_credentials()
        if credentials is None:
            raise ValueError("尚未保存账号信息")
        gateway = self.gateway_factory(self.paths, credentials)
        try:
            await gateway.connect()
            if await gateway.is_authorized():
                await gateway.log_out()
        finally:
            await gateway.disconnect()
        return "已退出当前账号"

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

    def selected_groups(self) -> tuple[GroupTarget, ...]:
        try:
            return self.config_store.load_config().groups
        except FileNotFoundError:
            return ()

    def selected_chat_ids(self) -> set[int]:
        return {group.chat_id for group in self.selected_groups()}

    def save_selected_groups(self, groups: tuple[GroupTarget, ...]) -> None:
        try:
            current = self.config_store.load_config()
        except FileNotFoundError:
            current = AppConfig()
        updated = replace(current, groups=groups).require_targets()
        self.config_store.save_config(updated)

    def current_download_root(self) -> Path:
        try:
            config = self.config_store.load_config()
        except FileNotFoundError:
            config = AppConfig()
        return effective_download_root(self.paths, config)

    def save_download_root(self, value: str | Path) -> Path:
        root = require_writable_download_root(self.paths, value)
        try:
            current = self.config_store.load_config()
        except FileNotFoundError:
            current = AppConfig()
        self.config_store.save_config(replace(current, download_root=root))
        return root

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

    async def run_doctor(self) -> tuple[DiagnosticReport, Path]:
        doctor = Doctor(
            self.paths,
            self.gateway_factory,
            login_active=lambda: self.login_active,
        )
        report = await doctor.run()
        return report, doctor.save(report)

    def read_status(self, *, now: datetime | None = None) -> dict[str, object]:
        snapshot = HeartbeatWriter(self.paths.heartbeat).read()
        if not snapshot:
            return {"status": "stopped"}
        if snapshot.get("status") != "running":
            return snapshot
        try:
            updated_at = datetime.fromisoformat(str(snapshot["updated_at"]))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=UTC)
            current = now or datetime.now(UTC)
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            age = (current.astimezone(UTC) - updated_at.astimezone(UTC)).total_seconds()
        except (KeyError, TypeError, ValueError):
            age = STALE_HEARTBEAT_SECONDS + 1
        if age > STALE_HEARTBEAT_SECONDS:
            snapshot["reported_status"] = "running"
            snapshot["status"] = "stale"
            snapshot["error"] = snapshot.get("error") or "后台心跳超过 15 秒未更新"
        return snapshot

    def open_downloads(self) -> None:
        download_root = self.current_download_root()
        download_root.mkdir(parents=True, exist_ok=True)
        os.startfile(download_root)

    def open_logs(self) -> None:
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        os.startfile(self.paths.logs)
