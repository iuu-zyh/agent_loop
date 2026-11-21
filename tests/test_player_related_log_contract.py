"""「与玩家相关」经历 · 源码契约（09-21）

需求：游戏 NPC 面板·经历页有一个「与我相关」页签（LocalText id=23183
`key=npcInfo_yuwoxiangguan`，实现是 `UINPCInfoLog.GetLogWithMe(List<LogItemData>)`），
用户要求把这份"与玩家有关的经历"也喂给模型 —— ① L1 新增独立段「与玩家相关」；
② `inspect_unit` 的 `log_filter` 新增 `with_me`。

**判定必须发生在 CleanLogText 之前** —— 这是本契约最重要的一条：
`LogItemText → TryDataString → CleanLogText` 会把 `@q_唐炎|BOlQu6@` 渲染成 `唐炎`，
unitID 在那一刻蒸发；渲染之后再判就只剩名字了。而**按名字判是错的**：
本作存在与玩家同名的 NPC（UnitLookup 09-12 实锤 `Resolve('濮阳彤萱') → unitID=fzppHw`），
按名匹配会把别人的经历算到玩家头上。

真机铁证（Player.log）：`[Logs] @标记[人物] @q_唐炎|BOlQu6@ → 唐炎`，
`BOlQu6` 即玩家（`load_happened world=BOlQu6` / `portrait_cache\\BOlQu6.png` /
`玩家名="唐炎"` 三处互证）。
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_loop.agent.system_prompt import SystemPrompt
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "csharp" / "game" / "UnitSnapshot.cs"
GAME = ROOT / "csharp" / "game" / "GameContext.cs"
TOOLEX = _cs_tools.TOOL_SRC
SP = ROOT / "agent" / "system_prompt.py"
SCHEMAS = ROOT / "tools" / "schemas.py"
RENDER = ROOT / "tools" / "text_render.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _body(src: str, signature: str) -> str:
    """按大括号配平截方法体（这几个方法体内没有含花括号的字符串字面量）。"""
    i = src.index(signature)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j: k + 1]
        k += 1
    raise AssertionError(f"方法体未闭合：{signature}")


def _strip_comments(src: str) -> str:
    """去掉 // 行注释与 /* */ 块注释，只留代码 —— 判据看代码不看注释。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


# ---------------------------------------------------------------------------
# ① 判据的位置与形态
# ---------------------------------------------------------------------------

def test_judgement_happens_before_cleantext():
    """判定必须在 CleanLogText 之前：MentionsPlayer 只许走原始 GetLogString/DataToString/values，
    绝不许复用 LogItemText（那是已渲染文本，unitID 已被 RenderAtTag 换成人话）。"""
    src = _strip_comments(_read(UNIT))
    body = _body(src, "private static bool MentionsPlayer(object item, string pid)")
    assert "DataListHasPid" in body, "MentionsPlayer 没有走原始数据判定路径"
    assert "LogItemText(" not in body, (
        "MentionsPlayer 复用了 LogItemText —— 那是 CleanLogText 之后的渲染文本，"
        "unitID 已被换成人话，判不出玩家引用（回退成了按名匹配）")
    raw = _body(src, "private static string RawDataText(object d)")
    assert "GetLogString" in raw and "DataToString" in raw, (
        "RawDataText 必须读未清洗原文（GetLogString 仍带 @ 标记 / DataToString 序列化）")
    # ★ 且不许做任何清洗/渲染（2026-09-21 补）：清洗会把 @q_名|unitID@ 里的 unitID 换成人话，
    #   判定路径当场断掉 —— 变异 A（RawDataText 里套一层 CleanLogText）在只有上面那条断言时
    #   是漏网鱼：GetLogString/DataToString 字样都还在，字面断言抓不住"包了一层清洗"。
    assert "CleanLogText" not in raw and "LogItemText" not in raw, (
        "RawDataText 不许调用 CleanLogText/LogItemText —— 那是渲染后的文本，"
        "unitID 已被换成人话，'与玩家相关'判定就断了（回退成只剩按名可判）")
    print("✓ 判定走原始层：MentionsPlayer 不经 CleanLogText，unitID 还在")


def test_never_matches_by_player_name():
    """★ 绝不许按玩家真名匹配 ★ —— 本作有与玩家同名的 NPC（UnitLookup 09-12 实锤），
    按名匹配会把别人的经历算到玩家头上。判据只许是 unitID 的字段级精确相等。"""
    src = _strip_comments(_read(UNIT))
    for sig in ("private static bool MentionsPlayer(object item, string pid)",
                "private static bool DataListHasPid(object list, string pid)",
                "private static bool FieldHasPid(string s, string pid)",
                "private static bool ValuesHasPid(object d, string pid)",
                "private static bool RawRowHasPid(object row, string pid)"):
        body = _body(src, sig)
        for banned in ("GetName()", "playerName", "PlayerName()", "pname"):
            assert banned not in body, f"{sig} 里出现了按名判定（{banned}）—— 会张冠李戴"
    field = _body(src, "private static bool FieldHasPid(string s, string pid)")
    assert re.search(r"field\s*==\s*pid", field), (
        "FieldHasPid 必须做**字段级精确相等**；Contains 子串会把别人的 unitID 误判成玩家")
    print("✓ 判据只用 unitID 字段级相等，不碰玩家真名")


def test_empty_player_id_is_never_a_match():
    """pid 取不到（未进世界 / 身份未就绪）必须一律 false —— 宁缺勿错，不许把空串当通配符。"""
    src = _strip_comments(_read(UNIT))
    m = _body(src, "private static bool MentionsPlayer(object item, string pid)")
    assert re.search(r"IsNullOrEmpty\(pid\)\s*\|\|\s*item\s*==\s*null", m), (
        "MentionsPlayer 没有先拦空 pid —— 空串会让所有条目都命中")
    f = _body(src, "private static bool FieldHasPid(string s, string pid)")
    assert "IsNullOrEmpty(pid)" in f, "FieldHasPid 没有拦空 pid"
    r = _body(src, "private static bool RawRowHasPid(object row, string pid)")
    assert "IsNullOrEmpty(pid)" in r, "RawRowHasPid 没有拦空 pid"
    print("✓ 空 pid 一律 false（不猜）")


def test_logsarr_marks_entries():
    """LogsArr 必须给命中条目写 with_player 键（解码层与原始层都要），且不命中不写键。"""
    src = _strip_comments(_read(UNIT))
    body = _body(src, "private static JArray LogsArr(object list, bool raw = false)")
    assert "MentionsPlayer(item, pid)" in body, "LogsArr 解码层没有调 MentionsPlayer 打标"
    assert "RawRowHasPid(item, pid)" in body, "LogsArr 原始层（raw 兜底）没有打标"
    assert body.count('jo["with_player"] = true') == 2, (
        "两条路径都应在命中时写 with_player=true")
    assert 'jo["with_player"] = false' not in body, (
        "不命中不该写 false 键 —— 缺键即 false，写 false 会让 48 条条目每条都多一个键")
    # 暂存层第三路也要打标，否则"本月刚写、未落库"的玩家往来会漏
    pend = src[src.index("GetCacheWriteLog(unitID);"):]
    assert "MentionsPlayer(item, pidP)" in pend, "暂存层路径没有打 with_player 标"
    print("✓ LogsArr 解码层/原始层/暂存层三路都打 with_player 标")


def test_with_player_is_only_writable_in_unitsnapshot():
    """with_player 这个键只许在 UnitSnapshot（取数处）写一次 —— ToolExecutor 只许读不许写，
    否则"判定发生在 CleanLogText 之前"这条铁律就有旁路。"""
    us = _read(UNIT)
    assert us.count('"with_player"') >= 2, "UnitSnapshot 没有写 with_player"
    te = _strip_comments(_read(TOOLEX))
    assert te.count('"with_player"') == 1, (
        "ToolExecutor 里 with_player 出现次数异常 —— 它只许在 PlayerHit 里读一次")
    ph = _body(te, "bool PlayerHit(JToken it)")
    assert "== true" in ph, "PlayerHit 没有按布尔读键"
    print("✓ with_player 只在取数处写一次，消费处只读")


# ---------------------------------------------------------------------------
# ② L1 独立段
# ---------------------------------------------------------------------------

def test_recent_with_player_is_independent_segment():
    """L1 独立段「与玩家相关」：先知道对面是谁、我们在哪，再读我们之间发生过什么。

    顺序仍是三段论（09-26 起气运两段加入，各自紧邻主体）：
    self → npc_luck → player → player_luck → where（我们在哪、身边还有谁）
    → recent_with_player（我们之间的事）。
    """
    # ★ 10-25 第四层拆分：新增 `npc_vital` / `player_vital`（状态段）★
    #   顺序：自身 → 后天 → 自身状态；玩家 → 玩家后天 → 玩家状态。
    #   为什么要加它们：实测「自身」占 L1 总字数 25%、均长 287 字，而相邻两次的差异
    #   只有"战力 7048→6992"这类数字 ⇒ 会变的部分独立成段，变化的代价降到几十字。
    assert SystemPrompt._CTX_SEGMENTS == (
        "time", "self", "npc_luck", "npc_vital", "player", "player_luck",
        "player_vital", "where", "recent_with_player", "recent", "action"), (
        f"段清单不对：{SystemPrompt._CTX_SEGMENTS}")
    assert SystemPrompt._CTX_SEGMENTS.index("npc_luck") \
        == SystemPrompt._CTX_SEGMENTS.index("self") + 1, "「气运」必须紧跟「自身」之后"
    assert SystemPrompt._CTX_SEGMENTS.index("npc_vital") \
        == SystemPrompt._CTX_SEGMENTS.index("npc_luck") + 1, "「自身状态」必须紧跟「后天气运」之后"
    assert SystemPrompt._CTX_SEGMENTS.index("player_luck") \
        == SystemPrompt._CTX_SEGMENTS.index("player") + 1, "「玩家气运」必须紧跟「玩家」之后"
    assert SystemPrompt._CTX_SEGMENTS.index("player_vital") \
        == SystemPrompt._CTX_SEGMENTS.index("player_luck") + 1, "「玩家状态」必须紧跟「玩家后天气运」"
    assert SystemPrompt._CTX_SEGMENTS.index("where") \
        == SystemPrompt._CTX_SEGMENTS.index("player_vital") + 1, "「所在」必须紧跟「玩家状态」之后"
    assert SystemPrompt._CTX_SEGMENTS.index("recent_with_player") \
        == SystemPrompt._CTX_SEGMENTS.index("where") + 1, "「与玩家相关」必须紧跟「所在」之后"
    assert SystemPrompt._CTX_LABELS["recent_with_player"] == "与玩家相关"
    # 排序键也要登记，否则 assemble 的顺序会随字典插入序漂移（CTX_OTHER_ORDER_BASE=150 之间）
    o = SystemPrompt._CTX_ORDERS
    assert o["player"] < o["where"] < o["recent_with_player"] < o["recent"], (
        f"where / recent_with_player 排序键没落在 player 与 recent 之间：{o}")
    # ctx_segment_of_label 必须认得它 —— _restore_segment_from 从历史消息反推段名靠它
    assert SystemPrompt.ctx_segment_of_label("与玩家相关") == "recent_with_player"
    print("✓ L1 独立段「与玩家相关」已登记（清单/标签/排序/反查）")


def test_l1_reads_raw_key_and_stays_empty_when_absent():
    """format_l1_context 必须读 raw.recent_with_player；缺/空时成文为空串（整段不渲染）。"""
    src = _strip_comments(_read(SP))
    assert '"recent_with_player"' in src, "format_l1_context 没有产出 recent_with_player 键"
    assert 'raw.get("recent_with_player")' in src, "没有从 raw.recent_with_player 取数"
    # ★ 10-25 改：与「近况」一起走**增量成文**（只发新增条目）★
    #   原来是 `"；".join(i+1. t)` 全量重编 —— 新增一条就要整段重发（实测这两段合计占
    #   L1 总字数约 40%，是最大的一块）。编号/分隔符现在在 `_incr_render` 的 renderer 里。
    assert "wp_text = self._incr_render(" in src, \
        "「与玩家相关」没有走增量成文（会退回「一条新增=整段重发」）"
    assert '"recent_with_player": wp_text' in src, "没有把 wp_text 放进返回 dict"
    # 无新增时必须**保持上次文本**（空串会被判成 remove ⇒ 段从容器消失、UI 反复"已移除"）
    assert "return self._last" in (ROOT / "agent" / "l1_incremental.py").read_text(encoding="utf-8"), \
        "增量段在无新增/空候选时没有保持上次文本"
    # 段标签已是「与玩家相关」，正文不许再叠一遍同义词（只看赋值行，注释正当提及不算）
    code_line = next((ln for ln in src.splitlines()
                      if ln.strip().startswith("wp_text = self._incr_render(")), "")
    assert code_line and "「与玩家相关：」" not in code_line, "正文里又叠了一遍段标签（噪音）"
    print("✓ format_l1_context 消费 raw.recent_with_player，空段返回空串")


# ---------------------------------------------------------------------------
# ③ 工具 log_filter="with_me"
# ---------------------------------------------------------------------------

def test_tool_enum_has_with_me_and_csharp_handles_it():
    """schema 列了 with_me，C# 必须真的处理（否则模型传了走进 default 分支）。"""
    from agent_loop.tools import schemas as S
    enum = S._ALL_MAP["inspect_unit"]["function"]["parameters"]["properties"]["log_filter"]["enum"]
    assert "with_me" in enum, "schema 的 log_filter 没列 with_me"
    te = _strip_comments(_read(TOOLEX))
    pl = _body(te, " JObject PageLogs(")
    assert 'if (filter == "with_me") return true;' in pl, (
        "PageLogs 的 filterHit 没放行 with_me —— 两个桶都会被滤光，items 恒空")
    assert "PlayerHit(it)" in pl, "PageLogs 没有接条目级筛选 PlayerHit"
    ph = _body(te, "bool PlayerHit(JToken it)")
    assert 'filter != "with_me"' in ph, "PlayerHit 没有把 with_me 当开关（会误伤其它 filter）"
    print("✓ log_filter=with_me：schema ↔ C# 两侧一致")


def test_with_me_filters_out_unmarked_entries():
    """条目级筛选：with_me 只留 with_player=true。缺键（=false）必须被剔除 ——
    这是「与玩家相关」语义成立的根，不钉住就成了第二个 all。"""
    te = _strip_comments(_read(TOOLEX))
    ph = _body(te, "bool PlayerHit(JToken it)")
    assert "return false" in ph, "PlayerHit 缺键时必须返回 false（剔除），不许放行"
    print("✓ with_me 会剔除未打标条目（缺键 = 不相关）")


def test_renderer_knows_with_me():
    """渲染层必须给 with_me 配中文表头，且 ★ 图例照给（它跨两桶，重要件混在里面）。"""
    src = _strip_comments(_read(RENDER))
    assert '"with_me": "与我相关"' in src, "渲染层没有给 with_me 配中文表头"
    mi = re.search(r"mark_important\s*=\s*flt\s+in\s+\(\"all\",\s*\"with_me\"\)", src)
    assert mi, "with_me 没有按混流处理 ★ 图例（重要/常规是两条流，并集后只有 tier 能区分）"
    print("✓ 渲染层认得 with_me：表头「与我相关」+ ★ 图例")


# ---------------------------------------------------------------------------
# ④ C# 侧的取数与常量
# ---------------------------------------------------------------------------

def test_gamecontext_emits_recent_with_player():
    """GameContext.GetL1 必须真的发 raw.recent_with_player —— 渲染层读的键 C# 不发 = 模型永远看不到
    （本仓已踩四次的形态）。"""
    src = _strip_comments(_read(GAME))
    assert '["recent_with_player"] = UnitSnapshot.RecentWithPlayer(ud.unitID)' in src, (
        "GameContext 没有发 recent_with_player")
    print("✓ GameContext.GetL1 发 recent_with_player")


def test_recentwithplayer_shape_matches_recenttexts():
    """RecentWithPlayer 必须与 RecentTexts 同构：两桶合并 → **只留玩家相关** → 月降序 → 去重
    → 命名常量 → CnYearMonth 前缀。任何一处偷懒都会把 09-13 修过的
    「近况退回陈年旧事 / 变成第二个近况 / 同条列两遍」原样重演一遍。"""
    src = _strip_comments(_read(UNIT))
    body = _body(src, "public static JArray RecentWithPlayer(string unitID)")
    assert 'foreach (var key in new[] { "vital", "regular" })' in body, "没有合并两桶"
    # ① 条目级筛选必须是**丢弃式守卫**（continue），不能只是"读了 with_player 键"
    assert re.search(r"if\s*\(\s*!wp\s*\)\s*continue\s*;", body), (
        "RecentWithPlayer 缺少 `if (!wp) continue;` 丢弃守卫 —— 少了它，"
        "本段就退化成第二个「近况」（把与他日常有关的条目也当成与玩家相关）")
    # ② 月降序（新→旧）
    assert re.search(r"merged\.Sort\(\(a,\s*b\)\s*=>\s*\(\(int\)b\[\"m\"\]\)\.CompareTo", body), (
        "必须按账面月**降序**排（新→旧），否则退回陈年旧事")
    # ③ 去重也必须是丢弃式守卫 —— 两桶可能各存一条同月同文（LayerOverlap 在盯这件事）
    assert re.search(r'if\s*\(\s*!seen\.Add\(mm \+ "\|"\s*\+\s*t\)\s*\)\s*continue\s*;', body), (
        "RecentWithPlayer 缺少去重守卫 —— 两桶各存一条同月同文时会列两遍")
    # ④ 守卫必须先于取文本（先筛后读，空条目不浪费）
    i_wp = body.index("if (!wp) continue;")
    i_txt = body.index('string t = (string)it["text"]')
    assert i_wp < i_txt, "应先按 with_player 筛，再取 text（顺序反了会读无关条目的文本）"
    assert "CnYearMonth(" in body, "月份前缀必须走全仓唯一的 CnYearMonth（不许内联换算）"
    cap = re.search(r"private const int RecentPlayerCap = (\d+);", src)
    assert cap and int(cap.group(1)) >= 3, "RecentPlayerCap 常量缺失或过小"
    print(f"✓ RecentWithPlayer：合并两桶 → 只留玩家相关 → 月降序 → 去重 → 取最近 {cap.group(1)} 条")


def test_csharp_and_game_anchor_words_present():
    """把判定依据写进代码注释（单元里得看得见为什么这么判），防后人"顺手简化"成按名匹配。"""
    src = _read(UNIT)
    assert "npcInfo_yuwoxiangguan" in src, "注释里没有游戏侧锚点（「与我相关」页签的 LocalText key）"
    assert "GetLogWithMe" in src, "注释里没有游戏实现锚点（UINPCInfoLog.GetLogWithMe）"
    assert "同名" in src and "UnitLookup" in src, "注释里没有写明为什么不能用名字匹配"
    print("✓ 判定依据留痕：游戏锚点 + 同名 NPC 风险都写在代码里")


if __name__ == "__main__":
    test_judgement_happens_before_cleantext()
    test_never_matches_by_player_name()
    test_empty_player_id_is_never_a_match()
    test_logsarr_marks_entries()
    test_with_player_is_only_writable_in_unitsnapshot()
    test_recent_with_player_is_independent_segment()
    test_l1_reads_raw_key_and_stays_empty_when_absent()
    test_tool_enum_has_with_me_and_csharp_handles_it()
    test_with_me_filters_out_unmarked_entries()
    test_renderer_knows_with_me()
    test_gamecontext_emits_recent_with_player()
    test_recentwithplayer_shape_matches_recenttexts()
    test_csharp_and_game_anchor_words_present()
    print("\n「与玩家相关」经历契约：13/13 通过")
