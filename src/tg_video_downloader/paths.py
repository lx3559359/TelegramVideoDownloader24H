from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    runtime: Path
    cache: Path
    temp: Path
    logs: Path
    downloads: Path
    config: Path
    credentials: Path
    session: Path
    database: Path
    heartbeat: Path
    stop_flag: Path
    gui_lock: Path
    gui_activation: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        resolved = root.resolve()
        runtime = resolved / ".runtime"
        return cls(
            root=resolved,
            runtime=runtime,
            cache=resolved / ".cache",
            temp=resolved / ".tmp",
            logs=resolved / "logs",
            downloads=resolved / "downloads",
            config=resolved / "config.toml",
            credentials=runtime / "credentials.toml",
            session=runtime / "telegram.session",
            database=runtime / "state.sqlite3",
            heartbeat=runtime / "heartbeat.json",
            stop_flag=runtime / "stop.flag",
            gui_lock=runtime / "gui.lock",
            gui_activation=runtime / "gui-activate.request",
        )

    @property
    def writable_directories(self) -> tuple[Path, ...]:
        return self.runtime, self.cache, self.temp, self.logs, self.downloads

    def assert_within_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"路径位于项目目录之外: {resolved}")
        return resolved

    def ensure_directories(self) -> None:
        for path in self.writable_directories:
            self.assert_within_root(path).mkdir(parents=True, exist_ok=True)
