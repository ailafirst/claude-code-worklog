# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个 repo 是什么

一个已实现、并打包成**标准 Claude Code 插件**的 worklog skill：名为 `journal` 的 skill（纯 Python 3 引擎 `journal.py` + `SKILL.md` + 内置 hook），外加一套端到端评测 `benchmark/`。原始规格见 `journal-skill-spec.md`（历史设计文档，规格中列为 v2/非目标的 `note`/`snapshot`/`rollup`/`selftest` 目前均已实现，以代码为准）。

**分发方式 = plugin + marketplace（不用复制脚本，install.py 已删）：**

- 插件根 = `dist/`，清单 `dist/.claude-plugin/plugin.json`（插件名 `worklog`）。
- skill → `dist/skills/journal/`（`SKILL.md`、`journal.py`、`templates/entry.md`、`test_journal.py`）。
- 内置 Stop hook → `dist/hooks/hooks.json`（会话结束自动 `snapshot`，把当时的 git delta 存成 pending 快照）。
- 仓库根 `.claude-plugin/marketplace.json` 把 `dist/` 登记为单插件 marketplace。
- 用户装法：`/plugin marketplace add ailafirst/claude-code-worklog` → `/plugin install worklog@claude-code-worklog`；调用名 **`/worklog:journal`**。
- skill 运行时写入的数据 → `~/.claude/journal/YYYY/MM/YYYY-MM-DD.md`（落点与插件位置无关，可用 `JOURNAL_ROOT` 覆盖）。

**插件内寻址用 `${CLAUDE_PLUGIN_ROOT}`**——Claude Code 在 skill 正文与 hook 命令里就地替换为插件安装绝对路径，别再写死 `~/.claude/skills/...`。本开发机是 Windows + PowerShell，解释器用 `py`（`python3`/`python` 是失效 stub）；hook 命令与文档示例都以 `py … || python3 …` 兜底跨平台。

## 常用命令

全程纯 Python 3 标准库，无需 `pip install`。

```bash
# 引擎单元测试（hermetic，走临时 JOURNAL_ROOT，不碰真实数据）
py dist/skills/journal/test_journal.py         # 全跑
py dist/skills/journal/test_journal.py collect # 只跑名字匹配 "collect" 的组
py dist/skills/journal/test_journal.py -v      # 详细输出每条断言

# 脚本自带 hermetic 自检（spec §10 第 3-6 条的可重复 verify）
py dist/skills/journal/journal.py selftest

# 端到端集成测试（固定 git 数据 + 真实 LLM，需要 env.txt 或环境变量凭据）
py test_e2e.py

# benchmark：13 个真 git 夹具的确定性层自检（不调 LLM）
py benchmark/build_case.py --all
py benchmark/build_case.py 02-dead-ends --collect   # 单个 case，打印其 collect JSON

# benchmark：跨月/跨年边界压力测试（threads 聚合 + rollup 周边界，纯确定性层，复用 13 个 case 的真实条目正文）
py benchmark/stress_multiday.py

# benchmark：端到端 LLM 评测（机器校验层 + LLM 裁判层）
py benchmark/run_bench.py --list-models
py benchmark/run_bench.py 02-dead-ends --dry-run          # 只打印 subject prompt，不调 API
py benchmark/run_bench.py --all --model <id>              # 全量，输出 benchmark/.work/report-*.{json,md}
py benchmark/run_bench.py --all --model <id> --no-judge   # 只跑机器层，省 token

# 交叉裁判（换一个 judge 模型重评已有报告，检验裁判是否过宽）
py benchmark/rejudge.py benchmark/.work/report-<ts>.json <judge_model>

# 插件清单/hook 校验（本地免安装试用）
claude --plugin-dir ./dist       # 直接加载本目录插件，/reload-plugins 热重载
claude plugin validate ./dist    # 校验 plugin.json / SKILL.md / hooks.json
```

**LLM 端点凭据**（`test_e2e.py`、`benchmark/run_bench.py`、`benchmark/rejudge.py` 都要用）：环境变量 `BENCH_BASE_URL` / `BENCH_API_KEY`（可选 `BENCH_MODEL`），否则回退读仓库根 `env.txt`（第一行 base_url，第二行 api_key）。`env.txt` 已被 `.gitignore` 排除，API key 全程不打印、不入报告/日志。若被测端点是**推理模型**（响应带 `reasoning_content`），思考会占用 `max_tokens` 预算，预算不够时 `content` 为空——`run_bench.py` 默认 `--max-tokens 6000` 并在 `content` 空时回退读 `reasoning_content`。

跑单个 benchmark case 前建议先 `build_case.py <id>`（或 `--all`）把夹具物化到 `benchmark/.work/`（gitignored，可随时重建）。

## 架构

### 两层职责划分（贯穿整个仓库的核心设计）

- **确定性层**（`journal.py`，可脚本验证）：日期→路径解析、frontmatter 读写 round-trip、git delta 计算（含 `--since` 回退链）、多 session 块解析、thread 活跃度聚合、pending snapshot / session notes 暂存区的读写。
- **判断层**（运行时模型，`SKILL.md` 驱动）：把一次 session 写成高信号条目、抽取关键决策/死路/卡点/open thread、synthesize 周 rollup、判断哪些 thread 停滞。

`benchmark/` 的整个评测设计就是在分别校验这两层：机器校验层复用真实 `journal` 引擎（能否解析成单块、frontmatter 是否过 `append` 校验、threads 是否精确命中、是否误贴 diff）；LLM 裁判层评判语义质量（死路/决策理由是否写全、thread 标签是否漂移）。见 `benchmark/README.md`。

### 数据流（capture 一次 session 的完整链路）

1. `journal.py collect` 采集 git 原料 → JSON 到 stdout。优先用 Stop hook 保存的 pending snapshot（`~/.claude/journal/.pending-collect.json`，cwd 匹配且 ≤24h 有效，`--fresh` 跳过），否则实时跑 git；会自动合并 `.session-scratch` 里的随手记（`note` 子命令写入）。非 git 目录返回空结构、退出码 0，不崩溃。
2. 模型读 collect JSON + 自己的 session 记忆，按 `templates/entry.md` 的五个固定槽位写一个 session 块（frontmatter 留空 `head`，由 append 回填）。
3. `journal.py append` 从 **stdin**（唯一入口，模型不自己拼路径/写文件）读入该块：解析日期 → 建目录 → 校验 frontmatter（`date`/`project`/`threads` 类型）→ append 到 `YYYY/MM/YYYY-MM-DD.md`→ 成功后清空 session notes 暂存区。校验失败 stderr 报错 + 非零退出。

### 文件格式（`journal.py` 与 `SKILL.md` 都必须遵守）

一个日文件 = 若干 **session 块**按时间顺序 append。每块：

- `## HH:MM · session`（H2 锚，供人浏览/供 parser 切块——`split_session_blocks` 先按 H2 分块，再在块内取第一个 `---...---` 作为 frontmatter，不会被正文里的水平线带偏）。
- frontmatter 字段顺序 `date, project, threads, blockers, head, next`（`FM_KEY_ORDER`）；`date`/`project` 必填，`threads` 必须是 list（可空），`blockers`/`next` 若存在必须是 list。列表输出一律规范化成合法 JSON 数组（`dump_frontmatter`），但解析时宽容裸词写法（`_parse_list` 先试 JSON 再退回逗号切分）。
- 正文五个固定槽位：做成了什么 / 关键决策·为什么 / 卡点·困惑 / 下次 TODO / 碰到的 open thread。空槽位写"无"，质量优先于完整。

### 暂存机制（两个隐藏文件，均在 `JOURNAL_ROOT` 下）

- `.pending-collect.json`：Stop hook 触发 `snapshot` 子命令写入，记录 session 结束那一刻的 git delta（连同 cwd + 时间戳），解决"session 结束后 collect 却要事后重算、git HEAD 可能已经变了"的问题。
- `.session-scratch`：`note` 子命令的随手记暂存区，session 中途积累，下次 `collect` 自动带入 JSON 的 `session_notes` 字段，`append` 成功后清空。

## 核心立意（构建前必须内化）

**日记记的是 git 丢掉的东西，不是 git 已经记下的东西。** `git log` 已经说了"改了什么"；这个 skill 补的是 git 拿不到、且 session 一结束就蒸发的信息：为什么这么改、试过哪条死路、卡在哪、下次接着干什么。

任何把 commit 记录换行转写成日记正文的实现都算失败 —— 这是判定一切功能的总闸。

## 不可协商的约束

- **纯 Python 3 标准库**，零 pip 依赖（含 frontmatter 解析也不用 PyYAML，格式自控）。
- **存储全是纯 markdown**：不引数据库，不引只有本脚本能解析的 JSON。脱离 skill 也能 `grep` / `diff` / 直接读。
- 引用提交**只写 commit hash + 改动文件名**，绝不贴整段 diff。
- Windows 全链路显式 `reconfigure(encoding="utf-8")`（stdin/stdout/stderr）——不这么做中文在 cp936 下会静默乱码，这是本机踩过的坑，新脚本照抄这个 header。

## 建议阅读顺序

`journal.py` 源码按分区注释组织（核心层解析 → 路径/校验 → git 封装 → 暂存区 → collect 采集核心 → 文件 I/O → 各子命令 → CLI 入口），从上往下读即为依赖顺序。改动前先跑 `py dist/skills/journal/journal.py selftest` 和 `py dist/skills/journal/test_journal.py` 建立基线。`SKILL.md` 是运行时模型看到的唯一提示词，改了 `journal.py` 的字段/子命令契约要同步改它。完整历史设计动机、验收清单（spec §10）见 `journal-skill-spec.md`。
