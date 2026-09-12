import hashlib
import io
import json
from threading import Event

import pytest

from tg_video_downloader import installer_update as update
from tg_video_downloader.paths import ProjectPaths


PAYLOAD = b'MZ' + b'test installer' * 300


def manifest(**changes):
    result = dict(schema=1, version='0.3.8',
                  url='https://www.cqtcshequ.com/downloads/TelegramVideoDownloader-v0.3.8-Windows-x64-Setup.exe',
                  size=len(PAYLOAD), sha256=hashlib.sha256(PAYLOAD).hexdigest(), notes='在线更新与新图标')
    result.update(changes)
    return result


class Response(io.BytesIO):
    status = 200


def manager(tmp_path, body=PAYLOAD, **kwargs):
    return update.InstallerUpdateManager(ProjectPaths.from_root(tmp_path), current_version='0.3.7',
                                         opener=lambda *a, **k: Response(body), **kwargs)


def test_manifest_accepts_stable_release():
    value = update.parse_manifest(manifest())
    assert value.version == '0.3.8'
    assert value.notes == '在线更新与新图标'


@pytest.mark.parametrize('changes', [dict(schema=2), dict(size=True), dict(size=0), dict(size=600*1024**2),
    dict(version='0.3.8;calc'), dict(version='0.3.8-beta'), dict(sha256='bad'), dict(notes=['bad']),
    dict(url='http://www.cqtcshequ.com/downloads/x.exe'),
    dict(url='https://evil.com/downloads/TelegramVideoDownloader-v0.3.8-Windows-x64-Setup.exe'),
    dict(url='https://www.cqtcshequ.com@evil.com/x.exe'), dict(url=manifest()['url']+'?x=1')])
def test_manifest_rejects_unsafe_values(changes):
    with pytest.raises(ValueError):
        update.parse_manifest(manifest(**changes))


def test_check_only_returns_newer_version(tmp_path):
    m = manager(tmp_path, json.dumps(manifest()).encode())
    assert m.check().version == '0.3.8'
    m.current_version = '0.3.8'
    assert m.check() is None
    m.current_version = '0.4.0'
    assert m.check() is None


def test_check_rejects_oversized_manifest(tmp_path):
    with pytest.raises(ValueError):
        manager(tmp_path, b' ' * 65537).check()


def test_download_verified_then_atomic_promote(tmp_path):
    progress = []
    item = manager(tmp_path).download(update.parse_manifest(manifest()), Event(), lambda n,t: progress.append((n,t)))
    assert item.path.read_bytes() == PAYLOAD
    assert progress[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert not list(item.path.parent.glob('*.part'))


@pytest.mark.parametrize('body', [PAYLOAD[:-1], PAYLOAD+b'extra', b'X'*len(PAYLOAD)])
def test_download_rejects_corruption(tmp_path, body):
    with pytest.raises(ValueError):
        manager(tmp_path, body).download(update.parse_manifest(manifest()), Event(), lambda *a: None)
    assert not list(tmp_path.rglob('*.exe'))
    assert not list(tmp_path.rglob('*.part'))


def test_cancel_before_download_preserves_running_data(tmp_path):
    stop = tmp_path/'.runtime/stop.flag'
    cancel = Event(); cancel.set()
    with pytest.raises(update.DownloadCancelled):
        manager(tmp_path).download(update.parse_manifest(manifest()), cancel, lambda *a: None)
    assert not stop.exists()
    assert not list(tmp_path.rglob('*.exe'))


def test_cancel_during_download_removes_partial(tmp_path):
    cancel = Event()
    with pytest.raises(update.DownloadCancelled):
        manager(tmp_path).download(update.parse_manifest(manifest()), cancel, lambda *a: cancel.set())
    assert not list(tmp_path.rglob('*.part'))
    assert not list(tmp_path.rglob('*.exe'))


def test_validate_prepared_rechecks_hash(tmp_path):
    m = manager(tmp_path)
    item = m.download(update.parse_manifest(manifest()), Event(), lambda *a: None)
    item.path.write_bytes(b'MZtampered')
    with pytest.raises(ValueError): m.validate_prepared(item)


def test_reject_redirect():
    with pytest.raises(ValueError):
        update.NoRedirect().redirect_request(None, None, 302, 'found', {}, 'https://evil.com/a')


def test_helper_shipped_as_package_data():
    assert update.helper_source().is_file()


def test_helper_launch_uses_working_hidden_powershell_flags(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(update.subprocess, 'Popen', lambda *args, **kwargs: calls.append(kwargs))
    update.launch_helper(tmp_path/'helper.ps1', tmp_path/'request.json')
    assert calls[0]['creationflags'] == update.subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize('value', [None, [], 'text', 42])
def test_malformed_result_rejected_as_value_error(tmp_path, value):
    paths=ProjectPaths.from_root(tmp_path); paths.runtime.mkdir()
    (paths.runtime/'installer-update-result.json').write_text(json.dumps(value))
    with pytest.raises(ValueError): update.consume_installer_result(paths)


def test_prepare_install_waits_for_helper_ready(tmp_path):
    launches = []
    def launch(script, request):
        data = json.loads(request.read_text(encoding='utf-8'))
        assert data['sha256'] == manifest()['sha256']
        assert data['restore_service'] is True
        assert data['root'] == str(tmp_path)
        request.with_suffix('.ready').write_text(data['token'])
        launches.append(script)
        return type('Process', (), {'poll': lambda self: None})()
    m = manager(tmp_path, launcher=launch)
    (tmp_path/'TelegramVideoDownloader.exe').write_bytes(b'MZ')
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/run-supervisor.ps1').write_text('# supervisor')
    item = m.download(update.parse_manifest(manifest()), Event(), lambda *a: None)
    m.prepare_install(item, True)
    assert launches[0].is_file()
    assert tmp_path in launches[0].parents


def test_helper_timeout_waits_for_exit_before_return(tmp_path, monkeypatch):
    calls=[]
    class Process:
        def poll(self): return None
        def wait(self, timeout): calls.append('exited'); return 1
    m=manager(tmp_path,launcher=lambda *a: Process())
    (tmp_path/'TelegramVideoDownloader.exe').write_bytes(b'MZ')
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/run-supervisor.ps1').write_text('# supervisor')
    item=m.download(update.parse_manifest(manifest()),Event(),lambda *a:None)
    times=iter((0,16))
    monkeypatch.setattr(update.time,'monotonic',lambda:next(times))
    with pytest.raises(TimeoutError): m.prepare_install(item,True)
    assert calls==['exited']
    assert (item.path.parent/'request.cancel').exists()
