# -*- coding: utf-8 -*-
"""tools/catalog —— 对话窗右侧「可做的事」侧边栏的**目录**（10-19 用户需求）。

## 为什么要有这个文件

十个工具全在 `tools/schemas.py` 里，但**玩家看不见**。玩家不知道这个 mod 能做什么，
于是那些工具只能等着他碰巧说出某句恰好能触发的台词 —— 发现性为零。

侧边栏把「能做的事」摆出来：一行 = 一句人话 + 它背后那个工具。点一下 = 说出那句话。

## 三条硬约束（改这个文件之前先读）

★ ① **`say` 会永久留在账本里。**
    点击等同玩家手打，所以那串字会进 `.jsonl`、重开窗时以玩家气泡回放、之后每一轮
    都作为 `user` 消息喂给模型。因此它**必须是一句像人说的台词** ——
    绝不能出现「（请调用 economy_item）」「帮我执行赠送」这类元指令：
    一旦写了，那行字就永久污染这个 NPC 的记忆，而且回放时会以玩家的话暴露在屏幕上。
    `validate()` 里有一张词表专门拦这个。

★ ② **`schemas.py` 是契约的唯一住所，本文件只做展示。**
    工具名 / op 取值 / 必填参数，一个字都不在这里重新定义 —— 只引用、只对账。
    `tests/test_tool_catalog_contract.py` 逐条核对：`TOOL_ORDER` 里每个工具要么在这里
    出现，要么出现在 `UNLISTED` 里并写明理由（当前只有 `search_units`）。

★ ③ **`mode` 决定「点了会发生什么」，两种含义必须分清。**
    · `send` —— 这句话直接发出去（走玩家发言的同一条路：时间戳、死者拦截、
      `TurnRegistry`、渲染闸全都不用复制一遍）。
    · `fill` —— 这句话**不发**，只填进输入框、光标停在空位，玩家自己补齐再发。
      为什么经济三件必须 fill：`economy_item` 要 `items`、`item_acquire` 要
      `item_name`、`trade` 要 `item`（外加价）—— **没有任何一句预设台词能确定玩家
      要哪件东西**。让模型自己猜 = 送错东西，比不让点更糟。
      fill 让这三件被看见，代价是多点两下。

## 点击不是「执行」

十个工具的 description 里全写着「发起方恒为当前对话 NPC」。工具是 **NPC 的动作**，
不是玩家的按钮；模型仍然可以拒、可以讲条件、可以反问。所以这里是**引导**，不是命令。
（真要「点了必调用」得另加一条隐藏 plugin 指令，那会盖掉 NPC 的自主性 —— 刻意不做。）

## token 开销

本目录**不进 prompt**，每回合固定开销零增加。它只经 `list_tools` RPC 下发给 C# 渲染。
"""

from __future__ import annotations

from typing import Any, Dict, List

#: 空位标记：玩家点 fill 型条目后，光标落在这个字符处。
#: 用**全角空格 U+3000** 而不是下划线/花括号 —— 它在中文里是"留白"，看上去像排版而不是
#: 占位符，也不会和玩家可能真打的字符混淆。C# 侧 `ToolCatalogCore.FirstBlank` 认的就是它。
BLANK = "\u3000"


def _g(key: str, title: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"key": key, "title": title, "items": items}


def _i(key: str, title: str, tool: str, say: str, sub: str,
       arg: str = "op", argval: str = "", fills: str = "") -> Dict[str, Any]:
    """一条目录项。

    两个只供**对账**、不进下发的键（它们说明"这一行凭什么能触发那个工具"）：

    · `send` 型：`arg`/`argval` = 这句话会把模型推向哪个参数的哪个取值。
      缺省 `arg="op"`；`inspect_unit` / `query_world` 没有 `op` 参数，用的是
      `classes` / `topic` —— 若把判别值硬写进 `op`，对账就会红（那正是要它红的原因）。
    · `fill` 型：`fills` = 空位补的是哪个**必填参数**。`economy_item` 要 `items`、
      `item_acquire` 要 `item_name`、`trade` 要 `item` —— 这个键让"空位确实补在
      真正的必填项上"可被断言，而不是靠注释自觉。
    """
    is_fill = BLANK in say
    return {
        "key": key,
        "title": title,
        "sub": sub,
        "tool": tool,
        "arg": "" if is_fill else arg,
        "argval": "" if is_fill else argval,
        "fills": fills if is_fill else "",
        "mode": "fill" if is_fill else "send",
        "say": say,
    }


# ══════════════════════════════════════════════════════════════════
# 目录本体
#
# 分组顺序 = 「先看后动」：只读组最先（点了绝不惊动世界），财物组最后
# （三条都是 fill、需要玩家补齐，最费手）。
# ══════════════════════════════════════════════════════════════════

GROUPS: List[Dict[str, Any]] = [
    _g("look", "看", [
        _i("bag", "问他囊中何物", "inspect_unit",
           "你囊中都有些什么好东西？", "inspect_unit · 储物", "classes", "inventory"),
        _i("art", "问他所习功法", "inspect_unit",
           "你习的都是些什么功法？", "inspect_unit · 功法", "classes", "abilities"),
        _i("past", "听他说说往事", "inspect_unit",
           "这些年你都经历过些什么？", "inspect_unit · 经历", "classes", "logs"),
        _i("events", "近来天下大事", "query_world",
           "近来天下可有什么大事？", "query_world · events", "topic", "events"),
        _i("rank", "如今天下谁最强", "query_world",
           "如今天下，谁人战力最高？", "query_world · rankings", "topic", "rankings"),
        _i("sects", "宗门光景", "query_world",
           "你所在的宗门，如今光景如何？", "query_world · sects", "topic", "sects"),
    ]),
    _g("relation", "关系", [
        _i("intim", "亲近一些", "social_relation",
           "相识一场便是缘分，往后还望多走动。", "social_relation · add_intim", argval="add_intim"),
        _i("marry", "结为道侣", "social_relation",
           "这世间路远，你可愿与我结为道侣？", "social_relation · marry", argval="marry"),
        _i("jieyi", "义结金兰", "social_relation",
           "你我一见如故，不若结为金兰之交？", "social_relation · jie_yi", argval="jie_yi"),
        _i("shoutu", "请收我为徒", "social_relation",
           "我诚心求道，还望你收我为徒。", "social_relation · shou_tu", argval="shou_tu"),
        _i("baishi", "收他为徒", "social_relation",
           "你根基不差，可愿拜在我门下？", "social_relation · bai_shi", argval="bai_shi"),
    ]),
    _g("act", "互动", [
        _i("spar", "切磋一场", "world_ai_action",
           "久闻你身手了得，不如就在此处走上两招？", "world_ai_action · spar", argval="spar"),
        _i("lundao", "论道", "world_ai_action",
           "左右无事，不如你我论道一番？", "world_ai_action · lun_dao", argval="lun_dao"),
        _i("shuangxiu", "双修", "world_ai_action",
           "夜深人静，可愿与我共修一场？", "world_ai_action · shuang_xiu", argval="shuang_xiu"),
        _i("yaoyue", "邀约同游", "world_ai_action",
           "听说城外景致正好，改日同游一番如何？", "world_ai_action · yao_yue", argval="yao_yue"),
        _i("chuangong", "传功与我", "world_ai_action",
           "我卡在这一层久矣，你可愿传我一门功法？", "world_ai_action · chuan_gong",
           argval="chuan_gong"),
        # 宗门人事（10-29）：玩家只能**提议**，去不去/走不走由 NPC 自己判断 ——
        # 所以这两句写的是"问他想不想"，不是"命他去"（schema 的 reason 参数就是让他写动机）。
        _i("joinsect", "劝他投个宗门", "world_ai_action",
           "你这一身本事，总散着也不是办法，可曾想过投个宗门讨个出身？",
           "world_ai_action · join_sect", argval="join_sect"),
        _i("leavesect", "劝他另寻出路", "world_ai_action",
           "我看你在这门里过得并不痛快，可曾想过另寻出路？",
           "world_ai_action · leave_sect", argval="leave_sect"),
    ]),
    _g("move", "走动", [
        _i("summon", "召我过去", "movement",
           "你在何处？我这就过去寻你。", "movement · summon", argval="summon"),
        _i("teleport", "你过来一趟", "movement",
           "你若有空，过来一趟罢。", "movement · teleport", argval="teleport"),
        _i("teaminv", "邀他同行", "team",
           "此去凶险，可愿与我同行？", "team · invite", argval="invite"),
        _i("teamdis", "解散队伍", "team",
           "咱们结伴一场，今日散伙罢。", "team · dismiss", argval="dismiss"),
        _i("teamst", "查看队伍", "team",
           "如今队里都有谁？", "team · status", argval="status"),
    ]),
    # ★ 财物三条全是 fill（见文件头约束 ③）：按钮把半句话填进输入框，玩家补齐再发。
    _g("money", "财物", [
        _i("askfor", "讨要一件", "item_acquire",
           "你那儿的那" + BLANK + "，可否匀我一件？", "item_acquire · ask_for",
           fills="item_name"),
        _i("gift", "送我一物", "economy_item",
           "你那儿可有" + BLANK + "？赠我一件罢。", "economy_item",
           fills="items"),
        _i("trade", "做笔买卖", "trade",
           "我想买你手中的" + BLANK + "，你开个价罢。", "trade",
           fills="item"),
    ]),
]

#: 工具名 → 该工具在目录里**不出现**的理由（空串 = 必须出现）。
#: 契约测试按 `TOOL_ORDER` 逐条查：既不出现、也不在这里，就红。
UNLISTED: Dict[str, str] = {
    "search_units": (
        "必填参数 filters 是一个过滤字典（姓名/关系/境界/宗门/地区/种族/性别）。"
        "没有任何一句预设台词能把它填对 —— 摆一个点下去只会返回「全图好感最高的十个人」的按钮，"
        "等于教玩家「这个按钮没用」。留给人自己描述要找谁。"
    ),
}

#: 元指令词表：`say` 里出现任何一个即视为「写给模型看的」而不是「玩家说的话」。
#: 判据收得很窄 —— 只收**正常玩家台词里绝不会出现**的词。
#:
#: ★ 10-19 补 `执行 / 帮我调 / 触发 / 走一遍`：反例表里「帮我执行 trade」最初**漏网**
#:   （词表只有"调用/工具/函数"）。这四个词同样的病 —— 玩家不会说"帮我执行"，但被
#:   产品经理写目录的人会。宁可多拦一次（改个说法即可），也不要让它躺进账本。
META_WORDS = (
    "调用", "工具", "函数", "schema", "参数", "系统提示", "提示词",
    "prompt", "tool", "assistant", "agent", "模型",
    "执行", "触发", "帮我调",
)


# ══════════════════════════════════════════════════════════════════
# 下发与自检
# ══════════════════════════════════════════════════════════════════

def payload() -> Dict[str, Any]:
    """给 C# 的纯展示数据（`list_tools` RPC 的 response.data）。

    只发 C# 用得上的键 —— `sub` 保留是为了让侧边栏小字能标出「背后是哪个工具」
    （用户要的「工具展示」就是这个）。C# **不据此做任何路由判断**，它只是字。
    """
    return {
        "groups": [
            {
                "key": g["key"],
                "title": g["title"],
                "items": [
                    {
                        "key": it["key"],
                        "title": it["title"],
                        "sub": it["sub"],
                        "mode": it["mode"],
                        "say": it["say"],
                    }
                    for it in g["items"]
                ],
            }
            for g in GROUPS
        ]
    }


def rows() -> List[Dict[str, Any]]:
    """摊平的全部条目（测试与统计用）。"""
    out: List[Dict[str, Any]] = []
    for g in GROUPS:
        out.extend(g["items"])
    return out


def _enum_of(spec: Dict[str, Any]) -> List[str]:
    """参数的合法取值：数组型看 `items.enum`，其余看 `enum`。"""
    if not isinstance(spec, dict):
        return []
    if spec.get("enum"):
        return list(spec["enum"])
    it = spec.get("items")
    if isinstance(it, dict) and it.get("enum"):
        return list(it["enum"])
    return []


def validate(schema_getter=None) -> List[str]:
    """自检：返回问题列表（空 = 合格）。

    `schema_getter(name)` 返回 `tools/schemas.py` 里那个工具的 schema dict（可选）。
    给了就顺带核对判别值是否真在该参数的合法取值里 —— 这是「目录与契约漂移」的唯一探针：
    有人改了 `schemas.py` 的枚举而没改这里，点击就会发出一句**永远失败**的话。
    """
    problems: List[str] = []
    seen_group: set = set()
    seen_key: set = set()
    for g in GROUPS:
        gk = g.get("key") or ""
        if not gk:
            problems.append("分组缺 key")
        elif gk in seen_group:
            problems.append("分组 key 重复: " + gk)
        seen_group.add(gk)
        if not (g.get("title") or "").strip():
            problems.append("分组 " + gk + " 缺 title")
        if not g.get("items"):
            problems.append("分组 " + gk + " 是空的")
        for it in g.get("items") or []:
            k = it.get("key") or ""
            if not k:
                problems.append("条目缺 key（分组 " + gk + "）")
            elif k in seen_key:
                problems.append("条目 key 重复: " + k)
            seen_key.add(k)
            where = "条目 " + (k or "?") + "（分组 " + gk + "）"
            say = it.get("say") or ""
            if not (it.get("title") or "").strip():
                problems.append(where + " 缺 title")
            if len(say) < 4:
                problems.append(where + " 的 say 太短")
            if len(say) > 40:
                problems.append(where + " 的 say 太长（%d 字），账本里会是一大段" % len(say))
            if say.lstrip().startswith("/"):
                problems.append(where + " 的 say 以 / 开头 —— 会被当命令拦截")
            low = say.lower()
            for w in META_WORDS:
                if w.lower() in low:
                    problems.append(where + " 的 say 含元指令词「%s」—— 那行字会永久留在账本里" % w)
                    break
            mode = it.get("mode")
            want = "fill" if BLANK in say else "send"
            if mode != want:
                problems.append(where + " 的 mode=%r 与 say 是否含空位不符（应为 %r）" % (mode, want))
            if not (it.get("tool") or "").strip():
                problems.append(where + " 缺 tool")
            if not (it.get("sub") or "").strip():
                problems.append(where + " 缺 sub（侧边栏小字就是它）")
            if schema_getter is not None:
                name = it.get("tool") or ""
                schema = schema_getter(name)
                if schema is None:
                    problems.append(where + " 的 tool=%r 在 schemas 里不存在" % name)
                    continue
                params = (schema.get("function") or {}).get("parameters") or {}
                props = params.get("properties") or {}
                required = list(params.get("required") or [])
                if it.get("mode") == "fill":
                    filled = it.get("fills") or ""
                    if not filled:
                        problems.append(where + " 是 fill 型，但没写 fills（空位补的是哪个参数）")
                    elif filled not in props:
                        problems.append(where + " 的 fills=%r 不是 %s 的参数（有：%s）"
                                        % (filled, name, "/".join(sorted(props))))
                    continue
                arg = it.get("arg") or "op"
                argval = it.get("argval") or ""
                spec = props.get(arg)
                if not isinstance(spec, dict):
                    problems.append(where + " 说要用参数 %r，但 %s 没有这个参数" % (arg, name))
                    continue
                enum = _enum_of(spec)
                if enum and argval not in enum:
                    problems.append(where + " 的 %s=%r 不在合法取值里（%s）"
                                    % (arg, argval, "/".join(enum)))
                if not enum and not argval:
                    problems.append(where + " 的 %s 没有取值可对账" % arg)
    return problems


def coverage_problems(tool_order: List[str]) -> List[str]:
    """目录 ↔ `TOOL_ORDER` 的对账：每个工具要么列出、要么在 `UNLISTED` 里写明理由。"""
    have = {it.get("tool") for it in rows()}
    problems: List[str] = []
    for name in tool_order:
        if name in have:
            continue
        if not (UNLISTED.get(name) or "").strip():
            problems.append("工具 %s 既不在目录里，也没在 UNLISTED 里写明理由" % name)
    for name in sorted(have):
        if name not in tool_order:
            problems.append("目录里的 %s 在 TOOL_ORDER 里不存在" % name)
    return problems


def fill_hint(say: str) -> str:
    """fill 型条目的提示语（填进输入框前显示一行系统提示用）。空串 = 不是 fill。"""
    if BLANK not in (say or ""):
        return ""
    return "把空处补上再发送"


__all__ = ["BLANK", "GROUPS", "UNLISTED", "META_WORDS", "payload", "rows", "validate",
           "coverage_problems", "fill_hint"]
