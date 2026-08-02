# 更新日志

## [v4.0.2] - 2026-08-02

- Codex 派发恢复为 `~/.codex/agents/handoff-ds.toml`、`handoff-gemini.toml`
  和 `handoff-opus.toml` custom agents，由 subagent 完成事件通知父会话。
- `handoff init` 不再向 Codex 安装 handoff skills，并清理仍指向本包的 v4.0.0
  Codex skill 链接；Claude Code 继续使用后台 shell skills。
- `handoff run` / `handoff resume` 新增 Codex `--fast`，仅对本轮启用 Fast Mode，
  并在运行记录和 TUI INFO 列显示 `Fast`。
- managed resume 的 Pro 状态改为继承同一 session 最新一轮；Fast 不继承，续接时
  必须再次显式指定。
- TUI 的 STATUS 列使用 `error|lost` 标识进程失联，INFO 列完整显示 resume、Pro、
  Fast 信息且不再省略；详情页支持复制鼠标选中的文本。
- 新增 `make agents` / `make generated`，在开发阶段生成 backend 变体；Codex Fast
  参数协议保持为独立 skill 文档，避免被通用模板覆盖。

## [v4.0.1] - 2026-07-29

- 修复 `handoff list` 详情页 Prompt/Result 标签无法稳定使用鼠标或触控板滚动的问题。
- 消除 Markdown 代码块与文档外层之间的嵌套纵向滚动，避免触控板惯性滚动到顶部时上下抖动。

## [v4.0.0] - 2026-07-28

- `handoff open` 和 `handoff resume` 支持通过 `--backend`、`--session-id`
  与 `--cwd` 接入尚未记录在 `handoff.db` 中的原生会话。
- `handoff resume` 专注向已有会话追加受管理的非交互任务；交互式重开统一由
  `handoff open` 处理。
- handoff skills 改为先通过 `handoff new --write` 预分配任务文件和 run ID，
  再执行 `run` 或 `resume`，调用方可在派发前确定结果文件路径。
- `handoff init` 自动发现内置 skills，并分别安装到 Claude Code 和 Codex 的
  skills 目录；`handoff-codex` 只允许在 Codex 中显式调用。
- Codex 集成从 `.toml` subagent 迁移到 skills。初始化时，旧的
  `~/.codex/agents/handoff-*.toml` 会重命名为 `.removed.bak` 并输出 warning，
  不会直接删除或覆盖已有备份。
- Codex backend 支持分别配置 `system_prompt`、`model_reasoning_effort`
  和 `pro_model_reasoning_effort`。
- Codex 执行默认绕过审批和 sandbox，避免后台任务因交互确认而中断。
- backend 模型配置会在创建运行记录前完成校验，避免配置错误留下永久
  `running` 记录。
- TUI 会识别进程组消失和 PID 复用，将对应运行标记为 `error` 并显示
  `proc-lost`。
- 终止任务时直接检查已保存的进程组 ID，即使进程组 leader 已退出也能处理
  剩余子进程。
- 新增 CLI 架构文档，说明模块职责、依赖关系、运行数据流与持久化结构。

[v4.0.2]: https://github.com/dazuiba/handoff/compare/v4.0.1...v4.0.2
[v4.0.1]: https://github.com/dazuiba/handoff/compare/v4.0.0...v4.0.1
[v4.0.0]: https://github.com/dazuiba/handoff/compare/v0.3.9...v4.0.0
