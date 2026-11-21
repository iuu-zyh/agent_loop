"""llm/responses_client — OpenAI Responses 协议内芯（`POST {base_url}/responses`）

为什么要有这一路（09-18）：OpenCode Zen/Go 把一部分模型**只挂在 `/responses`** 上
（`grok-4.6`、`gpt-5.6-luna`、`muse-spark-*`）。对它们打 `/chat/completions` 会得到
`Model grok-4.6 is not supported for format oa-compat`，打 `/messages` 会得到
`... not supported for format anthropic` —— 也就是说，**换了个模型名就 AI 全哑**，
而玩家手上只有一行"对方网关 5xx"。这一路把第三条协议接上。

与 OpenAI 兼容路的 wire 差异（全部实测自 opencode `/zen/go/v1/responses`，别照记忆写）：
  ① 端点是 `/responses`，`input` 是**扁平条目列表**（role 消息与 `function_call` /
     `function_call_output` 并列，而不是挂在 message.tool_calls 里）；
  ② system 走顶层 `instructions`；
  ③ 工具定义**扁平**：`{type:function, name, description, parameters}`（不套 "function" 键）；
  ④ 助手历史用 `output_text` 回放；
  ⑤ 输出在 `output[]` 里按类型分条目：`reasoning`（只有 summary 摘要）/ `message` / `function_call`；
  ⑥ 流式事件名前缀是 `response.`（`response.output_text.delta` 等），且**思考是
     `response.reasoning_summary_text.delta`**（摘要，不是原始思维链）；
  ⑦ 用量键名与 OpenAI 一致（`input_tokens` / `output_tokens` / `input_tokens_details.cached_tokens`），
     且 `input_tokens` **含**缓存命中 —— 与 Anthropic 那条路的口径**相反**，别照抄。

★ 不发 `max_output_tokens` ★：该协议的**推理 token 也计入 output**（实测一个"只回两个字"的
请求烧掉 278 个推理 token），拿 `llm.max_tokens`（默认 8192）去卡它会把长思考的回复截断。
上界改由两个看门狗负责（它们只判"还有没有新内容"，不判"总共花了多久"）；
真被网关侧截断时（`status=incomplete`）本层会打 WARNING，不会静默少半句。

分层职责与 Anthropic 路相同：**只换 wire**，看门狗/重试/「已推 token 不重试」/close 流
一律继承 `openai_client.OpenAILlmClient`（那些修复是两次线上事故换来的，抄一份迟早只修一半）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional, Tuple

from .. import log_setup
from . import llm_adapter
from . import _http
from .base import LlmResult
from .openai_client import OpenAILlmClient

log = log_setup.get_logger(__name__)


class ResponsesHttpError(_http.LlmHttpError):
    """Responses 路的 HTTP 错误（形状同 openai SDK，见 `_http.LlmHttpError`）。"""


def map_responses_usage(usage: Any) -> Optional[Dict[str, Any]]:
    """Responses usage → 本仓既有的「OpenAI 兼容形态」用量 dict。

    ★ 与 Anthropic 口径相反，别照抄那边 ★：
      Responses 的 `input_tokens` **包含**缓存命中（OpenAI 一贯口径），而 `stats.map_usage`
      要的是 `inputTokens = prompt_tokens - cache_read`，所以这里 **prompt_tokens = input_tokens**
      （**不**加 cached），缓存放 `prompt_tokens_details.cached_tokens`（map_usage 的首选键）。
      Anthropic 那边则必须相加 —— 两条路各错一次都会让账本静默算错，故两边都有测试钉住。
    """
    if not isinstance(usage, dict):
        return None

    def _i(k: str) -> int:
        v = usage.get(k)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else 0

    prompt = _i("input_tokens")
    completion = _i("output_tokens")
    if prompt == 0 and completion == 0:
        return None                     # 全零 = 没有可用用量，别往账本里塞个 0
    d: Dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion,
                         "total_tokens": _i("total_tokens") or (prompt + completion)}
    details = usage.get("input_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens")
        if isinstance(cached, int) and cached >= 0:
            d["prompt_tokens_details"] = {"cached_tokens": cached}
    odetails = usage.get("output_tokens_details")
    if isinstance(odetails, dict):
        rt = odetails.get("reasoning_tokens")
        if isinstance(rt, int) and rt >= 0:
            d["completion_tokens_details"] = {"reasoning_tokens": rt}
    return d


class _ResponsesEventParser:
    """SSE 行 → `(kind, payload)` 队列项（骨架只认这四种 kind，见 openai_client 的钩子契约）。

    实测事件序列（`/responses` + `stream:true`，纯文本与工具调用两种都验过）：
        response.created / response.in_progress   → 无正文，忽略
        response.output_item.added                → 条目类型（function_call 带 name/call_id）
        response.reasoning_summary_text.delta     → 思考摘要片
        response.content_part.added               → 无正文，忽略
        response.output_text.delta                → 正文片
        response.function_call_arguments.delta    → 工具参数片（只有 output_index，没有 id/name）
        response.function_call_arguments.done     → 无正文，忽略（参数已由片累积）
        response.output_item.done                 → 条目收尾（function_call 带全量 arguments）
        response.completed                        → 真用量 + status/incomplete_details

    ★ 三处**绝不能被当成正文**的东西 ★（每一条都是"同一段话演两遍"或"看门狗被骗"的来源）：
      · `response.output_text.done` 的 `text`（是前面所有 delta 的合并，再发一次就重复整段）；
      · `response.reasoning_summary_text.done` 同理；
      · `response.created` / `in_progress`（它们带 `response` 快照，里面没有正文，但一旦误取
        就会产出空内容项，首字看门狗随之被骗过）。
    """

    def __init__(self, on_incomplete: Any = None) -> None:
        self._buf = ""                       # 不完整行缓冲（SSE 行可能跨 chunk）
        self._args_seen: set = set()          # 已收到过参数片的 output_index
        self._usage: Optional[Dict[str, Any]] = None
        self._usage_sent = False
        self._incomplete: Optional[str] = None
        self._on_incomplete = on_incomplete

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
                break                        # 半行：留到下一个 chunk
            self._buf = rest
            for item in self._handle_line(line):
                yield item

    def finish(self) -> Iterable[Tuple[str, Any]]:
        tail = self._buf.strip()
        self._buf = ""
        if tail:
            for item in self._handle_line(tail):
                yield item
        for item in self._emit_usage():       # 兜底：网关没发 response.completed 也别丢用量
            yield item

    def _handle_line(self, raw: str) -> Iterable[Tuple[str, Any]]:
        line = raw.strip("\r").strip()
        if not line or line.startswith(":"):
            return                            # 空行（事件分隔）/ 注释心跳：都不是内容
        if line.startswith("event:"):
            return                            # 事件名冗余：类型以 data 里的 type 为准
        if not line.startswith("data:"):
            return
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            ev = json.loads(payload)
        except Exception:
            return                            # 半个 JSON：丢掉，不毒死整条流
        if not isinstance(ev, dict):
            return
        for item in self._handle_event(ev):
            yield item

    def _handle_event(self, ev: Dict[str, Any]) -> Iterable[Tuple[str, Any]]:
        etype = ev.get("type")
        if etype == "response.output_text.delta":
            text = ev.get("delta")
            if text:
                yield ("text", str(text))
            return
        if etype == "response.reasoning_summary_text.delta":
            think = ev.get("delta")
            if think:
                yield ("think", str(think))
            return
        if etype == "response.output_item.added":
            item = ev.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "function_call":
                idx = int(ev.get("output_index", 0) or 0)
                # 只报"这个索引是工具、它叫什么"；参数留给后续 delta 片
                yield ("tool", [{"index": idx, "id": item.get("call_id"),
                                 "function": {"name": item.get("name"), "arguments": ""}}])
            return
        if etype == "response.function_call_arguments.delta":
            idx = int(ev.get("output_index", 0) or 0)
            part = ev.get("delta")
            if part:
                self._args_seen.add(idx)
                yield ("tool", [{"index": idx, "function": {"arguments": part}}])
            return
        if etype == "response.output_item.done":
            item = ev.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "function_call":
                idx = int(ev.get("output_index", 0) or 0)
                tc: Dict[str, Any] = {"index": idx, "id": item.get("call_id"),
                                      "function": {"name": item.get("name")}}
                # 只有在**没收到过参数片**时才拿 done 里的全量参数补位 ——
                # 否则骨架会把参数拼两遍（它是按字符串累加的）。
                if idx not in self._args_seen and item.get("arguments"):
                    tc["function"]["arguments"] = item.get("arguments")
                yield ("tool", [tc])
            return
        if etype in ("response.completed", "response.incomplete", "response.failed"):
            resp = ev.get("response") if isinstance(ev.get("response"), dict) else {}
            usage = resp.get("usage")
            if isinstance(usage, dict):
                self._merge_usage(usage)
            self._check_incomplete(resp)
            for item in self._emit_usage():
                yield item
            return
        # 其余（created/in_progress/content_part.*/*.done 等）：无正文可报
        return

    def _check_incomplete(self, resp: Dict[str, Any]) -> None:
        reason = None
        details = resp.get("incomplete_details")
        if isinstance(details, dict):
            reason = details.get("reason")
        if not reason and resp.get("status") == "incomplete":
            reason = "incomplete"
        if not reason and resp.get("status") == "failed":
            err = resp.get("error")
            reason = (err or {}).get("message") if isinstance(err, dict) else "failed"
        if not reason:
            return
        self._incomplete = str(reason)
        if self._on_incomplete is not None:
            try:
                self._on_incomplete(self._incomplete)
            except Exception:  # noqa: BLE001 —— 告警失败绝不能影响正文
                pass

    def _emit_usage(self) -> Iterable[Tuple[str, Any]]:
        """把累积到的用量上报一次（骨架靠 `("usage", dict)` 这一项填 `LlmResult.usage`）。

        不能省：只累积不上报的话流式 `usage` 恒为 None —— 账本里没有 token、
        UI 上缓存命中率永远空白，而且**不报错**（Anthropic 那边第一版就踩了这个坑）。
        """
        if self._usage_sent or not self._usage:
            return
        mapped = map_responses_usage(self._usage)
        if mapped is None:
            return
        self._usage_sent = True
        yield ("usage", mapped)

    def _merge_usage(self, usage: Dict[str, Any]) -> None:
        """合并用量：同名取最大（`completed` 里那份是权威值，但取最大容得下顺序差异）。"""
        if self._usage is None:
            self._usage = {}
        for k, v in usage.items():
            if isinstance(v, dict):
                cur = self._usage.get(k)
                merged = dict(cur) if isinstance(cur, dict) else {}
                for sk, sv in v.items():
                    if isinstance(sv, (int, float)) and not isinstance(sv, bool):
                        merged[sk] = sv if sk not in merged else max(merged[sk], sv)
                self._usage[k] = merged
                continue
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            cur = self._usage.get(k)
            self._usage[k] = v if cur is None else max(cur, v)


class OpenAIResponsesLlmClient(OpenAILlmClient):
    """OpenAI Responses 内芯：只换 wire，骨架全继承（见模块 docstring）。"""

    protocol_name = "responses"

    def __init__(self, *args: Any, transport: Any = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._transport = transport

    # ---------- 传输 ----------
    def _get_transport(self) -> Any:
        """Responses 路的传输：`POST {base}/responses` + `Authorization: Bearer`（OpenAI 惯例）。"""
        if self._transport is None:
            self._transport = _http.HttpxSseTransport(
                base_url=self.base_url or "", path="/responses",
                auth_headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                extra_headers=dict(self.headers or {}),
                streaming_read=self._httpx_read_timeout(),
                nonstream_read=(float(self.timeout_nonstream) * 2.0
                                if self.timeout_nonstream else None),
                error_cls=ResponsesHttpError)
        return self._transport

    # ---------- wire 钩子 ----------
    def _wire_request(self, system, messages, tools):
        return llm_adapter.canonical_to_responses(system, messages, tools)

    def _body(self, wire_system: str, wire_messages: Any,
              wire_tools: Optional[Any], stream: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": self.model, "input": wire_messages}
        if wire_system:
            body["instructions"] = wire_system
        if wire_tools:
            body["tools"] = wire_tools
        if stream:
            body["stream"] = True
        # 刻意不发 max_output_tokens：该协议的推理 token 也计入 output，卡小了会截断长思考
        # （见模块 docstring）。上界由两个存活看门狗负责。
        return body

    def _call(self, wire_system, wire_messages, wire_tools):
        """非流式一发（上下文压缩走这条）。返回已解析的 dict 响应。"""
        body = self._body(wire_system, wire_messages, wire_tools, stream=False)
        return self._get_transport().post(body, stream=False).json()

    def _open_stream(self, wire_system, wire_messages, wire_tools):
        body = self._body(wire_system, wire_messages, wire_tools, stream=True)
        return self._get_transport().post(body, stream=True)

    def _event_parser(self):
        return _ResponsesEventParser(on_incomplete=self._warn_if_incomplete)

    def _parse_response(self, resp: Any):
        text, tool_calls, reasoning, reason = llm_adapter.parse_responses_response(resp)
        if reason:
            self._warn_if_incomplete(reason)
        return text, tool_calls, reasoning

    # ---------- 用量 ----------
    def _extract_response_usage(self, resp: Any) -> Optional[Dict[str, Any]]:
        if resp is None:
            return None
        if isinstance(resp, dict):
            return map_responses_usage(resp.get("usage"))
        try:
            return map_responses_usage(getattr(resp, "usage", None))
        except Exception:
            return None

    @staticmethod
    def map_responses_usage(usage: Any) -> Optional[Dict[str, Any]]:
        """见模块级 `map_responses_usage`（名字与 Anthropic 侧对称）。"""
        return map_responses_usage(usage)

    # ---------- 截断告警 ----------
    def _warn_if_incomplete(self, reason: Optional[str]) -> None:
        """`status=incomplete` = 回复被网关侧截断。

        刻意点名"这不是本程序的配置"：本路**不发** `max_output_tokens`，
        所以用户去改 `llm.max_tokens` 是白费力气 —— 那句提示必须说清去哪找原因，
        否则就是把人支到错的地方（比不说更糟）。
        """
        if not reason:
            return
        log.warning("Responses 回复未完成（reason=%s）—— 内容可能少了一截。"
                    "本协议不发 max_output_tokens，故这来自网关/账号侧的限制"
                    "（换模型或换协议；`llm.max_tokens` 对这条协议无效）。", reason)
