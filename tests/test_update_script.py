from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.update import UpdateRequest, write_update_request


ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = ROOT / "scripts" / "apply-update.ps1"


def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    ).stdout.strip()


def commit_all(repository: Path, message: str) -> str:
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=Update Script Test",
        "-c",
        "user.email=update-script@example.invalid",
        "commit",
        "-m",
        message,
    )
    return git(repository, "rev-parse", "HEAD")


@dataclass(frozen=True)
class ScriptRepository:
    project: Path
    paths: ProjectPaths
    base_commit: str
    target_commit: str
    runtime_fixture: Path
    external_fixture: Path
    fixture_hash: str
    external_hash: str


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_update_script_repository(tmp_path: Path) -> ScriptRepository:
    project = tmp_path / "project"
    git(tmp_path, "init", "-b", "master", str(project))
    (project / ".gitignore").write_text(
        ".runtime/\n.tmp/\nlogs/\n.venv/\n",
        encoding="utf-8",
    )
    scripts = project / "scripts"
    scripts.mkdir()
    (scripts / "bootstrap.ps1").write_text(
        """param()
$failure = Join-Path $PSScriptRoot '..\\.tmp\\fail-bootstrap'
if (Test-Path -LiteralPath $failure) {
    Remove-Item -LiteralPath $failure -Force
    exit 9
}
$unsafeFailure = Join-Path $PSScriptRoot '..\\.tmp\\dirty-bootstrap'
if (Test-Path -LiteralPath $unsafeFailure) {
    Remove-Item -LiteralPath $unsafeFailure -Force
    Set-Content -LiteralPath (Join-Path $PSScriptRoot '..\\tracked.txt') -Value 'unexpected change'
    exit 10
}
exit 0
""",
        encoding="utf-8",
    )
    (project / "tracked.txt").write_text("base\n", encoding="utf-8")
    base = commit_all(project, "base")
    git(project, "switch", "-c", "release")
    (project / "tracked.txt").write_text("target\n", encoding="utf-8")
    (project / "release.txt").write_text("v0.2.0\n", encoding="utf-8")
    target = commit_all(project, "release")
    git(project, "tag", "v0.2.0", target)
    git(
        project,
        "update-ref",
        "refs/tg-video-downloader/releases/v0.2.0",
        target,
    )
    git(project, "switch", "master")

    paths = ProjectPaths.from_root(project)
    paths.runtime.mkdir(parents=True)
    paths.temp.mkdir(parents=True)
    paths.logs.mkdir(parents=True)
    runtime_fixture = paths.runtime / "fixture.bin"
    runtime_fixture.write_bytes(bytes(range(256)) * 4)
    external_fixture = tmp_path / "external-fixture.bin"
    external_fixture.write_bytes(b"external user data" * 64)
    return ScriptRepository(
        project,
        paths,
        base,
        target,
        runtime_fixture,
        external_fixture,
        file_hash(runtime_fixture),
        file_hash(external_fixture),
    )


def write_request(repository: ScriptRepository) -> Path:
    write_update_request(
        repository.paths,
        UpdateRequest(
            token="a" * 32,
            tag="v0.2.0",
            base_commit=repository.base_commit,
            target_commit=repository.target_commit,
            restore_service=False,
        ),
    )
    return repository.paths.update_request


def run_updater(project: Path, request: Path) -> subprocess.CompletedProcess[str]:
    if shutil.which("powershell.exe") is None:
        pytest.skip("Windows PowerShell is unavailable")
    return subprocess.run(
        (
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(UPDATE_SCRIPT),
            "-ProjectRoot",
            str(project),
            "-RequestPath",
            str(request),
            "-NoRelaunch",
            "-NoServiceRestart",
            "-SkipImportSmoke",
        ),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def read_result(paths: ProjectPaths) -> dict[str, object]:
    return json.loads(paths.update_result.read_text(encoding="utf-8-sig"))


def test_update_script_has_safe_transaction_contract() -> None:
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")
    required = (
        "[Parameter(Mandatory = $true)][string]$ProjectRoot",
        "[Parameter(Mandatory = $true)][string]$RequestPath",
        "Resolve-Path -LiteralPath",
        "gui.lock",
        "refs/tg-video-downloader/releases/",
        "merge --ff-only",
        "bootstrap.ps1",
        "update-ref",
        "restore --source=HEAD --staged --worktree -- .",
        "update-result.json",
        "restore_service",
        "run-supervisor.ps1",
        "launch-gui.ps1",
    )
    for fragment in required:
        assert fragment in script
    forbidden = (
        "reset --hard",
        "schtasks",
        "Register-ScheduledTask",
        "Remove-Item -Recurse",
    )
    for fragment in forbidden:
        assert fragment not in script


def test_update_script_quotes_relaunch_paths_that_may_contain_spaces() -> None:
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")

    assert (
        "$supervisorArguments = "
        "'-NoProfile -ExecutionPolicy Bypass -File \"{0}\"' -f $supervisorScript"
    ) in script
    assert (
        "$guiArguments = "
        "'-NoProfile -ExecutionPolicy Bypass -File \"{0}\"' -f $guiScript"
    ) in script


def test_update_script_rolls_back_then_succeeds_without_touching_data(
    tmp_path: Path,
) -> None:
    repository = make_update_script_repository(tmp_path)
    (repository.paths.temp / "fail-bootstrap").write_text("fail", encoding="ascii")
    request = write_request(repository)

    failed = run_updater(repository.project, request)

    assert failed.returncode != 0, failed.stderr
    assert git(repository.project, "rev-parse", "HEAD") == repository.base_commit
    assert read_result(repository.paths)["status"] == "rolled_back"
    assert file_hash(repository.runtime_fixture) == repository.fixture_hash
    assert file_hash(repository.external_fixture) == repository.external_hash

    request = write_request(repository)
    succeeded = run_updater(repository.project, request)

    assert succeeded.returncode == 0, succeeded.stderr
    assert git(repository.project, "rev-parse", "HEAD") == repository.target_commit
    assert read_result(repository.paths)["status"] == "success"
    assert file_hash(repository.runtime_fixture) == repository.fixture_hash
    assert file_hash(repository.external_fixture) == repository.external_hash


def test_update_script_refuses_unsafe_rollback_after_dirty_failure(
    tmp_path: Path,
) -> None:
    repository = make_update_script_repository(tmp_path)
    (repository.paths.temp / "dirty-bootstrap").write_text(
        "fail",
        encoding="ascii",
    )

    result = run_updater(repository.project, write_request(repository))

    assert result.returncode != 0
    assert git(repository.project, "rev-parse", "HEAD") == repository.target_commit
    assert read_result(repository.paths)["status"] == "failed"
    assert "tracked.txt" in git(repository.project, "status", "--porcelain")
    assert "unexpected change" in (repository.project / "tracked.txt").read_text(
        encoding="utf-8-sig"
    )
    assert file_hash(repository.runtime_fixture) == repository.fixture_hash
    assert file_hash(repository.external_fixture) == repository.external_hash
