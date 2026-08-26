# Saved Session Recovery Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the account page reliably without opening the active downloader's Telethon session a second time, and provide a safe manual retry when fallback recovery fails.

**Architecture:** Keep `GuiController.saved_session_authorized()` as the single recovery boundary. It will trust the existing normalized 15-second heartbeat only when it is freshly `running`, otherwise it will preserve the current gateway probe. The account page will add one on-demand retry button and reuse the existing async bridge and secret-redaction path, without adding background work.

**Tech Stack:** Python 3.12 standard library, Tkinter/ttk, Telethon, pytest, PowerShell, Git

---

### Task 1: Create an isolated verified workspace

**Files:**
- Use: `.worktrees/saved-session-recovery-v033/`
- Verify: `scripts/bootstrap.ps1`
- Verify: `scripts/check.ps1`

- [ ] **Step 1: Verify isolation prerequisites**

Run from `D:\Codex Project\Telegram自动化脚本`:

```powershell
git status --short --branch
git rev-parse --git-dir
git rev-parse --git-common-dir
git check-ignore -v .worktrees
git branch --list codex/saved-session-recovery-v033
Test-Path -LiteralPath .worktrees\saved-session-recovery-v033
git worktree list --porcelain
```

Expected: `master` is clean except for committed planning documents, `.worktrees` is ignored, and the target branch and path do not exist. Leave the unrelated `codex/download-policy-progress-resume` worktree untouched.

- [ ] **Step 2: Create the feature worktree**

```powershell
git worktree add `
  'D:\Codex Project\Telegram自动化脚本\.worktrees\saved-session-recovery-v033' `
  -b codex/saved-session-recovery-v033
```

Expected: the new worktree starts at the committed implementation plan.

- [ ] **Step 3: Bootstrap only the isolated environment**

```powershell
Set-Location 'D:\Codex Project\Telegram自动化脚本\.worktrees\saved-session-recovery-v033'
& .\scripts\bootstrap.ps1
```

Expected: the worktree-local `.venv` installs the current package and reports `cryptg acceleration ready`. The real GUI and downloader remain untouched.

- [ ] **Step 4: Run the clean baseline**

```powershell
& .\scripts\check.ps1
```

Expected: 377 tests pass before implementation.

### Task 2: Bypass the second gateway when the downloader heartbeat is healthy

**Files:**
- Modify: `tests/test_gui_controller.py`
- Modify: `src/tg_video_downloader/gui/controller.py:193-202`

- [ ] **Step 1: Add the failing fresh-heartbeat regression test**

Append this test after `test_saved_session_probe_reuses_authorization_without_starting_login` in `tests/test_gui_controller.py`:

```python
@pytest.mark.asyncio
async def test_saved_session_uses_fresh_running_heartbeat_without_gateway(
    tmp_path: Path,
) -> None:
    controller, paths, gateway, _ = make_controller(tmp_path)
    controller.save_credentials(Credentials(12345, "hash"))
    controller.gateway_factory = lambda *_: (_ for _ in ()).throw(
        AssertionError("fresh running recovery must not create a gateway")
    )
    HeartbeatWriter(paths.heartbeat).write(
        {
            "status": "running",
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )

    assert await controller.saved_session_authorized() is True
    assert gateway.disconnect_calls == 0
    assert controller.login_active is False
```

Also extend the existing fallback test with:

```python
assert gateway.disconnect_calls == 1
```

This preserves proof that a stopped or missing heartbeat still performs and closes the remote probe.

- [ ] **Step 2: Run the controller tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gui_controller.py::test_saved_session_uses_fresh_running_heartbeat_without_gateway `
  tests/test_gui_controller.py::test_saved_session_probe_reuses_authorization_without_starting_login
```

Expected: the new test fails because `gateway.disconnect_calls` is `1`; the current implementation always creates, connects, and disconnects a gateway.

- [ ] **Step 3: Add the minimal healthy-heartbeat short circuit**

Change `GuiController.saved_session_authorized()` in `src/tg_video_downloader/gui/controller.py` to:

```python
async def saved_session_authorized(self) -> bool:
    credentials = self.load_credentials()
    if credentials is None:
        return False
    if self.read_status().get("status") == "running":
        return True
    gateway = self.gateway_factory(self.paths, credentials)
    try:
        await gateway.connect()
        return await gateway.is_authorized()
    finally:
        await gateway.disconnect()
```

Do not add another freshness constant or inspect raw heartbeat timestamps here; `read_status()` already normalizes malformed and older-than-15-second heartbeats to `stale`.

- [ ] **Step 4: Run the controller recovery tests and verify GREEN**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gui_controller.py::test_saved_session_uses_fresh_running_heartbeat_without_gateway `
  tests/test_gui_controller.py::test_saved_session_probe_reuses_authorization_without_starting_login `
  tests/test_gui_controller.py::test_stale_running_heartbeat_is_not_reported_as_healthy
```

Expected: all three pass; fresh running state uses no gateway, missing/stopped state probes, and stale state is not trusted.

- [ ] **Step 5: Commit the controller fix**

```powershell
git add src/tg_video_downloader/gui/controller.py tests/test_gui_controller.py
git commit -m "fix: reuse healthy background login state"
```

### Task 3: Add a safe saved-session retry action

**Files:**
- Modify: `tests/test_gui_app.py`
- Modify: `src/tg_video_downloader/gui/app.py:178-270`
- Modify: `src/tg_video_downloader/gui/app.py:672-706`
- Modify: `src/tg_video_downloader/gui/app.py:725-747`
- Modify: `src/tg_video_downloader/gui/app.py:990-1006`

- [ ] **Step 1: Extend the fake button and write failing retry tests**

Replace `FakeButton` in `tests/test_gui_app.py` with:

```python
class FakeButton:
    def __init__(self) -> None:
        self.text = ""
        self.states: list[str] = []
        self.visible = False

    def configure(self, **values: str) -> None:
        self.text = values["text"]

    def state(self, values: list[str]) -> None:
        self.states = values

    def pack(self, **_options) -> None:
        self.visible = True

    def pack_forget(self) -> None:
        self.visible = False
```

Replace `test_saved_session_probe_error_keeps_session_and_shows_generic_status` with:

```python
def test_saved_session_probe_error_shows_redacted_reason_and_retry() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4
    app.account_status_var = FakeVar()
    app.api_hash_var = FakeVar("secret-hash")
    app.phone_var = FakeVar("")
    app.code_var = FakeVar("")
    app.password_var = FakeVar("")
    app.qr_password_var = FakeVar("")
    app.session_retry_button = FakeButton()
    finished: list[str] = []
    app._finish_qr_login = finished.append

    app._handle_saved_session_error(
        RuntimeError("secret-hash database is locked"),
        4,
    )

    assert finished == ["尚未登录"]
    assert app.account_status_var.get() == "恢复失败：*** database is locked"
    assert app.session_retry_button.visible is True
    assert app.session_retry_button.states == ["!disabled"]
```

Add this retry-flow test:

```python
def test_saved_session_retry_restarts_probe_and_hides_after_success() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4
    app.account_status_var = FakeVar()
    app.qr_login_button = FakeButton()
    app.session_retry_button = FakeButton()
    app.session_retry_button.pack()
    operation = object()
    app.controller = SimpleNamespace(saved_session_authorized=lambda: operation)
    submitted: list[tuple[object, int, object, object]] = []
    app._run_qr_operation = (
        lambda current, generation, on_success, on_error: submitted.append(
            (current, generation, on_success, on_error)
        )
    )
    finished: list[str] = []
    app._finish_qr_login = finished.append

    app._check_saved_session()

    assert app.account_status_var.get() == "正在恢复已有登录会话"
    assert app.session_retry_button.visible is False
    assert app.session_retry_button.states == ["disabled"]
    assert submitted[0][0] is operation
    assert submitted[0][1] == 4

    submitted[0][2](True)
    assert finished == ["登录成功"]
    assert app.session_retry_button.visible is False
    assert app.session_retry_button.states == ["!disabled"]
```

Add `app.session_retry_button = FakeButton()` to both existing saved-session status tests so their normal success and unauthorized paths can verify the retry action remains hidden:

```python
assert app.session_retry_button.visible is False
```

- [ ] **Step 2: Run the app recovery tests and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gui_app.py::test_saved_session_status_restores_authorized_account_without_qr `
  tests/test_gui_app.py::test_saved_session_status_leaves_manual_login_available_when_unauthorized `
  tests/test_gui_app.py::test_saved_session_probe_error_shows_redacted_reason_and_retry `
  tests/test_gui_app.py::test_saved_session_retry_restarts_probe_and_hides_after_success
```

Expected: failures show that the retry button does not exist in production behavior and recovery errors still use the generic message.

- [ ] **Step 3: Build the retry button in the existing QR action row**

After the existing `qr_cancel_button` setup in `_build_account_page()`, add:

```python
self.session_retry_button = ttk.Button(
    self.qr_actions,
    text="重试恢复",
    command=self._check_saved_session,
)
self.session_retry_button.pack(side="left", padx=(8, 0))
self.session_retry_button.pack_forget()
```

Do not create a new frame, timer, or async bridge.

- [ ] **Step 4: Implement retry visibility and safe error behavior**

Add these focused helpers before `_check_saved_session()`:

```python
def _hide_session_retry(self) -> None:
    self.session_retry_button.pack_forget()
    self.session_retry_button.state(["!disabled"])

def _show_session_retry(self) -> None:
    self.session_retry_button.state(["!disabled"])
    self.session_retry_button.pack(side="left", padx=(8, 0))
```

Change `_check_saved_session()` to hide and disable retry while one operation is active:

```python
def _check_saved_session(self) -> None:
    generation = self._qr_generation
    self._hide_session_retry()
    self.session_retry_button.state(["disabled"])
    self.account_status_var.set("正在恢复已有登录会话")
    self.qr_login_button.state(["disabled"])
    self._run_qr_operation(
        self.controller.saved_session_authorized(),
        generation,
        lambda authorized: self._handle_saved_session_status(
            authorized,
            generation,
        ),
        lambda error: self._handle_saved_session_error(error, generation),
    )
```

Change the two handlers to:

```python
def _handle_saved_session_status(
    self,
    authorized: bool,
    generation: int,
) -> None:
    if not self._is_current_qr_generation(generation):
        return
    self._hide_session_retry()
    self._finish_qr_login("登录成功" if authorized else "尚未登录")

def _handle_saved_session_error(
    self,
    error: Exception,
    generation: int,
) -> None:
    if not self._is_current_qr_generation(generation):
        return
    safe_message = self._safe_error(error)
    self._finish_qr_login("尚未登录")
    self.account_status_var.set(f"恢复失败：{safe_message}")
    self._show_session_retry()
```

At the beginning of `_begin_qr_login()` and inside `_finish_qr_login()`, call:

```python
self._hide_session_retry()
```

This hides the action when QR login starts, after successful recovery, during phone login cleanup, and after logout without duplicating state logic.

- [ ] **Step 5: Run app tests and verify GREEN**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_app.py
```

Expected: all account, QR, phone, update, runtime-status, and close tests pass.

- [ ] **Step 6: Commit the account retry behavior**

```powershell
git add src/tg_video_downloader/gui/app.py tests/test_gui_app.py
git commit -m "fix: retry saved session recovery safely"
```

### Task 4: Verify the 900x720 account-page layout

**Files:**
- Create: `tests/test_gui_account_page.py`

- [ ] **Step 1: Add the real Tk layout acceptance test**

Create `tests/test_gui_account_page.py`:

```python
import tkinter as tk
from tkinter import ttk

from tg_video_downloader.gui.app import DownloaderApp


def test_account_retry_action_fits_900x720(tk_root: tk.Tk) -> None:
    tk_root.deiconify()
    tk_root.geometry("900x720")
    app = DownloaderApp.__new__(DownloaderApp)
    ttk.Frame.__init__(app, tk_root, padding=12)
    app.pack(fill="both", expand=True)
    app._build_account_page()
    try:
        app._show_session_retry()
        tk_root.update()
        assert app.notebook.winfo_viewable()
        assert app.qr_login_button.winfo_viewable()
        assert app.session_retry_button.winfo_viewable()
        assert app.phone_toggle_button.winfo_viewable()
        assert app.session_retry_button.cget("text") == "重试恢复"
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the layout test and verify GREEN**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gui_account_page.py::test_account_retry_action_fits_900x720
```

Expected: PASS with the status/action area and retry button visible at 900x720. This test is added after the widget behavior is green because it verifies layout acceptance, not a new code path.

- [ ] **Step 3: Commit the layout acceptance test**

```powershell
git add tests/test_gui_account_page.py
git commit -m "test: verify account recovery layout"
```

### Task 5: Prepare v0.3.3 metadata and user guidance

**Files:**
- Modify: `tests/test_release_metadata.py`
- Modify: `pyproject.toml`
- Modify: `README.md:39-45`

- [ ] **Step 1: Change release expectations first**

Rename the release test to `test_v033_docs_explain_release_boundaries`, change the version assertion to:

```python
assert pyproject["project"]["version"] == "0.3.3"
```

Keep the existing assertions and add:

```python
assert "健康后台" in readme
assert "不会重复打开同一会话" in readme
assert "重试恢复" in readme
```

- [ ] **Step 2: Run the release test and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_release_metadata.py
```

Expected: FAIL because the package is still 0.3.2 and the README does not describe recovery behavior.

- [ ] **Step 3: Bump the candidate and document recovery**

Set `pyproject.toml` to:

```toml
version = "0.3.3"
```

Add this bullet after the existing saved-session bullet in README's “日常使用” section:

```markdown
- v0.3.3 起，配置器遇到健康后台时会直接复用其已登录状态，不会重复打开同一会话；后台停止或心跳异常时才执行远程授权检查。检查失败会显示脱敏原因并提供“重试恢复”，不会停止当前下载。
```

- [ ] **Step 4: Run metadata verification and reinstall the candidate**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_release_metadata.py
& .\scripts\bootstrap.ps1
& .\.venv\Scripts\python.exe -m pip check
```

Expected: metadata passes, editable package version is 0.3.3, and pip reports `No broken requirements found.` without a new dependency.

- [ ] **Step 5: Commit release metadata**

```powershell
git add pyproject.toml README.md tests/test_release_metadata.py
git commit -m "docs: prepare the v0.3.3 recovery fix"
```

### Task 6: Review and verify the release candidate

**Files:**
- Review: `src/tg_video_downloader/gui/controller.py`
- Review: `src/tg_video_downloader/gui/app.py`
- Review: `tests/test_gui_controller.py`
- Review: `tests/test_gui_app.py`
- Review: `tests/test_gui_account_page.py`
- Modify: `docs/verification.md`

- [ ] **Step 1: Run scoped regression checks**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_gui_controller.py `
  tests/test_gui_app.py `
  tests/test_gui_account_page.py `
  tests/test_gui_runtime.py `
  tests/test_gateway.py
```

Expected: all recovery, QR, phone, gateway, GUI lifecycle, and layout tests pass.

- [ ] **Step 2: Perform a structured diff review**

```powershell
git diff --check master...HEAD
git diff --stat master...HEAD
git diff master...HEAD -- `
  src/tg_video_downloader/gui/controller.py `
  src/tg_video_downloader/gui/app.py `
  tests/test_gui_controller.py `
  tests/test_gui_app.py `
  tests/test_gui_account_page.py
```

Review these requirements line by line:

- Only fresh normalized `running` status bypasses Telegram.
- Stale, stopped, malformed, and `needs_login` status retain the fallback probe.
- Fallback gateway always disconnects.
- Retry is hidden by default, disabled in flight, shown only on failure, and hidden on normal/login lifecycle paths.
- Error text passes through `_safe_error()`.
- No session copy, service stop, new thread, new permanent `after()`, or dependency appears.

If no independent reviewer tool is available, record this structured self-review instead of claiming an external review.

- [ ] **Step 3: Run dependency and full project gates**

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
```

Expected: no broken requirements and 380 tests pass, including compileall and project-local runtime directory checks.

- [ ] **Step 4: Verify the real downloader remains uninterrupted**

Use worktree Python to read the original project at `D:\Codex Project\Telegram自动化脚本`. Check downloader and supervisor byte locks plus heartbeat twice eight seconds apart.

Expected: both locks remain active, heartbeat stays `running` and advances, and the verification does not stop the service, open Telegram, or modify the real session.

- [ ] **Step 5: Record measured verification evidence**

Append this exact section to `docs/verification.md` after the measured commands pass:

```markdown
## v0.3.3 登录恢复并发修复证据（2026-08-26）

- 根因回归：健康 `running` 心跳存在时，已有会话恢复直接返回授权状态，网关创建、连接和断开次数均为 0；后台停止或心跳陈旧时仍执行原授权探测并保证断开。
- 界面行为：恢复失败显示经 `_safe_error()` 脱敏的原因并出现“重试恢复”；重试期间按钮禁用，成功、未授权、扫码、手机号登录和退出账号路径都会隐藏该按钮。
- 轻量边界：没有复制或删除 Telegram 会话，没有停止真实后台，没有新增依赖、线程、进程、永久 `after()` 或启动网络轮询。
- 900×720 验收：账号页、扫码登录、重试恢复和手机号备用入口均可见。
- 发布候选：`python -m pip check` 无损坏依赖，完整自动化 380 项通过，源码编译检查通过。
- 实机只读核验：原下载器与监督器持续运行，8 秒观察窗口内心跳推进；没有修改真实凭据、会话、队列、断点或下载文件。
```

- [ ] **Step 6: Commit verification evidence**

```powershell
git add docs/verification.md
git commit -m "docs: verify the v0.3.3 recovery fix"
```

- [ ] **Step 7: Run the final clean candidate gate**

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
git diff --check master...HEAD
git status --short --branch
git log --oneline master..HEAD
```

Expected: dependencies and all 380 tests pass, the feature branch is clean, and commits contain only the approved v0.3.3 recovery work. Then use the finishing-a-development-branch workflow; do not merge or publish until the user chooses that action.
