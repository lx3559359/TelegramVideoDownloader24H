"""Windowed frozen entry point. Runtime data stays beside the installed EXE."""
import multiprocessing
import os
import sys
import ctypes

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Installer must never replace files while either GUI or service is active.
    ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
    installer_guard = ctypes.windll.kernel32.CreateMutexW(None, False, "TelegramVideoDownloader.Running")
    if not installer_guard:
        raise ctypes.WinError()
    # Windows GUI executables have no console handles; dependencies may print.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from tg_video_downloader.cli import main
    raise SystemExit(main())
