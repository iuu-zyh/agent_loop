"""玩家消息游戏时间戳契约（09-13）：C# 拼 `[N年M月] ` 前缀 → 账本 / 气泡 / 模型三处同串。

**要解决的问题**：NPC 此前完全感知不到时间 —— 账本里玩家的话没有任何时间信息，
模型无法说出"上次见你是三月前"。修法是 C# 在玩家消息**进 WS 之前**拼上当前游戏年月。

本文件锁住四条**静默失效**的约定（都不会抛异常、不会报错，只是行为悄悄不对）：

1. **标度**：`roundMonth` 是 0 起总月数，账面月 = `roundMonth + 1`，与
   `DataUnitLog.LogItemData.month` 同标度（1年1月=1、2年1月=13）。
   写成 `roundMonth` 直接当账面月 → 整体差一个月，且没有任何报错。
   实证：真机开局首月载荷 `"month": 1`；参照 mod 两处独立写法
   （`roundMonth/12+1` 年 + `roundMonth%12+1` 月；`ConvertToYearsMonths(roundMonth+1)`）。
   IL2CPP 反编 `WorldRunMgr` 只有 `roundMonth/roundDay/roundDayResidue/roundDayMax`
   —— **没有 roundYear**，年必须除出来。

2. **换算单一来源**：`N年M月` 的算式全仓只许出现一次（`UnitSnapshot.CnYearMonth`）。
   经历「近况」前缀与消息时间戳各写一份 → 同一个月份两条路径措辞不同。

3. **拼在本地回显之前**：气泡 / 账本 / 模型后续回合必须同一个串（同 `[图片：xx]`
   占位那条唯一来源约定）。若在 `WsClient.SendPlayerMessage` 里拼，实时气泡干净、
   重开窗口走 `get_history` 回放时气泡却长出时间戳 —— 同一条消息两种样子。

4. **故障不阻断**：日历读不到（未进世界/切档瞬间）→ 空标签 → 原样照发。
   时间戳绝不能把玩家的话卡住。

另外锁住 Python 侧：`/compact` 兜底判据必须先剥掉时间戳，否则带前缀的
`"[1年1月] /compact"` 永远匹配不上，会被当普通文本喂给模型（玩家以为要压缩、实际烧一个回合）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1. 标度 + 4. 不阻断

def test_account_month_is_roundmonth_plus_one():
    """账面月 = roundMonth + 1；且必须容错（读不到 → -1，不抛给调用方）。"""
    src = _read("NpcInitiativeMonitor.cs")
    body = src[src.index("internal static int CurrentAccountMonth()"):]
    body = body[: body.index("\n        }")]
    assert "run.roundMonth" in body, "必须从 g.world.run.roundMonth 取总月数"
    assert re.search(r"return\s+m\s*\+\s*1\s*;", body), \
        "roundMonth 是 0 起总月数，账面月必须 +1（否则与 LogItemData.month 差一个月）"
    assert "catch { return -1; }" in body, "日历不可用必须返回 -1 而不是抛出"


def test_time_label_empty_when_clock_unavailable():
    """空标签 = 不打时间戳；返回空串而非 None/异常，调用方才可能安全放行。"""
    src = _read("NpcInitiativeMonitor.cs")
    body = src[src.index("internal static string CurrentTimeLabel()"):]
    body = body[: body.index("\n        }")]
    assert re.search(r'return\s+d\.Length\s*>\s*0\s*\?\s*"\["\s*\+\s*d\s*\+\s*"\]"\s*:\s*""\s*;', body), \
        "日历不可用必须返回空串（不打时间戳），不能返回 None 或半截标签"


def test_day_reader_is_one_based_and_fault_tolerant():
    """`roundDay` 是 0 起月内日 → 报出的日必须 +1；读不到返回 -1（不抛）。"""
    src = _read("NpcInitiativeMonitor.cs")
    body = src[src.index("internal static int CurrentRoundDay()"):]
    body = body[: body.index("\n        }")]
    assert "run.roundDay" in body, "必须从 g.world.run.roundDay 取日"
    assert re.search(r"return\s+d\s*\+\s*1\s*;", body), \
        "roundDay 是 0 起（参照 mod 各处以 roundDay+1 报日），必须 +1"
    assert "catch { return -1; }" in body, "读不到必须返回 -1 而不是抛出"


def test_cndate_degrades_without_inventing_a_day():
    """日拿不到 → 退化成"N年M月"，**绝不编造一个日**；月也拿不到 → 空串。"""
    src = _read("UnitSnapshot.cs")
    body = src[src.index("public static string CnDate(int acctMonth, int day)"):]
    body = body[: body.index("\n        }")]
    assert 'return day > 0 ? ym + day + "日" : ym;' in body, \
        "日 <= 0 时必须退化成年月（不许编造出「3日」这种值）"


def test_split_account_month_is_the_only_arithmetic():
    """年月拆分只许有一处（`SplitAccountMonth`），CnYearMonth 与结构化 now 都走它。"""
    src = _read("UnitSnapshot.cs")
    body = src[src.index("public static string CnYearMonth(int acctMonth)"):]
    body = body[: body.index("public static string CnDate(")]
    assert "SplitAccountMonth(acctMonth, out y, out m)" in body, \
        "CnYearMonth 必须调用 SplitAccountMonth（否则又出现第二份算式）"


def test_no_roundyear_hallucination():
    """IL2CPP 反编实证 WorldRunMgr 没有 roundYear —— 别去读不存在的字段。"""
    # 只查成员访问 `.roundYear` —— 文档注释里正当地提到"没有 roundYear"不算违规
    for rel in ("NpcInitiativeMonitor.cs", "UnitSnapshot.cs", "GameContext.cs"):
        bad = [l for l in _read(rel).splitlines() if re.search(r"\.roundYear\b", l)]
        assert not bad, f"{rel} 读了不存在的 roundYear（WorldRunMgr 无此成员）：{bad}"


# ---------------------------------------------------------------- 2. 换算单一来源

def test_year_month_conversion_has_single_source():
    """`(x - 1) / 12 + 1` 这条算式全仓只许有一处（UnitSnapshot.CnYearMonth）。"""
    hits: list[str] = []
    for path in sorted(CS.rglob("*.cs")):
        if "obj" in path.parts or "bin" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"-\s*1\)\s*/\s*12\s*\+\s*1", line):
                hits.append(f"{path.relative_to(CS)}:{i}")
    assert len(hits) == 1, (
        "「账面月 → N年M月」的算式必须只有一处（UnitSnapshot.SplitAccountMonth），"
        f"否则两条路径会漂移；实际命中 {hits}"
    )
    assert hits[0].startswith("UnitSnapshot.cs"), f"算式应落在 UnitSnapshot.SplitAccountMonth，实际 {hits[0]}"


def test_recent_texts_reuses_the_shared_conversion():
    """经历「近况」前缀必须调用 CnYearMonth，不许内联。"""
    src = _read("UnitSnapshot.cs")
    block = src[src.index("public static JArray RecentTexts"): src.index("public static JObject BuildLogs")]
    assert re.search(r"CnYearMonth\(\w+\)", block), "近况前缀应调用 CnYearMonth（单一来源）"
    assert not re.search(r"-\s*1\)\s*/\s*12", block), "近况里又内联了一份换算"


# ---------------------------------------------------------------- 3. 拼在本地回显之前

def test_stamp_prepended_before_local_echo():
    """ChatPresenter：先拼时间戳，再 AppendUserMessage，再发 WS —— 顺序不能变。"""
    src = _read("UI/ChatPresenter.cs")
    i_stamp = src.index("NpcInitiativeMonitor.CurrentTimeLabel()")
    i_echo = src.index("_window.AppendUserMessage(clean)")
    # 注意分支顺序：带图那行 `if (attachments != null) _ws.SendPlayerMessage(npc, clean, …)` 在前，
    # 无图那行在 else 里 —— 取无图那行才是真正"发出去"的落点
    i_send = src.index("else _ws.SendPlayerMessage(npc, clean)")
    assert i_stamp < i_echo < i_send, (
        "时间戳必须拼在本地回显之前（气泡/账本/模型同串）；"
        "拼在 SendPlayerMessage 里会让回放气泡与实时气泡不一致"
    )
    # 回显与发送用的必须是**同一个** clean 变量（而不是一个带戳一个不带）
    window = src[i_stamp:i_send]
    assert "AppendUserMessage(clean)" in window and "SendPlayerMessage(npc, clean" in window


def test_transport_stays_dumb():
    """WS 传输层不许自己造时间戳 —— 否则又冒出一个"第二作者"。"""
    src = _read("WsClient.cs")
    body = src[src.index("public virtual void SendPlayerMessage(string npcId, string text, JArray images)"):]
    body = body[: body.index("\n        }")]
    assert "CurrentTimeLabel" not in body, "时间戳不该在 WsClient（传输层）里拼"


def test_stamp_failure_does_not_block_send():
    """日历读不到（空标签）时消息照发：不能 return / 不能抛。"""
    src = _read("UI/ChatPresenter.cs")
    i = src.index("string stamp = NpcInitiativeMonitor.CurrentTimeLabel();")
    seg = src[i: i + 400]
    assert re.search(r'if\s*\(stamp\.Length\s*>\s*0\)', seg), "空标签必须跳过拼接而不是中断发送"
    assert "return" not in seg.split("AppendUserMessage")[0], "时间戳分支里不允许 return（会吞掉玩家消息）"


# ---------------------------------------------------------------- Python：命令判据

def test_python_strips_stamp_for_command_detection():
    from agent_loop.ws_channel import _strip_time_stamp
    assert _strip_time_stamp("[1年1月] /compact").strip().lower() == "/compact"
    assert _strip_time_stamp("[12年10月]  /Compact").strip().lower() == "/compact"
    # 正文里的时间戳绝不能被误剥（只有行首那个算前缀）
    assert _strip_time_stamp("你好[1年1月]") == "你好[1年1月]"
    assert _strip_time_stamp("") == ""
    assert _strip_time_stamp(None) == ""
    # 正常消息：前缀保留（模型要看到时间）
    assert _strip_time_stamp("[1年1月] 你好呀") == "你好呀"


def test_command_check_uses_the_stripper():
    src = (ROOT / "ws_channel.py").read_text(encoding="utf-8")
    assert '_strip_time_stamp(text).strip().lower() == "/compact"' in src, \
        "handle_message 的 /compact 判据必须走 _strip_time_stamp"


def test_stamp_survives_into_ledger_when_not_a_command():
    """非命令消息：前缀**必须保留**进账本（NPC 靠它算时间跨度）。"""
    from agent_loop.ws_channel import _strip_time_stamp
    body = "[1年1月] 好久不见"
    assert _strip_time_stamp(body) == "好久不见"       # 仅供命令判据
    assert body.startswith("[1年1月]")                  # 落账的那一份仍带前缀


# ---------------------------------------------------------------- 端到端（Python 侧全链路）

def test_ledger_keeps_stamp_and_prefixed_compact_is_intercepted():
    """真过一遍 ChatHub：带前缀的正常消息**落账带前缀**；带前缀的 /compact 仍被拦下。

    这条锁的是本次改动的真正风险面 —— 判据与落账用的是**同一个 text**，
    稍不注意就会"顺手把前缀洗掉再落账"（那么 NPC 又感知不到时间了），
    或者"忘了剥前缀就判命令"（那么 /compact 变成一次 LLM 回合）。
    """
    import asyncio
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_compact_command import _FakeCompactor, _FakeWs, _reset  # 复用桩（同一仓库的测试工具）
    from agent_loop.agent_loop import AgentLoop
    from agent_loop.llm.stub_client import StubLlmClient

    NPC = "林婉清_时间戳测试"
    llm_calls = {"n": 0}

    def _llm_fn(req):
        llm_calls["n"] += 1
        return {"text": "（回声）", "tool_calls": []}

    _reset(llm=StubLlmClient(fn=_llm_fn),
           compactor=_FakeCompactor(result={"range": (0, 1), "compactionId": "c1"}))
    loop = AgentLoop()
    hub = __import__("agent_loop.ws_channel", fromlist=["ChatHub"]).ChatHub(loop, _FakeWs())

    async def scenario():
        await hub.handle_message(NPC, "[1年1月] 好久不见")
        agent = loop.get(NPC)
        texts = [b.get("text") for ev in agent.session.log if ev.get("type") == "user/message"
                 for b in ev["data"].get("content", []) if isinstance(b, dict)]
        # 账本里除玩家那句之外还有：首次「相识」注入 + L1 各段 context（同为 user/message），
        # 故这里断言**存在**（而不是位置），位置与本条契约无关
        assert "[1年1月] 好久不见" in texts, f"账本必须保留时间戳，实际 {texts}"
        before = len(texts)
        calls_before = llm_calls["n"]
        # 带前缀的 /compact：必须仍被兜底拦下（零 LLM 回合、零新账本条目）
        await hub.handle_message(NPC, "[1年1月] /compact")
        assert llm_calls["n"] == calls_before, "/compact 不该触发对话 LLM 回合"
        after = [b.get("text") for ev in agent.session.log if ev.get("type") == "user/message"
                 for b in ev["data"].get("content", []) if isinstance(b, dict)]
        assert len(after) == before, f"/compact 不该落进对话历史：{after}"
        assert agent.compactor.calls == 1

    asyncio.run(scenario())


# ---------------------------------------------------------------- L1「当前时间」段

def test_time_segment_registered_first():
    """段清单权威只有 `_CTX_SEGMENTS` 一处；「当前时间」在首位（先给"现在"，再读状态）。"""
    from agent_loop.system_prompt import SystemPrompt
    assert SystemPrompt._CTX_SEGMENTS[0] == "time", \
        f"当前时间段应在首位，实际 {SystemPrompt._CTX_SEGMENTS}"
    assert SystemPrompt._CTX_LABELS["time"] == "当前时间"


def test_l1_time_text_prefers_csharp_render():
    """C# 给了 `now.text` 就直接用（成文唯一来源），Python 不重新拼。"""
    from agent_loop.system_prompt import SystemPrompt
    sp = SystemPrompt()
    segs = sp.format_l1_context("云含", {"npc_id": "云含", "now": {
        "year": 1, "month": 1, "day": 3, "text": "1年1月3日"}})
    assert segs["time"] == "1年1月3日", segs


def test_l1_time_text_falls_back_to_numbers():
    """只有数字（旧载荷/部分字段）→ 按**同一种措辞**拼，绝不出现第二种写法。"""
    from agent_loop.system_prompt import SystemPrompt
    sp = SystemPrompt()
    assert sp.format_l1_context("云含", {"now": {"year": 2, "month": 10, "day": 7}})["time"] == "2年10月7日"
    # 没有日（旧 C# 只给到月）→ 只报年月，不写"0日"
    assert sp.format_l1_context("云含", {"now": {"year": 2, "month": 10}})["time"] == "2年10月"


def test_l1_time_independent_of_recent():
    """★本机制选"独立段"的决定性理由★：没有经历日志时，「近况」整段不渲染，
    但日期必须**照样在** —— 新 NPC / 系统角色恰恰最需要知道"今天几号"。"""
    from agent_loop.system_prompt import SystemPrompt
    sp = SystemPrompt()
    segs = sp.format_l1_context("云含", {"npc_id": "云含", "now": {"year": 1, "month": 1, "day": 3,
                                                          "text": "1年1月3日"}})
    assert segs["recent"] == "", "前提：无经历日志时近况段为空"
    assert segs["time"] == "1年1月3日", "近况为空也必须仍有日期（独立段的意义所在）"
    # 反过来：日期缺失不该影响近况
    segs2 = sp.format_l1_context("云含", {"npc_id": "云含", "recent": "初入八荒。"})
    assert segs2["time"] == "" and "初入八荒" in segs2["recent"]


def test_l1_time_absent_when_clock_unavailable():
    """日历不可用（旧 DLL / 世界未加载）→ 空串 → 整段不渲染，绝不编造日期。"""
    from agent_loop.system_prompt import SystemPrompt
    sp = SystemPrompt()
    for raw in ({}, {"npc_id": "云含"}, {"now": {}}, {"now": None}, {"now": {"day": 3}}):
        assert sp.format_l1_context("云含", raw)["time"] == "", f"不该凭空造日期：{raw}"


def test_render_context_segment_wording():
    from agent_loop.system_prompt import SystemPrompt
    sp = SystemPrompt()
    assert sp.render_context_segment("time", "1年1月3日") == \
        "Current runtime context —— 当前时间：1年1月3日"
    assert sp.render_context_segment("time", "") == "", "空段不渲染（差分据此判 remove）"


def test_dialogue_agent_segment_dicts_not_hardcoded():
    """段名不许在 DialogueAgent 里写死第二份清单（09-13 加段时踩到：`.get()` 不报错、静默漏段）。"""
    src = (ROOT / "dialogue_agent.py").read_text(encoding="utf-8")
    for dead in ('{"self": None, "player": None, "recent": None}',
                 '{"self", "player", "recent"}'):
        assert dead not in src, f"段清单被写死：{dead}（应从 _CTX_SEGMENTS 派生）"
    assert '_segs = tuple(getattr(system_prompt, "_CTX_SEGMENTS"' in src


def test_gamecontext_emits_now_in_both_branches():
    """快照成功/失败两条分支都要给 `now` —— 取快照失败 ≠ 不知道几号。"""
    src = _read("GameContext.cs")
    assert src.count('["now"] = NowBlock()') == 2, \
        "成功分支与 catch 分支各要一处（时钟独立于单位数据）"
    body = src[src.index("private static JObject NowBlock()"):]
    assert "SplitAccountMonth(acct, out y, out m)" in body, "结构化年月必须走 SplitAccountMonth"
    assert "UnitSnapshot.CnDate(acct, day)" in body, "text 必须走 CnDate（与消息前缀同函数）"


def test_time_segment_reaches_assembly_and_diff_is_isolated():
    """真过 turn：`runtime:time` 落进 contexts；**只改日期**时只有该段变化。"""
    import asyncio
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_bridge_tools import reset_with_stub

    loop, bridge = reset_with_stub()
    npc = "林婉清"
    agent = loop.create(npc)
    orig = bridge.get_context

    def with_day(d):
        def patched(npc_id):
            r = orig(npc_id)
            r.setdefault("raw", {})["now"] = {"year": 1, "month": 1, "day": d,
                                              "text": f"1年1月{d}日"}
            return r
        return patched

    bridge.get_context = with_day(3)
    agent.send("第一问")
    asyncio.run(agent.run_until_idle())
    by_name = {c["name"]: c["text"] for c in agent.system_prompt.assemble(scope=npc)["contexts"]}
    # contexts 里存的是**段原文**；`Current runtime context —— 当前时间：…` 那层前缀由
    # `render_context_segment` 在拼请求时才加（单独测见 test_render_context_segment_wording）
    assert by_name.get("runtime:time") == "1年1月3日", by_name
    assert agent.system_prompt.render_context_segment("time", by_name["runtime:time"]) == \
        "Current runtime context —— 当前时间：1年1月3日"
    self_before = by_name["runtime:self"]

    bridge.get_context = with_day(4)     # 只有日期变了（模拟过了一天）
    agent.send("第二问")
    asyncio.run(agent.run_until_idle())
    by_name2 = {c["name"]: c["text"] for c in agent.system_prompt.assemble(scope=npc)["contexts"]}
    assert by_name2["runtime:time"] == "1年1月4日", by_name2["runtime:time"]
    assert by_name2["runtime:self"] == self_before, "只改日期不该重写自身段（独立段的意义）"
