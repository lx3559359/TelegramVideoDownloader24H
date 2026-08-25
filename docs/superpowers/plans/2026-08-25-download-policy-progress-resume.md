# Download Policy, Progress, and Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每个 Telegram 群组或频道增加独立历史下载开关，并让单文件下载具备可见进度、`cryptg` 加速、断点续传、停滞恢复和及时停止能力。

**Architecture:** `config.toml` 与 SQLite `groups` 表共同保存每个目标的 `download_history` 策略，任务领取和历史扫描在数据层遵守该策略，而实时与补抓任务保持不受影响。Telethon 网关按固定块流式写入项目内 `.tmp`，工作线程负责断点校正、进度快照、停滞监控和任务状态，服务把快照写入心跳供 Tkinter GUI 展示。

**Tech Stack:** Python 3.11+、Telethon 1.44、cryptg 0.6、SQLite、asyncio、Tkinter、pytest、PowerShell。

---

## File map

- `src/tg_video_downloader/models.py`：目标历史策略数据类型。
- `src/tg_video_downloader/config.py`：新旧配置兼容、策略校验和序列化。
- `src/tg_video_downloader/state.py`：SQLite 迁移、策略同步、任务领取和暂停计数。
- `src/tg_video_downloader/coordinator.py`：暂停历史扫描，继续实时与补抓。
- `src/tg_video_downloader/gateway.py`：固定块写入、偏移续传和进度回调。
- `src/tg_video_downloader/worker.py`：断点校正、空间检查、停滞和停止。
- `src/tg_video_downloader/service.py`：进度心跳。
- `src/tg_video_downloader/gui/controller.py`、`gui/app.py`：策略保存与 GUI。
- `src/tg_video_downloader/diagnostics.py`：自检 `cryptg`。
- `tests/`：各层单元与集成测试。
- `pyproject.toml`、`config.example.toml`、`README.md`、`docs/verification.md`：依赖与交付说明。

### Task 1: Model and config compatibility

**Files:**
- Modify: `src/tg_video_downloader/models.py:21`
- Modify: `src/tg_video_downloader/config.py:35`
- Modify: `config.example.toml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

```python
def test_legacy_group_defaults_history_to_enabled(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.config.write_text(
        '[[groups]]\nchat_id = -1001\ntitle = "旧频道"\n',
        encoding="utf-8",
    )
    assert ConfigStore(paths).load_config().groups == (
        GroupTarget(-1001, "旧频道", download_history=True),
    )


def test_history_policy_round_trips_explicitly(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    store = ConfigStore(paths)
    config = AppConfig(groups=(GroupTarget(-1001, "频道", False),))
    store.save_config(config)
    assert store.load_config() == config
    assert "download_history = false" in paths.config.read_text(encoding="utf-8")


def test_history_policy_must_be_boolean(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.config.write_text(
        '[[groups]]\nchat_id = -1001\ntitle = "频道"\n'
        'download_history = "yes"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="download_history"):
        ConfigStore(paths).load_config()
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:TEMP=(Resolve-Path '.tmp').Path
$env:TMP=$env:TEMP
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: failures show that `GroupTarget` and `ConfigStore` do not support `download_history`.

- [ ] **Step 3: Implement the field and strict parser**

```python
@dataclass(frozen=True)
class GroupTarget:
    chat_id: int
    title: str
    download_history: bool = True


def _download_history(group: dict[str, Any]) -> bool:
    value = group.get("download_history", True)
    if not isinstance(value, bool):
        raise ValueError("download_history 必须是布尔值")
    return value
```

Use the helper when constructing every configured `GroupTarget`. Add this exact line to each saved group block:

```python
f"download_history = {str(group.download_history).lower()}",
```

Set `download_history = false` in `config.example.toml`.

- [ ] **Step 4: Run tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: all config tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/models.py src/tg_video_downloader/config.py config.example.toml tests/test_config.py
git commit -m "feat: configure history downloads per target"
```

### Task 2: Persist policy and pause history jobs in SQLite

**Files:**
- Modify: `src/tg_video_downloader/state.py:13`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write failing migration and queue tests**

```python
def test_legacy_groups_table_migrates_history_policy(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE groups (chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
        "enabled INTEGER NOT NULL DEFAULT 1, latest_seen_id INTEGER, "
        "history_cursor_id INTEGER, history_complete INTEGER NOT NULL DEFAULT 0, "
        "access_error TEXT)"
    )
    connection.execute("INSERT INTO groups(chat_id, title) VALUES(-1001, '旧频道')")
    connection.commit()
    connection.close()
    state = StateStore(database)
    try:
        assert state.get_group(-1001).download_history is True
    finally:
        state.close()


def test_paused_history_does_not_block_live_and_can_resume(
    store: StateStore,
    history_message: MessageInfo,
    live_message: MessageInfo,
) -> None:
    store.reconcile_targets((GroupTarget(-1001, "群", False),))
    store.upsert_job(history_message, "群", JobSource.HISTORY)
    store.upsert_job(live_message, "群", JobSource.LIVE)
    assert store.claim_next().source == JobSource.LIVE
    assert store.claim_next() is None
    assert store.counts()["paused_history"] == 1
    store.reconcile_targets((GroupTarget(-1001, "群", True),))
    assert store.claim_next().source == JobSource.HISTORY


def test_release_returns_downloading_job_to_pending(
    store: StateStore,
    live_message: MessageInfo,
) -> None:
    store.upsert_job(live_message, "群", JobSource.LIVE)
    job = store.claim_next()
    assert job is not None
    store.release(job)
    assert store.claim_next() is not None
```

Update every existing expected count dictionary to include `paused_history`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_state.py -q
```

Expected: failures cite the missing column, group field, count, or `release` method.

- [ ] **Step 3: Add an idempotent migration and policy synchronization**

Add `download_history INTEGER NOT NULL DEFAULT 1` to `SCHEMA`, then execute:

```python
columns = {
    str(row["name"])
    for row in self._connection.execute("PRAGMA table_info(groups)").fetchall()
}
if "download_history" not in columns:
    self._connection.execute(
        "ALTER TABLE groups ADD COLUMN download_history INTEGER NOT NULL DEFAULT 1"
    )
self._connection.commit()
```

Add `download_history: bool` to `GroupState`. Synchronize targets with:

```sql
INSERT INTO groups(chat_id, title, enabled, download_history)
VALUES (?, ?, 1, ?)
ON CONFLICT(chat_id) DO UPDATE SET
    title = excluded.title,
    enabled = 1,
    download_history = excluded.download_history
```

- [ ] **Step 4: Make claim and counts policy-aware**

Add to `claim_next`:

```sql
AND (jobs.source <> 'history' OR groups.download_history = 1)
```

Use `jobs JOIN groups` and add these non-overlapping count expressions:

```sql
SUM(CASE WHEN jobs.status = 'pending' AND jobs.priority = 10
          AND groups.enabled = 1 AND groups.download_history = 1
    THEN 1 ELSE 0 END) AS pending_history,
SUM(CASE WHEN jobs.source = 'history'
          AND jobs.status IN ('pending', 'retry_wait')
          AND groups.enabled = 1 AND groups.download_history = 0
    THEN 1 ELSE 0 END) AS paused_history
```

Implement active-job release:

```python
def release(self, job: DownloadJob) -> None:
    with self._connection:
        self._connection.execute(
            """
            UPDATE jobs
            SET status = 'pending', next_attempt_at = NULL, error = NULL
            WHERE chat_id = ? AND message_id = ? AND status = 'downloading'
            """,
            (job.chat_id, job.message_id),
        )
```

- [ ] **Step 5: Run tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_state.py -q
```

Expected: all state tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/tg_video_downloader/state.py tests/test_state.py
git commit -m "feat: pause historical jobs without deleting them"
```

### Task 3: Pause scans while preserving live and catch-up

**Files:**
- Modify: `src/tg_video_downloader/coordinator.py:92`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write failing coordinator tests**

```python
@pytest.mark.asyncio
async def test_history_scan_pauses_and_resumes_from_saved_cursor(tmp_path: Path) -> None:
    target = GroupTarget(-1001, "频道", False)
    gateway = FakeTelegramGateway(
        {-1001: [make_video(-1001, value) for value in (2, 3, 4)]}
    )
    store = StateStore(tmp_path / "state.sqlite3")
    store.reconcile_targets((target,))
    store.set_history_cursor(target.chat_id, 4, complete=False)
    coordinator = ScannerCoordinator(store, gateway)
    try:
        assert await coordinator.scan_once(target.chat_id) is False
        assert gateway.iterated_chat_ids == []
        assert store.get_group(target.chat_id).history_cursor_id == 4
        await coordinator.apply_targets((GroupTarget(-1001, "频道", True),))
        assert await coordinator.scan_once(target.chat_id) is True
    finally:
        store.close()


@pytest.mark.asyncio
async def test_live_events_ignore_history_pause(tmp_path: Path) -> None:
    target = GroupTarget(-1001, "频道", False)
    gateway = FakeTelegramGateway()
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = ScannerCoordinator(store, gateway)
    try:
        await coordinator.start((target,))
        await gateway.emit(make_video(-1001, 7))
        assert store.claim_next().message_id == 7
    finally:
        store.close()
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_coordinator.py -q
```

Expected: the paused target still reaches `iter_older_messages`.

- [ ] **Step 3: Guard only historical paths**

```python
group = self.state.get_group(chat_id)
if not group.enabled or not group.download_history or group.history_complete:
    return False
```

Apply the same condition in `run_scans`. Do not add it to `handle_live`, `catch_up_once`, or `run_catchups`.

- [ ] **Step 4: Run tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_coordinator.py -q
```

Expected: all coordinator tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/coordinator.py tests/test_coordinator.py
git commit -m "feat: pause history scans per target"
```

### Task 4: Add per-target GUI controls

**Files:**
- Modify: `src/tg_video_downloader/gui/controller.py:202`
- Modify: `src/tg_video_downloader/gui/app.py:214`
- Test: `tests/test_gui_controller.py`
- Test: `tests/test_gui_app.py`

- [ ] **Step 1: Write failing controller and GUI tests**

```python
def test_controller_preserves_selected_history_policy(tmp_path: Path) -> None:
    controller, _, _, _ = make_controller(tmp_path)
    groups = (
        GroupTarget(-1001, "只监听新内容", False),
        GroupTarget(-1002, "包含历史", True),
    )
    controller.save_selected_groups(groups)
    assert controller.selected_groups() == groups


def test_history_column_enables_target_and_history(app) -> None:
    app._groups = (GroupTarget(-1001, "频道", False),)
    app._selected_ids = set()
    app._history_ids = set()
    app.group_tree.identify_row.return_value = "-1001"
    app.group_tree.identify_column.return_value = "#2"
    app._toggle_group(SimpleNamespace(x=80, y=10))
    assert app._selected_ids == {-1001}
    assert app._history_ids == {-1001}
```

Add the inverse GUI assertion: turning monitoring off removes the ID from `_history_ids`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gui_controller.py tests/test_gui_app.py -q
```

Expected: missing `selected_groups`, `_history_ids`, and `history` column behavior.

- [ ] **Step 3: Implement controller policy access**

```python
def selected_groups(self) -> tuple[GroupTarget, ...]:
    try:
        return self.config_store.load_config().groups
    except FileNotFoundError:
        return ()


def selected_chat_ids(self) -> set[int]:
    return {group.chat_id for group in self.selected_groups()}
```

- [ ] **Step 4: Implement column-specific GUI state**

Initialize from saved config:

```python
saved = {group.chat_id: group for group in controller.selected_groups()}
self._selected_ids = set(saved)
self._history_ids = {
    chat_id for chat_id, group in saved.items() if group.download_history
}
```

Add `history` between the selected and title columns. Toggle it with:

```python
column = self.group_tree.identify_column(event.x) if hasattr(event, "x") else ""
if column == "#2":
    self._selected_ids.add(chat_id)
    if chat_id in self._history_ids:
        self._history_ids.remove(chat_id)
    else:
        self._history_ids.add(chat_id)
else:
    if chat_id in self._selected_ids:
        self._selected_ids.remove(chat_id)
        self._history_ids.discard(chat_id)
    else:
        self._selected_ids.add(chat_id)
```

Save explicit policy:

```python
groups = tuple(
    GroupTarget(group.chat_id, group.title, group.chat_id in self._history_ids)
    for group in self._groups
    if group.chat_id in self._selected_ids
)
```

- [ ] **Step 5: Run tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gui_controller.py tests/test_gui_app.py -q
```

Expected: all focused GUI tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/tg_video_downloader/gui/controller.py src/tg_video_downloader/gui/app.py tests/test_gui_controller.py tests/test_gui_app.py
git commit -m "feat: configure history per Telegram target"
```

### Task 5: Stream Telegram media from an offset

**Files:**
- Modify: `src/tg_video_downloader/gateway.py:40`
- Modify: `tests/fakes.py`
- Test: `tests/test_gateway.py`

- [ ] **Step 1: Write failing gateway streaming tests**

Create a fake Telethon client whose `iter_download` records offsets and yields two chunks:

```python
@pytest.mark.asyncio
async def test_download_appends_from_offset_and_reports_progress(tmp_path: Path) -> None:
    client = DownloadClient(chunks=(b"def", b"ghi"), media_size=9)
    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(123, "hash"),
        client_factory=lambda *_args, **_kwargs: client,
    )
    destination = tmp_path / ".tmp" / "job.part"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"abc")
    progress: list[tuple[int, int | None]] = []
    result = await gateway.download_message(
        -1001,
        1,
        destination,
        offset=3,
        progress_callback=lambda current, total: progress.append((current, total)),
    )
    assert result == destination
    assert destination.read_bytes() == b"abcdefghi"
    assert client.download_offsets == [3]
    assert progress == [(6, 9), (9, 9)]
```

Add a discovery assertion that every newly listed group/channel has `download_history is False`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gateway.py -q
```

Expected: the gateway lacks offset/progress arguments and new discovery uses the compatibility default.

- [ ] **Step 3: Define the streaming contract**

```python
DOWNLOAD_CHUNK_SIZE = 512 * 1024
SYNC_BYTES = 8 * 1024 * 1024
SYNC_SECONDS = 5.0
DownloadProgressCallback = Callable[[int, int | None], None]


async def download_message(
    self,
    chat_id: int,
    message_id: int,
    destination: Path,
    *,
    offset: int = 0,
    progress_callback: DownloadProgressCallback | None = None,
) -> Path: ...
```

Apply this signature to the protocol, `TelethonGateway`, and all fakes.

- [ ] **Step 4: Implement fixed-block writes and durability**

After loading the message, replace `download_media` with:

```python
total = getattr(getattr(message, "document", None), "size", None)
mode = "ab" if offset else "wb"
downloaded = offset
unsynced = 0
last_sync = time.monotonic()
stream = self._client.iter_download(
    message.media,
    offset=offset,
    request_size=DOWNLOAD_CHUNK_SIZE,
    chunk_size=DOWNLOAD_CHUNK_SIZE,
)
try:
    with destination.open(mode, buffering=0) as handle:
        async for chunk in stream:
            handle.write(chunk)
            downloaded += len(chunk)
            unsynced += len(chunk)
            if progress_callback is not None:
                progress_callback(downloaded, total)
            now = time.monotonic()
            if unsynced >= SYNC_BYTES or now - last_sync >= SYNC_SECONDS:
                os.fsync(handle.fileno())
                unsynced = 0
                last_sync = now
        os.fsync(handle.fileno())
finally:
    close = getattr(stream, "close", None)
    if close is not None:
        await close()
return destination
```

Keep the existing Telegram error mapping. In `list_groups`, pass `download_history=False` explicitly.

- [ ] **Step 5: Upgrade `FakeTelegramGateway`**

```python
self.download_offsets: list[int] = []

async def download_message(..., offset=0, progress_callback=None) -> Path:
    self.download_offsets.append(offset)
    payload = self.download_payloads[(chat_id, message_id)]
    with destination.open("ab" if offset else "wb") as handle:
        handle.write(payload[offset:])
    if progress_callback is not None:
        progress_callback(len(payload), len(payload))
    return destination
```

- [ ] **Step 6: Run tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gateway.py -q
```

Expected: all gateway tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/tg_video_downloader/gateway.py tests/fakes.py tests/test_gateway.py
git commit -m "feat: stream Telegram downloads from offsets"
```

### Task 6: Resume partial files and recover stalled transfers

**Files:**
- Modify: `src/tg_video_downloader/worker.py:17`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing resume tests**

```python
@pytest.mark.asyncio
async def test_worker_resumes_aligned_partial(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    payload = b"a" * (DOWNLOAD_CHUNK_SIZE * 2)
    message = make_video(1, size=len(payload))
    state.upsert_job(message, "群", JobSource.LIVE)
    part = paths.temp / "-1001_1.part"
    part.write_bytes(payload[:DOWNLOAD_CHUNK_SIZE])
    gateway.download_payloads[(-1001, 1)] = payload
    worker = DownloadWorker(paths, state, gateway)
    try:
        assert await worker.run_one() == "completed"
        assert gateway.download_offsets == [DOWNLOAD_CHUNK_SIZE]
    finally:
        state.close()


def test_recover_preserves_partial_file(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1, size=DOWNLOAD_CHUNK_SIZE * 2)
    state.upsert_job(message, "群", JobSource.LIVE)
    assert state.claim_next() is not None
    part = paths.temp / "-1001_1.part"
    part.write_bytes(b"a" * DOWNLOAD_CHUNK_SIZE)
    assert DownloadWorker(paths, state, gateway).recover() == 1
    assert part.stat().st_size == DOWNLOAD_CHUNK_SIZE
```

Add focused tests proving that an unaligned partial truncates down, an oversized partial restarts at 0, and disk guard receives only remaining bytes. Add a blocked historical download test: claim it while history is enabled, change the target policy to disabled, release the fake network call, and assert the already-active file still completes while the next historical job remains paused.

- [ ] **Step 2: Write failing progress, stall, and stop tests**

```python
@pytest.mark.asyncio
async def test_stalled_download_is_cancelled_and_retried(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1, size=100)
    state.upsert_job(message, "群", JobSource.LIVE)
    cancelled = asyncio.Event()
    async def stalled(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    gateway.download_message = stalled
    worker = DownloadWorker(
        paths, state, gateway, stall_seconds=0.03, monitor_seconds=0.005
    )
    assert await worker.run_one() == "retry_wait"
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_stop_cancels_download_and_releases_job(tmp_path: Path) -> None:
    paths, state, gateway = prepare(tmp_path)
    message = make_video(1, size=100)
    state.upsert_job(message, "群", JobSource.LIVE)
    stop = asyncio.Event()
    started = asyncio.Event()
    async def blocked(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()
    gateway.download_message = blocked
    worker = DownloadWorker(paths, state, gateway, monitor_seconds=0.005)
    task = asyncio.create_task(worker.run_one(stop))
    await started.wait()
    stop.set()
    assert await task == "stopped"
    assert state.claim_next() is not None
```

Add a callback test for bytes, total, percent, average speed, and `resumed` while active and cleared after completion.

- [ ] **Step 3: Run worker tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_worker.py -q
```

Expected: current recovery deletes partials and blocked downloads never return.

- [ ] **Step 4: Add progress state and partial validation**

```python
@dataclass(frozen=True)
class DownloadProgress:
    file_name: str
    downloaded_bytes: int
    total_bytes: int | None
    percent: float | None
    bytes_per_second: float
    resumed: bool
```

Inject timing controls:

```python
monotonic: Callable[[], float] = monotonic_clock,
stall_seconds: float = 120.0,
monitor_seconds: float = 1.0,
```

`recover()` only calls `state.recover_inflight()`. Add this helper and finalize immediately when its return equals the expected size:

```python
def _resume_offset(part_path: Path, expected_size: int | None) -> int:
    if not part_path.is_file():
        return 0
    if expected_size is None:
        part_path.unlink(missing_ok=True)
        return 0
    size = part_path.stat().st_size
    if size > expected_size:
        part_path.unlink(missing_ok=True)
        return 0
    if size == expected_size:
        return size
    aligned = size - (size % DOWNLOAD_CHUNK_SIZE)
    if aligned != size:
        with part_path.open("r+b") as handle:
            handle.truncate(aligned)
    return aligned
```

- [ ] **Step 5: Monitor the gateway task**

```python
download_task = asyncio.create_task(
    self.gateway.download_message(
        job.chat_id,
        job.message_id,
        part_path,
        offset=offset,
        progress_callback=on_progress,
    )
)
while not download_task.done():
    if stop is not None and stop.is_set():
        download_task.cancel()
        await asyncio.gather(download_task, return_exceptions=True)
        self.state.release(job)
        return "stopped"
    if self._monotonic() - last_progress >= self._stall_seconds:
        download_task.cancel()
        await asyncio.gather(download_task, return_exceptions=True)
        raise TransientTelegramError("下载连续 120 秒没有进度")
    await asyncio.sleep(self._monitor_seconds)
actual_path = await download_task
```

The callback computes speed from bytes received in the current process. `run(stop)` exits when `run_one(stop)` returns `stopped`. Transient errors retain valid partials; permanent errors delete only their own partial.

- [ ] **Step 6: Check only remaining disk requirement**

```python
remaining = None if job.message.size is None else max(0, job.message.size - offset)
if not self.disk_guard.has_space(remaining):
    self.state.mark_retry(job, "磁盘可用空间低于安全阈值", delay_seconds=60)
    return "disk_paused"
```

- [ ] **Step 7: Run tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_worker.py -q
```

Expected: all worker tests pass without a real 120-second wait.

- [ ] **Step 8: Commit**

```powershell
git add src/tg_video_downloader/worker.py tests/test_worker.py
git commit -m "feat: resume downloads and recover stalled transfers"
```

### Task 7: Publish progress through heartbeat and GUI

**Files:**
- Modify: `src/tg_video_downloader/service.py:126`
- Modify: `src/tg_video_downloader/gui/app.py:273`
- Test: `tests/test_service.py`
- Test: `tests/test_gui_app.py`

- [ ] **Step 1: Write failing heartbeat and formatter tests**

```python
def test_snapshot_includes_download_progress(service, state) -> None:
    worker = SimpleNamespace(
        current_file="video.mp4",
        progress=DownloadProgress(
            "video.mp4", 5 * 1024**2, 10 * 1024**2, 50.0, 2 * 1024**2, True
        ),
    )
    snapshot = service._snapshot("running", state, worker=worker)
    assert snapshot["progress"]["downloaded_bytes"] == 5 * 1024**2
    assert snapshot["progress"]["percent"] == 50.0


def test_format_progress_uses_binary_units() -> None:
    text, speed = format_download_progress({
        "downloaded_bytes": 5 * 1024**2,
        "total_bytes": 10 * 1024**2,
        "percent": 50.0,
        "bytes_per_second": 2 * 1024**2,
        "resumed": True,
    })
    assert text == "5.00 MiB / 10.00 MiB（50.0%，断点续传）"
    assert speed == "2.00 MiB/s"
```

Add status refresh assertions for `paused_history` and per-group historical policy text.

- [ ] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_service.py tests/test_gui_app.py -q
```

Expected: heartbeat lacks `progress`; GUI fields and formatters are missing.

- [ ] **Step 3: Serialize the worker snapshot**

```python
progress = getattr(worker, "progress", None)
if progress is not None:
    snapshot["progress"] = asdict(progress)
```

Keep `current_file` for compatibility. Add `download_history` to each group heartbeat entry.

- [ ] **Step 4: Add GUI progress fields**

```python
def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")
```

Add run-page labels for `下载进度`, `下载速度`, and `历史已暂停`. `format_download_progress` returns `("-", "-")` for absent or malformed data so the Tk poll callback never crashes.

- [ ] **Step 5: Run tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_service.py tests/test_gui_app.py -q
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/tg_video_downloader/service.py src/tg_video_downloader/gui/app.py tests/test_service.py tests/test_gui_app.py
git commit -m "feat: show Telegram download progress"
```

### Task 8: Add cryptg acceleration and self-check coverage

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/tg_video_downloader/diagnostics.py:188`
- Modify: `scripts/bootstrap.ps1`
- Test: `tests/test_diagnostics.py`
- Test: `tests/test_windows_scripts.py`

- [ ] **Step 1: Write failing dependency tests**

```python
def test_dependency_check_includes_cryptg(monkeypatch, doctor) -> None:
    seen: list[str] = []
    def fake_version(name: str) -> str:
        seen.append(name)
        return "1.0"
    monkeypatch.setattr("tg_video_downloader.diagnostics.version", fake_version)
    assert doctor._check_dependencies().status == "pass"
    assert "cryptg" in seen
```

Extend the Windows script test to assert the import check uses `$ProjectPython` after the editable install and that cache variables are set before pip runs.

- [ ] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py tests/test_windows_scripts.py -q
```

Expected: the doctor and bootstrap script do not mention `cryptg`.

- [ ] **Step 3: Declare and verify the acceleration package**

Add to `pyproject.toml`:

```toml
"cryptg>=0.6,<0.7",
```

Extend the doctor loop:

```python
for distribution in ("telethon", "cryptg", "tzdata", "qrcode"):
```

After editable installation in `bootstrap.ps1`:

```powershell
& $ProjectPython -c "import cryptg; print('cryptg acceleration ready')"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
```

- [ ] **Step 4: Run bootstrap and focused tests**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
.\.venv\Scripts\python.exe -c "import cryptg; print(cryptg.__file__)"
.\.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py tests/test_windows_scripts.py -q
```

Expected: the module path is under project `.venv`; tests pass.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml src/tg_video_downloader/diagnostics.py scripts/bootstrap.ps1 tests/test_diagnostics.py tests/test_windows_scripts.py
git commit -m "perf: enable Telethon native crypto acceleration"
```

### Task 9: Service integration and stop responsiveness

**Files:**
- Modify: `tests/test_service_integration.py`
- Modify: `tests/test_service.py`
- Modify: `tests/fakes.py`
- Modify: `src/tg_video_downloader/service.py`

- [ ] **Step 1: Write a failing hot-reload integration test**

Construct a service with one paused historical job and one live job. Assert the live job completes while history remains paused; save `download_history=True`; then assert the same service claims history after config reload. Use events and bounded `asyncio.wait_for(..., timeout=2)`, never a fixed sleep above 50 ms.

Core assertions:

```python
assert state.counts()["paused_history"] == 1
assert live_final_path.is_file()
ConfigStore(paths).save_config(
    AppConfig(
        groups=(GroupTarget(-1001, "频道", True),),
        config_poll_seconds=1,
    )
)
await asyncio.wait_for(history_completed.wait(), timeout=2)
```

- [ ] **Step 2: Write a failing stop integration test**

Use a gateway that waits forever inside `download_message`. Create `stop.flag`, then require:

```python
assert await asyncio.wait_for(service_task, timeout=2) == 0
assert part_path.exists()
reopened = StateStore(paths.database)
try:
    assert reopened.claim_next() is not None
finally:
    reopened.close()
```

- [ ] **Step 3: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_service.py tests/test_service_integration.py -q
```

Expected: the current service cannot stop while the worker is awaiting Telegram.

- [ ] **Step 4: Make the smallest service wiring change**

Keep one shared `asyncio.Event` and pass it to `worker.run(stop)`. Do not add forced process termination to Python; cancellation stays in the worker and the PowerShell supervisor only restarts exited services.

```python
asyncio.create_task(worker.run(stop), name="downloads"),
```

- [ ] **Step 5: Run tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_service.py tests/test_service_integration.py -q
```

Expected: all integration tests pass and stop completes within the deadline.

- [ ] **Step 6: Commit**

```powershell
git add src/tg_video_downloader/service.py tests/test_service.py tests/test_service_integration.py tests/fakes.py
git commit -m "fix: stop active Telegram downloads safely"
```

### Task 10: Documentation, full verification, real acceptance, and release

**Files:**
- Modify: `README.md`
- Modify: `docs/verification.md`

- [ ] **Step 1: Update user documentation**

Document the per-target history column, new-target default, paused-history count, progress/speed, aligned resume, `cryptg` in project `.venv`, remaining-byte disk checks, and 120-second stall recovery. Remove the statement that every partial always restarts from zero. Retain the project-path-only constraint.

- [ ] **Step 2: Run complete project-local verification**

```powershell
$projectTemp=(Resolve-Path '.tmp').Path
$env:TEMP=$projectTemp
$env:TMP=$projectTemp
$env:PYTHONPYCACHEPREFIX=(Join-Path $projectTemp 'pycache-final')
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import cryptg; from pathlib import Path; print(Path(cryptg.__file__).resolve())"
git diff --check
```

Expected: all collected tests pass, `pip check` reports no broken requirements, `cryptg` resolves under project `.venv`, and no whitespace errors exist.

- [ ] **Step 3: Request code review**

Invoke `superpowers:requesting-code-review`. Resolve each valid correctness issue, rerun its focused tests, then rerun `scripts/check.ps1`.

- [ ] **Step 4: Replace diagnostic foreground service safely**

Call `request_stop(ProjectPaths.from_root(Path.cwd()))`, wait for graceful cancellation, and verify exact project command lines are gone. Only if the validated project service launcher remains after the bounded wait may that exact PID be terminated. Clear the flag and start `start_hidden_supervisor(Path.cwd())`. Do not delete the session, database, queue, completed files, or valid `.part`.

- [ ] **Step 5: Run real Telegram acceptance**

Capture only sanitized evidence:

```text
session_authorized=true
cryptg_loaded=true
heartbeat_status=running
part_bytes_increased=true
heartbeat_progress_increased=true
resume_offset_greater_than_zero=true
history_pause_applied=true
live_monitoring_remained_enabled=true
completed_file_created=true
```

Pause history only after the current historical file completes; confirm no next historical task is claimed; restore the user's chosen switch state. Do not send or manufacture Telegram messages.

- [ ] **Step 6: Record verification and commit docs**

Update `docs/verification.md` with date, branch, exact test count, dependency result, project-venv acceleration status, and sanitized real results.

```powershell
git add README.md docs/verification.md config.example.toml
git commit -m "docs: verify resumable Telegram downloads"
```

- [ ] **Step 7: Verify before completion**

Invoke `superpowers:verification-before-completion`, then run fresh:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
.\.venv\Scripts\python.exe -m pip check
git diff --check master...HEAD
git status --short
git log -1 --oneline
```

Expected: all checks pass and the feature worktree is clean.

- [ ] **Step 8: Merge and publish**

Invoke `superpowers:finishing-a-development-branch`. The user has already approved merge and dual publication for this project: fast-forward `master`, rerun checks on `master`, push both remotes, and compare refs:

```powershell
git push github master
git push modelscope master
git ls-remote github refs/heads/master
git ls-remote modelscope refs/heads/master
```

Expected: both remote `master` refs equal local `master`. Remove the exact merged worktree only after this verification.
