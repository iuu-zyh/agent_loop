"""压缩纪要的 RPC 与推送事件（10-31）

三条链路：
  ① `edit_compact_summary` RPC：成功 / 四类拒绝（未激活、回合在跑、被新压缩取代、锁定）
  ② 手动 `/compact` 成功 → 先 `compact_summary`、后 `compact_result`（顺序是协议的一部分：
     C# 收到 result 时会把「正在压缩中」那一行收尾，折叠行必须**已经**在了）
  ③ 自动压缩 → 回合内 `on_step(kind=compact)` → 转成 `compact_summary` 事件（wire 上不出现
     新的 step kind），且 `editable=false`（自动压缩落在回合内部，永远不可改）

场景 ③ 走真实 WS（与 `tests/test_compact_command.py` 同款），因为它要验证"事件名/字段在
传输层没被筛掉"；①② 直接驱 ChatHub（不起服务器，快）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_PARENT = str(Path(__file__).resolve().parents[2])
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from agent_loop.agent_loop import AgentLoop
from agent_loop.compaction import Compressor, summary
from agent_loop.compaction.compress import SUMMARY_OPEN, SUMMARY_CLOSE
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.transport.ws_channel import ChatHub, WsServer

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_compact_view")
NPC = "林婉清_纪要"


class _StubSummaryLlm:
    async def generate(self, system, messages, tools, **kw):
        return SimpleNamespace(text=f"{SUMMARY_OPEN}\n## 自身\n- 你外冷内热。\n{SUMMARY_CLOSE}",
                               tool_calls=[], usage={})


class _FakeWs:
    """替身 WsServer：只收 send_event，供直驱 ChatHub 的用例断言回推帧。"""

    def __init__(self):
        self.events = []

    async def send_event(self, event, npc_id="", **fields):
        self.events.append({"event": event, "npc_id": npc_id, **fields})

    def of(self, name):
        return [e for e in self.events if e["event"] == name]


def _reset(compactor=None):
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=StubGameBridge(),
                             llm=EchoLlmClient(), compactor=compactor)
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)


def _compactor():
    return Compressor(llm=_StubSummaryLlm(), ctx_window=1200, retain_ratio=0.16, cool_down=0.0)


def _seed(agent, turns: int = 40):
    for i in range(turns):
        agent.session.append("turn/start", {"turn": i + 1, "scene": ""})
        agent.session.append("user/message", {
            "role": "user", "id": f"u{i}",
            "content": [{"type": "text", "text": f"玩家第{i}句话，问你最近有没有去过白帝城，路上可还太平"}],
        }, {"surfaceOp": "append"})
        agent.session.append("assistant/message", {
            "message": {"role": "assistant", "id": f"a{i}",
                        "content": [{"type": "text", "text": f"你第{i}次回答：去过，边关风沙大，小心为上。"}]},
        }, {"surfaceOp": "append"})
        agent.session.append("turn/end", {"turn": i + 1, "reason": {"kind": "completed"}})


# ---------------------------------------------------------------- ① RPC

def test_edit_rpc_roundtrip():
    """成功：回包带规范化正文/字数/编辑次数；账本里立刻能读到新正文。"""
    _reset(compactor=_compactor())
    loop = AgentLoop()
    agent = loop.create(NPC)
    _seed(agent)
    asyncio.run(agent.compact_now())
    info = summary.latest(agent.session)
    assert info and info["editable"], "前提：手动压缩后应当可编辑"

    hub = ChatHub(loop, _FakeWs())
    new_text = "## 自身\n- 你改过的纪要：少言，先听后说。"
    out = asyncio.run(hub.handle_request("edit_compact_summary", {
        "npc_id": NPC, "compaction_id": info["compaction_id"], "text": new_text}))
    assert out["ok"] is True, out
    assert out["text"] == new_text and out["chars"] == len(new_text)
    assert out["compaction_id"] == info["compaction_id"] and out["edits"] == 1
    assert new_text in summary.latest(agent.session)["text"]
    assert new_text in agent.session.derive_messages()[0]["content"][0]["text"]
    print(f"✓ edit_compact_summary 成功：{out['chars']} 字（第 {out['edits']} 次）")


def test_edit_rpc_rejections():
    """四类预期内拒绝都走 ok:false + 人话 reason（不抛异常、账本不动）。"""
    _reset(compactor=_compactor())
    loop = AgentLoop()
    agent = loop.create(NPC)
    _seed(agent)
    asyncio.run(agent.compact_now())
    info = summary.latest(agent.session)
    hub = ChatHub(loop, _FakeWs())

    # (a) 未激活的 npc
    out = asyncio.run(hub.handle_request("edit_compact_summary", {
        "npc_id": "查无此人", "compaction_id": info["compaction_id"], "text": "x"}))
    assert out["ok"] is False and "未激活" in out["reason"], out

    # (b) 回合进行中（运行时那一道；账本判据看不到"正在发生"）
    agent.phase = {"kind": "running", "turn": 9, "step": 1}
    out = asyncio.run(hub.handle_request("edit_compact_summary", {
        "npc_id": NPC, "compaction_id": info["compaction_id"], "text": "x"}))
    assert out["ok"] is False and "回合进行中" in out["reason"], out
    agent.phase = {"kind": "idle", "last_turn": 9}

    # (c) compactionId 对不上（面板开着时又压了一次）
    out = asyncio.run(hub.handle_request("edit_compact_summary", {
        "npc_id": NPC, "compaction_id": "not-the-current-one", "text": "x"}))
    assert out["ok"] is False and "取代" in out["reason"], out

    # (d) 已开启新一轮对话（账本判据；此处不再经过 phase）
    n_before = len(agent.session.log)
    agent.session.append("turn/start", {"turn": 41, "scene": ""})
    out = asyncio.run(hub.handle_request("edit_compact_summary", {
        "npc_id": NPC, "compaction_id": info["compaction_id"], "text": "x"}))
    assert out["ok"] is False and "新一轮对话" in out["reason"], out
    assert len(agent.session.log) == n_before + 1, "拒绝时账本只有那条 turn/start，没有多余写入"

    # (e) 缺 npc_id 是调用方 bug：抛（协议层会转 ok:false/error）
    try:
        asyncio.run(hub.handle_request("edit_compact_summary", {"text": "x"}))
        raise AssertionError("缺 npc_id 必须抛")
    except ValueError as e:
        assert "npc_id" in str(e)
    print("✓ 四类拒绝都如实回 reason，账本一字不动；缺 npc_id 抛调用方错误")


# ---------------------------------------------------------------- ② 手动压缩的推送

def test_manual_compact_pushes_summary_before_result():
    _reset(compactor=_compactor())
    loop = AgentLoop()
    agent = loop.create(NPC)
    _seed(agent)
    ws = _FakeWs()
    hub = ChatHub(loop, ws)
    asyncio.run(hub.handle_compact(NPC))

    names = [e["event"] for e in ws.events]
    assert "compact_summary" in names and "compact_result" in names, names
    assert names.index("compact_summary") < names.index("compact_result"), \
        "纪要必须先于结果提示行推送（C# 用 result 收尾，折叠行要已经在了）"
    msg = ws.of("compact_summary")[0]
    assert msg["npc_id"] == NPC
    assert msg["text"] and msg["chars"] == len(msg["text"])
    assert msg["editable"] is True, "手动压缩后还没开新回合，应当可改"
    assert msg["lock_reason"] == "" and msg["source"] == "manual"
    assert msg["compaction_id"] == summary.latest(agent.session)["compaction_id"]
    print(f"✓ 手动压缩：先 compact_summary（{msg['chars']} 字，可改）后 compact_result")


def test_manual_compact_failure_pushes_no_summary():
    """没压成（无可压切点）→ 只发结果，绝不发一条空纪要。"""
    _reset(compactor=_compactor())
    loop = AgentLoop()
    loop.create(NPC)          # 空账本：压不动
    ws = _FakeWs()
    hub = ChatHub(loop, ws)
    asyncio.run(hub.handle_compact(NPC))
    assert ws.of("compact_summary") == [], "没产出纪要就不该推 compact_summary"
    assert ws.of("compact_result"), "失败也必须回结果（否则 UI 卡在「正在压缩中」）"
    print("✓ 未压成：不发 compact_summary，只发失败结果")


# ---------------------------------------------------------------- ③ 自动压缩的推送（真实 WS）

async def _wait_conn(ws: WsServer, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while ws._conn is None:
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("客户端未连接")
        await asyncio.sleep(0.02)


def _connect(ws: WsServer):
    import websockets

    class _Ctx:
        async def __aenter__(self):
            self.conn = await websockets.connect(f"ws://127.0.0.1:{ws.bound_port}")
            return self.conn

        async def __aexit__(self, *exc):
            await self.conn.close()

    return _Ctx()


def test_auto_compact_pushes_locked_summary():
    """自动压缩：事件在回合内抛出 → 可改=false；wire 上**没有**新的 step kind。"""
    _reset(compactor=_compactor())
    loop = AgentLoop()
    agent = loop.create(NPC)
    _seed(agent)              # 先把历史堆过阈值，让本回合开始时就触发自动压缩

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_message_handler(hub.handle_message)
        await ws.start()
        try:
            async with _connect(ws) as client:
                await _wait_conn(ws)
                await client.send(json.dumps(
                    {"type": "event", "event": "player_message", "npc_id": NPC, "text": "继续聊"},
                    ensure_ascii=False))
                got = []
                while True:
                    msg = json.loads(await asyncio.wait_for(client.recv(), timeout=15))
                    got.append(msg)
                    if msg.get("event") == "npc_reply":
                        return got
        finally:
            await ws.stop()

    got = asyncio.run(scenario())
    summaries = [m for m in got if m.get("event") == "compact_summary"]
    assert len(summaries) == 1, f"自动压缩应推一次 compact_summary，实得 {len(summaries)}"
    m = summaries[0]
    assert m["npc_id"] == NPC and m["source"] == "auto"
    assert m["text"] and m["chars"] == len(m["text"])
    assert m["editable"] is False and "本轮对话已经开始" in m["lock_reason"], \
        "自动压缩落在回合内部 ⇒ 一开始就是锁定的（不能等模型回复后才锁）"
    assert m["turn"] >= 1
    # wire 上不许出现 kind=compact 的 step（它必须已经被翻译成独立事件）
    assert not [x for x in got if x.get("event") == "step" and x.get("kind") == "compact"], \
        "kind=compact 只能在 Python 内部传递，wire 上要走 compact_summary"
    # 顺序：纪要事件必须早于本回合的第一个 step（否则折叠行会画在回合内容后面）
    idx_sum = got.index(m)
    idx_first_step = next((i for i, x in enumerate(got) if x.get("event") == "step"), len(got))
    assert idx_sum < idx_first_step, "折叠行必须出现在本回合内容之前"
    print(f"✓ 自动压缩：compact_summary 先于本回合 step、editable=false（{m['chars']} 字）")
