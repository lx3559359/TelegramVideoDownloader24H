"""Exercise a disposable installed copy, never the developer's live configuration."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time

from PIL import Image

root = Path(__file__).resolve().parents[1]
installed = (root / ".tmp/installer-smoke").resolve()
exe = installed / "TelegramVideoDownloader.exe"
assert installed.is_relative_to(root / ".tmp") and exe.is_file()
env = os.environ.copy()
system = Path(os.environ["SystemRoot"])
env["PATH"] = os.pathsep.join(str(p) for p in (system / "System32", system, system / "System32/WindowsPowerShell/v1.0"))
for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
    env.pop(key, None)
doctor = subprocess.run([str(exe), "doctor"], env=env, cwd=system, timeout=30)
report = json.loads(max((installed / "logs/diagnostics").glob("*.json"), key=lambda p:p.stat().st_mtime).read_text(encoding="utf-8"))
checks = {item["key"]:item for item in report["checks"]}
for key in ("python", "dependencies", "qr_code", "tray_icon", "update_support", "project_paths"):
    assert checks[key]["status"] == "pass", checks[key]
print("Bundled runtime/dependencies/QR/tray/update/path checks passed with no Python on PATH.", flush=True)

gui = subprocess.Popen([str(exe)], env=env, cwd=system)
try:
    user32 = ctypes.windll.user32
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def enum_window(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == gui.pid and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, len(title))
            windows.append((hwnd, title.value))
        return True
    for _ in range(100):
        windows.clear()
        user32.EnumWindows(enum_window, 0)
        if any(title == "Telegram 视频自动下载器" for _, title in windows):
            break
        assert gui.poll() is None, "GUI exited before opening"
        time.sleep(0.1)
    hwnd = next(hwnd for hwnd,title in windows if title == "Telegram 视频自动下载器")
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    # Capture only our HWND, even if another task/user brings a browser forward.
    width, height = rect.right - rect.left, rect.bottom - rect.top
    gdi = ctypes.windll.gdi32
    user32.GetWindowDC.restype = wintypes.HDC
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.SelectObject.restype = wintypes.HGDIOBJ
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    dc = user32.GetWindowDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    assert user32.PrintWindow(hwnd, memory, 2), "Could not capture test window"
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi.GetBitmapBits.argtypes = [wintypes.HBITMAP, wintypes.LONG, ctypes.c_void_p]
    assert gdi.GetBitmapBits(bitmap, len(buffer), buffer) == len(buffer)
    Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1).save(root / ".tmp/installer-smoke-ui.png")
    gdi.SelectObject(memory, previous)
    gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi.DeleteObject(bitmap)
    gdi.DeleteDC(memory)
    user32.ReleaseDC(hwnd, dc)
    assert gui.poll() is None
    print("Double-click GUI startup passed; screenshot captured.", flush=True)
    setup = root / ".tmp/public-releases/TelegramVideoDownloader-v0.3.7-Windows-x64-Setup.exe"
    guard = subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/DIR=" + str(installed)], env=env, timeout=30)
    assert guard.returncode != 0, "Installer must refuse replacement while GUI is active"
    print("Installer correctly refused upgrade while the test GUI was running.", flush=True)
finally:
    # This process was created solely by this smoke test and has no user data.
    if gui.poll() is None:
        gui.terminate()
    gui.wait(timeout=10)

stop = installed / ".runtime/stop.flag"
stop.unlink(missing_ok=True)
ps = system / "System32/WindowsPowerShell/v1.0/powershell.exe"
supervisor = subprocess.Popen([str(ps), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installed / "scripts/run-supervisor.ps1")], env=env, cwd=system, creationflags=subprocess.CREATE_NO_WINDOW)
started = time.time()
try:
    # With no credentials, the EXE service should write a startup error then retry;
    # this exercises the real supervisor without connecting to a Telegram account.
    for _ in range(150):
        if any(p.stat().st_mtime >= started for p in (installed / "logs").glob("*.log")):
            break
        assert supervisor.poll() is None, "Supervisor exited unexpectedly"
        time.sleep(0.1)
    assert (installed / ".runtime/supervisor.pid").exists()
    assert any(p.stat().st_mtime >= started for p in (installed / "logs").glob("*.log")), "Packaged service did not create a fresh log"
    assert not (installed / ".venv").exists(), "Supervisor tried to bootstrap Python"
finally:
    stop.write_text("smoke-stop", encoding="ascii")
    supervisor.wait(timeout=30)
assert supervisor.returncode == 0
assert not (installed / ".runtime/supervisor.pid").exists()
print("Packaged supervisor/service launch and graceful stop passed; no Python bootstrap.")
