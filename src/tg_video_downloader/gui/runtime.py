from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox
from typing import Any

from tg_video_downloader.gateway import TelethonGateway
from tg_video_downloader.gui.app import DownloaderApp
from tg_video_downloader.gui.controller import GuiController
from tg_video_downloader.gui.instance import GuiInstanceCoordinator
from tg_video_downloader.gui.tray import TrayActions, TrayController
from tg_video_downloader.paths import ProjectPaths


def run_gui(
    paths: ProjectPaths,
    *,
    root_factory: Callable[[], Any] = tk.Tk,
    controller_factory: Callable[[ProjectPaths], Any] | None = None,
    app_factory: Callable[[Any, Any], Any] = DownloaderApp,
    tray_factory: Callable[..., Any] = TrayController,
    instance_factory: Callable[[ProjectPaths], Any] = GuiInstanceCoordinator,
) -> None:
    instance = instance_factory(paths)
    if not instance.acquire_or_signal():
        return

    root: Any | None = None
    app: Any | None = None
    tray: Any | None = None
    activation_after: object | None = None
    closing = False
    tray_ready = False

    try:
        root = root_factory()
        root.title("Telegram 视频自动下载器")
        root.geometry("900x720")
        root.minsize(800, 620)
        make_controller = controller_factory or (
            lambda current_paths: GuiController(current_paths, TelethonGateway)
        )
        app = app_factory(root, make_controller(paths))

        def show_window() -> None:
            if closing:
                return
            root.deiconify()
            root.lift()
            root.focus_force()

        def report_error(error: Exception) -> None:
            show_window()
            messagebox.showerror("操作失败", app._safe_error(error), parent=root)

        def check_update() -> None:
            show_window()
            app.show_update_page()
            app._check_for_update()

        def quit_ui() -> None:
            nonlocal closing
            if closing:
                return
            closing = True
            if activation_after is not None:
                try:
                    root.after_cancel(activation_after)
                except tk.TclError:
                    pass
            if tray is not None:
                tray.stop()
            app.close()
            root.destroy()

        app.set_update_exit(quit_ui)

        actions = TrayActions(
            show_window=show_window,
            start_service=app._start_service,
            stop_service=app._stop_service,
            open_downloads=app.controller.open_downloads,
            open_logs=app.controller.open_logs,
            check_update=check_update,
            exit_ui=quit_ui,
            report_error=report_error,
        )
        tray = tray_factory(
            schedule=lambda callback: root.after(0, callback),
            actions=actions,
        )
        try:
            tray.start()
            tray_ready = True
            app.set_status_listener(tray.update)
            tray.update(app.controller.read_status())
        except Exception as error:
            messagebox.showerror(
                "托盘不可用",
                app._safe_error(error),
                parent=root,
            )

        def close_window() -> None:
            if tray_ready:
                root.withdraw()
            else:
                quit_ui()

        def poll_activation() -> None:
            nonlocal activation_after
            if closing:
                return
            if instance.activation_requested():
                show_window()
            activation_after = root.after(500, poll_activation)

        root.protocol("WM_DELETE_WINDOW", close_window)
        activation_after = root.after(500, poll_activation)
        root.mainloop()
    finally:
        if not closing:
            if tray is not None:
                tray.stop()
            if app is not None:
                app.close()
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass
        instance.close()
