# 01 · 引擎事实与证据链

> 查表用。原始调研（含路线对比与逐轮进展）在
> [`../archive/2026-10-25-NPC对NPC战斗/`](../archive/2026-10-25-NPC对NPC战斗)。

> **本文件是查表用的引擎事实。** 两条路线调研的完整过程在
> [`../archive/2026-10-25-NPC对NPC战斗/`](../archive/2026-10-25-NPC对NPC战斗)。
> 原则：**能引反编地址就引地址，能引配表行就引配表行，能引第三方 mod 实证就引源码行**。
> 没证到的写进 §8「未验证」，不进任何设计判据。

数据来源（本机）：

| 来源 | 路径 | 说明 |
|---|---|---|
| 类型签名层 | `/mnt/f/_dt_dump/types/*.txt` | Cpp2IL 产出，**只有签名 + 字段偏移**，方法体是空的 |
| 方法汇编 | `/mnt/f/_dt_dump/asm/*.txt` + 现场 `objdump` | 真方法体 |
| 方法表 | `/mnt/f/_dt_dump/rvamap_all.txt` | 103555 条 `RVA ↔ 类型::方法` |
| 托管反编 | `/mnt/f/DecompDump/dump/types36_fulldll.txt` | **第三方 mod 的完整 C# 源码**（ILSpy 级）——最好的"API 怎么用"教材 |
| 官方配表 | `/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/配置修改教程/配置（只读）Json格式/` | 明文全表 |
| 本仓代码 | `csharp/`、`tools/schemas.py` | |

> 工具：`python3 scripts/dev/il2cpp_grep.py type|find|code|xref <名>`。
> 本次另建了一个按 `rvamap` 精确标注调用目标的脚本（`asm.py`/`calls.py`），
> 因为 `il2cpp_grep code` 的符号注解用"最近下界"兜底，**未命中的地址会被标成别的方法名**
> （实测把 `g.world` 标成了 `UnityEngine.VFX.*`）——据此读控制流会读错。

---

## F1 · 现状：`attack` 把目标写死成玩家

```csharp
// csharp/tools/ToolExecutor.cs:2516  case "attack"
var atk = new UnitActionRoleAttack(player);          // ← 目标恒为玩家
...
int atkRet = wub.CreateAction(atk, true);            // ← 发起方恒为当前 NPC
```

`tools/schemas.py` 的 `WORLD_AI_ACTION` 描述也钉着同一句：

> "对玩家发起一个行动（发起方恒为当前对话 NPC）…… **动作对象只能是玩家**"

⇒ 模型既没有"对另一个 NPC 出手"的入口，C# 侧也没有这条分支。**这是要改的两处，不是引擎限制。**

---

## F2 · 引擎按「谁是玩家」三分派

`UnitActionRoleToUnitBase`（所有"对某人做事"的动作基类）声明了三个 **protected virtual**：

```
PlayerToNPCAction()   // 发起方是玩家
NPCToPlayerAction()   // 目标是玩家
NPCToNPCAction()      // 两边都不是玩家  ← 就是我们要的那条
```

同一组三个方法在 `UnitActionRoleRelation` / `UnitActionRoleMarry` / `UnitActionRoleTeachSkill` /
`UnitActionRoleBattle` 里**各有一份实现**（`/mnt/f/DecompDump/dump/types26_social.txt` 逐条可查）。

`UnitActionRoleAttack` 自己**没有**这三个方法（`il2cpp_grep type UnitActionRoleAttack` 只有
`Init`@0x183702140、`IsCreate`@0x183702150、`OnCreate`@0x183702280、`get_isComplete`@0x1833D6900），
分流发生在基类 `UnitActionRoleToUnitBase::OnCreate`@**0x1837B9180**（0xdb0 字节）。
该函数体内多次 `call get_world` → `cmp 0x10(%rax),%rbx`（`WorldMgr.playerUnit` 在 **0x10**，
`WorldMgr.battle` 在 **0x18**，见 `_dt_dump/types/WorldMgr.txt`）——即"这两个单位里有没有玩家"。

### 结论 F2

**分流只认"这两方是不是玩家"，不认队伍、不认交情、不认场合。**
队友关系在这里没有任何权重 —— 这是路线 A 必须先讲清的前提。

---

## F3 · NPC→NPC 攻击：**引擎直接算完，不起场景**

`UnitActionRoleBattle` 方法表（`il2cpp_grep type UnitActionRoleBattle`）：

| 方法 | VA | 作用 |
|---|---|---|
| `PlayerToNPCAction` | 0x18370C820 | 玩家打 NPC |
| `NPCToPlayerAction` | 0x1837095F0 | NPC 打玩家 |
| **`NPCToNPCAction`** | **0x183709480** | **NPC 打 NPC** |
| `IntoBattle` | 0x183709320 | 真正进战斗 |

反汇编 `NPCToNPCAction`（长度 ~0x170）**全部调用只有三个**：

```
.cctor                                   0x1812F2A90
UnitActionFeedback101::.ctor             0x18350EB60
<委托>.Invoke                            （收尾回调）
```

**没有 `WorldBattleMgr::IntoBattle`（0x27FADA0），也没有任何 Scene/UI 调用。**
`UnitActionFeedback101` 是"行为反馈"对象（写经历/提示），不是场面。

### 第三方 mod 实证（同一引擎版本）

`/mnt/f/DecompDump/dump/types36_fulldll.txt:38463`（`MOD_SSCYAI.UnitActionAI`）：

```csharp
if (gradeID3 - gradeID4 == 1 || gradeID3 - gradeID4 == 0) {
    UnitActionRoleAttack val2 = new UnitActionRoleAttack(current2);   // 目标 = 另一个 NPC
    unitA.CreateAction((UnitActionBase)(object)val2, false);          // 发起方 = NPC
    ModMain.unitBaseInfo.unitActionCount[2]++;
    Console.WriteLine(unitA...GetName() + "，本次额外行动触发复仇事件,复仇对象："
                      + current2...GetName() + "。");
}
```

另一个 mod（同文件 46326 行）也是同一写法，只是包了一层"提出攻击请求"的剧情窗。

### 结论 F3

- **"A 打 B"这件事引擎完全支持**，参数就是 `new UnitActionRoleAttack(B)` + `A.CreateAction(...)`；
- 但**两边都不是玩家 ⇒ 无场景、无 UI、无镜头**，玩家只会（在 mod 自己写日志时）看到一行提示；
- 因此用户设想的"让 B 打 A，玩家同意后一起进战斗"**不成立**：
  B→A 同样是"两边都不是玩家"，引擎不会因为 A 在玩家队里就把玩家拉进战斗。

---

## F4 · 引擎的「观战正门」：`npcFightUnits`

### 4.1 数据字段

`_dt_dump/types/WorldBattleData.txt`：

```csharp
[FieldOffset("0x50")] public bool isSelfBattle;
[FieldOffset("0x52")] public bool isRealBattle;
[FieldOffset("0xA8")] public MathfFightDialogueMap fightDialogueMap;
[FieldOffset("0xD8")] public DataStruct<WorldUnitBase, WorldUnitBase> npcFightUnits;  // ★ 一对 NPC
```

`npcFightUnits` 的字面意思就是"**这场战斗里对打的两个 NPC**"，而 `fightDialogueMap`
是"这场战斗的对话脚本"（`MathfFightDialogueMap::BattlePre/UnitAttack/BattleEnd`，
见 `rvamap_all.txt`）。

### 4.2 引擎自己的实现：`DramaFunction::DramaUnitBattle`@0x224F110

这是剧情函数 `dramaUnitBattle_*` 的处理体。反汇编（0x18224F110–0x18224F490）读出来的流程：

```
values = 函数串按 '_' 切开（values[0] = 函数名本身）
  values.Length >= 2                           （0x18224F1F2: cmp $1,0x18(%rbx) / jbe 失败）
  values[1] ──Int32.Parse──► GetUnitInType 的**槽位号**  ──► rdi = 左方单位
  values.Length >= 3                           （0x18224F214: cmp $2 / jbe 失败）
  values[2] ──Int32.Parse──► 同上                       ──► rsi = 右方单位
  （两者非空且不相等才继续）
  values.Length >= 4 ⇒ values[3] 是 dramaId（0x18224F2F3 起）
        └─ ConfDungeonBaseBase::GetItem / 影射剧情（0x1829FA6D0）
  WorldBattleData::.ctor                0x1827F8360
  DataStruct<WorldUnitBase,WorldUnitBase>::.ctor(左, 右)
  [battleData + 0xD8] = 上面那一对        ★ npcFightUnits
  g.world.battle.IntoBattle(
        toUnit     : null,               ← 注意：没有"敌人单位"
        dungeonID  : 0xFA1 = 4001,       ← 固定观战副本
        onInitCall : null,
        battleData : battleData)
  UIMapBattlePre::InitData(...)          0x183571450   ← 战前窗
```

关键佐证：`mov 0x18(%rax),%rcx` 中 `rax = g.world`（`0x183F877C0`），而 `WorldMgr.battle`
字段偏移正好是 **0x18**，`WorldMgr.playerUnit` 是 **0x10**（`_dt_dump/types/WorldMgr.txt`）——
所以 `rcx = g.world.battle`，即**实例方法 `WorldBattleMgr::IntoBattle`**，`rdx(toUnit)=0`。

### 4.3 函数串格式

```
dramaUnitBattle_{左槽下标}_{右槽下标}[_{dramaId}]
```

槽下标由 `DramaFunction::GetUnitInType`@0x182253A90 解释（反汇编逐支核对）：

| 值 | 取哪个单位 | 依据（`GetUnitInType` 反汇编） |
|---|---|---|
| `0` | `data.unitA` | `mov 0x10(%rax),%rax`（+0x10） |
| `1` | `data.unitLeft` | `mov 0x18(%rax),%rax`（+0x18） |
| `2` | `data.unitRight` | `mov 0x20(%rax),%rax`（+0x20） |
| `-1` | **玩家** | `call 0x183F877C0`（取 WorldMgr）→ `mov 0x10(%rax)` = `WorldMgr.playerUnit` |
| `10` | 其它（走 `get_xxx` 分支） | 未细究 |

`values[0]` 是函数名本身（`DramaUnitBattle` 从 0x28/0x30 起读，即 `values[1]`/`values[2]`），
所以 `dramaUnitBattle_1_2` 正好是"左=unitLeft、右=unitRight"。

> 附带发现：槽位 `-1` 表示玩家 ⇒ 同一个函数也能表达"玩家 vs 某个 NPC"，
> 不必另写一条链路。

配表实证：`DramaDialogue.json`（21926 条）里有 **14 条** `function` 形如：

```
"function": "dramaUnitBattle_0_1_5015121"
"function": "unitLog_...|dramaUnitBattle_0_1_5015065"
```

### 4.4 第三方 mod 实证：这就是「观战」

`types36_fulldll.txt:20290`（宗门大比类 mod），玩家点某一对 NPC 的对局：

```csharp
g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否需要观战？", 2,
    /* 同意 */ delegate {
        CloseUI();
        DramaFunctionTool.OptionsFunction("dramaUnitBattle_1_2", new DramaFunctionData {
            unitLeft  = unit3,     // 1 号槽
            unitRight = toUnit     // 2 号槽
        });
        isFight = true;
    },
    /* 拒绝 */ delegate {
        WorldUnitBase winner = UnitActionRoleBattle.UnitBattle(unit3, toUnit, (SchoolPostType)0);
        ...  // 自己写经历日志 eventLogTotal999022/999023
    });
```

**同一份代码里两条路都出现了**：想看得见就走 `dramaUnitBattle_*`；不想看就直接 `UnitBattle` 算完。
这正好是我们两条实现分支的现成参照。

### 4.5 观战用哪个副本

`DungeonBase.json` id=**4001**：

```json
{ "id":"4001", "type":"79", "name":"battle_dungeon_name4001",
  "totalPoint":"1", "roomBaseID":"11", "sceneBaseID":"14", "scaleBaseID":"101" }
```

`type=79` 与普通决斗（`type=40`）是两条不同的副本：

| 副本 | type | className | sceneBaseID | roomBaseID | 用途 |
|---|---|---|---|---|---|
| **11** | 40 | `1000`（`SpecialBattle1000`） | 11 | 11 | 攻击 / 寻仇 / 切磋的决斗 |
| **4001** | 79 | `0`（默认） | **14** | 11 | `dramaUnitBattle_*` 的观战 |

⇒ 观战走的是**另一套场景与玩法脚本**，不会碰决斗那条 `SpecialBattle1000` 的特有逻辑
（逃跑/处置页等），这降低了路线 B 与现有 `BattleOutcome` 互相干扰的风险。

---

## F5 · 只要结果、不要场面：`UnitActionRoleBattle::UnitBattle`

```csharp
// types36_fulldll.txt:5650 / 7500 / 21539
WorldUnitBase winner = UnitActionRoleBattle.UnitBattle(unitA, unitB, (SchoolPostType)0);
```

- 方法 VA **0x18370CDE0**，签名（来自 mod 调用点）：`static WorldUnitBase UnitBattle(WorldUnitBase, WorldUnitBase, SchoolPostType)`；
- 反汇编内部调用 `CommonTool::Random`@0x1835BBAC0 —— **纯算术结算**，无场景、无非确定性副作用；
- **它不写经历日志**（上面那段 mod 在拿到 winner 后自己 `AddLogMain`）。要用它就必须自己补日志/好感/伤势。

⇒ 这是"后台 NPC 互殴"的干净入口：一次调用拿到胜负，其余结算我们自己按需要补。

---

## F6 · 宗门大比 / 入门小比：引擎里已有的"NPC 互打"玩法

### 6.1 两套入口

| 玩法 | 代码入口 | 说明 |
|---|---|---|
| 入门/晋升小比（1v1） | `MapBuildSchoolPostBase::PostBattle`@0x183CFF5E0 | 返回 `MatchFight`；参数含 `notBattleDrama / startBattleDrama / joinCompleteDrama / joinFailDrama / maxJoinCount / dramaData / onInitMatchFightCall` |
| 宗门大比（多宗门） | `SchoolBigFight`（82 项参数配表） | 积分赛 → 擂台赛 → 争锋；`OnFightBeginAsPlayer/AsNpc`、`OnFightEndAsPlayer/AsNpc`、`GetFightResult`、`StartBigFightAsPlayer`@0x18385C310 |

### 6.2 关键结论：**NPC 对局在宗门大比里是「模拟」，不是场景**

- `SchoolBigFight::OnFightBeginAsNpc`@0x183857F50 反汇编的全部具名调用里**没有 `IntoBattle`**，
  只有 `CallQueue` 组包 + 日志；
- `BigFightBattleSimulation::Run`@0x182B0A200 内部调 `CommonTool::Random` 两次以上 —— 概率模拟；
- 配表 `SchoolBigFight.json` 也印证：`npcBattleTimeLimit = 120`、`powerScaleAtk/Def/Hp/Skill`
  —— "NPC 对局的战力缩放与时长上限"，那是模拟参数，不是场景参数。

**玩家自己的场次**才是真场景：`MatchFight::FightNext`@0x184233AE0 里
`0x1842341AB` 往 `battleData+0xA8` 写 `fightDialogueMap`，随后
`0x1842341EF → 0x1827FADA0` 调 `WorldBattleMgr::IntoBattle`（`rdx=toUnit`, `r8d=dungeonID`）。

### 6.3 观战 / 督战是另一套

`BigFightExtensions.SpecialBattleSwitchHandler`：`GoTo` / `GoBack` / `SwitchAsync` ——
在**一场特殊战斗内**切换"看谁/指挥谁"（配表 `SchoolBigFightDrama.json` 有
`scene_arenaAppointStudTalk` / `scene_arenaNpcExitBattle` / `scene_arenaWinnerTalk` 等阶段剧情；
`LocalText` 里有"勾选感兴趣的弟子进行观战""可以为该弟子分配秘宝…指挥其在对应时机使用秘宝"）。

⇒ 这是**大比玩法专用**的观战壳，复用成本高（要复刻报名/积分/擂台状态机），
**不适合当作通用"两个 NPC 打一场"的入口**。

---

## F7 · 组队能不能进"攻击"这场战斗：**现在不能，但是配表问题**

（详见 [`../archive/2026-10-25-队友进决斗/事实链.md`](../archive/2026-10-25-队友进决斗/事实链.md)，此处只摘与本任务相关的两条）

1. 攻击 / 寻仇开出来的战斗 = `dungeonID=11`、`ConfDungeonBase.type=40`（决斗），
   走的是同一条 `WorldBattleMgr.IntoBattle` → `SceneBattle.Init` → `DungeonTrigger` 链；
2. 队友上场只有一口：`TeamContainerInject` 钩 `BattleFunction.AddBattleNPCData`，
   而它是**配表 `DungeonTrigger` 的 `battleFunction` 字符串**驱动的。
   我们的白名单 `dungeonType` 里**没有 40** ⇒ 这条钩子根本没被调到 ⇒ 队友无从入场。

⇒ 想让"玩家 + 队友 vs 目标"成立，前置是**给 type 40 补一条 `DungeonTrigger` 行**（路线 A 的 P0）。

---

## F8 · 未验证清单（写护栏之前必须先跑探针）

| # | 问题 | 为什么必须先验 |
|---|---|---|
| U1 | `dramaUnitBattle_1_2` 在**非剧情上下文**（工具调用里）直接调，是否稳定开场景 | 现有实证都发生在 UI 按钮/剧情回调里 |
| U2 | 观战场景里玩家是**什么身份**（无参战单位？旁观镜头？能不能操作） | 决定"玩家会不会被卷进去"与手感 |
| U3 | 观战结束后**谁赢了**从哪读（`npcFightUnits` + 存活？`MathfFightDialogueMap`？段里原话？） | 决定工具结果能不能给出胜负 |
| U4 | 观战是否要求**同格**、是否消耗精力、精力不足时是否直接失败 | 决定发起前的校验，避免"点了没反应" |
| U5 | 两个 NPC 对打后，**引擎自己**会不会写经历/掉好感（`NPCToNPCAction` 的 `UnitActionFeedback101`） | 决定我们补不补日志，避免双写 |

---

## F9 · 2026-10-26 复核：**分派链与时序**逐条反编（把 F4 的推断落成事实）

写代码前把"名字到底怎么变成一次 `IntoBattle`"整条链读完了。下面每一条都引地址，
**没有一条是推测**。`F4` 的结论全部成立，另外补了三条原先不知道的关键事实。

### 9.1 名字 → 处理体：两级 `Split` + 按 `parts[0]` 分派

`DramaFunctionTool::OptionsFunction(String,DramaFunctionData)`@**0x4634CC0**
（本仓 `NpcDuel` 直接调的就是它）：

```
rdi = 函数串   rsi = DramaFunctionData
rbx = new DramaFunction()                       ; 类型 token 0x186E80BB0
DramaFunction::OptionsFunction(this=rbx, 函数串, data)
```

`DramaFunction::OptionsFunction`@**0x2268F60**（签名见
`_dt_dump/types/DramaFunction.txt`：`public void OptionsFunction(string, [Optional] DramaFunctionData)`）：

| 步 | 反编 | 含义 |
|---|---|---|
| ① | `call IsBlankString` → `jne 返回` | 空串直接不管 |
| ② | `string == <字面量 0x186D78F98>` → `返回` | 配表用 `"0"` 表示"没有功能" |
| ③ | `[this+0x10] = data`（空则 `new DramaFunctionData()`） | `DramaFunction._data`@0x10 |
| ④ | `Split('|')`（先 `mov WORD [..],0x7C`） | **多命令分隔符是 `|`**，一条串可挂多条命令 |
| ⑤ | 逐条再 `Split('_')`（`mov WORD [..],0x5F`） | **参数分隔符是 `_`** |
| ⑥ | `parts[0]` 与字面量逐一 `String::op_Equality`（二分链），命中即 `call DramaFunction::X(this, parts)` | 分派键是**命令名**，参数是 `parts[1..]` |

⇒ `"dramaUnitBattle_1_2"` 得到 `parts = ["dramaUnitBattle","1","2"]`，
分派到 `DramaUnitBattle(this, parts)`；**第三个数字不是必需的**（配表里 `<dramaId>` 是可选尾巴）。
窗口尾巴那段（`0x18226DBAB` 起的 tail）实测就是 `rdx = rdi(parts)`、`rcx = rsi(this)`。

### 9.2 处理体：`DramaUnitBattle(string[] values)` 的完整流程

`DramaFunction::DramaUnitBattle`@**0x224F110**，签名 `private void DramaUnitBattle(string[] values)`：

```
values.Length > 1 → Int32.Parse(values[1]) → GetUnitInType(n) → rdi = 左
values.Length > 2 → Int32.Parse(values[2]) → GetUnitInType(n) → rsi = 右
（左/右都非空且不相等才继续，否则整段跳到最后直接 return）
r15b = (左 == g.world.playerUnit) || (右 == g.world.playerUnit)      ; 0x18224F25E/F274
battleData = new WorldBattleData()
battleData.npcFightUnits(+0xD8) = DataStruct(左, 右)
if (values.Length > 3) battleData.battleCustomData(+0x30)[values[3]] = …   ; dramaId
if (!r15b) g.world.battle.IntoBattle(toUnit:null, dungeonID:0xFA1=4001, onInitCall:null, battleData)
else       UIMapBattlePre::InitData(…)
```

`GetUnitInType(int)`@**0x2253A90** 逐支核对（每个分支都先 `call get_data`@0x227F880 再取字段）：

| 入参 | 取值 | 反编 |
|---|---|---|
| `1` | `data.unitLeft` | `0x2253B5B → get_data → mov 0x18(%rax)` |
| `2` | `data.unitRight` | `0x2253B45 → get_data → mov 0x20(%rax)` |
| `0` | `data.unitA` | `0x2253B71 → … mov 0x10(%rax)` |
| `-1` | `g.world.playerUnit` | `0x2253B87` |
| `10` | 宗门那套（`g.school` 链） | `0x2253B0A` |
| 其它 | `ConfNpcCondition::GetUnit(...)` | `0x2253AFF` |

`DramaFunctionData` 的字段偏移（`_dt_dump/types/DramaFunctionData.txt`）正好是
`unitA`@0x10 / `unitLeft`@0x18 / `unitRight`@0x20 ⇒ **我们塞进哪个字段，它就取到哪个**。

### 9.3 ★ `isBattle` 是**同步**置真的（这条决定了整个写法）★

`WorldBattleMgr::IntoBattle(WorldUnitBase,Int32,Action,WorldBattleData)`@**0x27FADA0**
（`rvamap` 里这个重载只有 **4 个**形参 + `this`：`rcx=this, rdx=toUnit, r8d=dungeonID,
r9=onInitCall, [rsp+0x20]=battleData`）：

```
MonstData.level = dungeonID
IntoBattleInit((-1,-1), Clone(ConfDungeonBase[4001]), level, battleData)   ; 0x1827FA800
    └ 0x27FA86F  movb $0x1,0x10(%rsi)      ← WorldBattleMgr.isBattle = true（字段在 +0x10）
    └ WorldBattleData::InitData(point, conf, level)
onInitCall?.Invoke()
[battleData+0x68] = toUnit                  ← WorldBattleData.npcUnit（我们这里是 null）
ActiveMap(false); g.ui.CloseAllUI()
SceneMgr::LoadSceneAsync(…)                 ← **场景是异步加载的**
```

⇒ **`isBattle` 在 `IntoBattleInit` 第一屏就置真，早于场景加载、早于任何一帧。**
所以 `OptionsFunction` 一返回就能读到 `isBattle == true` —— "有没有开打"是**同步可判的事实**，
不需要等任何时长。`NpcDuel.Attack` 的同步判据就是照这条写的。

### 9.4 观战副本 4001 的 `isRealBattle` 在引擎里被**显式置假**

`WorldBattleData::InitData`@**0x27F8100**：

```
[+0x18] = point;  [+0x20] = Clone(conf);  [+0x28] = level
[+0x10] = CommonTool::SoleID(6)
movw $0x101,0x51(%rdi)     ← 一次写两字节：isSelfBattle(+0x51)=1、isRealBattle(+0x52)=1
if (type ∈ {41,46,53} || id==10 || type==0x4F=79)  [+0x52] = 0
```

`type ∈ {41,46,53}` 来自位掩码 `0x20420000000000`（bit 41/46/53）；
第四个条件就是 `cmp $0x4f`，而**我们的副本 4001 正是 `type=79`**。

⇒ `isRealBattle = false` 是**引擎自己**为观战副本做的选择，不是"没设"。
它控制的是"这一场算不算一次真正的副本战果"——`UnitCondition::NpcDungeonResult`@0x25A3660
读 `g.world.battle.data.isRealBattle`，`BattleDataMgr::OnBattleEndCall`@0x33E32BF 也拿它
当结算门槛（`cmpb $0x0,0x52(%rax)` → 假则跳过奖励计算）。
**所以绝对不要给它写 `true`** —— 那等于把一场"表演赛"改成"真副本"。

### 9.5 `SceneBattle` 那边确实有"type 79 专用"的战斗类

`SpecialBattle79`（`rvamap` 8 个方法）：

| 方法 | 干什么（反编要点） |
|---|---|
| `Init`@0x2BFDE50 | 先 `SpecialBattleBase::Init`，再 `EventsMgr::On` 订阅一个事件（处理器是 `this` 的闭包） |
| `OneIntoRoomEnd`@0x2BFEB10 | **`BattleUnitMgr::CreateUnitHuman` 调两次**（左/右各一次），随后各一次 `BattleMapMgr::PointToWorld` + `UnitMoveBase::SetPosition` |
| `OnUnitDie`@0x2BFE0C0 | `g.world.battle.data.npcFightUnits` 的两个人**先**从 `npcUnits(+0xB0)` 里 `List::Remove`，再按死的是哪一边 `List::Add` 回去；随后 `UnitCtrlBase::AddState` + `DramaPackageTrigger14::Trigger` / `FortuitousTrigger14::Trigger` |
| `BattleEnd`@0x2BFDCD0 | 转 `BattleEndMgr::BattleEnd`（带 `isWin` 与倍率） |

配套配表：`DungeonTrigger.json` 里 4001 的两条触发器条件是
**`isBattleData_SpecialBattle79winUnit_0` / `_1`**
—— 也就是说这一场结束后，引擎会把"哪一边赢了"（0=左 / 1=右）写进战斗数据，
供剧情条件读取。**"谁赢了"在引擎里是有正式表示的，不必我们猜。**

> 交叉证据：`DramaDialogue.json` 里 14 条 `dramaUnitBattle_*` 中，
> `dramaUnitBattle_1_2_5015903` 与 `dramaUnitBattle_1_2_5016404` 的 `npcLeft/npcRight` 都是 `-1`
> —— 即"两个立绘位都不是主角"，正是"看别人打"的写法；我们用的 `1_2` 与它们同形。

### 9.6 复核后的口径修正

| 原先写的 | 现在改成 | 依据 |
|---|---|---|
| 观战可能"因为 `isRealBattle=false` 不播场景"（U6） | **不播场景的担心不成立**：场景由 `IntoBattle` 无条件 `LoadSceneAsync` 加载；`isRealBattle` 只影响结算/条件判定 | §9.3 §9.4 |
| "开打后要等一小会儿才知道有没有开" | **同步可判**：`isBattle` 在 `IntoBattleInit` 里就置真 | §9.3 |
| 观战副本的胜负只能靠 `isDie` 猜 | 引擎有 `SpecialBattle79winUnit` 这套正式表示；本仓仍只报**能读到的**事实（战场单位 `isDie`），读不到就不给胜负 | §9.5 |
| `IntoBattle` 是 4 形参 | 是 **4 形参 + `this`**，第 5 个栈位是 `battleData`（`rvamap` 签名与调用点一致） | §9.3 |

---

## §33 20403 处置页的**原版实现**就是 `UICustomDramaBattleNPCFailed2`（10-27 复核）

### 33.1 认门：它管的正是 20403

| 事实 | 证据 |
|---|---|
| `UICustomDramaBattleNPCFailed2::GetDramaID` 返回 **20403** | `0x2EEB290`：`mov eax,0x4FB3; ret`（0x4FB3 = 20403） |
| `OpenUI` 里就是开 20403 | `UICustomDramaBase::OpenUI`@`0x2EE8F40`：`… call DramaTool::OpenDrama(id, dramaData)`@`0x2EE9084`，`id` 来自 vtable`[+0x178]` |
| 五个选项 id 与本仓页面完全一致 | `UICustomDramaBattleNPCFailed2::Init`@`0x2EE99D0` 逐条 `UICustomDramaBase::OnOptionBackCall`，`edx = 0x31D00/01/02/03/04` = **204032/204033/204034/204035/204036** |
| 构造 | `.ctor(List<PropsData> props, WorldUnitBase npcUnit)`（托管反射实证）⇒ 基类 ctor 建 `DramaData`，再 `0x223AB70(dramaData, npcUnit, 0)` 把败者写进 `+0x10`/`+0x18` 两格 |

⇒ 之前参考的 `WorldUnitEffectSpecialNpcRelationSurrenderMeet::BattleNPCFailed`@`0x3D67B80`
是**另一条链**（投降/求饶，自己用 `UICustomDramaDyn` 模板页 22001 搭 4 个选项），
参数组合与 20403 **不通用**（它那条"击杀"支还要额外 `mov byte [act+0xc0],1`）。

### 33.2 四个回调的原文（每个选项的真身）

| 选项 | 方法 | 正文 |
|---|---|---|
| 204033 击杀此人 | `OnKillClick`@`0x2EEA860` | `new UnitActionRoleKill(unit, isMercy:true, isShowDrame:true, isCatlikeKill:false, isForceDie:false)` → `处置者.CreateAction(act,false)` |
| 204032 收下物品 | `OnPassClick`@`0x2EEA9D0` | `new UnitActionRoleRelease(unit, props=ctor 传进来的那张表, isAddHate:true)` → `处置者.CreateAction(...)` |
| 204034 算了 | `OnPassNotPropClick`@`0x2EEAB00` | `new UnitActionRoleRelease(unit, props=空表, isAddHate:false)` → `处置者.CreateAction(...)` |
| 204035 羞辱 | `OnInsultClick`@`0x2EEA500` | `WorldUnitData::CostPropItem`@`0x32C0680` + `RewardPropItem`@`0x32C4660`（缴物）+ `UnitActionRoleBattle::InsultUnit(player, unit, this.onEndCall, 0)`@`0x3708090` |
| 204036 选物 | `OnFeedClick`@`0x2EEA420` | **只做一件事**：`dramaData+0x60 = Delegate::Combine(…, this.onEndCall)`；真正干活的是配表的 `itemSelectNotCost` |

### 33.3 三条通用事实（推翻/修正了旧写法）

1. **`CreateAction` 的接收者恒是「处置者」，不是败者。**
   五个回调清一色 `rcx = [[0x3F877C0]()+0x10]`（= 玩家）→ `WorldUnitBase::CreateAction(act, false)`。
   `DramaFunction::KillRole`@`0x225AF90` 也一致（`rcx = GetUnitInType(values[1])`，即"发起方"）。
   ⇒ 旧代码 `loser.CreateAction(...)` 是**错的**：action 的 `+0x10`（creator）会变成败者，
   连带缴物、仇恨、门派日志、`GetEffects`、`SoulDevourSwordMgr::Drop` 全部用错人。

2. **`CreateAction` 的返回值 = `IsCreate` 的返回码**，不是"0 或非 0 的 bool"。
   `WorldUnitBase::CreateAction`@`0x32B8D40`：
   ```
   edi = IsCreate(isForce);          // [vtable+0x188]
   if (edi != 0) return edi;         // ← 直接把它当返回值吐出去
   UnitActionBase::Create(action, 0);
   return edi;                       // = 0
   ```
   `UnitActionRoleKill::IsCreate`@`0x3BEBBF0` 的三个出口：
   `0` = 可以执行；`1` = 硬失败（`creator+0x78`/`target+0x78` 即 `_isDie` 为真，`0x3BEBC59`）；
   **`2` = `OnParentBless` 已接管**（`0x3BEBC49`）——`OnParentBless` 自己会
   `ToUnit.CreateAction(new UnitActionParentBless(...), false)`（`0x3BEE8DA` → `0x3BEEB30`），
   原版语义 = **有人出面把这一刀挡了**。此时**绝不能**再补一刀。

3. **`isForceDie=true` 走的是另一条死法。**
   `UnitActionRoleKill::Kill`@`0x3BEBCB0` 开头：
   ```
   cmp byte [this+0xd2], 0 ; jne → 0x3BEE0D3   ← isForceDie
   cmp byte [this+0xc0], 0 ; jne → 0x3BEE0D3   ← b__7 那种"直接杀"
   …否则 new UnitActionRoleDying
   ```
   - `0x3BEE0D3` ⇒ `UnitActionRoleDie::.ctor` → `OnCreate` → `WorldUnitMgr::DelUnit`（**瞬删**）
   - `UnitActionRoleDying`@`0x3F1D4C0` ⇒ `ConfRoleDyingBase::GetItem` + `UnitActionLuckAdd`
     + `UnitActionRoleSight::OnAdd` + 门派日志 —— 即 `RoleDying.json` 那套
     （`deadFeatureID` 死亡气运 / `ghost`+`ghostLife` 残魂存续月数 / `itemID` 遗物 /
     `letterID` 信件 / `goalID`）。
   **原版 204033 两闸都不置 ⇒ 走 Dying。所以"击杀此人"之后败者 `isDie` 仍是 `false` 是正常的**，
   它进入的是死亡处理流程，不是当场 `DelUnit`。

### 33.4 `UnitActionRoleRelease` 的 `props` 语义（旧说法是错的）

`UnitActionRoleRelease::Release`@`0x3ABAAB0` 开头：
```
rax = this.props(+0xa8)
if (rax == null) → 0x3ABAFCD
if (props.Count == 0) → 0x3ABADD2      ← 空表：直接跳过整个取物循环
…否则遍历 props 逐个 CostPropItem …
0x3ABADD2: cmp byte [this+0xb0](isAddHate), 0 / je …
```
⇒ **空表 = 一件都不拿**。之前"传空表让引擎按 `RoleBattle.lostItemCount/Percent` 自己算"
是**错的**（那条规则只在别处），已在本仓删除。

`isAddHate` 两处取值不同（照抄即可）：`OnPassClick`=`true`、`OnPassNotPropClick`=`false`。

### 33.5 本仓据此的实装（`csharp/drama/NpcDuel.cs`）

- 204033 → `NativeKill`：`new UnitActionRoleKill(loser, true, true, false, false)` + `player.CreateAction`
  （`0`/`2` 都算"已交接"，`1` 才回退自研 `WorldUnitBase.Die()`）
- 204032 → `NativeRelease(useOffer:true, addHate:true)`（props = 页面上说的那一件）
- 204034 → `NativeRelease(useOffer:false, addHate:false)`
- 204035 → 缴物走 `NativeRelease(useOffer:true, addHate:true)` + `Humiliate`（`AddHate(abuseAddHate=300)`
  + 两条气运）；**体力上限/声望仍未落**，原因见 §33.6
- 204036 → 不干预（配表 `itemSelectNotCost` 自己管）

### 33.6 还差的一刀：`InsultUnit` 的委托参数

`UnitActionRoleBattle::InsultUnit`@`0x3708090` 的签名是
`(WorldUnitBase player, WorldUnitBase unit, <onEndCall 那个委托>, bool)`；
那个委托类型在 `MelonLoader/Managed/Assembly-CSharp.dll` 上
`MethodInfo.GetParameters()` 直接抛 `TypeLoadException`（`Il2CppSystem.Delegate` 解析不出来），
编译器拿不到可写签名 ⇒ **写不出这个调用**。硬传 `null` 有风险：闭包在 `0x37081FC`
把手上委托直接 `mov [rcx], rbx` 存进字段，内部大概率无保护 `Invoke`。
所以 204035 暂用等价实现，并把"体力上限 `abuseMaxhpDec 25~40` / 声望 `-500` 未落"如实写进日志。

### 33.7 委托探针（10-27 加，只读）

`NpcDuel.EnsureDelegateProbe()` 在 mod 装载时跑一次（跟 `EnsureDispatchProbe` 同一处），
只反射、不打补丁、不改任何行为。它回答的就是 §33.6 那个卡点，输出在 MelonLoader 日志里
（搜 `★委托探针★`）：

| 日志行 | 含义 |
|---|---|
| `类=…UICustomDramaBattleNPCFailed2` | 类型在本进程里拿得到 |
| `① dramaID 类型=System.Int32` | 顺带证实"这个类管的就是 20403" |
| `② onEndCall 类型=<名字>` | **能读到类型** ⇒ 可以试「整页 `OpenUI()`」 |
| `② onEndCall 类型=✗ TypeLoad…` | 读不到 ⇒ 看 `④` |
| `④ <asm> :: <Type> → Method/参数个数` | 互操作层现成的"造委托"入口（扫 `*Interop*`/`*Melon*`/`*Unhollower*`） |
| `⑤ InsultUnit 静态=… ｜参数=…` | 直接调 `InsultUnit` 这条路通不通（决定体力上限/声望能不能落） |
| `结论：…` | 三条路只走一条 |

三条路（探针自己会给结论）：
1. `onEndCall` 类型可读 ⇒ 换整页 `new UICustomDramaBattleNPCFailed2(props, 败者).OpenUI()`，
   噬魂 `OnSoulDevourClick` 与原生羞辱一起拿到；
2. 类型读不出但有 Delegate 助手 ⇒ 先用手册把 `onEndCall` 造出来，再决定要不要换整页；
3. 两条都不通 ⇒ 保持现版（击杀/缴物/放过已是原生），体力上限/声望**记为永久已知限制**，不再折腾。

### 33.8 委托探针的真机结果 —— §33.6 的结论**是错的**，已修

真机 `Player.log`（搜 `★委托探针★`）原文：

```
类=UICustomDramaBattleNPCFailed2
① dramaID 类型=System.Int32｜props 类型=Il2CppSystem.Collections.Generic.List`1[[DataProps+PropsData, ...]]
② onEndCall 类型=Il2CppSystem.Action
   是 C# 委托=False
   基类=Il2CppSystem.MulticastDelegate
   ctor(Il2CppSystem.Object, System.IntPtr)
   ctor(System.IntPtr)
③ Il2CppSystem.Action=Il2CppSystem.Action｜是 C# 委托=False｜onEndCall.IsAssignableFrom(Action)=True
④ UnhollowerBaseLib :: UnhollowerRuntimeLib.DelegateSupport → ConvertDelegate/1
⑤ InsultUnit 静态=True｜参数=WorldUnitBase winUnit, WorldUnitBase failUnit, Il2CppSystem.Action call
```

**修正**：`onEndCall` 不是"某种读不出来的自定义委托"，它就是 **`Il2CppSystem.Action`**。
之前判"写不出这个调用"，是因为 **PowerShell 反射工具**在 `GetParameters()` 上抛
`TypeLoadException`（`Il2CppSystem.Delegate` 解析不出来）—— 那是**工具的限制**，
不是类型真的拿不到。真进程里它清清楚楚。

⇒ `UnitActionRoleBattle::InsultUnit` **可以调**，而且本仓早有同款先例：
`ToolExecutor.cs:4726` / `:4936` 就在用
`UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action<bool>>(managed)`。

#### 于是 204035 改成原生（`NpcDuel.NativeInsult`）

```csharp
var cb = UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action>(
             new System.Action(() => { /* 只打一行日志 */ }));
UnitActionRoleBattle.InsultUnit(winUnit: 玩家, failUnit: 败者, call: cb);
```
- 参数名/语义来自 `OnInsultClick`@`0x2EEA500` 的调用点：`InsultUnit([玩家], [败者], this.onEndCall)`；
- 体力上限 `abuseMaxhpDec 25~40`、声望 `-abuseReputationDecMin(500)`、
  仇恨 `AddHate(abuseAddHate=300)` **全部由它落**（闭包 `0x3ADB470`）；
- **缴物那一支不再传 `isAddHate:true`**：原生 204035 用 `CostPropItem`/`RewardPropItem`，
  本来就不加仇恨；仇恨只由 `InsultUnit` 加一次（早一版会挂两遍，已修）；
- `abuseAddFeature1/2`（意志消沉/愁肠百结）**原生全链没有一处 `AddLuck`/`UnitActionLuckAdd`**
  （`InsultUnit` 只 `GetLuck` 读），所以仍由 `ApplyAbuseLuck()` 按配表概率补；
- 原生抛异常才退回旧的 `Humiliate`（`AddHate`+气运），此时体力上限/声望确实未落。

#### 还开着的口子

`UICustomDramaBattleNPCFailed2` 现在**整页可开**（`onEndCall` 类型已知、可构造），
即 `new UICustomDramaBattleNPCFailed2(props, 败者) { isWaitKillEffect = false }.OpenUI()`，
能白拿「噬魂」那支 `OnSoulDevourClick`。**没做**，因为它会把选项表交给引擎自己决定
（含条件显示），与当前"我们开页 + 自己分派"的实现只能二选一；等需要噬魂时再切。

### 33.9 整页改用原生：可行性分析与已落地的开关（10-27）

**能**。`NpcDuel.UseNativeVerdictPage = true` 时 `OpenVerdictPage` 先走：

```csharp
var ui = new UICustomDramaBattleNPCFailed2(props, 败者);
ui.isWaitKillEffect = false;                 // 观战副本没有击杀特效，等它会永远等
ui.onEndCall = ConvertDelegate<Il2CppSystem.Action>(...);   // 可给可不给
ui.OpenUI();                                 // GetDramaID()=20403 → Init() → DramaTool::OpenDrama
```
任何异常 ⇒ 原样退回"我们自己开页 + 自己分派"的老路（`UseNativeVerdictPage=false` 也走它）。

#### 为什么成立（四条，都有出处）
1. `GetDramaID`@`0x2EEB290` = `mov eax,0x4FB3; ret` ⇒ 20403；
2. `OpenUI`@`0x2EE8F40`：`Init()` → `DramaTool::OpenDrama(id, [this+0x18])` → `GameTool::RunInMainThread`；
3. `Init`@`0x2EE99D0` 只读 `this+0x18`(dd) / `+0x30`(props) / `+0x44` / `+0x48`，
   **不读 `onEndCall`(+0x38)** ⇒ 不给回调也能开；
4. 本仓那个 Harmony 挂点 `DramaSegmentProbeClickHook.Prefix` 是 `void`（**不吞原方法**）
   ⇒ 引擎自己的 `ClickOption` 分派照常跑，原生回调能收到点击。**这条是整页方案的前提。**

#### 拿到的
选项分派回引擎、`{0}` 由 `HretProps(props)` 拼、`Init` 后面那个第 6 个动态选项（id 存 `this+0x44`）、原生 `onEndCall` 收尾链。

#### 会丢的（已手工保住 / 已确认丢失）
| 丢什么 | 现状 |
|---|---|
| **原版羞辱没有独立页**：`OnInsultClick`@`0x2EEA500` 里 `OpenDrama` 调用数 = **0**，只把羞辱文本塞进当前页（`[dd+0x98]` + `0x5b91860`） | 我们那个 `145xxxx` 羞辱页是**自己加的**，已在 `NoteOptionClicked` 里保留（`PendingInsultPage` 延后开） |
| `functionData.unitA` 那个补丁（给 `28001 addClose_1_0` 取人用） | 原生 dd 的 `unitA` 来自 `0x223AB70`（= 败者），我们填不了 ⇒ 204036 后续那一环的取人可能与我方不同 |

#### 还没查清的一点
`[[0x3F877C0]()+0x10]` 是谁。它同时被当成：
`WorldUnitBase::CreateAction` 的接收者（`[vtable+0x1c8]`，且 `Kill` 里同一个 slot 由 `[rsi+0x40]`=toUnit 调用，确定是 WorldUnitBase）、
`UnitActionFeedback103::GetBegItem` 的 `this`（instance/2 参）、
`UnitConditionData::.ctor` 的第一个参数 —— 三者对不上，`GetBegItem` 的归属存疑（它内部几乎不用 `this`）。
⇒ **没有**去调 `GetBegItem`，`props` 只传页面上那一件，日志里写 `props=N件`。

#### 验收只有一行
原生页模式下，放行前必打：
`原生页收尾核对（结局=…）：败者=…｜isDie=…｜背包=N件｜败者灵石=…｜玩家灵石=…｜好感=…`
数字**动没动**就是"引擎回调到底跑没跑"的唯一证据。全都没动 ⇒ 去读 `UIDramaBase::ClickOption` 的派发路径，别再猜。

### 33.10 props 归谁 + `GetBegItem` 的真身（10-27）

**"引擎负责 props"只对一半**：
- **用** props 拼 `{0}`、显示、收走 —— 是 `UICustomDramaBattleNPCFailed2.Init` 里的 `HretProps(props)`，**引擎的**；
- **算** props（挑哪些）—— 是**调用方**的事：`NpcBegMercy`@`0x2907DE9` 和 `BatlePlayerWin` 都是先算好再 `new UICustomDramaBattleNPCFailed2(props, …)`。

调用方用的算法是：

```
public static List<PropsData> UnitActionFeedback103::GetBegItem(WorldUnitBase battleUnit, WorldUnitBase unit)   // @0x35182E0
```

**它是 `static`** —— 这条同时解掉了之前那个"`[[0x3F877C0]()+0x10]` 既当 `CreateAction` 接收者又当 `GetBegItem` 的 this"的矛盾：
static 调用的 `rcx` 就是第一个参数，而那个对象确实是 `WorldUnitBase`（玩家）。**两处都成立**，之前怀疑符号标错是多余的。

内部（无战机依赖，所以战斗外也能调，我们正是在"拦着 BattleEnd"时调）：

| 步骤 | 调用 |
|---|---|
| 取几件 | `CommonTool::Random(RoleBattle.lostItemCountMin, lostItemCountMax)` |
| 从败者背包筛 | `List::ctor` → `RemoveAt` / `RemoveAll` / `UnityAPIEx::Sort` |
| 抽 | `CommonTool::SortRandom` → `PropsData::Clone` → `List::Add` |
| 加奖励 | `AITool::GetUnitHumanNPC` → `RewardTool::GetReward` → `RewardTool::GetRewardPropsData` → `WorldUnitData::RewardPropItem` → `List::AddRange` |

**没有任何** `isRealBattle` / `BattleUnitMgr` / `battle.data` 读取 ⇒ 它在观战副本里照样能返回非空。

⇒ 本仓 `TryOpenNativePage` 现在把 `props` 交给它，只有返回 null/空表才退回自研的 `PlanOffer`（日志会写 `引擎 GetBegItem=N 件` 或 `→引擎没给，退回 PlanOffer 1 件`）。

### 33.11 原生页模式下仍由我们补的两样

1. **气运 `abuseAddFeature1/2`**：`OnInsultClick` → `InsultUnit` 全链**没有一处 `AddLuck`**（只有 `GetLuck` 读），配表这两个字段等于没人用 ⇒ 我们按概率补（`ApplyAbuseLuck`）；
2. **战败重伤 `hurtFeatureID`**：原生在 `UnitActionRoleBattle::BattleEnd`@`0x3703110`（`+0x600`）挂，那是**真实攻击战斗**的收尾；79 号观战副本没有 `UnitActionRoleBattle` ⇒ 仍由 `ApplyBattleHurt` 补。

---

## 34 · 真机验收（10-27 16:25–16:38）：原生链跑通 + 我自己的一个 bug

### 34.1 已证成立（原生页这条路线是通的）

`Player.log` 里的引擎栈，逐条对上反编：

```
[DrillChoice] 链外点击：id=204035 function=0
  UICustomDramaBattleNPCFailed2:OnInsultClick()           ← Init 注册的处理器
    UICustomDramaBase:<Init>b__8_1(ConfDramaOptionsItem)  ← dd+0x68/+0x70 的委托
      刷新获得的道具 → WorldUnitData:RewardPropItem      ← 缴物真的动了背包
```

| 待验问题 | 结论 | 证据 |
|---|---|---|
| `OpenUI()` 开出来的页，点击能否走到引擎回调 | **能** | `<Init>b__8_1` → `OnInsultClick()` |
| props 是否生效 | **能** | `RewardPropItem` 出现在 `OnInsultClick` 里侧；页面献物与 `GetBegItem=2 件` 一致 |
| 羞辱第二页是原生的吗 | **是，且引擎自己开** | `InsultUnit` → `<>c__DisplayClass46_0::<InsultUnit>b__1`@`0x3ADB470` → `OpenUI` → `剧情ID:1450005` |
| `onEndCall` 能否回调到托管 | **能** | `原生处置页 onEndCall 触发`（16:26:37.829） |

**这条推翻了我 10-26 的判断**："羞辱页要我们在处置页关掉后补开"。原版
`OnInsultClick` 确实没有 `OpenDrama`，但它的下游 `InsultUnit` 有 —— 见上一节 §33.8 的自我更正。
现在 `PendingInsultPage` 在原生模式下**置 false**，避免多开一页演两遍。

`GetBegItem` 返回 2 件（一品筑基丹、神目符），与页面文案一致
⇒ §33.10 那条"props 算法归引擎、我们只负责调"在真机上成立。

### 34.2 我引入的 bug：**提前 46 秒放行**

时间线（`_diag_npc_duel_report.txt` 的相对秒 + `drama_ledger.jsonl`）：

```
t=275.1s  开处置页（原生），同帧可见=True        ← 页真的开出来了
t=321.0s  玩家选了：closed（"玩家直接关掉了这一页，按放过算"）
          → 挂重伤 5011 → ReleaseHold → BattleEndMgr.BattleEnd
---       此时页面仍然开着、仍然可点          ---
t=384.0s  ledger: option「收下物品，羞辱此人」  ← 玩家真的点了
t=385.0s  ledger: page 1450005（羞辱页）
```

**根因**：`VerdictPageVisible()` 读的是 `WorldStateFacts.DramaWindowReason()`，
而它判的是 **UI 层层顶名字含不含 `Drama`** —— 一个**代理量**：
"此刻最上面是谁" ≠ "我那扇窗还在不在"。中间只是引擎自己的动作条/奖励窗盖到上面，
我连续两拍读到"看不见"就当成玩家关窗了。

### 34.3 修复：三级判据，逐级都记读数

`NativePageAlive(Watch)`：

1. `w.NativeEnded` —— 引擎 `dd.onEndCall` 已回调。**事件**，优先于任何轮询。
   为什么它能代表"整串结束"：`UICustomDramaBase::OpenUI`@`0x2EE8F40` 把委托 Combine 进 `dd+0x60`，
   后续页（1450005/1450006/20410）都带同一个 `dd` ⇒ `dd+0x60` 一路继承。
2. 按名字取 `UIDramaDialogue` 判可见（`g.ui.GetUI(...)` + `UiVisible`，与 `ChatWindowVisible` 同款）。
   **同一个对象**，不是代理。量到过一次就置 `NativeSawWindow`，之后永久退出第 3 级。
3. 仅在"从没量到过那个名字"时退回层顶 —— 纯粹为了防"名字对不上/取不到"把玩家卡死。

⚠ **`UICustomDramaBattleNPCFailed2` 不能持有实例来判存活**：
反射出它的基链是
`UICustomDramaBattleNPCFailed2 < UICustomDramaBase < Il2CppSystem.Object < Il2CppObjectBase`
—— **不继承 MonoBehaviour**，它是**控制器**不是窗口，没有 `gameObject` 可问。
真正显示的是引擎按剧情 id 造的 `UIDramaDialogue`。（我最初写的 `w.NativeUi = ui` 就是这么被编译器和反射联手否掉的。）

### 34.4 下一轮真机必须看到的四行

```
开处置页（原生）：… ｜同帧可见=True（①引擎未收尾=True ②UIDramaDialogue=取到/可见=True ③层顶含Drama=?）
原生处置页 onEndCall 触发（引擎自己的收尾链；下一拍放行）
关窗判定成立（原生=True）｜①onEndCall 已回调=True ｜②UIDramaDialogue 曾量到过=True ｜③… ｜连续看不到=2 拍 ｜已记结局=insult
原生页收尾核对（结局=insult）：… 背包=N-? 件 … 好感=…
```

判据：
- **第 1 行 `②…=取到/可见=True`** ⇒ 名字判据可用，第 3 级退出；
  若显示 `取不到` 而 `③层顶含Drama=True` ⇒ 名字不对，要靠第 3 级，需要换名字（读 `g.ui` 里实际的 key）。
- **第 2 行必须出现在第 3 行之前** ⇒ `onEndCall` 是真正的收尾信号。
- **第 3 行 `已记结局=insult`**（不是 `closed`）⇒ 点击被记录、放行时机正确。若又是 `closed` ⇒ 还有第三条提前放行路径。
- **第 4 行背包/灵石/好感与点选前对比** ⇒ 引擎那些 `OnXxxClick` 真的改了世界。
