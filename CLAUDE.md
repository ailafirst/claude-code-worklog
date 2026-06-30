# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个 repo 是什么

一个已实现、并打包成**标准 Claude Code 插件**的 worklog skill：名为 `journal` 的 skill（纯 Python 3 引擎 `journal.py` + `SKILL.md` + 内置 hook），外加一套端到端评测 `benchmark/`。原始规格见 `journal-skill-spec.md`。

**分发方式 = plugin + marketplace（不再用复制脚本，install.py 已删）：**

- 插件根 = `dist/`，清单 `dist/.claude-plugin/plugin.json`（插件名 `worklog`）。
- skill → `dist/skills/journal/`（`SKILL.md`、`journal.py`、`templates/entry.md`、`test_journal.py`）。
- 内置 Stop hook → `dist/hooks/hooks.json`（会话结束自动 `snapshot`）。
- 仓库根 `.claude-plugin/marketplace.json` 把 `dist/` 登记为单插件 marketplace。
- 用户装法：`/plugin marketplace add ailafirst/claude-code-worklog` → `/plugin install worklog@claude-code-worklog`；调用名 **`/worklog:journal`**。
- skill 运行时写入的数据 → `~/.claude/journal/YYYY/MM/YYYY-MM-DD.md`（落点与插件位置无关，可用 `JOURNAL_ROOT` 覆盖）。

**插件内寻址用 `${CLAUDE_PLUGIN_ROOT}`**——Claude Code 在 skill 正文与 hook 命令里就地替换为插件安装绝对路径，别再写死 `~/.claude/skills/...`。本开发机是 Windows + PowerShell，解释器用 `py`（`python3`/`python` 是失效 stub）；hook 命令以 `py … || python3 …` 兜底跨平台。

## 核心立意（构建前必须内化）

**日记记的是 git 丢掉的东西，不是 git 已经记下的东西。** `git log` 已经说了"改了什么"；这个 skill 补的是 git 拿不到、且 session 一结束就蒸发的信息：为什么这么改、试过哪条死路、卡在哪、下次接着干什么。

任何把 commit 记录换行转写成日记正文的实现都算失败 —— 这是判定一切功能的总闸。

## 不可协商的约束

- **纯 Python 3 标准库**，零 pip 依赖（含 frontmatter 解析也不用 PyYAML，格式自控）。
- **存储全是纯 markdown**：不引数据库，不引只有本脚本能解析的 JSON。脱离 skill 也能 `grep` / `diff` / 直接读。
- 引用提交**只写 commit hash + 改动文件名**，绝不贴整段 diff。
- 本次明确**不做**：周/月 roll-up、SessionEnd hook 自动捕获、stale 状态机、分析仪表盘（都是 v2，但 §4 存储布局要给它们预留位置）。

## 职责划分（工程化核心）

确定性的事进脚本，需要判断的事留给运行时的模型：

- **进脚本**：日期→路径解析、建目录、文件 append、frontmatter 读写 round-trip、git delta 计算、扫描聚合 thread 活跃度。
- **留给模型**：把一次 session 写成高信号条目、抽取关键决策/死路/卡点/open thread、判断哪些 thread 停滞。

凡日期算术、路径拼接、frontmatter 解析这类"不该每次重新推导"的，全部沉进脚本。

## 脚本 CLI 契约（`journal.py`，单入口 + 子命令）

根目录默认 `~/.claude/journal/`，可用环境变量 **`JOURNAL_ROOT` 覆盖**（测试用临时目录靠它）。

- `collect [--since REF]` —— 采集 git 原料，输出 JSON 到 stdout。`--since` 缺省按序回退：当天日文件最后一个 session 块的 `head` → 否则 `git log --since=midnight`。非 git 目录要输出 `commits: []`、`branch: null`，**不崩溃**。
- `append [--date DATE]` —— 从 **stdin** 读入一个完整 session 块（含 frontmatter），解析日期（`--date` > frontmatter `date` > today）→ 算路径 → 建目录 → 文件不存在先写 `# YYYY-MM-DD` 头 → append。入库前最小校验：`date` 合法、`project` 非空、`threads` 是 list；失败则 stderr 报错 + 非零退出（这是内置 verify）。`head` 最终要落盘以支撑下次 `--since`。
- `threads [--stale-days N]` —— 遍历 `JOURNAL_ROOT/**/YYYY-MM-DD.md`，聚合所有块的 `threads`，按最近活跃倒序出表（出现次数 / 首次 / 最近 / 距今 / STALE 标记）。`--stale-days` 默认 7。
- `path [--date DATE]` —— 打印解析出的日文件路径（调试用）。
- 建议补 `selftest` 子命令：用 `JOURNAL_ROOT` 指临时目录，把验收清单第 3–6 条跑成可重复的 verify。

## 文件格式（脚本与模型都得守）

一个日文件 = 若干 **session 块**按时间 append。每块固定结构：

- `## HH:MM · session`（H2，给人浏览的锚）
- 紧跟一个 fenced `---...---` frontmatter，是**机器读取的唯一真相源**。字段：`date`(必填)、`project`(必填)、`threads`(list,可空)、`blockers`(list,可选)、`head`(脚本回填的 HEAD sha)、`next`(list,可选)。
- 正文五个固定槽位：做成了什么 / 关键决策·为什么 / 卡点·困惑 / 下次 TODO / 碰到的 open thread。**质量优先于完整**，空槽位写"无"。
- 一个文件有多个 frontmatter 块，**parser 必须支持**：单独成行 `---` 开启、下一个单独成行 `---` 关闭，其后到下一个 frontmatter 之前为该块正文。

## 验证（目前还没有可跑的命令 —— 脚本尚未实现）

构建后，在一个**真实 git repo 内**逐条过 `journal-skill-spec.md` §10 的验收清单，全绿才算 MVP 完成。最关键的几条：

- `python3 journal.py --help` 可用，全程零额外依赖。
- 连续两次 `append` 到同一天文件后，仍能被 parser 正确拆成两个独立 session 块（多 frontmatter）。
- frontmatter round-trip：脚本写入的能被自己读回，字段不丢不乱。
- `append` 在 `date` 缺失或 `threads` 非 list 时报错并非零退出。
- `grep -r leakage-audit ~/.claude/journal` 能直接命中（数据确为纯 markdown）。

## 建议构建顺序

frontmatter 读写 + 路径/目录逻辑（最底层，先用临时目录测通 round-trip）→ `append`（含校验）→ `collect`（git delta）→ `SKILL.md` + `templates/entry.md` + `hooks/hooks.json`（走通端到端 capture）→ `threads` 聚合 → `selftest` / 验收清单逐条过。每步独立可用，做完即可自己上手。

完整细节、JSON 输出形状、SKILL.md 触发条件与 capture 工作流，均以 `journal-skill-spec.md` 为准。
