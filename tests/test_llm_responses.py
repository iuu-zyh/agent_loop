"""OpenAI Responses 协议契约：互译 / 流式解析 / 用量口径 / 事件不得重复计正文

上游事实（实测自 opencode `/zen/go/v1/responses`，grok-4.6）：
  · `grok-4.6` 打 /chat/completions → 401 `not supported for format oa-compat`；
    打 /messages → 401 `not supported for format anthropic`；只有 /responses 通
    （所以「加第三条协议」不是锦上添花，是这批模型**唯一**的路）。
  · `input` 是**扁平条目列表**：role 消息与 `function_call` / `function_call_output` 并列。
  · 工具定义**扁平**（不套 "function" 键）；助手历史用 `output_text` 回放。
  · 流式事件名前缀 `response.`，思考走 `response.reasoning_summary_text.delta`（是摘要）。
  · 用量键名同 OpenAI（`input_tokens` 含缓存命中），与 Anthropic 那条路的口径**相反**。

本文件重点钉两类**静默**失效：
  ① 把 `*.done` 事件里的合并文本再发一次 → 同一段话演两遍（玩家直接看到重影）；
  ② 用量口径抄错（照 Anthropic 那样把 cache_read 加进 prompt_tokens）→ 账本静默算错。
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from agent_loop.llm import llm_adapter
from agent_loop.llm._http import HttpxSseTransport
from agent_loop.llm.openai_client import OpenAILlmClient
from agent_loop.llm.responses_client import (
    OpenAIResponsesLlmClient,
    ResponsesHttpError,
    map_responses_usage,
)

TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查天气",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    },
}


def _run(coro):
    return asyncio.run(coro)


async def _noop_token(_s: str) -> None:
    """空 token 回调 —— 同时是「走流式」的开关（骨架按 `on_token is not None` 判定）。"""


def _gen(client, system="s", messages=None, tools=None):
    msgs = messages if messages is not None else [{"role": "user", "content": [{"type": "text", "text": "x"}]}]
    return client.generate(system, msgs, tools or [], on_token=_noop_token)


# ---------------------------------------------------------------- A. 互译


def test_instructions_top_level_and_input_items():
    """system 走顶层 `instructions`；`input` 是条目列表，不塞 role:system。"""
    inst, items, tools = llm_adapter.canonical_to_responses(
        "你是姜萌", [{"role": "user", "content": [{"type": "text", "text": "在吗"}]}], [])
    assert inst == "你是姜萌"
    assert items == [{"role": "user", "content": [{"type": "input_text", "text": "在吗"}]}]
    assert tools is None


def test_tool_call_is_standalone_function_call_item_with_call_id():
    """assistant 的 tool-call → 独立 `function_call` 条目，参数是**字符串**形态的 JSON。"""
    msgs = [{"role": "assistant", "content": [
        {"type": "text", "text": "查一下"},
        {"type": "tool-call", "id": "call_1", "name": "get_weather", "arguments": '{"city": "北京"}'},
    ]}]
    _, items, _ = llm_adapter.canonical_to_responses("s", msgs, [TOOL])
    fc = [i for i in items if i.get("type") == "function_call"]
    assert len(fc) == 1 and fc[0]["call_id"] == "call_1" and fc[0]["name"] == "get_weather"
    assert json.loads(fc[0]["arguments"]) == {"city": "北京"}
    # 文字先落，工具条目在后（顺序不能颠倒）
    assert items[0]["role"] == "assistant" and items.index(fc[0]) > 0


def test_tool_result_is_function_call_output_item():
    """工具结果 → 独立 `function_call_output` 条目（`call_id` 回指），**不是** role 消息。"""
    msgs = [{"role": "user", "content": [
        {"type": "tool-result", "toolCallId": "call_1", "content": [{"type": "text", "text": "晴，26 度"}]},
    ]}]
    _, items, _ = llm_adapter.canonical_to_responses("s", msgs, [])
    out = [i for i in items if i.get("type") == "function_call_output"]
    assert len(out) == 1 and out[0]["call_id"] == "call_1" and out[0]["output"] == "晴，26 度"
    assert all(i.get("role") != "tool" for i in items)


def test_assistant_history_uses_output_text():
    """助手历史用 `output_text` 回放（实测 input_text 也收，但前者是规范形态）。"""
    msgs = [{"role": "assistant", "content": [{"type": "text", "text": "记住了"}]}]
    _, items, _ = llm_adapter.canonical_to_responses("s", msgs, [])
    assert items[0]["content"] == [{"type": "output_text", "text": "记住了"}]


def test_tools_are_flat_not_nested_under_function_key():
    _, _, tools = llm_adapter.canonical_to_responses("s", [], [TOOL])
    assert tools == [{"type": "function", "name": "get_weather", "description": "查天气",
                      "parameters": TOOL["function"]["parameters"]}]
    assert "function" not in tools[0]


def test_empty_text_blocks_are_dropped():
    """空文本块不发（两家协议都拒收空 text）。"""
    msgs = [{"role": "user", "content": [{"type": "text", "text": ""},
                                         {"type": "text", "text": "有内容"}]}]
    _, items, _ = llm_adapter.canonical_to_responses("s", msgs, [])
    assert items[0]["content"] == [{"type": "input_text", "text": "有内容"}]


def test_parse_response_extracts_text_reasoning_and_tool_calls():
    text, calls, reasoning, reason = llm_adapter.parse_responses_response({
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "想了想"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "晴"}, {"type": "output_text", "text": "天"}]},
            {"type": "function_call", "call_id": "c9", "name": "f", "arguments": '{"a": 1}'},
        ],
    })
    assert text == "晴天"          # 与流式 "".join(deltas) 同口径
    assert reasoning == "想了想"
    assert calls == [{"id": "c9", "name": "f", "arguments": {"a": 1}}]
    assert reason is None


def test_parse_response_reports_incomplete_reason():
    _, _, _, reason = llm_adapter.parse_responses_response(
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []})
    assert reason == "max_output_tokens"
    # 没有 incomplete_details 但状态是 incomplete：也要给出一个非空原因，别静默
    _, _, _, r2 = llm_adapter.parse_responses_response({"status": "incomplete", "output": []})
    assert r2 == "incomplete"


# ---------------------------------------------------------------- B. 用量口径


def test_usage_prompt_tokens_does_not_add_cache_read():
    """★ 与 Anthropic 相反 ★：input_tokens **已含**缓存命中，不能再加一次。

    加错会让 stats 的 inputTokens 把缓存算成负数、账本静默错 —— 两条路各钉一条测试，
    正因为它们的口径相反，照抄另一条必然错。
    """
    d = map_responses_usage({"input_tokens": 217, "output_tokens": 181, "total_tokens": 398,
                             "input_tokens_details": {"cached_tokens": 128},
                             "output_tokens_details": {"reasoning_tokens": 176}})
    assert d["prompt_tokens"] == 217, "把 cached 加进 prompt_tokens 了（那是 Anthropic 的口径）"
    assert d["completion_tokens"] == 181
    assert d["prompt_tokens_details"] == {"cached_tokens": 128}
    assert d["completion_tokens_details"] == {"reasoning_tokens": 176}


def test_usage_all_zero_is_none():
    assert map_responses_usage({"input_tokens": 0, "output_tokens": 0}) is None
    assert map_responses_usage(None) is None


# ---------------------------------------------------------------- C. 假传输下的客户端


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        for c in self._chunks:
            yield c

    def close(self):
        self.closed = True


class _FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data


class _FakeTransport:
    def __init__(self, *, sse=None, data=None, error=None):
        self.sse = sse or []
        self.data = data
        self.error = error
        self.calls = []

    def post(self, body, stream):
        self.calls.append({"body": body, "stream": stream})
        if self.error is not None:
            raise self.error
        return _FakeStream(self.sse) if stream else _FakeResp(self.data)


def _client(transport, **kw):
    return OpenAIResponsesLlmClient(base_url="http://gw/v1", api_key="k", model="grok-4.6",
                                    transport=transport, retries=0, timeout=5.0, idle_timeout=5.0, **kw)


def _sse(*events) -> list:
    out = []
    for ev in events:
        out.append(f"event: {ev['type']}\n")
        out.append("data: " + json.dumps(ev, ensure_ascii=False) + "\n")
        out.append("\n")
    return out


def _completed(usage=None):
    return {"type": "response.completed", "response": {
        "status": "completed",
        "usage": usage or {"input_tokens": 217, "output_tokens": 8,
                           "input_tokens_details": {"cached_tokens": 128}}}}


def test_stream_text_via_output_text_delta():
    events = [
        {"type": "response.created", "response": {}},
        {"type": "response.output_item.added", "output_index": 1,
         "item": {"type": "message", "content": []}},
        {"type": "response.output_text.delta", "output_index": 1, "delta": "大"},
        {"type": "response.output_text.delta", "output_index": 1, "delta": "海"},
        {"type": "response.output_text.done", "output_index": 1, "text": "大海"},
        {"type": "response.output_item.done", "output_index": 1,
         "item": {"type": "message", "content": [{"type": "output_text", "text": "大海"}]}},
        _completed(),
    ]
    got = []

    async def on_tok(s):
        got.append(s)

    t = _FakeTransport(sse=_sse(*events))
    r = _run(_client(t).generate("s", [{"role": "user", "content": [{"type": "text", "text": "x"}]}],
                                [], on_token=on_tok))
    assert r.text == "大海", f"正文不对（done 事件被重复计入？）：{r.text!r}"
    assert got == ["大", "海"], f"流式片不对：{got}"
    assert r.usage and r.usage["prompt_tokens"] == 217


def test_stream_reasoning_summary_goes_to_reasoning_not_text():
    events = [
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "用户"},
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "在问"},
        {"type": "response.reasoning_summary_text.done", "output_index": 0, "text": "用户在问"},
        _completed(),
    ]
    t = _FakeTransport(sse=_sse(*events))
    r = _run(_gen(_client(t)))
    assert r.reasoning == "用户在问"
    assert r.text == "", "思考摘要混进正文了"


def test_stream_tool_call_with_partial_args_and_done_fallback():
    """工具参数：有片就用片累积；**只有 done** 时才用 done 里的全量（否则会拼两遍）。"""
    # 情形①：有参数片
    events = [
        {"type": "response.output_item.added", "output_index": 1,
         "item": {"type": "function_call", "call_id": "c1", "name": "get_weather", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"city"'},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": ': "上海"}'},
        {"type": "response.function_call_arguments.done", "output_index": 1,
         "arguments": '{"city": "上海"}', "name": "get_weather"},
        {"type": "response.output_item.done", "output_index": 1,
         "item": {"type": "function_call", "call_id": "c1", "name": "get_weather",
                  "arguments": '{"city": "上海"}'}},
        _completed(),
    ]
    r = _run(_gen(_client(_FakeTransport(sse=_sse(*events))), tools=[TOOL]))
    assert r.tool_calls == [{"id": "c1", "name": "get_weather", "arguments": {"city": "上海"}}]

    # 情形②：只有 done（网关不发片）→ 必须用 done 里的全量
    events2 = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "function_call", "call_id": "c2", "name": "get_weather", "arguments": ""}},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "function_call", "call_id": "c2", "name": "get_weather",
                  "arguments": '{"city": "北京"}'}},
        _completed(),
    ]
    r2 = _run(_gen(_client(_FakeTransport(sse=_sse(*events2))), tools=[TOOL]))
    assert r2.tool_calls == [{"id": "c2", "name": "get_weather", "arguments": {"city": "北京"}}]


def test_stream_body_shape():
    """报文体契约：instructions 顶层、input 条目、stream=true、不发 max_output_tokens。"""
    t = _FakeTransport(sse=_sse(*[_completed()]))
    _run(_gen(_client(t), system="我是姜萌", tools=[TOOL]))
    body = t.calls[0]["body"]
    assert body["instructions"] == "我是姜萌"
    assert body["stream"] is True and body["model"] == "grok-4.6"
    assert body["input"][0]["content"][0]["type"] == "input_text"
    assert body["tools"][0]["type"] == "function" and "function" not in body["tools"][0]
    assert "max_output_tokens" not in body, \
        "发了 max_output_tokens：该协议的推理 token 也计入 output，卡小了会截断长思考"


def test_nonstream_response_and_usage():
    t = _FakeTransport(data={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
        "usage": {"input_tokens": 10, "output_tokens": 2,
                  "input_tokens_details": {"cached_tokens": 4}}})
    r = _run(_client(t).generate("s", [{"role": "user", "content": [{"type": "text", "text": "x"}]}], []))
    assert t.calls[0]["stream"] is False and "stream" not in t.calls[0]["body"]
    assert r.text == "ok" and r.usage["prompt_tokens"] == 10
    assert r.usage["prompt_tokens_details"] == {"cached_tokens": 4}


def test_incomplete_response_warns_and_says_config_key_is_useless(caplog):
    """被网关截断要出声，并说清"改 llm.max_tokens 没用"（这条协议不发那个字段）。"""
    events = [
        {"type": "response.output_text.delta", "output_index": 0, "delta": "半句"},
        {"type": "response.incomplete", "response": {
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 5, "output_tokens": 8192}}},
    ]
    with caplog.at_level(logging.WARNING, logger="agent_loop.llm.responses_client"):
        r = _run(_gen(_client(_FakeTransport(sse=_sse(*events)))))
    assert r.text == "半句"
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "max_output_tokens" in joined and "llm.max_tokens" in joined, joined


def test_http_error_shapes_are_all_parsed():
    """错误体三家形态都要认（openai / anthropic / opencode-responses）。"""
    class _R:
        def __init__(self, code, body, text=""):
            self.status_code = code
            self._b = body
            self.text = text or json.dumps(body, ensure_ascii=False)

        def json(self):
            return self._b

    from agent_loop.llm._http import raise_for_status

    for body, want in (
        ({"type": "error", "error": {"message": "Internal server error"}}, "Internal server error"),
        ({"error": {"message": "bad key"}}, "bad key"),
        ({"model": "m", "error": {"message": "Upstream request failed"}}, "Upstream request failed"),
    ):
        with pytest.raises(ResponsesHttpError) as ei:
            raise_for_status(_R(400, body), ResponsesHttpError)
        assert want in str(ei.value), (body, str(ei.value))
        assert ei.value.status_code == 400


def test_transport_url_and_headers_for_responses():
    """URL 拼接与鉴权头：Bearer（不是 x-api-key），不带 anthropic-version。"""
    class _Resp:
        status_code = 200

        def json(self):
            return {}

    class _HttpxClient:
        def __init__(self):
            self.calls = []

        def post(self, url, **kw):
            self.calls.append((url, kw))
            return _Resp()

    for base, want in (("http://gw/v1", "http://gw/v1/responses"),
                       ("http://gw", "http://gw/v1/responses"),
                       ("http://gw/v1/responses", "http://gw/v1/responses")):
        c = OpenAIResponsesLlmClient(base_url=base, api_key="sk-1", model="m",
                                     headers={"User-Agent": "mine/1.0"},
                                     retries=0, timeout=5.0, idle_timeout=5.0)
        tr = c._get_transport()
        fc = _HttpxClient()
        tr._client = fc
        tr.post({"model": "m"}, stream=False)
        url, kw = fc.calls[0]
        assert url == want, f"{base} → {url}"
        h = kw["headers"]
        assert h["Authorization"] == "Bearer sk-1"
        assert "x-api-key" not in h and "anthropic-version" not in h
        assert h["User-Agent"] == "mine/1.0"


# ---------------------------------------------------------------- D. 骨架复用


def test_does_not_reimplement_the_reliability_skeleton():
    """骨架只能有一份（同 Anthropic 路的理由：那些修复是两次线上事故换来的）。"""
    own = set(OpenAIResponsesLlmClient.__dict__)
    for name in ("generate", "_call_stream", "_guarded", "_is_retryable", "_backoff",
                 "_watchdog_seconds", "_budget_deadline", "_httpx_read_timeout"):
        assert name not in own, f"{name} 被重写了 —— 可靠性骨架必须继承那一份"
    assert issubclass(OpenAIResponsesLlmClient, OpenAILlmClient)
    for name in ("_wire_request", "_open_stream", "_call", "_event_parser",
                 "_parse_response", "_extract_response_usage"):
        assert name in own, f"{name} 没覆盖 —— 会拿另一条协议的 wire 去说话"


def test_protocol_name():
    assert OpenAIResponsesLlmClient.protocol_name == "responses"
