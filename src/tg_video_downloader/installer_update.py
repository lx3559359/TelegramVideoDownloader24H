"""Independent installer updates. No Git and no code execution from the manifest."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from importlib.metadata import version
from threading import Event
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from tg_video_downloader.paths import ProjectPaths

SITE = 'https://www.cqtcshequ.com'
MANIFEST_URL = SITE + '/assets/installer-latest.json'
MAX_MANIFEST = 65536
MAX_INSTALLER = 512 * 1024 * 1024
VERSION = re.compile(r'(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})')


class DownloadCancelled(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('更新下载不允许重定向到其他地址，请稍后重试或访问官网')


def version_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError('无效的稳定版本号')
    return tuple(map(int, value.split('.')))


@dataclass(frozen=True)
class InstallerRelease:
    version: str
    url: str
    size: int
    sha256: str
    notes: str


@dataclass(frozen=True)
class DownloadedInstaller:
    release: InstallerRelease
    path: Path


def parse_manifest(data: object) -> InstallerRelease:
    if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1:
        raise ValueError('不支持的更新清单')
    current = data.get('version')
    version_tuple(current)
    expected = f'{SITE}/downloads/TelegramVideoDownloader-v{current}-Windows-x64-Setup.exe'
    if data.get('url') != expected:
        raise ValueError('安装包地址不是许可的官网版本地址')
    size = data.get('size')
    sha = data.get('sha256')
    notes = data.get('notes')
    if type(size) is not int or not 0 < size <= MAX_INSTALLER:
        raise ValueError('安装包大小无效')
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
        raise ValueError('安装包校验值无效')
    if not isinstance(notes, str) or len(notes) > 12000:
        raise ValueError('更新说明无效')
    return InstallerRelease(current, expected, size, sha, notes)


def helper_source() -> Path:
    return Path(__file__).parent / 'assets' / 'install-update.ps1'


def launch_helper(script: Path, request: Path):
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return subprocess.Popen(
        [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-RequestPath', str(request)],
        cwd=script.parent, creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class InstallerUpdateManager:
    def __init__(self, paths: ProjectPaths, *, current_version=None, opener=None, launcher=launch_helper):
        self.paths = paths
        self.current_version = current_version or version('telegram-video-downloader')
        self.opener = opener or build_opener(NoRedirect()).open
        self.launcher = launcher

    def check(self) -> InstallerRelease | None:
        request = Request(MANIFEST_URL + '?check=' + uuid4().hex,
                          headers={'Cache-Control': 'no-cache', 'Accept': 'application/json'})
        with self.opener(request, timeout=20) as response:
            if response.status != 200:
                raise ValueError('官网更新清单暂不可用')
            body = response.read(MAX_MANIFEST + 1)
        if len(body) > MAX_MANIFEST:
            raise ValueError('更新清单过大')
        release = parse_manifest(json.loads(body))
        return release if version_tuple(release.version) > version_tuple(self.current_version) else None

    def download(self, release: InstallerRelease, cancel: Event, progress) -> DownloadedInstaller:
        self._validate_release(release)
        if cancel.is_set():
            raise DownloadCancelled('已取消下载')
        directory = self.paths.assert_within_root(self.paths.cache / 'installer-updates' / uuid4().hex)
        directory.mkdir(parents=True)
        partial = directory / 'installer.part'
        target = directory / f'TelegramVideoDownloader-v{release.version}-Windows-x64-Setup.exe'
        digest = hashlib.sha256()
        received = 0
        started = time.monotonic()
        try:
            with self.opener(Request(release.url, headers={'Cache-Control': 'no-cache'}), timeout=20) as response, partial.open('xb') as output:
                if response.status != 200:
                    raise ValueError('安装包下载响应异常')
                while True:
                    if cancel.is_set():
                        raise DownloadCancelled('已取消下载')
                    if time.monotonic() - started > 1800:
                        raise TimeoutError('安装包下载超过 30 分钟，请重试')
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > release.size:
                        raise ValueError('安装包超出声明大小')
                    output.write(block)
                    digest.update(block)
                    progress(received, release.size)
                output.flush()
                os.fsync(output.fileno())
            if cancel.is_set():
                raise DownloadCancelled('已取消下载')
            if received != release.size or digest.hexdigest() != release.sha256:
                raise ValueError('安装包大小或 SHA-256 校验失败，请重新下载')
            partial.replace(target)
            return DownloadedInstaller(release, target)
        finally:
            partial.unlink(missing_ok=True)

    def _validate_release(self, release):
        parse_manifest({'schema': 1, **asdict(release)})
        if version_tuple(release.version) <= version_tuple(self.current_version):
            raise ValueError('不能安装相同或更旧的版本')

    def validate_prepared(self, item: DownloadedInstaller):
        self._validate_release(item.release)
        path = self.paths.assert_within_root(item.path)
        cache = self.paths.assert_within_root(self.paths.cache / 'installer-updates')
        if not path.is_relative_to(cache) or path.suffix != '.exe':
            raise ValueError('安装包不在更新缓存内')
        if path.stat().st_size != item.release.size:
            raise ValueError('安装包大小已改变')
        with path.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != item.release.sha256:
                raise ValueError('安装包校验失败')

    def validate_install_environment(self):
        if not helper_source().is_file():
            raise ValueError('缺少安装更新助手，请从官网覆盖安装')
        if not (self.paths.root / 'TelegramVideoDownloader.exe').is_file():
            raise ValueError('此更新方式仅适用于独立安装版')
        if not (self.paths.root / 'scripts/run-supervisor.ps1').is_file():
            raise ValueError('缺少后台启动脚本，请从官网覆盖安装')

    def prepare_install(self, item: DownloadedInstaller, restore_service: bool):
        self.validate_prepared(item)
        self.validate_install_environment()
        directory = item.path.parent
        request = directory / 'request.json'
        if request.exists():
            raise ValueError('本次更新已提交，请重新检查更新后重试')
        helper = directory / 'install-update.ps1'
        shutil.copyfile(helper_source(), helper)
        token = uuid4().hex
        data = dict(root=str(self.paths.root), installer=str(item.path.resolve()),
                    version=item.release.version, size=item.release.size, sha256=item.release.sha256,
                    restore_service=bool(restore_service), parent_pid=os.getpid(), token=token)
        request.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        process = self.launcher(helper, request)
        deadline = time.monotonic() + 15
        ready = request.with_suffix('.ready')
        while not ready.exists() or ready.read_text(encoding='utf-8') != token:
            if process.poll() is not None:
                raise RuntimeError('更新助手启动失败，请查看日志或从官网覆盖安装')
            if time.monotonic() >= deadline:
                request.with_suffix('.cancel').touch()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # This is our own helper, before GUI handoff. It cannot have
                    # started installation while this parent process is alive.
                    process.terminate()
                    process.wait(timeout=5)
                raise TimeoutError('更新助手启动超时，已取消安装')
            time.sleep(0.1)


def consume_installer_result(paths: ProjectPaths):
    path = paths.runtime / 'installer-update-result.json'
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_MANIFEST:
            raise ValueError('更新结果无效')
        result = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(result, dict) or result.get('status') not in ('success', 'failed') or not isinstance(result.get('message'), str):
            raise ValueError('更新结果无效')
        return result
    finally:
        path.unlink(missing_ok=True)
