import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'src/tg_video_downloader/assets/install-update.ps1'
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows helper')


def test_helper_has_valid_windows_powershell_syntax():
    command = "$tokens=$null; $errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('" + str(SOURCE).replace("'", "''") + "',[ref]$tokens,[ref]$errors); if($errors.Count){$errors | Out-String | Write-Error; exit 1}"
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], check=True, capture_output=True)


def fixture_request(tmp_path):
    root = tmp_path / 'installed app'
    stage = root / '.cache/installer-updates' / str(uuid.uuid4())
    stage.mkdir(parents=True)
    assert SOURCE.is_file(), 'installer helper must be shipped'
    shutil.copyfile(SOURCE, stage / SOURCE.name)
    installer = stage / 'TelegramVideoDownloader-v0.3.8-Windows-x64-Setup.exe'
    installer.write_bytes(b'not an executable')
    request = dict(root=str(root), installer=str(installer), version='0.3.8',
                   size=installer.stat().st_size, sha256=hashlib.sha256(installer.read_bytes()).hexdigest(),
                   restore_service=False, parent_pid=os.getpid(), token=uuid.uuid4().hex)
    return root, stage, request


def launch(stage, request):
    path = stage / 'request.json'
    path.write_text(json.dumps(request), encoding='utf-8')
    return subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                             '-File', str(stage / SOURCE.name), '-RequestPath', str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_helper_rejects_bad_hash_before_ready(tmp_path):
    root, stage, request = fixture_request(tmp_path)
    request['sha256'] = '0' * 64
    process = launch(stage, request)
    process.communicate(timeout=15)
    assert process.returncode != 0
    assert not (stage / 'request.ready').exists()
    assert 'hash' in json.loads((root / '.runtime/installer-update-result.json').read_text(encoding='utf-8-sig'))['message'].lower()


def test_helper_waits_for_parent_and_honors_cancel(tmp_path):
    root, stage, request = fixture_request(tmp_path)
    process = launch(stage, request)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not (stage / 'request.ready').exists() and process.poll() is None:
            time.sleep(.1)
        assert (stage / 'request.ready').exists(), process.communicate(timeout=2)
        time.sleep(.5)
        assert process.poll() is None
        (stage / 'request.cancel').touch()
        process.communicate(timeout=5)
        assert process.returncode != 0
        result = json.loads((root / '.runtime/installer-update-result.json').read_text(encoding='utf-8-sig'))
        assert result['status'] == 'failed'
        assert 'cancel' in result['message'].lower()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_helper_rejects_installer_outside_stage(tmp_path):
    root, stage, request = fixture_request(tmp_path)
    request['installer'] = str(tmp_path / 'outside.exe')
    process = launch(stage, request)
    process.communicate(timeout=15)
    assert process.returncode != 0
    assert not (stage / 'request.ready').exists()


def test_helper_does_not_overwrite_result_when_update_lock_is_held(tmp_path):
    import msvcrt

    root, stage, request = fixture_request(tmp_path)
    runtime = root / '.runtime'
    runtime.mkdir()
    result = runtime / 'installer-update-result.json'
    result.write_text('existing updater result', encoding='utf-8')
    with (runtime / 'installer-update.lock').open('w+b') as lock:
        lock.write(b'1')
        lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        process = launch(stage, request)
        process.communicate(timeout=15)
        assert process.returncode != 0
        assert not (stage / 'request.ready').exists()
        assert result.read_text(encoding='utf-8') == 'existing updater result'


@pytest.mark.parametrize('lock_name', ['gui.lock', 'downloader.lock', 'supervisor.pid'])
def test_helper_waits_for_runtime_lock_after_parent_exits(tmp_path, lock_name):
    import msvcrt

    root, stage, request = fixture_request(tmp_path)
    request['parent_pid'] = 2147483647
    runtime = root / '.runtime'
    runtime.mkdir()
    with (runtime / lock_name).open('w+b') as lock:
        lock.write(b'1')
        lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        process = launch(stage, request)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not (stage / 'request.ready').exists() and process.poll() is None:
                time.sleep(.1)
            assert (stage / 'request.ready').exists()
            time.sleep(.5)
            assert process.poll() is None
            (stage / 'request.cancel').touch()
            process.communicate(timeout=5)
            assert process.returncode != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


@pytest.mark.parametrize(('field', 'value'), [('size', True), ('size', 17.0), ('size', 536870913),
                                             ('parent_pid', True), ('parent_pid', 123.0),
                                             ('parent_pid', 2147483648), ('version', '00.3.8')])
def test_helper_rejects_invalid_scalar_fields(tmp_path, field, value):
    root, stage, request = fixture_request(tmp_path)
    request[field] = value
    process = launch(stage, request)
    try:
        process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        (stage / 'request.cancel').touch()
        process.communicate(timeout=5)
    assert not (stage / 'request.ready').exists()


@pytest.fixture
def stub_exe(tmp_path):
    source = tmp_path / 'stub.cs'
    source.write_text('''using System; using System.IO;
class Stub { static int Main(string[] args) {
 if(args.Length == 0) File.Copy(Path.Combine(Environment.CurrentDirectory,
 ".runtime/installer-update-result.json"), Path.Combine(Environment.CurrentDirectory,"gui-seen.json"), true);
 return 0; } }''', encoding='utf-8')
    exe = tmp_path / 'stub.exe'
    compiler = Path(os.environ['SystemRoot']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/out:' + str(exe), str(source)], check=True, capture_output=True)
    return exe


@pytest.mark.parametrize('backend_failure', [False, 'missing', 'exits'])
def test_helper_persists_restart_failure_after_successful_install(tmp_path, stub_exe, backend_failure):
    root, stage, request = fixture_request(tmp_path)
    installer = Path(request['installer'])
    shutil.copyfile(stub_exe, installer)
    request.update(size=installer.stat().st_size, sha256=hashlib.sha256(installer.read_bytes()).hexdigest(),
                   parent_pid=2147483647, restore_service=backend_failure)
    if backend_failure:
        shutil.copyfile(stub_exe, root / 'TelegramVideoDownloader.exe')
        request['restore_service'] = True
        if backend_failure == 'exits':
            (root / 'scripts').mkdir()
            (root / 'scripts/run-supervisor.ps1').write_text('exit 7', encoding='ascii')
    process = launch(stage, request)
    process.communicate(timeout=20)
    result = json.loads((root / '.runtime/installer-update-result.json').read_text(encoding='utf-8-sig'))
    assert result['status'] == 'failed'
    assert 'installed' in result['message'].lower()
    assert ('background' if backend_failure else 'gui') in result['message'].lower()
    if backend_failure:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (root / 'gui-seen.json').exists():
            time.sleep(.1)
        assert json.loads((root / 'gui-seen.json').read_text(encoding='utf-8-sig')) == result
