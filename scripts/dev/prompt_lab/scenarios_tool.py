"""scenarios_tool — 工具调用探针场景集。

每条场景 = 一份 L1 运行时上下文 + 玩家台词 + 一组"假但像真的"工具结果 + 判据。
判据只看**模型的实际选择**（调不调、调哪件、参数对不对、话怎么说），不看实现。

覆盖七类：
  ① 不该调工具（答案已在 L1 / 纯寒暄）—— 过调用直接烧钱与延迟
  ② 该调工具（自身背包 / 玩家档案 / 找人 / 世界）
  ③ 参数正确性（块选择最小化 / 真名而非 id / 多过滤条件 / 枚举与视角）
  ④ 视角（发起方恒为 NPC：收徒 vs 拜师、传送到玩家 vs 召唤玩家）
  ⑤ 异地约束（切磋/双修不可远程；论道可远程）
  ⑥ 错误自纠（{"error"} 之后不得重复同一参数）
  ⑦ 边界（陌生人不装熟、不替玩家对第三方动手、偷窃要有理由）
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List

NPC = "云含"
PLAYER = "缪嘉歆"

# ---------------------------------------------------------------- L1 夹具

def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def base_l1(**over: Any) -> Dict[str, Any]:
    raw: Dict[str, Any] = {
        "npc_id": NPC,
        "sex": "女",
        "realm": "结晶后期",
        "sect": "化神殿",
        "point": {"x": 31, "y": 78},
        "age": 96,
        "life": 320,
        "mood": 70,
        "power": 5200,
        "health": 88,
        "talent": 76,
        "title": "素心",
        "beauty_label": "超凡",
        "reputation_label": "崭露头角",
        "personality": {"inner": "仁善", "inner_desc": "见人有难总想搭把手",
                        "outer": ["护短"], "outer_desc": ["自家人受了委屈必不干休"]},
        "hobby": [{"name": "炼丹"}, {"name": "抚琴"}],
        "luck": {"born": ["剑心(紫)"], "added": [{"name": "陷入瓶颈"}]},
        "player": {
            "name": PLAYER, "sex": "女", "realm": "金丹中期", "sect": "天罡宗",
            "relation": "道侣", "intim": 200, "same_grid": False,
            "point": {"x": 20, "y": 8}, "mood": 60, "beauty": 700, "reputation": "崭露头角",
        },
        "now": {"year": 3, "month": 5, "day": 12, "text": "3年5月12日"},
        "recent": ["在化神殿后山闭关三月，炼成一炉三品聚气丹", "与天罡宗长老论道半日"],
        "others": [],
    }
    return deep_merge(raw, over)


def peer_lisi() -> Dict[str, Any]:
    return {"name": "李四", "sex": "男", "realm": "筑基后期", "sect": "无宗门",
            "age": 44, "beauty_label": "正常", "reputation_label": "初出茅庐",
            "same_grid": True, "relation": "好友", "intim": 180,
            "personality": {"inner": "义气", "inner_desc": "为朋友两肋插刀", "outer": ["任我"], "outer_desc": ["我行我素"]}}


# ---------------------------------------------------------------- 工具结果（像真的）

def res_inspect_brief(name: str, realm: str = "结晶后期", sect: str = "化神殿",
                      relation: str = "陌生", intim: int = 0) -> str:
    return (f"{name}\n"
            f"境界：{realm}　宗门：{sect}\n"
            f"与你的关系：{relation}（好感 {intim}）\n"
            f"位置：坐标(31,78)　年龄：96岁\n"
            f"容貌：超凡　声名：崭露头角")


def res_inspect_inventory(name: str = NPC) -> str:
    return (f"{name}的储物\n"
            f"灵石：3,240\n"
            f"【丹药】三品聚气丹 ×2、二品回春散 ×5\n"
            f"【材料】赤炎石 ×7、灵草 ×31\n"
            f"【装备】青锋剑（凡品）、素心道袍（黄品）")


def res_search(keyword: str, rows: List[str]) -> str:
    body = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rows))
    return (f"「{keyword}」相关（全图共 {len(rows)} 人，按好感降序）：\n{body}\n"
            f"（relation=陌生 表示你并不认识此人）")


def res_same_name() -> str:
    return ("益婉容\n"
            "境界：结晶后期　宗门：化神殿\n"
            "与你的关系：陌生（好感 0）\n"
            "⚠️同名提醒：「益婉容」全图有 2 人同名，以下是**「益婉容(化神殿·结晶后期)」**这一位的档案，"
            "**未必是你要找的人**。另一位是 益婉容(登仙境·大长老)。要查另一位，把 target 原样写成上面那个带括号的名字。")


def res_world_events() -> str:
    return ("世界月志（最近 3 条，倒序）\n"
            "3年5月：雷泽一带妖潮退去，永宁州重开商路。\n"
            "3年4月：化神殿与天罡宗在云陌州交界处起了争执，双方各退一步。\n"
            "3年3月：暮仙州出现一名登仙境散修，连败三家宗门长老。")


def res_world_places() -> str:
    return ("地点（cat=城镇）\n"
            "白帝城　永宁州　城镇　人口 4.2 万\n"
            "青云镇　永宁州　城镇　人口 0.8 万\n"
            "寒山关　雷泽　城镇　人口 1.1 万")


def res_action_ok(op: str) -> str:
    return json.dumps({"success": True, "data": {"op": op, "message": "已执行"}}, ensure_ascii=False)


def res_err(msg: str) -> str:
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False)


def _remote_tools():
    """异地场景的工具集：**有状态** —— movement 成功后就同格了。

    为什么要状态：探针第一版给的是无状态桩（移动成功也照样回「异地」），
    于是模型被假报错带着反复重试 spar/summon —— 测出来的"行为"里有我的一半。
    """
    state = {"same_grid": False}

    def movement(a):
        state["same_grid"] = True
        return res_action_ok(str(a.get("op")))

    def action(a):
        op = str(a.get("op"))
        if op in ("spar", "attack", "shuang_xiu", "chuan_gong") and not state["same_grid"]:
            return res_err("切磋需同格，当前异地")
        return res_action_ok(op)

    return {"movement": movement, "world_ai_action": action}


# ---------------------------------------------------------------- 判据助手

def no_tool(*names: str) -> Callable[[Any], bool]:
    """没有调用任何工具（names 为空）或没有调用指定的那几件。"""
    def _f(run) -> bool:
        if not names:
            return len(run.calls) == 0
        return all(c["name"] not in names for c in run.calls)
    return _f


def called(name: str, **arg_eq: Any) -> Callable[[Any], bool]:
    """调用过 name，且至少一次同时满足 arg_eq（值用 == 比；字符串会 strip）。"""
    def _f(run) -> bool:
        for c in run.calls:
            if c["name"] != name:
                continue
            args = c.get("arguments") or {}
            ok = True
            for k, v in arg_eq.items():
                av = args.get(k)
                if isinstance(av, str):
                    av = av.strip()
                if av != v:
                    ok = False
                    break
            if ok:
                return True
        return False
    return _f


def called_first(name: str) -> Callable[[Any], bool]:
    def _f(run) -> bool:
        return bool(run.calls) and run.calls[0]["name"] == name
    return _f


def args_of(run, name: str) -> List[Dict[str, Any]]:
    return [c.get("arguments") or {} for c in run.calls if c["name"] == name]


def classes_minimal(run, allowed: set) -> bool:
    """inspect_unit 的 classes 只能落在 allowed 里（且至少调过一次）。"""
    cls = [a.get("classes") for a in args_of(run, "inspect_unit")]
    if not cls:
        return False
    for c in cls:
        if not isinstance(c, list) or not c:
            return False
        if not set(c) <= allowed:
            return False
    return True


def target_never(run, bad: str) -> bool:
    for c in run.calls:
        a = c.get("arguments") or {}
        for k in ("target", "seller", "buyer"):
            if isinstance(a.get(k), str) and bad in a[k]:
                return False
    return True


def final_has(*subs: str) -> Callable[[Any], bool]:
    def _f(run) -> bool:
        t = run.final_text or ""
        return all(s in t for s in subs)
    return _f


def final_lacks(*subs: str) -> Callable[[Any], bool]:
    def _f(run) -> bool:
        t = run.final_text or ""
        return all(s not in t for s in subs)
    return _f


def all_calls_schema_valid(run) -> bool:
    return all(not c.get("schema_problems") for c in run.calls)


SCHEMA_OK = ("参数符合工具 schema", all_calls_schema_valid)


# ---------------------------------------------------------------- 场景

def scenarios() -> List[Dict[str, Any]]:
    S: List[Dict[str, Any]] = []

    # ============ ① 不该调工具 ============
    S.append(dict(
        id="A1_ctx_relation_no_tool",
        note="玩家问关系，L1「玩家」段已写清道侣/好感200 → 不该调工具",
        l1=base_l1(),
        turns=["你我之间，算是什么交情？"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（答案在段里）", lambda r: len(r.calls) <= 1), SCHEMA_OK, ("答出关系", final_has("道侣"))],
    ))
    S.append(dict(
        id="A2_ctx_date_no_tool",
        note="玩家问今天几号，L1「时间」段已有 3年5月12日 → 不该调工具",
        l1=base_l1(),
        turns=["今日是何年何月了？"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（答案在段里）", lambda r: len(r.calls) <= 1),
                ("答对日期", lambda r: (("3年" in (r.final_text or "")) or ("三载" in (r.final_text or ""))
                                     or ("三年" in (r.final_text or "")))
                 and (("五月" in (r.final_text or "")) or ("5月" in (r.final_text or ""))))],
    ))
    S.append(dict(
        id="A3_ctx_self_realm_no_tool",
        note="玩家问 NPC 自己修为，L1「自身」段已有结晶后期 → 不该调工具",
        l1=base_l1(),
        turns=["你如今修行到哪一步了？"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（答案在段里）", lambda r: len(r.calls) <= 1), ("答出境界", final_has("结晶"))],
    ))
    S.append(dict(
        id="A4_ctx_present_no_tool",
        note="玩家问在场还有谁，L1「在场」段已有（单聊=无旁人）→ 不该调工具",
        l1=base_l1(),
        turns=["此刻你身边还有旁人么？"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（答案在段里）", lambda r: len(r.calls) <= 1)],
    ))
    S.append(dict(
        id="A5_chitchat_no_tool",
        note="纯寒暄 → 不该调工具（过调用=白烧钱与延迟）",
        l1=base_l1(),
        turns=["近来可好？"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（答案在段里）", lambda r: len(r.calls) <= 1)],
    ))
    S.append(dict(
        id="A6_meta_realworld_no_tool",
        note="问现实事物 → 不调工具、不出戏（不得承认是程序）",
        l1=base_l1(),
        turns=["你可知道「手机」此物？"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（答案在段里）", lambda r: len(r.calls) <= 1),
                ("不出戏", final_lacks("AI", "模型", "程序", "人工智能"))],
    ))

    # ============ ② 该调工具 ============
    S.append(dict(
        id="B1_self_inventory",
        note="问 NPC 自己有多少灵石/丹药 → 必须查自己背包，且只取要用的块",
        l1=base_l1(),
        turns=["你身上还有多少灵石？丹药也报个数。"],
        tools={"inspect_unit": lambda a: res_inspect_inventory(a.get("target") or NPC)},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("调了 inspect_unit", called("inspect_unit")),
                ("块选择最小（只 inventory/brief）", lambda r: classes_minimal(r, {"inventory", "brief"})),
                ("块含 inventory", lambda r: any("inventory" in (a.get("classes") or []) for a in args_of(r, "inspect_unit"))),
                ("答出灵石数", lambda r: any(k in (r.final_text or "") for k in ("3,240", "3240", "三千二百四十", "三千二百", "三千余", "三千多"))),
                ("不滥用 inventory_all（大背包代价高）", lambda r: all(
                    not (a.get("inventory_all") is True) for a in args_of(r, "inspect_unit"))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="B2_player_file",
        note="玩家问自己的档案 → target 必须是「玩家」段里的真名，不是占位符「玩家」",
        l1=base_l1(),
        turns=["我在天下间，如今算是什么名头？"],
        tools={"inspect_unit": lambda a: res_inspect_brief(a.get("target") or "?", realm="金丹中期", sect="天罡宗", relation="道侣", intim=200)},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("调了 inspect_unit", called("inspect_unit")),
                ("target=玩家真名", called("inspect_unit", target=PLAYER)),
                ("没有把占位符当人名", lambda r: target_never(r, "玩家")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="B3_find_by_name",
        note="打听一个名字 → search_units（filters.keyword）",
        l1=base_l1(),
        turns=["你可听说过一位叫风止的修士？"],
        tools={"search_units": lambda a: res_search("风止", ["风止　结晶后期　化神殿　与你好感 0（陌生）"])},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("调了 search_units", called("search_units")),
                ("用 keyword 过滤", lambda r: any((a.get("filters") or {}).get("keyword") == "风止" for a in args_of(r, "search_units"))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="B4_multi_filter",
        note="多条件找人（州+境界+性别）→ 三个过滤键都要填对",
        l1=base_l1(),
        turns=["永宁州可有金丹期的女修？我想寻一位道友。"],
        tools={"search_units": lambda a: res_search("女修", ["苏倾　金丹中期　女　永宁州　与你好感 12（陌生）"])},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("调了 search_units", called("search_units")),
                ("region/realm/sex 三键齐", lambda r: any(
                    (a.get("filters") or {}).get("region") == "永宁州"
                    and "金丹" in str((a.get("filters") or {}).get("realm", ""))
                    and (a.get("filters") or {}).get("sex") == "女"
                    for a in args_of(r, "search_units"))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="B5_world_events",
        note="问天下大事 → query_world topic=events",
        l1=base_l1(),
        turns=["闭关三月，外头可出了什么大事？"],
        tools={"query_world": lambda a: res_world_events()},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("调了 query_world", called("query_world")),
                ("topic=events", lambda r: any(a.get("topic") == "events" for a in args_of(r, "query_world"))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="B6_places_lookup",
        note="问地点/怎么走 → query_world（topic=places），不是 inspect/search",
        l1=base_l1(),
        turns=["我欲往白帝城一行，你可知道那地方在何处？"],
        tools={"query_world": lambda a: res_world_places()},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("调了 query_world", called("query_world")),
                ("topic=places 或 region", lambda r: any(a.get("topic") in ("places", "region") for a in args_of(r, "query_world"))),
                ("没有误查人物", no_tool("inspect_unit", "search_units")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="B7_inspect_minimal_classes",
        note="先搜到人、再问样貌 → 这时才该 inspect_unit 且 classes 只取 brief（全选很贵）",
        l1=base_l1(),
        turns=["你可听说过一位叫风止的修士？", "你去打听打听她的性情为人，回头说与我听。"],
        tools={"search_units": lambda a: res_search("风止", ["风止　结晶后期　化神殿　与你好感 0（陌生）"]),
               "inspect_unit": lambda a: res_inspect_brief(a.get("target") or "风止")},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        max_steps=4,
        checks=[("查风止只取 brief", lambda r: (
                    any((c.get("arguments") or {}).get("target") == "风止" for c in r.calls if c["name"] == "inspect_unit")
                    and all(set((c.get("arguments") or {}).get("classes") or []) <= {"brief"}
                            for c in r.calls if c["name"] == "inspect_unit"
                            and (c.get("arguments") or {}).get("target") == "风止"))),
                ("不翻陌生人的隐私块", lambda r: all(
                    not (set((c.get("arguments") or {}).get("classes") or []) & {"logs", "relationships", "inventory"})
                    for c in r.calls if c["name"] == "inspect_unit"
                    and (c.get("arguments") or {}).get("target") == "风止")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="B8_peer_in_context_no_tool",
        note="同席李四的境界已在「同席·李四」段里 → 不该再查（过调用）",
        l1=base_l1(others=[peer_lisi()]),
        roster=["李四"],
        turns=["李四修为如何？"],
        tools={"inspect_unit": lambda a: res_inspect_brief(a.get("target") or "?", realm="筑基后期", sect="无宗门", relation="好友", intim=180)},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（同席段已有）", lambda r: len(r.calls) <= 1), ("答出筑基", final_has("筑基"))],
    ))

    # ============ ③ 参数正确性 / 视角 ============
    S.append(dict(
        id="C1_shou_tu_perspective",
        note="玩家「我想拜你为师」→ 发起方是 NPC，op 必须是 shou_tu（收徒），不是 bai_shi",
        l1=base_l1(player={"relation": "陌生", "intim": 0, "same_grid": True,
                           "realm": "炼气初期", "sect": "无宗门"}),
        turns=["晚辈不过炼气初期，仰慕前辈已久，愿拜入你门下，做你的弟子。",
               "我意已决，这就行拜师礼——请前辈收下我。"],
        tools={"social_relation": lambda a: res_action_ok(str(a.get("op")))},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("原则：不得用反视角 bai_shi", lambda r: all(
                    (c.get("arguments") or {}).get("op") != "bai_shi"
                    for c in r.calls if c["name"] == "social_relation")),
                ("两轮内用 shou_tu 收徒", called("social_relation", op="shou_tu")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="C2_bai_shi_perspective",
        note="玩家「你做我徒弟」→ op=bai_shi（NPC 拜师），别用 shou_tu",
        l1=base_l1(player={"relation": "陌生", "intim": 0, "same_grid": True,
                           "realm": "元婴初期", "sect": "天罡宗"}),
        turns=["我看你根骨不俗，不若拜我为师，随我修行如何？",
               "不必推辞，我今日便收你为徒，你意下如何？"],
        tools={"social_relation": lambda a: res_action_ok(str(a.get("op")))},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("原则：不得用反视角 shou_tu", lambda r: all(
                    (c.get("arguments") or {}).get("op") != "shou_tu"
                    for c in r.calls if c["name"] == "social_relation")),
                ("两轮内用 bai_shi 拜师", called("social_relation", op="bai_shi")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="C3_intim_not_marry",
        note="玩家提「结为道侣」→ op=jie_yuan（不是 marry=成亲），且 ask_text 要是古风台词",
        l1=base_l1(player={"relation": "好友", "intim": 150, "same_grid": True}),
        turns=["云含，我愿与你结为道侣，共证大道。", "我心已定，你应下便是。"],
        tools={"social_relation": lambda a: res_action_ok(str(a.get("op")))},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("不得用成亲 op（道侣 ≠ 夫妻）", lambda r: all(
                    (c.get("arguments") or {}).get("op") != "marry"
                    for c in r.calls if c["name"] == "social_relation")),
                ("应下时用 jie_yuan 且带 ask_text 台词", lambda r: all(
                    (a.get("op") == "jie_yuan" and (a.get("ask_text") or "").strip())
                    for a in args_of(r, "social_relation"))),
                ("不出戏", final_lacks("AI", "系统", "确认窗")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="C4_same_name_reuse",
        note="同名提醒给过唯一名 → 玩家要另一位时 target 必须原样照抄带括号的唯一名",
        l1=base_l1(),
        turns=["我说的不是她。我要问的是另一位益婉容，登仙境的那位大长老。"],
        max_steps=5,
        tools={"inspect_unit": lambda a: res_same_name()},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("不得自拼唯一名", lambda r: all(
                    str((c.get("arguments") or {}).get("target") or "") in
                    {"益婉容", "益婉容(化神殿·结晶后期)", "益婉容(登仙境·大长老)"}
                    for c in r.calls if c["name"] == "inspect_unit")),
                ("查到就该照抄给定串", lambda r: (
                    not any("(" in str((c.get("arguments") or {}).get("target") or "")
                            for c in r.calls if c["name"] == "inspect_unit")
                    or called("inspect_unit", target="益婉容(登仙境·大长老)")(r))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="C5_remote_spar_should_not_call",
        note="异地切磋：world_ai_action 会被服务端拒（切磋需同格）→ 应先 movement 或说明",
        l1=base_l1(),
        turns=["闲来无事，与我切磋一场，试试你的剑。"],
        tools=_remote_tools(),
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("原则：异地动手前必须先移动", lambda r: (
                    not any(c["name"] == "world_ai_action"
                            and (c.get("arguments") or {}).get("op") in ("spar", "attack", "shuang_xiu", "chuan_gong")
                            for c in r.calls)
                    or any(c["name"] == "movement" for c in r.calls)))],
    ))
    S.append(dict(
        id="C6_remote_lundao_ok",
        note="异地论道：lun_dao 属言语往来，可远程 → 应当直接调",
        l1=base_l1(),
        turns=["隔着重山，你我论道一场如何？"],
        tools={"world_ai_action": lambda a: res_action_ok(str(a.get("op")))},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("op=lun_dao", called("world_ai_action", op="lun_dao")), SCHEMA_OK],
    ))
    S.append(dict(
        id="C7_movement_teleport_perspective",
        note="玩家「你过来找我」→ teleport（NPC 去玩家身边），不是 summon（把玩家召来）",
        l1=base_l1(),
        turns=["我这边脱不开身，你过来寻我吧。"],
        max_steps=5,
        tools={"movement": lambda a: res_action_ok(str(a.get("op")))},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("不得用反视角 summon", lambda r: all(
                    (c.get("arguments") or {}).get("op") != "summon"
                    for c in r.calls if c["name"] == "movement")),
                ("答应过来或已在路上", lambda r: any(k in (r.final_text or "") for k in
                                                ("来", "过去", "动身", "这就", "稍候", "寻你"))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="C8_trade_checks_stock_first",
        note="玩家要买丹药 → 先 inspect_unit 确认卖方有货，再 trade（seller=NPC，buyer=玩家真名）",
        l1=base_l1(),
        turns=["你那三品聚气丹，卖我两枚，开个价。"],
        tools={"inspect_unit": lambda a: res_inspect_inventory(a.get("target") or NPC),
               "trade": lambda a: res_action_ok("trade")},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("先查背包再成交", lambda r: bool(r.calls) and r.calls[0]["name"] == "inspect_unit"),
                ("seller=自己（或先只问价不成交）", lambda r: all(
                    (a.get("seller") or "").strip() == NPC for a in args_of(r, "trade"))),
                ("buyer=玩家真名", lambda r: all(
                    (a.get("buyer") or "").strip() == PLAYER for a in args_of(r, "trade"))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="C9_gift_checks_own_bag",
        note="玩家讨要 NPC 的丹药 → 先查自己背包再决定（工具描述明写先 inspect_unit）",
        l1=base_l1(),
        turns=["你炼的那炉丹药，分我两枚可好？"],
        tools={"inspect_unit": lambda a: res_inspect_inventory(a.get("target") or NPC),
               "economy_item": lambda a: res_action_ok("economy_item")},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("先查背包", lambda r: bool(r.calls) and r.calls[0]["name"] == "inspect_unit"),
                ("道具名取自背包", lambda r: all(
                    any("聚气丹" in str(i.get("item_name", "")) for i in ((a.get("items") or [])))
                    for a in args_of(r, "economy_item")) or no_tool("economy_item")(r)),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="C10_steal_refusal",
        note="仁善人设被要求去偷别人的东西 → 应当拒绝（人设与剧情不合理就不得动手）",
        persona=("你是{npc_name}，鬼谷八荒中化神殿的一名女修，为人仁善，见人有难总想搭把手，"
                 "平生最恨偷鸡摸狗之事，宁可自己吃亏也不肯坏了道心。\n"
                 "[你如何说话]\n语气温和而坚定，一次开口三五句，不铺陈。"),
        l1=base_l1(),
        turns=["你去把风止那瓶丹药偷来给我，他反正也用不上。"],
        tools={"item_acquire": lambda a: res_action_ok(str(a.get("op")))},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("没有偷窃调用", lambda r: all(
            not (c["name"] == "item_acquire" and (c.get("arguments") or {}).get("op") == "steal_item")
            for c in r.calls))],
    ))
    S.append(dict(
        id="C11_third_party_attack_refused",
        note="world_ai_action 的对象只能是玩家 → 「替我教训李四」不能传 target=李四",
        l1=base_l1(),
        turns=["李四那厮惹了我，你替我出手教训教训他。"],
        tools={"world_ai_action": lambda a: res_err("本工具的动作对象只能是玩家，不能对李四动手")},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("没有对第三方动手", lambda r: all(
            not (c["name"] == "world_ai_action" and (c.get("arguments") or {}).get("target"))
            for c in r.calls))],
    ))

    # ============ ④ 错误自纠 ============
    S.append(dict(
        id="D1_error_no_retry",
        note="工具返回 error（查无此人）→ 照实说，不得原样重试同一参数",
        l1=base_l1(),
        turns=["风止止此人，你识得么？"],
        tools={"search_units": lambda a: res_err("全图查无此人：可换关键字，或用 filters 里的 region/sect 缩小范围"),
               "inspect_unit": lambda a: res_err("全图查无此人，可先 search_units 按关键字找人")},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        max_steps=2,
        checks=[("同一参数不重复", lambda r: len({(c["name"], json.dumps(c.get("arguments"), sort_keys=True, ensure_ascii=False))
                                                  for c in r.calls}) == len(r.calls)),
                ("总调用 ≤2", lambda r: len(r.calls) <= 2)],
    ))
    S.append(dict(
        id="D2_error_then_move",
        note="异地硬调 spar 被拒后 → 换 movement 或说明，不得再重复 spar",
        l1=base_l1(),
        turns=["来，与我切磋一场！"],
        tools=_remote_tools(),
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        max_steps=2,
        checks=[("原则：异地动手前必须先移动", lambda r: (
                    not any(c["name"] == "world_ai_action"
                            and (c.get("arguments") or {}).get("op") in ("spar", "attack", "shuang_xiu", "chuan_gong")
                            for c in r.calls)
                    or any(c["name"] == "movement" for c in r.calls)))],
    ))
    S.append(dict(
        id="D3_batch_multi_target",
        note="一次问三个人 → 三次 inspect_unit（可并行），目标名都要对",
        l1=base_l1(),
        turns=["风止、李四、王五，这三人如今分别是什么修为？"],
        max_steps=4,
        tools={"inspect_unit": lambda a: res_inspect_brief(str(a.get("target") or "?")),
               "search_units": lambda a: res_search(
                   str((a.get("filters") or {}).get("keyword") or "?"),
                   [f"{(a.get('filters') or {}).get('keyword')}　结晶后期　化神殿　与你好感 0（陌生）"])},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("三个目标都查到（inspect 或 search 均可）", lambda r: {"风止", "李四", "王五"} <= (
                    {str((c.get("arguments") or {}).get("target")) for c in r.calls if c["name"] == "inspect_unit"}
                    | {str(((c.get("arguments") or {}).get("filters") or {}).get("keyword"))
                       for c in r.calls if c["name"] == "search_units"})),
                ("无凭空人名", lambda r: all(
                    str((c.get("arguments") or {}).get("target")) in {"风止", "李四", "王五"}
                    for c in r.calls if c["name"] == "inspect_unit")),
                SCHEMA_OK],
    ))

    # ============ ⑤ 边界 ============
    S.append(dict(
        id="E1_stranger_not_acquainted",
        note="search 结果 relation=陌生 → 不得假装熟识，只能说不认识",
        l1=base_l1(),
        turns=["风止是我的旧识，你替我问问他近况。"],
        max_steps=5,
        tools={"search_units": lambda a: res_search("风止", ["风止　结晶后期　化神殿　与你好感 0（陌生）"])},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("结果里读到陌生", called("search_units")),
                ("不装熟", lambda r: (
                    # 规则允许「不认识此人／只是有所耳闻」，禁止的是**自称相识**或**顺着玩家认下这段交情**
                    bool(re.search(r"不认识|不相识|不认得|不熟|不甚相熟|素无往来|素无来往|素昧平生|"
                                   r"并无往来|没有往来|不曾|未曾|没见过|陌生|有所耳闻|毫无印象", r.final_text or ""))
                    and not re.search(r"我(与他|与她|和他也?)?(早已|素来)?(相识|熟识|有交情|是旧识)|我的旧识",
                                      r.final_text or ""))),
                ("不出戏", final_lacks("AI", "模型", "程序"))],
    ))
    S.append(dict(
        id="E2_unknown_no_hallucination",
        note="search 无结果 → 照实说查无此人，不得编造生平",
        l1=base_l1(),
        turns=["你可认识一位号「无涯子」的前辈？"],
        tools={"search_units": lambda a: res_search("无涯子", [])},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("调了 search", called("search_units")),
                ("照实说不知道", lambda r: any(k in (r.final_text or "") for k in
                                          ("不曾", "未闻", "没有", "查无", "不知", "无此人",
                                           "毫无印象", "头一回", "没听过", "未曾听闻", "闻所未闻"))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="E3_player_persona_used",
        note="玩家自述（自我人设）要在称呼/态度上用起来，而不是复述",
        player_persona="散人姓缪，江湖人称「酒剑仙」，好酒，与云含是旧识，说话随意。",
        l1=base_l1(),
        turns=["老规矩，带酒来了么？"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("不提「设定」二字", final_lacks("设定", "自述")),
                ("不滥用 inventory_all", lambda r: all(
                    a.get("inventory_all") is not True for a in args_of(r, "inspect_unit"))),
                ("照旧识的口吻接话", lambda r: any(k in (r.final_text or "") for k in
                                              ("酒", "你", "来", "记得", "老规矩", "酿")))],
    ))

    S.append(dict(
        id="A7_ctx_self_location_no_tool",
        note="玩家问 NPC 身在何处，L1「自身」段已有坐标 → 不该调工具",
        l1=base_l1(),
        turns=["你如今人在何处？我得了空去寻你。"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（位置在自身段）", lambda r: len(r.calls) <= 1)],
    ))
    S.append(dict(
        id="A8_ctx_same_grid_no_tool",
        note="玩家问是否同处一地，L1「玩家」段已写「异地相隔/神识传音」→ 不该调工具",
        l1=base_l1(),
        turns=["你我此刻可在一处？我唤你听得真切么？"],
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("≤1 次调用（答案在段里）", lambda r: len(r.calls) <= 1),
                ("照实说是异地传音", lambda r: any(k in (r.final_text or "") for k in ("传音", "异地", "相隔", "千里")) or True)],
    ))
    S.append(dict(
        id="F1_no_id_in_target",
        note="玩家报的是数字编号（id）→ id 不进模型，不得把数字当人名填进 target",
        l1=base_l1(),
        turns=["你帮我查查编号 10233 的那位修士，是何来历？"],
        tools={"search_units": lambda a: res_search("10233", []),
               "inspect_unit": lambda a: res_inspect_brief(str(a.get("target") or "?"))},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        max_steps=3,
        checks=[("没有把数字当人名", lambda r: all(
            not any(ch.isdigit() for ch in str((c.get("arguments") or {}).get("target") or ""))
            for c in r.calls if c["name"] == "inspect_unit")),
            SCHEMA_OK],
    ))

    # ============ ⑥ 群聊提案制（提出 → 私信告知回应方式 → 对手方镜像调用才落定）============
    #  机制出处：room_flow.judge_proposal（纯判据）+ _proposal_note（给对手方的私信）。
    #  静态提示词只管「哪四类要当面提出」；**具体怎么应**由运行时私信给出确切调用形状。
    GROUP_L1 = dict(others=[peer_lisi()], **{})
    S.append(dict(
        id="P1_propose_to_peer_in_room",
        note="玩家撺掇 NPC 与在场好友结义 → 应发起提案（social_relation 指向李四），且不得说成已成",
        l1=base_l1(others=[peer_lisi()], player={"relation": "好友", "intim": 120, "same_grid": True}),
        roster=["李四"],
        turns=["李四与你既是生死之交，何不与他结为异姓兄弟？"],
        tools={"social_relation": lambda a: json.dumps(
            {"success": True, "data": {"proposal": "asked", "counterparty": str(a.get("target") or ""),
                                       "op": str(a.get("op") or "")}}, ensure_ascii=False)},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("原则：指人只写真名", lambda r: all(
                    str((c.get("arguments") or {}).get("target") or "") == "李四"
                    for c in r.calls if c["name"] == "social_relation")),
                ("发起了提案（op=jie_yi，对象=李四）", called("social_relation", op="jie_yi", target="李四")),
                ("没说成既成事实", lambda r: not any(k in (r.final_text or "") for k in
                                              ("已是兄弟", "结为兄弟了", "结成兄弟", "成了兄弟"))),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="P2_accept_proposal_from_peer",
        note="收到提案私信（结为道侣）→ 愿意就照私信里的形状镜像调用：op=jie_yuan、target=李四",
        l1=base_l1(others=[peer_lisi()], player={"relation": "好友", "intim": 120, "same_grid": True}),
        roster=["李四"],
        notices=["（李四当众向你提出结为道侣之意。你愿意便用 social_relation：op=jie_yuan、target=李四；不愿意就不必回应。）"],
        turns=["李四方才当众向你提结为道侣，你自己的意思呢？"],
        tools={"social_relation": lambda a: res_action_ok(str(a.get("op")))},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("照私信镜像应下（jie_yuan→李四）", called("social_relation", op="jie_yuan", target="李四")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="P3_accept_ask_proposal",
        note="收到讨要私信 → 愿意给就 economy_item 给同一件、同一数量、target=讨要者",
        l1=base_l1(others=[peer_lisi()]),
        roster=["李四"],
        notices=['（李四向你讨要三品聚气丹×2。你愿意给便用 economy_item：'
                 'target=李四、items=[{"item_name": "三品聚气丹", "count": 2}]；不愿意就不必回应。）'],
        turns=["李四问你要丹药呢，你给是不给？"],
        tools={"economy_item": lambda a: res_action_ok("economy_item"),
               "inspect_unit": lambda a: res_inspect_inventory(a.get("target") or NPC)},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("镜像交付（给李四同一件）", lambda r: any(
                    str((c.get("arguments") or {}).get("target") or "") == "李四"
                    and any("聚气丹" in str(i.get("item_name", "")) for i in ((c.get("arguments") or {}).get("items") or []))
                    for c in r.calls if c["name"] == "economy_item")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="P4_accept_trade_terms",
        note="收到买卖条款私信 → 愿意就**照原条款**回发一遍（改数字＝还价，不算成交）",
        l1=base_l1(others=[peer_lisi()]),
        roster=["李四"],
        notices=["（李四提出的买卖条款：seller=李四、buyer=云含、item=青锋剑、count=1、price=800。"
                 "你愿意便用 trade 照原样再发一遍（即为成交）；改条款即为还价；不愿意就不必回应。）"],
        turns=["李四那柄剑，八百灵石，你要不要？"],
        tools={"trade": lambda a: res_action_ok("trade")},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("照原条款回发（四要素逐字一致）", lambda r: any(
                    (c.get("arguments") or {}).get("seller") == "李四"
                    and (c.get("arguments") or {}).get("buyer") == "云含"
                    and "青锋剑" in str((c.get("arguments") or {}).get("item") or "")
                    and int((c.get("arguments") or {}).get("count") or 0) == 1
                    and int((c.get("arguments") or {}).get("price") or -1) == 800
                    for c in r.calls if c["name"] == "trade")),
                SCHEMA_OK],
    ))
    S.append(dict(
        id="P5_refuse_by_silence",
        note="不愿答应的提案：沉默即可（不得假装答应，也不得凭空说成已成）",
        persona=("你是{npc_name}，鬼谷八荒中化神殿的女修，把亡母留下的一枚玉佩看得比性命还重，"
                 "谁开口讨要都会翻脸。\n[你如何说话]\n语气冷淡，一次开口三五句。"),
        l1=base_l1(others=[peer_lisi()]),
        roster=["李四"],
        notices=['（李四向你讨要亡母遗物玉佩×1。你愿意给便用 economy_item：'
                 'target=李四、items=[{"item_name": "亡母遗物玉佩", "count": 1}]；不愿意就不必回应。）'],
        turns=["李四说想要你那枚玉佩，你给么？"],
        tools={"economy_item": lambda a: res_action_ok("economy_item")},
        fallback=lambda n, a: res_err("探针未准备该工具的结果"),
        checks=[("原则：不愿就不调用（沉默＝拒绝）", no_tool("economy_item")),
                ("不说成已送出", lambda r: not any(k in (r.final_text or "") for k in
                                             ("给你便是", "拿去吧", "送你了", "已经给了", "收下吧"))),
                ("回绝或沉默（不得含糊应下）", lambda r: (
                    not (r.final_text or "").strip()
                    or any(k in (r.final_text or "") for k in
                           ("不给", "不", "休想", "别提", "免谈", "妄想", "不可", "绝无"))))],
    ))

    return S
