# Background Search IPC and Telegram Session Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate video-search `database is locked` failures by reusing the downloader's connected Telegram gateway while it is running, while preserving direct search when the downloader is stopped.

**Architecture:** The downloader owns the only active file-backed Telethon client through a Windows byte lock and exposes one authenticated, single-request search channel on an ephemeral `127.0.0.1` port. The GUI routes to that channel whenever the downloader lock is active and only creates a temporary gateway when the downloader is stopped; a direct-connect race may retry IPC once but may never open the same session concurrently.

**Tech Stack:** Python 3.12 standard library (`asyncio`, `json`, `secrets`, `msvcrt` through the existing lock wrapper), Telethon, Tkinter/ttk, SQLite, pytest, PowerShell, Git

---

### Task 1: Create an isolated, verified v0.3.4 workspace

**Files:**
- Use: `.worktrees/background-search-ipc-v034/`
- Verify: `scripts/bootstrap.ps1`
- Verify: `scripts/check.ps1`
- Preserve: `.runtime/telegram.session`
- Preserve: the active downloader and supervisor processes

- [ ] **Step 1: Load the required execution skills**

Use `using-git-worktrees` before creating the workspace. Use `test-driven-development` for Tasks 2–8, `requesting-code-review` before the final verification commit, `verification-before-completion` before any success claim, and `finishing-a-development-branch` only after every gate passes.

- [ ] **Step 2: Verify isolation prerequisites from the main checkout**

Run from `D:\Codex Project\Telegram自动化脚本`:

```powershell
git status --short --branch
git rev-parse --git-dir
git rev-parse --git-common-dir
git check-ignore -v .worktrees
git branch --list codex/background-search-ipc-v034
Test-Path -LiteralPath .worktrees\background-search-ipc-v034
git worktree list --porcelain
```

Expected: `master` is clean and ahead only by the two committed design-document commits, `.worktrees` is ignored, and neither the target branch nor target path exists. Leave `C:\Users\luojixiang1\.codex\worktrees\dcf3\Telegram自动化脚本` and branch `codex/download-policy-progress-resume` untouched.

- [ ] **Step 3: Create the feature worktree**

```powershell
git worktree add `
  'D:\Codex Project\Telegram自动化脚本\.worktrees\background-search-ipc-v034' `
  -b codex/background-search-ipc-v034
Set-Location 'D:\Codex Project\Telegram自动化脚本\.worktrees\background-search-ipc-v034'
```

Expected: the new branch starts at the committed design and implementation plan. Do not copy `.runtime`, credentials, session files, state databases, downloads, or the main checkout's `.venv` into the worktree.

- [ ] **Step 4: Bootstrap only the isolated environment**

```powershell
& .\scripts\bootstrap.ps1
& .\.venv\Scripts\python.exe -m pip check
```

Expected: the worktree-local editable install succeeds, `cryptg acceleration ready` is reported, and pip reports `No broken requirements found.` No dependency is added for IPC.

- [ ] **Step 5: Run and record the clean baseline**

```powershell
& .\scripts\check.ps1
```

Expected: all 380 existing tests, compile checks, and project-local path checks pass before implementation. If the count differs, stop and reconcile the branch base before changing code.

- [ ] **Step 6: Confirm the real downloader remains active using read-only checks**

From the worktree, inspect the original root `D:\Codex Project\Telegram自动化脚本` with the existing lock and heartbeat readers. Take two snapshots eight seconds apart.

Expected: downloader and supervisor locks are active, the normalized heartbeat remains `running`, and `updated_at` advances. Do not stop, restart, connect to, or write into the real runtime.

### Task 2: Make Telegram session ownership precede client construction

**Files:**
- Modify: `src/tg_video_downloader/paths.py`
- Modify: `src/tg_video_downloader/gateway.py`
- Modify: `tests/test_paths.py`
- Modify: `tests/test_gateway.py`
- Modify: `tests/test_windows.py`

- [ ] **Step 1: Add failing path tests for the endpoint and ownership lock**

Add assertions to the existing `ProjectPaths.from_root()` test in `tests/test_paths.py`:

```python
assert paths.search_endpoint == root / ".runtime" / "search-endpoint.json"
assert paths.telegram_client_lock == root / ".runtime" / "telegram-client.lock"
```

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_paths.py
```

Expected: RED because the two paths do not exist yet.

- [ ] **Step 2: Add the two derived runtime paths**

Add these properties to `ProjectPaths` in `src/tg_video_downloader/paths.py` so existing dataclass construction remains compatible:

```python
@property
def search_endpoint(self) -> Path:
    return self.runtime / "search-endpoint.json"

@property
def telegram_client_lock(self) -> Path:
    return self.runtime / "telegram-client.lock"
```

Run `tests/test_paths.py` again. Expected: GREEN.

- [ ] **Step 3: Add RED tests proving the lock is acquired before the client factory runs**

In `tests/test_gateway.py`, add a lifecycle-capable fake client and these cases:

```python
class LifecycleClient:
    def __init__(self, *, connect_error: Exception | None = None) -> None:
        self.connect_error = connect_error
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


@pytest.mark.asyncio
async def test_gateway_defers_client_factory_until_connect(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    clients: list[LifecycleClient] = []

    def factory(*args: object, **kwargs: object) -> LifecycleClient:
        client = LifecycleClient()
        clients.append(client)
        return client

    gateway = TelethonGateway(paths, Credentials(12345, "hash"), client_factory=factory)

    assert clients == []
    await gateway.connect()
    assert len(clients) == 1
    assert clients[0].connect_calls == 1
    await gateway.disconnect()
    assert clients[0].disconnect_calls == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-lock behavior")
@pytest.mark.asyncio
async def test_gateway_holds_session_lock_while_factory_runs(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)

    def factory(*args: object, **kwargs: object) -> LifecycleClient:
        with pytest.raises(RuntimeError, match="factory observed lock"):
            with SingleInstance(
                paths.telegram_client_lock,
                already_running_message="factory observed lock",
            ):
                raise AssertionError("factory must run after lock acquisition")
        return LifecycleClient()

    gateway = TelethonGateway(
        paths,
        Credentials(12345, "hash"),
        client_factory=factory,
    )
    await gateway.connect()
    await gateway.disconnect()


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-lock behavior")
@pytest.mark.asyncio
async def test_second_gateway_fails_before_constructing_client(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    clients: list[LifecycleClient] = []

    def factory(*args: object, **kwargs: object) -> LifecycleClient:
        client = LifecycleClient()
        clients.append(client)
        return client

    first = TelethonGateway(paths, Credentials(12345, "hash"), client_factory=factory)
    second = TelethonGateway(paths, Credentials(12345, "hash"), client_factory=factory)
    await first.connect()
    try:
        with pytest.raises(TelegramSessionInUseError, match="后台使用"):
            await second.connect()
        assert len(clients) == 1
    finally:
        await first.disconnect()

    await second.connect()
    assert len(clients) == 2
    await second.disconnect()
```

Also add tests named:

- `test_gateway_connect_failure_releases_session_lock`
- `test_gateway_disconnect_failure_releases_session_lock`
- `test_gateway_connect_and_disconnect_are_idempotent`

Each test must create a second gateway after the failure and prove its factory can run and connect. For disconnect failure, make the fake raise after incrementing `disconnect_calls`; assert the original mapped exception is surfaced and the next gateway still connects.

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gateway.py -k "defers_client_factory or second_gateway or session_lock or idempotent"
```

Expected: RED because construction is eager and no session-specific error exists.

- [ ] **Step 4: Add a stable ownership error and lazy client lifecycle**

In `src/tg_video_downloader/gateway.py`, import `SingleInstance` and add:

```python
class TelegramSessionInUseError(RuntimeError):
    pass
```

Change `TelethonGateway.__init__()` to validate and retain construction inputs without calling the factory:

```python
self._paths = paths
self._credentials = credentials
self._client_factory = client_factory
self._client: Any | None = None
self._session_guard: SingleInstance | None = None
self._event_callback: Callable[[Any], Awaitable[None]] | None = None
self._password_required = False
self._qr_login: Any | None = None
```

Implement the lifecycle with this exact ordering:

```python
async def connect(self) -> None:
    if self._client is not None:
        return
    guard = SingleInstance(
        self._paths.telegram_client_lock,
        already_running_message="Telegram 会话正在由后台使用",
    )
    try:
        guard.__enter__()
    except RuntimeError as error:
        raise TelegramSessionInUseError("Telegram 会话正在由后台使用") from error
    self._session_guard = guard
    client: Any | None = None
    try:
        client = self._client_factory(
            str(self._paths.session),
            self._credentials.api_id,
            self._credentials.api_hash,
            auto_reconnect=True,
            connection_retries=-1,
            retry_delay=5,
            flood_sleep_threshold=60,
        )
        self._client = client
        await client.connect()
    except BaseException as error:
        await self._release_client(client)
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, Exception):
            raise _mapped_error(error) from error
        raise

async def disconnect(self) -> None:
    client = self._client
    self._client = None
    mapped: Exception | None = None
    try:
        if client is not None:
            await client.disconnect()
    except Exception as error:
        mapped = _mapped_error(error)
    finally:
        self._release_session_guard()
        self._qr_login = None
        self._password_required = False
    if mapped is not None:
        raise mapped

async def _release_client(self, client: Any | None) -> None:
    self._client = None
    try:
        if client is not None:
            await client.disconnect()
    except Exception:
        pass
    finally:
        self._release_session_guard()
        self._qr_login = None
        self._password_required = False

def _release_session_guard(self) -> None:
    guard = self._session_guard
    self._session_guard = None
    if guard is not None:
        guard.__exit__(None, None, None)

def _require_client(self) -> Any:
    if self._client is None:
        raise RuntimeError("Telegram 客户端尚未连接")
    return self._client
```

For every remaining gateway operation, bind `client = self._require_client()` before its `try` block and replace direct `self._client` calls with `client`. This includes authorization, login, QR login, logout, group listing, search, event handlers, message iteration, and download. Keep `asyncio.CancelledError` propagation in search unchanged.

- [ ] **Step 5: Adapt existing gateway fakes to the now-real lifecycle**

Every test that constructs `TelethonGateway` and calls a Telegram operation must now call `await gateway.connect()` first and `await gateway.disconnect()` in `finally`. Add async `connect()` and `disconnect()` methods to the specialized fake clients used by search, iteration, handlers, login, and download. Do not restore eager construction merely to preserve tests.

Use this check to find every construction site and every unresolved direct client access:

```powershell
rg -n "TelethonGateway\(" tests/test_gateway.py
rg -n "self\._client" src/tg_video_downloader/gateway.py
```

Expected: all test gateways have explicit lifecycle coverage; production direct accesses remain only in lifecycle helpers and `_require_client()`.

- [ ] **Step 6: Add a Windows subprocess abandonment test**

In `tests/test_windows.py`, follow the existing subprocess lock-test pattern to acquire `telegram-client.lock` in a child process, terminate the child, and then acquire the same lock in the parent with `SingleInstance`.

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gateway.py tests/test_windows.py
```

Expected: GREEN; process exit releases the byte lock, client construction occurs only while the lock is held, and all existing gateway behavior still passes.

- [ ] **Step 7: Commit the session ownership boundary**

```powershell
git add `
  src/tg_video_downloader/paths.py `
  src/tg_video_downloader/gateway.py `
  tests/test_paths.py `
  tests/test_gateway.py `
  tests/test_windows.py
git commit -m "fix: serialize Telegram session ownership"
```

### Task 3: Define strict, private search IPC data and endpoint storage

**Files:**
- Create: `src/tg_video_downloader/search_ipc.py`
- Create: `tests/test_search_ipc.py`

- [ ] **Step 1: Write RED codec and endpoint tests**

Create `tests/test_search_ipc.py` with fixtures that construct one timezone-aware `MessageInfo`, `VideoSearchResult`, and `SelectableVideo`. Add these tests:

- `test_request_json_round_trip_preserves_typed_fields`
- `test_response_json_round_trip_preserves_message_and_queue_state`
- `test_request_rejects_wrong_schema_operation_token_and_extra_fields`
- `test_request_rejects_naive_dates_boolean_ids_and_invalid_limit`
- `test_response_rejects_extra_fields_invalid_enum_and_naive_message_date`
- `test_response_rejects_more_than_100_results_and_non_normalized_caption`
- `test_request_and_response_enforce_byte_limits`
- `test_endpoint_round_trip_accepts_only_ipv4_loopback`
- `test_endpoint_rejects_extra_fields_wrong_version_and_invalid_pid_port`
- `test_endpoint_write_is_atomic_and_never_contains_search_data`
- `test_endpoint_delete_requires_matching_token`

The valid test request must use a unique keyword such as `ipc-private-keyword-4f692d`, and the endpoint file assertion must prove that neither this keyword nor the fixture caption/file name appears in `.runtime/search-endpoint.json`.

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_search_ipc.py
```

Expected: RED because the module does not exist.

- [ ] **Step 2: Add the protocol constants, models, and stable errors**

Create `src/tg_video_downloader/search_ipc.py` with these public definitions:

```python
SCHEMA_VERSION = 1
LOOPBACK_HOST = "127.0.0.1"
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 3.0
INITIAL_READ_TIMEOUT_SECONDS = 5.0
ENDPOINT_ERROR = "后台检索通道尚未就绪，请稍后重试"
STALE_ENDPOINT_ERROR = "后台检索通道已失效，请稍后重试"
BUSY_ERROR = "已有检索正在进行，请稍后重试"
STOPPED_ERROR = "后台已停止，本次检索未完成"


class SearchChannelError(RuntimeError):
    pass


class SearchChannelBusyError(SearchChannelError):
    pass


@dataclass(frozen=True)
class SearchEndpoint:
    schema_version: int
    host: str
    port: int
    token: str
    pid: int
    started_at: datetime


@dataclass(frozen=True)
class SearchRequest:
    chat_id: int
    keyword: str
    start_utc: datetime | None
    end_utc: datetime | None
    limit: int
```

Use fixed-key validation rather than permissive `.get()` calls. Reject booleans wherever an integer is required, require timezone-aware ISO datetimes, require `operation == "search_videos"`, require limits accepted by `validate_search_limit()`, and reject keywords whose UTF-8 encoded request exceeds `MAX_REQUEST_BYTES`.

- [ ] **Step 3: Implement exact typed codecs**

Add internal encode/decode functions for:

```python
def encode_request(request: SearchRequest, token: str) -> bytes
def decode_request(raw: bytes, expected_token: str) -> SearchRequest
def encode_success(items: tuple[SelectableVideo, ...]) -> bytes
def encode_error(message: str) -> bytes
def decode_response(raw: bytes) -> tuple[SelectableVideo, ...]
```

The request object has exactly these keys:

```python
{
    "schema_version": 1,
    "token": token,
    "operation": "search_videos",
    "chat_id": request.chat_id,
    "keyword": request.keyword,
    "start_utc": request.start_utc.isoformat() if request.start_utc else None,
    "end_utc": request.end_utc.isoformat() if request.end_utc else None,
    "limit": request.limit,
}
```

The success response has exactly `schema_version`, `ok`, and `results`; the error response has exactly `schema_version`, `ok`, and `error`. Serialize every `MessageInfo` field, `duration_seconds`, `caption`, and `SearchQueueState.value`. Reject more than 100 results, a caption that is not already single-line normalized to at most 120 characters, invalid optional integer/string fields, and encoded output over `MAX_RESPONSE_BYTES`. On decode, reconstruct `MessageInfo`, `VideoSearchResult`, and `SelectableVideo` rather than returning dictionaries. Append one newline only at the stream boundary, not inside these codec return values.

- [ ] **Step 4: Implement atomic endpoint ownership**

Add:

```python
def write_search_endpoint(paths: ProjectPaths, endpoint: SearchEndpoint) -> None
def read_search_endpoint(paths: ProjectPaths) -> SearchEndpoint
def remove_search_endpoint(paths: ProjectPaths, token: str) -> None
```

`write_search_endpoint()` must call `paths.ensure_directories()`, write UTF-8 JSON to a uniquely named temporary sibling using exclusive creation, flush and `os.fsync()` the file, then use `os.replace()` onto `paths.search_endpoint`. Its JSON contains exactly `schema_version`, `host`, `port`, `token`, `pid`, and `started_at`.

`read_search_endpoint()` must reject missing, malformed, oversized, extra-field, non-loopback, invalid-port, invalid-PID, empty-token, wrong-version, and naive-time records with `SearchChannelError(ENDPOINT_ERROR)` or `SearchChannelError(STALE_ENDPOINT_ERROR)` as appropriate. It must not include raw JSON or the token in an exception.

`remove_search_endpoint()` must read the current file, compare its token with `secrets.compare_digest()`, and unlink only on a match. Missing or malformed files are harmless during shutdown; an endpoint owned by a newer process remains intact.

- [ ] **Step 5: Run strict data tests and inspect privacy**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_search_ipc.py -k "round_trip or rejects or byte_limits or endpoint"
rg -n "keyword|caption|original_name|results" .runtime\search-endpoint.json
```

Expected: codec and endpoint tests pass. The `rg` command should either report that the isolated worktree endpoint does not exist or return no search content; it must never inspect the real main-checkout endpoint.

- [ ] **Step 6: Commit the protocol and endpoint store**

```powershell
git add src/tg_video_downloader/search_ipc.py tests/test_search_ipc.py
git commit -m "feat: define private search IPC protocol"
```

### Task 4: Implement the lightweight loopback search server and client

**Files:**
- Modify: `src/tg_video_downloader/search_ipc.py`
- Modify: `tests/test_search_ipc.py`

- [ ] **Step 1: Add RED loopback lifecycle tests**

Add async tests using a temporary `ProjectPaths` and the real `asyncio.start_server()` path:

- `test_server_publishes_random_loopback_endpoint_and_client_round_trips`
- `test_client_rejects_endpoint_pid_mismatch_before_connecting`
- `test_client_reports_missing_refused_and_invalid_token_endpoints`
- `test_server_allows_only_one_active_search`
- `test_client_cancellation_closes_socket_and_cancels_only_search`
- `test_server_close_cancels_active_search_and_removes_owned_endpoint`
- `test_old_server_close_cannot_remove_new_server_endpoint`
- `test_server_rejects_oversized_malformed_and_extra_field_requests`
- `test_server_internal_error_returns_stable_message_without_token`

For cancellation, use events instead of sleeps:

```python
started = asyncio.Event()
cancelled = asyncio.Event()

async def blocking_search(request: SearchRequest) -> tuple[SelectableVideo, ...]:
    started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        cancelled.set()
        raise
```

Start the client task, wait for `started`, cancel the client, await it with `pytest.raises(asyncio.CancelledError)`, then wait for `cancelled`. Assert the server remains open and can serve a subsequent request after replacing the blocking behavior.

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_search_ipc.py -k "server or client or cancellation"
```

Expected: RED because server and client classes do not exist.

- [ ] **Step 2: Add the client protocol and single-request client**

Add this injectable boundary:

```python
class SearchClientProtocol(Protocol):
    async def search_videos(
        self,
        request: SearchRequest,
        *,
        expected_pid: int | None,
    ) -> tuple[SelectableVideo, ...]:
        raise NotImplementedError
```

Implement `SearchIpcClient(paths)` so `search_videos()` performs exactly one attempt:

1. Read and validate the endpoint.
2. If `expected_pid` is supplied, require an exact PID match.
3. Open `127.0.0.1:endpoint.port` by wrapping `asyncio.open_connection(endpoint.host, endpoint.port, limit=MAX_RESPONSE_BYTES + 1)` in `asyncio.wait_for()` with `CONNECT_TIMEOUT_SECONDS`.
4. Write `encode_request(request, endpoint.token) + b"\n"`, then drain.
5. Read one line and reject EOF, missing newline, or a payload over `MAX_RESPONSE_BYTES`.
6. Return `decode_response()`.
7. On cancellation or any exit, close the writer and await `wait_closed()` without masking the active error.

Map missing endpoint to `ENDPOINT_ERROR`; PID mismatch, refused connection, timeout, malformed response, and token/protocol failure to `STALE_ENDPOINT_ERROR`. Do not retry, poll, log the payload, or apply a timeout to the whole Telegram search after the connection has been established.

- [ ] **Step 3: Implement the one-search server lifecycle**

Define `SearchHandler` as an async callable from `SearchRequest` to a tuple of `SelectableVideo`. Implement `SearchIpcServer(paths, search_handler)` with:

```python
async def start(self) -> SearchEndpoint
async def close(self) -> None
async def _handle_client(
    self,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None
```

`start()` must:

- reject a second start on the same object;
- call `asyncio.start_server()` with host `127.0.0.1`, port `0`, and request-size stream limit;
- read the actual port from the bound socket;
- create a fresh `secrets.token_urlsafe(32)` token and timezone-aware UTC `started_at`;
- atomically publish the endpoint only after the socket is listening;
- close the socket if endpoint publication fails.

`_handle_client()` must:

- read one newline-terminated request by wrapping `reader.readline()` in `asyncio.wait_for()` with `INITIAL_READ_TIMEOUT_SECONDS`, and enforce `MAX_REQUEST_BYTES`;
- decode and authenticate before invoking the handler;
- reject a request with `SearchChannelBusyError(BUSY_ERROR)` when `_active_search` is not done;
- create one handler task and one `reader.read(1)` disconnect-watcher task;
- call `asyncio.wait((search_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED)`;
- cancel only the handler task when EOF wins;
- cancel only the disconnect watcher when search wins;
- write one success/error line when the client is still connected;
- always close the writer and clear `_active_search` only when it still refers to that request.

`close()` must close the listener first, cancel and await the active search task, then remove only its own token-matching endpoint. It must be idempotent. A `SearchChannelError` raised by the handler returns its already-safe message; unexpected handler exceptions return `后台检索失败，请稍后重试`; `CancelledError` remains cancellation and is never serialized as an internal traceback.

- [ ] **Step 4: Run all IPC tests and prove the server is idle without polling**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_search_ipc.py
rg -n "create_task|start_server|sleep\(|call_later|while True|Thread|Process|subprocess" `
  src/tg_video_downloader/search_ipc.py
```

Expected: all tests pass. The only request-time tasks are the handler and EOF watcher; there is no sleep loop, timer, file poller, thread, subprocess, or third-party import.

- [ ] **Step 5: Commit the loopback transport**

```powershell
git add src/tg_video_downloader/search_ipc.py tests/test_search_ipc.py
git commit -m "feat: add loopback video search channel"
```

### Task 5: Host search on the downloader's existing gateway

**Files:**
- Modify: `src/tg_video_downloader/service.py`
- Modify: `tests/test_service.py`
- Modify: `tests/fakes.py`

- [ ] **Step 1: Add RED service integration tests**

Extend `FakeTelegramGateway` in `tests/fakes.py` with configurable `search_results`, `search_started`, `search_release`, `search_cancelled`, and a `search_calls` list, while keeping defaults inert for unrelated tests.

Add these async service tests:

- `test_service_publishes_search_endpoint_after_gateway_and_coordinator_start`
- `test_service_search_reuses_connected_gateway_and_returns_queue_states`
- `test_service_rejects_chat_outside_current_enabled_config`
- `test_service_config_reload_changes_allowed_search_targets`
- `test_service_stop_closes_endpoint_before_gateway_disconnect`
- `test_service_stop_cancels_search_without_cancelling_download_worker`
- `test_service_search_and_download_complete_on_same_gateway`

The reuse test must prepopulate `StateStore` with one queued and one completed message, return those messages plus one unseen result from the fake gateway, call the real `SearchIpcClient`, and assert the three returned states are `QUEUED`, `COMPLETED`, and `AVAILABLE`. Assert the gateway factory was called once for service startup and not during search.

The ordering test must record lifecycle events and require:

```python
assert events.index("gateway_connected") < events.index("endpoint_written")
assert events.index("endpoint_removed") < events.index("gateway_disconnected")
```

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_service.py -k "search_endpoint or service_search"
```

Expected: RED because the service does not host IPC.

- [ ] **Step 2: Inject the server factory without changing production defaults**

Extend `DownloaderService.__init__()` with a keyword-only factory whose default builds `SearchIpcServer`. Retain the existing two positional arguments so current callers do not change:

```python
def __init__(
    self,
    paths: ProjectPaths,
    gateway_factory: Callable[[ProjectPaths, Credentials], TelegramGateway],
    *,
    search_server_factory: Callable[
        [ProjectPaths, SearchHandler], SearchIpcServer
    ] = SearchIpcServer,
) -> None:
```

Store the factory and do not create a server in `__init__()`.

- [ ] **Step 3: Add the in-process search callback**

Add an async helper whose inputs are the current request, open `StateStore`, connected gateway, and mutable current-config holder:

```python
async def _search_videos(
    self,
    request: SearchRequest,
    state: StateStore,
    gateway: TelegramGateway,
    config_holder: list[AppConfig],
) -> tuple[SelectableVideo, ...]:
    selected_ids = {group.chat_id for group in config_holder[0].groups}
    if request.chat_id not in selected_ids:
        raise SearchChannelError("只能检索当前已监听的群组或频道")
    try:
        results = await gateway.search_videos(
            request.chat_id,
            request.keyword,
            request.start_utc,
            request.end_utc,
            request.limit,
        )
    except asyncio.CancelledError:
        raise
    except (
        AuthenticationRequiredError,
        GroupAccessError,
        TransientTelegramError,
        ValueError,
    ) as error:
        raise SearchChannelError(str(error)) from error
    statuses = state.job_statuses(
        tuple(
            (result.message.chat_id, result.message.message_id)
            for result in results
        )
    )
    return tuple(
        SelectableVideo(
            result=result,
            queue_state=queue_state_for(
                statuses.get(
                    (result.message.chat_id, result.message.message_id)
                )
            ),
        )
        for result in results
    )
```

Keep this on the downloader event-loop thread because the gateway and the already-open SQLite connection belong there. Do not call `asyncio.to_thread()`.

- [ ] **Step 4: Insert the server into the exact service lifecycle**

In `_run_connected()`:

1. Declare `search_server` before the lifecycle `try` so cleanup always sees it, and import `SearchChannelError`, `SearchHandler`, `SearchIpcServer`, `SearchRequest`, `SelectableVideo`, `queue_state_for`, `GroupAccessError`, and `TransientTelegramError` for the callback above.
2. Connect and authorize the gateway as today.
3. Start the coordinator as today.
4. Construct `SearchIpcServer` with a closure calling `_search_videos()` and `await search_server.start()`.
5. Only then write the first `running` heartbeat and create the existing worker/config/heartbeat/stop tasks.
6. In `finally`, set stop, close the server, cancel/gather existing tasks, disconnect the gateway, write the final stopped heartbeat, and close state.

The shutdown sequence must be:

```python
stop.set()
if search_server is not None:
    try:
        await search_server.close()
    except Exception:
        logger.exception("关闭后台检索通道时发生错误")
for task in tasks:
    if not task.done():
        task.cancel()
if tasks:
    await asyncio.gather(*tasks, return_exceptions=True)
```

Keep the existing protected gateway-disconnect and state-close behavior after this block. A server-close failure must be logged but must not skip task cancellation, gateway disconnect, final heartbeat, or state close. If server startup fails, no `running` heartbeat may be published and gateway/state cleanup must still happen.

- [ ] **Step 5: Run service and coordinator regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_service.py `
  tests/test_coordinator.py `
  tests/test_worker.py
```

Expected: GREEN; search uses the same gateway, config reload is honored at request time, queue states are returned, and stop order is deterministic.

- [ ] **Step 6: Commit downloader-side search hosting**

```powershell
git add `
  src/tg_video_downloader/service.py `
  tests/test_service.py `
  tests/fakes.py
git commit -m "feat: serve search from the downloader gateway"
```

### Task 6: Route GUI searches without unsafe fallback

**Files:**
- Modify: `src/tg_video_downloader/gui/controller.py`
- Modify: `tests/test_gui_controller.py`

- [ ] **Step 1: Add RED routing matrix tests**

Add an injectable fake search client that records `SearchRequest` and `expected_pid`, and extend the controller test factory to accept `background_running` and `search_client_factory`. Add tests:

- `test_search_uses_ipc_and_never_constructs_gateway_while_background_runs`
- `test_search_passes_fresh_heartbeat_pid_to_ipc`
- `test_search_running_without_endpoint_surfaces_retryable_channel_error`
- `test_search_running_with_stale_endpoint_never_falls_back_to_gateway`
- `test_search_stopped_uses_direct_gateway_and_attaches_queue_states`
- `test_search_direct_session_race_retries_ipc_once`
- `test_search_direct_session_race_with_unavailable_ipc_returns_channel_error`
- `test_search_non_session_direct_error_does_not_try_ipc`
- `test_search_validation_happens_before_route_selection`
- `test_search_ipc_result_is_not_reopened_through_state_factory`

For the running-path test, replace `gateway_factory` with a function that raises `AssertionError("background search must not create gateway")`. For the unsafe-fallback tests, assert both factory and connect counts remain zero.

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_controller.py -k "search_"
```

Expected: RED because the controller always creates a temporary gateway.

- [ ] **Step 2: Add injectable route dependencies**

Extend `GuiController.__init__()` with keyword-only defaults:

```python
search_client_factory: Callable[[ProjectPaths], SearchClientProtocol] = SearchIpcClient,
background_running: Callable[[ProjectPaths], bool] = downloader_is_running,
```

Store both callables. This leaves `gui/runtime.py` unchanged because its existing `GuiController(paths, TelethonGateway)` call receives production defaults.

- [ ] **Step 3: Split direct search from route selection**

Move the current gateway lifecycle into:

```python
async def _search_videos_direct(
    self,
    credentials: Credentials,
    request: SearchRequest,
) -> tuple[SelectableVideo, ...]:
    gateway = self.gateway_factory(self.paths, credentials)
    active_error: BaseException | None = None
    try:
        await gateway.connect()
        if not await gateway.is_authorized():
            raise AuthenticationRequiredError("请先完成 Telegram 登录")
        results = await gateway.search_videos(
            request.chat_id,
            request.keyword,
            request.start_utc,
            request.end_utc,
            request.limit,
        )
    except BaseException as error:
        active_error = error
        raise
    finally:
        try:
            await gateway.disconnect()
        except Exception:
            if active_error is None:
                raise
    return await asyncio.to_thread(self._attach_queue_states, results)
```

Add:

```python
def _running_heartbeat_pid(self) -> int | None:
    snapshot = self.read_status()
    pid = snapshot.get("pid")
    if snapshot.get("status") == "running" and isinstance(pid, int):
        return pid
    return None

async def _search_videos_background(
    self,
    request: SearchRequest,
) -> tuple[SelectableVideo, ...]:
    client = self.search_client_factory(self.paths)
    return await client.search_videos(
        request,
        expected_pid=self._running_heartbeat_pid(),
    )
```

- [ ] **Step 4: Implement the no-unsafe-fallback routing order**

Keep the existing login-active, target, credential, date, and limit validation first. Construct one `SearchRequest`, then route:

```python
if self.background_running(self.paths):
    return await self._search_videos_background(request)
try:
    return await self._search_videos_direct(credentials, request)
except TelegramSessionInUseError:
    return await self._search_videos_background(request)
```

Do not catch `SearchChannelError` to fall back to direct mode. Do not loop, sleep, auto-retry, stop the downloader, copy the session, or replace `database is locked` text after the fact. The ownership lock prevents the SQLite conflict; the route communicates a stable retryable error.

- [ ] **Step 5: Run controller and search-page regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gui_controller.py `
  tests/test_gui_search_page.py
```

Expected: GREEN. The existing page still clears failed results, restores controls, supports cancel, and receives `SelectableVideo` directly on IPC routes.

- [ ] **Step 6: Commit GUI routing**

```powershell
git add `
  src/tg_video_downloader/gui/controller.py `
  tests/test_gui_controller.py
git commit -m "fix: route active searches through the downloader"
```

### Task 7: Prove cancellation, privacy, and lightweight behavior end to end

**Files:**
- Create: `tests/test_background_search_integration.py`
- Modify: `tests/test_gui_search_page.py`
- Modify: `tests/test_service.py`

- [ ] **Step 1: Add a real-loopback controller/service integration harness**

Create `tests/test_background_search_integration.py`. Use one temporary `ProjectPaths`, a real `StateStore`, a fake connected gateway, a real `SearchIpcServer`, and a real `SearchIpcClient`. Do not invoke Telethon or the real runtime.

Add tests:

- `test_running_controller_search_uses_one_gateway_and_real_loopback_channel`
- `test_cancel_from_search_page_reaches_server_without_disconnecting_gateway`
- `test_search_and_existing_download_task_can_finish_independently`
- `test_endpoint_and_runtime_files_never_persist_search_payload`
- `test_search_logs_never_contain_keyword_caption_filename_or_token`
- `test_repeated_searches_do_not_leave_tasks_or_endpoint_temp_files`

Use unique secrets in the privacy tests:

```python
keyword = "private-keyword-91d9d7"
caption = "private-caption-bf7a21"
filename = "private-filename-6c42a8.mp4"
```

After a completed and a cancelled search, read every regular file under the temporary `.runtime` directory as bytes and assert those UTF-8 strings are absent. Capture logs with `caplog` and assert the strings plus the endpoint token are absent from all messages. The endpoint token is allowed only in `search-endpoint.json` while the server is running.

- [ ] **Step 2: Prove page cancellation has the exact scope**

In `tests/test_gui_search_page.py`, drive the existing `CancellableSubmission` cancellation path with a real `SearchIpcClient` connected to the integration server. Assert:

- the client future becomes cancelled;
- the server handler receives `CancelledError`;
- the fake shared gateway's `disconnect_calls` remains zero;
- a synthetic download future remains pending and later completes;
- a second search succeeds after cancellation;
- search controls return to the idle state.

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_background_search_integration.py `
  tests/test_gui_search_page.py -k "cancel or background or private or repeated"
```

Expected: GREEN without fixed sleeps; synchronization uses events and bounded `asyncio.wait_for()` only in tests.

- [ ] **Step 3: Add a startup-gap service regression**

In `tests/test_service.py`, block server startup with an event and prove no `running` heartbeat is written until the endpoint is published. Then release startup and prove heartbeat PID equals endpoint PID.

Also simulate endpoint-start failure and assert gateway disconnect, state close, and absence of `running` heartbeat.

- [ ] **Step 4: Audit source for forbidden persistence and overhead**

```powershell
rg -n "keyword|caption|original_name|token" `
  src/tg_video_downloader/search_ipc.py `
  src/tg_video_downloader/service.py
rg -n "threading|Thread|Process|subprocess|sleep\(|call_later|after\(|poll" `
  src/tg_video_downloader/search_ipc.py `
  src/tg_video_downloader/service.py `
  src/tg_video_downloader/gui/controller.py
git diff master...HEAD -- pyproject.toml
```

Review every match. Expected: search fields appear only in in-memory protocol encoding/decoding and callback invocation; token appears only in endpoint authentication/ownership code; no log statement contains payloads; no new idle loop, thread, process, GUI timer, database table, or dependency exists.

- [ ] **Step 5: Commit integration and boundary evidence**

```powershell
git add `
  tests/test_background_search_integration.py `
  tests/test_gui_search_page.py `
  tests/test_service.py
git commit -m "test: verify shared search lifecycle and privacy"
```

### Task 8: Prepare v0.3.4 candidate metadata and guidance

**Files:**
- Modify: `tests/test_release_metadata.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

- [ ] **Step 1: Change release expectations first**

Update the release metadata test to expect:

```python
assert pyproject["project"]["version"] == "0.3.4"
assert "后台运行时复用同一个 Telegram 连接" in readme
assert "后台停止时仍可直接检索" in readme
assert "不会并发打开同一会话数据库" in readme
assert "取消检索不会中断下载" in readme
```

Keep all existing release-boundary assertions. Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_release_metadata.py
```

Expected: RED because the package and README still describe v0.3.3.

- [ ] **Step 2: Bump the candidate version**

Set in `pyproject.toml`:

```toml
version = "0.3.4"
```

- [ ] **Step 3: Document search behavior and recovery guidance**

Add this concise bullet to the README's daily-use/search section:

```markdown
- v0.3.4 起，后台运行时“视频检索”会复用同一个 Telegram 连接，不会并发打开同一会话数据库；后台停止时仍可直接检索。检索通道仅监听本机回环地址，搜索条件和结果不写入运行文件；取消检索不会中断当前下载。若提示后台检索通道尚未就绪或已失效，请等待后台状态恢复后手动重试。
```

Do not claim automatic retry, LAN access, a second session, or release publication.

- [ ] **Step 4: Verify metadata and reinstall the candidate**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_release_metadata.py
& .\scripts\bootstrap.ps1
& .\.venv\Scripts\python.exe -m pip check
```

Expected: metadata passes, the editable package reports 0.3.4, no dependency was added, and pip reports no broken requirements.

- [ ] **Step 5: Commit candidate metadata**

```powershell
git add pyproject.toml README.md tests/test_release_metadata.py
git commit -m "docs: prepare the v0.3.4 search fix"
```

### Task 9: Review and verify the complete candidate without touching production

**Files:**
- Review: `src/tg_video_downloader/gateway.py`
- Review: `src/tg_video_downloader/search_ipc.py`
- Review: `src/tg_video_downloader/service.py`
- Review: `src/tg_video_downloader/gui/controller.py`
- Review: all modified tests
- Modify: `docs/verification.md`

- [ ] **Step 1: Run focused high-risk regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gateway.py `
  tests/test_search_ipc.py `
  tests/test_service.py `
  tests/test_gui_controller.py `
  tests/test_gui_search_page.py `
  tests/test_background_search_integration.py `
  tests/test_worker.py `
  tests/test_state.py
```

Expected: all session, protocol, service, routing, cancellation, queue-state, and download regressions pass.

- [ ] **Step 2: Request an independent code review**

Use `requesting-code-review` against `master...HEAD`. Ask the reviewer to focus on:

- lock-before-client construction and release on every exit path;
- cancellation propagation without disconnecting the shared gateway;
- endpoint ownership and stale PID handling;
- strict protocol size/type/field validation;
- absence of unsafe direct fallback while downloader lock is active;
- privacy and idle-resource budget;
- no mutation of the user's unrelated worktree or real runtime.

Address every confirmed issue with a RED regression test, minimal fix, focused GREEN run, and its own commit. If no independent reviewer is available, perform the same structured review and state that it is self-review evidence.

- [ ] **Step 3: Perform static diff and placeholder review**

```powershell
git diff --check master...HEAD
git diff --stat master...HEAD
git diff master...HEAD -- `
  src/tg_video_downloader/gateway.py `
  src/tg_video_downloader/search_ipc.py `
  src/tg_video_downloader/service.py `
  src/tg_video_downloader/gui/controller.py
rg -n "TODO|TBD|FIXME|pass$|NotImplementedError" `
  src/tg_video_downloader `
  tests/test_search_ipc.py `
  tests/test_background_search_integration.py
```

Expected: no whitespace errors, no unimplemented production path, and only intentional protocol method bodies or pre-existing matches survive review. Inspect all type annotations so `SearchRequest`, `SelectableVideo`, `SearchHandler`, and factory types agree across modules.

- [ ] **Step 4: Run dependency and full project gates**

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
```

Expected: no broken requirements; all existing and newly added tests pass; compileall and project-local path checks pass. Record the actual measured test count from this run rather than estimating it.

- [ ] **Step 5: Recheck the real downloader read-only**

Read downloader/supervisor byte locks and normalized heartbeat from `D:\Codex Project\Telegram自动化脚本` twice, eight seconds apart, using the worktree interpreter and existing project readers.

Expected: both locks remain active, heartbeat remains `running` and advances, and no command opens the real Telegram session, state database, endpoint, downloads, or configuration for writing. The candidate worktree must not be launched against the real runtime.

- [ ] **Step 6: Record measured verification evidence**

After the commands pass, append a dated `v0.3.4 后台共享检索通道证据` section to `docs/verification.md` containing the exact observed test count and these verified facts:

- gateway client factory is not invoked until after `telegram-client.lock` is acquired;
- a second gateway receives `TelegramSessionInUseError` before Telethon construction;
- running mode uses one gateway through real loopback IPC, while stopped mode retains direct search;
- missing/stale IPC during active background state never falls back to the shared session;
- cancellation cancels only search and leaves gateway/download work active;
- endpoint lifecycle, PID/token validation, queue-state return, and config reload tests pass;
- unique keyword, caption, filename, result data, and token are absent from logs and unauthorized runtime files;
- no new dependency, thread, process, poller, persistent timer, database table, or search-history file exists;
- the real downloader stayed running and its heartbeat advanced throughout isolated candidate work.

Do not write an estimated count or claim that v0.3.4 is published.

- [ ] **Step 7: Commit measured evidence**

```powershell
git add docs/verification.md
git commit -m "docs: verify the v0.3.4 search fix"
```

- [ ] **Step 8: Run the final clean-candidate gate**

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
git diff --check master...HEAD
git status --short --branch
git log --oneline master..HEAD
```

Expected: dependencies and every test pass again, the feature branch is clean, and commits contain only the approved v0.3.4 candidate work. Use `finishing-a-development-branch` to present integration choices. Do not merge, tag, publish to GitHub or ModelScope, stop/restart the real background service, or delete the worktree until the user explicitly requests that next action.
