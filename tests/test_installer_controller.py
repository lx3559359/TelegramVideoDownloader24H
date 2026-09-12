from threading import Lock
from types import SimpleNamespace
import pytest
from tg_video_downloader.gui.controller import GuiController
from tg_video_downloader.paths import ProjectPaths


def controller(tmp_path, monkeypatch, running=True, fail_stop=False):
    calls=[]
    c=GuiController(ProjectPaths.from_root(tmp_path), lambda *a: None)
    c.process_control=SimpleNamespace(request_stop=lambda p:calls.append('stop'),
        clear_stop=lambda p:calls.append('clear'), start=lambda p:calls.append('start'))
    monkeypatch.setattr('tg_video_downloader.gui.controller.downloader_is_running',lambda p:running)
    monkeypatch.setattr('tg_video_downloader.gui.controller.supervisor_is_running',lambda p:False)
    def wait(p):
        calls.append('wait')
        if fail_stop: raise TimeoutError('stop failed')
    monkeypatch.setattr('tg_video_downloader.gui.controller.wait_for_downloader_stop',wait)
    m=SimpleNamespace(validate_prepared=lambda item:calls.append('validate'),
        validate_install_environment=lambda:calls.append('environment'),
        prepare_install=lambda item,restore:calls.append(('install',restore)))
    return c,m,calls


def test_install_validates_before_stopping(tmp_path,monkeypatch):
    c,m,calls=controller(tmp_path,monkeypatch)
    c.prepare_installer_install(m,object())
    assert calls==['validate','environment','stop','wait',('install',True)]


def test_stopped_backend_is_not_restarted(tmp_path,monkeypatch):
    c,m,calls=controller(tmp_path,monkeypatch,running=False)
    c.prepare_installer_install(m,object())
    assert calls[-1]==('install',False)
    assert 'stop' not in calls


def test_stop_timeout_does_not_launch_installer(tmp_path,monkeypatch):
    c,m,calls=controller(tmp_path,monkeypatch,fail_stop=True)
    with pytest.raises(TimeoutError): c.prepare_installer_install(m,object())
    assert not any(isinstance(x,tuple) for x in calls)
    assert 'clear' in calls
    assert c.installer_update_active is False


def test_duplicate_install_rejected(tmp_path,monkeypatch):
    c,m,calls=controller(tmp_path,monkeypatch)
    c.installer_update_active=True
    with pytest.raises(ValueError): c.prepare_installer_install(m,object())
    assert not calls


def test_start_blocked_while_installing(tmp_path,monkeypatch):
    c,m,calls=controller(tmp_path,monkeypatch)
    c.installer_update_active=True
    with pytest.raises(ValueError, match='更新'): c.start()


@pytest.mark.asyncio
async def test_connecting_login_blocks_install(tmp_path, monkeypatch):
    import asyncio
    from tg_video_downloader.models import Credentials
    started=asyncio.Event(); finish=asyncio.Event()
    class Gateway:
        async def connect(self):
            started.set(); await finish.wait()
        async def is_authorized(self): return True
        async def disconnect(self): pass
    c,m,calls=controller(tmp_path,monkeypatch)
    c.gateway_factory=lambda *a: Gateway()
    task=asyncio.create_task(c.start_qr_login(Credentials(12345,'a'*32)))
    try:
        await asyncio.wait_for(started.wait(),2)
        assert c.login_active
        with pytest.raises(ValueError,match='登录'): c.prepare_installer_install(m,object())
        assert not calls
    finally:
        finish.set(); await task
