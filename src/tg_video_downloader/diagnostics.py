from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.gateway import (
    AuthenticationRequiredError,
    TelegramGateway,
    TransientTelegramError,
)
from tg_video_downloader.models import AppConfig, Credentials
from tg_video_downloader.observability import HeartbeatWriter
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.worker import SAFETY_FREE_BYTES


DiagnosticStatus = Literal["pass", "warning", "fail"]
GatewayFactory = Callable[[ProjectPaths, Credentials], TelegramGateway]


@dataclass(frozen=True)
class DiagnosticCheck:
    key: str
    status: DiagnosticStatus
    message: str


@dataclass(frozen=True)
class DiagnosticReport:
    generated_at: str
    checks: tuple[DiagnosticCheck, ...]

    @property
    def exit_code(self) -> int:
        if any(item.status == "fail" for item in self.checks):
            return 2
        if any(item.status == "warning" for item in self.checks):
            return 1
        return 0

    @property
    def counts(self) -> dict[str, int]:
        return {
            status: sum(item.status == status for item in self.checks)
            for status in ("pass", "warning", "fail")
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "exit_code": self.exit_code,
            "counts": self.counts,
            "checks": [asdict(item) for item in self.checks],
        }


class Doctor:
    def __init__(self, paths: ProjectPaths, gateway_factory: GatewayFactory) -> None:
        self.paths = paths
        self.gateway_factory = gateway_factory
        self._secrets: tuple[str, ...] = ()

    async def run(self) -> DiagnosticReport:
        self.paths.ensure_directories()
        config_store = ConfigStore(self.paths)
        checks = [
            self._run_local("project_paths", self._check_project_paths),
            self._run_local("python", self._check_python),
            self._run_local("dependencies", self._check_dependencies),
        ]

        config, config_check = self._load_config(config_store)
        checks.append(config_check)
        credentials, credentials_check = self._load_credentials(config_store)
        checks.append(credentials_check)
        if credentials is not None:
            self._secrets = (credentials.api_hash, credentials.phone)

        checks.extend(
            (
                self._run_local("disk", self._check_disk),
                self._run_local("database", self._check_database),
                self._run_local("heartbeat", self._check_heartbeat),
                await self._check_telegram(config, credentials),
            )
        )
        redacted = tuple(
            DiagnosticCheck(item.key, item.status, self._redact(item.message))
            for item in checks
        )
        return DiagnosticReport(
            generated_at=datetime.now(UTC).isoformat(),
            checks=redacted,
        )

    def save(self, report: DiagnosticReport) -> Path:
        report_dir = self.paths.assert_within_root(self.paths.logs / "diagnostics")
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.fromisoformat(report.generated_at).astimezone(UTC)
        name = timestamp.strftime("doctor-%Y%m%dT%H%M%S-%fZ.json")
        destination = self.paths.assert_within_root(report_dir / name)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        content = json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def _run_local(
        self,
        key: str,
        check: Callable[[], DiagnosticCheck],
    ) -> DiagnosticCheck:
        try:
            return check()
        except Exception as error:
            return DiagnosticCheck(key, "fail", self._format_error(error))

    def _check_project_paths(self) -> DiagnosticCheck:
        for directory in self.paths.writable_directories:
            checked = self.paths.assert_within_root(directory)
            checked.mkdir(parents=True, exist_ok=True)
            probe = self.paths.assert_within_root(checked / ".doctor-write-test")
            try:
                probe.write_text("ok", encoding="ascii")
            finally:
                probe.unlink(missing_ok=True)
        return DiagnosticCheck("project_paths", "pass", "项目运行目录均位于项目内且可写")

    def _check_python(self) -> DiagnosticCheck:
        current = sys.version_info
        if current < (3, 11):
            return DiagnosticCheck("python", "fail", "Python 版本低于 3.11")
        return DiagnosticCheck(
            "python",
            "pass",
            f"Python {current.major}.{current.minor}.{current.micro}",
        )

    def _check_dependencies(self) -> DiagnosticCheck:
        installed: list[str] = []
        for distribution in ("telethon", "tzdata"):
            try:
                installed.append(f"{distribution} {version(distribution)}")
            except PackageNotFoundError as error:
                raise RuntimeError(f"缺少依赖 {distribution}") from error
        return DiagnosticCheck("dependencies", "pass", "，".join(installed))

    def _load_config(
        self,
        store: ConfigStore,
    ) -> tuple[AppConfig | None, DiagnosticCheck]:
        try:
            config = store.load_config().require_targets()
        except (OSError, KeyError, TypeError, ValueError) as error:
            return None, DiagnosticCheck("config", "fail", self._format_error(error))
        return config, DiagnosticCheck(
            "config",
            "pass",
            f"配置有效，已选择 {len(config.groups)} 个群",
        )

    def _load_credentials(
        self,
        store: ConfigStore,
    ) -> tuple[Credentials | None, DiagnosticCheck]:
        try:
            credentials = store.load_credentials().validate()
        except (OSError, KeyError, TypeError, ValueError) as error:
            return None, DiagnosticCheck(
                "credentials",
                "fail",
                self._format_error(error),
            )
        return credentials, DiagnosticCheck("credentials", "pass", "账号凭据格式有效")

    def _check_disk(self) -> DiagnosticCheck:
        free = int(shutil.disk_usage(self.paths.downloads).free)
        status: DiagnosticStatus = "pass" if free >= SAFETY_FREE_BYTES else "fail"
        return DiagnosticCheck(
            "disk",
            status,
            f"下载盘可用空间 {free / (1024**3):.2f} GiB",
        )

    def _check_database(self) -> DiagnosticCheck:
        if not self.paths.database.exists():
            return DiagnosticCheck("database", "warning", "状态数据库尚未创建")
        uri = self.paths.database.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if result.lower() != "ok":
            return DiagnosticCheck("database", "fail", f"SQLite 检查结果：{result}")
        return DiagnosticCheck("database", "pass", "SQLite quick_check 通过")

    def _check_heartbeat(self) -> DiagnosticCheck:
        snapshot = HeartbeatWriter(self.paths.heartbeat).read()
        if not snapshot:
            return DiagnosticCheck("heartbeat", "warning", "后台心跳尚未创建")
        status = str(snapshot.get("status", "unknown"))
        if status == "running":
            updated = datetime.fromisoformat(str(snapshot["updated_at"]))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - updated.astimezone(UTC)).total_seconds()
            if age > 15:
                return DiagnosticCheck(
                    "heartbeat",
                    "warning",
                    f"后台心跳已停止更新 {age:.0f} 秒",
                )
        if status in {"error", "needs_config", "needs_login"}:
            return DiagnosticCheck("heartbeat", "warning", f"后台状态：{status}")
        return DiagnosticCheck("heartbeat", "pass", f"后台状态：{status}")

    async def _check_telegram(
        self,
        config: AppConfig | None,
        credentials: Credentials | None,
    ) -> DiagnosticCheck:
        if config is None or credentials is None:
            return DiagnosticCheck("telegram", "fail", "配置或账号凭据无效，未执行在线检查")
        gateway: TelegramGateway | None = None
        try:
            gateway = self.gateway_factory(self.paths, credentials)
            await gateway.connect()
            if not await gateway.is_authorized():
                return DiagnosticCheck("telegram", "fail", "Telegram 会话需要重新登录")
            visible = {group.chat_id for group in await gateway.list_groups()}
            missing = {group.chat_id for group in config.groups} - visible
            if missing:
                return DiagnosticCheck(
                    "telegram",
                    "fail",
                    f"有 {len(missing)} 个白名单群当前不可见",
                )
            return DiagnosticCheck(
                "telegram",
                "pass",
                f"Telegram 在线检查通过，{len(config.groups)} 个白名单群可见",
            )
        except AuthenticationRequiredError as error:
            return DiagnosticCheck("telegram", "fail", self._format_error(error))
        except (TransientTelegramError, ConnectionError, TimeoutError, OSError) as error:
            return DiagnosticCheck("telegram", "warning", self._format_error(error))
        except Exception as error:
            return DiagnosticCheck("telegram", "fail", self._format_error(error))
        finally:
            if gateway is not None:
                try:
                    await gateway.disconnect()
                except Exception:
                    pass

    def _format_error(self, error: Exception) -> str:
        return self._redact(str(error) or type(error).__name__)

    def _redact(self, message: str) -> str:
        for secret in sorted((value for value in self._secrets if value), key=len, reverse=True):
            message = message.replace(secret, "***")
        return message
