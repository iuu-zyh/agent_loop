"""LLM 重试测试：OpenAILlmClient.generate 的可靠性策略

覆盖：
  1. 可重试错误 → 重试后成功（总调用 retries+1 次）
  2. 不可重试错误（HTTP 400）→ 立即抛，不重试
  3. retries=0 → 只调一次
  4. 重试耗尽 → 抛最后一个错误
  5. 流式零产出失败 → 可重试
  6. 流式已产出 token 后失败 → 不重试（防重复展示）
  7. 【09-14 线上回归】httpx/openai 传输层异常 → 必须判为可重试
  8. 单次上限 timeout：挂住的那一趟被掐掉 → 照样重试
  9. 总预算 total_budget：挂钟硬顶，重试次数没耗尽也照样放弃
 10. _drain 的 finally 必须 close 掉底层流（防被放弃的线程泄漏连接）
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from agent_loop.llm.openai_client import OpenAILlmClient
from agent_loop.llm.base import LlmResult

OK_RESP = {"choices": [{"message": {"content": "你好，道友。", "tool_calls": None}}]}


class _OkOnce:
    """callable client：前 fail_before_success 次抛网络错，之后成功（走 _call 的可调用对象分支）。"""

    def __init__(self, fail_before_success: int = 0):
        self.calls = 0
        self.fail_before_success = fail_before_success

    def __call__(self, oai_messages, oai_tools):
        self.calls += 1
        if self.calls <= self.fail_before_success:
            raise ConnectionError("连接失败")
        return OK_RESP


class _AlwaysFail:
    def __init__(self, err: Exception):
        self.err = err
        self.calls = 0

    def __call__(self, oai_messages, oai_tools):
        self.calls += 1
        raise self.err


class _FailThenOk:
    """前 fail_times 次抛 err_factory() 造的新异常，之后成功。

    用工厂而非实例：异常对象可能被复用，而真实网关每次都是新对象（且 .request/.response
    这类属性与单次请求绑定，复用会掩盖问题）。
    """

    def __init__(self, err_factory, fail_times: int = 1):
        self.err_factory = err_factory
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, oai_messages, oai_tools):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.err_factory()
        return OK_RESP


def _client(callable_obj, retries: int = 1) -> OpenAILlmClient:
    return OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=callable_obj, retries=retries)


def test_retry_after_connection_error_succeeds():
    c = _client(_OkOnce(fail_before_success=1))
    result = asyncio.run(c.generate("sys", [], []))
    assert isinstance(result, LlmResult) and "你好" in result.text
    assert c._client.calls == 2, f"应失败1次+重试1次，实际 {c._client.calls}"
    print("✓ 可重试错误 → 重试后成功（共 2 次调用）")


def test_non_retryable_error_raises_immediately():
    err = RuntimeError("bad request")
    err.status_code = 400
    c = _client(_AlwaysFail(err))
    with pytest.raises(RuntimeError):
        asyncio.run(c.generate("sys", [], []))
    assert c._client.calls == 1
    print("✓ 不可重试错误（400）→ 立即抛，不重试")


def test_retries_zero_calls_once():
    c = _client(_AlwaysFail(ConnectionError("x")), retries=0)
    with pytest.raises(ConnectionError):
        asyncio.run(c.generate("sys", [], []))
    assert c._client.calls == 1
    print("✓ retries=0 → 只调 1 次")


def test_retry_exhausted_raises_last():
    c = _client(_AlwaysFail(ConnectionError("boom")), retries=2)
    with pytest.raises(ConnectionError):
        asyncio.run(c.generate("sys", [], []))
    assert c._client.calls == 3
    print("✓ 重试耗尽 → 共 3 次调用，抛最后一个错误")


def test_stream_zero_output_retries():
    """流式（on_token 提供）但开流后零产出失败 → 可重试。"""
    c = _client(_OkOnce(fail_before_success=1))
    toks: list = []

    async def on_token(t):
        toks.append(t)

    result = asyncio.run(c.generate("sys", [], [], on_token=on_token))
    assert "你好" in result.text
    assert c._client.calls == 2
    print("✓ 流式零产出失败 → 可重试（2 次调用）")


# ---------- 流式已产出 token 后失败：不重试 ----------
class _StreamOnceThenFail:
    """client.chat.completions.create(stream=True) → 先 yield 一个文本块，再抛网络错。"""

    def __init__(self):
        self.create_calls = 0

    @property
    def chat(self):
        return SimpleNamespace(completions=_StreamCompletions(self))

    def _new_stream(self):
        self.create_calls += 1

        def gen():
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="半", tool_calls=None))]
            )
            raise ConnectionError("流中断")

        return gen()


class _StreamCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kw):
        if kw.get("stream"):
            return self._owner._new_stream()
        return OK_RESP


def test_stream_partial_output_does_not_retry():
    """已推送过 token 的流式中断 → 不重试（重打会让玩家看到两遍），直接抛。"""
    owner = _StreamOnceThenFail()
    c = OpenAILlmClient(
        base_url="http://x/v1", api_key="k", model="m",
        client=SimpleNamespace(chat=SimpleNamespace(completions=_StreamCompletions(owner))),
        retries=2,
    )
    toks: list = []

    async def on_token(t):
        toks.append(t)

    with pytest.raises(ConnectionError):
        asyncio.run(c.generate("sys", [], [], on_token=on_token))
    assert toks == ["半"], "应已对外推送过 token"
    assert owner.create_calls == 1, f"已推送过 token 不应重试，实际 create 调用 {owner.create_calls} 次"
    print("✓ 流式已产出 token → 不重试（直接抛）")


# ---------- 7. 【线上回归】传输层异常的判定 ----------
def test_httpx_transport_errors_are_retryable():
    """httpx 的传输层异常**不继承** OSError 家族，只枚举内建会整族漏判。

    线上实况：`RemoteProtocolError`（上游流到一半掐断）被判"不可重试" → 0 次重试直接放弃。
    实测其 MRO 为 RemoteProtocolError → ProtocolError → TransportError → RequestError
    → HTTPError → Exception，与 ConnectionError/TimeoutError/OSError 全不相交。
    """
    import httpx

    cases = {
        "RemoteProtocolError": httpx.RemoteProtocolError("peer closed connection (incomplete chunked read)"),
        "ReadTimeout": httpx.ReadTimeout("read timed out"),
        "ConnectTimeout": httpx.ConnectTimeout("connect timed out"),
        "ConnectError": httpx.ConnectError("refused"),
        "ReadError": httpx.ReadError("reset"),
        "WriteError": httpx.WriteError("broken pipe"),
        "PoolTimeout": httpx.PoolTimeout("no connection"),
    }
    for name, err in cases.items():
        assert OpenAILlmClient._is_retryable(err), f"{name} 应判为可重试"
    print(f"✓ httpx 传输层异常全部可重试（{len(cases)} 种）")


def test_openai_api_timeout_is_retryable():
    """openai.APITimeoutError 继承的是 APIConnectionError，不是内建 TimeoutError。"""
    import httpx
    import openai

    req = httpx.Request("POST", "http://x/v1/chat/completions")
    assert OpenAILlmClient._is_retryable(openai.APITimeoutError(request=req))
    assert OpenAILlmClient._is_retryable(openai.APIConnectionError(request=req))
    print("✓ openai APITimeoutError / APIConnectionError → 可重试")


def test_openai_bad_request_still_not_retryable():
    """扩判据不能把 4xx 也放进来：400（如 MissingSessionID）必须仍然立即抛。"""
    import httpx
    import openai

    req = httpx.Request("POST", "http://x/v1/chat/completions")
    err = openai.BadRequestError("MissingSessionID", response=httpx.Response(400, request=req), body=None)
    assert not OpenAILlmClient._is_retryable(err)
    print("✓ openai 400 → 仍不可重试（扩判据未误伤）")


def test_remote_protocol_error_retries_end_to_end():
    """端到端回归：上游掐断一次 → 重试一次 → 成功（而不是 0 次放弃）。"""
    import httpx

    c = _client(_FailThenOk(lambda: httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body (incomplete chunked read)")))
    result = asyncio.run(c.generate("sys", [], []))
    assert isinstance(result, LlmResult) and "你好" in result.text
    assert c._client.calls == 2, f"应失败1次+重试1次，实际 {c._client.calls}"
    print("✓ RemoteProtocolError → 重试后成功（线上事故不再复现）")


# ---------- 8/9. 两道时间闸 ----------
class _HangUntilReleased:
    """调用即阻塞，直到 event 被 set —— 模拟"上游收下请求后不吐任何数据"。"""

    def __init__(self, ev: threading.Event):
        self.ev = ev
        self.calls = 0

    def __call__(self, oai_messages, oai_tools):
        self.calls += 1
        self.ev.wait(timeout=10)
        return OK_RESP


def test_per_attempt_timeout_kills_hang_and_retries():
    """单次上限到点 → 掐掉这一趟（零 token）→ 允许重试 → 第二趟成功。"""
    ev = threading.Event()
    calls = {"n": 0}

    def callable_obj(m, t):
        calls["n"] += 1
        if calls["n"] == 1:
            ev.wait(timeout=5)           # 第一趟挂住，等被 deadline 掐
        return OK_RESP

    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=callable_obj, retries=1, timeout=0.2, total_budget=None)
    # 手动驱动循环，不用 asyncio.run：后者收尾会 shutdown_default_executor，
    #   那一步 join 被放弃的线程 → 测试要多等一个"泄漏线程的寿命"。生产不受影响
    #   （ws_channel 是长生命周期循环，不会每回合关停 executor）。
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(c.generate("sys", [], []))
    finally:
        ev.set()                         # 放掉被放弃的那条线程
        loop.close()
    assert "你好" in result.text
    assert calls["n"] == 2, f"应掐掉1趟+重试1趟，实际 {calls['n']}"
    print("✓ 单次上限掐掉挂住的那一趟 → 重试成功")


def test_total_budget_hard_cap_beats_remaining_retries():
    """总预算耗尽 → 即使 retries 还没用完也放弃；总耗时被预算封顶。

    这条是"等待时间减小"的最终保证：光限次数不限时长的话，次数×单次上限还是能拖很久。
    走**流式**路径测 —— 预算只约束流式（= 玩家在等的那条），见 _remaining_budget 注释。
    场景刻意复刻 09-14 事故：流开起来了，但首 token 永远不到。
    """
    ev = threading.Event()
    owner = _StreamHangOwner(_HangingStream(ev))
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=owner, retries=5,            # 次数给足
                        timeout=10.0, total_budget=0.3)     # 预算只有 0.3s

    async def on_token(t):
        pass

    loop = asyncio.new_event_loop()
    t0 = time.monotonic()
    raised = None
    try:
        loop.run_until_complete(c.generate("sys", [], [], on_token=on_token))
    except TimeoutError as e:
        raised = e
    finally:
        elapsed = time.monotonic() - t0   # 只量到"放弃"那一刻（见上一条注释）
        ev.set()
        loop.close()
    assert raised is not None, "预算耗尽应抛 TimeoutError"
    assert elapsed < 2.0, f"总预算 0.3s，实际耗时 {elapsed:.2f}s（硬顶失效）"
    assert owner.create_calls == 1, f"预算已耗尽不应再发起尝试，实际 {owner.create_calls} 次"
    print(f"✓ 总预算硬顶生效：retries=5 但 {elapsed:.2f}s 就放弃（只发起 1 次）")


def test_budget_does_not_constrain_nonstream_path():
    """预算只约束流式：压缩（非流式）不能被玩家的等待预算掐死，否则大摘要永远做不完。"""
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None,
                        timeout=45.0, timeout_nonstream=180.0, total_budget=100.0)
    assert c._remaining_budget(time.monotonic(), streaming=False) is None, "非流式无预算"
    assert c._remaining_budget(time.monotonic(), streaming=True) is not None
    # 非流式 deadline 取 timeout_nonstream(180)，不被 budget(100) 压成 100
    assert c._attempt_deadline(None, streaming=False) == 180.0
    assert c._attempt_deadline(None, streaming=True) == 45.0, "流式用 timeout"
    print("✓ 总预算只约束流式；非流式由 timeout_nonstream 封顶")


def test_timeout_nonstream_falls_back_to_timeout():
    """只设了 timeout 的用户，两条路径都仍有上界（不会静默退回 SDK 的 600s）。"""
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None, timeout=7.0)
    assert c.timeout_nonstream == 7.0
    print("✓ timeout_nonstream 缺省回落 timeout")


def test_attempt_deadline_shrinks_to_remaining_budget():
    """单次上限被剩余预算收缩：min(timeout, remaining)，保证不会在最后一趟冲破预算。"""
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None,
                        timeout=45.0, total_budget=100.0)
    assert c._attempt_deadline(None) == 45.0, "无预算信息 → 用单次上限"
    assert c._attempt_deadline(10.0) == 10.0, "剩余预算更小 → 收缩到 10"
    assert c._attempt_deadline(999.0) == 45.0, "剩余预算更大 → 仍受单次上限约束"
    c2 = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None,
                         timeout=None, total_budget=None)
    assert c2._attempt_deadline(None) is None, "两者都缺省 → 不设顶（回到旧行为）"
    print("✓ 单次上限与剩余预算取 min")


# ---------- 10. 被放弃的线程必须自己收尾 ----------
class _CloseTrackingStream:
    """可迭代 + 可 close 的假流；记录 close 是否被调用。"""

    def __init__(self, chunks, err=None):
        self.closed = False
        self._chunks = chunks
        self._err = err

    def __iter__(self):
        for c in self._chunks:
            yield c
        if self._err is not None:
            raise self._err

    def close(self):
        self.closed = True


class _StreamOwner:
    def __init__(self, stream):
        self.stream = stream
        self.create_calls = 0

    @property
    def chat(self):
        return SimpleNamespace(completions=self)

    def create(self, **kw):
        self.create_calls += 1
        return self.stream if kw.get("stream") else OK_RESP


class _HangingStream:
    """可迭代但**永不产出任何块**的假流 —— 复刻 09-14 事故：流开起来了，首 token 永不到。"""

    def __init__(self, ev: threading.Event):
        self.ev = ev
        self.closed = False

    def __iter__(self):
        self.ev.wait(timeout=5)
        return iter(())

    def close(self):
        self.closed = True


class _StreamHangOwner:
    def __init__(self, stream):
        self.stream = stream
        self.create_calls = 0

    @property
    def chat(self):
        return SimpleNamespace(completions=self)

    def create(self, **kw):
        self.create_calls += 1
        return self.stream if kw.get("stream") else OK_RESP


def test_drain_closes_stream_on_error():
    """流中断后必须 close 底层响应：被"总预算硬顶"放弃的线程还在跑，不 close 会泄漏连接。"""
    stream = _CloseTrackingStream(
        [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="半", tool_calls=None))])],
        err=ConnectionError("流中断"),
    )
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=_StreamOwner(stream), retries=2)

    async def on_token(t):
        pass

    with pytest.raises(ConnectionError):
        asyncio.run(c.generate("sys", [], [], on_token=on_token))
    assert stream.closed, "_drain 异常退出后没有 close 流"
    print("✓ 流异常退出 → finally 已 close")


def test_drain_closes_stream_on_success():
    stream = _CloseTrackingStream(
        [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你好", tool_calls=None))])]
    )
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=_StreamOwner(stream), retries=0)

    async def on_token(t):
        pass

    result = asyncio.run(c.generate("sys", [], [], on_token=on_token))
    assert "你好" in result.text and stream.closed
    print("✓ 流正常收尾 → finally 已 close")


if __name__ == "__main__":
    test_retry_after_connection_error_succeeds()
    test_non_retryable_error_raises_immediately()
    test_retries_zero_calls_once()
    test_retry_exhausted_raises_last()
    test_stream_zero_output_retries()
    test_stream_partial_output_does_not_retry()
    test_httpx_transport_errors_are_retryable()
    test_openai_api_timeout_is_retryable()
    test_openai_bad_request_still_not_retryable()
    test_remote_protocol_error_retries_end_to_end()
    test_per_attempt_timeout_kills_hang_and_retries()
    test_total_budget_hard_cap_beats_remaining_retries()
    test_budget_does_not_constrain_nonstream_path()
    test_timeout_nonstream_falls_back_to_timeout()
    test_attempt_deadline_shrinks_to_remaining_budget()
    test_drain_closes_stream_on_error()
    test_drain_closes_stream_on_success()
    print("\nLLM 重试测试全部通过")