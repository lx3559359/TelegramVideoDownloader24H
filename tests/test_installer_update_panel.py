import tkinter as tk
from types import SimpleNamespace
from threading import Event

import pytest

from tg_video_downloader.gui import installer_update_panel as panel_module
from tg_video_downloader.installer_update import InstallerRelease


@pytest.fixture
def panel(tmp_path, tk_root):
    root = tk_root
    controller = SimpleNamespace(login_active=False)
    value = panel_module.InstallerUpdatePanel(root, controller, on_exit=lambda: None,
        manager=SimpleNamespace(current_version='0.3.7'))
    yield value
    value.close(); value.destroy()


def test_initial_buttons(panel):
    assert panel.check_button.instate(['!disabled'])
    assert panel.install_button.instate(['disabled'])
    assert panel.cancel_button.instate(['disabled'])
    assert panel.source_choice.get() == '自动（镜像优先）'


def test_source_is_visible_and_locked_during_download(panel):
    panel.state = 'downloading'
    panel._buttons()
    assert str(panel.source_select.cget('state')) == 'disabled'
    panel.events.put(('source', '魔搭国内镜像', None))
    panel.after_cancel(panel.after_id)
    panel._poll()
    assert '魔搭国内镜像' in panel.source_status.get()
    panel._failed(ValueError('network'))
    assert str(panel.source_select.cget('state')) == 'readonly'


def test_check_result_enables_download(panel):
    panel.state = 'checking'
    release = InstallerRelease('0.3.8', 'url', 100, 'sha', '更新说明')
    panel._complete('check', release)
    assert panel.release == release
    assert panel.install_button.instate(['!disabled'])
    assert '0.3.8' in panel.status.get()


def test_error_restores_buttons(panel):
    panel.state = 'downloading'
    panel._failed(ValueError('bad hash'))
    assert panel.check_button.instate(['!disabled'])
    assert 'bad hash' in panel.status.get()


def test_duplicate_check_ignored(panel):
    panel.state = 'checking'
    panel.manager.check = lambda: pytest.fail('duplicate network call')
    panel.check()


def test_close_cancels_download(panel):
    panel.state = 'downloading'
    panel.close()
    assert panel.cancel_event.is_set()


def test_declined_install_does_not_stop_backend(panel, monkeypatch):
    panel.downloaded = SimpleNamespace(release=SimpleNamespace(version='0.3.8'))
    panel.state = 'ready'
    monkeypatch.setattr(panel_module.messagebox, 'askyesno', lambda *a, **k: False)
    panel.install()
    assert panel.state == 'ready'


def test_login_blocks_install(panel):
    panel.controller.login_active = True
    panel.downloaded = SimpleNamespace(release=SimpleNamespace(version='0.3.8'))
    panel.state = 'ready'
    panel.install()
    assert panel.state == 'ready'
    assert '登录' in panel.status.get()
