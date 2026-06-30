# 金标准条目 — 11-decision-heavy

```markdown
## 15:30 · session

---
date: 2026-06-30
project: dashboard-ui
threads: ["state-mgmt"]
head: <由 append 回填>
---

**做成了什么**
- 选定 Zustand 管理跨组件筛选状态，落地 filter store（c3..，src/store/filters.ts）
- 过程中 spike 了 Redux 又 revert（a1.. / b2..）

**关键决策 / 为什么**
- 三选一，选 Zustand：
  - React Context 否决：无选择性订阅，filter 一变整棵子树重渲，200 行表格实测掉帧。
  - Redux 否决：spike 验证能用但样板/心智对 3 人小团队不成比例（故 revert，即 git 那次撤回）。
  - Zustand 胜出：样板极少 + 选择性订阅正好治 Context 的重渲 + 免 Provider 包裹。

**卡点 / 困惑**
- 无

**下次 TODO**
- 无

**碰到的 open thread**
- state-mgmt：方案定型，后续筛选/分页等共享状态统一走 Zustand
```

## 评分要点

**必须命中**
- [ ] **三个候选都记到**，且各自写出"为什么选/为什么否"。
- [ ] Context 否决理由点到**选择性订阅缺失 + 实测掉帧**（量化动因）。
- [ ] 把 git 的 **spike+revert 解读成"Redux 被认真试过又否决"**，而非无意义反复。

**anti-pattern**
- 只写"选用 Zustand 做状态管理"，丢掉两个被否选项及理由。
- 把 revert 当成"撤销了一次误操作"，错过它承载的决策信息。
