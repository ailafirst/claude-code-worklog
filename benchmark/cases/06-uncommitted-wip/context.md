# session 上下文 — 06-uncommitted-wip

> 今天**没有任何 commit**——因为代码还编译不过，不该提交。但 session 很有价值：
> 撞上了一个模块边界的设计问题。collect 的 `uncommitted` 字段会显示这些半成品。

在把 `charge` 逻辑从单体 `billing.py` 抽成独立 `charges` 模块。抽到一半卡住，**没提交**。

卡点（也是今天最该记的）：`charge` 和 `refund` **共用同一个私有 helper `_apply`**。
我把 `charge` 移进 `charges.py` 后才发现，`refund` 也伸手进 `_apply`——于是 `_apply` 该归谁？

- 放 `charges` 里：`refund` 要反向依赖 charges，方向别扭。
- 留 `billing.py` 里：那 charges 又得 import 回去，等于没拆干净。

模块边界画错了。真正的问题是 `_apply` 是**两者共享的结算原语**，既不属于 charge 也不属于 refund。
倾向是把它提到第三个模块（`billing.core`），charge / refund 都依赖 core——但还没动手验证，
所以现在 tree **编译不过**（charges.py 那行 import 是错的，留了 FIXME）。

下一步：
1. 决定 `_apply` 归属——大概率提到 `billing.core`。
2. 让 tree 重新编译通过，再 commit（现在提交会把红的状态固化）。

这条线叫 module-extract，会接着干。
