# 金标准条目 — 05-multi-thread

```markdown
## 15:20 · session

---
date: 2026-06-30
project: web-app
threads: ["search-bug", "deps-upgrade", "onboarding-docs"]
head: <由 append 回填>
---

### 做成了什么
- search-bug：修了换筛选后结果不刷新（memo key 漏了 filter），含快照测试更新
  （a1.. / d4..，src/search/index.ts、__tests__）
- deps-upgrade：lodash 4.17.20→4.17.21（CVE-2021-23337，b2..）
- onboarding-docs：补新人上手文档（c3..）

### 关键决策 / 为什么
- search 的记忆化 key 只含 query 不含 filter，导致换 filter 命中旧缓存——key 改为 [q, filter]。
  （deps 升级与文档无判断含量，不展开。）

### 卡点 / 困惑
- 无

### 下次 TODO
- 无

### 碰到的 open thread
- 三条线本日均告一段落；search-bug 可留意是否还有别处用了同样的弱 memo key
```

## 评分要点

**必须命中**
- [ ] **threads 恰好三个**：search-bug / deps-upgrade / onboarding-docs。
- [ ] 把第 4 个 commit（snapshot 更新）**归到 search-bug**，不误当第四条线。
- [ ] 只对 search-bug 展开"为什么"，对 deps/docs **克制**（它们确实没判断含量）。

**anti-pattern**
- 四个 commit 排成一个不分线的清单，threads 只填一个或填成四个。
- 给 lodash 升级、写文档硬编出"决策/权衡"。
