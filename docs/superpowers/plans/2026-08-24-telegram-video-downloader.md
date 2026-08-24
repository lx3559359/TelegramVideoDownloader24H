# Telegram 群视频自动下载器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个仅下载用户白名单群组视频、首次全量回溯并持续实时监听的轻量 Windows Telegram 个人账号工具，带按需图形配置器、任务级断点恢复和项目内数据隔离。

**Architecture:** 单个 Telethon 异步后台服务负责事件监听、停机补抓和历史扫描，SQLite 同时保存扫描游标与优先级下载队列，单工作器把视频流式写入项目内临时目录后原子落盘。Tkinter 配置器只在用户操作时运行，PowerShell 守护脚本负责设置 D 盘环境和异常重启。

**Tech Stack:** Python 3.11+、Telethon 1.x、Tkinter/ttk、SQLite、pytest、pytest-asyncio、PowerShell。

---

## 文件结构

| 路径 | 单一职责 |
|---|---|
| `pyproject.toml` | Python 包、运行依赖、测试依赖和命令入口 |
| `.gitignore` | 排除凭据、会话、下载、缓存、日志和虚拟环境 |
| `config.example.toml` | 非敏感配置格式示例 |
| `src/tg_video_downloader/paths.py` | 解析并创建项目内允许的全部数据路径 |
| `src/tg_video_downloader/models.py` | 组件间共享的不可变数据模型与枚举 |
| `src/tg_video_downloader/config.py` | TOML 配置、凭据、校验和原子写入 |
| `src/tg_video_downloader/media.py` | 视频类型判定 |
| `src/tg_video_downloader/naming.py` | Windows 安全文件名与最终路径生成 |
| `src/tg_video_downloader/state.py` | SQLite schema、群游标、持久任务队列和统计 |
| `src/tg_video_downloader/gateway.py` | Telethon 登录、群列表、消息遍历、事件和下载适配 |
| `src/tg_video_downloader/coordinator.py` | 白名单热更新、实时消息、补抓与历史扫描协调 |
| `src/tg_video_downloader/worker.py` | 单下载工作器、空间检查、临时文件和重试 |
| `src/tg_video_downloader/observability.py` | 轮转日志、脱敏和心跳快照 |
| `src/tg_video_downloader/windows.py` | 单实例锁、停止标记、进程启动和阻止空闲休眠 |
| `src/tg_video_downloader/service.py` | 后台服务生命周期编排 |
| `src/tg_video_downloader/gui/controller.py` | GUI 与登录、配置、进程控制之间的边界 |
| `src/tg_video_downloader/gui/app.py` | Tkinter 三页配置器 |
| `src/tg_video_downloader/cli.py` | `gui`、`service` 命令入口 |
| `scripts/bootstrap.ps1` | 在项目内创建虚拟环境并安装依赖 |
| `scripts/launch-gui.ps1` | 设置项目内环境并启动图形配置器 |
| `scripts/run-supervisor.ps1` | 隐藏运行、异常退避重启和主动停止识别 |
| `scripts/check.ps1` | 项目内缓存条件下运行完整验证 |
| `打开配置器.cmd` | 用户双击入口 |
| `tests/fakes.py` | 可编排的 Telegram 测试替身 |
| `tests/test_*.py` | 按组件组织的单元与集成测试 |
| `README.md` | 安装、首次登录、运行、停止和限制说明 |

## Task 1: 项目骨架和 D 盘路径隔离

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `config.example.toml`
- Create: `src/tg_video_downloader/__init__.py`
- Create: `src/tg_video_downloader/paths.py`
- Create: `tests/test_paths.py`
- Create: `scripts/bootstrap.ps1`

- [ ] **Step 1: 写路径隔离的失败测试**

创建 `tests/test_paths.py`：

```python
from pathlib import Path

from tg_video_downloader.paths import ProjectPaths


def test_create_runtime_directories_under_project(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()

    for path in paths.writable_directories:
        assert path.is_dir()
        assert path.is_relative_to(tmp_path.resolve())


def test_reject_download_directory_outside_project(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    outside = tmp_path.parent / "outside-downloads"

    try:
        paths.assert_within_root(outside)
    except ValueError as error:
        assert "项目目录之外" in str(error)
    else:
        raise AssertionError("outside path should be rejected")
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `python -m pytest tests/test_paths.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'tg_video_downloader'`.

- [ ] **Step 3: 创建包配置和路径实现**

创建 `pyproject.toml`：

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "telegram-video-downloader"
version = "0.1.0"
description = "Whitelist-only Telegram group video downloader for Windows"
requires-python = ">=3.11"
dependencies = [
  "telethon>=1.44,<2",
  "tzdata>=2024.1",
]

[project.optional-dependencies]
dev = [
  "pytest>=8,<10",
  "pytest-asyncio>=0.24,<2",
]

[project.scripts]
tg-video-downloader = "tg_video_downloader.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

创建空的 `src/tg_video_downloader/__init__.py`，并创建 `src/tg_video_downloader/paths.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    runtime: Path
    cache: Path
    temp: Path
    logs: Path
    downloads: Path
    config: Path
    credentials: Path
    session: Path
    database: Path
    heartbeat: Path
    stop_flag: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        resolved = root.resolve()
        runtime = resolved / ".runtime"
        return cls(
            root=resolved,
            runtime=runtime,
            cache=resolved / ".cache",
            temp=resolved / ".tmp",
            logs=resolved / "logs",
            downloads=resolved / "downloads",
            config=resolved / "config.toml",
            credentials=runtime / "credentials.toml",
            session=runtime / "telegram.session",
            database=runtime / "state.sqlite3",
            heartbeat=runtime / "heartbeat.json",
            stop_flag=runtime / "stop.flag",
        )

    @property
    def writable_directories(self) -> tuple[Path, ...]:
        return self.runtime, self.cache, self.temp, self.logs, self.downloads

    def assert_within_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"路径位于项目目录之外: {resolved}")
        return resolved

    def ensure_directories(self) -> None:
        for path in self.writable_directories:
            self.assert_within_root(path).mkdir(parents=True, exist_ok=True)
```

创建 `.gitignore`：

```gitignore
.venv/
.cache/
.tmp/
.runtime/
downloads/
logs/
config.toml
__pycache__/
*.py[cod]
.pytest_cache/
```

创建 `config.example.toml`：

```toml
config_poll_seconds = 5
prevent_sleep = true

[[groups]]
chat_id = -1001234567890
title = "示例群（不会被自动启用）"
```

- [ ] **Step 4: 创建只向项目目录写入的安装脚本**

创建 `scripts/bootstrap.ps1`：

```powershell
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TempRoot = Join-Path $ProjectRoot ".tmp"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$VenvRoot = Join-Path $ProjectRoot ".venv"

[System.IO.Directory]::CreateDirectory($TempRoot) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $CacheRoot "pip")) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $CacheRoot "pycache")) | Out-Null

$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:PYTHONPYCACHEPREFIX = Join-Path $CacheRoot "pycache"

if (-not (Test-Path -LiteralPath (Join-Path $VenvRoot "Scripts\python.exe"))) {
    $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $PythonLauncher) {
        & py -3 -m venv $VenvRoot
    } else {
        & python -m venv $VenvRoot
    }
}

$ProjectPython = Join-Path $VenvRoot "Scripts\python.exe"
& $ProjectPython -m pip install --disable-pip-version-check -e "$ProjectRoot[dev]"
& $ProjectPython -c "import sys; assert sys.version_info >= (3, 11), '需要 Python 3.11 或更高版本'"
```

- [ ] **Step 5: 用项目虚拟环境运行路径测试**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1`

Expected: `.venv`、`.cache`、`.tmp` 均创建在项目目录，安装命令 exit 0。

Run: `.venv\Scripts\python.exe -m pytest tests/test_paths.py -v`

Expected: 2 passed.

- [ ] **Step 6: 提交项目骨架**

```powershell
git add pyproject.toml .gitignore config.example.toml src/tg_video_downloader/__init__.py src/tg_video_downloader/paths.py tests/test_paths.py scripts/bootstrap.ps1
git commit -m "build: scaffold project-local Python runtime"
```

## Task 2: 配置模型、凭据和原子热加载

**Files:**
- Create: `src/tg_video_downloader/models.py`
- Create: `src/tg_video_downloader/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: 写配置行为的失败测试**

创建 `tests/test_config.py`：

```python
from pathlib import Path

import pytest

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.models import AppConfig, Credentials, GroupTarget
from tg_video_downloader.paths import ProjectPaths


def test_round_trip_config_and_credentials(tmp_path: Path) -> None:
    store = ConfigStore(ProjectPaths.from_root(tmp_path))
    config = AppConfig(groups=(GroupTarget(-1001, "A 群"), GroupTarget(-1002, "B 群")))
    credentials = Credentials(api_id=12345, api_hash="secret-hash", phone="+8613800000000")

    store.save_config(config)
    store.save_credentials(credentials)

    assert store.load_config() == config
    assert store.load_credentials() == credentials
    assert "secret-hash" not in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_require_non_empty_whitelist() -> None:
    with pytest.raises(ValueError, match="至少选择一个群"):
        AppConfig().require_targets()


def test_reloader_keeps_last_valid_config(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    store = ConfigStore(paths)
    valid = AppConfig(groups=(GroupTarget(-1001, "A 群"),))
    store.save_config(valid)
    reloader = store.reloader()
    assert reloader.load_if_changed() == valid

    paths.config.write_text("[[groups]\n", encoding="utf-8")
    assert reloader.load_if_changed() == valid
    assert reloader.last_error is not None
```

- [ ] **Step 2: 运行测试并确认缺少配置类型**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`

Expected: FAIL importing `AppConfig` or `ConfigStore`.

- [ ] **Step 3: 实现共享模型**

创建 `src/tg_video_downloader/models.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class JobSource(StrEnum):
    LIVE = "live"
    CATCHUP = "catchup"
    HISTORY = "history"


class JobStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    PERMANENT_ERROR = "permanent_error"


@dataclass(frozen=True)
class GroupTarget:
    chat_id: int
    title: str


@dataclass(frozen=True)
class AppConfig:
    groups: tuple[GroupTarget, ...] = ()
    config_poll_seconds: int = 5
    prevent_sleep: bool = True

    def require_targets(self) -> "AppConfig":
        if not self.groups:
            raise ValueError("至少选择一个群后才能启动下载器")
        if len({group.chat_id for group in self.groups}) != len(self.groups):
            raise ValueError("群组白名单包含重复群 ID")
        return self


@dataclass(frozen=True)
class Credentials:
    api_id: int
    api_hash: str
    phone: str

    def validate(self) -> "Credentials":
        if self.api_id <= 0 or not self.api_hash.strip() or not self.phone.strip():
            raise ValueError("API ID、API Hash 和手机号均不能为空")
        return self


@dataclass(frozen=True)
class MessageInfo:
    chat_id: int
    message_id: int
    date: datetime
    mime_type: str | None
    original_name: str | None
    extension: str
    size: int | None
    is_video: bool
    is_animated: bool
    is_round: bool


@dataclass(frozen=True)
class DownloadJob:
    chat_id: int
    message_id: int
    group_title: str
    source: JobSource
    status: JobStatus
    message: MessageInfo
    attempts: int
```

- [ ] **Step 4: 实现 TOML 读写和最后有效配置**

创建 `src/tg_video_downloader/config.py`，使用 `tomllib.loads` 读取；写入字符串时使用 `json.dumps(value, ensure_ascii=False)` 生成 TOML 兼容引号；保存时写入同目录临时文件、`flush`、`os.fsync` 后用 `os.replace` 原子替换。完整公开接口固定为 `ConfigStore.__init__(paths)`、`load_config() -> AppConfig`、`save_config(config) -> None`、`load_credentials() -> Credentials`、`save_credentials(credentials) -> None`、`reloader() -> ConfigReloader`，以及 `ConfigReloader.load_if_changed() -> AppConfig | None` 和可读属性 `last_error: str | None`。

写出的非敏感配置格式固定为：

```toml
config_poll_seconds = 5
prevent_sleep = true

[[groups]]
chat_id = -1001
title = "A 群"
```

凭据格式固定为：

```toml
api_id = 12345
api_hash = "secret-hash"
phone = "+8613800000000"
```

`load_config()` 将 `groups` 转成 `tuple[GroupTarget, ...]`，校验轮询秒数范围为 1–60；`load_credentials()` 调用 `Credentials.validate()`；`ConfigReloader` 以 `st_mtime_ns` 判断变化，解析失败时保留并返回上次有效配置，同时设置 `last_error`。

- [ ] **Step 5: 运行配置测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`

Expected: 3 passed.

- [ ] **Step 6: 提交配置组件**

```powershell
git add src/tg_video_downloader/models.py src/tg_video_downloader/config.py tests/test_config.py
git commit -m "feat: add atomic whitelist configuration"
```

## Task 3: 视频识别和 Windows 安全路径

**Files:**
- Create: `src/tg_video_downloader/media.py`
- Create: `src/tg_video_downloader/naming.py`
- Create: `tests/test_media.py`
- Create: `tests/test_naming.py`

- [ ] **Step 1: 写视频分类失败测试**

创建 `tests/test_media.py`，用 `dataclasses.replace` 从一个普通视频样本构造 MIME 视频文件、GIF、圆形视频、图片和普通文档，断言只有普通视频与非动画 `video/*` 返回 `True`：

```python
from dataclasses import replace
from datetime import UTC, datetime

from tg_video_downloader.media import is_downloadable_video
from tg_video_downloader.models import MessageInfo


BASE = MessageInfo(-1001, 7, datetime.now(UTC), "video/mp4", "a.mp4", ".mp4", 10, True, False, False)


def test_video_rules() -> None:
    assert is_downloadable_video(BASE)
    assert is_downloadable_video(replace(BASE, is_video=False, mime_type="video/x-matroska"))
    assert not is_downloadable_video(replace(BASE, is_animated=True))
    assert not is_downloadable_video(replace(BASE, is_round=True))
    assert not is_downloadable_video(replace(BASE, is_video=False, mime_type="image/jpeg"))
    assert not is_downloadable_video(replace(BASE, is_video=False, mime_type="application/pdf"))
```

- [ ] **Step 2: 写文件路径失败测试**

创建 `tests/test_naming.py`：

```python
from datetime import UTC, datetime
from pathlib import Path

from tg_video_downloader.models import MessageInfo
from tg_video_downloader.naming import build_final_path, sanitize_windows_name
from tg_video_downloader.paths import ProjectPaths


def test_sanitize_reserved_and_forbidden_names() -> None:
    assert sanitize_windows_name("CON") == "_CON"
    assert sanitize_windows_name('bad<>:"/\\|?* .') == "bad_________"


def test_build_path_is_unique_and_inside_downloads(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    message = MessageInfo(
        -1001, 42, datetime(2026, 8, 24, 1, tzinfo=UTC), "video/mp4",
        "same:name.mp4", ".mp4", 100, True, False, False,
    )
    result = build_final_path(paths, "测试群", message)

    assert result == paths.downloads / "测试群_-1001" / "2026-08" / "42_same_name.mp4"
    assert result.resolve().is_relative_to(paths.downloads.resolve())
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_media.py tests/test_naming.py -v`

Expected: FAIL importing `media` or `naming`.

- [ ] **Step 4: 实现分类和命名函数**

创建 `src/tg_video_downloader/media.py`：

```python
from tg_video_downloader.models import MessageInfo


def is_downloadable_video(message: MessageInfo) -> bool:
    if message.is_animated or message.is_round:
        return False
    return message.is_video or bool(message.mime_type and message.mime_type.lower().startswith("video/"))
```

创建 `src/tg_video_downloader/naming.py`，使用以下完整实现：

```python
from __future__ import annotations

import re
from pathlib import Path
from zoneinfo import ZoneInfo

from tg_video_downloader.models import MessageInfo
from tg_video_downloader.paths import ProjectPaths


WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_windows_name(value: str, max_length: int = 120) -> str:
    cleaned = FORBIDDEN.sub("_", value).rstrip(" .") or "_"
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(" .") or "_"


def build_final_path(paths: ProjectPaths, group_title: str, message: MessageInfo) -> Path:
    group_dir = sanitize_windows_name(f"{group_title}_{message.chat_id}")
    month = message.date.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")
    parent = paths.downloads / group_dir / month
    filename_limit = min(180, max(32, 240 - len(str(parent)) - 1))
    if message.original_name:
        original = sanitize_windows_name(message.original_name, 150)
        filename = sanitize_windows_name(f"{message.message_id}_{original}", filename_limit)
    else:
        filename = f"{message.message_id}_video{message.extension or '.mp4'}"
    return paths.assert_within_root(parent / filename)
```

- [ ] **Step 5: 运行测试并校准明确的清理结果**

Run: `.venv\Scripts\python.exe -m pytest tests/test_media.py tests/test_naming.py -v`

Expected: all tests pass，且保留名、禁止字符、唯一消息 ID 和项目内路径断言全部成立。

- [ ] **Step 6: 提交媒体规则**

```powershell
git add src/tg_video_downloader/media.py src/tg_video_downloader/naming.py tests/test_media.py tests/test_naming.py
git commit -m "feat: classify videos and build safe paths"
```

## Task 4: SQLite 群游标和持久优先级队列

**Files:**
- Create: `src/tg_video_downloader/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: 写 schema、去重、优先级和恢复失败测试**

创建 `tests/test_state.py`，构造同一群的历史任务和实时任务，覆盖以下断言：

```python
def test_jobs_are_deduplicated_and_live_is_claimed_first(store, history_message, live_message):
    store.upsert_job(history_message, "群", JobSource.HISTORY)
    store.upsert_job(history_message, "群", JobSource.LIVE)
    store.upsert_job(live_message, "群", JobSource.LIVE)
    assert store.job_count() == 2
    claimed = store.claim_next()
    assert claimed is not None
    assert claimed.source == JobSource.LIVE


def test_recover_inflight_and_preserve_cursors(store, history_message):
    store.reconcile_targets((GroupTarget(-1001, "群"),))
    store.set_latest_seen(-1001, 50)
    store.set_history_cursor(-1001, 20, complete=False)
    store.upsert_job(history_message, "群", JobSource.HISTORY)
    claimed = store.claim_next()
    assert claimed is not None
    assert store.recover_inflight() == ((-1001, history_message.message_id),)
    group = store.get_group(-1001)
    assert (group.latest_seen_id, group.history_cursor_id, group.history_complete) == (50, 20, False)
```

添加 fixture，数据库固定为 `tmp_path / ".runtime/state.sqlite3"`，消息 fixture 使用 `MessageInfo`。

- [ ] **Step 2: 运行测试并确认状态模块不存在**

Run: `.venv\Scripts\python.exe -m pytest tests/test_state.py -v`

Expected: FAIL importing `StateStore`.

- [ ] **Step 3: 建立明确 schema**

在 `src/tg_video_downloader/state.py` 中初始化连接时执行 `PRAGMA journal_mode=WAL`、`PRAGMA foreign_keys=ON` 和以下 schema：

```sql
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    latest_seen_id INTEGER,
    history_cursor_id INTEGER,
    history_complete INTEGER NOT NULL DEFAULT 0,
    access_error TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    group_title TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('live','catchup','history')),
    priority INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','downloading','retry_wait','completed','permanent_error')),
    message_date TEXT NOT NULL,
    mime_type TEXT,
    original_name TEXT,
    extension TEXT NOT NULL,
    expected_size INTEGER,
    is_video INTEGER NOT NULL,
    is_animated INTEGER NOT NULL,
    is_round INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    final_path TEXT,
    error TEXT,
    PRIMARY KEY(chat_id, message_id),
    FOREIGN KEY(chat_id) REFERENCES groups(chat_id)
);

CREATE INDEX IF NOT EXISTS jobs_next_idx
ON jobs(status, priority, next_attempt_at, message_date DESC);
```

- [ ] **Step 4: 实现完整状态接口**

实现 `GroupState` 数据类和以下完整 `StateStore` 公共接口：`__init__(database)`、`close()`、`reconcile_targets(targets) -> tuple[set[int], set[int]]`、`enabled_chat_ids() -> set[int]`、`group_states() -> tuple[GroupState, ...]`、`get_group(chat_id) -> GroupState`、`set_latest_seen(chat_id, message_id)`、`set_history_cursor(chat_id, message_id, complete)`、`set_access_error(chat_id, error)`、`upsert_job(message, group_title, source)`、`claim_next(now=None) -> DownloadJob | None`、`mark_completed(job, final_path)`、`mark_retry(job, error, delay_seconds)`、`mark_permanent_error(job, error)`、`recover_inflight() -> tuple[tuple[int, int], ...]`、`job_count() -> int` 和 `counts() -> dict[str, int]`。

`reconcile_targets` 原子启用传入群、更新标题并禁用缺失群；返回新增与移除 ID。`upsert_job` 使用 `INSERT ... ON CONFLICT`，已有任务不是 `completed` 时允许把来源升级到更高优先级：live=0、catchup=0、history=10。`claim_next` 用 `BEGIN IMMEDIATE` 联结 `groups` 并只领取 `enabled=1` 的到期任务，然后将其改为 `downloading`、`attempts=attempts+1`。`recover_inflight` 在更新状态前返回所有遗留任务键，供工作器精确删除对应临时文件。`counts` 必须分别返回 `pending_live`、`pending_history`、`retry_wait`、`completed` 和 `permanent_error`。所有 SQL 参数化，不拼接用户输入。

- [ ] **Step 5: 运行状态测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_state.py -v`

Expected: all tests pass.

- [ ] **Step 6: 提交持久队列**

```powershell
git add src/tg_video_downloader/state.py tests/test_state.py
git commit -m "feat: add durable priority download queue"
```

## Task 5: Telethon 网关和个人账号登录

**Files:**
- Create: `src/tg_video_downloader/gateway.py`
- Create: `tests/test_gateway.py`

- [ ] **Step 1: 写消息规范化失败测试**

创建 `tests/test_gateway.py`，用 `types.SimpleNamespace` 构造带 `DocumentAttributeFilename`、`DocumentAttributeAnimated` 和 `DocumentAttributeVideo(round_message=True)` 等价字段的伪消息，断言 `normalize_message()` 正确生成 `MessageInfo`，并测试群列表只保留 `dialog.is_group is True`。

核心断言：

```python
assert info.chat_id == -1001
assert info.message_id == 9
assert info.original_name == "movie.mp4"
assert info.mime_type == "video/mp4"
assert info.is_animated is False
assert info.is_round is False
```

- [ ] **Step 2: 运行测试并确认网关模块不存在**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gateway.py -v`

Expected: FAIL importing `normalize_message`.

- [ ] **Step 3: 定义网关协议和错误类型**

在 `src/tg_video_downloader/gateway.py` 中定义四个错误类型 `AuthenticationRequiredError`、`GroupAccessError`、`PermanentMessageError`、`TransientTelegramError`，并定义以下完整 `TelegramGateway` 协议接口：`connect()`、`disconnect()`、`is_authorized()`、`send_login_code(phone)`、`complete_login(phone, code, password)`、`list_groups()`、`set_new_message_handler(handler)`、`latest_message_id(chat_id)`、`iter_newer_messages(chat_id, min_id)`、`iter_older_messages(chat_id, offset_id)` 和 `download_message(chat_id, message_id, destination)`。异步遍历接口返回 `AsyncIterator[MessageInfo]`，下载接口返回实际 `Path`。

```python
class AuthenticationRequiredError(RuntimeError): pass
class GroupAccessError(RuntimeError): pass
class PermanentMessageError(RuntimeError): pass
class TransientTelegramError(RuntimeError): pass
```

- [ ] **Step 4: 实现 Telethon 适配器**

实现 `normalize_message(message, chat_id)` 和 `TelethonGateway`：

- `TelegramClient` 会话参数必须是 `paths.session` 的字符串路径，开启 `auto_reconnect=True`，`connection_retries=-1`，`retry_delay=5`。
- `send_login_code` 调用 `client.send_code_request(phone)`。
- `complete_login` 先调用 `client.sign_in(phone, code)`；捕获 `SessionPasswordNeededError` 后，密码非空时调用 `client.sign_in(password=password)`，否则抛出 `AuthenticationRequiredError("需要二步验证密码")`。
- `list_groups` 遍历 `client.iter_dialogs()`，只返回 `dialog.is_group` 的 `GroupTarget(int(dialog.id), dialog.name)`，按群名大小写不敏感排序。
- 新消息事件只做规范化并调用已注册 handler；白名单过滤由协调器执行。
- 新消息补抓使用 `iter_messages(chat_id, min_id=min_id, reverse=True)`。
- 历史扫描使用 `iter_messages(chat_id, offset_id=offset_id or 0)`，保持最新到最旧顺序。
- `download_message` 先用 `get_messages(chat_id, ids=message_id)` 重新获取消息，再调用 `download_media(message, file=str(destination))`；不存在消息时抛 `PermanentMessageError`。
- 把 Telegram 的认证、群访问、永久消息和临时网络异常映射到上一步定义的错误类型；`FloodWaitError` 由 Telethon 自身按 `flood_sleep_threshold` 以内等待，超过阈值时包装为包含等待秒数的 `TransientTelegramError`。

- [ ] **Step 5: 运行网关测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gateway.py -v`

Expected: all tests pass without网络访问。

- [ ] **Step 6: 提交 Telethon 网关**

```powershell
git add src/tg_video_downloader/gateway.py tests/test_gateway.py
git commit -m "feat: add Telethon account gateway"
```

## Task 6: 白名单协调器、停机补抓和历史扫描

**Files:**
- Create: `src/tg_video_downloader/coordinator.py`
- Create: `tests/fakes.py`
- Create: `tests/test_coordinator.py`

- [ ] **Step 1: 创建可控 Telegram 替身**

在 `tests/fakes.py` 创建 `FakeTelegramGateway`，保存 `messages: dict[int, list[MessageInfo]]`、`download_payloads`、`iterated_chat_ids` 和新消息 handler；所有遍历均按消息 ID 过滤并按协议要求排序，`emit(message)` 调用 handler，`download_message` 把预设 bytes 写入目标文件。该替身不得导入 Telethon。

- [ ] **Step 2: 写严格白名单、补抓和实时优先失败测试**

创建 `tests/test_coordinator.py`：

```python
@pytest.mark.asyncio
async def test_only_selected_groups_are_scanned_and_live_is_upserted(tmp_path):
    selected = GroupTarget(-1001, "选中群")
    unselected_message = make_video(-1002, 8)
    selected_history = make_video(-1001, 5)
    gateway = FakeTelegramGateway({-1001: [selected_history], -1002: [unselected_message]})
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = ScannerCoordinator(store, gateway)

    await coordinator.apply_targets((selected,))
    await coordinator.scan_once(-1001)
    await gateway.emit(make_video(-1001, 6))
    await gateway.emit(unselected_message)

    assert gateway.iterated_chat_ids == [-1001]
    assert store.job_count() == 2
    assert store.claim_next().message_id == 6
```

再添加测试：设置 `latest_seen_id=5` 后网关存在 6、7，`catch_up_once` 只入队 6、7；设置 `history_cursor_id=5` 后历史扫描只继续 4 以下；移除群后新事件被忽略。

- [ ] **Step 3: 运行测试并确认协调器不存在**

Run: `.venv\Scripts\python.exe -m pytest tests/test_coordinator.py -v`

Expected: FAIL importing `ScannerCoordinator`.

- [ ] **Step 4: 实现协调器**

创建 `src/tg_video_downloader/coordinator.py`，实现完整 `ScannerCoordinator` 公共接口：`__init__(state, gateway)`、`start(targets)`、`apply_targets(targets) -> tuple[set[int], set[int]]`、`handle_live(message)`、`catch_up_once(chat_id)`、`scan_once(chat_id, batch_size=100) -> bool` 和 `run_scans(stop)`。除构造函数外均为异步方法。

行为固定为：

- `start` 先注册 `handle_live`，再 `apply_targets`，然后依次补抓启用群。
- `handle_live` 先检查 `state.enabled_chat_ids()`；非白名单立即返回。白名单消息先用 `max` 语义更新 `latest_seen_id`，再用 `is_downloadable_video` 判定并以 `JobSource.LIVE` 入队。
- 新群若 `latest_seen_id` 为空，调用 `latest_message_id` 保存边界，但历史扫描仍从最新消息开始，依赖唯一键去重。
- `catch_up_once` 仅遍历 `min_id=latest_seen_id` 之后的消息，按 ID 递增更新游标，以 `JobSource.CATCHUP` 入队。
- `scan_once` 最多处理 `batch_size` 条消息；每条处理后更新 `history_cursor_id`，遍历结束时把 `history_complete` 设为真。
- `run_scans` 对启用且未完成历史扫描的群轮询执行一批，群之间 `await asyncio.sleep(0)` 让出事件循环；无工作时等待 1 秒或停止事件。
- 群访问错误只写入该群 `access_error`，不终止其他群扫描。

- [ ] **Step 5: 运行协调器测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_coordinator.py -v`

Expected: all tests pass.

- [ ] **Step 6: 提交扫描协调器**

```powershell
git add src/tg_video_downloader/coordinator.py tests/fakes.py tests/test_coordinator.py
git commit -m "feat: scan selected groups with durable cursors"
```

## Task 7: 下载工作器、原子落盘和错误恢复

**Files:**
- Create: `src/tg_video_downloader/worker.py`
- Create: `tests/test_worker.py`

- [ ] **Step 1: 写成功下载、中断恢复和磁盘不足失败测试**

创建 `tests/test_worker.py`，覆盖：

- 下载写入 `.tmp`，最终路径大小正确，临时文件消失，任务为 `completed`。
- 网关抛 `TransientTelegramError` 时任务进入 `retry_wait` 且不阻塞下一任务。
- 网关抛 `PermanentMessageError` 时任务进入 `permanent_error`。
- 可用空间小于 `expected_size + 512 * 1024 * 1024` 时不调用网关并返回 `disk_paused`。
- 启动恢复会把 `downloading` 改回 `pending` 并删除仅与该任务对应的 `.part` 文件。

核心成功测试调用：

```python
result = await worker.run_one()
assert result == "completed"
assert final_path.read_bytes() == payload
assert state.counts()["completed"] == 1
```

- [ ] **Step 2: 运行测试并确认工作器不存在**

Run: `.venv\Scripts\python.exe -m pytest tests/test_worker.py -v`

Expected: FAIL importing `DownloadWorker`.

- [ ] **Step 3: 实现空间守卫和工作器**

创建 `src/tg_video_downloader/worker.py`，定义常量并实现 `DiskGuard.__init__(downloads, usage=shutil.disk_usage)`、`has_space(expected_size) -> bool`，以及 `DownloadWorker.__init__(paths, state, gateway)`、`recover() -> int`、异步 `run_one() -> str`、异步 `run(stop) -> None`。群标题直接使用 `DownloadJob.group_title`：

```python
SAFETY_FREE_BYTES = 512 * 1024 * 1024
QUICK_RETRY_DELAYS = (5, 15, 30, 60, 120)
LONG_RETRY_SECONDS = 15 * 60
```

`run_one` 必须执行以下确定流程：领取任务；计算最终路径；若现有最终文件大小匹配则直接完成；检查空间；删除同任务旧 `.part`；调用 `gateway.download_message` 写 `.part`；校验大小；`os.replace(temp, final)`；标记完成。临时错误按 `attempts` 选择 `QUICK_RETRY_DELAYS`，之后使用 15 分钟；永久消息错误标记永久失败；认证错误重新抛给服务层；未领取任务返回 `idle`；空间不足返回 `disk_paused`。`run` 在 idle 时等待 1 秒，磁盘暂停时等待 60 秒，其余情况立即领取下一任务。

`recover` 调用 `state.recover_inflight()` 取得精确任务键，只清理 `.tmp` 下对应的 `chat_id_message_id.part` 文件，不递归删除其他目录。

- [ ] **Step 4: 运行工作器测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_worker.py -v`

Expected: all tests pass.

- [ ] **Step 5: 提交下载工作器**

```powershell
git add src/tg_video_downloader/worker.py tests/test_worker.py
git commit -m "feat: download videos with atomic recovery"
```

## Task 8: 可观测性、Windows 生命周期和后台服务

**Files:**
- Create: `src/tg_video_downloader/observability.py`
- Create: `src/tg_video_downloader/windows.py`
- Create: `src/tg_video_downloader/service.py`
- Create: `src/tg_video_downloader/cli.py`
- Create: `tests/test_observability.py`
- Create: `tests/test_service.py`

- [ ] **Step 1: 写日志脱敏、心跳和停止失败测试**

创建 `tests/test_observability.py`，断言 `SecretRedactionFilter(("secret-hash", "123456"))` 把日志消息中的值替换为 `***`；`HeartbeatWriter.write(snapshot)` 使用临时文件原子生成 UTF-8 JSON，并且没有残留临时文件。

创建 `tests/test_service.py`，使用假配置、假网关、内存协调器和工作器，断言：

- 配置为空时服务在连接 Telegram 前抛出包含“至少选择一个群”的错误。
- `stop.flag` 出现后服务设置停止事件并退出。
- 配置热加载新增群时调用 `apply_targets`，非法配置不替换上次有效白名单。
- `AuthenticationRequiredError` 产生 `needs_login` 心跳而不是紧密重启登录。

- [ ] **Step 2: 运行测试并确认模块不存在**

Run: `.venv\Scripts\python.exe -m pytest tests/test_observability.py tests/test_service.py -v`

Expected: FAIL importing observability or service modules.

- [ ] **Step 3: 实现日志和心跳**

在 `src/tg_video_downloader/observability.py` 实现 `SecretRedactionFilter.__init__(secrets)` 与 `filter(record) -> bool`、`configure_logging(log_dir, secrets) -> logging.Logger`，以及 `HeartbeatWriter.__init__(path)`、`write(snapshot)`、`read() -> dict[str, object]`。

日志 handler 使用 `RotatingFileHandler(maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")`。过滤器先执行 `record.getMessage()`，逐个替换非空秘密，再把 `record.msg` 设置为脱敏字符串并清空 `record.args`。心跳使用同目录 `.new` 文件、`flush`、`os.fsync` 和 `os.replace`。

- [ ] **Step 4: 实现 Windows 单实例、休眠和停止控制**

在 `src/tg_video_downloader/windows.py` 实现 `SingleInstance.__init__(lock_path)`、`__enter__()`、`__exit__()`，`PreventIdleSleep.__enter__()`、`__exit__()`，以及 `request_stop(paths)`、`clear_stop(paths)`、`is_stop_requested(paths) -> bool`、`start_hidden_supervisor(project_root) -> subprocess.Popen[bytes]`。

`SingleInstance` 在 Windows 使用 `msvcrt.locking(..., LK_NBLCK, 1)` 锁定 `.runtime/downloader.lock` 第一个字节并写当前 PID；锁失败抛 `RuntimeError("下载器已经在运行")`。`PreventIdleSleep` 调用 `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`，退出时恢复 `ES_CONTINUOUS`。`start_hidden_supervisor` 使用 `CREATE_NO_WINDOW | DETACHED_PROCESS` 启动 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-supervisor.ps1`，工作目录固定为项目根。

- [ ] **Step 5: 实现服务编排和 CLI**

在 `src/tg_video_downloader/service.py` 实现 `DownloaderService(paths, gateway_factory)` 和 `async run()`：先创建目录、校验凭据与非空白名单、配置日志、持有单实例锁、恢复下载任务、连接网关、注册协调器、启动扫描/下载/配置监视/心跳/停止标记五个 asyncio 任务。配置监视每 `config_poll_seconds` 调用 reloader；心跳每 5 秒写状态；停止监视每 1 秒检查标记。任何任务出现认证错误时写 `needs_login` 后有序断开；其他未处理异常由进程返回非零，让守护脚本重启。

在 `src/tg_video_downloader/cli.py` 实现：

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("gui", "service"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    paths = ProjectPaths.from_root(root)
    if args.command == "gui":
        from tg_video_downloader.gui.app import run_gui
        run_gui(paths)
        return 0
    return asyncio.run(DownloaderService(paths, TelethonGateway).run())
```

同时创建 `src/tg_video_downloader/__main__.py`，内容为 `raise SystemExit(main())`。

- [ ] **Step 6: 运行服务测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_observability.py tests/test_service.py -v`

Expected: all tests pass.

- [ ] **Step 7: 提交后台服务**

```powershell
git add src/tg_video_downloader/observability.py src/tg_video_downloader/windows.py src/tg_video_downloader/service.py src/tg_video_downloader/cli.py src/tg_video_downloader/__main__.py tests/test_observability.py tests/test_service.py
git commit -m "feat: orchestrate resilient Windows service"
```

## Task 9: Tkinter 图形配置器

**Files:**
- Create: `src/tg_video_downloader/gui/__init__.py`
- Create: `src/tg_video_downloader/gui/controller.py`
- Create: `src/tg_video_downloader/gui/app.py`
- Create: `tests/test_gui_controller.py`

- [ ] **Step 1: 写控制器失败测试**

创建 `tests/test_gui_controller.py`，注入假网关工厂和假进程控制，覆盖：保存凭据；发送验证码；需要二步验证时返回明确状态；群列表只来自网关；保存选择后 `config.toml` 只含勾选群；零选择拒绝保存；启动前清除 `stop.flag`；停止时创建 `stop.flag`；心跳文件不存在时返回 `stopped`。

关键断言：

```python
controller.save_selected_groups((GroupTarget(-1001, "选中群"),))
assert controller.config_store.load_config().groups == (GroupTarget(-1001, "选中群"),)

with pytest.raises(ValueError, match="至少选择一个群"):
    controller.save_selected_groups(())
```

- [ ] **Step 2: 运行测试并确认 GUI 控制器不存在**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_controller.py -v`

Expected: FAIL importing `GuiController`.

- [ ] **Step 3: 实现异步桥和 GUI 控制器**

在 `src/tg_video_downloader/gui/controller.py` 实现 `AsyncBridge`：后台 daemon 线程持有单独 asyncio event loop，`submit(coroutine)` 返回 `concurrent.futures.Future`，Tkinter 主线程通过 `after` 轮询 future，不直接运行网络调用。

实现完整 `GuiController` 接口：`__init__(paths, gateway_factory)`、`load_credentials() -> Credentials | None`、`save_credentials(credentials)`、异步 `send_code(credentials)`、异步 `complete_login(code, password) -> str`、异步 `list_groups() -> tuple[GroupTarget, ...]`、`selected_chat_ids() -> set[int]`、`save_selected_groups(groups)`、`start()`、`stop()`、`read_status() -> dict[str, object]`、`open_downloads()` 和 `open_logs()`。`gateway_factory` 的固定类型为 `Callable[[ProjectPaths, Credentials], TelegramGateway]`。

验证码和密码只保存在局部调用参数中；控制器字段不得保存二者。`open_downloads` 和 `open_logs` 先创建项目内目录，再用 `os.startfile` 打开。

- [ ] **Step 4: 实现三页 Tkinter 界面**

在 `src/tg_video_downloader/gui/app.py` 创建 `DownloaderApp(ttk.Frame)` 和 `run_gui(paths)`：

- “账号”页包含 API ID、API Hash（`show="*"`）、手机号、验证码和二步验证密码（`show="*"`），以及“发送验证码”“完成登录”按钮和状态标签。
- “群组”页包含搜索框、`ttk.Treeview` 和“保存选择”。Treeview 第一列显示 `☐`/`☑`，双击或空格切换；搜索只过滤显示，不改变已选集合；底部显示 `已选择 N 个群`；不提供全选按钮。
- “运行”页包含启动、停止、打开下载目录、打开日志目录按钮，以及最后心跳、当前文件、实时等待、历史等待、完成、重试和每群扫描状态。
- 所有异步操作通过 `AsyncBridge`；进行中禁用对应按钮，完成后在 Tk 主线程更新控件；错误使用 `messagebox.showerror`，秘密不进入错误详情。
- 状态页每 2 秒读取一次本地心跳，不产生 Telegram 请求。
- 关闭窗口只停止 GUI 自己的轮询和异步桥，不调用 `controller.stop()`。

- [ ] **Step 5: 运行 GUI 控制器测试并手工打开窗口**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_controller.py -v`

Expected: all tests pass.

Run: `.venv\Scripts\pythonw.exe -m tg_video_downloader gui`

Expected: 显示三个页面；关闭窗口后命令退出；尚未配置时不发生 Telegram 网络请求。

- [ ] **Step 6: 提交图形配置器**

```powershell
git add src/tg_video_downloader/gui tests/test_gui_controller.py
git commit -m "feat: add lightweight graphical configurator"
```

## Task 10: Windows 双击入口和守护脚本

**Files:**
- Create: `scripts/launch-gui.ps1`
- Create: `scripts/run-supervisor.ps1`
- Create: `scripts/check.ps1`
- Create: `打开配置器.cmd`
- Create: `tests/test_windows_scripts.py`

- [ ] **Step 1: 写脚本隔离失败测试**

创建 `tests/test_windows_scripts.py`，读取三个 PowerShell 脚本文本并断言都设置 `TEMP`、`TMP`、`PIP_CACHE_DIR`、`PYTHONPYCACHEPREFIX`，虚拟环境路径包含 `.venv`，守护脚本包含 `stop.flag` 和 `-WindowStyle Hidden`，且脚本不包含 `schtasks`、用户 Startup 路径或硬编码 C 盘路径。

- [ ] **Step 2: 运行测试并确认脚本不存在**

Run: `.venv\Scripts\python.exe -m pytest tests/test_windows_scripts.py -v`

Expected: FAIL because launch and supervisor scripts do not exist.

- [ ] **Step 3: 创建统一环境设置的 GUI 启动脚本**

创建 `scripts/launch-gui.ps1`，解析项目根后设置四个项目内环境变量；若 `.venv\Scripts\pythonw.exe` 不存在，同步调用 `bootstrap.ps1`；最后用 `Start-Process -WindowStyle Hidden -FilePath .venv\Scripts\pythonw.exe -ArgumentList "-m", "tg_video_downloader", "gui" -WorkingDirectory $ProjectRoot`。所有路径通过 `Join-Path` 和 `-LiteralPath` 处理。

创建 `打开配置器.cmd`：

```batch
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-gui.ps1"
```

- [ ] **Step 4: 创建异常重启守护脚本**

创建 `scripts/run-supervisor.ps1`：设置项目内环境；以独占方式创建 `.runtime\supervisor.pid` 并写 `$PID`；循环启动 `.venv\Scripts\python.exe -m tg_video_downloader service`，使用 `Start-Process -WindowStyle Hidden -Wait -PassThru`；若存在 `.runtime\stop.flag` 则退出；否则按 5、10、20、40、80、160、300 秒上限退避，服务连续运行超过 10 分钟后把退避重置为 5 秒。`finally` 只删除当前守护进程创建且内容等于 `$PID` 的 PID 文件。

- [ ] **Step 5: 创建统一验证脚本**

创建 `scripts/check.ps1`：设置相同四个环境变量，依次运行项目 Python 的 `-m pytest -q`、`-m compileall -q src`，再检查 `.runtime`、`.cache`、`.tmp`、`logs`、`downloads` 均位于项目根；任一子命令失败时 `exit 1`。

- [ ] **Step 6: 运行脚本测试和验证脚本**

Run: `.venv\Scripts\python.exe -m pytest tests/test_windows_scripts.py -v`

Expected: all tests pass.

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`

Expected: test suite passes and compileall exits 0；新缓存只出现在 `.cache`。

- [ ] **Step 7: 提交 Windows 入口**

```powershell
git add scripts/launch-gui.ps1 scripts/run-supervisor.ps1 scripts/check.ps1 "打开配置器.cmd" tests/test_windows_scripts.py
git commit -m "feat: add project-local Windows launchers"
```

## Task 11: 端到端故障注入、文档和最终验收

**Files:**
- Create: `tests/test_service_integration.py`
- Create: `README.md`
- Modify: `tests/fakes.py`

- [ ] **Step 1: 写端到端集成失败测试**

创建 `tests/test_service_integration.py`，使用 `FakeTelegramGateway`、真实临时 SQLite 和真实文件系统，按以下场景运行协调器与工作器：

1. 白名单只有群 A，网关同时含群 A、群 B；断言只遍历并下载 A。
2. A 有三个历史视频，扫描一个批次后模拟重启；断言游标继续且最终只有三个完成任务。
3. 历史任务等待时发送实时视频；断言当前任务之后先下载实时视频。
4. 下载第一次抛临时错误，重试时间到达后成功；断言没有错误最终文件和残留 `.part`。
5. 修改配置增加群 B、移除群 A；断言 B 开始历史扫描、A 后续事件被忽略、A 已有文件保留。
6. 所有创建的路径均 `is_relative_to(project_root)`。

- [ ] **Step 2: 运行端到端测试并确认暴露的集成缺口**

Run: `.venv\Scripts\python.exe -m pytest tests/test_service_integration.py -v`

Expected: all integration scenarios pass；若存在失败，只修改对应组件实现，不降低白名单、优先级、恢复或路径隔离断言。

- [ ] **Step 3: 修复集成边界并运行全套测试**

按失败位置修正 `coordinator.py`、`state.py`、`worker.py` 或 `tests/fakes.py`，保持已定义公共方法名不变。每次只修一个失败，然后运行：

Run: `.venv\Scripts\python.exe -m pytest tests/test_service_integration.py -v`

Expected: all integration scenarios pass.

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: full suite passes.

- [ ] **Step 4: 写用户文档**

创建 `README.md`，包含以下明确章节和命令：

- 前提：Windows、Python 3.11+、有权访问目标群、从 `https://my.telegram.org` 获取 API ID/API Hash。
- 首次使用：双击 `打开配置器.cmd`，填写账号信息，发送验证码，必要时输入二步验证密码，勾选群并保存，点击启动。
- 日常使用：关闭配置器不停止下载；重新打开可查看状态、调整白名单、停止或再次启动。
- 文件位置：完整列出 `.runtime`、`downloads`、`logs`、`.tmp`、`.cache`、`.venv`。
- 运行限制：电脑必须开机联网；程序阻止空闲休眠但不阻止主动关机；重启后需手动点击启动；不创建系统启动项。
- 下载范围：普通视频与 `video/*` 文件；排除 GIF、圆形视频和非视频；严格只下载勾选群。
- 恢复语义：已完成任务和历史游标续接；单个残缺文件从头下载。
- 故障处理：需要重新登录、群无权限、磁盘不足、查看日志。
- 隐私：验证码和二步验证密码不保存；会话文件等同登录权限；不要分享 `.runtime`。
- 验证命令：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`。

- [ ] **Step 5: 执行静态、自测和仓库检查**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`

Expected: all tests pass, compileall exits 0.

Run: `git status --short`

Expected: 仅显示本任务尚未提交的 `README.md`、集成测试和必要修复。

- [ ] **Step 6: 提交端到端实现和文档**

```powershell
git add README.md tests/test_service_integration.py tests/fakes.py src/tg_video_downloader
git commit -m "test: verify downloader end to end"
```

- [ ] **Step 7: 进行真实账号手工验收**

Run: 双击 `打开配置器.cmd`。

Expected:

- 窗口显示账号、群组、运行三页。
- 用户完成登录后能搜索并勾选群，零选择无法启动。
- 选择一个测试群后，仅该群历史视频出现在 `downloads`。
- 在群中发送新普通视频，当前文件结束后它优先下载。
- 点击停止、重新启动后，已完成文件不重复下载，未完成历史继续。
- 任务管理器中关闭 GUI 后后台仍运行；同步空闲时记录工作集并确认不高于 150 MiB 目标。
- 项目根之外没有本项目主动创建的依赖、缓存、会话、日志或下载数据。

- [ ] **Step 8: 提交手工验收记录**

把不含手机号、群名、群 ID、消息内容和凭据的验收结果写入 `docs/verification.md`，记录测试时间、通过项、资源占用数值和无法自动化的账号步骤，然后：

```powershell
git add docs/verification.md
git commit -m "docs: record Windows acceptance verification"
```

## 计划自检映射

- 严格群组白名单：Tasks 2、4、6、9、11。
- 全量历史、停机补抓、实时优先和游标恢复：Tasks 4、6、7、11。
- 普通视频与 `video/*`、排除 GIF/圆形视频：Tasks 3、5、11。
- 图形登录、搜索勾选、启动停止和状态：Tasks 8、9、10。
- 单工作器、原子落盘、磁盘保护和重试：Task 7。
- 日志脱敏、心跳、单实例、休眠和异常重启：Tasks 8、10。
- D 盘项目内隔离：Tasks 1、3、7、10、11。
- 文档、真实账号联调和 150 MiB 目标：Task 11。
