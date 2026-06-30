# session 上下文 — 07-refactor-no-behavior

> 一个**大 diff、零行为变化**的提交。git 能完整告诉你"哪些文件被改名/拆分"——
> 所以日记**绝不该**复述这些。唯一该留的是 git 看不到的**意图**。

把单体 `Renderer` 改名成 `RenderPipeline`，并拆成离散的 stage（RasterStage / ShadeStage）。
**渲染输出一字节没变**，纯结构调整，diff 不小（删 renderer.py，新增 pipeline.py + stages/*）。

**为什么做这个改名（关键，git 里完全看不出来）**：
为了下一步的**插件架构**。需求是让第三方能在 raster 和 shade **之间**插入自定义 stage
（比如自定义后处理、调试可视化）。旧的单体 `Renderer.run()` 把 raster/shade 写死在一个方法里，
**根本没有可插入的缝**。

改成 `RenderPipeline(stages=[...])` 后，stage 变成一个**有序列表**，注册插件就是往列表里插一项。
所以这次提交本身**不带任何插件功能**——它纯粹是**拆掉那堵墙、给插件让出位置**的使能改动。

真正的插件注册 hook（`pipeline.register(stage, after=...)`）下次做。这条线叫 plugin-arch。

无 blocker。改名做了一遍全量测试确认行为不变。
