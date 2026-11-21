# 02 · 路线 A：玩家参战的决斗（队友盟军入场）

> ⚠ **已归档**。现状见 [`docs/features/npc-duel.md`](../../features/npc-duel.md)。本文只保留当时的排查过程与被推翻的中间结论，**追溯「这个结论怎么来的」时才需要读**。
>
> **结局**：路线 A 落地为 `mode=together`（玩家当发起方，队友含 A 一起上），见 `mode-together.md`。
>
- **起止**：2026-10-25
- **触发**：让一个 NPC 与一个 NPC 真的打起来
- **涉及**：`csharp/drama/NpcDuel.cs`


---

## §1 先纠正一个前提

用户原话：

> "a 调用攻击工具对 b，实际上是让 b 对 a 攻击（发出对应的 UI），然后你同意后就一起进入战斗了"

这条链在引擎分流层面**断在第一步**（证据 [01-F2/F3](../../reference/engine-facts.md)）：

```
UnitActionRoleToUnitBase::OnCreate
    ├─ 发起方是玩家  → PlayerToNPCAction
    ├─ 目标是玩家    → NPCToPlayerAction   ← 只有这两条会起场景
    └─ 都不是玩家    → NPCToNPCAction     ← 直接结算，无 IntoBattle、无 UI
```

B 打 A 属于第三支。**队伍关系不参与这个判断**——A 是玩家的队友，也不会让 B→A 变成"玩家参战"。

⇒ 路线 A 唯一能成立的形态是：**让玩家成为两个当事人之一**，队友 A 作为**盟军**被塞进同一场战斗。
于是 A 与 B 在场景里分属敌对两方，真的会互相打——这就是"NPC 对 NPC 战斗"在路线 A 里的真实含义。

---

## §2 两种可用形态

| | 谁发起 | 谁是对手 | 玩家的角色 | 语义 |
|---|---|---|---|---|
| **A1（现状即可）** | 目标 B 打玩家 | B | 当事人 | 已有 `attack` 工具就是这条；缺的只是队友入场 |
| **A2（本方案）** | 玩家打目标 B（由 agent 的调用触发） | B | 当事人 | "A 请你一起出手"——A 作为盟军与 B 交手 |

A1 不需要改任何"谁打谁"的代码：B 打玩家这条链已经是真场景（`dungeonID=11`、`type=40`），
缺的只是**队友能不能进来**（[01-F7](../../reference/engine-facts.md)）。

A2 是"agent 说'我要收拾他' → 玩家被拉进战斗"的形态。它需要 C# 侧替**玩家**建一个 action：

```csharp
var atk = new UnitActionRoleAttack(targetB);     // 发起方 = 玩家，目标 = B
player.CreateAction(atk, true);                  // 项目里已有 player.CreateAction 先例
```

> `player.CreateAction(...)` 在本仓已有用例（`ToolExecutor.cs:2248` 召唤用的是
> `mover.CreateAction(mvSet, true)`，`mover` 可能是玩家），不是新机制。
>
> ⚠ **但"玩家发起攻击"这一条本身还没真机验过**（仓里现有调用是移动类动作）。
> 需要验的点：`PlayerToNPCAction` 是否照常弹 `UIMapBattlePre`、
> 战后处置页是否与"NPC 打玩家"那条链同形。**这是 A2 的 U6 探针**，见 [04 §3](路线对比与推荐.md)。

---

## §3 落地链路（A2 为例）

```
agent A 调 world_ai_action(op=attack, target=B)
   │  （Python：target 从"禁止"改成"允许填 NPC 真名"）
   ▼
ToolExecutor.attack
   ├─ 解析 B（UnitLookup.ResolveEx(name, id)，与其它工具同一口）
   ├─ 判：玩家与 A 是否同队？           ← TeamTool.Members() 现成
   │    ├─ 否 → 不拉玩家：走 03 的"后台结算"分支（A vs B 直接算）
   │    └─ 是 → 继续
   ├─ 同格判定（引擎的 IsCreate 也会判，这里先判一次给人话错误）
   └─ player.CreateAction(new UnitActionRoleAttack(B), true)
         ▼
       UIMapBattlePre（玩家点「应战」= 唯一的同意点）
         ▼
       WorldBattleMgr.IntoBattle(toUnit=B, dungeonID=11, …)
         ▼
       SceneBattle.Init → DungeonTrigger MID&004（type=40 白名单）
         ▼
       addBattleNPCData_MID&003_0  →  TeamContainerInject.Prefix
         ▼                            清空 npcUnits，按号位压入队友
       addBattleUnit_3 / _4 / _5  →  队友作为「盟军」(队伍类型 2) 上场
         ▼
       场景：玩家 + A(+队友) vs B   —— A 与 B 敌对，真的会打
         ▼
       BattleEndFront / BattleExit → BattleOutcome 收口（玩家侧结局）
                                    TeamBattleFollowup 写队友经历
```

---

## §4 改动清单

### 4.1 配表（P0，不改则队友永远进不来）

| 表 | 动作 | 内容 |
|---|---|---|
| `DungeonTrigger` | **新增** `MID&004` | `dungeonType` 白名单**含 40**（其余照抄 `MID&001` 的 10..92 列表）；`triggerType=2`；`battleFunction = addBattleNPCData_MID&003_0 \| addBattleNPCData_MID&003_1 \| addBattleNPCData_MID&003_2 \| addBattleUnit_3_-1_10_2_1_2_0 \| addBattleUnit_4_-1_10_2_1_2_0 \| addBattleUnit_5_-1_10_2_1_2_0 \| setBattleData_alTeamIn_ok` |

**不要复用 `MID&001`**：它带 `setDramaUnitData`，在决斗里会改写战后处置页的单位表
（[队友进决斗/方案.md](../2026-10-25-队友进决斗/方案.md) §「把 40 塞进 MID&001 就行」那条已被否掉）。

`addBattleUnit` 的**第 6 个参数 = 队伍类型**（官方《配置对照表》sheet「副本作用功能」）：

```
1 = 玩家一方/召唤物   2 = 盟军   3 = 怪物一方   4 = 不参与战斗的 NPC
```

我们用 `2`（盟军）——与副本里队友的用法完全一致。

### 4.2 C# 代码

| 文件 | 改动 |
|---|---|
| `csharp/tools/ToolExecutor.cs` `case "attack"` | target 为 NPC 时新增分支：同队 → `player.CreateAction(new UnitActionRoleAttack(B), true)`；不同队 → 交给路线 B 的结算口 |
| `csharp/tools/TeamContainerInject.cs` | **逻辑不用改**（它挂在 `AddBattleNPCData` 上）；只需确认"非白名单时清容器并跳过原方法"的分支不会把决斗的原有 NPC 清掉 —— 现在白名单是配表驱动，新行生效后自然命中 |
| `csharp/tools/TeamBattleFollowup.cs` | 战后按 `battle.data.dungeonBaseItem.type == 40` 分支写"并肩应战"文案（已有设计，见 team-battle-join） |

### 4.3 Python 侧

| 文件 | 改动 |
|---|---|
| `tools/schemas.py` `WORLD_AI_ACTION` | `target` 描述从"只能填玩家"改成"可填另一个 NPC 真名"；`attack` 描述补一句"与玩家同队时玩家会被拉进战斗" |
| `tools/text_render.py` | `attack` 结果多一个 `party` 字段（谁在场）→ 渲染成"你与X并肩" |
| `agent/dialogue_agent.py` | 无需改（工具参数注入已通用） |

---

## §5 边界与失败姿态

| 场景 | 处理 |
|---|---|
| 玩家没和 A 同队 | **不拉玩家**。要么走后台结算（03），要么直接拒绝并回一句人话理由（推荐前者） |
| 玩家正忙（战斗/剧情窗/别的对话） | 沿用 `speech_only_turn` 与 C# 的忙碌闸：动作工具本就会被拦 |
| A 精力不足（`residueDay`） | `CreateAction` 返回非 0 时如实回"精力不足"（本仓已有 `NativeRefuseWhy` 文案链，见 lun_dao 复盘） |
| 玩家点了「不应战」 | 不注入、不参战；工具结果按"未应战"收口（`BattleOutcome` 已有该分支） |
| 队友格位被副本占用 | `TeamContainerInject` 已有 `dupSlots` 检测与日志，照旧 |

> **不做**：不改工具表数量、不加定时器、不"等 N 秒再猜"。所有分支都由事件（`AddBattleNPCData` 被调 /
> `BattleEndFront` / 玩家点击）驱动 —— 与仓里既有铁律一致。

---

## §6 路线 A 的天然局限（写给决策用）

1. **玩家必须在场且参战**。它做不出"两个 NPC 单挑、玩家只看"。
2. **只能 2 方**：玩家(+队友) 对 1 个目标。做不出"NPC 阵营 A 打 NPC 阵营 B"。
3. **目标 B 的对手被记成玩家**（引擎侧），战后 B 的仇怨/经历会写成"与你"而不是"与 A"。
   若模型要按"他和 A 的恩怨"叙事，需要我们在文案层做一次归因（可行，但要知道有这层偏差）。
4. **依赖配表**：少一行 `DungeonTrigger`，整条链就不生效（且是静默的 —— 钩子不被调用）。

⇒ 路线 A 适合"玩家要替队友出头 / 一起打"的产品语义；**不适合当作"NPC 互殴"的通用能力**。
