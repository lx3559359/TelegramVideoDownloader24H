from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from uuid import uuid4

from tg_video_downloader.paths import ProjectPaths


GITHUB_URL = "https://github.com/lx3559359/TelegramVideoDownloader24H.git"
MODELSCOPE_URL = (
    "https://www.modelscope.cn/studios/lx3559359/TelegramVideoDownloader24H.git"
)
STABLE_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
UPDATE_TOKEN = re.compile(r"^[0-9a-f]{32}$")
COMMIT_ID = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, order=True)
class StableVersion:
    parts: tuple[int, int, int]

    def __str__(self) -> str:
        return ".".join(str(part) for part in self.parts)


@dataclass(frozen=True)
class AvailableRelease:
    version: StableVersion
    tag: str
    source: str
    remote_url: str


@dataclass(frozen=True)
class ChangedFile:
    status: str
    path: str


@dataclass(frozen=True)
class PreparedRelease:
    release: AvailableRelease
    base_commit: str
    target_commit: str
    changes: tuple[ChangedFile, ...]


@dataclass(frozen=True)
class UpdateRequest:
    token: str
    tag: str
    base_commit: str
    target_commit: str
    restore_service: bool


@dataclass(frozen=True)
class UpdateResult:
    token: str
    tag: str
    status: Literal["success", "rolled_back", "failed"]
    message: str
    completed_at: str


CommandRunner = Callable[..., str]


class UpdateSafetyError(RuntimeError):
    pass


class UpdateCheckError(RuntimeError):
    pass


def run_command(
    arguments: tuple[str, ...],
    *,
    cwd: Path | None = None,
) -> str:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return completed.stdout


def parse_stable_tag(tag: str) -> StableVersion:
    match = STABLE_TAG.fullmatch(tag)
    if match is None:
        raise ValueError(f"不是稳定版本标签: {tag}")
    return StableVersion(tuple(int(value) for value in match.groups()))


def parse_changed_files(output: str) -> tuple[ChangedFile, ...]:
    fields = output.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: list[ChangedFile] = []
    index = 0
    while index < len(fields):
        status_field = fields[index]
        index += 1
        status = status_field[:1]
        if status in {"R", "C"} and index + 1 < len(fields):
            index += 1
            path = fields[index]
            index += 1
        elif status in {"A", "M", "D", "T"} and index < len(fields):
            path = fields[index]
            index += 1
        else:
            raise UpdateSafetyError("无法解析版本变更文件列表")
        changes.append(ChangedFile(status, path))
    return tuple(changes)


def filter_changes(
    changes: tuple[ChangedFile, ...],
    query: str,
) -> tuple[ChangedFile, ...]:
    needle = query.casefold().strip()
    if not needle:
        return changes
    return tuple(item for item in changes if needle in item.path.casefold())


def _validate_request(request: UpdateRequest) -> UpdateRequest:
    if not isinstance(request.token, str) or UPDATE_TOKEN.fullmatch(request.token) is None:
        raise ValueError("更新令牌格式无效")
    if not isinstance(request.tag, str):
        raise ValueError("更新标签格式无效")
    parse_stable_tag(request.tag)
    if (
        not isinstance(request.base_commit, str)
        or COMMIT_ID.fullmatch(request.base_commit) is None
    ):
        raise ValueError("基础提交格式无效")
    if (
        not isinstance(request.target_commit, str)
        or COMMIT_ID.fullmatch(request.target_commit) is None
    ):
        raise ValueError("目标提交格式无效")
    if not isinstance(request.restore_service, bool):
        raise ValueError("服务恢复状态格式无效")
    return request


def _validate_result(result: UpdateResult) -> UpdateResult:
    if not isinstance(result.token, str) or UPDATE_TOKEN.fullmatch(result.token) is None:
        raise ValueError("更新结果令牌格式无效")
    if not isinstance(result.tag, str):
        raise ValueError("更新结果标签格式无效")
    parse_stable_tag(result.tag)
    if result.status not in {"success", "rolled_back", "failed"}:
        raise ValueError("更新结果状态无效")
    if not isinstance(result.completed_at, str):
        raise ValueError("更新完成时间格式无效")
    datetime.fromisoformat(result.completed_at)
    if not isinstance(result.message, str) or len(result.message) > 500:
        raise ValueError("更新结果摘要无效")
    return result


def _write_atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_update_request(paths: ProjectPaths, request: UpdateRequest) -> None:
    checked = _validate_request(request)
    _write_atomic_json(paths.update_request, asdict(checked))


def read_update_request(paths: ProjectPaths) -> UpdateRequest:
    raw = json.loads(paths.update_request.read_text(encoding="utf-8"))
    expected = {"token", "tag", "base_commit", "target_commit", "restore_service"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("更新请求字段无效")
    return _validate_request(UpdateRequest(**raw))


def write_update_result(paths: ProjectPaths, result: UpdateResult) -> None:
    checked = _validate_result(result)
    _write_atomic_json(paths.update_result, asdict(checked))


def read_update_result(paths: ProjectPaths) -> UpdateResult:
    raw = json.loads(paths.update_result.read_text(encoding="utf-8"))
    expected = {"token", "tag", "status", "message", "completed_at"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("更新结果字段无效")
    return _validate_result(UpdateResult(**raw))


def consume_update_result(paths: ProjectPaths) -> UpdateResult | None:
    if not paths.update_result.is_file():
        return None
    result = read_update_result(paths)
    paths.update_result.unlink()
    return result


class UpdateManager:
    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        runner: CommandRunner = run_command,
        current_version: str | None = None,
        launch_update: Callable[[Path, Path, Path], object] | None = None,
    ) -> None:
        installed = current_version or version("telegram-video-downloader")
        self.paths = paths
        self.current_version = parse_stable_tag(f"v{installed}")
        self._runner = runner
        self._launch_update = launch_update

    def _run(self, arguments: tuple[str, ...]) -> str:
        if self.paths is None:
            raise UpdateSafetyError("更新操作缺少项目路径")
        return self._runner(arguments, cwd=self.paths.root)

    def check_latest(self) -> AvailableRelease | None:
        remotes = (("GitHub", GITHUB_URL), ("魔塔", MODELSCOPE_URL))
        for index, (source, remote_url) in enumerate(remotes):
            try:
                output = self._runner(
                    ("git", "ls-remote", "--tags", "--refs", remote_url),
                    cwd=self.paths.root if self.paths is not None else None,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                if index == 0:
                    continue
                raise UpdateCheckError("两个更新源均不可用") from error
            releases: list[AvailableRelease] = []
            for line in output.splitlines():
                fields = line.split(maxsplit=1)
                if len(fields) != 2 or not fields[1].startswith("refs/tags/"):
                    continue
                tag = fields[1].removeprefix("refs/tags/")
                try:
                    candidate = parse_stable_tag(tag)
                except ValueError:
                    continue
                if candidate > self.current_version:
                    releases.append(
                        AvailableRelease(candidate, tag, source, remote_url)
                    )
            return max(releases, key=lambda item: item.version, default=None)
        return None

    def prepare_latest(self) -> PreparedRelease | None:
        release = self.check_latest()
        return None if release is None else self.prepare_release(release)

    def prepare_release(self, release: AvailableRelease) -> PreparedRelease:
        if release.version <= self.current_version:
            raise UpdateSafetyError("目标版本不是更高的稳定版本")
        branch = self._run(("git", "branch", "--show-current")).strip()
        if branch != "master":
            raise UpdateSafetyError("在线更新必须位于 master 分支")
        if self._run(
            ("git", "status", "--porcelain", "--untracked-files=all")
        ).strip():
            raise UpdateSafetyError("工作区不干净，拒绝在线更新")
        base_commit = self._run(("git", "rev-parse", "HEAD")).strip()
        self._run(
            (
                "git",
                "fetch",
                "--no-tags",
                release.remote_url,
                f"refs/tags/{release.tag}:refs/tags/{release.tag}",
            )
        )
        target_commit = self._run(
            ("git", "rev-list", "-n", "1", f"refs/tags/{release.tag}")
        ).strip()
        try:
            self._run(
                (
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    base_commit,
                    target_commit,
                )
            )
        except subprocess.CalledProcessError as error:
            raise UpdateSafetyError(
                "目标版本不是当前版本的快进更新"
            ) from error
        changes = parse_changed_files(
            self._run(
                (
                    "git",
                    "diff",
                    "--name-status",
                    "-z",
                    base_commit,
                    target_commit,
                )
            )
        )
        return PreparedRelease(release, base_commit, target_commit, changes)

    def validate_prepared(self, prepared: PreparedRelease) -> None:
        if prepared.release.version <= self.current_version:
            raise UpdateSafetyError("目标版本不是更高的稳定版本")
        branch = self._run(("git", "branch", "--show-current")).strip()
        if branch != "master":
            raise UpdateSafetyError("在线更新必须位于 master 分支")
        if self._run(
            ("git", "status", "--porcelain", "--untracked-files=all")
        ).strip():
            raise UpdateSafetyError("工作区不干净，拒绝在线更新")
        head = self._run(("git", "rev-parse", "HEAD")).strip()
        if head != prepared.base_commit:
            raise UpdateSafetyError("检查更新后 HEAD 已变化，请重新检查")
        target = self._run(
            (
                "git",
                "rev-list",
                "-n",
                "1",
                f"refs/tags/{prepared.release.tag}",
            )
        ).strip()
        if target != prepared.target_commit:
            raise UpdateSafetyError("稳定标签目标已变化，请重新检查")
        try:
            self._run(
                (
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    head,
                    prepared.target_commit,
                )
            )
        except subprocess.CalledProcessError as error:
            raise UpdateSafetyError(
                "目标版本不是当前版本的快进更新"
            ) from error
