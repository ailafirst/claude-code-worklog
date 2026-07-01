---
name: journal
description: >-
  捕获与回顾跨 session 的工作日记——记 git 留不住、session 一结束就蒸发的推理：
  为什么这么改、试过哪条死路、卡在哪、下次第一步、还有哪些 open thread。当用户说
  "记一下本次/今天做了啥""把这次 session 记下来""我之前在搞什么来着""还有哪些没
  收尾""列一下 open thread / 停滞的线程"时触发。仅在用户明确要记录或回顾工作时用；
  普通写代码、改 bug、解释代码、回答问题不要触发。
---

# journal — 跨 session 工作日记

## 立意（每次都要记住）

**日记记的是 git 丢掉的东西，不是 git 已经记下的东西。** `git log` 已经说清"改了
什么"；你只补 git 拿不到、session 一结束就蒸发的推理层：**为什么这么改、试过哪条
死路、卡在哪、下次第一步、还有哪些 open thread**。把 commit 换行抄一遍当正文 = 失败。

确定性的活（日期/路径、frontmatter 读写、git delta、thread 聚合）已经沉进
`journal.py`，你不要重算；你只做需要判断力的事——把一次 session 写成高信号条目。

## 触发入口

| 用户说 | 对应模式 |
|--------|---------|
| "记一下这次 session""日记" | `/worklog:journal` → capture |
| "open thread""线程停了没" | `/worklog:journal threads` |
| "周 rollup""总结这周" | `/worklog:journal rollup` |
| "随手记一下 …" | `! py … journal.py note -m "…"` |

## 参数路由

显式调用 `/worklog:journal` 时按 `$ARGUMENTS` 分流（模型也会按上表语义自动触发）：

- 空 → **capture** 本次 session（见下「capture 工作流」）
- `threads` → **回顾** open / 停滞线程（见「回顾工作流」）
- `rollup` → 生成**本周蒸馏**（见「周蒸馏工作流」）

**collect 与 append 必须在当前工作 git 仓目录下运行**，脚本才看得到 git、才能回填 `head`。

## capture 工作流（无参数）

**1. 取 git 原料**

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/journal/journal.py collect
```

拿到 JSON，可能带以下附加字段：

- `from_snapshot: true` — 用的是 SessionEnd 时保存的 pending 快照（比实时 git 更准确，
  因为它记录的是 session 结束那一刻的 delta，优先使用）。
- `session_notes` — 用户 session 中途用 `note` 记录的想法，合并进上下文。

非 git 目录返回空结构，退出码 0，照常写日记。

**2. 写条目（本 skill 的灵魂，见下「怎么写」）**

按 `templates/entry.md` 填五个槽位 + frontmatter。`head` 留空即可，append 会回填。

**3. 落盘——只能经 stdin 交给 append**

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/journal/journal.py append <<'EOF'
<这里放第 2 步写好的完整 session 块，含 ## 标题与 frontmatter>
EOF
```

**绝不自己拼路径、自己写文件。** append 是唯一真相源：解析日期、建目录、保证多块
可被正确再拆分，并在入库前校验 frontmatter。校验失败会打印原因并非零退出——按提示
改 frontmatter 重试，**别绕过直接写文件**。

append 成功后会自动清空 `session_notes` 暂存区（下次 collect 就不再带入了）。

**4. 回显**：写到了哪个文件、记了哪些 thread。

## 怎么写出高信号条目

对每条 commit 先问一句：**"git 已经能看到这条，那它看不到的是什么？"** 只写后者。

- **commit 只引 hash + 文件名**，绝不贴 diff。`stats`/`files_changed` 是 git 的活。
- **死路是一等公民**：试过什么、为什么放弃、排除了哪个假设——这是最省未来时间的信息。
- **卡点写具体**：卡在哪、还没定位到哪、下一步打算怎么验证，而不是"有个 bug"。
- **质量优先于完整**：宁可三行有信息，不要十行套话。空槽位写"无"，不要硬凑。
- **thread 用稳定短标签**（如 `leakage-audit`），跨天复用同一个名，threads 聚合才连得起来。
- **frontmatter `threads` ≠ 正文「碰到的 open thread」**：`threads` 是这次 session 涉及的
  **具名主题标签**，为的是以后能搜回来——哪怕这次已经收尾（比如一次性定位并修完的 bug），
  只要它值得起名字，就该打标；正文「碰到的 open thread」才专指**还没收尾**、下次要接着处理的。
  只有真正琐碎到不值得起名的改动（版本号、错别字）才把 `threads` 留空。

✅ 高信号：
> **关键决策 / 为什么**
> - 归一化只在训练折 fit：之前全量 fit 把测试分布泄漏进 scaler，cross-subject AUC 虚高 ~0.04（abc1234 改 src/scaler.py）
>
> **卡点 / 困惑**
> - cairosvg 渲染中文 fallback 缺字，还没定位是字体路径还是 cairo 版本

❌ 失败（git log 换行转写，零增量信息）：
> **做成了什么**
> - 改了 src/split.py (+41 -13)、src/scaler.py
> - 提交 abc1234 "fix subject leakage"

✅/❌ threads 该不该打（同一件事，两种结局都该打标）：
> 当天定位并修完的内存泄漏 → `threads: ["mem-leak"]` + 正文「碰到的 open thread」写"无"
> （已收尾，但主题值得留标签，方便以后搜「这类泄漏上次怎么修的」）
>
> ❌ 反例：因为"今天就修完了、没有遗留"就写 `threads: []`——
> 收尾状态只影响「open thread」槽位怎么写，不该影响要不要打标签。

## 回顾工作流（`threads`）

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/journal/journal.py threads
```

脚本输出按最近活跃倒序的表（出现次数 / 首次 / 最近 / 距今 / `STALE`）。你只做**解读**：
点名哪些线程停滞、建议先收哪个、哪些可能已悄悄完成该确认。不要复述整张表。

## 周蒸馏工作流（`rollup`）

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/journal/journal.py rollup
```

输出本周（周一到今天）所有日条目的原文。你阅读后合成一份结构化周 rollup，格式：

```markdown
# Week NN · YYYY-MM-DD ~ YYYY-MM-DD

## 本周推进了什么
- ...

## 关键决策与权衡
- ...

## 还开着的 thread
- thread-name: 当前状态一句话

## 下周优先
- ...
```

写好后经 stdin 落盘：

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/journal/journal.py rollup --save
```

写入 `~/.claude/journal/YYYY/week-NN.md`，回显路径。

## 随手记（session 中途）

session 过程中随时可用 `note` 记下稍纵即逝的想法：

```
! py ${CLAUDE_PLUGIN_ROOT}/skills/journal/journal.py note -m "某个绕过方案值得试"
```

也可从 stdin 传入多行内容。想法存到 `.session-scratch`，下次 `collect` 自动带入
`session_notes` 字段，模型写日记时可看到。落盘（append）后暂存区自动清空。

## SessionEnd 快照（自动，插件内置）

本插件已内置 Stop hook（`hooks/hooks.json`）：**启用插件即生效**，每次 Claude Code
会话结束自动运行 `journal.py snapshot`，无需手动改 `settings.json`。

snapshot 把当前 git delta 保存成 `.pending-collect.json`。下次 `collect` 若
working directory 匹配且快照不超过 24 小时，直接使用——即使 session 已结束、
git HEAD 已经移动，也能看到那次 session 的准确 delta。

hook 命令用 `py … || python3 …` 兜底：Windows 走 `py`，类 Unix 回退 `python3`。

## 边界与排错

- **非 git 目录**：collect 返回空，正常；推理照写。
- **补记过去某天**：给 append 加 `--date YYYY-MM-DD`。
- **append 报"校验失败"**：八成是 `threads` 没写成 list 或 `date` 格式错——改了重试。
- **Windows 本机**：`python3` 是失效的 WindowsApps stub，改用 `py`。
- **collect --fresh**：加此标志跳过 pending snapshot，强制实时采集。

## 渐进式细节（按需读，别预读）

- 正文模板与槽位语义 → `templates/entry.md`
- 脚本完整接口、JSON 形状、frontmatter 契约 → `python3 journal.py --help` 与源码
