"""Online device licensing. No client wall-clock or persisted trial counter."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
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
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false);"
        "$u=$null;$b=$null;$queryFailed=$false;"
        "try {$u=(Get-CimInstance Win32_ComputerSystemProduct).UUID} catch {$queryFailed=$true};"
        "try {$b=(Get-CimInstance Win32_BIOS).SerialNumber} catch {$queryFailed=$true};"
        "@{uuid=$u;bios=$b;query_failed=$queryFailed}|ConvertTo-Json -Compress"
    )
    system_root = os.environ.get('SystemRoot')
    if not system_root:
        raise LicenseError('找不到 Windows 系统目录，无法启动设备读取程序')
    powershell = Path(system_root) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    for attempt in range(2):
        try:
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, timeout=15, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            values = json.loads(result.stdout.decode("utf-8-sig"))
            if not isinstance(values, dict) or any(
                values.get(key) is not None and not isinstance(values[key], str)
                for key in ('uuid', 'bios')
            ):
                raise ValueError('Invalid hardware response')
            try:
                return fingerprint(values.get('uuid'), values.get('bios'))
            except LicenseError:
                if values.get('query_failed') is True:
                    raise subprocess.CalledProcessError(2, str(powershell)) from None
                raise
        except FileNotFoundError as error:
            raise LicenseError('找不到系统 Windows PowerShell，请检查系统组件') from error
        except subprocess.TimeoutExpired as error:
            failure = LicenseError('设备读取超时（每次 15 秒，已尝试 2 次），请点击重新获取设备码')
            cause = error
        except subprocess.CalledProcessError as error:
            failure = LicenseError(f'设备读取命令失败（退出码 {error.returncode}），请检查 PowerShell / WMI 权限后重新获取')
            cause = error
        except LicenseError:
            raise
        except (ValueError, UnicodeError) as error:
            raise LicenseError('设备读取结果格式错误，请重新获取设备码') from error
        except OSError as error:
            raise LicenseError(f'无法启动设备读取程序（系统错误 {getattr(error, "winerror", None) or error.errno}）') from error
        if attempt == 1:
            raise failure from cause


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

    async def identify(self, *, force: bool = False) -> str:
        """Read the hardware ID without requesting or starting a license trial."""
        async with self._lock:
            if self._device is None or force:
                device = await asyncio.to_thread(self._identify)
                if self._device is not None and device != self._device:
                    raise LicenseError('硬件标识发生变化，已保留原设备绑定，请联系管理员核对')
                self._device = device
            return self._device

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
