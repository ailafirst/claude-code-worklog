# 金标准条目 — 06-uncommitted-wip

```markdown
## 17:30 · session

---
date: 2026-06-30
project: billing-svc
threads: ["module-extract"]
head: <由 append 回填>
blockers: ["_apply 是 charge/refund 共享的结算原语，归属未定，tree 当前编译不过，未提交"]
next: ["把 _apply 提到 billing.core，charge/refund 同依赖 core", "编译通过后再 commit，别把红状态固化"]
---

### 做成了什么
- 进行中：把 charge 从单体 billing.py 抽进 charges 模块（未提交，tree 还编译不过）

### 关键决策 / 为什么
- 发现模块边界画错：charge 与 refund 共用私有 _apply，它既不属 charge 也不属 refund，
  是共享的结算原语 → 倾向提到第三个模块 billing.core，二者同依赖它。

### 卡点 / 困惑
- _apply 归属未定：放 charges 则 refund 反向依赖、留 billing 则 charges import 回去等于没拆。
  当前 charges.py 的 import 是错的（留了 FIXME），tree 编译不过，故未 commit。

### 下次 TODO
- _apply 提到 billing.core；编译通过再提交

### 碰到的 open thread
- module-extract：边界已看清，待落地 billing.core 拆分
```

## 评分要点

**必须命中**
- [ ] **不因 0 commit 判定"没产出"**——认出 uncommitted 代表"进行中的工作"。
- [ ] 记下**真正的洞见**：`_apply` 是共享结算原语、边界画错、倾向提到 billing.core。
- [ ] next 写明"编译通过再提交"，体现"现在不提交是有意的"。

**anti-pattern**
- 输出"今天没有提交记录，无可记录"。
- 只描述"在重构 billing"，不写卡住的设计问题。
