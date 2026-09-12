import sys
from pathlib import Path
from types import SimpleNamespace

from tg_video_downloader.cli import main
from tg_video_downloader.diagnostics import Doctor
from tg_video_downloader.paths import ProjectPaths


def test_frozen_double_click_uses_executable_directory(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "TelegramVideoDownloader.exe"))
    monkeypatch.setattr("tg_video_downloader.gui.runtime.run_gui", lambda paths: seen.append(paths.root))
    assert main([]) == 0
    assert seen == [tmp_path]


def test_frozen_update_diagnostics_do_not_need_git(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    check = Doctor(ProjectPaths.from_root(tmp_path), None)._check_update_support()
    assert check.status == "pass"
    assert "安装包" in check.message


def test_frozen_update_button_opens_official_site(monkeypatch):
    from tg_video_downloader.gui.app import DownloaderApp
    seen = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("webbrowser.open", lambda url: seen.append(url))
    stub = SimpleNamespace(update_status_var=SimpleNamespace(set=lambda value: None))
    DownloaderApp._check_for_update(stub)
    assert seen == ["https://www.cqtcshequ.com/#download"]


def test_supervisor_has_frozen_launch_without_bootstrap():
    script = (Path(__file__).resolve().parents[1] / "scripts/run-supervisor.ps1").read_text(encoding="utf-8-sig")
    assert 'TelegramVideoDownloader.exe' in script
    assert '$LaunchArguments = @("service")' in script
    assert '-ArgumentList $LaunchArguments' in script


def test_installer_guard_covers_gui_service_and_supervisor():
    root = Path(__file__).resolve().parents[1]
    for relative in ("packaging/windows_entry.py", "scripts/run-supervisor.ps1"):
        assert "TelegramVideoDownloader.Running" in (root / relative).read_text(encoding="utf-8-sig")
