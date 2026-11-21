"""llm/openai_client — OpenAI 兼容 adapter：Canonical→wire→HTTP→wire→Canonical（迁移原 _step_via_openai）

可靠性（网络类错误自动重试，generate 幂等）：
- 只重试「可重试」错误：网络层 + HTTP 408/429/5xx；认证/格式类错误（400/401/403 等）
  立即抛出，重试无意义。
- 网络层的判据**必须包含 httpcore/httpx/openai 自己的异常树**：它们不继承 OSError 家族
  （见 _RETRYABLE_TRANSPORT_ERRORS 的注释），只枚举 Python 内建会漏掉整整一族传输层错误。
- 指数退避：0.5s / 1s / 2s，封顶 4s（config llm.retries 控制总次数，0=不重试）。
- 流式约束：一旦已对外推送过 token 则不重试（重发会让玩家看到同一内容两遍）；开流即断
  （零产出）等价未开始，可重试。
- 超时 = **存活看门狗**，不是"整趟耗时上限"（09-18 重做，起因见 docs/reference/brain-liveness.md §4）：
  ① 首字看门狗 `llm.timeout`：请求发出 → **第一个内容块**；
  ② 停顿看门狗 `llm.idle_timeout`：相邻两个内容块之间（缺省回落 ①）。
  两个都只问"多久没有新内容了"，**不问"已经过了多久"**。只要模型还在出字，跑十分钟也不算超时
  —— 长回复/长思考被"整趟挂钟"按秒砍断是 09-17 实机事故的直接成因。
  心跳免疫：看门狗数的是**解析出来的内容块**（`_drain` 塞进队列的项），不是 socket 字节；
  对端发再多 SSE 注释/心跳也不会续命（旧实现靠 httpx read timeout，那才会被心跳续命，
  于是不得不额外加一道 total_budget 挂钟 —— 现在两者都不需要了）。
- `llm.total_budget` 降级为**可选**绝对上限：默认 0/不设（None），只有用户显式设置时才生效，
  且只做"收缩上面两个看门狗"这一件事。默认关的理由：整趟耗时不是"卡住"的判据。
- `_open_stream` 下发给 httpx 的 read timeout 只当**背板**（被放弃的读线程靠它退出），
  取两个看门狗之和，保证"看门狗先响、httpx 后响"。
"""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .. import log_setup
from . import llm_adapter
from ..llm import think as _think
from .base import LlmResult

log = log_setup.get_logger(__name__)


def _collect_transport_error_types() -> tuple:
    """传输层异常根类（httpx / openai 两条线），任一 import 失败则跳过该条。

    为什么不能只枚举 Python 内建（09-14 线上事故的直接根因）：

    实测 httpx 0.28.1 —— `httpx.RemoteProtocolError` 的 MRO 是
        RemoteProtocolError → ProtocolError → TransportError → RequestError → HTTPError → Exception
    与 `ConnectionError` / `TimeoutError` / `OSError` 三条线**全不相交**，`status_code` 也是 None。
    于是 `_is_retryable` 判它"不可重试"，上游流到一半掐断时 **0 次重试**直接放弃。
    openai 侧同理：`APITimeoutError → APIConnectionError → APIError → Exception`，
    也**不是**内建 `TimeoutError`（所以 16:36 那次超时同样被判"不可重试"）。

    范围：
      httpx.TransportError      覆盖 RemoteProtocolError / LocalProtocolError / ReadTimeout /
                                ConnectTimeout / ReadError / WriteError / ConnectError / PoolTimeout
      openai.APIConnectionError 含子类 APITimeoutError

    guarded import 的原因：本模块允许在无 openai 的环境下被 import（依赖方自行 pip install），
    且测试惯用普通异常 mock —— 内建分支原样保留，那类测试行为不变。
    """
    types: list = []
    try:
        import httpx

        types.append(httpx.TransportError)
    except Exception:  # noqa: BLE001 —— 缺库时静默降级，不影响内建判据
        pass
    try:
        import openai

        types.append(openai.APIConnectionError)
    except Exception:  # noqa: BLE001
        pass
    return tuple(types)


_RETRYABLE_TRANSPORT_ERRORS = _collect_transport_error_types()


def _positive_or_none(v: Any) -> Optional[float]:
    """数值 → 正浮点；None/0/非法值 → None（= 这个看门狗不启用）。

    归一放在构造器里做，免得直接 `OpenAILlmClient(timeout=0)` 的调用方踩到
    "0 秒看门狗 = 立刻超时"（config 层 `_positive_float_or_none` 同义：0 与 null 都表示关）。
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


class _OpenAIEventParser:
    """OpenAI 兼容 chunk → `(kind, payload)` 队列项（骨架侧唯一认得的中间形态）。

    kind 语义（与 `OpenAILlmClient._call_stream` 的消费端一一对应）：
      "usage" → 已归一为纯 dict 的尾巴用量
      "text"  → 正文片段（已按内嵌 <think> 标签分流）
      "think" → 思考片段
      "tool"  → OpenAI 形态的 tool_calls 增量列表（{index,id,function:{name,arguments}}）

    本类就是原先内联在 `_drain` 里的那段解析，**逐行搬出来**（顺序、判据、兜底全不变），
    只为让 Anthropic 侧能换掉它而不动骨架。
    """

    def __init__(self) -> None:
        self._splitter = _think.ThinkSplitter()

    def __call__(self, chunk: Any):
        # 尾巴用量（DSH 早样本）：附在 finish 块或用量专块，随 [DONE] 前到达
        try:
            cu = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
        except Exception:
            cu = None
        if cu is not None:
            plain = OpenAILlmClient._usage_to_plain(cu)
            if plain is not None:
                yield ("usage", plain)
        choices = getattr(chunk, "choices", None) if not isinstance(chunk, dict) else chunk.get("choices")
        if not choices:
            return
        first = choices[0]
        if isinstance(first, dict):
            delta = first.get("delta") or {}
        else:
            delta = getattr(first, "delta", None) or {}
        if isinstance(delta, dict):
            content = delta.get("content")
            tool_calls_delta = delta.get("tool_calls")
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        else:
            content = getattr(delta, "content", None)
            tool_calls_delta = getattr(delta, "tool_calls", None)
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning:
            if not isinstance(reasoning, str):
                reasoning = str(reasoning)
            # 来源①：独立 reasoning_content 字段（DeepSeek-R1 / mimo 等原生思考通道）
            yield ("think", reasoning)
        if content:
            # 来源②：content 内嵌 <think> 标签拆分（无标签则原样走 body）
            out: List[Any] = []
            self._splitter.feed(content, on_body=lambda s: out.append(("text", s)),
                                on_think=lambda s: out.append(("think", s)))
            for item in out:
                yield item
        if tool_calls_delta:
            yield ("tool", tool_calls_delta)

    def finish(self):
        """流结束：吐出拆分器缓冲的尾巴（可能是半个标签）。"""
        out: List[Any] = []
        self._splitter.flush(on_body=lambda s: out.append(("text", s)),
                             on_think=lambda s: out.append(("think", s)))
        for item in out:
            yield item


class OpenAILlmClient:
    """持 base_url/api_key/model + 现成 client，统一 generate 出口，内部做 wire 互译"""

    # 本内芯说的是哪条协议（供日志/探针/自动识别上报；子类 Anthropic 覆盖为 "anthropic"）
    protocol_name = "openai"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = "test-model",
        provider: str = "openai",
        client: Any = None,
        supports_image: Optional[bool] = None,
        retries: int = 1,
        timeout: Optional[float] = None,
        timeout_nonstream: Optional[float] = None,
        idle_timeout: Optional[float] = None,
        total_budget: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
        reasoning_effort: Optional[str] = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.provider = provider
        # 可靠性：网络/瞬时错误重试次数（总尝试 = retries + 1，0 表示不重试）
        self.retries = max(0, retries)
        # ★ 首字看门狗（秒）★：从请求发出（含连接/排队/响应头/预填充）到**第一个内容块**为止。
        #   量的是"多久没有新内容"，**不是**"这一趟已经跑了多久" —— 09-17 实机事故就是后者
        #   造成的：思考流已推 382 token，第 45 秒被整趟挂钟砍断，成员本轮跳过。
        #   只要还在出字，跑多久都不算超时；真·长回复（长思考/长文）因此不再被误杀。
        #   同一个值还兼作中途停顿看门狗的**回落值**（idle_timeout 缺省时）。
        #   0/null = 不设看门狗（可无限等，勿用）。
        self.timeout = _positive_or_none(timeout)
        # 非流式等待上限（秒）。**必须与流式分开**：非流式请求在整段生成完之前一个字节都不
        # 发，read timeout 于是直接量"完整生成时长"（含首 token），量纲完全不同。
        # 唯一调用方是回合边界的上下文压缩（compaction/compress.py:282，不传 on_token）——
        # 那是一发大 prompt（阈值 = ctx_window×0.8，默认 32768×0.8≈26k token），
        # 用 45s 去卡它会**静默废掉压缩**（压缩失败是软失败，只落一条 warning，回合照常继续）。
        # 非流式没有"块"可数，本层**不设看门狗**，上界完全由 httpx 的 read timeout（本值，
        # 由 `_build_client` 下发到客户端级）给出 = 它的"首字"语义。
        # 未显式配置时回落 timeout，保证"只设了 timeout 的用户"两条路径都仍有上界。
        self.timeout_nonstream = (_positive_or_none(timeout_nonstream)
                                  if timeout_nonstream is not None else self.timeout)
        # ★ 停顿看门狗（秒）★：相邻两个内容块之间的间隔上限。缺省回落 timeout。
        #   为什么要有它：首字与"吐字中途"是两个量纲 —— 首字要容忍网关排队/预填充（实测最高
        #   28.5s），而吐字中途的正常间隔是毫秒级（实测 decode 4094ms/247 token ≈ 16ms/token），
        #   同一个数字套两处必然一边太紧、一边太松。它只认"有没有新内容"，心跳/注释不算。
        # 三态：缺省(None) = 回落首字看门狗；显式 0 = 关掉停顿看门狗；正数 = 用它。
        self.idle_timeout = (_positive_or_none(idle_timeout)
                             if idle_timeout is not None else self.timeout)
        # 可选绝对上限（秒）：整个 generate（含全部重试）的挂钟硬顶。**默认关（None/0）**。
        # ★ 它不再是"等待时间上界"的承担者 ★：承担者是上面两个看门狗（它们对心跳免疫，
        #   见模块 docstring）。用户显式设了本值时才生效，且只做一件事 —— 把两个看门狗
        #   收缩到"距到期还剩多少秒"，于是"总耗时 ≤ 本值"重新成为硬保证。
        #   仍然保留这个键的原因：面板里有这一行、旧用户可能手编过它；但它**默认不参与**，
        #   因为"整趟耗时"从来不是"卡住"的判据（长回复会被它按秒砍断）。
        self.total_budget = total_budget

        # 附加请求头（llm.headers）。有些网关要求客户端自报家门，例如 OpenCode Go 要求
        # `x-opencode-session`（稳定会话 ID，供其路由与 prompt 缓存）+ 自定义 User-Agent
        # （用 SDK 默认 UA 会被其前置 Cloudflare 判为机器人 → 403 error code 1010）。
        self.headers = dict(headers) if headers else None
        # 思考强度（config llm.reasoning_effort，OpenAI 生态的**请求体**字段）。
        # 仅 openai 协议注入（anthropic/responses 两条路没有这个键）；None/空 = 完全不发，
        # 请求体与改动前逐字节相同。值域（low/high/max）与"探测结论说了才算"的门控在
        # factory（_gated_reasoning_effort）——本层只做透传，不做判断。
        # 注入走 SDK 的 extra_body（并进 JSON 顶层）：SDK 1.x 全版本支持，且不会与
        # create() 的具名参数撞车。实测 CodeBuddy 上游：不带 = 不出思维链（v4.1-flash
        # 默认非思考），带 low/high/max = 思考流随 reasoning_content 分段到达。
        self.reasoning_effort = (str(reasoning_effort).strip().lower()
                                 if reasoning_effort and str(reasoning_effort).strip() else None)
        # 图片能力（三态）：
        #   None  → 未知，由 model 能力表 / config enabled 在 factory 判定后传入
        #   True  → 强制允许带图；False → 请求带图时报错而非静默
        self.supports_image = supports_image
        # client 可传现成 openai 兼容客户端（openai 1.x / 低层 callable）；None 则懒构造
        self._client = client
        self._built_client: Optional[Any] = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._built_client is None:
            self._built_client = self._build_client()
        return self._built_client

    def _build_client(self) -> Any:
        # 懒造真客户端；依赖方需自行 pip install openai
        from openai import OpenAI

        kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        # 客户端级 timeout 供**非流式**路径（_call）使用：它只是**背板**（量的是"整段生成
        # 什么时候开始回来"），判据是本层的首字看门狗（`generate` 非流式分支，取值
        # timeout_nonstream）。取两倍 = 给看门狗一个宽限期，保证"看门狗先响、httpx 后响"，
        # 报错文案才说得出人话；流式路径则在 _open_stream 里按请求单独下发（见 __init__）。
        try:
            if self.timeout_nonstream is not None:
                kwargs["timeout"] = float(self.timeout_nonstream) * 2.0
        except (TypeError, ValueError):
            pass
        # 无条件关掉 SDK 自带的隐式重试
        # 本层已有受控重试（self.retries），两层叠加会让"最坏等待"不可预期（SDK 默认还会
        # 自己重试 2 次，玩家等的时间就被乘了三倍）。关掉之后，"一次请求最多等多少秒"
        # 完全由两个看门狗 / timeout_nonstream（+ 可选的 total_budget）决定。
        kwargs["max_retries"] = 0
        # 附加头（含自定义 User-Agent / 会话 ID）；SDK 会默认发 OpenAI/Python-x.y 的 UA，
        # 某些网关的前置 Cloudflare 会因此拦截（403 error code 1010），故允许覆盖。
        if self.headers:
            kwargs["default_headers"] = dict(self.headers)
        return OpenAI(**kwargs)

    def _call(self, wire_system: str, wire_messages: List[Dict[str, Any]],
              wire_tools: Optional[List[Dict[str, Any]]]):
        """非流式一发（wire_system 本协议不用：已在 wire_messages 里）。"""
        oai_messages, oai_tools = wire_messages, wire_tools
        client = self._get_client()
        body_extra = self._reasoning_effort_kwargs()
        # openai 1.x：client.chat.completions.create
        if hasattr(client, "chat") and hasattr(client.chat, "completions"):
            return client.chat.completions.create(
                model=self.model,
                messages=oai_messages,
                tools=oai_tools,
                tool_choice="auto" if oai_tools else None,
                **body_extra,
            )
        if hasattr(client, "chat_completions_create"):
            return client.chat_completions_create(messages=oai_messages, tools=oai_tools, **body_extra)
        # 兜底：可调用对象
        return client(oai_messages, oai_tools)

    def _reasoning_effort_kwargs(self) -> Dict[str, Any]:
        """思考强度 → SDK 的 extra_body kwarg（并进 JSON 顶层）；未配置返回 {}（零改动）。"""
        if not self.reasoning_effort:
            return {}
        return {"extra_body": {"reasoning_effort": self.reasoning_effort}}

    # ------------------------------------------------------------------
    # 协议钩子：子类只换 wire，不碰可靠性骨架
    # ------------------------------------------------------------------
    # 为什么抽这三个方法：Anthropic Messages 与 OpenAI 兼容**只在报文形状上**不同，
    # 而看门狗 / 重试 / 「已推 token 就不重试」/ 放弃时 close 掉流这一整套骨架**逐字相同**
    # （每一条都是 09-14、09-17 两次线上事故换来的）。骨架抄一份 = 那些修复以后要修两遍，
    # 迟早只修一半。故本类保留骨架与全部默认实现（= 原 OpenAI 行为，逐字节不变），
    # `llm/anthropic_client.py` 只覆盖下面三个钩子 + 传输层。
    def _wire_request(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Any:
        """Canonical → 本协议 wire 报文，返回 `(wire_system, wire_messages, wire_tools)`。

        system **单列**出来：OpenAI 兼容把它当**首条 role:system 消息**，而 Anthropic 把它当
        **顶层字段**（写进 messages 会被判非法）。默认实现把 system 塞进 messages（本协议要的
        形态）后**原样再返回一份** —— 对本协议无用的那个返回值，是给另一条协议留的位。
        """
        wire_messages, wire_tools = llm_adapter.canonical_to_openai(system, messages, tools)
        return system, wire_messages, wire_tools

    def _event_parser(self) -> Any:
        """造一个**本次调用专用**的「wire 块 → 队列项」解析器。

        契约：`parse(chunk)` 与 `parse.finish()` 都产出 `(kind, payload)` 序列，
        kind ∈ {"usage","text","think","tool"}，与 `_drain` 塞进队列的项同义。
        必须每次新造：解析带跨块状态（OpenAI 侧是 <think> 标签拆分器，Anthropic 侧是
        SSE 行缓冲与块索引）——复用实例会让并发回合互相串台（max_concurrent_turns>1）。
        """
        return _OpenAIEventParser()

    def _parse_response(self, resp: Any) -> Any:
        """非流式响应 → `(text, tool_calls, reasoning)`，与流式路径出口同形。"""
        text, tool_calls = llm_adapter.parse_openai_response(resp)
        # 思考内容：reasoning_content 字段（首选）+ content 内嵌标签拆出（兜底）
        reasoning = self._extract_reasoning(resp)
        emb, text = _think.split_think(text)
        if emb:
            reasoning = (reasoning + "\n" + emb).strip() if reasoning else emb
        return text or "", tool_calls, reasoning

    def _open_stream(self, wire_system: str, wire_messages: List[Dict[str, Any]],
                     wire_tools: Optional[List[Dict[str, Any]]]):
        """打开 OpenAI 兼容流（返回同步可迭代 Stream；真正网络在迭代时发生）。

        wire_system 本协议不用（system 已在 wire_messages 首条）。

        附带 stream_options.include_usage=True 索取尾巴用量（DSH 早样本）；
        网关/旧桩不支持该参数时回退为普通流（用量缺失，统计跳过）。

        按请求下发 `_httpx_read_timeout()`（**背板**，不是判据）：不覆盖的话，流式会沿用为
        "非流式大摘要"准备的宽松值（见 __init__ 注释）。
        """
        oai_messages, oai_tools = wire_messages, wire_tools
        client = self._get_client()
        extra: Dict[str, Any] = {}
        try:
            _backstop = self._httpx_read_timeout()
            if _backstop is not None:
                extra["timeout"] = float(_backstop)
        except (TypeError, ValueError):
            extra = {}
        extra.update(self._reasoning_effort_kwargs())
        if hasattr(client, "chat") and hasattr(client.chat, "completions"):
            try:
                return client.chat.completions.create(
                    model=self.model,
                    messages=oai_messages,
                    tools=oai_tools,
                    tool_choice="auto" if oai_tools else None,
                    stream=True,
                    stream_options={"include_usage": True},
                    **extra,
                )
            except TypeError:
                return client.chat.completions.create(
                    model=self.model,
                    messages=oai_messages,
                    tools=oai_tools,
                    tool_choice="auto" if oai_tools else None,
                    stream=True,
                    **extra,
                )
        if hasattr(client, "chat_completions_create"):
            try:
                return client.chat_completions_create(messages=oai_messages, tools=oai_tools,
                                                      stream=True, stream_options={"include_usage": True},
                                                      **extra)
            except TypeError:
                return client.chat_completions_create(messages=oai_messages, tools=oai_tools,
                                                      stream=True, **extra)
        return None

    @staticmethod
    def _usage_to_plain(usage: Any) -> Optional[Dict[str, Any]]:
        """把 wire usage（dict/object）压成可进账本的纯 dict；无用量返回 None。"""
        if usage is None:
            return None
        if isinstance(usage, dict):
            d = dict(usage)
        else:
            d = {}
            for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                      "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
                try:
                    v = getattr(usage, k, None)
                except Exception:
                    v = None
                if isinstance(v, int):
                    d[k] = v
            for k in ("prompt_tokens_details", "completion_tokens_details"):
                try:
                    v = getattr(usage, k, None)
                except Exception:
                    v = None
                if v is None:
                    continue
                if isinstance(v, dict):
                    d[k] = dict(v)
                else:
                    sub: Dict[str, Any] = {}
                    for sk in ("cached_tokens", "reasoning_tokens"):
                        try:
                            sv = getattr(v, sk, None)
                        except Exception:
                            sv = None
                        if isinstance(sv, int):
                            sub[sk] = sv
                    if sub:
                        d[k] = sub
        if not isinstance(d.get("prompt_tokens"), int) or not isinstance(d.get("completion_tokens"), int):
            return None
        return d

    @classmethod
    def _extract_response_usage(cls, resp: Any) -> Optional[Dict[str, Any]]:
        """非流式响应抽用量（dict/object 皆容）。"""
        if resp is None:
            return None
        if isinstance(resp, dict):
            return cls._usage_to_plain(resp.get("usage"))
        try:
            return cls._usage_to_plain(getattr(resp, "usage", None))
        except Exception:
            return None

    @staticmethod
    def _is_retryable(e: Exception) -> bool:
        """判定错误是否值得重试：网络层 + HTTP 408/429/5xx。

        网络层判据是**两条线并集**：
          ① Python 内建 ConnectionError / TimeoutError / OSError
          ② httpcore/httpx/openai 自己的传输层根类（_RETRYABLE_TRANSPORT_ERRORS）
        只做①会漏掉整整一族（RemoteProtocolError / ReadTimeout / ConnectError / …），
        详见 _collect_transport_error_types 的注释。

        不依赖 openai 库类型的**强绑定**（guarded import + 鸭子判定并存），
        便于测试用普通异常 mock —— 普通异常走①，行为与历史一致。
        """
        if isinstance(e, (ConnectionError, TimeoutError, OSError)):
            return True
        # 空元组时 isinstance 恒为 False，无需额外守卫
        if isinstance(e, _RETRYABLE_TRANSPORT_ERRORS):
            return True
        sc = getattr(e, "status_code", None) or getattr(e, "statusCode", None)
        return isinstance(sc, int) and sc in (408, 429, 500, 502, 503, 504)

    @staticmethod
    def _backoff(attempt: int) -> float:
        """指数退避：0.5 / 1 / 2 秒，封顶 4 秒。"""
        return min(0.5 * (2 ** attempt), 4.0)

    def _budget_deadline(self, started: float, streaming: bool = True) -> Optional[float]:
        """可选绝对上限的到期时刻（monotonic）；没设/非流式/非正值 → None = 不设顶。

        ★ 默认关 ★。它**不再**是"等待时间上界"的承担者（那是两个看门狗的活），只在用户
        显式配置后把看门狗收缩到剩余额度，让"总耗时 ≤ total_budget"重新成立。
        为什么不再默认参与：整趟耗时不是"卡住"的判据 —— 09-17 实机事故里模型一直在正常
        出字，只是这条回复长，于是被 45s 的整趟挂钟砍断，该成员本轮整段丢失。

        预算**只作用于流式（聊天）路径**：非流式的唯一调用方是上下文压缩，那是后台维护
        动作，失败是软失败（回合照常继续，只是这轮不压缩），拿玩家等待预算去卡它，结果
        只会是"大摘要永远做不完"。非流式仍有上界，由 timeout_nonstream 自然给出。
        """
        if not streaming or not self.total_budget:
            return None
        try:
            b = float(self.total_budget)
        except (TypeError, ValueError):
            return None
        return started + b if b > 0 else None

    def _watchdog_seconds(self, phase: Optional[float],
                          budget_deadline: Optional[float] = None) -> Optional[float]:
        """这一等最多等多久 = min(阶段看门狗, 距绝对上限还剩多少秒)；都可能缺省。

        取 min 而不是只取阶段值，是为了让"总耗时 ≤ total_budget"在用户显式设了上限时
        成为硬保证：越接近上限，允许的单次等待自动收缩，不会再冲过去。上限没设时
        （默认）本函数恒等于阶段看门狗 —— 于是**不存在任何形式的整趟耗时判据**。
        """
        caps: List[float] = []
        for c in (phase, None if budget_deadline is None else (budget_deadline - monotonic())):
            if c is None:
                continue
            try:
                caps.append(float(c))
            except (TypeError, ValueError):
                continue
        return min(caps) if caps else None

    def _httpx_read_timeout(self) -> Optional[float]:
        """下发给 httpx 的 read timeout —— **只当背板**，不是判据（流式路径）。

        判据是我们自己的看门狗（只认"解析出来的内容块"，见 `_call_stream`）。但看门狗到点
        放弃一趟时，读线程还阻塞在 socket 上（Python 杀不掉线程），得靠它才能退出并把连接
        还回池子。取**两个看门狗之和**当宽限期，是为了让"看门狗先响、httpx 后响"成为必然：
        两者同值会抢跑，报错文案就变成 SDK 的 read timeout（`httpx.ReadTimeout`），
        玩家看到的原因从"模型没开口/说到一半卡住"退化成一句模糊的"读取超时"。
        """
        vals: List[float] = []
        for v in (self.timeout, self.idle_timeout):
            try:
                if v:
                    vals.append(float(v))
            except (TypeError, ValueError):
                continue
        return sum(vals) if vals else None

    async def _guarded(self, aw: Any, seconds: Optional[float], what: str) -> Any:
        """按存活看门狗 await 一个 awaitable：seconds=None 原样 await（不设看门狗）。

        到点抛**内建 TimeoutError**（`_is_retryable` 认得），而不是让 asyncio 的取消语义
        外泄 —— 调用方按"网络类错误"统一处理即可。文案里点名是哪一种超时（首字/停顿），
        因为两者的玩家体感与处置完全不同（一直没开口 vs 说到一半卡住）。

        ★ 被放弃的底层工作不会被杀死（Python 杀不掉线程）：调用方负责尽力 close 掉流，
          剩下的交给 `_httpx_read_timeout()` 那道背板。
        """
        if seconds is None:
            return await aw
        try:
            return await asyncio.wait_for(aw, timeout=max(0.01, float(seconds)))
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"{what}：{float(seconds):.0f}s 内没有任何新内容"
                               "（存活看门狗，与整趟耗时无关）") from e

    @staticmethod
    def _extract_reasoning(resp: Any) -> str:
        """非流式响应提取 message.reasoning_content（dict / object 两种形态皆容）。
        无该字段返回 ""（非 reasoning 模型，不影响任何现有路径）。"""
        try:
            choices = resp.get("choices") if isinstance(resp, dict) else getattr(resp, "choices", None)
            if not choices:
                return ""
            first = choices[0]
            msg = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
            if msg is None:
                return ""
            rc = msg.get("reasoning_content") if isinstance(msg, dict) else getattr(msg, "reasoning_content", None)
            if not rc:
                # 兜底：OpenRouter 风格网关用 reasoning 字段承载思考流（8123 网关 mimo-v2.5-free）
                rc = msg.get("reasoning") if isinstance(msg, dict) else getattr(msg, "reasoning", None)
            return str(rc) if rc else ""
        except Exception:
            return ""

    async def _call_stream(
        self,
        wire_system: str,
        wire_messages: List[Dict[str, Any]],
        wire_tools: Optional[List[Dict[str, Any]]],
        on_token: Callable[[str], Awaitable[None]],
        stats: Optional[Dict[str, int]] = None,
        on_reasoning: Optional[Callable[[str], Awaitable[None]]] = None,
        budget_deadline: Optional[float] = None,
    ) -> "LlmResult":
        """流式调用：后台线程迭代 stream → 队列 → 主协程逐 token 回调 + 累积结果。

        stats：本次尝试的输出统计（{"tokens": int}），供 generate 判定"是否已对外推送过
        token"（已推送则不重试）。必须用局部 dict 传入——max_concurrent_turns>1 时
        多个回合共享同一 LlmClient 实例，实例级字段会串台。

        budget_deadline：可选的绝对上限到期时刻（monotonic，默认 None = 不设顶）。只用来
        收缩下面两个看门狗，见 `_watchdog_seconds`。

        ★ 超时只在这里发生，且只有两种，都是存活看门狗 ★：
          ① 首字（`self.timeout`）：开流 → 队列里出现**第一项**；
          ② 停顿（`self.idle_timeout`）：队列里相邻两项之间。
        两者数的都是"有没有新的内容块"，**没有**任何"整趟已经跑了多久"的判据 ——
        这就是长回复/长思考不会被按秒砍断的原因（09-17 事故的反面）。
        """
        stream = await self._guarded(
            asyncio.to_thread(self._open_stream, wire_system, wire_messages, wire_tools),
            self._watchdog_seconds(self.timeout, budget_deadline),
            "首字超时：模型一直没回应")
        if stream is None:
            # 兜底可调用对象不支持流式：退非流式，整段回调一次。
            # 这一等同样按"首字"量（非流式在整段生成完之前一个字节都不发，两件事在这里是同一件）。
            resp = await self._guarded(
                asyncio.to_thread(self._call, wire_system, wire_messages, wire_tools),
                self._watchdog_seconds(self.timeout_nonstream, budget_deadline),
                "首字超时：模型一直没回应")
            text, tool_calls, reasoning = self._parse_response(resp)
            if stats is not None:
                stats["tokens"] = stats.get("tokens", 0) + len(text) + len(reasoning)
            if reasoning and on_reasoning is not None:
                await on_reasoning(reasoning)
            if text:
                await on_token(text)
            return LlmResult(text=text or "", reasoning=reasoning, tool_calls=tool_calls,
                             raw=resp, usage=self._extract_response_usage(resp),
                             provider=self.provider, model=self.model)

        queue: asyncio.Queue = asyncio.Queue()
        _END = object()
        _ERR = object()
        # 本次调用专用的 wire 解析器（协议钩子；OpenAI 侧即内嵌 <think> 标签拆分）。
        # 每次调用新造：解析器带跨块状态，复用会让并发回合串台。
        parse = self._event_parser()

        def _drain():
            # 同步迭代 stream（网络在此发生），把片段塞进队列
            try:
                for chunk in stream:
                    for kind, val in parse(chunk):
                        queue.put_nowait((kind, val))
                for kind, val in parse.finish():
                    queue.put_nowait((kind, val))
                queue.put_nowait((_END, None))
            except Exception as e:  # noqa: BLE001
                queue.put_nowait((_ERR, e))
            finally:
                # 必须关：本线程可能正被看门狗放弃着 —— 那种情况下调用方早已不再读队列，
                #   但**线程还在跑**（Python 杀不掉线程），它会一直阻塞到 httpx 自己的 read timeout
                #   到点为止。不显式 close 的话，底层连接要拖到 GC 才归还连接池。
                #   被放弃时向 queue 写入只是塞进一个无人消费的本地对象，无副作用。
                _close_quietly(stream)

        task = asyncio.get_running_loop().run_in_executor(None, _drain)
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_deltas: Dict[int, Dict[str, Any]] = {}
        stream_usage: Optional[Dict[str, Any]] = None  # 尾巴用量（DSH 早样本，同尝试只留最后一份）
        first_seen = False                             # 首个内容块是否已到 → 决定用哪个看门狗
        while True:
            # ── 存活看门狗：只问"多久没有新内容"，不问"已经过了多久" ──
            # first_seen=False 时量的是开流→首块（含连接/排队/响应头/预填充），
            # 之后量的是"相邻两块之间"。两者都由 `_watchdog_seconds` 与可选绝对上限取 min。
            watch = self._watchdog_seconds(
                self.idle_timeout if first_seen else self.timeout, budget_deadline)
            try:
                kind, val = await self._guarded(
                    queue.get(), watch,
                    "输出停顿超时：模型说到一半卡住了" if first_seen
                    else "首字超时：模型一直没开口")
            except TimeoutError:
                # 尽力把还在阻塞的读线程叫醒（幂等、可重入，失败无副作用）：
                # 不 close 的话它要等 `_httpx_read_timeout()` 那道背板才退出。
                _close_quietly(stream)
                raise
            first_seen = True
            if kind is _END:
                break
            if kind is _ERR:
                await task
                raise val
            if kind == "usage":
                if isinstance(val, dict):
                    stream_usage = val
                continue
            if kind == "think":
                # 思考流：回调 on_reasoning（不传则仅累积进 LlmResult，不影响现有调用方）
                reasoning_parts.append(val)
                if stats is not None:
                    stats["tokens"] = stats.get("tokens", 0) + len(val)
                if on_reasoning is not None:
                    _r = on_reasoning(val)
                    if asyncio.iscoroutine(_r):
                        await _r
            elif kind == "text":
                text_parts.append(val)
                if stats is not None:
                    stats["tokens"] = stats.get("tokens", 0) + len(val)
                _r = on_token(val)
                if asyncio.iscoroutine(_r):
                    await _r
            else:  # tool
                for tc in val:
                    if isinstance(tc, dict):
                        # index 防御（10-16）：Gemini OpenAI 兼容层流式 delta 是 "index": null
                        #（键存在、值为 None）—— .get(key, 0) 的默认值只管"键不存在"，
                        # int(None) 直接炸（症状：调工具的回合永远失败，纯文本回合正常）。
                        # isinstance 守卫：None/脏值兜底 0，真 int（OpenAI 分片序号）原样。
                        raw = tc.get("index")
                        idx = int(raw) if isinstance(raw, int) else 0
                        entry = tool_deltas.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            entry["name"] = fn["name"]
                        entry["arguments"] += fn.get("arguments") or ""
                    else:
                        # 同上（对象形态）：getattr 默认值只管"属性不存在"，属性存在但为 None 同样炸
                        raw = getattr(tc, "index", None)
                        idx = int(raw) if isinstance(raw, int) else 0
                        entry = tool_deltas.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                        if getattr(tc, "id", None):
                            entry["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                entry["name"] = fn.name
                            entry["arguments"] += getattr(fn, "arguments", "") or ""
        await task
        tool_calls = llm_adapter.normalize_openai_tool_calls(
            [
                {"id": v["id"], "function": {"name": v["name"], "arguments": v["arguments"]}}
                for v in tool_deltas.values()
            ]
        )
        return LlmResult(text="".join(text_parts), reasoning="".join(reasoning_parts),
                         tool_calls=tool_calls, raw=None, usage=stream_usage,
                         provider=self.provider, model=self.model)

    async def generate(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        on_reasoning: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> "LlmResult":
        # 图片能力判定：请求带图但模型不支持 → 明确报错，不静默丢图
        if self.supports_image is False:
            has_img = any(
                isinstance(m, dict) and llm_adapter.content_has_image(m.get("content"))
                for m in messages
            )
            if has_img:
                raise RuntimeError(
                    f"模型 {self.model} 不支持图片输入（supports_image=False），"
                    "但本次请求携带了图片。请改用支持多模态的模型，或移除图片。"
                )
        wire_system, wire_messages, wire_tools = self._wire_request(system, messages, tools)

        # 可靠性重试：只重试「可重试」错误；流式若已对外推送过 token 则不再重试
        # （重发会让玩家看到同一内容两遍）；序列化只做一次（不因重试反复）。
        # 可观测：重试过程**曾经完全静默**，是"卡住但无报错"的典型来源，现全程留痕。
        last_err: Optional[Exception] = None
        total_attempts = self.retries + 1
        started = monotonic()                       # 可选绝对上限的锚（默认不设上限，用不到）
        # 本趟走流式还是非流式，决定了用哪套上限（两者量纲不同，见 __init__ 注释）
        streaming = on_token is not None or on_reasoning is not None
        log.debug("LLM 请求开始：model=%s messages=%d tools=%d retries=%d 流式=%s "
                  "首字看门狗=%s 停顿看门狗=%s 绝对上限=%s",
                  self.model, len(messages), len(tools or []), self.retries, streaming,
                  f"{self.timeout}s" if self.timeout else "关",
                  f"{self.idle_timeout}s" if self.idle_timeout else "关",
                  f"{self.total_budget}s" if (streaming and self.total_budget) else "关（默认）")
        for attempt in range(total_attempts):
            stats: Dict[str, int] = {"tokens": 0}   # 本次尝试的输出统计（局部，防并发串台）
            # ── 可选绝对上限（默认关）：用户显式设了才在这里拦，并把它传给 _call_stream ──
            # 传下去的形式是"到期时刻"，由 `_watchdog_seconds` 收缩两个看门狗 ——
            # 于是"总耗时 ≤ total_budget"在设了上限时仍成立，而没设时**不存在任何整趟判据**。
            budget_deadline = self._budget_deadline(started, streaming)
            if budget_deadline is not None and budget_deadline - monotonic() <= 0:
                log.error("LLM 绝对上限 %.1fs 已耗尽（已尝试 %d/%d 次）→ 放弃：%s",
                          self.total_budget, attempt, total_attempts, last_err)
                raise last_err if last_err is not None else TimeoutError(
                    f"LLM 绝对上限 {self.total_budget}s 耗尽且无更早的错误")
            try:
                if streaming:
                    # ★ 流式不再有"整趟挂钟"★：这一步不带 wait_for，上限全在 _call_stream
                    #   的两个存活看门狗里（首字 / 停顿），它们只认"有没有新内容"。
                    return await self._call_stream(
                        wire_system, wire_messages, wire_tools, on_token or _noop, stats,
                        on_reasoning=on_reasoning, budget_deadline=budget_deadline)
                # 非流式（唯一调用方 = 上下文压缩）：**没有"中途"可言**（一个字节都不发，
                # 要么整段回来要么没有），所以它只有"首字"这一种语义 —— 上界取
                # timeout_nonstream，与流式首字看门狗同构；httpx 的 read timeout 仍是背板。
                # ★ total_budget 不作用于它 ★（见 _budget_deadline）：拿玩家等待上限去卡
                # 后台压缩只会静默废掉压缩。
                resp = await self._guarded(
                    asyncio.to_thread(self._call, wire_system, wire_messages, wire_tools),
                    self._watchdog_seconds(self.timeout_nonstream, budget_deadline),
                    "首字超时：模型一直没回应")
                text, tool_calls, reasoning = self._parse_response(resp)
                return LlmResult(text=text or "", reasoning=reasoning, tool_calls=tool_calls,
                                 raw=resp, usage=self._extract_response_usage(resp),
                                 provider=self.provider, model=self.model)
            except Exception as e:
                last_err = e
                # 先把「事实」全部打进日志，再决策
                # 旧实现把"不可重试"判在"已推送 token"之前并立刻 raise，于是那次事故的日志里
                # **永远看不到 token 数** —— 而它恰恰是决定"能不能重试"的那个变量。
                # 判据顺序可以不变（分类本来就是第一优先级），但事实必须无条件留痕。
                pushed = int(stats.get("tokens", 0) or 0)
                retryable = self._is_retryable(e)
                spent = monotonic() - started
                log.error("LLM 第 %d/%d 次尝试失败：%s：%s ｜ 已推送 token=%d 已耗时=%.1fs "
                          "可重试=%s 分类=%s",
                          attempt + 1, total_attempts, type(e).__name__, e, pushed, spent,
                          retryable, "传输层/网络" if retryable else "认证/格式/其他")
                if not retryable:
                    raise                              # 认证/格式类错误：立即抛，重试无意义
                if pushed > 0:
                    # 流式已推送过 token：不重试（防重复展示）。超时同理 ——
                    # "吐了一半才超时"重发会把同一段内容演两遍；"一个字没吐就超时"才该重试。
                    log.warning("LLM 流式中断且已推送过 %d 个 token，不重试（防重复展示）：%s",
                                pushed, e)
                    raise
                if attempt < self.retries:
                    delay = self._backoff(attempt)
                    log.warning("LLM 第 %d/%d 次尝试失败（可重试，%s）：%s —— %.1fs 后重试",
                                attempt + 1, total_attempts, type(e).__name__, e, delay)
                    await asyncio.sleep(delay)
                else:
                    log.error("LLM 全部 %d 次尝试均失败，放弃：%s", total_attempts, e)
        raise last_err if last_err is not None else RuntimeError("llm generate failed with no error")


async def _noop(_token: str) -> None:
    """只传 on_reasoning 不传 on_token 时的正文丢弃出口（保持 generate 单口语义）。"""


def _close_quietly(stream: Any) -> None:
    """尽力关掉底层流：幂等、绝不抛。

    两处用它：① `_drain` 的 finally（正常/异常收尾都还连接）；
    ② 看门狗放弃一趟时由主协程调用 —— 读线程此刻正阻塞在 socket 上（Python 杀不掉线程），
    close 是唯一能立刻把它叫醒的手段（否则要等 httpx 的背板 read timeout 到点）。
    两个线程同时 close 同一响应是安全的：httpx/httpcore 的 close 幂等，重复调用无害。
    """
    try:
        stream.close()
    except Exception:  # noqa: BLE001
        pass