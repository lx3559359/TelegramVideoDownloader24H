"""Installer-only update view; worker threads communicate through a queue."""
from __future__ import annotations

from queue import Empty, Queue
from threading import Event, Thread
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from tg_video_downloader.installer_update import (
    DownloadCancelled, InstallerUpdateManager, SITE, consume_installer_result,
)


class InstallerUpdatePanel(ttk.Frame):
    def __init__(self, parent, controller, *, on_exit, manager=None):
        super().__init__(parent, padding=12)
        self.controller = controller
        self.manager = manager or InstallerUpdateManager(controller.paths)
        self.on_exit = on_exit
        self.state = 'idle'
        self.release = None
        self.downloaded = None
        self.closed = False
        self.cancel_event = Event()
        self.events = Queue()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        ttk.Label(self, text=f'当前版本  {self.manager.current_version}  · Windows 安装版').grid(row=0, column=0, sticky='w')
        buttons = ttk.Frame(self)
        buttons.grid(row=1, column=0, sticky='w', pady=12)
        self.check_button = ttk.Button(buttons, text='检查更新', command=self.check)
        self.check_button.pack(side='left')
        self.install_button = ttk.Button(buttons, text='下载更新', command=self.install)
        self.install_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(buttons, text='取消下载', command=self.cancel)
        self.cancel_button.pack(side='left')
        ttk.Button(buttons, text='打开官网', command=lambda: webbrowser.open(SITE)).pack(side='left', padx=8)
        self.source_choice = tk.StringVar(value='自动（镜像优先）')
        self.source_select = ttk.Combobox(buttons, textvariable=self.source_choice,
            values=('自动（镜像优先）', '魔搭国内镜像', '官网'), state='readonly', width=19)
        self.source_select.pack(side='left', padx=8)
        self.source_status = tk.StringVar(value='更新源：自动（镜像优先）；切换源后从头下载并重新校验。')
        ttk.Label(self, textvariable=self.source_status).grid(row=6, column=0, sticky='w')
        self.status = tk.StringVar(value='仅在手动检查时联网；更新会保留配置、授权和下载数据。')
        ttk.Label(self, textvariable=self.status, wraplength=750).grid(row=2, column=0, sticky='w')
        self.progress = ttk.Progressbar(self, maximum=100)
        self.progress.grid(row=3, column=0, sticky='ew', pady=10)
        self.notes = tk.Text(self, height=14, wrap='word', state='disabled')
        self.notes.grid(row=4, column=0, sticky='nsew')
        ttk.Label(self, text='安装前会请求确认并正常停止后台；安装成功后重启工具，恢复原后台状态。\n安装包尚未进行发行者代码签名。', wraplength=750).grid(row=5, column=0, sticky='w', pady=10)
        self._buttons()
        self.after_id = self.after(100, self._poll)
        if hasattr(controller, 'paths'):
            try:
                result = consume_installer_result(controller.paths)
                if result:
                    self.status.set(result['message'])
            except (ValueError, OSError) as error:
                self.status.set(f'无法读取上次更新结果：{error}')

    def _buttons(self):
        busy = self.state in ('checking', 'downloading', 'installing', 'handoff')
        self.check_button.state(['disabled' if busy else '!disabled'])
        self.install_button.state(['disabled' if busy or self.release is None else '!disabled'])
        self.install_button.configure(text='安装并重启' if self.downloaded else '下载更新')
        self.cancel_button.state(['!disabled' if self.state == 'downloading' else 'disabled'])
        self.source_select.configure(state='disabled' if busy else 'readonly')

    def _run(self, operation, action):
        def worker():
            try:
                self.events.put(('done', operation, action()))
            except Exception as error:
                self.events.put(('error', operation, error))
        Thread(target=worker, daemon=True, name='installer-update').start()

    def check(self):
        if self.closed or self.state in ('checking', 'downloading', 'installing', 'handoff'):
            return
        self.state = 'checking'
        self.release = self.downloaded = None
        self.progress['value'] = 0
        self.status.set('正在检查官网稳定版本……')
        self._buttons()
        self._run('check', self.manager.check)

    def install(self):
        if self.closed or self.state not in ('available', 'ready'):
            return
        if self.downloaded is None:
            if self.release is None:
                return
            self.cancel_event.clear()
            self.state = 'downloading'
            self.status.set('正在下载安装包，后台任务保持运行……')
            self._buttons()
            source = {'自动（镜像优先）': 'auto', '魔搭国内镜像': 'mirror', '官网': 'official'}[self.source_choice.get()]
            self._run('download', lambda: self.manager.download(self.release, self.cancel_event,
                lambda n,t: self.events.put(('progress', n, t)), source=source,
                source_changed=lambda name: self.events.put(('source', name, None))))
            return
        if self.controller.login_active:
            self.status.set('请先完成或取消当前登录任务，再安装更新。')
            return
        if not messagebox.askyesno('安装软件更新',
                f'即将安装 v{self.downloaded.release.version}。\n将正常停止后台、退出工具并安装，完成后重启并恢复原后台状态。\n配置、授权和下载记录会保留。\n\n确认继续？', parent=self):
            return
        self.state = 'installing'
        self.status.set('正在校验并等待后台安全停止，请勿关闭电脑……')
        self._buttons()
        self._run('install', lambda: self.controller.prepare_installer_install(self.manager, self.downloaded))

    def cancel(self):
        if self.state == 'downloading':
            self.cancel_event.set()
            self.status.set('正在取消下载（网络超时最长约 20 秒）……')
            self.cancel_button.state(['disabled'])

    def _poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, operation, value = self.events.get_nowait()
                if kind == 'source':
                    self.source_status.set(f'当前下载源：{operation}；若需手动换源，请先取消下载。')
                elif kind == 'progress':
                    self.progress['value'] = operation / value * 100
                    self.status.set(f'下载安装包：{operation / 1024**2:.1f} / {value / 1024**2:.1f} MiB')
                elif kind == 'error':
                    self._failed(value)
                else:
                    self._complete(operation, value)
                if self.closed:
                    return
        except Empty:
            pass
        if not self.closed:
            self.after_id = self.after(100, self._poll)

    def _complete(self, operation, value):
        if operation == 'check':
            self.release = value
            self.state = 'available' if value else 'idle'
            self.status.set(f'发现 v{value.version} · 安装包 {value.size/1024**2:.1f} MiB' if value else '当前已是最新稳定版')
            self.notes.configure(state='normal')
            self.notes.delete('1.0', 'end')
            if value:
                self.notes.insert('1.0', value.notes)
            self.notes.configure(state='disabled')
        elif operation == 'download':
            self.downloaded = value
            self.state = 'ready'
            self.status.set('安装包校验通过。点击“安装并重启”继续。')
        elif operation == 'install':
            self.state = 'handoff'
            self.status.set('更新助手已就绪，工具即将退出并安装。')
            self.on_exit()
            return
        self._buttons()

    def _failed(self, error):
        self.state = 'available' if self.release else 'idle'
        self.downloaded = None
        self.status.set(str(error) if isinstance(error, DownloadCancelled) else f'更新未完成：{error}。可重试或打开官网。')
        self._buttons()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel_event.set()
        self.after_cancel(self.after_id)
