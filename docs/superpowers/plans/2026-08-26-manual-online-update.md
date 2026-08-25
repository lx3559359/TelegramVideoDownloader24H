# Manual Online Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual, stable-tag-only, one-click updater with GitHub-to-ModelScope fallback, searchable change preview, safe rollback, and restoration of the downloader's prior state.

**Architecture:** A lazy-loaded Python manager handles version parsing, remote discovery, Git safety checks, and request/result state. A detached PowerShell copy applies the already-validated fast-forward after GUI shutdown, runs bootstrap, rolls back only its own clean update on failure, relaunches the GUI, and restores the service when appropriate.

**Tech Stack:** Python 3.11+, subprocess/Git, PowerShell 5+, Tkinter/ttk, pystray, JSON, pytest, temporary local Git repositories.

---

## File map

- Create `src/tg_video_downloader/update.py`: versions, releases, command runner, manager, change preview, state files, install preparation.
- Create `tests/test_update.py`: pure and temporary-Git tests.
- Create `scripts/apply-update.ps1`: detached application, bootstrap, rollback, relaunch, state restore.
- Create `tests/test_update_script.py`: script contract and temp-repository integration.
- Modify `src/tg_video_downloader/paths.py`: update request/result/log paths.
- Modify `src/tg_video_downloader/windows.py`: downloader-running probe and detached updater launch.
- Modify `src/tg_video_downloader/gui/controller.py`: async update facade and install preparation.
- Modify `src/tg_video_downloader/gui/app.py`: update panel, search, check, confirmation, result message.
- Modify `src/tg_video_downloader/gui/tray.py` and `runtime.py`: manual update action.
- Modify `pyproject.toml`, `README.md`, diagnostics, Windows-script tests, and verification docs.

### Task 1: Stable semantic versions and remote fallback

**Files:**
- Create: `src/tg_video_downloader/update.py`
- Create: `tests/test_update.py`

- [ ] **Step 1: Write failing version and discovery tests**

```python
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
        self.calls.append(arguments)
        remote_url = arguments[-1]
        if remote_url in self.failures:
            raise subprocess.CalledProcessError(1, arguments, stderr="network error")
        return self.outputs.get(remote_url, "")


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v0.2.0", (0, 2, 0)), ("v10.11.12", (10, 11, 12))],
)
def test_parse_stable_tag(tag: str, expected: tuple[int, int, int]) -> None:
    assert parse_stable_tag(tag).parts == expected


@pytest.mark.parametrize("tag", ["0.2.0", "v1.2", "v1.2.3-rc1", "latest", "v-1.2.3"])
def test_parse_stable_tag_rejects_non_release_tags(tag: str) -> None:
    with pytest.raises(ValueError):
        parse_stable_tag(tag)


def test_github_failure_falls_back_to_modelscope() -> None:
    runner = FakeRunner(
        failures={GITHUB_URL},
        outputs={MODELSCOPE_URL: "abc refs/tags/v0.2.0\n"},
    )
    release = UpdateManager(runner=runner, current_version="0.1.0").check_latest()
    assert release.tag == "v0.2.0"
    assert release.source == "魔塔"


def test_no_remote_is_queried_until_manual_check() -> None:
    runner = FakeRunner()
    UpdateManager(runner=runner, current_version="0.1.0")
    assert runner.calls == []


def test_successful_primary_with_no_higher_stable_tag_does_not_fallback() -> None:
    runner = FakeRunner(
        outputs={
            GITHUB_URL: "a refs/tags/v0.1.0\nb refs/tags/v0.2.0-rc1\n",
            MODELSCOPE_URL: "c refs/tags/v9.0.0\n",
        }
    )
    assert UpdateManager(runner=runner, current_version="0.1.0").check_latest() is None
    assert len(runner.calls) == 1


def test_both_remote_failures_are_reported() -> None:
    runner = FakeRunner(failures={GITHUB_URL, MODELSCOPE_URL})
    with pytest.raises(UpdateCheckError, match="两个更新源"):
        UpdateManager(runner=runner, current_version="0.1.0").check_latest()
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_update.py -q
```

Expected: collection fails because `update.py` does not exist.

- [ ] **Step 3: Implement version and release discovery**

Define:

```python
GITHUB_URL = "https://github.com/lx3559359/TelegramVideoDownloader24H.git"
MODELSCOPE_URL = "https://www.modelscope.cn/studios/lx3559359/TelegramVideoDownloader24H.git"
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
```

Implement discovery with this control flow (the constructor stores a parsed current version and performs no command):

```python
def check_latest(self) -> AvailableRelease | None:
    remotes = (("GitHub", GITHUB_URL), ("魔塔", MODELSCOPE_URL))
    for index, (source, remote_url) in enumerate(remotes):
        try:
            output = self._runner(
                ("git", "ls-remote", "--tags", "--refs", remote_url),
                cwd=self.paths.root if self.paths is not None else None,
            )
        except (OSError, subprocess.CalledProcessError):
            if index == 0:
                continue
            raise UpdateCheckError("两个更新源均不可用")
        releases = []
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
                releases.append(AvailableRelease(candidate, tag, source, remote_url))
        return max(releases, key=lambda item: item.version, default=None)
    return None
```

- [ ] **Step 4: Run tests and verify GREEN**

Run Step 2.

Expected: semantic-version and fallback tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/update.py tests/test_update.py
git commit -m "feat: discover stable online updates"
```

### Task 2: Fetch, validate, and preview a release

**Files:**
- Modify: `src/tg_video_downloader/update.py`
- Modify: `tests/test_update.py`

- [ ] **Step 1: Add temporary-Git failing tests**

Add these complete repository helpers and fixture to `tests/test_update.py`:

```python
def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit_file(repository: Path, relative: str, content: str, message: str) -> str:
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repository, "add", relative)
    git(
        repository,
        "-c", "user.name=Update Test",
        "-c", "user.email=update@example.invalid",
        "commit", "-m", message,
    )
    return git(repository, "rev-parse", "HEAD")


@dataclass(frozen=True)
class UpdateRepository:
    project: Path
    remote: Path
    base_commit: str
    release_commit: str


@pytest.fixture
def update_repository(tmp_path: Path) -> UpdateRepository:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    project = tmp_path / "project"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "master", str(source))
    base = commit_file(source, "src/tg_video_downloader/gui/app.py", "v1\n", "base")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "-u", "origin", "master")
    git(tmp_path, "clone", str(remote), str(project))
    release = commit_file(source, "src/tg_video_downloader/gui/app.py", "v2\n", "app")
    commit_file(source, "README-new.md", "release\n", "readme")
    release = git(source, "rev-parse", "HEAD")
    git(source, "tag", "v0.2.0", release)
    git(source, "push", "origin", "master", "refs/tags/v0.2.0")
    return UpdateRepository(project, remote, base, release)


def test_prepare_release_fetches_validates_and_filters_changes(
    update_repository: UpdateRepository,
) -> None:
    release = AvailableRelease(
        parse_stable_tag("v0.2.0"),
        "v0.2.0",
        "测试远端",
        str(update_repository.remote),
    )
    manager = UpdateManager(
        paths=ProjectPaths.from_root(update_repository.project),
        current_version="0.1.0",
    )
    prepared = manager.prepare_release(release)
    assert prepared.base_commit == update_repository.base_commit
    assert prepared.target_commit == update_repository.release_commit
    assert prepared.changes == (
        ChangedFile("A", "README-new.md"),
        ChangedFile("M", "src/tg_video_downloader/gui/app.py"),
    )
    assert filter_changes(prepared.changes, "GUI") == (prepared.changes[1],)
```

Add explicit safety tests using the fixture:

```python
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda root: (root / "src/tg_video_downloader/gui/app.py").write_text(
                "dirty\n", encoding="utf-8"
            ),
            "工作区不干净",
        ),
        (lambda root: git(root, "switch", "-c", "feature"), "必须位于 master"),
        (lambda root: (root / "untracked.txt").write_text("new", encoding="utf-8"), "工作区不干净"),
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
    release = AvailableRelease(
        parse_stable_tag("v0.2.0"), "v0.2.0", "测试远端", str(update_repository.remote)
    )
    with pytest.raises(UpdateSafetyError, match=message):
        manager.prepare_release(release)


def test_prepared_release_rejects_changed_head(
    update_repository: UpdateRepository,
) -> None:
    manager = UpdateManager(
        paths=ProjectPaths.from_root(update_repository.project),
        current_version="0.1.0",
    )
    release = AvailableRelease(
        parse_stable_tag("v0.2.0"), "v0.2.0", "测试远端", str(update_repository.remote)
    )
    prepared = manager.prepare_release(release)
    commit_file(update_repository.project, "local.txt", "local\n", "local")
    with pytest.raises(UpdateSafetyError, match="HEAD 已变化"):
        manager.validate_prepared(prepared)
```

Add the divergent-target test without mutating the checked-out project:

```python
def test_prepare_release_rejects_divergent_target(
    update_repository: UpdateRepository,
) -> None:
    source = update_repository.remote.parent / "source"
    tree = git(source, "rev-parse", "HEAD^{tree}")
    divergent = git(
        source,
        "-c", "user.name=Update Test",
        "-c", "user.email=update@example.invalid",
        "commit-tree", tree, "-m", "divergent",
    )
    git(source, "tag", "v0.3.0", divergent)
    git(source, "push", "origin", "refs/tags/v0.3.0")
    manager = UpdateManager(
        paths=ProjectPaths.from_root(update_repository.project),
        current_version="0.1.0",
    )
    release = AvailableRelease(
        parse_stable_tag("v0.3.0"), "v0.3.0", "测试远端", str(update_repository.remote)
    )
    with pytest.raises(UpdateSafetyError, match="不是当前版本的快进更新"):
        manager.prepare_release(release)
```

No lifecycle or stop fake is constructed in these tests, proving safety rejection precedes service control.

- [ ] **Step 2: Run update tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_update.py -q
```

Expected: failures cite missing preparation, safety, and change-preview interfaces.

- [ ] **Step 3: Implement repository safety and preview**

Add:

```python
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


def filter_changes(changes: tuple[ChangedFile, ...], query: str) -> tuple[ChangedFile, ...]:
    needle = query.casefold().strip()
    if not needle:
        return changes
    return tuple(item for item in changes if needle in item.path.casefold())


def prepare_latest(self) -> PreparedRelease | None:
    release = self.check_latest()
    return None if release is None else self.prepare_release(release)
```

`prepare_release` must run, in order:

```text
git branch --show-current
git status --porcelain --untracked-files=all
git rev-parse HEAD
git fetch --no-tags <remote_url> refs/tags/<tag>:refs/tags/<tag>
git rev-list -n 1 refs/tags/<tag>
git merge-base --is-ancestor <base> <target>
git diff --name-status -z <base> <target>
```

Parse each `git diff --name-status` line exactly:

```python
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
            index += 1  # old path is preview metadata only
            path = fields[index]
            index += 1
        elif status in {"A", "M", "D", "T"} and index < len(fields):
            path = fields[index]
            index += 1
        else:
            raise UpdateSafetyError("无法解析版本变更文件列表")
        changes.append(ChangedFile(status, path))
    return tuple(changes)
```

Never execute shell strings; pass argument arrays with `shell=False`.

Implement `validate_prepared` as the branch/status/HEAD checks shared by install preparation:

```python
def validate_prepared(self, prepared: PreparedRelease) -> None:
    if prepared.release.version <= self.current_version:
        raise UpdateSafetyError("目标版本不是更高的稳定版本")
    branch = self._run(("git", "branch", "--show-current")).strip()
    if branch != "master":
        raise UpdateSafetyError("在线更新必须位于 master 分支")
    if self._run(("git", "status", "--porcelain", "--untracked-files=all")).strip():
        raise UpdateSafetyError("工作区不干净，拒绝在线更新")
    head = self._run(("git", "rev-parse", "HEAD")).strip()
    if head != prepared.base_commit:
        raise UpdateSafetyError("检查更新后 HEAD 已变化，请重新检查")
    target = self._run(
        ("git", "rev-list", "-n", "1", f"refs/tags/{prepared.release.tag}")
    ).strip()
    if target != prepared.target_commit:
        raise UpdateSafetyError("稳定标签目标已变化，请重新检查")
    try:
        self._run(
            ("git", "merge-base", "--is-ancestor", head, prepared.target_commit)
        )
    except subprocess.CalledProcessError as error:
        raise UpdateSafetyError(
            "目标版本不是当前版本的快进更新"
        ) from error
```

- [ ] **Step 4: Run tests and verify GREEN**

Run Step 2.

Expected: all update-manager tests through release preview pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/update.py tests/test_update.py
git commit -m "feat: preview a safe fast-forward update"
```

### Task 3: Atomic update request/result state

**Files:**
- Modify: `src/tg_video_downloader/paths.py`
- Modify: `src/tg_video_downloader/update.py`
- Modify: `tests/test_paths.py`
- Modify: `tests/test_update.py`

- [ ] **Step 1: Write failing state tests**

```python
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
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_update.py tests\test_paths.py -q
```

Expected: failures cite missing update paths and request/result state helpers.

- [ ] **Step 3: Implement strict dataclasses and atomic JSON**

Add these `ProjectPaths` properties, each built from task-owned runtime/log directories:

```python
@property
def update_request(self) -> Path:
    return self.runtime / "update-request.json"

@property
def update_result(self) -> Path:
    return self.runtime / "update-result.json"

@property
def update_log(self) -> Path:
    return self.logs / "update.log"
```

Define fixed state types:

```python
UPDATE_TOKEN = re.compile(r"^[0-9a-f]{32}$")
COMMIT_ID = re.compile(r"^[0-9a-f]{40}$")


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


def _validate_request(request: UpdateRequest) -> UpdateRequest:
    if UPDATE_TOKEN.fullmatch(request.token) is None:
        raise ValueError("更新令牌格式无效")
    parse_stable_tag(request.tag)
    if COMMIT_ID.fullmatch(request.base_commit) is None:
        raise ValueError("基础提交格式无效")
    if COMMIT_ID.fullmatch(request.target_commit) is None:
        raise ValueError("目标提交格式无效")
    if not isinstance(request.restore_service, bool):
        raise ValueError("服务恢复状态格式无效")
    return request


def write_update_request(paths: ProjectPaths, request: UpdateRequest) -> None:
    checked = _validate_request(request)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    temporary = paths.runtime / f".{paths.update_request.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(asdict(checked), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, paths.update_request)
    finally:
        temporary.unlink(missing_ok=True)


def read_update_request(paths: ProjectPaths) -> UpdateRequest:
    raw = json.loads(paths.update_request.read_text(encoding="utf-8"))
    expected = {"token", "tag", "base_commit", "target_commit", "restore_service"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("更新请求字段无效")
    return _validate_request(UpdateRequest(**raw))
```

Implement result validation and consumption:

```python
def _validate_result(result: UpdateResult) -> UpdateResult:
    if UPDATE_TOKEN.fullmatch(result.token) is None:
        raise ValueError("更新结果令牌格式无效")
    parse_stable_tag(result.tag)
    if result.status not in {"success", "rolled_back", "failed"}:
        raise ValueError("更新结果状态无效")
    datetime.fromisoformat(result.completed_at)
    if not isinstance(result.message, str) or len(result.message) > 500:
        raise ValueError("更新结果摘要无效")
    return result


def write_update_result(paths: ProjectPaths, result: UpdateResult) -> None:
    checked = _validate_result(result)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    temporary = paths.runtime / f".{paths.update_result.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(asdict(checked), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, paths.update_result)
    finally:
        temporary.unlink(missing_ok=True)


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
```

Use this exact request shape:

```json
{
    "token": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "tag": "v0.2.0",
    "base_commit": "1111111111111111111111111111111111111111",
    "target_commit": "2222222222222222222222222222222222222222",
  "restore_service": true
}
```

- [ ] **Step 4: Run tests and verify GREEN**

Run Step 2.

Expected: all update and path tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/paths.py src/tg_video_downloader/update.py tests/test_paths.py tests/test_update.py
git commit -m "feat: persist validated update state"
```

### Task 4: Graceful preparation and detached launcher

**Files:**
- Modify: `src/tg_video_downloader/windows.py`
- Modify: `src/tg_video_downloader/gui/controller.py`
- Modify: `src/tg_video_downloader/update.py`
- Modify: `tests/test_windows.py`
- Modify: `tests/test_gui_controller.py`
- Modify: `tests/test_update.py`

- [ ] **Step 1: Write failing lifecycle tests**

Add controller coverage with explicit injected fakes:

```python
@pytest.mark.asyncio
async def test_prepare_update_stops_only_a_previously_running_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, paths, _, process = make_controller(tmp_path)
    prepared = SimpleNamespace(tag="v0.2.0")
    installs: list[tuple[object, bool]] = []
    controller.update_manager = SimpleNamespace(
        validate_prepared=lambda _value: None,
        prepare_install=lambda value, restore: installs.append((value, restore))
    )
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.downloader_is_running",
        lambda _paths: True,
    )
    waited: list[Path] = []
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.wait_for_downloader_stop",
        lambda value, timeout_seconds=30: waited.append(value),
    )

    await controller.prepare_update_install(prepared)

    assert process.actions == ["stop"]
    assert waited == [paths]
    assert installs == [(prepared, True)]


@pytest.mark.asyncio
async def test_prepare_update_does_not_stop_an_already_stopped_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _, _, process = make_controller(tmp_path)
    prepared = SimpleNamespace(tag="v0.2.0")
    installs: list[tuple[object, bool]] = []
    controller.update_manager = SimpleNamespace(
        validate_prepared=lambda _value: None,
        prepare_install=lambda value, restore: installs.append((value, restore))
    )
    monkeypatch.setattr(
        "tg_video_downloader.gui.controller.downloader_is_running",
        lambda _paths: False,
    )
    await controller.prepare_update_install(prepared)
    assert process.actions == []
    assert installs == [(prepared, False)]
```

Add `tests/test_windows.py` cases with injected monotonic/sleep functions showing `wait_for_downloader_stop` returns after the lock probe turns false and raises `TimeoutError("30 秒")` when it stays true. In `tests/test_update.py`, patch `launch_update_executor` to capture arguments and assert `prepare_install` writes a request only after `validate_prepared`, copies exactly `scripts/apply-update.ps1` to `.tmp/update-<token>/apply-update.ps1`, and passes that copy plus `paths.update_request` to the launcher. Add an active-login controller test that expects `ValueError("登录")` and no process actions.

- [ ] **Step 2: Run tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_update.py tests\test_windows.py tests\test_gui_controller.py -q
```

Expected: failures cite missing lifecycle probes, lazy manager facade, or detached launcher.

- [ ] **Step 3: Implement running probe and controller facade**

Expose:

```python
def downloader_is_running(paths: ProjectPaths) -> bool:
    return _file_is_locked(paths.runtime / "downloader.lock")


def wait_for_downloader_stop(
    paths: ProjectPaths,
    timeout_seconds: float = 30.0,
    *,
    monotonic: Callable[[], float] = monotonic_clock,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout_seconds
    while downloader_is_running(paths):
        if monotonic() >= deadline:
            raise TimeoutError("后台下载器未在 30 秒内停止")
        sleep(0.1)


def launch_update_executor(
    project_root: Path,
    executor: Path,
    request_path: Path,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        (
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(executor), "-ProjectRoot", str(project_root),
            "-RequestPath", str(request_path),
        ),
        cwd=project_root,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
```

Add async controller methods:

```python
def _get_update_manager(self) -> "UpdateManager":
    if self.update_manager is None:
        from tg_video_downloader.update import UpdateManager

        self.update_manager = UpdateManager(paths=self.paths)
    return self.update_manager


async def check_for_update(self) -> PreparedRelease | None:
    return await asyncio.to_thread(self._get_update_manager().prepare_latest)


async def prepare_update_install(self, prepared: PreparedRelease) -> None:
    if self.login_active:
        raise ValueError("请先完成或取消当前登录任务")
    manager = self._get_update_manager()
    await asyncio.to_thread(manager.validate_prepared, prepared)
    restore_service = downloader_is_running(self.paths)
    if restore_service:
        self.process_control.request_stop(self.paths)
        try:
            await asyncio.to_thread(wait_for_downloader_stop, self.paths)
        except Exception:
            self.process_control.clear_stop(self.paths)
            if not downloader_is_running(self.paths):
                self.process_control.start(self.paths.root)
            raise
    try:
        await asyncio.to_thread(
            manager.prepare_install,
            prepared,
            restore_service,
        )
    except Exception:
        if restore_service:
            self.process_control.clear_stop(self.paths)
            self.process_control.start(self.paths.root)
        raise
```

Initialize `self.update_manager = None` in the controller constructor and keep update-only types behind `TYPE_CHECKING`. Thus ordinary GUI startup imports no updater module and runs no Git command; the first manual check performs the lazy import.

Implement preparation without touching the service a second time:

```python
def prepare_install(
    self,
    prepared: PreparedRelease,
    restore_service: bool,
) -> UpdateRequest:
    self.validate_prepared(prepared)
    token = secrets.token_hex(16)
    request = UpdateRequest(
        token=token,
        tag=prepared.release.tag,
        base_commit=prepared.base_commit,
        target_commit=prepared.target_commit,
        restore_service=restore_service,
    )
    executor_dir = self.paths.assert_within_root(self.paths.temp / f"update-{token}")
    executor_dir.mkdir(parents=True, exist_ok=False)
    executor = self.paths.assert_within_root(executor_dir / "apply-update.ps1")
    shutil.copy2(self.paths.root / "scripts" / "apply-update.ps1", executor)
    write_update_request(self.paths, request)
    launcher = self._launch_update
    if launcher is None:
        from tg_video_downloader.windows import launch_update_executor

        launcher = launch_update_executor
    launcher(self.paths.root, executor, self.paths.update_request)
    return request
```

Initialize `_launch_update` to `launch_update_executor` but allow injection in tests. Wrap copy/write/launch in `try/except`; if launch raises before returning, unlink only the copied executor, remove its now-empty token directory, and unlink `update-request.json` only after `read_update_request` proves its token equals the local token. Once `Popen` returns, treat the request as owned by the detached updater and perform no cleanup from the GUI process.

- [ ] **Step 4: Run tests and verify GREEN**

Run Step 2.

Expected: all update, Windows, and GUI controller tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/windows.py src/tg_video_downloader/gui/controller.py src/tg_video_downloader/update.py tests/test_windows.py tests/test_gui_controller.py tests/test_update.py
git commit -m "feat: prepare a graceful detached update"
```

### Task 5: Apply, rollback, relaunch, and restore service state

**Files:**
- Create: `scripts/apply-update.ps1`
- Create: `tests/test_update_script.py`
- Modify: `tests/test_windows_scripts.py`

- [ ] **Step 1: Write failing script-contract tests**

Add this static contract test:

```python
UPDATE_SCRIPT = Path("scripts/apply-update.ps1")


def test_update_script_has_safe_transaction_contract() -> None:
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")
    required = (
        "[Parameter(Mandatory = $true)][string]$ProjectRoot",
        "[Parameter(Mandatory = $true)][string]$RequestPath",
        "Resolve-Path -LiteralPath",
        "gui.lock",
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
    forbidden = ("reset --hard", "schtasks", "Register-ScheduledTask", "Remove-Item -Recurse")
    for fragment in forbidden:
        assert fragment not in script
```

Add a `make_update_script_repository(tmp_path)` helper that initializes `master`, commits `.gitignore` entries for `.runtime/`, `.tmp/`, `logs/`, and `.venv/`, and commits this deterministic bootstrap in both base and target:

```powershell
param()
$failure = Join-Path $PSScriptRoot '..\.tmp\fail-bootstrap'
if (Test-Path -LiteralPath $failure) {
    Remove-Item -LiteralPath $failure -Force
    exit 9
}
$unsafeFailure = Join-Path $PSScriptRoot '..\.tmp\dirty-bootstrap'
if (Test-Path -LiteralPath $unsafeFailure) {
    Remove-Item -LiteralPath $unsafeFailure -Force
    Set-Content -LiteralPath (Join-Path $PSScriptRoot '..\tracked.txt') -Value 'unexpected change'
    exit 10
}
exit 0
```

The helper tags the target `v0.2.0`, creates ignored `.runtime/fixture.bin`, and returns base/target IDs. Invoke the copied script with:

```python
def run_updater(project: Path, request: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(Path("scripts/apply-update.ps1").resolve()),
            "-ProjectRoot", str(project),
            "-RequestPath", str(request),
            "-NoRelaunch", "-NoServiceRestart", "-SkipImportSmoke",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
```

First create `.tmp/fail-bootstrap`, run the updater, and assert `git rev-parse HEAD == base_commit`, result status is `rolled_back`, and the SHA-256 of `.runtime/fixture.bin` is unchanged. The fixture bootstrap consumes only that test marker, so rewrite the same request and rerun; assert `HEAD == target_commit`, result status is `success`, and the fixture hash is still unchanged.

In a fresh fixture, create `.tmp/dirty-bootstrap` and run once. Assert the result is `failed`, `HEAD` remains the target commit, `git status --porcelain` reports `tracked.txt`, and its changed contents remain; this proves the updater refuses a destructive rollback when post-merge state is no longer exactly its own clean target.

- [ ] **Step 2: Run script tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_update_script.py tests\test_windows_scripts.py -q
```

Expected: collection or contract tests fail because `apply-update.ps1` is missing.

- [ ] **Step 3: Implement the PowerShell updater**

The script must use `Resolve-Path -LiteralPath`, verify the project root contains `.git`, parse JSON with `ConvertFrom-Json`, enforce regexes `^v\d+\.\d+\.\d+$` and `^[0-9a-f]{40}$`, and execute these stages:

```powershell
function Write-UpdateLog([string]$Message) {
    $line = "{0} {1}" -f ([DateTimeOffset]::UtcNow.ToString('o')), $Message
    Add-Content -LiteralPath (Join-Path $ProjectRoot 'logs\update.log') -Value $line -Encoding UTF8
}
```

Log only fixed stage names, tag, commit IDs, and sanitized exception types/messages; never serialize environment variables, request JSON, credentials, Telegram data, or directory listings.

```powershell
& git -C $ProjectRoot rev-parse HEAD
& git -C $ProjectRoot status --porcelain --untracked-files=all
& git -C $ProjectRoot rev-list -n 1 ("refs/tags/{0}" -f $Request.tag)
& git -C $ProjectRoot merge --ff-only $Request.target_commit
& (Join-Path $ProjectRoot 'scripts\bootstrap.ps1')
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -c "import cryptg, pystray, PIL, tg_video_downloader"
```

On post-merge failure, only when `HEAD` equals the target and status is clean:

```powershell
& git -C $ProjectRoot update-ref refs/heads/master $Request.base_commit $Request.target_commit
& git -C $ProjectRoot restore --source=HEAD --staged --worktree -- .
& (Join-Path $ProjectRoot 'scripts\bootstrap.ps1')
```

Require the resolved request path to equal `<ProjectRoot>/.runtime/update-request.json`. Compare the JSON property-name set exactly to `token`, `tag`, `base_commit`, `target_commit`, and `restore_service`; require `restore_service` to be a Boolean and the token to match `^[0-9a-f]{32}$` in addition to the tag/commit regexes. Before merging, require current `HEAD == base_commit`, clean porcelain output, current branch `master`, and the peeled request tag `== target_commit`. Wait up to 30 seconds for `.runtime/gui.lock` to unlock. Wrap external commands in `Invoke-Checked` so every nonzero `$LASTEXITCODE` throws. Declare `-NoRelaunch`, `-NoServiceRestart`, and `-SkipImportSmoke` as test-only switches; production launch never passes them, and `-SkipImportSmoke` skips only the import command after a successful bootstrap.

Write `update-result.json` through `update-result.<token>.tmp` and `[System.IO.File]::Replace` when the destination exists or `[System.IO.File]::Move` otherwise. In `finally`, start `run-supervisor.ps1` hidden only when `restore_service` is true and `-NoServiceRestart` is absent, then call `launch-gui.ps1` unless `-NoRelaunch` is present. Set the process exit code to 0 only for `success`, and nonzero for `rolled_back` or `failed`. Never delete or enumerate user download roots.

- [ ] **Step 4: Run script tests and verify GREEN**

Run Step 2.

Expected: static contract, success, rollback, and data-preservation tests pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/apply-update.ps1 tests/test_update_script.py tests/test_windows_scripts.py
git commit -m "feat: apply and roll back online updates"
```

### Task 6: Add update UI and tray action

**Files:**
- Modify: `src/tg_video_downloader/gui/app.py`
- Modify: `src/tg_video_downloader/gui/tray.py`
- Modify: `src/tg_video_downloader/gui/runtime.py`
- Modify: `tests/test_gui_app.py`
- Modify: `tests/test_tray.py`
- Modify: `tests/test_gui_runtime.py`

- [ ] **Step 1: Write failing UI and routing tests**

Add focused app callback tests using `object.__new__(DownloaderApp)`, `FakeVar`, `FakeButton`, and a `FakeTree` whose `get_children`, `delete`, and `insert` calls are recorded:

```python
def test_application_version_reads_package_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tg_video_downloader.gui.app.version", lambda _name: "0.2.0")
    assert application_version() == "0.2.0"


def test_show_prepared_release_enables_install_and_lists_changes() -> None:
    app = object.__new__(DownloaderApp)
    app.update_status_var = FakeVar()
    app.update_search_var = FakeVar("")
    app.update_install_button = FakeButton()
    app.update_changes = FakeTree()
    app._prepared_release = None
    prepared = PreparedRelease(
        release=AvailableRelease(
            parse_stable_tag("v0.2.0"), "v0.2.0", "GitHub", "https://example.invalid/repo.git"
        ),
        base_commit="1" * 40,
        target_commit="2" * 40,
        changes=(ChangedFile("M", "src/gui/app.py"), ChangedFile("A", "README.md")),
    )
    app._show_prepared_release(prepared)
    assert app._prepared_release is prepared
    assert "v0.2.0" in app.update_status_var.get()
    assert "GitHub" in app.update_status_var.get()
    assert app.update_install_button.states == ["!disabled"]
    assert app.update_changes.rows == [("M", "src/gui/app.py"), ("A", "README.md")]


def test_successful_install_preparation_requests_update_exit() -> None:
    app = object.__new__(DownloaderApp)
    exits: list[bool] = []
    app._request_update_exit = lambda: exits.append(True)
    app._handle_update_install_success(None)
    assert exits == [True]


def test_failed_install_does_not_request_update_exit() -> None:
    app = object.__new__(DownloaderApp)
    exits: list[bool] = []
    shown: list[str] = []
    app._request_update_exit = lambda: exits.append(True)
    app._show_error = lambda error: shown.append(str(error))
    app._handle_update_install_error(RuntimeError("dirty tree"))
    assert exits == []
    assert shown == ["dirty tree"]
```

Add the no-update callback test asserting `update_status_var == "当前已是最新稳定版"`, install stays disabled, and the changes tree is cleared. Add a confirmation test by patching `messagebox.askyesno`; assert its message contains tag, source, changed-file count, and “停止后恢复”. Existing `_run_async` tests must prove the check button is disabled until the future completes and re-enabled on both success and failure.

Extend `TrayActions` and `make_actions` with `check_update: Callable[[], None]`, add `("check_update", "update")` to the existing callback parametrization, and assert the pystray menu text contains `"检查更新"`. Extend `FakeApp` with counters and these methods:

```python
def show_update_page(self) -> None:
    self.update_page_calls += 1

def _check_for_update(self) -> None:
    self.update_check_calls += 1

def set_update_exit(self, callback) -> None:
    self.update_exit = callback
```

In the runtime test, call `captured["tray"].actions.check_update()` and assert the window records `deiconify/lift/focus` followed by one page selection and one check call.

- [ ] **Step 2: Run GUI/tray tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_app.py tests\test_tray.py tests\test_gui_runtime.py -q
```

Expected: failures cite missing update widgets, callbacks, and tray action.

- [ ] **Step 3: Build the lazy update panel**

Add this metadata helper, then in the run page add current version, status, check button, search `StringVar`, `Treeview` with status/path columns, and disabled install button. Import update types only inside update callbacks or behind `TYPE_CHECKING`.

```python
def application_version() -> str:
    try:
        return version("telegram-video-downloader")
    except PackageNotFoundError:
        return "unknown"
```

Implement:

```python
def show_update_page(self) -> None:
    self.notebook.select(self.run_page)


def _check_for_update(self) -> None:
    self._run_async(
        self.controller.check_for_update(),
        self.update_check_button,
        self._show_prepared_release,
    )


def set_update_exit(self, callback: Callable[[], None]) -> None:
    self._request_update_exit = callback


def _handle_update_install_success(self, _result: object) -> None:
    self.update_status_var.set("更新器已启动，配置器即将重启")
    self._request_update_exit()
```

Installation confirmation must include target tag, source, changed-file count, and the statement that a running downloader will stop and resume. After `prepare_update_install` succeeds, call a runtime-injected `request_update_exit` callback so normal GUI cleanup releases `gui.lock` without issuing another service stop.

Consume and show the detached updater result only at GUI startup:

```python
# GuiController: avoid importing update.py on ordinary startups.
def consume_update_result(self) -> "UpdateResult | None":
    if not self.paths.update_result.is_file():
        return None
    from tg_video_downloader.update import consume_update_result

    return consume_update_result(self.paths)


# DownloaderApp.__init__, after widgets exist.
result = self.controller.consume_update_result()
if result is not None:
    title = "更新完成" if result.status == "success" else "更新未完成"
    messagebox.showinfo(title, result.message, parent=self)
```

Add “检查更新” to the tray menu. In `runtime.py`, define `check_update()` to call `show_window()`, `app.show_update_page()`, and `app._check_for_update()`; pass it to `TrayActions`, then call `app.set_update_exit(quit_ui)` immediately after `quit_ui` is defined. All tray callbacks continue to use the existing `root.after(0, callback)` marshalling.

- [ ] **Step 4: Run GUI/tray tests and verify GREEN**

Run Step 2.

Expected: all GUI, tray, and runtime tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/gui/app.py src/tg_video_downloader/gui/tray.py src/tg_video_downloader/gui/runtime.py tests/test_gui_app.py tests/test_tray.py tests/test_gui_runtime.py
git commit -m "feat: check and install updates from the GUI"
```

### Task 7: Diagnostics, version, docs, and simulated update

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/tg_video_downloader/diagnostics.py`
- Modify: `tests/test_diagnostics.py`
- Modify: `README.md`
- Modify: `docs/verification.md`

- [ ] **Step 1: Bump and expose the release version**

Set `version = "0.2.0"` in `pyproject.toml`. Add this local-only diagnostic and include it after `tray_icon`:

```python
def _check_update_support(self) -> DiagnosticCheck:
    git = shutil.which("git")
    powershell = shutil.which("powershell.exe")
    script = self.paths.root / "scripts" / "apply-update.ps1"
    if git is None or powershell is None or not script.is_file():
        missing = [
            name
            for name, present in (
                ("Git", git is not None),
                ("PowerShell", powershell is not None),
                ("apply-update.ps1", script.is_file()),
            )
            if not present
        ]
        return DiagnosticCheck("update_support", "warning", "在线更新不可用：" + "、".join(missing))
    branch = subprocess.run(
        (git, "-C", str(self.paths.root), "branch", "--show-current"),
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()
    package_version = version("telegram-video-downloader")
    status: DiagnosticStatus = "pass" if branch == "master" else "warning"
    return DiagnosticCheck(
        "update_support",
        status,
        f"手动更新组件可用，版本 {package_version}，分支 {branch or '-'}",
    )
```

Update diagnostics key/count expectations. A temporary project that is not a Git repository should receive only an `update_support` warning; existing independent checks must still execute.

- [ ] **Step 2: Add docs and tests**

Add this no-network regression test:

```python
def test_update_diagnostic_never_queries_a_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(arguments, **_kwargs):
        calls.append(tuple(str(item) for item in arguments))
        return SimpleNamespace(stdout="master\n", returncode=0)

    monkeypatch.setattr("tg_video_downloader.diagnostics.shutil.which", lambda name: name)
    monkeypatch.setattr("tg_video_downloader.diagnostics.subprocess.run", fake_run)
    paths = ProjectPaths.from_root(tmp_path)
    (paths.root / "scripts").mkdir(parents=True)
    (paths.root / "scripts" / "apply-update.ps1").write_text("param()\n", encoding="utf-8")
    Doctor(paths, gateway_factory=lambda *_: FakeTelegramGateway())._check_update_support()
    assert calls
    assert all("ls-remote" not in call for call in calls)
```

Add this README text under “在线更新” and mirror the tested details in `docs/verification.md`:

```markdown
在线更新只在你点击“检查更新”时联网，优先读取 GitHub，连接失败时改用魔塔。程序只接受 `vX.Y.Z` 稳定标签；安装前会拒绝非 `master`、本地改动、未跟踪文件或非快进版本。文件搜索仅用于查看改动，安装始终是完整版本，不能选择部分文件。若后台原本在运行，更新器会正常停止它，成功或回滚后再恢复；日志位于 `logs/update.log`。

`v0.1.0` 还没有内置更新器，需要按原方式手动升级一次到 `v0.2.0`；从 `v0.2.0` 开始，后续稳定版本可在工具内一键更新。
```

- [ ] **Step 3: Run focused and full verification**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_update.py tests\test_update_script.py tests\test_diagnostics.py tests\test_windows_scripts.py -q
& .\scripts\check.ps1
```

- [ ] **Step 4: Simulate v0.1.0 to v0.2.0 in a disposable clone**

Run the integration helper from `tests/test_update_script.py` against `.tmp/update-acceptance`, first with `.tmp/fail-bootstrap` and then without it. Record base/target/rolled-back/success commits from:

```powershell
git -C .tmp\update-acceptance rev-parse HEAD
Get-FileHash -Algorithm SHA256 .tmp\update-acceptance\.runtime\fixture.bin
Get-FileHash -Algorithm SHA256 .tmp\update-external-fixture.bin
Get-Content -Raw .tmp\update-acceptance\.runtime\update-result.json
```

The first HEAD must equal the v0.1.0 base, the second must equal the v0.2.0 target, and both fixture hashes must remain identical.

- [ ] **Step 5: Perform real Windows UI acceptance**

Launch the synthetic GUI fixture from the disposable `.tmp/update-acceptance` clone while it is clean and on `master` (not from the feature worktree). Its base commit must already contain the updater but report test version `0.1.0`; this tests the mechanism without claiming that the historical published v0.1.0 had this feature. Verify no network activity before clicking, source fallback with an intentionally invalid GitHub URL in that test instance, searchable changes, running-service stop/resume, result message, and tray action. Record actual tags, commits, state, and log path without credentials or Telegram filenames.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml src/tg_video_downloader/diagnostics.py tests/test_diagnostics.py README.md docs/verification.md
git commit -m "docs: prepare the v0.2.0 updater release"
```

### Task 8: Merge and publish v0.2.0 to both remotes

**Files:**
- Modify: `docs/verification.md`

- [ ] **Step 1: Run code review and final completion gate**

Use `requesting-code-review`, address all Critical and Important findings, then run:

```powershell
& .\scripts\check.ps1
git diff --check
git status --short
```

Expected: full check exits 0, diff check is empty, and status is clean.

Append the actual full-test count, dependency versions, storage/progress/update acceptance results, candidate commit, and final downloader PID/heartbeat to `docs/verification.md`, then commit before creating the release tag:

```powershell
git add docs/verification.md
git commit -m "docs: record v0.2.0 release verification"
& .\scripts\check.ps1
git status --short
```

Expected: the second full check exits 0 and status is clean. This documentation commit is part of v0.2.0.

- [ ] **Step 2: Fast-forward local master**

Close the test configurator so its GUI lock is released. From the main repository root:

```powershell
git switch master
git merge --ff-only codex/windows-system-tray
& .\scripts\bootstrap.ps1
& .\scripts\check.ps1
```

Expected: merge and verification succeed on `master`.

- [ ] **Step 3: Create stable annotated tags**

Create `v0.1.0` at the previously published commit `19728a51f0fdffcb9b931a9c7728f9c49b13fb5a` and `v0.2.0` at verified `master`:

```powershell
git tag -a v0.1.0 19728a51f0fdffcb9b931a9c7728f9c49b13fb5a -m "Telegram 视频自动下载器 v0.1.0"
git tag -a v0.2.0 master -m "Telegram 视频自动下载器 v0.2.0"
```

Before creating, verify neither tag exists locally or remotely; if one exists, compare its peeled commit and stop on mismatch rather than replacing it.

```powershell
git rev-parse --verify --quiet 'refs/tags/v0.1.0^{}'
git rev-parse --verify --quiet 'refs/tags/v0.2.0^{}'
git ls-remote github 'refs/tags/v0.1.0^{}' 'refs/tags/v0.2.0^{}'
git ls-remote modelscope 'refs/tags/v0.1.0^{}' 'refs/tags/v0.2.0^{}'
```

Exit code 1 with no output from the local probes and empty remote output means creation is safe. Any returned tag must peel to the intended commit; otherwise stop without using `-f`.

- [ ] **Step 4: Publish branch and tags**

```powershell
git push github master
git push github v0.1.0 v0.2.0
git push modelscope master
git push modelscope v0.1.0 v0.2.0
```

If GitHub CLI is installed and authenticated, create release notes for `v0.2.0` without changing the tag; otherwise the annotated tag remains the release source used by the updater.

- [ ] **Step 5: Verify both published refs**

```powershell
git ls-remote github refs/heads/master refs/tags/v0.1.0 'refs/tags/v0.1.0^{}' refs/tags/v0.2.0 'refs/tags/v0.2.0^{}'
git ls-remote modelscope refs/heads/master refs/tags/v0.1.0 'refs/tags/v0.1.0^{}' refs/tags/v0.2.0 'refs/tags/v0.2.0^{}'
```

Expected: both `master` refs equal local `master`; peeled tag commits equal the intended v0.1.0 and v0.2.0 commits on both remotes.

- [ ] **Step 6: Verify the preserved running state**

Read `.runtime/downloader.lock` and `.runtime/heartbeat.json` without modifying either. If the downloader was running before publication, require a live PID and a fresh heartbeat; if it was stopped, require that publication did not start it. Record this post-publication observation in the task handoff only—the immutable `v0.2.0` commit already contains all pre-release evidence.

- [ ] **Step 7: Clean the merged worktree**

Only after both remote refs are verified, remove the exact `.worktrees/windows-system-tray` worktree from the main root, prune registrations, and delete the merged feature branch. Do not remove the other `codex/download-policy-progress-resume` worktree.
