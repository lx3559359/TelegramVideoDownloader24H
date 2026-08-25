from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from tg_video_downloader.paths import ProjectPaths


GITHUB_URL = "https://github.com/lx3559359/TelegramVideoDownloader24H.git"
MODELSCOPE_URL = (
    "https://www.modelscope.cn/studios/lx3559359/TelegramVideoDownloader24H.git"
)
STABLE_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


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
