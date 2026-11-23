# 文档索引

> 五层分工，不要混。**记住这张表就够了**：
>
> | 层 | 回答什么问题 | 什么时候看 |
> |---|---|---|
> | [`../AGENTS.md`](../AGENTS.md) | "我该去哪" | 第一次进仓库 |
> | [`DEV_RULES.md`](DEV_RULES.md) | "怎么干活不出事" | **改代码之前** |
> | [`features/`](./features/) | "**这东西现在怎么工作**" | 接手某模块时 |
> | [`reference/`](./reference/) | "**这个字段/表/函数是什么**" | 写代码时查 |
> | [`archive/`](archive/) | "当时是怎么查出来的" | 只在追溯时 |
> | [`journal/`](journal/) | "正在做什么" | 只有当事人 |
> | [`CHANGELOG.md`](CHANGELOG.md) | "什么时候做了什么" | 想知道最近动静 |
> | [`release/`](release/) | 打包、发布、工坊页 | 发版时 |

---

## 一、功能现状（`features/`）

**接手任何一块之前先看这里。** 每篇只写"现在是什么样 + 关键分工 + 坑在哪"，
**不写实现细节** —— 细节以代码为准。

| 文件 | 内容 |
|---|---|
| [`team.md`](./features/team.md) | **组队**：气运→条件组→容器直塞；号位 vs 槽位；六条引擎坑 |
| [`group-chat.md`](./features/group-chat.md) | **群聊**：Room 是什么/不是什么；路线 C；增量投递；四条不变量 |
| [`npc-duel.md`](./features/npc-duel.md) | **决斗**：三种形态（打玩家 / solo / together）；处置页；收口判据 |
| [`team-duel-join.md`](./features/team-duel-join.md) | **队友进决斗**：`type=40` 白名单；应战窗；战后分类写经历 |
| [`battle-flow-gate.md`](./features/battle-flow-gate.md) | **战前/战中闸**：两个态、三档处置、`ClickScope` 自愈 |
| [`context-projection.md`](./features/context-projection.md) | **上下文投影**：账本 vs 投影；L1 四段；为什么只 append（前缀缓存） |
| [`tool-result-contract.md`](./features/tool-result-contract.md) | **工具结果契约**：C# 给数据、Python 组句；五条原则；禁止截断 |
| [`image-context.md`](./features/image-context.md) | **图片进上下文**：字节落图片仓、账本只记 ref、发前投影、GC keep-set |
| [`brain-liveness.md`](./features/brain-liveness.md) | **Python 存活检测**：状态模型；九条自动拉起风险与对应的闸 |
| [`lun-dao-fixed.md`](./features/lun-dao-fixed.md) | **论道静默失败**：传送扣光天数；四道保险 |
| [`ui-lifecycle-and-input.md`](./features/ui-lifecycle-and-input.md) | **世界输入冻结**：双根因；R1/R2/R3 三条现行规则 |
| [`user-data-root.md`](./features/user-data-root.md) | **用户数据根**：`.agent_loop` 优先级；迁移只补缺失、绝不覆盖 |
| [`llm-gateway-compat.md`](./features/llm-gateway-compat.md) | **网关协议兼容**：空工具列表整对省略（不发 `null`）；`base_url` 填到 `/v1`；模型用 API id |

## 二、参考资料（`reference/`）

**查表用，不从头读。** 长期有效、需要反复查的东西都在这。

| 文件 | 查什么 |
|---|---|
| [`csharp-iron-laws.md`](./reference/csharp-iron-laws.md) | **C# 铁律**（写 C# 前必读）：IL2CPP 转型 / 裸指针 / 线程 / 数据源 / 本地化 |
| [`bridge-contract.md`](./reference/bridge-contract.md) | **桥**：帧格式、超时幂等、热生效、落盘时机、历史回放 |
| [`logging.md`](./reference/logging.md) | **日志规约与排障**（「慢 / 卡住 / 没回复」先看这个） |
| [`ui-workflow.md`](./reference/ui-workflow.md) | **UI 工作流**：预制件 → AB → 部署 → 验证；气泡行距与富文本 |
| [`design-notes.md`](./reference/design-notes.md) | **功能实现要点**：剧情窗按钮注入 / 自主交互十道闸 / 工具文档分层 |
| [`api-reference.md`](./reference/api-reference.md) | **官方 API 与反编速查**：本机路径 / API 层级 / 反编方法 |
| [`engine-facts.md`](./reference/engine-facts.md) | **反编出来的引擎真相**：战斗分派、观战正门、字段偏移、函数地址 |
| [`config-table-guide.md`](./reference/config-table-guide.md) | **配表怎么改**：官方文档原文 + 实证 |
| [`condition-group-selection.md`](./reference/condition-group-selection.md) | **条件组选人**：`GetUnits`/`GetLuck` 反汇编求证（5 问速答） |
| [`team-selection-logic.md`](./reference/team-selection-logic.md) | **组队筛人逻辑**：反编定案 |
| [`dungeon-type-mapping.md`](./reference/dungeon-type-mapping.md) | **副本类型**：`ConfDungeonBaseItem.type` 全部 46 个值 |
| [`tool-result-fields.md`](./reference/tool-result-fields.md) | **逐工具字段清单**：每个工具 `data` 里有什么、渲染器怎么组句 |
| [`context-projection-details.md`](./reference/context-projection-details.md) | **投影实现细节**：逐段链路、基线实例、前缀缓存论证、复现方法 |
| [`ui-interception-spec.md`](./reference/ui-interception-spec.md) | **UI 全貌**：三面板架构、与游戏和 Python 的关系 |
| [`il2cpp-toolchain.md`](./reference/il2cpp-toolchain.md) | **反编译工具链**：怎么 dump、怎么反汇编（**缓存优先，别每次重跑**） |
| [`lookup-standard.md`](./reference/lookup-standard.md) | **按名字找单位**：统一标准与四步降级 |

## 三、归档（`archive/`）

**日常不用看。** 已完成功能的过程详文、事故复盘、被推翻的中间结论。
过程流水已精简 —— 只留每轮"定下了什么"，细节用 `git log --follow` 查。

**一条规则**：日期开头。一件事写一份 → `.md`；一件事要写多份 → 同名目录。
类型（事故 / 调研 / 体检）只记在 [`archive/README.md`](archive/README.md) 的表里，不用目录分。

16 个条目按时间排下来就是一条开发史。

## 四、开发过程（`journal/`）

**只放带日期的子文件夹**，每篇 `README.md` 是概要。完工后详文归档、文件夹留索引。
用法见 [`journal/README.md`](journal/README.md)。

## 五、计划（未实现）

| 文件 | 内容 |
|---|---|
| [`plan-sect.md`](plan-sect.md) | 宗门功能计划（752 行） |
| [`plan-compaction-view.md`](plan-compaction-view.md) | 压缩纪要的展示与修改 |
| [`plan-team-rescue-dialogue.md`](plan-team-rescue-dialogue.md) | 队友倒下救援窗 |

## 六、发布（`release/`）

| 文件 | 内容 |
|---|---|
| [`PACKAGING.md`](release/PACKAGING.md) | 怎么打包（发行档 = Nuitka 单文件） |
| [`PUBLISH.md`](release/PUBLISH.md) | 发到创意工坊的操作步骤 |
| [`RELEASE_STATUS.md`](release/RELEASE_STATUS.md) | 现在做到哪了、还差什么 |
| [`SHOTS.md`](release/SHOTS.md) | 工坊页配图清单 |
| [`COVER_PROMPT.md`](release/COVER_PROMPT.md) | 封面设计思路与生图提示词 |
| [`ui-skin-prompts.md`](release/ui-skin-prompts.md) | UI 换肤素材与提示词 |
| [`COMMUNITY_POST.md`](release/COMMUNITY_POST.md) | 社区宣传稿（长 / 短两版） |
| [`rebuild-ab-checklist.md`](release/rebuild-ab-checklist.md) | 重打 AB 清单 |
