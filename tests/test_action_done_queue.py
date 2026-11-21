"""「动作办妥」回执（intent=action_done）+ 动作排队器：Python 侧包装与投递 + 跨语言接线契约。

背景（09-24 用户拍板 B 方案）：玩家在战斗 / 剧情链收尾期间请求的动作，游戏此刻**发不出去**
—— 原生窗沉进 `WorldSystemMgr` 系统队列不出队，硬发就是 90 秒后的静默超时
（"RPC call_tool 返回失败（90031ms）：玩家长时间未响应"）。C# 侧改成
**立即受理（回 queued）→ 此间事了自动执行 → 办完发 action_done 告知**。

本文件验 Python 侧三件事：

  1. **包装**：`format_action_done_message` 把 C# 给的结果摘要包成舞台指令 ——
     必须点明"这是已经发生的事实"与"不要再重复发起同一个动作"（缺前者模型会当成玩家的新请求，
     缺后者它会再调一次工具，而那正是排队要避免的）。
  2. **投递**（用户 09-24 指定）：该 NPC 正忙 → 投成 next-step 增量（当前回合下一步领走，
     不另起回合）；空闲 → 走正常主动开口回合；**两者都豁免全局节流**（模型必须知道结局）。
  3. **渲染 + 接线**：排队受理结果 `{queued:true,summary,note}` 的模型可读文案，
     以及**跨语言键成对**（C# 写的键 = Python 读的键；错一个字母完全静默 ——
     帧照到、功能失效，与 `test_drama_segment_contract.py` 同一类护栏）。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from time import monotonic

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.agent.initiative import (
    INITIATIVE_LABELS, format_action_done_message, intent_label,
)
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.transport.ws_channel import WsServer, ChatHub
from agent_loop.tools import text_render as tr
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
ROOT_TMP = os.path.join(tempfile.gettempdir(), "agent_loop_action_done")
NPC = "姜萌_回执测试"


def _reset(bridge=None, llm=None):
    AgentLoop.reset_for_test(storage_root=ROOT_TMP, bridge=bridge, llm=llm)
    if os.path.isdir(ROOT_TMP):
        shutil.rmtree(ROOT_TMP, ignore_errors=True)


def _seed(bridge: StubGameBridge):
    bridge.seed_unit(
        NPC,
        {
            "realm": "元婴",
            "sect": "七星阁",
            "pos": "永宁州",
            "mood": 80,
            "power": 22000,
            "player": {"name": "韩立", "realm": "筑基", "relation": "道侣", "intim": 180, "same_grid": True},
            "recent": "上月收下了一株青须藤",
        },
    )


def _texts_of(msg) -> str:
    return "".join(b.get("text", "") for b in (msg.get("content") or [])
                   if isinstance(b, dict) and b.get("type") == "text")


# ---------- 1. 包装文案 ----------


def test_action_done_message_wording():
    t = format_action_done_message("向姜萌赠送物品：已完成")
    assert t.startswith("（系统："), t
    assert "向姜萌赠送物品：已完成" in t, "结果摘要没进文案（模型不知道办成了什么）"
    assert "已经发生的事实" in t, "没点明'这是已发生的事实' → 模型会当成玩家的新请求去回应"
    assert "不要再重复发起" in t, "没点明'别重复发起' → 模型会再调一次工具（正是排队要避免的）"
    # 空摘要兜底：不许出现空的"结果："（模型会以为漏了信息、转而追问玩家）
    t2 = format_action_done_message("")
    assert "没附上结果说明" in t2, t2

    # UI 短标签（对话窗分隔条 `—— 主动传音 · 动作回执 ——` 用）
    assert "action_done" in INITIATIVE_LABELS
    assert intent_label("action_done") == "动作回执"
    print("✓ action_done 包装文案（已发生/别重复发起）+ UI 短标签通过")


# ---------- 2a. 空闲：走正常回合 + 豁免节流 ----------


def test_action_done_idle_turn_and_throttle_exempt():
    bridge = StubGameBridge()
    _seed(bridge)
    seen = {}

    def _llm_fn(req):
        joined = "|".join(
            b.get("text", "")
            for m in req.get("messages", [])
            for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
        seen["joined"] = joined
        return {"text": "那株青须藤已经送到你手上了，收着便是。", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        # 制造"刚刚有过一次主动开口"的节流现场：action_done 必须豁免
        # （否则模型永远不知道结果 —— 而它很可能因此再发起一遍同一个动作）
        hub._last_initiative_ts = monotonic()
        await hub.handle_initiative(NPC, "action_done", "向姜萌赠送物品：已完成")

    asyncio.run(scenario())

    agent = loop.get(NPC)
    assert agent is not None
    assert "向姜萌赠送物品：已完成" in (seen.get("joined") or ""), "舞台指令没进模型上下文"
    ini = [ev for ev in agent.session.log
           if ev.get("type") == "user/message"
           and ev.get("data", {}).get("source", {}).get("kind") == "initiative"]
    assert ini and ini[-1]["data"]["source"]["intent"] == "action_done", "落账的 source.intent 不对"
    assert any(ev["type"] == "turn/end" for ev in agent.session.log), "回合没闭合"
    print("✓ 空闲：走正常主动回合 + 豁免节流通过")


# ---------- 2b. 忙：投成 next-step 增量（不另起回合） ----------


def test_action_done_busy_goes_next_step():
    bridge = StubGameBridge()
    _seed(bridge)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回复", "tool_calls": []}))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))
    agent = loop.create(NPC)

    async def scenario():
        agent.phase = {"kind": "running", "turn": 1, "step": 1}   # 该 NPC 正有回合在跑
        await hub.handle_initiative(NPC, "action_done", "邀约姜萌：已完成")
        assert agent.inbox.has_pending is True, "忙时没投进筐（回执会静默蒸发 —— 模型永远不知道结局）"
        # 投的是 next-step（当前回合的下一步领走），不是 next-turn（另起回合）
        assert agent.inbox.next_step, "没投到 next-step"
        assert not agent.inbox.next_turn, "不该进 next-turn（那会变成另起一个回合）"
        assert "邀约姜萌：已完成" in _texts_of(agent.inbox.next_step[-1]), "筐里的正文不对"
        # 没有新回合：账本里不该出现 turn/start
        assert not [ev for ev in agent.session.log if ev.get("type") == "turn/start"], "忙时不该开新回合"

    asyncio.run(scenario())
    print("✓ 忙：投成 next-step 增量、不另起回合通过")


# ---------- 3. 渲染层：排队受理结果 ----------


def test_render_queued_result():
    res = {"success": True, "data": {
        "queued": True,
        "summary": "邀约姜萌",
        "note": "此刻发不出去（战斗中/剧情收尾中）——这个动作已受理，此间事了会自动办理；"
                "不要重复调用它；办完后你会收到系统通知。",
    }}
    out = tr.render("world_ai_action", {"op": "yao_yue"}, res)
    assert out and out.startswith("（系统："), out
    assert "邀约姜萌" in out and "已受理" in out

    # 兜底：queued 但没有 summary/note 时也不能是空串（空 tool_result 比一句话更费解）
    out2 = tr.render("world_ai_action", {}, {"success": True, "data": {"queued": True}})
    assert out2 and "已受理" in out2, out2
    # 回归：普通结果不受影响（没有 queued 键时照旧走各工具渲染器/兜底）
    assert tr.render("unknown_tool", {}, {"success": True, "data": {"a": 1}}) is None
    print("✓ 渲染层：排队受理 → 一句系统说明（含兜底）通过")


# ---------- 4. 跨语言接线契约（C# 写的键 = Python 读的键） ----------


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_action_done_wiring_contract_across_languages():
    core = _read("csharp/drama/DeferredActionCore.cs")
    # 核的常数一旦被改小/改大，行为会静默变化
    # （上限 8 / 硬顶 600s / 静默两档 1.5s·0.5s / 告知 TTL 120s）
    assert "MaxWaiting = 8" in core, "多槽 FIFO 上限不见了"
    assert "HardCeilingSeconds = 600" in core, "硬顶不见了（会变成'排了队却没有下文'）"
    assert "StableSeconds = 1.5" in core, "轮询档静默不见了（会抢在处置页/尾页之前执行）"
    assert "StableEdgeSeconds = 0.5" in core, "边沿档静默不见了（有事件也要等 1.5s）"
    assert "NoticeTtlSeconds = 120" in core, "告知 TTL 不见了（会半分钟后凭空开口）"
    # 边沿证据把静默降到短档；竞争（blocked）把静默清零 —— 两条都不能被改掉
    assert "edgeSeen ? StableEdgeSeconds : StableSeconds" in core, "分档取值的写法被改了"
    assert "if (blocked) { _readySince = DateTime.MinValue; return Step.Wait; }" in core, \
        "被挡时不清零静默窗（用户拍板：有竞争就等待、续期时间置为零）"

    te = _cs_tools.tool_src()
    assert '["queued"] = true' in te, "C# 没回 queued 标记（Python 侧渲染与 WsClient 判据都靠它）"
    assert '["summary"]' in te and '["note"]' in te, "queued 结果缺 summary/note"
    assert "DeferredActionQueue.ShouldQueue()" in te, "工具层没有接排队闸"
    assert "DeferredActionQueue.BlockerNow().Length > 0) return null" in te, \
        "执行体没复查'又忙了 → 退回等待'（用户拍板：不判失败）"
    assert "IsActionTool" in te and "DescribeAction" in te, "动作白名单/一句话说明不在"

    ws = _read("csharp/bridge/WsClient.cs")
    assert "_tools.Execute(name, args, npcId)" in ws, "call_tool 没把 npc_id 传给工具层（排队没有收件人）"
    assert "queued" in ws and "ActionWatcher.OnActionDone" in ws, \
        "排队受理必须跳过'动作完成即开窗'（动作此刻还没执行）"

    mm = _read("csharp/app/ModMain.cs")
    assert "DeferredActionQueue.WaitBlocker" in mm, "主工程没注入'能不能执行'的判据"
    assert '"action_done"' in mm, "主工程没发 action_done 告知"
    assert "DeferredActionQueue.OnWorldChanged()" in mm, "世界重载没作废排队动作"

    py = _read("transport/ws_channel.py")
    assert 'intent == "action_done"' in py, "Python 没认 action_done"
    assert 'target="next-step", wake=False' in py, "忙时的投递没走 next-step 增量"
    assert "format_action_done_message" in py, "Python 没用包装函数"
    assert 'text = action_done_text' in py, "空闲分支没复用舞台指令"

    init = _read("agent/initiative.py")
    assert "def format_action_done_message" in init and '"action_done": "动作回执"' in init

    trs = _read("tools/text_render.py")
    assert 'if data.get("queued"):' in trs, "渲染层没认 queued（模型会看到一坨原始 JSON）"
    print("✓ 跨语言接线契约（核常数 + C#/Python 键成对）通过")


def test_chain_over_edge_and_yield_wiring():
    """边沿触发（BattleEnd / 收尾占位撤）与**让步集**（有竞争就等待）的接线必须都在。

    09-24 用户拍板的补充：① 有真事件就别靠"猜多久算结束"（静默降到 0.5s 短档）；
    ② 玩家正被游戏界面占着时**让路** —— 不抢屏，也别把窗又塞进系统队列（那正是要修的故障）。
    """
    gate = _read("csharp/drama/DramaGate.cs")
    assert "public static void NoteChainOver(string source)" in gate, "边沿入口不见了"
    assert "_chainOverAt != DateTime.MinValue" in gate, "Tick 没用边沿标记（分档失效）"
    assert 'NoteChainOver("收尾占位已撤：" + why)' in gate, "收尾占位撤除没记边沿"
    assert "_chainOverAt = DateTime.MinValue;" in gate, "世界重载没作废边沿标记"

    hook = _read("csharp/drama/BattleEndHook.cs")
    assert 'NoteChainOver("BattleEndMgr.BattleEnd")' in hook, \
        "BattleEnd 事件没有作为边沿（这是最硬的'整条流程走完了'）"

    # ★10-25 第二步（状态模块重构）★ 让步集与五条件**整体搬到** `state/`：
    #   · 让步判据（剧情窗/Mask/Window）→ `WorldStateCore.YieldReason`
    #   · 五条件（链收尾/战斗中/挂起/让路/不在主世界）→ `WorldStateCore.ExecuteBlocker`
    #   · 剧情标记由 `ModMain` **注入**（state/ 不依赖 drama/，否则成环）
    # 保证一字未变：成员、顺序、文案全部逐条钉在 `csharp/tests/WorldStateCoreTests`（53 项）。
    # 这里只保留"接线还在"这一层（判据本体由 C# 侧断言，比字符串匹配硬）。
    mon = _read("csharp/npc/NpcInitiativeMonitor.cs")
    assert "internal static string YieldReason()" in mon, "让步判据的转发不在"

    core = _read("csharp/state/WorldStateCore.cs")
    assert "Mask 层有模态遮罩" in core and "Window 层有窗口" in core, "让步判据少了 Mask/Window 层"

    mm = _read("csharp/app/ModMain.cs")
    assert "WorldStateGate.Attach(" in mm, "剧情标记（ChainHold/HasPending）没注入 → 那两条判据恒 false"
    assert "WorldStateGate.ExecuteBlocker()" in mm, "五条件没接上（WaitBlocker 应转发到状态模块）"
    # 五个成员逐个钉在核里（与 C# 断言同一批词，跨语言成对）
    for key, why in (("剧情链收尾中", "链收尾 → 该等待"),
                     ("另有一个动作在等玩家点选", "另有一个动作在等玩家点选 → 该排队等待"),
                     ("玩家正被界面占着（", "玩家被游戏界面占着 → 让路"),
                     ("不在主世界（世界界面未就绪）", "不在主世界 → 等回世界视图")):
        assert key in core, f"让步集缺项：{why}"
    print("✓ 边沿触发 + 让步集接线通过")
