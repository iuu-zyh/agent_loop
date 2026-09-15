"""LlmRouter 测试：委托转发 / swap 热换 / 旧芯收尾 / on_token 透传 / None 拒绝"""
from __future__ import annotations

import asyncio

import pytest

from agent_loop.llm.base import LlmResult
from agent_loop.llm.router import LlmRouter


class _StubClient:
    """记录调用的桩内芯：text 可定制，可选延迟模拟进行中的调用。"""

    def __init__(self, name: str, text: str = "", delay: float = 0.0):
        self.name = name
        self.text = text
        self.delay = delay
        self.calls: list = []

    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None) -> LlmResult:
        self.calls.append((system, messages, tools))
        if self.delay:
            await asyncio.sleep(self.delay)
        if on_token is not None and self.text:
            await on_token(self.text)
        return LlmResult(text=self.text, provider=self.name, model=self.name)


def test_router_delegates_to_current_client():
    """generate 委托当前内芯：参数原样透传，LlmResult 原样返回。"""
    a = _StubClient("a", text="hi")
    router = LlmRouter(a)
    res = asyncio.run(router.generate("sys", [{"role": "user", "content": "x"}], []))
    assert res.text == "hi" and res.provider == "a"
    assert a.calls and a.calls[0][0] == "sys"
    print("✓ generate 委托当前内芯")


def test_swap_routes_new_calls_to_new_client():
    """swap 后新 generate 走新芯，旧芯不再被调用。"""
    a, b = _StubClient("a"), _StubClient("b", text="new")
    router = LlmRouter(a)
    old = router.swap(b)
    assert old is a, "swap 返回旧芯"
    res = asyncio.run(router.generate("s", [], []))
    assert res.provider == "b" and res.text == "new"
    assert not a.calls
    print("✓ swap 后新调用走新芯")


def test_swap_inflight_call_finishes_on_old_client():
    """进行中的 generate 持旧芯引用收尾：swap 不撕裂进行中的调用。"""

    async def _scenario():
        slow = _StubClient("slow", text="from-old", delay=0.05)
        fast = _StubClient("fast", text="from-new")
        router = LlmRouter(slow)
        task = asyncio.create_task(router.generate("s", [], []))
        await asyncio.sleep(0.01)          # 让旧芯调用先行启动
        router.swap(fast)                  # 调用进行中换芯
        return await task

    res = asyncio.run(_scenario())
    assert res.text == "from-old", "进行中的调用应持旧芯收尾"
    print("✓ swap 不撕裂进行中的调用")


def test_router_forwards_on_token():
    """on_token 透传内芯（流式逐 token 回调；协议要求 awaitable）。"""
    toks: list = []

    async def _collect(tok):
        toks.append(tok)

    router = LlmRouter(_StubClient("a", text="流式文本"))
    asyncio.run(router.generate("s", [], [], on_token=_collect))
    assert toks == ["流式文本"]
    print("✓ on_token 透传")


def test_swap_rejects_none():
    router = LlmRouter(_StubClient("a"))
    with pytest.raises(ValueError):
        router.swap(None)
    with pytest.raises(ValueError):
        LlmRouter(None)
    print("✓ swap/构造拒绝 None")


if __name__ == "__main__":
    test_router_delegates_to_current_client()
    test_swap_routes_new_calls_to_new_client()
    test_swap_inflight_call_finishes_on_old_client()
    test_router_forwards_on_token()
    test_swap_rejects_none()
    print("\nLlmRouter 测试全部通过")
