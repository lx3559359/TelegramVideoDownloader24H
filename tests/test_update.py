from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tg_video_downloader.update import (
    AvailableRelease,
    ChangedFile,
    GITHUB_URL,
    MODELSCOPE_URL,
    PreparedRelease,
    UpdateCheckError,
    UpdateManager,
    UpdateRequest,
    UpdateResult,
    UpdateSafetyError,
    filter_changes,
    parse_stable_tag,
    consume_update_result,
    read_update_request,
    write_update_request,
    write_update_result,
)
from tg_video_downloader.paths import ProjectPaths


class FakeRunner:
    def __init__(
        self,
        *,
        failures: set[str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        self.failures = failures or set()
        self.outputs = outputs or {}
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None = None,
    ) -> str:
        del cwd
        self.calls.append(arguments)
        remote_url = arguments[-1]
        if remote_url in self.failures:
            raise subprocess.CalledProcessError(
                1,
                arguments,
                stderr="network error",
            )
        return self.outputs.get(remote_url, "")


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v0.2.0", (0, 2, 0)), ("v10.11.12", (10, 11, 12))],
)
def test_parse_stable_tag(tag: str, expected: tuple[int, int, int]) -> None:
    assert parse_stable_tag(tag).parts == expected


@pytest.mark.parametrize(
    "tag",
    ["0.2.0", "v1.2", "v1.2.3-rc1", "latest", "v-1.2.3"],
)
def test_parse_stable_tag_rejects_non_release_tags(tag: str) -> None:
    with pytest.raises(ValueError):
        parse_stable_tag(tag)


def test_github_failure_falls_back_to_modelscope() -> None:
    runner = FakeRunner(
        failures={GITHUB_URL},
        outputs={MODELSCOPE_URL: "abc refs/tags/v0.2.0\n"},
    )

    release = UpdateManager(runner=runner, current_version="0.1.0").check_latest()

    assert release is not None
    assert release.tag == "v0.2.0"
    assert release.source == "魔塔"


def test_no_remote_is_queried_until_manual_check() -> None:
    runner = FakeRunner()

    UpdateManager(runner=runner, current_version="0.1.0")

    assert runner.calls == []


def test_successful_primary_with_no_higher_stable_tag_does_not_fallback() -> None:
    runner = FakeRunner(
        outputs={
            GITHUB_URL: (
                "a refs/tags/v0.1.0\n"
                "b refs/tags/v0.2.0-rc1\n"
            ),
            MODELSCOPE_URL: "c refs/tags/v9.0.0\n",
        }
    )

    assert UpdateManager(
        runner=runner,
        current_version="0.1.0",
    ).check_latest() is None
    assert len(runner.calls) == 1


def test_both_remote_failures_are_reported() -> None:
    runner = FakeRunner(failures={GITHUB_URL, MODELSCOPE_URL})

    with pytest.raises(UpdateCheckError, match="两个更新源"):
        UpdateManager(runner=runner, current_version="0.1.0").check_latest()


def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit_file(
    repository: Path,
    relative: str,
    content: str,
    message: str,
) -> str:
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repository, "add", relative)
    git(
        repository,
        "-c",
        "user.name=Update Test",
        "-c",
        "user.email=update@example.invalid",
        "commit",
        "-m",
        message,
    )
    return git(repository, "rev-parse", "HEAD")


@dataclass(frozen=True)
class UpdateRepository:
    project: Path
    remote: Path
    source: Path
    base_commit: str
    release_commit: str


@pytest.fixture
def update_repository(tmp_path: Path) -> UpdateRepository:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    project = tmp_path / "project"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "master", str(source))
    base = commit_file(
        source,
        "src/tg_video_downloader/gui/app.py",
        "v1\n",
        "base",
    )
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "-u", "origin", "master")
    git(tmp_path, "clone", str(remote), str(project))
    commit_file(
        source,
        "src/tg_video_downloader/gui/app.py",
        "v2\n",
        "app",
    )
    commit_file(source, "README-new.md", "release\n", "readme")
    release = git(source, "rev-parse", "HEAD")
    git(source, "tag", "v0.2.0", release)
    git(source, "push", "origin", "master", "refs/tags/v0.2.0")
    return UpdateRepository(project, remote, source, base, release)


def make_release(repository: UpdateRepository, tag: str = "v0.2.0") -> AvailableRelease:
    return AvailableRelease(
        parse_stable_tag(tag),
        tag,
        "测试远端",
        str(repository.remote),
    )


def test_prepare_release_fetches_validates_and_filters_changes(
    update_repository: UpdateRepository,
) -> None:
    manager = UpdateManager(
        paths=ProjectPaths.from_root(update_repository.project),
        current_version="0.1.0",
    )

    prepared = manager.prepare_release(make_release(update_repository))

    assert prepared.base_commit == update_repository.base_commit
    assert prepared.target_commit == update_repository.release_commit
    assert prepared.changes == (
        ChangedFile("A", "README-new.md"),
        ChangedFile("M", "src/tg_video_downloader/gui/app.py"),
    )
    assert filter_changes(prepared.changes, "GUI") == (prepared.changes[1],)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda root: (root / "src/tg_video_downloader/gui/app.py").write_text(
                "dirty\n",
                encoding="utf-8",
            ),
            "工作区不干净",
        ),
        (lambda root: git(root, "switch", "-c", "feature"), "必须位于 master"),
        (
            lambda root: (root / "untracked.txt").write_text(
                "new",
                encoding="utf-8",
            ),
            "工作区不干净",
        ),
    ],
)
def test_prepare_release_rejects_unsafe_repository(
    update_repository: UpdateRepository,
    mutation: Callable[[Path], object],
    message: str,
) -> None:
    mutation(update_repository.project)
    manager = UpdateManager(
        paths=ProjectPaths.from_root(update_repository.project),
        current_version="0.1.0",
    )

    with pytest.raises(UpdateSafetyError, match=message):
        manager.prepare_release(make_release(update_repository))


def test_prepared_release_rejects_changed_head(
    update_repository: UpdateRepository,
) -> None:
    manager = UpdateManager(
        paths=ProjectPaths.from_root(update_repository.project),
        current_version="0.1.0",
    )
    prepared = manager.prepare_release(make_release(update_repository))
    commit_file(update_repository.project, "local.txt", "local\n", "local")

    with pytest.raises(UpdateSafetyError, match="HEAD 已变化"):
        manager.validate_prepared(prepared)


def test_prepare_release_rejects_divergent_target(
    update_repository: UpdateRepository,
) -> None:
    tree = git(update_repository.source, "rev-parse", "HEAD^{tree}")
    divergent = git(
        update_repository.source,
        "-c",
        "user.name=Update Test",
        "-c",
        "user.email=update@example.invalid",
        "commit-tree",
        tree,
        "-m",
        "divergent",
    )
    git(update_repository.source, "tag", "v0.3.0", divergent)
    git(
        update_repository.source,
        "push",
        "origin",
        "refs/tags/v0.3.0",
    )
    manager = UpdateManager(
        paths=ProjectPaths.from_root(update_repository.project),
        current_version="0.1.0",
    )

    with pytest.raises(UpdateSafetyError, match="不是当前版本的快进更新"):
        manager.prepare_release(make_release(update_repository, "v0.3.0"))


def test_update_request_round_trips_inside_runtime(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    request = UpdateRequest(
        token="a" * 32,
        tag="v0.2.0",
        base_commit="1" * 40,
        target_commit="2" * 40,
        restore_service=True,
    )

    write_update_request(paths, request)

    assert read_update_request(paths) == request
    assert paths.update_request.resolve().is_relative_to(paths.root)
    assert list(paths.runtime.glob(".update-request.json.*.tmp")) == []


def test_update_state_rejects_invalid_tag_or_commit(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    paths.update_request.write_text('{"tag":"main"}', encoding="utf-8")

    with pytest.raises(ValueError):
        read_update_request(paths)


def test_update_result_is_validated_and_consumed_once(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    result = UpdateResult(
        token="b" * 32,
        tag="v0.2.0",
        status="success",
        message="更新完成",
        completed_at="2026-08-26T12:00:00+00:00",
    )

    write_update_result(paths, result)

    assert consume_update_result(paths) == result
    assert consume_update_result(paths) is None


def test_prepare_install_copies_executor_writes_request_and_launches(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    scripts = paths.root / "scripts"
    scripts.mkdir()
    source = scripts / "apply-update.ps1"
    source.write_text("param()\n", encoding="utf-8")
    prepared = PreparedRelease(
        release=AvailableRelease(
            parse_stable_tag("v0.2.0"),
            "v0.2.0",
            "测试",
            "https://example.invalid/repo.git",
        ),
        base_commit="1" * 40,
        target_commit="2" * 40,
        changes=(),
    )
    events: list[str] = []
    launched: list[tuple[Path, Path, Path]] = []

    def launch(project_root: Path, executor: Path, request_path: Path) -> object:
        events.append("launch")
        launched.append((project_root, executor, request_path))
        assert executor.read_text(encoding="utf-8") == "param()\n"
        assert read_update_request(paths).target_commit == prepared.target_commit
        return object()

    manager = UpdateManager(
        paths=paths,
        current_version="0.1.0",
        launch_update=launch,
    )
    manager.validate_prepared = lambda value: events.append("validate")

    request = manager.prepare_install(prepared, True)

    assert events == ["validate", "launch"]
    assert request.restore_service is True
    assert read_update_request(paths) == request
    assert launched == [
        (
            paths.root,
            paths.temp / f"update-{request.token}" / "apply-update.ps1",
            paths.update_request,
        )
    ]


def test_prepare_install_cleans_only_its_files_when_launch_fails(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    scripts = paths.root / "scripts"
    scripts.mkdir()
    (scripts / "apply-update.ps1").write_text("param()\n", encoding="utf-8")
    prepared = PreparedRelease(
        release=AvailableRelease(
            parse_stable_tag("v0.2.0"),
            "v0.2.0",
            "测试",
            "https://example.invalid/repo.git",
        ),
        base_commit="1" * 40,
        target_commit="2" * 40,
        changes=(),
    )

    def fail_launch(*_args) -> object:
        raise OSError("launch failed")

    manager = UpdateManager(
        paths=paths,
        current_version="0.1.0",
        launch_update=fail_launch,
    )
    manager.validate_prepared = lambda _value: None

    with pytest.raises(OSError, match="launch failed"):
        manager.prepare_install(prepared, False)

    assert not paths.update_request.exists()
    assert list(paths.temp.glob("update-*")) == []
