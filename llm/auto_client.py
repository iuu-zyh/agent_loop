"""llm/auto_client — 协议自动识别：同一行 base_url，换个模型名就能用

要解决的问题（09-18 实机）：
    OpenCode Zen/Go 把模型分挂在三条协议路上（`/chat/completions` OpenAI 兼容、
    `/messages` Anthropic、`/responses` OpenAI Responses）。用户面板里只有一行**模型名**，
    填了 `union-alpha`（只在 /messages 上）就会得到一句"对方网关 5xx" —— 他没有任何线索
    知道自己填的模型和程序说的协议不是一回事。要求用户先读网关文档、再手配 `llm.protocol`，
    等于把这个坑转嫁给玩家。

三条路的识别靠网关**自己报出来的格式名**（实测 opencode）：
    /chat/completions → `Model union-alpha is not supported for format oa-compat`
                        （union-alpha 这种连格式名都不报的，回裸 500 —— 也认）
    /messages         → `Model grok-4.6 is not supported for format anthropic`
    /responses        → 命中即成功
所以顺序是 `[openai, anthropic, responses]`：openai 打头（历史行为，绝大多数网关就是它），
anthropic 次之（该网关 9 个模型挂在这条），responses 最后（4 个）。

本模块的做法：**先用配置声明的协议打一发，失败了再拿另一条协议试一次**，成功就锁定。
    为什么不查"模型名 → 协议"的内置表：那张表**只会在网关加模型的那天开始说谎**，
    而说谎的方式恰恰是本仓最忌讳的形态（看起来能跑、实际打到了错的路）。真打一发得到的
    是**当时的事实**，且代价只有一次请求。

三条安全边界（缺一不可）：
    ① **只在"一个 token 都没推出去"时切换**：中途换协议重发会把同一段话演两遍。
       靠包装 on_token/on_reasoning 计数得知，而不是猜。
    ② **只认"协议不对"的签名**才切：网关明说 `oa-compat` / `not supported for format`，
       或回那个"模型没挂在这条路上"的裸 500 `{"type":"error","error":{"type":"error",
       "message":"Internal server error"}}`（实测 opencode 对 union-alpha 就是这个）。
       网络超时/断连一律不切 —— 那与协议无关，切了只会把等待翻倍。
    ③ **判定失败后不再反复试**：一次识别失败后进入"存疑"态，直到主协议有一次成功调用
       才解除。否则一个两条路都不通的模型会让**每一次**对话都白发两发请求。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from .. import log_setup
from .base import LlmResult

log = log_setup.get_logger(__name__)

# 网关明说"这条路不为这个模型服务"的措辞（实测自 opencode zen/go）：
#   grok-4.6   → 401 {"type":"error","error":{"type":"ModelError",
#                     "message":"Model grok-4.6 is not supported for format oa-compat"}}
#   union-alpha→ 500 {"type":"error","error":{"type":"error","message":"Internal server error"}}
#                 （同一个模型在 /messages 上是 200 —— 它只是没挂在这条路上）
_FORMAT_HINTS = ("oa-compat", "not supported for format", "unsupported format",
                 "is not supported for format")
_BARE_500_HINT = "internal server error"

# "这条错误会自己好吗"：408/429/5xx 与一切无状态码的网络层错误都算瞬时（过会儿再试有意义）；
# 其余 4xx 是配置层面的定性失败。**只用来决定要不要保留"下次再试探"的机会**，不参与切换判定。
_TRANSIENT_STATUS = (408, 429, 500, 502, 503, 504)


def _transient(e: BaseException) -> bool:
    """瞬时失败？（无状态码 = 网络层/超时，也算瞬时 —— 明天可能就好了）"""
    status = getattr(e, "status_code", None)
    if not isinstance(status, int):
        return True
    return status in _TRANSIENT_STATUS


def _error_text(e: BaseException) -> str:
    """尽量凑出"对方到底说了什么"的全文（body 优先，其次 str(e)）—— 判据与证据同源。"""
    body = getattr(e, "body", None)
    parts: List[str] = []
    if body is not None:
        try:
            import json as _json

            parts.append(_json.dumps(body, ensure_ascii=False))
        except Exception:
            parts.append(str(body))
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            txt = getattr(resp, "text", "")
            if txt:
                parts.append(str(txt))
        except Exception:
            pass
    parts.append(str(e))
    return " ".join(p for p in parts if p)


def protocol_mismatch(e: BaseException) -> bool:
    """这条错误像不像"模型不挂在本协议上"？只在这里放行协议切换。

    判据刻意很窄（宁可漏切一次、也不要为了"聪明"把真故障当成协议问题）：
      · 必须有 HTTP 状态码 —— 网络层异常没有 status，那类问题与协议无关；
      · 状态码必须是"这条路不认这个请求"那一类（400/401/403/404/405/415/422/500/501）；
      · 正文必须命中上面两类措辞之一。
    """
    status = getattr(e, "status_code", None)
    if not isinstance(status, int):
        return False
    if status not in (400, 401, 403, 404, 405, 415, 422, 500, 501):
        return False
    low = _error_text(e).lower()
    if any(h in low for h in _FORMAT_HINTS):
        return True
    # 裸 500：必须是"就这一句 Internal server error"的形态（别的 500 文案可能有意义）
    return status == 500 and _BARE_500_HINT in low and len(low) < 400


class AutoProtocolLlmClient:
    """协议自动识别门面：对外就是一个 LlmClient（router/agent 全都不知它存在）。

    `clients` 是**有序**候选表：第一个是主协议，其余按试的顺序排在后面。
    """

    def __init__(self, clients: List[Any], primary_name: Optional[str] = None):
        clients = [c for c in (clients or []) if c is not None]
        if len(clients) < 2:
            raise ValueError("AutoProtocolLlmClient 至少需要两条协议的内芯")
        self._clients = clients
        self._primary_name = primary_name or getattr(clients[0], "protocol_name", "primary")
        self._locked: Optional[str] = None
        self._suspect = False          # 上次识别失败 → 存疑（下一次主协议成功即解除）

    # ---------- 诊断信息 ----------
    @property
    def protocol_name(self) -> str:
        """当前实际在用的协议（未定时为 "auto"）—— 供日志/探针/面板上报。"""
        return self._locked or "auto"

    @property
    def client(self) -> Any:
        """当前内芯（仅供装配层审计日志）。"""
        if self._locked:
            for c in self._clients:
                if getattr(c, "protocol_name", None) == self._locked:
                    return c
        return self._clients[0]

    def _order(self) -> List[Any]:
        """本次要试的顺序：已锁定就只用它；否则按候选表顺序。"""
        if self._locked:
            for c in self._clients:
                if getattr(c, "protocol_name", None) == self._locked:
                    return [c]
        return list(self._clients)

    # ---------- LlmClient 协议 ----------
    async def generate(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        on_reasoning: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> LlmResult:
        pushed = {"n": 0}       # 已对外推送的块数：>0 就禁止换协议重发（否则同一段话演两遍）

        async def _tok(s: str) -> None:
            pushed["n"] += 1
            if on_token is not None:
                await on_token(s)

        async def _think(s: str) -> None:
            pushed["n"] += 1
            if on_reasoning is not None:
                await on_reasoning(s)

        order = self._order()
        # 本轮**是否允许一路试下去**：已锁定（只用那一条）/ 上次识别失败过（存疑）/ 只有一条候选
        # → 不允许。注意这与"能不能从第 1 条走到第 3 条"是两件事：链内必须一路走到底，
        # 存疑只用来拦住**下一次调用**再开一条新链（否则两条路都不通的模型每次都白发两发）。
        probing = (not self._locked) and (not self._suspect) and len(order) > 1
        switched = False
        first_err: Optional[BaseException] = None
        for i, c in enumerate(order):
            try:
                r = await c.generate(system, messages, tools, on_token=_tok, on_reasoning=_think)
            except Exception as e:  # noqa: BLE001 —— 判定之后要么换协议、要么原样上抛
                name = getattr(c, "protocol_name", f"#{i}")
                if first_err is None:
                    first_err = e
                mis = protocol_mismatch(e)
                last = (i == len(order) - 1)
                if last or not probing or pushed["n"] > 0 or not mis:
                    if switched and not _transient(e):
                        # 一路试完仍以**定性失败**收场（格式错/模型不存在/鉴权错）→ 试探已被
                        # 证明无用，别再让每次对话都白发请求。若是 503/429/超时这类瞬时失败，
                        # 说明某条路可能只是上游在抖，留着下次再试才有机会自愈。
                        self._suspect = True
                        log.warning("协议识别失败：%d 条协议都不通（%s）—— 后续不再试探，"
                                    "直到主协议有一次成功调用；请核对模型名与鉴权",
                                    len(order), str(e)[:150])
                    if i > 0 and last:
                        log.warning("协议识别的候选（%s）也失败：%s", name, str(e)[:200])
                    # **报回第一条原始错误**（它对应配置里写的那条路，文案稳定，面板的归类与
                    # 建议也是照它给的）；后续几条的原文挂进日志与 __cause__，排障时都在。
                    raise first_err from e if first_err is not e else e
                switched = True
                log.warning("协议疑似不对（%s 协议被拒）：%s —— 换下一条协议再试"
                            "（本次未推送任何内容，不会重复展示）", name, str(e)[:200])
                continue
            if i > 0:
                self._locked = getattr(c, "protocol_name", None)
                log.info("协议自动识别完成：该模型走 %s 协议 —— 本内芯已锁定，后续请求不再试探。"
                         "若想固定，可在 config.json 写 llm.protocol=%s", self._locked, self._locked)
            elif self._suspect:
                self._suspect = False          # 主协议恢复可用 → 解除存疑
            return r
        raise first_err if first_err is not None else RuntimeError("auto protocol: no client tried")
