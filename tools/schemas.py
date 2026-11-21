"""tools/schemas — 9 工具 JSON Schema（OpenAI function calling 兼容）

设计原则（分层分权）：
- 查询 3 只读，长度差异用 detail/page 枚举控制，不各开工具
- 动作 6 按底层执行路径聚类（combat_duel 已并入 world_ai_action），op 用 enum 收敛
- 所有工具 required 极简，description 写明何时调与错误自纠

★ 本文件是**契约的唯一住所**（09-13 去重）★
    参数名 / 枚举取值 / 类型范围 / 「何时必传」——只写在这里，`prompts/sections/tool_usage.txt`
    不再复述。分工判据：删掉后模型「不知道该传什么」→ 归这里；「不知道该干什么」→ 归 tool_usage。
    两侧互相复述的东西（如"同格/异地"、"先 inspect_unit 查背包"）一律只保留一处：
    落在**填参那一刻视线所在**的地方（即工具自己），政策与路由留在 tool_usage。

★ 精简地板（09-13 实测，别再往下压）★
    9 工具序列化后 ~7.3k chars，其中 **~3.1k 是 JSON 结构本体**（`"type"`/`"properties"`/
    `"required"`/`"additionalProperties"`）与**枚举字面量**（`social_relation.op` 13 个值、
    `search_units.filters` 7 个子键…）——那是契约本身，删了就是改功能不是瘦身。
    真正的散文只有 ~3.7k，压到 ~2.3k 已是"每个字都在讲契约"的程度。
    故本文件的瘦身空间上限约 −20%；想再省只能砍功能（少一个枚举值、少一个过滤键）。
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------- 查询域 ----------
INSPECT_UNIT: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "inspect_unit",
        "description": "查询指定人物的分类档案（classes 可多选，默认 brief）。\n**`target` 填人名本身**：查玩家用「玩家」段里的姓名（**不要填「玩家」二字**，也不要填 id/编号），查自己填自己的名字；id 不进模型，别自己拼。全图有重名：命中多人时返回会带 `⚠️同名提醒`，并给每位一个**唯一名**（形如 `益婉容(化神殿·结晶后期)`）——要查另一位，把 `target` 原样写成那个带括号的名字。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "人物真名：查玩家填「玩家」段里的姓名（**不要填「玩家」二字**、不要填 id/编号），查自己填自己名字；同名者照抄结果给的**唯一名**（如 益婉容(化神殿·结晶后期)）"},
                "classes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["brief", "stats", "abilities", "inventory", "relationships", "logs"],
                    },
                    "default": ["brief"],
                    "minItems": 1,
                    "uniqueItems": True,
                    "description": "brief=基础身份(境界/宗门/位置、**他对你的关系与好感**、气运) stats=全量属性(含加成) abilities=功法中文名 inventory=储物(灵石/分类背包/装备) relationships=关系网(每人带名字，同名者带括号后缀) logs=人生经历分页。log_*/inventory_* 参数仅选中对应块时生效",
                },
                "log_page": {"type": "integer", "minimum": 1, "default": 1,
                             "description": "每页5条。【第1页=最新】；正序读生平用 log_since_month/until_month"},
                "inventory_top": {"type": "integer", "minimum": 1, "maximum": 50, "default": 3,
                                  "description": "背包分类内展示价值最高的前 N 种（总种类≤12 时自动全展示）"},
                "inventory_all": {"type": "boolean", "default": False,
                                  "description": "true=全量展示不裁剪（五类全出，大背包慎用）。只想看某一类用 inventory_cat，只找某件东西用 inventory_find"},
                "inventory_cat": {
                    "type": "array",
                    "items": {"type": "string",
                              "enum": ["丹符", "书籍", "装备", "材料", "其他"]},
                    "minItems": 1,
                    "uniqueItems": True,
                    "description": "只看点名的类别（可多选，与游戏背包页签同名）。**点名后该类全量列出、不再裁剪**，其余类不输出——玩家问「他有什么丹药」这类定向问题时用它，比 inventory_all 省得多",
                },
                "inventory_find": {
                    "type": "string",
                    "description": "按名字在这人全部家当里**模糊查找**（例：化瘀丹），返回所有名称含该字串的道具（带类别、数量、单价），不受类别与 inventory_top 限制。玩家问「他有没有 X」时用它**代替**inventory_all",
                },
                "log_filter": {
                    "type": "string",
                    "enum": ["important", "regular", "all", "with_me"],
                    "default": "all",
                    "description": "all=重要+常规（默认；重要件带 ★） important=仅重要（突破/习法等大事） regular=仅常规（论道/双修/赠礼等日常） with_me=仅与玩家相关（与你的往来，同游戏「与我相关」页签）",
                },
                "log_since_month": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "只返回该账面月（含）之后的经历。换算：N年M月=(N-1)*12+M（2年1月=13）。与 log_until_month 成闭区间，先过滤再分页",
                },
                "log_until_month": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "只返回该账面月（含）之前的经历，换算同 log_since_month",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

SEARCH_UNITS: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_units",
        "description": "按过滤条件在全图找人。**玩家报出一个人名问你是否认得、或你打算答「不认识/没听过」之前，必须先用它查**——没印象不等于不认识。条件全部放进 filters（顶层没有 keyword/realm 这些参数）；只填你有把握确定的值，不确定的不要填（空字典=全图好感最高者）；结果按好感降序，重名者会带上唯一名（形如 `李四(化神殿)`），可直接照抄进别的工具的 target。",
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": "过滤条件字典（全部可选，只填确信的值）",
                    "properties": {
                        "keyword": {"type": "string", "description": "姓名/关键字模糊匹配"},
                        "relation": {"type": "string", "enum": ["亲族", "好友", "道侣", "师徒", "结义", "好感", "陌生"], "description": "与玩家的关系类型"},
                        "realm": {"type": "string", "description": "境界，如 金丹/元婴"},
                        "sect": {"type": "string", "description": "宗门名"},
                        "region": {"type": "string", "description": "地区，如 永宁州/雷泽"},
                        "race": {"type": "string", "enum": ["人族", "魔族"], "description": "种族"},
                        "sex": {"type": "string", "enum": ["男", "女"], "description": "性别"},
                    },
                    "additionalProperties": False,
                },
                "top": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "返回前 N 名（按好感降序），默认 10，上限 50。命中多于 10 时（如查某宗全部弟子）传大值取全",
                },
            },
            "required": ["filters"],
            "additionalProperties": False,
        },
    },
}

QUERY_WORLD: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_world",
        "description": "了解世界全局。**玩家问起宗门状况、天下大事、排行榜、可去的地点、州域名，而「近况」段只是顺带提到过它时——那不算你知道，必须调此工具**：问自己宗门/别家宗门的光景用 sects。结果多时传 region 收窄（州名见 world_basis 或先 topic=region）。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["events", "rankings", "places", "sects", "region"],
                    "description": "events=近期天下大事(世界月志) rankings=天下排行榜(战力/声望/魅力/灵石) places=可去地点(城镇/宗门/突破材料/器灵材料，movement travel 的目标目录) sects=宗门(配 name 查族系、detail 查功法与人事) region=地区(州)名列表",
                },
                "cat": {
                    "type": "string",
                    "enum": ["城镇", "宗门", "突破材料", "器灵材料"],
                    "description": "仅 places：城镇/宗门=可 travel 的建物，突破材料/器灵材料=资源点。省略=城镇+宗门",
                },
                "region": {
                    "type": "string",
                    "description": "仅 places/sects 用：按州过滤（州名见世界背景或先 topic=region）。sects 与 name 同给＝精确到该州，只回该州那支；places 传 name 时忽略",
                },
                "name": {
                    "type": "string",
                    "description": "sects=给宗门名/主名→整族(主宗+各州分舵)；给分舵全名→只回那一条；与 region 同给→只回该州那支。places=按地点名包含匹配，四类全搜、无视 region/cat。查无均空表+说明",
                },
                "detail": {
                    "type": "string",
                    "enum": ["info", "people", "all"],
                    "description": "仅 sects：info=功法偏好/人数名额/灵石战力/交情 people=人事树(宗主→大长老→各堂长老→真传) all=两者。只对单宗用",
                },
                "board": {
                    "type": "string",
                    "enum": ["power", "reputation", "beauty", "money"],
                    "description": "仅 rankings：power=战力 reputation=声望 beauty=魅力 money=灵石",
                },
                "top": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "仅 rankings 用：返回前 N 名，默认 10（上限 200）",
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "description": "仅 events 用：按月志倒序取最近 N 条，默认 12（上限 120）",
                },
            },
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
}

# ---------- 动作域 ----------
SOCIAL_RELATION: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "social_relation",
        "description": "管理社会关系与好感（发起方恒为当前对话 NPC）。**玩家说要拜你为师、结道侣结义、或要断这些名分时，调它才作数**。对**其他 NPC**（传 target 真名）：加/减好感与解除类直接生效，**缔结类是当面提出**——当次不生效，对方用同一 op 反向回应（target 填你）才算结下。\n**引擎里一对人只记一条关系**：对方若已有关系（夫妻/道侣/师徒/结义…任一），缔结类会被游戏当场拒绝（引擎码 3），本工具**不做覆盖**——想改结必须先解除原有关系。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "可选。关系动作的对象真名（**任意 NPC**；缔结类与须当面的动作要求同处一地，加/减好感远程也作数）。不填 = 玩家（弹确认窗，**别填「玩家」二字**）；填其他 NPC：加/减好感与解除直接生效，缔结类是提出（等对方回应）"},
                "op": {
                    "type": "string",
                    "enum": [
                        "add_intim",
                        "reduce_intim",
                        "jie_yuan",
                        "jie_chu_jie_yuan",
                        "marry",
                        "divorce",
                        "bai_shi",
                        "jie_chu_bai_shi",
                        "shou_tu",
                        "jie_chu_shou_tu",
                        "ren_yi_fu_mu",
                        "jie_yi",
                        "jie_chu_jie_yi",
                        "chuan_hua",
                    ],
                    "description": "jie_yuan=结为道侣 marry=结为夫妻（须先有道侣且同处一地） divorce=解除夫妻 bai_shi=你拜对方为师 shou_tu=你收对方为徒 ren_yi_fu_mu=认义父母 jie_yi=结义 jie_chu_jie_yuan/jie_chu_bai_shi/jie_chu_shou_tu/jie_chu_jie_yi=解除对应关系 add_intim/reduce_intim 需 value chuan_hua=捎话（需 message，可远程）",
                },
                "value": {"type": "integer", "minimum": 1, "maximum": 120,
                          "description": "仅 add/reduce_intim。**刻度**（面板同源，负向同理）：0 陌路 / 20 泛泛之交 / 60 亲近 / 120 知己 / 180 生死之交；负向 -20 嫌隙 / -60 结怨 / -120 深仇 / -180 不共戴天。**一次事件给多少**：寒暄 5~10、小忙 15、交心或还人情 30、救命或背叛 60、生死大恩 120。**这是「请求值」不是实得**——游戏按当前好感分档打折（正负同构 0~60 全额 / 60~120 五折 / 以上逐档减半），想推动 30 得填更大；结果回读**真实**好感，以那个为准。"},
                "message": {"type": "string", "description": "仅 chuan_hua：捎给他的那句话，第一人称古风口吻，原样送到，超 200 字截断"},
                "ask_text": {"type": "string", "description": "可选。确认窗正文：你的第一人称台词（求婚/结义/拜师/绝交词等），须合人设与语境的古风口吻，过长会截断；不传用默认文案。add/reduce_intim 忽略。"},
            },
            "required": ["op"],
            "additionalProperties": False,
        },
    },
}

MOVEMENT: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "movement",
        "description": "位移与碰面（发起方恒为当前对话 NPC）。**玩家说「我来找你」「你召唤我」「同去某地」这类碰面/同行请求时调它**。travel 的地名先 query_world topic=places 查；到后可等候玩家或约他前来。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "可选。位移的对象真名（**别填「玩家」二字**）。**含义随 op 变**：summon=把谁召到身边；teleport=去谁身边；两者不填都 = 玩家。travel 无对象，不要填"},
                "op": {"type": "string", "enum": ["summon", "teleport", "travel"],
                       "description": "summon=召唤玩家到NPC处 teleport=NPC传送到玩家处 travel=NPC前往指定城镇/宗门并在当地等候"},
                "destination": {"type": "string",
                                "description": "仅 travel 需要：城镇/宗门名（来自 query_world topic=places 的 name，如 白帝城），同名地点可用 region 消歧"},
                "region": {"type": "string",
                           "description": "仅 travel 可选：区域名消歧（如 永宁州），各州分宗常同名"},
            },
            "required": ["op"],
            "additionalProperties": False,
        },
    },
}

WORLD_AI_ACTION: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "world_ai_action",
        "description": "对玩家发起一个行动（发起方恒为当前对话 NPC）。**玩家要你把事做出来（教功法、切磋、双修、邀约、疗伤、送礼之外的动作）只有调它才算数，口头应承等于没做**。无需杀人指令，生死由游戏结算。**没有 ask_text**（原生窗文案由游戏给）。**进行中的动作别重复发起**（舞台指令写「这场交互还在进行中」）。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "不填 = 玩家（别填「玩家」二字）。填另一个 NPC 真名时只能用 `attack`，是单挑还是叫上玩家由 `mode` 定。**join_sect/leave_sect 不用填**（那是你自己的事，不是对谁动手）"},
                "op": {
                    "type": "string",
                    "enum": ["spar", "attack", "shuang_xiu", "lun_dao", "yao_yue", "chuan_gong", "join_sect", "leave_sect"],
                    "description": "spar=切磋（弹原生确认窗，玩家应战才打） attack=攻击 —— 两者都会**真打一场**，要等战后那串对话走完才回，可能几分钟；结果是双方原话，按结局说话，写着未知或「生死未定」就别断胜负、别替他决定。 shuang_xiu=双修 lun_dao=论道 yao_yue=邀约 chuan_gong=传功。shuang_xiu 与 chuan_gong 须同处一地；lun_dao 与 yao_yue 属言语往来，可远程。好感：论道完成双方各+10（本项目规则），双修/切磋等由原生结算。 **join_sect=你自己入宗门 leave_sect=你自己退出宗门**——这两条不是对玩家做的事：玩家只能提议，去不去、走不走由你判断（门槛是宗门自己的规矩，不是谁的命令）",
                },
                "mode": {
                    "type": "string",
                    "enum": ["solo", "together"],
                    "default": "solo",
                    "description": "**仅 target 填了另一个 NPC 时有效**，决定玩家算不算这一场。solo=单独应战：**你自己上**，玩家不参战、只在旁边看，输赢重伤都是你一个人的事。together=一起上：**请玩家带队友一起打**，玩家当发起方，你（若在队中）和队友都会真进场出手、并肩打他；玩家会在「一起应战 / 我一个人」窗里自己决定带不带人。要拉玩家入伙用 together，自己扛用 solo（留空即 solo）。",
                },
                "skill": {"type": "string", "description": "仅 chuan_gong 用；省略时由玩家在原版功法面板自选（推荐），要指定某本才填"},
                "sect": {"type": "string", "description": "仅 join_sect：要入的宗门名（先 query_world topic=sects 看清它收不收人、功法偏好对不对你的路子）。同名分宗用 region 消歧。退宗不用填——退的永远是当前所在那个"},
                "region": {"type": "string", "description": "仅 join_sect 可选：宗门所在州，用于同名分宗消歧（如 永宁州）"},
                "post": {"type": "string", "enum": ["out", "in", "inherit"],
                         "description": "仅 join_sect：以什么身份进门。out=外门弟子 in=内门弟子 inherit=真传弟子（须同时给 master）。**拿不准就先别填**：不填（或 inherit 却没给 master）时本工具不动手，只把该宗在任人数与可选师父列给你，让你先去问玩家"},
                "master": {"type": "string", "description": "仅 join_sect 且 post=inherit：拜哪一堂的长老为师（填长老真名，如 林清）。引擎里「真传」挂在某堂长老名下，所以必须落到具体的人"},
                "reason": {"type": "string",
                           "description": "**join_sect / leave_sect 必填**，别的 op 不要填。写清**你自己**的动机，不是给系统备案：入宗——这门功法偏好对不对你的路子、宗门气运合不合你、那位长老你敬不敬、宗主与你有无旧、这门在你州名声如何；退宗——受了委屈、看不惯、想另投，还是只是被玩家一说。**去不去、走不走由你定**：玩家只是提议，宗门自有规矩（境界、交情、名额），不合你意就当面说不去，并把缘由讲给玩家"},
            },
            "required": ["op"],
            "additionalProperties": False,
        },
    },
}

ECONOMY_ITEM: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "economy_item",
        "description": "你赠送道具或灵石（发起方恒为你），items 可多种、灵石与道具混送。**玩家开口向你讨要时用它把东西真给出去**。**接收方缺省是玩家**：纯灵石弹确认窗（拒收则不送）、含道具时走原版赠送 UI。**接收方填其他 NPC（target）**：当面**提出赠予**（一次一件）——对方用 item_acquire 领取才真交付。异地也可赠；数量不足时全送，并在结果里说明实际数量。赠礼挂钩好感：收下方对给出方 +（配表值≈15）。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "可选。接收方真名（须与你同处一地）。不填 = 玩家（弹确认窗，**别填「玩家」二字**）；填其他 NPC = 提出赠予（单件），对方领取才算"},
                "items": {
                    "type": "array",
                    "description": "赠送清单（可多种，灵石/道具可混送）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string", "description": "道具中文名（如 丹药）或 灵石"},
                            "count": {"type": "integer", "minimum": 1, "maximum": 99999, "default": 1},
                        },
                        "required": ["item_name"],
                        "additionalProperties": False,
                    },
                },
                "via": {"type": "string", "enum": ["direct", "letter"], "default": "direct",
                "description": "送达方式，仅影响**纯灵石**赠送的确认窗与结果文案（含道具时道具恒走原版赠送 UI，此参数只作标注）。direct=按同格/异地自动措辞 letter=信件赠达"}
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
}

ITEM_ACQUIRE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "item_acquire",
        "description": "你取得东西：偷窃或讨要。**要向玩家讨东西，调它才落得了地**；**玩家开口说要送你什么，收下也走它**。对象缺省是玩家。讨要是当面提出（对方用 economy_item 交付才算），偷窃直接生效且被发现掉好感。灵石是货币：偷不了但可讨。好感的增减方向见 accepting_gift",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "可选。被偷/被讨的人的真名（须与你同处一地）。不填 = 玩家（弹原生窗，**别填「玩家」二字**）；填其他 NPC：讨要为提出（对方交付才算）、偷窃直接生效"},
                "op": {"type": "string", "enum": ["steal_item", "ask_for"], "description": "steal_item=偷窃 ask_for=讨要"},
                "item_name": {"type": "string", "description": "中文名（取自 inspect_unit inventory 的 props）；收/讨灵石就填「灵石」，灵石只能配 ask_for。**单件用它，多样改用 items**"},
                "count": {"type": "integer", "minimum": 1, "maximum": 9999, "default": 1, "description": "仅 op=ask_for 时传入（同一件物品的数量）；steal_item 为整栈偷取，不要传"},
                "items": {
                        "type": "array",
                        "description": "仅 op=ask_for：一次讨/收多样东西（≥2 项；可混灵石）。原生接口一次只吃一件，多件走我方确认窗（玩家点「给他」才转移，整笔计）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_name": {"type": "string", "description": "道具中文名（如 丹药）或 灵石"},
                                "count": {"type": "integer", "minimum": 1, "maximum": 99999, "default": 1},
                            },
                            "required": ["item_name"],
                            "additionalProperties": False,
                        },
                    },
                "accepting_gift": {"type": "boolean", "default": False,
                                   "description": "对方先前**主动说要送你**此物，你是在收下而非讨要（对玩家、对 NPC 都同理）：确认窗据此换措辞，好感也据此分流——收下＝收礼方对送礼方 +（单边）；讨要＝取得方 + / 交付方 −（双边）。默认 false=你在讨要"},
            },
            "required": ["op"],
            "additionalProperties": False,
        },
    },
}

TRADE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "trade",
        "description": "买卖：把卖方背包里的一件道具卖给买方，买方付灵石。**玩家说「我出多少灵石买你某物」只有调它才算成交，还价要重发**。**卖方与买方都写真名**（谈买卖的一方应当是你自己——不许替他人做主）。玩家参与会弹确认窗，点「成交」才转移；**双方都在场时是当面提出**，对方照原条款发一遍才成交（改条款=还价）。数量必须**整笔满足**：存货不足或灵石不够都整笔失败（不像 economy_item 有多少送多少）；只成交一部分就改小 count 重发。成交挂钩好感：有价成交双方各+5；price=0 按赠予算（收下方对给出方 +配表值）——原生交易不挂好感，本项目规则。",
        "parameters": {
            "type": "object",
            "properties": {
                "seller": {"type": "string", "description": "卖方真名（出货方）"},
                "buyer": {"type": "string", "description": "买方真名（付灵石方）"},
                "item": {"type": "string", "description": "道具中文名（来自 inspect_unit inventory 的 props）；不能填灵石"},
                "count": {"type": "integer", "minimum": 1, "maximum": 9999, "default": 1,
                          "description": "成交数量；卖方存货不足则整笔失败"},
                "price": {"type": "integer", "minimum": 0, "maximum": 99999999,
                          "description": "买方付给卖方的灵石**总额**（整笔价，不是单价）；0=无偿赠予"},
            },
            "required": ["seller", "buyer", "item", "price"],
            "additionalProperties": False,
        },
    },
}

TEAM: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "team",
        "description": "结伴同行：邀请入队 / 请离或散队 / 查看队伍。**上限 3 人**。`target` 缺省是玩家（**别填「玩家」二字**），填真名则是别的 NPC。invite 与 dismiss 会弹同意窗，同意才生效；status 不弹窗。带人下副本的代价与后果见工具使用规则。",
        "parameters": {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["invite", "dismiss", "status"],
                    "description": "invite=邀请入队（弹窗） dismiss=请离或散队（弹窗；不填 target = 解散整队） status=查看队友（不弹窗）",
                },
                "target": {
                    "type": "string",
                    "description": "对象真名。不填 = 玩家（**别填「玩家」二字**）。invite=要拉的人；dismiss=要请离的人，留空 = 解散整队；status 忽略",
                },
            },
            "required": ["op"],
            "additionalProperties": False,
        },
    },
}

# ---------- 汇总与顺序 ----------
# 按调用频率与决策优先级排序：查询前置，动作按社交→位移→AI→经济（赠送→买卖）→索取（战斗已并入 world_ai_action）
TOOL_ORDER: List[str] = [
    "inspect_unit",
    "search_units",
    "query_world",
    "social_relation",
    "movement",
    "world_ai_action",
    "economy_item",
    "trade",
    "item_acquire",
    "team",
]

_ALL_MAP = {
    "inspect_unit": INSPECT_UNIT,
    "search_units": SEARCH_UNITS,
    "query_world": QUERY_WORLD,
    "social_relation": SOCIAL_RELATION,
    "movement": MOVEMENT,
    "world_ai_action": WORLD_AI_ACTION,
    "economy_item": ECONOMY_ITEM,
    "trade": TRADE,
    "item_acquire": ITEM_ACQUIRE,
    "team": TEAM,
}

ALL_TOOL_SCHEMAS: List[Dict[str, Any]] = [_ALL_MAP[n] for n in TOOL_ORDER]

# ---------- 只读 / 动作 分组（玩家忙碌回合的工具裁剪用）----------
# 只读三件：查询世界与人物，不改任何状态、不弹任何窗 → 玩家忙碌时**仍允许**
# （与 TOOL_ORDER 前三位一致；顺序即"查询前置"的设计意图）
READONLY_TOOLS: List[str] = ["inspect_unit", "search_units", "query_world"]
# 动作六件：改关系 / 位移 / 原生行动 / 经济（赠送·买卖） / 索取 —— 会弹确认窗或直接改变世界
# → 玩家忙碌（看界面/战斗中/有模态）时**禁用**（闸门管"能不能打扰"，见 README 附录二 G.5）
ACTION_TOOLS: List[str] = [n for n in TOOL_ORDER if n not in READONLY_TOOLS]


def get_tool_by_name(name: str) -> Dict[str, Any] | None:
    return _ALL_MAP.get(name)


# ---------- 出站前参数**结构**自检（09-27 真机事故后新增） ----------
#
# 事故原样（真机 09-27；原始帧存于 `~/.sessions/worlds/<存档>/<npc>.jsonl` 的 tool/call 记录）：
#   模型给 economy_item 生成的是
#     {"items": "\n[{\"count\": 1, \"item_name\": \"荒角碧游鲲\"}]\n<parameter=target>\n唐炎\n"}
#   —— `items` 是**字符串**（里面塞着本该是数组的 JSON，还混进 `<parameter=…>` 那套 XML 标签），
#   `target` 压根没成形。后果是一条四连响的坏链：
#     ① C# `args["items"] as JArray` → null → `Fail("缺少 items…")`；
#     ② 那条失败帧又撞上 WsClient 解析崩溃（同日已修）→ 整帧丢失；
#     ③ Python 只看到 120s「请求超时」；
#     ④ NPC 随后把**没办成**的事讲成了"已交割"（玩家开背包：坐骑没回来）。
#   本函数把这类"形状就不对"的调用**拦在出站前**：不进桥（零副作用、零 120s 等待），
#   回一条模型照着改就能过的错误 —— 比让 C# 报一句笼统的"缺少 items"更快、更准。
#
# 判据边界（故意收窄，别再扩）：
#   · 只管**结构**：schema 声明为 array/object 的键型不符、required 缺失；
#   · **不管标量**：`"count": "1"` 这类由 C# 侧 `SafeInt`/窄化容错，拦下只会白丢一次调用；
#   · harness 自己的键（`initiator` / `*_id` / `target_id`…）不在任何 schema 里，天然不参与判定。


def _type_name(val: Any) -> str:
    """给模型看的类型名（中文，与错误文案同语言）。"""
    if isinstance(val, str):
        return "字符串"
    if isinstance(val, bool):
        return "布尔值"
    if isinstance(val, (int, float)):
        return "数字"
    if isinstance(val, list):
        return "数组"
    if isinstance(val, dict):
        return "对象"
    return "空值"


def _array_hint(spec: Dict[str, Any]) -> str:
    """数组形参的样例（元素是对象就列出键名）—— 错误文案里带上靶子，模型照抄即过。"""
    it = spec.get("items")
    if isinstance(it, dict):
        p = it.get("properties") or {}
        if isinstance(p, dict) and p:
            return "[{" + ", ".join('"%s": …' % k for k in list(p)[:4]) + "}]"
    return "[...]"


def shape_error(name: str, args: Dict[str, Any] | None) -> str:
    """工具参数结构自检："" = 合格放行；非空 = 人话错误（回模型，不进桥）。"""
    schema = get_tool_by_name(name)
    if schema is None:
        return ""
    params = (schema.get("function") or {}).get("parameters") or {}
    props = params.get("properties") or {}
    a = args if isinstance(args, dict) else {}
    for key in params.get("required") or []:
        if key not in a or a[key] is None:
            return "参数结构不对：缺 `%s` —— 请按 %s 的参数说明补齐后重发一次" % (key, name)
    for key, val in a.items():
        spec = props.get(key)
        if not isinstance(spec, dict) or val is None:
            continue
        want = spec.get("type")
        if want == "array" and not isinstance(val, list):
            return ("参数结构不对：`%s` 应为数组 %s，收到%s。请把值重新发成 JSON 数组"
                    "（不要写成字符串）" % (key, _array_hint(spec), _type_name(val)))
        if want == "object" and not isinstance(val, dict):
            return "参数结构不对：`%s` 应为对象，收到%s。请按参数说明重发" % (key, _type_name(val))
    return ""
