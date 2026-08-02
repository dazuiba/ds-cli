# TODO

ds-cli → handoff 迁移的未完成项（2026-06-11 整理）。已完成的四阶段计划存档于 `plans/archive/handoff/`。

## 1. 发布

- [ ] `uv build && uv publish`（需 PyPI token；包名 `handoff-cli`，命令 `handoff`）
- [ ] 发布后核对：README 顶部两个 badge 渲染正常；干净机器 `uv tool install handoff-cli && handoff init` 全流程
- [ ] 旧 Homebrew tap 的 ds-cli formula 加 `deprecate!`，指向 PyPI 安装方式

## 2. README 截图（4 张，新拍，旧图全是 ds-cli 时代的）

占位注释里写了每张的内容要求与宽度，README.md 与 README.zh-CN.md 共用：

- [ ] `assets/claude-code.jpg`（720 宽）— 主演示：一句话派发 → 只回显 RESULT= → 读 .result.md 汇报
- [ ] `assets/list-tui.jpg`（~480）— TUI 列表 + 详情，圈出 G/C 快捷键
- [ ] `assets/tail.jpg`（~480）— tail 实时输出流
- [ ] `assets/parallel.jpg`（621）— 同一条消息派发多任务、各自 RESULT=

## 3. 本机切换与旧装置退役

现状（2026-06-11）：`~/.ds-cli` 已被迁移收编进 `~/.handoff`（历史数据完好）；全局 `handoff`
指向 `~/dev/github/handoff` clone 的 editable 安装；`~/.handoff/config.yaml` 被
init 重置为模板（旧 opencode 配置在 `config.yaml.bak`，需要的话手动合回，opencode 段落可参考
docs/configuration.zh-CN.md 的示例）。

- [ ] 把 opencode 配置从 `config.yaml.bak` 合回 `config.yaml`（或填 DEEPSEEK_API_KEY 走官方 API）
- [ ] 实战验证三个 skill：在 Claude Code 真实派发 `/handoff-ds`、`/handoff-gemini`、`/handoff-codex` 各一次
- [ ] Codex 侧实战验证 `handoff-ds`、`handoff-gemini`、`handoff-opus` 三个 custom agents（`~/.codex/agents/handoff-*.toml`）
- [ ] 验证交互式 codex 重开（`handoff open <codex-seq>` → `codex resume <id>`，从未人工测过）
- [ ] 退役死装置：`~/.claude/skills/ds-cli/`（其依赖的 `~/.ds-cli` 已不存在，skill 已不可用）、
  `~/bin/ds-cli`、旧 checkout `~/dev/github/ds-cli`（merge 后即冗余）；
  `acpx` / `headless` 两个 skill 与 handoff 功能重叠，考虑收敛

## 4. 文档遗留

- [ ] 英文文档缺口：README.md（英文主版）的"More"链接指向 `docs/*.zh-CN.md` 中文文档。决定翻译英文版 docs 还是在链接处注明 Chinese-only
- [ ] `docs/configuration.zh-CN.md`、`docs/design.zh-CN.md` 按"并列多维内容用 table 而非 section"标准复查（cli-reference 已按此重构）
- [ ] `configuration.zh-CN.md` system_prompt 一节仍提到死键名 `type_defaults`，措辞改为"机制层字段"

## 5. 近期要求的复查项（落地状态备忘）

- [ ] 「不对用户说"后端"」红线：当前全文档 0 命中；新增文案（截图说明、skill description 等）时保持
- [ ] `default_backend` 配置键已彻底移除（用户 config 出现会被警告忽略）；代码内部仍有 `Config.default_backend` 访问器（语义＝backends 第一个条目），名字如介意可重命名
- [ ] `~/.handoff/config.yaml.bak-pre-0.3` / `.bak-pre-0.4` 两个备份，确认稳定后删除
