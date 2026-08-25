from __future__ import annotations

import os
from uuid import uuid4

from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.windows import SingleInstance


class GuiInstanceCoordinator:
    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self._lock = SingleInstance(
            paths.assert_within_root(paths.gui_lock),
            already_running_message="配置器已经在运行",
        )
        self._active = False
        self._last_token = self._read_token()

    def acquire_or_signal(self) -> bool:
        try:
            self._lock.__enter__()
        except RuntimeError:
            self._write_token(uuid4().hex)
            return False
        self._active = True
        self._last_token = self._read_token()
        return True

    def activation_requested(self) -> bool:
        token = self._read_token()
        if not token or token == self._last_token:
            return False
        self._last_token = token
        return True

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        self._lock.__exit__(None, None, None)

    def _read_token(self) -> str | None:
        try:
            token = self.paths.gui_activation.read_text(encoding="ascii").strip()
        except (FileNotFoundError, UnicodeError):
            return None
        return token or None

    def _write_token(self, token: str) -> None:
        path = self.paths.assert_within_root(self.paths.gui_activation)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{token}.new")
        try:
            with temporary.open("w", encoding="ascii", newline="\n") as handle:
                handle.write(token + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
