# 战斗流程闸（战前 / 战中）—— 实现现状

> **一句话**：NPC 在"一场仗正在安排/正在打"的窗口里再发起会开新战斗的动作（攻击、切磋、传送、召唤），
> **当场拒绝、不补做**；其余动作排队等此间事了。
>
> 起因：10-25 用户报「我点对话（按道理来说是会把上下文传进去的啊，为什么没有反应）」——
> 引擎的剧情链（玩家点「战斗」）与我们排队落刀的那条攻击**重叠**，同一页被开了两次，
> 处置页整段被标成"不投"，`TakeDelta` 返回 0，玩家看到的就是"没反应"。
>
> 逐毫秒时间线与被推翻的中间结论在
> [`docs/archive/2026-10-25-战斗流程闸.md`](../archive/2026-10-25-战斗流程闸.md)。

---

## 1. 两个态

| 态 | 性质 | 怎么点亮 | 怎么退出 |
|---|---|---|---|
| `PreBattle` | **记忆态**（点亮后保持到明确证据） | ① 页上有开战按钮 ② 应战窗亮着 ③ 刚点了开战按钮 | 转战中 / 点了非开战按钮 / 开了一页没有开战按钮的新页且没点过开战 / 应战窗关了但没进战斗 / **兜底 60s 零证据** |
| `InBattle` | **实时电平** | `BattleActive()`（`isBattle`） | 战斗位落定 |

**"战前"为什么必须记住**：真机 20 秒里出现过 **2.2 秒空档**（18:51:28.141 页关 → 18:51:30.395 应战窗才弹），
而我们排队的那条 attack 恰好在 28.687 落刀 —— 只看瞬时电平会漏掉整个窗口。

**60 秒兜底不是判定**：`DefaultFallbackSeconds = 60` 只回答"这个状态还要不要挂着"，
**不参与任何归因**，也不写进给模型的话术。

## 2. 三档处置

调用点在 `csharp/tools/ToolExecutor.cs:111`（带 `npcId` 的 `Execute` 里，**在排队闸之前**），
排队执行体里另有一次复查（`:136`，又忙起来就退回等待，不判失败）。

| 档 | 谁 | 为什么 |
|---|---|---|
| **Reject** | `world_ai_action`: `attack` / `spar`；`movement`: `teleport` / `summon` | 排队的语义是"等会儿办"，而这四个是"把玩家拖去打一场 / 把玩家挪走"。事后补一刀 = 把已发生的事再办一遍 |
| **Queue** | `economy_item` / `trade` / `item_acquire` / `social_relation`，以及 `world_ai_action` 的其余 op、`movement` 的 `travel` | 与战斗不冲突，只是此刻弹窗会沉进系统队列不出队 |
| **Pass** | 不在这六个工具里的（`inspect_unit` / `search_units` / `query_world` / 沟通类…） | 只读，或不在战前/战中 |

白名单是**故意**写成枚举而不是"除几个之外都走闸"：将来新增工具默认走直通（旧行为），
不会被静默塞进排队。名单与 `tools/schemas.ACTION_TOOLS` 同集合。

被拒时回给模型的话必须说三件事：**发生了什么 / 为什么这次不行 / 别再重试**。
缺了最后一句模型会反复重调（09-24 排队文案就是为这个写的）。

## 3. `ClickScope`：唯一会"学错东西"的地方

闸要知道"哪一枚按钮是开战按钮"。学习点是 `ClickOption` 的 prefix/postfix，
而**学错一个编号的后果不是"少挡一次"，是那枚选项从此每次都被当成开战按钮**
⇒ 那一页一弹出来就进战前 ⇒ 攻击/切磋/传送/召唤被持续误拒，且落盘后跨存档、跨重启都在。

- 正常路径：`Enter` 记下 optionId + 帧号，`Exit` 在 postfix 里还回去。
- **泄漏自愈**：Harmony 的 postfix 在原方法**正常返回之后**才跑；引擎的 `ClickOption` 内部要跑完
  整个选项效果（`DramaFunction` → `IntoDungeon` → `IntoBattle`），任一处抛异常 postfix 就不跑、
  作用域永远不闭合。此后"引擎自己发起的进副本命令"会被记到那枚早先点过的选项上。
  帧号是对它的**结构性**否定：真机栈证明 `ClickOption → OptionsFunction → IntoDungeon` 是同步调用
  ⇒ **同一帧**。所以"作用域只在开启它的那一帧有效"不是时间窗，是那条事实的直接推论。
  代价：万一真机跨帧，结果是**学不到**（= 少挡），方向安全。
- 三种"不在作用域"分开记：`Outside`（根本没人点）/ `Expired`（上一帧残留，已作废）/ `NoId`（点了但配表字段读不到）。

学的编号落盘在 `<数据根>/battle_flow_ids.json`（跨存档共用、可手改）。
内置初值 `SeedIds = { 100003, 212042 }`（寻仇页「战斗」/ 切磋「好，就让我和你切磋一下」）。

## 4. 相关常量

| 名 | 值 | 位置 |
|---|---|---|
| 采样间隔 | `SampleSeconds = 0.25` | `state/BattleFlowGate.cs:50` |
| 战前兜底 | `DefaultFallbackSeconds = 60` | `state/BattleFlowCore.cs:166` |
| 落盘文件 | `battle_flow_ids.json`（键 `battle_option_ids`） | `state/BattleFlowGate.cs:46` |
| 内置开战编号 | `100003` / `212042` | `state/BattleFlowGate.cs:53` |

**没有独立的总闸开关文件**：停用它只能靠清空 `battle_flow_ids.json` 的内置项之外的内容，
或改代码（`BattleFlowGate.VerdictOf` 在 `!_core.Armed` 时返回 `Pass`）。

## 5. 现状

**已验证**

- C#：`BattleFlowCoreTests` 125 项、`BattleFlowSim` 26 项（五个场景，含"闸关掉时复现原始故障"）。
- Python：`tests/test_battle_flow_gate.py` 12 项跨语言契约。
- 两个变异都实测变红：把 `attack`/`spar` 从 Reject 降级成 Queue ⇒ 模拟 8 项红；
  去掉帧号自愈 ⇒ 单元 5 项红 + 模拟 3 项红。

**未验证**

- **真机探针一次都没跑过**。全部结论来自源码比对 + 单元/契约/模拟。
- 尤其 `ClickScope` 的"同一帧"假设：证据是真机栈（同步调用），没有一条运行时日志直接确认过帧号相等。
  上线后若日志出现 `落在上一帧残留的作用域上`，说明这条假设在真机上不成立（后果是"学不到"，方向安全）。
- 战前记忆态 60s 兜底在真机上的实际触发频率未知。

## 6. 相关代码

| 文件 | 职责 |
|---|---|
| `csharp/state/BattleFlowCore.cs` | 状态机 + `Grade` 三档 + `ClickScope`（纯逻辑，零 Unity） |
| `csharp/state/BattleFlowGate.cs` | 接线层：0.25s 采样、事件订阅、编号落盘、`VerdictOf` |
| `csharp/state/BattleFlowHooks.cs` | 三个挂点（`ClickOption` / `IntoBattle` / `BattleActive`） |
| `csharp/tools/ToolExecutor.cs` | 闸的两处调用点（直发 + 排队复查） |
| `docs/archive/2026-10-25-战斗流程闸.md` | 原始故障时间线、五个模拟场景、变异记录 |
