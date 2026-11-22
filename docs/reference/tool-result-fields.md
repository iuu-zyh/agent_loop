# 工具结果字段清单（渲染层读什么）

> 查表用。**分工原则与边界铁律**见 [`../features/tool-result-contract.md`](../features/tool-result-contract.md)。
> 这里只列每个工具 `data` 里有什么、渲染器怎么组句。**实现以代码为准**：
> C# 侧 `csharp/tools/*Tool.cs` 组装 data，Python 侧 `tools/text_render.py` 组句。

## 1. inspect_unit — 查询人物档案

**参数**：`unit_id`（**精确指人，优先**；来自 relationships 或 search_units）｜`target`（人物真名，没有 unit_id 时才用）｜`classes`（多选：brief/stats/abilities/inventory/relationships/logs，默认 brief）｜`log_filter`（important/regular/all，默认 all）｜`log_page`（每页 5 条，**第 1 页 = 最新**）

**C# 输出**（`data`；按 `classes` 出现的块才有）

| 块 | 字段 |
|---|---|
| 顶层 | `name` |
| brief | `sex` `realm` `sect` `sect_id` `race` `beauty` `beauty_label` `reputation` `reputation_label` `hobby[]` `title`(道号) `personality{inner,outer[]}` `relation` `intim` `same_grid` `ref` `point{x,y}` `luck{born[],added[],all[]}`（项=`{id,name,desc}`） |
| stats | `power` `defense` `hp` `hp_max` `energy` `mood` `beauty` `reputation` `talent` `heart` `heart_state?` |
| abilities | `skill_left` `skill_right` `step` `ultimate`（各 `{type,id,name,desc?}`）+ `abilitys[]`（心法）。**`desc` = 技能说明**（面板「技能说明」同源：`UIMartialInfoTool.GetDesc` 取模板 → `ResolveSkillSigils` 替换 `&数值&`/`$本地化$` 占位符 → 剥 `<y>…</y>` 配色标签）；取不到时**不落该键**（缺失 ≠ 没有说明） |
| inventory | `props[{cat, items[{name,count,worth,total}], misc{kinds,pieces,worth}}]` `equips[{name,worth}]` `money` |
| relationships | `parent` `children` `brother` `parent_back` `children_back` `brother_back` `lover` `master` `student` `married` `friend_units` `enemy_units`：**每人 `{name, unit_id}`**（`married` 单个对象，无则 `""`）；行人情 `human_value`。★`unit_id` 必须带出去★——模型靠它精确指人，否则只能拿名字回去猜（全图重名是常态，见附录 D.1 重名事故） |
| logs | `filter` `page` `items[{month,text,tier}]` `total` `has_more`；`tier`∈`important`/`regular` = 条目来自哪个桶（两桶是独立的流，`filter=all` 取并集后只有这个字段能区分） |

**关系三项的口径（09-21 修正，勿再写死玩家）**

`relation`/`intim`/`same_grid` 是**相对量**：语义 = 「**被查者** 对 **发起者**」，取数处用
`UnitSnapshot.RelationTo(被查者, 发起者)`（发起者 = harness 注入的 `initiator`，即当前对话 NPC）。
`ref` 随数据回传那个发起者的**真名**，渲染层据此措辞——**不许**在渲染层写「玩家」二字。

三条边界：① 发起者解析不到（老调用方/无 initiator 的探针）→ 回落 `Build` 的对玩家口径且**不给 `ref`**，
渲染层缺省写「玩家」，形状与改动前逐字一致；② **自查**（被查者就是发起者）→ 三项**都不给**，
自己对自己的关系簿没有意义，宁缺不假；③ 查玩家本人时若沿用 `Build` 写死的对手方，就退化成
「玩家的关系簿查玩家自己」→ 恒 陌生/好感0/同处一地，与 L1 玩家段写着的真实关系当面打架
（真机：模型据此否掉了道侣之谊，并把「同处一地」当成事实）。

`UnitSnapshot.Build` 里那份对玩家的三项**照旧保留**——L1 的 `raw.relations.player` 吃的是它，
那条路本来就是「本 NPC 对玩家」，写死玩家是对的。

**Python 渲染规则**

- 简档行：`名，性别，境界/宗门/种族；道号：X；性格：内X·外Y；魅力<档位>（数值）；声名<档位>（数值）；爱好：…；与{ref|玩家}同处一地/异地，关系X，好感N；坐标(x,y)`
- 气运：`气运：先天A（desc）、B；后天C`
- 属性：`属性：气血h/hp，攻击…，防御…，精力…，心情…，资质…，魅力…，声望…，道心X（state）`
- 关系簿：`关系簿：父母：益婉容(Xs6JDI)，道侣：唐炎(BOlQu6)，好友：唐乐咏(yqVSYV)，人情0` —— **`名(unit_id)`**，模型直接把括号里的 id 填进 `inspect_unit(unit_id=…)`
- 找人结果：`赵勤（女，关系陌生，好感0，结晶后期/散修，永宁州，unit_id=293n6V）`
- **同名歧义**（`data.ambiguous` 存在且 `matched>1`）→ 正文**最前面**加一行：
  `⚠️同名提醒：「益婉容」全图有 3 人同名，以下是 unit_id=Xs6JDI（登仙境，散修，声望3129，坐标(119,51)）这一位的档案，**未必是你要找的人**。其余同名者：益婉容(AbC123)、益婉容(Zz9Q8w)。要查其中某一位，用 inspect_unit(unit_id="…") 精确指定。`
- 功法（**逐槽一行**，技能说明动辄上百字，挤一行没法读）：`功法：` 换行后每槽一行 `· 灵技「名」(id=…)——技能说明`
- 背包：`背包（约N件）：` 换行 + 每类一行 `类别：名×数（单价w，小计t）、…`；`身着：名（值w）`；`灵石：N`
- 关系簿：`关系簿：道侣：X，师尊：A、B，好友：…，人情N`（12 容器全列）
- 经历：`经历（重要｜第2页，共7条，还有更早的）：` 换行 + `（N年M月）文本`。**条目按新→旧排列（第 1 页 = 最新）**——游戏库里是旧→新，取数处 `UnitSnapshot.LogsArr` 统一反转；`RecentTexts` 取头部即"最近几条"喂 L1「近况」段。`filter=all` 且载荷带 `tier` 时，重要件加 `★` 前缀并在表头补 `；★=重要`（单选层不加，同质无需区分）

**示例**（群聊里 **云含** 查 **姜萌** → 关系三项是「姜萌 对 云含」，`ref` 点名那个「对谁」）

```json
{"success":true,"data":{"name":"姜萌","sex":"女","realm":"筑基境","sect":"散修","race":"人族",
 "title":"玉罗刹","beauty":375,"beauty_label":"仙姿","reputation":12,"reputation_label":"初出茅庐",
 "hobby":["饰品","琴"],"personality":{"inner":"狂邪","outer":["护短","名声"]},
 "relation":"好友","intim":60,"same_grid":true,"ref":"云含","point":{"x":31,"y":78},
 "inventory":{"props":[{"cat":"丹符","items":[{"name":"六品玉琼丹","count":14,"worth":48,"total":672}]}],
   "equips":[{"name":"青纹袍","worth":180}],"money":381}}}
```
```
姜萌，女，筑基境/散修/人族；道号：玉罗刹；性格：内狂邪·外护短·外名声；魅力仙姿（375）；声名初出茅庐（12）；爱好：饰品、琴；与云含同处一地，关系好友，好感60；坐标(31,78)
背包（约14件）：
丹符：六品玉琼丹×14（单价48，小计672）
身着：青纹袍（值180）
灵石：381
```

---

## 2. search_units — 全局找人

**参数**：`filters{keyword,relation,realm,sect,region,race,sex}`（有值才过滤）

**C# 输出**：`{filters, items[{name,sex?,relation,intim,realm?,sect?,region?}], total}`

> ★ 这里的关系两项**仍是「被查者 对 玩家」**（`SearchUnits` 里写死 `g.world.playerUnit`），没有跟着
> inspect 一起改成"对发起者"——因为结果按 `intim` 降序取前 N，换对手方等于换排序语义（"谁跟玩家最亲"
> → "谁跟这个 NPC 最亲"），是一次产品决策而不是一处同步修补。要改请单独定（09-21 记）。
- `items` 按好感降序，**最多 10 条**；`total` 为命中总数（>10 时只给前 10）
- `sex` 是 09-13 补的：`filters` 早就支持按性别筛人，结果行却从不回性别 —— 筛完「找女修」拿到一串名字仍不知谁是谁

**渲染**：
```
找人结果（命中12人，按好感降序，最多列前10）｜筛选：sect=北斗剑宗：
林婉清（女，关系好友，好感72，金丹后期/北斗剑宗，白源区）
张三（关系相识，好感40）
```

---

## 3. query_world — 世界全局

**参数**：`topic`（events/rankings/places/sects/region）｜`board`（rankings 用）｜`top`（rankings 用）

### 3.1 topic=events（天下月志）
- **C#**：`{events:[{month,text}], count}`，按月倒序取最近 N 条（N 由 `count` 决定，**默认 12**，上限 120 仅防呆）
- **渲染**：全部条目 + 计数头
```
近期天下大事（24条，时间倒序）：
（3月）魔修袭扰白源市集，散修死伤惨重
（4月）北斗剑宗开山门收徒
```
- ✅ **已实施（09-10）**：`count` 参数（默认 12 条 ≈ 最近一年，上限 120）——见 §9.2

### 3.2 topic=rankings（天下排行榜）
- **C#**：`{board, items:[{name,score,realm?,sect?}], total}`；`items` 为前 `top` 名（**默认 10，上限 200 仅防呆——条数由模型决定**），`total` 为全图参与人数
- **渲染**：榜名 + 榜内总人数 + 全部条目（名次/名/分数/境界/宗门）
```
战力榜（列出10人，榜内共312人）：
1. 林婉清 8900（金丹后期/北斗剑宗）
2. 玄阳子 8100（金丹/北斗剑宗）
```
- ✅ **已实施（09-10）**：`top` 上限 20 → 200——见 §9.1

### 3.3 topic=places（可去地点）
- **C#**：`{places:[{name,cat,region,point{x,y}}], total}`（全图城镇+宗门，无上限）
- **渲染**：全列出（不截断）+ 分类计数 + 坐标
```
可去地点共50处（城镇26、宗门24）：
[城镇] 白源区·青城(12,40)
[宗门] 永宁州·丹阳谷永宁州分舵(33,150)
```

### 3.4 topic=sects（宗门概览）
- **C#**：`{sects:[…], total, scope?, name?, region?, family_total?, family_others[]?, note?}`，每条：
  `name` `region` `point{x,y}` `sect_id?` `main_name?` `branch_name?` `name_origin?` `name_part1[]?` `name_part2[]?`
  `is_hold` `hold_by?` `is_top` `top_school?` `sub_schools[]?` `type?` `stand?` `slogans[{slogan,desc?}]?` `fate?`
  `member_count?` `reputation?` `leader?` `enemy?`
- **查询粒度**（10-29）：`name` 空 → 按 `region` 过滤；`name` 给、`region` 空 → **族系**（主宗+全部分舵）；
  两者同给 → **只回该州内的那支**（不跨州展开）；`name` 是分舵全名 → **只回那一条**。
  顶层带 `scope` ∈ `all|region|family|self|state`；被收窄时带 `family_total` / `family_others` / `note`。
- **detail 分层**（10-29，仅 sects）：
  - `info` 追加 `bas_types[]`（12 项功法偏好，中文）、`member{total,big_elders,elders,inherit,inner,outer}`、
    `capacity{…}`（名额上限）、`money` `faith_money` `power` `is_small?` `honor{score,rank}?` `intim[{name,value}]?`
  - `people` 追加 `people{main,big_elders[{…,depts[]}],elders[{dept,name,realm,inherit[],inner_count,outer_count,vacant?}]}`；
    `postData` 不可读时降级为 `elders_flat[]` / `inherit_flat[]` 并给 `people_note`
  - `all` = 两者合并；不给 `detail` 则与旧版逐字节一致
- **渲染**：一条一行（段间 `｜`），detail 段追加缩进子行
```
宗门概览（共2个）：
· 迷途荒漠·天钧观（观，立场值1）｜名号：主名天钧，支名观，源名天钧观，名词组1天钧/观｜层级：主宗，下辖2宗（赤星宫、九幻宫）｜状况：未被占据｜宗主韶和顺，弟子460人，声望10000，敌对天魔宗｜宗旨：顺天应人（以天为尊，以观为道）｜气运：天钧气运｜坐标(210,88)｜id=SCH_0001
· 永宁州·丹阳谷永宁州分舵（谷，立场值0）｜名号：主名丹阳，支名谷｜层级：分宗，隶属丹阳谷｜状况：被占据（持有方天魔宗）｜宗主濮阳坻，弟子69人，声望711｜坐标(33,150)
```

`detail=all` 且 `name`+`region` 精确到一支时：

```
「丹阳谷」永宁州内的宗门（1处）｜已按 region=「永宁州」收窄到 1 处；同族另有 2 处
· 永宁州·丹阳谷永宁州分舵｜名号：主名丹阳，支名谷｜层级：分宗，隶属丹阳谷｜状况：被占据（持有方天魔宗）｜宗主濮阳坻，弟子69人，声望711，敌对天魔宗｜坐标(33,150)
  功法偏好：剑法资质、枪法资质、火灵根、水灵根
  人数：大长老2/2、长老5/5、真传2/10、内门12/12、外门25/25，共69人
  经济：灵石12000、信仰3500、战力8600
  战绩：宗门大比 3分，第17名
  交情：丹阳谷25、天魔宗-40
  宗主：濮阳坻（金丹后期）
  大长老（2）：
    · 玄阳子（元婴初期）｜辖招贤堂、巡查堂
    · 白芷（金丹后期）
  长老（2）：
    · 招贤堂 林清（金丹中期）｜真传：苏小满（筑基后期）；内门12人；外门25人
    · 巡查堂：空缺
```

### 3.5 topic=region（未实装）
- **C#**：`{topic:"region", note:"…暂不返回"}` → 无渲染器 → **回退 JSON**

---

## 4. social_relation — 关系与好感

**参数**：`op`（add_intim/reduce_intim/jie_yuan/marry/bai_shi/shou_tu/jie_yi/ren_yi_fu_mu/jie_chu_*/divorce…）｜`value`｜`ask_text`｜`initiator`

**C# 输出（分支）**

| 形态 | 字段 |
|---|---|
| add_intim / reduce_intim | `op` `target` `requested` `actual_delta` `intim_before` `current_intim` |
| 缔结类（确认窗，玩家同意后） | `op` `target` `relation`（中文）`verified` `precheck` `create_ret` `relation_before` `relation_after` `diag{npc_realm,player_realm,npc_students,npc_masters,player_students,player_masters}` |
| 缔结类（**预检就被拒**，未落刀、不弹窗） | 同上，但 `create_ret = -1`（-1 = 没落刀）、`verified:false` |
| 缔结类（直写兜底：文案构建全失败） | `op` `target` `relation` `verified` `create_ret` `note` |
| 解除类（确认窗） | `op` `target` `relation` `precheck` `relation_before` `relation_after` `create_ret` `state_at_return` `bw_err?` `readback_still_related` |
| 结婚（字段直写，无引擎动作） | `op` `target` `relation:"夫妻"` `relation_before` `relation_after` `diag`（无 precheck/create_ret） |

> **五类缔结共用同一处**（`jie_yuan`/`bai_shi`/`shou_tu`/`ren_yi_fu_mu`/`jie_yi` → `StartRelationAction`；
> NPC↔NPC 免窗 → `DirectEstablish`），所以诊断是**整族**覆盖的，不是只给收徒做的。
>
> **引擎裁决码（`UnitActionRelationSet.IsCreate(bool)`）：0 = 同意；非 0 = 拒绝原因码。**
> 判据来自 `GameAssembly.dll` 反汇编：`WorldUnitBase.CreateAction` @0x32B8D40 是
> `code = IsCreate(isTip); if (code != 0) return code;` 然后才 `Create()` —— 所以**只有 0 会执行
> `OnCreate`**。码表（权威注释在 `csharp/ToolExecutor.cs` 关系段顶部，渲染措辞在
> `tools/text_render.py` 的 `_RELATION_ENGINE_CODE`，两处必须同步）：
>
> | 码 | 含义 |
> |---|---|
> | 0 | 引擎同意（可创建） |
> | 2 | 目标是自己 |
> | 3 | **两人之间已经有一条关系**（一对人只记一条 ⇒ 缔结类最常撞这条） |
> | 4 | 认义父母另有前置未满足 |
> | 5 | 同性不能结为夫妻/道侣 |
> | 6 | 异性不能结义 |
> | 7 | 对方或自己已有配偶 |
> | 8 | 结婚前必须先结为道侣 |
> | 9 | `type=None` 的解除路径提示码 |
>
> **一对人在引擎里只有一条关系**：`RelationData.GetRelation(对方)` @0x3CF2390 是"先命中先返回的
> 单个枚举"（优先级 父母 > 子女 > 私生 > 兄弟 > 义父母 > 义子女 > 义兄弟 > **夫妻 > 道侣 > 师父 > 徒弟**），
> 且 `IsCreate` 对**除结婚外**的所有类型都要求 `GetRelation(对方) == None`；真落刀时 `OnCreate`
> 会先 `ClearRelation(unit,toUnit)`（双向）再写新关系 ⇒ **能落刀 = 旧关系必然被删（覆盖，不是叠加）**。
> 所以"道侣 + 师徒共存"在本引擎不存在；本工具**不做覆盖**，引擎拒绝就如实报"未能结下"。
>
> **`precheck`（`IsCreateAction`，只问不落刀）**：与 `CreateAction` 同源（`IsCreate`），
> **必须用一次性实例**——`UnitActionBase.IsCreate` @0x35083B0 会把该实例的 `isCreateCall` 置 true，
> 而 `CreateAction` 见到这个标记会**跳过全部校验直接 `Create()`**。09-17 就是用同一个实例先问后砍，
> 把"引擎拒绝（码 3：两人已是夫妻）"撬成了强行落刀、`ClearRelation` 拆掉了用户原有的夫妻关系。
> 铁律：**预检一次性实例、落刀只调一次并读返回码**；`precheck > 0` ⇒ 弹窗之前就收口（`create_ret = -1`，
> 未落刀）；`precheck = -2`（问不出来）⇒ 照旧落刀，让落刀那次自己判。
> **解除类是例外：引擎码只报不拦**（下面还有字段直写兜底，解除是单方面行为），
> 渲染层只附一句"（原生解除动作没跑起来：引擎码 N，本次走字段直写）"。
> 渲染层据此写"未能结下"并列出这些事实；**没有 `create_ret` 的旧载荷保留旧文案**（两侧升级不同步时不回归）。

**渲染**
```
姜萌对你的好感提升了3点（请求3点，59→62）
你与姜萌结为道侣。
你与姜萌结为夫妻。（引擎把原有的「道侣」关系换成了「夫妻」—— 引擎里一对人只记一条关系）
你与姜萌的「师徒」未能结下：游戏引擎在动手之前就判定这个动作建不起来（引擎码 3：两人之间已经有一条关系
（引擎里一对人只记一条，要结新的必须先解除旧的）），未落刀，关系簿未改动；你与姜萌的关系簿里已是夫妻；
双方境界：姜萌炼气境中期／你炼气境初期。以游戏弹出的提示为准，请如实告诉玩家未能结下。
你同意了与姜萌解除夫妻关系（原生解除动作没跑起来：引擎码 2，本次走字段直写）；读回确认关系已解除
你与姜萌结为结义（C# 未返回状态校验）。        ← 直写兜底形态
```

---

## 5. movement — 位移与碰面

**参数**：`op`（summon/teleport/travel）｜`destination`（travel）｜`region`（travel 消歧）

**C# 输出**

| 情形 | 字段 |
|---|---|
| summon/teleport 成功 | `op` `target` `moved:true` |
| summon/teleport 同格 | `op` `target` |
| travel 出发 | `op` `target` `destination` `cat` `region` `point{x,y}` `moved:true` |
| travel 已在地 | `op` `target` `destination` `region` `already_there:true` |

**渲染**
```
你已被召唤到姜萌身边（你移动到姜萌所在处）。
你与姜萌已在同一处，无需传送。
姜萌已动身前往青城（白源区）[城镇]，并在此地等候。（目的地坐标3,4）
```

---

## 6. world_ai_action — 对玩家发起行动

**参数**：`op`（attack/spar/shuang_xiu/lun_dao/yao_yue/chuan_gong/**join_sect**/**leave_sect**）｜`skill`（chuan_gong）｜
`sect`/`region`/`post`/`master`/`reason`（仅宗门两条，见 §6.1）

**C# 输出**

| 形态 | 字段 |
|---|---|
| attack / spar | `op` `target` |
| 论道/双修（挂起结局） | `op` `target` `completed:true/false` |
| 邀约（挂起结局） | `op` `target` `accepted:true/false` `upset?` `invite_text?` |
| 传功（挂起结局） | `op` `target` `skill` `accepted` `upset?` |
| AutoConfirm 立即发起 | `op` `target`(+`skill`) `pending:true` |

> 挂起类工具经 DramaGate 憋住 response，玩家在原生界面操作完才补发上面的结局 data。

**`invite_text`（仅 `accepted:true` 时可能出现；抓不到就没有该字段）**

邀约接受后游戏会弹**第二层原生剧情**，文本是 `RoleLogLocal keyID=drama_dialogue81012`（6 个变体，
形如「我先前在**新达镇**附近发现了一处幽静之地…我会在新达镇附近等你三个月。」）——**约定地点就在这句话里**，
过去只回 `accepted` 把地点丢了。C# `DramaTextCapture` 把这句话抓下来随结果透出。

**数据源只有一个**：`UIDramaBase.GetDialogueText` 的**返回值**（hook 见 `UI/DramaTextHook.cs`）——
它是游戏填好的**成品句**，零推断。真机实测排除的两条路（勿回退）：

- `DramaTool.lastOpenDramaDialogueText` 是**未替换的模板**（`{0}` 还在），不可直接用；
- `DramaTool.lastOpenDramaDialogueValues`（`Dictionary<int, Il2CppStringArray>`）**不含 key 0**
  （实测 `values0=<no-key-0>`），据此"自己填 `{0}`"的路已证伪删除。

配对：`BeginInvite(npc)` 武装 → 每次 `InitData` 覆盖式记录（取最后一条 = 第二层，按参与者 unitID
比对发起 NPC 过滤）→ `OnEnd` 取走。**失败即静默**（字段缺失 ≠ 否定），渲染层退回旧文案，不许编地点。

**渲染**
```
你应下了姜萌的切磋，两人动起手来。
你说：厉害，我输了。
姜萌说：看来是我赢了！                              ← 切磋：战后原话带归属，胜负由模型读（我们不解析关键词）
你应下了姜萌的切磋。打完了，但没拿到战后的话，谁胜谁负未知 —— 别自行推断。   ← 打完没抓到词
你与姜萌的论道已结束。
你拒绝了姜萌的邀约（对方似有不悦，好感或受影响）。
姜萌已向你发出邀约（等待你在原版剧情中回应）。
姜萌已向你发起论道，等待完成（C# 未返回结局）。        ← 字段缺失形态（不得说成"未完成"）
你接受了云含的邀约。云含说：「我先前在新达镇附近发现了一处幽静之地，不如我们到那边去吧。我会在新达镇附近等你三个月。」
（原版剧情里玩家已看过这句话，勿复述，接着往下说即可。）
你接受了云含的邀约（将按剧情赴约）。                    ← 没抓到原句（改动前文案，兜底）
```

### 6.1 join_sect / leave_sect（10-29，宗门人事）

**参数**：`sect`（join 必填）｜`region`（同名分宗消歧）｜`post`（`out`外门/`in`内门/`inherit`真传）｜
`master`（仅 inherit，长老真名）｜`reason`（**两条都必填**，NPC 自己的动机）

**C# 输出**（四种形态）

| 形态 | 字段 |
|---|---|
| 没定身份（反问） | `op` `ask:true` `sect` `roster{out,in,inherit,elders[{dept,name?,vacant?,inherit}]}` `question` `reason` |
| 已落地 | `op` `sect` `post` `joined:true` `now_sect` `now_post?` `master_dept?` `reason` `who?` |
| 已受理未落地 | `op` `sect` `post` `joined:false` `pending:true` `note` `reason` |
| 退宗 | `op` `from` `post` `left:true/false`（或 `pending:true`）`now_sect` `reason` `who?` |

> 失败一律走错误帧（人话）：已在别的宗门（先退宗）、目标宗门查无、`reason` 缺失、
> 引擎拒绝（返回码 1=通用闸 / 5=该州境界区间或交情（推测，文案已标"常见原因"）/ 12=特殊剧情角色）。

**渲染**
```
（系统：这一调没有动手 —— 他想进「丹阳谷」，但还没定以什么身份。要先问清玩家：是入外门、入内门，还是拜在哪一堂的长老门下做真传弟子？）
该宗在任：外门12人、内门12人、真传2人
可选师父：招贤堂·林清（门下真传1人）、巡查堂（空缺）
你已入「丹阳谷」，身份是真传弟子，拜在招贤堂长老门下（现属丹阳谷永宁州分舵）。（你给的理由：敬林清为人）
你入「丹阳谷」的事引擎已受理，名册下一拍才更新——别当没成。
你已退出「丹阳谷永宁州分舵」，如今是散修。（你给的理由：看不惯宗门盘剥乡里）
```

**边界**：`target` 不参与（actor 恒为当前对话 NPC）；名额上限**不显示也不预拦**（两处候选数字语义未定，
`GetStudentMax` 实为"当前人数"）；宗主/大长老不许退（一退空位，要牵继承流程）；真传不模拟"师父肯不肯收"。

---

## 7. economy_item — NPC 赠送（发起方=NPC，接收方=玩家）

**参数**：`items[{item_name,count}]`｜`via`（direct/letter）｜`initiator`

**C# 输出**：`op:"give_item"` `target`（=玩家名）`items[{item,requested,actual,mode,error?,from?}]` `via`
`accepted` `accepted_count?`（=receiveProps 计数）`refused_count?` `delivered_all?` `same_grid?`
（纯灵石：同形，`items[].mode="money"`；失败分支为 `{success:false,error,data:{items}}`）

> **09-17：一件走原生、多件走自制窗 + 直写。** 原生 `UnitActionRoleGive` **一次只处理一件**
> （真机 `giveProps=4 → receiveProps=1 refuseProps=0`；签名 `propsData` 是单数；配表剧情 21701
> 文案即「我这里有一个{0}准备赠于你」+「我收下了／我不需要这个」；没送出去的由原生
> `FeedbackAllProps()` 退回发起方）。所以 ≥2 件改走 `ShowDramaService.ShowConfirm`
> （economy 段，正文列全清单）→ 玩家点一次「我收下了」→ 逐件 `AddProps` **全部到账**，
> 并复刻原生副作用（好感 `AddIntim`，配表 `RoleGive.closeParaA=15`；角色日志
> `eventLogRoleGive21311/21312`）。`delivered_all:true` 标记这条"全部到账"的路。
> 两条路都留了 `[Give] 好感探针` 日志行 —— 原生 `OnEnd` 的好感公式在 native 体内看不到，
> 真机送一次即可核对该常量（**不猜**）。

**渲染**（明确"谁赠谁收"；逐项请求/实得；未达成项带原因；部分拒收；**按引擎真值报**）
```
姜萌赠予唐炎：唐炎收下了部分赠礼（六品玉琼丹×14（请求20，实得14）），拒收了其余8件；唐炎收下了灵石×50；未送达：九转金丹（请求1，未达成：背包中没有）（当面送达）
姜萌赠予唐炎：唐炎收下了赠礼（六品化瘀丹×10）；未送达：六品蓄力丹×3、六品玉琼丹×3（引擎一次只处理一件，其余已退回发起方）（当面送达）
```
（第二行是 09-17 之前的真实形态：4 件被旧文案写成全部收下 —— `accepted_count` 才是引擎真值。）

---

## 8. trade — 买卖（双方都由模型指定）

**参数**：`seller`（卖方真名）｜`buyer`（买方真名）｜`item`（道具中文名）｜`count`（默认 1）｜`price`（**整笔总价**，0=无偿让与）

**实质**：四条字段变更 —— 卖方背包 −道具 / 买方背包 +道具；买方灵石 −价 / 卖方灵石 +价。
**不驱动游戏原生交易系统**（那要开原版交易 UI、两边都进交易态），走引擎自己的
`DataProps.DelProps(soleID,n)` / `AddProps(PropsData)` 拆栈入栈 + `WorldUnitData.CostPropItem(10001,…)` / `RewardPropMoney(…)` 结算灵石。

**C# 输出**：`op:"trade"` `seller` `buyer` `item` `count` `price`
`item_moved`（落刀后核对：买方该道具确实 +count）
`buyer_item_before/after` `seller_money_before/after` `buyer_money_before/after` `same_grid`

**渲染**
```
云含把「青木箭」×2卖给了缪嘉歆，价 3000 灵石（云含灵石 0→3000，缪嘉歆灵石 5000→2000），当面交割。
云含把「丹药」×1卖给了缪嘉歆（不取分文），异地交割。
```

**三条硬约束**（源码契约测试 `tests/test_trade_contract.py`）

| 约束 | 为什么 |
|---|---|
| 拆栈只用 `DelProps`，**绝不写 `propsCount`** | `propsCount = n` 不是"取出 n 个"而是"把整栈改成 n 个"，余量凭空消失且失败时找不回 |
| 数量**整笔满足**（`got < count` → 回滚 + 失败） | 买卖是定价交易，少给货却收全款不成立；与 `economy_item` 赠送的"有多少送多少"**语义相反** |
| 落刀顺序 **拆栈 → 灵石 → 入包** | 前两步失败时道具还在手上，`RollbackGive` 能无损退回卖方；先入包则失败时得从买方"抠回来"，那时栈可能已被合并 |

预校验（双方存在且不同人／卖方持货足／买方灵石够）**全只读且先于弹窗** —— 避免"弹了窗、
玩家点了成交、才发现做不了"。确认窗复用 `economy_item` 段 `ModIds.DramaEconomyItemBase`
（DramaGate 全局同时只允许一个挂起窗，壳条目正文/选项全部运行时覆盖，故 ModExcel 8 段不动）。

**立绘位**：`left` = 屏幕左位 = **玩家**，`right` = **对方**（约定见 `ShowDramaService` 文档）。
买卖双方都不固定是玩家，故按「谁是玩家」推导：玩家买 → 左买右卖；玩家卖 → 左卖右买；
NPC↔NPC → 左卖右买。★09-13 事故★：初版直接传 `(seller, buyer)`，卖家是 NPC、买家是玩家时
右位落成玩家，实机表现为**两个自己的立绘与名字**（同一坑 09-12 在自主互动窗踩过一次）。

---

## 9. item_acquire — 偷窃 / 讨要（目标=玩家）

**参数**：`op`（steal_item/ask_for）｜`item_name`｜`count`（ask_for）｜`initiator`

**C# 输出**

| 形态 | 字段 |
|---|---|
| 偷窃（立即返回） | `op` `target` `item_name` `stack_count` |
| 讨要（挂起结局） | `op` `target` `item_name` `count` `accepted` `upset?` |
| 讨要（AutoConfirm 立即） | `op` `target` `item_name` `count` `pending:true` |

**渲染**
```
姜萌偷取了你的「风玄丝」，整栈共16个。
你把「六品玉琼丹」×6交给了姜萌。
你拒绝了姜萌的讨要（「六品玉琼丹」×6），对方似有不悦（好感或受影响）。
姜萌向你讨要「六品玉琼丹」×6（等待你在原版剧情中回应）。
```

---
