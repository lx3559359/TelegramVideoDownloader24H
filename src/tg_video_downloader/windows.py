from __future__ import annotations

import ctypes
import os
import subprocess
import time
from pathlib import Path
from types import TracebackType

from tg_video_downloader.paths import ProjectPaths


SUPERVISOR_START_OBSERVE_SECONDS = 2.0
SUPERVISOR_START_POLL_SECONDS = 0.05


class SingleInstance:
    def __init__(
        self,
        lock_path: Path,
        *,
        already_running_message: str = "下载器已经在运行",
    ) -> None:
        self.lock_path = lock_path
        self.already_running_message = already_running_message
        self._handle = None

    def __enter__(self) -> "SingleInstance":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
            handle.write(str(os.getpid()).encode("ascii"))
            handle.truncate()
            handle.flush()
        except OSError as error:
            handle.close()
            raise RuntimeError(self.already_running_message) from error
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        self._handle.close()
        self._handle = None


class PreventIdleSleep:
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __enter__(self) -> "PreventIdleSleep":
        if os.name == "nt":
            result = ctypes.windll.kernel32.SetThreadExecutionState(
                self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED
            )
            if result == 0:
                raise OSError("无法阻止 Windows 自动空闲休眠")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)


def request_stop(paths: ProjectPaths) -> None:
    paths.runtime.mkdir(parents=True, exist_ok=True)
    paths.assert_within_root(paths.stop_flag).write_text(
        str(os.getpid()),
        encoding="ascii",
    )


def clear_stop(paths: ProjectPaths) -> None:
    paths.assert_within_root(paths.stop_flag).unlink(missing_ok=True)


def is_stop_requested(paths: ProjectPaths) -> bool:
    return paths.assert_within_root(paths.stop_flag).is_file()


def start_hidden_supervisor(project_root: Path) -> subprocess.Popen[bytes]:
    root = project_root.resolve()
    paths = ProjectPaths.from_root(root)
    script = root / "scripts" / "run-supervisor.ps1"
    supervisor_pid = paths.runtime / "supervisor.pid"
    ready_files = (
        supervisor_pid,
        paths.runtime / "downloader.lock",
        paths.heartbeat,
    )
    initial_states = {path: _file_state(path) for path in ready_files}
    supervisor_was_active = _file_is_locked(supervisor_pid)
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=root,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if supervisor_was_active:
        return process

    deadline = time.monotonic() + SUPERVISOR_START_OBSERVE_SECONDS
    while time.monotonic() < deadline:
        if any(
            (current := _file_state(path)) is not None
            and current != initial_states[path]
            for path in ready_files
        ):
            return process
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(f"后台启动器提前退出，退出码 {returncode}")
        time.sleep(SUPERVISOR_START_POLL_SECONDS)
    return process


def _file_state(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _file_is_locked(path: Path) -> bool:
    try:
        with path.open("rb"):
            pass
    except PermissionError:
        return True
    except OSError:
        return False
    return False
