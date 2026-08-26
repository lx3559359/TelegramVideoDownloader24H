# v0.3.5 Search Caption Boundary Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make truncated Telegram search captions idempotently normalized so real background IPC searches cannot fail when character 120 is a word-separating space, then publish and live-verify v0.3.5.

**Architecture:** Keep the v0.3.4 loopback IPC protocol unchanged. Fix the single producer-side normalization function so every direct and background search result already satisfies the existing strict protocol invariant, prove the boundary through pure-function and real codec tests, then publish an immutable patch tag.

**Tech Stack:** Python 3.12, pytest/pytest-asyncio, Telethon, Windows PowerShell, Git annotated tags.

---

## File map

- `src/tg_video_downloader/selective.py`: owns caption whitespace normalization and the 120-character display bound.
- `tests/test_selective.py`: proves normalization output, length, whitespace and idempotence.
- `tests/test_search_ipc.py`: proves normalized boundary captions survive the real strict IPC response codec.
- `pyproject.toml`: exposes package version 0.3.5.
- `tests/test_release_metadata.py`: locks release version and user-facing boundaries.
- `README.md`: gives users a concise v0.3.5 fix note without changing usage.
- `docs/verification.md`: records measured candidate evidence and the release boundary.

### Task 1: Reproduce and fix the non-idempotent caption boundary

**Files:**
- Modify: `tests/test_selective.py`
- Modify: `tests/test_search_ipc.py`
- Modify: `src/tg_video_downloader/selective.py:76-79`

- [ ] **Step 1: Create an isolated hotfix worktree**

From `D:\Codex Project\Telegram自动化脚本`, use the `using-git-worktrees` workflow. Verify `.worktrees` is ignored, then create branch `codex/search-caption-boundary-v035` from `b2f069e` at:

```powershell
git check-ignore .worktrees
git worktree add ".worktrees/search-caption-boundary-v035" -b "codex/search-caption-boundary-v035" b2f069e
```

Run setup in the new worktree:

```powershell
& .\scripts\bootstrap.ps1
& .\.venv\Scripts\python.exe -m pytest -q tests\test_selective.py tests\test_search_ipc.py
```

Expected: the existing focused suite passes before any hotfix edits. Do not point the worktree at the main checkout's `.runtime`, configuration, downloads or session.

- [ ] **Step 2: Write both failing regression tests**

Add this pure-function test after `test_caption_is_single_line_and_bounded` in `tests/test_selective.py`:

```python
def test_caption_truncation_does_not_leave_boundary_space() -> None:
    normalized = normalize_search_caption("a" * 119 + " " + "b")

    assert normalized == "a" * 119
    assert normalize_search_caption(normalized) == normalized
```

Extend the import from `tg_video_downloader.selective` in `tests/test_search_ipc.py` to include `normalize_search_caption`, then add this test after `test_response_json_round_trip_preserves_message_and_queue_state`:

```python
def test_response_round_trip_accepts_caption_truncated_at_word_boundary() -> None:
    ipc = _ipc()
    item = _item(
        caption=normalize_search_caption("a" * 119 + " " + "b")
    )

    raw = ipc.encode_success((item,))

    assert ipc.decode_response(raw) == (item,)
```

- [ ] **Step 3: Run the regressions and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests\test_selective.py::test_caption_truncation_does_not_leave_boundary_space `
  tests\test_search_ipc.py::test_response_round_trip_accepts_caption_truncated_at_word_boundary
```

Expected: both tests fail against v0.3.4. The pure test receives 119 `a` characters followed by a space, and the codec test raises `SearchChannelError` with the existing caption-validation message. If either test fails for import, fixture or syntax reasons, correct the test and rerun until the failures prove the production defect.

- [ ] **Step 4: Implement the one-line source fix**

Change `normalize_search_caption()` in `src/tg_video_downloader/selective.py` to:

```python
def normalize_search_caption(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text[:120].rstrip()
```

Do not change the IPC schema, its strict decoder, the 120-character limit, gateway search logic or GUI rendering.

- [ ] **Step 5: Run focused tests and verify GREEN**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests\test_selective.py `
  tests\test_search_ipc.py `
  tests\test_gateway.py `
  tests\test_background_search_integration.py
```

Expected: all focused normalization, codec, gateway and background integration tests pass, including the existing rejection of manually constructed multiline and 121-character captions.

- [ ] **Step 6: Commit the behavioral fix**

```powershell
git add src/tg_video_downloader/selective.py tests/test_selective.py tests/test_search_ipc.py
git diff --cached --check
git commit -m "fix: normalize truncated search captions"
```

### Task 2: Prepare v0.3.5 release metadata

**Files:**
- Modify: `tests/test_release_metadata.py`
- Modify: `pyproject.toml:7`
- Modify: `README.md:44-45`

- [ ] **Step 1: Change release expectations first**

Rename the release test to `test_v035_docs_explain_caption_boundary_fix`, change its version assertion to:

```python
assert pyproject["project"]["version"] == "0.3.5"
```

Keep every existing boundary assertion and add:

```python
assert "120 字符边界" in readme
assert "后台共享检索" in readme
assert "直接检索" in readme
```

- [ ] **Step 2: Run the metadata test and verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests\test_release_metadata.py
```

Expected: failure because `pyproject.toml` still reports 0.3.4 and the README lacks the v0.3.5 boundary note.

- [ ] **Step 3: Apply the minimal release metadata changes**

Change `pyproject.toml` to:

```toml
version = "0.3.5"
```

Add this bullet immediately after the existing v0.3.4 daily-use bullet in `README.md`:

```markdown
- v0.3.5 修复了说明文字恰好在 120 字符边界截断时的检索错误；说明仍以最多 120 字符的单行文本显示，后台共享检索、直接检索和下载队列行为不变。
```

- [ ] **Step 4: Reinstall and verify metadata GREEN**

```powershell
& .\scripts\bootstrap.ps1
& .\.venv\Scripts\python.exe -m pytest -q tests\test_release_metadata.py
& .\.venv\Scripts\python.exe -c "from importlib.metadata import version; print(version('telegram-video-downloader'))"
& .\.venv\Scripts\python.exe -m pip check
```

Expected: the release test passes, installed version prints `0.3.5`, and pip reports `No broken requirements found.` No dependency version changes are permitted.

- [ ] **Step 5: Commit release metadata**

```powershell
git add pyproject.toml README.md tests/test_release_metadata.py
git diff --cached --check
git commit -m "docs: prepare the v0.3.5 caption hotfix"
```

### Task 3: Verify and document the complete candidate

**Files:**
- Modify: `docs/verification.md`
- Review: all files changed since `b2f069e`

- [ ] **Step 1: Perform static scope review**

```powershell
git diff --check b2f069e...HEAD
git diff --stat b2f069e...HEAD
git diff b2f069e...HEAD -- `
  src/tg_video_downloader/selective.py `
  tests/test_selective.py `
  tests/test_search_ipc.py `
  pyproject.toml `
  README.md `
  tests/test_release_metadata.py
rg -n "TODO|TBD|FIXME|NotImplementedError" `
  src/tg_video_downloader/selective.py `
  tests/test_selective.py `
  tests/test_search_ipc.py
```

Expected: no whitespace errors or placeholders; the production diff is the single normalization change and no IPC, gateway, GUI, database or dependency code changed.

- [ ] **Step 2: Run dependency and complete project gates**

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
```

Expected: pip reports no broken requirements; every test passes, including the two new regressions, followed by successful source compilation and project-local path checks. Record the exact test count and elapsed time printed by this run.

- [ ] **Step 3: Read the real downloader without modifying it**

From the main checkout, read `.runtime/heartbeat.json` twice eight seconds apart and probe existing downloader/supervisor byte locks with project readers. Record only PID, status, update time, current downloaded/total bytes and resume flag; do not print Telegram text, credentials, endpoint token or filenames.

Expected: the current v0.3.4 downloader remains running and advances while the isolated candidate is tested. Do not stop or restart it before the candidate and merged-master gates pass.

- [ ] **Step 4: Append measured candidate evidence**

Append `## v0.3.5 检索说明边界热修复证据（2026-08-27）` to `docs/verification.md`. Include these measured facts:

- the old normalizer produced a 120-character value ending in a space and failed idempotence;
- both RED tests failed for that exact reason before production code changed;
- the new normalizer removes only truncation-created trailing whitespace and is idempotent;
- strict IPC validation and multiline/overlength rejection remain unchanged;
- exact focused and complete test results from Steps 1-2;
- no dependency, protocol, thread, process, poller, database or persistence change;
- the real v0.3.4 downloader stayed active and advanced during isolated candidate verification;
- this evidence describes a candidate and does not claim publication or live post-restart acceptance.

- [ ] **Step 5: Commit evidence and rerun the final clean-candidate gate**

```powershell
git add docs/verification.md
git diff --cached --check
git commit -m "docs: verify the v0.3.5 caption hotfix"
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
git diff --check b2f069e...HEAD
git status --short --branch
git log --oneline b2f069e..HEAD
```

Expected: dependencies and the full suite pass again, the feature branch is clean, and exactly three scoped hotfix commits follow `b2f069e`.

### Task 4: Review and merge the hotfix

**Files:**
- Review: `b2f069e...codex/search-caption-boundary-v035`

- [ ] **Step 1: Request focused code review**

Use `requesting-code-review` if an independent reviewer is callable. Ask it to verify normalization idempotence, protocol invariants, RED/GREEN evidence, release metadata and absence of scope expansion. If no independent reviewer is available, perform and record the same structured self-review; do not claim independent review.

Resolve every confirmed Critical or Important issue with its own failing regression, minimal fix and focused green run before continuing.

- [ ] **Step 2: Merge into local master**

From `D:\Codex Project\Telegram自动化脚本`, verify both main and hotfix worktrees are clean, then merge without rebasing or rewriting the published v0.3.4 history:

```powershell
git checkout master
git merge --no-ff codex/search-caption-boundary-v035 -m "release: merge v0.3.5 caption hotfix"
```

- [ ] **Step 3: Verify the exact merged commit**

```powershell
& .\scripts\bootstrap.ps1
& .\.venv\Scripts\python.exe -m pip check
& .\scripts\check.ps1
git diff --check
git status --short --branch
```

Expected: installed version is 0.3.5, pip and the full suite pass on merged `master`, and the worktree is clean. Publication is blocked if any command fails.

### Task 5: Publish immutable v0.3.5 to both update sources

**Files:**
- No file changes

- [ ] **Step 1: Reconfirm remote state and tag absence**

```powershell
git fetch --no-tags github master
git fetch --no-tags modelscope master
git ls-remote github refs/heads/master refs/tags/v0.3.5 "refs/tags/v0.3.5^{}"
git ls-remote modelscope refs/heads/master refs/tags/v0.3.5 "refs/tags/v0.3.5^{}"
```

Expected: both remote masters still point to the published v0.3.4 lineage and neither remote contains v0.3.5. Stop rather than overwrite if either tag exists with another object.

- [ ] **Step 2: Create and inspect the annotated tag**

```powershell
git tag -a v0.3.5 -m "TelegramDownloader 0.3.5"
git for-each-ref refs/tags/v0.3.5 --format="%(refname) %(objecttype) %(objectname) %(subject)"
git rev-list -n 1 v0.3.5
git rev-parse HEAD
```

Expected: `v0.3.5` is an annotated tag and its peeled commit equals merged `HEAD`.

- [ ] **Step 3: Push master and tag to both remotes**

```powershell
git push github master
git push modelscope master
git push github refs/tags/v0.3.5
git push modelscope refs/tags/v0.3.5
```

Do not force-push. If one source temporarily fails after the other succeeds, keep the exact local tag and retry only the missing normal push.

- [ ] **Step 4: Verify publication and updater discovery**

```powershell
git ls-remote github refs/heads/master refs/tags/v0.3.5 "refs/tags/v0.3.5^{}"
git ls-remote modelscope refs/heads/master refs/tags/v0.3.5 "refs/tags/v0.3.5^{}"
& .\.venv\Scripts\python.exe -c "from tg_video_downloader.update import UpdateManager; r=UpdateManager(current_version='0.3.4').check_latest(); print(None if r is None else (str(r.version), r.tag, r.source))"
```

Expected: both remote masters and peeled tags equal merged `HEAD`; the old-version simulation reports `('0.3.5', 'v0.3.5', 'GitHub')` or the same tag from the configured fallback source.

### Task 6: Restart and live-verify the real downloader

**Files:**
- Runtime observation only: `.runtime/heartbeat.json`, `.runtime/search-endpoint.json`, downloader/supervisor byte locks

- [ ] **Step 1: Capture a privacy-safe pre-restart baseline**

Record current service PID, status, downloaded and total bytes, resume flag, queue counts and whether downloader/supervisor locks are held. Do not print credentials, captions, filenames, Telegram session data or the endpoint token.

- [ ] **Step 2: Stop and restart through existing lifecycle helpers**

Use `ProjectPaths.from_root(Path.cwd())`, `request_stop()`, `wait_for_downloader_stop()`, `clear_stop()` and `start_hidden_supervisor()` from `tg_video_downloader.windows`. Require both byte locks to release and the old endpoint to disappear before starting. Do not delete runtime files, SQLite files, Telegram session files or partial downloads.

- [ ] **Step 3: Wait on readiness conditions**

Poll for at most 45 seconds until:

- heartbeat status is `running` with a PID different from the pre-restart PID;
- `search-endpoint.json` has schema 1, host `127.0.0.1`, the same new PID and a non-empty token;
- downloader and supervisor locks are held;
- the active progress snapshot reports `resumed=true` when a partial file is active.

Print endpoint token length only, never the token value.

- [ ] **Step 4: Run the original real-search acceptance**

Instantiate `GuiController` against the main project paths with a gateway factory that raises `AssertionError("direct gateway must not be created")`. Call:

```python
await controller.search_videos(
    -1004439542081,
    "",
    "",
    "",
    20,
)
```

Expected: the call succeeds through IPC and returns zero to twenty typed rows without invoking the direct gateway factory. Print only result count and aggregate queue-state counts; do not print captions, filenames, message IDs or endpoint metadata. A returned Telegram/channel error blocks completion.

- [ ] **Step 5: Confirm download continuity**

Read heartbeat again after an observation interval and confirm the new PID remains `running`; downloaded bytes advance or the current item completes and queue counters change consistently. Confirm logs contain no new traceback for caption validation and no search content was written.

### Task 7: Final cleanup and handoff

**Files:**
- No source changes

- [ ] **Step 1: Verify final repository and remote state**

```powershell
git status --short --branch
git log -5 --oneline --decorate
git ls-remote github refs/heads/master "refs/tags/v0.3.5^{}"
git ls-remote modelscope refs/heads/master "refs/tags/v0.3.5^{}"
```

Expected: local and both remote masters and v0.3.5 peeled tags agree; main is clean.

- [ ] **Step 2: Clean only owned merged worktrees**

From the main root, verify the resolved targets remain under `D:\Codex Project\Telegram自动化脚本\.worktrees`, then remove the merged v0.3.4 and v0.3.5 worktrees, prune registrations and delete their merged branches:

```powershell
git worktree remove "D:\Codex Project\Telegram自动化脚本\.worktrees\background-search-ipc-v034"
git worktree remove "D:\Codex Project\Telegram自动化脚本\.worktrees\search-caption-boundary-v035"
git worktree prune
git branch -d codex/background-search-ipc-v034
git branch -d codex/search-caption-boundary-v035
```

Do not touch `C:\Users\luojixiang1\.codex\worktrees\dcf3\Telegram自动化脚本` or branch `codex/download-policy-progress-resume`.

- [ ] **Step 3: Record durable project memory when available**

Use `flowus-knowledge-memory` only if FlowUs tools are callable. Update the existing Telegram downloader project note with the v0.3.4 post-release defect, v0.3.5 root-cause fix, publication commit/tag, full test result and live IPC acceptance. Do not store credentials, tokens, captions, filenames or Telegram identifiers.

- [ ] **Step 4: Report outcome**

Lead with whether v0.3.5 is published and live verification passed. Include the release commit, both remote links, exact full-test count, new downloader PID, resume/download continuity result and real IPC search result count. Explicitly report any incomplete gate instead of implying completion.
