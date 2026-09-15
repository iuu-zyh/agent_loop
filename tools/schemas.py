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
        "description": "查询指定人物的分类档案。classes 可多选需要的数据块（默认 brief），无笼统 full。\n★指人方式二选一★：优先 `unit_id`（精确，来自 relationships 或 search_units）；没有 id 时才用 `target` 填真名。**全图有重名**（同名 NPC 是常态），按名查询命中多人时返回里会带 `ambiguous` 同名提醒并列出候选——那时别直接用，先确认是不是你要找的人。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "人物真名（拿不到 unit_id 时才用）：查玩家用「玩家」段里的姓名，查自己填自己的名字"},
                "unit_id": {"type": "string", "description": "精确指定要查的人：填 relationships 或 search_units 结果里的 unit_id（形如 Xs6JDI）。与 target 同时给出时以它为准，不再按名字匹配"},
                "classes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["brief", "stats", "abilities", "inventory", "relationships", "logs"],
                    },
                    "default": ["brief"],
                    "minItems": 1,
                    "uniqueItems": True,
                    "description": "brief=基础身份(境界/宗门/位置/关系/好感/气运) stats=全量属性(含加成) abilities=功法中文名 inventory=储物(灵石/分类背包/装备) relationships=关系网(每人带 unit_id，可再喂回 unit_id 参数) logs=人生经历分页。下缀 log_*/inventory_* 参数仅在选中对应块时生效",
                },
                "log_page": {"type": "integer", "minimum": 1, "default": 1,
                             "description": "每页5条。【第1页=最新】，经历按新→旧排列；要按时间正序读完整生平，用 log_since_month/log_until_month 圈范围"},
                "inventory_top": {"type": "integer", "minimum": 1, "maximum": 50, "default": 3,
                                  "description": "背包分类内展示价值最高的前 N 种（总种类≤12 时自动全展示）"},
                "inventory_all": {"type": "boolean", "default": False,
                                  "description": "true=全量展示不裁剪（大背包慎用；找特定低价值道具时用）"},
                "log_filter": {
                    "type": "string",
                    "enum": ["important", "regular", "all"],
                    "default": "all",
                    "description": "all=重要+常规（默认，查经历用这个；重要件带 ★） important=仅重要（突破/习法等大事，早年常为空） regular=仅常规（论道/双修/赠礼等日常）",
                },
                "log_since_month": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "只返回该账面月（含）之后的经历。换算：N年M月=(N-1)*12+M（如 2年1月=13）。与 log_until_month 组成闭区间，先过滤再分页",
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
        "description": "按过滤条件在全图找人。filters 只填你有把握确定的值，不确定的不要填（空字典=返回全图与你好感最高的前 10）；结果按好感降序，**每行带 unit_id**，可直接填进 inspect_unit。",
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
                }
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
        "description": "了解世界全局。世界层信息无需每轮带走，按需查询。places/sects 结果可能很多，应传 region 只查目标州（州名见 world_basis 或先 topic=region）。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["events", "rankings", "places", "sects", "region"],
                    "description": "events=近期天下大事(世界月志) rankings=天下排行榜(战力/声望/魅力/灵石) places=可去地点(城镇/宗门/突破材料/器灵材料，movement travel 的目标目录) sects=宗门概览(名号/层级/存亡被占/宗主/宗旨气运) region=世界地区(州)名列表",
                },
                "cat": {
                    "type": "string",
                    "enum": ["城镇", "宗门", "突破材料", "器灵材料"],
                    "description": "仅 topic=places 时使用：城镇/宗门=可 travel 的建物，突破材料/器灵材料=地图资源点。省略时只回城镇+宗门",
                },
                "region": {
                    "type": "string",
                    "description": "仅 topic=places/sects 时使用：按州过滤（州名见世界背景 或先 topic=region）；不传返回全部",
                },
                "board": {
                    "type": "string",
                    "enum": ["power", "reputation", "beauty", "money"],
                    "description": "仅 topic=rankings 时使用：power=战力 reputation=声望 beauty=魅力 money=灵石",
                },
                "top": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "仅 topic=rankings 时使用：返回前 N 名，默认 10（上限 200）",
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "description": "仅 topic=events 时使用：按月志倒序取最近 N 条，默认 12（上限 120，可翻更早历史）",
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
        "description": "管理社会关系与好感（发起方恒为当前对话 NPC，对象恒为玩家，无需传 target）。服务端二次校验好感阈值与关系互斥。",
        "parameters": {
            "type": "object",
            "properties": {
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
                    ],
                    "description": "add/reduce_intim 需 value",
                },
                "value": {"type": "integer", "minimum": 1, "maximum": 10, "description": "仅 add/reduce_intim 需要，1-10"},
                "ask_text": {"type": "string", "description": "可选。关系确认窗正文，NPC 第一人称台词（求婚词/结义词/拜师词/绝交词等）。仅关系类 op 有效，add/reduce_intim 会忽略。须符合人设与语境的古风口吻，过长会被截断；不传则用默认文案。"},
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
        "description": "位移与碰面（发起方恒为当前对话 NPC）。travel 的目标地名先 query_world topic=places 查；到后可在当地等候玩家或约玩家前来碰面。",
        "parameters": {
            "type": "object",
            "properties": {
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
        "description": "对玩家发起一个行动（发起方恒为当前对话 NPC，对象恒为玩家，无需传 target）。服务端校验同格/好感/战力。攻击会触发战斗 UI，战后游戏自然结算是否杀死，无需单独杀人指令。",
        "parameters": {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["spar", "attack", "shuang_xiu", "lun_dao", "yao_yue", "chuan_gong"],
                    "description": "spar=切磋（会弹原生确认窗，**等玩家应战或婉拒之后**才返回结果，届时应按玩家的实际选择说话） attack=攻击（触发战斗，战后自然结算） shuang_xiu=双修 lun_dao=论道 yao_yue=邀约 chuan_gong=传功。shuang_xiu 与 chuan_gong 须同处一地；lun_dao 与 yao_yue 属言语往来，可远程",
                },
                "skill": {"type": "string", "description": "可选；仅 chuan_gong 用到。省略时由玩家在游戏原生功法面板自行选择要学的功法（推荐）；要指定某本时才填功法 ID 或名称"},
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
        "description": "NPC 赠送道具或灵石给玩家（发起方恒为当前对话 NPC，接收方恒为玩家，无需传 target），items 可多种、灵石与道具混送。先 inspect_unit(classes=inventory) 确认 NPC 自己的背包（道具 props/装备 equips/灵石 money）。纯灵石赠送弹确认窗（拒收则不送）；含道具时道具走原版赠送 UI、灵石随之即时到账。异地也可赠；数量不足时全送，并在结果 actual 里说明。",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "赠送清单（可多种，灵石/道具可混送）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string", "description": "道具中文名（来自 inspect_unit inventory，如 丹药）或 灵石"},
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
        "description": "NPC 从玩家处取得道具：偷窃或讨要（发起方恒为当前对话 NPC，对象恒为玩家，无需传 target）。先 inspect_unit(classes=inventory) 确认玩家背包（props 每个条目 = 一个道具栈，count 即该栈数量）。steal_item 游戏原生无数量参数，恒为整栈偷取（同名多栈时一次偷一栈），被发现会掉好感；ask_for 可指定数量，限普通道具，灵石不适用。",
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["steal_item", "ask_for"], "description": "steal_item=偷窃 ask_for=讨要"},
                "item_name": {"type": "string", "description": "道具中文名（来自 inspect_unit inventory 的 props），限普通道具"},
                "count": {"type": "integer", "minimum": 1, "maximum": 9999, "default": 1, "description": "仅 op=ask_for 时传入（索要数量）；steal_item 为整栈偷取，不要传"},
            },
            "required": ["op", "item_name"],
            "additionalProperties": False,
        },
    },
}

TRADE: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "trade",
        "description": "买卖：把卖方背包里的一件道具卖给买方，买方付灵石给卖方。**买卖双方都由你指定**（玩家或任意 NPC 均可，写真名），不限当前对话对象。先 inspect_unit(classes=inventory) 确认卖方有货（props 的 name/count）与买方有灵石（money）。会弹确认窗，玩家点「成交」才真正转移。数量必须**整笔满足**：卖方存货不足或买方灵石不够都整笔失败（不像 economy_item 有多少送多少），只成交一部分就改小 count 重发。",
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
