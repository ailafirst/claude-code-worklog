# 金标准条目 — 01-clean-feature

```markdown
## 14:30 · session

---
date: 2026-06-30
project: shopcart-api
threads: ["api-pagination"]
head: <由 append 回填>
next: ["可选：给 cursor 加签名防篡改（内部接口暂不急）"]
---

### 做成了什么
- /orders 上线 cursor 分页（a1b2c3d，src/api/orders.py + pagination.py）

### 关键决策 / 为什么
- 选 cursor 而非 offset：orders 追加密集，offset 在翻页中途插入新单会整体下移，
  造成重复/漏单；cursor 锚在数据本身，插入不影响已翻过的窗口。
- cursor 编码 (created_at, id) 而非只 created_at：同毫秒多单时必须用 id 做 tie-break，
  否则边界错漏。

### 卡点 / 困惑
- 无

### 下次 TODO
- 可选：cursor 加签名防篡改（当前内部接口不急）

### 碰到的 open thread
- 无
```

## 评分要点

**必须命中（高信号）**
- [ ] 写出**为什么 cursor 而非 offset**，且点到"追加密集 / 翻页插入下移"这个真实动因。
- [ ] 记下 **(created_at, id) tie-break** 这个 git diff 里看不出意图的细节。
- [ ] commit 只引 hash + 文件名，**不贴 diff、不复述 stats**。

**anti-pattern（命中即扣分）**
- 正文是三条 commit 标题的换行转写（"add cursor pagination / add tests / document openapi"）。
- 把 `+N -M`、files_changed 列表抄进正文——那是 git 自己的活。
- 把"加了测试""写了文档"当成洞见列出（它们本身零增量信息，除非有踩坑）。
