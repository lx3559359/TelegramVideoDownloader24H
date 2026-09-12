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
    def __init__(self, parent, *, run_async, refresh_license, identify=None, on_device=None):
        super().__init__(parent, text="赞助解锁", padding=10)
        self._run_async = run_async
        self._identify = identify
        self._on_device = on_device
        self._loaded = False
        self._picture = None
        self._poller = VisibleRefresh(self, refresh_license)
        self._device = None
        self._busy = False
        self._closed = False
        self._photo = None
        self.columnconfigure(0, weight=1)
        self.short_var = tk.StringVar(value="加载后显示")
        self.details_var = tk.StringVar(value="打开本页后加载赞助信息，无需开始试用。")
        self.message_var = tk.StringVar()
        reference = ttk.Frame(self)
        reference.grid(row=0, column=0, sticky="ew")
        ttk.Label(reference, text="付款备注号：").pack(side="left")
        ttk.Entry(reference, textvariable=self.short_var, state="readonly", width=15).pack(side="left")
        ttk.Button(reference, text="复制", command=self.copy_device).pack(side="left", padx=6)
        self.reload_button = ttk.Button(reference, text="刷新赞助信息", command=self.reload)
        self.reload_button.pack(side="left")
        ttk.Label(self, textvariable=self.details_var, wraplength=490).grid(
            row=1, column=0, sticky="nw", pady=8)
        self.instructions = ttk.Label(
            self, wraplength=490,
            text="微信扫码 → 选择金额 → 添加备注 → 填写付款备注号 → 付款 → 等待管理员核实。\n"
                 "管理员核实实际到账后开通，不会付款即自动生效。\n"
                 "点击上方开始试用 / 刷新授权后，本页每 30 秒刷新授权。")
        self.instructions.grid(row=2, column=0, sticky="nw")
        ttk.Label(self, textvariable=self.message_var, wraplength=490).grid(row=3, column=0, sticky="w")
        self.image_label = ttk.Label(self)
        self.image_label.grid(row=0, column=1, rowspan=4, padx=(12, 0))
        self.image_label.bind("<Button-1>", self.enlarge_qr)
        ttk.Button(self, text="点击放大二维码", command=self.enlarge_qr).grid(row=4, column=1)
        self.bind("<Map>", self._visibility, add="+")
        self.bind("<Unmap>", self._visibility, add="+")

    def _visibility(self, event) -> None:
        if event.widget is self:
            self._poller.set_visible(bool(self.winfo_viewable()))
            if self.winfo_viewable() and not self._loaded and self._identify is not None:
                self.reload()

    def enlarge_qr(self, event=None) -> None:
        if self._picture is None:
            return
        window = tk.Toplevel(self)
        window.title("微信扫码 · 请填写付款备注号")
        picture = self._picture.copy()
        scale = max(1, (440 + picture.width - 1) // picture.width)
        picture = picture.resize((picture.width * scale, picture.height * scale), Image.Resampling.NEAREST)
        if max(picture.size) > 700:
            picture.thumbnail((700, 700), Image.Resampling.NEAREST)
        window._photo = ImageTk.PhotoImage(picture, master=window)
        ttk.Label(window, image=window._photo).pack(padx=16, pady=16)
        ttk.Label(window, text=f"付款备注：{self.short_var.get()}；等待管理员核实到账。").pack(pady=(0, 16))

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
            self.message_var.set("已复制付款备注号；它不是激活码，无需填写完整硬件设备码。")

    def reload(self) -> None:
        if self._closed or self._busy:
            return
        self._busy = True
        self.message_var.set("正在加载赞助信息…")

        async def load():
            async def identity():
                device = self._device
                if device is None:
                    if self._identify is None:
                        raise ValueError("设备码尚未读取")
                    device = await self._identify()
                try:
                    short = await asyncio.to_thread(fetch_device_info, device)
                    return device, short, None
                except Exception as error:
                    return device, None, error
            return await asyncio.gather(identity(), asyncio.to_thread(fetch_sponsor), return_exceptions=True)

        def finished(result):
            self._busy = False
            if self._closed:
                return
            self._loaded = True
            identity, display = result
            identity_error = identity if isinstance(identity, Exception) else identity[2]
            if not isinstance(identity, Exception):
                device, short, _ = identity
                self._device = device
                if self._on_device is not None:
                    self._on_device(device)
                if short is not None:
                    self.short_var.set(short)
            if isinstance(display, Exception):
                failed(display)
                return
            self._photo = None
            self._picture = None
            self.image_label.configure(image="")
            config = display.config
            if not config.enabled:
                self.details_var.set("暂未配置，请联系管理员。")
            else:
                self.details_var.set(
                    f"月卡 30 天 ¥{config.month / 100:.2f} · "
                    f"年卡 365 天 ¥{config.year / 100:.2f} · 永久 ¥{config.permanent / 100:.2f}\n"
                    f"开通说明：{config.contact}")
                self._picture = display.image.copy()
                picture = self._picture.copy()
                scale = max(1, (220 + min(picture.size) - 1) // min(picture.size))
                picture = picture.resize((picture.width * scale, picture.height * scale), Image.Resampling.NEAREST)
                if max(picture.size) > 280:
                    picture.thumbnail((280, 280), Image.Resampling.NEAREST)
                self._photo = ImageTk.PhotoImage(picture, master=self)
                self.image_label.configure(image=self._photo)
            self.message_var.set("请核对收款人、金额及授权期限后扫码。" if config.enabled else "")
            if identity_error is not None:
                self.message_var.set(f"付款备注号未确认，暂勿付款；请刷新重试。{identity_error}")

        def failed(error):
            self._busy = False
            if self._closed:
                return
            # Hide potentially stale payment instructions; licensing state stays intact.
            self._photo = None
            self._picture = None
            self.image_label.configure(image="")
            self.details_var.set("赞助信息暂不可用，请稍后刷新；原激活码入口仍可使用。")
            self.message_var.set(str(error))

        self._run_async(load(), self.reload_button, finished, failed)

    def close(self) -> None:
        self._closed = True
        self._poller.close()
