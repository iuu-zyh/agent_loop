"""NPC 主动开口：意图文案映射 + ChatHub.handle_initiative 全链路 + 忙守卫/节流

验证：
  1. INITIATIVE_INTENTS 目录齐全（正向 7 + 负向 4 = 11）与文案包装（括号舞台指令 + 缘由）
  2. handle_initiative 驱动正常 turn：伪 user 消息带 source=initiative 落账，产出开场白
  3. 忙守卫：agent 非 idle 时静默丢弃（不落账、不触发）
  4. 全局节流：短间隔二次开口被丢弃
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

from agent_loop.agent_loop import AgentLoop
from agent_loop.bridge import StubGameBridge
from agent_loop.initiative import INITIATIVE_INTENTS, format_initiative_message, format_game_drama_message
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.ws_channel import WsServer, ChatHub

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_initiative")
NPC = "李慕白_主动测试"


def _reset(bridge=None, llm=None):
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=bridge, llm=llm)
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)


def _seed(bridge: StubGameBridge, same_grid: bool = True):
    bridge.seed_unit(
        NPC,
        {
            "realm": "元婴",
            "sect": "七星阁",
            "pos": "永宁州",
            "mood": 80,
            "power": 22000,
            "player": {"name": "韩立", "realm": "筑基", "relation": "道侣", "intim": 180, "same_grid": same_grid},
            "recent": "上月在雷泽觅得一件异宝",
        },
    )


# ---------- 1. 意图目录与文案 ----------
def test_intent_catalog_and_formatter():
    # 神识传音 11 种：正向 7 + 负向 4
    assert len(INITIATIVE_INTENTS) == 11
    positive = {"greet", "smalltalk", "courteous", "life", "recent", "missing", "affection"}
    negative = {"malice", "vent", "provocation", "disdain"}
    assert set(INITIATIVE_INTENTS) == positive | negative, set(INITIATIVE_INTENTS)

    t = format_initiative_message("greet")
    assert t.startswith("（NPC主动传音") and "招呼" in t, t
    t2 = format_initiative_message("missing", "你们已有数日未见")
    assert "思念" in t2 and "数日未见" in t2, t2
    # 未知意图兜底为 greet
    assert "招呼" in format_initiative_message("unknown_key")
    print(f"✓ 意图目录 11 种 + 文案包装（含缘由插值）通过")


# ---------- 2. handle_initiative 全链路（直调，不依赖 WS） ----------
def test_handle_initiative_drives_agent_turn():
    bridge = StubGameBridge()
    _seed(bridge)

    def _llm_fn(req):
        # 主动回合的 messages 里必须带「NPC主动传音」舞台指令
        texts = [
            b.get("text", "")
            for m in req.get("messages", [])
            for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        joined = "|".join(texts)
        assert "NPC主动传音" in joined, f"意图未注入 {joined}"
        return {"text": "这位道友，别来无恙？我正闲逛，想起你便来搭句话。", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    asyncio.run(hub.handle_initiative(NPC, "greet", "你们已有数日未见"))

    agent = loop.get(NPC)
    # 伪 user 消息带 source=initiative 落账（审计可区分）
    src_evs = [
        ev for ev in agent.session.log
        if ev.get("type") == "user/message" and ev.get("data", {}).get("source", {}).get("kind") == "initiative"
    ]
    assert src_evs, "应有 initiative 标记的伪 user 消息"
    assert src_evs[0]["data"]["source"]["intent"] == "greet"
    # NPC 开场白已产出（走正常 assistant 路径）
    reply = ChatHub._collect_reply(agent)
    assert "别来无恙" in reply, f"开场白错误 {reply!r}"
    # 回合结构完整：turn/start ∧ turn/end
    assert any(ev["type"] == "turn/end" for ev in agent.session.log)
    print(f"✓ handle_initiative 全链路：意图注入 → turn → 开场白 = {reply!r}")


# ---------- 3. 忙守卫：agent 非 idle 时静默丢弃 ----------
def test_handle_initiative_busy_guard():
    bridge = StubGameBridge()
    _seed(bridge)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回复", "tool_calls": []}))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))
    agent = loop.create(NPC)

    async def scenario():
        # 模拟玩家回合进行中
        agent.phase = {"kind": "running", "turn": 1, "step": 1}
        await hub.handle_initiative(NPC, "greet")
        assert agent.inbox.has_pending is False, "忙时不得投流入筐"
        # 恢复 idle 后可正常触发
        agent.phase = {"kind": "idle", "last_turn": 1}
        hub._last_initiative_ts = 0.0
        await hub.handle_initiative(NPC, "greet")

    asyncio.run(scenario())
    replies = [ev for ev in agent.session.log if ev.get("type") == "user/message"]
    # 只有 idle 后那次触发了新的伪 user 消息（忙时那次未产生任何消息）
    src_cnt = sum(1 for ev in agent.session.log
                  if ev.get("type") == "user/message" and ev["data"].get("source", {}).get("kind") == "initiative")
    assert src_cnt == 1, f"忙守卫失效，落账 {src_cnt} 条"
    print(f"✓ 忙守卫：running 时静默丢弃，idle 后恢复正常（伪 user 共 {len(replies)} 条）")


# ---------- 3b. 同意放行却被丢弃 → 必须回执 UI（否则聊天窗占位一直挂着） ----------
def test_consented_drop_notifies_ui():
    bridge = StubGameBridge()
    _seed(bridge)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回复", "tool_calls": []}))
    loop = AgentLoop()

    class _RecWs(WsServer):
        def __init__(self):
            super().__init__(port=0)
            self.events = []

        async def send_event(self, kind, **payload):
            self.events.append((kind, payload))

    ws = _RecWs()
    hub = ChatHub(loop, ws)
    agent = loop.create(NPC)

    async def scenario():
        agent.phase = {"kind": "running", "turn": 1, "step": 1}
        # ① 自动触发被丢弃：静默（错过就丢是设计语义，不该打扰玩家）
        await hub.handle_initiative(NPC, "greet")
        assert ws.events == [], ws.events
        # ② 玩家点过「同意」却被丢弃：回一条 error 收口帧，让 C# 撤占位 + 给系统提示
        await hub.handle_initiative(NPC, "greet", consented=True)
        assert len(ws.events) == 1, ws.events
        kind, payload = ws.events[0]
        assert kind == "npc_reply" and payload.get("error") is True, payload
        assert payload.get("initiative") is True and payload.get("npc_id") == NPC

    asyncio.run(scenario())
    print("✓ 同意放行被丢弃 → 回执 UI（自动触发仍静默）")


# ---------- 4. 全局节流：短间隔二次开口被丢弃 ----------
def test_handle_initiative_throttle():
    bridge = StubGameBridge()
    _seed(bridge)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回", "tool_calls": []}))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_initiative(NPC, "greet")  # 第一次：通过
        await hub.handle_initiative(NPC, "say_hi_missing")  # 短间隔：节流丢弃

    asyncio.run(scenario())
    agent = loop.get(NPC)
    src_cnt = sum(1 for ev in agent.session.log
                  if ev.get("type") == "user/message" and ev["data"].get("source", {}).get("kind") == "initiative")
    assert src_cnt == 1, f"节流失效，触发 {src_cnt} 次"
    print("✓ 全局节流：30 秒内二次开口被丢弃")


# ---------- 5. game_drama：原生剧情窗「AI 对话」按钮 ----------
def test_game_drama_formatter():
    t = format_game_drama_message("韩立！你我之间的账，今日该做个了断！")
    assert "游戏内交互" in t and "了断" in t and "不要复读" in t, t
    # 中性：**不点明谁主动**——同一枚按钮既服务 NPC 主动剧情（过月寻仇），也服务玩家点闲聊（用户定调）
    assert "你主动" not in t and "玩家主动" not in t, t
    # 空原文兜底：只剩舞台指令
    t2 = format_game_drama_message("")
    assert "游戏内交互" in t2 and "「" not in t2, t2
    print("✓ game_drama 文案包装：中性（不点明谁主动）+ 原文内插 + 空原文兜底")


def test_handle_initiative_game_drama():
    """game_drama：原文入种子、跳过全局节流（紧连两次均触发）、source 落账 intent=game_drama。"""
    bridge = StubGameBridge()
    _seed(bridge)

    captured = {}

    def _llm_fn(req):
        texts = [
            b.get("text", "")
            for m in req.get("messages", [])
            for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        captured["joined"] = "|".join(texts)
        return {"text": "哼，你倒是来得快。", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_initiative(NPC, "game_drama", "", "韩立！你我之间的账，今日该做个了断！")
        # 紧连第二次 game_drama：玩家主动点击不受全局节流限制（greet 则仍受限）
        await hub.handle_initiative(NPC, "game_drama", "", "别走！把话说清楚！")

    asyncio.run(scenario())
    agent = loop.get(NPC)
    assert "了断" in captured.get("joined", ""), f"剧情原文未注入 {captured.get('joined', '')[:200]}"
    src = [ev for ev in agent.session.log
           if ev.get("type") == "user/message" and ev.get("data", {}).get("source", {}).get("kind") == "initiative"]
    assert len(src) == 2, f"game_drama 应跳过节流连触发两次，实际 {len(src)}"
    assert all(ev["data"]["source"]["intent"] == "game_drama" for ev in src)
    print("✓ game_drama：原文注入 + 跳过节流（紧连两次均触发）+ source 落账")


def test_game_drama_speaker_attribution():
    """引文归属：speaker=npc/player 点名，未知保持中性。

    屏幕句本身不含说话人（「我这里有一个青须藤*48准备赠于你，你需要此物吗？」两头都可能说），
    一律中性丢给模型 → 09-13 真机猜反（NPC 送礼那页被当成"玩家送我"）。归属由 C# 从剧情窗
    按角色命名的压暗遮罩读出（imgBgPlayerDark/imgBgOtherDark），这里只验文案分支。
    """
    line = "我这里有一个青须藤*48准备赠于你，你需要此物吗？"
    neutral = format_game_drama_message(line)
    # 未知支路必须与历史文案逐字一致——加归属不许改动"判不出"时的措辞
    assert neutral.startswith("（游戏内交互：你和玩家之间刚经过了这样一幕——「"), neutral

    npc = format_game_drama_message(line, "npc")
    assert npc.startswith("（游戏内交互：你刚对玩家说了这样一句——「"), npc
    assert "你和玩家之间" not in npc, npc

    player = format_game_drama_message(line, "player")
    assert player.startswith("（游戏内交互：玩家刚对你说了这样一句——「"), player

    for m in (neutral, npc, player):
        assert line in m, m
        assert "不要复读括号内容" in m, m
    # 大小写 / 空白 / 未知值
    assert format_game_drama_message(line, "  ") == neutral
    assert format_game_drama_message(line, " NPC ") == npc
    assert format_game_drama_message(line, "???") == neutral
    # 空原文：三种情形都退回纯舞台指令（不阻断）
    for s in ("", "npc", "player"):
        assert format_game_drama_message("", s).startswith("（游戏内交互：你和玩家之间刚发生了一段交谈")
    print("✓ game_drama 引文归属：npc / player / 未知中性（且未知支路逐字未变）")


def test_handle_initiative_game_drama_speaker_plumbing():
    """speaker 一路透传：handle_initiative(..., speaker=) → 舞台指令按说话人归属。"""
    bridge = StubGameBridge()
    _seed(bridge)
    captured = {}

    def _llm_fn(req):
        texts = [
            b.get("text", "")
            for m in req.get("messages", [])
            for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        captured["joined"] = "|".join(texts)
        return {"text": "（接住）", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    asyncio.run(hub.handle_initiative(NPC, "game_drama", "", "我这里有一个青须藤*48准备赠于你",
                                      speaker="npc"))
    joined = captured.get("joined", "")
    assert "你刚对玩家说了这样一句" in joined, joined[:300]
    assert "你和玩家之间刚经过了这样一幕" not in joined, joined[:300]
    print("✓ game_drama speaker 透传：命中 NPC 归属支路")


def test_handle_initiative_consented_bypass():
    """consented=true（当面确认窗点了同意）：紧随上一条 initiative 也放行（正常节流会吞）；
    source 落账带 consented 标记。"""
    bridge = StubGameBridge()
    _seed(bridge)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回", "tool_calls": []}))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_initiative(NPC, "greet")                       # 自动触发：通过
        await hub.handle_initiative(NPC, "missing", consented=True)     # 玩家刚点同意：节流豁免

    asyncio.run(scenario())
    agent = loop.get(NPC)
    src = [ev for ev in agent.session.log
           if ev.get("type") == "user/message" and ev.get("data", {}).get("source", {}).get("kind") == "initiative"]
    assert len(src) == 2, f"consented 应豁免节流，实际 {len(src)} 条"
    assert src[1]["data"]["source"].get("consented") is True, src[1]["data"]["source"]
    print("✓ consented 豁免节流：紧连两次均触发，source 带 consented 标记")


def test_handle_initiative_debug_bypass():
    """debug=true（诊断强制触发，C# `_diag_initiative_force.txt`）：跳过全局节流，
    使测试节拍不受 min_interval（默认 300s）压制；source 落账带 debug 标记。"""
    bridge = StubGameBridge()
    _seed(bridge)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回", "tool_calls": []}))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_initiative(NPC, "greet")                     # 自动触发：通过
        await hub.handle_initiative(NPC, "vent", debug=True)          # 诊断强制：节流豁免
        await hub.handle_initiative(NPC, "disdain", debug=True)       # 紧接再来一次：同样豁免

    asyncio.run(scenario())
    agent = loop.get(NPC)
    src = [ev for ev in agent.session.log
           if ev.get("type") == "user/message" and ev.get("data", {}).get("source", {}).get("kind") == "initiative"]
    assert len(src) == 3, f"debug 应豁免节流连触发三次，实际 {len(src)}"
    assert src[1]["data"]["source"].get("debug") is True, src[1]["data"]["source"]
    assert src[2]["data"]["source"].get("debug") is True, src[2]["data"]["source"]
    assert src[0]["data"]["source"].get("debug") is None, "正常触发不该带 debug 标记"
    print("✓ debug 豁免节流：紧连三次均触发，source 带 debug 标记")


class _RecordingBridge(StubGameBridge):
    """记账桩：记录 call_tool 实际被调用的工具名（验证"动作被拦下、没到桥"）。"""

    def __init__(self):
        super().__init__()
        self.calls = []

    def call_tool(self, name, arguments, npc_id=None):
        self.calls.append(name)
        return super().call_tool(name, arguments, npc_id=npc_id)


def test_handle_initiative_busy_note_keeps_tools_intact():
    """busy=true（玩家忙）：**不改工具表**（恒定 8 个 → 不破坏上游前缀缓存），
    改为在本回合**尾部追加一条 plugin 约束消息**（模型可见、UI 不可见）；执行层标记复位。

    这是用户 09-12 拍板的方案：自主交互本质就是"模拟用户发一条消息"，
    约束应当也是消息（像 L1 运行期上下文那样），而不是把工具从能力表里删掉。"""
    import json as _json

    from agent_loop.history import project_ui_history
    from agent_loop.tools.schemas import ALL_TOOL_SCHEMAS

    bridge = StubGameBridge()
    _seed(bridge)
    seen = {}

    def _fn(req):
        seen.setdefault("tools", [t.get("function", {}).get("name") for t in (req.get("tools") or [])])
        return {"text": "（忙碌只言语）回", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_initiative(NPC, "greet", busy=True)

    asyncio.run(scenario())
    names = seen.get("tools") or []
    assert len(names) == len(ALL_TOOL_SCHEMAS), f"工具表必须恒定（不删工具），实际 {names}"

    agent = loop.get(NPC)
    # ① 约束真的到了模型（derive_messages 是喂给 LLM 的消息流）
    blob = _json.dumps(agent.session.derive_messages(), ensure_ascii=False)
    assert "不要发起任何行动" in blob, "忙碌约束必须进本回合消息流"
    # ② 账本里那条是 plugin 源（UI 投影滤除）且 id 固定
    notes = [ev for ev in agent.session.log
             if ev.get("type") == "user/message"
             and ev.get("data", {}).get("id") == "ctx-initiative-busy"]
    assert len(notes) == 1, f"应恰好追加一条忙碌约束，实际 {len(notes)}"
    assert notes[0]["data"].get("source", {}).get("kind") == "plugin", notes[0]["data"].get("source")
    ui = _json.dumps(project_ui_history(agent.session), ensure_ascii=False)
    assert "不要发起任何行动" not in ui, "plugin 内部约束不该出现在聊天窗时间线上"
    # ③ 执行层标记复位
    assert agent.speech_only_turn is False, "回合结束必须复位执行层标记"
    print("✓ busy：工具表恒定 + 约束以 plugin 消息进模型（UI 不显示）+ 标记复位")


def test_speech_only_blocks_action_tool_execution():
    """执行层兜底：即使模型硬调动作工具（历史残留/幻觉），忙碌回合也不得落到桥上；
    拒绝结果以 canonical tool 消息落账（形状与正常路径同构，模型看得到、不会死循环重试）。"""
    import json as _json

    bridge = _RecordingBridge()
    _seed(bridge)
    calls = {"n": 0}

    def _fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [{"id": "t1", "name": "world_ai_action",
                                                "arguments": {"op": "yao_yue", "target": "韩立"}}]}
        return {"text": "好，我不动手。", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_initiative(NPC, "greet", busy=True)

    asyncio.run(scenario())
    assert "world_ai_action" not in bridge.calls, f"动作工具不该到桥，实际调用 {bridge.calls}"
    blob = _json.dumps(loop.get(NPC).session.log, ensure_ascii=False)
    assert "不能发起行动" in blob, "拒绝结果必须落账（否则模型看不到、会反复重试）"
    print("✓ 忙碌兜底：动作工具没到桥 + 拒绝结果落账（模型可见）")


def test_speech_only_blocks_every_tool_execution():
    """诊断变体 `speech_only=true`（`_diag_initiative_force.txt` 的 no_tools=1）：

    **连只读工具也拦**（busy 只拦动作工具）。用途是观测纯传音形态（横幅/红点），所以
    "模型硬调查询"也必须被挡在桥外，且拒绝结果要落账（否则模型看不到会反复重试）。
    同时钉住：约束以 plugin 消息追加（模型可见、UI 不可见），工具表不变（前缀缓存不受伤）。
    """
    import json as _json

    bridge = _RecordingBridge()
    _seed(bridge)
    calls = {"n": 0}

    def _fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            # 只读工具（busy 场景下是放行的）——诊断场景必须也拦下
            return {"text": "", "tool_calls": [{"id": "t1", "name": "query_world",
                                                "arguments": {"topic": "events"}}]}
        return {"text": "好，我只说话。", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_initiative(NPC, "smalltalk", speech_only=True)

    asyncio.run(scenario())
    assert "query_world" not in bridge.calls, f"只读工具也不该到桥，实际调用 {bridge.calls}"
    agent = loop.get(NPC)
    blob = _json.dumps(agent.session.log, ensure_ascii=False)
    assert "只说话" in blob, "拒绝结果必须落账（模型可见），否则会反复重试"
    assert "【调试场景】" in blob, "调试约束文案应进账本（plugin 源）"
    # 标记复位：回合结束不能把 no_tools 留在 agent 上（否则下一个正常回合也被拦）
    assert agent.no_tools_turn is False, "回合收尾必须复位 no_tools_turn"
    print("✓ 诊断只说话：连只读工具都被拦 + 拒绝落账 + 标记复位")


if __name__ == "__main__":
    test_intent_catalog_and_formatter()
    test_handle_initiative_drives_agent_turn()
    test_handle_initiative_busy_guard()
    test_handle_initiative_throttle()
    test_game_drama_formatter()
    test_handle_initiative_game_drama()
    test_handle_initiative_consented_bypass()
    test_handle_initiative_debug_bypass()
    test_handle_initiative_busy_note_keeps_tools_intact()
    test_speech_only_blocks_action_tool_execution()
    test_speech_only_blocks_every_tool_execution()
    print("\nNPC 主动开口测试全部通过")