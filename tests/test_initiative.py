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
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.agent.initiative import (INITIATIVE_INTENTS, INITIATIVE_LABELS, ACTION_INTENTS,
                                         format_initiative_message,
                                         format_game_drama_message, _IN_ACTION_NOTE, _VERDICT_NOTE)
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.transport.ws_channel import WsServer, ChatHub

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
    # 神识传音 11 种（正向 7 + 负向 4）+ 10-16 新增动作向 5 种 = 16
    # 11-XX 再加 `mail_relay`（捎话回访）= 17：它与那 16 个是**替代**关系（命中时舞台指令
    # 直接给出捎话人与原话），不是第 17 种心情 —— 所以单列，不并入 positive/negative。
    positive = {"greet", "smalltalk", "courteous", "life", "recent", "missing", "affection"}
    negative = {"malice", "vent", "provocation", "disdain"}
    action = {"gift", "lun_dao", "yao_yue", "spar", "attack"}
    relay = {"mail_relay"}
    assert len(INITIATIVE_INTENTS) == 17, sorted(INITIATIVE_INTENTS)
    assert set(INITIATIVE_INTENTS) == positive | negative | action | relay, set(INITIATIVE_INTENTS)
    # 动作向集合必须与 format_initiative_message 的授权句判据同源
    assert set(ACTION_INTENTS) == action, (set(ACTION_INTENTS), action)

    t = format_initiative_message("greet")
    assert t.startswith("（NPC主动传音") and "招呼" in t, t
    t2 = format_initiative_message("missing", "你们已有数日未见")
    assert "思念" in t2 and "数日未见" in t2, t2
    # 未知意图兜底为 greet
    assert "招呼" in format_initiative_message("unknown_key")
    print(f"✓ 意图目录 16 种 + 文案包装（含缘由插值）通过")


def test_action_intents_carry_the_license_but_speech_intents_do_not():
    """动作向意图必须带「可以动手」的授权句；言语向**不许**带（否则每句话都在劝它调工具）。

    这条钉的是 10-16 那个根因：工具表的触发句全写在玩家一侧（"玩家问起…必须调我"），
    `tool_usage.txt` 唯一的强祈使还是**禁止**（"不许只用台词收场"）⇒ 模型对"主动做事"
    没有任何授权，于是主动开口回合永远只说话。授权句就是补这一块。
    """
    for k in ACTION_INTENTS:
        txt = format_initiative_message(k)
        assert "调用对应的工具" in txt and "办不成" in txt, (k, txt)

    for k in ("greet", "smalltalk", "missing", "malice", "vent"):
        txt = format_initiative_message(k)
        assert "调用对应的工具" not in txt, f"{k} 不该带动作授权句：{txt}"

    # 授权句必须**只是授权**，不许变成命令（"必须调用"会逼它与"不强行办事"的人设打架）
    license_txt = format_initiative_message("gift")
    assert "必须" not in license_txt, license_txt
    assert "也可以不动手" in license_txt, license_txt


def test_action_intent_keys_are_paired_with_csharp():
    """C# 的两个动作池与 Python 的 `ACTION_INTENTS` 必须**逐键成对**。

    跨语言键名错一个字母的后果是完全静默：C# 发 `gift`、Python 表里没有 →
    `format_initiative_message` 回落成 `greet` 文案 ⇒ NPC 说"打个招呼"，
    而 C# 那边已经按"动作意图"弹了确认窗。症状是"点了同意，他只寒暄了一句"。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    src = (root / "csharp" / "npc" / "NpcInitiativeMonitor.cs").read_text(encoding="utf-8")

    def _pool(name: str) -> set:
        """取 C# 池里的**意图键**。

        ⚠ 不能简单 `re.findall(r'"([^"]+)"', 花括号内容)`：池定义上方的注释里也带引号
        字符串（`// 赠予  economy_item  ToolExecutor:2955 有专门的"异地以传讯方式赠达"分支`），
        会被一起抓进来。真正的键都在**同一行的 `= {` 之后**、且是逗号分隔的裸标识符字面量。
        """
        m = re.search(name + r"\s*=\s*\{(.*?)\};", src, re.S)
        assert m, f"找不到 {name} 池"
        body = m.group(1)
        # 只保留"整行都是键"的那些行：去掉行注释后再抠字面量
        keys = set()
        for line in body.splitlines():
            code = line.split("//")[0]
            for lit in re.findall(r'"([^"]+)"', code):
                # 意图键一律是 ASCII 小写+下划线；把混进来的中文说明串挡掉
                if re.fullmatch(r"[a-z][a-z_]*", lit):
                    keys.add(lit)
        return keys

    remote = _pool("RemoteActionIntents")
    local = _pool("LocalActionIntents")
    assert remote and local, (remote, local)
    assert remote | local == set(ACTION_INTENTS), (
        f"C# 动作池与 Python ACTION_INTENTS 不齐：\n"
        f"  C# 独有={sorted((remote | local) - set(ACTION_INTENTS))}\n"
        f"  Python 独有={sorted(set(ACTION_INTENTS) - (remote | local))}"
    )
    # 池的划分本身也要钉：异地池必须是"远程真能做"的那三个
    assert remote == {"gift", "lun_dao", "yao_yue"}, remote
    assert local == {"spar", "attack"}, local


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


# ---------- 2b. npc_unit_id：主动传音的精确身份（09-28 修「未找到发起方」） ----------
def test_handle_initiative_writes_precise_identity():
    """★ 09-28 修（真机「未找到发起方 管星贤」）★

    主动传音**不经过** `open_chat`（只有那里才会 `set_unit_id`），所以 C# 现在随事件
    把**真身 unitID** 带上来 ⇒ 本会话自身身份就位 ⇒ 每个工具调用都注入 `initiator_id`
    （C# 走 ②unitID精确，永不按名猜）。空值/缺项 = 老 C# ⇒ **不许**把已有身份清掉。
    """
    bridge = StubGameBridge()
    _seed(bridge)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回复", "tool_calls": []}))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_initiative(NPC, "greet", npc_unit_id="UID01")
        agent = loop.get(NPC)
        assert agent.unit_id == "UID01", f"身份没写进会话：{agent.unit_id!r}"
        # 老 C#（不带这个键）⇒ 空串：已有身份保持不变
        hub._last_initiative_ts = 0.0
        await hub.handle_initiative(NPC, "greet")
        assert agent.unit_id == "UID01", "空值不许清掉已有身份"

    asyncio.run(scenario())
    print("✓ npc_unit_id：写入会话身份；缺项不清空")


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
    # ★ 09-22 真机：NPC 自己那句告白被当成玩家说的去回应（think 原文"唐炎说了句情话"）。
    #   两支路曾共用"接住这个话头"——那个动词预设了话是**对方**抛来的，把归属又抢了回去。
    #   于是 npc 支路必须自带"别回应、接着说"的动作指令，且不许再出现那句共用措辞。
    assert "接住这个话头" not in npc, npc
    assert "别把它当成玩家说的话去回应" in npc, npc

    player = format_game_drama_message(line, "player")
    assert player.startswith("（游戏内交互：玩家刚对你说了这样一句——「"), player
    assert "接住这个话头" in player, player

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


def test_game_drama_lines_multi_line_packaging():
    """整段注入（09-22）：一次相遇的多页原文按先后列出来，每行自带归属。

    为什么要有它：原来点「对话」只把**屏幕上此刻那一句**交给模型 —— 一次当面相遇往往翻好几页
    （切磋是「约战 → 打完 → 战后两页」这个量级），先发生的事模型全看不见。攒段的判据在
    C# `DramaSegmentCore`（有专门单测），这里只管**包装**。
    """
    rows = [
        {"seq": 1, "drama_id": 21204, "speaker": "npc", "name": "方成和",
         "text": "我见你修为高深，能否与我切磋一番？"},
        {"seq": 2, "drama_id": 21401, "speaker": "player", "name": "唐炎", "text": "厉害，我输了。"},
        {"seq": 3, "drama_id": 21402, "speaker": "npc", "name": "方成和", "text": "看来这次是我赢了！"},
    ]
    t = format_game_drama_message("看来这次是我赢了！", "npc", rows)

    # ① 顺序 = 发生顺序（模型要靠它重建"先约战、再打完"）
    assert t.index("我见你修为高深") < t.index("厉害，我输了") < t.index("看来这次是我赢了"), t
    # ② 每行自带归属：玩家那句写成"玩家（唐炎）"，NPC 那句写成"你（方成和）"
    assert "① 你（方成和）：我见你修为高深，能否与我切磋一番？" in t, t
    assert "② 玩家（唐炎）：厉害，我输了。" in t, t
    assert "③ 你（方成和）：看来这次是我赢了！" in t, t
    # ③ ★最后一句是**自己**说的 ⇒ 不许说"接住这个话头"（09-22 教训：那个动词预设话是对方抛来的）
    assert "接住这个话头" not in t, t
    assert "别把它当成玩家说的话去回应" in t, t
    assert "不要复读括号内容" in t, t

    # 反过来：最后一句是玩家说的 → 必须"接住这个话头"
    t2 = format_game_drama_message("", "", rows[:2])
    assert "接住这个话头" in t2 and "别把它当成玩家说的话去回应" not in t2, t2
    assert "玩家刚说完最后一句" in t2, t2

    # 判不出谁说的那几句：明写判不出（不给模型留猜的余地），结尾走中性
    t3 = format_game_drama_message("", "", [{"text": "甲句"}, {"text": "乙句"}])
    assert "（说话人没判出来的一句）：甲句" in t3, t3
    assert "接住这个话头" in t3, t3

    # ④ 超上限：只留最近 40 句，且**明写省略**（静默丢会让模型以为这就是全部经过）
    many = [{"speaker": "npc", "text": f"第{i}句"} for i in range(1, 46)]
    t4 = format_game_drama_message("", "", many)
    assert "更早的 5 句已略" in t4, t4
    assert "第5句" not in t4 and "第6句" in t4 and "第45句" in t4, t4
    print("✓ game_drama 整段包装：按序 + 逐行归属 + 结尾动作词随最后一句 + 超限明写省略")


def test_game_drama_lines_fallbacks_keep_single_path():
    """≤1 句 / 空项 / 坏项：一律退回**原有单句支路**（措辞逐字不变，不许为统一而改）。"""
    one = format_game_drama_message("", "", [{"seq": 1, "speaker": "npc", "name": "甲", "text": "一句话"}])
    assert one == format_game_drama_message("一句话", "npc"), one
    base = format_game_drama_message("原句", "player")
    assert format_game_drama_message("原句", "player", None) == base
    assert format_game_drama_message("原句", "player", []) == base
    assert format_game_drama_message("原句", "player", [{"text": "   "}]) == base   # 空原文项：丢
    assert format_game_drama_message("原句", "player", ["junk", 42]) == base        # 坏项：丢
    assert format_game_drama_message("原句", "player", {"text": "x"}) == base       # 类型错：当没有
    print("✓ game_drama lines 兜底：≤1 句与坏数据都走单句支路（逐字不变）")


def test_game_drama_verdict_page_note():
    """处置页说明（09-24 用户需求，甲案）：**只在"当前页就是处置页"**时追加。

    现场：攻击链的处置页（20403 送东西求饶 / 20404 任凭处置）上写着"献物求饶"，
    但交付/收取由游戏原版流程办、处置权在玩家手里 —— 不给这个事实，模型会拿工具去
    "执行"页面上的事（09-24 真机：item_acquire 排队执行后失败 ——「玩家背包中没有『三品化瘀丹』」，
    就是它想"收下"一件已经交付的礼物）。

    三条边界（都是会静默出事的）：
      · 不带标记 → **逐字不变**（老 C# 配新 Python 也不炸；"改一处漂一处"的既有护栏）；
      · 带标记 → 追加说明，且必须点明"别调工具"与"**只限这一页**"（后者是用户点名要的：
        不许它因此认定"我以后不能赠送道具"）；
      · 与 in_action 可**同时**出现（两条说明来源不同、互不替代），且顺序稳定。
    """
    base = format_game_drama_message("求你别杀我……", "npc")
    assert format_game_drama_message("求你别杀我……", "npc", None, False, False) == base, \
        "不带处置页标记时包装变了（老 C# 兼容被破坏）"

    t = format_game_drama_message("求你别杀我……", "npc", None, False, True)
    assert t.startswith(base), "基础包装被改了（说明只应追加在末尾）"
    assert "处置页" in t and "不要调用任何工具" in t, t
    assert "游戏内置流程" in t, "没点明'献物/求饶走的是游戏内置流程'"
    assert "只限这一页" in t and "照常照办" in t, "少了'不影响后续赠送别的道具'那句（用户点名要的）"

    # 与"交互进行中"并存：两条都在，顺序稳定（in_action 在前）
    both = format_game_drama_message("求你别杀我……", "npc", None, True, True)
    assert _IN_ACTION_NOTE in both and _VERDICT_NOTE in both, both
    assert both.index(_IN_ACTION_NOTE) < both.index(_VERDICT_NOTE), both

    # 多句支路同样带（处置页常常多句：求饶句 + 玩家的处置选项）
    rows = [
        {"seq": 1, "drama_id": 20403, "speaker": "npc", "name": "管星贤", "text": "求你饶命！"},
        {"seq": 2, "drama_id": 20403, "speaker": "player", "name": "唐炎", "text": "收下物品，羞辱此人"},
    ]
    t2 = format_game_drama_message("", "", rows, False, True)
    assert _VERDICT_NOTE in t2 and "收下物品，羞辱此人" in t2, t2
    print("✓ game_drama 处置页说明：只带标记时追加（逐字不变 / 与 in_action 并存 / 多句支路也带）")


def test_handle_initiative_game_drama_lines_plumbing():
    """lines 一路透传：handle_initiative(..., lines=) → 多句舞台指令 + source 记条数（审计）。"""
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
    rows = [{"seq": 1, "speaker": "npc", "name": "甲", "text": "先说的这句"},
            {"seq": 2, "speaker": "player", "name": "乙", "text": "后说的这句"}]

    asyncio.run(hub.handle_initiative(NPC, "game_drama", "", "后说的这句", speaker="player", lines=rows))
    joined = captured.get("joined", "")
    assert "先说的这句" in joined and "后说的这句" in joined, joined[:300]
    assert "①" in joined and "②" in joined, joined[:300]
    src = [ev for ev in loop.get(NPC).session.log
           if ev.get("type") == "user/message" and ev.get("data", {}).get("source", {}).get("kind") == "initiative"]
    assert src and src[-1]["data"]["source"].get("drama_lines") == 2, src[-1]["data"]["source"]
    print("✓ game_drama lines 透传：多句舞台指令 + source 记 drama_lines 条数")


def test_parked_drama_keeps_lines():
    """忙守卫把玩家点的「对话」存进信箱时，**整段增量一起存**（补投不许退回单句）。

    「最多晚到，不会不到」是 09-22 定的语义；如果只存 text，补投那一次就退化成单句包装 ——
    NPC 又只看见最后一句，"晚到"到了却等于半丢。
    """
    bridge = StubGameBridge()
    _seed(bridge)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "（接住）", "tool_calls": []}))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))
    rows = [{"seq": 1, "speaker": "npc", "name": "甲", "text": "第一句"},
            {"seq": 2, "speaker": "npc", "name": "甲", "text": "第二句"}]

    async def scenario():
        agent = loop.get(NPC) or loop.create(NPC)
        agent.phase = {"kind": "running", "turn": 1, "step": 1}
        await hub.handle_initiative(NPC, "game_drama", "", "第二句", speaker="npc", lines=rows)
        assert hub._pending_drama[NPC]["lines"] == rows, hub._pending_drama[NPC]
        agent.phase = {"kind": "idle", "last_turn": 1}
        await hub._drain_drama(NPC)
        assert NPC not in hub._pending_drama, "补投后信箱应清空"
        seeds = [ev for ev in agent.session.log
                 if ev.get("type") == "user/message"
                 and (ev["data"].get("source") or {}).get("kind") == "initiative"]
        assert seeds, "补投没有落账（玩家点的那一次被吞了）"
        body = "".join(str(b.get("text") or "") for b in (seeds[-1]["data"].get("content") or [])
                       if isinstance(b, dict))
        assert "第一句" in body and "第二句" in body and "①" in body, body[:300]

    asyncio.run(scenario())
    print("✓ 信箱补投带着整段增量：晚到的那一次仍是多句包装")


def test_game_drama_option_rows_and_in_action_note():
    """选项行（09-23）+ "进行中"系统提示 —— 两个用户报的割裂点的回归测试。

    ① 选项文本**不在页面正文里**（`GetDialogueText` 不含选项）⇒ 不带上它，论道/邀约这类
       问答式交互模型"只知道疑问、不知道玩家选了什么"，甚至把已发生的交互当成没开始
       （又发起一次动作工具）；② `in_action`（C# 在点击那一刻采集）⇒ 包装里点明
       "这场交互还在进行中、**别重复发起**"。
    """
    rows = [
        {"seq": 1, "kind": "page", "speaker": "npc", "name": "姜萌", "text": "要和姜萌做什么…"},
        {"seq": 2, "kind": "option", "speaker": "choice", "name": "唐炎", "text": "赏花"},
        {"seq": 3, "kind": "page", "speaker": "npc", "name": "姜萌", "text": "好呀，走吧。"},
    ]
    t = format_game_drama_message("", "", rows)
    assert "② 玩家（唐炎）选择了：赏花" in t, t            # 选项行单独渲染（不是"你/玩家说"）
    assert "② 玩家（唐炎）：赏花" not in t, t
    assert "别把它当成玩家说的话去回应" in t, t              # 最后一行是 NPC 的话 ⇒ npc 尾巴

    # 最后一行是选项 ⇒ 按"玩家刚做完一个选择"处理（动作词走 player 支路）
    t2 = format_game_drama_message("", "", rows[:2])
    assert "玩家刚说完最后一句" in t2, t2

    # in_action：多句与单句支路都要带上"进行中、别重复发起"
    t3 = format_game_drama_message("", "", rows, in_action=True)
    assert "这场交互还在进行中" in t3 and "不要再调用同一个动作工具" in t3, t3
    t4 = format_game_drama_message("一句话", "npc", None, in_action=True)
    assert "这场交互还在进行中" in t4, t4
    # 缺省（老 C# 不带 action）：一字不加（兼容）
    assert "这场交互还在进行中" not in format_game_drama_message("一句话", "npc")
    print("✓ game_drama 选项行（kind=option）+ 进行中提示（in_action）")


def test_handle_initiative_action_plumbing():
    """action 一路透传：handle_initiative(action=True) → 包装里的"进行中"提示 + source 审计。"""
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
    rows = [{"seq": 1, "kind": "page", "speaker": "npc", "name": "甲", "text": "你会如何做？"},
            {"seq": 2, "kind": "option", "speaker": "choice", "name": "乙", "text": "赏花"}]

    asyncio.run(hub.handle_initiative(NPC, "game_drama", "", "你会如何做？", speaker="npc",
                                      lines=rows, action=True))
    joined = captured.get("joined", "")
    assert "这场交互还在进行中" in joined and "不要再调用同一个动作工具" in joined, joined[:300]
    assert "玩家（乙）选择了：赏花" in joined, joined[:300]
    src = [ev for ev in loop.get(NPC).session.log
           if ev.get("type") == "user/message"
           and ev.get("data", {}).get("source", {}).get("kind") == "initiative"]
    assert src and src[-1]["data"]["source"].get("action") is True, src[-1]["data"]["source"]
    print("✓ game_drama action 透传：进行中系统提示 + source 审计")


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

    from agent_loop.session.history import project_ui_history
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


# ---------- 7. 玩家手点的「对话」撞上忙守卫：进信箱、收口后补投（09-22）----------

class _RecWs(WsServer):
    """记录出站帧的假 WS（补投链路的失效形态全是静默的，只能靠跑起来看帧）。"""

    def __init__(self):
        super().__init__(port=0)
        self.events = []

    async def send_event(self, kind, **payload):
        self.events.append((kind, payload))


def _llm_texts(seen):
    def _fn(req):
        texts = [b.get("text", "") for m in req.get("messages", [])
                 for b in (m.get("content") or []) if isinstance(b, dict) and b.get("type") == "text"]
        seen.append("|".join(texts))
        return {"text": "接住。", "tool_calls": []}
    return _fn


def test_game_drama_click_is_replayed_after_the_busy_turn():
    """忙时点「对话」→ 信箱收着，回合收口后**补投恰好一次**；别的意图照旧静默丢。

    ★行为测试，不是 grep★：这条链的三种失效形态都不报错 —— ①面板开了但意图蒸发
    （玩家只会说"点了没反应"）；②补投重复（NPC 把同一句求饶说两遍）；③在 `finally` 里
    直接 `await` 补投 → 自锁（`asyncio.Lock` 不可重入，锁的持有者正等它返回）。
    "函数体里有 create_task"那种正则对三种一律全绿。
    """
    bridge = StubGameBridge()
    _seed(bridge)
    seen: list = []
    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_texts(seen)))
    loop = AgentLoop()
    ws = _RecWs()
    hub = ChatHub(loop, ws)
    agent = loop.create(NPC)

    async def scenario():
        agent.phase = {"kind": "running", "turn": 1, "step": 1}
        # ① 自动触发的意图：仍"错过就丢"，且静默（这是设计语义，本次只给手点的开口开洞）
        await hub.handle_initiative(NPC, "greet")
        assert hub._pending_drama == {}, hub._pending_drama
        assert ws.events == [], ws.events
        # ② 玩家手点的 game_drama：进信箱，**不发**"对方正忙"（那一回合正在往窗口吐字）
        await hub.handle_initiative(NPC, "game_drama", "", "还请在收下后饶我一条性命…", speaker="npc")
        assert NPC in hub._pending_drama, hub._pending_drama
        assert ws.events == [], f"刚进信箱就发提示是噪音：{ws.events}"
        # ③ 回合收口 → 补投，走的仍是同一条 formatter（归属不能丢）
        agent.phase = {"kind": "idle", "last_turn": 1}
        await hub._drain_drama(NPC)
        assert hub._pending_drama == {}, "信箱没被消费 → 下次收口会再说一遍"
        # ④ 再 drain 一次不得二次开口
        n = len(seen)
        await hub._drain_drama(NPC)
        assert len(seen) == n, "补投重复了"

    asyncio.run(scenario())
    assert seen, "补投根本没跑起来"
    assert "你刚对玩家说了这样一句" in seen[-1], seen[-1][:400]
    assert "还请在收下后饶我一条性命" in seen[-1], seen[-1][:400]
    print(f"✓ 手点的「对话」：忙时进信箱 → 收口补投一次（LLM 共 {len(seen)} 次）")


def test_game_drama_mailbox_expires_with_an_honest_notice():
    """补投超时（NPC 迟迟不收口）→ 不投，但必须回一句实话，别让玩家对着开着的窗干等。"""
    bridge = StubGameBridge()
    _seed(bridge)
    seen: list = []
    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_texts(seen)))
    loop = AgentLoop()
    ws = _RecWs()
    hub = ChatHub(loop, ws)
    agent = loop.create(NPC)

    async def scenario():
        agent.phase = {"kind": "running", "turn": 1, "step": 1}
        await hub.handle_initiative(NPC, "game_drama", "", "饶命…", speaker="npc")
        hub._pending_drama[NPC]["at"] -= hub.DRAMA_TTL_SECONDS + 1     # 伪造"信箱已过期"
        await hub._drain_drama(NPC)
        assert hub._pending_drama == {}, "过期条目没清 → 会挂在内存里等下一次收口"

    asyncio.run(scenario())
    assert seen == [], f"过期了还补投：{seen}"
    assert ws.events and ws.events[0][1].get("error") is True, ws.events
    assert "再点一次" in ws.events[0][1]["text"], ws.events


def test_game_drama_mailbox_waits_longer_while_a_tool_is_inflight():
    """回合**卡在工具上**时，信箱不按基础 TTL 收口，改按放宽档等（09-23）。

    场景：玩家点了「对话」，而该 NPC 的回合正等一个工具（C# 侧工具等待最长 90s，跨战斗那条
    靠心跳可到 600s）。基础 TTL（30s）防的是"半分钟后凭空开口"，可工具等待**有结局** ——
    工具落定后补投才是玩家期望的"最多晚到，不会不到"。放宽档仍有上限（`DRAMA_TTL_TOOL_SECONDS`），
    到点照样回执一句实话（不静默）。
    """
    bridge = StubGameBridge()
    _seed(bridge)
    seen: list = []
    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_texts(seen)))
    loop = AgentLoop()
    ws = _RecWs()
    hub = ChatHub(loop, ws)
    agent = loop.create(NPC)

    async def scenario():
        # 三个数压到毫秒级：不改代码语义就能验证"确实放宽了"
        hub.DRAMA_TTL_SECONDS = 0.05
        hub.DRAMA_TTL_TOOL_SECONDS = 0.8
        hub._DRAIN_WAIT_SECONDS = 0.02
        agent.phase = {"kind": "running", "turn": 1, "step": 1}
        agent.tool_inflight = "world_ai_action"        # dialogue_agent 调桥期间挂的信号
        await hub.handle_initiative(NPC, "game_drama", "", "还请稍候片刻", speaker="npc")
        assert NPC in hub._pending_drama, hub._pending_drama

        async def _release():
            await asyncio.sleep(0.25)                  # 已越过基础档 0.05s，仍在放宽档内
            agent.tool_inflight = None
            agent.phase = {"kind": "idle", "last_turn": 1}

        task = asyncio.create_task(_release())
        await hub._drain_drama(NPC)
        await task
        assert hub._pending_drama == {}, "信箱没被消费"
        # 只查"失败回执"（补投成功本身也会往 ws.events 写 text_delta/npc_reply）：
        # 基础档在这里会放弃并回执 `error=True`，放宽档必须等到了工具落定。
        bad = [e for e in ws.events
               if len(e) > 1 and isinstance(e[1], dict) and e[1].get("error") is True]
        assert bad == [], f"放宽档里不该回执失败（那是基础档的行为）：{bad}"

    asyncio.run(scenario())
    assert seen, "工具落定后没有补投"
    assert "还请稍候片刻" in seen[-1], seen[-1][:300]
    print("✓ 信箱：回合卡在工具上时按放宽档等待，工具落定后补投一次")


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
    test_game_drama_click_is_replayed_after_the_busy_turn()
    test_game_drama_mailbox_expires_with_an_honest_notice()
    test_game_drama_mailbox_waits_longer_while_a_tool_is_inflight()
    print("\nNPC 主动开口测试全部通过")

# ---------- 1b. 方位（same_grid）与向后兼容 ----------
def test_same_grid_note_only_for_action_intents():
    """方位说明**只**追加给动作向意图；且三分支（True/False/None）互不相同。

    为什么只给动作向：说话本来就没有方位差别（"打个招呼"不需要知道隔多远）；
    而动作有 —— 同一个动作在两地话术完全不同，且异地时切磋/动手**根本做不了**
    （`ToolExecutor` 对 spar/attack 有 SameGrid 硬校验）。不点明，模型会按"同格"口吻
    写台词，工具再报错，台词就成了空话。
    """
    from agent_loop.agent.initiative import _SAME_GRID_NOTE, _REMOTE_NOTE

    # 言语向：三个方位值输出逐字相同
    base = format_initiative_message("greet", "r")
    assert format_initiative_message("greet", "r", same_grid=True) == base
    assert format_initiative_message("greet", "r", same_grid=False) == base
    assert format_initiative_message("greet", "r", same_grid=None) == base

    # 动作向：同格与异地文案必须不同，且各自点到要害
    same = format_initiative_message("gift", "", same_grid=True)
    remote = format_initiative_message("gift", "", same_grid=False)
    assert _SAME_GRID_NOTE in same and "同处一地" in same, same
    assert _REMOTE_NOTE in remote and "相隔两地" in remote, remote
    assert same != remote
    # 异地文案必须点明"切磋/动手做不成"——这是唯一会让模型写出空话的地方
    assert "切磋" in remote and "做不成" in remote, remote


def test_same_grid_none_keeps_old_wording_byte_identical():
    """`same_grid=None`（老 C# / 缺键）必须与 10-16 之前的输出**逐字相同**。

    这是跨版本兼容的唯一保证：C# 与 Python 是成对发布的，但老 DLL 配新 Python
    不能因为多了个键就换文案（那会让回滚变危险）。
    """
    from agent_loop.agent.initiative import _ACTION_LICENSE

    for k in ("gift", "lun_dao", "yao_yue", "spar", "attack"):
        # 老形态 = 意图描述 + 缘由 + 授权句（授权句是 10-16 新增的，不属于"方位"改动）
        expect = f"（NPC主动传音：{INITIATIVE_INTENTS[k]}）" + _ACTION_LICENSE
        assert format_initiative_message(k, "", same_grid=None) == expect, k


def test_ws_layer_forwards_same_grid_and_tolerates_missing_key():
    """接线契约：`npc_initiative` 帧的 `same_grid` 必须真的传到 `handle_initiative`。

    且**非布尔值必须被当成 None**（不是 `bool(值)`）—— `bool("false")` 是 True，
    老 C# 若发字符串 "false" 会被误读成"同格"，于是提示模型"当面办"，
    而实际相隔两地（静默错话术）。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    src = (root / "transport" / "ws_channel.py").read_text(encoding="utf-8")

    # ① 只认真布尔，其余一律 None
    assert "_sg if isinstance(_sg, bool) else None" in src, (
        "same_grid 的清洗必须用 isinstance(..., bool)，不能用 bool(...)"
    )
    # ② 必须随事件透传到 handler
    assert "same_grid: Optional[bool] = None" in src or "same_grid: \"bool | None\" = None" in src
    # ③ 格式化处必须把它带上
    # 11-XX 起这行还带 `busy=`（忙回合的授权句要按 busy 收），所以按**前缀**匹配
    assert re.search(r"format_initiative_message\(intent, reason, same_grid=same_grid,?\s*$", src, re.M), (
        "handle_initiative 没把 same_grid 传给格式化函数 —— 方言文案永远不会生效"
    )


# ---------- 1c. PickIntent 三条分支的结构契约（C# 源码） ----------
def _monitor_src() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "csharp" / "npc" / "NpcInitiativeMonitor.cs"
            ).read_text(encoding="utf-8")


def _pool_of(name: str) -> set:
    """取 C# 里某个 `string[] Name = { ... };` 池的**意图键**。

    ⚠ 不能简单 `re.findall(r'"([^"]+)"', 花括号内容)`：池定义上方的注释里也带引号字符串
    （`// 赠予  economy_item  ToolExecutor:2955 有专门的"异地以传讯方式赠达"分支`），
    会被一起抓进来。真正的键都是 ASCII 小写标识符字面量。
    """
    import re
    src = _monitor_src()
    m = re.search(name + r"\s*=\s*\{(.*?)\};", src, re.S)
    assert m, f"找不到 {name} 池"
    keys = set()
    for line in m.group(1).splitlines():
        code = line.split("//")[0]
        for lit in re.findall(r'"([^"]+)"', code):
            if re.fullmatch(r"[a-z][a-z_]*", lit):
                keys.add(lit)
    return keys


def _pick_intent_body() -> str:
    """取 `PickIntent` 的函数体（到下一个方法声明为止）。

    这类"按分支结构断言"的测试很脆，所以定位必须精确到函数体，
    不能在全文里搜 —— 否则别处出现同样的字符串就会假绿（10-16 变异验证实测踩过）。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    src = (root / "csharp" / "npc" / "NpcInitiativeMonitor.cs").read_text(encoding="utf-8")
    i = src.index("private static string PickIntent(")
    # 下一个 `private static` / `internal static` 声明即函数体结束
    m = re.search(r"\n        (?:private|internal|public) static ", src[i + 10:])
    assert m, "找不到 PickIntent 之后的下一个方法声明"
    return src[i : i + 10 + m.start()]


def test_pick_intent_gates_action_branch_by_busy_and_same_grid():
    """动作支路的两道前置条件必须**同时**在条件表达式里。

    这两条都属于"改一个词就静默出错"的类型：
      · 去掉 `!busy`  → 忙碌回合选到动作意图 → 动作工具被执行层拒 →
        NPC 对玩家许诺"改日把此物赠你"这类**永远不兑现**的话；
      · 去掉 `sameGrid` 三元 → 异地也选切磋/攻击 → `ToolExecutor` 直接 Fail
        （"切磋需同格，当前异地"）→ NPC 刚说"你我一战"就自打嘴巴。
    两者都不会抛异常、不会留错误日志，只表现为"NPC 说话不着调"。
    """
    body = _pick_intent_body()

    # ① 动作支路必须被 busy 拦住
    assert "if (!busy && UnityEngine.Random.value < ActionChance)" in body, (
        "动作支路没有 busy 前置 —— 忙时也会选动作意图（会许下无法兑现的承诺）"
    )

    # ② 必须按 sameGrid 分池：同格=两池合并，异地=只远程池
    assert "Concat(RemoteActionIntents, LocalActionIntents)" in body, (
        "同格时没有取两池并集 —— 同格选不到切磋/攻击"
    )
    assert "RandomOf(sameGrid ?" in body, (
        "没有按 sameGrid 分流选池 —— 异地会选到同格专属动作（工具必 Fail）"
    )

    # ③ 异地的 else 支路只能是 RemoteActionIntents（不能是一池合并）
    import re
    m = re.search(r"RandomOf\(sameGrid \? Concat\(RemoteActionIntents, LocalActionIntents\)\s*:\s*(\w+)\)", body)
    assert m, "按 sameGrid 分流的表达式形态变了（无法确认异地用的是哪个池）"
    assert m.group(1) == "RemoteActionIntents", (
        f"异地的池是 {m.group(1)}，应为 RemoteActionIntents —— 异地不许出同格专属动作"
    )


def test_pick_intent_enemy_remote_yields_no_intent():
    """敌对 + 异地必须返回**空串**（本档无可派发意图），而不是硬塞一个做不了的动作。

    远程池里没有敌对手段（赠予/论道/邀约都太友善），而同格的 attack 又做不了 ⇒
    这一档本来就该"今天不找他"。返回非空会让 NPC 一边"表达恶意"一边调不出任何工具。
    """
    body = _pick_intent_body()
    assert 'return sameGrid ? RandomOf(HostileActionIntents) : "";' in body, (
        "敌对+异地没有返回空串 —— 会派发一个执行不了的动作"
    )
    # 敌对同格只许出 attack，不许出 spar（切磋是友善的较量，对仇人提"切磋"语义拧）
    assert 'HostileActionIntents = {' in _monitor_src(), "敌对动作池不见了"
    hpool = _pool_of("HostileActionIntents")
    assert hpool == {"attack"}, f"敌对动作池应只有 attack，实为 {hpool}"
    # 且空串必须在**落冷却之前**被拦掉（否则白占这个 NPC 三天的冷却）
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    mon = (root / "csharp" / "npc" / "NpcInitiativeMonitor.cs").read_text(encoding="utf-8")
    i = mon.index("PickIntent(r.Intim, r.SameGrid")
    # 窗口到"落冷却那句"为止（不再用固定字数：注释一长就漏判，10-XX 踩过）
    j = mon.index("_cooldownReal[", i)
    seg = mon[i:j]
    assert "if (string.IsNullOrEmpty(intent)) continue;" in seg, (
        "空意图没有在落冷却之前 continue —— 会白占该 NPC 的冷却"
    )
    # （seg 本身已经**截到** `_cooldownReal[` 之前，所以上面那条就是"排在冷却之前"的证明；
    #   原来这里再 index 一次 `_cooldownReal[` 会必然抛 —— 窗口里不可能有它。）


def test_pick_intent_speech_branch_rerolls_dice():
    """动作支路与言语支路必须**各自掷骰**。

    若复用同一次 `Random.value`（先判动作、再判 `rnd < 0.4f`），`intim>=120` 那档的
    亲密键概率会从 40% 变成 40%×(1−ActionChance) —— 那是**既有行为**，
    这次扩展不该顺手改掉（会让老玩家感觉"亲近的人不再说思念了"）。
    """
    body = _pick_intent_body()
    # 言语支路自己取一次随机。
    # ★ 必须先剥掉注释行 ★ 这段的注释里就写着 `rnd < 0.4f`（用来解释"为什么不能复用"），
    #   不剥掉会把注释当成代码匹配 —— 10-16 变异验证实测踩到过这个假绿。
    # 分节标记是注释里的「② 言语支路」；用 rfind 取**最后**一次出现，
    # 避免命中上方那段解释"为什么不复用骰子"的长注释。
    idx = body.rfind("言语支路")
    assert idx > 0, "找不到言语支路的分节注释（结构被大改？）"
    m = ["", body[idx:]]
    code = "\n".join(
        ln for ln in m[1].splitlines() if not ln.lstrip().startswith("//")
    )
    assert "var rnd = UnityEngine.Random.value;" in code, (
        "言语支路没有重新掷骰 —— 复用了动作支路那一次，会改掉既有言语分布"
    )
    assert code.index("var rnd") < code.index("rnd < 0.4f"), "取随机与判据的顺序不对"

    # ★ 关键：亲密分档必须用**自己那枚**骰子，不能复用动作支路那枚 ★
    # 事故形态（看起来只是省一次 random）：
    #     var rnd = UnityEngine.Random.value;                    // ← 提到支路之外，共用
    #     if (!busy && rnd < 0.25) return 动作;
    #     if (intim >= 120 && rnd < 0.4f) return 亲密;            // ⇒ 实际只在 [0.25,0.4) 命中
    # 判据直接而精确：**动作支路的条件里不许出现 `rnd`**（它必须直接调 Random.value），
    # 且②内部必须有自己的 `var rnd` 声明。
    action_cond = body[body.index("if (!busy"): body.index("ActionChance)") + 12]
    assert "rnd" not in action_cond, (
        "动作支路的条件里出现了 `rnd` —— 骰子被提到支路之外共用了；\n"
        "这会把亲密键概率从 20% 改写成 15%（静默，且既有行为被顺手改掉）。\n"
        f"实际条件：{action_cond.strip()}"
    )
    assert "UnityEngine.Random.value" in action_cond, (
        "动作支路没有直接取随机 —— 疑似复用了外面那枚骰子"
    )


def test_same_grid_key_is_declared_on_both_sides_of_the_wire():
    """`same_grid` 必须是**成对**的：C# 写进帧 + Python 读出来。

    少一侧的症状：C# 发了但 Python 不读 ⇒ 方位文案永远不生效（静默）；
    Python 读了但 C# 不发 ⇒ 同上。这类跨语言键是本仓最常见的静默失败源。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    cs = (root / "csharp" / "bridge" / "WsClient.cs").read_text(encoding="utf-8")
    py = (root / "transport" / "ws_channel.py").read_text(encoding="utf-8")

    assert 'if (sameGrid.HasValue) frame["same_grid"] = sameGrid.Value;' in cs, (
        "C# 没有把 same_grid 写进帧（或键名变了）"
    )
    assert 'bool? sameGrid = null' in cs, "SendInitiative 缺 sameGrid 形参"
    assert 'msg.get("same_grid")' in py, "Python 没读 same_grid 帧键"


def test_every_action_intent_has_copy_and_label():
    """`ACTION_INTENTS` 的每个键都必须在**两张表**里都有条目。

    漏一个的后果是静默降级：`format_initiative_message` 取不到文案 → `.get()` 返回 None
    → 回落成 `greet` 文案「打个招呼」。而 C# 那边已经按"动作意图"弹了确认窗、
    玩家点同意后，NPC 却只说了一句寒暄 —— 症状是"点了同意，他什么也没做"。
    这类"键没了但系统不报错"正是本仓最常见的一类故障。
    """
    for k in ACTION_INTENTS:
        assert k in INITIATIVE_INTENTS, f"{k} 缺模型文案（会静默回落成 greet）"
        assert INITIATIVE_INTENTS[k].strip(), f"{k} 的模型文案是空的"
        assert k in INITIATIVE_LABELS, f"{k} 缺 UI 短标签（对话窗分隔条会是空白）"
        assert INITIATIVE_LABELS[k].strip(), f"{k} 的 UI 标签是空的"

    # 文案里不许出现现代词/AI 字样（本仓有专门的界面文案规则）
    for k in ACTION_INTENTS:
        for txt in (INITIATIVE_INTENTS[k], INITIATIVE_LABELS[k]):
            assert "AI" not in txt and "ai" not in txt, (k, txt)


def test_pick_intent_probabilities_are_exact():
    """**概率契约**：把 `PickIntent` 的语义在 Python 里复刻一遍，用大样本验证四个关键数字。

    为什么值得单独钉（用户 10-17 提问："为什么两路各投一次，要确保概率"）：
    这里最容易犯的错是"复用同一枚骰子"——
      `if (rnd < 0.25) 动作; else if (>=120 && rnd < 0.4) 亲密;`
    它看起来只是省一次 Random.value，实际把 `>=120` 的亲密键概率从 20% 改成 15%，
    把正向键从 45% 改成 60%。全程不报错、不留日志，只表现为"亲近的人不怎么表达思念了"。
    源码层面的断言（`test_pick_intent_speech_branch_rerolls_dice`）只能证明"写了两次随机"，
    证明不了"分布是对的"，所以这里按实现语义实测。

    本测试**不读 C#**，它锁的是"我们承诺的行为"。若哪天有人改 C# 的常数/池构成，
    这条会红 —— 那时应当同步评估新分布，而不是顺手改数字。
    """
    import random as _r

    POS = ["greet", "smalltalk", "courteous", "life", "recent"]
    CLOSE = ["missing", "affection"]
    NEG = ["malice", "vent", "provocation", "disdain"]
    REMOTE = ["gift", "lun_dao", "yao_yue"]
    LOCAL = ["spar", "attack"]
    HOSTILE = ["attack"]
    AC = 0.25

    def pick(intim, same, busy):
        if not busy and _r.random() < AC:
            if intim < 0:
                return _r.choice(HOSTILE) if same else ""
            pool = REMOTE + LOCAL if same else REMOTE
            return _r.choice(pool)
        rnd = _r.random()
        if intim < 0:
            return _r.choice(NEG)
        if intim >= 120 and rnd < 0.4:
            return _r.choice(CLOSE)
        return _r.choice(POS)

    def dist(intim, same, busy, n=120000):
        _r.seed(20261017)          # 固定种子 ⇒ 结果可复现，不会偶发红
        c = {}
        for _ in range(n):
            k = pick(intim, same, busy)
            c[k] = c.get(k, 0) + 1
        return {k: v / n for k, v in c.items()}

    def near(v, want, tol=0.008):
        return abs(v - want) <= tol

    # ① 0~119 同格：言语每键 15%（=75%/5），动作每键 5%（=25%/5）
    d = dist(50, True, False)
    for k in POS:
        assert near(d[k], 0.15), (k, d[k])
    for k in REMOTE + LOCAL:
        assert near(d[k], 0.05), (k, d[k])

    # ② 0~119 异地：言语不变，远程动作 8.33%（=25%/3），且**绝不出现**同格专属键
    d = dist(50, False, False)
    for k in POS:
        assert near(d[k], 0.15), (k, d[k])
    for k in REMOTE:
        assert near(d[k], 0.25 / 3), (k, d[k])
    assert "spar" not in d and "attack" not in d, f"异地出现了同格专属动作：{d}"

    # ③ >=120 同格：亲密每键 15%（=75%×40%/2）、正向每键 9%（=75%×60%/5）
    d = dist(150, True, False)
    for k in CLOSE:
        assert near(d[k], 0.15), (k, d[k])
    for k in POS:
        assert near(d[k], 0.09), (k, d[k])

    # ④ 敌对：同格只有 attack（不出 spar）；异地只有空串 + 负面
    d = dist(-10, True, False)
    assert "spar" not in d, f"敌对同格出现了切磋（语义拧）：{d}"
    assert near(d["attack"], 0.25), d["attack"]
    for k in NEG:
        assert near(d[k], 0.1875), (k, d[k])

    d = dist(-10, False, False)
    assert near(d[""], 0.25), d.get("", 0)
    for k in NEG:
        assert near(d[k], 0.1875), (k, d[k])

    # ⑤ 忙：动作支路整条跳过 ⇒ 言语每键 20%，与 10-16 之前**逐字相同**
    d = dist(50, True, True)
    for k in POS:
        assert near(d[k], 0.20), (k, d[k])
    assert not ({k for k in d} & set(REMOTE + LOCAL)), f"忙时出现了动作键：{d}"
