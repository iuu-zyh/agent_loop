"""think 分流测试：split_think / ThinkSplitter（标签跨 token）/ stub reasoning 透传 / WS 集成"""
from __future__ import annotations

import asyncio

from agent_loop.llm.think import ThinkSplitter, split_think


def test_split_think_basic():
    r, b = split_think("<think>内心活动</think>正文开始")
    assert r == "内心活动" and b == "正文开始"
    print("✓ 成对标签拆分")


def test_split_think_none():
    r, b = split_think("纯正文，没有任何标签")
    assert r == "" and b == "纯正文，没有任何标签"
    print("✓ 无标签原样返回")


def test_split_think_multi_and_variants():
    text = "前言<think>想A</think>中段<thought>想B</thought>后记"
    r, b = split_think(text)
    assert r == "想A想B" and b == "前言中段后记"
    r2, b2 = split_think("<reasoning>R</reasoning>答案")
    assert r2 == "R" and b2 == "答案"
    print("✓ 多段 + think/thought/reasoning 变体")


def test_split_think_unclosed():
    r, b = split_think("<think>被截断的思考")
    assert r == "被截断的思考" and b == ""
    print("✓ 未闭合兜底")


def test_think_splitter_streaming_tag_across_tokens():
    """标签被切成多个 token（<th|i|nk|>）仍能正确分流。"""
    parts: list = []
    sp = ThinkSplitter()
    for tok in ["你好", "<th", "ink>", "悄悄", "想", "</th", "ink>", "：", "答案是8"]:
        sp.feed(tok, on_body=lambda s: parts.append(("body", s)), on_think=lambda s: parts.append(("think", s)))
    sp.flush(on_body=lambda s: parts.append(("body", s)), on_think=lambda s: parts.append(("think", s)))
    assert "".join(s for k, s in parts if k == "body") == "你好：答案是8"
    assert "".join(s for k, s in parts if k == "think") == "悄悄想"
    print("✓ 流式标签跨 token 分流")


def test_think_splitter_no_tags():
    parts: list = []
    sp = ThinkSplitter()
    for tok in ["普通", "回复"]:
        sp.feed(tok, on_body=lambda s: parts.append(("body", s)), on_think=lambda s: parts.append(("think", s)))
    sp.flush(on_body=lambda s: parts.append(("body", s)), on_think=lambda s: parts.append(("think", s)))
    assert "".join(s for k, s in parts if k == "body") == "普通回复"
    assert not any(k == "think" for k, _ in parts)
    print("✓ 无标签流全走 body")


def _run(coro):
    return asyncio.run(coro)


def test_stub_reasoning_passthrough():
    """stub 返回 reasoning → LlmResult.reasoning + on_reasoning 回调（不传不回调）。"""
    from agent_loop.llm.stub_client import StubLlmClient

    async def _scenario():
        seen_r, seen_t = [], []
        client = StubLlmClient(fn=lambda req: {"text": "答案", "reasoning": "推理中"})
        res = await client.generate("s", [], [], on_token=seen_t.append, on_reasoning=seen_r.append)
        return res, seen_r, seen_t

    res, seen_r, seen_t = _run(_scenario())
    assert res.reasoning == "推理中" and seen_r == ["推理中"] and seen_t == ["答案"]
    print("✓ stub reasoning 透传")


def test_ws_think_event_flow():
    """WS 集成：stub 带 reasoning → ChatHub 全链路 → step(think) + text_delta(part=think/body)。"""
    import asyncio
    from agent_loop.agent_loop import AgentLoop
    from agent_loop.bridge import StubGameBridge
    from agent_loop.compaction.compress import Compressor
    from agent_loop.llm.stub_client import StubLlmClient

    async def _scenario():
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            events: list = []

            class FakeWs:
                async def send_event(self, event, npc_id="", **fields):
                    events.append({"event": event, **fields})

                async def request(self, method, params=None, timeout=None):
                    return {"success": False}

            AgentLoop.reset_for_test(storage_root=d,
                                     bridge=StubGameBridge(),
                                     llm=StubLlmClient(fn=lambda req: {"text": "正文回复", "reasoning": "暗自盘算"}),
                                     compactor=Compressor(llm=StubLlmClient()))
            loop = AgentLoop()
            hub_events = events
            from agent_loop.ws_channel import ChatHub
            hub = ChatHub(loop, FakeWs(), config_service=None, prompt_service=None)
            hub.ws = FakeWs()
            # 直接驱动 handle_message（FakeWs 不建真连接）
            await hub.handle_message("测试侠", "你好")

            kinds = [e["event"] for e in hub_events]
            assert "step" in kinds and "text_delta" in kinds and "npc_reply" in kinds
            think_delta = [e for e in hub_events if e["event"] == "text_delta" and e.get("part") == "think"]
            body_delta = [e for e in hub_events if e["event"] == "text_delta" and e.get("part") == "body"]
            think_step = [e for e in hub_events if e["event"] == "step" and e.get("kind") == "think"]
            assert think_delta and think_delta[0]["text"] == "暗自盘算"
            assert body_delta and body_delta[-1]["text"] == "正文回复"
            assert think_step and think_step[0]["text"] == "暗自盘算"
            reply = [e for e in hub_events if e["event"] == "npc_reply"][0]
            assert reply["text"] == "正文回复", "收尾帧只给正文，不含思考"
            return True

    assert asyncio.run(_scenario())
    print("✓ WS 集成：step(think) + text_delta(part) + npc_reply 只含正文")


def test_history_think_projection():
    """历史投影：assistant 带 reasoning → 拆成 kind=think + kind=assistant 两项。"""
    import tempfile
    from agent_loop.history import project_ui_history
    from agent_loop.persistence import save_session, load_session
    from agent_loop.session import Session
    from agent_loop import llm_adapter

    with tempfile.TemporaryDirectory() as d:
        s = Session(id="思考", header={"id": "思考"})
        s.append("turn/start", {"turn": 1})
        s.append("assistant/message", {
            "message": llm_adapter.create_canonical_assistant_message("正文", []),
            "reasoning": "思考内容",
        }, {"surfaceOp": "append"})
        s.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
        save_session(s, d)
        s2 = load_session("思考", d)
        out = project_ui_history(s2, max_turns=10)
        kinds = [i["kind"] for i in out["items"]]
        assert kinds == ["think", "assistant"], out["items"]
        assert out["items"][0]["text"] == "思考内容"
        assert out["items"][1]["text"] == "正文"
    print("✓ 历史投影：think 独立项")


def test_history_embedded_tag_projection():
    """历史投影：content 内嵌 <think>（旧账本无 reasoning 字段）→ 兜底拆分。"""
    from agent_loop.history import project_ui_history
    from agent_loop.session import Session
    from agent_loop import llm_adapter

    s = Session(id="标签", header={"id": "标签"})
    s.append("turn/start", {"turn": 1})
    s.append("assistant/message", {
        "message": llm_adapter.create_canonical_assistant_message("<think>旧思考</think>新正文", []),
    }, {"surfaceOp": "append"})
    s.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    out = project_ui_history(s, max_turns=10)
    assert [i["kind"] for i in out["items"]] == ["think", "assistant"]
    assert out["items"][0]["text"] == "旧思考" and out["items"][1]["text"] == "新正文"
    print("✓ 历史投影：内嵌标签兜底拆分")


if __name__ == "__main__":
    test_split_think_basic()
    test_split_think_none()
    test_split_think_multi_and_variants()
    test_split_think_unclosed()
    test_think_splitter_streaming_tag_across_tokens()
    test_think_splitter_no_tags()
    test_stub_reasoning_passthrough()
    test_ws_think_event_flow()
    test_history_think_projection()
    test_history_embedded_tag_projection()
    print("\nthink 测试全部通过")
