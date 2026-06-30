# 金标准条目 — 09-continuation

```markdown
## 15:30 · session

---
date: 2026-06-30
project: billing-svc
threads: ["module-extract"]
head: <由 append 回填>
next: ["清理仍走 billing.py 兼容 shim 的调用方"]
---

**做成了什么**
- 收口 charges/refunds 抽取：_apply 提到 billing.core 改名 apply，charge/refund 同依赖它
  （a1.. / b2..，src/billing/core.py、charges.py、refunds.py）
- 老 billing.py 留成兼容 shim，避免一次改光调用方

**关键决策 / 为什么**
- 落地上次悬着的边界问题：_apply 既不属 charge 也不属 refund，提到第三方 billing.core，
  二者单向依赖 core，消除互相反向依赖。tree 编译通过、测试绿才提交（接上次"不提交红状态"的纪律）。

**卡点 / 困惑**
- 无（上次的 _apply 归属问题已解）

**下次 TODO**
- 清理还在走 billing.py shim 的调用方

**碰到的 open thread**
- module-extract：基本收口，仅剩 shim 调用方清理
```

## 评分要点

**必须命中**
- [ ] **延续同一 thread 名** `module-extract`（不另起新名，否则聚合断线）。
- [ ] **承接上次的未决问题**（_apply 归属）并写出它今天如何解决，而非从零介绍任务。
- [ ] 体现利用了 `since` 的精确增量：只讲今天这两个提交，不复述全量历史。

**anti-pattern**
- 把 module-extract 当全新任务，从"开始重构 billing"讲起。
- 换 thread 名（如 billing-refactor）导致与 06 的记录连不上。
