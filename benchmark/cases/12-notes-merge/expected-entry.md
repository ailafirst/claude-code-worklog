# 金标准条目 — 12-notes-merge

```markdown
## 14:30 · session

---
date: 2026-06-30
project: api-gateway
threads: ["cache-layer"]
head: <由 append 回填>
next: ["规范化 cache key 的 query 参数顺序，复测命中率（当前 ~40% 疑因顺序敏感）", "/health 等探活端点 bypass 缓存"]
---

**做成了什么**
- GET 端点加响应缓存层 + 单测（a1.. / b2..，src/cache.ts、gateway.ts）

**关键决策 / 为什么**
- 缓存按 path+query 做 key；当前实现 query 原样拼接，顺序敏感（见卡点）。

**卡点 / 困惑**
- 命中率仅 ~40%，疑因 cacheKey 把 query 原样拼接、参数顺序不固定 → 同义请求被当不同 key。待验证。

**下次 TODO**
- 规范化 cache key 的 query 顺序后复测命中率
- /health 等探活端点 bypass 缓存（当前被误缓存）

**碰到的 open thread**
- cache-layer：基础缓存已上线，命中率优化与探活 bypass 未完
```

## 评分要点

**必须命中（本 case 命门）**
- [ ] **两条 session_notes 都被吸收**：命中率/query 顺序 → next + 卡点；/health bypass → next。
- [ ] 区分清楚：缓存层是**今天已做**，两条 note 是**待办/观察**（不混为已完成）。
- [ ] thread 用 cache-layer，open-thread 标明优化未完。

**anti-pattern**
- 只记两个 commit（加缓存 + 单测），**完全无视 session_notes**——本 case 直接判失败。
- 把 note 里的"怀疑/待验证"写成已确认结论。
