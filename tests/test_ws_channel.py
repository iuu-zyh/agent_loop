"""WS 通道测试：单 WebSocket 全双工 + step 事件 + text_delta 流式 + WsGameBridge 往返

用 websockets 客户端模拟 C# 侧，验证：
  1. ChatHub：player_message → 回合中推 step(tool_call/tool_result) + text_delta → 收尾 npc_reply
  2. WsGameBridge：request/response 往返（get_context / call_tool）
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile

from agent_loop.agent_loop import AgentLoop
from agent_loop.bridge import StubGameBridge, WsGameBridge
from agent_loop.ws_channel import WsServer, ChatHub
from agent_loop.llm.stub_client import StubLlmClient

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_ws_channel")
NPC = "林婉清_通道测试"


def _reset(bridge=None, llm=None):
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=bridge, llm=llm)
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)


async def _wait_conn(ws: WsServer, timeout: float = 2.0):
    """等 C# 客户端真正连上（self._conn 就位），避免 request 提前落空。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while ws._conn is None:
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("C# 客户端未连接")
        await asyncio.sleep(0.02)


def test_chat_hub_streams_steps_and_reply():
    """一次 player_message → step(tool_call) + step(tool_result) + text_delta + npc_reply。"""
    bridge = StubGameBridge()
    bridge.seed_unit(NPC, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 72, "power": 8900})

    calls = {"n": 0}
    FINAL = "张三道友是筑基中期修士，实力约五千。"

    def _llm_fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [{"id": "t1", "name": "inspect_unit", "arguments": {"target": "张三"}}]}
        return {"text": FINAL, "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    events: list = []

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_message_handler(hub.handle_message)
        await ws.start()
        try:
            async with _connect(ws) as client:
                await client.send(json.dumps({"type": "event", "event": "player_message", "npc_id": NPC, "text": "查一下张三"}, ensure_ascii=False))
                while True:
                    raw = await asyncio.wait_for(client.recv(), timeout=10)
                    msg = json.loads(raw)
                    events.append(msg)
                    if msg.get("event") == "npc_reply":
                        break
        finally:
            await ws.stop()

    asyncio.run(scenario())

    kinds = [m.get("kind") for m in events if m.get("event") == "step"]
    assert "tool_call" in kinds, f"应有 tool_call step，实际={kinds}"
    assert "tool_result" in kinds, f"应有 tool_result step，实际={kinds}"
    assert any(m.get("event") == "text_delta" for m in events), "应有 text_delta 流式事件"
    replies = [m.get("text") for m in events if m.get("event") == "npc_reply"]
    assert replies and replies[0] == FINAL, f"npc_reply 应为最终文本，实际={replies}"
    # `error` 必须是**真布尔**，不能是 JSON null（回归锁定）。
    # 成功回合若发 `"error": null`，C# 惯用的 `payload["error"]?.Value<bool>()` 会抛
    # InvalidCastException（键存在但值为 null 时 `?.` 不短路）→ 该异常在
    # `ChatPresenter.OnReplyEvent` 的 try 之外炸掉 → finally 的忙标复位被跳过 →
    # 玩家看到"对话完了还说回合进行中"，且只有重启能恢复。
    for m in events:
        if m.get("event") != "npc_reply":
            continue
        assert m.get("error") is not None or "error" not in m, \
            f'npc_reply 的 error 不能是 null（会炸 C# 的 Value<bool>()），实际={m.get("error")!r}'
        if "error" in m:
            assert isinstance(m["error"], bool), f"error 必须是布尔，实际={type(m['error'])}"
    # 顺序：tool_call 在 tool_result 前，都在 npc_reply 前
    order = [m.get("event") + ":" + (m.get("kind") or "") for m in events]
    assert order.index("step:tool_call") < order.index("step:tool_result") < order.index("npc_reply:"), order
    print(f"✓ ChatHub：step(tool_call/tool_result) + text_delta + npc_reply 全链路（{len(events)} 事件）")


def test_ws_game_bridge_round_trip():
    """WsGameBridge 经 WS 通道与 C# 的 request/response 往返。"""
    async def scenario():
        ws = WsServer(port=0)
        await ws.start()
        bridge = WsGameBridge(ws)
        try:
            async with _connect(ws) as client:
                async def _respond():
                    async for raw in client:
                        msg = json.loads(raw)
                        if msg.get("type") != "request":
                            continue
                        req_id = msg.get("req_id")
                        if msg.get("method") == "get_context":
                            resp = {"type": "response", "req_id": req_id, "ok": True,
                                    "data": {"text": "自身：林婉清 金丹…", "raw": {"npc_id": "林婉清"}}}
                        elif msg.get("method") == "call_tool":
                            resp = {"type": "response", "req_id": req_id, "ok": True,
                                    "data": {"tool": "inspect_unit", "items": [1, 2]}}
                        else:
                            resp = {"type": "response", "req_id": req_id, "ok": False, "error": "unknown"}
                        await client.send(json.dumps(resp, ensure_ascii=False))

                responder = asyncio.create_task(_respond())
                ctx = await bridge.get_context("林婉清")
                assert ctx["text"].startswith("自身：林婉清"), ctx
                res = await bridge.call_tool("inspect_unit", {"target": "张三"})
                assert res["success"] is True and res["data"]["tool"] == "inspect_unit", res
                responder.cancel()
        finally:
            await ws.stop()

    asyncio.run(scenario())
    print("✓ WsGameBridge：get_context / call_tool 经 WS request/response 往返成功")


def test_ws_request_timeout_fallback():
    """C# 不应答时，WsGameBridge 返回兜底（success:false / 兜底文本），不抛异常。"""
    async def scenario():
        ws = WsServer(port=0)
        await ws.start()
        bridge = WsGameBridge(ws)
        try:
            async with _connect(ws):
                # 连接在，但模拟 C# 不应答 → 超时兜底
                res = await bridge.call_tool("inspect_unit", {"target": "张三"})
                assert res["success"] is False
        finally:
            await ws.stop()

    asyncio.run(scenario())
    print("✓ WsGameBridge：C# 不应答时超时兜底，不抛异常")


def test_rpc_slow_warning_threshold_is_milliseconds(tmp_path):
    """回归：RPC 慢告警阈值必须是「毫秒」。

    `request_timeout` 的单位是秒，若误当毫秒传给 `stage()`，阈值会缩到 0.2ms 之类，
    在超时窗口内瞬间连发多次告警（首次告警 elapsed≈0.0s）。正确算法 = min(半个超时, 30s)。
    """
    import re

    from agent_loop import log_setup

    log_path = tmp_path / "rpc.log"
    log_setup.setup_logging({"logging": {"enabled": True, "level": "DEBUG", "file": str(log_path),
                                         "console": False, "rotation": "none",
                                         "slow_ms": 30000, "slow_escalations": 3}}, force=True)

    async def scenario():
        ws = WsServer(port=0, request_timeout=0.5)
        await ws.start()
        bridge = WsGameBridge(ws)
        try:
            async with _connect(ws):
                res = await bridge.call_tool("inspect_unit", {"target": "张三"})   # C# 不应答
                assert res["success"] is False and res["error"] == "请求超时"
        finally:
            await ws.stop()

    try:
        asyncio.run(scenario())
    finally:
        log_setup.shutdown_logging()

    text = log_path.read_text(encoding="utf-8")
    assert "等 C# 主线程无应答" in text, "RPC 超时必须有显式留痕（曾完全静默）"
    elapsed = [float(x) for x in re.findall(r"已运行 ([\d.]+)s 仍未结束", text)]
    if elapsed:
        assert elapsed[0] >= 0.2, f"首个慢告警过早（{elapsed[0]}s）——阈值单位疑似写成毫秒"
    print("✓ RPC 慢告警阈值单位为毫秒（半个超时 = 250ms）")


def test_npc_initiative_event_round_trip():
    """NPC 主动开口事件：npc_initiative → ChatHub.handle_initiative → 带 initiative 标记的 npc_reply。"""
    bridge = StubGameBridge()
    bridge.seed_unit(NPC, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 72, "power": 8900,
                            "player": {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 90, "same_grid": False},
                            "recent": "上月与韩立切磋一次"})
    OPENING = "这位道友，你我如今相隔千里，心中却系着往昔情谊，特以神识传音来问候一句。"

    def _llm_fn(req):
        return {"text": OPENING, "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    events: list = []

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_message_handler(hub.handle_message)
        ws.register_initiative_handler(hub.handle_initiative)
        await ws.start()
        try:
            async with _connect(ws) as client:
                await client.send(json.dumps(
                    {"type": "event", "event": "npc_initiative", "npc_id": NPC,
                     "intent": "missing", "reason": "你们已有数日未见"}, ensure_ascii=False))
                while True:
                    raw = await asyncio.wait_for(client.recv(), timeout=10)
                    msg = json.loads(raw)
                    events.append(msg)
                    if msg.get("event") == "npc_reply":
                        break
        finally:
            await ws.stop()

    asyncio.run(scenario())

    replies = [m for m in events if m.get("event") == "npc_reply"]
    assert replies and replies[0]["text"] == OPENING, f"开场白错误 {replies}"
    assert replies[0].get("initiative") is True, "npc_reply 应带 initiative=true 标记"
    assert replies[0].get("intent") == "missing", replies[0]
    # 主动回合仍走流式：应有 text_delta 事件
    assert any(m.get("event") == "text_delta" for m in events), "主动回合应有流式事件"
    print(f"✓ npc_initiative 端到端：意图(missing) → 流式 → 带标记 npc_reply（{len(events)} 事件）")


def test_multi_npc_turns_run_in_parallel():
    """per-agent 锁：不同 NPC 的回合并行（肉眼可见的耗时差）。"""
    bridge = StubGameBridge()
    for nid in ("王二_并", "赵三_并"):
        bridge.seed_unit(nid, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 70, "power": 8000,
                                "player": {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 80, "same_grid": True},
                                "recent": "无事"})

    async def llm_fn(req):
        await asyncio.sleep(0.25)   # 慢回合：模拟长 LLM 输出
        return {"text": "回", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=llm_fn))
    loop = AgentLoop()

    async def scenario():
        hub = ChatHub(loop, WsServer(port=0), max_concurrent_turns=2)
        t0 = asyncio.get_running_loop().time()
        await asyncio.gather(
            hub.handle_message("王二_并", "你好"),
            hub.handle_message("赵三_并", "你好"),
        )
        dt = asyncio.get_running_loop().time() - t0
        assert dt < 0.45, f"两个 0.25s 回合应并行（串行约0.5s），实际 {dt:.3f}s"

    asyncio.run(scenario())
    print("✓ per-agent 锁：不同 NPC 回合并行")


def test_same_npc_turns_serialized():
    """per-agent 锁：同一 NPC 的回合绝对串行（不重叠）。"""
    bridge = StubGameBridge()
    bridge.seed_unit("李四_串", {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 70, "power": 8000,
                                "player": {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 80, "same_grid": True},
                                "recent": "无事"})
    llm_calls: list = []

    async def llm_fn(req):
        llm_calls.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.2)
        return {"text": "回", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=llm_fn))
    loop = AgentLoop()

    async def scenario():
        hub = ChatHub(loop, WsServer(port=0), max_concurrent_turns=2)
        await asyncio.gather(
            hub.handle_message("李四_串", "第一问"),
            hub.handle_message("李四_串", "第二问"),
        )
        assert len(llm_calls) == 2
        # 第二条的 LLM 调用必须晚于第一条开始 + 0.2s（串行不重叠）
        assert llm_calls[1] >= llm_calls[0] + 0.18, f"同 NPC 应串行，时间={llm_calls}"

    asyncio.run(scenario())
    print("✓ per-agent 锁：同 NPC 回合串行（第二问等第一问）")


def test_concurrent_turns_cap():
    """全局并发闸：同时运行的回合数不超过 max_concurrent_turns。"""
    bridge = StubGameBridge()
    for nid in ("甲_限", "乙_限", "丙_限"):
        bridge.seed_unit(nid, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 70, "power": 8000,
                                "player": {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 80, "same_grid": True},
                                "recent": "无事"})
    active = {"cur": 0, "peak": 0}

    async def llm_fn(req):
        active["cur"] += 1
        active["peak"] = max(active["peak"], active["cur"])
        await asyncio.sleep(0.2)
        active["cur"] -= 1
        return {"text": "回", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=llm_fn))
    loop = AgentLoop()

    async def scenario():
        hub = ChatHub(loop, WsServer(port=0), max_concurrent_turns=1)
        await asyncio.gather(*[hub.handle_message(nid, "你好") for nid in ("甲_限", "乙_限", "丙_限")])
        assert active["peak"] == 1, f"并发上限=1 时峰值应=1，实际峰值={active['peak']}"

    asyncio.run(scenario())
    print("✓ 全局并发闸：max_concurrent_turns=1 → 峰值并发 1")


def test_initiative_fast_fail_when_busy():
    """快速失败：同 NPC 回合进行中（锁外 phase=running），initiative 直接丢弃不投信。"""
    bridge = StubGameBridge()
    bridge.seed_unit("百里_急", {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 70, "power": 8000,
                                "player": {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 80, "same_grid": True},
                                "recent": "无事"})

    async def llm_fn(req):
        await asyncio.sleep(0.3)
        return {"text": "回", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=llm_fn))
    loop = AgentLoop()

    async def scenario():
        hub = ChatHub(loop, WsServer(port=0), max_concurrent_turns=2)
        t1 = asyncio.create_task(hub.handle_message("百里_急", "你好"))
        await asyncio.sleep(0.05)                       # 让玩家回合进入 LLM（phase=running）
        await hub.handle_initiative("百里_急", "greet")  # 忙 → 快速失败丢弃
        agent = loop.get("百里_急")
        init_msgs = [ev for ev in agent.session.log
                     if ev.get("type") == "user/message" and ev["data"].get("source", {}).get("kind") == "initiative"]
        assert not init_msgs, "忙时应丢弃 initiative，不得投信落账"
        await t1

    asyncio.run(scenario())
    print("✓ initiative 快速失败：同 NPC 回合进行中 → 直接丢弃")


def test_failed_turn_returns_fallback_event_and_keeps_account_clean():
    """B：回合失败 → 推带 error=True 的兜底 npc_reply；session 账本不落 assistant（模型下轮看事实账）。"""
    bridge = StubGameBridge()
    bridge.seed_unit("败田_兜", {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 70, "power": 8000,
                                "player": {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 80, "same_grid": True},
                                "recent": "无事"})

    class _FailAlwaysLlm:
        async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
            raise ConnectionError("模型服务不可达")

    _reset(bridge=bridge, llm=_FailAlwaysLlm())
    loop = AgentLoop()
    rec = _RecWs()

    async def scenario():
        hub = ChatHub(loop, rec)
        await hub.handle_message("败田_兜", "你还在吗？")

    asyncio.run(scenario())
    replies = [e for e in rec.events if e["event"] == "npc_reply"]
    assert replies and replies[0].get("error") is True, f"兜底应带 error=True {replies}"
    assert replies[0]["text"] == ChatHub.FALLBACK_TEXT
    agent = loop.get("败田_兜")
    # 账本不落 assistant（无剧情文案）；失败回合已由 _turn 闭合为 turn/end:error
    assert not any(ev["type"] == "assistant/message" for ev in agent.session.log), "失败回合不应落 assistant"
    assert any(ev["type"] == "turn/end" and ev["data"].get("reason", {}).get("kind") == "error" for ev in agent.session.log)
    print(f"✓ 兜底：error=True {replies[0]['text']!r}；账本无 assistant（turn/end:error 闭合）")


def test_failed_turn_phase_recovers_and_initiative_not_lost():
    """C + 集成：失败后 phase 自愈为 idle → 下一次 initiative 不被误丢。"""
    bridge = StubGameBridge()
    bridge.seed_unit("回春_愈", {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 70, "power": 8000,
                                "player": {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 80, "same_grid": True},
                                "recent": "无事"})

    class _FlakyLlm:
        def __init__(self):
            self.calls = 0

        async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
            from agent_loop.llm.base import LlmResult as _R
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("第一次失败")
            return _R(text="道友见谅，方才传音中断，如今寻你来了。", tool_calls=[])

    _reset(bridge=bridge, llm=_FlakyLlm())
    loop = AgentLoop()
    rec = _RecWs()

    async def scenario():
        hub = ChatHub(loop, rec)
        await hub.handle_message("回春_愈", "在吗？")          # 第一次 → 失败兜底
        agent = loop.get("回春_愈")
        assert agent.phase["kind"] == "idle", f"失败后 phase 应自愈 idle，实际 {agent.phase}"
        await hub.handle_initiative("回春_愈", "greet")        # 第二次 → 成功（不应被误丢）

    asyncio.run(scenario())
    evs = [e for e in rec.events if e["event"] == "npc_reply"]
    assert evs[0].get("error") is True, "第一次应为兜底"
    assert evs[1].get("initiative") is True, "失败自愈后 NPC 开口不应被误丢"
    agent = loop.get("回春_愈")
    # 第二次回合有正常 assistant；第一次没有
    assistants = [ev for ev in agent.session.log if ev["type"] == "assistant/message"]
    assert len(assistants) == 1, f"只有第二次回合落 assistant，实际 {len(assistants)}"
    print("✓ 自愈：失败后 phase=idle，initiative 不误丢；账本只含成功的回合")


class _RecWs:
    """记录 send_event 的假 WsServer，供兜底/自愈测试隔离事件层。"""

    def __init__(self):
        self.events: list = []

    async def send_event(self, event, npc_id="", **fields):
        self.events.append({"event": event, "npc_id": npc_id, **fields})
        return True


class _connect:
    """async with 包装：连接 + 等 server 端 _conn 就位。"""

    def __init__(self, ws: WsServer):
        self.ws = ws

    async def __aenter__(self):
        import websockets

        self._client = await websockets.connect(f"ws://127.0.0.1:{self.ws.bound_port}")
        await _wait_conn(self.ws)
        return self._client

    async def __aexit__(self, *exc):
        await self._client.close()


if __name__ == "__main__":
    test_chat_hub_streams_steps_and_reply()
    test_ws_game_bridge_round_trip()
    test_ws_request_timeout_fallback()
    test_npc_initiative_event_round_trip()
    test_multi_npc_turns_run_in_parallel()
    test_same_npc_turns_serialized()
    test_concurrent_turns_cap()
    test_initiative_fast_fail_when_busy()
    test_failed_turn_returns_fallback_event_and_keeps_account_clean()
    test_failed_turn_phase_recovers_and_initiative_not_lost()
    print("\nWS 通道测试全部通过")
