# 计划：队友倒下剧情 —— 从「无选项死窗」改成「可救助的救援窗」

> 状态：**仅调研完成，未写任何代码 / 未改任何表**
> 前置：`docs/features/team.md`（组队整合文档；本计划是它的下一阶段）
> 调研日期：2026-10-22
> 依据：官方配置库 `Mod/modFQA/配置修改教程/`（761 张表，只读）+ 本工程 `ModExcel`

---

## 一、用户的诉求（原话）

> 「当前是副本中队友倒下之后出现一个剧情UI然后挂重伤buff，能不能更加丰富一些呢？
> 例如可以选择一些选项（比如喂丹药让队友重新起来之类的），这些主要是可以参考一下
> 游戏原生的救助npc那里，那里就是会有剧情窗口，可以选择喂服丹药之类的」

⇒ 两个子目标：

1. **窗口里要有选项**（现在是纯展示的死窗）；
2. **选项要有后果**（喂丹药 → 队友重新站起来）。

---

## 二、现状（改之前是什么样）

我们的 `DramaDialogue MID&190`：

| 字段 | 值 | 说明 |
|---|---|---|
| `uiType` | 1 | 普通对话窗 |
| `npcLeft` / `npcRight` / `speaker` | `1` / `11` / `2` | 与原生 `982142` **一字不差**（当初照它建的） |
| `dialogue` | `agentloop_team_hurt_190` | |
| `openFunction` | `addNPCLuck_1_MID&52` | **挂「内外重伤」全靠这一行** |
| `options` | **`0`（空）** | ← **这就是要改的地方**（xlsx 里存的是字符串 `0`，不是空白） |

`DramaOptions` 里 `MID&191~199` **全部未占用**（实测：已占用的只有
`101/102/111/112/121/122/131/132/141/142/151/152/161/162/171/172/181/182`，
其中 `181/182` 是组队邀请窗）。

触发链（现状，已验证）：

```
队友战场单位 Die
  └→ Effect6009「自身死亡时打开剧情对话」
       └→ DramaDialogue MID&190 （无选项）
            └→ openFunction: addNPCLuck_1_MID&52   ← 挂重伤
  └→ TeamBattleFollowup.OnBattleExit
       └→ 摘掉号位气运 = 自动离队
```

---

## 三、原生救助 NPC 是怎么做的（可直接照抄的范本）

### 3.1 触发：`DungeonTrigger id=132`

```text
triggerType = 1                     ← 单位死亡触发
condition   = isBattleData_rescue_1
            | isBattleData_npcAlive_1
            | isBattleData_isBossRoom_1
dramaFunction = battleFunction_setDramaUnitData_3_2 | callDrama_982142
isRepeat = 1     probability = 10000
```

语义：**有 `rescue` 标记 + NPC 还活着 + 在 BOSS 房** ⇒ 把战斗位 3 设成剧情主角 ⇒ 开剧情 `982142`。

> ⚠ 注意：`isBattleData_rescue_1` 这个 key **在我们这边从来没出现过**。
> 这正是 10-21 删掉 `IsRescueBattle()` 守卫的原因（`battleCustomData` 里没有 `rescue`/`NPC`）。
> 也就是说：**原生救助剧情的触发条件依赖一个我们复刻不出来的 battleCustomData 标记**。
> ⇒ 触发方式不能照抄，得继续用我们自己的 6009 链路。

### 3.2 窗口结构：`DramaDialogue 982142`

```text
uiType=1  npcLeft=1  npcRight=11  speaker=2
options = 0      ← 原生这个窗也没有选项！
```

**关键发现**：原生 `982142` 本身**也是无选项的**。选项在**后续剧情**里。

### 3.3 「喂丹药」的完整范本：`DramaDialogue 630024`（老婆婆咳嗽）

这才是真正的样板：

| 元素 | 配置 |
|---|---|
| 正文 | 「咳！咳。。。咳！（剧烈咳嗽）好心人，你有豆子吗？…」 |
| 选项1 | 「我有豆子（给予丹药）」→ `itemSelect_dramaSelectItem6300241_1_1_0_0` + `notNextDialogue` |
| 选项2 | 「我没有豆子」→ `function=0`，`next=630034` |

**★ 关键细节：分支挂在「选项」的 `nextDialogue` 上，不在「对话」上 ★**

```
DramaDialogue 630024   nextDialogue = 0          ← 对话本身没有分支
DramaOptions  6300241  nextDialogue = 630025|630031   ← 两个分支挂在这里
DramaOptions  6300242  nextDialogue = 630034
```

⇒ 语义是：**玩家点了这个选项 → 执行它的 `function` → 再按 `condition` 从它的
`nextDialogue` 里挑一条继续**。选项的 `function` 用 `notNextDialogue` 表示"执行完先别走，
等我选完道具再决定"。

**两个分支靠 `condition` 自动分流**：

| 分支 | condition | 正文 |
|---|---|---|
| `630025`（好转） | `lastItemPillEffectType_101_201` | 「虽然你的豆子跟雪儿的好像有点不同，不过我已经好多了…」 |
| `630031`（恶化） | `0`（兜底） | 「啊！你这豆子有。。。有毒！…」 |

### 3.4 分流判据的准确语义

官方《条件说明表》第 28 行：

> `lastItemPillEffectType` | **丹药作用分类ID1_ID2…**
> `lastItemPillEffectType_101_201` = 上一个选择的丹药的作用类型为 **101 或 201**

而 `effectType` 的真实含义（我从 `ItemPill.json` 反查 `ItemProps` 名字确认）：

| effectType | 实际是 | 条数 |
|---|---|---|
| **101** | **补元丹（回体力）** + 同类增益 | 665 |
| **201** | **延寿丹** 等 | 376 |
| 501 | 淬肠百虫膏（毒/负面） | 270 |
| 301 | 筑基丹 | 117 |
| 302 / 102 / 103 | 其它 | 28 |

⇒ **原生那句「喂对了就好转」= 玩家喂的是补元丹或延寿丹**。语义完全对得上。

---

## 四、让队友「重新站起来」的命令

### 4.1 `reviveUnit`（★核心★）

官方《副本作用功能》第 40 行：

> `reviveUnit` | 战斗单位ID | `reviveUnit_2` | **复活战斗单位**

#### 写法有两套，取决于你在哪张表里用

| 用在哪 | 写法 | 依据 |
|---|---|---|
| `DungeonTrigger.battleFunction` | `reviveUnit_3` | 原生 `id=82` 等 4 处 |
| `DramaDialogue.function` / `DramaOptions.function` | **`battleFunction_reviveUnit_3`** | 原生 6 处（见下） |

**忘了加 `battleFunction_` 前缀 = 命令不会被识别**（这是本计划最容易踩的坑）。

#### 在剧情里用的实证（与我们的 190 同型）

原生 `DramaDialogue.function` 里 `battleFunction_reviveUnit` 有 **6 处，全部 `uiType=1`**：

```text
80111    battleFunction_stopUnitAI_0 | battleFunction_reviveUnit_2
82131    battleFunction_releaseUnit_3 | battleFunction_reviveUnit_3 | unitLog_...
82211    battleFunction_setDramaUnitData_2_3 | battleFunction_reviveUnit_2 | battleExit_0 | setAttr_hp_1
1800021  battleFunction_delNPC_3 | battleFunction_reviveUnit_4 | ...
1800031  battleFunction_delNPC_4 | battleFunction_reviveUnit_3 | ...
1800321  battleFunction_setDramaUnitData_3_4 | battleFunction_reviveUnit_3 | ...
```

#### 在触发器里用的实证

```text
id=82  triggerType=1  condition=isBattleData_qiyumsr1_zhandou|isBattleUnitDie_3_1
       battleFunction = reviveUnit_3
```

**这一行是最贴近的模板**：`triggerType=1` + `isBattleUnitDie_3_1`（战斗位 3 死亡）
⇒ `reviveUnit_3`（复活战斗位 3）。

原生 `DungeonTrigger` 里**一共 4 处** `reviveUnit`：

| id | triggerType | 条件 | battleFunction |
|---|---|---|---|
| **82** | **1（单位死亡）** | `isBattleUnitDie_3_1` | `reviveUnit_3` |
| 74 | 4 | `isBattleData_zhuoj2_1` | `reviveUnit_2` + `setBattleData_...` |
| 75 | 4 | `isBattleData_zhuoj3_1` | `reviveUnit_3` + `setBattleData_...` |
| 77 | 4 | `isBattleData_chourentiaox1_1/2` | `reviveUnit_2` + … |

> ⚠ **只有 `id=82` 是「单位死亡触发 → 复活」**，另外三处是 `tt=4`
> （靠 `battleCustomData` 标记驱动的另一种时机），与"死亡后复活"不是同一场景。
>
> ⇒ `reviveUnit` 这条命令本身在原生被稳定使用（触发器 4 处 + 剧情 6 处）；
> ⇒ 但「**死亡 → 复活**」这个**具体时序**只有 `id=82` 一个先例。

### 4.2 `addAttribute`（备选：只回血不复活的折中）

```text
addAttribute_<战斗位>_<属性ID>_<类型>_<数值>_<是否隐藏UI>
属性ID：6=体力 / 7=灵力 / 8=念力 / 200=体灵念
类型：  0=最大值百分比 / 1=固定值 / 2=当前值百分比
```

原生实例：`addAttribute_3_6_0_100_1`（战斗位 3 体力回满）。

### 4.3 `npcRevive` —— **不要用**

官方第 200 行：

> `npcRevive` | 剧情id | …**先进入选择界面选择需要复活的NPC，过月时复活该NPC**

⇒ 对**世界 NPC**、而且**过月才生效**。我们的队友本体根本没死（死的只是战场克隆体），
这条命令**语义完全不适用**，不要误用。

---

## 五、必须承认的不确定性（这是本计划的核心）

### 5.1 `itemSelect` 在「战斗副本内」能不能用 —— **未知**

`itemSelect` 打开的是**道具选择界面**。原生一共 **121 条**用例
（`itemSelect` 73 / `itemSelectAll` 39 / `itemSelectElement` 4 / `itemSelectLevel` 3 /
`itemSelectNotCost` 2）。

**一条可查的硬事实**：把 386 行 `DungeonTrigger` 的 `dramaFunction` 全扫一遍，
含 `itemSelect` 的 **= 0 条**。

⇒ 也就是说：**没有任何原生用例是在"战斗触发器直接调起"的剧情里用 `itemSelect` 的**。
（它们全都在大地图事件 / 城镇 / 剧情包这些走 `callDrama` 的路径里。）

这**不能证明**它不能用，只能说明**没有先例**。所以必须实测。

这是决定整个方案形态的关键：

- **能用** ⇒ 做「从背包挑丹药」的完整版（更自由，玩家可以选喂哪颗）
- **不能用** ⇒ 退化成「预设 2~3 种丹药，各给一个选项」，用 `item` 条件控制可见性（僵硬但可靠）

⇒ **必须先验这一条**，否则方案可能白写。

### 5.2 `battleFunction_reviveUnit_3` 能否救活「已经 mIsDie=1 的战场单位」 —— **未知**

原生 6 处剧情用例的背景都是**引擎自己开的剧情**（`callDrama` 路径），
我们是 **6009 死亡回调**开的。时机差别在于：走 6009 时，单位不仅死了、
`mIsDie` 也已置 1，而且死亡流程（`UnitCtrlBase.Die`）已经走完。

理论上 `reviveUnit` 读的是同一份 `battle.data` 战斗位表，
**但我不确定引擎是否允许把一个 `mIsDie=1` 的单位拉回来**
（它内部可能只重置动画/血条，也可能有 `if (isDie) return` 之类的守卫）。

⇒ 这也是必须先验的。

### 5.3 C# 怎么知道玩家选了哪个选项 —— **未查到**

选项的 `function` 能改状态（`reviveUnit` / `addNPCLuck` / `setBattleData`），
但 `TeamBattleFollowup.OnBattleExit` 那段 C# 逻辑**现在是硬编码的**：
「倒下 ⇒ 挂重伤 + 摘号位（= 离队）」。

如果玩家选了「救」，C# 不该再摘号位。那 C# 怎么知道？

目前**没查到**确定的回调点。可能的路径（都未验证）：

| 路径 | 思路 | 风险 |
|---|---|---|
| A | 选项里 `setBattleData_<key>_1`，C# 读 `battleCustomData` | 需确认 `OnBattleExit` 时该字典还在 |
| B | 选项里 `addNPCLuck` 一个「已获救」气运，C# 查该气运 | 需新增气运条目 |
| C | 选项里直接 `addNPCLuck` / `clearFeature` 撤销重伤，C# 不改逻辑 | **最省事**：让选项自己把状态改对，C# 只管离队判断 |

> **C 看起来最有希望**：如果选项能直接撤销「内外重伤」气运，
> 那 C# 侧的 `ApplyHurtAndLeave` 只需要改成"查气运在不在"，
> 不再需要知道玩家点了什么 —— 状态即事实。

### 5.4 `reviveUnit` 放在「选项」里能否立即生效 —— **无先例**

三个层级的执行时机（表头定义）：

| 字段 | 含义 | 执行时机 |
|---|---|---|
| `openFunction` | 打开作用 | 窗弹出瞬间 |
| `function` | 关闭作用 | 玩家关窗瞬间 |
| `DramaOptions.function` | 选项作用 | **点这个选项时**（立即） |

原生 `battleFunction_reviveUnit` 的分布：

| 位置 | 处数 |
|---|---|
| `DramaDialogue.function`（关窗时） | **6** |
| `DramaOptions.function`（点选项时） | **0** |
| `DungeonTrigger.battleFunction` | 4 |

⇒ 想要「喂完丹药**立刻**看到队友起身」，就得放选项里，而**这条路原生零先例**。
相邻证据：选项级 `battleFunction_addAttribute` 有 3 处（说明选项里能放战斗命令），
但 `reviveUnit` 没有。

**降级方案**：放对话级 `function`（关窗时复活）—— 有 6 处先例，但玩家要等窗关掉才看到人起来。
观感差一点，但可靠。

---

## 六、建议的实施顺序（每步单独可回退）

> **原则：先用最小改动验证两个未知，再写完整方案。**
> 理由见 `docs/features/team.md` §9.4 —— 这个功能历史上因为"同时改多个变量"浪费过大量时间。

### 第 0 步：验证 `reviveUnit_3`（**最小、最快、最独立**）

**★ 写法必须是 `battleFunction_reviveUnit_3`，不能直接写 `reviveUnit_3` ★**

`reviveUnit` 是「副本作用功能」表（= `battleFunction`）的命令；
要在**剧情 / 选项**里用它，必须加 `battleFunction_` 前缀。原生 6 处全是这个写法。

第 0 步的具体做法：

- 在 `190` 的 **`function`** 字段追加 `|battleFunction_reviveUnit_3`
  - `openFunction` 保留现有的 `addNPCLuck_1_MID&52` 不动（挂重伤仍需它）
  - `function` 当前为空 ⇒ 正好放这里
- 进副本让队友倒下 → 看队友是否**当场站起来**
- **只看这一件事**，不加选项、不改 C#

**★ 预先说明一个时序问题**：表头把 `function` 定义为「**关闭作用**」，
`openFunction` 才是「打开作用」。所以放在 `function` 里的 `reviveUnit_3`
是**玩家关掉剧情窗那一刻**才执行的，不是弹出的瞬间。

这对第 0 步的验证没有影响（关窗后队友站起来也算成立），
但如果最终要求"喂完丹药**立刻**看到队友起身"，就得改成：
把 `reviveUnit` 放进**选项的 `function`**（选项点击即刻执行），
而不是对话的 `function`。原生 6 处剧情用例用的是对话级 `function`，
所以「选项级能不能立即生效」也属于待验项。

**已有的旁证**：选项级 `battleFunction_addAttribute` 在原生有 **3 处**
（`8000021` / `8001021` / `9810002`）⇒ 选项里放战斗命令这条路本身是通的。
但选项级 `battleFunction_reviveUnit` **一处都没有**（原生 6 处全在对话级 `function`）。
⇒ 「选项里复活」比「对话关闭时复活」**少一个先例**，要单独验。

**为什么这一步把握比原先大得多**：原生 `DramaDialogue` 里 `battleFunction_reviveUnit`
有 **6 处**，全部是 `uiType=1`（普通对话窗），**与我们的 190 完全同型**：

```text
80111    function = battleFunction_stopUnitAI_0 | battleFunction_reviveUnit_2
82131    function = battleFunction_releaseUnit_3 | battleFunction_reviveUnit_3 | unitLog_...
82211    function = battleFunction_setDramaUnitData_2_3 | battleFunction_reviveUnit_2 | battleExit_0 | setAttr_hp_1
1800021  function = battleFunction_delNPC_3 | battleFunction_reviveUnit_4 | ...
1800031  function = battleFunction_delNPC_4 | battleFunction_reviveUnit_3 | ...
1800321  function = battleFunction_setDramaUnitData_3_4 | battleFunction_reviveUnit_3 | ...
```

> 仍存的未知：这 6 处都是**引擎自己开的剧情**，我们的是 **6009 死亡回调**开的。
> 时机不同，但同一个 `uiType=1` 窗、同一份战斗数据 ⇒ 仍需实测，只是风险低多了。

### 第 1 步：验证 `itemSelect` 在战斗内

- 给 `190` 加**一个**选项，`function = itemSelect_<tips>_1_<类型>_<分类>_0`
- 进副本 → 看**道具选择界面弹不弹**
- 若弹 ⇒ 能做完整版；若不弹 ⇒ 走"预设选项"的退化版

### 第 2 步：验证「选项能改状态」

- 选项 `function` 里挂气运 / 打标记（同样注意战斗命令要加 `battleFunction_` 前缀）
- 确认选项点击后引擎真的执行了（`battleCustomData` / 气运变化 / 日志）
- 这一步同时回答 §5.3 的「C# 怎么知道」

> 原生先例：`DramaOptions 9810002` 的 function 是
> `addAttr_standUp_1_50 | battleFunction_setBattleData_interact_1 | battleFunction_addAttribute_1_7_0_-50_0`
> ⇒ **选项里可以混用「纯剧情命令」和「带 `battleFunction_` 前缀的战斗命令」**。
> 这正好支撑"选项既改状态、又给 C# 留标记"的设计。

### 第 3 步：写完整设计（前两步都成立后）

草案（**待前两步结果确认后再定稿，不要现在照着写**）：

```text
190 正文：<队友名>倒在血泊中，气息微弱。
│  （190.function 里先放 battleFunction_reviveUnit_3 已由第 0 步验证）
│
├─ 选项1「喂服丹药」   condition=itemType_0_<丹药大类>_0_2_1   ← 没丹药不显示
│    └─ function = itemSelect_<tips>_1_<大类>_<分类>_0
│         选项.nextDialogue = <好转页> | <恶化页>
│         ├─ 好转页 condition=lastItemPillEffectType_101_201
│         │     function = battleFunction_reviveUnit_3 | battleFunction_setBattleData_<key>_1
│         └─ 恶化页 condition=0（兜底）
│               function = （保留重伤，可再追加负面气运）
├─ 选项2「以灵力护住心脉」cost=1_<灵石数>
│    └─ function = battleFunction_addAttribute_3_6_0_50 | battleFunction_setBattleData_<key>_1
└─ 选项3「无能为力」
     └─ function = 0（维持现状：挂重伤 + 自动离队）
```

> 三个选项都要留 `battleFunction_setBattleData_<key>_1` 这类标记，
> 供 C# 在 `OnBattleExit` 时判断"玩家救没救"（见 §5.3 路径 A）。
> 战斗命令一律加 `battleFunction_` 前缀。

**表改动预估**：

| 表 | 新增 |
|---|---|
| `DramaOptions` | `MID&191/192/193`（三选项）+ `MID&194/195`（喂丹药结果分支） |
| `DramaDialogue` | `MID&191`（喂对）/ `MID&192`（喂错） |
| `LocalText` | 对应 5~7 条文案 |

`ModIds.DramaTeamHurtBase = Mod + 190` 段有 `+190~+199` 十个号，够用。

**代码改动预估**（`tools/TeamBattleFollowup.cs`）：
`ApplyHurtAndLeave()` 的判据从"无条件挂重伤+摘号位"改成"查状态"（见 §5.3 路径 C）。

---

## 七、调研中确认的「不能照抄」的坑

| 坑 | 原因 |
|---|---|
| 照抄 `id=132` 的 `condition` | 依赖 `isBattleData_rescue_1`，这个 key 我们复刻不出来（10-21 已验证不存在） |
| 照抄 `npcRevive` | 对世界 NPC、过月生效；我们队友本体没死 |
| 照抄 `982142` 想找选项 | 它本身就是无选项窗，选项在后续剧情里 |
| 以为 `effectType` 是"丹药分类 ID" | 实际是**作用类型**（101=回体力 / 201=延寿 / 501=毒） |
| 以为 `lastItemPillEffectType` 判"品质" | 它判的是**作用类型**；判品质的是 `lastItemLevelType` |
| 在剧情/选项里直接写 `reviveUnit_3` | **必须加 `battleFunction_` 前缀**（`battleFunction_reviveUnit_3`），否则命令不被识别 |
| 以为 `function` 和 `openFunction` 是同义 | 表头写得很清楚：`openFunction`=**打开作用**、`function`=**关闭作用**。放错 = 执行时机错一整拍 |

---

## 八、参考索引（不用重新查）

| 内容 | 位置 |
|---|---|
| 官方剧情命令全表（311 行） | `配置对照/配置对照表.xlsx` → `剧情条件及影响` |
| 官方条件全表（436 行） | 同上 → `条件说明表` |
| 官方副本作用功能（112 行） | 同上 → `副本作用功能` |
| 官方效果说明（339 行） | 同上 → `效果说明表` |
| 原生配置全量 json（761 张） | `配置（只读）Json格式/` |
| 丹药表 | `ItemPill.json`（1456 行，`effectType` 判作用） |
| 道具基础表 | `ItemProps.json`（3295 行，`name` 是 LocalText key） |
| 我们自己的表 | `F:\mod\ModProject_Jgmg5L\ModProject\ModExcel\` |

**已定位的原生样例（直接可查）**：

| 用途 | id |
|---|---|
| 单位死亡触发 + 复活战斗位 3 | `DungeonTrigger 82` |
| 救助 NPC 触发（含 rescue 标记） | `DungeonTrigger 132` |
| 喂丹药完整范本（选项 + 分流） | `DramaDialogue 630024` → 分支 `630025`/`630031` |
| 复活选项（简单版） | `DramaOptions 6400121`（`revive`） |
| 原生救助窗结构 | `DramaDialogue 982142` |

---

## 九、下一步

**待用户确认后，从第 0 步开始**（验证 `reviveUnit_3`）。

第 0 步是纯配置改动（`190` 的 `openFunction` 追加一个命令）、可秒回退、
且与"要不要做选项"完全独立 —— 即使整个选项方案最后放弃，
「让队友站起来」这个能力本身也有价值（可以只做一个「舍身相救」的单选项）。
