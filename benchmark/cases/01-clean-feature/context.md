# session 上下文 — 01-clean-feature

> 这是喂给被测模型的"git 看不到的"那部分。连同 `collect` 的 JSON 一起给模型，
> 看它能否写出**增量信息**而非复述提交。

今天给 `/orders` 加分页。需求只说"要能翻页"。

- 一开始想用最简单的 offset/limit（`?page=2`），但 orders 表是**追加密集**的——
  下单高峰时每秒新增几十行。offset 分页在用户翻到第 2 页时，如果中间又插了新单，
  整个窗口会下移，导致**重复或漏单**。所以改用 cursor 分页。
- cursor 用 `(created_at, id)` 的 base64。**单用 created_at 不够**：同一毫秒可能有多单，
  会在边界处错漏；必须带 `id` 做 tie-break。这点踩过一次（第一版只编码 created_at，
  测试 `test_cursor_stable_under_insert` 挂了才反应过来）。
- limit 取 `limit+1` 行来判断"还有没有下一页"，多出的那行不返回，只用来生成 next_cursor。
- 没有死路，整体顺。openapi 文档是顺手补的。

没有遗留 blocker。下次可以考虑给 cursor 加签名防篡改，但当前内部接口不急。
