# Resilience and Self-Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee incremental recovery after reconnect or group re-enable, add trustworthy runtime status, and provide a privacy-safe one-click self-check in the CLI and GUI.

**Architecture:** Extend `ScannerCoordinator` with periodic catch-up and per-group in-memory access backoff, while keeping SQLite as the durable source for message cursors and download jobs. Add a focused diagnostics module that runs independent checks and writes redacted reports under the project root; surface the same report through a new CLI command and the existing Tkinter controller. Keep all generated state in `ProjectPaths` and preserve the single-worker architecture.

**Tech Stack:** Python 3.11, asyncio, SQLite, Telethon, Tkinter, PowerShell, pytest/pytest-asyncio.

---

## File map

- Create `src/tg_video_downloader/diagnostics.py`: diagnostic result types, local checks, online checks, redaction, atomic report writing.
- Create `tests/test_diagnostics.py`: local/online diagnosis and report safety tests.
- Modify `src/tg_video_downloader/coordinator.py`: re-enable catch-up, periodic catch-up, group access backoff.
- Modify `src/tg_video_downloader/worker.py`: expose a safe current-task display value.
- Modify `src/tg_video_downloader/service.py`: startup-status heartbeat, periodic catch-up task, current-task heartbeat fields.
- Modify `src/tg_video_downloader/gui/controller.py`: stale-heartbeat interpretation and doctor orchestration.
- Modify `src/tg_video_downloader/gui/app.py`: self-check button and complete status rendering.
- Modify `src/tg_video_downloader/cli.py`: `doctor` command and diagnostic exit codes.
- Modify coordinator, service, worker, controller, GUI, CLI, and Windows-script tests as listed below.
- Modify `README.md` and `docs/verification.md`: document self-check and resilience behavior.

### Task 1: Incremental catch-up after re-enable and during long-running sessions

**Files:**
- Modify: `src/tg_video_downloader/coordinator.py`
- Modify: `src/tg_video_downloader/service.py`
- Test: `tests/test_coordinator.py`
- Test: `tests/test_service.py`

- [ ] **Step 1: Write failing coordinator tests**

Add tests that create a previously enabled group with `latest_seen_id=5`, disable it, add messages 6 and 7, then call `apply_targets((group,))` and assert jobs 6 and 7 are queued. Add a second test for `catch_up_enabled_once()` asserting only enabled groups are queried.

```python
@pytest.mark.asyncio
async def test_reenabled_group_catches_up_messages_seen_while_disabled(tmp_path: Path) -> None:
    gateway = FakeTelegramGateway({-1001: [make_video(-1001, i) for i in (5, 6, 7)]})
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = ScannerCoordinator(store, gateway)
    try:
        store.reconcile_targets((GroupTarget(-1001, "群"),))
        store.set_latest_seen(-1001, 5)
        store.reconcile_targets(())
        await coordinator.apply_targets((GroupTarget(-1001, "群"),))
        assert store.job_count() == 2
        assert store.get_group(-1001).latest_seen_id == 7
    finally:
        store.close()
```

- [ ] **Step 2: Run the new coordinator tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coordinator.py -k "reenabled or catch_up_enabled" -q`

Expected: failures because re-enabled groups are not caught up and `catch_up_enabled_once` does not exist.

- [ ] **Step 3: Implement minimal catch-up behavior**

Add `CATCHUP_INTERVAL_SECONDS = 5 * 60`, remember known group state before reconciliation, and call `catch_up_once` only when an added group previously existed with a non-null `latest_seen_id`. Add:

```python
async def catch_up_enabled_once(self) -> None:
    for chat_id in sorted(self.state.enabled_chat_ids()):
        await self.catch_up_once(chat_id)
        await asyncio.sleep(0)

async def run_catchups(self, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await _wait_or_stop(stop, CATCHUP_INTERVAL_SECONDS)
        if not stop.is_set():
            await self.catch_up_enabled_once()
```

Register `coordinator.run_catchups(stop)` as its own service task.

- [ ] **Step 4: Run coordinator and service tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coordinator.py tests/test_service.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/coordinator.py src/tg_video_downloader/service.py tests/test_coordinator.py tests/test_service.py
git commit -m "fix: catch up selected groups after reconnect gaps"
```

### Task 2: Back off inaccessible groups independently

**Files:**
- Modify: `src/tg_video_downloader/coordinator.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write failing backoff tests**

Inject a monotonic clock into `ScannerCoordinator`, make one group raise `GroupAccessError`, and assert repeated `scan_once` calls before 60 seconds do not call the gateway again. Advance the clock to 60 seconds and assert one retry occurs; then return success and assert the next failure starts again at 60 seconds.

```python
clock = FakeClock()
coordinator = ScannerCoordinator(store, gateway, monotonic=clock)
assert await coordinator.scan_once(-1001) is False
assert await coordinator.scan_once(-1001) is False
assert gateway.older_calls[-1001] == 1
clock.advance(60)
assert await coordinator.scan_once(-1001) is False
assert gateway.older_calls[-1001] == 2
```

- [ ] **Step 2: Run the targeted test and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coordinator.py -k backoff -q`

Expected: failure because every call currently reaches Telegram.

- [ ] **Step 3: Implement per-group access backoff**

Add `ACCESS_RETRY_DELAYS = (60, 300, 1800)`, `_access_failures`, `_access_retry_at`, `_can_access`, `_record_access_failure`, and `_record_access_success`. Apply them to `catch_up_once` and `scan_once`. A skipped access returns without clearing the stored group error; a successful Telegram call clears both in-memory backoff and `groups.access_error`.

- [ ] **Step 4: Run coordinator tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coordinator.py -q`

Expected: all coordinator tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/coordinator.py tests/test_coordinator.py tests/fakes.py
git commit -m "fix: back off inaccessible Telegram groups"
```

### Task 3: Make heartbeat state trustworthy and complete

**Files:**
- Modify: `src/tg_video_downloader/worker.py`
- Modify: `src/tg_video_downloader/service.py`
- Modify: `src/tg_video_downloader/gui/controller.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_service.py`
- Test: `tests/test_gui_controller.py`

- [ ] **Step 1: Write failing tests for current task, startup errors, and stale heartbeats**

Cover these behaviors separately:

```python
assert worker.current_file is None
# while the fake gateway is blocked in download_message:
assert worker.current_file == final_path.name
# after completion or exception:
assert worker.current_file is None
```

```python
result = await DownloaderService(paths, factory).run()
assert result == 2
assert HeartbeatWriter(paths.heartbeat).read()["status"] == "needs_config"
```

```python
snapshot = controller.read_status(now=fixed_now)
assert snapshot["status"] == "stale"
assert snapshot["reported_status"] == "running"
```

- [ ] **Step 2: Run targeted tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_worker.py tests/test_service.py tests/test_gui_controller.py -k "current_file or needs_config or stale" -q`

Expected: failures because the new state is not produced or interpreted.

- [ ] **Step 3: Implement current-task and startup heartbeat state**

Add a read-only `DownloadWorker.current_file` property. Set it immediately before disk/download work and clear it in `finally` after a job is claimed. Pass the worker to `_snapshot` and `_write_heartbeat`, adding `current_file` only when non-null.

Initialize `HeartbeatWriter` and non-secret logging before loading configuration. Convert missing/invalid config or credentials into a `needs_config` heartbeat with an error and exit code 2. Preserve `needs_login` and `error` handling.

Extend `GuiController.read_status` with an injectable `now`; parse `updated_at`, and turn `running` into `stale` when older than 15 seconds.

- [ ] **Step 4: Run targeted and full component tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_worker.py tests/test_service.py tests/test_gui_controller.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tg_video_downloader/worker.py src/tg_video_downloader/service.py src/tg_video_downloader/gui/controller.py tests/test_worker.py tests/test_service.py tests/test_gui_controller.py
git commit -m "feat: report trustworthy downloader health"
```

### Task 4: Add privacy-safe local and online diagnostics

**Files:**
- Create: `src/tg_video_downloader/diagnostics.py`
- Create: `tests/test_diagnostics.py`

- [ ] **Step 1: Write failing diagnostic result and local-check tests**

Define the desired public API through tests:

```python
doctor = Doctor(paths, gateway_factory=factory)
report = await doctor.run()
assert {item.key for item in report.checks} >= {
    "project_paths", "python", "dependencies", "config",
    "credentials", "disk", "database", "heartbeat", "telegram"
}
assert report.exit_code in (0, 1, 2)
saved = doctor.save(report)
assert saved.resolve().is_relative_to(paths.root)
```

Add a report-safety test using fake secrets in configuration and gateway exceptions, then assert none of those strings occur in the saved JSON.

- [ ] **Step 2: Run diagnostics tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_diagnostics.py -q`

Expected: import failure because `diagnostics.py` does not exist.

- [ ] **Step 3: Implement result types and local checks**

Create frozen dataclasses:

```python
@dataclass(frozen=True)
class DiagnosticCheck:
    key: str
    status: Literal["pass", "warning", "fail"]
    message: str

@dataclass(frozen=True)
class DiagnosticReport:
    generated_at: str
    checks: tuple[DiagnosticCheck, ...]

    @property
    def exit_code(self) -> int:
        if any(item.status == "fail" for item in self.checks):
            return 2
        return 1 if any(item.status == "warning" for item in self.checks) else 0
```

`Doctor.run()` executes each check independently and converts exceptions to failed checks. Use `shutil.disk_usage`, `importlib.metadata.version`, `ConfigStore`, `sqlite3.connect(...).execute("PRAGMA quick_check")`, and `HeartbeatWriter`. A missing database or heartbeat before first launch is a warning, not a failure.

- [ ] **Step 4: Implement online validation and redacted atomic report writing**

If credentials are valid, connect a gateway, verify authorization, list groups, and compare visible IDs with the configured whitelist. Missing authorization is a failed `telegram` check; network errors are warnings. Always disconnect in `finally`.

Save JSON through a temporary file plus `os.replace` under `logs/diagnostics`. Redact API Hash and phone from every message before serialization.

- [ ] **Step 5: Run diagnostics tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_diagnostics.py -q`

Expected: all diagnostics tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/tg_video_downloader/diagnostics.py tests/test_diagnostics.py
git commit -m "feat: add privacy-safe downloader diagnostics"
```

### Task 5: Expose doctor through CLI and GUI

**Files:**
- Modify: `src/tg_video_downloader/cli.py`
- Modify: `src/tg_video_downloader/gui/controller.py`
- Modify: `src/tg_video_downloader/gui/app.py`
- Modify: `tests/test_gui_controller.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI and controller tests**

Patch `Doctor` with a fake report and assert `main(["doctor"])` prints a one-line summary, saves the report, and returns its exit code. Test `GuiController.run_doctor()` returns `(report, path)` and that the path is under `logs/diagnostics`.

- [ ] **Step 2: Run targeted tests and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_gui_controller.py -k doctor -q`

Expected: failures because `doctor` is not a command and the controller method is absent.

- [ ] **Step 3: Implement CLI and controller orchestration**

Extend parser choices to `("gui", "service", "doctor")`. For doctor, construct `Doctor(paths, TelethonGateway)`, await `run`, save the report, print counts and report path without secrets, and return `report.exit_code`.

Add asynchronous `GuiController.run_doctor()` with the same orchestration so it runs through the existing `AsyncBridge`.

- [ ] **Step 4: Add GUI button and complete status fields**

Add “运行自检” to the run-page actions. While running, disable the button. On success, show pass/warning/fail counts and the report path. Add `permanent_error` and `last_error` display variables; map `snapshot["error"]` or `snapshot["config_error"]` to `last_error`.

- [ ] **Step 5: Run CLI, controller, and GUI-related tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_gui_controller.py tests/test_windows_scripts.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/tg_video_downloader/cli.py src/tg_video_downloader/gui/controller.py src/tg_video_downloader/gui/app.py tests/test_cli.py tests/test_gui_controller.py
git commit -m "feat: expose one-click self-check in CLI and GUI"
```

### Task 6: Documentation, end-to-end verification, and publication readiness

**Files:**
- Modify: `README.md`
- Modify: `docs/verification.md`
- Modify: `tests/test_service_integration.py`
- Modify: `tests/test_windows_scripts.py`

- [ ] **Step 1: Add end-to-end resilience and path-isolation tests**

Add an integration scenario that disables a group, inserts a video, re-enables the group, runs the worker, and verifies the missed video is downloaded exactly once. Assert diagnostic reports and all new temporary files remain under the project root. Update script tests to reject user-profile, AppData, Startup, `schtasks`, and hard-coded C-drive paths.

- [ ] **Step 2: Run integration tests and verify RED if any wiring is incomplete**

Run: `.venv/Scripts/python.exe -m pytest tests/test_service_integration.py tests/test_windows_scripts.py -q`

Expected: pass after all previous tasks; any failure identifies missing cross-component wiring and must be fixed without weakening assertions.

- [ ] **Step 3: Document the user workflow**

Update README with the GUI “运行自检” workflow, `doctor` command, report location, status meanings, periodic catch-up interval, access backoff, and the guarantee that diagnostic files remain under the project directory. Record verification evidence in `docs/verification.md`.

- [ ] **Step 4: Run fresh full verification**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
.venv\Scripts\python.exe -m pip check
git diff --check
git status --short --branch
```

Expected: all tests pass, no broken requirements, no whitespace errors, and only intended source/test/doc changes before the final commit.

- [ ] **Step 5: Commit**

```powershell
git add README.md docs/verification.md tests/test_service_integration.py tests/test_windows_scripts.py
git commit -m "docs: explain resilience and self-check workflow"
```

- [ ] **Step 6: Review before merge or publication**

Run the requesting-code-review workflow, address verified findings with regression tests, then repeat the full verification command. Do not push until the user explicitly asks to publish this new version.
