"""UI 历史回放测试：project_ui_history 投影 + get_history WS 端到端

验证（对应游戏内对话 UI 的历史回放需求）：
  1. 投影：账本 → UI 时间线（user/tool_call/tool_result/assistant 顺序与字段）
  2. 筛选：L1 runtime context（source.kind=plugin）不进 UI；initiative 伪 user 转分隔条而非玩家气泡
  3. 截断：半截 turn（无 turn/end）不入历史；max_turns 取尾部
  4. 失败回合：turn/end:error → turn_error 项
  5. WS：get_history request → response 往返（含未知 NPC / 未知 method / 缺参错误路径）
  6. 全链路：player_message 回合完成后，同连接 get_history 能取回该回合
"""
from __future__ import annotations

import sys
from pathlib import Path

# 直跑（python tests/test_ui_history.py）时保证 agent_loop 以包名解析：项目根本身即包，插其父目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import os
import shutil
import tempfile

from agent_loop.agent_loop import AgentLoop
from agent_loop.bridge import StubGameBridge
from agent_loop.session import Session
from agent_loop.dialogue_agent import create_user_message
from agent_loop import llm_adapter
from agent_loop.history import project_ui_history
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.ws_channel import WsServer, ChatHub

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_ui_history")
NPC = "林婉清_历史"


def _reset(bridge=None, llm=None):
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=bridge, llm=llm)
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)


def _seed(bridge, npc):
    bridge.seed_unit(npc, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 72, "power": 8900,
                           "player": {"name": "韩立", "realm": "筑基", "relation": "道友", "intim": 90, "same_grid": True},
                           "recent": "上月与韩立切磋一次"})


async def _wait_conn(ws: WsServer, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while ws._conn is None:
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("客户端未连接")
        await asyncio.sleep(0.02)


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


def _kinds(items):
    return [i["kind"] for i in items]


def test_project_ui_history_filters_plugin_and_projects_tool_flow():
    """真实 stub 流：user → tool_call → tool_result → assistant；L1 context(plugin) 被滤掉。"""
    bridge = StubGameBridge()
    _seed(bridge, NPC)
    bridge.seed_unit("张三", {"realm": "筑基", "sect": "化神殿", "pos": "永宁州", "mood": 60, "power": 5000})

    calls = {"n": 0}

    def _llm_fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [{"id": "t1", "name": "inspect_unit", "arguments": {"target": "张三"}}]}
        if calls["n"] == 2:
            return {"text": "张三道友是筑基中期修士。", "tool_calls": []}
        return {"text": "第二问也答了。", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()

    async def scenario():
        agent = loop.create(NPC)
        agent.send("查一下张三")
        await agent.run_until_idle()
        agent.send("第二问")
        await agent.run_until_idle()

    asyncio.run(scenario())

    agent = loop.get(NPC)
    # 账本里确有 plugin 源的 L1 context（投影的过滤对象真实存在，非空转）
    plugin_msgs = [ev for ev in agent.session.log
                   if ev.get("type") == "user/message" and (ev["data"].get("source") or {}).get("kind") == "plugin"]
    assert plugin_msgs, "前提：账本应含 L1 runtime context（plugin 源 user/message）"

    out = project_ui_history(agent.session, max_turns=10)
    assert out["complete_turns"] == 2, out
    items = out["items"]
    assert _kinds(items) == ["user", "tool_call", "tool_result", "assistant", "user", "assistant"], _kinds(items)
    assert items[0]["text"] == "查一下张三" and items[4]["text"] == "第二问", items
    # tool_call / tool_result 配对与字段（args 含 dialogue_agent 注入的 initiator=当前对话 NPC）
    assert items[1]["name"] == "inspect_unit" and items[1]["args"] == {"target": "张三", "initiator": NPC}, items[1]
    assert items[2]["call_id"] == items[1]["call_id"] and items[2]["is_error"] is False, items[2]
    # plugin 源（L1 context）一条都不许出现在 UI 时间线
    assert not any(i["kind"] == "user" and i["text"].startswith("Current runtime context") for i in items)
    assert sum(1 for i in items if i["kind"] == "user") == 2, "玩家消息恰好 2 条（context 不算）"
    print("✓ 投影：顺序/配对/字段正确；L1 context(plugin) 全部滤除")


def test_project_ui_history_initiative_divider_not_user():
    """主动开口伪 user（source.kind=initiative）→ initiative 项，不显示为玩家气泡。"""
    bridge = StubGameBridge()
    _seed(bridge, NPC)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "道友安好。", "tool_calls": []}))
    loop = AgentLoop()

    async def scenario():
        agent = loop.create(NPC)
        agent.send("（林婉清想主动问候）", source={"kind": "initiative", "intent": "greet", "reason": "重逢"})
        await agent.run_until_idle()

    asyncio.run(scenario())

    agent = loop.get(NPC)
    out = project_ui_history(agent.session)
    assert _kinds(out["items"]) == ["initiative", "assistant"], _kinds(out["items"])
    assert out["items"][0]["intent"] == "greet" and out["items"][0]["reason"] == "重逢", out["items"][0]
    assert not any(i["kind"] == "user" for i in out["items"]), "伪 user 不得进玩家气泡"
    print("✓ 投影：initiative 伪 user → 分隔条项（intent/reason 保留），非玩家气泡")


def test_project_ui_history_open_turn_cut_and_limit():
    """半截 turn（无 turn/end）不入历史；max_turns 只取尾部 N 个完整 turn。"""
    bridge = StubGameBridge()
    _seed(bridge, NPC)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回。", "tool_calls": []}))
    loop = AgentLoop()

    async def scenario():
        agent = loop.create(NPC)
        agent.send("第一问")
        await agent.run_until_idle()
        agent.send("第二问")
        await agent.run_until_idle()
        # 手造半截 turn：开转不闭合（模拟崩溃残留）
        sess = agent.session
        sess.append("turn/start", {"turn": 99})
        sess.append("user/message", create_user_message("半截消息"), {"surfaceOp": "append"})
        sess.append("assistant/message", {"message": llm_adapter.create_canonical_assistant_message("半截回复", [])},
                    {"surfaceOp": "append"})

    asyncio.run(scenario())

    agent = loop.get(NPC)
    out = project_ui_history(agent.session, max_turns=10)
    assert out["complete_turns"] == 2, out
    assert "半截" not in json.dumps(out, ensure_ascii=False), "半截 turn 不得入历史"
    assert _kinds(out["items"]) == ["user", "assistant", "user", "assistant"], _kinds(out["items"])
    # max_turns=1：只取最后一个完整 turn
    out1 = project_ui_history(agent.session, max_turns=1)
    assert out1["complete_turns"] == 2 and _kinds(out1["items"]) == ["user", "assistant"], out1
    assert out1["items"][0]["text"] == "第二问"
    print("✓ 截断：半截 turn 被切；max_turns 取尾部")


def test_project_ui_history_turn_error_item():
    """失败回合（turn/end:error）→ turn_error 项，回放时 UI 可显示系统提示。"""
    bridge = StubGameBridge()
    _seed(bridge, NPC)

    class _FailAlwaysLlm:
        async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
            raise ConnectionError("模型服务不可达")

    _reset(bridge=bridge, llm=_FailAlwaysLlm())
    loop = AgentLoop()

    async def scenario():
        agent = loop.create(NPC)
        agent.send("你还在吗？")
        try:
            await agent.run_until_idle()
        except Exception:
            pass  # 异常照抛（编排层兜底），账本已闭合 turn/end:error

    asyncio.run(scenario())

    out = project_ui_history(loop.get(NPC).session)
    errors = [i for i in out["items"] if i["kind"] == "turn_error"]
    assert len(errors) == 1 and "模型服务不可达" in errors[0]["text"], out["items"]
    # 失败回合不落 assistant，故无 assistant 项
    assert not any(i["kind"] == "assistant" for i in out["items"]), out["items"]
    print("✓ 投影：失败回合 → turn_error 项（含错误文本），无 assistant")


def test_project_ui_history_max_turns_zero_means_all():
    """max_turns=0 / None / 负数 = **全部**（09-13 用户要求"历史要看全部的"）。

    三态语义：正数 = 最近 N 个完整 turn；0/负数/None = 不截断；非法值回落缺省 10。
    `complete_turns` 恒为"未被删的完整 turn 总数"，不受截断影响。
    """
    bridge = StubGameBridge()
    _seed(bridge, NPC)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回。", "tool_calls": []}))
    loop = AgentLoop()

    async def scenario():
        agent = loop.create(NPC)
        for q in ("第一问", "第二问", "第三问"):
            agent.send(q)
            await agent.run_until_idle()

    asyncio.run(scenario())
    sess = loop.get(NPC).session

    full = project_ui_history(sess, max_turns=0)
    assert full["complete_turns"] == 3, full["complete_turns"]
    assert _kinds(full["items"]) == ["user", "assistant"] * 3, _kinds(full["items"])
    assert full["items"][0]["text"] == "第一问", "0 = 全部：必须含最早的一回合"
    for alias in (None, -5):
        assert _kinds(project_ui_history(sess, max_turns=alias)["items"]) == _kinds(full["items"]), alias
    assert _kinds(project_ui_history(sess, max_turns=2)["items"]) == ["user", "assistant"] * 2
    assert _kinds(project_ui_history(sess, max_turns="all")["items"]) == _kinds(full["items"]), \
        "非法值回落缺省 10（此处仅 3 回合，故等于全部）"
    print("✓ 投影：max_turns=0/None/负数 = 全部；正数仍取尾部 N；非法值回落 10")


def test_ws_open_chat_limit_zero_returns_all_turns():
    """端到端：open_chat `limit=0` → 回全部回合；缺省 limit 仍是最近 10 轮（向后兼容）。

    钉住 09-13 的"历史看全部"改动：C# `ChatPresenter` 两处固定传 `limit=0`
    （开窗回放 + 删除后重放），Python `_parse_limit` 解释成"不截断"。
    """
    bridge = StubGameBridge()
    _seed(bridge, NPC)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "回。", "tool_calls": []}))
    loop = AgentLoop()
    out: list = []

    async def scenario():
        agent = loop.get(NPC) or loop.create(NPC)
        for i in range(12):
            agent.send(f"问{i}")
            await agent.run_until_idle()
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_request_handler(hub.handle_request)
        await ws.start()
        try:
            async with _connect(ws) as client:
                for req_id, params in (("u1", {"npc_id": NPC, "limit": 0}), ("u2", {"npc_id": NPC})):
                    await client.send(json.dumps({"type": "request", "req_id": req_id, "method": "open_chat",
                                                  "params": params}, ensure_ascii=False))
                    out.append(json.loads(await asyncio.wait_for(client.recv(), timeout=5)))
        finally:
            await ws.stop()

    asyncio.run(scenario())
    all_items = out[0]["data"]["items"]
    assert out[0]["ok"] is True and out[0]["data"]["complete_turns"] == 12, out[0]
    assert [i["text"] for i in all_items if i["kind"] == "user"] == [f"问{i}" for i in range(12)], \
        "limit=0 必须回全部 12 轮"
    default_items = out[1]["data"]["items"]
    assert len([i for i in default_items if i["kind"] == "user"]) == 10, "缺省 limit 仍是最近 10 轮"
    print("✓ WS open_chat：limit=0 回全部 12 轮；缺省仍为最近 10 轮")


def test_parse_limit_semantics():
    """RPC 层 limit 解析（open_chat/get_history/delete_history 共用）。"""
    assert ChatHub._parse_limit({}) == 10, "缺省 = 10（老行为）"
    assert ChatHub._parse_limit({"limit": None}) == 10
    assert ChatHub._parse_limit({"limit": "junk"}) == 10
    assert ChatHub._parse_limit({"limit": 0}) == 0, "0 = 全部"
    assert ChatHub._parse_limit({"limit": -3}) == 0, "负数 = 全部"
    assert ChatHub._parse_limit({"limit": 7}) == 7
    assert ChatHub._parse_limit({"limit": "7"}) == 7, "字符串数字照解（JSON 客户端可能发字符串）"
    print("✓ RPC：limit 0/负 = 全部，缺省/非法 = 10")


def test_ws_get_history_round_trip():
    """WS request/response：get_history 正常路径 + 未知 NPC / 未知 method / 缺参错误路径。"""
    sess = Session(id=NPC, header={"id": NPC})
    sess.append("turn/start", {"turn": 1})
    sess.append("step/start", {"turn": 1, "step": 1})
    sess.append("user/message", create_user_message("你好"), {"surfaceOp": "append"})
    sess.append("tool/call", {"turn": 1, "step": 1, "callId": "t1", "name": "inspect_unit",
                              "arguments": json.dumps({"target": "张三"}, ensure_ascii=False)})
    sess.append("tool/result", {"message": llm_adapter.create_canonical_tool_result_message(
        "t1", json.dumps({"success": True, "data": {"x": 1}}, ensure_ascii=False))}, {"surfaceOp": "append"})
    sess.append("assistant/message", {"message": llm_adapter.create_canonical_assistant_message("张三是筑基修士。", [])},
                {"surfaceOp": "append"})
    sess.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})

    class _FakeAgent:
        id = NPC
        session = sess

    class _FakeLoop:
        def get(self, npc_id):
            return _FakeAgent() if npc_id == NPC else None

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(_FakeLoop(), ws)
        ws.register_request_handler(hub.handle_request)
        await ws.start()
        try:
            async with _connect(ws) as client:
                # 正常路径
                await client.send(json.dumps({"type": "request", "req_id": "u1", "method": "get_history",
                                              "params": {"npc_id": NPC, "limit": 10}}, ensure_ascii=False))
                resp = json.loads(await asyncio.wait_for(client.recv(), timeout=5))
                assert resp["type"] == "response" and resp["req_id"] == "u1" and resp["ok"] is True, resp
                assert _kinds(resp["data"]["items"]) == ["user", "tool_call", "tool_result", "assistant"], resp
                assert resp["data"]["items"][1]["args"] == {"target": "张三"}
                # 未知 NPC → 空历史（不建活体）
                await client.send(json.dumps({"type": "request", "req_id": "u2", "method": "get_history",
                                              "params": {"npc_id": "查无此人"}}, ensure_ascii=False))
                resp2 = json.loads(await asyncio.wait_for(client.recv(), timeout=5))
                assert resp2["ok"] is True and resp2["data"]["items"] == [] and resp2["data"]["complete_turns"] == 0, resp2
                # 未知 method → ok:false
                await client.send(json.dumps({"type": "request", "req_id": "u3", "method": "nope", "params": {}},
                                             ensure_ascii=False))
                resp3 = json.loads(await asyncio.wait_for(client.recv(), timeout=5))
                assert resp3["ok"] is False and "unknown method" in resp3["error"], resp3
                # 缺 npc_id → ok:false
                await client.send(json.dumps({"type": "request", "req_id": "u4", "method": "get_history", "params": {}},
                                             ensure_ascii=False))
                resp4 = json.loads(await asyncio.wait_for(client.recv(), timeout=5))
                assert resp4["ok"] is False and "npc_id" in resp4["error"], resp4
        finally:
            await ws.stop()

    asyncio.run(scenario())
    print("✓ WS get_history：正常往返 + 未知NPC(空) + 未知method/缺参(ok:false)")


def test_ws_get_history_after_real_turn():
    """全链路：player_message 回合完成后，同一连接 get_history 取回该回合（真实 AgentLoop）。"""
    bridge = StubGameBridge()
    _seed(bridge, NPC)

    calls = {"n": 0}

    def _llm_fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [{"id": "t1", "name": "inspect_unit", "arguments": {"target": "张三"}}]}
        return {"text": "张三道友是筑基中期修士，实力约五千。", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    out: list = []  # 场景闭包外的容器：接住 get_history 的 response 供外部断言

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(loop, ws)
        ws.register_message_handler(hub.handle_message)
        ws.register_initiative_handler(hub.handle_initiative)
        ws.register_request_handler(hub.handle_request)
        await ws.start()
        try:
            async with _connect(ws) as client:
                await client.send(json.dumps({"type": "event", "event": "player_message",
                                              "npc_id": NPC, "text": "查一下张三"}, ensure_ascii=False))
                while True:
                    msg = json.loads(await asyncio.wait_for(client.recv(), timeout=10))
                    if msg.get("event") == "npc_reply":
                        break
                # 回合收口后拉历史（模拟游戏 UI 开窗回放）
                await client.send(json.dumps({"type": "request", "req_id": "u1", "method": "get_history",
                                              "params": {"npc_id": NPC, "limit": 10}}, ensure_ascii=False))
                out.append(json.loads(await asyncio.wait_for(client.recv(), timeout=5)))
        finally:
            await ws.stop()

    asyncio.run(scenario())
    resp = out[0]
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["npc_id"] == NPC and data["complete_turns"] == 1, data
    assert _kinds(data["items"]) == ["user", "tool_call", "tool_result", "assistant"], data["items"]
    assert data["items"][0]["text"] == "查一下张三"
    assert data["items"][3]["text"] == "张三道友是筑基中期修士，实力约五千。"
    print("✓ 全链路：player_message 回合 → 同连接 get_history 完整取回该回合")


if __name__ == "__main__":
    test_project_ui_history_filters_plugin_and_projects_tool_flow()
    test_project_ui_history_initiative_divider_not_user()
    test_project_ui_history_open_turn_cut_and_limit()
    test_project_ui_history_max_turns_zero_means_all()
    test_ws_open_chat_limit_zero_returns_all_turns()
    test_parse_limit_semantics()
    test_project_ui_history_turn_error_item()
    test_ws_get_history_round_trip()
    test_ws_get_history_after_real_turn()
    print("\nUI 历史回放测试全部通过")
