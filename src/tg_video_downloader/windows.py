from __future__ import annotations

import ctypes
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from time import monotonic as monotonic_clock
from types import TracebackType

from tg_video_downloader.paths import ProjectPaths


SUPERVISOR_START_OBSERVE_SECONDS = 2.0
SUPERVISOR_START_POLL_SECONDS = 0.05
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
DETACHED_PROCESS = subprocess.DETACHED_PROCESS


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


def downloader_is_running(paths: ProjectPaths) -> bool:
    return _file_is_locked(paths.runtime / "downloader.lock")


def supervisor_is_running(paths: ProjectPaths) -> bool:
    return _file_is_locked(paths.runtime / "supervisor.pid")


def wait_for_downloader_stop(
    paths: ProjectPaths,
    timeout_seconds: float = 30.0,
    *,
    monotonic: Callable[[], float] = monotonic_clock,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout_seconds
    while downloader_is_running(paths) or supervisor_is_running(paths):
        if monotonic() >= deadline:
            raise TimeoutError("后台下载器未在 30 秒内停止")
        sleep(0.1)


def launch_update_executor(
    project_root: Path,
    executor: Path,
    request_path: Path,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        (
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(executor),
            "-ProjectRoot",
            str(project_root),
            "-RequestPath",
            str(request_path),
        ),
        cwd=project_root,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
    mode = "r+b" if os.name == "nt" else "rb"
    try:
        handle = path.open(mode)
    except PermissionError:
        return True
    except OSError:
        return False

    with handle:
        if os.name != "nt":
            return False

        import msvcrt

        acquired = False
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            acquired = True
        except OSError:
            return True
        finally:
            if acquired:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    return False
