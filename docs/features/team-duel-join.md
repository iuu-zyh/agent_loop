# 队友进决斗（攻击 / 寻仇，`type=40`）—— 实现现状

> **一句话**：攻击/寻仇开出来的战斗，本就是一个 `type=40` 的普通副本（`dungeonID=11`）。
> 队友进不去不是"引擎不支持"，而是我们在 `DungeonTrigger` 的 `dungeonType` 白名单里把 40 排除了。
> 所以这件事**不需要造新机制**：新增一行触发器 + 在应战窗上问一句 + 战后按类型写另一套经历。
>
> 调研、设计与逐轮真机记录在 [`docs/archive/2026-10-25-队友进决斗/`](../archive/2026-10-25-队友进决斗/README.md)。
> 组队机制主线（副本那条）见 [`team.md`](team.md)。

---

## 1. 决斗这场战斗的真实身份

| 项 | 值 |
|---|---|
| dungeonID / type | `11` / `40`（官方语义「决斗」） |
| className / sceneBaseID / roomBaseID | `1000`（`SpecialBattle1000`）/ `11` / `11` |
| 走哪个重载 | `WorldBattleMgr.IntoBattle(WorldUnitBase, Int32, Action, WorldBattleData)` @RVA `0x27FADA0` |
| 触发器唯一评估点 | `SceneBattle::Init`（xref `ConfDungeonTrigger::GetItem` 只有 1 个调用方） |

真机日志原文：`[BattleOutcome] 弹出进战斗的预告窗` → `进入副本：11，等级：1`
→ 栈里 `WorldBattleMgr:IntoBattleInit(Vector2Int, ConfDungeonBaseItem, …)` / `UIMapBattlePre:<UpdateUI>b__10_3()`
→ `[BattleOutcome] 进入战斗`。

## 2. 为什么队友进不去

注入只有一个入口：`TeamContainerInject.Prefix` 钩 `BattleFunction.AddBattleNPCData`。
而 `addBattleNPCData` 是**由配表 `battleFunction` 驱动**的 —— 白名单不含 40，
`addBattleNPCData` 根本不会被调，钩子连跑都跑不起来（真机对照：2101/4801/4901 都有
`★直塞完成★`，`进入副本：11` 之后一条都没有）。

**为什么不能把 40 直接塞进 `MID&001`**：那一行的 `battleFunction` 里带
`setDramaUnitData_MID&003_0_0_0`，它改的是**剧情单位数据** —— 决斗的战后处置页 `20403`
会被换掉。影响未验证，不赌。同理不能进 `MID&003`（它含 `clearBattleNPCDatas_0`，会把刚注入的队友清掉）。

## 3. 配表改动（最终落地）

位号：`Mod = -803_158_451`。`MID&001/002/003` = `Mod+1/+2/+3`，`MID&004` = `Mod+4`。

| 表 | 行 | 改动 | 用途 |
|---|---|---|---|
| `DungeonTrigger` | `MID&004`（**新增**） | `dungeonType=40`、`triggerType=2`、`dramaFunction=0`（刻意留空）、`battleFunction` 与 `MID&001` 逐字相同 | **决斗里注入队友** |
| `DungeonTrigger` | `MID&002` | `dungeonType` 末尾**追加** `\|40`（53 → 54 类），其余字段一字未动 | **重伤/阵亡链**（`BattleEffect 6009` → `OpenDrama(190)`），唯一改值的既有行 |
| `DungeonTrigger` | `MID&001` / `MID&003` | **不改** | 副本注入 / 战后清理（改了会互相踩） |
| `RoleLogLocal` | `MID&905` / `906` / `907` | `eventLogTeamDuel` / `…Drama` / `…Hurt` | 决斗三套经历文案（`与{0}并肩应战{1}。` 等） |
| `DramaDialogue` | `MID&200` | 新增，`options=MID&201\|MID&202` | 确认窗壳行 |
| `DramaOptions` | `MID&201` / `MID&202` | 「一起应战」/「我一个人」 | 两个按钮（缺了会"点了没回调"） |

备份：`*.xlsx.bak_1025_before_duel`（整体）、`*.bak_1026_2359_before_duel_mid002`（改 MID&002 前）。

`addBattleUnit_3/4/5_…_2_…` 里那个 `2` 是**队伍类型 = 盟军**（官方《配置对照表》
sheet「副本作用功能」：1=玩家方/召唤物、2=盟军、3=怪物方、4=不参战 NPC）。

## 4. 三条链

### 判定

权威判据：`g.world.battle.data.dungeonBaseItem.type == 40`（`ModIds.DungeonTypeDuel`）。
询问时**不需要** type（那时还没有 `battle` 对象）—— 答案先存，注入时才判，非决斗就不消费。
备份判据：`IntoBattle` 补丁里读 `int` 参数得 dungeonID → `g.conf.dungeonBase.GetItem(id).type`。

### 询问

- 采样：`TeamJoinGate.Tick()`，0.25s。
- 判据：`WorldStateFacts.BattlePreShowing()` 读 UI **层顶节点名**含 `MapBattlePre`。
  ⚠ 节点真名比类名少 `UI` 前缀 —— 按 `UIMapBattlePre` 查**恒 false 且不报错**（踩过）。
- 五条前置（`TeamJoinCore.ShouldAsk`）：应战窗亮 / 有队友 / 知道对手 / 我方窗没开 / **本场未表态**。
  最后那条防"窗盖住应战窗 → 流程重进 PreBattle → 二次弹窗顶掉第一次的选择"。
- 文案：「一起应战」/「我一个人」，正文 `who + " 来势不善。可要叫上同行之人，一起应战？"`。
- 弹窗后再打一拍 `确认窗已显示（层顶=…）` / `⚠ 确认窗没显示出来` —— 因为模板缺行/被压住时
  `DramaTool.OpenDrama` 是**静默**的，不主动核就只能看到"没带队友"这个结果。

**为什么必须问在这儿**：应战窗关闭与 `IntoBattle` 在**同一毫秒**（`18:51:32.349`），
中间没有可用窗口；`IntoBattle` 是点应战后**同步**调的，那时场景已在加载。

### 注入

`TeamContainerInject.Prefix` 决斗三分支：

1. `isDuel && _decided` → 跳过（`addBattleNPCData_0/_1/_2` 三个 token 各调一次，必须幂等）；
2. 玩家同意且有队 → 清 `npcUnits` → 按号位压入 → `★直塞完成★` → `return false`；
3. 其余 → **清空容器 + `return false`**。

第 3 条是这套设计的关键：**"不带队友"不能靠放行原方法** —— 原方法走 `ConfNpcCondition MID&003`
（条件就是三枚号位气运 `MID&51/55/56`），放行 = 把队友重新选出来塞进容器 = **变相同意**。

失败姿态：决斗分支自身异常 → **fail-closed**（按未同意，不带队友，也不放行原方法）；
`Prefix` 外层异常 → fail-open 放行（宁可回到引擎选人，不挡战斗）。

`_decided` 的两个清零点：`OnBattleStarting`（`IntoBattle` 那一拍）、`ResetForBattle`（`BattleEndFront`）。

### 收尾

`TeamBattleFollowup.OnBattleEndFront` 第一拍读 `_kind`（`ReadBattleKind()`：`type==40 → KindDuel`），
`OnBattleExit` 里按它分派 `WriteDuelLogs` / `WriteJourneyLogs`。

之后三件事**副本与决斗共用** `ApplyHurtAndLeave()`：

```
归队传送（UnitActionMoveNPC + 回读 SamePoint）
  → 挂「内外重伤」（已带则跳过）
  → 摘号位气运 = 解除组队
```

战败处置页那条链（190）也一样：`BattleEffect 6009` 的死亡回调 → `DramaTool.OpenDrama(190)`
→ 我们的 `InitData` 前缀换立绘 → `addNPCLuck_1_MID&52`。

## 5. 现状

**已真机验证**

- 决斗里**队友真的进场**（`★直塞完成★ 队员 2 人` → `addBattleUnit,3/4/5` 三条执行 → `队友倒下（OnUnitDie）`）。
- 归队 / 挂重伤 / 摘气运三步生效。
- 确认窗：4 次弹窗 4 次回选择，按钮文案与点击回调都通。
- 战斗窗 = [队友进场 … BattleExit]，处置页与处置选择**在窗内**；开场页 `20203` 在窗外（前情不写）。

**必须人眼确认的一件事**

- 确认窗是不是**真的画在应战窗上面**。日志只能证明"它被创建且可见"，
  若画面被盖住，`确认窗已显示` 这一行会与实际相反 ⇒ **看画面 + 对日志**一起做。

**未验证 / 缺口**

| 项 | 说明 |
|---|---|
| U1 `MapBattlePre` 的适用范围 | 是否只在"被攻击/寻仇"类战斗出现 —— 已降级为观感问题（`type≠40` 不注入、表态当场作废） |
| U4 决斗是否照常触发 `BattleEndFront/BattleExit` | 有间接证据（`[TeamFollowup]` 三行），没有显式闭环 |
| U5 处置页 → `BattleExit` 的先后 | 只有 1 次实证（玩家胜、收下物品）；玩家败 / 逃跑 / 直接离开未跑 |
| 190 补好后 | 仍未真机验过（`190 立绘修正` 在两份日志 0 命中） |
| `type` 读不到时 | 按副本处理 ⇒ 队友会"不请自来"（自认的代价），未真机触发过 |
| 弹窗失败路径措辞 | 设计写"打 ERROR"，实现是 `TryTip` + `onNo`（级别更轻） |

## 6. 相关代码

| 文件 | 职责 |
|---|---|
| `csharp/state/TeamJoinCore.cs` | 判据核（纯逻辑，`JoinAnswer{None,Yes,No}`、`ShouldAsk`、`Take`） |
| `csharp/tools/TeamJoinGate.cs` | 接线：0.25s 采样、弹窗、模板注册、触发器自检 |
| `csharp/tools/TeamContainerInject.cs` | 容器直塞 + 决斗三分支 + 幂等 + 失败姿态 |
| `csharp/tools/TeamBattleFollowup.cs` | 战后收尾：归队 / 重伤 / 摘气运 / 写经历（决斗分支） |
| `csharp/game/ModIds.cs` | `DungeonTypeDuel=40`、`DramaTeamJoinBase=Mod+200`、`LogTeamDuel*=905/906/907` |
| `tests/test_team_duel_join_contract.py` | 23 项跨语言契约 |
| `tests/test_team_tool_contract.py` | 白名单例外（`MID&004` 只许 40 / `MID&002` 允许含 40） |
| `tests/test_state_module_boundary.py` | `state/` 不许反向引用 `drama/` 业务类型（`TeamJoinGate` 因此在 `tools/`） |
