from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class JobSource(StrEnum):
    LIVE = "live"
    CATCHUP = "catchup"
    HISTORY = "history"


class JobStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    PERMANENT_ERROR = "permanent_error"


@dataclass(frozen=True)
class GroupTarget:
    chat_id: int
    title: str


@dataclass(frozen=True)
class AppConfig:
    groups: tuple[GroupTarget, ...] = ()
    config_poll_seconds: int = 5
    prevent_sleep: bool = True

    def require_targets(self) -> "AppConfig":
        if not self.groups:
            raise ValueError("至少选择一个群后才能启动下载器")
        if len({group.chat_id for group in self.groups}) != len(self.groups):
            raise ValueError("群组白名单包含重复群 ID")
        return self


@dataclass(frozen=True)
class Credentials:
    api_id: int
    api_hash: str
    phone: str

    def validate(self) -> "Credentials":
        if self.api_id <= 0 or not self.api_hash.strip() or not self.phone.strip():
            raise ValueError("API ID、API Hash 和手机号均不能为空")
        return self


@dataclass(frozen=True)
class MessageInfo:
    chat_id: int
    message_id: int
    date: datetime
    mime_type: str | None
    original_name: str | None
    extension: str
    size: int | None
    is_video: bool
    is_animated: bool
    is_round: bool


@dataclass(frozen=True)
class DownloadJob:
    chat_id: int
    message_id: int
    group_title: str
    source: JobSource
    status: JobStatus
    message: MessageInfo
    attempts: int
