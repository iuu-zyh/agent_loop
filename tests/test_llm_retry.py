"""LLM 重试测试：OpenAILlmClient.generate 的可靠性策略

覆盖：
  1. 可重试错误 → 重试后成功（总调用 retries+1 次）
  2. 不可重试错误（HTTP 400）→ 立即抛，不重试
  3. retries=0 → 只调一次
  4. 重试耗尽 → 抛最后一个错误
  5. 流式零产出失败 → 可重试
  6. 流式已产出 token 后失败 → 不重试（防重复展示）
  7. 【09-14 线上回归】httpx/openai 传输层异常 → 必须判为可重试
  8. 首字看门狗：开流后 N 秒没有任何内容 → 可重试的 TimeoutError
  9. 停顿看门狗：吐字中途 N 秒没有新内容 → TimeoutError，且不重试（已推送过）
 10. 【09-17 线上回归】慢速长流：一直有内容 → **总耗时再久也不许判超时**
 11. 可选绝对上限 total_budget：**默认关**；显式设了才收缩看门狗（非流式不受它约束）
 12. _drain 的 finally 必须 close 掉底层流（防被放弃的线程泄漏连接）
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


# ---------- 8/9/10/11. 两道存活看门狗（＋可选绝对上限）----------
# 语义（09-18 重做，见 docs/reference/brain-liveness.md §4）：
#   首字看门狗 `timeout`       ：请求发出 → 第一个内容块
#   停顿看门狗 `idle_timeout`  ：相邻两个内容块之间（缺省回落 timeout）
#   两者都只问"多久没有新内容"，**不存在**"整趟已经跑了多久"这种判据。
#   `total_budget` 默认关，只在显式设置后收缩上面两个看门狗。
def _run_no_executor_join(coro_factory):
    """跑一个协程，返回 (result, err)；**不关停默认 executor**。

    本文件有几条用例会刻意留下"被看门狗放弃的读线程"——那本身就是生产语义的一部分
    （Python 杀不掉线程，只能 close + 背板超时兜底）。`asyncio.run` 收尾会
    shutdown_default_executor 去 join 它们，测试于是要多等一个"泄漏线程的寿命"。
    生产不受影响：ws_channel 是长生命周期循环，不会每回合关停 executor。
    """
    loop = asyncio.new_event_loop()
    err: Optional[BaseException] = None
    result = None
    try:
        result = loop.run_until_complete(coro_factory())
    except BaseException as e:      # noqa: BLE001 —— 断言在调用方做
        err = e
    finally:
        loop.close()                # close 用 shutdown(wait=False)，不 join 线程
    return result, err


class _TimedStream:
    """按脚本吐块的假流：script 项 = `(延迟秒, 文本或 None)` 或 `("gate", 文本或 None)`。

    · 延迟项发生在 `_drain` 线程里 —— "读线程阻塞在 socket 上"的等价物；
    · `("gate", text)` = 阻塞在 `gate` 上直到测试放行，再吐 text。**没有真实睡眠**，
      所以"卡住"这一类判据不靠竞速：看门狗必在闸门放行之前到点（慢机器上也不会抖）。
      放行由测试负责（`finally` 里 `gate.set()`，或用 `threading.Timer` 定时放行）。
    · 文本 None = 这一块不含内容（只推进时间）。
    """

    def __init__(self, script, err=None, gate=None):
        self.script = list(script)
        self.err = err
        self.gate = gate
        self.closed = False

    def __iter__(self):
        for step, text in self.script:
            if step == "gate":
                if self.gate is not None:
                    self.gate.wait(timeout=15)
            elif step:
                time.sleep(step)
            if text is None:
                continue
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))]
            )
        if self.err is not None:
            raise self.err

    def close(self):
        self.closed = True


class _StreamScriptOwner:
    """client.chat.completions.create(stream=True) → 第 N 次调用返回 factory(N) 造的流。"""

    def __init__(self, factory):
        self.factory = factory
        self.create_calls = 0

    @property
    def chat(self):
        return SimpleNamespace(completions=self)

    def create(self, **kw):
        if not kw.get("stream"):
            return OK_RESP
        self.create_calls += 1
        return self.factory(self.create_calls)


def test_slow_stream_is_never_killed_by_elapsed_time():
    """★ 09-17 线上回归 ★ 慢速长流：总耗时远超 timeout，但一直有内容 → 必须成功。

    旧实现把 timeout 当"整趟尝试的挂钟"（`asyncio.wait_for` 包住整个 _call_stream，
    收到数据块**不重置**），于是"吐字很顺、总时长超了"的长回复照样被砍断：实机里姜萌的
    思考流就是这样在第 45 秒被砍、群聊该成员本轮整段丢失的。
    现在只有存活看门狗 —— 只要还有新内容，跑多久都不算超时。
    """
    # 每块间隔 0.02s（看门狗的 1/50）、总耗时 1.6s（看门狗的 1.6 倍）：
    #   旧的"整趟挂钟"必然在 1.0s 处砍断它（与机器快慢无关）；
    #   新的存活看门狗要误判，得出现一次 >1s 的块间停顿（= 50 倍负载抖动）。
    owner = _StreamScriptOwner(lambda _i: _TimedStream([(0.02, "字")] * 80))
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=owner, retries=0, timeout=1.0, idle_timeout=1.0)
    toks: list = []

    async def on_token(t):
        toks.append(t)

    t0 = time.monotonic()
    result, err = _run_no_executor_join(lambda: c.generate("sys", [], [], on_token=on_token))
    elapsed = time.monotonic() - t0
    assert err is None, f"慢速长流不该被判超时：{err!r}"
    assert result.text == "字" * 80 and len(toks) == 80
    assert elapsed > 1.0, f"本用例必须真的跑得比 timeout(1.0s) 久，实际 {elapsed:.2f}s"
    print(f"✓ 慢速长流不被整趟耗时砍断（{elapsed:.2f}s > timeout 1.0s，80 块全部收到）")


def test_first_token_watchdog_times_out_then_retries():
    """首字看门狗：开流后 timeout 秒内一个内容块都不到 → TimeoutError（可重试）→ 第二次成功。

    第一趟的流**闸门永不放行**（不是"睡 1.2 秒"）：看门狗必到点，与机器快慢无关。
    """
    gate = threading.Event()
    owner = _StreamScriptOwner(
        lambda i: _TimedStream([("gate", "迟到")], gate=gate) if i == 1
        else _TimedStream([(0.0, "你好")]))
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=owner, retries=1, timeout=0.3, idle_timeout=0.3)
    toks: list = []

    async def on_token(t):
        toks.append(t)

    try:
        result, err = _run_no_executor_join(lambda: c.generate("sys", [], [], on_token=on_token))
        assert err is None, f"第二趟应当成功：{err!r}"
        assert "你好" in result.text
        assert owner.create_calls == 2, f"零产出超时应当重试，实际 create {owner.create_calls} 次"
        assert toks == ["你好"], "第一趟零产出，不该有任何 token 流出去"
    finally:
        gate.set()                  # 放掉第一趟那条被放弃的读线程
    print("✓ 首字看门狗到点 → 重试 → 第二趟成功（零产出重试没有被误伤）")


def test_midstream_stall_raises_stall_timeout_and_closes_stream():
    """停顿看门狗：吐了一块之后卡住 → "停顿超时"，且已推送过 token 故不重试。

    顺带钉住放弃时的 close：读线程此刻还阻塞在 socket 上（闸门未放行），主协程 close 是
    唯一能立刻叫醒它的手段（否则要等 `_httpx_read_timeout()` 那道背板）。断言紧跟 raise
    之后、闸门尚未放行，所以 closed=True 只可能来自看门狗那条路径。
    """
    gate = threading.Event()
    stream = _TimedStream([(0.0, "半"), ("gate", "后半")], gate=gate)
    owner = _StreamScriptOwner(lambda _i: stream)
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=owner, retries=2, timeout=0.3, idle_timeout=0.3)
    toks: list = []

    async def on_token(t):
        toks.append(t)

    try:
        result, err = _run_no_executor_join(lambda: c.generate("sys", [], [], on_token=on_token))
        assert isinstance(err, TimeoutError), f"应抛 TimeoutError，实际 {err!r}"
        assert "停顿" in str(err), f"文案必须点名是「停顿」（首字与中途是两种体感）：{err}"
        assert "没有任何新内容" in str(err)
        assert toks == ["半"], "已推送的半个字保留（不外泄重发）"
        assert owner.create_calls == 1, f"已推送过 token 不应重试，实际 {owner.create_calls} 次"
        assert stream.closed, "看门狗放弃时必须 close 掉流，否则读线程要等背板超时才退出"
    finally:
        gate.set()
    print("✓ 停顿看门狗到点 → 停顿超时、不重试、已 close 流")


def test_stall_phase_uses_idle_not_first_token_watchdog():
    """阶段切换（正向）：首块到达后判据换成 idle_timeout —— 更长的间隔不算停顿。

    间隔取 1.0s：> timeout(0.3s) 但 < idle_timeout(5.0s)。判据用错（仍用首字那个值）
    就会在 0.3s 处报错；判据正确则只在闸门放行后继续，两边的余量都是 3 倍以上。
    """
    gate = threading.Event()
    owner = _StreamScriptOwner(lambda _i: _TimedStream([(0.0, "甲"), ("gate", "乙")], gate=gate))
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=owner, retries=0, timeout=0.3, idle_timeout=5.0)

    async def on_token(t):
        pass

    timer = threading.Timer(1.0, gate.set)      # 放行第二块：晚于 timeout，早于 idle_timeout
    timer.start()
    try:
        result, err = _run_no_executor_join(lambda: c.generate("sys", [], [], on_token=on_token))
    finally:
        timer.cancel()
        gate.set()
    assert err is None, f"首块之后的 1.0s 间隔该归 idle_timeout(5.0s) 管：{err!r}"
    assert result.text == "甲乙"
    print("✓ 首块之后判据切换为 idle_timeout")


def test_first_token_phase_ignores_idle_watchdog():
    """阶段切换（反向）：首块之前的等待只认 timeout，不能被更宽松的 idle_timeout 续命。

    首块 1.0s 后才到（闸门放行）：timeout=0.3 必须已经判超时。若误用 idle_timeout(5.0) 则会
    一直等下去 —— 那时候第一块已经在路上，测试会看到"成功"，断言即红。
    """
    gate = threading.Event()
    owner = _StreamScriptOwner(lambda _i: _TimedStream([("gate", "甲")], gate=gate))
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=owner, retries=0, timeout=0.3, idle_timeout=5.0)

    async def on_token(t):
        pass

    timer = threading.Timer(1.0, gate.set)
    timer.start()
    try:
        _result, err = _run_no_executor_join(lambda: c.generate("sys", [], [], on_token=on_token))
    finally:
        timer.cancel()
        gate.set()
    assert isinstance(err, TimeoutError), f"首字阶段应受 timeout(0.3s) 约束：{err!r}"
    assert "首字" in str(err), f"文案必须点名首字阶段：{err}"
    print("✓ 首字阶段只认 timeout（不被 idle_timeout 续命）")


def test_stream_open_hang_hits_first_token_watchdog():
    """连响应头都不到（create 本身阻塞）→ 首字看门狗照样兜住。

    这条路径以前被"整趟挂钟"顺带覆盖着；挂钟删掉后必须由开流那一步自己的看门狗接住
    （`_open_stream` 走 to_thread + 看门狗），否则"卡在建连/等响应头"会变成无限等。
    """
    ev = threading.Event()

    class _BlockingCreateOwner:
        def __init__(self):
            self.create_calls = 0

        @property
        def chat(self):
            return SimpleNamespace(completions=self)

        def create(self, **kw):
            self.create_calls += 1
            ev.wait(timeout=5.0)
            return _TimedStream([(0.0, "迟到的字")])

    owner = _BlockingCreateOwner()
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=owner, retries=0, timeout=0.25, idle_timeout=0.25)

    async def on_token(t):
        pass

    _result, err = _run_no_executor_join(lambda: c.generate("sys", [], [], on_token=on_token))
    try:
        assert isinstance(err, TimeoutError), f"应抛 TimeoutError，实际 {err!r}"
        assert "首字" in str(err), f"文案必须点名是首字阶段：{err}"
        assert owner.create_calls == 1
    finally:
        ev.set()                    # 放掉被放弃的那条线程
    print("✓ 开流自身阻塞（响应头不到）→ 首字看门狗兜住")


def test_watchdog_defaults_and_http_backstop():
    """停顿看门狗缺省回落 timeout；httpx 背板 = 两者之和（保证看门狗先响、文案不退化）。"""
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None, timeout=120.0)
    assert c.idle_timeout == 120.0, "idle_timeout 缺省应回落 timeout"
    assert c._httpx_read_timeout() == 240.0, "背板应宽于看门狗"
    c2 = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None,
                         timeout=120.0, idle_timeout=60.0)
    assert (c2.timeout, c2.idle_timeout) == (120.0, 60.0), "两个看门狗必须各自独立"
    assert c2._httpx_read_timeout() == 180.0
    c3 = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None)
    assert c3._watchdog_seconds(c3.timeout) is None, "0/None = 不设看门狗"
    assert c3._httpx_read_timeout() is None, "不设看门狗时不下发背板（落 SDK 默认）"
    print("✓ 看门狗缺省回落与背板取值正确")


def test_opt_in_budget_clamps_watchdogs_but_is_off_by_default():
    """total_budget **默认关**；显式设了才把看门狗收缩进剩余额度，且不作用于非流式。"""
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None,
                        timeout=120.0, idle_timeout=60.0)
    assert c._budget_deadline(time.monotonic(), streaming=True) is None, "默认不设绝对上限"
    assert c._watchdog_seconds(c.timeout, None) == 120.0, "没设上限时看门狗不受任何收缩"
    c2 = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None,
                         timeout=120.0, idle_timeout=60.0, total_budget=5.0)
    bd = c2._budget_deadline(time.monotonic(), streaming=True)
    assert bd is not None
    left = c2._watchdog_seconds(c2.timeout, bd)
    assert 0 < left <= 5.0, f"设了上限时看门狗应被收缩进剩余额度，实际 {left}"
    assert c2._budget_deadline(time.monotonic(), streaming=False) is None, "上限不约束非流式（压缩）"
    c3 = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None,
                         timeout=120.0, total_budget=0)
    assert c3._budget_deadline(time.monotonic(), streaming=True) is None, "0 与 null 同义 = 不设顶"
    print("✓ 绝对上限默认关；显式设置时只做「收缩看门狗」这一件事")


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
    """非流式（压缩路径）首字看门狗到点 → 掐掉这一趟（零 token）→ 允许重试 → 第二趟成功。"""
    ev = threading.Event()
    calls = {"n": 0}

    def callable_obj(m, t):
        calls["n"] += 1
        if calls["n"] == 1:
            ev.wait(timeout=5)           # 第一趟挂住，等被看门狗掐
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
    print("✓ 首字看门狗掐掉挂住的那一趟 → 重试成功")


def test_total_budget_hard_cap_beats_remaining_retries():
    """显式设了绝对上限 → 即使 retries 还没用完也放弃；总耗时被上限封顶。

    这条是**可选**上限（默认关）的保证：用户明确要"最坏等待 ≤ N 秒"时，靠收缩看门狗
    把总耗时压进 N。走**流式**路径测 —— 上限只约束流式（= 玩家在等的那条）。
    场景刻意复刻 09-14 事故：流开起来了，但首 token 永远不到。
    """
    ev = threading.Event()
    owner = _StreamHangOwner(_HangingStream(ev))
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                        client=owner, retries=5,            # 次数给足
                        timeout=10.0, total_budget=0.3)     # 上限只有 0.3s

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
    assert raised is not None, "上限耗尽应抛 TimeoutError"
    assert elapsed < 2.0, f"绝对上限 0.3s，实际耗时 {elapsed:.2f}s（硬顶失效）"
    assert owner.create_calls == 1, f"上限已耗尽不应再发起尝试，实际 {owner.create_calls} 次"
    print(f"✓ 可选绝对上限生效：retries=5 但 {elapsed:.2f}s 就放弃（只发起 1 次）")


def test_timeout_nonstream_falls_back_to_timeout():
    """只设了 timeout 的用户，两条路径都仍有上界（不会静默退回 SDK 的 600s）。"""
    c = OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m", client=None, timeout=7.0)
    assert c.timeout_nonstream == 7.0
    print("✓ timeout_nonstream 缺省回落 timeout")


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
    test_slow_stream_is_never_killed_by_elapsed_time()
    test_first_token_watchdog_times_out_then_retries()
    test_midstream_stall_raises_stall_timeout_and_closes_stream()
    test_stall_phase_uses_idle_not_first_token_watchdog()
    test_first_token_phase_ignores_idle_watchdog()
    test_stream_open_hang_hits_first_token_watchdog()
    test_watchdog_defaults_and_http_backstop()
    test_opt_in_budget_clamps_watchdogs_but_is_off_by_default()
    test_per_attempt_timeout_kills_hang_and_retries()
    test_total_budget_hard_cap_beats_remaining_retries()
    test_timeout_nonstream_falls_back_to_timeout()
    test_drain_closes_stream_on_error()
    test_drain_closes_stream_on_success()
    print("\nLLM 重试测试全部通过")