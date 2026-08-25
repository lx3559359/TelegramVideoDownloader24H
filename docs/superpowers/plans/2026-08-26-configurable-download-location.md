# Configurable Download Location Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users choose any writable local Windows folder for future videos while preserving old files, bound resumable jobs, and all project control data.

**Architecture:** Add a focused storage-path module, persist an optional default root in configuration and a per-job bound root in SQLite, then make the worker derive final and partial paths from that binding. GUI, doctor, and directory-opening behavior use the current default root; destructive operations remain restricted to task-owned paths.

**Tech Stack:** Python 3.11+, pathlib, SQLite, Tkinter/ttk, TOML, pytest, Windows local filesystem.

---

## File map

- Create `src/tg_video_downloader/storage.py`: parse, validate, probe, and contain user-selected download roots and task paths.
- Create `tests/test_storage.py`: local/UNC/protected-path and generated-path containment tests.
- Modify `src/tg_video_downloader/models.py`: add config default and job-bound output roots.
- Modify `src/tg_video_downloader/config.py`: backward-compatible TOML load/save.
- Modify `src/tg_video_downloader/state.py`: migrate and bind `jobs.output_root`.
- Modify `src/tg_video_downloader/naming.py`: generate final paths under an explicit root.
- Modify `src/tg_video_downloader/worker.py`: bind roots, use destination-local partials, migrate legacy partials, and check the correct volume.
- Modify `src/tg_video_downloader/service.py`: expose hot-reloaded default root to the worker.
- Modify `src/tg_video_downloader/gui/controller.py`: load/save/open the selected root without losing group policy.
- Modify `src/tg_video_downloader/gui/app.py`: folder entry, picker, save, and open controls.
- Modify `src/tg_video_downloader/diagnostics.py`: validate the configured root and disk.
- Modify focused tests and `README.md`.

### Task 1: Download-root parsing and configuration

**Files:**
- Create: `src/tg_video_downloader/storage.py`
- Create: `tests/test_storage.py`
- Modify: `src/tg_video_downloader/models.py`
- Modify: `src/tg_video_downloader/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing storage and config tests**

Add tests with these concrete assertions:

```python
def test_old_config_uses_project_downloads(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.config.write_text("config_poll_seconds = 5\n", encoding="utf-8")
    config = ConfigStore(paths).load_config()
    assert effective_download_root(paths, config) == paths.downloads


def test_config_round_trips_external_download_root(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path / "project")
    selected = (tmp_path / "media").resolve()
    paths.ensure_directories()
    ConfigStore(paths).save_config(AppConfig(download_root=selected))
    assert ConfigStore(paths).load_config().download_root == selected


@pytest.mark.parametrize("value", [r"relative\folder", r"\\server\share"])
def test_invalid_download_root_is_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError):
        parse_download_root(ProjectPaths.from_root(tmp_path), value)


def test_project_control_directory_is_rejected(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    with pytest.raises(ValueError, match="运行目录"):
        parse_download_root(paths, str(paths.runtime))
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_storage.py tests\test_config.py -q
```

Expected: collection or assertions fail because `storage.py` and `download_root` do not exist.

- [ ] **Step 3: Implement the storage parser and config field**

Create the module with these public interfaces:

```python
if TYPE_CHECKING:
    from tg_video_downloader.models import AppConfig


PROTECTED_PROJECT_DIRECTORIES = (
    ".git", ".venv", ".runtime", ".cache", ".tmp", "logs"
)


def parse_download_root(paths: ProjectPaths, value: str | Path) -> Path:
    raw = str(value).strip()
    if raw.startswith(("\\\\", "//")):
        raise ValueError("下载目录不支持 UNC 网络共享路径")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError("下载目录必须是本地磁盘绝对路径")
    resolved = candidate.resolve()
    protected = tuple((paths.root / name).resolve() for name in PROTECTED_PROJECT_DIRECTORIES)
    if any(resolved == item or resolved.is_relative_to(item) for item in protected):
        raise ValueError("下载目录不能位于项目运行目录中")
    return resolved


def effective_download_root(paths: ProjectPaths, config: "AppConfig") -> Path:
    return config.download_root or paths.downloads


def require_writable_download_root(paths: ProjectPaths, value: str | Path) -> Path:
    root = parse_download_root(paths, value)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("下载保存位置不是文件夹")
    probe = root / f".tg-video-downloader-write-{os.getpid()}-{uuid4().hex}"
    try:
        probe.write_text("ok", encoding="ascii")
    finally:
        probe.unlink(missing_ok=True)
    return root
```

Import `Path` in `models.py` and add `download_root: Path | None = None` to `AppConfig`. In `ConfigStore.load_config`, use `None` when the TOML key is absent and otherwise require a string before calling `parse_download_root`. In `save_config`, insert `download_root = {_quoted(str(config.download_root))}` before group tables when non-null. Do not create external directories during ordinary config parsing; the `TYPE_CHECKING` import above avoids a `config`/`storage`/`models` cycle.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all storage and config tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/storage.py src/tg_video_downloader/models.py src/tg_video_downloader/config.py tests/test_storage.py tests/test_config.py
git commit -m "feat: configure an external download root"
```

### Task 2: Persist the output root per job

**Files:**
- Modify: `src/tg_video_downloader/models.py`
- Modify: `src/tg_video_downloader/state.py`
- Modify: `tests/test_state.py`

- [ ] **Step 1: Write failing migration and binding tests**

```python
def test_existing_database_adds_output_root_column(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE jobs (
            chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            group_title TEXT NOT NULL, source TEXT NOT NULL,
            priority INTEGER NOT NULL, status TEXT NOT NULL,
            message_date TEXT NOT NULL, mime_type TEXT,
            original_name TEXT, extension TEXT NOT NULL,
            expected_size INTEGER, is_video INTEGER NOT NULL,
            is_animated INTEGER NOT NULL, is_round INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,
            final_path TEXT, error TEXT,
            PRIMARY KEY(chat_id, message_id)
        )
        """
    )
    connection.commit()
    connection.close()
    store = StateStore(database)
    columns = {row[1] for row in store._connection.execute("PRAGMA table_info(jobs)")}
    assert "output_root" in columns
    store.close()


def test_bind_output_root_is_first_writer_wins(
    tmp_path: Path,
    store: StateStore,
    live_message: MessageInfo,
) -> None:
    store.upsert_job(live_message, "群", JobSource.LIVE)
    job = store.claim_next()
    assert job is not None
    first = store.bind_output_root(job, tmp_path / "first")
    second = store.bind_output_root(first, tmp_path / "second")
    assert first.output_root == (tmp_path / "first").resolve()
    assert second.output_root == first.output_root
    assert store.get_job(job.chat_id, job.message_id) == second
```

- [ ] **Step 2: Run the state tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_state.py -q
```

Expected: failures cite missing `output_root` and `bind_output_root`.

- [ ] **Step 3: Implement schema migration and atomic binding**

Add `output_root TEXT` to `SCHEMA`, migrate old `jobs` tables using `PRAGMA table_info(jobs)`, and add `output_root: Path | None = None` to `DownloadJob`.

Implement:

```python
def bind_output_root(self, job: DownloadJob, root: Path) -> DownloadJob:
    resolved = root.resolve()
    with self._connection:
        self._connection.execute(
            """
            UPDATE jobs SET output_root = ?
            WHERE chat_id = ? AND message_id = ? AND output_root IS NULL
            """,
            (str(resolved), job.chat_id, job.message_id),
        )
        row = self._connection.execute(
            "SELECT output_root FROM jobs WHERE chat_id = ? AND message_id = ?",
            (job.chat_id, job.message_id),
        ).fetchone()
    if row is None or row["output_root"] is None:
        raise RuntimeError("下载任务输出目录绑定失败")
    return replace(job, output_root=Path(str(row["output_root"])).resolve())


def get_job(self, chat_id: int, message_id: int) -> DownloadJob | None:
    row = self._connection.execute(
        "SELECT * FROM jobs WHERE chat_id = ? AND message_id = ?",
        (chat_id, message_id),
    ).fetchone()
    if row is None:
        return None
    return self._job_from_row(
        row,
        status=JobStatus(str(row["status"])),
        attempts=int(row["attempts"]),
    )
```

Populate `DownloadJob.output_root` in `_job_from_row`.

- [ ] **Step 4: Run state tests and verify GREEN**

Run the command from Step 2. Expected: all state tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/models.py src/tg_video_downloader/state.py tests/test_state.py
git commit -m "feat: bind queued jobs to output roots"
```

### Task 3: Generate safe final and partial paths

**Files:**
- Modify: `src/tg_video_downloader/storage.py`
- Modify: `src/tg_video_downloader/naming.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_naming.py`

- [ ] **Step 1: Write failing path-layout tests**

```python
def test_job_paths_stay_under_selected_root(tmp_path: Path) -> None:
    root = (tmp_path / "selected").resolve()
    paths = ProjectPaths.from_root(tmp_path / "project")
    message = video(-1001, 7)
    final_path = build_final_path(paths, "群", message, download_root=root)
    part_path = build_part_path(root, message.chat_id, message.message_id)
    assert final_path.resolve().is_relative_to(root)
    assert part_path.parent == root / ".tg-video-downloader" / "partial"


def test_generated_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / ".tg-video-downloader").symlink_to(
            outside,
            target_is_directory=True,
        )
    except OSError:
        pytest.skip("当前 Windows 配置不允许创建目录符号链接")
    with pytest.raises(ValueError, match="下载目录之外"):
        build_part_path(root, -1001, 7)


@pytest.mark.skipif(os.name != "nt", reason="Windows hidden attribute")
def test_partial_directory_has_windows_hidden_attribute(tmp_path: Path) -> None:
    parent = ensure_partial_directory(tmp_path / "selected")
    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(parent))
    assert attributes != 0xFFFFFFFF
    assert attributes & 0x2
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_storage.py tests\test_naming.py -q
```

Expected: failures cite missing explicit-root and partial-directory helpers.

- [ ] **Step 3: Implement explicit-root path builders**

Add:

```python
def assert_download_path(root: Path, path: Path) -> Path:
    checked_root = root.resolve()
    checked = path.resolve()
    if not checked.is_relative_to(checked_root):
        raise ValueError(f"任务路径位于下载目录之外: {checked}")
    return checked


def build_part_path(root: Path, chat_id: int, message_id: int) -> Path:
    parent = assert_download_path(
        root,
        root / ".tg-video-downloader" / "partial",
    )
    return assert_download_path(parent, parent / f"{chat_id}_{message_id}.part")


def ensure_partial_directory(root: Path) -> Path:
    parent = assert_download_path(root, root / ".tg-video-downloader" / "partial")
    parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(parent))
        if attributes == 0xFFFFFFFF:
            raise ctypes.WinError()
        if not ctypes.windll.kernel32.SetFileAttributesW(
            str(parent),
            attributes | 0x2,
        ):
            raise ctypes.WinError()
    return parent
```

Extend `build_final_path` with keyword-only `download_root: Path | None = None`; use the explicit root when supplied and finish with `assert_download_path(root, candidate)` instead of project containment.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command.

Expected: all storage and naming tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/storage.py src/tg_video_downloader/naming.py tests/test_storage.py tests/test_naming.py
git commit -m "feat: isolate task files in selected downloads"
```

### Task 4: Download and resume on the bound volume

**Files:**
- Modify: `src/tg_video_downloader/worker.py`
- Modify: `tests/test_worker.py`

- [ ] **Step 1: Add failing worker tests**

Use the existing `prepare` and `make_video` helpers in `tests/test_worker.py`:

```python
@pytest.mark.asyncio
async def test_unbound_job_uses_current_download_root(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    selected = (tmp_path / "external").resolve()
    message = make_video(20)
    state.upsert_job(message, "群", JobSource.LIVE)
    gateway.download_payloads[(message.chat_id, message.message_id)] = b"payload"
    worker = DownloadWorker(paths, state, gateway, download_root=lambda: selected)
    try:
        assert await worker.run_one() == "completed"
        stored = state.get_job(message.chat_id, message.message_id)
        assert stored is not None
        assert stored.output_root == selected
        assert build_final_path(paths, "群", message, download_root=selected).is_file()
    finally:
        state.close()


@pytest.mark.asyncio
async def test_bound_retry_ignores_new_default_root(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    first = (tmp_path / "first").resolve()
    second = (tmp_path / "second").resolve()
    current = [first]
    message = make_video(21)
    state.upsert_job(message, "群", JobSource.LIVE)
    claimed = state.claim_next()
    assert claimed is not None
    state.release(state.bind_output_root(claimed, first))
    gateway.download_payloads[(message.chat_id, message.message_id)] = b"payload"
    worker = DownloadWorker(paths, state, gateway, download_root=lambda: current[0])
    current[0] = second
    try:
        assert await worker.run_one() == "completed"
        assert build_final_path(paths, "群", message, download_root=first).is_file()
        assert not build_final_path(paths, "群", message, download_root=second).exists()
    finally:
        state.close()


@pytest.mark.asyncio
async def test_disk_guard_checks_bound_volume(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    selected = (tmp_path / "external").resolve()
    message = make_video(22)
    state.upsert_job(message, "群", JobSource.LIVE)
    gateway.download_payloads[(message.chat_id, message.message_id)] = b"payload"
    seen: list[Path] = []
    worker = DownloadWorker(
        paths,
        state,
        gateway,
        download_root=lambda: selected,
        disk_usage=lambda path: seen.append(path) or SimpleNamespace(free=10**12),
    )
    try:
        assert await worker.run_one() == "completed"
        assert seen == [selected]
    finally:
        state.close()


@pytest.mark.asyncio
async def test_legacy_part_binds_to_old_root_before_resume(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"a" * (DOWNLOAD_CHUNK_SIZE * 2)
    message = make_video(23, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    legacy = paths.temp / f"{message.chat_id}_{message.message_id}.part"
    legacy.write_bytes(payload[:DOWNLOAD_CHUNK_SIZE])
    gateway.download_payloads[(message.chat_id, message.message_id)] = payload
    worker = DownloadWorker(
        paths,
        state,
        gateway,
        download_root=lambda: (tmp_path / "new-root").resolve(),
    )
    try:
        assert await worker.run_one() == "completed"
        stored = state.get_job(message.chat_id, message.message_id)
        assert stored is not None
        assert stored.output_root == paths.downloads
        assert gateway.download_offsets == [DOWNLOAD_CHUNK_SIZE]
        assert not legacy.exists()
    finally:
        state.close()


@pytest.mark.asyncio
async def test_unavailable_selected_root_retries_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, state, gateway = prepare(tmp_path)
    selected = (tmp_path / "missing-drive").resolve()
    message = make_video(24)
    state.upsert_job(message, "群", JobSource.LIVE)
    monkeypatch.setattr(
        "tg_video_downloader.worker.ensure_partial_directory",
        lambda _root: (_ for _ in ()).throw(OSError("drive unavailable")),
    )
    worker = DownloadWorker(paths, state, gateway, download_root=lambda: selected)
    try:
        assert await worker.run_one() == "retry_wait"
        stored = state.get_job(message.chat_id, message.message_id)
        assert stored is not None and stored.output_root == selected
        assert not build_final_path(paths, "群", message).exists()
    finally:
        state.close()


@pytest.mark.asyncio
async def test_failed_legacy_migration_preserves_source_part(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(25, size=DOWNLOAD_CHUNK_SIZE * 2)
    state.upsert_job(message, "群", JobSource.LIVE)
    legacy = paths.temp / f"{message.chat_id}_{message.message_id}.part"
    legacy.write_bytes(b"a" * DOWNLOAD_CHUNK_SIZE)
    monkeypatch.setattr(
        "tg_video_downloader.worker.os.replace",
        lambda _source, _target: (_ for _ in ()).throw(OSError("move failed")),
    )
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert await worker.run_one() == "retry_wait"
        assert legacy.read_bytes() == b"a" * DOWNLOAD_CHUNK_SIZE
    finally:
        state.close()
```

- [ ] **Step 2: Run worker tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_worker.py -q
```

Expected: failures show jobs still use the fixed project download/temporary paths.

- [ ] **Step 3: Implement bound-root worker flow**

Change the constructor to accept `download_root: Callable[[], Path] | None = None` and `disk_usage: Callable[[Path], Any] = shutil.disk_usage`. In `run_one`:

```python
legacy_part = self.paths.temp / f"{job.chat_id}_{job.message_id}.part"
if job.output_root is None:
    selected = self.paths.downloads if legacy_part.is_file() else self._download_root()
    job = self.state.bind_output_root(job, selected)
root = job.output_root
if root is None:
    raise RuntimeError("下载任务缺少输出目录")
final_path = build_final_path(
    self.paths,
    job.group_title,
    job.message,
    download_root=root,
)
partial_directory = ensure_partial_directory(root)
part_path = assert_download_path(
    partial_directory,
    partial_directory / f"{job.chat_id}_{job.message_id}.part",
)
if legacy_part.is_file() and legacy_part != part_path:
    if part_path.exists():
        raise OSError("旧断点与目标断点同时存在，拒绝覆盖任一文件")
    os.replace(legacy_part, part_path)
disk_guard = DiskGuard(root, usage=self._disk_usage)
```

Move root selection, final-directory creation, hidden partial-directory creation, and legacy migration inside the existing retry-aware `try` block so `OSError` records `retry_wait` after the job has been bound. Replace the project-root assertion on the gateway result with `assert_download_path(part_path.parent, Path(actual_path))`. Preserve all current cancellation, stall, size, retry, and atomic-completion semantics.

- [ ] **Step 4: Run worker and service tests**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_worker.py tests\test_service.py tests\test_service_integration.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/worker.py tests/test_worker.py
git commit -m "feat: resume downloads on their bound volume"
```

### Task 5: Apply download-root changes without service restart

**Files:**
- Modify: `src/tg_video_downloader/service.py`
- Modify: `tests/test_service.py`
- Modify: `tests/test_service_integration.py`

- [ ] **Step 1: Write a failing hot-reload integration test**

Start with root A, block the active download, save config with root B, release the active download, enqueue a second message, and assert:

```python
assert first_final.resolve().is_relative_to(root_a)
assert second_final.resolve().is_relative_to(root_b)
assert state.get_job(first.chat_id, first.message_id).output_root == root_a
assert state.get_job(second.chat_id, second.message_id).output_root == root_b
```

- [ ] **Step 2: Run the new integration test and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_service_integration.py -q
```

Expected: the second task still uses root A or the new config field is unavailable to the worker.

- [ ] **Step 3: Inject the hot config into the worker**

Create `config_holder = [config]` before the worker and construct it as:

```python
worker = DownloadWorker(
    self.paths,
    state,
    gateway,
    download_root=lambda: effective_download_root(
        self.paths,
        config_holder[0],
    ),
)
```

Keep `_watch_config` replacing `config_holder[0]` only after target and path validation succeeds.

- [ ] **Step 4: Run service tests and verify GREEN**

Run the Step 2 command plus `tests/test_service.py`.

Expected: all service and integration tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/service.py tests/test_service.py tests/test_service_integration.py
git commit -m "feat: hot reload the download destination"
```

### Task 6: Add GUI directory controls

**Files:**
- Modify: `src/tg_video_downloader/gui/controller.py`
- Modify: `src/tg_video_downloader/gui/app.py`
- Modify: `tests/test_gui_controller.py`
- Modify: `tests/test_gui_app.py`

- [ ] **Step 1: Write failing controller and app tests**

```python
def test_save_download_root_preserves_groups(tmp_path: Path) -> None:
    controller, _, _, _ = make_controller(tmp_path)
    controller.save_selected_groups((GroupTarget(-1001, "群", False),))
    selected = controller.save_download_root(tmp_path / "external")
    config = controller.config_store.load_config()
    assert selected == (tmp_path / "external").resolve()
    assert config.groups == (GroupTarget(-1001, "群", False),)


def test_open_downloads_uses_configured_root(tmp_path: Path, monkeypatch) -> None:
    controller, _, _, _ = make_controller(tmp_path)
    selected = controller.save_download_root(tmp_path / "external")
    opened: list[Path] = []
    monkeypatch.setattr(os, "startfile", lambda path: opened.append(Path(path)))
    controller.open_downloads()
    assert opened == [selected]
```

Add this app unit test using the existing fake-app pattern:

```python
def test_choose_download_root_saves_and_displays_normalized_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = (tmp_path / "external").resolve()
    saved: list[Path] = []
    app = object.__new__(DownloaderApp)
    app.download_root_var = FakeVar(str(tmp_path))
    app._call_sync = lambda function: function()
    app.controller = SimpleNamespace(
        current_download_root=lambda: tmp_path,
        save_download_root=lambda value: saved.append(Path(value)) or selected,
    )
    monkeypatch.setattr(
        "tg_video_downloader.gui.app.filedialog.askdirectory",
        lambda **kwargs: str(selected),
    )

    app._choose_download_root()

    assert saved == [selected]
    assert app.download_root_var.get() == str(selected)
```

- [ ] **Step 2: Run GUI tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_controller.py tests\test_gui_app.py -q
```

Expected: failures cite missing controller methods, callbacks, or download-root variable.

- [ ] **Step 3: Implement controller preservation and GUI controls**

Add controller methods:

```python
def current_download_root(self) -> Path:
    try:
        config = self.config_store.load_config()
    except FileNotFoundError:
        config = AppConfig()
    return effective_download_root(self.paths, config)


def save_download_root(self, value: str | Path) -> Path:
    root = require_writable_download_root(self.paths, value)
    try:
        current = self.config_store.load_config()
    except FileNotFoundError:
        current = AppConfig()
    self.config_store.save_config(replace(current, download_root=root))
    return root
```

Use `replace(current, groups=groups)` in `save_selected_groups` so it preserves `download_root`. Make `open_downloads` call `current_download_root()`.

Import `filedialog` from `tkinter`. At the top of `_build_run_page`, below the action buttons, create:

```python
self.download_root_var = tk.StringVar(
    value=str(self.controller.current_download_root())
)
storage = ttk.LabelFrame(page, text="下载保存位置", padding=10)
storage.pack(fill="x", pady=(0, 16))
storage.columnconfigure(0, weight=1)
ttk.Entry(storage, textvariable=self.download_root_var).grid(
    row=0, column=0, sticky="ew", padx=(0, 8)
)
ttk.Button(storage, text="选择文件夹", command=self._choose_download_root).grid(
    row=0, column=1, padx=(0, 8)
)
ttk.Button(storage, text="保存位置", command=self._save_download_root).grid(
    row=0, column=2, padx=(0, 8)
)
ttk.Button(
    storage,
    text="打开目录",
    command=lambda: self._call_sync(self.controller.open_downloads),
).grid(row=0, column=3)
```

Add the callbacks:

```python
def _choose_download_root(self) -> None:
    selected = filedialog.askdirectory(initialdir=self.download_root_var.get())
    if selected:
        self._save_download_root(selected)


def _save_download_root(self, value: str | None = None) -> None:
    def save() -> None:
        root = self.controller.save_download_root(
            value if value is not None else self.download_root_var.get()
        )
        self.download_root_var.set(str(root))

    self._call_sync(save)
```

- [ ] **Step 4: Run GUI tests and verify GREEN**

Run the Step 2 command.

Expected: all GUI controller and app tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/gui/controller.py src/tg_video_downloader/gui/app.py tests/test_gui_controller.py tests/test_gui_app.py
git commit -m "feat: choose the download directory in settings"
```

### Task 7: Diagnostics, docs, and acceptance

**Files:**
- Modify: `src/tg_video_downloader/diagnostics.py`
- Modify: `tests/test_diagnostics.py`
- Modify: `README.md`
- Modify: `config.example.toml`
- Modify: `docs/verification.md`

- [ ] **Step 1: Add failing diagnostics tests**

```python
def test_download_root_check_uses_configured_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _, _ = configure_valid_project(tmp_path / "project")
    selected = (tmp_path / "external").resolve()
    ConfigStore(paths).save_config(
        replace(ConfigStore(paths).load_config(), download_root=selected)
    )
    seen: list[Path] = []
    monkeypatch.setattr(
        "tg_video_downloader.diagnostics.shutil.disk_usage",
        lambda path: seen.append(Path(path)) or SimpleNamespace(free=2 * 1024**3),
    )
    check = Doctor(paths, gateway_factory=lambda *_: FakeTelegramGateway())._check_download_root(
        ConfigStore(paths).load_config()
    )
    assert check.status == "pass"
    assert seen == [selected]
    assert str(selected) in check.message


def test_download_root_check_rejects_a_file(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path / "project")
    selected = tmp_path / "occupied"
    selected.write_text("not a directory", encoding="utf-8")
    config = AppConfig(download_root=selected.resolve())
    check = Doctor(paths, gateway_factory=lambda *_: FakeTelegramGateway())._run_local(
        "download_root",
        lambda: Doctor(paths, gateway_factory=lambda *_: FakeTelegramGateway())._check_download_root(config),
    )
    assert check.status == "fail"
    assert "文件夹" in check.message
```

For an unavailable removable root, monkeypatch `Path.mkdir` for the selected path to raise `FileNotFoundError`; assert `_run_local` returns `warning` by mapping unavailable-drive errors inside `_check_download_root`, while permission and file-path errors remain `fail`. Assert messages include only the configured root and free-space count, never `iterdir()` output.

- [ ] **Step 2: Implement `_check_download_root` and update docs**

Add a local diagnostic after config load:

```python
def _check_download_root(self, config: AppConfig) -> DiagnosticCheck:
    root = effective_download_root(self.paths, config)
    try:
        checked = require_writable_download_root(self.paths, root)
        free = int(shutil.disk_usage(checked).free)
    except FileNotFoundError as error:
        return DiagnosticCheck("download_root", "warning", f"下载盘暂不可用：{error}")
    status: DiagnosticStatus = "pass" if free >= SAFETY_FREE_BYTES else "fail"
    return DiagnosticCheck(
        "download_root",
        status,
        f"下载目录 {checked}，可用空间 {free / (1024**3):.2f} GiB",
    )
```

Replace the old fixed-root `disk` check with this `download_root` check and update the expected key sets/counts in existing diagnostics tests. Document the picker, external `.tg-video-downloader/partial`, old-file behavior, removable-drive errors, and privacy boundary.

- [ ] **Step 3: Run focused and full checks**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_diagnostics.py tests\test_windows_scripts.py -q
& .\scripts\check.ps1
```

Expected: all tests and compile checks pass.

- [ ] **Step 4: Perform Windows storage smoke test**

Choose a temporary folder on another local drive, let one real task resume and complete, verify its `.part` and final file remain on that volume, switch back, and confirm unrelated files in the selected folder are untouched. Record only byte counts and paths, not Telegram names.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/diagnostics.py tests/test_diagnostics.py README.md config.example.toml docs/verification.md
git commit -m "docs: verify configurable download storage"
```
