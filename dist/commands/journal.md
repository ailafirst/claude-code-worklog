---
description: 记录或回顾跨 session 工作日记（capture / threads / rollup）
---

用 `$ARGUMENTS` 区分三种模式。脚本路径：`~/.claude/skills/journal/journal.py`。
Windows 本机用 `py` 替代 `python3`。**collect 与 append 必须在当前工作 git 仓目录下运行。**

---

## 参数为空 → capture 本次 session

1. **取 git 原料**（自动合并 pending snapshot + session notes）

   ```
   python3 ~/.claude/skills/journal/journal.py collect
   ```

   输出 JSON 含 `commits / files_changed / stats / uncommitted`。
   若有 `from_snapshot: true`，说明用的是 SessionEnd 时保存的快照（更准确，优先使用）。
   若有 `session_notes`，说明用户用 `note` 记录过想法，一并读入。

2. **写条目**（本 skill 的灵魂）

   按 `templates/entry.md` 填五个槽位 + frontmatter。
   commit 只引 hash，正文重在"为什么 / 死路 / 卡点 / 下一步"，不复述 diff。
   `head` 留空，append 会自动回填。

3. **落盘**

   ```
   python3 ~/.claude/skills/journal/journal.py append <<'EOF'
   <整块 session 块，含 ## 标题与 frontmatter>
   EOF
   ```

4. **回显**：写到了哪个文件、记了哪些 thread。

---

## `$ARGUMENTS` 是 `threads` → 回顾 open thread

```
python3 ~/.claude/skills/journal/journal.py threads
```

把表格用一两句话点评：哪些 thread STALE（建议先收）、哪些最近活跃。不要复述整张表。

---

## `$ARGUMENTS` 是 `rollup` → 生成本周蒸馏

1. **收集本周素材**

   ```
   python3 ~/.claude/skills/journal/journal.py rollup
   ```

   输出本周所有日条目的原文（`第 NN 周：YYYY-MM-DD ~ YYYY-MM-DD`）。

2. **蒸馏**：阅读原文，生成结构化的周 rollup，格式：

   ```markdown
   # Week NN · YYYY-MM-DD ~ YYYY-MM-DD

   ## 本周推进了什么
   - ...

   ## 关键决策与权衡
   - ...

   ## 还开着的 thread
   - thread-name: 状态一句话

   ## 下周优先
   - ...
   ```

3. **落盘**

   ```
   python3 ~/.claude/skills/journal/journal.py rollup --save
   ```

   从 stdin 读刚才写好的 rollup，写入 `~/.claude/journal/YYYY/week-NN.md`。

4. **回显**：告知写入路径。

---

$ARGUMENTS
