# journal benchmark

验证 `journal` skill 的**真实表现**，而不是纸面假设。每个 case 是一组
`(git 任务, session 上下文)`，覆盖一种真实开发场景与一个 skill 易踩的失败模式（trap）。

## 为什么这样设计

skill 分两层：

- **确定性层**（`journal.py`）：git delta、`--since` 回退、`uncommitted` 统计、frontmatter
  round-trip、thread 聚合。可被脚本直接验证。
- **判断层**（运行时模型）：把一次 session 写成**高信号**条目——只记 git 拿不到的东西
  （为什么这么改、试过哪条死路、卡在哪、下次第一步、open thread）。需要人/裁判评分。

所以每个 case 同时给出：

| 文件 | 喂给谁 | 作用 |
|------|--------|------|
| `case.json` | 构建器 | 元数据 + **git 任务**（init/commits/uncommitted/分支/session_notes），重放成真实仓库 |
| `context.md` | 被测模型 | **session 上下文**——git 看不到的推理 ground truth，连同 collect JSON 一起喂入 |
| `expected-entry.md` | 评分者 | 金标准日记条目 + 评分要点（高信号清单 + 该 case 的 anti-pattern） |

构建器把 `case.json` 的 commit 序列**真的重放进 git**，再跑真正的 `journal.py collect`，
因此 collect 输出是真的；判断层再拿 `collect + context.md` 让模型写条目，对照
`expected-entry.md` 评分。

## 目录

```
benchmark/
  build_case.py        夹具构建器（重放 git + 跑 collect + 自检 expect）
  run_bench.py         端到端评测（collect+context → 被测模型 → 机器校验 + 裁判评分）
  cases/<id>/
    case.json          元数据 + git 任务 + expect（确定性层断言）
    context.md         session 上下文（推理 ground truth）
    expected-entry.md  金标准条目 + 评分要点
  .work/<id>/          构建产物（repo/ + journal/ + collect.json），可重建，已 gitignore
  .work/report-*.{json,md}  评测报告（每次运行带时间戳）
```

## 用法

```
py build_case.py --all                 # 构建全部 + 核对每个 case 的 expect（确定性层自检）
py build_case.py 02-dead-ends --collect # 构建单个，打印它的 collect JSON
py build_case.py 09-continuation        # 只落夹具到 .work/，不跑 collect
py stress_multiday.py                  # 跨月/跨年边界压力测试：threads 聚合 + rollup 周边界（确定性层，见下）
```

### 跨月/跨年压力测试（stress_multiday.py）

12 个 case 只验证"单次 session 写得对不对"，不验证"攒了一两个月日记后，`threads`/
`rollup` 的日期算术还对不对"。`stress_multiday.py` 补这一块：复用 12 个 case 的金标准
条目正文 + 真实重放出的 commit head sha，循环铺满一段连续 45 天（2025-11-20 ~
2026-01-03，横跨 11/12 月末、且盖住 ISO 2026 年 W1 这条横跨上一年 12 月的边界），断言
`threads` 的 count/first/last/STALE 与独立算出的期望值一致，`rollup --week/--year` 只
收录该 ISO 周范围内的日文件（周边界用 `date.fromisocalendar` 独立计算，不复用
`journal.py` 自己的实现，避免自证）。只测确定性层，judgment 层质量仍由上面的
12 个 case + `run_bench.py` 覆盖。

`--all` 通过 = 确定性层在 12 个真实夹具上行为正确（commit 数、分支、uncommitted、
session_notes 合并、`--since` 续接都符合声明）。判断层另需人工/裁判按
`expected-entry.md` 评分。

## 端到端评测（run_bench.py）

把夹具变成可重复跑的端到端评测：**collect+context → 被测模型写条目 → 机器校验 + 裁判评分**。

```
py run_bench.py --list-models                  # 探测端点可用模型
py run_bench.py 02-dead-ends --dry-run         # 只构造并打印 subject prompt，不调 API
py run_bench.py 02-dead-ends --model <id>      # 单 case 端到端
py run_bench.py --all --model <id>             # 全量，输出 .work/report-*.{json,md}
py run_bench.py --all --model <id> --no-judge  # 只跑机器层（省 token）
```

**两段流程：**

1. **被测（subject）**：系统提示 = 真·`SKILL.md` 写作指南 + 真·`templates/entry.md`，
   保证测的就是线上 skill。用户提示 = 该 case 的 collect JSON + `context.md`。模型产出一个
   session 块。
2. **评分**，两层独立、互补：
   - **机器层**（确定性，复用真 `journal` 引擎）：能否解析成单块、frontmatter 是否过 append 校验、
     threads 是否丢线/是否精确命中、有 commit 时是否引真实 hash、是否粘了 diff/stats。
     解析+校验是**门槛**，不过则强制判负。
   - **裁判层**（LLM-as-judge，已强化）：用**结构化输出**（`response_format: json_schema` 严格模式，
     端点不支持时自动降档 `json_object`）强制裁判按固定 schema 作答，并走**强制评审顺序**：逐条核对
     必中项 → 逐条核对 anti-pattern → 单列 **thread 标签质量**维度 → 列**扣分账 deductions**（一处
     不足一条 `{reason, points}`）→ `score = 10 − Σpoints`（脚本侧按扣分账回算对齐，杜绝"列了一堆
     问题却仍打满分"）→ 给 verdict。两条 **few-shot** 合成样例锚定标度（一条垃圾条目扣到 fail、一条
     好条目仍因 thread 标签含糊扣 1 分），把"满分"压成稀缺。解析失败自动重采重试。

   两层都要，因为各抓各的：机器层抓结构硬伤（如 threads 留空、贴 diff），裁判层抓语义质量（如死路、
   决策理由是否写全）。**强化前** MiMo 裁判对所有条目一律给 10 分、零区分度；**强化后**同一批条目
   分数拉开（如 02/03/05 因 thread 标签漂移/信号不全被扣到 8–9），并与机器层在 thread 标签问题上
   达成一致——这正是双层的意义。

**凭据**：优先环境变量 `BENCH_BASE_URL` / `BENCH_API_KEY` / `BENCH_MODEL`，否则回退读
`../env.txt`（第一行 base_url、第二行 api_key）。**API key 全程不打印、不入报告。**

**报告**：`.work/report-<时间戳>.json`（含每个 case 的 collect、被测条目、机器校验明细、裁判 JSON——
裁判 JSON 带 `deductions` 扣分账、`thread_label_quality`、`score_self`「模型自报分」与对齐后 `score`）
与同名 `.md`（汇总表 + 逐 case 细节，逐条列出扣分账与 thread 标签判定 + 可折叠的被测条目）。

> 注意：被测端点若是**推理模型**（响应带 `reasoning_content`），思考会吃掉 `max_tokens` 预算，
> 预算不足时 `content` 会空。`run_bench.py` 默认 `--max-tokens 6000` 并在 content 空时回退读
> `reasoning_content`、报出 `finish_reason`。

## 12 个 case 与覆盖矩阵

| id | 场景 | git 形态 | 主要考点 | trap（易犯错） |
|----|------|---------|---------|---------------|
| 01-clean-feature   | 加分页 | 富（3 提交） | 基线：写出增量信息 | 把 commit 标题抄成正文 |
| 02-dead-ends       | 修内存泄漏 | 贫（1 提交） | **死路是一等公民** | 只剩 1 提交→丢掉 3 条死路 |
| 03-blocker-stuck   | flaky CI | 贫 + uncommitted | **卡点 + 下一步写具体** | 写成"有个 bug" |
| 04-non-git         | 写设计稿 | **无 git** | 空 git 仍照记 | "没 git，没啥可记" |
| 05-multi-thread    | 串台的一天 | 富（4 提交） | 多 thread 分离 | 揉成一坨 |
| 06-uncommitted-wip | 重构到一半 | **仅 uncommitted** | WIP/未提交也有价值 | "没提交→没啥可记" |
| 07-refactor-no-behavior | 大改名 | 富（大 diff） | 抓战略 why，不抄 diff | 列举所有改名文件 |
| 08-debug-investigation  | 线上排障 | **0 提交** | 纯调查的高价值 | git 没东西→空条目 |
| 09-continuation    | 接昨天的线 | 续接（`--since`） | 跨 session 续接 + 复用 thread | 从头重述，无连续性 |
| 10-low-signal      | 改版本号/错别字 | 富但琐碎 | **克制**，空槽写"无" | 把琐事吹成"洞见" |
| 11-decision-heavy  | 选状态管理 | spike+revert | 决策 + **被否的备选及理由** | 只记赢家，丢掉理由 |
| 12-notes-merge     | 中途随手记 | 富 + session_notes | 合并 `note` 进 next/open-thread | 无视 notes |

矩阵维度：git 富/贫/无/仅未提交/续接/琐碎，正交覆盖
死路·卡点·决策·续接·随手记·克制·多线程。

## 加新 case

1. 建 `cases/<id>/case.json`，至少含 `title/project/git/commits` 和 `expect`。
2. 写 `context.md`（git 看不到的推理）与 `expected-entry.md`（金标准 + 评分要点）。
3. `py build_case.py <id> --collect` 确认 collect 输出符合预期，补全 `expect` 字段。
4. `py build_case.py --all` 应全绿。

`case.json` 关键字段：`git`(`repo`|`none`)、`init/commits[]`（`message/files{}/delete[]/day/time`）、
`uncommitted`、`session_notes[]`、`prior_head_from`（`"init"` 或提交下标，预置续接 head）、
`expect`（`commit_count/branch/files_changed_min/uncommitted_min/has_session_notes/since_used`）。
`day` 为相对今天的天偏移（0=今天，-1=昨天），保证夹具可复现。
