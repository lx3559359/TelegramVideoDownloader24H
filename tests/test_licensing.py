import pytest
import asyncio

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
