"""llm/openai_client — OpenAI 兼容 adapter：Canonical→wire→HTTP→wire→Canonical（迁移原 _step_via_openai）

可靠性（网络类错误自动重试，generate 幂等）：
- 只重试「可重试」错误：网络层 + HTTP 408/429/5xx；认证/格式类错误（400/401/403 等）
  立即抛出，重试无意义。
- 网络层的判据**必须包含 httpcore/httpx/openai 自己的异常树**：它们不继承 OSError 家族
  （见 _RETRYABLE_TRANSPORT_ERRORS 的注释），只枚举 Python 内建会漏掉整整一族传输层错误。
- 指数退避：0.5s / 1s / 2s，封顶 4s（config llm.retries 控制总次数，0=不重试）。
- 流式约束：一旦已对外推送过 token 则不重试（重发会让玩家看到同一内容两遍）；开流即断
  （零产出）等价未开始，可重试。
- 等待有上界：单次上限 llm.timeout（httpx read timeout，**每两块数据之间**的闸）+ 总预算
  llm.total_budget（挂钟硬顶）。两者互补：前者会被对端 SSE 心跳无限续命，后者不会。
"""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .. import llm_adapter, log_setup
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


class OpenAILlmClient:
    """持 base_url/api_key/model + 现成 client，统一 generate 出口，内部做 wire 互译"""

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
        total_budget: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.provider = provider
        # 可靠性：网络/瞬时错误重试次数（总尝试 = retries + 1，0 表示不重试）
        self.retries = max(0, retries)
        # 单次请求超时（秒）。None = 不传给 SDK，保持其内建默认（1.x 为 600s）——
        # 这是"卡住但零报错"的静默窗口来源，故由 config llm.timeout 显式收紧（默认 45s）。
        # 语义澄清：httpx 的 read timeout 是**相邻两个数据块之间**的上限，不是整个请求的
        #   上限 —— 每收到一块就重置。所以长回复只要在持续吐字就永远不会被它砍掉，
        #   而"卡住不动"会被立刻发现。实测 TTFT 5~22s，45s 有 2 倍余量。
        self.timeout = timeout
        # 非流式单次上限（秒）。**必须与流式分开**：非流式请求在整段生成完之前一个字节都不
        # 发，read timeout 于是直接量"完整生成时长"（含首 token），量纲完全不同。
        # 唯一调用方是回合边界的上下文压缩（compaction/compress.py:261，不传 on_token）——
        # 那是一发大 prompt（阈值 = ctx_window×0.8，默认 32768×0.8≈26k token），
        # 用 45s 去卡它会**静默废掉压缩**（压缩失败是软失败，只落一条 warning，回合照常继续）。
        # 未显式配置时回落 timeout，保证"只设了 timeout 的用户"两条路径都仍有上界。
        self.timeout_nonstream = timeout_nonstream if timeout_nonstream is not None else timeout
        # 整个 generate（含全部重试尝试）的挂钟硬顶（秒）。None = 不设顶（回到旧行为）。
        # 为什么光有 timeout + retries 不够：对端若定期发 SSE 心跳/注释，每个字节都会重置
        #   httpx 的 read 计时器，timeout 于是**永远不触发** —— 等待时间再次失去上界。
        #   本预算不看字节流，只看挂钟，是"等待时间减少"这个目标的最终保证。
        self.total_budget = total_budget
        # 附加请求头（llm.headers）。有些网关要求客户端自报家门，例如 OpenCode Go 要求
        # `x-opencode-session`（稳定会话 ID，供其路由与 prompt 缓存）+ 自定义 User-Agent
        # （用 SDK 默认 UA 会被其前置 Cloudflare 判为机器人 → 403 error code 1010）。
        self.headers = dict(headers) if headers else None
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
        # 客户端级 timeout 供**非流式**路径（_call）使用；流式路径在 _open_stream 里按请求
        # 单独下发更短的值（两者量纲不同，见 __init__ 注释）。
        try:
            if self.timeout_nonstream is not None:
                kwargs["timeout"] = float(self.timeout_nonstream)
        except (TypeError, ValueError):
            pass
        # 无条件关掉 SDK 自带的隐式重试
        # 本层已有受控重试（self.retries），两层叠加会让"最坏等待"不可预期（SDK 默认还会
        # 自己重试 2 次，玩家等的时间就被乘了三倍）。关掉之后，"一次请求最多等多少秒"
        # 完全由 timeout / total_budget 决定 —— 这正是"等待时间可算"的前提。
        kwargs["max_retries"] = 0
        # 附加头（含自定义 User-Agent / 会话 ID）；SDK 会默认发 OpenAI/Python-x.y 的 UA，
        # 某些网关的前置 Cloudflare 会因此拦截（403 error code 1010），故允许覆盖。
        if self.headers:
            kwargs["default_headers"] = dict(self.headers)
        return OpenAI(**kwargs)

    def _call(self, oai_messages: List[Dict[str, Any]], oai_tools: Optional[List[Dict[str, Any]]]):
        client = self._get_client()
        # openai 1.x：client.chat.completions.create
        if hasattr(client, "chat") and hasattr(client.chat, "completions"):
            return client.chat.completions.create(
                model=self.model,
                messages=oai_messages,
                tools=oai_tools,
                tool_choice="auto" if oai_tools else None,
            )
        if hasattr(client, "chat_completions_create"):
            return client.chat_completions_create(messages=oai_messages, tools=oai_tools)
        # 兜底：可调用对象
        return client(oai_messages, oai_tools)

    def _open_stream(self, oai_messages: List[Dict[str, Any]], oai_tools: Optional[List[Dict[str, Any]]]):
        """打开 OpenAI 兼容流（返回同步可迭代 Stream；真正网络在迭代时发生）。

        附带 stream_options.include_usage=True 索取尾巴用量（DSH 早样本）；
        网关/旧桩不支持该参数时回退为普通流（用量缺失，统计跳过）。

        按请求下发更短的 timeout（self.timeout，流式语义 = 相邻两块之间的间隔）；
        不覆盖客户端级的话，流式会沿用为"非流式大摘要"准备的宽松值（见 __init__ 注释）。
        """
        client = self._get_client()
        extra: Dict[str, Any] = {}
        try:
            if self.timeout is not None:
                extra["timeout"] = float(self.timeout)
        except (TypeError, ValueError):
            extra = {}
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

    def _remaining_budget(self, started: float, streaming: bool = True) -> Optional[float]:
        """距总预算耗尽还剩多少秒；未设预算（None）返回 None = 不设顶。

        预算**只作用于流式（聊天）路径** —— 它表达的是"玩家在等的这一回合最久等多久"。
        非流式的唯一调用方是上下文压缩：那是后台维护动作，失败是软失败（回合照常继续，
        只是这轮不压缩），拿玩家等待预算去卡它，结果只会是"大摘要永远做不完"。
        非流式仍有上界，由 timeout_nonstream × (retries+1) 自然给出。
        """
        if not streaming or self.total_budget is None:
            return None
        try:
            return float(self.total_budget) - (monotonic() - started)
        except (TypeError, ValueError):
            return None

    def _attempt_deadline(self, remaining: Optional[float], streaming: bool = True) -> Optional[float]:
        """这一趟尝试的挂钟上限 = min(该路径的单次上限, 剩余预算)；两者都可能缺省。

        取 min 而不是只取单次上限，是为了让"总耗时 ≤ total_budget"成为硬保证：
        越接近预算末端，允许的单次时长会自动收缩，不会在最后一趟冲破预算。
        """
        caps: List[float] = []
        for c in (self.timeout if streaming else self.timeout_nonstream, remaining):
            if c is None:
                continue
            try:
                caps.append(float(c))
            except (TypeError, ValueError):
                continue
        return min(caps) if caps else None

    async def _with_deadline(self, coro: Any, deadline: Optional[float], what: str) -> Any:
        """给一趟尝试套挂钟硬顶；deadline=None 时原样 await（零行为变更）。

        超时抛**内建 TimeoutError**（`_is_retryable` 认得），而不是让 asyncio 的取消语义
        外泄 —— 调用方按"网络类错误"统一处理即可。

        ★ 被放弃的底层工作不会被杀死（Python 杀不掉线程）：流式路径的 _drain 线程会继续
          阻塞，直到 httpx 自己的 read timeout 到点，再由它的 finally 关掉响应。
          这正是"单次上限（timeout）必须与总预算一起设"的原因 —— 只设总预算会留下
          悬挂的读取线程，只设单次上限则会被对端心跳无限续命。
        """
        if deadline is None:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=deadline)
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                f"{what} 超过 {deadline:.1f}s 未完成（挂钟硬顶；对端心跳无法续命）") from e

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
        oai_messages: List[Dict[str, Any]],
        oai_tools: Optional[List[Dict[str, Any]]],
        on_token: Callable[[str], Awaitable[None]],
        stats: Optional[Dict[str, int]] = None,
        on_reasoning: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> "LlmResult":
        """流式调用：后台线程迭代 stream → 队列 → 主协程逐 token 回调 + 累积结果。

        stats：本次尝试的输出统计（{"tokens": int}），供 generate 判定"是否已对外推送过
        token"（已推送则不重试）。必须用局部 dict 传入——max_concurrent_turns>1 时
        多个回合共享同一 LlmClient 实例，实例级字段会串台。
        """
        stream = self._open_stream(oai_messages, oai_tools)
        if stream is None:
            # 兜底可调用对象不支持流式：退非流式，整段回调一次
            resp = await asyncio.to_thread(self._call, oai_messages, oai_tools)
            text, tool_calls = llm_adapter.parse_openai_response(resp)
            # 思考内容：reasoning_content 字段（首选）+ content 内嵌标签拆出（兜底）
            reasoning = self._extract_reasoning(resp)
            emb, text = _think.split_think(text)
            if emb:
                reasoning = (reasoning + "\n" + emb).strip() if reasoning else emb
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
        splitter = _think.ThinkSplitter()   # content 内嵌 <think> 标签兜底拆分（无标签时原样过 body）

        def _emit(part: str, chunk: str):
            queue.put_nowait((part, chunk))

        def _drain():
            # 同步迭代 stream（网络在此发生），把片段塞进队列
            try:
                for chunk in stream:
                    # 尾巴用量（DSH 早样本）：附在 finish 块或用量专块，随 [DONE] 前到达
                    try:
                        cu = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
                    except Exception:
                        cu = None
                    if cu is not None:
                        plain = OpenAILlmClient._usage_to_plain(cu)
                        if plain is not None:
                            queue.put_nowait(("usage", plain))
                    choices = getattr(chunk, "choices", None) if not isinstance(chunk, dict) else chunk.get("choices")
                    if not choices:
                        continue
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
                        _emit("think", reasoning)
                    if content:
                        # 来源②：content 内嵌 <think> 标签拆分（无标签则原样走 body）
                        splitter.feed(content, on_body=lambda s: _emit("text", s),
                                      on_think=lambda s: _emit("think", s))
                    if tool_calls_delta:
                        queue.put_nowait(("tool", tool_calls_delta))
                splitter.flush(on_body=lambda s: _emit("text", s), on_think=lambda s: _emit("think", s))
                queue.put_nowait((_END, None))
            except Exception as e:  # noqa: BLE001
                queue.put_nowait((_ERR, e))
            finally:
                # 必须关：本线程可能正被"总预算硬顶"放弃着 —— 那种情况下调用方早已不再读队列，
                #   但**线程还在跑**（Python 杀不掉线程），它会一直阻塞到 httpx 自己的 read timeout
                #   到点为止。不显式 close 的话，底层连接要拖到 GC 才归还连接池。
                #   被放弃时向 queue 写入只是塞进一个无人消费的本地对象，无副作用。
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

        task = asyncio.get_running_loop().run_in_executor(None, _drain)
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_deltas: Dict[int, Dict[str, Any]] = {}
        stream_usage: Optional[Dict[str, Any]] = None  # 尾巴用量（DSH 早样本，同尝试只留最后一份）
        while True:
            kind, val = await queue.get()
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
                        idx = int(tc.get("index", 0))
                        entry = tool_deltas.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            entry["name"] = fn["name"]
                        entry["arguments"] += fn.get("arguments") or ""
                    else:
                        idx = int(getattr(tc, "index", 0))
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
        oai_messages, oai_tools = llm_adapter.canonical_to_openai(system, messages, tools)

        # 可靠性重试：只重试「可重试」错误；流式若已对外推送过 token 则不再重试
        # （重发会让玩家看到同一内容两遍）；序列化只做一次（不因重试反复）。
        # 可观测：重试过程**曾经完全静默**，是"卡住但无报错"的典型来源，现全程留痕。
        last_err: Optional[Exception] = None
        total_attempts = self.retries + 1
        started = monotonic()                       # 挂钟起点 = 总预算的锚
        # 本趟走流式还是非流式，决定了用哪套上限（两者量纲不同，见 __init__ 注释）
        streaming = on_token is not None or on_reasoning is not None
        log.debug("LLM 请求开始：model=%s messages=%d tools=%d retries=%d 流式=%s "
                  "timeout=%s budget=%s",
                  self.model, len(messages), len(tools or []), self.retries, streaming,
                  f"{self.timeout if streaming else self.timeout_nonstream}s"
                  if (self.timeout if streaming else self.timeout_nonstream) is not None
                  else "SDK默认(约600s)",
                  f"{self.total_budget}s" if (streaming and self.total_budget is not None) else "无上限")
        for attempt in range(total_attempts):
            stats: Dict[str, int] = {"tokens": 0}   # 本次尝试的输出统计（局部，防并发串台）
            # ── 总预算收缩：这一趟最多只给「剩余预算」与「单次上限」中更小的那个 ──
            # 预算检查放在尝试**之前**，且把单次上限也压到剩余预算以内，
            # 于是"整个 generate 的挂钟耗时 ≤ total_budget"成为硬保证（不是近似）。
            remaining = self._remaining_budget(started, streaming)
            if remaining is not None and remaining <= 0:
                log.error("LLM 总预算 %.1fs 已耗尽（已尝试 %d/%d 次）→ 放弃：%s",
                          self.total_budget, attempt, total_attempts, last_err)
                raise last_err if last_err is not None else TimeoutError(
                    f"LLM 总预算 {self.total_budget}s 耗尽且无更早的错误")
            try:
                deadline = self._attempt_deadline(remaining, streaming)
                if streaming:
                    return await self._with_deadline(
                        self._call_stream(oai_messages, oai_tools, on_token or _noop, stats,
                                          on_reasoning=on_reasoning),
                        deadline, f"流式请求（第 {attempt + 1}/{total_attempts} 次）")
                resp = await self._with_deadline(
                    asyncio.to_thread(self._call, oai_messages, oai_tools),
                    deadline, f"非流式请求（第 {attempt + 1}/{total_attempts} 次）")
                text, tool_calls = llm_adapter.parse_openai_response(resp)
                reasoning = self._extract_reasoning(resp)
                emb, text = _think.split_think(text)
                if emb:
                    reasoning = (reasoning + "\n" + emb).strip() if reasoning else emb
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