# `mode=together`：玩家参战（A 请玩家一起打 B）

> ⚠ **已归档**。现状见 [`docs/features/npc-duel.md`](../../features/npc-duel.md)。本文只保留当时的排查过程与被推翻的中间结论，**追溯「这个结论怎么来的」时才需要读**。
>
> **结局**：`mode=together` 已真机验过（16:30 那场）；§7 的验收清单留着做回归。
>
- **起止**：2026-10-27
- **触发**：用户原话：A 调用攻击工具，对象是 B，实际是让 B 攻击玩家，同时把 A 拉进去
- **涉及**：`csharp/drama/NpcDuel.cs`


---

## 0 · 两个分支的口径（10-27 用户定）

| mode | 谁参战 | 玩家角色 |
|---|---|---|
| `solo`（默认） | 只有 A 与 B | **旁观**，不参战 |
| `together` | A + 玩家 + 队友 | **参战**，当发起方 |

判据是"**玩家算不算这一场**"，不是"打还是不打"。

> 名字为什么是这组：早先用过 `watch`/`join`（引擎视角），但 `join` 容易被读成
> "加入队伍"、`watch` 读不出"这是默认行为"。`solo`/`together` 是玩家的语言。
> 渲染层 `text_render.py` 与 `NpcDuel` 的结果字段都跟着用 `solo`/`together`，
> 旧名 `watch` 只为兼容保留。

## 1 · 一句话

`world_ai_action(op=attack, target=B, mode=together)` ⇒ **玩家当发起方**打 B，
玩家的队友（若 A 是队友则含 A）由引擎自己拉进同一场战斗。

**整条技术内容就是一行**：

```csharp
player.CreateAction(new UnitActionRoleAttack(B), true);   // 接收者必须是玩家
```

再往外一层（确认窗、队友注入、战后归队、写经历）**一个字都没改**，全部复用。

---

## 2 · 为什么"复用现成的确认窗"是对的（推翻早先的错判）

我 10-27 先说"`CreateAction` 是同步链，中间没机会等玩家点确认窗"——**错**。
反编定案（`GameAssembly.dll`）：

```
WorldBattleMgr::IntoBattle@0x27FAAA0      ← 建数据 + 弹战前窗，不进场景
    IntoBattleInit(...)                   @0x27FAC0E
    UIMgr::OpenUI(...)                    @0x27FACF9
    UIMapBattlePre::InitData(...)         @0x27FAD59   ← 战前窗在这
WorldBattleMgr::IntoBattle@0x27FAD70      ← 薄壳（dungeonID 填 -1 转上面那个）
WorldBattleMgr::IntoBattle@0x27FADA0      ← 真正进场景
    ← 由 UIMapBattlePre::<UpdateUI>b__10_3（玩家点「应战」）调用
```

`UIMapBattlePre::InitData` 全仓**只有 4 个调用者**：

| 调用点 | 归属 |
|---|---|
| `0x27FAD59` | **`WorldBattleMgr::IntoBattle`@`0x27FAAA0`** |
| `0x224F482` | `DramaFunction::DramaUnitBattle` |
| `0x36FF5BD` | `UnitActionRoleAppellationAttack::<BattleDungeon>b__13_0` |
| `0x3ADA3B5` | 匿名 |

`UnitActionRoleAttack::OnCreate`@`0x3702280` 结尾是 `jmp CallQueue::Run`（`0x370241D`）——
**队列驱动**，`CreateAction` 返回时战斗还没开始。

⇒ "弹窗 → 等玩家点 → 才进场景"在引擎里本来就是**两个阶段**。
  `BattleFlowGate` 采样 `preShowing` → `TeamJoinGate` 弹「一起应战 / 我一个人」→
  玩家点完 → `DungeonTrigger` → `TeamContainerInject` 取 answer → 队友进场。
  **一个挂点都不用新加。**

---

## 3 · 这就是引擎自己的路

`UINPCInfoProp::OnAttackClick`@`0x330CC30`（玩家在 NPC 面板点「攻击」）：

```text
0x330CD24  call UnitActionRoleAttack::.ctor        ← new UnitActionRoleAttack(那个NPC)
0x330CD3F  call 0x3F877C0 ; mov rdx,[rax+0x10]      ← 取玩家 = g.world.playerUnit
0x330CD67  call [r9+0x178]                          ← CreateAction(player, atk, isCreate)
```

同一款代码路径，我们只是把"玩家亲手点"换成"NPC 请你一起上"。

### ⚠ 唯一的坑：接收者不能写错

| 写法 | 走哪条 | 结果 |
|---|---|---|
| `player.CreateAction(new UnitActionRoleAttack(B))` | `PlayerToNPCAction` | **真场景 + 战前窗**（要的） |
| `A.CreateAction(new UnitActionRoleAttack(B))` | `NPCToNPCAction` | **直接结算、没有场景**（`0x183709480`，无 `IntoBattle`） |

同一个动作类，`rcx` 是谁就完全两码事。

---

## 4 · 真机已验过的证据（`Player.log` 16:30 那场）

那场是 `world_ai_action(attack, target=唐乐咏)`（`target` 留空 ⇒ 分派到现有
`case "attack"`，即"NPC 打玩家"），但它走的是**同一条 `UnitActionRoleAttack` + 同一个战前窗**：

```
[UnitAction] attack 已挂起等待玩家选择 slot=3          ← CreateAction 已返回，战斗未开始
[BattleFlow] PreBattle ⇐ 页 20203 上有开战按钮 100003   ← 中间夹着一层剧情页
[BattleFlow] PreBattle ⇐ 点了开战按钮 100003
[BattleOutcome] 16:30:50.702 弹出进战斗的预告窗（等你点应战）   ← UIMapBattlePre 来了
[TeamJoin] 本次不问：没有队友（队=0）                    ← 唯一原因是队空
[BattleFlow] 采样 应战窗=True 战斗中=False ⇒ PreBattle
[BattleFlow] InBattle ⇐ IntoBattle(...)
```

而队友真的进过场（16:28 齐莺那场，`dungeonID=11 type=40`）：

```
[TeamJoin] 弹出确认窗：对手=齐莺 队=2 窗模板=1 层顶=MapBattlePre
[TeamJoin] 玩家选择：一起应战（对手=齐莺）
[TeamInject] ★直塞完成★ 队员 2 人：唐乐咏 贡浩大
AddBattleUnit addBattleUnit,3,-1,10,2,1,2,0 / 4 / 5
[TeamInject][death] 队友倒下（OnUnitDie）贡浩大(AC7edF)
[TeamFollowup] 本场有 2 名队友参战 → 战后归队 + 写经历
```

⇒ **"战前窗会弹" 与 "队友会进场" 两件事各自都有真机样本**，
   `together` 只是把它们接在同一场里。

---

## 5 · 队友判据：A 必须是队友

`TeamContainerInject` 读的是 `TeamTool.Members()`，而它只认**号位气运**。
⇒ **A 不在队里就拉不进去**（那就是"玩家一个人上"）。

结果里如实回两个字段，让模型知道实际发生了什么：

```json
{ "mode": "together", "caller_is_mate": true, "team_size": 2 }
```

`caller_is_mate=false` ⇒ 别对玩家说"我跟你一起打了"。

---

## 6 · `spar` 对 NPC 仍然拒绝（不是没做，是做了会说谎）

`NpcDuel.Attack` 走 `npcFightUnits` + `IntoBattle(null, 4001, …)`，
那是**真战斗**——`RoleBattle` 的重伤/击杀/缴物一整套都会落。
把它标成"切磋"回给模型，模型就会对玩家说"我们切磋过了"，
而实际可能有人被打成重伤甚至死了。

要真切（不落重伤）得让 NPC 当 `UnitActionRoleDrill` 的接收者 —— **那条没验过**，不装作能做。

---

## 7 · 验收要看什么

真机跑一次 `world_ai_action(attack, target=B, mode=together)`，队伍里有 A，日志应出现：

```
[UnitAction][together] 玩家当发起方 ⇒ 已 CreateAction 对手=B(...) 请人者=队友(slot=N) 队=N
[BattleFlow] PreBattle ⇐ ...
[BattleOutcome] ... 弹出进战斗的预告窗（等你点应战）
[TeamJoin] 进入战前 ⇒ 本场可问（...）
[TeamJoin] 弹出确认窗：对手=B 队=N 窗模板=1 层顶=MapBattlePre
[TeamJoin] 玩家选择：一起应战（对手=B）
[TeamJoin] 决斗开打：id=11 type=40 表态=Yes
[TeamInject] ★直塞完成★ 队员 N 人
```

### 各步缺失分别意味着

| 没出现的那行 | 说明 | 下一步 |
|---|---|---|
| `[UnitAction][together]` | `CreateAction` 被拒（返回码非 0，日志里有 `code=`） | 看 `NativeRefuseWhy` 那句（精力/同格） |
| `弹出进战斗的预告窗` | `PlayerToNPCAction` 这条分支不弹 `UIMapBattlePre` | 退回"前置问"方案（见 §8） |
| `弹出确认窗` | 战前窗弹了但 `ShouldAsk` 拒了 | 看 `本次不问：…（队=N）` 的原因 |
| `★直塞完成★` | 玩家点了「我一个人」，或容器映射变了 | 看 `决斗：玩家未同意` / `Clear 后 npcUnits` |

### 一个已知的语义错位（不影响功能，但要知道）

`TeamJoinGate.PickAttacker()` 优先取 `DramaNativeClaim.CurrentChainUid()`（op=attack 时），
而 `together` 里那张凭证的 uid 是 **B**（对手），不是 A。
⇒ 确认窗的**立绘/人名显示 B**，文案是「**B** 来势不善。可要叫上同行之人，一起应战？」。

从玩家视角这仍然说得通（"你要打 B，带不带人？"），但它**不是**"A 请你一起上"的口吻。
若要改成 A 的口吻，得给 `TeamJoinGate` 单独一条 `together` 的文案支路 —— 暂不做，
先确认功能通了，措辞后面再调。

---

## 8 · 备选方案（仅当 §7 第 2 行缺失时）

若 `PlayerToNPCAction` 这条分支**不**弹 `UIMapBattlePre`，就得在 `CreateAction` **之前**
先弹我们自己的确认窗、等玩家点完再发起。代价：

- 需要"异步等玩家"的状态机（`DramaGate` 已有 `TryDefer` 那套，但确认窗不是剧情窗，
  采样点要新加）；
- 工具结果要挂起在"等玩家表态"上，而不是"等战斗"。

**在跑过 §7 之前不要写这套** —— 证据不支持它。
