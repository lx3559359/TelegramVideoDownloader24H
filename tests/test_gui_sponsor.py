import asyncio
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

from tg_video_downloader.gui.sponsor_panel import VisibleRefresh, SponsorPanel
from tg_video_downloader.gui.app import DownloaderApp


class Scheduler:
    def __init__(self):
        self.pending = {}
        self.serial = 0

    def after(self, delay, callback):
        assert delay == 30_000
        self.serial += 1
        self.pending[self.serial] = callback
        return self.serial

    def after_cancel(self, token):
        self.pending.pop(token, None)


def test_polling_only_when_visible_and_registered():
    timer = Scheduler()
    calls = []
    poller = VisibleRefresh(timer, lambda: calls.append(True))
    poller.set_visible(True)
    assert timer.pending == {}
    poller.set_ready()
    assert len(timer.pending) == 1
    callback = timer.pending.pop(next(iter(timer.pending)))
    callback()
    assert calls == [True]
    assert len(timer.pending) == 1
    poller.set_visible(False)
    assert not timer.pending
    poller.set_visible(True)
    assert len(timer.pending) == 1
    poller.close()
    assert not timer.pending
    poller.set_visible(True)
    assert not timer.pending


def test_panel_has_manual_verification_and_copy(tk_root):
    queued = []
    def run(operation, button, success, error):
        queued.append(operation)
        operation.close()
    panel = SponsorPanel(tk_root, run_async=run, refresh_license=lambda: None)
    try:
        assert "管理员核实" in panel.instructions.cget("text")
        assert "暂未配置" in panel.details_var.get()
        panel.short_var.set("ABCDE23456")
        panel.copy_device()
        assert tk_root.clipboard_get() == "ABCDE23456"
    finally:
        panel.close()
        panel.destroy()


def test_panel_duplicate_reload_does_not_start_another_request(tk_root):
    queued = []
    def run(operation, button, success, error):
        queued.append(operation)
        operation.close()
    panel = SponsorPanel(tk_root, run_async=run, refresh_license=lambda: None)
    try:
        panel.license_refreshed("a" * 64)
        panel.license_refreshed("a" * 64)
        assert len(queued) == 1
        assert panel._device == "a" * 64
    finally:
        panel.close()
        panel.destroy()


class Variable:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


def license_app():
    app = object.__new__(DownloaderApp)
    app.activation_code_var = Variable()
    app.license_status_var = Variable("月卡｜可使用｜上次验证")
    app.license_device_var = Variable()
    app.license_refresh_button = SimpleNamespace(state=lambda *_: None)
    app.license_activate_button = SimpleNamespace(state=lambda *_: None)
    app.controller = SimpleNamespace(license_gate=SimpleNamespace(refresh=lambda code: code))
    app._license_busy = False
    return app


def test_license_refresh_failure_preserves_display_and_releases_busy():
    app = license_app()
    app._run_async = lambda operation, button, success, error: error(ValueError("离线"))
    app._refresh_license(False)
    assert "月卡｜可使用｜上次验证" in app.license_status_var.get()
    assert "离线" in app.license_status_var.get()
    assert app._license_busy is False


def test_license_refresh_does_not_overlap_and_sends_device_to_panel():
    app = license_app()
    calls = []
    devices = []
    app.sponsor_panel = SimpleNamespace(license_refreshed=devices.append)
    app._run_async = lambda operation, button, success, error: calls.append(success)
    app._refresh_license(False)
    app._refresh_license(False)
    assert len(calls) == 1
    calls[0](SimpleNamespace(device="a" * 64, plan="permanent", allowed=True, expires_at=0))
    assert devices == ["a" * 64]
    assert app._license_busy is False


def test_real_tab_hide_and_window_withdraw_cancel_timer(tk_root):
    tk_root.deiconify()
    book = ttk.Notebook(tk_root)
    book.pack(fill="both", expand=True)
    page = ttk.Frame(book)
    other = ttk.Frame(book)
    book.add(page, text="授权")
    book.add(other, text="其他")
    panel = SponsorPanel(page, run_async=lambda *args: None, refresh_license=lambda: None)
    panel.pack()
    try:
        tk_root.update()
        panel._poller.set_ready()
        assert panel._poller.token is not None
        book.select(other)
        tk_root.update()
        assert panel._poller.token is None
        book.select(page)
        tk_root.update()
        assert panel._poller.token is not None
        tk_root.withdraw()
        tk_root.update()
        assert panel._poller.token is None
    finally:
        panel.close()
        book.destroy()


def test_panel_fetch_failure_keeps_device_reference(tk_root, monkeypatch):
    from tg_video_downloader.gui import sponsor_panel as module
    from tg_video_downloader.sponsorship import SponsorError
    monkeypatch.setattr(module, "fetch_device_info", lambda device: "ABCDE23456")
    def unavailable():
        raise SponsorError("离线")
    monkeypatch.setattr(module, "fetch_sponsor", unavailable)
    def run(operation, button, success, error):
        success(asyncio.run(operation))
    panel = SponsorPanel(tk_root, run_async=run, refresh_license=lambda: None)
    try:
        panel.license_refreshed("a" * 64)
        assert panel.short_var.get() == "ABCDE23456"
        assert "原激活码入口" in panel.details_var.get()
        assert panel._photo is None
        assert panel._busy is False
    finally:
        panel.close()
        panel.destroy()


def test_license_page_sponsor_controls_fit_default_window(tk_root):
    tk_root.deiconify()
    tk_root.geometry("900x720")
    app = DownloaderApp.__new__(DownloaderApp)
    ttk.Frame.__init__(app, tk_root, padding=12)
    app.pack(fill="both", expand=True)
    try:
        app.notebook = ttk.Notebook(app)
        app.notebook.pack(fill="both", expand=True)
        app._build_license_page()
        tk_root.update()
        panel = app.sponsor_panel
        bottom = panel.winfo_rooty() + panel.winfo_height()
        assert bottom <= tk_root.winfo_rooty() + tk_root.winfo_height()
    finally:
        app.sponsor_panel.close()
        app.destroy()
        tk_root.withdraw()
