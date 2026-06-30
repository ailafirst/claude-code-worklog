# session 上下文 — 11-decision-heavy

> git 里能看到一个 spike commit 和一个 revert（试了 Redux 又撤了）。但**为什么试、为什么撤、
> 为什么最终选 Zustand**——这些判断 git 一概看不到，正是日记该留的。

任务：给 dashboard 的**跨组件筛选状态**选一个状态管理方案。三个候选都认真评估了：

1. **React Context**（最先想到）——**否决**。筛选状态一变，Provider 下**整棵子树都 re-render**。
   在那张 200 行的大表格上**实测有可感的卡顿**（输入筛选词时掉帧）。Context 没有选择性订阅，
   这条对性能是硬伤。

2. **Redux**——**做了 spike（就是 git 里那个 prototype），然后 revert**。它能用、生态成熟，
   但对一个**3 人小团队**来说，action/reducer/dispatch 的样板和额外心智模型**不成比例**——
   为这点状态引入一整套范式不划算。spike 验证了"能做但太重"，于是撤掉。

3. **Zustand**——**最终选择**。理由：样板极少（一个 `create` 就够）；**选择性订阅**正好解决
   Context 的 re-render 问题（组件只订阅自己用到的切片，表格不再整体重渲）；不需要 Provider 包裹。
   对小团队心智负担最低。

所以 git 里的 spike+revert 不是"反复横跳"，而是**Redux 这条备选被认真试过又否决**的证据。
最终落地 `src/store/filters.ts` + 加 zustand 依赖。

无 blocker。state-mgmt 这条线本日定型。
