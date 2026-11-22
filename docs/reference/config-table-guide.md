# 鬼谷八荒 Mod 配置表修改 —— 实战手册

> 本文只写**经过验证**的东西。每条结论都标了出处：是官方文档原文、官方配置实证，
> 还是真机实测。**凡是我推断/猜测的，一律显式标注「未验证」** —— 这份文档的价值
> 就在于把「确定的」和「不确定的」分开，不再让人（包括我自己）在两者之间反复试错。
>
> 起因：2026-10-18 做「组队下副本」时，因为反复猜测 `addBattleUnit` / `setNPCA` 的
> 语义，来回导出了 8 次、烧掉大半天。事后把官方文档和原版配置全部翻出来核对，
> 发现**答案一直都在官方教程里，只是没读到那一段**。

---

## 〇、先记住这条方法论

**遇到「这个字段/命令怎么用」，按下面顺序找，不要跳步：**

| 顺序 | 去哪里找 | 能回答什么 |
|---|---|---|
| ① | `Mod/modFQA/配置修改教程/**配置修改教程.docx**` | 全局机制、A/B 变量、NPC 列表、ID 规则 —— **最容易漏读，信息密度最高** |
| ② | `Mod/modFQA/配置修改教程/配置对照/配置对照表.xlsx` | 每条命令/条件的参数含义（sheet：副本作用功能、条件说明表、剧情条件及影响） |
| ③ | `Mod/modFQA/配置修改教程/配置（只读）Json格式/*.json` | **原版游戏的完整配置（明文）** —— 找同类功能的范本，看官方自己怎么填 |
| ④ | `Mod/modFQA/配置修改教程/配置表头/*.xlsx` | 表结构 + 字段注释（3 行表头：中文名/字段名/类型，后面若干行「注释」） |
| ⑤ | 已装 mod 的 `ModExcel/*.xlsx` | 别人的写法（**注意：能跑≠多人可用，可能只是单人设计**） |

**③ 是最强的一环**：原版配置是明文 JSON、386 条 `DungeonTrigger` 全在里面。想知道某个命令怎么写，直接统计所有用到它的条目，规律自己就浮出来了 —— 比读文档可靠。

---

## 一、ID 规则（官方文档「二、ID冲突说明」原文要点）

```
1. 编辑器首次初始化随机一个 int（不含 -100万 ~ 1亿 这段游戏已占区）
2. 配置表里所有自定义 ID 前缀必须是 MID&，例如 MID&XXXX
3. 导出时：真 ID = excelMID + XXXX
4. 只能在 excel 里写 MID&XXXX，不能在 json 里这样写
5. 引用游戏内 ID 无需前缀，直接写数字
```

本 mod：`excelMID = -803158451`，所以 `MID&51` → `-803158400`。

**分段与表的对应关系**：`MID&NNN` 的偏移是**每张表各自独立**的。
`RoleCreateFeature.MID&51` 和 `DramaDialogue.MID&51` 是两个不同东西，互不冲突。

---

## 二、`NpcCondition` —— 人物条件组（多人机制的核心）

这是整份文档最重要的一张表。

### 2.1 它是什么

一行 = 一个「动态人物集合」。**不是一张表，是一个条件组**，被别的表用 ID 引用。

```excel
id          = MID&003
condition1  = feature_1_MID&51     ← 只要「身上有结伴而行气运」的人
raceType    = 1                    ← 人族
```

它不枚举具体是谁，而是在**运行时**筛出一批人。挂 3 个人的气运，这个集合就有 3 个人。

### 2.2 关键：`condition1` ~ `condition5` 是「串联抓取段」

**一个条件段 = 产出一个人。** 这是多人的全部秘密。

官方表头注释（`配置表头/NpcCondition.xlsx`）原文：

```
condition1ABC: 说明：-1传递过来的单位1（默认玩家），0列表循环的当前单位，
               1到5为已选取的单位，11到15为传递进来的单位。
condition2ABC: -10 玩家，-2 传递过来的单位2
例子：-1_1_2：A玩家，B条件1获取的单位，C条件2获取的单位
```

### 2.3 硬证据：原版配置里的数量对应

从 `配置（只读）Json格式/DungeonTrigger.json` 统计所有带 `addBattleUnit` 的条目：

| 原版 id | setNPCA（段数） | setNPCB（段数） | addBattleUnit 数 | 位置 |
|---|---|---|---|---|
| 41040 | 400070（**1**） | 0 | **1** | 3 |
| 10001 | 70001（**1**） | 70002（**1**） | **2** | 3, 4 |
| 10101 | 70011（**2**） | 70012（**2**） | **4** | 3, 4, 5, 6 |
| 133 / 134 | 0 | 0 | 1 | `addBattleUnit_<条件组>` 另一种写法 |
| **20201** | 100403（**2**） | 100404（**2**） | **2** | 3, 4 |

**⚠️ 更正（2026-10-18 晚）**：本文档曾写「**规律：条件段总数 == `addBattleUnit` 的条数。完全一致，无一例外。**」
这是**错的** —— `id=20201` 抓了 4 个（2+2）却只写了 2 条 `addBattleUnit`（位置 3、4），直接推翻该规律。

正确表述：

- `setNPCA` / `setNPCB` 把抓到的 NPC **按顺序追加**进 NPC 列表：A 组的在前，B 组的在后；
- 列表第 1 个 = 战斗单位 `3`、第 2 个 = `4`、第 3 个 = `5` ……（官方教程 §44 原文「按顺序增加到NPC列表，对应 1 2 3 N」）；
- **想让几个上场就写几条 `addBattleUnit_<N>`**，可以抓 4 个只放 2 个。

### 2.4 ★ 最重要的发现：重复同一条件会产出不同的人 ★

原版 `NpcCondition` **70012**：

```
condition1: heresy_1_1002|abGrade_1_0_0_0
condition2: heresy_1_1002|abGrade_1_0_0_0     ← 与 condition1 完全相同
condition2ABC: -1_0_-1
condition3ABC: -1_0_-1
```

它在 `DungeonTrigger 10101` 里对应出 **2 个不同的人**（位置 5、6）。

**所以：同一条件重复多段 ≠ 抓到同一人，而是依次取下一个。**
这解决了我一开始最不敢确定的问题 —— 不用给每个队友设计不同气运。

### 2.5 字段速查

| 字段 | 含义 | 常用值 |
|---|---|---|
| `id` | 组 ID | `MID&NNN` |
| `condition1~5` | 串联抓取段，**每段出 1 人** | 条件表达式，如 `feature_1_MID&51` |
| `condition1ABC` | 第 1 段要传的 A/B/C | 原版 `70012` 这里是**空** |
| `condition2~5ABC` | 后续段的 A/B/C | 原版 `70012` 填 `-1_0_-1` |
| `area` | 区域 | `0`=所有区域 / `-1`=相同区域 |
| `raceType` | 种族 | `1`=人族 / `2`=冥妖 / `0`=不限 |
| `sortType` / `sortPara` | 选人排序 | `0`=随机 / `1`=亲密度 / `2`=仇恨 / `3`=正道 / `4`=魔道 |
| `save` | 是否存数据 | `0`=不存 / `1`=存（存了以后调用不再判条件） |

> `sortType=0`（随机）意味着 `&N` 取到的是**随机排序后的第 N 个** —— 顺序不固定，
> 但只要凑够人数就够用。

#### 2.5.1 ★ 条件前缀 `feature_*` 的真实语义（10-18 反汇编确证）★

`UnitCondition` 里有 **6 个** Feature 方法，前缀必须逐个对上，**不能凭名字近似**：

| 配置前缀 | 方法 | VA |
|---|---|---|
| `feature_` | `Feature` | `0x182590500` |
| `featureGroup_` | `FeatureGroup` | `0x1825900E0` |
| `featureNot_` | `FeatureNot` | `0x1825903A0` |
| `featureTime_` | `FeatureTime` | `0x182590440` |
| `featureNotLastBreath_` | `FeatureNotLastBreath` | `0x182590200` |

**`feature_1_X` 走的是 `Feature`：**

```csharp
bool Feature(string[] a) {                    // Split("feature_1_-803158400", '_')
    var unit   = GetUnitInType(int.Parse(a[1]));  // a[1]="1" → unitB（列表循环的当前候选）
    var luckId = int.Parse(a[2]);                 // ★ 气运 **id**
    return unit.GetLuck(luckId) != null;          // 按 id 查，不是 group
}
```

`GetUnitInType(int)` @ `0x1825931A0`（分支目标用 `raw` 反到原始字节确认，10-19）：

```
0  → data+0x18   ★ NpcConditionData 的"当前上下文单位"（ctor 默认置 1 的那个 bool 旁边）
1  → data+0x28   ★ feature_1_ 用的 = NpcConditionData.unitB
2  → data+0x48   （另一结构 UnitConditionData.unitC 的偏移——分支2落在共享stub区）
10 → world.worldUnitAIMgr.curRunUnit
其他 → 把参数当成 **NpcCondition ID** 去 ConfNpcCondition.GetUnit 抓
```

> ⚠ 10-19 勘误：上表早先写的 `unitA=+0x10 / unitB=+0x18` 是 **UnitConditionData** 的布局；
> `GetUnitInType` 实际操作的是 **NpcConditionData**（getter/setter 原始字节：
> `get_unitA → [this+0x20]`、`get_unitB → [this+0x28]`）。两者是不同结构，别混。
> 另外 `xref set_unitB` = **0 个调用者** —— `unitB` 不由 `GetUnitsInID` 本体赋值，
> 由 `conditionNABC` 的过滤闭包（`b__5/b__6/b__7`）侧写；若 ABC 配置不对，
> `GetUnitInType(1)` 返回 null ⇒ `feature_1_*` 恒 false（这正是 E13 排查时的疑点 B）。

`GetLuck(int)` 读 `WorldUnitBase[+0x040] = allLuck` —— **运行时层**。
只写 `addLuck`（存档/面板层）的气运，条件当场看不见（见 §六"两层存储"）。

> ⚠ 曾把 `feature_1_X` 误读成 `FeatureGroup`（它比的是 `气运分组`），得出「要填 group」的
> 错误结论。**已作废**：`feature_1_X` 填的是**气运 id**。


---

## 三、`DungeonTrigger` —— 副本触发器

### 3.1 字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `triggerType` | 何时触发 | 见 3.2 |
| `triggerValue` | 触发参数 | 随 triggerType 变 |
| `dungeonType` | 副本类型 | **`0` = 所有类型**（代码分支确证，见 §3.1.1；写具体值只覆盖那些 type） |
| `dungeonID` | 副本 ID | **`0` = 所有副本** |
| `condition` | 触发条件 | 命令串，`\|` 分隔 |
| `battleFunction` | **只能副本内执行**的命令 | `\|` 分隔 |
| `dramaFunction` | 剧情命令 | `\|` 分隔 |
| `setNPCA` / `setNPCB` | **两个变量槽 A / B**，各跑一个 `NpcCondition` | 见 3.3 |
| `isRepeat` / `weight` | 是否重复 / 权重 | `weight=-1` = 必定触发 |

### 3.1.1 ★ 候选筛选的真实代码（反汇编 `ConfDungeonTrigger::GetItem` @ `0x184BA3EA0`）★

触发器不是"引擎挨个试条件"，而是**先筛候选清单**、再对候选求条件。筛选用的是三样东西：

```csharp
foreach (var item in _allConfList) {
    if (item.dungeonType != "0") {                       // +0x18，字符串比较
        if (!StrSplitIntList(item.dungeonType, '|')      // '|' = 0x7C
                .Contains(dungeonBaseItem.type))          // +0x18 = type
            continue;
    }
    if (item.dungeonID != null && item.dungeonID.Count != 0
        && !item.dungeonID.Contains(dungeonBaseItem.id)) continue;
    if (item.setNPCA != "0" && conf.npcCondition.GetUnit(item.setNPCA) == null) continue;
    if (item.setNPCB != "0" && conf.npcCondition.GetUnit(item.setNPCB) == null) continue;
    res.Add(item);
}
```

三条结论（都有代码依据）：

1. **`dungeonType` 写具体值 = 只在这些 type 的副本里生效**，判断方式是
   `StrSplitIntList(值, '|').Contains(dungeonBaseItem.type)`。写错/写漏某类型 ⇒
   该副本里**触发器连条件都不会算、直接不是候选**，日志里一行都不会有
   （踩过一次：`10|11|…|73` 照抄别人的列表，1012 号副本正好不在里面，队友一个都不来）。
2. **`dungeonType` 写字符串 `"0"` 会跳过整个类型过滤**（代码是 `!= "0"` 才进过滤分支）。
   想"所有副本都生效"就写 `0`，别去猜枚举值。
3. **`setNPCA` / `setNPCB` 也是候选过滤器**，不只是"变量槽"：非 `"0"` 时，
   若它指向的条件组**当前选不出人**，这条触发器连候选都不是。

配套确证（`UnitCondition::NpcConditionExamine` @ `0x1825A3280`）：

```csharp
bool NpcConditionExamine(string[] a)        // npcConditionExamine_<条件组id>
    => conf.npcCondition.GetUnits(int.Parse(a[1]), new NpcConditionData())
           is { Count: > 0 };
```

即**只有"该条件组能选出 ≥1 人"这一个门槛** —— 它是"队伍为空时不触发"的天然闸门，
不要随手删掉。

### 3.1.2 `NpcCondition` 的求值链（反汇编 `GetUnitsInID` @ `0x185991CB0`，10-19）

`GetItem` 选出候选触发器后，条件串里的 `npcConditionExamine_<组>` 才求值。链路：

```
UnitCondition::NpcConditionExamine @0x1825A3280
  → ConfNpcCondition::GetUnits(id, data) → GetUnitsInID(item, data)
      ① 候选池：area/region 决定 GetUnitsInArea 或全图 GetUnits(includeHidden=hideRole)
      ② sortType=0 → SortRandom 打乱
      ③ 条件1..5 各包一个 SelectObservable(池, 条件N串, 条件NABC串)
         —— 惰性：ctor 只存指针
      ④ <GetUnitsInID>b__8 @0x182BDD340（订阅回调）逐单位执行
         ReturnAction4<...>.Invoke → UnitCondition::Condition（大分发器，
         "feature" 前缀 → UnitCondition::Feature）
      ⑤ RemoveAll(null) 后返回；每槽产出 ≤1 人
```

三个要紧的推论：

1. **条件组的"能选出几个人"可以随时用 `ConfNpcCondition::GetUnits(String, NpcConditionData)`
   反射问引擎**（第二参传 null）。这是排障的第一入口——`TeamMultiProbe` 的
   `action=getunits` 就是它（10-19 加）。
2. `condition1..5` 的过滤是**惰性**的，但订阅回调在返回前已执行 ⇒ 结果是过滤后的；
   池子来自 `WorldUnitMgr.GetUnits(includeHidden)`，`hideRole=0` 时**隐藏单位不进池**。
3. `feature_1_X` 的 `unitB` 若为 null ⇒ `Feature` 直接 false ⇒ 该槽 0 人。
   `unitB` 不由 `GetUnitsInID` 本体赋值（xref `set_unitB`=0），来源是 `conditionNABC`
   配置经 `b__5/b__6/b__7` 侧写 —— **ABC 配置错 = 槽位恒空**。

### 3.2 `triggerType` 取值（官方表头注释）

```
1-通关当前房间     2-进入副本        3-开始战斗
4-单位死亡         41-所有指定单位死亡（副本结束则不触发）
42-单位死亡（无延迟监测）  43-所有指定单位死亡（无延迟）
44-任意单位死亡     5-单位hp低于百分比
6-副本结束          7-进入奇遇房间     8-满足战斗条件
9-当前副本内持续一定时间没有敌人
10-进入BOSS房间     11-副本结束（并打断） 12-房间清理
13-进入房间（0=初始化怪物之前 1=初始化怪物之后）
14-开始战斗后间隔触发（参数=毫秒）
```

### 3.3 ★ `setNPCA` / `setNPCB` 到底是什么 ★

**不是「NPC 1 / NPC 2」，是「变量 A / 变量 B」两个抓取槽。**

官方 `配置修改教程.docx` 第 35 段原文：

> 条件里面的 **A 和 B**，意思是游戏内部逻辑调用条件函数之前，需要传递 A 和 B，
> 如果没有传递，则 A 是玩家，B 是空……如果设置 **setNPCA、setNPCB** 字段，
> 则奇遇打开条件的 **A 和 B 就是 setNPC 抓取的 A 和 B**。

官方教程第 44 段原文（**多人机制的全部答案**）：

> **其他NPC1/2/3**，指的是通过 DungeonTrigger 表 **setNPCA/B 抓取到 NPC 后会按顺序
> 增加到 NPC 列表**，对应 1 2 3 N...
> 提示：配合 DungeonTrigger 表，可以实现在指定副本抓取 NPC 到副本中。

配合 `副本作用功能` 里的「战斗单位ID」总表：

```
1-玩家
2-当前副本NPC
3-其他NPC1
4-其他NPC2
5-其他NPC3
#NpcCondition的ID（抓取NPC）
其他数字-怪物ID
```

**所以 `addBattleUnit_3_...` 的 `3` = 「列表里第 1 个被抓进来的 NPC」。**

> **别把队伍槽位和战斗位混用（10-19 救助剧情事故）**
>
> | 队伍气运槽位 | `npcUnits` 下标 | 命令 | 战斗位 |
> |---|---|---|---|
> | 1 号位（第一个队友） | `[0]` | `addBattleUnit_3` | 3 |
> | 2 号位（第二个队友） | `[1]` | `addBattleUnit_4` | 4 |
> | 3 号位（第三个队友） | `[2]` | `addBattleUnit_5` | 5 |
>
> `unitAnim_3_Die` / `addAttribute_3` 里的 `3` 是**战斗位 3**，
> 也就是第一个队友，不是队伍 3 号位。
> 特殊救助剧情默认战斗位 3 = 救助 NPC；队伍占用 3 会让死亡动画打到队友。

---

## 四、`addBattleUnit` / `addBattleNPCData` —— 两条易混的命令

### 4.1 参数

`addBattleUnit`（10 个参数）：

| 位 | 含义 | 常用值 |
|---|---|---|
| 1 | 战斗单位 ID | `3`/`4`/`5`（列表位置）、`#NpcCondition的ID`（现场抓）、怪物 ID |
| 2 | x 坐标 | `-1`玩家面前 / `-2`地图中心 / `-3`npc面前 / `-4`玩家身后随机 |
| 3 | y 坐标 | `x=-3` 时 y=战斗单位id；`x=-4` 时 y=限制区域边长 |
| 4 | 进场方式 | `1`直接出现 / `2`移动进场 |
| 5 | 出现过程是否暂停 | `0`/`1` |
| 6 | 队伍类型 | `1`玩家方召唤物 / `2`盟军 / `3`怪物方 / `4`不参战 |
| 7 | 剧情 ID | `0` = 无 |
| 8 | 初始朝向 | `1`左 / `2`右 |
| 9 | 是否跟随玩家 | 默认跟随；`0`否 / `1`是 |
| 10 | 去其他房间是否隐藏 | `0`不隐藏 / `1`隐藏 |
| 11 | 是否重置剧情单位 | `0`不重置 / `1`重置 |

`addBattleNPCData`（2 个参数）：

```
addBattleNPCData_<NpcConditionID>_<索引>
```

官方原文：

> 将 NpcCondition 表中 ID 为 100201 的 NPC 组中**第一个** NPC 加入**战斗NPC容器**中

**注意「第一个」**：这条命令只取组里第一个，索引是**容器槽位**（`clearBattleNPCDatas`
的「1-5 / 0-所有」印证容器有 5 槽）。

### 4.2 ★ 三条命令的适用场景（别混用）★

| 目标 | 用哪条 | 原版范本 |
|---|---|---|
| 抓 1 个条件组的人进副本 | `addBattleNPCData_<组>_0` + `addBattleUnit_<组>_...` | `id=133/134`（`tt=5`、`dungeonType=10\|11`、`dungeonID=0`） |
| **抓 N 个不同的人（★本项目最终方案，实测 3 人成功）** | **`addBattleNPCData_<组>_0/_1/../_N-1` + `addBattleUnit_3/4/../N+2_...`** | 条件组要 **N 段**（`save` 0/1 均可，见 §4.2.2） |
| 抓 N 个不同的人（备用写法，有导出风险） | `addBattleUnit_<组ID>&0_...` ~ `&N-1_...` | `id=22201`（5 人，组 ID 是纯数字 `100801`） |
| 换用 `setNPCA` + 战斗单位 `3/4/5/6` | ❌ **本项目已弃用** | 仅原版 `10001/10101/10201/10301/41040` 用；见下方"为什么弃用" |

> **为什么首选裸数字 `3/4/5`**：`MID&003` 导出后是**纯整数 `-803158448`，不含 `&`**
> （运行时快照实证）。`GetUnitWorld` 只在字符串含 `&` 时才走下标分支；要拼双 `&` 就得
> 确认导出器怎么处理 `MID&003&0`（未知），而原版 386 条里双 `&` 出现 **0 次**。
> 裸数字走 `id-3` 分支，且写入/读取是同一块内存，零风险：
> `g.world→[+18h]→[+18h]→[+0B0h]`。

### 4.2.1 为什么弃用 `setNPCA`（10-18 最终结论）

`setNPCA` 看起来是"正规"的多入口（官方教程 §45 也这么写），但把它当**唯一**方案是个坑：

| 检查 | 结果 |
|---|---|
| 全部已装 mod（99 个 xlsx）用 `setNPCA` 的次数 | **0** |
| 原版 76 条 `setNPCA` 里写 `MID&` 的 | **0**（全是纯数字 ID） |
| 原版 `triggerType=13 + dungeonType=0 + dungeonID=0` | **0 条**（该组合零先例） |
| 原版 `tt=2 + dungeonType=0 + dungeonID=0` | **0 条** |

也就是说：`setNPCA` 要求**指定具体 `dungeonID`**（原版 100% 如此），而我们想做"所有副本通用"，
一旦 `dungeonID=0` 就落进零先例区。真机实测也印证了——触发器压根没进候选清单。

**结论：优先用 `addBattleNPCData` + `addBattleUnit_<组ID>&N`，它既有先例、又能通用。**

**⚠️ 更正（2026-10-18 晚）**：本文档曾把 `id=22201` 的 `addBattleUnit_100801&0..&4` 说成「**同一实体生成多份**」。
这是**错的**。`100801` 是 **5 段**条件组（5 段内容完全相同：`grade_1_9_10|schoolHeadquarters_1_2_9|schoolBranch_1_1`），
而 §2.4 已经证明「相同条件重复多段会**依次取到不同的人**」。所以 `&N` 取的是**第 N 个队友**，
它本身就能做多人。

不用它的唯一理由是**语法风险**：我们自己的 ID 是 `MID&003`，写成索引形式就是 `MID&003&0`（**双 `&`**）。
已扫描全部 99 个已装 mod 的 xlsx，`MID&<n>&<n>` 出现 **0 次**；原版的 `&N` 范本全是纯数字 ID。
而 `setNPCA` 路线只写 `MID&003`、单位写裸数字 `3/4/5`，**完全不碰双 `&`** —— 这是选它的决定性理由。

#### 4.2.2 ★ `&N` 的真实解析规则（2026-10-18 反汇编确认，不再是推断）★

反编译 `BattleFunction::GetUnitWorld(string)`（VA `0x182905240`）拿到**完整真身**：

```csharp
WorldUnitBase GetUnitWorld(string s) {
    if (s.IndexOf("&") != -1) {                  // 0x26 = '&'
        int[] a = CommonTool.StrSplitInt(s, '&');
        var list = g.conf.npcCondition.GetUnits(a[0].ToString());
        int i = a[1];                            // ★ 固定读 a[1]
        if (i < 0 || i >= list.Count) {
            Debug.LogWarning($"抓取NPC失败：{a[0]}_{i}");   // ← 日志出现这句就是这里
            return null;
        }
        return list[i];                          // ★ 普通下标，无特殊规则
    }
    int id = int.Parse(s);
    if (id == 1) return <玩家>;
    if (id == 2) return <当前副本NPC>;
    if (id >= 3 && id <= 10) {
        var arr = g.world.<obj>[0xB0];           // ★ 与 addBattleNPCData 同一个列表
        return CommonTool.Index(arr, id - 3);    // 3→[0], 4→[1], 5→[2]
    }
    return g.conf.npcCondition.GetUnit(id.ToString());   // 返回单个，不是列表
}
```

**三条可直接用的结论：**

1. **`X&N` 里 `N` 纯粹是 `List[N]`**，越界 → `null` + 一句 `抓取NPC失败：X_N`。
2. **`X` 必须是「条件组 ID」本身**。我们写 `MID&003` 时导出后会**展开成纯整数
   `-803158448`，不含 `&`** ⇒ 写 `addBattleUnit_MID&003_...` 会走**最后那条分支**
   `GetUnit(id)`，**只返回单个单位**。这就是「只出 1 人」的根因。
3. **想多人，两条路：**
   - **路 A（推荐，零语法风险）**：`addBattleNPCData_<组>_0/_1/_2` 把 N 个人写进
     `g.world.<obj>[0xB0]`，再用 **`addBattleUnit_3/4/5_...`**（裸数字）从**同一列表**按
     `id-3` 取。两条链在汇编层已确认是同一块内存：
     ```asm
     ; 写入
     g::get_world → [+18h] → [+18h] → [+0B0h] → List::Add
     ; 读取
     g::get_world → [+18h] → [+18h] → [+0B0h] → Index(id-3)
     ```
   - **路 B（有风险）**：`addBattleUnit_MID&003&0` —— 双 `&` 导出时如何切分**未知**
     （若切成 `["MID","003","0"]`，`a[0]="MID"` 必然失败）。原版 386 条里双 `&` 出现 **0 次**。

**去重不存在**：`GetUnitsInID` 尾部是 `list.RemoveAll(u => u == null)`
（闭包 `b__10_9` 全文只有 `test rdx,rdx; sete al; ret`），**只删 null，不去重**。
排序键是亲密度/仇恨度/正道值/魔道值/境界，`sortType=0` 走 `CommonTool.SortRandom`。
⇒ 多段相同条件**各自独立随机采样**，能取到不同的人。

**`probability=0` 不是「不触发」**：原版 386 行里 `probability=0` 有 **147 行**，
其中 `triggerType=2` 且 `probability=0` 的 **37 行照样 `createMonst`**。
（此前「0 万分之零所以不触发」的判断是错的。）

### 4.3 ★ 血泪教训：组队 mod 不能当多人范本 ★

市面上的组队 mod（`excelMID -2004589739`）的 `DungeonTrigger MID&001`：

```
setNPCA = 0        ← 完全没用
setNPCB = 0
battleFunction:
  addBattleNPCData_MID&003_0
  addBattleUnit_MID&003_-1_10_2_1_2_0
  setBattleData_wvTeamIn_ok
```

**它天生只能带 1 个人** —— 根因是它的 `NpcCondition MID&003`（队友）**只写了 1 段**
（`condition1 = feature_1_MID&51`，`condition2~5` 全 0）：一个条件组只有一段，就只能产出一个人。
它的英文文案也写着 "with **aother** cultivator"（单数，原文如此）。

所以照着它加 `_1`/`_2` 抓不到第二个人，只会把**同一个人**塞进不同槽 → **三个克隆人**（真机实测确认）。

> **能跑通的别人的 mod ≠ 支持你要的规模。** 先看它的结构上限，再决定要不要参考。

---

## 五、其它已验证命令

| 命令 | 参数 | 用途 |
|---|---|---|
| `setDramaUnitData` | `<NpcConditionID>_<索引>_<x>_<y>` | 设置「当前剧情单位」。原版 `133` 用它把条件组绑成具体单位 |
| `unitAddEffect` | `<条件组/单位>_<BattleEffectID>` | 给单位加战斗效果 |
| `addNPCLuck` | `<对象>_<气运ID>_<时间>` | 对象：`0`A / `1`B / `2`C / 其他=npcCondition ID |
| `addMultiNPCLuck` | `<对象类型>_<对象>_<气运ID>_<时间>` | 对象类型：`1`=npcCondition / `2`=条件组 |
| `clearBattleNPCDatas` | `0`=清除所有 / 其他=指定索引(1-5) | 清容器 |
| `setBattleData` | `<key>_<value>` | 写副本数据，供 `condition` 里的 `isBattleData_<key>_<value>` 判断 |
| `createMonst` | `<ID>_<x>_<y>_...` | 创建怪物（队伍方） |

### `BattleEffect.typeID = 6009`

「自身死亡时打开剧情对话」，参数 1 = 剧情 ID，一次性。
官方出处：`配置对照/效果配置对照表.xlsx` 第 416 行。

组队 mod 的「重伤」链路（反推自它的配置，已在本仓库复刻并真机验证到「上气运」环节）：

```
DungeonTrigger(triggerType=14, 1000ms)    每 1 秒轮询
  └─ unitAddEffect_<条件组>_<BattleEffect>
       └─ BattleEffect typeID=6009
            └─ DramaDialogue openFunction: addNPCLuck_1_<重伤气运>
```

---

## 六、气运写入：★ 判据是「面板可见」，不是「读得回来」★

2026-10-18 真机事故，值得单独记一节。

### 症状

工具报「组队成功」，`u.GetLuck(id)` 也读得回对象，但 **NPC 面板的「后天气运」一片空白**；
而且读一次世界就丢（日志里 `挂 3 个 → 重进世界 → 又挂 3 个`）。

### 根因

引擎里有**两层存储**，职责不同、必须都写：

| 层 | 位置 | 谁读它 | 写入方式 |
|---|---|---|---|
| **运行时层** | `WorldUnitBase.allLuck`（+0x40） | `GetLuck(id)` ⇒ **`feature_1_X` 等条件判定** | `u.CreateLuck(data)` |
| **存档/面板层** | `propertyData.addLuck`（+0x120） | NPC 面板、存档序列化 | `propertyData.AddAddLuck(data)` |

- `CreateLuck(data)` **只进运行时层，不写 `addLuck`**；
- `AddAddLuck(data)` **只进存档层**；存档层的气运要等**读档**才被物化成运行时层
  （`WorldMgr::EnterWorldFixData` 启动时逐个 `GetLuck` 做修复）。

⇒ **同一次会话里只写其中一层的后果**：
  只写运行时层 ⇒ 面板空白、读档丢失（本节上面的"面板事故"）；
  只写存档层 ⇒ **条件组（`NpcCondition` 的 `feature_1_*`）当场看不见** ⇒
  `GetUnits` 返回 0 ⇒ 依赖该条件组的触发器**整条不跑**（10-19 E13 实测：
  探针挂载 2/2 面板可见，`GetUnits` 却是 0 人，进副本无人）。

而更早的旧代码成功判据是 `u.GetLuck(id) != null` —— 自己写的自己读，永远为真，
**等于用同一个 API 自己证明自己**。

### 正确写法

```csharp
// 判据：必须真的进了 addLuck 列表
static bool InAddLuck(WorldUnitBase u, int luckId) {
    var arr = u.data.unitData.propertyData.addLuck;
    if (arr == null) return false;
    for (int i = 0; i < arr.Count; i++) {
        if ((int)((dynamic)arr[i]).id == luckId) return true;
    }
    return false;
}

// ★ 现行实现（TeamTool.AddLuck）：#1 CreateLuck 进运行时层（条件立即可见），
// #2 AddAddLuck 进存档/面板层（面板可见、可存档）。两层都写才算挂上。
// 删除同理（TeamTool.RemoveLuck）：DestroyLuck + propertyData.DelAddLuck(id)
// —— 只 DestroyLuck 的话面板还在，一读档又被物化回来（10-19 定案）。
u.CreateLuck(data);
u.data.unitData.propertyData.AddAddLuck(data);
```

### 教训

**「操作没抛异常」和「用同一个 getter 读得回来」都不算成功。**
判据必须落在**最终消费者真正读取的那个字段**上 —— 这里有**两类**消费者：
UI 面板/存档（读 `addLuck`）与条件判定（读 `allLuck`），**缺一个就是静默失败**。

---

## 七、同名 ID 冲突：按名反查是错的

工坊里可能有多个组队 mod，气运都叫「结伴而行」。

按名字反查时，命中谁**完全取决于扫描顺序** —— 真机日志里就撞到过别人的
`-2004589688`（而不是我们的 `-803158400`）。

后果不是显示错，而是**功能失效且静默**：

- 我们的 `NpcCondition` = `feature_1_MID&51` 匹配的是**本 mod** 的气运 id
- 挂上别人的，我们的 `DungeonTrigger` 永远不命中 → 队友不上场，工具却报成功

**正确做法：识别只认已知偏移（`ModIds` 登记），没有按名反查。**
名字只用于显示，不参与识别。

> **11-16 更新：连"兜底"也删掉了。** 上一版这里写的是"按名反查只作兜底"，
> 但兜底本身有三处硬伤，任何一处都足够让它下线：
>
> 1. **它救不了它声称要救的情况。** 按名扫描的候选基址第一位就是 `ModIds.Mod` 自己，
>    扫描范围 `+0..+300` 必然包含 `+51` —— 也就是刚刚 `GetItem` 失败的那个位置，
>    所以它只可能找回"同一个不存在的 id"。
> 2. **它恰恰会重新引入上面这件事**：本 mod 那一枚缺了的时候，按名反查会把工坊里
>    那个同名气运找出来挂上 —— 就是本节要防的"功能失效且静默"。
> 3. **它在最坏的时机烧 75ms。** 它要读各 mod 的 `ModData.cache`（玩家机器上没有这文件，
>    部署目录只有二进制的 `ModExportData.cache`），于是退化成探 1500 条 + 逐条翻译；
>    而它偏偏总在"气运表还没加载好"时触发 —— 也就是进世界那几秒。实测日志：10 次重试 ≈ 750ms。
>
> 偏移与表是否同源，由**开发期测试**对 xlsx 逐行核对
> （`tests/test_team_tool_contract.py::test_luck_offsets_match_config_table`）——
> 那才是这条风险的防线，不是让玩家在游戏里替我们扫表自救。

---

## 八、导出与部署流程

```bash
# 1. 模组编辑器 → 「导出模组」→ 产物落在 F:\modtest\ModExportData_<时间戳>\
# 2. 只拷配置表与 cache，不要用导出包里的 DLL/exe（那是旧的）
G=/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/ModExportData/Mod_Jgmg5L
E=/mnt/f/modtest/ModExportData_<时间戳>/Mod_Jgmg5L
cp -p "$E/ModExcel/"*.json  "$G/ModExcel/"
cp -p "$E/ModExportData.cache" "$G/ModExportData.cache"

# 3. 必须重启游戏 —— 表是启动时加载的，游戏先启、表后到 = 读到旧表
```

### 三条必查

1. **导出的 json 是加密的**（头部 `1b8bg-/%ePe]P[-/921CG`），无法直接核对内容 ——
   只能靠**文件大小变化**和行数间接判断。
2. **`ModExportData.cache` 必须一起拷** —— 只换 `ModExcel/` 而 cache 是旧的，
   编辑器/游戏可能仍按旧清单加载。
3. **游戏启动时刻 > 表文件时刻**，否则白跑一趟。

### 数据根目录（本项目特有）

发行版的**数据根**是用户目录（`ModPaths.DataRoot` → `%USERPROFILE%\.agent_loop`）：

```
C:\Users\<用户名>\.agent_loop\config.json
C:\Users\<用户名>\.agent_loop\prompts\
C:\Users\<用户名>\.agent_loop\logs/        ← 日志
C:\Users\<用户名>\.agent_loop\_diag_*.txt  ← 诊断开关
```

旧版的 `<Mod根>/ModAssets/` 只保留为出厂模板；首次启动会把缺失的用户数据迁到 `.agent_loop`，
之后工坊整包更新不再覆盖用户数据。

**不是** `<Mod根>/logs/`。放错层 = 开关不生效、日志找不到（踩过一次）。

---

## 九、诊断方法论

### 9.1 反射诊断：拿不到 ≠ 没有

写探针查「战斗单位列表」时，**不要猜字段名**。

反例（踩过）：猜 `SceneBattle.unit` 字段是 `IEnumerable`，遍历它数单位 ——
拿不到就报「战斗单位总数 = 0」，而玩家肉眼明明看到 3 个人站在场上。
**它把「我反射失败」报成了「场上没人」，直接误导了好几轮排查。**

正确做法：

```csharp
// 1) 把对象的字段/属性全打出来：名字 + 类型 + 值摘要
// 2) 顺着找所有「集合类」成员，元素类型名带 Unit/Human/Player 的展开
// 3) 结论只陈述观测到的事实，不断言
foreach (var f in o.GetType().GetFields(所有绑定)) {
    sink.Add($"F {f.Name} : {f.FieldType.Name} = {Brief(f.GetValue(o))}");
}
```

### 9.2 探针必须心跳式 + 独立落盘

- 游戏一开就断网，**结论必须落盘成文件**，关掉游戏后离线读
- 心跳循环（每 ~2s），不是一次性 —— 状态会变
- 独立日志文件，不要只写 MelonLoader 日志（后台线程会分流到另一个文件，容易找不到）

### 9.3 一次只改一个变量

反面教材（我踩的）：为了测「`addBattleNPCData` 的索引是不是第几个人」，
写了三对 `addBattleNPCData_N` + `addBattleUnit` 的组合。结果出来三个克隆人 ——
但这个实验里**同时存在两个可能的原因**（索引语义 / `addBattleUnit` 无索引），
**它根本不能证明任何一件事**，我却拿它当结论用了好几轮。

---

## 十、待验证清单（不要当成已知）

| 项 | 状态 |
|---|---|
| `setNPCA` + 三段 `condition` + `addBattleUnit_3/4/5` 能否出 3 个不同的人 | **待真机验证**。配置已逐字段对齐原版 `10001`；但**原版最多 2 段**，3 段无先例 |
| `setNPCA=MID&NNN`（`MID&` 前缀用于 setNPCA 字段） | **未验证，且无先例**。已扫全部 99 个已装 mod 的 xlsx：`setNPCA` 非 0 使用次数 = **0**；原版 100% 是数字 ID。机制上应可行（setNPCA 是 ID 字段），但没有一份可参照的成品 |
| `dungeonID=0`（全部副本）配合 `setNPCA` | **未验证**。原版 `setNPCA` 行**全部**指定具体 `dungeonID`；全表 `dungeonID=0` 仅 16/386，其中真的生成 NPC 的只有 `id=133/134`，且走 `addBattleNPCData` 路线。表头注释写「填0则所有副本都可以触发」，但**没有 setNPCA 的实证** |
| `addBattleUnit_<ID>&N` 在 `MID&` 前缀下是否可用（双 `&`） | **未验证且不建议**。`MID&<n>&<n>` 在 99 个已装 mod 里出现 **0 次**；原版范本全是纯数字 ID |
| `addBattleUnit` 的 `entry`（进场方式）/ `pause` | **未验证差异**。原版 `setNPCA` 路线清一色 `entry=1, pause=0`；组队 mod 用 `entry=2, pause=1`。本项目已改为原版的 `1_0` |
| `condition1ABC` 留空 vs 填 `-1_0_-1` 的差异 | 照原版 `70012` 抄的，未独立验证 |
| `sortType=0`（随机）时的取人顺序是否稳定 | 未验证 |
| 重伤链路末端 `addNPCLuck` 是否真的在阵亡时上气运 | 待验证 |
| `addBattleNPCData` 的索引到底是容器槽位还是「组里第几个」 | 文档写「容器」，但**未独立验证**；本文档推荐绕开这条命令 |

### 10.1 已确认无误（可放心依赖）

| 项 | 证据 |
|---|---|
| `feature_<谁>_<气运ID>`：谁 = 0-A / 1-B / 2-C / 10-X | 官方 `条件说明表` |
| NpcCondition 段内默认 `ABC=0` ⇒ **A=传递进来的单位、B=列表循环的当前候选、C=空** | 官方 `配置表头/NpcCondition.xlsx` 注释原文 |
| 所以 `feature_1_MID&51` = 「**当前候选 NPC 拥有气运 MID&51**」 | 上两条推出；组队 mod 同写法且能跑 |
| 相同条件重复多段 ⇒ 依次取到**不同**的人 | 原版 `70012`（2 段全同→2 人）、`100801`（5 段全同→5 人） |
| `dungeonType=0` = 所有副本类型 | 表头注释 + 原版 158 条带 `battleFunction` 的行用它（最高频）；`tt=13 + dungeonType=0 + setNPCA` 有 4 条实证 |
| `weight=-1` = 必定触发；`probability` = 万分比（原版 `setNPCA` 行**全是 10000**） | 表头注释 + 原版统计。组队 mod 的 `probability=0` 是异类，**别抄** |
| 其他NPC1/2/3 = 战斗单位 `3/4/5`；抓到的 NPC 按 A 组在前、B 组在后追加进列表 | 官方教程 §45 原文 + `副本作用功能` 表头 |

---

## 附录 A：本次踩坑全记录（按时间）

| # | 错误做法 | 症状 | 根因 | 正确做法 |
|---|---|---|---|---|
| 1 | 照组队 mod 写 `addBattleNPCData_0/1/2` + `addBattleUnit` ×3 | 三个一模一样的人 | 条件组只有 **1 段** ⇒ 组里只有 1 个人；`addBattleUnit_<组ID>`（不带索引）三次都抓这同一个人 | 条件组写足 N 段 + `setNPCA` + `addBattleUnit_3/4/5` |
| 2 | 去掉 `addBattleUnit` 只留 `addBattleNPCData` | 一个人都没有 | 容器不自动生成战斗单位 | 必须配 `addBattleUnit` |
| 3 | 用 `GetLuck` 判挂载成功 | 面板看不见、读档就丢 | 判据是运行时对象，不是 `addLuck` | `InAddLuck()` |
| 4 | 按名字反查气运 ID | 命中别人的同名气运，触发器不命中 | 扫描顺序不可控 | `ModIds` 登记已知偏移 |
| 5 | 猜 `SceneBattle.unit` 字段名 | 恒报「场上 0 人」 | 反射失败被报成「没有」 | 深度反射打印真实结构 |
| 6 | 诊断开关放在 `<Mod根>/` | 探针完全不响应 | 数据根是 `ModAssets/` | 放 `ModAssets/` |
| 7 | 游戏先启动、表后部署 | 读不到新表 | 表在启动时加载 | 部署后再启动 |

**共同点：每一次都是「用一个未经验证的假设去解释现象」，而不是先去读官方文档和原版配置。**
官方教程 52 段、原版 386 条 `DungeonTrigger` 一直在那里。

---

## 附录 B：常用路径速查

```
游戏根            E:\SteamLibrary\steamapps\common\鬼谷八荒\
官方教程          Mod\modFQA\配置修改教程\配置修改教程.docx          ← 先读这个
配置对照表        Mod\modFQA\配置修改教程\配置对照\配置对照表.xlsx
效果对照表        Mod\modFQA\配置修改教程\配置对照\效果配置对照表.xlsx
原版配置(明文)    Mod\modFQA\配置修改教程\配置（只读）Json格式\*.json  ← 最强证据源
表头+注释         Mod\modFQA\配置修改教程\配置表头\*.xlsx
本 mod 工程       F:\mod\ModProject_Jgmg5L\ModProject\ModExcel\*.xlsx
导出产物          F:\modtest\ModExportData_<时间戳>\Mod_Jgmg5L\
游戏加载目录      E:\...\ModExportData\Mod_Jgmg5L\
```

> 只读 json 是**明文**，可以直接 `json.load()` 后统计。这是查「某命令怎么用」
> 最快的路：把所有用到它的条目捞出来，对比参数与结果数量，规律自然显现。
