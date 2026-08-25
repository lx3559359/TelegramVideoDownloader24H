from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from tg_video_downloader.models import AppConfig, Credentials, GroupTarget
from tg_video_downloader.paths import ProjectPaths


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _download_history(group: dict[str, Any]) -> bool:
    value = group.get("download_history", True)
    if not isinstance(value, bool):
        raise ValueError("download_history 必须是布尔值")
    return value


class ConfigStore:
    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths

    def load_config(self) -> AppConfig:
        data = _read_toml(self.paths.config)
        poll_seconds = data.get("config_poll_seconds", 5)
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, int):
            raise ValueError("配置轮询秒数必须是整数")
        if not 1 <= poll_seconds <= 60:
            raise ValueError("配置轮询秒数必须在 1 到 60 之间")

        prevent_sleep = data.get("prevent_sleep", True)
        if not isinstance(prevent_sleep, bool):
            raise ValueError("prevent_sleep 必须是布尔值")

        groups_data = data.get("groups", [])
        if not isinstance(groups_data, list):
            raise ValueError("groups 必须是群组/频道列表")
        groups = tuple(
            GroupTarget(
                chat_id=int(group["chat_id"]),
                title=str(group["title"]),
                download_history=_download_history(group),
            )
            for group in groups_data
        )
        return AppConfig(
            groups=groups,
            config_poll_seconds=poll_seconds,
            prevent_sleep=prevent_sleep,
        )

    def save_config(self, config: AppConfig) -> None:
        lines = [
            f"config_poll_seconds = {config.config_poll_seconds}",
            f"prevent_sleep = {str(config.prevent_sleep).lower()}",
        ]
        for group in config.groups:
            lines.extend(
                [
                    "",
                    "[[groups]]",
                    f"chat_id = {group.chat_id}",
                    f"title = {_quoted(group.title)}",
                    f"download_history = {str(group.download_history).lower()}",
                ]
            )
        _atomic_write(self.paths.config, "\n".join(lines) + "\n")

    def load_credentials(self) -> Credentials:
        data = _read_toml(self.paths.credentials)
        credentials = Credentials(
            api_id=int(data["api_id"]),
            api_hash=str(data["api_hash"]),
            phone=str(data.get("phone", "")),
        )
        return credentials.validate_api()

    def save_credentials(self, credentials: Credentials) -> None:
        credentials.validate_api()
        content = "\n".join(
            [
                f"api_id = {credentials.api_id}",
                f"api_hash = {_quoted(credentials.api_hash)}",
                f"phone = {_quoted(credentials.phone)}",
                "",
            ]
        )
        _atomic_write(self.paths.credentials, content)

    def reloader(self) -> "ConfigReloader":
        return ConfigReloader(self)


class ConfigReloader:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store
        self._observed_mtime_ns: int | None = None
        self._initialized = False
        self._last_valid: AppConfig | None = None
        self.last_error: str | None = None

    def load_if_changed(self) -> AppConfig | None:
        try:
            mtime_ns = self._store.paths.config.stat().st_mtime_ns
        except OSError as error:
            self.last_error = str(error)
            return self._last_valid

        if self._initialized and mtime_ns == self._observed_mtime_ns:
            return None

        self._initialized = True
        self._observed_mtime_ns = mtime_ns
        try:
            config = self._store.load_config()
        except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
            self.last_error = str(error)
            return self._last_valid

        self._last_valid = config
        self.last_error = None
        return config
