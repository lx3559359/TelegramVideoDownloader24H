from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from datetime import datetime
from math import isfinite
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from tg_video_downloader.diagnostics import DiagnosticReport
from tg_video_downloader.gateway import (
    QrLoginChallenge,
    QrLoginExpiredError,
    TransientTelegramError,
)
from tg_video_downloader.gui.controller import AsyncBridge, GuiController
from tg_video_downloader.gui.qr_view import (
    draw_qr,
    make_qr_matrix,
    retry_delay,
    seconds_until_expiry,
)
from tg_video_downloader.models import Credentials, GroupTarget


def _format_bytes(value: int | float) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def format_download_progress(progress: object) -> tuple[str, str]:
    if not isinstance(progress, dict):
        return "-", "-"
    downloaded = progress.get("downloaded_bytes")
    total = progress.get("total_bytes")
    percent = progress.get("percent")
    speed = progress.get("bytes_per_second")
    resumed = progress.get("resumed")
    if (
        isinstance(downloaded, bool)
        or not isinstance(downloaded, int)
        or downloaded < 0
        or (
            total is not None
            and (
                isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
            )
        )
        or (
            percent is not None
            and (
                isinstance(percent, bool)
                or not isinstance(percent, (int, float))
                or not isfinite(percent)
            )
        )
        or isinstance(speed, bool)
        or not isinstance(speed, (int, float))
        or not isfinite(speed)
        or speed < 0
        or not isinstance(resumed, bool)
    ):
        return "-", "-"

    details: list[str] = []
    if percent is not None:
        details.append(f"{percent:.1f}%")
    if resumed:
        details.append("断点续传")
    suffix = f"（{'，'.join(details)}）" if details else ""
    total_text = _format_bytes(total) if total is not None else "未知"
    return (
        f"{_format_bytes(downloaded)} / {total_text}{suffix}",
        f"{_format_bytes(speed)}/s",
    )


class DownloaderApp(ttk.Frame):
    def __init__(self, master: tk.Tk, controller: GuiController) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.controller = controller
        self.bridge = AsyncBridge()
        self._closed = False
        self._status_after: str | None = None
        self._status_listener: Callable[[dict[str, object]], None] = (
            lambda _snapshot: None
        )
        self._groups: tuple[GroupTarget, ...] = ()
        saved = {group.chat_id: group for group in controller.selected_groups()}
        self._selected_ids = set(saved)
        self._history_ids = {
            chat_id
            for chat_id, group in saved.items()
            if group.download_history
        }
        self._qr_generation = 0
        self._qr_wait_future: Future[Any] | None = None
        self._qr_retry_after: str | None = None
        self._qr_countdown_after: str | None = None
        self._qr_expires_at: datetime | None = None
        self._qr_retry_attempt = 0

        self.pack(fill="both", expand=True)
        self._build_account_page()
        self._build_groups_page()
        self._build_run_page()
        saved_credentials = self._load_saved_credentials()
        if saved_credentials is not None:
            self._check_saved_session()
        self._refresh_status()

    def _build_account_page(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        page = ttk.Frame(self.notebook, padding=18)
        self.notebook.add(page, text="账号")
        page.columnconfigure(1, weight=1)

        self.api_id_var = tk.StringVar()
        self.api_hash_var = tk.StringVar()
        self.phone_var = tk.StringVar()
        self.code_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.qr_password_var = tk.StringVar()
        self.account_status_var = tk.StringVar(value="尚未登录")
        self.qr_countdown_var = tk.StringVar()
        self.phone_login_visible = False

        for row, (label, variable, mask) in enumerate(
            (
                ("API ID", self.api_id_var, None),
                ("API Hash", self.api_hash_var, "*"),
            )
        ):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=7)
            entry = ttk.Entry(page, textvariable=variable, show=mask or "")
            entry.grid(row=row, column=1, sticky="ew", pady=7)

        ttk.Label(page, textvariable=self.account_status_var).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 0),
        )
        ttk.Label(page, textvariable=self.qr_countdown_var).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(4, 0),
        )

        self.qr_canvas = tk.Canvas(
            page,
            width=260,
            height=260,
            background="white",
            highlightthickness=0,
        )
        self.qr_canvas.grid(row=4, column=0, columnspan=2, pady=12)
        self.qr_canvas.grid_remove()

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
        self.qr_refresh_button.state(["disabled"])
        self.qr_cancel_button = ttk.Button(
            self.qr_actions,
            text="取消登录",
            command=self._cancel_qr_login,
        )
        self.qr_cancel_button.pack(side="left")
        self.qr_cancel_button.state(["disabled"])

        self.qr_password_frame = ttk.Frame(page)
        self.qr_password_frame.grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(10, 0),
        )
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
        self.phone_toggle_button.grid(
            row=7,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(18, 0),
        )
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

        self.logout_button = ttk.Button(
            page,
            text="退出当前账号",
            command=self._log_out,
        )
        self.logout_button.grid(row=9, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.logout_button.grid_remove()

    def _build_groups_page(self) -> None:
        page = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(page, text="群组/频道")
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(page)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="搜索").grid(row=0, column=0, padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_groups())
        ttk.Entry(toolbar, textvariable=self.search_var).grid(row=0, column=1, sticky="ew")
        self.refresh_groups_button = ttk.Button(
            toolbar,
            text="刷新群组/频道",
            command=self._load_groups,
        )
        self.refresh_groups_button.grid(row=0, column=2, padx=(8, 0))

        self.group_tree = ttk.Treeview(
            page,
            columns=("selected", "history", "title", "chat_id"),
            show="headings",
            selectmode="browse",
        )
        self.group_tree.heading("selected", text="监听")
        self.group_tree.heading("history", text="历史")
        self.group_tree.heading("title", text="名称")
        self.group_tree.heading("chat_id", text="会话 ID")
        self.group_tree.column("selected", width=60, anchor="center", stretch=False)
        self.group_tree.column("history", width=60, anchor="center", stretch=False)
        self.group_tree.column("title", width=360)
        self.group_tree.column("chat_id", width=180, anchor="e")
        self.group_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=self.group_tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.group_tree.configure(yscrollcommand=scrollbar.set)
        self.group_tree.bind("<Double-1>", self._toggle_group)
        self.group_tree.bind("<space>", self._toggle_group)

        footer = ttk.Frame(page)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.selection_count_var = tk.StringVar()
        self._update_selection_count()
        ttk.Label(footer, textvariable=self.selection_count_var).pack(side="left")
        ttk.Button(footer, text="保存选择", command=self._save_groups).pack(side="right")

    def _build_run_page(self) -> None:
        page = ttk.Frame(self.notebook, padding=18)
        self.notebook.add(page, text="运行")
        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(0, 16))
        for text, command in (
            ("启动后台", self._start_service),
            ("停止后台", self._stop_service),
            ("打开下载目录", self.controller.open_downloads),
            ("打开日志目录", self.controller.open_logs),
        ):
            ttk.Button(actions, text=text, command=lambda fn=command: self._call_sync(fn)).pack(
                side="left", padx=(0, 8)
            )
        self.doctor_button = ttk.Button(
            actions,
            text="运行自检",
            command=self._run_doctor,
        )
        self.doctor_button.pack(side="left")

        self.status_vars = {
            "status": tk.StringVar(value="stopped"),
            "updated_at": tk.StringVar(value="-"),
            "current_file": tk.StringVar(value="-"),
            "download_progress": tk.StringVar(value="-"),
            "download_speed": tk.StringVar(value="-"),
            "pending_live": tk.StringVar(value="0"),
            "pending_history": tk.StringVar(value="0"),
            "paused_history": tk.StringVar(value="0"),
            "completed": tk.StringVar(value="0"),
            "retry_wait": tk.StringVar(value="0"),
            "permanent_error": tk.StringVar(value="0"),
            "last_error": tk.StringVar(value="-"),
        }
        labels = (
            ("运行状态", "status"),
            ("最后心跳", "updated_at"),
            ("当前文件", "current_file"),
            ("下载进度", "download_progress"),
            ("下载速度", "download_speed"),
            ("实时/补抓等待", "pending_live"),
            ("历史等待", "pending_history"),
            ("历史已暂停", "paused_history"),
            ("已完成", "completed"),
            ("等待重试", "retry_wait"),
            ("永久失败", "permanent_error"),
            ("最近错误", "last_error"),
        )
        grid = ttk.Frame(page)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        for row, (label, key) in enumerate(labels):
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=4)
            ttk.Label(grid, textvariable=self.status_vars[key]).grid(row=row, column=1, sticky="w", pady=4)

        ttk.Label(page, text="每群扫描状态").pack(anchor="w", pady=(18, 6))
        self.group_status = tk.Text(page, height=10, wrap="word", state="disabled")
        self.group_status.pack(fill="both", expand=True)

    def _load_saved_credentials(self) -> Credentials | None:
        credentials = self.controller.load_credentials()
        if credentials is None:
            return None
        self.api_id_var.set(str(credentials.api_id))
        self.api_hash_var.set(credentials.api_hash)
        self.phone_var.set(credentials.phone)
        return credentials

    def _check_saved_session(self) -> None:
        generation = self._qr_generation
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

    def _handle_saved_session_status(
        self,
        authorized: bool,
        generation: int,
    ) -> None:
        if not self._is_current_qr_generation(generation):
            return
        self._finish_qr_login("登录成功" if authorized else "尚未登录")

    def _handle_saved_session_error(
        self,
        _error: Exception,
        generation: int,
    ) -> None:
        if not self._is_current_qr_generation(generation):
            return
        self._finish_qr_login("尚未登录")
        self.account_status_var.set("暂时无法检查已有会话，可稍后重试")

    def _credentials_from_form(self) -> Credentials:
        return Credentials(
            api_id=int(self.api_id_var.get().strip()),
            api_hash=self.api_hash_var.get().strip(),
            phone=self.phone_var.get().strip(),
        ).validate_api()

    def _toggle_phone_login(self) -> None:
        self.phone_login_visible = not self.phone_login_visible
        if self.phone_login_visible:
            self.phone_login_frame.grid()
            text = "收起手机号验证码登录"
        else:
            self.phone_login_frame.grid_remove()
            text = "使用手机号验证码登录"
        self.phone_toggle_button.configure(text=text)

    def _is_current_qr_generation(self, generation: int) -> bool:
        return not self._closed and generation == self._qr_generation

    def _start_qr_login(self) -> None:
        self._qr_generation += 1
        self._cancel_qr_callbacks()
        self._begin_qr_login(self._qr_generation)

    def _begin_qr_login(self, generation: int) -> None:
        if not self._is_current_qr_generation(generation):
            return
        try:
            credentials = self._credentials_from_form()
        except Exception as error:
            self._show_error(error)
            return
        self._set_qr_controls(active=True)
        self.account_status_var.set("正在生成二维码")
        self.qr_countdown_var.set("")
        self.qr_password_frame.grid_remove()
        self._run_qr_operation(
            self.controller.start_qr_login(credentials),
            generation,
            lambda challenge: self._handle_qr_started(challenge, generation),
            lambda error: self._handle_qr_terminal_error(error, generation),
        )

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
                if self._qr_wait_future is future:
                    self._qr_wait_future = None
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

    def _handle_qr_started(
        self,
        challenge: QrLoginChallenge | None,
        generation: int,
    ) -> None:
        if not self._is_current_qr_generation(generation):
            return
        if challenge is None:
            self._finish_qr_login("登录成功")
            return
        self._show_qr_challenge(challenge, generation)

    def _show_qr_challenge(
        self,
        challenge: QrLoginChallenge,
        generation: int,
    ) -> None:
        if not self._is_current_qr_generation(generation):
            return
        self._qr_retry_attempt = 0
        self._qr_expires_at = challenge.expires_at
        self.qr_password_frame.grid_remove()
        self.qr_canvas.grid()
        draw_qr(self.qr_canvas, make_qr_matrix(challenge.url))
        self.account_status_var.set("请使用 Telegram 扫描二维码")
        self._schedule_qr_countdown(generation)
        self._wait_for_qr_login(generation)

    def _schedule_qr_countdown(self, generation: int) -> None:
        if self._qr_countdown_after is not None:
            try:
                self.after_cancel(self._qr_countdown_after)
            except tk.TclError:
                pass
            self._qr_countdown_after = None

        def tick() -> None:
            self._qr_countdown_after = None
            if not self._is_current_qr_generation(generation):
                return
            expires_at = self._qr_expires_at
            if expires_at is None:
                return
            remaining = seconds_until_expiry(expires_at)
            self.qr_countdown_var.set(f"二维码剩余 {remaining} 秒")
            if remaining > 0:
                self._qr_countdown_after = self.after(1000, tick)

        tick()

    def _wait_for_qr_login(self, generation: int) -> None:
        if not self._is_current_qr_generation(generation):
            return
        self._run_qr_operation(
            self.controller.wait_qr_login(),
            generation,
            lambda status: self._handle_qr_wait_status(status, generation),
            lambda error: self._handle_qr_wait_error(error, generation),
        )

    def _handle_qr_wait_status(self, status: str, generation: int) -> None:
        if not self._is_current_qr_generation(generation):
            return
        if status == "需要二步验证密码":
            self.account_status_var.set(status)
            self.qr_countdown_var.set("")
            self.qr_canvas.delete("all")
            self.qr_canvas.grid_remove()
            if self._qr_countdown_after is not None:
                try:
                    self.after_cancel(self._qr_countdown_after)
                except tk.TclError:
                    pass
                self._qr_countdown_after = None
            self.qr_password_frame.grid()
            self.qr_refresh_button.state(["disabled"])
            return
        self._finish_qr_login(status)

    def _refresh_qr_login(self, generation: int) -> None:
        if not self._is_current_qr_generation(generation):
            return
        self.account_status_var.set("正在重新生成二维码")
        self._run_qr_operation(
            self.controller.refresh_qr_login(),
            generation,
            lambda challenge: self._show_qr_challenge(challenge, generation),
            lambda error: self._handle_qr_wait_error(error, generation),
        )

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

            def retry() -> None:
                self._qr_retry_after = None
                self._refresh_qr_login(generation)

            self._qr_retry_after = self.after(delay * 1000, retry)
            return
        self._handle_qr_terminal_error(error, generation)

    def _handle_qr_terminal_error(self, error: Exception, generation: int) -> None:
        if not self._is_current_qr_generation(generation):
            return
        self._show_error(error)
        self.account_status_var.set("登录失败，正在清理连接")
        self.qr_refresh_button.state(["disabled"])
        self.qr_cancel_button.state(["disabled"])
        self._run_qr_operation(
            self.controller.cancel_login(),
            generation,
            lambda _: self._finish_qr_login("登录失败"),
            lambda _: self._finish_qr_login("登录失败"),
        )

    def _complete_qr_password(self) -> None:
        password = self.qr_password_var.get()
        if not password:
            self._show_error(ValueError("二步验证密码不能为空"))
            return
        generation = self._qr_generation
        self.qr_password_button.state(["disabled"])
        self._run_qr_operation(
            self.controller.complete_qr_password(password),
            generation,
            lambda status: self._handle_qr_password_success(status, generation),
            lambda error: self._handle_qr_password_error(error, generation),
        )

    def _handle_qr_password_success(self, status: str, generation: int) -> None:
        if not self._is_current_qr_generation(generation):
            return
        self.qr_password_button.state(["!disabled"])
        self._finish_qr_login(status)

    def _handle_qr_password_error(self, error: Exception, generation: int) -> None:
        if not self._is_current_qr_generation(generation):
            return
        safe_message = self._safe_error(error)
        self.qr_password_button.state(["!disabled"])
        self.qr_password_var.set("")
        self.account_status_var.set("二步验证密码错误，请重试")
        messagebox.showerror("操作失败", safe_message)

    def _manual_refresh_qr(self) -> None:
        self._qr_generation += 1
        generation = self._qr_generation
        self._cancel_qr_callbacks()

        def restart(_: object) -> None:
            self._begin_qr_login(generation)

        self._run_qr_operation(
            self.controller.cancel_login(),
            generation,
            restart,
            lambda error: self._handle_qr_terminal_error(error, generation),
        )

    def _cancel_qr_login(self) -> None:
        self._qr_generation += 1
        generation = self._qr_generation
        self._cancel_qr_callbacks()
        self._run_qr_operation(
            self.controller.cancel_login(),
            generation,
            lambda _: self._finish_qr_login("尚未登录"),
            lambda error: self._handle_qr_terminal_error(error, generation),
        )

    def _cancel_qr_callbacks(self) -> None:
        future = self._qr_wait_future
        self._qr_wait_future = None
        if future is not None:
            future.cancel()
        for attribute in ("_qr_retry_after", "_qr_countdown_after"):
            after_id = getattr(self, attribute, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)

    def _set_qr_controls(self, *, active: bool) -> None:
        if active:
            self.qr_login_button.state(["disabled"])
            self.qr_refresh_button.state(["!disabled"])
            self.qr_cancel_button.state(["!disabled"])
        else:
            self.qr_login_button.state(["!disabled"])
            self.qr_refresh_button.state(["disabled"])
            self.qr_cancel_button.state(["disabled"])

    def _finish_qr_login(self, status: str) -> None:
        self._cancel_qr_callbacks()
        self._qr_expires_at = None
        self._qr_retry_attempt = 0
        self.qr_canvas.delete("all")
        self.qr_canvas.grid_remove()
        self.qr_password_frame.grid_remove()
        self.qr_countdown_var.set("")
        self.qr_password_var.set("")
        self.password_var.set("")
        self.account_status_var.set(status)
        self.qr_password_button.state(["!disabled"])
        self._set_qr_controls(active=False)
        if status == "登录成功":
            self.logout_button.grid()
        else:
            self.logout_button.grid_remove()

    def _log_out(self) -> None:
        if not messagebox.askyesno("退出账号", "确认退出当前 Telegram 账号？"):
            return

        def finished(status: str) -> None:
            self._finish_qr_login(status)

        self._run_async(self.controller.log_out(), self.logout_button, finished)

    def _send_code(self) -> None:
        try:
            credentials = self._credentials_from_form().validate_phone_login()
        except Exception as error:
            self._show_error(error)
            return
        self._qr_generation += 1
        self._cancel_qr_callbacks()
        self._finish_qr_login("尚未登录")
        self._run_async(
            self.controller.send_code(credentials),
            self.send_code_button,
            lambda _: self.account_status_var.set("验证码已发送"),
        )

    def _complete_login(self) -> None:
        code = self.code_var.get().strip()
        password = self.password_var.get()

        def finished(status: str) -> None:
            if status == "登录成功":
                self.code_var.set("")
                self._finish_qr_login(status)
            else:
                self.account_status_var.set(status)

        self._run_async(
            self.controller.complete_login(code, password),
            self.login_button,
            finished,
        )

    def _load_groups(self) -> None:
        def finished(groups: tuple[GroupTarget, ...]) -> None:
            self._groups = groups
            saved = {
                group.chat_id: group for group in self.controller.selected_groups()
            }
            self._selected_ids = set(saved)
            self._history_ids = {
                chat_id
                for chat_id, group in saved.items()
                if group.download_history
            }
            self._render_groups()

        self._run_async(
            self.controller.list_groups(),
            self.refresh_groups_button,
            finished,
        )

    def _render_groups(self) -> None:
        query = self.search_var.get().strip().casefold()
        self.group_tree.delete(*self.group_tree.get_children())
        for group in self._groups:
            if query and query not in group.title.casefold() and query not in str(group.chat_id):
                continue
            selected = "☑" if group.chat_id in self._selected_ids else "☐"
            history = "☑" if group.chat_id in self._history_ids else "☐"
            self.group_tree.insert(
                "",
                "end",
                iid=str(group.chat_id),
                values=(selected, history, group.title, group.chat_id),
            )
        self._update_selection_count()

    def _toggle_group(self, event: tk.Event[Any]) -> str:
        item = self.group_tree.identify_row(event.y) if hasattr(event, "y") else ""
        if not item:
            item = self.group_tree.focus()
        if item:
            chat_id = int(item)
            column = (
                self.group_tree.identify_column(event.x)
                if hasattr(event, "x")
                else ""
            )
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
            self._render_groups()
        return "break"

    def _update_selection_count(self) -> None:
        self.selection_count_var.set(f"已选择 {len(self._selected_ids)} 个群组/频道")

    def _save_groups(self) -> None:
        groups = tuple(
            GroupTarget(
                group.chat_id,
                group.title,
                group.chat_id in self._history_ids,
            )
            for group in self._groups
            if group.chat_id in self._selected_ids
        )
        try:
            self.controller.save_selected_groups(groups)
        except Exception as error:
            self._show_error(error)
            return
        messagebox.showinfo("已保存", f"已保存 {len(groups)} 个群组/频道")

    def set_status_listener(
        self,
        listener: Callable[[dict[str, object]], None],
    ) -> None:
        self._status_listener = listener

    def _publish_status(self, snapshot: dict[str, object]) -> None:
        try:
            self._status_listener(snapshot)
        except Exception:
            pass

    def _start_service(self) -> None:
        self.controller.start()
        snapshot: dict[str, object] = {"status": "starting"}
        self.status_vars["status"].set("starting")
        self._publish_status(snapshot)

    def _stop_service(self) -> None:
        self.controller.stop()

    def _run_doctor(self) -> None:
        def finished(result: tuple[DiagnosticReport, Path]) -> None:
            report, saved = result
            messagebox.showinfo("自检完成", format_doctor_summary(report, saved))
            self._refresh_status()

        self._run_async(
            self.controller.run_doctor(),
            self.doctor_button,
            finished,
        )

    def _call_sync(self, function) -> None:
        try:
            function()
        except Exception as error:
            self._show_error(error)

    def _run_async(self, coroutine, button: ttk.Button, on_success) -> None:
        button.state(["disabled"])
        try:
            future = self.bridge.submit(coroutine)
        except Exception as error:
            button.state(["!disabled"])
            self._show_error(error)
            return

        def poll() -> None:
            if self._closed:
                return
            if not future.done():
                self.after(100, poll)
                return
            button.state(["!disabled"])
            try:
                on_success(future.result())
            except Exception as error:
                self._show_error(error)

        self.after(100, poll)

    def _refresh_status(self) -> None:
        if self._closed:
            return
        try:
            snapshot = self.controller.read_status()
            counts = snapshot.get("counts", {})
            self.status_vars["status"].set(str(snapshot.get("status", "stopped")))
            self.status_vars["updated_at"].set(str(snapshot.get("updated_at", "-")))
            self.status_vars["current_file"].set(str(snapshot.get("current_file", "-")))
            progress_text, speed_text = format_download_progress(
                snapshot.get("progress")
            )
            self.status_vars["download_progress"].set(progress_text)
            self.status_vars["download_speed"].set(speed_text)
            for key in (
                "pending_live",
                "pending_history",
                "paused_history",
                "completed",
                "retry_wait",
                "permanent_error",
            ):
                value = counts.get(key, 0) if isinstance(counts, dict) else 0
                self.status_vars[key].set(str(value))
            last_error = snapshot.get("error") or snapshot.get("config_error") or "-"
            self.status_vars["last_error"].set(str(last_error))
            groups = snapshot.get("groups", [])
            lines = []
            if isinstance(groups, list):
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    if group.get("access_error"):
                        state = f"访问错误：{group['access_error']}"
                    elif not group.get("download_history", True):
                        state = "监听新内容；历史下载已暂停"
                    elif group.get("history_complete"):
                        state = "历史扫描完成"
                    else:
                        state = "监听新内容；历史下载开启"
                    lines.append(f"{group.get('title', group.get('chat_id'))}：{state}")
            self.group_status.configure(state="normal")
            self.group_status.delete("1.0", "end")
            self.group_status.insert("1.0", "\n".join(lines) or "暂无群组/频道状态")
            self.group_status.configure(state="disabled")
            self._publish_status(snapshot)
        except Exception as error:
            message = self._safe_error(error)
            self.status_vars["status"].set(f"状态读取失败：{message}")
            self._publish_status({"status": "error", "error": message})
        self._status_after = self.after(2000, self._refresh_status)

    def _show_error(self, error: Exception) -> None:
        messagebox.showerror("操作失败", self._safe_error(error))

    def _safe_error(self, error: Exception) -> str:
        message = str(error) or type(error).__name__
        for secret in (
            self.api_hash_var.get(),
            self.phone_var.get(),
            self.code_var.get(),
            self.password_var.get(),
            self.qr_password_var.get(),
        ):
            if secret:
                message = message.replace(secret, "***")
        return message

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._qr_generation += 1
        self._cancel_qr_callbacks()
        if self._status_after is not None:
            try:
                self.after_cancel(self._status_after)
            except tk.TclError:
                pass
            self._status_after = None
        try:
            future = self.bridge.submit(self.controller.cancel_login())
            future.result(timeout=2)
        except Exception:
            pass
        self.code_var.set("")
        self.password_var.set("")
        self.qr_password_var.set("")
        self.qr_canvas.delete("all")
        self.bridge.close()


def format_doctor_summary(report: DiagnosticReport, saved: Path) -> str:
    counts = report.counts
    return (
        f"通过：{counts['pass']}\n"
        f"警告：{counts['warning']}\n"
        f"失败：{counts['fail']}\n\n"
        f"完整报告：{saved}"
    )
