# session 上下文 — 02-dead-ends

> git 里只有 1 个修复 commit。真正省未来时间的信息全在下面这些**没进 git 的死路**里。

telemetry-svc 的 event consumer 内存一直涨，RSS 大约 **每分钟 +50MB**，几小时后被 OOM kill。
今天一整天在抓这个泄漏。

排查过程（这些都不在 git 里，因为都被推翻了）：

1. **假设一：事件监听器没摘。** 以为是注册了 listener 没 `off`。逐个查了 consumer 的
   订阅点，listener 数量稳定、没增长——**排除**。白花了大概一小时。
2. **假设二：缓存条目没被 GC。** 把 `cache` 从普通 dict 换成 `WeakMap` 试，想让没人引用的
   条目自动回收。结果 RSS 曲线**没有任何变化**——说明条目根本不是"没人引用"，而是有人
   一直引着。**回退**了这次改动（所以 git 里看不到 WeakMap）。
3. **真凶（最终修复）：** consumer 里有个 `log_event(lambda: ...)` 的日志回调，闭包**捕获了
   整个 `record`**（解码后的完整 payload，每条几十 KB），而日志其实只需要 `record['id']`。
   这些 lambda 被日志缓冲区持有，于是每条 record 的完整 payload 都被吊住不放。
   修复：先把 `rid = record['id']` 绑出来，lambda 只捕获 `rid`；缓存也只存 `summarize(record)`
   的摘要而非整条。RSS 立刻平了。

教训：内存泄漏先看**谁在闭包里被捕获**，再怀疑缓存/监听器。下次遇到稳定增长优先用
heap snapshot diff 而不是猜。

无遗留 blocker。
