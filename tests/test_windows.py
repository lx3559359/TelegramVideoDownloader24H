from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader import windows
from tg_video_downloader.paths import ProjectPaths


def test_hidden_supervisor_does_not_use_detached_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    captured: dict[str, object] = {}
    process = SimpleNamespace(poll=lambda: None)

    def fake_popen(*_args, **kwargs):
        captured.update(kwargs)
        (paths.runtime / "supervisor.pid").write_text("1", encoding="ascii")
        return process

    monkeypatch.setattr(windows.subprocess, "Popen", fake_popen)

    assert windows.start_hidden_supervisor(tmp_path) is process
    flags = int(captured["creationflags"])
    assert flags & windows.subprocess.CREATE_NO_WINDOW
    assert not flags & windows.subprocess.DETACHED_PROCESS


def test_hidden_supervisor_reports_early_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(poll=lambda: 0)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(RuntimeError, match="退出码 0"):
        windows.start_hidden_supervisor(tmp_path)


def test_stale_heartbeat_does_not_hide_early_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    paths.heartbeat.write_text('{"status":"stopped"}', encoding="utf-8")
    process = SimpleNamespace(poll=lambda: 0)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(RuntimeError, match="退出码 0"):
        windows.start_hidden_supervisor(tmp_path)


def test_stale_supervisor_pid_does_not_hide_early_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    (paths.runtime / "supervisor.pid").write_text("999999", encoding="ascii")
    process = SimpleNamespace(poll=lambda: 0)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(RuntimeError, match="退出码 0"):
        windows.start_hidden_supervisor(tmp_path)


def test_hidden_supervisor_allows_slow_running_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(poll=lambda: None)
    times = iter((0.0, 2.0))
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(windows.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(windows.time, "sleep", lambda _seconds: None)

    assert windows.start_hidden_supervisor(tmp_path) is process


def test_single_instance_supports_context_specific_error_message(tmp_path: Path) -> None:
    lock_path = tmp_path / "gui.lock"

    with windows.SingleInstance(lock_path):
        with pytest.raises(RuntimeError, match="配置器已经在运行"):
            with windows.SingleInstance(
                lock_path,
                already_running_message="配置器已经在运行",
            ):
                pass
