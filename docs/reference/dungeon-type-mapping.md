# 副本类型（ConfDungeonBaseItem.type）与触发器作用域调查

> 调查方式：Cpp2IL runner 反汇编（工具 `/mnt/f/.cpp2il_work/runner/`）+ 游戏自带 mod 教程的官方配置表头
> （`/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/配置修改教程/配置表头/DungeonBase.xlsx` 等）。
> 日期：2026-10-19。所有地址为 GameAssembly.dll VA。

## 结论摘要

1. **切磋不进副本触发器**：`UnitActionRoleBattle`（IntoBattle @`0x183709320`）不引用 `WorldBattleMgr`/`ConfDungeon*`，读的是独立配置 `ConfMgr.get_npcBattle`（`ConfNpcBattleBase`）。它不产生 `ConfDungeonBaseItem`（下称 dbi），`DungeonTrigger` 候选清单为空，**`dungeonType=0` 对切磋无影响**。
2. **但演武（`UnitActionRoleDrill`）会真的开一个副本**：`UnitActionRoleDrill::OnEnd` @`0x183F1C650` 调 `WorldBattleMgr::IntoBattle` @`0x1827FADA0`（实参 `rdx=NpcData、r8d=0x0C（等级12）、r9d=0`），该包装**强制 `g.conf.dungeonBase.GetItem(id)`，查不到直接中止**，然后 Clone 出 dbi 走 `IntoBattleInit#2` @`0x1827FA800`。切磋/演武产生的战斗 dbi 为 **type=41（与NPC切磋副本）**（官方表头注释 + `InitData` 的 `cmp [rsi+10h],0Ah` type=10 特判位图旁证）。
3. **副本触发器的全库唯一评估点是 `SceneBattle::Init`**（xref `ConfDungeonTrigger::GetItem` @`0x184BA3EA0` 仅 1 个调用方）。它从 `[world+18h]→[mgr+18h]→[battleData+20h]`（dbi 槽）取对象，**槽空则跳过 GetItem**——切磋战斗（无 dbi）天然无触发器；演武战斗（有 dbi，type=41）**会被 dungeonType 命中**。
4. **type 官方语义已全量拿到**（46 个值，来自官方 `DungeonBase.xlsx` 注释行，与 `ConfDungeonSpecial<N>` 类存在性交叉一致）：10~19 常规、20~29 剧情、39~92 各专项（40=决斗、41=切磋、47/48/49=突破材料/悬赏、55=道界、73/74=羽化/登仙突破、91=五朵金花约会 等），见第二节表。
5. **对组队 mod 的建议**：`dungeonType` 填 `0`（全类型）时演武/切磋副本（41）也会触发组队。若不希望如此，**排除 41**（保守再加 40/78/79 战斗型剧情副本）；若希望"任何进副本战斗都组队"则保持 0——官方文档明确"填0则所有类型的副本都可以触发"。

---

## 一、切磋/演武/剧情是否走副本触发器

### 1.1 `WorldBattleMgr::IntoBattleInit`（重载1，@`0x1827FAA10`，查 dungeonBase）的全部调用方

`xref 1827FAA10`（扫 137,813 个方法体，6 个调用方）：

| 调用方 | 地址 | 引用（反汇编 `raw`） | 判定 |
|---|---|---|---|
| `<>c__DisplayClass26_0::<CreateBattle>b__0` | `0x182F70840` | `SceneType.get_battle`、`WorldBattleData.ctor`、`MonstData`、`KeyGeneratedEventHandler` | 副本/地图战斗创建闭包 |
| `<>c__DisplayClass10_0::<UpdateUI>b__0` | `0x183E16A60` | 同上 + `SceneMgr.LoadSceneAsync`、`g.get_ui` | UI 触发的进副本闭包 |
| `<>c__DisplayClass20_0::<CreateBattle>b__0` | `0x182FF2640` | 同 26_0 | 副本战斗闭包 |
| `<>c__DisplayClass23_0::<CreateBattle>b__0` | `0x1837F7170` | 同 10_0 | 进副本闭包 |
| `<>c__DisplayClass5_0::<CreateBattle>b__0` | `0x182F26900` | 同 10_0 | 进副本闭包 |
| `<>c__DisplayClass8_0::<CreateBattle>b__0` | `0x182F2E570` | 同 10_0 | 进副本闭包 |

**没有任何 `UnitActionRole*`**。注：闭包的 declaringType 元数据在此构建中未解析（`nested`/`tclosure`/`xrefi`/`xreflea` 均无法还原宿主类，il2cpp 嵌套类型 parentIndex 缺失），但闭包体的 API 引用一致表明它们是"加载 battle 场景 + IntoBattleInit"的进副本流程，与切磋无关。

### 1.2 切磋链路（UnitActionRoleBattle）

- `UnitActionRoleBattle::IntoBattle` @`0x183709320`（raw 900B 全文核对）：构造 `UnitActionNpcUsePropsAddHM`（ctor @`0x183CA27B0`）并 `call qword [r10+1C8h]` 执行；无 `WorldBattleMgr`、无 `ConfDungeon*`。
- `UnitActionRoleBattle::UnitBattle` @`0x18370CDE0`：引用 `ConfMgr::get_npcBattle` @`0x1822DE740`、`ConfNpcBattleBase::get_victoryPara` @`0x185997530` —— 切磋胜负走 **`ConfNpcBattle` 配置**，与副本表无关。
- `UnitActionNpcUsePropsAddHM::.ctor` 的全部 5 个调用方（`xref 183CA27B0`）：`UnitActionRoleBattle::IntoBattle/BattleEnd/UnitBattle`、`UnitActionRoleStartReve::<NPCToPlayerAction>b__9_0`、`WorldUnitAIAction1034::ActionStart` —— 全是世界侧动作，非副本入口。
- 结论：**切磋不产生 dbi、不进 dungeonBase 流程**。

### 1.3 演武链路（UnitActionRoleDrill）——关键修正

- `UnitActionRoleDrill::OnEnd` @`0x183F1C650`（rawx 20000B）：
  ```
  mov rdx,[r15+10h]        ; arg2 = NpcData
  xor r9d,r9d              ; arg4 = 0
  mov r8d,0Ch              ; arg3 = 12 (等级)
  call 1827FADA0           ; WorldBattleMgr::IntoBattle
  ```
- `WorldBattleMgr` 有 3 个 `IntoBattle` 包装（`0x1827FADA0` / `0x1827FAD70` / `0x1827FAAA0`）与 2 个 `IntoBattleInit` 重载（`0x1827FAA10` 查 dungeonBase / `0x1827FA800` 不查）。
- **`IntoBattle` @`0x1827FADA0`（rawx 1400B）**：
  ```
  call ConfMgr::get_dungeonBase        ; 1822D97E0
  mov edx,[rbx+10h]                    ; dungeonId
  call ConfDungeonBaseBase::GetItem    ; 183919F40
  test rax,rax
  je   <中止>                          ; 查不到配置直接放弃进战斗
  call ConfDungeonBaseItem::Clone      ; 18605C210
  ...（clone 作 arg3）
  call WorldBattleMgr::IntoBattleInit  ; 1827FA800（重载2）
  ```
- `IntoBattleInit#2` @`0x1827FA800` → `WorldBattleData::InitData` @`0x1827F8100`：`data+0x10=id`、`data+0x20=dbi`（仅当 arg3≠null 才 Clone 填充）；随后 `进入副本：{id}，等级：{lv}` 日志（与实测日志吻合）+ type=10 特判位图（`cmp dword [rsi+10h],0Ah`）。
- 演武所用 dbi 来自 **dungeonBase 表内 type=41 的行**（41=与NPC切磋副本，官方注释；演武 UI 传入的 dungeonId 指向 41 类条目）。
- `IntoBattle@0x1827FADA0` 的其他调用方（xref）：`DramaFunction::DramaUnitBattle` @`0x18224F110`、`UnitActionRoleAppellationAttack::<BattleDungeon>b__13_1`、`UnitActionRoleStartReve::<NPCToPlayerAction>b__9_0`（称号袭击/奇遇切磋类，同走 41 型）、`MapBuildSchoolPost*`（宗门建筑战斗）、`DramaFunction` 系剧情战斗等。
- `IntoBattle@0x1827FAD70` 的调用方：`DramaFunction::IntoDungeon/IntoAreaDungeon/IntoDistrictDungeon/IntoDungeon91WithUnit/IntoDungeonWithNpc`、`PointResourcesMgr::IntoBattle60/61`（资源占领/积分赛）、`SchoolBigFight::OnChallengeAsPlayer`、`FairyChickenBattle`（斗鸡76）、`DungeonFishingAction`、`WorldUnitEffectSpecialDungeon::NewCreate` 等 —— **全部是"按 id 查 dungeonBase 后进副本"**。

### 1.4 剧情演出（DramaFunction）

剧情**确实创建副本**：`DramaFunction::CreateDungeon` @`0x18224685B~` / `IntoDungeon` @`0x182258552~` / `CreateDungeonInRange` @`0x1822464E7~` 中有对 type 1~7 参数的 `Int32::Parse` 序列（`typecmp` 扫描命中；此处 `+0x18` 是 List 元素数边界检查，解析出的 1~5/1~7 是脚本参数 slot 而非 type 字段本身，但语义上它们最终落进 dungeonBase 行）。剧情进副本统一经 `IntoBattle@0x1827FAD70`（见 1.3 调用方清单）→ 查表 → 有 dbi → **会被 DungeonTrigger 命中**。

### 1.5 触发器评估点唯一性

- `xref ConfDungeonTrigger::GetItem @0x184BA3EA0` → **唯一调用方 `SceneBattle::Init` @`0x183410980`**（该偏移在 Init+~0x6xx 处）。
- `SceneBattle::Init` 调用前守卫（raw 2600B）：
  ```
  mov rcx,[rax+18h]      ; world.battleMgr
  test rcx,rcx → je skip
  mov rdx,[rcx+18h]      ; mgr.battleData
  test rdx,rdx → je skip
  mov rdx,[rdx+20h]      ; battleData.item (dbi)
  ... xor r8d,r8d / call ConfDungeonTrigger::GetItem
  ```
  **dbi 槽空 ⇒ 完全跳过 GetItem ⇒ 触发器候选为空列表**。
- 与 `BattleTrigger2::Init`（triggerType=2=进入副本）出现 `SceneType.get_battle` 的事实相容：触发器在战斗场景初始化时求值，但**仅在存在 dbi（真副本）时**。

### 1.6 演武场（Arena）独立系统核查

- `ConfSchoolArenaItem/Base`（`0x184D877C0` 等）存在，字段 `key/value`，方法全是 `get_powerValue1~5`、`get_requirePowerArea1~5`、`get_leadListNumber*` 等 **数值参数**——是宗门演武场的**数值配置**，不是独立战斗系统；其战斗仍走 `SchoolBigFight`/`UnitActionRoleDrill` 链路。
- `find -x Arena` 无独立方法；无 `Campus`/`Tournament` 类型；`ConfSchoolWar*` 是宗门大比/宗门站(65)系统，进副本走 `IntoBattle@0x1827FAD70`。
- 独立切磋配置系统 = `ConfNpcBattle`（get_npcBattle @`0x1822DE740`）。

---

## 二、能确证的 type 取值（官方 DungeonBase.xlsx 注释，46 个）

来源：`/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/配置修改教程/配置表头/DungeonBase.xlsx`（sheet1 注释行，row 9–62），与代码中 `ConfDungeonSpecial<N>` 类存在性（事实6）交叉验证一致。置信度：**高（官方文档）**。

| type | 语义 | 备注 |
|---|---|---|
| 10~19 | 常规副本（"10-常规副本，10~19均为常规副本"） | 特判位图 `0x2042...` 中含 10 |
| 20~29 | 剧情副本（"20-剧情副本"） | |
| 39 | 天元山副本 | |
| 40 | 与NPC决斗副本 | 战斗型，建议组队黑名单 |
| **41** | **与NPC切磋副本** | **演武/切磋/称号袭击走此型** |
| 42 | 渡劫失败副本 | |
| 43 | 劫镖副本 | |
| 44 | 开局剧情副本 | |
| 45 | DPS测试副本 | |
| 46 | 宗门职位副本 | |
| 47 | 突破材料副本 | 有 `ConfDungeonSpecial47` |
| 48 | 悬赏寻宝副本（任务113专用） | 有 Special48 |
| 49 | 悬赏寻宝副本（任务114专用） | 有 Special49 |
| 50 | 悬赏救人副本（任务115专用） | **无 Special50 类**（纯数据） |
| 51 | 坟墓奇遇专用 | 有 Special51 |
| 52 | 何罗鱼第一场剧情 | 无 Special52 类 |
| 53 | 小比副本 | |
| 54 | 化神天路副本 | 无 Special54 类 |
| 55 | 道界副本（typeDesc=`dungeon_typeDesc7`，className=0） | 表内唯一真实数据行 id=注释 |
| 56 | 邀约副本 | 有 Special56（+DungeonSpecial56Trigger） |
| 57 | 玩家与多人战斗副本 | |
| 58 | 冥山打捞副本 | 无 Special58 类 |
| 59 | 七夕道具救人副本 | |
| 61 | 资源占领战 | `PointResourcesMgr::IntoBattle60/61` 实证 60/61 走副本链 |
| 62 | 宗门积分赛副本 | |
| 63 | 宗门擂台赛副本 | |
| 64 | 宗门阵眼 | |
| 65 | 宗门站 | 有 Special65 |
| 66 | 晋升长老/大长老混战副本 | |
| 67 | 玩家挑战传承副本 | |
| 68 | 挑战长老副本 | |
| 69 | 挑战大长老副本 | |
| 70 | 此副本玩家不会真正死亡 | |
| 71 | 道号挑战副本 | "1000-此分类的副本才对五朵金花道号生效"注释相邻 |
| 72 | 神秘组织副本 | |
| 73 | 羽化突破副本 | 有 Special73 |
| 74 | 登仙突破副本 | 有 Special74（DLC 同名表） |
| 75 | 羽化冥气副本 | 有 Special75（+DungeonSpecial75System） |
| 76 | 斗鸡副本 | `FairyChickenBattle::IntoBattle` 实证走副本链 |
| 77 | 兽潮战副本 | 有 Special77（+SpecialRoom77Parameters） |
| 78 | 兽潮NPC决斗副本 | |
| 79 | 剧情NPC对战副本 | |
| 81 | 伤害测试副本 | |
| 82 | 壶妖对战副本 | |
| 83 | 李四剧情副本 | 有 Special83 |
| 84 | 苍龙战斗副本 | 有 Special84 |
| 85 | 女娲战斗副本 | 有 Special85 |
| 86 | 琅琊阁主副本 | 有 Special86 |
| 87 | 堪舆师副本 | 有 Special87 |
| 88 | 琅琊阁主与张三副本 | 有 Special88 |
| 89 | 噬魂剑反噬副本 | |
| 90 | 阴阳眼仇人残魂副本 | |
| 91 | 5朵金花约会副本 | 有 Special91；`DramaFunction::IntoDungeon91WithUnit` 实证 |
| 92 | NPC猎杀噬魂剑副本 | |

附：代码交叉验证（与上表一致）：`ConfDungeonSpecial47Base::Init1` @`0x1850A35A0` 模板条目 `mov dword [rdi+18h],0x125D`=4701（编码=type47+子类型01，证明 Special 类按 type 命名）；`typecmp` 全库扫描（`cmp [reg+0x18],imm` 共 17,652 处）中 `ConfDungeonSpecial60/61/65/80` 的 `Init1`、`ConfDungeonBase::GetReward`、`GuideTriggerPassDungeon::OnBattleExit`（imm=10/19）等均有各自 type 常量。

**DungeonTrigger 表官方注释**（`DungeonTrigger.xlsx`，与 mod 侧一致）：
- `dungeonType`："可以填入多个，用|分"、"**填0则所有类型的副本都可以触发**"（官方背书 mod 表注释）。
- `dungeonID`："填0则所有副本都可以触发"。
- `triggerType`：1-通关当前房间、2-进入副本、3-开始战斗、4-单位死亡（参数：单位ID）、41/42/43-指定单位死亡（含无延迟监测变体）、44-任意单位死亡、5-单位hp低于百分比、6-副本结束、7-进入奇遇房间、8-满足战斗条件（#BattleSkillCondition）、9-无敌人持续时间、10-进入BOSS房间、11-副本结束并打断、12-房间清理、13-进入房间、14-开始战斗后间隔触发（毫秒）、15-无敌人持续（间隔）。
- `probability`："填-1则必定触发"、"角色的幸运值会影响触发概率"。

---

## 三、对 dungeonType 配置的建议

1. **`dungeonType=0`（全类型）是安全的当且仅当**你接受**演武/切磋副本（type=41）和剧情/NPC 决斗副本（40/78/79）也触发组队**。切磋本身（非副本路径）不受影响。
2. **推荐组队 mod 配置**（工作坊组队 DungeonTrigger）：
   - 若目标是"大世界/任务副本组队"：`dungeonType = 10|11|20|30|39|47|48|49|50|52|54|55|58|73|74|75`，**去掉 41**（现 mod 列表含 73，未含 74/75，可补）；
   - 若还想让"演武/切磋也组队"：加 `41`；
   - `40/78/79`（决斗/兽潮NPC决斗/剧情NPC对战）按需取舍——它们是战斗型，队友上场合理但可能干扰剧情演出。
3. `dungeonType=0` **不会**让 DungeonTrigger 在纯切磋（`UnitActionRoleBattle`，无 dbi）中触发——`SceneBattle::Init` 的空槽守卫保证这一点。
4. 演武场数值调整用 `ConfSchoolArena` 表（key/value），与 DungeonTrigger 无关。

---

## 三·补、勘误（10-19 续查四轮）：1012 是 dungeonId，不是 dungeonType —— 「1012 不在白名单⇒被过滤」判错对象

**错误出处在哪**：`team-experiment-log.md` E12 L622「这次的副本是 **1012**，它的 `type` 不在这 14 个里⇒不是候选⇒触发器连条件都不会去算」、E14 L720「1012 不在列表⇒静默不触发」、E14 L755-756「1012 可能是 41 型大地图战斗⇒白名单收窄后 1012 不再触发组队是正确行为」——以上**全部作废**，读这几处时以本节为准。根因：**把 `dungeonId`（1012）当成了 `dungeonType` 的取值去比白名单**。

**正确的反汇编口径**（`ConfDungeonTrigger::GetItem(ConfDungeonBaseItem)`@`0x184BA3EA0`）：

```csharp
if (item.dungeonType != "0") {
    var types = CommonTool.StrSplitIntList(item.dungeonType, '|');
    if (!types.Contains(dbi.type)) continue;        // dbi[+0x18] = type 字段
}
if (item.dungeonID != null && item.dungeonID.Count != 0
    && !item.dungeonID.Contains(dbi.id)) continue;  // dbi[+0x10] = id 字段（副本日志「进入副本：1012」里的 1012 就是它）
```

- `dungeonType` 白名单（E12 记录的 14 类，或者 E14 起 53 类——都含 `10|11|…` 万年秘境类）比对的是 **`dbi.type`**（[dbi+0x18]），不是副本编号。
- **1012 是 `dungeonId`**（「进入副本：1012，等级：1」日志打的就是 dbi.id，`WorldBattleData::InitData` 的 `data+0x10`）。因其与「类属型 type 值域 10~19/20-29/40-92」**同值域易混**——一个四位副本编号被误读为 type。
- 父 agent 提供的实测口径（【引】，Mod 表加密、我这边无法二进制化 dBase）：**1012 的 `type=10`**——即 `dType=10|11|…`（含 10）的触发器**白名单本来就命中 1012**；E14 白名单收窄（53 类，仍含 10）**同样命中**。
- **由此可得出的正确推断**：`dungeonType` 过滤**不是**「进 1012 没人」的原因。留给 B 组（触发器重复节流/`DataObjectData` lastTime）与条件门（`npcConditionExamine` Count>0、`featureNot_MID&003_*` 参数族——E12 L151 抄录的 `1011|1012|1013` 属另一个 id 域，与副本 1012 的同名巧合才是元凶假相）去找。
- 提醒：`triggerType` 家族（1-9、41-44 等，见上表）与 `BattleTrigger<N>` 类同号系（`BattleTrigger6::OnBattleEndFront` 对 `triggerType=6`"副本结束"）——`BattleTrigger` 类号↔`triggerType` 同域，**别再把它与 `dungeonType` 混谈**。

---

## 四、未确证部分

1. **6 个 CreateBattle 闭包的宿主类名**：此 il2cpp 构建的嵌套类型 parentIndex 未解析（`nested`/`tclosure`/`xrefi`/`xreflea` 全部失败），只能以闭包体 API 引用定性（均为进副本流程），不能给出宿主 UI 类名。
2. **41 型 dbi 的具体 dungeonId 行**（如演武用的 id=4101 之类）：真实 dungeonBase 数据在加密 `cacheSimple/*.cache` 与 Mod 表内（`Mod_Jgmg5L/ModExcel` 加密），未解；但"演武传 dungeonId → dungeonBase.GetItem(id) → 查不到中止"的机制已实证，id 只能指向 41 型条目（`InitData` 对 type=10 特判 + 官方 41 注释）。
3. **`IntoBattleInit#1`（@`0x1827FAA10`）与 `#2` 的功能差异细节**：`#1` 先 `dungeonBase.GetItem(arg2)` 再 Clone 进 `#2`；`#2` 直接收成品 dbi（arg3）。二者最终都落 `InitData`，dbi 槽语义一致。
4. type `60` 的官方注释行缺失（注释行从 61 开始），但 `PointResourcesMgr::IntoBattle60/61` 方法名 + `ConfDungeonSpecial60` 类存在，可判定 60=资源占领战（61 注释"资源占领战"应为 60/61 配对，61=宗门积分赛、60=资源占领，置信中）。
5. type `30`（mod 列表出现）与 `80`（有 Special80 类）的语义未在官方注释中出现——**未确证**（80 在 `ConfDungeonSpecial80::OnInit` 有 imm=1 常量，无语义文本）。

---

## 附：关键地址

| 符号 | VA |
|---|---|
| `WorldBattleMgr::IntoBattleInit`（重载1，查 dungeonBase） | `0x1827FAA10` |
| `WorldBattleMgr::IntoBattleInit`（重载2，收成品 dbi） | `0x1827FA800` |
| `WorldBattleMgr::IntoBattle`（包装A：dungeonBase.GetItem(id)→Clone→Init#2） | `0x1827FADA0` |
| `WorldBattleMgr::IntoBattle`（包装B：剧情/宗门/斗鸡等） | `0x1827FAD70` |
| `WorldBattleMgr::IntoBattle`（包装C：UIMapBattle 等） | `0x1827FAAA0` |
| `WorldBattleData::InitData`（dbi 槽 +0x20、id +0x10、"进入副本"日志、type=10 特判） | `0x1827F8100` |
| `ConfDungeonTrigger::GetItem(ConfDungeonTriggerItem, ConfDungeonBaseItem, …)` | `0x184BA3EA0` |
| `SceneBattle::Init`（全库唯一触发器评估点；dbi 空槽守卫） | `0x183410980` |
| `ConfMgr::get_dungeonBase` | `0x1822D97E0` |
| `ConfMgr::get_dungeonTrigger` | `0x1822DA410` |
| `ConfMgr::get_npcBattle`（切磋独立配置） | `0x1822DE740` |
| `ConfDungeonBaseBase::GetItem` | `0x183919F40` |
| `ConfDungeonTriggerBase::GetItem`（触发器表按 id 查候选条目，IsTrigger 配套读档） | `0x184B8E3A0` |
| `BattleTriggerBase::IsTrigger`（四道门【五轮权威字段名】：门② isRepeat==0 才查「已触发」永久标记；门③ triggerCD≠0 才做月 CD；门④ dungeonIsRepeat==0 才查战场 Contains；门⑤ condition 串 2 参求值；门⑥ setNPCA/ setNPCB != \"0\" 才再查 GetUnits） | `0x182B01E40`（真体注解自 0x182B01F40-182B021B8；我方 (0,1,0) 门②③ 自动闭口） |
| `BattleTriggerBase::Trigger`（执行体：Random(0,10000)+GetItemsAdding 候选构建+RandomRate 找行→写回【五轮】：probability=0/weight=-1 ⇒ 候选整列筛选到底是「必中」还是「随机分币」（H1 三参 lambda 走向），但 (0,1,0) 行的月 CD / 已触发两个节流门全闭；字段名 setNPC=\"0\" skip、weight=0） | `0x182B02220`（写回段 0x182B02855；「已触发」键=Concat(\"ConfDungeonTrigger\",row.id)，月 CD 键=Concat\"ConfDungeonTrigger\"`.就 序在 0x182B02778/0x182B027DB 附注同体 D.NOT） |
| `BattleTriggerBase::Trigger` 尾段（写回 lastTime：`SetString(Concat([186D70188],id), 已触发字面量)` 与 `SetString(Concat([186C53900],id), roundMonth.ToString())`） | `0x182B02855` |
| `BattleTrigger6::OnBattleEndFront`（`xor edx,edx; jmp Trigger(0)`【五轮】= **同 VA 是 `BattleTrigger1::OnPass` 的同体去重**（编译器同情对空参 wrapper 去重）**OnBattleEndFront 自体确认【新反汇编/bt6_init】；**tt=6 = 战斗结束事件链**：`BattleTrigger6::Init` 里 `EventsMgr::On(EBattleType.BattleEndFront 类箱 [186E8D928])+KeyGeneratedEventHandler::ctor(this, OnBattleEndFront)`；fire 侧 = `BattleEndMgr::BattleEnd(bool)`@0x1828FA0D0 的 `EventsMgr::Emit(key, new DataStruct<bool,bool>(win))`） | `0x182AFE620`（BT6.Init=0x182B01070）|
| `BattleTrigger1001::DlcBattleEndTrigger`（同帧去重：`Time::frameCount==静态记录帧→跳过`） | `0x182AFC720` |
| `SceneBattle::Init` 的触发器准备段（合并排序【五轮 dll 侧订正】——[scene+0xC0]=`List<BattleTriggerBase> battleTriggerBases`，段内 `List.Add([scene+0xC0], new BattleTriggerN 实例)`；同段 `String.Concat(\"BattleTrigger\", row.triggerType.ToString())` 作为 `GetStaticObj` 的 dict key（按 tt，不 per-row）→`CommonTool::CreateClass`(0x18338AD10)+虚调用 `[klass+0x178]` 的 new+`Init(row.id)`；**拼接字面量 0x186CED5E8=\"BattleTrigger\"【lit】；`[item+0x28]=triggerType`【引 dll】） | `0x18341126D`（循环体）／`0x183411420`（key 拼装） |
| `BattleFunction::AddBattleNPCData`（battleFunction：`GetUnits(int组id, data=null)` → `CommonTool::Index(结果, 槽位)` → 加进战斗 NPC 容器 `[battleData+0xB0]`） | `0x1828FDEE0` |
| `CommonTool::Index`（越界/空 → 返 null；`index<0` 或 `index≥count` 双防卫） | `0x18338C9B0` |
| `NpcConditionExamine`（condition 函数：`GetUnits(int.Parse(id串), new NpcConditionData()` **且置 `[data+18h]=0`**`) → Count>0`） | `0x1825A3280` |
| `NpcConditionData::.ctor`（`[+0x10]=new List`、`byte [+0x18]=1`、尾跳空 stub `0x1820EFF80`） | `0x182B230F0` |
| `NpcConditionData::get_unitA`（读 `[data+0x20]`，null 时惰性取 `g.world[+0x10]` 写回） | `0x182B23170` |
| `ConfNpcCondition::GetUnits(int, data, int)`（int 版总入口；`Count==0 && id!=0` → 行[+0xA0] 剧情函数 → OptionsFunction → 重试） | `0x1859937F0` |
| `ConfNpcCondition::GetUnitsInID`（月缓存刷新+TheList 复用+子项分派） | `0x185991CB0` |
| `ConfNpcCondition::get_data`（静态上下文槽 getter，槽=  `[186C72F40 box+0xB8]`，null 惰性建默认） | `0x185993F80` |
| `UnitCondition::GetUnitInType`（type=0/1/2/10 四分支+throw 保护，default=按 id 查） | `0x1825931A0` |
| `UnitCondition::Feature`（谓词：`GetUnitInType(a[1])` 记命 → `GetLuck(a[2]) != null` 才真） | `0x182590500` |
| `ConfDungeonBaseItem::Clone` | `0x18605C210` |
| `UnitActionRoleBattle::IntoBattle`（切磋，无副本引用） | `0x183709320` |
| `UnitActionRoleBattle::UnitBattle`（读 ConfNpcBattle） | `0x18370CDE0` |
| `UnitActionNpcUsePropsAddHM::.ctor` | `0x183CA27B0` |
| `UnitActionRoleDrill::OnEnd`（演武→IntoBattle，r8d=0x0C） | `0x183F1C650` |
| `DramaFunction::IntoDungeon` / `CreateDungeon` | `0x182258500` / `0x18224685B` |
| `PointResourcesMgr::IntoBattle60` / `IntoBattle61` | `0x183449C80` / `0x18344A390` |
| `ConfDungeonSpecial47Base::Init1`（模板 +0x18=0x125D） | `0x1850A35A0` |
| `ConfSchoolArenaBase`（演武场数值表，非战斗系统） | `0x184D85D50~` |
| 官方表头目录 | `/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/配置修改教程/配置表头/` |
