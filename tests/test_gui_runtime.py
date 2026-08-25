from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_video_downloader.gui.runtime import run_gui
from tg_video_downloader.gui.tray import TrayActions
from tg_video_downloader.paths import ProjectPaths


class FakeRoot:
    def __init__(self) -> None:
        self.protocols = {}
        self.after_calls: list[tuple[int, object]] = []
        self.withdraw_calls = 0
        self.show_calls: list[str] = []
        self.destroy_calls = 0
        self.mainloop_action = lambda: None

    def title(self, _value: str) -> None:
        pass

    def geometry(self, _value: str) -> None:
        pass

    def minsize(self, _width: int, _height: int) -> None:
        pass

    def protocol(self, name: str, callback) -> None:
        self.protocols[name] = callback

    def withdraw(self) -> None:
        self.withdraw_calls += 1

    def deiconify(self) -> None:
        self.show_calls.append("deiconify")

    def lift(self) -> None:
        self.show_calls.append("lift")

    def focus_force(self) -> None:
        self.show_calls.append("focus")

    def after(self, delay: int, callback):
        self.after_calls.append((delay, callback))
        return f"after-{len(self.after_calls)}"

    def after_cancel(self, _identifier: str) -> None:
        pass

    def destroy(self) -> None:
        self.destroy_calls += 1

    def mainloop(self) -> None:
        self.mainloop_action()


class FakeInstance:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.closed = 0
        self.activate = False

    def acquire_or_signal(self) -> bool:
        return self.acquired

    def activation_requested(self) -> bool:
        result, self.activate = self.activate, False
        return result

    def close(self) -> None:
        self.closed += 1


class FakeApp:
    def __init__(self, root, controller) -> None:
        self.root = root
        self.controller = controller
        self.closed = 0
        self.listener = lambda _snapshot: None

    def set_status_listener(self, listener) -> None:
        self.listener = listener

    def _start_service(self) -> None:
        self.controller.start()

    def _stop_service(self) -> None:
        self.controller.stop()

    def _safe_error(self, error: Exception) -> str:
        return str(error)

    def close(self) -> None:
        self.closed += 1


class FakeTray:
    def __init__(self, *, schedule, actions: TrayActions) -> None:
        self.schedule = schedule
        self.actions = actions
        self.started = 0
        self.stopped = 0
        self.snapshots = []

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def update(self, snapshot) -> None:
        self.snapshots.append(snapshot)


def test_duplicate_launch_signals_existing_instance_without_creating_tk(
    tmp_path: Path,
) -> None:
    created: list[bool] = []
    instance = FakeInstance(acquired=False)

    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: created.append(True),
        instance_factory=lambda _paths: instance,
    )

    assert created == []
    assert instance.closed == 0


def test_close_hides_to_tray_and_tray_exit_does_not_stop_service(
    tmp_path: Path,
) -> None:
    root = FakeRoot()
    instance = FakeInstance()
    controller = SimpleNamespace(
        start=lambda: None,
        stop_calls=0,
        stop=lambda: setattr(controller, "stop_calls", controller.stop_calls + 1),
        open_downloads=lambda: None,
        open_logs=lambda: None,
        read_status=lambda: {"status": "running"},
    )
    captured = {}

    def app_factory(created_root, created_controller):
        app = FakeApp(created_root, created_controller)
        captured["app"] = app
        return app

    def tray_factory(**kwargs):
        tray = FakeTray(**kwargs)
        captured["tray"] = tray
        return tray

    def mainloop_action() -> None:
        root.protocols["WM_DELETE_WINDOW"]()
        captured["tray"].actions.exit_ui()
        captured["tray"].actions.exit_ui()
        captured["tray"].actions.show_window()

    root.mainloop_action = mainloop_action
    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: root,
        controller_factory=lambda _paths: controller,
        app_factory=app_factory,
        tray_factory=tray_factory,
        instance_factory=lambda _paths: instance,
    )

    assert root.withdraw_calls == 1
    assert root.destroy_calls == 1
    assert root.show_calls == []
    assert captured["app"].closed == 1
    assert captured["tray"].stopped == 1
    assert controller.stop_calls == 0
    assert instance.closed == 1


def test_activation_request_restores_hidden_window(tmp_path: Path) -> None:
    root = FakeRoot()
    instance = FakeInstance()
    instance.activate = True
    created_tray = None

    def mainloop_action() -> None:
        poll = next(callback for delay, callback in root.after_calls if delay == 500)
        poll()
        created_tray.actions.exit_ui()

    def tray_factory(**kwargs):
        nonlocal created_tray
        created_tray = FakeTray(**kwargs)
        return created_tray

    root.mainloop_action = mainloop_action
    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: root,
        controller_factory=lambda _paths: SimpleNamespace(
            start=lambda: None,
            stop=lambda: None,
            open_downloads=lambda: None,
            open_logs=lambda: None,
            read_status=lambda: {"status": "stopped"},
        ),
        app_factory=FakeApp,
        tray_factory=tray_factory,
        instance_factory=lambda _paths: instance,
    )

    assert root.show_calls == ["deiconify", "lift", "focus"]


def test_tray_start_failure_keeps_close_as_real_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    instance = FakeInstance()
    captured = {}
    monkeypatch.setattr(
        "tg_video_downloader.gui.runtime.messagebox.showerror",
        lambda *_args, **_kwargs: None,
    )

    class FailingTray(FakeTray):
        def start(self) -> None:
            raise RuntimeError("tray unavailable")

    def app_factory(created_root, controller):
        app = FakeApp(created_root, controller)
        captured["app"] = app
        return app

    def tray_factory(**kwargs):
        tray = FailingTray(**kwargs)
        captured["tray"] = tray
        return tray

    root.mainloop_action = lambda: root.protocols["WM_DELETE_WINDOW"]()
    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: root,
        controller_factory=lambda _paths: SimpleNamespace(
            start=lambda: None,
            stop=lambda: None,
            open_downloads=lambda: None,
            open_logs=lambda: None,
            read_status=lambda: {"status": "stopped"},
        ),
        app_factory=app_factory,
        tray_factory=tray_factory,
        instance_factory=lambda _paths: instance,
    )

    assert root.withdraw_calls == 0
    assert root.destroy_calls == 1
    assert captured["app"].closed == 1
    assert captured["tray"].stopped == 1
    assert instance.closed == 1


def test_tray_actions_route_to_existing_controller_methods(tmp_path: Path) -> None:
    root = FakeRoot()
    instance = FakeInstance()
    calls: list[str] = []
    controller = SimpleNamespace(
        start=lambda: calls.append("start"),
        stop=lambda: calls.append("stop"),
        open_downloads=lambda: calls.append("downloads"),
        open_logs=lambda: calls.append("logs"),
        read_status=lambda: {"status": "stopped"},
    )
    captured = {}

    def tray_factory(**kwargs):
        tray = FakeTray(**kwargs)
        captured["tray"] = tray
        return tray

    def mainloop_action() -> None:
        actions = captured["tray"].actions
        actions.start_service()
        actions.stop_service()
        actions.open_downloads()
        actions.open_logs()
        actions.exit_ui()

    root.mainloop_action = mainloop_action
    run_gui(
        ProjectPaths.from_root(tmp_path),
        root_factory=lambda: root,
        controller_factory=lambda _paths: controller,
        app_factory=FakeApp,
        tray_factory=tray_factory,
        instance_factory=lambda _paths: instance,
    )

    assert calls == ["start", "stop", "downloads", "logs"]
