# 03 · 路线 B：引擎原生观战（两个 NPC 单挑，玩家旁观）

> ⚠ **已归档**。现状见 [`docs/features/npc-duel.md`](../../features/npc-duel.md)。本文只保留当时的排查过程与被推翻的中间结论，**追溯「这个结论怎么来的」时才需要读**。
>
> **结局**：路线 B 落地为 `solo`：走引擎自己的 `dramaUnitBattle_*`，玩家旁观。§8 起是施工与真机记录。
>
- **起止**：2026-10-25 ～ 2026-10-27
- **触发**：同上；本文是「NPC 打 NPC」的直接解
- **涉及**：`csharp/drama/NpcDuel.cs`


---

## §1 主路径 B1：走引擎自己的剧情函数 `dramaUnitBattle_*`

### 1.1 只要一行

```csharp
DramaFunctionTool.OptionsFunction("dramaUnitBattle_1_2", new DramaFunctionData {
    unitLeft  = unitA,   // 1 号槽
    unitRight = unitB    // 2 号槽
});
```

这一行的语义（反编实证，见 [01-F4](../../reference/engine-facts.md)）：

```
new WorldBattleData()
battleData.npcFightUnits = (unitLeft, unitRight)     // 引擎自己写
g.world.battle.IntoBattle(toUnit: null,
                          dungeonID: 4001,
                          onInitCall: null,
                          battleData)
UIMapBattlePre.InitData(...)                          // 战前窗
```

⇒ **引擎把"谁跟谁打"完全交给 `npcFightUnits`**，`toUnit` 传 null 也成立。
这就是"玩家不参战、只看"的官方形态。

槽位取值（`DramaFunction::GetUnitInType`@0x182253A90 实测）：
`1 = DramaFunctionData.unitLeft`、`2 = unitRight`、`0 = unitA`、**`-1` = 玩家**。
后一条意味着同一个函数也能写"玩家 vs NPC"（`dramaUnitBattle_-1_1`），不必另造链路。

### 1.2 为什么它比"自己拼 battleData"优先

| | 走 `dramaUnitBattle` | 自己拼 `WorldBattleData` |
|---|---|---|
| 谁负责建 `npcFightUnits` / 选副本 / 开战前窗 | 引擎 | 我们（每一处都要复刻，漏一个就静默少东西） |
| 已知可用的调用上下文 | 第三方 mod 在 UI 按钮回调里调过（实证） | 无实证 |
| 出问题时的定位成本 | 低（要么开窗要么不开） | 高（自己拼的对象，字段错一位就静默） |

### 1.3 第三方 mod 的完整用法（可直接照抄）

`/mnt/f/DecompDump/dump/types36_fulldll.txt:20290`：

```csharp
g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否需要观战？", 2,
    /* 同意 */ delegate {
        CloseUI();
        DramaFunctionTool.OptionsFunction("dramaUnitBattle_1_2", new DramaFunctionData {
            unitLeft = unit3, unitRight = toUnit });
        isFight = true;
    },
    /* 拒绝 */ delegate {
        WorldUnitBase winner = UnitActionRoleBattle.UnitBattle(unit3, toUnit, (SchoolPostType)0);
        … // 自己写经历：eventLogTotal999022 / 999023
    });
```

它把"看不看"做成一次玩家选择：**看 = 真场景观战；不看 = 纯结算**。
这正是我们要的两档语义。

---

## §2 备选 B2：自己拼 `WorldBattleData`（仅当 B1 走不通）

```csharp
var bd = new WorldBattleData();
bd.npcFightUnits = new DataStruct<WorldUnitBase, WorldUnitBase>(unitA, unitB);  // 0xD8
bd.dungeonBaseItem = g.conf.dungeonBase.GetItem(4001);
g.world.battle.IntoBattle(null, 4001, null, bd);
```

字段与调用都能对着反编写出来，但要注意：

- `DramaFunctionData.battleData`（0x48）也是给这条链预留的口子 —— 若 B1 的剧情串不好用，
  可以 `OptionsFunction("dramaUnitBattle_1_2", new DramaFunctionData{ unitLeft=A, unitRight=B, battleData=bd })`
  把已经拼好的对象塞进去，而不是从零调 `IntoBattle`；
- `isRealBattle`（0x52）/ `isSelfBattle`（0x50）/ `fightDialogueMap`（0xA8）
  这三个标志位引擎在剧情函数里是怎么设的，我们还没逐个核对（见未验证 U6）；
- **没有实证**表明脱离剧情函数直接调 `IntoBattle(null, 4001, …)` 安全 —— 这是 B2 只在 B1 失败时启用的原因。

---

## §3 备选 B3：只要结果，不起场景

```csharp
WorldUnitBase winner = UnitActionRoleBattle.UnitBattle(unitA, unitB, (SchoolPostType)0);
```

- 纯算术（内部 `CommonTool::Random`），无场景、无镜头；
- **不写经历日志**（第三方 mod 自己补 `AddLogMain`）——我们也要自己补；
- 适用：玩家拒绝观战 / 两人不同格 / 玩家正忙 / 只想让世界"自己运转"。

> 这条同时是**路线 A 的降级口**：不同队时 A 打 B 可以走它（快速、无打扰）。

---

## §4 接入工具链

### 4.1 触发点

`world_ai_action(op=attack, target=B)`（发起方 A 恒为当前对话 NPC）：

| 条件 | 走向 |
|---|---|
| `target == 玩家` | **现状不变**（A 打玩家，`NPCToPlayerAction`，真场景） |
| `target == 另一个 NPC` 且玩家与 A 同队 | **路线 A2**（[02](路线A-玩家参战.md)）：玩家代打，A 作盟军 |
| `target == 另一个 NPC` 且玩家未参战 | **路线 B**：先问"要不要观战" → 看(B1) / 不看(B3) |

新增一个可选参数而不是新 op：`watch`（三态 `ask` / `yes` / `no`，缺省 `ask`）。
理由：模型侧不要多学一个工具/枚举；"打不打得起来"与"看不看"是同一个动作的两个选项。

### 4.2 回合不许提前收口

观战可能几分钟。复用现成的挂起机械（`csharp/drama/DramaGate.cs` + `BattleOutcome`）：

1. `DramaGate.MakePendingMarker(slot)` 把 response 憋住（模型不会在仗打完前乱说"我赢了"）；
2. C# 每 30s 发 `type:"pending"` 心跳 → Python `_await_response` 顺延 deadline（已有）；
3. 收口证据：`BattleEndMgr.BattleEnd` / `BattleEndFront`（项目已有 `BattleEndHook`）。

### 4.3 结果怎么给模型

不要自己判胜负，**读场面事实**（与本仓既有哲学一致：不解析、只读原话/事实）：

| 来源 | 给什么 |
|---|---|
| `battle.data.npcFightUnits` | 确认"打的是这两个人"（防串场） |
| 战后各单位的存活/`isDie` | "谁倒下了"（事实） |
| `MathfFightDialogueMap` / 段区间 | 战中原话（若有） |
| `UnitBattle(a,b)` 的返回值 | **仅**在不观战分支用（那是引擎算的胜负） |

若读不到（U3 未定），**如实回"没看到结局"**，不许编 —— 与 `BattleOutcome` 的
"字段缺失 ≠ 否定"同一条规矩。

### 4.4 Python 侧文案

`tools/text_render.py` 的 `attack` 渲染要能表达三种形态：

```
（你与X并肩迎战Y）      ← 路线 A
（X与Y在你眼前动手）    ← 路线 B 观战
（X与Y的交手已了）      ← 路线 B 后台结算
```

渲染层**只翻译 C# 给的事实**，不在 Python 判胜负（本仓铁律：叙述唯一来源在 `text_render`，
但事实只有一个来源在 C#）。

---

## §5 关于「入门大比 / 宗门大比」——能复用，但不建议当通用入口

### 5.1 它们各自是什么

| 玩法 | 代码 | 玩家角色 | NPC 对局怎么跑 |
|---|---|---|---|
| **入门小比 / 晋升比试**（1v1） | `MapBuildSchoolPostBase::PostBattle`@0x183CFF5E0 → `MatchFight` | 参赛者 | 玩家场次 → `MatchFight::FightNext` → `WorldBattleMgr.IntoBattle`（真场景） |
| **宗门大比**（多宗门） | `SchoolBigFight`（积分赛→擂台赛→争锋） | 宗主/长老/弟子，可**观战+督战** | `OnFightBeginAsNpc` **无 IntoBattle**；`BigFightBattleSimulation::Run` 用 `CommonTool.Random` ⇒ **模拟** |

### 5.2 为什么"能复用"但"不该复用"

**能**：`SpecialBattleSwitchHandler.GoTo/GoBack/SwitchAsync` 就是"在一场战斗里切换看谁"的现成实现，
`UIMatchFight*` 一整套擂台 UI 也在。真要做"玩家在台下看两个 NPC 打擂台"，这条路是成品。

**不该**当通用入口，原因三条（都是配表事实）：

1. **它由月结事件驱动**：`SchoolBigFight.json` 的 `openMonthInterval=36`、`notifyMonths=3`、
   `dayCost=30` —— 大比是**每 36 个月一次的赛事**，不是随时能开的一场架；
2. **它有完整状态机**：报名 → 积分小组赛 → 擂台赛 → 争锋，
   `SchoolBigFightDrama.json` 里 45 条阶段剧情（`scene_pointRaceBegin` / `scene_arenaEnter` / …）
   都要按秩序走；单独抽"打一场"出来，等于把状态机拆一半，后患无穷；
3. **NPC 对局是模拟**，不是场景 —— 想"看"，还得走 §1 的 `npcFightUnits`，绕一圈回到原点。

⇒ **结论**：大比/小比作为**玩法**可以另立项目（复用它的 UI 与 `MatchFight`），
但**不要**把它当作"让两个 NPC 打一架"的底层能力。

---

## §6 改动清单（最小可用）

| 层 | 文件 | 改动 |
|---|---|---|
| C# | **新增** `csharp/battle/NpcDuel.cs` | `TryWatch(a, b)` → `OptionsFunction("dramaUnitBattle_1_2", …)`；`Resolve(a, b)` → `UnitActionRoleBattle.UnitBattle`；两个都失败返回 `fail(reason)` |
| C# | `csharp/tools/ToolExecutor.cs` `case "attack"` | target 为 NPC 且玩家未参战 → 按 `watch` 参数分派到 `NpcDuel`；观战分支返回 `DramaGate.MakePendingMarker` |
| C# | `csharp/drama/BattleOutcome.cs` | 新增"观战"台账（`_op="npc_watch"`）：`npcFightUnits` 对得上才认；收口读场面事实 |
| C# | `csharp/tools/UnitActionHooks.cs` 或 `BattleEndHook` | 确认观战结束也会走 `BattleEndFront`（U3 探针；不走则补挂点） |
| Py | `tools/schemas.py` | `WORLD_AI_ACTION`：`target` 放开 + 新增 `watch` 三态 |
| Py | `tools/text_render.py` | `attack` 三分支文案（见 §4.4） |
| 配表 | — | **不需要改配表**（4001 是游戏自带副本；这正是 B 比 A 干净的地方） |

---

## §7 风险与失败姿态

| 风险 | 处置 |
|---|---|
| U1：非剧情上下文调 `OptionsFunction` 不生效 | 失败即降级到 B3 纯结算，结果里标 `watched:false`；绝不空等 |
| U4：两人不同格 / 精力不足 | **不作为前置**：观战副本在战场内重新摆位（`BattleMapMgr::PointToWorld`），与地图坐标无关；精力由引擎在进战斗时照常结算 |
| 观战中途玩家退出/读档 | `BattleExit` / 世界重载 → 按已抓到的部分收口（沿用 `BattleOutcome` 的重载分支） |
| 观战把玩家拖进战斗 | `SpecialBattle79::OneIntoRoomEnd` 只 `CreateUnitHuman` 两次、没有 `CreateUnitPlayer`（反编）⇒ 玩家不是参战单位；真机只需确认画面表现（U2） |
| 引擎双写经历（U5） | 探针确认后再决定补不补；**宁可少写，不可双写** |

---

## §8 已实现（2026-10-26）

### 8.1 代码地图

| 文件 | 改了什么 |
|---|---|
| `csharp/drama/NpcDuel.cs` | **新增**。观战唯一入口：`Attack()` / `NoteBattleStarted()` / `Tick()` / 事件收口 / 结果组装 |
| `csharp/game/ModIds.cs` | 新增 `DramaNpcDuelWatch = Mod + 210`（挂起槽位键，不是剧情窗） |
| `csharp/tools/ToolExecutor.cs` | `WorldAiAction` 里"对象只能是玩家"的硬拒，`attack` 改成放行 → `NpcDuel.Attack(wub, target, cpName)`；其余 op 仍拒 |
| `csharp/state/BattleFlowHooks.cs` | `BattleStartHook.Prefix` 多报一句 `NpcDuel.NoteBattleStarted(sig)`（开打的第一手证据） |
| `csharp/state/BattleFlowGate.cs` | 采样处多调一次 `NpcDuel.Tick(pre, inBattle)`（复用现有 0.25s 采样，**不新增定时器**） |
| `csharp/app/ModMain.cs` | 初始化里 `NpcDuel.EnsureInstalled()`（订阅 `BattleEndFront`/`BattleExit`） |
| `tools/schemas.py` | `world_ai_action.target` 描述改为"**不填 = 玩家**；`attack` 可填 NPC 真名（当场开战，玩家旁观，打完才回）" |
| `tools/text_render.py` | 新增 `_render_npc_watch()`：`mode=watch` 分支，按 `battle_seen`/`winner` 三档措辞；10-26 追加 `judgement` 一句（见 §8.8） |
| `csharp/drama/BattleEndHook.cs` | 10-26：`Prefix` 改成 `bool` + 带 `__instance`/`__originalMethod`；先照原顺序报信，**最后**问 `NpcDuel.NoteBattleEnding` 要不要拦住这次收尾（见 §8.8） |
| `csharp/drama/DramaSegmentProbe.cs` | 10-26：`ClickOption` 的 prefix 里多转一次 `NpcDuel.NoteOptionClicked(__0)`（收处置页的选择，单独 try/catch） |

> `csharp/drama/*.cs` 是 csproj 里已有的通配包含（`<Compile Include="drama\*.cs" />`），
> **新文件不用登记** —— 这是选 `drama/` 而不是新建 `battle/` 目录的原因。

### 8.2 实际调用链

```
模型：world_ai_action(op=attack, target=「姜萌」)
  → ToolExecutor.WorldAiAction：ResolveCounterparty 拿到 NPC 单位（cpName 非空）
  → NpcDuel.Attack(发起方NPC, 目标NPC, "姜萌")
      ├─ 守卫：同一人 / 任一方是玩家 / 已有观战在跑 → 直接 Fail（人话理由）
      │    注：**没有"同格"前置** —— 引擎与本家剧情都不要求（见 §8.5），加一条等于给主用例上锁
      ├─ DramaGate.TryDefer(+210, 180s, beat:true) 取槽位
      ├─ _watch 就位            ← ★ 必须在调用之前（下面这条是同步的）
      └─ DramaFunctionTool.OptionsFunction("dramaUnitBattle_1_2",
              new DramaFunctionData { unitLeft = 发起方, unitRight = 目标 })
            └─ DramaFunction::DramaUnitBattle → 两边都不是玩家 → 直接
               g.world.battle.IntoBattle(toUnit:null, dungeonID:4001, onInitCall:null, battleData)
                 └─ battleData.npcFightUnits = (左, 右)
      ├─ 同步判"到底开没开"：BattleStartHook 已报信 或 g.world.battle.isBattle 为真
      │     └─ 都没发生 ⇒ 立刻 Fail（**不等超时**：这是事实，不是时长）
      └─ return DramaGate.MakePendingMarker(slot)   ← 本轮工具结果挂起

战斗结束（三条证据先到先算）
  ├─ BattleEndFront → Snapshot（世界单位还在，抓 hp / isDie）
  ├─ BattleExit     → 收口
  └─ isBattle 真→假（BattleFlowGate 采样边沿）→ 收口
        └─ DramaGate.Resolve(slot, {success:true, data:{...}})
              └─ WsClient 按 req_id 补发 → Python 拿到结果 → text_render 渲染
```

长战斗续期：`DramaGate.OnStillPending` 每拍回调 `NpcDuel.OnSlotBeat`，
**还在打就 `Renew`**（不是猜时长，是"看见了就延"）。

### 8.3 结果形状（给模型的那份）

```json
{"success": true, "data": {
  "op": "attack", "target": "姜萌", "counterparty": "姜萌",
  "mode": "watch", "watched": true,
  "battle_seen": true, "battle_finished": true, "crossed_battle": true,
  "left":  {"name": "江宇文", "unit_id": "…", "alive": true,  "hp": 120, "hp_max": 200},
  "right": {"name": "姜萌",   "unit_id": "…", "alive": false, "hp": 0,   "hp_max": 180},
  "winner": "江宇文"
}}
```

`winner` **只在恰好一方 `isDie` 时出现**；两边都站着就不给这个键
（渲染层据此说"胜负未定"，不替模型编）。

### 8.4 上机要看什么（grep 清单）

```
[NpcDuel] 发起观战 江宇文(8DQUkF) vs 姜萌(XXXX) slot=3 已开打=True
[BattleStartHook] 游戏开打：IntoBattle(WorldUnitBase, Int32, Action, WorldBattleData)
[NpcDuel] 已开打：IntoBattle(WorldUnitBase, Int32, Action, WorldBattleData)
[NpcDuel] 战斗结束快照：江宇文 hp=120/200 倒下=False ｜ 姜萌 hp=0/180 倒下=True
[NpcDuel] 收口（BattleExit）：江宇文 vs 姜萌 胜者=江宇文
```

判读：

| 看到什么 | 说明 |
|---|---|
| `发起观战 … 已开打=False` + 工具收到"没能开起来" | 引擎没接（两人不同格 / 副本 4001 未就绪 / 函数串解析失败） |
| 有 `已开打`、无 `收口` | 战斗还没结束，或场景被异常卸载（槽位 180s 后兜底过期） |
| `快照` 里两人都 `倒下=False` | 战斗判负不以 `isDie` 体现 → 结果只说"胜负未定"（**这是本实现最大的未知**） |
| 屏幕上没有战斗场景 | `isRealBattle` 默认 false 是否等价"不播场景"，见 §8.5 |

### 8.4.1 诊断开关（不必等模型主动出手）

```
<Mod根>/_diag_npc_duel.txt
─────────────────────────────
# 改一次文件 = 触发一次（mtime 变才重武装）
left=江宇文
right=姜萌
```

- 120 帧复查一次（与 `DiagSwitches` 同节拍），**只是一种"确定会跑"的入口**，
  触发后走的完全是生产链路（同一个 `NpcDuel.Attack`）；
- 它没有 `req_id` 可回，所以工具结果会被丢弃（`DramaGate.Resolve` 找不到请求方就静默），
  **战斗照常发生** —— 验场景时只关心屏幕与日志；
- 删文件即停用；文件里的名字要能在世界里解析出来（`UnitLookup.Resolve`）。

### 8.5 复核后：哪些问题已经在代码里答掉，哪些才需要真机

**答掉了（反编证据，见 [01 §F9](../../reference/engine-facts.md)）**

| 原问题 | 答案 |
|---|---|
| U1 非剧情上下文调 `OptionsFunction` 会不会不生效 | 分派是 `Split('|')` → 逐条 `Split('_')` → 按 `parts[0]` 比字符串，**与调用方在不在剧情里无关**；第三方 mod 就是在按钮回调里这么调的 |
| U6 `isRealBattle=false` 是不是"不播场景" | **不是**。场景由 `IntoBattle` 无条件 `LoadSceneAsync`；`type=79` 被 `WorldBattleData::InitData` **显式**置 `isRealBattle=0`，语义是"这一场不算真正的一次副本战果"（结算与 NPC 条件判定用）。**不要改成 `true`** |
| "打完之前判不出开没开打" | `isBattle` 在 `IntoBattleInit` 第 6 条指令置真，**同步**；`OptionsFunction` 返回时就能读 |
| 胜负有没有正式表示 | 有：`DungeonTrigger` 用 `isBattleData_SpecialBattle79winUnit_0/1` 读"左赢还是右赢" |
| 这一场是谁在台上打 | `SpecialBattle79::OneIntoRoomEnd` 里 `BattleUnitMgr::CreateUnitHuman` **正好两次**（左、右各一次），没有 `CreateUnitPlayer` |

**只能真机定**

| # | 问题 | 现在的姿态 |
|---|---|---|
| U2 | 玩家在观战场景里**看到什么**（镜头给谁、能不能操作） | 代码不假设：结果里只声明"玩家在旁观"（`spectator`），不描述画面 |
| U3 | 打完那一瞬，**世界单位**的 `hp/isDie` 有没有跟着变 | 双路取证：先读战场单位（引擎事实），读不到才退世界单位。日志把两路数值并列打出来，一次就能对账 |
| U7 | 这一场打完，两个 NPC 在世界里的状态会不会被引擎持久化（掉血/重伤/经历） | 不处理（不双写）。真机日志能看到 `isDie`/`hp` 的前后值 |

### 8.6 一次探针（真机只需要跑这一趟）

`<Mod根>/_diag_npc_duel.txt`（= `ModAssets\_diag_npc_duel.txt`）：

```
left=江宇文
right=姜萌
```

保存即触发一次（`mtime` 变才重武装；UTF-8 / GBK 都认）。它会写一份**完整报告**到
`<Mod根>/_diag_npc_duel_report.txt`（每次触发先清空），报告按时间轴分段：

| 段 | 记了什么 | 能判什么 |
|---|---|---|
| `[0]` 触发/解析 | 两人 uid、格、是否玩家、`isBattle`、`SceneBattle` 拿不拿得到 | 名字有没有认对人、战前状态 |
| `[1]` 调用 | `OptionsFunction("dramaUnitBattle_1_2", unitLeft=…, unitRight=…)` 的入参 + 返回后的 `Started/isBattle` | **分派成不成**（同步判据） |
| `[1]` 处理体 | `DramaUnitBattle` 后置钩子：`values=[…]` 与它取到的左右两人 | 分派到了处理体、取到的是不是我们要的两个人（挂不上会明说） |
| `[1]` 战斗数据 | `battle.data` 的副本 `id/type/sceneBaseID/roomBaseID`、`isRealBattle/isSelfBattle`、`npcFightUnits` | 进的是不是 4001、引擎存的是不是这一对 |
| `[2]` 战场名单 | `SceneBattle.unit` 全体：类型名 + 名字 + uid + hp + `isDie`；两种取数口径的条数 | **场上是哪几个**、玩家在不在台上、有没有真的生成两个战斗单位 |
| `[3]` 战中 | 每 2s 一行战场血量 | 有没有真的在打（血量在掉） |
| `[4]` `BattleEndFront` | 战场名单 + 世界单位前后值（hp/`isDie` 并列，含"战场 hp"与"世界 hp"两列） | **胜负从哪读**、世界单位会不会被改 |
| `[5]` `BattleExit` / 收口 | 证据来源、最终结果 JSON | 交付给模型的那份事实长什么样 |
| `[9]` 收尾 | 玩家在场与否、场上单位数 | 玩家身份 |

配套日志行（`Player.log` 里 grep `[NpcDuel]`）：

```
[NpcDuel] ★BattleEndFront/BattleExit/OnResolved 已挂★
[NpcDuel] DramaFunction.DramaUnitBattle=已挂（分派证明可用）
[NpcDuel]诊断触发：江宇文 vs 姜萌（走生产链路 Attack）
[NpcDuel] 发起观战 江宇文(8DQUkF) vs 姜萌(XXXX) slot=3 已开打=True
[NpcDuel] 战斗结束快照（战场单位＋世界单位）：… hp=… (战场 …) 倒下=…
[NpcDuel] 收口（BattleExit）：江宇文 vs 姜萌 胜者=江宇文
```

判读表：

| 看到什么 | 说明 | 怎么改 |
|---|---|---|
| `[1] 调用…已开打=False` | 引擎没接（副本 4001 没就绪 / 名字解析错） | 看 `[0]` 段；若处理体那行**也没出现** ⇒ 分派就没命中，检查函数串 |
| 处理体那行出现、但 `isBattle=False` | 分派到了、处理体提前返回（左右为空/相等/`values` 长度不够） | 处理体那行会打印它取到的左右**名字** |
| `isBattle=True` 但屏幕上没有战斗 | 与 `isRealBattle` 无关（它不控场景）；查 `SceneMgr` 加载 | 看 `[2]` 段有没有战场单位 |
| `[2]` 里没有这两个人、但有别的单位 | 场景起来了但人没生成 | 看 `[2]` 的两个口径条数是否不一致 |
| `[4]` 里"战场 hp"在掉而"世界 hp"不变 | 结算没回写世界单位 ⇒ 结果只报战场事实 | 结果里的 `hp` 仍取世界单位（与 `inspect_unit` 同口径），不混 |
| 两个人 `isDie` 都是 False | 这一场不是"打到倒下"收的（可能超时/双方都站住） | 结果不给 `winner`，渲染层说"胜负未定" |

> 报告里 `[2]` 段的两个口径条数（`口径 false=N true=M`）是**故意并排**的：
> `GetAllUnitIncludeDie(bool)` 的布尔会过滤单位种类，观战要的是"全体"，
> 而"全体"到底含哪些，只有真机能定 —— 两个都取、按引用去重，条数不一致就说明有过滤差异。

### 8.7 结果形状（v2，10-26 复核后）

```json
{"success": true, "data": {
  "op": "attack", "target": "姜萌", "counterparty": "姜萌",
  "mode": "watch", "watched": true,
  "battle_seen": true, "battle_finished": true, "crossed_battle": true,
  "spectator": "唐炎",
  "left":  {"name": "江宇文", "unit_id": "…", "alive": true,  "hp": 120, "hp_max": 200},
  "right": {"name": "姜萌",   "unit_id": "…", "alive": false, "hp": 0,   "hp_max": 180},
  "winner": "江宇文",
  "judgement": {"loser": "姜萌", "outcome": "kill", "label": "击杀此人"}
}}
```

三条塌缩规则（写死在 `NpcDuel.BuildResult`）：

| 情况 | 结果里长什么样 |
|---|---|
| 战场/世界两处都读不到某人 | 那个人**整个 `alive`/`hp` 都不出现**（字段缺失 ≠ 否定） |
| 读到两方、恰好一方倒下 | 给 `winner` |
| 读到两方、都没倒 / 有一方读不到 | **不给** `winner`，渲染层说"胜负未定，别自行断言" |
| 玩家名字读不到 | 不给 `spectator`，文案里不提"谁在看着" |
| **没走到战后处置**（没人倒下 / 页没开成 / 玩家还没选） | **不给** `judgement`（整块不出现，文案一个字都不加） |

`judgement`（10-26 追加）只在**真的拦住 `BattleEnd`、弹了原版处置页 20403、拿到了玩家的选择**
之后才出现。三个键各有归处：

| 键 | 含义 | 值从哪来 |
|---|---|---|
| `loser` | 被处置的那个人（**总是世界 NPC**，玩家或 null 时我们根本不拦） | 快照判出的败者 |
| `outcome` | 处置结局的机器码 | `kill` / `spare_took_items` / `insult` / `spare` / `pill` / `closed` |
| `label` | 玩家点的那一枚选项的**原文** | `ConfDramaOptionsItem.text`；读不到时用内置兜底串（同一句话，写死在 `NpcDuel.FallbackLabel`） |

> 渲染层（`tools/text_render.py::_watch_judgement`）：有 `judgement` 就补一句
> 「你上前处置了姜萌：击杀此人。」；没这个键**一个字都不多加**。`label` 缺失时用
> `outcome` 兜底成一模一样的中文（表在同文件 `_WATCH_OUTCOME_TEXT`，键与 C# 一一对应）。

---

### 8.8 战后处置页：在战斗里**拦住**一次 `BattleEndMgr.BattleEnd`（10-26）

> **10-27 复核补充**：这一节讲的是「怎么拦住收尾」；**五个选项各自怎么落实**已改到
> `UICustomDramaBattleNPCFailed2` 的原版回调上（击杀走 `UnitActionRoleDying` 死亡处理，
> 不是 `DelUnit` 瞬删；`CreateAction` 的接收者是玩家不是败者；`Release` 空表=一件不拿）。
> 详见 [01 §33](../../reference/engine-facts.md)。§8.8 下面的内容仍然有效，但别再照抄旧的选项实现。

#### 为什么必须拦

观战副本 4001 是 `type=79`，`WorldBattleData::InitData` 对它**显式**置 `isRealBattle=false`
（见 [01 §F9](../../reference/engine-facts.md)）⇒ 这种战斗里**单位不会死**（`isDie` 恒 false、世界单位的
hp 也不回写），引擎自己更没有"败者求饶"这一步。原版决斗（`type=40`）能停 4 分钟，
是因为它的战斗类在结束前**自己等那扇窗**（真机：18:53:54 记页 20403 → 18:57:57 玩家才点
→ 之后才 `BattleEndMgr.BattleEnd`，见 [队友进决斗/事实链.md F8](../2026-10-25-队友进决斗/事实链.md)）。
79 没有这一步，所以要我们自己拦。

#### 拦在哪、为什么是干净的

```text
SpecialBattle79::BattleEnd@0x2BFDCD0
  …
  movss [rcx+0x18], xmm6      ; 结算倍率写进 BattleEndMgr 实例
  movzx edx, bl               ; edx = isWin
  jmp   0x1828FA0D0           ; ← **尾跳**到 BattleEndMgr::BattleEnd，之后什么都不做
```

`BattleEndMgr::BattleEnd` 是整个 79 收尾的**最后一个动作**（调用方在它之后什么都不做）
⇒ `Prefix` 返回 `false` 就是干净的"整场还没结束"，没有"半结束"的中间态；倍率在进它之前
已经写进管理器，放行重入不会丢。

`BattleEndMgr::BattleEnd(Boolean)` 是**实例方法**：
`il2cpp_disasm.py 0x28FA0D0` 里 `rcx` 被反复当对象读写（`cmp BYTE PTR [r14+0x10]` /
`mov BYTE PTR [r14+0x11],al` / `mov BYTE PTR [r14+0x10],1`），`dl` 才是那个 bool。
所以放行是 `__originalMethod.Invoke(__instance, new object[]{ isWin })`，不是 `Invoke(null, …)`。

```text
BattleEndHook.Prefix(bool arg0, __instance, __originalMethod)
  ① 无条件日志  ② BattleOutcome.NoteBattleEnded(hard)  ③ DeferredActionQueue.NoteChainOver
  ④ NpcDuel.NoteBattleEnding(arg0, __instance, __originalMethod)   ← 顺序不能反
        ├─ 当场做胜负快照（BattleEndFront 就在被拦的方法体里，拦住就等不到它）
        ├─ 判出唯一败者 && 败者是世界 NPC && 本次还没放行过
        ├─ 存 __originalMethod + __instance + isWin ⇒ return false（= 拦住）
        └─ DramaTool.OpenDrama(20403, new DramaData{ unitLeft = 玩家, unitRight = 败者 })
```

`Prefix` 里那个 `_releasing` 重入闸是必须的：Harmony 的 `__originalMethod` 就是被补丁的
那个方法本身，反射调它会**再次**进前缀；重入那一次直接放行原方法体。没有它 = 每次都再拦
一次（战斗永远结束）或原方法跑两遍。

#### 20403 的五个选项：效果归谁

| 选项 | 配表 `function` | 原生做了什么 | 我们做什么（`NpcDuel.NoteOptionClicked`） |
|---|---|---|---|
| `204032` 东西不错我收下了，放你一马 | `0` | 关页 | `outcome=spare_took_items`；**不改世界状态**（物品/好感未落账） |
| `204033` 击杀此人 | `sound_sharen` | **只播一个音效** | `outcome=kill` + **真击杀**：`WorldUnitBase.Die()` |
| `204035` 收下物品，羞辱此人 | `0` | 关页 | `outcome=insult`；未落账 |
| `204034` 算了，东西不要了，你走吧 | `0` | 关页 | `outcome=spare` |
| `204036`（隐藏，需持有 110 类物品） | `itemSelectNotCost_0_1_1_110_0_0\|notNextDialogue` | 开选物面板 → 自己跳 `28001`（`addClose_1_0_50_70` = 好感 +50~70，再跳 28101~28104） | `outcome=pill`，**不拦**它的后续页 |

> 五个 `function` 里除了音效和 204036 的选物，**没有任何"击杀/羞辱/收物品"的效果** ——
> 原版那些副作用是攻击动作自己的 C# 回调干的。所以效果得我们写：这次只落"击杀"一项，
> 其余如实记 `outcome`，日志里写"该项未落账"（物品/好感不在这次范围）。
>
> `WorldUnitBase.Die()` 是公开无参虚方法（`F:\_dt_dump\types\WorldUnitBase.txt:109`，RVA
> `0x32BBED0`；反编里走 `DataUnitDie.UnitDie::AddUnitDie` + 两次 `EventsMgr::Emit` + 退宗门动作）
> —— 这是"击杀"的正门。本仓此前**没有** `Die()` 调用先例，所以这一路全程留日志（"已调 / 读回 isDie"）。

#### 立绘位怎么填

配表 `DramaDialogue 20403`：`npcLeft=1` / `npcRight=0`。`UIDramaDialogue.UpdateUnit`@0x3300D80
的 id 分支（`TeamContainerInject` 的 190 那段已逐条核过、真机用过）：

```text
  id==1  → g.world.playerUnit          ← **硬编码玩家**，写 unitLeft 也改不了它
  id==0  → 按 isLeft 取 unitLeft/unitRight，两边都空才退回 dramaData.unit
  id==11 → dramaData.unitLeft          id==12 → dramaData.unitRight
```

⇒ 左位（npcLeft=1）**恒是玩家**；右位（npcRight=0，isLeft=false）读 `unitRight`
⇒ 只要 `unitRight = 败者`，画面就是"玩家在左、败者在右"。所以那行是
`new DramaData { unitLeft = 玩家, unitRight = 败者 }`。

> ⚠ **不要**写 `data.unit = 败者`：`DramaData.set_unit`@0x223AB70 会**同时**写
> `_unitLeft(+0x10)` 与 `_unitRight(+0x18)`（还有 `functionData` 那两个），写在 `unitLeft`
> 之后就把左侧也改成败者了。
>
> 真机若看到左右立绘反了 / 求饶句里的称谓反了：换 `NpcDuel.OpenVerdictPage` 里那一行。
> 开页日志会把两侧名字都打出来（`左(unitLeft/玩家)=… 右(unitRight/败者)=…`）。

#### 什么时候放行（无限等，不设超时）

`NpcDuel.HoldTick`，复用的是 `BattleFlowGate` 既有的 **0.25s 采样**（**没有新定时器**）：

1. **开页核对**：`OpenDrama` 返回 `true`、但下一拍仍看不到剧情窗 ⇒ 立刻放行；
2. **关窗**：连续**两拍**都看不到剧情窗才算"玩家把窗关了"
   （`DramaSegmentProbe` 头注释实测过"同一扇窗翻页时会有一两帧窗不可见" —— 单拍会把
   `204036 → 28001` 这种跳页当成关窗，把战斗提前放掉）；
3. 窗关着 + 结局已定 ⇒ 反射调回被拦下的那次 `BattleEnd`。没选就关窗 ⇒ `outcome=closed`（放过）。

"有没有剧情窗开着"用的是**共用唯一判据** `WorldStateFacts.DramaWindowReason()`
（我方确认窗可见 + UI 层层顶名字含 `Drama`，`tests/test_drama_gate_contract.py` 钉着"判可见
不判存在"）。不用"UI 实例存在"：`CloseUI` 不保证销毁实例，判存在会让"刚关掉的页"永远算
开着 ⇒ 永远放不了行。

**外加一条**：`WorldStateFacts.ChatWindowVisible()`（我方对话面板 `UIChatAi`）可见时也**不算关窗**。
理由：玩家能在处置页上点「对话」（面板盖在页上，`BattleOutcome` 的 B 支注释写的就是这个场景），
而那一刻 UI 层顶会变成 `UIChatAi`（不含 `Drama`）—— 只看层顶会把"玩家正在聊天"误判成
"他把页关了"，提前放掉战斗还把结局错记成"放过"。两个判据都是仓里现成的、判可见的共用实现。

玩家不选就一直拦着（用户 10-26 拍板）。`DramaGate` 那个 180s 槽位在拦住期间
**每拍无条件续期**（`OnSlotBeat`）—— 否则"玩家选了 5 分钟"会把槽位等过期，结局没处送、
战斗还拦着。

#### fail-open 纪律（铁律）

任一条成立 ⇒ **立刻放行**，绝不把玩家卡在一场结束不了的战斗里：

- `NpcDuel` 里任何异常（`NoteBattleEnding` / `HoldTick` 全程 try/catch）；
- `DramaTool.OpenDrama` 抛异常，或返回 `false`（`RandomDramaID` 解不出 id —— 引擎自己只打
  一行「找不到剧情ID」，`OpenDrama(string)`@0x463F1F0 的 `xor al,al` 分支）；
- 开完下一拍仍看不到剧情窗（同帧可见性不假设：`TeamJoinGate.CheckWindowShown` 就是"下一拍核对"）；
- 槽位被外力收掉（180s 过期 / 世界重载 `ForceResolveAll`）。

放行后 `w.HoldDone = true`，同一场再来的 `BattleEnd` 一律放行。
拦住失败的代价只有一个方向：**少弹一次页、战斗照常收**。

#### 上机要看的三行（都带 `[NpcDuel]` 前缀）

```text
[NpcDuel] ★拦住 BattleEnd★（isWin=True）败者=姜萌(…) 胜者=江宇文(…) 方法=BattleEndMgr.BattleEnd[instance]实例=BattleEndMgr → 原地不动等玩家处置（无限等，不设超时）
[NpcDuel] 玩家选了：kill（击杀此人）｜败者=姜萌｜击杀=已调 WorldUnitBase.Die()
[NpcDuel] 放行（isWin=True）：处置页已关（结局=kill）｜方法=BattleEndMgr.BattleEnd[instance]实例=BattleEndMgr
```

| 看到什么 | 说明 |
|---|---|
| 只有 `拦住`、很久没有 `玩家选了` / `放行` | 玩家还没选 —— **这是设计**（无限等），不是卡死 |
| `处置页没能开出来（…返回 false）` | 配表里没有 20403（或 id 解析失败）⇒ 已自动放行，战斗正常收尾 |
| `⚠ 放行调用失败` | 反射打回去失败 ⇒ 这一场可能收不了尾（**唯一无法兜底的形态**，见到就必须报） |
| `重复的 BattleEnd（已在拦）→ 继续拦` | 引擎在拦住的这段时间里又喊了一次收尾，已被一并拦住（不是错） |

#### 未验证（真机必须看的三条）

| # | 未验证 | 怎么验 / 不成立时怎么改 |
|---|---|---|
| V1 | 左右立绘与求饶句里的 `{call|B|A}` 是否真的按"左=玩家、右=败者"解析 | 看开页日志两侧名字 + 截图；反了就换 `OpenVerdictPage` 里 `unitLeft`/`unitRight` |
| V2 | `204036` 的**选物面板 → 28001** 会不会被这次放行截断（选物面板不是剧情窗，"两拍关窗"会把它当关窗） | 拿 110 类物品点一次，看 28001 有没有出来。若被截断：给 `pill` 单独加判据（那一支目前**不猜**，宁可少演一次后续页） |
| V3 | 在"战斗还被拦着"时调 `Die()` 是否安全（世界单位死了、战场单位还在场上） | 点 `204033` 后看有没有异常栈、战斗是否照常收尾。真机若炸：把 `Die()` 挪到放行之后（`ReleaseHold` 里）再执行 |
