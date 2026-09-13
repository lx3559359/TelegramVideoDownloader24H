import subprocess
from types import SimpleNamespace
from pathlib import Path
import pytest
from tg_video_downloader import licensing as module

PAYLOAD = b'{"uuid":"12345678-1234-1234-1234-1234567890ab","bios":"bios","query_failed":false}'

def test_clr_failure_uses_installed_powershell7(monkeypatch, tmp_path):
    alias = tmp_path / 'Microsoft/WindowsApps/pwsh.exe'
    alias.parent.mkdir(parents=True); alias.touch()
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.delenv('ProgramFiles', raising=False)
    calls = []
    def run(args, **kwargs):
        calls.append(args[0])
        if args[0] != str(alias):
            raise subprocess.CalledProcessError(4294901760, args, stderr=b'private')
        assert kwargs['timeout'] == 15
        return SimpleNamespace(stdout=PAYLOAD)
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.device_code() == module.fingerprint('12345678-1234-1234-1234-1234567890ab', 'bios')
    assert calls[-1] == str(alias)

def test_primary_success_does_not_launch_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(module.subprocess, 'run', lambda args, **kw: calls.append(args[0]) or SimpleNamespace(stdout=PAYLOAD))
    assert len(module.device_code()) == 64
    assert len(calls) == 1

def test_discovery_only_standard_absolute_locations(monkeypatch, tmp_path):
    monkeypatch.setenv('ProgramFiles', str(tmp_path/'programs'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'local'))
    standard=tmp_path/'programs/PowerShell/7/pwsh.exe'
    alias=tmp_path/'local/Microsoft/WindowsApps/pwsh.exe'
    for path in [standard, alias]:
        path.parent.mkdir(parents=True); path.touch()
    assert module._powershell7_candidates() == [standard, alias]
    monkeypatch.setenv('ProgramFiles', '.')
    assert module._powershell7_candidates() == [alias]

def test_both_runtimes_fail_with_safe_diagnostics(monkeypatch,tmp_path):
    alias=tmp_path/'Microsoft/WindowsApps/pwsh.exe'
    alias.parent.mkdir(parents=True);alias.touch()
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    monkeypatch.delenv('ProgramFiles',raising=False)
    calls=[]
    def run(args,**kwargs):
        calls.append(args[0])
        raise subprocess.CalledProcessError(4294901760,args,stderr=b'private serial')
    monkeypatch.setattr(module.subprocess,'run',run)
    with pytest.raises(module.LicenseError) as error: module.device_code()
    assert 'PowerShell 7' in str(error.value) and 'private' not in str(error.value)
    assert len(calls)==4
