# 设计说明

[← 返回 README](../README.zh-CN.md)

本文档解释 handoff 的几个关键设计决策。

## 为什么 Claude Code 用 skill、Codex 用 custom agent

两个宿主都先用 `handoff new --write` 创建规范的 prompt 文件并确定 run ID，但等待
机制不能共用：

**Claude Code — 后台 shell skill**

- 后台 shell 完成时会主动通知当前会话，不需要轮询。
- 实时进度留在 shell view，不进入主会话上下文。
- `handoff init` 把 `handoff-ds`、`handoff-gemini`、`handoff-codex` 三个 skills
  软链接到 `~/.claude/skills/`。

**Codex — custom agent**

- 后台 terminal 进程退出时不会唤醒已经 idle 的主线程；主线程必须主动
  `write_stdin` 才能取得完成状态。
- Codex 能感知 subagent 完成事件。因此 custom agent 在自己的线程中阻塞执行
  `handoff run`，必要时持续读取同一个 terminal session；进程退出后只向父线程返回
  `RESULT=` 路径。
- `handoff init` 把 `handoff-ds`、`handoff-gemini`、`handoff-opus` 三个 TOML
  硬链接到 `~/.codex/agents/`，不再向 `~/.codex/skills/` 安装 handoff skill。

Claude 的通用 backend skills 与 Codex custom agents 各自以 `handoff-ds` 为母版，
Makefile 在开发阶段生成 Gemini/Opus 变体。带 Pro/Fast 专属协议的
`handoff-codex/SKILL.md` 单独维护。发布包携带生成后的完整文件；初始化阶段只负责
链接，不需要模板引擎。

## RESULT= 协议

handoff 与 AI 调用者之间的交互只靠一行文本：

```
RESULT=~/.handoff/tasks/hd-0611-03.result.md
```

这行同时编码了两条信息：

1. **结果文件路径**——完成后读它就拿到最终结论
2. **run_id**（`hd-0611-03`）——文件名主干去掉 `.result.md` 就是 run_id，它是续接的稳定句柄

协议约定：
- `RESULT=` 在任务启动时**立刻**打印到 stdout 和 stderr——调用者不等任务完成就能拿到路径
- stderr 持续输出进度（带时间戳），供人工观看或诊断
- stdout 在任务完成后打印最终结果正文（普通 shell 用户直接看到结果；AI 调用者应忽略 stdout 正文，只读 `.result.md`）
- 进度同时落盘到 `.out.txt`（与 `RESULT=` 路径同名，后缀换 `.out.txt`）
- 输入落盘到 `.prompt.txt`

这个极简协议让 handoff 能对接任何能执行 shell 命令的 AI 平台——skill 或 custom agent 只需确定结果路径，其余全部交给文件系统。

## codex 集成

handoff 的 codex backend基于对 `codex-cli 0.139.0` 的实测调研。关键结论：

### 事件流

`codex exec --json` 输出 JSONL 事件流。handoff 关心三类：

| handoff 信号 | codex 事件 |
| --- | --- |
| `session(id)` | `thread.started.thread_id` |
| `progress(text)` | `item.*` 中的 `agent_message`、`reasoning`、`command_execution` |
| `result(text)` | `turn.completed` 前最后一个 `agent_message` 的 `text` |

未知事件/类型直接跳过，容忍 minor schema drift。

### 会话续接

`codex exec resume <SESSION_ID> [PROMPT]` **不 fork**——返回相同的 `thread_id`。所以 handoff 的 session_id 稳定句柄策略对 codex 同样有效，不需要任何特殊处理。

### 自动执行

codex 默认需要确认才能执行命令。handoff 通过显式 flag 跳过所有交互：

- `--sandbox workspace-write` — 允许在工作区内编辑文件
- `--skip-git-repo-check` — handoff 可能在非 git 仓库目录工作
- `-C <cwd>` — 显式设定工作根目录

Resume 不能带 `--sandbox` / `-C`，继承原会话的设置。handoff 的 `continue_id_flags` 已正确区分两种路径。

### PTY

codex 不需要 PTY 包装——`codex exec --json` 本身就是为管道/非交互场景设计的，管道输出是干净的 JSONL。

### 认证

codex 使用自己的登录态（`~/.codex/auth.json` 或 `OPENAI_API_KEY`）——handoff 对 codex 型 backend 不设 `ANTHROPIC_*` 环境变量，也不跑 token 占位符检查。

### 流解析器

`CodexStreamParser`（`cli/stream.py`）实现了上述事件映射。codex 路径的详细启动配置见 `cli/backend_types.yaml` → `types.codex`。
