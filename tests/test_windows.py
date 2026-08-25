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


def test_wait_for_downloader_stop_returns_after_lock_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    running = iter((True, False))
    sleeps: list[float] = []
    monkeypatch.setattr(
        windows,
        "downloader_is_running",
        lambda _paths: next(running),
    )

    windows.wait_for_downloader_stop(
        paths,
        monotonic=lambda: 0.0,
        sleep=sleeps.append,
    )

    assert sleeps == [0.1]


def test_wait_for_downloader_stop_reports_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    times = iter((0.0, 30.0))
    monkeypatch.setattr(
        windows,
        "downloader_is_running",
        lambda _paths: True,
    )

    with pytest.raises(TimeoutError, match="30 秒"):
        windows.wait_for_downloader_stop(
            paths,
            monotonic=lambda: next(times),
            sleep=lambda _seconds: None,
        )


def test_wait_for_downloader_stop_also_waits_for_supervisor_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    supervisor_running = iter((True, False))
    sleeps: list[float] = []
    monkeypatch.setattr(
        windows,
        "downloader_is_running",
        lambda _paths: False,
    )
    monkeypatch.setattr(
        windows,
        "supervisor_is_running",
        lambda _paths: next(supervisor_running),
    )

    windows.wait_for_downloader_stop(
        paths,
        monotonic=lambda: 0.0,
        sleep=sleeps.append,
    )

    assert sleeps == [0.1]


def test_detached_update_executor_has_no_console_or_pipes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    process = object()

    def fake_popen(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return process

    monkeypatch.setattr(windows.subprocess, "Popen", fake_popen)
    executor = tmp_path / "apply-update.ps1"
    request = tmp_path / ".runtime" / "update-request.json"

    assert windows.launch_update_executor(tmp_path, executor, request) is process
    flags = int(captured["creationflags"])
    assert flags & windows.subprocess.CREATE_NO_WINDOW
    assert flags & windows.DETACHED_PROCESS
    assert captured["stdin"] is windows.subprocess.DEVNULL
    assert captured["stdout"] is windows.subprocess.DEVNULL
    assert captured["stderr"] is windows.subprocess.DEVNULL
