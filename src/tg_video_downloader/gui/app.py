from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from tkinter import messagebox, ttk
from typing import Any

from tg_video_downloader.gateway import TelethonGateway
from tg_video_downloader.gui.controller import AsyncBridge, GuiController
from tg_video_downloader.models import Credentials, GroupTarget
from tg_video_downloader.paths import ProjectPaths


class DownloaderApp(ttk.Frame):
    def __init__(self, master: tk.Tk, controller: GuiController) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.controller = controller
        self.bridge = AsyncBridge()
        self._closed = False
        self._status_after: str | None = None
        self._groups: tuple[GroupTarget, ...] = ()
        self._selected_ids = controller.selected_chat_ids()

        self.pack(fill="both", expand=True)
        self._build_account_page()
        self._build_groups_page()
        self._build_run_page()
        self._load_saved_credentials()
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
        fields = (
            ("API ID", self.api_id_var, None),
            ("API Hash", self.api_hash_var, "*"),
            ("手机号", self.phone_var, None),
            ("验证码", self.code_var, None),
            ("二步验证密码", self.password_var, "*"),
        )
        for row, (label, variable, mask) in enumerate(fields):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=7)
            entry = ttk.Entry(page, textvariable=variable, show=mask or "")
            entry.grid(row=row, column=1, sticky="ew", pady=7)

        actions = ttk.Frame(page)
        actions.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(14, 8))
        self.send_code_button = ttk.Button(actions, text="发送验证码", command=self._send_code)
        self.send_code_button.pack(side="left", padx=(0, 8))
        self.login_button = ttk.Button(actions, text="完成登录", command=self._complete_login)
        self.login_button.pack(side="left")
        self.account_status_var = tk.StringVar(value="尚未登录")
        ttk.Label(page, textvariable=self.account_status_var).grid(
            row=len(fields) + 1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

    def _build_groups_page(self) -> None:
        page = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(page, text="群组")
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
            text="刷新群列表",
            command=self._load_groups,
        )
        self.refresh_groups_button.grid(row=0, column=2, padx=(8, 0))

        self.group_tree = ttk.Treeview(
            page,
            columns=("selected", "title", "chat_id"),
            show="headings",
            selectmode="browse",
        )
        self.group_tree.heading("selected", text="选择")
        self.group_tree.heading("title", text="群名")
        self.group_tree.heading("chat_id", text="群 ID")
        self.group_tree.column("selected", width=60, anchor="center", stretch=False)
        self.group_tree.column("title", width=420)
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

        self.status_vars = {
            "status": tk.StringVar(value="stopped"),
            "updated_at": tk.StringVar(value="-"),
            "current_file": tk.StringVar(value="-"),
            "pending_live": tk.StringVar(value="0"),
            "pending_history": tk.StringVar(value="0"),
            "completed": tk.StringVar(value="0"),
            "retry_wait": tk.StringVar(value="0"),
        }
        labels = (
            ("运行状态", "status"),
            ("最后心跳", "updated_at"),
            ("当前文件", "current_file"),
            ("实时/补抓等待", "pending_live"),
            ("历史等待", "pending_history"),
            ("已完成", "completed"),
            ("等待重试", "retry_wait"),
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

    def _load_saved_credentials(self) -> None:
        credentials = self.controller.load_credentials()
        if credentials is None:
            return
        self.api_id_var.set(str(credentials.api_id))
        self.api_hash_var.set(credentials.api_hash)
        self.phone_var.set(credentials.phone)

    def _credentials_from_form(self) -> Credentials:
        return Credentials(
            api_id=int(self.api_id_var.get().strip()),
            api_hash=self.api_hash_var.get().strip(),
            phone=self.phone_var.get().strip(),
        ).validate()

    def _send_code(self) -> None:
        try:
            credentials = self._credentials_from_form()
        except Exception as error:
            self._show_error(error)
            return
        self._run_async(
            self.controller.send_code(credentials),
            self.send_code_button,
            lambda _: self.account_status_var.set("验证码已发送"),
        )

    def _complete_login(self) -> None:
        code = self.code_var.get().strip()
        password = self.password_var.get()

        def finished(status: str) -> None:
            self.account_status_var.set(status)
            if status == "登录成功":
                self.code_var.set("")
                self.password_var.set("")

        self._run_async(
            self.controller.complete_login(code, password),
            self.login_button,
            finished,
        )

    def _load_groups(self) -> None:
        def finished(groups: tuple[GroupTarget, ...]) -> None:
            self._groups = groups
            self._selected_ids = self.controller.selected_chat_ids()
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
            self.group_tree.insert(
                "",
                "end",
                iid=str(group.chat_id),
                values=(selected, group.title, group.chat_id),
            )
        self._update_selection_count()

    def _toggle_group(self, event: tk.Event[Any]) -> str:
        item = self.group_tree.identify_row(event.y) if hasattr(event, "y") else ""
        if not item:
            item = self.group_tree.focus()
        if item:
            chat_id = int(item)
            if chat_id in self._selected_ids:
                self._selected_ids.remove(chat_id)
            else:
                self._selected_ids.add(chat_id)
            self._render_groups()
        return "break"

    def _update_selection_count(self) -> None:
        self.selection_count_var.set(f"已选择 {len(self._selected_ids)} 个群")

    def _save_groups(self) -> None:
        groups = tuple(group for group in self._groups if group.chat_id in self._selected_ids)
        try:
            self.controller.save_selected_groups(groups)
        except Exception as error:
            self._show_error(error)
            return
        messagebox.showinfo("已保存", f"已保存 {len(groups)} 个群")

    def _start_service(self) -> None:
        self._call_sync(self.controller.start)

    def _stop_service(self) -> None:
        self._call_sync(self.controller.stop)

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
            for key in ("pending_live", "pending_history", "completed", "retry_wait"):
                value = counts.get(key, 0) if isinstance(counts, dict) else 0
                self.status_vars[key].set(str(value))
            groups = snapshot.get("groups", [])
            lines = []
            if isinstance(groups, list):
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    state = "历史完成" if group.get("history_complete") else "历史扫描中"
                    if group.get("access_error"):
                        state = f"访问错误：{group['access_error']}"
                    lines.append(f"{group.get('title', group.get('chat_id'))}：{state}")
            self.group_status.configure(state="normal")
            self.group_status.delete("1.0", "end")
            self.group_status.insert("1.0", "\n".join(lines) or "暂无群组状态")
            self.group_status.configure(state="disabled")
        except Exception as error:
            self.status_vars["status"].set(f"状态读取失败：{self._safe_error(error)}")
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
        ):
            if secret:
                message = message.replace(secret, "***")
        return message

    def close(self) -> None:
        self._closed = True
        if self._status_after is not None:
            self.after_cancel(self._status_after)
        self.bridge.close()


def run_gui(paths: ProjectPaths) -> None:
    root = tk.Tk()
    root.title("Telegram 视频自动下载器")
    root.geometry("860x620")
    root.minsize(760, 540)
    app = DownloaderApp(root, GuiController(paths, TelethonGateway))

    def close_window() -> None:
        app.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_window)
    root.mainloop()
