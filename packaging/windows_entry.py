"""Windowed frozen entry point. Runtime data stays beside the installed EXE."""
import multiprocessing
import os
import sys
import ctypes
from pathlib import Path


def installer_update_in_progress(root):
    from tg_video_downloader.windows import _file_is_locked
    return _file_is_locked(Path(root) / '.runtime' / 'installer-update.lock')

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Installer must never replace files while either GUI or service is active.
    ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
    installer_guard = ctypes.windll.kernel32.CreateMutexW(None, False, "TelegramVideoDownloader.Running")
    if not installer_guard:
        raise ctypes.WinError()
    if installer_update_in_progress(Path(sys.executable).resolve().parent):
        if len(sys.argv) == 1:
            ctypes.windll.user32.MessageBoxW(None, '正在安装更新，请等待工具自动重启。', '软件更新', 0x40)
        raise SystemExit(1)
    # Windows GUI executables have no console handles; dependencies may print.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from tg_video_downloader.cli import main
    raise SystemExit(main())
