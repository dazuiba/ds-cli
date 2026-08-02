<div align="center">
<img src="https://raw.githubusercontent.com/dazuiba/handoff/main/docs/assets/handoff-hero.jpg" width="100%" alt="hero">

# 有了 **Handoff**，你手里的几个 coding agent 终于可以互相协作了。




| coding agent | → 派给 | 为什么 |
| :-- | :-- | :-- |
| Claude Code / Codex | **DeepSeek** | 执行性工作又快又便宜，旗舰额度留给决策 |
| DeepSeek | **Codex / Opus** | 难题借个脑子，答案带回当前会话 |

不用切来切去，也不丢上下文。

[English](README.md) · **简体中文**

</div>

## 为什么需要 handoff

如果你同时用着几家 coding agent，这些场景你一定眼熟：

- 💸 **「Claude / Codex 订阅：$20 的量太少不经用，$100 的又太贵用不起」**<br>
  — 只需一句：*「把这个任务交给 `/handoff-ds` 做」*。执行性工作 DeepSeek 干得又快又便宜，旗舰额度留给决策。
- 🤔 **「DeepSeek 干活时卡住了，想听听 Codex 的意见」**<br>
  — 只需一句：*「问问 `/handoff-codex` 的意见」*。不用开新终端、复述半天背景，答案带回当前会话。
- 🔁 **「想接着上次派出去的活儿继续」**<br>
  — 只需一句：*「接着刚才那个 `/handoff-ds` 的会话继续」*。之前改过的文件、读过的代码、得出的结论全都还在。
- 🔄 **「想换个模型干活，就得重开会话、重述一遍背景」**<br>
  — 不用换。你始终留在熟悉的会话里，handoff 在中间转交任务、拿回结果。

**算笔账就明白了**：写代码、跑测试这类事务性工作，DeepSeek V4 不输 Sonnet 级模型，价格只有零头。真正稀缺、值得付订阅费的，是顶端那一两个模型（Opus / GPT-5.5）的判断力。

| 方案 | 相对单价（干同样的活） |
| --- | --- |
| Claude Sonnet | 1×（基准） |
| DeepSeek 官方 API | **1/3** |
| [OpenCode Go](https://opencode.ai/go?ref=JC54498ZNF)（含 DeepSeek V4） | **1/18** |

旗舰模型负责沟通、拆解、验收；执行全部 handoff 出去——**$20 的订阅指挥 $5 的算力，干出 ~$200 的活**。这就是 handoff 的全部用法：在你的 agent 会话里说一句话。

## 快速开始

### 1. 安装

```bash
uv tool install handoff-cli
handoff init        # 初始化配置，链接 Claude skills / Codex custom agents
uv tool upgrade handoff-cli   # 更新到最新版
```

### 2. 配 token

opus / codex 走你本机的 claude / codex 登录态，零配置；**只有 DeepSeek 需要填一个 token**。

DeepSeek 算力推荐走 [OpenCode Go 套餐](https://opencode.ai/go?ref=JC54498ZNF)（单价最低，含 DeepSeek V4）。拿到 key 后，编辑 `~/.handoff/config.yaml`，只改 `ANTHROPIC_AUTH_TOKEN` 这一行：

> ⚠️ **OpenCode Go 用户**：需要先搭一个本地代理才能配合 Claude Code 使用。详见 **[routatic/proxy](https://github.com/routatic/proxy)**。

```yaml
# ~/.handoff/config.yaml —— handoff init 帮你生成
backends:
  deepseek:                          # ← 第一个 = 默认
    type: claude
    model: deepseek-v4-flash
    pro_model: "deepseek-v4-pro[1m]"
    env:
      ANTHROPIC_BASE_URL: https://api.deepseek.com/anthropic
      ANTHROPIC_AUTH_TOKEN: "sk-..."  # ← 改这里。本地代理设置见 https://github.com/routatic/proxy
      ANTHROPIC_MODEL: "{model}"

  opus:                              # 本机 claude 登录态，零配置
    type: claude
    ...
  codex:                             # 本机 codex 登录态，零配置
    type: codex
    ...
```

### 3. 派第一个活

回到 Claude Code，对它说：

> 制定计划，交给 `/handoff-ds` 执行。

任务进入后台执行，不阻塞你的会话；完成后 agent 自动读取结果、向你汇报。

### 4. 能派给谁

| 你怎么说 | 从 | 派给 | 适合 |
| --- | --- | --- | --- |
| `/handoff-ds` | Claude Code | DeepSeek V4 | 写代码、跑测试、重构、批量修改等执行性工作 |
| `handoff-ds`（custom agent） | Codex | DeepSeek V4 | 同上——你人在 Codex 里时走这条 |
| `/handoff-gemini` / `handoff-gemini` | Claude Code / Codex | Gemini | 交给 Gemini 执行或调查 |
| `/handoff-codex` | Claude Code | Codex (GPT-5.5) | 复杂推理、第二意见、疑难调试 |
| `handoff-opus`（custom agent） | Codex | Claude Opus | 需要顶级模型出马的关键决策 |

> Codex 里没有 slash 命令，直接提到同名 custom agent：说「让 `handoff-ds` 执行上述任务」即可。

### 5. 盯进度 / 看历史

在 Claude Code 里展开那条后台 shell，实时进度流就在那里——走 shell view，不烧主会话上下文。想单独浏览历史、实时跟踪，用 `handoff list` 和 `handoff tail`（详见下方 FAQ）。

## FAQ

<details>
<summary><b>怎么看任务列表、盯某条任务的进度？</b></summary>

<br>

派发和续接是 AI 的事（背后是 `handoff run` / `handoff resume`）；下面这两个命令给你——看列表、盯进度：

<table>
<tr>
<td width="50%" valign="top">

**`handoff list` / `handoff ls`** — 交互式 TUI，浏览全部历史任务。看 prompt 全文、实时状态、最终结果；选中按 `O` 直接把那次会话重新加载进来接着聊。

</td>
<td width="50%" valign="top">

**`handoff tail <run-id>`** — 实时跟踪某条任务的输出流，相当于盯着它干活。

</td>
</tr>
<tr>
<td valign="top">

<!-- docs/assets/list-tui.jpg — 建议 ~480 宽 — TUI 列表 + 详情视图，圈出 G/C 快捷键 -->
<img src="https://raw.githubusercontent.com/dazuiba/handoff/main/docs/assets/list-tui.jpg" width="100%" alt="handoff list 交互式 TUI">

</td>
<td valign="top">

<!-- docs/assets/tail.jpg — 建议 ~480 宽 — handoff tail 实时输出流 -->
<img src="https://raw.githubusercontent.com/dazuiba/handoff/main/docs/assets/tail.jpg" width="100%" alt="handoff tail 实时跟踪">

</td>
</tr>
</table>

</details>

<details>
<summary><b>能同时派发多个任务吗？</b></summary>

<br>

可以。在同一条消息里让 agent 派出多个任务，各自独立执行、独立完成通知，互不干扰。

<!-- docs/assets/parallel.jpg — 建议 621 宽 — 同一条消息派发 2~3 个后台任务，各自拿到不同 RESULT= 路径 -->
<img src="https://raw.githubusercontent.com/dazuiba/handoff/main/docs/assets/parallel.jpg" width="621" alt="并行派发多任务">

</details>

<details>
<summary><b>没有 uv / 想从源码装？</b></summary>

<br>

`pipx install handoff-cli` 或 `pip install handoff-cli` 同样可用。源码安装：

```bash
git clone https://github.com/dazuiba/handoff && cd handoff
uv tool install -e .
handoff init
```

</details>

<details>
<summary><b>怎么加一个自定义后端 / env 块怎么写？</b></summary>

<br>

`backends` 下再加一项即可，任何 Anthropic 兼容端点都行：

```yaml
backends:
  kimi:
    type: claude
    model: kimi-k3
    env:
      ANTHROPIC_BASE_URL: https://api.moonshot.cn/anthropic
      ANTHROPIC_AUTH_TOKEN: "${MOONSHOT_API_KEY}"
      ANTHROPIC_MODEL: "{model}"
```

env 块完全由你定义——你设的每个 key=value 都会在拉起 CLI 前导出为环境变量。`{model}` 替换为解析出的模型名，`${ENV_VAR}` 从当前 shell 展开。运行 `handoff env` 看各路径在哪。完整细节见 **[配置文档 →](docs/configuration.zh-CN.md)**。

</details>

<details>
<summary><b>它到底是怎么工作的？</b></summary>

<br>

1. 你的 agent 把任务整包交给 handoff，**后台执行**，会话不阻塞。
2. handoff 在独立上下文里拉起对应的 CLI（`claude -p` / `codex exec`），完整输出流式落盘。
3. 主会话只拿到一行 `RESULT=<结果文件路径>`；执行进度打在后台 shell view，**不进**主会话上下文。
4. 完成后 agent 收到通知，读取结果文件，向你汇报。
5. `RESULT=` 路径同时是这个会话的稳定句柄——之后每轮续接都指向同一个会话。

</details>

**更多文档**

- **[命令参考 →](docs/cli-reference.zh-CN.md)** — `run` / `resume` / `list` / `tail` / `env` / `init` 全部用法，run id 编码与落盘文件布局。
- **[配置文档 →](docs/configuration.zh-CN.md)** — 机制与数据两层、env 块、`${ENV}` 插值、include、自定义后端。
- **[设计说明 →](docs/design.zh-CN.md)** — 为什么 Claude Code 使用 skill、Codex 使用 custom agent，以及 RESULT= 协议细节。
