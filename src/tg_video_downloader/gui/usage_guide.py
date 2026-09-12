"""Offline, always-accessible instructions. Navigation never mutates an account."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk


SECTIONS = (
    ("开始使用", "第一次使用，按顺序完成以下设置", """1. 准备 Telegram API 信息
在浏览器打开 https://my.telegram.org，登录自己的 Telegram 账号，进入 API development tools 获取 API ID 和 API Hash。在工具“账号”页填写，发起扫码或手机号登录时保存。不要使用或分享他人的 API 信息。

2. 登录 Telegram
优先使用手机 Telegram 扫描工具生成的二维码；也可展开手机号登录。若账号开启二步验证，按提示输入密码。保存了 API 信息不等于已经登录成功，请核对“账号”页状态。

3. 完成试用或授权
前往“授权”页，点击“开始试用 / 刷新授权”。首次联网验证开始 24 小时试用；已有激活码可按页面提示激活，后续使用需要有效授权。授权尚未验证时，不代表已经开通。

4. 选择监听范围
在“群组/频道”页加载列表，勾选要监听的目标并保存。仅处理你选择且账号有权访问的内容。需要旧视频时，再开启对应目标的历史补抓；不需要时可暂停。也可在“视频检索”页筛选并添加任务。

5. 选择目录，启动后台
在“运行”页选择并保存下载目录，然后点击“启动后台”。查看运行状态与进度，确认电脑保持开机联网。文件按队列依次下载。

以后再次使用，也可以随时点击顶部“使用指南”返回这里。
"""),
    ("更换账号 / API", "切换前先停后台，切换后重新确认目标", """1. 停止正在进行的操作
前往“运行”页点击“停止后台”，等后台停止。取消正在进行的扫码、手机号登录或视频检索，避免多个操作同时使用登录会话。

2. 退出旧账号
前往“账号”页，确认当前账号状态后点击“退出当前账号”，核对确认提示。指南里的“前往账号”只切换页面，不会自动退出或解绑。
如果账号仍显示正在恢复或恢复失败，请先按账号页提示重试恢复，确认状态后再操作；不要手动删除会话文件来尝试换绑。

3. 按需要更换 API 信息并重新登录
在“账号”页修改 API ID / API Hash，再发起扫码或手机号登录。若只换 Telegram 账号，是否需要换 API 信息取决于你准备使用的应用凭据。请核对新登录账号，不要把验证码或 API Hash 发给别人。

4. 重新加载并保存群组/频道
新账号可访问的范围可能不同。重新加载目标列表，重新确认监听与历史补抓选项，再保存。旧任务、队列与历史记录不会因退出账号而自动清空，也不保证能由新账号继续处理；发现旧账号任务或访问错误时，先保持后台停止并核实。

5. 检查保存位置后再启动
确认当前账号、监听范围和保存目录都正确，再到“运行”页启动后台。已下载的视频不会自动删除。

切换 Telegram 账号不等于转移设备授权。更换电脑请查看“设备授权与换绑”。
"""),
    ("设备授权与换绑", "Telegram 登录与设备授权是两套独立信息", """查看当前设备
前往“授权”页查看设备信息及已验证的授权状态。按需要刷新授权或激活。查看本指南不会连接授权服务器，也不会自动开始试用。

更换电脑或硬件
设备标识发生变化时，可能需要联系管理员核实并办理换绑。请按授权页提供的联系方式说明情况；不要把激活码、账号会话或密码公开发送。

当前没有自助转移授权按钮
退出 Telegram、重新填写 API、重装程序或删除本地文件，都不会自动把旧设备授权转移到新设备，也不会重新获得试用时间。指南只提供操作说明和页面导航，不代替管理员授权。

同一设备切换 Telegram 账号
这与更换设备不同，不要仅因更换账号就删除授权数据。以“授权”页实际验证结果为准；授权不一致或网络验证失败时，先联系管理员核实。

数据保留
授权到期不等于删除已下载的视频。换绑前请妥善保管本地文件，不要通过清空目录排查授权问题。
"""),
    ("后台与常见问题", "日常使用、退出和升级", """关闭窗口后还会下载吗？
关闭配置器窗口通常会隐藏到 Windows 系统托盘，后台可以继续运行。点击托盘图标可重新打开。若工具提示托盘不可用，请按实际提示处理，不要假设已进入托盘。

如何彻底退出？
先在“运行”页点击“停止后台”，确认停止后，再从托盘菜单退出配置器。只关窗口不等于停止下载。安装新版或卸载前也要完成这两步。

关机或休眠后还能下载吗？
不能。电脑需要开机并能正常访问 Telegram。重启电脑后需重新打开工具并启动后台；中断任务会尝试恢复有效断点。

需要另外安装 Python 吗？
官网 EXE 安装版已经内置运行环境，无需安装 Python 或 Git。源码 ZIP 的运行要求不同，请查看随包说明。安装版更新请从官网下载新安装包，退出后台后覆盖安装到原目录。

账号和文件保存在哪里？
配置、登录会话、队列和日志保存在工具运行目录（安装版为安装目录），视频保存在你选择的目录。会话文件代表账号访问能力，不要打包分享整个目录。

出现错误怎么办？
在“运行”页查看最近错误、运行自检或打开日志目录。没有填写 API 或尚未登录时，自检提示配置/账号缺失是正常的。分享日志前先检查并隐藏手机号、API Hash、验证码和其他私人信息。

本工具不能绕过账号权限，也不会因为启用历史补抓而获得无权访问的消息。
"""),
)


class UsageGuidePage(ttk.Frame):
    def __init__(self, notebook: ttk.Notebook, *, navigate: Callable[[str], None],
                 read_state: Callable[[], dict[str, object]]) -> None:
        super().__init__(notebook, padding=12)
        self.read_state = read_state
        notebook.add(self, text="使用指南")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        ttk.Label(self, text="使用指南 · 随时可查看", font=("Microsoft YaHei UI", 13)).grid(sticky="w")
        self.progress_var = tk.StringVar(self)
        self.progress_label = ttk.Label(self, textvariable=self.progress_var, wraplength=700)
        self.progress_label.grid(row=1, sticky="ew", pady=(8, 10))
        actions = ttk.Frame(self)
        actions.grid(row=2, sticky="w", pady=(0, 12))
        self.navigation_buttons = []
        for target in ("账号", "授权", "群组/频道", "运行"):
            button = ttk.Button(actions, text="前往" + target, command=lambda page=target: navigate(page))
            button.pack(side="left", padx=(0, 8))
            self.navigation_buttons.append(button)
        self.sections = ttk.Notebook(self)
        self.sections.grid(row=3, sticky="nsew")
        self.text_widgets = []
        for title, heading, body in SECTIONS:
            page = ttk.Frame(self.sections, padding=10)
            self.sections.add(page, text=title)
            page.columnconfigure(0, weight=1)
            page.rowconfigure(0, weight=1)
            text = tk.Text(page, wrap="word", width=1, height=1, relief="flat",
                           padx=10, pady=10, font=("Microsoft YaHei UI", 10), spacing3=8)
            text.grid(row=0, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(page, orient="vertical", command=text.yview)
            scroll.grid(row=0, column=1, sticky="ns")
            text.configure(yscrollcommand=scroll.set)
            text.tag_configure("heading", font=("Microsoft YaHei UI", 12, "bold"), spacing3=16)
            text.insert("end", heading + "\n\n", "heading")
            text.insert("end", body)
            text.configure(state="disabled")
            self.text_widgets.append(text)
        self.bind("<Map>", lambda _event: self.refresh())
        self.bind("<Configure>", lambda event: self.progress_label.configure(wraplength=max(200, event.width - 30)))
        self.refresh()

    def refresh(self) -> None:
        try:
            state = self.read_state()
            api = "已保存" if state.get("api_saved") else "待配置"
            account = "已登录" if state.get("account") == "登录成功" else "尚未验证 / 请查看账号页"
            targets = state.get("targets", 0)
            target_text = f"已选择 {targets} 个" if targets else "未选择"
            running = {"running": "运行中", "stopped": "已停止", "starting": "启动中"}.get(str(state.get("status", "stopped")), "请查看运行页")
            license_text = str(state.get("license") or "尚未验证").splitlines()[0]
            self.progress_var.set(f"API：{api}　账号：{account}\n目标：{target_text}　后台：{running}\n授权：{license_text}")
        except (OSError, ValueError, TypeError):
            self.progress_var.set("暂时无法读取配置状态，请前往对应设置页检查；使用指南仍可正常查看。")
