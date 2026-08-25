# Telegram QR Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Tkinter 配置器中以内嵌、自动刷新的二维码作为 Telegram 默认登录方式，同时保留手机号验证码备用登录，并保证所有持久数据仍只写入项目目录。

**Architecture:** 保留 Telethon 会话和现有单账号网关，在领域层增加二维码挑战及可映射异常，在 `GuiController` 中管理唯一临时登录连接，在 GUI 中通过现有 `AsyncBridge` 等待扫码并用登录代次隔离迟到回调。二维码 URL 由纯 Python `qrcode` 编码为矩阵后直接绘制到 Tkinter `Canvas`，不生成图片或系统临时文件。

**Tech Stack:** Python 3.11、Telethon 1.44、Tkinter/ttk、qrcode 8.x（不安装 Pillow 扩展）、pytest、pytest-asyncio、PowerShell 项目内验证脚本。

---

## 文件结构

- Modify: `pyproject.toml` — 声明不带图像扩展的二维码编码依赖。
- Modify: `src/tg_video_downloader/models.py` — 允许空手机号并拆分 API 与手机登录校验。
- Modify: `src/tg_video_downloader/config.py` — 兼容缺失或为空的 `phone` 字段。
- Modify: `src/tg_video_downloader/gateway.py` — 提供二维码挑战、刷新、等待、二步验证和注销接口。
- Modify: `src/tg_video_downloader/gui/controller.py` — 管理二维码与备用登录的单一生命周期。
- Create: `src/tg_video_downloader/gui/qr_view.py` — 二维码矩阵生成、Canvas 绘制、重试间隔计算。
- Modify: `src/tg_video_downloader/gui/app.py` — 构建内嵌二维码、折叠备用区和异步状态切换。
- Modify: `src/tg_video_downloader/diagnostics.py` — 自检二维码依赖、空手机号和活动登录状态。
- Modify: `tests/fakes.py` — 让通用 Telegram 假网关满足扩展后的协议。
- Modify: `tests/test_config.py` — 覆盖空手机号及旧配置兼容。
- Modify: `tests/test_gateway.py` — 覆盖 Telethon 二维码和二步验证映射。
- Modify: `tests/test_gui_controller.py` — 覆盖会话复用、二维码生命周期、备用登录和注销。
- Create: `tests/test_qr_view.py` — 覆盖无文件二维码渲染和重试策略。
- Create: `tests/test_gui_app.py` — 用无 Tk 窗口的探针覆盖备用区切换和敏感错误脱敏。
- Modify: `tests/test_diagnostics.py` — 覆盖二维码组件、空敏感值和登录状态检查。
- Modify: `README.md` — 更新首次登录、故障处理、数据位置和安全说明。
- Modify: `docs/verification.md` — 记录二维码专项与完整回归验证证据。

## Task 1: 让手机号成为二维码登录的可选凭据

**Files:**
- Modify: `src/tg_video_downloader/models.py:43`
- Modify: `src/tg_video_downloader/config.py:79`
- Modify: `src/tg_video_downloader/gui/controller.py:112`
- Test: `tests/test_config.py`
- Test: `tests/test_gui_controller.py`

- [ ] **Step 1: 写入失败测试，锁定 API 校验和手机号校验边界**

在 `tests/test_config.py` 添加：

```python
def test_qr_credentials_allow_empty_phone_but_phone_login_rejects_it() -> None:
    credentials = Credentials(api_id=12345, api_hash="secret-hash")

    assert credentials.validate_api() is credentials
    assert credentials.validate() is credentials
    with pytest.raises(ValueError, match="手机号"):
        credentials.validate_phone_login()


def test_load_credentials_defaults_missing_phone_to_empty(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    paths.credentials.write_text(
        'api_id = 12345\napi_hash = "secret-hash"\n',
        encoding="utf-8",
    )

    assert ConfigStore(paths).load_credentials() == Credentials(12345, "secret-hash")
```

在 `tests/test_gui_controller.py` 添加：

```python
@pytest.mark.asyncio
async def test_phone_login_still_requires_phone(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)

    with pytest.raises(ValueError, match="手机号"):
        await controller.send_code(Credentials(12345, "secret-hash"))

    assert gateway.connected is False
    assert gateway.sent_phone is None
```

- [ ] **Step 2: 运行测试并确认因新接口或默认值不存在而失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_gui_controller.py -q
```

Expected: FAIL，包含 `Credentials.__init__()` 缺少 `phone` 或 `validate_api` 不存在。

- [ ] **Step 3: 实现最小凭据和配置改动**

将 `Credentials` 改为：

```python
@dataclass(frozen=True)
class Credentials:
    api_id: int
    api_hash: str
    phone: str = ""

    def validate_api(self) -> "Credentials":
        if self.api_id <= 0 or not self.api_hash.strip():
            raise ValueError("API ID 和 API Hash 均不能为空")
        return self

    def validate_phone_login(self) -> "Credentials":
        self.validate_api()
        if not self.phone.strip():
            raise ValueError("手机号不能为空")
        return self

    def validate(self) -> "Credentials":
        return self.validate_api()
```

在 `ConfigStore.load_credentials` 使用 `phone=str(data.get("phone", ""))`，并让加载和保存调用 `validate_api()`。在 `GuiController.send_code` 的第一行调用 `credentials.validate_phone_login()`；其他网关创建、后台服务和自检继续使用 API 基础校验。

- [ ] **Step 4: 运行凭据、控制器和服务回归测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_gui_controller.py tests/test_service.py -q
```

Expected: PASS，且现有手机号登录测试不变。

- [ ] **Step 5: 提交可选手机号改动**

```powershell
git add -- src/tg_video_downloader/models.py src/tg_video_downloader/config.py src/tg_video_downloader/gui/controller.py tests/test_config.py tests/test_gui_controller.py
git commit -m "feat: allow QR credentials without phone"
```

## Task 2: 在 Telegram 网关中实现二维码授权原语

**Files:**
- Modify: `src/tg_video_downloader/gateway.py:19`
- Modify: `tests/fakes.py:8`
- Test: `tests/test_gateway.py`

- [ ] **Step 1: 写入二维码创建、刷新、过期和二步验证失败测试**

在 `tests/test_gateway.py` 添加一个可控 QR 对象和测试客户端：

```python
class FakeQrLogin:
    def __init__(self) -> None:
        self.url = "tg://login?token=first"
        self.expires = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
        self.wait_error: Exception | None = None

    async def recreate(self) -> None:
        self.url = "tg://login?token=second"

    async def wait(self) -> None:
        if self.wait_error is not None:
            raise self.wait_error


@pytest.mark.asyncio
async def test_qr_login_create_refresh_and_wait(tmp_path: Path) -> None:
    qr = FakeQrLogin()

    class QrClient:
        async def qr_login(self) -> FakeQrLogin:
            return qr

    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(12345, "hash"),
        client_factory=lambda *args, **kwargs: QrClient(),
    )

    first = await gateway.start_qr_login()
    second = await gateway.refresh_qr_login()
    await gateway.wait_qr_login()

    assert first.url.endswith("first")
    assert first.expires_at == qr.expires
    assert second.url.endswith("second")


@pytest.mark.asyncio
async def test_qr_wait_maps_expiry_and_two_step_password(tmp_path: Path) -> None:
    qr = FakeQrLogin()

    class QrClient:
        def __init__(self) -> None:
            self.passwords: list[str] = []

        async def qr_login(self) -> FakeQrLogin:
            return qr

        async def sign_in(self, *, password: str) -> None:
            self.passwords.append(password)

    client = QrClient()
    gateway = TelethonGateway(
        ProjectPaths.from_root(tmp_path),
        Credentials(12345, "hash"),
        client_factory=lambda *args, **kwargs: client,
    )
    await gateway.start_qr_login()

    qr.wait_error = TimeoutError()
    with pytest.raises(QrLoginExpiredError):
        await gateway.wait_qr_login()

    qr.wait_error = errors.SessionPasswordNeededError(request=None)
    with pytest.raises(AuthenticationRequiredError, match="二步验证密码"):
        await gateway.wait_qr_login()
    await gateway.complete_password("two-factor")

    assert client.passwords == ["two-factor"]


def test_qr_login_error_mapping_preserves_user_action_and_retry_time() -> None:
    invalid_api = _mapped_error(errors.ApiIdInvalidError(request=None))
    flood_wait = _mapped_error(errors.FloodWaitError(request=None, capture=73))

    assert isinstance(invalid_api, InvalidApiCredentialsError)
    assert "API ID" in str(invalid_api)
    assert isinstance(flood_wait, TransientTelegramError)
    assert flood_wait.retry_after == 73
```

把 `_mapped_error`、`InvalidApiCredentialsError`、`QrLoginExpiredError` 和 `TransientTelegramError` 加入该测试文件的网关导入列表。

- [ ] **Step 2: 运行网关专项测试并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gateway.py -q
```

Expected: FAIL，`QrLoginChallenge`、`QrLoginExpiredError` 或二维码方法尚未定义。

- [ ] **Step 3: 添加领域类型和协议方法**

在 `gateway.py` 定义并加入 `TelegramGateway`：

```python
@dataclass(frozen=True)
class QrLoginChallenge:
    url: str
    expires_at: datetime


class QrLoginExpiredError(RuntimeError):
    pass


class InvalidApiCredentialsError(ValueError):
    pass


class TransientTelegramError(RuntimeError):
    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TelegramGateway(Protocol):
    async def start_qr_login(self) -> QrLoginChallenge: ...
    async def refresh_qr_login(self) -> QrLoginChallenge: ...
    async def wait_qr_login(self) -> None: ...
    async def complete_password(self, password: str) -> None: ...
    async def log_out(self) -> None: ...
```

保留协议中已有连接、手机号登录、群组和下载方法；只在相应位置插入以上五个方法。

- [ ] **Step 4: 用 Telethon `qr_login()` 实现网关方法**

在 `TelethonGateway.__init__` 增加 `self._qr_login: Any | None = None`，并实现：

```python
async def start_qr_login(self) -> QrLoginChallenge:
    try:
        self._qr_login = await self._client.qr_login()
        return self._qr_challenge()
    except Exception as error:
        raise _mapped_error(error) from error

async def refresh_qr_login(self) -> QrLoginChallenge:
    if self._qr_login is None:
        raise ValueError("请先开始二维码登录")
    try:
        await self._qr_login.recreate()
        return self._qr_challenge()
    except Exception as error:
        raise _mapped_error(error) from error

async def wait_qr_login(self) -> None:
    if self._qr_login is None:
        raise ValueError("请先开始二维码登录")
    try:
        await self._qr_login.wait()
    except TimeoutError as error:
        raise QrLoginExpiredError("二维码已过期") from error
    except errors.SessionPasswordNeededError as error:
        self._password_required = True
        raise AuthenticationRequiredError("需要二步验证密码") from error
    except Exception as error:
        raise _mapped_error(error) from error

async def complete_password(self, password: str) -> None:
    if not password:
        raise AuthenticationRequiredError("需要二步验证密码")
    try:
        await self._client.sign_in(password=password)
    except Exception as error:
        raise _mapped_error(error) from error
    self._password_required = False

async def log_out(self) -> None:
    try:
        await self._client.log_out()
    except Exception as error:
        raise _mapped_error(error) from error

def _qr_challenge(self) -> QrLoginChallenge:
    return QrLoginChallenge(
        url=str(self._qr_login.url),
        expires_at=self._qr_login.expires,
    )
```

让 `complete_login` 的密码分支调用 `await self.complete_password(password or "")`，并把 `disconnect` 改为：

```python
async def disconnect(self) -> None:
    try:
        await self._client.disconnect()
    except Exception as error:
        raise _mapped_error(error) from error
    finally:
        self._qr_login = None
        self._password_required = False
```

把 `_mapped_error` 的函数签名改为 `def _mapped_error(error: Exception) -> Exception:`，并在该函数第一条现有类型判断之前插入：

```python
if isinstance(error, errors.ApiIdInvalidError):
    return InvalidApiCredentialsError("API ID 或 API Hash 无效")
if isinstance(error, errors.PasswordHashInvalidError):
    return AuthenticationRequiredError("二步验证密码错误")
if isinstance(error, errors.FloodWaitError):
    seconds = int(getattr(error, "seconds", 0))
    return TransientTelegramError(
        f"Telegram 要求等待 {seconds} 秒",
        retry_after=seconds,
    )
```

删除后面原有的 `FloodWaitError` 分支，确保限流只映射一次并保留 `retry_after`。

- [ ] **Step 5: 扩展通用假网关并运行网关回归**

在 `tests/fakes.py` 增加不写文件的协议方法：

```python
async def start_qr_login(self):
    raise RuntimeError("fake QR login is not configured")

async def refresh_qr_login(self):
    raise RuntimeError("fake QR login is not configured")

async def wait_qr_login(self) -> None:
    return None

async def complete_password(self, password: str) -> None:
    self.authorized = True

async def log_out(self) -> None:
    self.authorized = False
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gateway.py tests/test_service.py tests/test_diagnostics.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交网关二维码能力**

```powershell
git add -- src/tg_video_downloader/gateway.py tests/fakes.py tests/test_gateway.py
git commit -m "feat: add Telegram QR login gateway"
```

## Task 3: 在控制器中管理唯一登录生命周期

**Files:**
- Modify: `src/tg_video_downloader/gui/controller.py:94`
- Test: `tests/test_gui_controller.py`

- [ ] **Step 1: 扩展测试假网关并写会话复用与二维码状态测试**

先把 `QrLoginChallenge` 加入测试文件导入，并把 `LoginGateway` 的授权相关部分改为：

```python
class LoginGateway:
    def __init__(self) -> None:
        self.connected = False
        self.authorized = True
        self.sent_phone = None
        self.login_calls = []
        self.groups = (GroupTarget(-1001, "A 群"), GroupTarget(-1002, "B 群"))
        self.challenge = QrLoginChallenge(
            "tg://login?token=first",
            datetime(2026, 8, 25, 1, 0, tzinfo=UTC),
        )
        self.refreshed_challenge = QrLoginChallenge(
            "tg://login?token=second",
            datetime(2026, 8, 25, 1, 1, tzinfo=UTC),
        )
        self.password_required = False
        self.passwords: list[str] = []
        self.logged_out = False

    async def is_authorized(self) -> bool:
        return self.authorized

    async def start_qr_login(self) -> QrLoginChallenge:
        return self.challenge

    async def refresh_qr_login(self) -> QrLoginChallenge:
        return self.refreshed_challenge

    async def wait_qr_login(self) -> None:
        if self.password_required:
            raise AuthenticationRequiredError("需要二步验证密码")
        self.authorized = True

    async def complete_password(self, password: str) -> None:
        self.passwords.append(password)
        self.authorized = True

    async def log_out(self) -> None:
        self.logged_out = True
        self.authorized = False
```

保留该假网关原有 `connect`、`disconnect`、手机号登录和 `list_groups` 方法，然后添加测试：

```python
@pytest.mark.asyncio
async def test_qr_login_reuses_authorized_session(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    gateway.authorized = True

    challenge = await controller.start_qr_login(Credentials(12345, "hash"))

    assert challenge is None
    assert gateway.connected is False
    assert controller.login_active is False


@pytest.mark.asyncio
async def test_qr_login_refresh_password_and_cleanup(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    gateway.authorized = False

    first = await controller.start_qr_login(Credentials(12345, "hash"))
    refreshed = await controller.refresh_qr_login()
    gateway.password_required = True

    assert first == gateway.challenge
    assert refreshed == gateway.refreshed_challenge
    assert await controller.wait_qr_login() == "需要二步验证密码"
    assert await controller.complete_qr_password("two-factor") == "登录成功"
    assert gateway.passwords == ["two-factor"]
    assert gateway.connected is False
    assert controller.login_active is False


@pytest.mark.asyncio
async def test_cancel_and_logout_release_login_gateway(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    gateway.authorized = False
    await controller.start_qr_login(Credentials(12345, "hash"))
    await controller.cancel_login()

    gateway.authorized = True
    await controller.log_out()

    assert gateway.logged_out is True
    assert gateway.connected is False
    assert controller.login_active is False
```

- [ ] **Step 2: 运行控制器测试并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gui_controller.py -q
```

Expected: FAIL，控制器二维码方法和 `login_active` 尚未定义。

- [ ] **Step 3: 实现二维码启动、等待、刷新、密码和取消**

在 `GuiController` 增加：

```python
@property
def login_active(self) -> bool:
    return self._login_gateway is not None

async def start_qr_login(
    self,
    credentials: Credentials,
) -> QrLoginChallenge | None:
    credentials.validate_api()
    self.save_credentials(credentials)
    await self.cancel_login()
    gateway = self.gateway_factory(self.paths, credentials)
    try:
        await gateway.connect()
        if await gateway.is_authorized():
            await gateway.disconnect()
            return None
        challenge = await gateway.start_qr_login()
    except Exception:
        await gateway.disconnect()
        raise
    self._login_gateway = gateway
    self._login_credentials = credentials
    return challenge

async def refresh_qr_login(self) -> QrLoginChallenge:
    if self._login_gateway is None:
        raise ValueError("请先开始二维码登录")
    return await self._login_gateway.refresh_qr_login()

async def wait_qr_login(self) -> str:
    if self._login_gateway is None:
        raise ValueError("请先开始二维码登录")
    try:
        await self._login_gateway.wait_qr_login()
    except AuthenticationRequiredError as error:
        if "二步验证" in str(error):
            return "需要二步验证密码"
        raise
    await self._clear_login()
    return "登录成功"

async def complete_qr_password(self, password: str) -> str:
    if self._login_gateway is None:
        raise ValueError("请先扫码登录")
    await self._login_gateway.complete_password(password)
    await self._clear_login()
    return "登录成功"

async def cancel_login(self) -> None:
    await self._clear_login()

async def _clear_login(self) -> None:
    gateway = self._login_gateway
    self._login_gateway = None
    self._login_credentials = None
    if gateway is not None:
        await gateway.disconnect()
```

保留 `QrLoginExpiredError` 和 `TransientTelegramError` 原样向 GUI 传播，以便 GUI 分别执行静默刷新与延迟重试。把 `send_code` 改为先 `await self.cancel_login()`，再创建手机号登录网关。

- [ ] **Step 4: 实现项目内会话注销**

添加：

```python
async def log_out(self) -> str:
    await self.cancel_login()
    credentials = self.load_credentials()
    if credentials is None:
        raise ValueError("尚未保存账号信息")
    gateway = self.gateway_factory(self.paths, credentials)
    try:
        await gateway.connect()
        if await gateway.is_authorized():
            await gateway.log_out()
    finally:
        await gateway.disconnect()
    return "已退出当前账号"
```

注销只调用 Telegram 注销并让 Telethon 更新项目内 `.runtime/telegram.session`；不删除配置、白名单、下载或日志。

- [ ] **Step 5: 运行控制器、服务和群组回归测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gui_controller.py tests/test_service.py tests/test_coordinator.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交控制器生命周期**

```powershell
git add -- src/tg_video_downloader/gui/controller.py tests/test_gui_controller.py
git commit -m "feat: manage QR login lifecycle"
```

## Task 4: 添加不落盘的二维码矩阵与 Canvas 渲染

**Files:**
- Modify: `pyproject.toml:11`
- Create: `src/tg_video_downloader/gui/qr_view.py`
- Create: `tests/test_qr_view.py`

- [ ] **Step 1: 写入二维码矩阵、Canvas 和退避失败测试**

创建 `tests/test_qr_view.py`：

```python
from datetime import UTC, datetime
from pathlib import Path

from tg_video_downloader.gui.qr_view import (
    draw_qr,
    make_qr_matrix,
    retry_delay,
    seconds_until_expiry,
)


class FakeCanvas:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.configured: dict[str, int] = {}
        self.rectangles: list[tuple[tuple[int, int, int, int], dict[str, str]]] = []

    def delete(self, tag: str) -> None:
        self.deleted.append(tag)

    def configure(self, **values: int) -> None:
        self.configured.update(values)

    def create_rectangle(self, *coords: int, **options: str) -> None:
        self.rectangles.append((coords, options))


def test_qr_matrix_and_canvas_render_without_files(tmp_path: Path) -> None:
    before = set(tmp_path.rglob("*"))
    matrix = make_qr_matrix("tg://login?token=secret-token")
    canvas = FakeCanvas()

    draw_qr(canvas, matrix, max_pixels=260)

    assert len(matrix) == len(matrix[0])
    assert any(any(row) for row in matrix)
    assert canvas.deleted == ["all"]
    assert canvas.rectangles
    assert set(tmp_path.rglob("*")) == before


def test_retry_delay_caps_network_errors_and_honors_flood_wait() -> None:
    assert [retry_delay(attempt) for attempt in range(5)] == [2, 5, 10, 20, 30]
    assert retry_delay(9) == 30
    assert retry_delay(0, retry_after=73) == 73


def test_seconds_until_expiry_normalizes_naive_utc() -> None:
    now = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)

    assert seconds_until_expiry(datetime(2026, 8, 25, 1, 0, 5), now=now) == 5
```

- [ ] **Step 2: 运行测试并确认模块或依赖缺失**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_qr_view.py -q
```

Expected: FAIL，`tg_video_downloader.gui.qr_view` 尚不存在。

- [ ] **Step 3: 声明依赖并在项目内虚拟环境安装**

在 `pyproject.toml` 的运行依赖中加入：

```toml
  "qrcode>=8,<9",
```

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
```

Expected: PASS；pip 缓存位于项目 `.cache/pip`，依赖位于项目 `.venv`。

- [ ] **Step 4: 实现纯矩阵编码、绘制和重试策略**

创建 `src/tg_video_downloader/gui/qr_view.py`：

```python
from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from typing import Any

import qrcode
from qrcode.constants import ERROR_CORRECT_M


QrMatrix = tuple[tuple[bool, ...], ...]


def make_qr_matrix(payload: str) -> QrMatrix:
    code = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    code.add_data(payload)
    code.make(fit=True)
    return tuple(tuple(bool(cell) for cell in row) for row in code.get_matrix())


def draw_qr(canvas: Any, matrix: QrMatrix, *, max_pixels: int = 260) -> None:
    modules = len(matrix)
    if modules == 0 or any(len(row) != modules for row in matrix):
        raise ValueError("二维码矩阵必须为非空正方形")
    cell = max(1, max_pixels // modules)
    size = cell * modules
    canvas.delete("all")
    canvas.configure(width=size, height=size)
    canvas.create_rectangle(0, 0, size, size, fill="white", outline="")
    for row_index, row in enumerate(matrix):
        for column_index, dark in enumerate(row):
            if dark:
                x1 = column_index * cell
                y1 = row_index * cell
                canvas.create_rectangle(
                    x1,
                    y1,
                    x1 + cell,
                    y1 + cell,
                    fill="black",
                    outline="",
                )


def retry_delay(attempt: int, *, retry_after: int | None = None) -> int:
    if retry_after is not None:
        return max(1, retry_after)
    return (2, 5, 10, 20, 30)[min(max(attempt, 0), 4)]


def seconds_until_expiry(
    expires_at: datetime,
    *,
    now: datetime | None = None,
) -> int:
    expires = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0, ceil((expires.astimezone(UTC) - current.astimezone(UTC)).total_seconds()))
```

- [ ] **Step 5: 运行二维码测试并确认没有 Pillow 或图片文件依赖**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_qr_view.py -q
.\.venv\Scripts\python.exe -c "from tg_video_downloader.gui.qr_view import make_qr_matrix; assert make_qr_matrix('tg://login?token=test')"
```

Expected: PASS；代码路径只调用 `qrcode.QRCode.get_matrix()`。

- [ ] **Step 6: 提交二维码渲染模块**

```powershell
git add -- pyproject.toml src/tg_video_downloader/gui/qr_view.py tests/test_qr_view.py
git commit -m "feat: render QR codes in memory"
```

## Task 5: 重构账号页为二维码优先界面

**Files:**
- Modify: `src/tg_video_downloader/gui/app.py:28`
- Create: `tests/test_gui_app.py`

- [ ] **Step 1: 写无窗口探针测试，锁定备用区和错误脱敏**

创建 `tests/test_gui_app.py`：

```python
from tg_video_downloader.gui.app import DownloaderApp


class FakeFrame:
    def __init__(self) -> None:
        self.visible = False

    def grid(self) -> None:
        self.visible = True

    def grid_remove(self) -> None:
        self.visible = False


class FakeButton:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **values: str) -> None:
        self.text = values["text"]


class FakeVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


def test_phone_login_panel_toggles_without_tk_window() -> None:
    app = object.__new__(DownloaderApp)
    app.phone_login_visible = False
    app.phone_login_frame = FakeFrame()
    app.phone_toggle_button = FakeButton()

    app._toggle_phone_login()
    assert app.phone_login_frame.visible is True
    assert app.phone_toggle_button.text == "收起手机号验证码登录"

    app._toggle_phone_login()
    assert app.phone_login_frame.visible is False
    assert app.phone_toggle_button.text == "使用手机号验证码登录"


def test_safe_error_ignores_empty_phone_and_redacts_qr_sensitive_fields() -> None:
    app = object.__new__(DownloaderApp)
    app.api_hash_var = FakeVar("secret-hash")
    app.phone_var = FakeVar("")
    app.code_var = FakeVar("123456")
    app.password_var = FakeVar("two-factor")

    message = app._safe_error(RuntimeError("secret-hash 123456 two-factor remains"))

    assert message == "*** *** *** remains"
```

- [ ] **Step 2: 运行 GUI 辅助测试并确认切换方法缺失**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gui_app.py -q
```

Expected: FAIL，`_toggle_phone_login` 尚未定义。

- [ ] **Step 3: 构建账号页的五个区域**

在 `_build_account_page` 中只把 API ID/API Hash 放在顶层，新增以下实例成员：

```python
self.account_status_var = tk.StringVar(value="尚未登录")
self.qr_countdown_var = tk.StringVar(value="")
self.phone_login_visible = False
self.qr_canvas = tk.Canvas(page, width=260, height=260, highlightthickness=0)
self.qr_canvas.grid(row=4, column=0, columnspan=2, pady=12)

self.qr_actions = ttk.Frame(page)
self.qr_actions.grid(row=5, column=0, columnspan=2, sticky="w")
self.qr_login_button = ttk.Button(
    self.qr_actions,
    text="扫码登录",
    command=self._start_qr_login,
)
self.qr_login_button.pack(side="left", padx=(0, 8))
self.qr_refresh_button = ttk.Button(
    self.qr_actions,
    text="重新生成",
    command=self._manual_refresh_qr,
)
self.qr_refresh_button.pack(side="left", padx=(0, 8))
self.qr_cancel_button = ttk.Button(
    self.qr_actions,
    text="取消登录",
    command=self._cancel_qr_login,
)
self.qr_cancel_button.pack(side="left")
```

创建扫码二步验证和手机号备用 Frame：

```python
self.qr_password_var = tk.StringVar()
self.qr_password_frame = ttk.Frame(page)
self.qr_password_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
self.qr_password_frame.columnconfigure(1, weight=1)
ttk.Label(self.qr_password_frame, text="二步验证密码").grid(
    row=0,
    column=0,
    padx=(0, 12),
)
ttk.Entry(
    self.qr_password_frame,
    textvariable=self.qr_password_var,
    show="*",
).grid(row=0, column=1, sticky="ew")
self.qr_password_button = ttk.Button(
    self.qr_password_frame,
    text="提交密码",
    command=self._complete_qr_password,
)
self.qr_password_button.grid(row=0, column=2, padx=(8, 0))
self.qr_password_frame.grid_remove()

self.phone_toggle_button = ttk.Button(
    page,
    text="使用手机号验证码登录",
    command=self._toggle_phone_login,
)
self.phone_toggle_button.grid(row=7, column=0, columnspan=2, sticky="w", pady=(18, 0))
self.phone_login_frame = ttk.Frame(page)
self.phone_login_frame.grid(row=8, column=0, columnspan=2, sticky="ew")
self.phone_login_frame.columnconfigure(1, weight=1)
for row, (label, variable, mask) in enumerate(
    (
        ("手机号", self.phone_var, ""),
        ("验证码", self.code_var, ""),
        ("二步验证密码", self.password_var, "*"),
    )
):
    ttk.Label(self.phone_login_frame, text=label).grid(
        row=row,
        column=0,
        sticky="w",
        padx=(0, 12),
        pady=7,
    )
    ttk.Entry(
        self.phone_login_frame,
        textvariable=variable,
        show=mask,
    ).grid(row=row, column=1, sticky="ew", pady=7)
phone_actions = ttk.Frame(self.phone_login_frame)
phone_actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
self.send_code_button = ttk.Button(
    phone_actions,
    text="发送验证码",
    command=self._send_code,
)
self.send_code_button.pack(side="left", padx=(0, 8))
self.login_button = ttk.Button(
    phone_actions,
    text="完成登录",
    command=self._complete_login,
)
self.login_button.pack(side="left")
self.phone_login_frame.grid_remove()
```

顶层切换按钮调用：

```python
def _toggle_phone_login(self) -> None:
    self.phone_login_visible = not self.phone_login_visible
    if self.phone_login_visible:
        self.phone_login_frame.grid()
        text = "收起手机号验证码登录"
    else:
        self.phone_login_frame.grid_remove()
        text = "使用手机号验证码登录"
    self.phone_toggle_button.configure(text=text)
```

登录成功区加入按钮和确认回调：

```python
self.logout_button = ttk.Button(page, text="退出当前账号", command=self._log_out)
self.logout_button.grid(row=9, column=0, columnspan=2, sticky="w", pady=(12, 0))

def _log_out(self) -> None:
    if not messagebox.askyesno("退出账号", "确认退出当前 Telegram 账号？"):
        return
    self._run_async(
        self.controller.log_out(),
        self.logout_button,
        lambda status: self.account_status_var.set(status),
    )
```

- [ ] **Step 4: 分离 API 凭据和手机号备用登录取值**

让表单取值和手机号发送方法明确使用不同校验：

```python
def _credentials_from_form(self) -> Credentials:
    return Credentials(
        api_id=int(self.api_id_var.get().strip()),
        api_hash=self.api_hash_var.get().strip(),
        phone=self.phone_var.get().strip(),
    ).validate_api()

def _send_code(self) -> None:
    try:
        credentials = self._credentials_from_form().validate_phone_login()
    except Exception as error:
        self._show_error(error)
        return
    self._run_async(
        self.controller.send_code(credentials),
        self.send_code_button,
        lambda _: self.account_status_var.set("验证码已发送"),
    )
```

扫码二步验证只读取 `qr_password_var`；手机号流程继续只读取 `password_var`，防止两个流程共享密码控件。

- [ ] **Step 5: 运行 GUI 辅助和控制器回归测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gui_app.py tests/test_gui_controller.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交二维码优先布局**

```powershell
git add -- src/tg_video_downloader/gui/app.py tests/test_gui_app.py
git commit -m "feat: make QR login the primary account UI"
```

## Task 6: 接通 GUI 异步等待、自动刷新和安全关闭

**Files:**
- Modify: `src/tg_video_downloader/gui/app.py:15`
- Modify: `tests/test_gui_app.py`
- Test: `tests/test_gui_controller.py`

- [ ] **Step 1: 写代次隔离和关闭清理测试**

在 `tests/test_gui_app.py` 增加一个只测试纯回调判定的用例，并在应用中提供 `_is_current_qr_generation`：

```python
def test_stale_qr_generation_is_ignored() -> None:
    app = object.__new__(DownloaderApp)
    app._closed = False
    app._qr_generation = 4

    assert app._is_current_qr_generation(4) is True
    assert app._is_current_qr_generation(3) is False
```

在 `tests/test_gui_controller.py` 增加：

```python
@pytest.mark.asyncio
async def test_cancel_login_is_idempotent(tmp_path: Path) -> None:
    controller, _, gateway, _ = make_controller(tmp_path)
    gateway.authorized = False
    await controller.start_qr_login(Credentials(12345, "hash"))

    await controller.cancel_login()
    await controller.cancel_login()

    assert controller.login_active is False
    assert gateway.connected is False
```

- [ ] **Step 2: 运行测试并确认代次方法缺失**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gui_app.py tests/test_gui_controller.py -q
```

Expected: FAIL，`_is_current_qr_generation` 尚未定义。

- [ ] **Step 3: 初始化二维码异步状态并显示挑战**

把相关导入改为包含：

```python
from concurrent.futures import CancelledError, Future
from datetime import datetime

from tg_video_downloader.gateway import (
    QrLoginChallenge,
    QrLoginExpiredError,
    TelethonGateway,
    TransientTelegramError,
)
from tg_video_downloader.gui.qr_view import (
    draw_qr,
    make_qr_matrix,
    retry_delay,
    seconds_until_expiry,
)
```

在 `DownloaderApp.__init__` 增加：

```python
self._qr_generation = 0
self._qr_wait_future: Future[str] | None = None
self._qr_retry_after: str | None = None
self._qr_countdown_after: str | None = None
self._qr_expires_at: datetime | None = None
self._qr_retry_attempt = 0
```

实现：

```python
def _is_current_qr_generation(self, generation: int) -> bool:
    return not self._closed and generation == self._qr_generation

def _show_qr_challenge(
    self,
    challenge: QrLoginChallenge,
    generation: int,
) -> None:
    draw_qr(self.qr_canvas, make_qr_matrix(challenge.url))
    self._qr_expires_at = challenge.expires_at
    self._qr_retry_attempt = 0
    self.account_status_var.set("等待扫码")
    self._schedule_qr_countdown()
    self._wait_for_qr_login(generation)

def _schedule_qr_countdown(self) -> None:
    if self._qr_countdown_after is not None:
        self.after_cancel(self._qr_countdown_after)
        self._qr_countdown_after = None

    def tick() -> None:
        if self._closed or self._qr_expires_at is None:
            return
        remaining = seconds_until_expiry(self._qr_expires_at)
        self.qr_countdown_var.set(f"二维码剩余 {remaining} 秒")
        if remaining > 0:
            self._qr_countdown_after = self.after(1000, tick)

    tick()
```

倒计时归零后只等待网关的过期结果触发刷新，不自行启动第二个并发等待。

- [ ] **Step 4: 实现启动、等待、自动刷新和二步验证状态**

实现统一的二维码 Future 轮询器：

```python
def _run_qr_operation(
    self,
    coroutine,
    generation: int,
    on_success,
    on_error,
) -> None:
    try:
        future = self.bridge.submit(coroutine)
    except Exception as error:
        on_error(error)
        return
    self._qr_wait_future = future

    def poll() -> None:
        if not self._is_current_qr_generation(generation):
            future.cancel()
            return
        if not future.done():
            self.after(100, poll)
            return
        if self._qr_wait_future is future:
            self._qr_wait_future = None
        try:
            result = future.result()
        except CancelledError:
            return
        except Exception as error:
            on_error(error)
            return
        on_success(result)

    self.after(100, poll)
```

实现启动、等待、刷新和成功状态：

```python
def _start_qr_login(self) -> None:
    self._qr_generation += 1
    self._begin_qr_login(self._qr_generation)

def _begin_qr_login(self, generation: int) -> None:
    try:
        credentials = self._credentials_from_form()
    except Exception as error:
        self._show_error(error)
        return
    self.account_status_var.set("正在生成二维码")
    self._run_qr_operation(
        self.controller.start_qr_login(credentials),
        generation,
        lambda challenge: self._handle_qr_started(challenge, generation),
        self._handle_qr_terminal_error,
    )

def _handle_qr_started(
    self,
    challenge: QrLoginChallenge | None,
    generation: int,
) -> None:
    if challenge is None:
        self._finish_qr_login("登录成功")
        return
    self._show_qr_challenge(challenge, generation)

def _wait_for_qr_login(self, generation: int) -> None:
    self._run_qr_operation(
        self.controller.wait_qr_login(),
        generation,
        lambda status: self._handle_qr_wait_status(status, generation),
        lambda error: self._handle_qr_wait_error(error, generation),
    )

def _handle_qr_wait_status(self, status: str, generation: int) -> None:
    if status == "需要二步验证密码":
        self.account_status_var.set(status)
        self.qr_password_frame.grid()
        return
    self._finish_qr_login(status)

def _refresh_qr_login(self, generation: int) -> None:
    if not self._is_current_qr_generation(generation):
        return
    self.account_status_var.set("正在刷新二维码")
    self._run_qr_operation(
        self.controller.refresh_qr_login(),
        generation,
        lambda challenge: self._show_qr_challenge(challenge, generation),
        lambda error: self._handle_qr_wait_error(error, generation),
    )

def _finish_qr_login(self, status: str) -> None:
    self.account_status_var.set(status)
    self.qr_canvas.delete("all")
    self.qr_countdown_var.set("")
    self.qr_password_var.set("")
    self.password_var.set("")
    self.qr_password_frame.grid_remove()
    self._qr_expires_at = None

def _handle_qr_terminal_error(self, error: Exception) -> None:
    self.account_status_var.set("登录失败")
    self._show_error(error)
```

等待失败处理固定为：

```python
def _handle_qr_wait_error(self, error: Exception, generation: int) -> None:
    if not self._is_current_qr_generation(generation):
        return
    if isinstance(error, QrLoginExpiredError):
        self._refresh_qr_login(generation)
        return
    if isinstance(error, TransientTelegramError):
        delay = retry_delay(
            self._qr_retry_attempt,
            retry_after=error.retry_after,
        )
        self._qr_retry_attempt += 1
        self.account_status_var.set(f"等待网络恢复，{delay} 秒后重试")
        self._qr_retry_after = self.after(
            delay * 1000,
            lambda: self._refresh_qr_login(generation),
        )
        return
    self.account_status_var.set("登录失败")
    self._show_error(error)
```

扫码二步验证按钮调用：

```python
def _complete_qr_password(self) -> None:
    password = self.qr_password_var.get()
    if not password:
        self._show_error(ValueError("请输入二步验证密码"))
        return
    generation = self._qr_generation
    self._run_qr_operation(
        self.controller.complete_qr_password(password),
        generation,
        self._finish_qr_login,
        self._handle_qr_terminal_error,
    )
```

- [ ] **Step 5: 实现手动刷新、取消和窗口关闭**

实现手动刷新和取消；手动刷新使用新连接，避免在旧 `wait()` 尚未完成时并发 `recreate()`：

```python
def _cancel_qr_callbacks(self) -> None:
    if self._qr_wait_future is not None:
        self._qr_wait_future.cancel()
        self._qr_wait_future = None
    for attribute in ("_qr_retry_after", "_qr_countdown_after"):
        after_id = getattr(self, attribute)
        if after_id is not None:
            self.after_cancel(after_id)
            setattr(self, attribute, None)

def _manual_refresh_qr(self) -> None:
    self._qr_generation += 1
    generation = self._qr_generation
    self._cancel_qr_callbacks()
    self._run_qr_operation(
        self.controller.cancel_login(),
        generation,
        lambda _: self._begin_qr_login(generation),
        self._handle_qr_terminal_error,
    )

def _cancel_qr_login(self) -> None:
    self._qr_generation += 1
    generation = self._qr_generation
    self._cancel_qr_callbacks()
    self._run_qr_operation(
        self.controller.cancel_login(),
        generation,
        lambda _: self._finish_qr_login("尚未登录"),
        self._handle_qr_terminal_error,
    )
```

窗口关闭按以下代码清理：

```python
def close(self) -> None:
    self._closed = True
    self._qr_generation += 1
    for after_id in (
        self._status_after,
        self._qr_retry_after,
        self._qr_countdown_after,
    ):
        if after_id is not None:
            self.after_cancel(after_id)
    if self._qr_wait_future is not None:
        self._qr_wait_future.cancel()
    try:
        cleanup = self.bridge.submit(self.controller.cancel_login())
        cleanup.result(timeout=2)
    except Exception:
        pass
    self.qr_password_var.set("")
    self.password_var.set("")
    self.bridge.close()
```

不要把二维码 URL、密码或异常原对象写入状态变量；`_safe_error` 只展示脱敏后的文本。

- [ ] **Step 6: 运行 GUI、控制器和网关专项测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gui_app.py tests/test_qr_view.py tests/test_gui_controller.py tests/test_gateway.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交异步二维码交互**

```powershell
git add -- src/tg_video_downloader/gui/app.py tests/test_gui_app.py tests/test_gui_controller.py
git commit -m "feat: automate QR login refresh and cleanup"
```

## Task 7: 扩展自检与敏感信息防护

**Files:**
- Modify: `src/tg_video_downloader/diagnostics.py:58`
- Modify: `src/tg_video_downloader/gui/controller.py:192`
- Modify: `tests/test_diagnostics.py`
- Modify: `tests/test_gui_controller.py`

- [ ] **Step 1: 写二维码依赖、空敏感值和活动登录检查测试**

把 `test_doctor_runs_local_and_online_checks_and_saves_inside_project` 的检查键断言改为：

```python
assert {item.key for item in report.checks} == {
    "project_paths",
    "python",
    "dependencies",
    "qr_code",
    "login_task",
    "config",
    "credentials",
    "disk",
    "database",
    "heartbeat",
    "telegram",
}
```

把路径失败测试的数量断言改为 `assert len(report.checks) == 11`，然后添加：

```python
@pytest.mark.asyncio
async def test_doctor_accepts_empty_phone_and_checks_qr_component(tmp_path: Path) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    ConfigStore(paths).save_credentials(Credentials(12345, "secret-hash"))
    doctor = Doctor(
        paths,
        gateway_factory=lambda *_: FakeTelegramGateway({group.chat_id: []}),
        login_active=lambda: False,
    )

    report = await doctor.run()
    checks = {item.key: item for item in report.checks}

    assert checks["credentials"].status == "pass"
    assert checks["qr_code"].status == "pass"
    assert checks["login_task"].status == "pass"


@pytest.mark.asyncio
async def test_doctor_warns_when_login_is_active_without_redaction_corruption(
    tmp_path: Path,
) -> None:
    paths, _, group = configure_valid_project(tmp_path)
    ConfigStore(paths).save_credentials(Credentials(12345, "secret-hash"))
    doctor = Doctor(
        paths,
        gateway_factory=lambda *_: FakeTelegramGateway({group.chat_id: []}),
        login_active=lambda: True,
    )

    report = await doctor.run()
    login_task = next(item for item in report.checks if item.key == "login_task")

    assert login_task.status == "warning"
    assert login_task.message == "图形界面存在进行中的登录任务"
```

- [ ] **Step 2: 运行自检测试并确认新检查不存在**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py tests/test_gui_controller.py -q
```

Expected: FAIL，`Doctor` 尚不接受 `login_active`，且缺少 `qr_code`、`login_task`。

- [ ] **Step 3: 实现二维码和登录任务自检**

把 `Doctor.__init__` 扩展为：

```python
def __init__(
    self,
    paths: ProjectPaths,
    gateway_factory: GatewayFactory,
    *,
    login_active: Callable[[], bool] | None = None,
) -> None:
    self.paths = paths
    self.gateway_factory = gateway_factory
    self.login_active = login_active
    self._secrets: tuple[str, ...] = ()
```

在本地检查列表加入：

```python
self._run_local("qr_code", self._check_qr_code),
self._run_local("login_task", self._check_login_task),
```

实现：

```python
def _check_qr_code(self) -> DiagnosticCheck:
    matrix = make_qr_matrix("tg://login?token=doctor-probe")
    if not matrix or len(matrix) != len(matrix[0]):
        return DiagnosticCheck("qr_code", "fail", "二维码矩阵生成失败")
    return DiagnosticCheck("qr_code", "pass", "二维码组件可用且无需图片文件")

def _check_login_task(self) -> DiagnosticCheck:
    if self.login_active is not None and self.login_active():
        return DiagnosticCheck(
            "login_task",
            "warning",
            "图形界面存在进行中的登录任务",
        )
    return DiagnosticCheck("login_task", "pass", "没有未清理的登录任务")
```

在 `diagnostics.py` 加入 `from tg_video_downloader.gui.qr_view import make_qr_matrix`，把依赖循环改为 `for distribution in ("telethon", "tzdata", "qrcode")`。设置敏感值时使用：

```python
self._secrets = tuple(
    value for value in (credentials.api_hash, credentials.phone) if value
)
```

`GuiController.run_doctor` 创建自检器时使用：

```python
doctor = Doctor(
    self.paths,
    self.gateway_factory,
    login_active=lambda: self.login_active,
)
```

同步把 `tests/test_gui_controller.py` 中的 `FakeDoctor.__init__` 签名改为接受仅限关键字参数 `login_active`，并断言 `login_active()` 为 `False`：

```python
def __init__(self, doctor_paths, gateway_factory, *, login_active) -> None:
    assert doctor_paths is paths
    assert login_active() is False
```

- [ ] **Step 4: 运行全部自检与脱敏测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py tests/test_gui_controller.py -q
```

Expected: PASS；诊断 JSON 不包含 API Hash、手机号、二维码 URL、验证码或密码。

- [ ] **Step 5: 提交自检扩展**

```powershell
git add -- src/tg_video_downloader/diagnostics.py src/tg_video_downloader/gui/controller.py tests/test_diagnostics.py tests/test_gui_controller.py
git commit -m "feat: diagnose QR login readiness"
```

## Task 8: 更新说明并完成全量验证

**Files:**
- Modify: `README.md`
- Modify: `docs/verification.md`
- Verify: `scripts/bootstrap.ps1`
- Verify: `scripts/check.ps1`

- [ ] **Step 1: 更新用户说明**

将 README“首次使用”登录步骤改为：

```markdown
2. 在“账号”页填写 API ID 和 API Hash，点击“扫码登录”。
3. 使用已经登录的 Telegram 客户端扫描内嵌二维码；二维码过期会自动刷新。账号启用了二步验证时，在扫码区输入密码。
4. 如果扫码不可用，展开“使用手机号验证码登录”，填写手机号并按验证码流程登录。
```

同步更新：

- `.runtime/credentials.toml` 的手机号为可选字段。
- 二维码只在内存和 Tkinter Canvas 中存在，不创建图片或临时文件。
- “需要重新登录”优先重新扫码，手机号为备用。
- 二维码令牌、验证码和两种密码均不写日志、自检或配置。
- 已授权会话后续自动复用，API ID/API Hash 仍不可省略。

- [ ] **Step 2: 运行完整项目验证**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
.\.venv\Scripts\python.exe -m pip check
```

Expected: `scripts/check.ps1` 退出码 0，pytest 全部通过，compileall 通过，`pip check` 输出 `No broken requirements found.`。

- [ ] **Step 3: 执行项目内路径和敏感信息静态检查**

Run:

```powershell
rg -n "tempfile|NamedTemporaryFile|mkstemp|PIL|qrcode\.make" src tests
rg -n "qr.*url|token|password|code" src/tg_video_downloader
git status --short
```

Expected: 第一条没有运行时代码命中；第二条中的令牌与密码只出现在内存字段、受控调用或脱敏代码中；工作区只包含本任务预期的 README 和验证记录改动。

- [ ] **Step 4: 进行人工 GUI 冒烟测试并记录结果**

使用项目内启动器打开 GUI，按顺序验证：

1. 空手机号可以生成内嵌二维码。
2. 二维码过期后原位自动刷新，项目目录内没有新增二维码图片。
3. 取消登录后连接释放，按钮恢复可用。
4. 扫码成功后群列表可刷新；关闭再打开 GUI 时直接复用项目内会话。
5. 二步验证账号显示独立密码区，错误密码可重试且控件成功后清空。
6. 手机号备用区默认折叠，展开后原发送验证码流程仍可用。
7. 点击注销并确认后会话变为未授权，下载文件和群组白名单保持不变。
8. 运行自检，报告只出现在项目 `logs/diagnostics/`，不包含敏感值。

把日期、测试账号类型、结果、报告相对路径和“项目外未产生二维码文件”的结论追加到 `docs/verification.md`；不记录账号、手机号、二维码令牌、验证码或密码。

- [ ] **Step 5: 提交文档和验证证据**

```powershell
git add -- README.md docs/verification.md
git commit -m "docs: document QR login workflow"
```

- [ ] **Step 6: 检查最终提交序列与工作区**

Run:

```powershell
git log --oneline -8
git status --short
```

Expected: 包含本计划的功能与文档提交，`git status --short` 无输出。
