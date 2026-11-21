"""llm/_http — OpenAI/Anthropic 两条协议共用的 HTTP + SSE 传输层

为什么单独一个模块：`/chat/completions`、`/messages`、`/responses` 三条路在**传输层完全同构** ——
都是一个 JSON POST、都是 `event:`/`data:` 的 SSE、都要"看门狗先响、httpx 后响"、都要在
错误分支**先把 body 读全再退上下文**（不读就退出 = 连接不还池，5xx 风暴里会把 fd 吃光）。
差异只有三处：路径、鉴权头、协议版本头。把这三处做成参数，剩下的只写一份 ——
否则 09-14/09-17/09-18 那几次传输层修复以后要修三遍，而**一定会**只修一两遍。

分层：本模块只管"怎么把一发请求送出去、怎么把 SSE 行读回来"，不认识任何 wire 语义
（那归 llm_adapter 与各协议的 client）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# 连接/写入给固定值：它们量的是"握手有多慢"，与看门狗要判的"模型有没有出声"不是一回事 ——
# 混进一个数字里必然一边太紧一边太松（见 openai_client.__init__ 的长注）。
_CONNECT_TIMEOUT = 15.0
_WRITE_TIMEOUT = 60.0
_POOL_TIMEOUT = 15.0


class LlmHttpError(Exception):
    """带 `status_code` / `body` 的 HTTP 错误（形状**刻意对齐** openai SDK 的 APIStatusError）。

    对齐的收益是上游一行都不用改：
      · `OpenAILlmClient._is_retryable` 按 `status_code in (408,429,5xx)` 判可重试；
      · `llm/probe.classify` 按 `status_code` 归类（401→auth / 404→not_found / 5xx→server）；
      · `llm/probe._extract_message` 读 `body["error"]["message"]`（三家网关的错误体同形）。
    `response` 只需提供 `.text`（probe 抽原文时的第二顺位）。

    各协议各自派生一个**同名有别的子类**（AnthropicHttpError / ResponsesHttpError）：
    行为完全一致，只为了让日志里那一行 `type(e).__name__` 直接说出是哪条协议挂了。
    """

    def __init__(self, status_code: int, body: Any, message: str, response: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.response = response


class TextResponse:
    """极简响应壳：只给 `probe` 抽原文用的 `.text`（避免把 httpx 类型泄漏到上游）。"""

    def __init__(self, text: str):
        self.text = text


class SseStream:
    """httpx 流式上下文 → 「可迭代 + 可 close」的对象（骨架只认这两件事）。

    为什么要包一层：httpx 的流式只能这样开 —— `with client.stream("POST", ...) as resp:`，
    而 `Client.post(..., stream=True)` 是**不存在**的参数（实测直接 TypeError）。骨架那边却是
    "开流（线程里）→ 稍后迭代 → 看门狗到点就 close"，横跨两个线程、没有 with 的位置，
    故把上下文收成对象，`close()` 同时做两件事：关响应（还连接回池）+ 退出上下文。

    `__iter__` 给每行**补回换行符**：`iter_lines()` 吐的是剥掉行尾的行，而解析器认的是
    "原始 SSE 文本块"（这样才能同时吃下"一次一个字节"和"一次一整块"两种喂法）。
    """

    def __init__(self, cm: Any, resp: Any):
        self._cm = cm
        self._resp = resp

    def __iter__(self):
        for line in self._resp.iter_lines():
            yield line + "\n"

    def close(self) -> None:
        try:
            self._cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 —— close 是尽力而为，绝不抛（骨架的约定）
            pass


def raise_for_status(resp: Any, error_cls: type) -> None:
    """≥400 → `error_cls(status_code, body, message, response)`；<400 直接返回。

    错误体的三种真实形态都要认（实测自三家网关）：
      · `{"type":"error","error":{"type":"X","message":"..."}}`  —— Anthropic / opencode
      · `{"error":{"message":"..."}}`                            —— OpenAI 兼容
      · `{"model":"m","error":{"message":"..."}}`                —— opencode 的 /responses
    取不到 message 就退回 HTTP 状态行 + 原文，绝不返回空串（空串会让面板显示一片空白）。
    """
    code = int(getattr(resp, "status_code", 0) or 0)
    if code < 400:
        return
    text = ""
    try:
        text = resp.text or ""
    except Exception:
        text = ""
    body: Any = None
    try:
        body = resp.json()
    except Exception:
        body = None
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("msg") or "")
        elif isinstance(err, str):
            msg = err
        if not msg:
            msg = str(body.get("message") or "")
    if not msg:
        msg = text.strip() or f"HTTP {code}"
    raise error_cls(code, body, msg, response=TextResponse(text))


class HttpxSseTransport:
    """一发 JSON POST（流式/非流式同一条路径）。

    差异全在构造参数里：`path`（/chat/completions|/messages|/responses）、`auth_headers`
    （Bearer 还是 x-api-key）、`extra_headers`（如 anthropic-version）。

    背板 read timeout 由本对象按流式/非流式各取一套：流式取两个看门狗之和（保证"看门狗先响、
    httpx 后响"，报错文案才说得出人话），非流式取 timeout_nonstream 的两倍（非流式一个字节
    都不发，read 就在量整段生成时长）。`None` = 不设（与"看门狗关掉"同义）。
    """

    def __init__(self, base_url: str, path: str, auth_headers: Dict[str, str],
                 extra_headers: Optional[Dict[str, str]], streaming_read: Optional[float],
                 nonstream_read: Optional[float], error_cls: type):
        self._base = (base_url or "").rstrip("/")
        self._path = path
        self._auth = dict(auth_headers or {})
        self._extra = dict(extra_headers or {})
        self._streaming_read = streaming_read
        self._nonstream_read = nonstream_read
        self._error_cls = error_cls
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=httpx.Timeout(connect=_CONNECT_TIMEOUT, read=None,
                                                              write=_WRITE_TIMEOUT,
                                                              pool=_POOL_TIMEOUT))
        return self._client

    def url(self) -> str:
        """base_url 允许带或不带版本段：已含则直接拼路径，否则补一段。

        为什么不硬要求用户写全：面板里那一行本来就是给 OpenAI 兼容地址用的，
        让"同一行 base_url 换个模型就能用"成立，比让用户改地址重要。
        """
        base, path = self._base, self._path
        if base.endswith(path):
            return base
        if base.rsplit("/", 1)[-1].startswith("v") and base.rsplit("/", 1)[-1][1:].isdigit():
            return base + path
        return base + "/v1" + path

    def headers(self) -> Dict[str, str]:
        h = {"content-type": "application/json"}
        h.update(self._auth)
        h.update(self._extra)
        return h

    def _timeout(self, read: Optional[float]) -> Any:
        import httpx

        return httpx.Timeout(connect=_CONNECT_TIMEOUT, read=read,
                             write=_WRITE_TIMEOUT, pool=_POOL_TIMEOUT)

    def post(self, body: Dict[str, Any], stream: bool) -> Any:
        """发一发。`stream=True` → `SseStream`；否则 → 已读全的 httpx.Response（可直接 .json()）。"""
        import httpx

        read = self._streaming_read if stream else self._nonstream_read
        try:
            client = self._get_client()
            if stream:
                cm = client.stream("POST", self.url(), json=body, headers=self.headers(),
                                   timeout=self._timeout(read))
                resp = cm.__enter__()
                if int(getattr(resp, "status_code", 0) or 0) >= 400:
                    # 错误体必须**先读全**：流式响应不读就退出上下文，连接不还池，
                    # 下一次请求会新建连接 —— 网关侧 5xx 风暴时这一点会把 fd 吃光。
                    try:
                        resp.read()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        raise_for_status(resp, self._error_cls)
                    finally:
                        try:
                            cm.__exit__(None, None, None)
                        except Exception:  # noqa: BLE001
                            pass
                return SseStream(cm, resp)
            resp = client.post(self.url(), json=body, headers=self.headers(),
                               timeout=self._timeout(read))
            raise_for_status(resp, self._error_cls)
            return resp
        except httpx.TransportError:
            # 传输层异常原样上抛：两条判据（_is_retryable / probe.classify）都按 httpx 的
            # 类名与类树认得它们，包一层反而认不出。
            raise
