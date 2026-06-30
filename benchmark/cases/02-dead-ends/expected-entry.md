# 金标准条目 — 02-dead-ends

```markdown
## 16:50 · session

---
date: 2026-06-30
project: telemetry-svc
threads: ["mem-leak"]
head: <由 append 回填>
next: ["下次稳定增长型泄漏优先用 heap snapshot diff，别先猜"]
---

**做成了什么**
- 定位并修掉 event consumer 的内存泄漏，RSS 由 +50MB/min 归平
  （d4e5f6a，src/consumer.py）

**关键决策 / 为什么**
- 根因是日志 lambda 闭包捕获了整个解码 payload（只需 id）；改成先 `rid = record['id']`
  再捕获，缓存也只存 summarize 摘要。

**卡点 / 困惑**
- 无（已根因）

**下次 TODO**
- 稳定增长型泄漏优先 heap snapshot diff，别先猜假设

**碰到的 open thread**
- 无

**死路（重点，省下次的时间）**
- 假设"监听器没摘"：listener 数稳定，排除（~1h）
- 试 WeakMap 缓存让条目自动回收：RSS 曲线无变化 → 不是"没人引用"而是被闭包吊住，已回退
```

## 评分要点

**必须命中（这一条是本 case 的命门）**
- [ ] **三条死路至少写到两条**（监听器假设、WeakMap 尝试），并说清"为什么放弃"。
      死路没写 = 本 case 直接判失败，无论其他多漂亮。
- [ ] 根因点到"闭包捕获整个 payload"，而非含糊说"修了个泄漏"。
- [ ] commit 只引 hash + 文件名。

**anti-pattern（命中即扣分）**
- 正文≈"修复了 event consumer 的内存泄漏（commit d4e5f6a）"一行带过——
  把一天的排查压成一句话，正好丢光了日记唯一该留的东西。
- 因为"git 里只有 1 个 commit"就认为这是个低信息量 session。
