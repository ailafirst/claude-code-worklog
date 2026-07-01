# 金标准条目 — 03-blocker-stuck

```markdown
## 17:10 · session

---
date: 2026-06-30
project: orders-api
threads: ["flaky-ci", "db-migrations"]
head: <由 append 回填>
blockers: ["test_checkout_idempotency CI 上 ~1/5 失败、本地无法复现；缩到 seeding 早于 migration 0007 索引就绪的竞态，未证实"]
next: ["seed_orders 前加 'migration 0007 已应用' 屏障（查 indexisready）", "或给 CI job 加 readiness probe 把 seeding gate 住", "跑一轮带 explain_query 的 DEBUG 抓 CI 查询计划，证实后撤掉"]
---

### 做成了什么
- 把 flaky 的 checkout 测试缩到一个竞态假设（未修复，见 blocker）
- 临时手段：@flaky.retry(3) 压红、CI 加 wait_for_db_ready（a1.. / b2..），失败率降未消

### 关键决策 / 为什么
- retry 和 wait_for_db 都明确只是止血，不是修复——根因疑似 seeding 与 migration 索引就绪的时序。

### 卡点 / 困惑
- CI ~1/5 失败、本地必不复现：本地 migration 串行先跑完，没有 CI 并行的时序窗口。
  疑似 seed_orders 早于 0007 索引就绪 → checkout 查询计划变化 → 幂等去重偶尔漏判。未证实。

### 下次 TODO
- 见 frontmatter next（屏障 / readiness probe / 用 DEBUG explain 证实假设）

### 碰到的 open thread
- db-migrations：并行 CI 下 migration 就绪信号缺失，可能不止这一个测试受影响
```

## 评分要点

**必须命中**
- [ ] blocker 写到**具体竞态**（seeding vs migration 0007 索引就绪 + 本地不复现的原因），
      不是"测试偶尔挂"。
- [ ] next 是**可执行步骤**（加屏障 / readiness probe / 用 explain 证实），不是"继续查"。
- [ ] 点明 retry / wait_for_db 是**止血而非修复**——这层判断 git diff 看不出来。

**anti-pattern**
- blocker = "checkout 测试 flaky"；next 空着或"明天再看"。
- 把两个临时 commit 当成"已修复"来记。
- 漏掉 uncommitted 里的 DEBUG print 代表的"假设尚未证实"状态。
