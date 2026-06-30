# session 上下文 — 03-blocker-stuck

> 这次没修好，停在一个**具体的竞态假设**上。日记的价值就是把这个假设和下一步钉死，
> 让下次（可能是别人、也可能是一周后的自己）不用从零重建。

`test_checkout_idempotency` 在 **CI 上约 1/5 概率失败，本地从不复现**。两次提交都是
试探，**没解决**：

- 先给测试加了 `@flaky.retry(3)`（commit 1）——这只是**压住症状**让流水线别红，不是修复。
- 又在 CI 加了 `wait_for_db_ready`（commit 2），怀疑是 DB 还没起来。失败率有降但**没消失**。

今天的关键进展（也是卡点）：把范围缩到一个**竞态**——
CI 的并行 job 里，测试的 `seed_orders()` 有时**先于 migration 0007（`idx_orders_key` 索引）
应用完成**就插了数据。索引在不在，`checkout` 的查询计划不同；没索引时幂等去重偶尔漏判，
于是同一个 order 被重复结算，断言失败。

**为什么还没解决：本地无法复现这个时序**——本地 migration 是串行先跑完的，CI 才有并行窗口。
未提交的工作区里加了一行 `print('DEBUG idx state', explain_query(...))` 想在 CI 上抓查询计划，
还没跑出结果就到点了。

下一步（明确）：
1. 在 `seed_orders()` 前加一个**显式的 "migration 0007 已应用" 屏障**（查 pg 的 indexisready）。
2. 或给 CI job 加 readiness probe，gate 住 seeding。
3. 把那行 DEBUG print 跑一轮 CI 看查询计划证实/证伪假设，然后撤掉。
