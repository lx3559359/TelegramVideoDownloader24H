import pytest
import asyncio


@pytest.mark.asyncio
async def test_forced_identification_preserves_binding_and_trial():
    answers = iter(['a' * 64, 'a' * 64, 'b' * 64])
    gate = LicenseGate(identify=lambda: next(answers))
    assert await gate.identify() == 'a' * 64
    assert await gate.identify(force=True) == 'a' * 64
    with pytest.raises(LicenseError, match='变化'):
        await gate.identify(force=True)
    assert await gate.identify() == 'a' * 64
    assert gate.status is None


def test_device_read_retries_timeout_with_absolute_powershell(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from tg_video_downloader import licensing as module
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args, 15)
        return SimpleNamespace(stdout=b'{"uuid":"12345678-1234-1234-1234-1234567890ab","bios":""}')
    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setenv('SystemRoot', 'C:\\Windows')
    assert module.device_code() == fingerprint('12345678-1234-1234-1234-1234567890ab', '')
    assert len(calls) == 2
    assert calls[0][0][0].lower().endswith('system32\\windowspowershell\\v1.0\\powershell.exe')
    assert 'UTF8Encoding' in calls[0][0][-1]
    assert 'try {$u=' in calls[0][0][-1]
    assert 'try {$b=' in calls[0][0][-1]
    assert calls[0][1]['timeout'] == 15


@pytest.mark.parametrize('kind,expected,count', [
    ('missing', 'PowerShell', 1), ('timeout', '超时', 2),
    ('command', '退出码 7', 2), ('json', '格式错误', 1), ('shape', '格式错误', 1),
])
def test_device_read_failures_are_bounded_and_specific(monkeypatch, kind, expected, count):
    import subprocess
    from types import SimpleNamespace
    from tg_video_downloader import licensing as module
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        if kind == 'missing': raise FileNotFoundError()
        if kind == 'timeout': raise subprocess.TimeoutExpired('probe', 15)
        if kind == 'command': raise subprocess.CalledProcessError(7, 'probe', stderr=b'private serial')
        return SimpleNamespace(stdout=b'bad' if kind == 'json' else b'[]')
    monkeypatch.setattr(module.subprocess, 'run', run)
    with pytest.raises(LicenseError, match=expected) as error:
        module.device_code()
    assert 'private serial' not in str(error.value)
    assert len(calls) == count


def test_cim_query_failure_retries_without_changing_fallback(monkeypatch):
    from types import SimpleNamespace
    from tg_video_downloader import licensing as module
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(stdout=(b'{"uuid":null,"bios":null,"query_failed":true}'
            if len(calls) == 1 else b'{"uuid":null,"bios":"valid-bios","query_failed":true}'))
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.device_code() == fingerprint(None, 'valid-bios')
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_failed_forced_read_keeps_existing_cache_and_status():
    gate = LicenseGate(identify=lambda: 'a' * 64)
    await gate.identify()
    def fail(): raise LicenseError('timeout')
    gate._identify = fail
    with pytest.raises(LicenseError): await gate.identify(force=True)
    assert await gate.identify() == 'a' * 64
    assert gate.status is None

from tg_video_downloader.licensing import LicenseError, LicenseGate, fingerprint
from tg_video_downloader.licensing import create_license_gate as release_gate_factory


@pytest.mark.asyncio
async def test_identify_is_cached_and_never_starts_license_trial():
    reads = []
    calls = []
    gate = LicenseGate(identify=lambda: reads.append(1) or 'a' * 64,
                       transport=lambda *args: calls.append(args))
    assert await asyncio.gather(gate.identify(), gate.identify()) == ['a' * 64] * 2
    assert reads == [1]
    assert calls == []
    assert gate.status is None


def test_release_factory_enforces_online_licensing():
    assert type(release_gate_factory()) is LicenseGate


def test_device_hash_is_stable_and_rejects_missing_hardware():
    first = fingerprint('12345678-1234-1234-1234-1234567890AB', 'old')
    assert first == fingerprint('12345678-1234-1234-1234-1234567890ab', 'new')
    assert len(first) == 64
    with pytest.raises(LicenseError):
        fingerprint('00000000-0000-0000-0000-000000000000', 'Default string')


@pytest.mark.asyncio
async def test_expiry_uses_server_time_and_monotonic_clock():
    clock = [100.0]
    def transport(device, code):
        return dict(device=device, allowed=True, plan='trial', expires_at=1050, server_time=1000)
    gate = LicenseGate(identify=lambda: 'device', transport=transport, clock=lambda: clock[0])
    await gate.ensure()
    clock[0] = 151
    with pytest.raises(LicenseError):
        gate.require_cached()


@pytest.mark.asyncio
async def test_offline_grace_is_bounded_and_new_process_requires_network():
    clock = [0.0]
    offline = [False]
    def transport(device, code):
        if offline[0]: raise LicenseError('offline')
        return dict(device=device, allowed=True, plan='permanent', expires_at=0, server_time=1000)
    gate = LicenseGate(identify=lambda: 'device', transport=transport, clock=lambda: clock[0])
    await gate.ensure()
    offline[0] = True
    clock[0] = 301
    await gate.ensure()
    clock[0] = 601
    with pytest.raises(LicenseError): await gate.ensure()
    fresh = LicenseGate(identify=lambda: 'device', transport=transport)
    with pytest.raises(LicenseError): await fresh.ensure()


@pytest.mark.asyncio
async def test_expired_response_overrides_previous_permission():
    response = dict(device='device', allowed=True, plan='month', expires_at=2000, server_time=1000)
    gate = LicenseGate(identify=lambda: 'device', transport=lambda *_: response)
    await gate.ensure()
    response.update(allowed=False, server_time=2001)
    await gate.refresh()
    with pytest.raises(LicenseError): gate.require_cached()
