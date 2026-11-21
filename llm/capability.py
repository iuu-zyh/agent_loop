"""llm/capability — 请求体能力探测（「测试连接」的附加步骤，09-21）

回答一个问题：**这个网关 + 这个模型，认不认识 `reasoning_effort`、真出不出思维链？**

为什么值得单独一个模块：同一个请求体键，各网关的表现从「认识并生效」「静默忽略未知键」
到「对非法内容整单拒绝」都有（实测 CodeBuddy：胡写键被忽略、`reasoning_effort` 非法值
回 400 code 11150、合法档位出思考流）。玩家不可能知道该填哪个键 —— 这里替他把试错跑掉，
结论驱动配置面板「思考强度」一行的显隐与是否注入请求体。

设计核心：**差分法** —— 三发请求只动一个变量，比行为差异，**不解析任何报错文本**
（11150 是 CodeBuddy 的私码，DeepSeek 官方 / vLLM / OpenRouter 各不相同，文本匹配必然漏）。

  A 基准 ping（不带键）        → 连通参照：B 的失败只有"相对 A 成功"才有意义
  B 非法档位 ping              → 4xx(bad_request) ⇒ 键被认识；成功 ⇒ 宽容/忽略（不可信）
  C low + 强制推理题（流式）    → 观测思考流（reasoning_content / reasoning / <think>
                                 三形态，复用 OpenAILlmClient 的现成解析器）

判定原则：**模糊一律判 unknown/unsupported（保守）** —— 最坏结果是面板少显示一行，
绝不会多传参数把真实回合打成 400。这与"宁可漏屏蔽、不可永久屏蔽"那类判据的失败姿态
**刻意相反**：这里保守的代价极小，激进的代价是整个回合失败。

与 `llm/probe.py` 的分工：那边回答「连不连得上」（用户配置对不对），本模块回答
「这个模型有什么能力」（面板显隐与注入依据）。本模块**只读**：不碰 config、不写盘 ——
探测结论随 test_llm 回包上行，由 C# 在「保存大模型设置」时一并落盘。

安全：判定只看「成功/失败归类/思考字符数」，不回传报错原文 → 无密钥脱敏负担。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from .openai_client import OpenAILlmClient
from .probe import classify

# 思考档位（OpenAI 生态 `reasoning_effort` 的通用值域）。config_store._ENUM_KEYS
# 与 C# ConfigPresenter 的保存校验各有一份字面量 —— 三处必须一致（契约测试钉住）。
REASONING_EFFORTS = ("low", "high", "max")

# 探测结论的值域（回包 probe.verdict；与 config llm.probe / C# 显隐判据同源）：
#   supported   — 键被认识且观测到思维流 → 面板显示「思考强度」，可注入
#   no_think    — 键被认识但没观测到思维流（模型不思考/思考不上报）→ 面板显示但标注；
#                 注入无害（键本身可用），由用户决定要不要开
#   unsupported — 键不被认识（被忽略）或档位被拒 → 面板不显示，绝不注入
#   unknown     — 基准失败 / 网络类错误 / 探测器自身异常 → 面板不显示（与现状相同）
VERDICTS = ("supported", "no_think", "unsupported", "unknown")

# 故意非法的档位值：既然是枚举校验，任何不在值域里的串都该被严格网关拒掉。
# 不用空串/纯数字 —— 那类值有被宽容网关"顺手归一成默认档"的空间，哨兵要够怪。
PROBE_SENTINEL = "__agent_loop_probe__"

# A/B 发的探针提示词：与 probe.py 同款（越短越省钱，吐一个字就够）。
_PING_SYSTEM = "你是连通性测试。收到任何输入都只回复两个字：ok"
_PING_USER = "ping"

# C 发必须**逼模型思考**：太简单的问题思考模型也会跳过 reasoning 直接答（探测就假阴性了）。
# 「9.11 和 9.9 哪个大」实测对 DeepSeek/GLM/Qwen 系思考模型都能稳定触发思考流且正文短。
_REASONING_SYSTEM = "回答前先想清楚再答。"
_REASONING_USER = "9.11 和 9.9 哪个大？先想清楚再答，回答保持简短。"

# 探测各发的默认首字看门狗（秒）。探测不该让用户等满对话链路的 120s：测不出就判 unknown。
_PROBE_TIMEOUT = 20.0


def _make_client(base_url: str, api_key: str, model: str,
                 headers: Optional[Dict[str, str]], timeout: float,
                 effort: Optional[str]) -> OpenAILlmClient:
    """探测专用的 openai 内芯。

    为什么**不走 factory**：factory 会按 config 的门控结论自动附加 reasoning_effort，
    而探测的 A/B/C 三发必须**完全控制**请求体（A 绝不能带键，否则基准被污染）。
    探测只针对 openai 协议：reasoning_effort 本就是 openai 生态的 body 字段，
    网关若只说 anthropic 协议，真实对话本来也不该带这个键。
    retries=0：探测要的是立刻的真实结论，重试只会把"要重测一次"拖成"等三次"。
    """
    return OpenAILlmClient(base_url=base_url, api_key=api_key, model=model,
                           headers=headers or None, retries=0,
                           timeout=timeout, timeout_nonstream=timeout,
                           idle_timeout=timeout, reasoning_effort=effort)


async def _sink(_chunk: str) -> None:
    """流式回调的丢弃出口（确保 C 发走流式路径，与真实对话 wire 同构）。"""


async def _fire(client: OpenAILlmClient, system: str, user: str,
                *, stream: bool) -> Dict[str, Any]:
    """发一发探测请求。**永不抛** —— 失败归类进返回值（判定只认 kind，不看原文）。"""
    try:
        kwargs: Dict[str, Any] = {}
        if stream:
            # 传 on_reasoning(+on_token) 让骨架走流式（streaming 判据见 generate）：
            # 有的网关只在流式里给 reasoning_content，非流式探测会假阴性。
            kwargs["on_token"] = _sink
            kwargs["on_reasoning"] = _sink
        result = await asyncio.wait_for(
            client.generate(system=system,
                            messages=[{"role": "user", "content": [
                                {"type": "text", "text": user}]}],
                            tools=[], **kwargs),
            timeout=max(1.0, float(client.timeout or _PROBE_TIMEOUT)) + 10.0)
        reasoning = (getattr(result, "reasoning", "") or "")
        return {"ok": True, "kind": None, "reasoning_chars": len(reasoning)}
    except Exception as e:  # noqa: BLE001 —— 探测的职责就是"什么都别抛出去"
        kind, _status, _code, _msg = classify(e)
        return {"ok": False, "kind": kind, "reasoning_chars": 0}


def _judge(base: Dict[str, Any], strict: Dict[str, Any],
           think: Dict[str, Any]) -> tuple:
    """三发结果 → (verdict, why)。纯函数，判定矩阵见模块 docstring，逐分支有测试钉。

    ★ 判定顺序有讲究（09-21 实测 proxy-hub 后修正）★：**思考流出现 = 最强证据，优先判**。
    网关可能把上游对非法档位的 400 **包装成 502** 再回给客户端（proxy-hub 就是这么干的：
    "CodeBuddy upstream 400" 套在 502 里）—— 于是 B 发的 HTTP 归类**不可尽信**：
    bad_request 只能证明"认识键"，而 server 可能是包装的 400 也可能是真 5xx。
    唯一穿透包装的信号是 C 发的思考差分：键被忽略时推理题的思考流不会凭空出现
    （A 发的 ping 不逼思考，可作差分参照）。
    """
    if not base["ok"]:
        return "unknown", "基准请求失败（" + str(base.get("kind")) + "），无从比较"
    thinks = think["ok"] and think["reasoning_chars"] > 0
    if thinks:
        # 带键 + 推理题 → 思考流出现：功能真实生效（无论网关是严格还是宽容）。
        # 注入若被宽容网关忽略也无害 —— 内心思量照样出现，档位差异可能失效而已。
        return "supported", "合法档位实测出思维流（" + str(think["reasoning_chars"]) + " 字符）"
    if not think["ok"]:
        if think["kind"] == "bad_request":
            return "unsupported", "网关认识 reasoning_effort，但当前模型拒绝 low 档"
        return "unknown", "合法档位探测失败（" + str(think.get("kind")) + "）"
    # C 成功但无思考：此时唯一的信息来自 B。
    if strict["ok"]:
        return "unsupported", "网关宽容未知键（键可能被忽略），且未观测到思维流"
    if strict["kind"] == "bad_request":
        return "no_think", "网关认识 reasoning_effort，但该模型未观测到思维流"
    return "unknown", "非法档位探测失败的原因与键无关（" + str(strict.get("kind")) + "）"


async def probe_reasoning_effort(
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    headers: Optional[Dict[str, str]] = None,
    timeout: float = _PROBE_TIMEOUT,
) -> Dict[str, Any]:
    """探测 `reasoning_effort` 能力。**永不抛** —— 一切失败都收进 verdict。

    返回：{verdict, reasoning_chars, why, base_url, model, latency_ms}
      · verdict ∈ VERDICTS（值域与语义见上）
      · reasoning_chars：C 发观测到的思维流字符数（0 = 没观测到）
      · base_url / model：探测时的**表单值**（C# 据此判断结论是否还属于当前表单；
        factory 的门控也用它当指纹 —— 结论换网关/换模型即作废，必须重测）
      · why：判定依据一句话（进日志/调试，UI 不显示）
    """
    started = time.monotonic()
    bu, ak, md = (base_url or "").strip(), (api_key or "").strip(), (model or "").strip()
    hdrs: Dict[str, str] = {}
    if isinstance(headers, dict):
        hdrs = {str(k).strip(): str(v) for k, v in headers.items() if str(k).strip()}

    def _out(verdict: str, reasoning_chars: int = 0, why: str = "") -> Dict[str, Any]:
        return {
            "verdict": verdict,
            "reasoning_chars": int(reasoning_chars),
            "why": why,
            "base_url": bu,
            "model": md,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    missing = [n for n, v in (("base_url", bu), ("api_key", ak), ("model", md)) if not v]
    if missing:
        return _out("unknown", why="缺少配置：" + "、".join(missing))

    try:
        base, strict, think = await asyncio.gather(
            _fire(_make_client(bu, ak, md, hdrs, timeout, None),
                  _PING_SYSTEM, _PING_USER, stream=False),
            _fire(_make_client(bu, ak, md, hdrs, timeout, PROBE_SENTINEL),
                  _PING_SYSTEM, _PING_USER, stream=False),
            _fire(_make_client(bu, ak, md, hdrs, timeout, "low"),
                  _REASONING_SYSTEM, _REASONING_USER, stream=True),
        )
    except Exception as e:  # noqa: BLE001 —— 理论到不了（_fire 不抛），兜一道
        return _out("unknown", why="探测器异常：" + type(e).__name__)

    verdict, why = _judge(base, strict, think)
    return _out(verdict, reasoning_chars=think.get("reasoning_chars", 0), why=why)


__all__ = ["REASONING_EFFORTS", "VERDICTS", "PROBE_SENTINEL", "probe_reasoning_effort"]
