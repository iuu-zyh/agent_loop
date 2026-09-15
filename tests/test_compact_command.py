"""手动压缩 /compact（2026-09-09）：专用 compact 事件通道 + player_message 兜底拦截

验证四条链路：
  1. event=compact（C# 新端专用通道）→ 压缩成功 → compact_result(ok=true, 文案含压缩数据)
  2. event=compact → 压缩无可压（compactor 返回 None）→ compact_result(ok=false, 如实文案)
  3. event=compact → compaction 未配置 → compact_result(ok=false, "压缩功能未启用")
  4. player_message "/compact"（旧 DLL / chat_cli 兜底）→ npc_reply(error=true) 系统提示，
     不触发对话 LLM 回合、不把 "/compact" 写进对话历史
  5. 压缩进行中再发 /compact → 快速失败"正在压缩中"（防重入）
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile

from agent_loop.agent_loop import AgentLoop
from agent_loop.bridge import StubGameBridge
from agent_loop.ws_channel import WsServer, ChatHub
from agent_loop.llm.stub_client import StubLlmClient

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_compact_cmd")
NPC = "林婉清_压缩测试"


def _reset(llm=None, compactor=None):
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=StubGameBridge(), llm=llm, compactor=compactor)
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)


class _FakeCompactor:
    """压缩桩：compact_now 返回可编程结果，maybe 恒不动（自动压缩不参与本测试）"""

    def __init__(self, result=None, delay: float = 0.0):
        self.result = result
        self.delay = delay
        self.calls = 0

    async def maybe(self, session, header):
        return None

    async def compact_now(self, session, npc_id):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


class _FakeWs:
    """替身 WsServer：只收 send_event，供直驱 ChatHub 的用例断言回推帧"""

    def __init__(self):
        self.events = []

    async def send_event(self, event, npc_id="", **fields):
        self.events.append({"event": event, "npc_id": npc_id, **fields})


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


def _user_ledger_texts(loop, npc):
    """会话账本里玩家可见的 user/message 文本（排除 plugin 内部物：新相识风味/压缩 checkpoint）"""
    agent = loop.get(npc)
    out = []
    for ev in agent.session.log:
        if ev.get("type") != "user/message":
            continue
        data = ev.get("data") or {}
        if (data.get("source") or {}).get("kind") == "plugin":
            continue
        out.append(data.get("content"))
    return out


def test_compact_event_channel_success():
    """专用通道：compact 事件 → 压缩成功 → compact_result(ok=true) 含上下文前后对比"""
    _reset(compactor=_FakeCompactor(result={"range": (0, 1), "compactionId": "c1"}))
    loop = AgentLoop()

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_compact_handler(hub.handle_compact)
        await ws.start()
        try:
            async with _connect(ws) as client:
                await _wait_conn(ws)
                await client.send(json.dumps({"type": "event", "event": "compact", "npc_id": NPC}, ensure_ascii=False))
                while True:
                    msg = json.loads(await asyncio.wait_for(client.recv(), timeout=10))
                    if msg.get("event") == "compact_result":
                        return msg
        finally:
            await ws.stop()

    msg = asyncio.run(scenario())
    assert msg["ok"] is True, msg
    assert "压缩完成" in msg["text"], msg
    assert "→" in msg["text"], msg  # 压缩前后数值对比
    agent = loop.get(NPC)
    assert agent.compactor.calls == 1
    # /compact 是控制指令：不进对话历史
    assert not _user_ledger_texts(loop, NPC), "/compact 不应落进对话历史"
    print(f"✓ 专用通道压缩成功：{msg['text']}")


def test_compact_event_channel_nothing_to_compact():
    """专用通道：压缩无可压（compactor 返回 None）→ ok=false 如实反馈"""
    _reset(compactor=_FakeCompactor(result=None))
    loop = AgentLoop()

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_compact_handler(hub.handle_compact)
        await ws.start()
        try:
            async with _connect(ws) as client:
                await _wait_conn(ws)
                await client.send(json.dumps({"type": "event", "event": "compact", "npc_id": NPC}, ensure_ascii=False))
                while True:
                    msg = json.loads(await asyncio.wait_for(client.recv(), timeout=10))
                    if msg.get("event") == "compact_result":
                        return msg
        finally:
            await ws.stop()

    msg = asyncio.run(scenario())
    assert msg["ok"] is False, msg
    assert "没有需要压缩" in msg["text"], msg
    print(f"✓ 无可压缩如实反馈：{msg['text']}")


def test_compact_event_channel_no_compactor():
    """专用通道：compaction 未配置 → "压缩功能未启用"，绝不静默"""
    _reset(compactor=None)
    loop = AgentLoop()

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_compact_handler(hub.handle_compact)
        await ws.start()
        try:
            async with _connect(ws) as client:
                await _wait_conn(ws)
                await client.send(json.dumps({"type": "event", "event": "compact", "npc_id": NPC}, ensure_ascii=False))
                while True:
                    msg = json.loads(await asyncio.wait_for(client.recv(), timeout=10))
                    if msg.get("event") == "compact_result":
                        return msg
        finally:
            await ws.stop()

    msg = asyncio.run(scenario())
    assert msg["ok"] is False and "未启用" in msg["text"], msg
    print(f"✓ 未配置压缩如实反馈：{msg['text']}")


def test_player_message_compact_fallback():
    """兜底通道：player_message "/compact" → npc_reply(error=true)，零 LLM 回合、零历史污染"""
    llm_calls = {"n": 0}

    def _llm_fn(req):
        llm_calls["n"] += 1
        return {"text": "不该被调到", "tool_calls": []}

    _reset(llm=StubLlmClient(fn=_llm_fn), compactor=_FakeCompactor(result={"range": (0, 1), "compactionId": "c1"}))
    loop = AgentLoop()

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_message_handler(hub.handle_message)
        await ws.start()
        try:
            async with _connect(ws) as client:
                await _wait_conn(ws)
                await client.send(json.dumps(
                    {"type": "event", "event": "player_message", "npc_id": NPC, "text": "  /Compact "},
                    ensure_ascii=False))
                while True:
                    msg = json.loads(await asyncio.wait_for(client.recv(), timeout=10))
                    if msg.get("event") == "npc_reply":
                        return msg
        finally:
            await ws.stop()

    msg = asyncio.run(scenario())
    assert msg["error"] is True, msg  # C# 端渲染为系统提示行
    assert "压缩完成" in msg["text"], msg
    assert llm_calls["n"] == 0, f"/compact 不应触发对话 LLM，实际调用 {llm_calls['n']} 次"
    # 大小写/空白容忍："  /Compact " 命中同一条指令
    agent = loop.get(NPC)
    assert agent.compactor.calls == 1
    assert not _user_ledger_texts(loop, NPC), "/compact 不应落进对话历史"
    print(f"✓ 兜底通道（player_message）拦截成功：{msg['text']}")


def test_compact_reentry_guard():
    """防重入：压缩进行中再发 /compact → 快速失败"正在压缩中"，不排队二次压缩"""
    slow = _FakeCompactor(result={"range": (0, 1), "compactionId": "c1"}, delay=0.3)
    _reset(compactor=slow)
    loop = AgentLoop()
    hub = ChatHub(loop, _FakeWs())

    async def scenario():
        first = asyncio.create_task(hub.handle_compact(NPC))
        await asyncio.sleep(0.05)  # 让第一个进入 _compacting + 持锁
        second = asyncio.create_task(hub.handle_compact(NPC))
        await asyncio.gather(first, second)

    asyncio.run(scenario())
    assert slow.calls == 1, f"第二个 /compact 应被快速失败，实际压缩执行 {slow.calls} 次"
    events = hub.ws.events
    assert any(e["event"] == "compact_result" and "正在压缩中" in e.get("text", "") for e in events), events
    print("✓ 防重入快速失败：正在压缩中")


def test_compact_missing_npc_id_still_replies(tmp_path):
    """回归：compact 事件缺 npc_id 时曾是静默 return → C# 的「正在压缩中」永远不更新且零日志。

    现在必须回推 compact_result（失败文案）并留 WARNING —— 这是"提示行卡死"的一类成因。
    """
    from agent_loop import log_setup

    _reset(compactor=_FakeCompactor(result=None))
    hub = ChatHub(AgentLoop(), _FakeWs())
    log_path = tmp_path / "c.log"
    log_setup.setup_logging({"logging": {"enabled": True, "level": "DEBUG", "file": str(log_path),
                                        "console": False, "rotation": "none",
                                        "slow_ms": 0, "slow_escalations": 0}}, force=True)
    try:
        asyncio.run(hub.handle_compact(""))
    finally:
        log_setup.shutdown_logging()

    events = hub.ws.events
    assert len(events) == 1 and events[0]["event"] == "compact_result", events
    assert events[0]["ok"] is False and "缺少" in events[0]["text"], events
    assert "缺少 npc_id" in log_path.read_text(encoding="utf-8")
    print("✓ 缺 npc_id 也必回推结果 + 留痕")


def test_manual_compact_path_is_fully_logged(tmp_path):
    """回归：手动压缩路径必须全程留痕（曾经一行日志都没有，"卡在压缩中"无从查起）。"""
    from agent_loop import log_setup

    _reset(compactor=_FakeCompactor(result={"range": (0, 1), "compactionId": "c1"}))
    hub = ChatHub(AgentLoop(), _FakeWs())
    log_path = tmp_path / "c2.log"
    log_setup.setup_logging({"logging": {"enabled": True, "level": "DEBUG", "file": str(log_path),
                                        "console": False, "rotation": "none",
                                        "slow_ms": 0, "slow_escalations": 0}}, force=True)
    try:
        asyncio.run(hub.handle_compact(NPC))
    finally:
        log_setup.shutdown_logging()

    text = log_path.read_text(encoding="utf-8")
    for frag in ("/compact 收到", "压缩前置检查", "压缩开始", "压缩成功", "/compact 结束"):
        assert frag in text, f"缺少埋点：{frag}\n---\n{text}"
    assert f"[{NPC} " in text, "压缩日志必须带 npc 上下文"
    assert "compact.manual]" in text, "整段压缩要走 stage（便于超时告警）"
    print("✓ 手动压缩全程留痕（含 stage 与 npc 上下文）")


def test_manual_compact_slow_emits_warning(tmp_path):
    """手动压缩超阈值走慢告警 —— "卡在压缩中"当场可见，不必等它结束。"""
    from agent_loop import log_setup

    _reset(compactor=_FakeCompactor(result={"range": (0, 1), "compactionId": "c1"}, delay=0.35))
    hub = ChatHub(AgentLoop(), _FakeWs())
    log_path = tmp_path / "c3.log"
    log_setup.setup_logging({"logging": {"enabled": True, "level": "DEBUG", "file": str(log_path),
                                        "console": False, "rotation": "none",
                                        "slow_ms": 100, "slow_escalations": 3}}, force=True)
    try:
        asyncio.run(hub.handle_compact(NPC))
    finally:
        log_setup.shutdown_logging()

    text = log_path.read_text(encoding="utf-8")
    assert "慢告警" in text and "compact.manual" in text, text
    print("✓ 压缩慢告警生效")
