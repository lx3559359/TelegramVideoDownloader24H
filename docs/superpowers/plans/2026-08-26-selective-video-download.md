# Selective Video Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand Telegram video search page that lets the user select individual results and enqueue them through the existing lightweight downloader.

**Architecture:** A small pure domain module owns date parsing, bounded-search constants, queue presentation states, and selection summaries. `TelethonGateway` performs one bounded server-side search, `GuiController` enforces authorization and whitelist boundaries, `StateStore` atomically enqueues selected messages, and a dedicated ttk page owns only transient UI results and one cancellable Future. Existing automatic monitoring, history scanning, download storage, one-worker execution, heartbeat, tray, and updater behavior remain unchanged.

**Tech Stack:** Python 3.11+, Telethon 1.44, Tk/ttk, SQLite WAL, pytest, pytest-asyncio, PowerShell verification scripts.

**Approved specification:** `docs/superpowers/specs/2026-08-26-selective-video-download-design.md`

---

## File map

- Create `src/tg_video_downloader/selective.py`: pure limits, date parsing, caption normalization, queue presentation types, and summary values.
- Modify `src/tg_video_downloader/models.py`: immutable `VideoSearchResult` transport model.
- Modify `src/tg_video_downloader/gateway.py`: protocol and bounded Telethon video search.
- Modify `src/tg_video_downloader/state.py`: queue-state lookup and atomic manual enqueue/requeue.
- Modify `src/tg_video_downloader/gui/controller.py`: authorization, whitelist, date, gateway, state, and enqueue orchestration.
- Create `src/tg_video_downloader/gui/search_page.py`: transient ttk search page and its pure selection model.
- Modify `src/tg_video_downloader/gui/app.py`: mount, refresh, clear, and close the search page.
- Modify `tests/fakes.py`: fake search support shared by integration tests.
- Create `tests/test_selective.py`: pure domain behavior.
- Modify `tests/test_gateway.py`: Telethon search arguments, normalization, filtering, and bounds.
- Modify `tests/test_state.py`: atomic enqueue and status semantics.
- Modify `tests/test_gui_controller.py`: search lifecycle and whitelist enforcement.
- Create `tests/test_gui_search_page.py`: selection model and ttk page behavior.
- Modify `tests/test_gui_app.py`: app integration and close/logout behavior.
- Modify `tests/test_service_integration.py`: selected result to existing worker queue.
- Modify `README.md`, `docs/verification.md`, and `pyproject.toml`: v0.3.0 user guidance and release evidence.

## Task 1: Pure search domain and validation

**Files:**

- Create: `src/tg_video_downloader/selective.py`
- Modify: `src/tg_video_downloader/models.py`
- Create: `tests/test_selective.py`

- [ ] **Step 1: Write failing tests for date, limit, caption, and queue-state behavior**

```python
from datetime import UTC, timedelta, timezone

import pytest

from tg_video_downloader.models import JobStatus
from tg_video_downloader.selective import (
    SearchQueueState,
    normalize_search_caption,
    parse_search_dates,
    queue_state_for,
    validate_search_limit,
)


def test_parse_search_dates_uses_inclusive_local_days() -> None:
    china = timezone(timedelta(hours=8))
    start, end = parse_search_dates("2026-08-01", "2026-08-02", china)
    assert start.isoformat() == "2026-07-31T16:00:00+00:00"
    assert end.isoformat() == "2026-08-02T16:00:00+00:00"
    assert start.tzinfo is UTC
    assert end.tzinfo is UTC


def test_parse_search_dates_rejects_reverse_range() -> None:
    with pytest.raises(ValueError, match="开始日期不能晚于结束日期"):
        parse_search_dates("2026-08-03", "2026-08-02", timezone.utc)


@pytest.mark.parametrize("value", [20, 50, 100])
def test_validate_search_limit_accepts_only_supported_values(value: int) -> None:
    assert validate_search_limit(value) == value


def test_caption_is_single_line_and_bounded() -> None:
    assert normalize_search_caption(" a\n b " + "字" * 200) == (
        "a b " + "字" * 116
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, SearchQueueState.AVAILABLE),
        (JobStatus.PENDING, SearchQueueState.QUEUED),
        (JobStatus.DOWNLOADING, SearchQueueState.QUEUED),
        (JobStatus.RETRY_WAIT, SearchQueueState.QUEUED),
        (JobStatus.COMPLETED, SearchQueueState.COMPLETED),
        (JobStatus.PERMANENT_ERROR, SearchQueueState.RETRYABLE),
    ],
)
def test_queue_state_mapping(status, expected) -> None:
    assert queue_state_for(status) is expected
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_selective.py -q
```

Expected: collection fails because `tg_video_downloader.selective` and `VideoSearchResult` do not exist.

- [ ] **Step 3: Add the immutable result model and pure implementation**

Append after `MessageInfo` in `models.py`:

```python
@dataclass(frozen=True)
class VideoSearchResult:
    message: MessageInfo
    duration_seconds: int | None
    caption: str
```

Create `selective.py`:

```python
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum

from tg_video_downloader.models import JobStatus, VideoSearchResult


SEARCH_LIMITS = (20, 50, 100)
MAX_SEARCH_CANDIDATES = 500


class SearchQueueState(StrEnum):
    AVAILABLE = "available"
    QUEUED = "queued"
    COMPLETED = "completed"
    RETRYABLE = "retryable"


@dataclass(frozen=True)
class SelectableVideo:
    result: VideoSearchResult
    queue_state: SearchQueueState


@dataclass(frozen=True)
class ManualQueueSummary:
    added: int = 0
    requeued: int = 0
    already_queued: int = 0
    completed: int = 0


def _optional_date(value: str, label: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{label}必须使用 YYYY-MM-DD 格式") from error


def parse_search_dates(
    start_text: str,
    end_text: str,
    local_timezone: tzinfo,
) -> tuple[datetime | None, datetime | None]:
    start_date = _optional_date(start_text, "开始日期")
    end_date = _optional_date(end_text, "结束日期")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    start = (
        datetime.combine(start_date, time.min, tzinfo=local_timezone).astimezone(UTC)
        if start_date is not None
        else None
    )
    end = (
        datetime.combine(
            end_date + timedelta(days=1),
            time.min,
            tzinfo=local_timezone,
        ).astimezone(UTC)
        if end_date is not None
        else None
    )
    return start, end


def validate_search_limit(value: int) -> int:
    if isinstance(value, bool) or value not in SEARCH_LIMITS:
        raise ValueError("结果数量必须是 20、50 或 100")
    return value


def normalize_search_caption(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text[:120]


def queue_state_for(status: JobStatus | None) -> SearchQueueState:
    if status is None:
        return SearchQueueState.AVAILABLE
    if status is JobStatus.COMPLETED:
        return SearchQueueState.COMPLETED
    if status is JobStatus.PERMANENT_ERROR:
        return SearchQueueState.RETRYABLE
    return SearchQueueState.QUEUED


def is_selectable(item: SelectableVideo) -> bool:
    return item.queue_state in {
        SearchQueueState.AVAILABLE,
        SearchQueueState.RETRYABLE,
    }
```

- [ ] **Step 4: Run the pure tests and existing media/model tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_selective.py tests\test_media.py tests\test_state.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the domain layer**

```powershell
git add src/tg_video_downloader/models.py src/tg_video_downloader/selective.py tests/test_selective.py
git commit -m "feat: model selective video searches"
```

## Task 2: Bounded Telethon video search

**Files:**

- Modify: `src/tg_video_downloader/gateway.py`
- Modify: `tests/test_gateway.py`

- [ ] **Step 1: Write failing gateway tests**

Add a fake client whose `iter_messages` records arguments and yields downloadable, animated, round, out-of-range, and non-video messages. Assert:

```python
@pytest.mark.asyncio
async def test_search_videos_is_server_filtered_bounded_and_latest_first(
    tmp_path: Path,
) -> None:
    client = SearchClient(search_messages())
    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(123, "hash"),
        client_factory=lambda *_args, **_kwargs: client,
    )
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 9, 1, tzinfo=UTC)

    results = await gateway.search_videos(-1001, "课程", start, end, 20)

    assert [item.message.message_id for item in results] == [9, 7]
    assert results[0].duration_seconds == 95
    assert results[0].caption == "第一段 说明"
    assert client.options["limit"] == 500
    assert client.options["search"] == "课程"
    assert client.options["offset_date"] == end
    assert client.options["filter"] is InputMessagesFilterVideo


@pytest.mark.asyncio
async def test_search_videos_stops_at_requested_result_limit(tmp_path: Path) -> None:
    client = SearchClient(downloadable_messages(150))
    gateway = make_search_gateway(tmp_path, client)
    results = await gateway.search_videos(-1001, "", None, None, 100)
    assert len(results) == 100
    assert client.yield_count == 100
```

Add another fake stream containing 600 non-downloadable candidates and assert the iterator yields exactly `MAX_SEARCH_CANDIDATES` items and returns no results. Also test that a client exception is passed through `_mapped_error`, that cancellation is not converted into a generic error, and that negative or non-finite duration metadata becomes `None`.

- [ ] **Step 2: Run gateway search tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gateway.py -k search_videos -q
```

Expected: failures report that `TelethonGateway.search_videos` is missing.

- [ ] **Step 3: Extend the gateway protocol and implementation**

Add the protocol signature and imports for `math`, `InputMessagesFilterVideo`, `is_downloadable_video`, `VideoSearchResult`, `MAX_SEARCH_CANDIDATES`, and `normalize_search_caption`. Implement:

```python
def _video_duration(message: Any) -> int | None:
    document = getattr(message, "document", None)
    for attribute in getattr(document, "attributes", ()) if document else ():
        if type(attribute).__name__ == "DocumentAttributeVideo":
            value = getattr(attribute, "duration", None)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            ):
                return int(value)
    return None


async def search_videos(
    self,
    chat_id: int,
    keyword: str,
    start_utc: datetime | None,
    end_utc: datetime | None,
    result_limit: int,
) -> tuple[VideoSearchResult, ...]:
    results: list[VideoSearchResult] = []
    try:
        async for raw in self._client.iter_messages(
            chat_id,
            limit=MAX_SEARCH_CANDIDATES,
            search=keyword.strip() or None,
            filter=InputMessagesFilterVideo,
            offset_date=end_utc,
        ):
            message = normalize_message(raw, chat_id)
            message_date = message.date
            if message_date.tzinfo is None:
                message_date = message_date.replace(tzinfo=UTC)
            message_date = message_date.astimezone(UTC)
            if start_utc is not None and message_date < start_utc:
                break
            if end_utc is not None and message_date >= end_utc:
                continue
            if not is_downloadable_video(message):
                continue
            results.append(
                VideoSearchResult(
                    message=message,
                    duration_seconds=_video_duration(raw),
                    caption=normalize_search_caption(getattr(raw, "message", "")),
                )
            )
            if len(results) >= result_limit:
                break
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise _mapped_error(error) from error
    return tuple(results)
```

The method must remain inside `TelethonGateway`; add the same signature to `TelegramGateway` so fakes remain type-compatible.

- [ ] **Step 4: Run gateway and media tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gateway.py tests\test_media.py -q
```

Expected: all gateway and media tests pass.

- [ ] **Step 5: Commit the gateway search**

```powershell
git add src/tg_video_downloader/gateway.py tests/test_gateway.py
git commit -m "feat: search Telegram videos on demand"
```

## Task 3: Atomic manual queue insertion

**Files:**

- Modify: `src/tg_video_downloader/state.py`
- Modify: `tests/test_state.py`

- [ ] **Step 1: Write failing state tests for all four dispositions**

Build four messages in one selected group: absent, pending, completed, and permanent error. Bind the failed task to an external root before marking it permanent. Assert:

```python
def test_enqueue_manual_results_is_atomic_idempotent_and_requeues_failure(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "state.sqlite3")
    group = GroupTarget(-1001, "课程群", download_history=False)
    state.reconcile_targets((group,))
    pending, completed, failed, fresh = messages_for_manual_queue()
    state.upsert_job(pending, group.title, JobSource.LIVE)
    state.upsert_job(completed, group.title, JobSource.HISTORY)
    completed_job = state.get_job(group.chat_id, completed.message_id)
    state.mark_completed(completed_job, tmp_path / "done.mp4")
    state.upsert_job(failed, group.title, JobSource.HISTORY)
    failed_job = state.get_job(group.chat_id, failed.message_id)
    bound = state.bind_output_root(failed_job, tmp_path / "external")
    state.mark_permanent_error(bound, "deleted")

    summary = state.enqueue_manual_results(
        group,
        (pending, completed, failed, fresh),
    )

    assert summary == ManualQueueSummary(
        added=1,
        requeued=1,
        already_queued=1,
        completed=1,
    )
    retried = state.get_job(group.chat_id, failed.message_id)
    assert retried.status is JobStatus.PENDING
    assert retried.source is JobSource.LIVE
    assert retried.attempts == 0
    assert retried.output_root == (tmp_path / "external").resolve()
```

Add a trigger-based test that aborts one insert and proves the entire selected batch rolls back:

```python
def test_enqueue_manual_results_rolls_back_the_whole_batch_on_sql_error(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "state.sqlite3")
    group = GroupTarget(-1001, "课程群", download_history=False)
    first, second = messages_for_manual_queue()[:2]
    state._connection.execute(
        f"""
        CREATE TRIGGER abort_selected_batch
        BEFORE INSERT ON jobs
        WHEN NEW.message_id = {second.message_id}
        BEGIN
            SELECT RAISE(ABORT, 'forced selected-batch failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced selected-batch failure"):
        state.enqueue_manual_results(group, (first, second))

    assert state.get_job(group.chat_id, first.message_id) is None
    assert state.get_job(group.chat_id, second.message_id) is None
```

- [ ] **Step 2: Run focused state tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_state.py -k "manual or queue_status" -q
```

Expected: failures report missing `enqueue_manual_results` and `job_statuses`.

- [ ] **Step 3: Implement status lookup and one-transaction enqueue**

Add:

```python
def job_statuses(
    self,
    keys: tuple[tuple[int, int], ...],
) -> dict[tuple[int, int], JobStatus]:
    statuses: dict[tuple[int, int], JobStatus] = {}
    for chat_id, message_id in keys:
        row = self._connection.execute(
            "SELECT status FROM jobs WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        if row is not None:
            statuses[(chat_id, message_id)] = JobStatus(str(row["status"]))
    return statuses


def enqueue_manual_results(
    self,
    group: GroupTarget,
    messages: tuple[MessageInfo, ...],
) -> ManualQueueSummary:
    counts = {"added": 0, "requeued": 0, "already_queued": 0, "completed": 0}
    with self._connection:
        self._connection.execute(
            """
            INSERT INTO groups(chat_id, title, enabled, download_history)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                enabled = 1,
                download_history = excluded.download_history
            """,
            (group.chat_id, group.title, int(group.download_history)),
        )
        for message in messages:
            row = self._connection.execute(
                "SELECT status FROM jobs WHERE chat_id = ? AND message_id = ?",
                (message.chat_id, message.message_id),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO jobs(
                        chat_id, message_id, group_title, source, priority, status,
                        message_date, mime_type, original_name, extension,
                        expected_size, is_video, is_animated, is_round
                    ) VALUES (?, ?, ?, 'live', 0, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.chat_id,
                        message.message_id,
                        group.title,
                        _as_utc(message.date).isoformat(),
                        message.mime_type,
                        message.original_name,
                        message.extension,
                        message.size,
                        int(message.is_video),
                        int(message.is_animated),
                        int(message.is_round),
                    ),
                )
                counts["added"] += 1
                continue
            status = JobStatus(str(row["status"]))
            if status is JobStatus.COMPLETED:
                counts["completed"] += 1
            elif status is JobStatus.PERMANENT_ERROR:
                self._connection.execute(
                    """
                    UPDATE jobs SET
                        group_title = ?, source = 'live', priority = 0,
                        status = 'pending', message_date = ?, mime_type = ?,
                        original_name = ?, extension = ?, expected_size = ?,
                        is_video = ?, is_animated = ?, is_round = ?,
                        attempts = 0, next_attempt_at = NULL, error = NULL
                    WHERE chat_id = ? AND message_id = ?
                    """,
                    (
                        group.title,
                        _as_utc(message.date).isoformat(),
                        message.mime_type,
                        message.original_name,
                        message.extension,
                        message.size,
                        int(message.is_video),
                        int(message.is_animated),
                        int(message.is_round),
                        message.chat_id,
                        message.message_id,
                    ),
                )
                counts["requeued"] += 1
            else:
                counts["already_queued"] += 1
    return ManualQueueSummary(**counts)
```

Import `ManualQueueSummary`; validate before entering the transaction that every message belongs to `group.chat_id` and is downloadable. Raise `ValueError` before any SQL write when the tuple is empty, contains duplicates, contains another chat ID, or contains a non-downloadable item.

- [ ] **Step 4: Run state and worker tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_state.py tests\test_worker.py -q
```

Expected: all selected tests pass; existing claim, retry, binding, and completion behavior remains green.

- [ ] **Step 5: Commit the atomic queue API**

```powershell
git add src/tg_video_downloader/state.py tests/test_state.py
git commit -m "feat: enqueue selected videos atomically"
```

## Task 4: Controller search and enqueue orchestration

**Files:**

- Modify: `src/tg_video_downloader/gui/controller.py`
- Modify: `tests/test_gui_controller.py`

- [ ] **Step 1: Write failing controller lifecycle tests**

Extend `LoginGateway` with a configurable `search_results` tuple and `search_videos` method. Test success, unauthorized, non-whitelist, active login, invalid date, cancellation, disconnect-on-error, and enqueue validation. The success assertion is:

```python
@pytest.mark.asyncio
async def test_controller_searches_selected_group_and_attaches_queue_state(
    tmp_path: Path,
) -> None:
    controller, paths, gateway, _ = make_controller(tmp_path)
    controller.save_credentials(Credentials(123, "hash"))
    controller.save_selected_groups((GroupTarget(-1001, "课程群", False),))
    gateway.search_results = (video_search_result(-1001, 9),)

    items = await controller.search_videos(
        -1001,
        "课程",
        "2026-08-01",
        "2026-08-31",
        100,
        local_timezone=timezone(timedelta(hours=8)),
    )

    assert items == (
        SelectableVideo(gateway.search_results[0], SearchQueueState.AVAILABLE),
    )
    assert gateway.search_calls[0].keyword == "课程"
    assert gateway.connected is False
```

The cancellation fake blocks on an `asyncio.Event`; cancel the task and assert `disconnect` ran in `finally`.

- [ ] **Step 2: Run controller search tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_controller.py -k "search_video or enqueue_selected" -q
```

Expected: failures report missing controller methods.

- [ ] **Step 3: Implement controller methods with injectable state creation**

Add `state_factory: Callable[[Path], StateStore] = StateStore` as a keyword-only constructor dependency and store it. Implement:

```python
async def search_videos(
    self,
    chat_id: int,
    keyword: str,
    start_text: str,
    end_text: str,
    limit: int,
    *,
    local_timezone: tzinfo | None = None,
) -> tuple[SelectableVideo, ...]:
    if self.login_active:
        raise ValueError("请先完成或取消当前登录任务")
    groups = {group.chat_id: group for group in self.selected_groups()}
    if chat_id not in groups:
        raise ValueError("只能检索当前已监听的群组或频道")
    credentials = self.load_credentials()
    if credentials is None:
        raise ValueError("请先填写并保存账号信息")
    timezone_value = local_timezone or datetime.now().astimezone().tzinfo
    if timezone_value is None:
        timezone_value = UTC
    start_utc, end_utc = parse_search_dates(start_text, end_text, timezone_value)
    result_limit = validate_search_limit(limit)
    gateway = self.gateway_factory(self.paths, credentials)
    active_error: BaseException | None = None
    try:
        await gateway.connect()
        if not await gateway.is_authorized():
            raise AuthenticationRequiredError("请先完成 Telegram 登录")
        results = await gateway.search_videos(
            chat_id,
            keyword,
            start_utc,
            end_utc,
            result_limit,
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


def _attach_queue_states(
    self,
    results: tuple[VideoSearchResult, ...],
) -> tuple[SelectableVideo, ...]:
    store = self.state_factory(self.paths.database)
    try:
        keys = tuple(
            (result.message.chat_id, result.message.message_id)
            for result in results
        )
        statuses = store.job_statuses(keys)
    finally:
        store.close()
    return tuple(
        SelectableVideo(
            result,
            queue_state_for(
                statuses.get((result.message.chat_id, result.message.message_id))
            ),
        )
        for result in results
    )


def enqueue_selected_videos(
    self,
    chat_id: int,
    results: tuple[VideoSearchResult, ...],
) -> ManualQueueSummary:
    groups = {group.chat_id: group for group in self.selected_groups()}
    group = groups.get(chat_id)
    if group is None:
        raise ValueError("只能下载当前已监听目标的检索结果")
    if not results:
        raise ValueError("请至少选择一个视频")
    if any(result.message.chat_id != chat_id for result in results):
        raise ValueError("检索结果与当前目标不一致")
    store = self.state_factory(self.paths.database)
    try:
        return store.enqueue_manual_results(
            group,
            tuple(result.message for result in results),
        )
    finally:
        store.close()
```

If `disconnect` itself fails while another exception is active, preserve the original exception and suppress only the cleanup failure, matching the login cancellation pattern already present in the controller.

- [ ] **Step 4: Run controller, state, and gateway tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_controller.py tests\test_state.py tests\test_gateway.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit controller orchestration**

```powershell
git add src/tg_video_downloader/gui/controller.py tests/test_gui_controller.py
git commit -m "feat: orchestrate selective video searches"
```

## Task 5: Pure result selection and presentation model

**Files:**

- Create: `src/tg_video_downloader/gui/search_page.py`
- Create: `tests/test_gui_search_page.py`

- [ ] **Step 1: Write failing tests for selection and formatting**

```python
def test_selection_model_only_selects_available_and_retryable_items() -> None:
    items = make_selectable_items(
        SearchQueueState.AVAILABLE,
        SearchQueueState.RETRYABLE,
        SearchQueueState.QUEUED,
        SearchQueueState.COMPLETED,
    )
    model = SearchSelectionModel()
    model.replace(items)

    model.select_eligible()

    assert model.selected_keys == {
        items[0].result.message.message_id,
        items[1].result.message.message_id,
    }
    assert model.selected_results() == (
        items[0].result,
        items[1].result,
    )


def test_selection_model_rejects_toggle_for_non_selectable_row() -> None:
    item = make_selectable_item(SearchQueueState.COMPLETED, message_id=7)
    model = SearchSelectionModel()
    model.replace((item,))
    model.toggle(7)
    assert model.selected_keys == set()


def test_search_presentation_formats_missing_metadata_safely() -> None:
    assert format_search_size(None) == "-"
    assert format_search_size(float("inf")) == "-"
    assert format_search_duration(None) == "-"
    assert format_search_duration(-1) == "-"
    assert format_search_duration(65) == "01:05"
    assert queue_state_text(SearchQueueState.RETRYABLE) == "可重新排队"
```

- [ ] **Step 2: Run selection tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_search_page.py -q
```

Expected: collection fails because `gui.search_page` is missing.

- [ ] **Step 3: Implement the pure model before adding widgets**

Create `search_page.py` with:

```python
import math
from dataclasses import replace
from datetime import UTC, datetime, tzinfo

from tg_video_downloader.models import VideoSearchResult
from tg_video_downloader.selective import (
    SearchQueueState,
    SelectableVideo,
    is_selectable,
)


class SearchSelectionModel:
    def __init__(self) -> None:
        self.items: tuple[SelectableVideo, ...] = ()
        self._by_id: dict[int, SelectableVideo] = {}
        self.selected_keys: set[int] = set()

    def replace(self, items: tuple[SelectableVideo, ...]) -> None:
        self.items = items
        self._by_id = {
            item.result.message.message_id: item
            for item in items
        }
        self.selected_keys.clear()

    def clear(self) -> None:
        self.replace(())

    def toggle(self, message_id: int) -> None:
        item = self._by_id.get(message_id)
        if item is None or not is_selectable(item):
            return
        if message_id in self.selected_keys:
            self.selected_keys.remove(message_id)
        else:
            self.selected_keys.add(message_id)

    def select_eligible(self) -> None:
        self.selected_keys = {
            item.result.message.message_id
            for item in self.items
            if is_selectable(item)
        }

    def clear_selection(self) -> None:
        self.selected_keys.clear()

    def selected_results(self) -> tuple[VideoSearchResult, ...]:
        return tuple(
            item.result
            for item in self.items
            if item.result.message.message_id in self.selected_keys
        )

    def mark_selected_queued(self) -> None:
        selected = set(self.selected_keys)
        self.items = tuple(
            replace(item, queue_state=SearchQueueState.QUEUED)
            if item.result.message.message_id in selected
            else item
            for item in self.items
        )
        self._by_id = {
            item.result.message.message_id: item
            for item in self.items
        }
        self.selected_keys.clear()
```

Add deterministic presentation helpers. Reuse binary size units and never display negative or non-finite values:

```python
def format_search_size(value: int | float | None) -> str:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        return "-"
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"


def format_search_duration(value: int | float | None) -> str:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        return "-"
    total = int(value)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_search_date(
    value: datetime,
    local_timezone: tzinfo | None = None,
) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    zone = local_timezone or datetime.now().astimezone().tzinfo or UTC
    return normalized.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def queue_state_text(state: SearchQueueState) -> str:
    return {
        SearchQueueState.AVAILABLE: "可加入",
        SearchQueueState.QUEUED: "已在队列",
        SearchQueueState.COMPLETED: "已完成",
        SearchQueueState.RETRYABLE: "可重新排队",
    }[state]
```

- [ ] **Step 4: Run pure GUI model tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_search_page.py -q
```

Expected: selection and presentation tests pass without constructing a Tk window.

- [ ] **Step 5: Commit the selection model**

```powershell
git add src/tg_video_downloader/gui/search_page.py tests/test_gui_search_page.py
git commit -m "feat: model selectable video results"
```

## Task 6: Dedicated cancellable ttk search page

**Files:**

- Modify: `src/tg_video_downloader/gui/search_page.py`
- Modify: `src/tg_video_downloader/gui/app.py`
- Modify: `tests/test_gui_search_page.py`
- Modify: `tests/test_gui_app.py`

- [ ] **Step 1: Write failing Tk page and lifecycle tests**

Construct a withdrawn `tk.Tk()` and a `ttk.Notebook`, then instantiate `VideoSearchPage` with fake controller and bridge objects. Assert the tab text, controls, table headings, target refresh, one active Future, cancellation, enqueue summary, and close behavior. The core integration assertion is:

```python
def test_search_page_builds_lightweight_controls_and_no_timer_until_search(
    tk_root,
) -> None:
    notebook = ttk.Notebook(tk_root)
    page = VideoSearchPage(
        notebook,
        controller=FakeSearchController(),
        bridge=FakeBridge(),
        show_error=lambda error: None,
    )
    assert notebook.tab(page, "text") == "视频检索"
    assert page.limit_var.get() == "100"
    assert page.search_future is None
    assert page.poll_after is None
    assert page.result_tree["show"] == "headings"
```

Add an app test with a fake page factory proving `DownloaderApp.close()` calls `search_page.close()` before `bridge.close()`, and a test proving a successful group save calls `search_page.refresh_targets()`.

- [ ] **Step 2: Run GUI search tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_search_page.py tests\test_gui_app.py -q
```

Expected: failures identify the missing `VideoSearchPage` and app integration.

- [ ] **Step 3: Build the page and wire it into the app**

Implement `VideoSearchPage(ttk.Frame)` with constructor arguments:

```python
def __init__(
    self,
    notebook: ttk.Notebook,
    controller: GuiController,
    bridge: AsyncBridge,
    show_error: Callable[[Exception], None],
) -> None:
```

Extend the module imports with `Callable`, `CancelledError`, `Future`, `tkinter as tk`, `ttk`, `AsyncBridge`, and `GuiController`. The constructor must create only widgets and variables. It calls `notebook.add(self, text="视频检索")`, stores `search_future: Future | None`, `poll_after: str | None`, `generation = 0`, and calls `refresh_targets()` without networking.

Build the toolbar and result tree with these exact persistent fields so later methods use one consistent interface:

```python
self.target_var = tk.StringVar()
self.keyword_var = tk.StringVar()
self.start_date_var = tk.StringVar()
self.end_date_var = tk.StringVar()
self.limit_var = tk.StringVar(value="100")
self.status_var = tk.StringVar(value="尚未检索")
self.count_var = tk.StringVar(value="结果 0，已选 0")
self.target_box = ttk.Combobox(
    toolbar,
    textvariable=self.target_var,
    state="readonly",
)
self.target_box.bind(
    "<<ComboboxSelected>>",
    lambda _event: self.clear_results("目标已变化，请重新检索"),
)
self.result_tree = ttk.Treeview(
    self,
    columns=("selected", "date", "name", "size", "duration", "caption", "state"),
    show="headings",
)
self.result_tree.bind("<Double-1>", self._toggle_row)
self.result_tree.bind("<space>", self._toggle_focused_row)
```

Create buttons named `search_button`, `cancel_button`, `select_all_button`, `clear_button`, and `enqueue_button`. Initially only search is enabled. The table uses message ID strings as item IDs, so `_toggle_row` and `_toggle_focused_row` can convert the focused item to `int` and delegate to `SearchSelectionModel.toggle`.

Wire the action buttons to `start_search`, `cancel_search`, `model.select_eligible`, `model.clear_selection`, and `enqueue_selected`. Selection-changing actions call `_render_results()`. Implement the row handlers and renderer directly:

```python
def _toggle_message(self, message_id: int) -> None:
    self.model.toggle(message_id)
    self._render_results()


def _toggle_row(self, event: tk.Event) -> str:
    item_id = self.result_tree.identify_row(event.y)
    if item_id:
        self._toggle_message(int(item_id))
    return "break"


def _toggle_focused_row(self, _event: tk.Event) -> str:
    item_id = self.result_tree.focus()
    if item_id:
        self._toggle_message(int(item_id))
    return "break"


def _render_results(self) -> None:
    self.result_tree.delete(*self.result_tree.get_children())
    for item in self.model.items:
        message = item.result.message
        selected = message.message_id in self.model.selected_keys
        marker = "☑" if selected else ("☐" if is_selectable(item) else "")
        fallback_name = f"video_{message.message_id}{message.extension or ''}"
        self.result_tree.insert(
            "",
            "end",
            iid=str(message.message_id),
            values=(
                marker,
                format_search_date(message.date),
                message.original_name or fallback_name,
                format_search_size(message.size),
                format_search_duration(item.result.duration_seconds),
                item.result.caption or "-",
                queue_state_text(item.queue_state),
            ),
        )
    selected_count = len(self.model.selected_keys)
    self.count_var.set(f"结果 {len(self.model.items)}，已选 {selected_count}")
    self.enqueue_button.state(
        ["!disabled"] if selected_count else ["disabled"]
    )
```

Implement these concrete methods:

```python
def refresh_targets(self) -> None:
    groups = self.controller.selected_groups()
    self._groups_by_label = {
        f"{group.title} ({group.chat_id})": group for group in groups
    }
    self.target_box.configure(values=tuple(self._groups_by_label))
    if self.target_var.get() not in self._groups_by_label:
        self.target_var.set(next(iter(self._groups_by_label), ""))
        self.clear_results("请选择一个已监听目标" if not groups else "尚未检索")


def start_search(self) -> None:
    if self.search_future is not None and not self.search_future.done():
        return
    group = self._groups_by_label.get(self.target_var.get())
    if group is None:
        self.show_error(ValueError("请选择一个已监听的群组或频道"))
        return
    self.clear_results("正在检索")
    self.generation += 1
    generation = self.generation
    self.target_box.configure(state="disabled")
    self.search_button.state(["disabled"])
    self.cancel_button.state(["!disabled"])
    try:
        self.search_future = self.bridge.submit(
            self.controller.search_videos(
                group.chat_id,
                self.keyword_var.get(),
                self.start_date_var.get(),
                self.end_date_var.get(),
                int(self.limit_var.get()),
            )
        )
    except Exception as error:
        self._finish_search_controls()
        self.show_error(error)
        return
    self.poll_after = self.after(100, lambda: self._poll_search(generation))


def cancel_search(self, on_finished: Callable[[], None] | None = None) -> None:
    self.generation += 1
    if self.search_future is not None and not self.search_future.done():
        self._after_cancel = on_finished
        self.search_future.cancel()
        return
    self.clear_results("已取消")
    self._finish_search_controls()
    if on_finished is not None:
        on_finished()


def close(self) -> None:
    self.generation += 1
    if self.poll_after is not None:
        try:
            self.after_cancel(self.poll_after)
        except tk.TclError:
            pass
        self.poll_after = None
    if self.search_future is not None and not self.search_future.done():
        self.search_future.cancel()
    self.search_future = None
    self.model.clear()
```

Add these helper bodies so cancellation, rendering, and button restoration have one owner:

```python
def clear_results(self, status: str) -> None:
    self.model.clear()
    self.result_tree.delete(*self.result_tree.get_children())
    self.status_var.set(status)
    self.count_var.set("结果 0，已选 0")
    self.enqueue_button.state(["disabled"])


def _finish_search_controls(self) -> None:
    self.target_box.configure(state="readonly")
    self.search_button.state(["!disabled"])
    self.cancel_button.state(["disabled"])
    self.poll_after = None


def _poll_search(self, generation: int) -> None:
    future = self.search_future
    if future is None:
        self._finish_search_controls()
        return
    if not future.done():
        self.poll_after = self.after(100, lambda: self._poll_search(generation))
        return
    self.search_future = None
    try:
        items = future.result()
    except CancelledError:
        self.clear_results("已取消")
    except Exception as error:
        self.clear_results("检索失败")
        self.show_error(error)
    else:
        if generation == self.generation:
            self.model.replace(items)
            self._render_results()
            self.status_var.set(f"检索完成，共 {len(items)} 条")
    finally:
        self._finish_search_controls()
        callback, self._after_cancel = self._after_cancel, None
        if callback is not None:
            callback()
```

Initialize `_after_cancel: Callable[[], None] | None = None`. A stale generation must not render results, but its completion must still restore controls and run a pending cancellation callback. No method may schedule a timer while idle.

`enqueue_selected` must call `controller.enqueue_selected_videos` synchronously because it is one short local transaction, show the exact four summary counts, call `model.mark_selected_queued()`, and rerender the rows. It must not start or stop the service:

```python
def enqueue_selected(self) -> None:
    group = self._groups_by_label.get(self.target_var.get())
    if group is None:
        self.show_error(ValueError("请选择一个已监听的群组或频道"))
        return
    selected = self.model.selected_results()
    try:
        summary = self.controller.enqueue_selected_videos(group.chat_id, selected)
    except Exception as error:
        self.show_error(error)
        return
    self.model.mark_selected_queued()
    self._render_results()
    self.status_var.set(
        f"新增 {summary.added}，重新排队 {summary.requeued}，"
        f"已在队列 {summary.already_queued}，已完成 {summary.completed}"
    )
```

In `DownloaderApp.__init__`, construct the page after `_build_groups_page()` and before `_build_run_page()`. In `_save_groups` success, call `search_page.refresh_targets()`. Refactor the existing `_log_out` body after confirmation so it cancels an active search before starting the existing logout coroutine:

```python
def start_logout() -> None:
    def finished(status: str) -> None:
        self._finish_qr_login(status)
        self.search_page.clear_results("已退出账号")

    self._run_async(self.controller.log_out(), self.logout_button, finished)

self.search_page.cancel_search(on_finished=start_logout)
```

In `close`, call `search_page.close()` before `bridge.close()`; `AsyncBridge.close()` already cancels and gathers remaining tasks, so the gateway `finally` cleanup completes before the event loop closes.

- [ ] **Step 4: Run GUI tests and the controller tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_gui_search_page.py tests\test_gui_app.py tests\test_gui_controller.py -q
```

Expected: all selected tests pass and the page owns no idle polling callback.

- [ ] **Step 5: Commit the ttk page**

```powershell
git add src/tg_video_downloader/gui/search_page.py src/tg_video_downloader/gui/app.py tests/test_gui_search_page.py tests/test_gui_app.py
git commit -m "feat: add selective video search page"
```

## Task 7: End-to-end queue and worker compatibility

**Files:**

- Modify: `tests/fakes.py`
- Modify: `tests/test_service_integration.py`
- Modify: `tests/test_gui_controller.py`
- Modify: `tests/test_gui_search_page.py`

- [ ] **Step 1: Add a failing integration test from search result to existing worker**

First write the integration test against `FakeTelegramGateway.search_results` and `search_videos` without adding those fake members yet. The RED run must fail with `AttributeError` for the missing fake search support. After observing that failure, extend `FakeTelegramGateway` with `search_results`, `search_calls`, and:

```python
async def search_videos(
    self,
    chat_id: int,
    keyword: str,
    start_utc,
    end_utc,
    result_limit: int,
) -> tuple[VideoSearchResult, ...]:
    self.search_calls.append((chat_id, keyword, start_utc, end_utc, result_limit))
    return tuple(
        result
        for result in self.search_results
        if result.message.chat_id == chat_id
    )[:result_limit]
```

Then test:

```python
@pytest.mark.asyncio
async def test_selected_search_result_uses_existing_worker_and_current_root(
    tmp_path: Path,
) -> None:
    paths, gateway, controller, state = selective_download_fixture(tmp_path)
    result = video_search_result(GROUP_A.chat_id, 77)
    gateway.search_results = (result,)
    gateway.download_payloads[(GROUP_A.chat_id, 77)] = b"video"

    found = await controller.search_videos(
        GROUP_A.chat_id, "", "", "", 20, local_timezone=UTC
    )
    summary = controller.enqueue_selected_videos(
        GROUP_A.chat_id,
        tuple(item.result for item in found),
    )
    worker = DownloadWorker(paths, state, gateway, download_root=lambda: paths.downloads)
    assert await worker.run_once() is True

    assert summary.added == 1
    assert state.get_job(GROUP_A.chat_id, 77).status is JobStatus.COMPLETED
    assert gateway.downloaded_keys == [(GROUP_A.chat_id, 77)]
```

Add a priority test with one currently claimed live job, one selected manual job, and one history job. Release the current job after simulating completion and assert the selected task is claimed before history without preempting the current job.

- [ ] **Step 2: Run integration tests and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_service_integration.py -k selected_search -q
```

Expected: failure reports that `FakeTelegramGateway` has no `search_results` or `search_videos` member.

- [ ] **Step 3: Complete fake, cancellation, stale-result, and privacy coverage**

Add the fake method above, then cover these exact cases:

- Cancelled controller search disconnects and creates no SQLite rows.
- A target removed after search but before enqueue is rejected.
- A result with another chat ID is rejected before transaction start.
- Search caption and keyword never appear in captured log records.
- A permanent-error selected task preserves `output_root` and resumes through the existing worker.
- The GUI keeps the previous 2-second heartbeat callback count unchanged while idle on the search page.

Do not add production logging solely to satisfy the privacy test; assert that the existing logger output does not contain the supplied unique values.

- [ ] **Step 4: Run all selective and service tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_selective.py tests\test_gateway.py tests\test_state.py tests\test_gui_controller.py tests\test_gui_search_page.py tests\test_gui_app.py tests\test_service_integration.py tests\test_worker.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit end-to-end coverage**

```powershell
git add tests/fakes.py tests/test_service_integration.py tests/test_gui_controller.py tests/test_gui_search_page.py
git commit -m "test: cover selective video downloads end to end"
```

## Task 8: v0.3.0 documentation, review, and release verification

**Files:**

- Modify: `README.md`
- Modify: `docs/verification.md`
- Modify: `pyproject.toml`
- Create: `tests/test_release_metadata.py`

- [ ] **Step 1: Write documentation assertions before changing docs**

Create `tests/test_release_metadata.py` with:

```python
def test_v030_docs_explain_selective_download_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert pyproject["project"]["version"] == "0.3.0"
    assert "视频检索" in readme
    assert "最多 100" in readme
    assert "不建立本地索引" in readme
    assert "下载选中项" in readme
```

- [ ] **Step 2: Run the release metadata test and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_release_metadata.py -q
```

Expected: version remains 0.2.0 and the new README guidance is absent.

- [ ] **Step 3: Update version and user documentation**

Set `project.version = "0.3.0"`. Add README instructions covering:

```text
打开“视频检索”页 → 选择一个已监听目标 → 输入可选关键词和日期
→ 选择 20/50/100 条上限 → 点击“检索” → 勾选结果
→ 点击“下载选中项”。
```

State explicitly that search is manual, single-target, text-only, capped at 100 results, has a 500-candidate safety ceiling, creates no persistent index, does not interrupt the current file, and preserves automatic monitoring. Add the focused test counts and Windows acceptance evidence to `docs/verification.md` without Telegram titles, filenames, keywords, credentials, or message IDs.

- [ ] **Step 4: Bootstrap and run complete verification**

Run:

```powershell
& .\scripts\bootstrap.ps1
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
git diff --check
git status --short
```

Expected: installed package metadata is 0.3.0, `pip check` reports no broken requirements, the complete pytest suite passes, compileall passes, project path checks pass, and only intended files are modified.

- [ ] **Step 5: Perform Windows GUI and live-background acceptance**

With the real downloader left running, open a synthetic GUI/controller against temporary credentials, state, heartbeat, and download directories. Verify at 900×720:

- “视频检索” is between “群组/频道” and “运行”.
- Top controls, result table, action buttons, and status line are visible.
- Search returns bounded fake results and cancellation restores buttons.
- The “运行” page progress bar remains visible and keeps the existing 2-second refresh schedule.
- No real Telegram message is sent, no real queue row is added, and no real download, session, configuration, or partial file is changed.

Record only non-sensitive counts and booleans in `docs/verification.md`.

- [ ] **Step 6: Request structured code review and fix findings with TDD**

Use `requesting-code-review` against the full branch diff. Review these risks explicitly:

- Telegram iteration stops at both the result and candidate bounds.
- Date boundaries are correct across local timezone conversion.
- Cancellation always disconnects the temporary client.
- No idle timer or second download worker exists.
- Queue writes are atomic, deduplicated, and preserve completed/bound data.
- GUI close and logout cancel search before the async bridge closes.
- Search content is absent from logs and persistent search storage.

For every correctness finding, first add a failing regression test, observe RED, implement the minimal fix, and rerun focused tests.

- [ ] **Step 7: Run final verification and commit the release candidate**

Run:

```powershell
& .\scripts\check.ps1
& .\.venv\Scripts\python.exe -m pip check
git diff --check
git status --short
```

Then commit:

```powershell
git add README.md docs/verification.md pyproject.toml tests/test_release_metadata.py
git commit -m "docs: prepare the v0.3.0 selective download release"
```

Run `scripts/check.ps1` once more on the clean committed branch. Use `verification-before-completion` before reporting success and `finishing-a-development-branch` to present merge choices.

## Release handoff after implementation

Do not push or tag merely because the tests pass. After the user explicitly chooses merge and publication:

1. Fast-forward `master` to the feature branch.
2. Run `scripts/bootstrap.ps1`, `pip check`, and `scripts/check.ps1` again on merged `master`.
3. Confirm both remote masters are ancestors and no remote `v0.3.0` exists.
4. Because this local repository contains unrelated historical version tags, create the annotated `v0.3.0` tag in an isolated temporary Git repository; never delete, move, force, or push the local conflicting tag.
5. Push `master` normally to GitHub and ModelScope, then push only the isolated `v0.3.0` tag.
6. Re-read both remote heads and peeled tag targets.
7. Confirm the existing downloader, supervisor, and GUI locks remain valid and the heartbeat still advances.
