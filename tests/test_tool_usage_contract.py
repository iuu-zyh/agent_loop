"""tool_usage.txt ↔ schemas.py 去重契约（09-13 精简后的防回涨护栏）

**背景**：`prompts/sections/tool_usage.txt` 曾把 9 个工具的参数名、枚举取值、取值范围
逐个复述了一遍——那些内容在 `tools/schemas.py` 里**已经作为 JSON 传给模型**，
等于每次请求把同一份契约付两遍钱（实测：tool_usage 2,704 字里约 70% 是复述）。

**分工判据**（判一条内容该住哪）：
    删掉后模型「不知道该传什么」→ 归 schemas.py
    删掉后模型「不知道该干什么」→ 归 tool_usage.txt

**这个文件锁两件事**：
  A. 精简成果不许回涨（长度预算 + 禁复述）
  B. 精简过程不许误删承重墙（必存政策清单）

A 与 B 必须成对存在：只锁 A，下次有人为了加一条政策把契约抄回来（A 松了）；
只锁 B，下次有人为了"写全"把 9 个工具的 schema 再抄一遍（B 一点不拦）。
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL_USAGE = ROOT / "prompts" / "sections" / "tool_usage.txt"

# 长度预算：现 919 字（09-21 由「复述参数」改成「场景→工具」路由表）。
# 2026-09-21 上调 900→950：补了「玩家要送你 → 收下也走 item_acquire（accepting_gift）」这条
#   路由（真机：玩家说"我送你三百灵石"，模型想不出"收东西"居然有工具，1400 字思考后干脆不调），
#   并把 `Current runtime context` 这个锚点从第 4 行挪进第 3 行的段清单那句（用户拍板：超一点没关系）。
#   要再加政策仍然先删一条更弱的——预算的意义在这。
# 2026-10-18 上调 950→1040：新增第 10 个工具 `team`（组队下副本），
#   路由行必须覆盖它，否则模型只知道工具存在、不知道什么时候该伸手。
#   同批还顺手把 `team` 的代价（重伤 4 个月 / dismiss 掉好感）从 schemas.py 挪到这里
#   —— 那是"政策"不是"填参"，本来就该在这儿（schemas.py 侧同期瘦身 5612→5019）。
# 2026-11-20 上调 1040→1130：路由行补了**两条新政策**（都是此前完全没有的事实，
#   不是复述）——
#     ① 「好感也要主动调」：玩家帮过你/说了重话/羞辱或救过你，都是一次事件，别只停在台词里；
#        在此之前模型只在"玩家明说加好感"时才调，等于把一半该记账的事件丢了。
#     ② 「捎话走 social_relation」：对象可以是不在跟前的角色，且**当次无回应**
#        （他下次与玩家说话才读到）。不写这句模型会去等一个永远不来的回答。
#   同批没有删旧政策：这一行原本就只有一句路由，删掉任何半句都会丢一条路由。
#   1080 是这次加完的实测值，往上留 50 字余量。
USAGE_CHAR_BUDGET = 1130

# schema 散文冒烟上限：**刻意宽松**——它只是"回涨报警器"，
# 不是精确尺子（真正该拦的是 tool_usage 的复述，见上）。
# 地板参考：序列化后 ~4,665 字是 JSON 结构本体 + 枚举字面量，删了就是改功能不是瘦身。
# 2026-09-21：query_world 增 name（按宗门名精准查单一族系），水位 3,969 → 4,100，预算随之上调。
# 2026-09-21（同日第二次）：给 query_world/search_units/movement/world_ai_action 各补一条
#   「什么情况下必须调我」触发句，同时把 economy_item/item_acquire/trade 里三处重复的
#   「先 inspect_unit 查背包」删掉（已由 tool_usage 的路由行统一承担）。水位 4,183 → 4,270。
#   动机是实测数字：同一模型同一上下文，**不开思考**时"该调工具"的回合有六成一次都没调，
#   而触发句写在工具定义里（离决策最近）比只写在提示词里更能兜住这种漏调。
#   同日再补：trade/economy_item/item_acquire/social_relation 各加一条「玩家这样说就必须调我」
#   的触发句（实测这一类"玩家要 NPC 动手"的请求即使开思考也最常变成光说话），水位 4,270 → 4,4xx。
# 2026-10-12：好感机制上契约（直写值域 1~120 与面板刻度；赠礼/取得/成交/论道各自的好感加值），
#   水位 4,530 → 4,748。这几句是**新事实**不是复述：模型此前不知道 1~10 之外能不能写、
#   也不知道赠礼/讨要会动谁对谁的好感（4 条各一句，直写刻度写在 value 参数上）。
# 2026-10-15：① 直写"实得随当前好感衰减"（档位表：0~60 全额 / 60~120 五折 / 120~180 25% /
#   180~240 12.5% / 240~300 6.25%）——不然模型会把"加 30 只涨 8"当故障；② item_acquire 增
#   `items`（多件讨要，原生接口一次只吃一件 ⇒ 多件走我方确认窗）；③ 好感按 `accepting_gift`
#   分流（收下赠礼=单边；讨要=双边 ±8）。水位 4,748 → 5,083。
# 2026-10-27：`world_ai_action` 增 `mode`（solo=你自己上、玩家旁观 / together=叫上玩家与队友一起打）。
#   这不是复述而是**新维度**：在此之前"NPC 打 NPC"只有一种形态（玩家必然旁观），模型没有
#   任何办法表达"我要拉玩家入伙"；`target` 那句旧文案也写着"只能是玩家/传 NPC 会被拒"，
#   与代码早就对不上了，一并更正。水位 5,120 → 5,340。
# 2026-11-20 上调 5340→6435：三批**新维度**（都是模型此前无法表达的）——
#   ① `inspect_unit` 增 `inventory_cat` / `inventory_find`：前者"点名一类、该类全量不裁剪"，
#      后者"按名模糊查找、不受类别与 top 限制"。旧的两个开关只能说"全量/裁剪"，
#      玩家问"他有没有化瘀丹"时模型只能开全量（大背包几十件）。
#   ② `query_world` 的 `sects` 增 `detail`（功法与人事）与 `region`（收窄）；`places` 的 `cat` 枚举
#      补了突破/器灵材料——那是 `movement travel` 的目标目录，不写模型不知道能去。
#   ③ 「玩家问起宗门状况/天下大事时，近况段顺带提过**不算你知道**，必须调此工具」——
#      这条是**判据**不是描述，删了会让模型拿"近况里好像提过"当已有知识。
#   6,428 是这次加完的实测值。
SCHEMA_PROSE_BUDGET = 6435


def _usage() -> str:
    return TOOL_USAGE.read_text(encoding="utf-8")


def _schemas():
    import sys

    parent = str(ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module(f"{ROOT.name}.tools.schemas")


# ---------- A. 精简成果不许回涨 ----------


def test_tool_usage_covers_every_tool():
    """双向覆盖：TOOL_ORDER 每个工具都要在 tool_usage 里出现，反之不许出现野名字。

    单向检查不够：漏一个 = 那个工具没有任何"什么时候用"的路由提示（模型只知道它存在）；
    野名字 = 工具改名后 tool_usage 还指着旧名（模型会去调一个不存在的工具）。
    """
    S = _schemas()
    txt = _usage()
    missing = [t for t in S.TOOL_ORDER if t not in txt]
    assert not missing, f"tool_usage 没提这些工具（模型不知道何时该用）：{missing}"

    known = set(S.TOOL_ORDER)
    params = set()
    for t in S.TOOL_ORDER:
        for k, v in S._ALL_MAP[t]["function"]["parameters"]["properties"].items():
            params.add(k)
            for kk in (v.get("properties") or {}):
                params.add(kk)
            if isinstance(v.get("items"), dict):
                for kk in (v["items"].get("properties") or {}):
                    params.add(kk)
    stray = {r for r in re.findall(r"\b([a-z]+_[a-z_]+)\b", txt) if r not in known | params}
    assert not stray, f"tool_usage 引用了不存在的东西：{sorted(stray)}（工具改名了？）"


def test_tool_usage_length_budget():
    """长度预算。超了不是"写得细"，是契约又被抄回来了 —— 去 schemas.py 里删，不是在这里加。"""
    n = len(_usage())
    assert n <= USAGE_CHAR_BUDGET, (
        f"tool_usage.txt 已 {n} 字，超过预算 {USAGE_CHAR_BUDGET}。\n"
        f"参数名/枚举/取值范围属于 schemas.py（那里已作为 JSON 传给模型，不重复付费）；\n"
        f"这里只留「怎么选、怎么用、结果怎么读」。要腾地方请先删旧政策。"
    )


# 曾经被抄进 tool_usage、现已删除的复述片段。任一条回来 = 又开始付两遍钱。
_RESTATED_PHRASES = [
    "classes=[",        # 参数签名式枚举
    "items=[{",         # 同上
    "filters 可填",     # 过滤键清单（schema 里有 7 个键的完整定义）
    "无需传 target",    # 9 个工具里写了 6 遍的同一句
    "price 是整笔总价",
    "destination 填",
    "op=steal_item",
    "topic=events",
]


def test_tool_usage_does_not_restate_schema_contract():
    """tool_usage 不许复述 schema 的契约。

    判据一：不许出现 `xxx=yyy` 式的参数赋值签名（`classes=`/`topic=`/`op=`/`price=`…）
            —— 那是 JSON Schema 的写法，写在散文里就是抄。
    判据二：历史上抄过的具体句子不许回来。
    """
    txt = _usage()
    sig = sorted({m for m in re.findall(r"\b[a-z_]{2,}=(?!=)", txt)})
    assert not sig, (
        f"tool_usage 出现参数赋值签名 {sig} —— 这是把 schema 的契约抄成散文。\n"
        f"参数名与取值只在 tools/schemas.py 里维护（模型已经能看到那份 JSON）。"
    )
    back = [p for p in _RESTATED_PHRASES if p in txt]
    assert not back, f"这些已被删掉的 schema 复述又回来了：{back}"


def test_schema_prose_smoke_budget():
    """schemas.py 散文冒烟上限（宽松报警，不是精确尺子）。"""
    S = _schemas()
    prose = 0
    for t in S.ALL_TOOL_SCHEMAS:
        f = t["function"]
        prose += len(f.get("description", ""))
        for v in f["parameters"]["properties"].values():
            prose += len(v.get("description", ""))
            for sub in (v.get("properties") or {}).values():
                prose += len(sub.get("description", ""))
            if isinstance(v.get("items"), dict):
                prose += len(v["items"].get("description", ""))
                for sub in (v["items"].get("properties") or {}).values():
                    prose += len(sub.get("description", ""))
    assert prose <= SCHEMA_PROSE_BUDGET, (
        f"schemas.py 散文已 {prose} 字（上限 {SCHEMA_PROSE_BUDGET}）—— 检查是否在 "
        f"function.description 与参数 description 之间互相复述（09-13 前 world_ai_action "
        f"的「同格/异地」写了两遍、economy_item 的「先查背包」写了两遍）"
    )


# ---------- B. 精简不许误删承重墙 ----------
#
# 这些是 schema **表达不了**的东西：跨工具路由、结果解读、语气约束、错误处理。
# 精简时最容易被当成"重复"顺手删掉，所以逐条钉住。
_LOAD_BEARING = [
    ("指人只写名字", "唯一名", "模型不接触 id：只写名字；重名时照抄唯一化显示名（益婉容事故：按名查到另一个同名的人）"),
    ("同名提醒", None, "ambiguous 同名提醒出现时该怎么处置（schema 只说会返回它）"),
    ("陌生", None, "search_units 结果里 relation=陌生 = 你不认识这个人（世界语义，schema 无）"),
    ("异地", None, "面对面类动作异地不可行（跨工具路由，schema 只在 op 里管自己）"),
    ("trade", "非玩家", "给非玩家的人物送东西只能用 trade（economy_item 接收方恒为玩家）"),
    ("讨要", "偷", "讨要比偷窃温和，能用讨要就不偷（价值判断，schema 无）"),
    ("ask_text", "现代词汇", "确认窗台词要与对话口吻一致、不得现代词汇（人设约束）"),
    ("error", None, "工具返回 error 时解释原因 + 给替代方案 + 不重试同一参数"),
]


def test_tool_usage_keeps_load_bearing_policy():
    """承重墙清单：每条政策都必须还在（防这轮精简删过头）。"""
    txt = _usage()
    lost = [why for a, b, why in _LOAD_BEARING if a not in txt or (b and b not in txt)]
    assert not lost, "tool_usage 丢了这些 schema 表达不了的政策：\n  - " + "\n  - ".join(lost)


def test_tool_usage_delegates_param_detail_to_schemas():
    """必须有一句明确把参数细节委派给工具定义。

    不是废话：模型看不到某条规则时，可能**自己编一个参数/取值**。显式写"以工具定义为准"
    把"信息缺失"变成"明确委派"，是删掉复述后必须补上的一句。
    """
    txt = _usage()
    assert "工具定义" in txt and "不重复" in txt, (
        "tool_usage 删掉了参数复述，却没写「参数以工具定义为准」—— "
        "模型可能自己编一个参数名或枚举值"
    )


def test_schema_json_still_serializable_and_ordered():
    """去重不能碰结构性契约：9 工具、`_ALL_MAP` 无孤儿、只读组必须是 TOOL_ORDER 的前三位。

    ⚠ 这里刻意**不**用 `[t["name"] for t in ALL_TOOL_SCHEMAS] == TOOL_ORDER` 当判据 ——
    `ALL_TOOL_SCHEMAS` 是 `[_ALL_MAP[n] for n in TOOL_ORDER]` 推导出来的，那样断言是恒真式，
    打乱 TOOL_ORDER 也照样通过（本测试的变异验证就是这么发现的）。
    真正会静默出事的是「查询前置」这个**顺序语义**：`READONLY_TOOLS` 的注释与
    `ACTION_TOOLS` 的推导都假定它是 TOOL_ORDER 的前三位；顺序一改，
    "忙碌时仍允许只读"就变成"允许了某两个动作工具"，且没有任何地方会报错。
    """
    S = _schemas()
    expected = {
        "inspect_unit", "search_units", "query_world",
        "social_relation", "movement", "world_ai_action",
        "economy_item", "trade", "item_acquire", "team",
    }
    assert set(S.TOOL_ORDER) == expected and len(S.TOOL_ORDER) == 10, \
        f"工具集变了：{S.TOOL_ORDER}"
    assert set(S._ALL_MAP) == set(S.TOOL_ORDER), \
        f"_ALL_MAP 与 TOOL_ORDER 不齐（孤儿 schema 永远不会给模型看）：{set(S._ALL_MAP) ^ set(S.TOOL_ORDER)}"
    assert S.READONLY_TOOLS == S.TOOL_ORDER[:3], (
        f"「查询前置」被破坏：READONLY_TOOLS={S.READONLY_TOOLS} 不是 TOOL_ORDER {S.TOOL_ORDER} 的前三位。\n"
        f"READONLY_TOOLS 决定玩家忙碌时**仍允许**哪些工具（见 schemas.py 注释与 README 附录二 G.5）；\n"
        f"顺序与分组一旦错位，忙碌回合会静默放行动作工具。"
    )
    assert S.ACTION_TOOLS == [n for n in S.TOOL_ORDER if n not in S.READONLY_TOOLS]
    json.dumps(S.ALL_TOOL_SCHEMAS, ensure_ascii=False)  # 必须可序列化
