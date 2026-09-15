"""llm/probe —— 「测试连接」：拿**表单当前值**真调一次模型，把失败原因翻译成人话。

为什么值得单独一个模块：
  全仓只有这一处会**故意制造一次真实 API 调用**，它和对话链路无关，却必须与对话链路
  **共用同一条构造路径**（`factory.create_llm_client`）。若测试自己 `new OpenAI(...)`，
  就会出现「测试通过、线上照样失败」—— 那这个按钮就是在骗人。本模块与真实调用唯一的差别是：
  请求体最小、超时更短、`retries=0`（测试要的是**立刻**看到真错误，不是重试三次才报）。

分层：本模块只负责「发起 + 翻译错误」，不碰 config 文件、不碰 LlmRouter —— 那些归 ConfigService。

安全：`api_key` 与**所有 header 的值**在返回前一律脱敏。回包会经 C# 进状态条与 Player.log，
  而仓库的泄漏闸只扫文件、扫不到运行期回包，这条只能靠代码自觉。回给 UI 的只有 header **名字**。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

# 探针提示词：越短越省钱、越不容易被"模型不想答"干扰。只要它能吐一个字就证明整条链路通了。
_PROBE_SYSTEM = "你是连通性测试。收到任何输入都只回复两个字：ok"
_PROBE_USER = "ping"

# 单条错误原文的回传上限：状态条只显示一行，完整原文在日志里（Python 侧 log 有全文）
_MESSAGE_MAX = 800

# 处置建议。**长度有硬约束**：这些字要显示在配置面板那条状态栏里
# （`BG/Status` 是 736×32、Wrap+Overflow，一行约 50 个汉字），
# 所以每条压到 ~30 字以内，剩下的细节写进 message 原文与日志。
# 措辞一律「先说是什么、再说怎么办」，因为面板会按字数截断尾部。
_HINTS: Dict[str, str] = {
    "no_config": "base_url / api_key / model 要填齐（base_url 通常带 /v1）",
    "auth": "API Key 不对或已失效：核对有无空格、是不是本站的 key",
    "forbidden": "被网关拒绝：多为要自定义 User-Agent，或该 key 无此模型权限",
    "not_found": "base_url 路径不对（多半漏了 /v1），或模型名不存在",
    "bad_request": "请求被网关判为非法，看原文指出的是哪一项",
    "rate_limit": "限流或额度用尽：稍后再试，或去对方后台看余额",
    "timeout": "等超时了：网络/代理慢，或对方网关卡住",
    "connection": "连不上：检查 base_url 拼写、本机网络与代理",
    "bad_json": "返回的不是 JSON —— base_url 多半指向了一个网页",
    "server": "对方网关 5xx，是它那边的问题，过会儿再试",
    "build": "内芯构造失败，多半是 base_url 写法不对",
    "unknown": "未归类的失败，看下面的错误原文",
}

# 按类名兜底的网络异常（openai SDK 各版本类名不完全一致，故同时按名字匹配）
_NET_TIMEOUT = {"APITimeoutError", "Timeout", "TimeoutError", "ReadTimeout", "ConnectTimeout",
                "ConnectTimeoutError", "PoolTimeout"}
_NET_CONN = {"APIConnectionError", "ConnectError", "ConnectionError", "ConnectionResetError",
             "SSLError", "SSLCertVerificationError", "NewConnectionError", "ProxyError",
             "RemoteProtocolError", "ConnectError"}
_BAD_JSON = {"JSONDecodeError", "APIResponseValidationError", "JSONDecodeFailure"}


def _redact(text: Any, secrets: List[str]) -> str:
    """把密钥/头值从任意文本里抹掉。长度 <6 的串不替换 —— 那种短串（"1"、"ok"）
    出现在正常文本里的概率太高，替换会把错误原文打成筛子，反而更难排查。"""
    out = str(text if text is not None else "")
    for s in secrets:
        s = str(s or "")
        if len(s) >= 6:
            out = out.replace(s, "***")
    return out


def _extract_message(exc: BaseException) -> str:
    """尽量挖出**对方自己说的那句话**，而不是 Python 的 repr。

    优先级：SDK 解析好的 `body.error.message` > `response.text` > `str(exc)`。
    为什么较真：用户要判断的正是「是不是 header 的问题」，而那只能从原文里看出来
    （OpenCode Go 的 `MissingSessionID` 就只出现在 body 里）。
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            for k in ("message", "msg", "detail", "type"):
                if err.get(k):
                    return str(err[k])
        if isinstance(err, str) and err.strip():
            return err
        for k in ("message", "msg", "detail"):
            if body.get(k):
                return str(body[k])
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            t = resp.text
            if isinstance(t, str) and t.strip():
                return t
        except Exception:
            pass
    s = str(exc)
    return s if s.strip() else type(exc).__name__


def _extract_code(exc: BaseException) -> Optional[str]:
    """对方返回的业务错误码（如 Cloudflare 的 1010、OpenAI 的 invalid_api_key）。"""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("code") is not None:
            return str(err["code"])
        if body.get("code") is not None:
            return str(body["code"])
    return None


def classify(exc: BaseException) -> Tuple[str, Optional[int], Optional[str], str]:
    """异常 → (kind, http_status, 业务码, 原文)。

    顺序有讲究：超时要在连接之前判（openai 的 APITimeoutError 是 APIConnectionError 的子类），
    有 status 的先按 status 判（那是最可信的信号），最后才按类名兜底。
    """
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None
    msg = _extract_message(exc)
    code = _extract_code(exc)
    low = msg.lower()

    if name in _NET_TIMEOUT or isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout", status, code, msg
    if status == 401:
        return "auth", status, code, msg
    if status == 403:
        return "forbidden", status, code, msg
    if status == 404:
        return "not_found", status, code, msg
    if status == 429:
        return "rate_limit", status, code, msg
    if status in (400, 422):
        return "bad_request", status, code, msg
    if status is not None and status >= 500:
        return "server", status, code, msg
    if name in _BAD_JSON or "expecting value" in low or "not valid json" in low \
            or "<!doctype html" in low or "<html" in low:
        return "bad_json", status, code, msg
    if name in _NET_CONN:
        return "connection", status, code, msg
    return "unknown", status, code, msg


def _refine_hint(kind: str, message: str, code: Optional[str]) -> str:
    """在通用建议之上，按**对方原文**再特化一句 —— 这才是这个按钮的价值所在。

    通用 hint 顶多说到「400 = 请求非法」，而用户真正要知道的是「到底哪一项填错了」。
    下面几条都是从真实网关的措辞里认出来的（OpenCode Go 的 MissingSessionID、
    Cloudflare 的 1010、OpenAI 的 model_not_found）。
    """
    low = (message or "").lower()
    tag = (code or "").lower()

    if kind == "bad_request":
        if any(k in low or k in tag for k in
               ("session", "missing_session", "header", "x-opencode")):
            return ("网关要自定义请求头：在 headers 那行填 "
                    "x-opencode-session: <任意稳定值>")
        if "model" in low and any(k in low or k in tag for k in
                                  ("not found", "not_found", "not exist", "unknown", "invalid",
                                   "不存在", "没有")):
            return "该模型名在这站不存在，去对方文档核对 model 名"
        if any(k in low for k in ("context length", "too long", "max_tokens", "maximum context")):
            return "请求超长：在【记忆与压缩】组把上下文窗口调小"

    if kind == "forbidden":
        if "1010" in tag or "1010" in low or "cloudflare" in low or "browser" in low:
            return "被 Cloudflare 拦了：在 headers 加一条 User-Agent（别用默认的）"
        if any(k in low or k in tag for k in ("model", "permission", "not allowed", "无权")):
            return "该 key 没有这个模型的权限，去对方后台确认已开通"

    if kind == "not_found" and "model" in low:
        return "base_url 通了但这个模型名不存在，核对 model"

    if kind == "auth" and any(k in low or k in tag for k in ("balance", "quota", "欠费", "余额")):
        return "key 有效但余额/额度不足，先充值或换一个 key"

    return _HINTS.get(kind) or _HINTS["unknown"]


def _error(kind: str, status: Optional[int], code: Optional[str],
           message: str, secrets: List[str]) -> Dict[str, Any]:
    msg = _redact(message, secrets)[:_MESSAGE_MAX]
    return {
        "kind": kind,
        "status": status,
        "code": code,
        "message": msg,
        # hint 按**已脱敏**的原文特化：万一密钥混在原文里，也不会被抄进 hint
        "hint": _refine_hint(kind, msg, code),
    }


async def probe_llm(
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """用给定参数真调一次模型。**永不抛异常** —— 失败一律走返回值的 `error`。

    返回：{ok, latency_ms, model, base_url, header_names, reply, usage, error}
      · header_names 只给**名字**（值里有 session/鉴权信息，不回传）
      · error: {kind, status, code, message, hint}；hint 是给玩家看的中文处置建议
    """
    started = time.monotonic()
    bu, ak, md = (base_url or "").strip(), (api_key or "").strip(), (model or "").strip()
    hdrs: Dict[str, str] = {}
    if isinstance(headers, dict):
        hdrs = {str(k).strip(): str(v) for k, v in headers.items() if str(k).strip()}
    # 脱敏用的"秘密清单"：key + 每个头的值
    secrets: List[str] = [ak] + list(hdrs.values())

    def _out(ok: bool, *, reply: Optional[str] = None,
             usage: Any = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "ok": bool(ok),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "model": md,
            "base_url": bu,
            "header_names": sorted(hdrs.keys()),
            "reply": reply,
            "usage": usage,
            "error": error,
        }

    missing = [n for n, v in (("base_url", bu), ("api_key", ak), ("model", md)) if not v]
    if missing:
        return _out(False, error=_error("no_config", None, None,
                                        "缺少配置：" + "、".join(missing), secrets))

    try:
        from .factory import create_llm_client

        # use_config=False：只测**表单里这一份**，绝不偷偷拿 config.json 里的旧值兜底 ——
        # 否则用户改了 base_url 却测出"成功"，其实测的是文件里那份旧配置。
        client = create_llm_client(base_url=bu, api_key=ak, model=md,
                                   headers=hdrs or None,
                                   use_config=False, retries=0, timeout=timeout)
    except Exception as e:  # noqa: BLE001 —— 探针的职责就是"什么都别抛出去"
        kind, status, code, msg = classify(e)
        return _out(False, error=_error(kind if kind != "unknown" else "build",
                                        status, code, msg, secrets))

    # 空值上面已经挡掉，这里再挡一次 Echo 兜底：漏了它就会"复读 ping 却报成功"，
    # 而那正是这个按钮**最不能犯**的错（用户会以为配好了）。
    from .echo_client import EchoLlmClient

    if isinstance(client, EchoLlmClient):
        return _out(False, error=_error("no_config", None, None,
                                        "配置未生效：构造出来的是 Echo 回显兜底（不是真模型）",
                                        secrets))

    try:
        # 外层再兜一道硬超时：SDK 的 timeout 覆盖不到"连接卡在建连阶段"的少数情形，
        # 而面板不能永远转圈。留 timeout+10 的余量，避免和 SDK 自己那层抢跑。
        result = await asyncio.wait_for(
            client.generate(
                system=_PROBE_SYSTEM,
                messages=[{"role": "user", "content": [{"type": "text", "text": _PROBE_USER}]}],
                tools=[],
            ),
            timeout=max(1.0, float(timeout)) + 10.0,
        )
    except Exception as e:  # noqa: BLE001
        kind, status, code, msg = classify(e)
        return _out(False, error=_error(kind, status, code, msg, secrets))

    text = (getattr(result, "text", "") or "").strip()
    return _out(True, reply=text[:200], usage=getattr(result, "usage", None))
