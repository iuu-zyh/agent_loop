# 工具结果契约（C# 原始数据 → Python 渲染 text）

> 版本：2026-09-10（对应部署 `EF8B747B`）
> 适用：`csharp/ToolExecutor.cs`（数据层） ↔ `tools/text_render.py`（渲染层）

## 0. 分工与原则

```
C# ToolExecutor.Execute(name,args) → {success, data}     ← 只给原始数据，不组装叙述
        │  （WsClient 转发 / DramaGate 挂起后补发）
        ▼
Python dialogue_agent._execute_tool_calls
        text = text_render.render(name, args, res) or json.dumps(res)
        │  （渲染器缺失 / 形态不认识 / 抛异常 → 回退全量 JSON）
        ├──→ LLM 的 tool_result
        └──→ UI 行动记录行（同一字符串）
```

**原则（用户定调）**

1. **忠实优先于简短**：data 里的语义字段一律呈现——不截断列表、不隐藏失败项、不省略数值；
   省 token 只靠"去掉 JSON 结构包装"。
2. **字段缺失 ≠ 否定**：`completed`/`accepted` 缺失（非挂起形态）不得说成"未完成/被拒绝"。
3. **本地化归 C#**：conf 查表中文字段（性格/魅力档位/关系中文/区域名）在 C# 完成；Python 只组句。
4. **人称**：「你」=玩家；NPC 一律用真名。列表类结果换行分条。
5. **边界铁律（2026-09-10 用户定调）：渲染层禁止任何截断/上限/丢弃——N 条进必须 N 条出。**
   "取多少/留几条"全部由 C# 决定：`query_world.count`（events 条数）、`query_world.top`（榜单名次）、
   `search_units` 前 10、`inspect_unit.log_page`（经历每页 5 条）、`movement/…` 等工具的语义校验。
   Python 不替 C# 做取舍；**防回退哨兵** = `tests/test_text_render.py::test_no_truncation_anywhere`
   （events/places/sects/logs/search/inventory/关系簿/rankings 各喂 N 条断言 N 条出）。

### 0.1 机器字段例外清单（渲染层省略，data 中完整保留）

省略只影响 text 呈现，**C# 给的数据没有任何丢失**（模型看不到而已）。登记在此以便审查：

| 工具 | 省略字段 | 理由 |
|---|---|---|
| inspect_unit | `sect_id`、`stats.heart_confID`、`luck.all` | 内部 id / 冗余（`luck.all` = born+added 去重合并） |
| economy_item | `items[].from`（"npc->player"） | 方向已由标题「X 赠予 Y」表达 |
| social_relation | `create_ret`、`state_at_return` | 引擎诊断值（`bw_err` 有异常才渲染） |

> 如后续需要这些字段进 text（例如要让模型用 `sect_id` 交叉引用宗门），在此清单里划掉并补渲染器即可。

---

## 1. inspect_unit — 查询人物档案

**参数**：`unit_id`（**精确指人，优先**；来自 relationships 或 search_units）｜`target`（人物真名，没有 unit_id 时才用）｜`classes`（多选：brief/stats/abilities/inventory/relationships/logs，默认 brief）｜`log_filter`（important/regular/all，默认 all）｜`log_page`（每页 5 条，**第 1 页 = 最新**）

**C# 输出**（`data`；按 `classes` 出现的块才有）

| 块 | 字段 |
|---|---|
| 顶层 | `name` |
| brief | `sex` `realm` `sect` `sect_id` `race` `beauty` `beauty_label` `reputation` `reputation_label` `hobby[]` `title`(道号) `personality{inner,outer[]}` `relation` `intim` `same_grid` `point{x,y}` `luck{born[],added[],all[]}`（项=`{id,name,desc}`） |
| stats | `power` `defense` `hp` `hp_max` `energy` `mood` `beauty` `reputation` `talent` `heart` `heart_state?` |
| abilities | `skill_left` `skill_right` `step` `ultimate`（各 `{type,id,name,desc?}`）+ `abilitys[]`（心法）。**`desc` = 技能说明**（面板「技能说明」同源：`UIMartialInfoTool.GetDesc` 取模板 → `ResolveSkillSigils` 替换 `&数值&`/`$本地化$` 占位符 → 剥 `<y>…</y>` 配色标签）；取不到时**不落该键**（缺失 ≠ 没有说明） |
| inventory | `props[{cat, items[{name,count,worth,total}], misc{kinds,pieces,worth}}]` `equips[{name,worth}]` `money` |
| relationships | `parent` `children` `brother` `parent_back` `children_back` `brother_back` `lover` `master` `student` `married` `friend_units` `enemy_units`：**每人 `{name, unit_id}`**（`married` 单个对象，无则 `""`）；行人情 `human_value`。★`unit_id` 必须带出去★——模型靠它精确指人，否则只能拿名字回去猜（全图重名是常态，见附录 D.1 重名事故） |
| logs | `filter` `page` `items[{month,text,tier}]` `total` `has_more`；`tier`∈`important`/`regular` = 条目来自哪个桶（两桶是独立的流，`filter=all` 取并集后只有这个字段能区分） |

**Python 渲染规则**

- 简档行：`名，性别，境界/宗门/种族；道号：X；性格：内X·外Y；魅力<档位>（数值）；声名<档位>（数值）；爱好：…；与玩家同处一地/异地，关系X，好感N；坐标(x,y)`
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

**示例**

```json
{"success":true,"data":{"name":"姜萌","sex":"女","realm":"筑基境","sect":"散修","race":"人族",
 "title":"玉罗刹","beauty":375,"beauty_label":"仙姿","reputation":12,"reputation_label":"初出茅庐",
 "hobby":["饰品","琴"],"personality":{"inner":"狂邪","outer":["护短","名声"]},
 "relation":"道侣","intim":200,"same_grid":true,"point":{"x":31,"y":78},
 "inventory":{"props":[{"cat":"丹符","items":[{"name":"六品玉琼丹","count":14,"worth":48,"total":672}]}],
   "equips":[{"name":"青纹袍","worth":180}],"money":381}}}
```
```
姜萌，女，筑基境/散修/人族；道号：玉罗刹；性格：内狂邪·外护短·外名声；魅力仙姿（375）；声名初出茅庐（12）；爱好：饰品、琴；与玩家同处一地，关系道侣，好感200；坐标(31,78)
背包（约14件）：
丹符：六品玉琼丹×14（单价48，小计672）
身着：青纹袍（值180）
灵石：381
```

---

## 2. search_units — 全局找人

**参数**：`filters{keyword,relation,realm,sect,region,race,sex}`（有值才过滤）

**C# 输出**：`{filters, items[{name,sex?,relation,intim,realm?,sect?,region?}], total}`
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
- **C#**：`{sects:[…], total}`，每条：
  `name` `region` `point{x,y}` `sect_id?` `main_name?` `branch_name?` `name_origin?` `name_part1[]?` `name_part2[]?`
  `is_hold` `hold_by?` `is_top` `top_school?` `sub_schools[]?` `type?` `stand?` `slogans[{slogan,desc?}]?` `fate?`
  `member_count?` `reputation?` `leader?` `enemy?`
- **渲染**：一条一行，段间 `｜`
```
宗门概览（共2个）：
· 迷途荒漠·天钧观（观，立场值1）｜名号：主名天钧，支名观，源名天钧观，名词组1天钧/观｜层级：主宗，下辖2宗（赤星宫、九幻宫）｜状况：未被占据｜宗主韶和顺，弟子460人，声望10000，敌对天魔宗｜宗旨：顺天应人（以天为尊，以观为道）｜气运：天钧气运｜坐标(210,88)｜id=SCH_0001
· 永宁州·丹阳谷永宁州分舵（谷，立场值0）｜名号：主名丹阳，支名谷｜层级：分宗，隶属丹阳谷｜状况：被占据（持有方天魔宗）｜宗主濮阳坻，弟子69人，声望711｜坐标(33,150)
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
| 缔结类（确认窗，玩家同意后） | `op` `target` `relation`（中文）`verified` |
| 缔结类（直写兜底） | `op` `target`（无 relation/verified） |
| 解除类（确认窗） | `op` `target` `relation` `create_ret` `state_at_return` `bw_err?` `readback_still_related?` |

**渲染**
```
姜萌对你的好感提升了3点（请求3点，59→62）
你与姜萌结为道侣。
你同意了与姜萌解除夫妻关系；读回确认关系已解除
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

**参数**：`op`（attack/spar/shuang_xiu/lun_dao/yao_yue/chuan_gong）｜`skill`（chuan_gong）

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
姜萌已向你发起切磋，即将进入战斗/切磋界面。
你与姜萌的论道已结束。
你拒绝了姜萌的邀约（对方似有不悦，好感或受影响）。
姜萌已向你发出邀约（等待你在原版剧情中回应）。
姜萌已向你发起论道，等待完成（C# 未返回结局）。        ← 字段缺失形态（不得说成"未完成"）
你接受了云含的邀约。云含说：「我先前在新达镇附近发现了一处幽静之地，不如我们到那边去吧。我会在新达镇附近等你三个月。」
（原版剧情里玩家已看过这句话，勿复述，接着往下说即可。）
你接受了云含的邀约（将按剧情赴约）。                    ← 没抓到原句（改动前文案，兜底）
```

---

## 7. economy_item — NPC 赠送（发起方=NPC，接收方=玩家）

**参数**：`items[{item_name,count}]`｜`via`（direct/letter）｜`initiator`

**C# 输出**：`op:"give_item"` `target`（=玩家名）`items[{item,requested,actual,mode,error?,from?}]` `via`
`accepted` `accepted_count?`（=receiveProps 计数）`refused_count?` `same_grid?`
（纯灵石：同形，`items[].mode="money"`；失败分支为 `{success:false,error,data:{items}}`）

**渲染**（明确"谁赠谁收"；逐项请求/实得；未达成项带原因；部分拒收）
```
姜萌赠予唐炎：唐炎收下了部分赠礼（六品玉琼丹×14（请求20，实得14）），拒收了其余8件；唐炎收下了灵石×50；未送达：九转金丹（请求1，未达成：背包中没有）（当面送达）
```

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

## 9. 参数化改动（09-10 已实施）

### 9.1 ✅ rankings：放开 `top` 上限（20 → 200），查询条数由模型决定

**现状**：schema `top` 1~20、C# 钳制 1~20（默认 10）。模型想多看只能一次 20 名，**上限本身没有语义依据**。

**提案**
| 位置 | 改动 |
|---|---|
| `tools/schemas.py` | `top`：`maximum: 20 → 200`；描述补"默认 10，需要更多可自行上调" |
| `csharp/ToolExecutor.QueryWorldRankings` | `if (top > 20) top = 20;` → `if (top > 200) top = 200;` |
| `bridge.py` 同名 mock | 同步 1~200 |
| 测试 | 断言 top 边界（20→200） |

**理由**：榜单是"一次性查询"，条数应由模型按需决定；保留 200 只是防呆（防止模型填 100000 时全图单位 dump 爆 token）。
默认仍 10，不改变常规开销。

### 9.2 ✅ events：新增 `count` 参数，默认 12 条（≈最近一年）

**现状**：C# 固定 `const int MaxItems = 24`，无参数。

**提案**
| 位置 | 改动 |
|---|---|
| `tools/schemas.py` | 新增 `count`：`type: integer, minimum: 1, maximum: 120`，描述"仅 topic=events：按月志倒序取最近 N 条，默认 12（约一年）" |
| `csharp/ToolExecutor.QueryWorldEvents` | 读 `args["count"]`（默认 12，钳制 1~120）替换常量 24 |
| `bridge.py` mock | 同步该参数 |
| 渲染层 | **无需改**（已全列 + 计数头） |

**语义说明**：`count` 是**条数**（月志每月条目数不定，通常 1~3 条），"12 条 ≈ 最近一年"是近似。
若要严格"最近 12 个月的全部条目"，需按 `month` 窗口过滤（要用到游戏当前月份字段，未实证）——
本提案先做条数版，简单可靠；确需按月窗口再单独提。

### 9.3 events 上限 120（随 9.2 落地）
与 9.2 同批：上限 120 条 ≈ 十年量级，足够"翻旧账"场景，再往上主要是防爆。

---

## 10. 兜底与错误

- **错误帧**：`{success:false, error:"人话原因"(, data:{…})}` → 无 `data.text`，渲染器返回 `None`
  → **回退 `json.dumps(全量)`**（`error` 本身已是人话，模型能直接读）。
- **未实装的 topic**（如 `region`）：同上回退。
- **未来新工具**：没写渲染器 → 自动回退 JSON，行为与旧版一致，不会坏。
- **测试**：`tests/test_text_render.py`（渲染器单测，含兜底路径）+ `tests/test_bridge_tools.py`（mock 与渲染联动）。
