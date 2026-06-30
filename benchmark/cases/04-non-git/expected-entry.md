# 金标准条目 — 04-non-git

```markdown
## 16:00 · session

---
date: 2026-06-30
project: auth-redesign
threads: ["auth-redesign"]
head: <非 git 目录，留空>
blockers: ["移动端离线窗口的校验方式未定，与'选有状态以保即时吊销'相冲突"]
next: ["调研移动端短时效派生凭证方案，评估其吊销口子"]
---

**做成了什么**
- 起草 auth v2 会话方案，定下 cookie/JWT 的取舍方向（design/auth-v2.md）

**关键决策 / 为什么**
- 倾向不透明 session token + 服务端会话存储，而非 JWT：决定性因素是**即时吊销**——
  admin token 泄漏必须能立刻失效；JWT 过期前无法即时吊销，维护黑名单又退回有状态。
  对内部系统，吊销能力 > 无状态便利。

**卡点 / 困惑**
- 移动端离线窗口：有状态方案离线没法查存储；发短时效派生凭证又会重新引入短期不可吊销的口子。

**下次 TODO**
- 调研移动端派生凭证方案，量化它的吊销风险窗口

**碰到的 open thread**
- auth-redesign：会话方案大方向已定，移动端离线分支未决
```

## 评分要点

**必须命中**
- [ ] **照常产出完整条目**——不因 git 为空而拒绝/空写。
- [ ] 抓住**决策的决定性理由是"即时吊销"**，而非泛泛列 JWT/session 优缺点。
- [ ] 把移动端离线这个**未决冲突**记成 blocker/open thread。

**anti-pattern**
- 输出类似"当前目录不是 git 仓库，没有可记录的改动"。
- frontmatter 硬塞假的 head（非 git 应留空）。
