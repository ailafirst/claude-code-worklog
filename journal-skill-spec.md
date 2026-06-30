# Claude Code 日记 Skill —— 实现规格（MVP）

> 读者是 Claude Code。这是一份实现任务说明书，不是最终交付物本身。照此构建一个名为 `journal` 的 skill：一组脚本 + 一个 `SKILL.md` + 一个 slash command。先把"核心立意"和"非目标"读完再动手——它们决定了这个 skill 是有用还是无用。开始前请先确认本机 Claude Code 当前版本的 skill 与 slash command 发现路径（通常是 `~/.claude/skills/` 与 `~/.claude/commands/`），以实际为准。

---

## 1. 核心立意（必须内化）

**日记要记的是 git 丢掉的东西，不是 git 已经记下的东西。**

`git log` 已经告诉你"改了什么"。本 skill 要补的是 git 拿不到、且 session 结束后蒸发最快的信息：为什么这么改、试过哪条死路、卡在哪、下次接着干什么、还有什么没想明白。

任何把 `git log` 换行转写一遍当日记的实现都是失败的。所有功能从这条立意派生。

---

## 2. 非目标 / 明确不要做

- ❌ 不要把 git 提交记录直接转写成日记正文（git 能自动推导的让脚本去拿，正文只放推理）。
- ❌ 不要引入数据库或只有本脚本能解析的 JSON 存储。数据必须是纯 markdown，脱离本 skill 也能 grep / diff / 直接读。
- ❌ 不要贴整段 diff。只引用 commit hash + 改动文件名。
- ❌ 不要引入重依赖。脚本只用 **Python 3 标准库**（理由：装了 python3 的任何环境零摩擦即用；frontmatter 格式受我们自己控制，无需 PyYAML）。
- ❌ 本次不做：周/月 roll-up、SessionEnd hook 自动捕获、stale thread 的状态机、时间/精力分析仪表盘。这些是 v2，但存储布局要为它们预留位置（见 §4）。

---

## 3. 职责划分（工程化的核心，务必划清）

| 确定性 → 进脚本 | 需要判断 → 交给模型（Claude Code 运行时） |
|---|---|
| 日期 → 路径解析、目录创建、文件 append | 把一次 session 写成高信号条目 |
| frontmatter 的读 / 写 / round-trip | 抽取关键决策、死路、卡点、open thread |
| git delta 计算（commits / 改动文件 / 统计） | 判断哪些 thread 已经停滞 |
| 扫描所有日条目、聚合 thread 活跃度 | （v2）写蒸馏式 roll-up |

凡是日期算术、路径拼接、frontmatter 解析这类"不该每次重新推导"的事，全部进脚本。模型只做需要判断力的部分。

---

## 4. 存储布局（已锁定）

全局存储（工作天然跨多个 repo，跨项目主线才连得起来），用 `project` 字段区分来源：

```
~/.claude/journal/
  2026/
    06/
      2026-06-29.md        # 日文件：每次 capture 往里 append 一个 session 块
    # week-26.md           # v2 预留：周 roll-up
  # 2026-06.md             # v2 预留：月 roll-up
```

日文件名严格为 `YYYY-MM-DD.md`，脚本据此识别哪些文件是日条目。

---

## 5. 文件内格式约定（脚本与模型都必须遵守）

一个日文件由若干 **session 块**按时间 append 而成。每个 session 块结构固定：

```markdown
## 14:32 · session

---
date: 2026-06-29
project: hi-spiced
threads: [leakage-audit, replay-buffer]
blockers: [cairosvg-font-render]
head: abc1234
next: ["补被试级 split 校验", "重跑 cross-subject baseline"]
---

**做成了什么**
- 修正 cross-subject split，被试 ID 不再跨 train/test（commit abc1234）

**关键决策 / 为什么**
- 归一化统计量改为只在训练折上 fit，避免把测试分布泄漏进 scaler

**卡点 / 困惑**
- cairosvg 渲染中文字体 fallback 异常，图标题缺字

**下次 TODO**
- 把 leakage audit 接进 CI

**碰到的 open thread**
- leakage-audit：被试级已修，窗口重叠检测还没做
```

约定要点：

- `## HH:MM · session` 是给人浏览的导航锚（H2）。
- 紧跟的 `---...---` fenced 块是 **frontmatter**，是机器读取的唯一真相源。
- frontmatter 字段：`date`（必填）、`project`（必填）、`threads`（list，可空）、`blockers`（list，可选）、`head`（本次 capture 时的 HEAD sha，由脚本回填）、`next`（list，可选）。
- 正文五个固定槽位（做成了什么 / 关键决策·为什么 / 卡点·困惑 / 下次 TODO / 碰到的 open thread）。**质量优先于完整**：宁可三行有信息，不要十行套话。空槽位可写"无"。
- 一个文件里有多个 frontmatter 块，parser 必须支持。识别规则：单独成行的 `---` 开启 frontmatter，下一个单独成行的 `---` 关闭；其后直到下一个 frontmatter 块之前为该块正文。

---

## 6. 交付物清单

```
~/.claude/skills/journal/
  SKILL.md
  journal.py              # 单一入口 CLI（标准库）
  templates/entry.md      # §5 的正文模板（供模型填充）
~/.claude/commands/
  journal.md              # slash command：/journal 与 /journal threads
```

---

## 7. 脚本接口规格（`journal.py`）

单一入口 + 子命令。仅标准库。所有路径默认根 `~/.claude/journal/`，允许用环境变量 `JOURNAL_ROOT` 覆盖（便于测试）。

### `journal.py collect [--since REF]`
采集 git 原料，输出 JSON 到 stdout。`--since` 缺省时按序回退：当天日文件里最后一个 session 块的 `head` → 否则 `git log --since=midnight`。

输出形状：
```json
{
  "since": "abc1234",
  "head": "def5678",
  "branch": "main",
  "commits": [{"sha": "def5678", "subject": "fix subject leakage"}],
  "files_changed": ["src/split.py", "src/scaler.py"],
  "stats": {"files": 2, "insertions": 41, "deletions": 13}
}
```
非 git 目录时输出 `commits: []` 并在 `branch` 置 `null`，不报错退出。

### `journal.py append [--date DATE]`
从 **stdin** 读入一个完整 session 块（含 frontmatter）。职责：解析日期（`--date` > frontmatter `date` > today）→ 计算路径 → 建目录 → 文件不存在则写 `# YYYY-MM-DD` 文件头 → append 该块。
- 入库前做最小校验：`date` 存在且合法、`project` 非空、`threads` 为 list。校验失败 → stderr 报错 + 非零退出（这是内置 verify）。
- 若调用方未填 `head`，可结合 `collect` 的结果回填（实现细节自定，但 `head` 最终要落盘以支撑下次 `--since`）。

### `journal.py threads [--stale-days N]`
遍历 `JOURNAL_ROOT/**/YYYY-MM-DD.md`，解析所有 session 块的 frontmatter `threads`，聚合后输出表格到 stdout：

```
thread          出现  首次        最近        距今  状态
leakage-audit   4     2026-06-20  2026-06-29   0
replay-buffer   2     2026-06-22  2026-06-24   5
fonts-svg       1     2026-06-18  2026-06-18  11    STALE
```
按"最近活跃"倒序。`--stale-days` 默认 7，距今超过即标 `STALE`。（thread 的显式 closed 状态留给 v2，MVP 只做活跃度聚合。）

### `journal.py path [--date DATE]`
打印解析出的日文件路径（辅助 / 调试用）。

---

## 8. SKILL.md 要点

- **description / 触发条件要精准**：覆盖"记录本次 session / 记一下今天做了啥 / 我之前在搞什么来着 / 还有哪些没收尾 / 列一下 open thread"，避免对无关编码请求误触发。
- **capture 工作流**（写进 SKILL.md，指导运行时的模型）：
  1. 跑 `journal.py collect` 拿 git 原料；
  2. 结合**本次对话里的推理**（决策、死路、卡点、遗留），按 `templates/entry.md` 填充五个槽位，写好 frontmatter；
  3. 把整块经 stdin 交给 `journal.py append` 落盘；
  4. 向用户回显简短确认（写到了哪个文件、记了哪些 thread）。
- 明确提示模型：commit 只引 hash + 文件名，正文重在"为什么/卡点/下一步"，不要复述 diff。
- 按需加载（progressive disclosure）：模板、threads 说明等放独立文件，SKILL.md 主体保持精简。

---

## 9. Slash command（`commands/journal.md`）

- 无参数 `/journal` → 执行 §8 的 capture 工作流。
- `/journal threads` → 跑 `journal.py threads` 并把结果用一两句话点评（哪些线程停滞、建议先收哪个）。
- 用 `$ARGUMENTS` 区分两种模式。

---

## 10. 完成判定（Claude Code 自检清单）

实现后在一个真实 git repo 内逐条验证，全绿才算 MVP 完成：

1. `python3 journal.py --help` 可用，全程零额外 pip 依赖。
2. `journal.py collect` 在 git repo 内输出合法 JSON，正确反映最近改动；在非 git 目录不崩溃。
3. 走通一次 `/journal`：当天日文件被创建/追加，frontmatter 合法，正文含全部五个槽位，commit 以 hash 形式被引用而非整段 diff。
4. **连续两次 capture** append 到同一天文件后，文件仍可被 parser 正确拆成两个独立 session 块（多 frontmatter 解析正确）。
5. frontmatter round-trip：脚本写入的内容能被自己读回，字段不丢不乱。
6. `append` 在 `date` 缺失或 `threads` 非 list 时报错并非零退出。
7. `/journal threads` 能列出出现过的 thread、最近活跃日期与 stale 标记。
8. 数据全为纯 markdown，`grep -r leakage-audit ~/.claude/journal` 能直接命中。

建议在 `journal.py` 里附一个 `selftest` 子命令或独立测试，用 `JOURNAL_ROOT` 指向临时目录跑通第 3–6 条，作为可重复的 verify。

---

## 11. 建议实现顺序

1. frontmatter 读写 + 文件路径/目录逻辑（最底层，先用临时目录测通 round-trip）。
2. `append`（含校验）→ 能手工喂一个块落盘。
3. `collect`（git delta）。
4. `SKILL.md` + `templates/entry.md` + slash command → 走通端到端 capture。
5. `threads` 聚合。
6. `selftest` / 验收清单逐条过。

每一步独立可用，做完即可自己上手用，不必等全部完工。
