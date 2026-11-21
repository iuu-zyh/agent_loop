# 9 个工具 schema 的中文描述（发给模型的原文）
来源：`C:\Users\<用户名>\.sessions\worlds\BOlQu6\%E5%A7%9C%E8%90%8C.jsonl`（= 姜萌）里的 **`request/header` 事件** → `data.header.tools[]`；源头是 `F:\agent_loop\tools\schemas.py`。
token 口径 = 项目计量器（中文 1 字≈1 token，ASCII 4 字符≈1 token）。

---

## `inspect_unit` —— 942 tokens
**顶层 description（96 tokens）**

> 查询指定人物的分类档案。classes 可多选需要的数据块（默认 brief），按 UnitInfoData 字段分类，无笼统 full。优先读 Current runtime context 各段（自身/玩家/近况），缺什么再调此工具；target 填人物真名，查玩家用「玩家」段里的姓名。

**参数（8 个，必填：target）**
- **`target`** (string) —— 44 tokens
  > 人物真名：查玩家用 Current runtime context「玩家」段里的姓名，查自己填自己的名字
- **`classes`** (array) —— 304 tokens
  > brief=基础(境界/宗门/位置/关系/好感/气运 luck 先天born+后天added) stats=全量属性(动态值含加成：个人 寿命年/心情/健康/精力/体力/灵力/念力/幸运/悟性，战斗 攻防/脚力/移速/双抗/会心/护心/暴击/抗暴，资质 刀枪剑拳掌指+六灵根+炼丹炼器风水画符药材矿材，另有道心/魅力/声望) abilities=功法(灵技/绝技/身法/神通/心法中文名) inventory=储物(灵石 money/分类背包 props 按 丹符·书籍·装备·材料·其他 分组、类内按单价worth降序、含栈总值total，大背包每类取top其余聚进misc/身上装备 equips) relationships=关系网(父母/义父母/子女/义子女/兄弟/结义/道侣/师徒/情人/好友 friend_units/仇人 enemy_units，返回中文名) logs=人生经历分页
- **`log_page`** (integer) —— 72 tokens
  > 仅 classes 含 logs 时有效，每页5条。【第1页=最新】，经历按新→旧排列（要按时间正序读完整生平，用 log_since_month/log_until_month 圈范围）
- **`inventory_top`** (integer) —— 58 tokens
  > 仅 classes 含 inventory 时有效：背包分类内展示价值最高的前 N 种（总种类≤12 时自动全展示）
- **`inventory_all`** (boolean) —— 52 tokens
  > 仅 classes 含 inventory 时有效：true=全量展示不裁剪（大背包慎用，找特定低价值道具时用）
- **`log_filter`** (string  枚举: important / regular / all) —— 107 tokens
  > 仅 classes 含 logs 时有效：all=重要+常规（默认，查经历用这个；渲染时重要件带 ★ 前缀，表头给图例） important=仅重要（突破/习法等大事，早年常为空） regular=仅常规（论道/双修/赠礼等日常互动）
- **`log_since_month`** (integer) —— 76 tokens
  > 仅 classes 含 logs 时有效：只返回该账面月（含）之后的经历。换算：N年M月 = (N-1)*12+M，如 1年1月=1、2年1月=13。与 log_until_month 组成闭区间，先过滤再分页
- **`log_until_month`** (integer) —— 53 tokens
  > 仅 classes 含 logs 时有效：只返回该账面月（含）之前的经历。换算同 log_since_month（N年M月=(N-1)*12+M）

---

## `search_units` —— 341 tokens
**顶层 description（75 tokens）**

> 按过滤条件在全图找人。filters 只填你有把握确定的值，不确定的不要填（空字典=返回全图与你好感最高的前 10）；结果按好感降序。找到具体的人后再用 inspect_unit 看详细。

**参数（1 个，必填：filters）**
- **`filters`** (object) —— 214 tokens
  > 过滤条件字典（全部可选，只填确信的值，不确定的不要填）

---

## `query_world` —— 677 tokens
**顶层 description（146 tokens）**

> 了解世界全局：近期天下大事(events)、天下排行榜(rankings)、可去地点(places，城镇/宗门/突破材料/器灵材料的名字+区域+分类)、宗门概览(sects，名号/主分宗层级/存亡被占/宗旨气运)。这类世界层信息无需每轮带走，按需查询。places/sects 结果可能很多，应传 region 只查目标州（州名见 world_basis 世界背景 或 topic=region），places 还应传 cat 只查目标分类。

**参数（6 个，必填：topic）**
- **`topic`** (string  枚举: events / rankings / places / sects / region) —— 158 tokens
  > events=近期天下大事(世界月志，默认最近12条，可用 count 调) rankings=天下排行榜(战力/声望/魅力/灵石 Top N) places=可去地点(城镇/宗门/突破材料/器灵材料+区域，movement travel 的目标目录) sects=宗门概览(名号/主宗-分宗层级/存亡被占/宗主弟子声望敌对/宗旨气运) region=当前世界地区(州)名列表(供 places/sects 的 region 过滤与 travel 消歧)
- **`cat`** (string  枚举: 城镇 / 宗门 / 突破材料 / 器灵材料) —— 92 tokens
  > 仅 topic=places 时使用：地点分类（必传，别全量扫——城镇/宗门=可 travel 的建物，突破材料/器灵材料=地图资源点）。省略时只回城镇+宗门（旧行为）
- **`region`** (string) —— 59 tokens
  > 仅 topic=places/sects 时使用：按地区过滤（州名如 永宁州/雷泽/华封州，见世界背景 或先 topic=region 查询）；不传返回全部
- **`board`** (string  枚举: power / reputation / beauty / money) —— 48 tokens
  > 仅 topic=rankings 时使用：power=战力 reputation=声望 beauty=魅力 money=灵石
- **`top`** (integer) —— 50 tokens
  > 仅 topic=rankings 时使用：返回前 N 名，默认 10；需要更多可自行上调（上限 200）
- **`count`** (integer) —— 62 tokens
  > 仅 topic=events 时使用：按月志倒序取最近 N 条，默认 12（约最近一年）；要翻更早的历史可上调（上限 120）

---

## `social_relation` —— 326 tokens
**顶层 description（80 tokens）**

> 管理社会关系与好感（发起方恒为当前对话 NPC，对象恒为玩家，无需传 target）。服务端二次校验好感阈值与关系互斥，失败返回 error 需向玩家解释并给替代方案，不得重试同一参数。

**参数（3 个，必填：op）**
- **`op`** (string  枚举: add_intim / reduce_intim / jie_yuan / jie_chu_jie_yuan / marry / divorce / bai_shi / jie_chu_bai_shi / shou_tu / jie_chu_shou_tu / ren_yi_fu_mu / jie_yi / jie_chu_jie_yi) —— 65 tokens
  > add/reduce_intim 需 value
- **`value`** (integer) —— 27 tokens
  > 仅 add/reduce_intim 需要，1-10
- **`ask_text`** (string) —— 98 tokens
  > 可选。以 NPC 第一人称说的台词，作为关系确认窗正文（求婚词/结义词/拜师词/绝交词等）。仅关系类 op 有效（add/reduce_intim 会忽略）。须符合 NPC 人设与语境的古风口吻，过长会被截断；不传则用默认文案。

---

## `movement` —— 287 tokens
**顶层 description（84 tokens）**

> 位移与碰面（发起方恒为当前对话 NPC）。summon=把玩家拉到NPC处；teleport=NPC移到玩家处；travel=NPC自主前往指定城镇/宗门（先 query_world topic=places 查可去地点），到后可在当地等候玩家或约玩家前来碰面。

**参数（3 个，必填：op）**
- **`op`** (string  枚举: summon / teleport / travel) —— 55 tokens
  > summon=召唤玩家到NPC处 teleport=NPC传送到玩家处 travel=NPC前往指定城镇/宗门并在当地等候
- **`destination`** (string) —— 50 tokens
  > 仅 travel 需要：城镇/宗门名（来自 query_world topic=places 的 name，如 白帝城），同名地点可用 region 消歧
- **`region`** (string) —— 43 tokens
  > 仅 travel 可选：区域名消歧（如 永宁州），同名地点（各州分宗）时建议带上

---

## `world_ai_action` —— 349 tokens
**顶层 description（148 tokens）**

> 对玩家发起一个行动（发起方恒为当前对话 NPC，对象恒为玩家，无需传 target；战斗/双修/论道/邀约/传功/提升心情）。按底层动作类分发，服务端校验同格/好感/战力；切磋/攻击/双修/传功必须同处一地，异地传音时不可行（论道、邀约等言语往来之事可远程）。攻击会触发战斗 UI，战斗结束后游戏自然结算是否杀死，无需单独杀人指令。

**参数（2 个，必填：op）**
- **`op`** (string  枚举: spar / attack / shuang_xiu / lun_dao / yao_yue / chuan_gong) —— 70 tokens
  > spar=切磋 attack=攻击（触发战斗，战后自然结算） shuang_xiu 需同格；chuan_gong 需同格；论道/邀约可远程
- **`skill`** (string) —— 77 tokens
  > 可选；仅 chuan_gong 用到。省略时由玩家在游戏原生功法面板自行选择要学的功法（推荐）；若要指定某本，填功法 ID 或名称（可先 inspect_unit(classes=abilities) 查看）

---

## `economy_item` —— 391 tokens
**顶层 description（192 tokens）**

> NPC 赠送道具或灵石给玩家（发起方恒为当前对话 NPC，接收方恒为玩家，无需传 target），可一次赠多种（items 数组，灵石与道具可混送）。先 inspect_unit(classes=inventory) 确认 NPC 背包（道具 props/装备 equips/灵石 money）；纯灵石赠送弹自制确认窗（玩家选「我收下了/我不需要」，收下后灵石才到账、拒收则不送）；灵石与道具混送时道具走原版赠送 UI、灵石随之即时到账（结果文案注明）；异地也可赠（结果说明方式）；数量不足时全送并在 actual 说明。

**参数（2 个，必填：items）**
- **`items`** (array) —— 107 tokens
  > 赠送清单（可多种，灵石/道具可混送）
- **`via`** (string  枚举: direct / letter) —— 38 tokens
  > direct=当面/异地传讯赠送 letter=信件赠送

---

## `trade` —— 436 tokens
**顶层 description（211 tokens）**

> 买卖：把卖方背包里的一件道具卖给买方，买方付灵石给卖方。**买卖双方都由你指定**（玩家或任意 NPC 均可，写真名），不限当前对话对象。实质是四条字段变更：卖方背包 −道具 / 买方背包 +道具；买方灵石 −价 / 卖方灵石 +价。先 inspect_unit(classes=inventory) 确认卖方有货（props 的 name/count）与买方有灵石（money）。会弹确认窗，玩家点「成交」才真正转移。数量必须**整笔满足**：卖方存货不足或买方灵石不够都整笔失败（不像 economy_item 赠送那样有多少送多少），要给一部分就改小 count 重发。

**参数（5 个，必填：seller, buyer, item, price）**
- **`seller`** (string) —— 21 tokens
  > 卖方真名（出货的一方）
- **`buyer`** (string) —— 22 tokens
  > 买方真名（付灵石的一方）
- **`item`** (string) —— 33 tokens
  > 道具中文名（来自 inspect_unit inventory 的 props）；不能填灵石
- **`count`** (integer) —— 37 tokens
  > 成交数量；卖方存货不足则整笔失败
- **`price`** (integer) —— 46 tokens
  > 买方付给卖方的灵石**总额**（整笔价，不是单价）；0=无偿赠予

---

## `item_acquire` —— 350 tokens
**顶层 description（172 tokens）**

> NPC 从玩家处取得道具：偷窃(steal_item)或讨要(ask_for)（发起方恒为当前对话 NPC，对象恒为玩家，无需传 target）。先 inspect_unit(classes=inventory) 确认玩家背包（props_detail 有每栈数量）；steal_item=偷窃玩家身上指定道具，游戏原生无数量参数、恒为整栈偷取（同名多栈时一次偷一栈，可再次调用偷其余），被发现会掉好感；ask_for=向玩家讨要指定道具/数量（UnitActionRoleAskfor，支持按数量，限普通道具，灵石不适用）。

**参数（3 个，必填：op, item_name）**
- **`op`** (string  枚举: steal_item / ask_for) —— 27 tokens
  > steal_item=偷窃 ask_for=讨要
- **`item_name`** (string) —— 33 tokens
  > 道具中文名（来自 inspect_unit inventory 的 props），限普通道具
- **`count`** (integer) —— 59 tokens
  > 仅 op=ask_for 时传入（索要数量）；steal_item 为整栈偷取，不要传此参数（传了也会被忽略）

---

## 合计

**4099 tokens**（9 个工具，每回合固定开销）
