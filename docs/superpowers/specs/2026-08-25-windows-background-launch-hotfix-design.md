# Windows 后台启动无响应热修设计

## 结论

> `DETACHED_PROCESS` 会让当前 Windows 环境中的 PowerShell 在执行脚本前直接以 0 退出。热修将仅保留 `CREATE_NO_WINDOW`，并让 GUI 明确显示启动中或启动失败。

## 问题与证据

- GUI 已保存有效凭据和 1 个白名单目标，但点击“启动后台”后没有 PID、进程锁或心跳。
- 同一个 `run-supervisor.ps1` 前台运行时可以正常进入 `running` 状态，证明配置、会话、服务和 PowerShell 脚本本身可用。
- 隔离探针对 Windows 创建标志逐项对比：
  - 默认标志：脚本执行。
  - 仅 `CREATE_NO_WINDOW`：脚本执行且无控制台窗口。
  - 仅 `DETACHED_PROCESS`：脚本第一行未执行，进程以 0 退出。
  - 两者组合：与仅 `DETACHED_PROCESS` 相同。

## 选定方案

1. `start_hidden_supervisor` 移除 `DETACHED_PROCESS`，保留 `CREATE_NO_WINDOW`。普通 Windows 子进程在 GUI 关闭后仍可继续运行，不需要脱离标志。
2. 启动后短时间轮询守护进程标记和子进程退出状态：
   - 出现 `supervisor.pid`、下载器锁或心跳即视为启动请求已被接收。
   - 子进程在标记出现前退出时抛出明确错误，包括退出码；GUI 使用现有脱敏错误框展示。
   - 子进程仍存活但启动较慢时不误判失败，状态页继续通过心跳更新最终状态。
3. “启动后台”点击成功后立即把运行状态设为 `starting`，让用户看到按钮已经生效；现有两秒状态刷新随后替换为真实心跳状态。
4. 不改 Telegram 登录、白名单、下载队列、历史游标、目录布局或停止语义。

## 测试设计

- 单元测试确认 Windows 启动器使用 `CREATE_NO_WINDOW` 且不包含 `DETACHED_PROCESS`。
- 单元测试确认子进程提前退出且没有启动标记时会报告失败。
- GUI 单元测试确认成功点击后显示 `starting`，控制器异常仍走现有错误提示。
- 完整运行 `scripts/check.ps1` 和 `pip check`。
- 真实 Windows 冒烟：点击“启动后台”后确认 PID、锁和 `running` 心跳出现；关闭 GUI 后后台继续；再通过停止标记有序退出。

## 安全与数据边界

- 所有 PID、心跳、日志、临时文件和下载内容继续位于项目目录。
- 不创建计划任务，不写用户启动目录，不把运行数据迁移到 C 盘。
- 测试不更改当前登录会话和已保存白名单。
