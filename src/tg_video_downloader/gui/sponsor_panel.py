"""Sponsorship display and visibility-scoped license refresh."""
from __future__ import annotations

import asyncio
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from tg_video_downloader.sponsorship import fetch_device_info, fetch_sponsor


class VisibleRefresh:
    def __init__(self, scheduler, callback):
        self.scheduler = scheduler
        self.callback = callback
        self.visible = False
        self.ready = False
        self.closed = False
        self.token = None

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        self._schedule()

    def set_ready(self) -> None:
        self.ready = True
        self._schedule()

    def _schedule(self) -> None:
        if self.token is not None:
            self.scheduler.after_cancel(self.token)
            self.token = None
        if self.visible and self.ready and not self.closed:
            self.token = self.scheduler.after(30_000, self._tick)

    def _tick(self) -> None:
        self.token = None
        if self.visible and self.ready and not self.closed:
            self.callback()
            self._schedule()

    def close(self) -> None:
        self.closed = True
        self._schedule()


class SponsorPanel(ttk.LabelFrame):
    def __init__(self, parent, *, run_async, refresh_license):
        super().__init__(parent, text="赞助解锁", padding=10)
        self._run_async = run_async
        self._poller = VisibleRefresh(self, refresh_license)
        self._device = None
        self._busy = False
        self._closed = False
        self._photo = None
        self.columnconfigure(0, weight=1)
        self.short_var = tk.StringVar(value="验证后显示")
        self.details_var = tk.StringVar(value="暂未配置，请联系管理员。验证授权后加载赞助信息。")
        self.message_var = tk.StringVar()
        reference = ttk.Frame(self)
        reference.grid(row=0, column=0, sticky="ew")
        ttk.Label(reference, text="付款备注设备识别号：").pack(side="left")
        ttk.Entry(reference, textvariable=self.short_var, state="readonly", width=15).pack(side="left")
        ttk.Button(reference, text="复制", command=self.copy_device).pack(side="left", padx=6)
        self.reload_button = ttk.Button(reference, text="刷新赞助信息", command=self.reload)
        self.reload_button.pack(side="left")
        ttk.Label(self, textvariable=self.details_var, wraplength=490).grid(
            row=1, column=0, sticky="nw", pady=8)
        self.instructions = ttk.Label(
            self, wraplength=490,
            text="扫码时请备注设备识别号；如无法备注，请通过上方联系方式发送识别号。\n"
                 "管理员核实实际到账后开通，不会付款即自动生效。\n"
                 "本页显示期间每 30 秒刷新授权，也可手动刷新。")
        self.instructions.grid(row=2, column=0, sticky="nw")
        ttk.Label(self, textvariable=self.message_var, wraplength=490).grid(row=3, column=0, sticky="w")
        self.image_label = ttk.Label(self)
        self.image_label.grid(row=0, column=1, rowspan=4, padx=(12, 0))
        self.bind("<Map>", self._visibility, add="+")
        self.bind("<Unmap>", self._visibility, add="+")

    def _visibility(self, event) -> None:
        if event.widget is self:
            self._poller.set_visible(bool(self.winfo_viewable()))

    def license_refreshed(self, device: str) -> None:
        if self._closed:
            return
        self._device = device
        self._poller.set_ready()
        if self.winfo_viewable():
            self._poller.set_visible(True)
        self.reload()

    def copy_device(self) -> None:
        from tg_video_downloader.sponsorship import SHORT_ID
        if SHORT_ID.fullmatch(self.short_var.get()):
            self.clipboard_clear()
            self.clipboard_append(self.short_var.get())
            self.message_var.set("已复制设备识别号；它不是激活码。")

    def reload(self) -> None:
        if self._closed or self._busy:
            return
        if self._device is None:
            self.message_var.set("请先点击上方开始试用或刷新授权；首次验证开始 24 小时试用。")
            return
        self._busy = True
        self.message_var.set("正在加载赞助信息…")

        async def load():
            # Do not couple a failed optional sponsor request to the license lease.
            short = await asyncio.to_thread(fetch_device_info, self._device)
            try:
                display = await asyncio.to_thread(fetch_sponsor)
                return short, display, None
            except Exception as error:
                return short, None, error

        def finished(result):
            self._busy = False
            if self._closed:
                return
            short, display, error = result
            self.short_var.set(short)
            if error is not None:
                failed(error)
                return
            self._photo = None
            self.image_label.configure(image="")
            config = display.config
            if not config.enabled:
                self.details_var.set("暂未配置，请联系管理员。")
            else:
                self.details_var.set(
                    f"收款人：{config.recipient}\n月卡 30 天 ¥{config.month / 100:.2f} · "
                    f"年卡 365 天 ¥{config.year / 100:.2f} · 永久 ¥{config.permanent / 100:.2f}\n"
                    f"联系方式：{config.contact}")
                picture = display.image.copy()
                picture.thumbnail((180, 180), Image.Resampling.LANCZOS)
                self._photo = ImageTk.PhotoImage(picture, master=self)
                self.image_label.configure(image=self._photo)
            self.message_var.set("请核对收款人、金额及授权期限后扫码。" if config.enabled else "")

        def failed(error):
            self._busy = False
            if self._closed:
                return
            # Hide potentially stale payment instructions; licensing state stays intact.
            self._photo = None
            self.image_label.configure(image="")
            self.details_var.set("赞助信息暂不可用，请稍后刷新；原激活码入口仍可使用。")
            self.message_var.set(str(error))

        self._run_async(load(), self.reload_button, finished, failed)

    def close(self) -> None:
        self._closed = True
        self._poller.close()
