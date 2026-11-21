# -*- coding: utf-8 -*-
"""聊天帧缓冲队列的**接线契约**（Python 侧读源码钉住 C#）。

背景：玩家在群里 / 单聊里说一句就关窗（去打架 / 换界面），Python 侧那一轮照跑；
`room_say` / `text_delta` / `room_state` / `step` 到达时窗口关着 → 被 `!IsOpen` 守卫丢掉。
重开窗的 `open_room` / `open_chat` 重放能把**说完的话**补上，补不回来的是两样：
① **正在说的那半截**（`text_delta`）；② **在跑什么 / 谁在说**（speak 状态、工具折叠、思考）。
09-17 由一个队列统一服务两个世界（原 `RoomFrameQueue`）；**同日二次改造**把单聊从
"只记关窗期间的帧"改成"**整轮记录**"（含玩家自己的输入与关窗前的工具行），代价是必须
配一道**渲染闸**（见下）：

  · `UI/ChatFrameQueue.cs` —— 纯逻辑队列：
      群聊（键 room_id）：按轮组织 / baseline 判决；
      单聊（键 npc_id）：整轮记录（`user_say` 开段 / `npc_reply` 闭段 / step 回合号推进隐式闭段），
      delta **按 part 合并**成累计文本，另有段闭合代数（识别"账本快照过期"）。
  · `UI/ChatFrameTee.cs`   —— 常驻静态订阅者：群帧只在"没人显示这个群"时收；
    **单聊帧与是否显示无关，一律收**（记录归 tee，"谁负责画"归渲染闸）。
  · `ChatPresenter.SendOpenChatReplay` / `DrainRoomFrames` / `DrainSoloFrames` —— 重放落地之后消费。
  · `ChatPresenter._replayGate` —— 单聊渲染闸：开窗请求发出前置位、回执里消费完落位；
    闸门期间直播不画帧（否则同一帧会被"重演 + 直播"画两遍）。

规则本身由 C# 单测工程 `csharp/tests/ChatFrameQueueTests` 直接跑（101 断言）。
**本文件锁的是接线** —— 每条静默失败都有一个位置在等：

  A. 消费点必须在 `ReplaceHistory` **之后**（先消费会被整个擦掉）；
  B. 群消费在 `SetBusy(false)` **之后**（未闭合轮的 busy 是真的）；单聊消费在
     `ClearReplayGate` / `EnterSelecting` **之前**；
  C. 群帧判据 `AbChatPanel.IsShowingRoom`：正显示时不许缓冲；**单聊没有这条判据**
     （二次改造删掉的就是它：那个开关一旦残留为真，帧只进直播不进队列，事后无迹可查）；
  D. 单聊 `npc_reply` 段闭合信号必须真的送进队列；
  E. 渲染闸：置位在请求之前，**每个出口**都要落位（漏一个 = 该窗口直播永久静默）；
  F. 玩家发言帧（`user_say`）必须在发送处本地记一次（单聊没有回声帧）；
  G. 批量重建（历史重放 / 整段重演）之后要"钉底保持"，否则视图停在顶部；
  H. 生命周期：群解散清桶 / 换存档清全部；
  I. 单聊"直播优先"的欠账分支要 `ForgetSolo` 丢段（否则过期段会在未来安静重放里演成幽灵泡）。

外加一条**跨语言契约**：`ChatFrameTee.Decode` 解构的字段名必须与 Python 侧实际发的键
一一对上（`_emit_say` / `_emit_state` / `_run_turn` 的 step 钩子）—— 帧字段名漂移是
**完全静默**的（解构出空串 / -1，不报错）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"
QUEUE = CS / "UI" / "ChatFrameQueue.cs"
TEE = CS / "UI" / "ChatFrameTee.cs"
PRESENTER = CS / "UI" / "ChatPresenter.cs"
WINDOW = CS / "UI" / "ChatWindow.cs"
AB_PANEL = CS / "UI" / "AbChatPanel.cs"
VIEWS = CS / "UI" / "ChatItemViews.cs"
ROOM_FLOW = ROOT / "room" / "room_flow.py"
MODMAIN = CS / "app" / "ModMain.cs"
TESTPROJ = CS / "tests" / "ChatFrameQueueTests"
RUNNER = ROOT / "scripts" / "dev" / "run_csharp_tests.py"
WS = ROOT / "transport" / "ws_channel.py"
DIALOGUE = ROOT / "agent" / "dialogue_agent.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _method(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员正文（含花括号）。签名必须唯一。"""
    src = _strip_comments(src)
    assert src.count(signature) == 1, "签名不唯一（%d 次）：%s" % (src.count(signature), signature)
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError("大括号不配对：" + signature)


# ----------------------------------------------------------------------
# A. 队列：纯逻辑（能被单测工程链接的前提）
# ----------------------------------------------------------------------

def test_queue_is_pure_logic_no_unity_no_newtonsoft():
    """`ChatFrameQueue.cs` 必须零 Unity / 零 Newtonsoft 依赖。

    单测工程把它当源码链接进 net8.0 控制台程序跑（不需要 Unity）——混进任何
    `using UnityEngine` / `JObject` 都会让那条路整个断掉，而断掉的表现是
    "队列从此没有测试"，静默且致命。
    """
    src = _read(QUEUE)
    # 剥注释再查：文件头注释里会**提到**这些词（"零 Unity / 零 Newtonsoft 依赖"），
    # 那不是依赖，是说明。
    code = _strip_comments(src)
    for banned in ("using UnityEngine", "MonoBehaviour", "GameObject",
                   "UnityEngine.", "Newtonsoft", "JObject"):
        assert banned not in code, f"队列必须保持纯逻辑，发现了：{banned}"
    assert "namespace AgentLoopBridge" in src


def test_queue_keeps_the_room_hard_rules():
    """群聊三条规则必须在源码里立着（规则本身由 C# 单测跑，这里只是防被整体删改）。"""
    src = _read(QUEUE)
    # ① 按轮组织：speak 开轮 / end 闭轮 / 无归属 delta 不收
    assert 'Phase == "speak"' in src and 'Phase == "end"' in src, "轮的开关判据不见了"
    assert "OpenRound == 0" in src, "未闭合轮的判据不见了"
    # ② 只裁 delta：裁剪函数里不许出现 room_say / room_state / step 的整类删除
    trim = _method(src, "private static void TrimOpenDeltas(Bucket b)")
    assert '"text_delta"' in trim, "delta 限流没钉在 text_delta 上"
    assert "room_say" not in trim and "room_state" not in trim and '"step"' not in trim, \
        "只裁 delta —— 关键帧（say/state/step）不许裁"
    # ③ 闭合轮整轮丢（不撕结构）
    assert "RemoveAll(f => f.Round == rid)" in src, "闭合轮裁剪必须整轮删（撕开会让 delta 变孤儿）"
    # 09-17 统一：step 与 delta 同规则（归轮 / 无归属不收）
    assert 'f.Event == "text_delta" || f.Event == "step"' in src, \
        "step 必须跟着它那一轮同留同丢（与 delta 同一条归轮规则）"


def test_queue_keeps_the_solo_segment_rules():
    """单聊"整轮记录"的四条规则必须在源码里立着（09-17 二次改造后的形态）。"""
    src = _read(QUEUE)
    # ① npc_reply = 段闭合信号（作废整段；自身不入段），并且**推进段闭合代数**
    assert 'if (f.Event == "npc_reply")' in src, "段闭合信号不见了（整段作废的判据）"
    assert "InvalidateSegment(sb);" in src, "npc_reply 没有真的作废段"
    # ② step 回合号推进 → 旧段隐式作废（死段不留），同样走 InvalidateSegment（代数要动）
    assert "f.Turn > b.SegmentTurn" in src, "隐式闭合（回合号推进清段）不见了"
    assert src.count("InvalidateSegment(b);") >= 2, \
        "回合边界（npc_reply / 隐式闭合）都必须走 InvalidateSegment（只 Clear 会让代数不动 ⇒ 快照过期兜底失效）"
    # ③ user_say = 玩家发言（本地帧）：段**重置**（且不推进代数 —— 那只是新一轮开始）
    assert 'if (f.Event == "user_say")' in src, "玩家发言帧的段重置不见了"
    assert "ClearSegment(b0);" in src, "user_say 没有重置段（会把上一轮的残留带进这一轮）"
    assert "ClearSegment(b0);\n                b0.Segment.Add(f);" in src, "user_say 自己必须入段"
    # ④ delta 按 part 合并（且 struct 必须写回表里 —— 漏写回是"合并了但没人看得见"）
    assert "AppendDeltaText(" in src, "delta 合并不见了（记整轮时逐帧列表会被裁掉开头）"
    assert "b.Segment[b.Segment.Count - 1] = last;" in src, \
        "ChatFrame 是 struct：合并后必须写回表里，否则改的是副本"
    assert "MaxSoloTextChars" in src and "MaxSoloFrames" in src, "单聊段的上限常量不见了"
    assert "MaxSoloDeltas" not in src, \
        "逐帧 delta 限流必须一并删掉：记整轮时它裁的是**最旧**的 delta（等于把正文砍掉开头）"
    # ⑤ 段闭合代数：消费侧靠它识别"open_chat 快照已过期"
    assert "ClosedEpoch++" in src and "internal static int SoloEpoch(" in src, "段闭合代数不见了"
    # 接口完备：收 / 取 / 弃 / 计数 / 代数
    for name in ("OfferSolo", "TakeSolo", "ForgetSolo", "CountOfSolo", "SoloEpoch"):
        assert name in src, f"单聊桶少了接口：{name}"


# ----------------------------------------------------------------------
# B. 接线：收帧分流 + 消费时序 + 生命周期
# ----------------------------------------------------------------------

def test_tee_routes_room_and_solo_frames():
    """tee：两路分流（群帧 / 单聊帧）+ 白名单 + 群帧的"没人显示才收" + 单聊"始终记"。"""
    src = _read(TEE)
    code = _strip_comments(src)   # 判据检查必须剥注释：文件头**提到**这些名字（说明为什么删掉）
    # 白名单：群四帧 + 单聊两帧 + npc_reply / user_say
    for ev in ('"room_say"', '"room_state"', '"text_delta"', '"step"'):
        assert ev in src, f"白名单少了 {ev}"
    assert '"npc_id"' in src and '"room_id"' in src, "两路分流的键（room_id / npc_id）不见了"
    # ★ 09-17 二次改造：单聊**没有**"谁在显示"判据（那个开关一旦残留为真就是静默丢帧）
    assert "IsShowingSolo" not in code, \
        "单聊帧不许再按'谁在显示'分流：记录归 tee，谁负责画归渲染闸（见 ChatFrameTee 文件头）"
    assert "IsShowingNpc" not in code, "单聊的显示判据已废弃，不许回潮"
    # 群帧仍保留显示判据（群聊有 baseline + room_say 终稿覆盖两套去重，不在本次改造范围）
    assert "IsShowingRoom" in code, "群帧的显示判据不见了"
    assert "if (IsShowingRoom(roomId)) return;" in code, "正显示这个群时不许缓冲"
    assert "if (!IsShowingRoom(roomId))" not in code, "判据反向 = '重开又演一遍'的经典写法"
    # 单聊：一律收（判据删掉之后，OfferSolo 必须在没有前置 return 的位置）
    solo_tail = code[code.index('string npcId = (string)payload["npc_id"]'):]
    i_offer = solo_tail.index("ChatFrameQueue.OfferSolo(npcId, Decode(ev, payload));")
    assert "IsShowing" not in solo_tail[:i_offer], "单聊分支在收帧前又插了显示判据（静默丢帧）"
    # npc_reply 段闭合信号必须真的送进队列（只判存在不判送达 = 分支里什么都不做也能过）
    assert "ChatFrameQueue.OfferSolo(npcId, new ChatFrame { Event = ev });" in src, \
        "npc_reply 的段闭合信号没有真的送进队列（残留段没人清）"
    # step 按 kind 过滤（sys/ctx 是输入侧折叠，只实时不重放）：判据**同源**在队列侧
    # （`ChatFrameQueue.IsPlayableStepKind`），tee 只转调 —— 见 `test_gate_swallows_only_buffered_frames`
    assert "ChatFrameQueue.IsPlayableStepKind(" in code, "step 的 kind 白名单没走队列侧同源判据"
    kinds = _method(_read(QUEUE), "internal static bool IsPlayableStepKind(string kind)")
    for k in ('"tool_call"', '"tool_result"', '"think"', '"text"'):
        assert k in kinds, f"step 白名单少了 kind={k}"
    assert '"sys"' not in kinds and '"ctx"' not in kinds, \
        "sys/ctx 是'只实时不重放'的输入侧折叠，不许进帧缓冲"
    attach = _method(src, "public static void Attach(WsClient ws)")
    assert "_attached" in attach and "UiEvent += OnUiEvent" in attach, "Attach 必须幂等且真的订阅"
    # ★ 换绑（2026-09-21 实机事故）：ModMain.Init 每次进世界 / 读档都会 new 新的 WsClient，
    #   初版 `if (_attached) return;` 会让 tee 永远订阅在第一个已废弃实例上 —— 整场失聪：
    #   npc_reply 清段送不到（残留 user_say 在重开窗重演成重复气泡 + 卡死的「NPC 回应中」），
    #   关窗期间的帧也一帧收不到（重演段"玩家输入=1 工具/思考=0 流式文本=0"是它的指纹）。
    #   钉两条：同一实例幂等跳过（ws == _ws 短路）+ 换实例先摘旧订阅（UiEvent -=）。
    assert "ws == _ws" in attach, "Attach 必须对同一实例幂等短路（防重复订阅）"
    assert "UiEvent -= OnUiEvent" in attach, \
        "Attach 必须支持换绑：Init 重跑换了 WsClient 实例要迁到新实例（否则 tee 整场失聪）"


def test_solo_frames_are_recorded_even_while_showing():
    """★ 单聊"始终记"的两条接线：tee 收帧无前置判据 + 玩家发言帧在**发送处**本地记一次。

    为什么单独钉：这是本次改造的**存在理由**。少了哪一条，实机表现都是"重开窗缺东西"
    且**零日志**：少了 tee 那条 ⇒ 关窗前的工具/思考行补不回来；少了 user_say ⇒
    玩家的输入永远不在重放里（单聊没有回声帧，Python 侧不会把它送回来）。
    """
    pres = _read(PRESENTER)
    send = _method(pres, "private void OnSubmitRequested()")
    assert 'new ChatFrame { Event = "user_say"' in send, \
        "发送处没有记玩家发言帧（重开窗时'我说过什么'永远补不回来）"
    i_bubble = send.index("_window.AppendUserMessage(clean);")
    i_frame = send.index('new ChatFrame { Event = "user_say"')
    assert i_bubble < i_frame, \
        "发言帧要记在本地回显之后（记的是发出去/落账的那一串：含时间戳与图片占位；上屏才剥戳）"
    assert "ChatFrameQueue.OfferSolo(npc," in send, "发言帧没进单聊队列"
    # 消费侧必须认识它（不认识 = switch 静默落空，玩家那句照样不画）
    drain = _method(pres, "private void DrainSoloFrames(string npcId)")
    assert 'case "user_say":' in drain, "整段重演的 switch 不认识 user_say（静默落空）"
    i_case = drain.index('case "user_say":')
    seg = drain[i_case: i_case + 400]
    assert "_window.AppendUserMessage(f.Text" in seg, "user_say 分支没有把人话说回屏幕上"


def test_replay_gate_is_armed_before_request_and_cleared_on_every_exit():
    """★ 单聊渲染闸：置位在请求**之前**，且**每个出口**都要落位。

    为什么是硬要求：单聊帧现在"始终记"，闸门期间不掐掉直播就会一帧两画（重演 + 直播）；
    反过来，闸门**落位漏一个出口**，这个窗口的直播就永久静默（帧只进队列不出画）——
    零日志、只能靠"发了消息没反应"发现，与忙标卡死同一形态。
    """
    src = _read(PRESENTER)
    body = _method(src, "private void SendOpenChatReplay(string npc)")
    assert "ArmReplayGate(npc);" in body, "渲染闸没有在请求前落位"
    assert body.index("ArmReplayGate(npc);") < body.index('_ws.SendRequest("open_chat"'), \
        "置位必须在请求**发出之前**（回执到达前那几十毫秒的帧也要归队列管）"
    # ★ 发不出去时不许置位：没有回执 ⇒ 闸门永远落不下来 ⇒ 该窗口永久静默
    assert body.index("!_ws.IsConnected") < body.index("ArmReplayGate(npc);"), \
        "连接判据必须排在置位之前（断线期开窗置位 = 窗口永久静默）"
    assert 'ClearReplayGate("未连接（请求未发）")' in body, "断线早退分支没有落位闸门"
    assert "ChatFrameQueue.SoloEpoch(npc)" in body, "没有记'请求前'的段闭合代数（快照过期兜底失效）"
    # 出口落位：成功路径 + 四个早退 + 直播优先分支
    for why in ("窗口已关", "回执失败", "回执无 data", "NPC 已切", "回执到达时已在直播", "重放已落地"):
        assert 'ClearReplayGate("' + why in src, f"出口没落位：{why}"
    i_drain = body.index("DrainSoloFrames(npc)")
    i_clear = body.index('ClearReplayGate("重放已落地")')
    i_sel = body.index("EnterSelecting")
    assert i_drain < i_clear < i_sel, \
        "成功路径的顺序必须是：消费队列 → 落位闸门 → （删除模式入口）"
    # 换窗口 / 换 NPC / 进群 / 离群 / 群解散 都要落位（跨场合残留 = 白吞帧）
    for why in ("关窗", "换 NPC", "进群", "离群回单聊", "群解散回单聊外观", "玩家发消息"):
        assert 'ClearReplayGate("' + why in src, f"缺少落位点：{why}"
    # 闸门判据必须真的在直播入口里短路，且排在 switch 之前
    live = _method(src, "private void OnUiEvent(JObject payload)")
    assert "_replayGate" in live, "OnUiEvent 没有闸门判据（回执到达前直播照画 ⇒ 一帧两画）"
    assert live.index("_replayGate") < live.index("switch (ev0)"), "闸门判据要排在分派之前"
    # 入口方法只负责守卫 + 转发（递归回入口会重复那套忙态判断）
    entry = _method(src, "private void OpenChatAndReplay()")
    assert "SendOpenChatReplay(npc);" in entry, "入口没有转发到发送方法"
    assert "_replayEpochRetries = 0;" in entry, "每次开窗都要把重试计数清零"


def test_gate_swallows_only_buffered_frames():
    """★ 渲染闸只许吞"队列真会补画"的那几类帧（判据与 tee 的收帧白名单**同源**）。

    两边各自只有一半信息，所以必须共用 `ChatFrameQueue.IsSoloBuffered`：
      · 闸门吞掉一个队列**没收**的帧（`stats_update` / `compact_result` / `step(sys|ctx)`）
        ⇒ 那一帧"两边都没有"：既不直播也不重演。`compact_result` 更狠 —— 它是
        "正在压缩中"那一行**唯一**的收尾信号，吞掉就停在那儿直到看门狗误报"长时间无响应"
        （而压缩其实成功了），全程零日志；
      · 闸门少吞一个已记帧 ⇒ 同一帧画两遍（重演一次 + 直播一次）。
    规则本身（哪些 kind 可重放）由 C# 单测跑，这里钉的是**接线同源**。
    """
    q = _read(QUEUE)
    helper = _method(q, "internal static bool IsSoloBuffered(string ev, string kind)")
    assert 'ev == "npc_reply" || ev == "text_delta"' in helper, \
        "单聊两条无 kind 的帧（段闭合信号 / 流式）必须算'会进段'"
    assert 'ev == "step"' in helper and "IsPlayableStepKind(kind)" in helper, \
        "step 必须按 kind 白名单判（sys/ctx 不进段）"
    # 两个消费方都必须走它：tee 的收帧 + presenter 的闸门
    tee_code = _strip_comments(_read(TEE))
    assert "ChatFrameQueue.IsSoloBuffered(" in tee_code, \
        "tee 的收帧白名单没走同源判据（两边会各自漂移）"
    pres = _read(PRESENTER)
    live = _method(pres, "private void OnUiEvent(JObject payload)")
    gate_line = live[live.index("_replayGate"): live.index("switch (ev0)")]
    assert "ChatFrameQueue.IsSoloBuffered(" in gate_line, \
        "闸门判据没走同源白名单（吞掉队列不收的帧 = 那一帧两边都没有）"
    assert 'IsSoloBuffered(ev0, payload["kind"]?.ToString() ?? "")' in gate_line, \
        "闸门必须把 event 与 step.kind 一起交给判据（只判 event 会把 sys/ctx 也吞掉）"
    # 闸门判据仍要排在分派之前（顺序本身也是硬要求）
    assert live.index("_replayGate") < live.index("switch (ev0)")


def test_drain_room_is_after_replace_history_and_after_busy_reset():
    """★ 群消费时序（两条都是硬要求，写反了都不报错）。"""
    src = _read(PRESENTER)
    body = _method(src, "public bool ApplyOpenRoom(JObject resp)")
    assert "DrainRoomFrames(" in body, "ApplyOpenRoom 没有消费队列"
    i_replay = body.index("ReplaceHistory")
    i_drain = body.index("DrainRoomFrames(")
    i_busy = body.index("SetBusy(false)")
    assert i_replay < i_drain, "消费必须排在 ReplaceHistory 之后（先消费会被重放擦掉）"
    assert i_busy < i_drain, "消费必须排在 SetBusy(false) 之后（未闭合轮的 busy 是真的）"
    # 消费只许有一处定义 + 一处调用（多了 = 有人加了第二条消费路径，时序无法保证）
    assert _strip_comments(_read(PRESENTER)).count("DrainRoomFrames(") == 2, \
        "DrainRoomFrames 只许一次定义 + 一次调用（多了说明有别的消费路径）"
    # 早退分支（窗口已关 / 失败）不许消费 —— 那条路径上重放没发生，消费会把帧白丢
    head = body[:i_replay]
    assert "DrainRoomFrames(" not in head


def test_drain_room_uses_baseline_and_forgets_last():
    """群消费实现：baseline 取重放 items 的最大 seq；`ForgetRoom` 放在最后。"""
    src = _read(PRESENTER)
    body = _method(src, "private void DrainRoomFrames(string roomId, JArray items)")
    assert 'it?["seq"]' in body, "baseline 必须从重放 items 的 seq 算"
    assert "ChatFrameQueue.TakeRoom(roomId, baseline)" in body, "取帧必须带 baseline（去重的唯一防线）"
    assert "ChatFrameQueue.ForgetRoom(roomId)" in body, "消费完必须清桶"
    # ★ 用 rindex（最后一次）：早退分支（空桶）也有一处 ForgetRoom，用 index 会拿它比，
    #   再撞上算 baseline 的那个前置 `foreach` 就变成恒真断言（这里踩过一次）。
    assert body.rindex("ChatFrameQueue.ForgetRoom(roomId)") > body.index("foreach (var f in frames)"), \
        "ForgetRoom 必须在重放循环之后（中途抛异常时桶还在，下次开窗还能重放）"
    # 四条帧都要有对应的应用路径（字段版方法）
    for needle in ("ApplyRoomSay(", "ApplyRoomState(", "ApplyStep(", "ApplyDelta("):
        assert needle in body, f"消费分派少了：{needle}"


def test_drain_solo_is_after_replace_history_and_before_selecting():
    """★ 单聊消费时序与"直播优先"欠账分支的配套动作（都在 `SendOpenChatReplay` 回调里）。

    时序：`ReplaceHistory` → `DrainSoloFrames` → `ClearReplayGate` → `EnterSelecting`
    （重演出来的行不带选择遮罩；闸门落位不能晚于删除模式入口 —— 那之后就不该再有"不画帧"的状态）；
    欠账分支（`_wantReplay = true`）里必须先 `ForgetSolo` 丢段 —— 那段要么已被直播续写
    （收口全文覆盖）、要么已收口（重放必含），留着 = 过期段，下次"安静重放"会把幽灵泡演出来。
    """
    src = _read(PRESENTER)
    body = _method(src, "private void SendOpenChatReplay(string npc)")
    assert "DrainSoloFrames(npc)" in body, "回执里没有消费单聊帧"
    i_replay = body.index("ReplaceHistory")
    i_drain = body.index("DrainSoloFrames(npc)")
    i_clear = body.index('ClearReplayGate("重放已落地")')
    i_sel = body.index("EnterSelecting")
    # ★ `ForgetSolo(npc)` / `_wantReplay = true` 各只出现一处（回调里的"响应到达时已在直播"分支）
    i_forget = body.rindex("ForgetSolo(npc)")
    i_want = body.rindex("_wantReplay = true")
    assert i_replay < i_drain, "消费必须排在 ReplaceHistory 之后（先消费会被重放擦掉）"
    assert i_drain < i_clear < i_sel, \
        "顺序必须是：消费 → 落位闸门 → 删除模式入口"
    assert i_forget < i_want < i_drain, \
        "欠账分支要在记欠账前 ForgetSolo 丢段，且整体排在消费点之前"
    # 欠账分支不许消费 —— 那条路径上重放没发生，段已被直播续写/收口
    head = body[:i_replay]
    assert "DrainSoloFrames(" not in head
    # 消费只许有一处定义 + 一处调用
    assert _strip_comments(_read(PRESENTER)).count("DrainSoloFrames(") == 2, \
        "DrainSoloFrames 只许一次定义 + 一次调用（多了说明有别的消费路径）"


def test_bulk_rebuild_holds_the_scroll_at_bottom():
    """★ 批量重建（历史重放 / 整段重演）之后必须"钉底保持"几帧。

    只有"内容变高就钉底"是不够的：一次画几十行时布局要好几帧才结算，钉底那一刻量到的高度
    可能还是中间态；随后布局定稿会让位置**不再在底部**，而下一帧是"内容没长高"⇒
    走 `else if (!AtBottom()) _followingBottom = false;` ⇒ 跟随被**永久关掉**，视图停在顶部
    （实机：重开窗后要自己往下翻）。保持期内无条件钉底 + 不吃那条判决。
    """
    src = _read(WINDOW)
    hold = _method(src, "public void HoldScrollAtBottom(int frames = 6)")
    assert "_pinHoldFrames" in hold and "_followingBottom = true" in hold, \
        "保持期没把跟随打开（保持结束后第一帧仍要能接上）"
    assert "_lastChildCount = -1" in hold and "_lastContentH = -1f" in hold, \
        "保持期要把两个跟随判据归零，结束后的第一帧才会必判'长了'再钉一次"
    late = _method(src, "private void LateUpdate()")
    i_hold = late.index("_pinHoldFrames > 0")
    i_follow = late.index("if (!_followingBottom)")
    assert i_hold < i_follow, "保持期必须排在'未跟随则只做自愈'之前（否则保持期内根本不钉）"
    assert late.index("verticalNormalizedPosition = 0f", i_hold) < i_follow, \
        "保持期里要真的钉底，不是只记账"
    # 两条批量重建路径都要进保持期
    reset = _method(src, "private void ResetContent()")
    assert "_pinHoldFrames = 6;" in reset, "整块重建（Open / ReplaceHistory）没进保持期"
    pres = _read(PRESENTER)
    drain = _method(pres, "private void DrainSoloFrames(string npcId)")
    assert drain.count("HoldScrollAtBottom()") == 2, \
        "两条路都要钉底：空段早退（ReplaceHistory 刚重建完）+ 整段重演之后"


def test_drain_solo_replays_segment_and_marks_busy():
    """单聊消费实现：取段 → 逐帧字段版（含 `user_say`）→ `ForgetSolo` 放最后 → 段非空 ⇒ **置忙**。

    ★ 10-17：置忙的方式改了（用户拍板"忙标改成单一事实来源"）★
    原先是一句 `SetBusy(true)` 直接写忙标；现在改成
    `TurnRegistry.Begin(...)` + `RefreshSoloBusy(...)` —— 状态进登记表、UI 从登记表读。
    为什么段非空还要**补一次 Begin**：段非空 = "有帧但还没等到 npc_reply" = 这一轮确实没闭合
    （证据）。而登记表才是忙标的唯一判据，所以这份证据要登记进去。
    ⚠ 反过来不成立：段是被第一条 `step`/`text_delta` **懒开**的，答不了首 token 之前的空窗
      （4~15s），所以 `Begin` 的主路径在**回合起点**，不能只靠这里补。
    """
    src = _read(PRESENTER)
    body = _method(src, "private void DrainSoloFrames(string npcId)")
    assert "ChatFrameQueue.TakeSolo(npcId)" in body, "取帧必须走 TakeSolo"
    assert "ChatFrameQueue.ForgetSolo(npcId)" in body, "消费完必须清桶"
    # ★ rindex：早退分支（空段）也有一处 ForgetSolo，用 index 会拿它当"消费完的清桶"
    assert body.rindex("ChatFrameQueue.ForgetSolo(npcId)") > body.index("foreach (var f in frames)"), \
        "ForgetSolo 必须在重放循环之后（中途抛异常时段还在，下次开窗还能重放）"
    # 置忙：状态登记 + 按登记表刷新（不再是直接写忙标 —— 见本测试 docstring）
    assert "TurnRegistry.Begin(" in body, \
        "未闭合段重演后没有登记回合 —— 段非空就是'还在说'的证据，不登记则忙标不亮"
    assert "RefreshSoloBusy(" in body, \
        "登记了却没按登记表刷新忙标 —— UI 不会亮（登记表只负责状态，刷新才是渲染）"
    for needle in ("ApplyStep(", "ApplyDelta(", 'case "user_say":'):
        assert needle in body, f"单聊消费分派少了：{needle}"
    # 空段也要钉底：`ReplaceHistory` 刚整块重建过，不钉就停在顶部
    i_empty = body.index("if (frames.Count == 0)")
    assert "HoldScrollAtBottom()" in body[i_empty: i_empty + 400], \
        "空段早退分支没有钉底（开窗后视图停在顶部的原因之一）"


def test_no_second_consumption_path_in_ui_event():
    """`OnUiEvent` 里不许消费 —— 那是直播入口，消费只许发生在重放落地之后。"""
    src = _strip_comments(_read(PRESENTER))
    i = src.index("private void OnUiEvent(JObject payload)")
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                body = src[j:k + 1]
                break
    assert "DrainRoomFrames" not in body, "OnUiEvent 里出现了群消费调用"
    assert "DrainSoloFrames" not in body, "OnUiEvent 里出现了单聊消费调用"


def test_step_has_a_shared_field_version():
    """`step` 必须走字段版共享路径：直播（OnStepEvent）与两条重放（Drain*）不许各写各的。

    群聊的 `ApplyRoomSay` / `ApplyRoomState` 立下的先例：两种来路（直播 / 关窗重放）共用
    同一个字段版方法 —— 只钉直播入口的话，重放路径少传一个字段照样会被静默放过。
    """
    src = _strip_comments(_read(PRESENTER))
    assert src.count("private void ApplyStep(") == 1, "ApplyStep 只许一处定义"
    live = _method(_read(PRESENTER), "private void OnStepEvent(JObject payload)")
    assert "ApplyStep(" in live, "直播入口必须转调字段版（sys/ctx 除外，那条留在直播专用路径）"
    assert '"sys"' in live and '"ctx"' in live, "sys/ctx 的直播专用分支不见了（它们是只实时不重放的）"
    solo = _method(_read(PRESENTER), "private void DrainSoloFrames(string npcId)")
    assert "ApplyStep(" in solo
    room = _method(_read(PRESENTER), "private void DrainRoomFrames(string roomId, JArray items)")
    assert "ApplyStep(" in room


def test_lifecycle_cleanups_are_wired():
    """生命周期：群解散清该桶；换存档清全部；Attach 挂在 ModMain 建连处。"""
    pres = _read(PRESENTER)
    dis = _method(pres, "public void OnRoomDissolved(string roomId)")
    assert "ChatFrameQueue.ForgetRoom(roomId)" in dis, "群解散没清桶（room_id 复用会灌进新群）"
    assert dis.index("ChatFrameQueue.ForgetRoom(roomId)") < dis.index("AcceptsRoomFrame"), \
        "清桶要放在'不是当前群就早退'之前 —— 解散的往往不是当前显示的群"

    mm = _read(MODMAIN)
    assert "ChatFrameTee.Attach(_wsClient)" in mm, "tee 没在 ModMain 附着"
    body = _method(mm, "private void OnIntoWorldEvent(ETypeData e)")
    assert "ChatFrameQueue.ClearAll()" in body, "换存档没清队列（旧世界的帧会灌进新档）"


# ----------------------------------------------------------------------
# B2. 09-17 复核修的三处群窗毛病（Python 侧读源码钉住接线）
# ----------------------------------------------------------------------

def test_room_member_error_speaks_up_and_gate_is_quiet():
    """★ 09-17 用户拍板的四件事（群聊）：异常不静默 / 点名不预开泡 / 输入侧折叠不显示 / 折叠带说话人。

    事故背景（实机截图 + 日志）：姜萌的群聊回合被 45s 挂钟硬顶砍断（思考流已推 382 token），
    Python 异常分支**一个帧都不发** ⇒ C# 那条空气泡挂了 45 秒、忙碌态不复位，
    直到下一个成员的 `speak` 顺手撤泡；玩家只看到"气泡自己消失了、另一个人开始说话"，
    零解释。用户同时拍板：群聊**不要**「系统组装 / 运行时上下文」（多人各一套太乱），
    只留「内心思量」+「行动记录」，且这两块标题要带说话人（气泡要等正文才出现，
    思考期间它是唯一的归属线索）。
    """
    # ---- Python：异常必须发帧，且给**人话**原因 ----
    flow = _read(ROOM_FLOW)
    assert 'phase="error", speaking=[npc_id]' in flow, "成员异常没有发 error 帧（玩家将零解释）"
    i_err = flow.index('phase="error", speaking=[npc_id]')
    seg = flow[i_err: i_err + 400]
    assert "note=errs[npc_id]" in seg, "error 帧必须带上人话原因"
    assert "errs[npc_id] = _member_error_note(exc)" in flow, "异常原因没经过人话翻译"
    # 注意：`_method` 是**按 C# 大括号配对**写的，不能拿来切 Python 源码
    _i_fn = flow.index("def _member_error_note(")
    note_fn = flow[_i_fn: flow.index("\ndef ", _i_fn + 1)]
    assert "TimeoutError" in note_fn and "超时" in note_fn, "超时要翻成'模型超时'"
    # 09-18：超时文案要**分型**（首字"一直没开口" / 停顿"说到一半卡住"），
    # 这两种体感完全不同，一句笼统的"模型超时"会让玩家不知道该等还是该重说。
    assert '"首字" in msg' in note_fn and '"停顿" in msg' in note_fn, \
        "超时没按 首字/停顿 分型（看门狗消息里的前缀就是判据）"
    assert "Traceback" not in note_fn and "exc_info" not in note_fn, "上屏文案不许带堆栈"
    # 终帧 note：**只有**全员都没开口且有异常时才写（单成员的解释已在 error 帧上）
    assert "if not spoken and errs:" in flow, "全员出错时终帧少了汇总（屏幕上一个字都没有）"
    # note 要真的进帧
    _i_emit = flow.index("async def _emit_state(")
    emit = flow[_i_emit: flow.index("\n    # ══", _i_emit)]
    assert "note: str = \"\"" in emit, "_emit_state 没有 note 形参"
    assert "text=note or \"\"" in emit, "note 没进 room_state 载荷（C# 拿不到文案）"

    # ---- C#：error 分支（撤泡/定稿 + 系统提示 + **不复位**忙碌态）----
    pres = _read(PRESENTER)
    st = _method(pres, "private void ApplyRoomState(string phase, string who, string note, string whoId = null)")
    assert 'phase == "error"' in st, "C# 不认识 error 帧（异常仍然是静默的）"
    i_err_cs = st.index('phase == "error"')
    err_seg = st[i_err_cs: st.index('phase == "end"', i_err_cs)]
    assert "AppendSystemNotice(" in err_seg, "error 分支没给玩家任何解释"
    assert "SetBusy(false)" not in err_seg, "error 不是终帧，不许复位忙碌态（轮还在跑）"
    assert "CancelActiveBubble()" in err_seg and "EndTurnStreaming()" in err_seg, \
        "error 分支要撤空气泡 / 定稿半截正文"
    assert "这次没能开口" in err_seg, "error 提示必须点名到人"

    # ---- C#：点名**不再预开气泡**（C2）----
    speak_seg = st[st.index('phase == "speak"'): i_err_cs]
    assert "BeginNpcBubble" not in speak_seg, \
        "speak 又预开气泡了：弃权/出错时会留下一条要等别人说话才消失的幽灵泡"
    assert "SetRoomSpeaker(" in speak_seg, "speak 仍要记说话人（step(text) 的归属与取图靠它）"
    assert 'SetBusy(true, "群点名 ' in speak_seg, "群点名要带理由进忙标日志（排障唯一线索）"
    # 正文那条路要自己带名字前缀（气泡是现建的，没人替它写「甲：」）
    step_fn = _method(pres, "private void ApplyStep(int turn, string kind, string name, string args, string text, bool isError)")
    assert '_roomSpeaking + "：" + text' in step_fn, "step(text) 在群聊里没补说话人前缀"
    # 每条 room_state 都留痕（这一族帧此前零日志）
    assert '"[ChatPresenter] 群状态 "' in st, "群状态帧没日志（这次排查就卡在这里）"

    # ---- C#：群聊不显示输入侧折叠（E），但 NoteTurn 必须在判据之前 ----
    live = _method(pres, "private void OnStepEvent(JObject payload)")
    i_kind = live.index('if (kind == "sys" || kind == "ctx")')
    # 锚点必须是**代码**：`_method` 先剥注释，锚注释会找不到
    branch = live[i_kind: live.index('ApplyStep(payload["turn"]', i_kind)]
    assert "NoteTurn(" in branch, "NoteTurn 丢了：回合号是气泡去重的判据"
    assert branch.index("NoteTurn(") < branch.index("IsRoomMode"), \
        "NoteTurn 必须排在群聊早退**之前**（跳掉它会让同段正文成两次泡）"
    assert "if (_window.IsRoomMode) return;" in branch, "群聊仍在渲染系统组装/运行时上下文（用户要求隐藏）"
    assert "AppendInputParts(" in branch, "单聊那条路不许被一起删掉"

    # ---- C#：每个成员边界都要关掉「内心思量」折叠（09-17 复核补）----
    # 漏掉它的形态：群聊里思考折叠**从来不关**（`FinishThink` 全工程只有单聊 npc_reply 调用过），
    # 下一个成员的思考增量并进上一位那块 —— 实机一块折叠显示 850 字，而 Python 日志显示
    # 那位只推了 382 字，标题还只写着第一个人的名字。
    assert st.count("_window.FinishThink();") == 4, \
        "speak / silent / error / end 四个成员边界都要关思考折叠（实得 %d 处）" % st.count("_window.FinishThink();")
    _branches = ('phase == "speak"', 'phase == "silent"', 'phase == "error"', 'phase == "end"')
    _pos = [st.index(b) for b in _branches]                     # 源码顺序即 if/else-if 链顺序
    assert _pos == sorted(_pos), "四个 phase 分支的顺序变了（下面的切片假设按源码顺序）"
    for _i, _b in enumerate(_branches):
        _seg = st[_pos[_i]: (_pos[_i + 1] if _i + 1 < len(_pos) else len(st))]
        assert "_window.FinishThink();" in _seg, f"{_b} 分支没关思考折叠"

    # ---- C#：折叠标题带说话人（B）----
    win = _read(WINDOW)
    lab = _method(win, "private string SpeakerLabel(string baseLabel)")
    assert "IsRoomMode" in lab and "_roomSpeaker" in lab, "归属前缀判据不对（单聊必须逐字不变）"
    assert 'NewStepGroup(SpeakerLabel("行动记录"))' in win, "行动记录没带说话人"
    assert 'new ThinkGroup(_r.Content, SpeakerLabel("内心思量"))' in win, "内心思量没带说话人"
    views = _read(VIEWS)
    assert 'public ThinkGroup(Transform contentParent, string label = "内心思量")' in views, \
        "ThinkGroup 没有 label 形参（标题无法带说话人）"
    assert '_label + " · " + _chars' in views, "ThinkGroup 标题没真的用上 label"
    print("✓ 群聊：异常发帧 + 点名不预开泡 + 不显示输入侧折叠 + 折叠带说话人")


def test_unclosed_round_is_filtered_by_logged_say():
    """未闭合段里**已经落账**的 `room_say` ⇒ 整段丢（否则重开窗同一句画两遍）。

    未闭合段不受基线约束是**有意为之**（关窗时正在流的那半截要补上），但同一段里可能
    已经有一条 `room_say` 落了账（说话人说完了、这一轮还在往下跑），而回放读的正是
    `Room.log` ⇒ 那条已经在屏幕上。判据必须是**整段**丢：只丢 say 会留下一条永远等不到
    定稿的半截泡（speak/delta 还在）。
    """
    q = _read(QUEUE)
    body = _method(q, "internal static List<ChatFrame> TakeRoom(string roomId, int baselineSeq)")
    assert "loggedRounds" in body, "TakeRoom 少了'已落账的段'判决"
    assert 'f.Event == "room_say" && f.Seq > 0 && f.Seq <= baselineSeq' in body, \
        "判决必须按 room_say 自己的 seq 与基线比较"
    assert body.count("if (loggedRounds.Contains(f.Round)) continue;") == 1, \
        "判决必须作用于**整段**（含它的 speak/delta）"


def test_silent_speaker_drops_the_empty_bubble():
    """弃权的成员必须**撤掉** speak 给它开的那条空气泡（否则永远留在群窗上）。

    链路：Python `_run_round` 判 `is_silent` → 发 `room_state(phase="silent")`
    （`room_flow.py`）→ C# `ApplyRoomState` 撤泡且**不复位忙碌态**（轮还在跑）。
    另有两道兜底，防"silent 帧丢了/旧 Python 不发"：`end` 与下一个 `speak` 都会撤空气泡。
    """
    flow = _read(ROOM_FLOW)
    i = flow.index("if is_silent(body):")
    seg = flow[i: i + 1200]
    assert 'phase="silent"' in seg, "Python 弃权分支没发 silent 帧（C# 的泡没人撤）"

    pres = _read(PRESENTER)
    body = _method(pres, "private void ApplyRoomState(string phase, string who, string note, string whoId = null)")
    assert 'phase == "silent"' in body, "C# 不认识 silent 帧（会落空到什么都不做）"
    i = body.index('phase == "silent"')
    seg = body[i: body.index('phase == "end"', i)]      # 只取 silent 这一支
    assert "CancelActiveBubble()" in seg, "silent 帧没撤泡"
    assert "SetBusy(false)" not in seg, "silent 不是终帧，不许复位忙碌态"

    win = _read(WINDOW)
    # 判据的**正文**也要钉住：只查"有没有这个成员"的话，把它的表达式改成 false 照样绿
    # （变异验证实测：`=> false` 那条第一次跑**没被抓到**）。
    assert "public bool ActiveBubbleHasNoBody => _deltaBubble != null && _delta.Length <= _deltaPrefixLen;" in win, \
        "ChatWindow 少了'只有开头没正文'的判据（或判据被掏空）"
    assert "_deltaPrefixLen = (text ?? \"\").Length;" in win, \
        "BeginNpcBubble 没记开头长度 ⇒ 判据恒假（空气泡撤不掉）"
    assert body.count("_window.ActiveBubbleHasNoBody") == 3, \
        "speak / error / end 三道兜底都要接上（silent 或 error 帧丢了就留下空气泡）"


def test_player_echo_is_not_drawn_twice():
    """玩家群发言的回声**只销账、不再画**（本地那条已经画过了）。

    为什么不能只靠 `_roomEchoSeen`：本地泡是同步画的，那时还没有 uid ——
    `SendRoomMessage` 是 fire-and-forget 的 event，回执里拿不到 uid，所以那个集合里
    永远不会有本地这条 ⇒ 每条群消息都会画两遍。待销账表按 FIFO 与回声文本对号。
    """
    pres = _read(PRESENTER)
    assert "private readonly Queue<string> _roomEchoPending" in pres, "少了回声待销账表"
    send = _method(pres, "private void OnSubmitRequested()")
    assert "_roomEchoPending.Enqueue(msg);" in send, "发送时没记待销账"
    say = _method(pres, "private void ApplyRoomSay(string uid, string speaker, string text, string speakerId = null,")
    i = say.index('if (speaker == "player")')
    seg = say[i: i + 1800]
    assert "_roomEchoPending.Peek() == key" in seg and "_roomEchoPending.Dequeue();" in seg, \
        "回声没有与待销账对号（会画第二遍）"
    assert seg.index("Dequeue();") < seg.index("AppendUserMessage(text);"), \
        "销账必须发生在画之前"

    # ★ 对号键必须是 `echo_text`（玩家原文），不能是剥过的 `text` ★（11-21 截图事故）
    #   Python 把 `@姜萌 你在干什么` 剥成 `你在干什么` 再发回来，而本地气泡存的是原文
    #   ⇒ 拿 `text` 比**永远不等**，同一条消息画两遍。实证截图：两条内容相同、
    #   第一条带 `@姜萌`、第二条没有。
    assert "echoText" in seg, "对号没用 echoText（玩家原文）⇒ @ 过的消息会画两遍"
    assert "string.IsNullOrEmpty(echoText) ? text : echoText" in seg, \
        "echoText 为空时没有回落比 text（老服务端/无 @ 的发言会失去销账）"
    # 换窗/重放要一并作废（否则旧账会吞掉之后一条同文本的回声）
    assert pres.count("_roomEchoPending.Clear();") >= 4


# ----------------------------------------------------------------------
# C. 跨语言：帧字段名必须与 Python 侧 emit 的键一一对上
# ----------------------------------------------------------------------

def test_frame_field_names_match_python_emit():
    """`ChatFrameTee.Decode` 解构的键 == Python 侧实际发的键。

    漂移是**完全静默**的：解构出空串 / -1，帧变成"空发言"，界面上什么都不发生。
    所以这里对着 `room_flow.py` / `dialogue_agent.py` / `ws_channel.py` 的实际源码钉。
    """
    flow = _read(ROOM_FLOW)

    def _next_member(start: int) -> int:
        """下一个成员签名的起点（类内方法：缩进 4 空格 + def，async 与否都算）。"""
        m = re.search(r"\n    (?:async )?def ", flow[start + 1:])
        return start + 1 + (m.start() if m else len(flow) - start - 1)

    i = flow.index("async def _emit_say")
    say = flow[i:_next_member(i)]
    for k in ("room_id", "seq", "uid", "speaker", "text"):
        assert f"{k}=" in say, f"_emit_say 不再发 {k}（C# 解构会静默变空）"
    i = flow.index("async def _emit_state")
    state = flow[i:_next_member(i)]
    for k in ("room_id", "phase", "speaking", "last_seq"):
        assert f"{k}=" in state, f"_emit_state 不再发 {k}"

    tee = _read(TEE)
    for k in ('["seq"]', '["uid"]', '["speaker"]', '["text"]',
              '["last_seq"]', '["phase"]', '["speaking"]', '["room_id"]', '["npc_id"]'):
        assert k in tee, f"Tee 没解构 {k}"
    # step 帧的解构键（单聊段的回合号 / 折叠重建都靠它们）
    for k in ('["turn"]', '["kind"]', '["name"]', '["args"]', '["isError"]'):
        assert k in tee, f"Tee 没解构 step 的 {k}"

    da = _read(DIALOGUE)
    for kind in ("think", "text", "tool_call", "tool_result"):
        m = re.search(r'_emit_step\(\{"kind": "%s"[^}]*\}\)' % kind, da)
        assert m, f"dialogue_agent 里找不到 {kind} 的 _emit_step 调用（帧契约漂移）"
        line = m.group(0)
        assert '"turn": turn' in line and '"step": step' in line, (
            f"{kind} 的 step 帧不再带 turn/step（C# 侧段回合号 / 去重会静默失效）：{line}")
    m = re.search(r'_emit_step\(\{"kind": "tool_call"[^}]*\}\)', da)
    assert '"name": name' in m.group(0) and '"args": args' in m.group(0), \
        "tool_call 帧不再带 name/args（C# 的工具折叠会静默变空）"
    m = re.search(r'_emit_step\(\{"kind": "tool_result"[^}]*\}\)', da)
    assert '"tool": name' in m.group(0) and '"text": text' in m.group(0), \
        "tool_result 帧不再带 tool/text"

    # 群 / 单聊共用同一个 _run_turn：step/delta 的 room_id 随 `_extra` 空/非空分流
    # （单聊帧不带 room_id、群聊帧带 —— Tee 的两路分流就建立在这上面）
    ws = _read(WS)
    assert '_extra = {"room_id": room_id} if room_id else {}' in ws, \
        "_extra 的 room_id 分流判据不见了（单聊帧会混进群桶，或反之）"
    assert 'send_event("step", npc_id=npc_id, **_extra, **info)' in ws, \
        "step 帧不再随 _extra 带 room_id（Tee 分流会静默错桶）"


# ----------------------------------------------------------------------
# D. 单测工程：链接同一份源码（不是副本）
# ----------------------------------------------------------------------

def test_unit_test_project_links_the_same_source():
    csproj = _read(TESTPROJ / "ChatFrameQueueTests.csproj")
    assert r"..\..\UI\ChatFrameQueue.cs" in csproj, "单测工程必须链接同一份队列源码"
    assert not (TESTPROJ / "ChatFrameQueue.cs").exists(), (
        "不许把队列复制成副本 —— 副本会与真源码漂移，单测就失去意义"
    )
    assert RUNNER.exists(), "缺 scripts/dev/run_csharp_tests.py（单测的跑法入口）"
    assert (TESTPROJ / "Program.cs").exists()
    assert not (CS / "tests" / "RoomFrameQueueTests" / "RoomFrameQueueTests.csproj").exists(), (
        "旧工程 RoomFrameQueueTests 的 csproj 还活着 —— 改名后必须删干净（否则同名队列会被跑两套）"
    )
