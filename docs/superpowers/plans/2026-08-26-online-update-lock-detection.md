# Online Update Lock Detection Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Windows runtime lock detection recognize the real `msvcrt` byte lock so online update installation reliably stops and restores an active downloader, then release the fix as v0.3.1.

**Architecture:** Keep `SingleInstance` and the update controller unchanged. Replace the read-open heuristic inside `_file_is_locked()` with a short-lived probe of the same first-byte lock used by `SingleInstance`; a failed probe means running, while a successful probe is immediately released. Prove the regression with real Windows file locks before changing production code.

**Tech Stack:** Python 3.12, `msvcrt`, pytest, PowerShell, Git

---

### Task 1: Create an isolated verified workspace

**Files:**
- Use: `.worktrees/online-update-lock-v031/`
- Verify: `scripts/bootstrap.ps1`
- Verify: `scripts/check.ps1`

- [ ] **Step 1: Confirm the main checkout is clean and the worktree directory is ignored**

Run:

```powershell
git status --short --branch
git check-ignore .worktrees
```

Expected: clean `master` status and `.worktrees` printed as ignored.

- [ ] **Step 2: Create the feature worktree**

Run:

```powershell
git worktree add '.worktrees/online-update-lock-v031' -b 'codex/online-update-lock-v031'
```

Expected: a new worktree based on the approved design commit.

- [ ] **Step 3: Install the project in the worktree-local virtual environment**

Run from `.worktrees/online-update-lock-v031`:

```powershell
& .\scripts\bootstrap.ps1
```

Expected: editable `telegram-video-downloader 0.3.0` installation completes.

- [ ] **Step 4: Verify the baseline**

Run:

```powershell
& .\scripts\check.ps1
```

Expected: 363 tests pass and compileall exits 0.

### Task 2: Reproduce the Windows byte-lock false negative

**Files:**
- Modify: `tests/test_windows.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Add real-lock regression tests**

Add these tests after `test_single_instance_supports_context_specific_error_message`:

```python
def test_downloader_running_detects_real_single_instance_lock(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    lock_path = paths.runtime / "downloader.lock"

    assert not windows.downloader_is_running(paths)
    with windows.SingleInstance(lock_path):
        assert windows.downloader_is_running(paths)
    assert not windows.downloader_is_running(paths)


def test_downloader_running_ignores_unlocked_stale_lock_file(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    (paths.runtime / "downloader.lock").write_text("stale", encoding="ascii")

    assert not windows.downloader_is_running(paths)
```

- [ ] **Step 2: Run the occupied-lock test and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_windows.py::test_downloader_running_detects_real_single_instance_lock
```

Expected: FAIL at the assertion inside `SingleInstance` because the existing read-open heuristic returns `False`.

- [ ] **Step 3: Run the stale-lock test to preserve current behavior**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_windows.py::test_downloader_running_ignores_unlocked_stale_lock_file
```

Expected: PASS.

### Task 3: Probe the real Windows lock

**Files:**
- Modify: `src/tg_video_downloader/windows.py:209`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Replace `_file_is_locked()` with the minimal byte-lock probe**

Use this implementation:

```python
def _file_is_locked(path: Path) -> bool:
    mode = "r+b" if os.name == "nt" else "rb"
    try:
        handle = path.open(mode)
    except PermissionError:
        return True
    except OSError:
        return False

    with handle:
        if os.name != "nt":
            return False

        import msvcrt

        acquired = False
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            acquired = True
        except OSError:
            return True
        finally:
            if acquired:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    return False
```

- [ ] **Step 2: Run the real-lock tests and verify GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_windows.py
```

Expected: all Windows runtime tests pass, including occupied and released lock behavior.

- [ ] **Step 3: Verify the update controller lifecycle tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_gui_controller.py -k update
```

Expected: running service is stopped and marked for restoration; already-stopped service remains stopped; timeout recovery tests pass.

- [ ] **Step 4: Commit the regression fix**

Run:

```powershell
git add tests/test_windows.py src/tg_video_downloader/windows.py
git commit -m "fix: detect active Windows byte locks"
```

### Task 4: Prepare v0.3.1 release metadata

**Files:**
- Modify: `tests/test_release_metadata.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/verification.md`

- [ ] **Step 1: Change the release metadata test first**

Rename `test_v030_docs_explain_selective_download_boundaries` to `test_v031_docs_explain_release_boundaries`, change the version assertion to:

```python
assert pyproject["project"]["version"] == "0.3.1"
```

Keep all selective-download assertions and add:

```python
assert "Windows 字节锁" in readme
assert "更新前正确停止后台" in readme
```

- [ ] **Step 2: Run the metadata test and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_release_metadata.py
```

Expected: FAIL because `pyproject.toml` is still 0.3.0 and README lacks the v0.3.1 lock note.

- [ ] **Step 3: Bump the package version**

Change `pyproject.toml` to:

```toml
version = "0.3.1"
```

- [ ] **Step 4: Document the lock fix in README**

Add this paragraph to the online-update section:

```markdown
v0.3.1 起，Windows 字节锁探测与后台单实例锁使用同一机制，安装更新时能够在更新前正确停止后台，并只在更新前确实运行时恢复后台。
```

- [ ] **Step 5: Record verification evidence**

Append this section to `docs/verification.md` after running the listed checks:

```markdown
## v0.3.1 Windows 锁检测修复证据（2026-08-26）

- 根因复现：真实 `SingleInstance` 持有 `downloader.lock` 时，旧实现的 `downloader_is_running()` 返回 `False`；回归测试在修复前按预期失败。
- 修复验证：状态探测改为尝试同一首字节非阻塞锁；占用时返回运行中，释放后及无人持有的遗留锁文件返回未运行。
- 更新生命周期：既有控制器测试确认运行中的后台会先停止并标记恢复，停止状态不会被错误启动，停止超时保持安全回退。
- 发布候选：`scripts/check.ps1` 共 365 项测试通过，`python -m pip check` 无损坏依赖，源码编译检查通过。
- 实机只读验收：真实后台锁被识别为运行中，心跳在观察窗口内继续推进；验收没有停止当前下载或执行更新安装。
```

- [ ] **Step 6: Run the metadata test and verify GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_release_metadata.py
```

Expected: PASS.

- [ ] **Step 7: Commit release metadata**

Run:

```powershell
git add pyproject.toml README.md docs/verification.md tests/test_release_metadata.py
git commit -m "docs: prepare the v0.3.1 lock fix release"
```

### Task 5: Review and verify the release candidate

**Files:**
- Review: `src/tg_video_downloader/windows.py`
- Review: `tests/test_windows.py`
- Review: `tests/test_release_metadata.py`
- Review: `README.md`
- Review: `docs/verification.md`

- [ ] **Step 1: Inspect the complete change**

Run:

```powershell
git diff --check master...HEAD
git diff --stat master...HEAD
git diff master...HEAD -- src/tg_video_downloader/windows.py tests/test_windows.py tests/test_release_metadata.py README.md docs/verification.md
```

Expected: only the scoped lock fix, tests and v0.3.1 release documentation; no whitespace errors.

- [ ] **Step 2: Run dependency and full project checks**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
```

Expected: no broken requirements and 365 tests pass.

- [ ] **Step 3: Probe the real active downloader without stopping it**

Run with the worktree Python while targeting the main project root:

```powershell
& .\.venv\Scripts\python.exe -c "from pathlib import Path; from tg_video_downloader.paths import ProjectPaths; from tg_video_downloader.windows import downloader_is_running; print(downloader_is_running(ProjectPaths.from_root(Path(r'D:\Codex Project\Telegram自动化脚本'))))"
```

Expected: `True` while the existing background downloader remains active.

- [ ] **Step 4: Confirm heartbeat continuity and clean branch**

Read `.runtime/heartbeat.json` twice three seconds apart and compare only `updated_at`, `status`, PID and aggregate counts. Then run:

```powershell
git status --short --branch
```

Expected: heartbeat advances with `running`; feature branch is clean.

### Task 6: Merge and publish v0.3.1

**Files:**
- Merge: `codex/online-update-lock-v031` into `master`
- Tag: `v0.3.1`

- [ ] **Step 1: Fast-forward merge into the main checkout**

Run from the main project root:

```powershell
git merge --ff-only codex/online-update-lock-v031
```

Expected: `master` advances without conflicts.

- [ ] **Step 2: Reinstall and verify the merged result**

Run:

```powershell
& .\scripts\bootstrap.ps1
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
```

Expected: installed version 0.3.1, no broken requirements and 365 tests pass.

- [ ] **Step 3: Create an annotated release tag**

Run:

```powershell
git tag -a v0.3.1 -m "v0.3.1 Windows lock detection fix"
```

Expected: `v0.3.1^{}` resolves to the merged `master` commit.

- [ ] **Step 4: Push without force to both remotes**

Run:

```powershell
git push github master
git push modelscope master
git push github v0.3.1
git push modelscope v0.3.1
```

Expected: all four pushes succeed without force.

- [ ] **Step 5: Read back remote branch and tag targets**

Run:

```powershell
git ls-remote github refs/heads/master refs/tags/v0.3.1 'refs/tags/v0.3.1^{}'
git ls-remote modelscope refs/heads/master refs/tags/v0.3.1 'refs/tags/v0.3.1^{}'
```

Expected: both remote `master` refs and both peeled tags resolve to the same release commit.

- [ ] **Step 6: Remove only the worktree created by this plan**

Run from the main project root after all remote verification succeeds:

```powershell
git worktree remove 'D:\Codex Project\Telegram自动化脚本\.worktrees\online-update-lock-v031'
git worktree prune
git branch -d codex/online-update-lock-v031
```

Expected: the release worktree and merged feature branch are removed; unrelated worktrees remain untouched.
