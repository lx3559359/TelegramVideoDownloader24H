import tkinter as tk
from tkinter import ttk

import pytest

from tg_video_downloader.gui import app as app_module
from tg_video_downloader.gui.controller import GuiController
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.models import Credentials


@pytest.fixture
def root(tk_root):
    tk_root.deiconify()
    tk_root.geometry("800x620")
    yield tk_root
    for child in tk_root.winfo_children():
        child.destroy()
    tk_root.withdraw()


def make_guide(root, state=None):
    assert hasattr(app_module, "UsageGuidePage"), "Permanent usage guide is not implemented"
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    visits = []
    current = state if state is not None else {}
    guide = app_module.UsageGuidePage(notebook, navigate=visits.append, read_state=lambda: current)
    root.update()
    return notebook, guide, visits, current


def test_guide_remains_available_after_leaving_and_refreshes(root):
    notebook, guide, visits, state = make_guide(root)
    other = ttk.Frame(notebook)
    notebook.add(other, text="账号")
    notebook.select(other)
    state.update(api_saved=True, account="登录成功", targets=2, status="running")
    notebook.select(guide)
    root.update()
    assert notebook.tab(guide, "text") == "使用指南"
    assert "API：已保存" in guide.progress_var.get()
    assert "账号：已登录" in guide.progress_var.get()
    assert "2 个" in guide.progress_var.get()
    assert visits == []


def test_navigation_only_calls_page_callback(root):
    _, guide, visits, _ = make_guide(root)
    for button in guide.navigation_buttons:
        button.invoke()
    assert set(visits) == {"账号", "授权", "群组/频道", "运行"}


def test_credentials_do_not_imply_login_or_license(root):
    _, guide, _, _ = make_guide(root, {"api_saved": True})
    assert "账号：尚未验证" in guide.progress_var.get()
    assert "授权：尚未验证" in guide.progress_var.get()


def test_sections_cover_rebinding_and_fit_small_window(root):
    _, guide, _, _ = make_guide(root)
    assert len(guide.text_widgets) == 4
    all_text = "\n".join(widget.get("1.0", "end") for widget in guide.text_widgets)
    for phrase in ("退出当前账号", "API Hash", "管理员", "不会自动", "托盘", "旧任务", "停止后台"):
        assert phrase in all_text
    for widget in guide.text_widgets:
        assert str(widget.cget("state")) == "disabled"
        assert widget.cget("yscrollcommand")
    assert guide.winfo_width() <= root.winfo_width()


@pytest.mark.parametrize("saved", [False, True])
def test_real_app_startup_and_safe_navigation(root, tmp_path, monkeypatch, saved):
    def no_network(*args, **kwargs):
        raise AssertionError("Guide must not connect to Telegram")
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    controller = GuiController(paths, no_network)
    if saved:
        controller.save_credentials(Credentials(12345, "a" * 32, ""))
    monkeypatch.setattr(app_module.DownloaderApp, "_check_saved_session", lambda self: None)
    app = app_module.DownloaderApp(root, controller)
    try:
        root.update()
        selected = app.account_page if saved else app.guide_page
        assert app.notebook.select() == str(selected)
        for target, page in (("账号", app.account_page), ("授权", app.license_page),
                             ("群组/频道", app.groups_page), ("运行", app.run_page)):
            app._navigate_guide(target)
            assert app.notebook.select() == str(page)
        app.notebook.select(app.guide_page)
        root.update()
        assert "账号：尚未验证" in app.guide_page.progress_var.get()
        app.account_status_var.set("登录成功")
        app._refresh_status()
        assert "账号：已登录" in app.guide_page.progress_var.get()
        app.account_status_var.set("已退出当前账号")
        app._refresh_status()
        assert "账号：尚未验证" in app.guide_page.progress_var.get()
    finally:
        app.close()
        app.destroy()
