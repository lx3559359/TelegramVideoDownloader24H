"""Online device licensing. No client wall-clock or persisted trial counter."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import ssl
import subprocess
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LICENSE_URL = "https://license.cqtcshequ.com/v1/license"
# DNS, trusted TLS and synthetic three-plan redemption accepted on 2026-09-12.
LICENSING_ENFORCED = True


class LicenseError(ValueError):
    pass


def device_code() -> str:
    command = (
        "$u=(Get-CimInstance Win32_ComputerSystemProduct).UUID;"
        "$b=(Get-CimInstance Win32_BIOS).SerialNumber;"
        "@{uuid=$u;bios=$b}|ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=15, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        values = json.loads(result.stdout.decode("utf-8-sig"))
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise LicenseError("无法读取设备标识，请检查 Windows WMI 服务") from error
    return fingerprint(values.get("uuid"), values.get("bios"))


def fingerprint(uuid: str | None, bios: str | None) -> str:
    invalid = {"", "none", "unknown", "default string", "to be filled by o.e.m.", "system serial number"}
    value = (uuid or "").strip().lower()
    compact = value.replace("-", "")
    if not re.fullmatch(r"[0-9a-f]{32}", compact) or set(compact) in ({"0"}, {"f"}):
        value = (bios or "").strip().lower()
        if value in invalid or len(value) < 4 or set(value) <= {"0", " "}:
            raise LicenseError("此设备没有可靠硬件标识，无法自动开通试用，请联系管理员")
        value = "bios:" + value
    else:
        value = "uuid:" + compact
    return hashlib.sha256(("tg-video-downloader:v1:" + value).encode()).hexdigest()


def request_license(device: str, code: str) -> dict:
    try:
        context = ssl.create_default_context()
        request = Request(LICENSE_URL, data=json.dumps({"device": device, "code": code}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, context=context, timeout=10) as response:
            payload = response.read(8193)
        if len(payload) > 8192:
            raise ValueError("oversized response")
        return json.loads(payload)
    except HTTPError as error:
        if error.code == 400:
            raise LicenseError("激活码无效、已绑定其他设备，或授权服务暂不可用") from None
        raise LicenseError("授权服务器暂不可用，请稍后重试") from None
    except (OSError, URLError, ValueError) as error:
        raise LicenseError("无法安全连接授权服务器，请检查网络后重试") from None


@dataclass(frozen=True)
class LicenseStatus:
    device: str
    allowed: bool
    plan: str
    expires_at: int
    server_time: int


class LicenseGate:
    def __init__(self, *, transport=request_license, identify=device_code, clock=time.monotonic):
        self._transport = transport
        self._identify = identify
        self._clock = clock
        self._device: str | None = None
        self.status: LicenseStatus | None = None
        self._deadline = 0.0
        self._refresh_at = 0.0
        self._lock = asyncio.Lock()

    def require_cached(self) -> None:
        if self.status is None:
            raise LicenseError("请先在“授权”页开始试用或激活")
        if not self.status.allowed or self._clock() >= self._deadline:
            raise LicenseError("试用/授权已到期或需要联网验证，请到“授权”页刷新或激活")

    async def refresh(self, code: str = "") -> LicenseStatus:
        async with self._lock:
            if self._device is None:
                self._device = await asyncio.to_thread(self._identify)
            started = self._clock()
            payload = await asyncio.to_thread(self._transport, self._device, code.strip())
            try:
                if (payload["device"] != self._device
                    or type(payload["allowed"]) is not bool
                    or payload["plan"] not in {"trial", "month", "year", "permanent"}
                    or type(payload["expires_at"]) is not int or payload["expires_at"] < 0
                    or type(payload["server_time"]) is not int or payload["server_time"] <= 0):
                    raise ValueError("invalid response")
                remaining = (600 if payload["plan"] == "permanent" else
                             max(0, payload["expires_at"] - payload["server_time"]))
                if payload["allowed"] and remaining <= 0:
                    raise ValueError("invalid expiry")
                status = LicenseStatus(**payload)
            except (KeyError, TypeError, ValueError):
                raise LicenseError("授权响应无效") from None
            self.status = status
            self._deadline = started + min(600, remaining) if status.allowed else 0
            self._refresh_at = started + min(300, remaining)
            return status

    async def ensure(self) -> None:
        if self.status is None or self._clock() >= self._refresh_at:
            try:
                await self.refresh()
            except LicenseError:
                self.require_cached()
                self._refresh_at = min(self._clock() + 30, self._deadline)
        self.require_cached()


def create_license_gate() -> LicenseGate:
    return LicenseGate() if LICENSING_ENFORCED else PendingDeploymentGate()


class PendingDeploymentGate(LicenseGate):
    async def ensure(self) -> None:
        return

    def require_cached(self) -> None:
        return
