# 参考文档（reference）

> **这里是查表用的，不是"读"的。** 写代码时想知道某个字段叫什么、某个函数在哪、
> 某个配表怎么填，来这里查。
>
> - 想知道"某功能现在怎么工作" → [`../features/`](../features/)
> - 想知道"当时是怎么查出来的" → [`../archive/`](../archive/)

---

## 怎么找

| 你要查什么 | 看哪个文件 |
|---|---|
| **游戏引擎字段/函数/结构**——反编出来的真相 | [`engine-facts.md`](engine-facts.md) |
| **配表怎么改**——`g.conf` 各表、字段含义、改法 | [`config-table-guide.md`](config-table-guide.md) |
| **组队筛人逻辑**——条件组 `GetUnits`/`GetLuck` 的反编定案 | [`condition-group-selection.md`](condition-group-selection.md) |
| **副本类型**——`ConfDungeonBaseItem.type` 全部 46 个值 | [`dungeon-type-mapping.md`](dungeon-type-mapping.md) |
| **按名字找单位**——统一标准与四步降级 | [`lookup-standard.md`](lookup-standard.md) |
| **UI 全貌**——三面板、与游戏和 Python 的关系 | [`ui-interception-spec.md`](ui-interception-spec.md) |
| **反编译工具链**——怎么 dump、怎么反汇编 | [`il2cpp-toolchain.md`](il2cpp-toolchain.md) |
| **桥：帧格式 / 超时 / 热生效 / 落盘 / 回放**（RPC 全表在 `ui-interception-spec.md` §3.2） | [`bridge-contract.md`](bridge-contract.md) |
| **日志规约与排障**（「慢 / 卡住 / 没回复」先看这里） | [`logging.md`](logging.md) |
| **C# 侧铁律**：IL2CPP 转型 / 裸指针 / 线程 / 数据源 / 本地化（**写 C# 前必读**） | [`csharp-iron-laws.md`](csharp-iron-laws.md) |
| **UI 工作流**：预制件 → AB → 部署 → 验证；气泡行距与富文本 | [`ui-workflow.md`](ui-workflow.md) |
| **功能实现要点**：剧情窗按钮 / 自主交互十道闸 / 工具文档分层 | [`design-notes.md`](design-notes.md) |
| **官方 API 与反编速查**：本机路径 / API 层级 / 反编方法 | [`api-reference.md`](api-reference.md) |

---

## 文件命名约定

文件名要能**单独一眼看出**是哪部分、干什么。所以：

| ✓ 好 | ✗ 不好 |
|---|---|
| `condition-group-selection.md` | `getunits-luck-eval-report.md` |
| `engine-facts.md` | `事实链.md` |
| `config-table-guide.md` | `mod-config-guide.md` |

带日期的是**一次性报告**，属 `archive/`；`reference/` 里都是**长期有效、需要反复查**的。
