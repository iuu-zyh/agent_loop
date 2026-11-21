"""Anthropic Messages 内芯契约：互译 / 流式解析 / 用量口径 / 骨架复用

起因（09-18 实机事故）：OpenCode Zen 把一部分模型**只挂在 `/messages`（Anthropic 协议）**上
（union-alpha、minimax-m*、qwen3.x-max…），对它们打 OpenAI 兼容的 `/chat/completions`
只会得到一个光秃秃的 500。这里钉住的是"接上另一条协议"所需的全部事实 ——
每一条都实测自真实网关，**不是照文档抄的**：

  1. `system` 是顶层字段（写进 messages 会被判非法）；
  2. `max_tokens` **必填**（缺了回 400 `max_tokens is required and must be a positive integer`）；
  3. 鉴权走 `x-api-key`（只发 `Authorization: Bearer` → 401 `Missing API key`）；
  4. 工具结果是 user 消息里的 `tool_result` 块（`tool_use_id` 回指），**没有** role:tool；
  5. 流式：`content_block_start` 给 tool_use 的 id/name，参数靠 `input_json_delta.partial_json`
     逐片累积；`message_start` 里的用量是 0，**真用量在 `message_delta`**；
  6. 用量口径：Anthropic 的 `input_tokens` **不含**缓存命中，而本仓 `stats.map_usage` 的口径是
     `inputTokens = prompt_tokens - cache_read` —— 换算错了账本会静默算错（本文件专门钉这条）。

骨架复用也在这里钉住：看门狗/重试/「已推 token 不重试」/放弃时 close 流只有**一份**实现
（继承自 OpenAILlmClient）。抄一份的那些修复迟早只修一半，故用断言把"没抄"钉死。
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from agent_loop.llm import llm_adapter
from agent_loop.llm._http import HttpxSseTransport, SseStream
from agent_loop.llm.anthropic_client import (
    ANTHROPIC_VERSION,
    AnthropicHttpError,
    AnthropicLlmClient,
    map_anthropic_usage,
)
from agent_loop.llm.openai_client import OpenAILlmClient

# ---------------------------------------------------------------- 工具与消息夹具

TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查天气",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    },
}


def _msgs_tool_roundtrip():
    return [
        {"role": "user", "content": [{"type": "text", "text": "北京天气怎么样？"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "我查一下"},
            {"type": "tool-call", "id": "call_1", "name": "get_weather", "arguments": '{"city": "北京"}'},
        ]},
        {"role": "user", "content": [
            {"type": "tool-result", "toolCallId": "call_1", "content": [{"type": "text", "text": "晴，26 度"}]},
        ]},
    ]


# ---------------------------------------------------------------- A. 互译


def test_system_goes_to_top_level_field_not_messages():
    """system 必须是顶层字段：Anthropic 不认 role:system 消息，混进去会被 400。"""
    system, msgs, tools = llm_adapter.canonical_to_anthropic(
        "你是姜萌", [{"role": "user", "content": [{"type": "text", "text": "在吗"}]}], [])
    assert system == "你是姜萌"
    assert all(m["role"] != "system" for m in msgs), "system 混进了 messages"
    assert [m["role"] for m in msgs] == ["user"]
    assert tools is None


def test_tool_result_is_user_message_with_tool_result_block():
    """工具结果走 user + tool_result 块（tool_use_id 回指）—— 没有 role:tool 这东西。"""
    _, msgs, _ = llm_adapter.canonical_to_anthropic("s", _msgs_tool_roundtrip(), [TOOL])
    assert all(m["role"] in ("user", "assistant") for m in msgs), "出现了 role:tool"
    last = msgs[-1]
    assert last["role"] == "user"
    blocks = last["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "call_1"
    assert blocks[0]["content"][0]["text"] == "晴，26 度"


def test_tool_call_becomes_tool_use_with_dict_input():
    """assistant 的 tool-call → tool_use，且 arguments 必须**解回对象**（字符串会被判非法）。"""
    _, msgs, _ = llm_adapter.canonical_to_anthropic("s", _msgs_tool_roundtrip(), [TOOL])
    asst = [m for m in msgs if m["role"] == "assistant"][0]
    tu = [b for b in asst["content"] if b["type"] == "tool_use"][0]
    assert tu["id"] == "call_1" and tu["name"] == "get_weather"
    assert tu["input"] == {"city": "北京"}, "input 不是对象（塞了 JSON 字符串？）"


def test_consecutive_same_role_merged_and_tool_result_first():
    """同角色相邻消息合并；合并后 tool_result **排在文字前**（Anthropic 的硬要求）。"""
    msgs = [
        {"role": "user", "content": [{"type": "tool-result", "toolCallId": "c9",
                                      "content": [{"type": "text", "text": "结果"}]}]},
        {"role": "user", "content": [{"type": "text", "text": "接着刚才那事说"}]},
    ]
    _, out, _ = llm_adapter.canonical_to_anthropic("s", msgs, [])
    assert len(out) == 1, f"同角色没合并：{[m['role'] for m in out]}"
    types = [b["type"] for b in out[0]["content"]]
    assert types == ["tool_result", "text"], types


def test_empty_text_block_is_dropped():
    """空文本块不发：Anthropic 拒收空 text 块，发出去整趟请求就 400。两条分支都要挡。"""
    # ① 普通 user 消息
    msgs = [{"role": "user", "content": [{"type": "text", "text": ""},
                                         {"type": "text", "text": "有内容"}]}]
    _, out, _ = llm_adapter.canonical_to_anthropic("s", msgs, [])
    assert out[0]["content"] == [{"type": "text", "text": "有内容"}]
    # ② 工具结果那条消息（Canonical 允许它同时带文字）
    msgs2 = [{"role": "user", "content": [
        {"type": "text", "text": ""},
        {"type": "tool-result", "toolCallId": "c1", "content": [{"type": "text", "text": "r"}]},
    ]}]
    _, out2, _ = llm_adapter.canonical_to_anthropic("s", msgs2, [])
    assert [b["type"] for b in out2[0]["content"]] == ["tool_result"], out2[0]["content"]
    # ③ **混装**（文字 + 图片）那条：不能靠"最后 join 成一条"把空块洗掉 ——
    #    有图片时不会走 join 分支，空 text 块会原样发出去被判非法。
    #    玩家截图配一句可选说明正是这个形态。
    msgs3 = [{"role": "user", "content": [
        {"type": "text", "text": ""},
        {"type": "image", "url": "data:image/png;base64,QUJD"},
    ]}]
    _, out3, _ = llm_adapter.canonical_to_anthropic("s", msgs3, [])
    assert [b["type"] for b in out3[0]["content"]] == ["image"], out3[0]["content"]


def test_tool_result_precedes_text_inside_one_message():
    """★ 同一条 user 消息内 tool_result 必须**前置** ★（Anthropic 的硬要求）。

    Canonical 的 content 顺序不保证这一点：[text, tool-result] 是合法 Canonical，
    照原序发出去整趟 400。这条与"合并两条消息时前置"是**两处独立**的顺序保证。
    """
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "顺便说一句"},
        {"type": "tool-result", "toolCallId": "c7", "content": [{"type": "text", "text": "结果"}]},
    ]}]
    _, out, _ = llm_adapter.canonical_to_anthropic("s", msgs, [])
    types = [b["type"] for b in out[0]["content"]]
    assert types == ["tool_result", "text"], f"tool_result 没排在文字前：{types}"


def test_merge_puts_incoming_tool_result_first():
    """合并两条同角色消息时，**后到**的那条若带 tool_result，也必须排到前面。"""
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "玩家先说的话"}]},
        {"role": "user", "content": [{"type": "tool-result", "toolCallId": "c1",
                                      "content": [{"type": "text", "text": "结果"}]}]},
    ]
    _, out, _ = llm_adapter.canonical_to_anthropic("s", msgs, [])
    assert len(out) == 1
    types = [b["type"] for b in out[0]["content"]]
    assert types == ["tool_result", "text"], types


def test_tools_wire_shape_is_name_description_input_schema():
    _, _, tools = llm_adapter.canonical_to_anthropic("s", [], [TOOL])
    assert tools == [{"name": "get_weather", "description": "查天气",
                      "input_schema": TOOL["function"]["parameters"]}]


def test_image_data_uri_becomes_base64_source_with_media_type():
    msgs = [{"role": "user", "content": [
        {"type": "image", "url": "data:image/jpeg;base64,QUJD"},
        {"type": "text", "text": "这是什么"},
    ]}]
    _, out, _ = llm_adapter.canonical_to_anthropic("s", msgs, [])
    img = out[0]["content"][0]
    assert img == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}


def test_image_http_url_becomes_url_source():
    msgs = [{"role": "user", "content": [{"type": "image", "url": "https://x/y.png"}]}]
    _, out, _ = llm_adapter.canonical_to_anthropic("s", msgs, [])
    assert out[0]["content"][0]["source"] == {"type": "url", "url": "https://x/y.png"}


def test_unrecognized_image_source_dropped_not_sent_broken():
    """认不出的图片来源宁可不发：发个坏结构会让**整趟**请求 400，比少一张图严重得多。"""
    msgs = [{"role": "user", "content": [{"type": "image", "url": "file:///tmp/a.png"},
                                         {"type": "text", "text": "看图"}]}]
    _, out, _ = llm_adapter.canonical_to_anthropic("s", msgs, [])
    assert all(b["type"] != "image" for b in out[0]["content"])


def test_parse_response_concatenates_text_and_extracts_tool_use():
    text, calls, reasoning, stop = llm_adapter.parse_anthropic_response({
        "content": [{"type": "text", "text": "前半"},
                    {"type": "thinking", "thinking": "想了想"},
                    {"type": "text", "text": "后半"},
                    {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": 1}}],
        "stop_reason": "tool_use",
    })
    assert text == "前半后半"          # 与流式的 "".join(deltas) 同口径，不插分隔符
    assert reasoning == "想了想"
    assert calls == [{"id": "t1", "name": "f", "arguments": {"a": 1}}]
    assert stop == "tool_use"


def test_parse_response_accepts_string_content():
    """有些代理会把 content 压成字符串：认它，别把正文整段丢掉。"""
    text, calls, _, _ = llm_adapter.parse_anthropic_response({"content": "纯文本", "stop_reason": "end_turn"})
    assert text == "纯文本" and calls == []


# ---------------------------------------------------------------- B. 用量口径


def test_usage_prompt_tokens_includes_cache_read():
    """★ 口径陷阱 ★：input_tokens 不含缓存命中，而本仓口径要求 prompt_tokens 含它。

    少加这一项 → stats 里 inputTokens 把缓存算成负数、账本静默错。
    """
    d = map_anthropic_usage({"input_tokens": 798, "output_tokens": 126,
                             "cache_read_input_tokens": 3116})
    assert d["prompt_tokens"] == 798 + 3116
    assert d["completion_tokens"] == 126
    assert d["total_tokens"] == 798 + 3116 + 126
    assert d["prompt_cache_hit_tokens"] == 3116


def test_usage_all_zero_is_none_not_zeros():
    """全零 = 没有可用用量 → None（往账本塞 0 会让"无用量"和"零用量"混为一谈）。"""
    assert map_anthropic_usage({"input_tokens": 0, "output_tokens": 0}) is None
    assert map_anthropic_usage(None) is None
    assert map_anthropic_usage({"input_tokens": "x", "output_tokens": None}) is None


def test_usage_cache_write_counted_into_prompt():
    d = map_anthropic_usage({"input_tokens": 10, "output_tokens": 2, "cache_creation_input_tokens": 90})
    assert d["prompt_tokens"] == 100


# ---------------------------------------------------------------- C. 假传输下的客户端


class _FakeStream:
    def __init__(self, chunks, err=None):
        self._chunks = list(chunks)
        self._err = err
        self.closed = False

    def __iter__(self):
        for c in self._chunks:
            yield c
        if self._err is not None:
            raise self._err

    def close(self):
        self.closed = True


class _FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data


class _FakeTransport:
    """假传输：记录每次报文体，按需返回流或 JSON 响应。"""

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
    return AnthropicLlmClient(base_url="http://gw/v1", api_key="k", model="union-alpha",
                              transport=transport, retries=0, timeout=5.0, idle_timeout=5.0, **kw)


def _sse(*events) -> list:
    """把事件 dict 列表编成真实 SSE 文本块（含 event:/data:/空行，与实测字节一致）。"""
    out = []
    for ev in events:
        out.append(f"event: {ev['type']}\n")
        out.append("data: " + json.dumps(ev, ensure_ascii=False) + "\n")
        out.append("\n")
    return out


def _text_reply(text="ok", usage=None):
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": usage or {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 0}},
        {"type": "message_stop"},
    ]


def _run(coro):
    """一次跑完一个协程（每个用例独立事件循环，互不串台）。"""
    return asyncio.run(coro)


async def _noop_token(_s: str) -> None:
    """空 token 回调。★ 它同时是「走流式」的开关 ★。

    骨架按 `on_token is not None` 判定流式（见 `generate` 的 `streaming = ...`）：
    不传它就是非流式一发。压缩走非流式、对话走流式，两条路的解析与用量出口都不同，
    所以每个流式用例都必须显式带上它 —— 漏了会静默测成另一条路（本文件第一版就踩了）。
    """


def _gen(client, system="s", messages=None, tools=None):
    """默认按**流式**调（对话路径）；要测非流式就直接调 client.generate(...) 不传 on_token。"""
    msgs = messages if messages is not None else [{"role": "user", "content": [{"type": "text", "text": "x"}]}]
    return client.generate(system, msgs, tools or [], on_token=_noop_token)


def test_stream_text_and_usage_reported():
    """流式：正文回调 + 用量必须上报（第一版只累积不上报，usage 恒为 None 且不报错）。"""
    t = _FakeTransport(sse=_sse(*_text_reply("ok")))
    got = []

    async def on_tok(s):
        got.append(s)

    r = _run(_client(t).generate("sys", [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
                              [], on_token=on_tok))
    assert r.text == "ok"
    assert got == ["ok"], f"正文没流式回调：{got}"
    assert r.usage is not None, "流式用量丢了（账本会没有 token）"
    assert r.usage["prompt_tokens"] == 7 and r.usage["completion_tokens"] == 3
    assert r.provider == "openai" or r.provider  # provider 字段照旧有值即可


def test_stream_tool_use_accumulates_partial_json():
    """tool_use：id/name 来自 content_block_start，参数靠多个 input_json_delta 拼起来。"""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "call_9", "name": "get_weather", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"city"'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": ': "上海"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"input_tokens": 5, "output_tokens": 9}},
        {"type": "message_stop"},
    ]
    t = _FakeTransport(sse=_sse(*events))
    r = _run(_gen(_client(t), messages=[{"role": "user", "content": [{"type": "text", "text": "天气"}]}],
                  tools=[TOOL]))
    assert r.tool_calls == [{"id": "call_9", "name": "get_weather", "arguments": {"city": "上海"}}]
    assert r.text == ""


def test_stream_thinking_delta_goes_to_reasoning_not_text():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "想"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "xx"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "了想"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 1, "output_tokens": 2}},
    ]
    t = _FakeTransport(sse=_sse(*events))
    r = _run(_gen(_client(t)))
    assert r.reasoning == "想了想"
    assert r.text == "", "思考内容混进正文了"


def test_message_start_alone_yields_no_content():
    """`message_start` 只有用量、没有正文 —— 不许产出内容项。

    骨架把队列里**任何**一项都当作"模型开口了"，若在此处上报用量，
    首字看门狗会被一个不含正文的项骗过去，那就等于没有看门狗。
    """
    body = _sse(_text_reply("ok")[0])
    t = _FakeTransport(sse=body)
    r = _run(_gen(_client(t)))
    assert r.text == ""
    assert r.usage is None, "message_start 的零用量被当成真用量上报了"


def test_sse_framing_survives_split_lines_and_noise():
    """半行跨 chunk / 注释心跳 / ping / event 行 / [DONE] 都不能丢正文或造出正文。"""
    raw = ("event: message_start\n"
           "data: {\"type\":\"message_start\",\"message\":{\"usage\":{\"input_tokens\":0}}}\n\n"
           ": 心跳注释\n"
           "event: ping\ndata: {\"type\":\"ping\"}\n\n"
           "data: {\"type\":\"content_block_delta\",\"index\":0,"
           "\"delta\":{\"type\":\"text_delta\",\"text\":\"你\"}}\n\n")
    # 故意把每一行切成两半喂进去
    chunks = [raw[i:i + 7] for i in range(0, len(raw), 7)]
    chunks.append("data: [DONE]\n\n")
    chunks.append("data: {\"type\":\"content_block_delta\",\"index\":0,"
                  "\"delta\":{\"type\":\"text_delta\",\"text\":\"好\"}}\n\n")
    t = _FakeTransport(sse=chunks)
    r = _run(_gen(_client(t)))
    assert r.text == "你好"


def test_stream_body_shape_has_model_max_tokens_system_stream():
    """报文体契约：max_tokens 必填、system 顶层、stream=true、没有 tools 时不塞空数组。"""
    t = _FakeTransport(sse=_sse(*_text_reply("ok")))
    _run(_gen(_client(t), system="我是姜萌"))
    body = t.calls[0]["body"]
    assert body["max_tokens"] > 0, "max_tokens 缺失（Anthropic 会回 400）"
    assert body["system"] == "我是姜萌"
    assert body["stream"] is True
    assert body["model"] == "union-alpha"
    assert "tools" not in body, "空工具列表不该发出去"
    assert all(m["role"] != "system" for m in body["messages"])


def test_on_token_presence_selects_streaming_path():
    """`on_token` 在不在 = 走流式还是非流式（这是骨架既有的开关语义，不是本协议特有的）。"""
    t1 = _FakeTransport(sse=_sse(*_text_reply("ok")))
    _run(_gen(_client(t1)))
    assert t1.calls[0]["stream"] is True and t1.calls[0]["body"]["stream"] is True

    t2 = _FakeTransport(data={"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1,
                                                                                    "output_tokens": 1}})
    _run(_client(t2).generate("s", [{"role": "user", "content": [{"type": "text", "text": "x"}]}], []))
    assert t2.calls[0]["stream"] is False and "stream" not in t2.calls[0]["body"]


def test_nonstream_body_has_no_stream_flag_and_parses_usage():
    t = _FakeTransport(data={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                             "usage": {"input_tokens": 10, "output_tokens": 2,
                                       "cache_read_input_tokens": 4}})
    r = _run(_client(t).generate("s", [{"role": "user", "content": [{"type": "text", "text": "x"}]}], []))
    assert t.calls[0]["stream"] is False
    assert "stream" not in t.calls[0]["body"]
    assert r.text == "ok"
    assert r.usage["prompt_tokens"] == 14 and r.usage["prompt_cache_hit_tokens"] == 4


def test_max_tokens_truncation_warns_and_names_config_key(caplog):
    """被 max_tokens 顶断必须出声，且点名配置键 —— 否则玩家只看到"话说了半句"。"""
    t = _FakeTransport(sse=_sse(*[
        {"type": "message_start", "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "半句"}},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"},
         "usage": {"input_tokens": 3, "output_tokens": 8192}},
    ]))
    with caplog.at_level(logging.WARNING, logger="agent_loop.llm.anthropic_client"):
        r = _run(_gen(_client(t, max_tokens=8192)))
    assert r.text == "半句"
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "max_tokens" in joined and "llm.max_tokens" in joined, joined


def test_http_error_is_retryable_5xx_not_retryable_400():
    """错误形状必须让骨架的既有判据认得（它就是按 openai SDK 的形状写的）。"""
    e500 = AnthropicHttpError(500, {"type": "error", "error": {"message": "Internal server error"}}, "boom")
    e400 = AnthropicHttpError(400, {"type": "error", "error": {"message": "bad"}}, "bad")
    assert OpenAILlmClient._is_retryable(e500) is True
    assert OpenAILlmClient._is_retryable(e400) is False


def test_http_error_is_classified_by_probe():
    """探针归类也必须认得（面板上那句人话就是它给的）。"""
    from agent_loop.llm import probe

    e = AnthropicHttpError(401, {"type": "error", "error": {"type": "AuthError", "message": "Missing API key."}},
                           "Missing API key.")
    kind, status, _code, msg = probe.classify(e)
    assert kind == "auth" and status == 401
    assert "Missing API key" in msg


def test_streaming_5xx_raises_before_iteration_and_closes_context():
    """流式下 5xx：必须先把错误体读全再退出上下文（不读就退出 = 连接不还池）。"""
    resp = _FakeResp(None)

    class _FakeStreamResp:
        status_code = 503
        text = '{"type":"error","error":{"message":"Endpoint is unavailable."}}'

        def __init__(self):
            self.read_called = False

        def read(self):
            self.read_called = True

        def json(self):
            return {"type": "error", "error": {"message": "Endpoint is unavailable."}}

        def iter_lines(self):
            raise AssertionError("错误响应不该被迭代")

    fr = _FakeStreamResp()

    class _CM:
        def __init__(self):
            self.exited = False

        def __enter__(self):
            return fr

        def __exit__(self, *a):
            self.exited = True
            return False

    cm = _CM()

    class _HttpxClient:
        def post(self, *a, **kw):
            raise AssertionError("走流式分支时不该调用 post")

        def stream(self, *a, **kw):
            return cm

    tr = HttpxSseTransport("http://gw/v1", "/messages", {"x-api-key": "k"}, None, 5.0, 10.0,
                           AnthropicHttpError)
    tr._client = _HttpxClient()
    with pytest.raises(AnthropicHttpError) as ei:
        tr.post({"model": "m"}, stream=True)
    assert ei.value.status_code == 503
    assert "unavailable" in str(ei.value)
    assert fr.read_called, "错误体没读全就退出上下文 —— 连接不会还池"
    assert cm.exited, "上下文没退出（连接泄漏）"


def test_transport_url_and_headers_contract():
    """URL 拼接与鉴权头：x-api-key + anthropic-version 必发；自定义头最后覆盖。"""
    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {}

    class _HttpxClient:
        def __init__(self):
            self.calls = []

        def post(self, url, **kw):
            self.calls.append((url, kw))
            return _Resp()

    for base, want in (("http://gw/v1", "http://gw/v1/messages"),
                       ("http://gw", "http://gw/v1/messages"),
                       ("http://gw/v1/messages", "http://gw/v1/messages")):
        # 走**客户端真实构造路径**（而不是直接捏传输对象）：协议版本头与自定义头的合并
        # 都发生在 client._get_transport 里，绕开它就测不到"实际发出去的是什么"。
        c = AnthropicLlmClient(base_url=base, api_key="sk-1", model="m",
                               headers={"User-Agent": "mine/1.0"},
                               retries=0, timeout=5.0, idle_timeout=5.0)
        tr = c._get_transport()
        fc = _HttpxClient()
        tr._client = fc
        tr.post({"model": "m"}, stream=False)
        url, kw = fc.calls[0]
        assert url == want, f"{base} → {url}"
        h = kw["headers"]
        assert h["x-api-key"] == "sk-1", "鉴权头不是 x-api-key（只发 Bearer 会被回 401 Missing API key）"
        assert h["anthropic-version"] == ANTHROPIC_VERSION
        assert h["User-Agent"] == "mine/1.0"


def test_sse_stream_close_is_idempotent_and_appends_newline():
    """`SseStream`：给 iter_lines 的每行补回换行（解析器认的是原始 SSE 文本块）。"""
    class _Resp:
        def iter_lines(self):
            return iter(["data: a", "data: b"])

    class _CM:
        def __init__(self):
            self.n = 0

        def __enter__(self):
            return _Resp()

        def __exit__(self, *a):
            self.n += 1
            return False

    cm = _CM()
    st = SseStream(cm, cm.__enter__())
    assert list(st) == ["data: a\n", "data: b\n"]
    st.close()
    st.close()
    assert cm.n == 2, "close 不幂等（骨架可能从两个线程同时关）"


# ---------------------------------------------------------------- D. 骨架复用


def test_does_not_reimplement_the_reliability_skeleton():
    """骨架只能有一份：本类不许自己实现 generate / _call_stream / 看门狗 / 重试。

    这些修复是 09-14（传输层异常判可重试）与 09-17（整趟挂钟误杀长回复）两次线上事故
    换来的。抄一份 = 以后要修两遍，且**一定会**只修一半。
    """
    own = set(AnthropicLlmClient.__dict__)
    for name in ("generate", "_call_stream", "_guarded", "_is_retryable", "_backoff",
                 "_watchdog_seconds", "_budget_deadline", "_httpx_read_timeout"):
        assert name not in own, f"{name} 被重写了 —— 可靠性骨架必须继承那一份"
    assert issubclass(AnthropicLlmClient, OpenAILlmClient)
    # 该覆盖的必须覆盖（否则会在说 OpenAI 协议）
    for name in ("_wire_request", "_open_stream", "_call", "_event_parser",
                 "_parse_response", "_extract_response_usage"):
        assert name in own, f"{name} 没覆盖 —— 会拿 OpenAI 的 wire 去说 Anthropic 协议"


def test_protocol_name_is_reported_for_diagnostics():
    assert AnthropicLlmClient.protocol_name == "anthropic"
    assert OpenAILlmClient.protocol_name == "openai"
