# Windows 验收记录

测试日期：2026-08-25（Asia/Shanghai）
环境：Windows，Python 3.12.10
实施分支：`codex/download-policy-progress-resume`
实现基线：`9956a2c47c5ce28feaaf7669f4490af64b976c58`

本记录不包含手机号、群组/频道名称、会话 ID、消息 ID、文件名、API 凭据、二维码令牌或登录会话内容。

## 自动化验证

- 项目内 `scripts/check.ps1` 完整通过：127 项测试通过，`src` 语法编译通过，运行目录边界检查通过。
- `python -m pip check` 输出 `No broken requirements found.`。
- `cryptg 0.6.0` 可从原项目虚拟环境导入，解析路径位于 `D:\Codex Project\Telegram自动化脚本\.venv\Lib\site-packages\cryptg\`。
- 配置、SQLite、扫描协调器、Telethon 网关、下载工作器、心跳、GUI 控制器、服务集成和 Windows 后台启动回归均包含在完整测试中。
- 停止后台专项集成测试连续运行 3 次均在 2 秒时限内退出；完整服务集成测试 15 项通过。
- 最终检查显式把 `TEMP`、`TMP`、pytest `--basetemp`、pip 缓存和 Python 字节码缓存指向项目或任务工作树在 D 盘的目录。
- `git diff --check` 无空白错误；仅出现 Git 对 Windows CRLF 转换的提示。

## TDD、调试与代码审查

- 各计划任务均先增加失败测试，再实施最小改动并运行聚焦测试与完整回归。
- 按 `requesting-code-review` 模板逐文件复核下载生命周期、取消路径、SQLite 状态和运行时切换语义。
- 审查发现父任务取消时可能遗留嵌套 Telegram 下载任务；先用测试复现，再以提交 `d80135e` 修复并验证任务释放。
- 完整回归随后暴露停止测试的 2 秒边界抖动；按系统化调试定位到两个连续 1 秒轮询等待，以提交 `815b472` 改为停止事件可唤醒等待。
- 已向独立审查通道发起复核；委派源任务未返回文字审查结论，因此本记录不虚构独立批准。当前执行者完成了结构化自审、聚焦回归和完整回归，未发现遗留 Critical 或 Important 正确性问题。

## 数据迁移与用户数据保护

- 真实原项目只读基线：1 个已配置目标、789 条队列任务；状态为完成 3、下载中 1、等待 785；已有 3 个完成文件和 1 个有效 `.part`。
- SQLite 迁移先对在线数据库执行只读备份副本验收；成功副本为 `D:\Codex Project\Telegram自动化脚本\.cache\codex-worktrees\dcf3\tmp\migration-acceptance-20260825-172636-889.sqlite3`。
- 迁移副本增加 `groups.download_history`，并保持 1 个目标、789 条任务、各状态计数和扫描游标不变；旧配置缺少字段时按历史开启处理。
- 真实运行数据库随后完成同一加列迁移。实施和验收未清空、重建或删除 Telegram 会话、SQLite 队列、游标、已下载文件或合法 `.part`。
- 验收切换前保存的配置副本为 `D:\Codex Project\Telegram自动化脚本\.tmp\acceptance-config-backup-20260825-180554.toml`；验收结束后配置已恢复为明确的历史开启状态。

## 真实 Telegram 登录与下载验收

使用原项目已有授权会话和既有队列完成；没有发送、伪造或制造 Telegram 新消息。

```text
session_authorized=true
cryptg_loaded=true
heartbeat_status=running
part_bytes_increased=true
heartbeat_progress_increased=true
resume_offset_greater_than_zero=true
history_pause_applied=true
live_monitoring_remained_enabled=true
completed_file_created=true
```

证据摘要：

- 原会话无需重新登录，且 `cryptg` 从项目 `.venv` 加载。
- 首个真实续传样本的心跳标记 `resumed=true`；已下载字节和项目 `.tmp` 中的 `.part` 均连续增长。
- 真实完成文件数从 3 增至 8；完成后 `.part` 被原子收尾，不以半成品冒充完成文件。
- 关闭目标历史时，开关在一个配置轮询周期内生效：781 条历史任务显示为暂停、可执行历史为 0、目标监听保持启用。
- 开关切换前已经领取的 465,385,323 字节历史文件被允许继续；其心跳从 98.69% 增长到 99.81%，随后完成数从 7 增至 8、`.part` 消失，期间没有领取下一条历史任务。
- 文件完成后恢复历史开关：暂停数回到 0，剩余历史重新可执行，服务继续受监督运行。
- 最终监督进程链经 PID、命令行、解释器和项目路径核验为 `96544 -> 145784 -> 175484`；心跳状态为 `running`。旧诊断服务只在最终替换阶段处理，实际服务 PID `91020` 在请求优雅停止并等待后才被精确终止，未匹配或终止其他 Python 进程。

## 项目内生成数据位置

- 原项目运行数据：`D:\Codex Project\Telegram自动化脚本\.venv`、`.runtime`、`.tmp`、`.cache`、`logs`、`downloads`。
- 任务工作树运行和测试数据物理位置：`D:\Codex Project\Telegram自动化脚本\.cache\codex-worktrees\dcf3\` 下的 `venv`、`tmp`、`cache`、`runtime`、`logs`、`downloads`。
- 工作树内对应目录是指向上述 D 盘位置的 junction；测试缓存、pytest 临时目录、字节码、会话替身、日志和下载替身没有落到 C 盘。
- 迁移验收副本与缓存：`D:\Codex Project\Telegram自动化脚本\.cache\codex-worktrees\dcf3\tmp\migration-acceptance-*` 和 `pycache-migration-acceptance`。
- 取消路径诊断材料：`D:\Codex Project\Telegram自动化脚本\.cache\codex-worktrees\dcf3\tmp\cancel_debug.py`、`cancel-debug-root`。
- 代码审查与回归临时目录：原项目 `.tmp\code-review-*`、`.tmp\senior-review-latest`，以及任务工作树物理 `tmp\pytest-of-luojixiang1`。
- 真实下载断点始终位于原项目 `.tmp`，完成文件始终位于原项目 `downloads`。

所有这些目录都位于项目或本任务工作树的项目数据路径内；没有删除验收产生的缓存、临时文件、测试输出、日志或下载数据。
