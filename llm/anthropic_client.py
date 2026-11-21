"""llm/anthropic_client — Anthropic Messages 协议内芯（`POST {base_url}/messages`）

为什么要有这一路（09-18 实机事故）：
    OpenCode Zen/Go 的端点表把一部分模型**只挂在 `/messages`（Anthropic 协议）**上 ——
    例如 `union-alpha`、`minimax-m3`、`qwen3.8-max`、`qwen3.7-*`、`qwen3.6-plus`。
    对这批模型打 OpenAI 兼容的 `/chat/completions`，网关回的是一个**光秃秃的 500**
    `{"type":"error","error":{"type":"error","message":"Internal server error"}}`
    （同族的 `grok-4.6` 才会明说 `Model grok-4.6 is not supported for format oa-compat`）。
    用户看到的现象是"配了个新模型，AI 全哑"，而面板只能说一句"对方网关 5xx，过会儿再试"
    —— 那句话既不准确也不可执行。本模块把这条路接上，模型名照填就能用。

与 OpenAI 路的四处 wire 差异（全部实测自 opencode `/zen/go/v1/messages`，别照文档猜）：
    ① `system` 是**顶层字段**，不是首条 role:system 消息；
    ② `max_tokens` **必填**（缺了回 400 `max_tokens is required and must be a positive integer`）；
    ③ 鉴权走 **`x-api-key`**（只发 `Authorization: Bearer` 会被回 `401 Missing API key`；
       `x-api-key` 与 `Authorization` **同时**发是 200 → 本层只发前者，需要 Bearer 的网关
       用 `llm.headers` 自己加，那是那个键的正当用途）；
    ④ 工具结果用 user 消息里的 `tool_result` 块（`tool_use_id` 回指），**没有** role:tool。

分层职责：本模块**只换 wire**。看门狗（首字/停顿）、重试策略、「已推 token 就不重试」、
放弃时 close 掉流 —— 这一整套骨架全部继承自 `openai_client.OpenAILlmClient`
（那些修复是 09-14 / 09-17 两次线上事故换来的，抄一份等于以后要修两遍）。此处只覆盖：
`_wire_request` / `_open_stream` / `_call` / `_event_parser` / `_parse_response` /
`_extract_response_usage`，以及传输层（`httpx`，不用 openai SDK —— 它说不了这个协议）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .. import log_setup
from . import llm_adapter
from . import _http
from .base import LlmResult
from .openai_client import OpenAILlmClient

log = log_setup.get_logger(__name__)

# Anthropic 协议版本。**必发**（真实 api.anthropic.com 缺它直接 400；opencode 网关容忍缺失，
# 但发着无害且能保证换个真 Anthropic 端点时不用改代码）。
ANTHROPIC_VERSION = "2023-06-01"

# 输出上限缺省值。Anthropic 的 `max_tokens` 是必填项，所以我们必须给一个默认值。
#   为什么是 8192：本仓真实产出量级很小（NPC 回复 100~300 token，压缩摘要 ~2k token），
#   8192 有三倍以上余量；实测该网关 8192/32000 均 200（65536 起被上游拒）。
#   为什么不给更大：`max_tokens` 一旦被顶到，回复会**静默截断**（stop_reason=max_tokens），
#   所以本层对截断单独打 WARNING 并点名这个配置键 —— 宁可小一点、响一声，也不要悄悄少半句。
DEFAULT_MAX_TOKENS = 8192


class AnthropicHttpError(_http.LlmHttpError):
    """Anthropic 路的 HTTP 错误（带 `status_code` / `body`，形状同 openai SDK，见 `_http.LlmHttpError`）。"""


def map_anthropic_usage(usage: Any) -> Optional[Dict[str, Any]]:
    """Anthropic usage → 本仓既有的「OpenAI 兼容形态」用量 dict。

    ★ 口径陷阱（踩了会静默算错账）★：Anthropic 的 `input_tokens` 是**不含缓存命中**的
    纯新增输入，而 `stats.map_usage` 的口径是 `inputTokens = prompt_tokens - cache_read`
    （DSH 互不重叠口径）。故这里必须 **prompt_tokens = input_tokens + cache_read**，
    否则缓存命中那部分会被减成负数、账本里的输入/缓存两个桶一起错。
    """
    if not isinstance(usage, dict):
        return None

    def _i(k: str) -> int:
        v = usage.get(k)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else 0

    cache_read = _i("cache_read_input_tokens")
    cache_write = _i("cache_creation_input_tokens")
    inp = _i("input_tokens") + cache_read + cache_write
    out = _i("output_tokens")
    if inp == 0 and out == 0:
        return None                     # 全零 = 没有可用用量，别往账本里塞个 0
    d: Dict[str, Any] = {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}
    if cache_read:
        d["prompt_cache_hit_tokens"] = cache_read
    if cache_write:
        d["prompt_cache_miss_tokens"] = inp - cache_read
    return d


class _AnthropicEventParser:
    """SSE 行 → `(kind, payload)` 队列项（骨架只认这四种 kind，见 openai_client 的钩子契约）。

    实测事件序列（`/messages` + `stream:true`）：
        message_start        → message.usage（**首字前那个 input_tokens 是 0**，真用量在后面）
        content_block_start  → 块类型（text / thinking / tool_use；tool_use 带 id/name）
        content_block_delta  → text_delta.text / input_json_delta.partial_json / thinking_delta
        content_block_stop
        message_delta        → delta.stop_reason + **真 usage**（input/output/cache_read）
        message_stop / ping  → 忽略（ping 由网关周期性插入，**不能**当成内容，否则看门狗被骗）

    两个刻意的实现选择：
      · 收 `event:` 行也收 `data:` 行，但**只信 data 里的 JSON**（`event:` 只是冗余标签；
        有些网关/代理会丢 event 行，只认它就会整趟解析不出东西）；
      · tool_use 的 id/name 来自 `content_block_start`，参数来自后续 `input_json_delta.partial_json`
        **逐片累积** —— 累积动作交给骨架（它本来就是按 index 拼字符串的），这里只把片
        翻译成它认得的 `{index, id, function:{name, arguments}}` 形状。
    """

    def __init__(self, on_stop_reason: Any = None) -> None:
        self._buf = ""                      # 不完整行缓冲（SSE 行可能跨 chunk）
        self._blocks: Dict[int, Dict[str, Any]] = {}   # index → {type, id, name}
        self._usage: Optional[Dict[str, Any]] = None
        self._usage_sent = False
        self._stop_reason: Optional[str] = None
        self._on_stop_reason = on_stop_reason

    # ---- SSE 解析 ----
    def __call__(self, chunk: Any) -> Iterable[Tuple[str, Any]]:
        if isinstance(chunk, (bytes, bytearray)):
            chunk = bytes(chunk).decode("utf-8", "replace")
        if not isinstance(chunk, str):
            return
        self._buf += chunk
        while True:
            line, sep, rest = self._buf.partition("\n")
            if not sep:
                break                       # 半行：留到下一个 chunk
            self._buf = rest
            for item in self._handle_line(line):
                yield item

    def finish(self) -> Iterable[Tuple[str, Any]]:
        tail = self._buf.strip()
        self._buf = ""
        if tail:
            for item in self._handle_line(tail):
                yield item
        for item in self._emit_usage():     # 兜底：网关没发 message_delta 时用量不丢
            yield item

    def _handle_line(self, raw: str) -> Iterable[Tuple[str, Any]]:
        line = raw.strip("\r").strip()
        if not line or line.startswith(":"):
            return                          # 空行（事件分隔）/ 注释心跳：都不是内容
        if line.startswith("event:"):
            return                          # 事件名冗余，类型以 data 里的 type 为准
        if not line.startswith("data:"):
            return
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            ev = json.loads(payload)
        except Exception:
            return                          # 半个 JSON（理论上不该发生）：丢掉，不毒死整条流
        if not isinstance(ev, dict):
            return
        for item in self._handle_event(ev):
            yield item

    def _handle_event(self, ev: Dict[str, Any]) -> Iterable[Tuple[str, Any]]:
        etype = ev.get("type")
        if etype == "message_start":
            msg = ev.get("message") or {}
            usage = msg.get("usage") if isinstance(msg, dict) else None
            if isinstance(usage, dict):
                self._merge_usage(usage)
            return
        if etype == "content_block_start":
            idx = int(ev.get("index", 0) or 0)
            blk = ev.get("content_block") or {}
            btype = blk.get("type")
            self._blocks[idx] = {"type": btype, "id": blk.get("id"), "name": blk.get("name")}
            if btype == "tool_use":
                # 只报"这个索引是工具、它叫什么"；参数留给后续 partial_json 片
                yield ("tool", [{"index": idx, "id": blk.get("id"),
                                 "function": {"name": blk.get("name"), "arguments": ""}}])
            return
        if etype == "content_block_delta":
            idx = int(ev.get("index", 0) or 0)
            delta = ev.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text") or ""
                if text:
                    yield ("text", text)
            elif dtype == "thinking_delta":
                think = delta.get("thinking") or ""
                if think:
                    yield ("think", think)
            elif dtype == "input_json_delta":
                part = delta.get("partial_json")
                if part:
                    yield ("tool", [{"index": idx, "function": {"arguments": part}}])
            # signature_delta / citations_delta 等：与正文无关，忽略
            return
        if etype == "message_delta":
            delta = ev.get("delta") or {}
            if isinstance(delta, dict) and delta.get("stop_reason"):
                self._stop_reason = str(delta["stop_reason"])
                if self._on_stop_reason is not None:
                    try:
                        self._on_stop_reason(self._stop_reason)
                    except Exception:  # noqa: BLE001 —— 告警失败绝不能影响正文
                        pass
            usage = ev.get("usage")
            if isinstance(usage, dict):
                self._merge_usage(usage)
            for item in self._emit_usage():
                yield item
            return
        # content_block_stop / message_stop / ping / error：无正文可报
        # （error 事件在 HTTP 层已是 4xx/5xx，这里不做二次翻译，免得两处文案打架）
        return

    def _emit_usage(self) -> Iterable[Tuple[str, Any]]:
        """把累积到的用量上报一次（骨架靠 `("usage", dict)` 这一项填 `LlmResult.usage`）。

        ★ 不能省 ★：用量只累积不上报的话，流式路径的 `usage` 恒为 None —— 账本里没有
        token、UI 上缓存命中率永远空白，而且**不报错**。实测第一版就踩了这个坑。

        为什么不在 `message_start` 就上报：那一份实测 `input_tokens=0`（真值在
        `message_delta`），更关键的是骨架把队列里的**任何**一项都当成"有内容了"，
        于是首字看门狗会被一个不含正文的用量项骗过去 —— 那是看门狗存在的全部意义所在。
        """
        if self._usage_sent or not self._usage:
            return
        mapped = map_anthropic_usage(self._usage)
        if mapped is None:
            return
        self._usage_sent = True
        yield ("usage", mapped)

    def _merge_usage(self, usage: Dict[str, Any]) -> None:
        """合并用量：**同名取最大**。

        为什么不是"后来者覆盖"：`message_start` 里带一份（实测 input_tokens=0）、
        `message_delta` 里带真值，两者都在报同一件事且只会往上长。取最大既容得下顺序差异，
        也容得下某些网关只在其中一处报值。
        """
        if self._usage is None:
            self._usage = {}
        for k, v in usage.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            cur = self._usage.get(k)
            self._usage[k] = v if cur is None else max(cur, v)


class AnthropicLlmClient(OpenAILlmClient):
    """Anthropic Messages 内芯：只换 wire，骨架全继承（见模块 docstring）。"""

    protocol_name = "anthropic"

    def __init__(self, *args: Any, max_tokens: Optional[int] = None,
                 transport: Any = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        try:
            mt = int(max_tokens) if max_tokens is not None else DEFAULT_MAX_TOKENS
        except (TypeError, ValueError):
            mt = DEFAULT_MAX_TOKENS
        # 0/负数没有意义（Anthropic 要求正整数）→ 回落默认值而不是发个必然 400 的报文
        self.max_tokens = mt if mt > 0 else DEFAULT_MAX_TOKENS
        # 传输可注入（测试用假传输；默认真 httpx）
        self._transport = transport

    # ---------- 传输 ----------
    def _get_transport(self) -> Any:
        """Anthropic 路的传输：`POST {base}/messages` + `x-api-key`（**不是** Bearer）。

        实测（opencode `/zen/go/v1/messages`）：只发 `Authorization: Bearer` → 401 `Missing API key`；
        `x-api-key` 与 `Authorization` 同时发 → 200。本层只发前者 —— 需要 Bearer 的网关
        用 `llm.headers` 自己加，那正是 headers 这个键的用途。
        """
        if self._transport is None:
            self._transport = _http.HttpxSseTransport(
                base_url=self.base_url or "", path="/messages",
                auth_headers={"x-api-key": self.api_key} if self.api_key else {},
                extra_headers={**{"anthropic-version": ANTHROPIC_VERSION}, **(self.headers or {})},
                streaming_read=self._httpx_read_timeout(),
                nonstream_read=(float(self.timeout_nonstream) * 2.0
                                if self.timeout_nonstream else None),
                error_cls=AnthropicHttpError)
        return self._transport

    # ---------- wire 钩子 ----------
    def _wire_request(self, system, messages, tools):
        wire_system, wire_messages, wire_tools = llm_adapter.canonical_to_anthropic(
            system, messages, tools)
        return wire_system, wire_messages, wire_tools

    def _body(self, wire_system: str, wire_messages: List[Dict[str, Any]],
              wire_tools: Optional[List[Dict[str, Any]]], stream: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens,
                                "messages": wire_messages}
        if wire_system:
            body["system"] = wire_system
        if wire_tools:
            body["tools"] = wire_tools
        if stream:
            body["stream"] = True
        return body

    def _call(self, wire_system, wire_messages, wire_tools):
        """非流式一发（上下文压缩走这条）。返回已解析的 dict 响应。"""
        body = self._body(wire_system, wire_messages, wire_tools, stream=False)
        return self._get_transport().post(body, stream=False).json()

    def _open_stream(self, wire_system, wire_messages, wire_tools):
        """开流：返回「可迭代 + 可 close」的 SSE 流（骨架在后台线程里迭代它）。

        注意这一步**已经在发请求并等响应头**了 —— 骨架把它放在 `_guarded(..., 首字看门狗)`
        里跑，所以"连不上/网关不回响应头"会被如实算进首字超时，而不是无限等。
        """
        body = self._body(wire_system, wire_messages, wire_tools, stream=True)
        return self._get_transport().post(body, stream=True)

    def _event_parser(self):
        # 流式路径的 stop_reason 只在 message_delta 里出现一次，而骨架不认这个字段
        # （它只认 usage/text/think/tool 四种 kind），所以截断判定就地做在解析器里。
        return _AnthropicEventParser(on_stop_reason=self._warn_if_truncated)

    def _parse_response(self, resp: Any):
        text, tool_calls, reasoning, stop_reason = llm_adapter.parse_anthropic_response(resp)
        self._warn_if_truncated(stop_reason)
        return text, tool_calls, reasoning

    # ---------- 用量 ----------
    def _extract_response_usage(self, resp: Any) -> Optional[Dict[str, Any]]:
        if resp is None:
            return None
        if isinstance(resp, dict):
            return self.map_anthropic_usage(resp.get("usage"))
        try:
            return self.map_anthropic_usage(getattr(resp, "usage", None))
        except Exception:
            return None

    @staticmethod
    def map_anthropic_usage(usage: Any) -> Optional[Dict[str, Any]]:
        """见模块级 `map_anthropic_usage`（保留这个名字是为了与 OpenAI 侧 `_usage_to_plain` 对称）。"""
        return map_anthropic_usage(usage)

    # ---------- 截断告警 ----------
    def _warn_if_truncated(self, stop_reason: Optional[str]) -> None:
        """`stop_reason=max_tokens` = 回复被 `llm.max_tokens` 顶断。

        两条路都经此处（流式由解析器在 message_delta 处回调、非流式在 `_parse_response` 里调），
        各报一次、不设去重标志 —— 一次回复一行不构成刷屏，而"只报第一次"会让长跑进程里
        第二次起的截断**永远静默**，那正是本仓最忌讳的失败形态（玩家看到半句话，日志里没有）。
        """
        if stop_reason == "max_tokens":
            log.warning("Anthropic 回复被 max_tokens=%d 顶断（stop_reason=max_tokens）——"
                        "内容可能少了一截。调大 config.json 的 llm.max_tokens 可解。",
                        self.max_tokens)
