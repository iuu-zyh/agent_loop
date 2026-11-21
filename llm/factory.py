"""llm/factory — LlmClient 工厂：参数 > env > config(llm 块) → Echo 兜底（AgentLoop 唯一造口）

config 读取统一收编到 agent_loop/config_loader（分块 + 深合并 + 旧扁平 llm 键兼容），
工厂只读「llm 块」：base_url / api_key / model / image.enabled / protocol / max_tokens。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .. import log_setup as _log_setup
from .base import LlmClient
from .anthropic_client import AnthropicLlmClient
from .auto_client import AutoProtocolLlmClient
from .openai_client import OpenAILlmClient
from .responses_client import OpenAIResponsesLlmClient
from .echo_client import EchoLlmClient

# 协议取值（config llm.protocol）：
#   "auto"（默认）—— 依次试 openai → anthropic → responses，命中即锁定
#                    （网关会自己报出格式名，见 llm/auto_client.py）
#   "openai"       —— 只用 OpenAI 兼容 /chat/completions（历史行为，不做任何试探）
#   "anthropic"    —— 只用 Anthropic /messages（x-api-key 鉴权、max_tokens 必填）
#   "responses"    —— 只用 OpenAI Responses /responses（扁平 input 条目、instructions 顶层）
# 显式写具体协议 = 用户要的是确定性，故**不做**回退。
PROTOCOLS = ("auto", "openai", "anthropic", "responses")

log = _log_setup.get_logger(__name__)


# 已知多模态模型（白名单）。请求带图时的能力来源之一。
# 判定优先级：config image.enabled < 本表 < 显式 supports_image 参数。
MODEL_MODALITY: Dict[str, bool] = {
    "gpt-4o": True,
    "gpt-4o-mini": True,
    "gpt-4.1": True,
    "gpt-4.1-mini": True,
    "gpt-5.0": True,
    "gpt-5.1": True,
    "claude-opus-4-8": True,
    "claude-opus-4-7": True,
    "claude-opus-4-6": True,
    "claude-opus-4-5": True,
    "claude-sonnet-5": True,
    "claude-sonnet-4-6": True,
    "claude-sonnet-4-5": True,
    "claude-sonnet-4": True,
    "claude-haiku-4-5": True,
    "gemini-3.6-flash": True,
    "gemini-3.1-pro": True,
    "gemini-3-flash": True,
}


def _resolve_supports_image(llm_cfg: Dict[str, Any], model: str) -> Optional[bool]:
    """解析图片能力（三态）：显式 supports_image 参数优先，其次 model 白名单，其次 config llm.image.enabled。
    仍未知返回 None（放行，由 API 自身决定）。"""
    # config 里显式 enabled
    image_cfg = llm_cfg.get("image")
    if isinstance(image_cfg, dict) and "enabled" in image_cfg:
        return bool(image_cfg["enabled"])
    # model 白名单
    if model in MODEL_MODALITY:
        return MODEL_MODALITY[model]
    return None


def create_llm_client(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    client: Any = None,
    env: Optional[Dict[str, str]] = None,
    echo_only: bool = False,
    config_path: Optional[str | os.PathLike] = None,
    use_config: bool = True,
    supports_image: Optional[bool] = None,
    retries: Optional[int] = None,
    timeout: Optional[float] = None,
    timeout_nonstream: Optional[float] = None,
    idle_timeout: Optional[float] = None,
    total_budget: Optional[float] = None,
    headers: Optional[Dict[str, str]] = None,
    protocol: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> LlmClient:
    """按优先级显式参数 → env → config(llm 块) → Echo 兜底。

    - 显式 base_url+api_key 或 client 实例 → OpenAILlmClient
    - env 缺省时读 OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL
    - 仍未齐全且 use_config 时读项目根 config.json 的 llm 块（兼容旧扁平顶层键）
    - 图片能力：显式 supports_image > config image.enabled > model 白名单；仍未知放行
    - retries：网络/瞬时错误重试次数，缺省读 config llm.retries（默认 1，0 不重试）
    - timeout：**首字看门狗**秒，缺省读 config llm.timeout（默认 120）。
      语义是"请求发出 → 第一个内容块"，**不是**整趟耗时上限（09-18 重做，见 docs/features/brain-liveness.md §4）；
    - idle_timeout：**停顿看门狗**秒，缺省读 config llm.idle_timeout（默认 60），
      语义是"相邻两个内容块之间的间隔"；未配置时回落 timeout；
      null/0 = 保持 SDK 内建默认（单次 600s，是"卡住但不报错"的静默窗口来源）。
    - timeout_nonstream：**非流式**单次上限秒，缺省读 config llm.timeout_nonstream（默认 180）。
      非流式在整段生成完前一个字节都不发，所以这里量的是完整生成时长；唯一调用方是上下文压缩。
      缺省时回落 timeout。
    - total_budget：整个 generate（含全部重试）的挂钟硬顶秒，缺省读 config llm.total_budget
      （默认 100）；null/0 = 不设顶。**只约束流式路径**（玩家最坏等待由它封顶）。
    - protocol：`auto`（默认）/ `openai` / `anthropic` / `responses`，缺省读 config llm.protocol。
      auto 会造**三条协议各一个内芯**并用自动识别门面包起来（见 llm/auto_client.py）；
      显式指定则只造那一个、不做任何试探（用户要确定性时用）。
    - max_tokens：**仅 Anthropic 协议**用（Anthropic 的 max_tokens 必填），缺省读
      config llm.max_tokens（默认 8192）。
    - 全部缺省或 echo_only=True → EchoLlmClient（离线兜底）
    """
    if echo_only:
        return EchoLlmClient()
    env = env if env is not None else os.environ

    # 显式参数优先
    base_url = base_url or env.get("OPENAI_BASE_URL")
    api_key = api_key or env.get("OPENAI_API_KEY")
    model = model or env.get("OPENAI_MODEL")

    # 显式 + env 都未齐功能，从 config 补（显式/env 各自保留优先）
    llm_cfg: Dict[str, Any] = {}
    if use_config and not (base_url and api_key):
        from ..config.config_loader import load_config

        llm_cfg = load_config(config_path).get("llm", {})
        base_url = base_url or llm_cfg.get("base_url")
        api_key = api_key or llm_cfg.get("api_key")
        model = model or llm_cfg.get("model")

    model = model or "test-model"

    # 备注：即便 base_url/api_key 已由显式参数齐全，仍要读 config 以解析图片能力
    if use_config and not llm_cfg:
        from ..config.config_loader import load_config

        llm_cfg = load_config(config_path).get("llm", {})
    if supports_image is None:
        supports_image = _resolve_supports_image(llm_cfg, model)
    if retries is None:
        retries = llm_cfg.get("retries", 1)
    retries = int(retries) if retries is not None else 1
    if timeout is None:
        timeout = _positive_float_or_none(llm_cfg.get("timeout"))
    if timeout_nonstream is None:
        timeout_nonstream = _positive_float_or_none(llm_cfg.get("timeout_nonstream"))
    if idle_timeout is None:
        idle_timeout = _float_or_explicit_off(llm_cfg.get("idle_timeout"))
    if total_budget is None:
        total_budget = _positive_float_or_none(llm_cfg.get("total_budget"))
    # 附加请求头（llm.headers）：网关若要求自报家门（如 OpenCode Go 的 x-opencode-session
    # 与自定义 User-Agent），在此透传给 SDK 的 default_headers。
    if headers is None:
        raw_h = llm_cfg.get("headers")
        headers = {str(k): str(v) for k, v in raw_h.items()} if isinstance(raw_h, dict) and raw_h else None
    # 协议：显式参数 > config llm.protocol > "auto"（认不出的一律当 auto，不 fail-closed ——
    # 一个拼错的 protocol 不该让整个 mod 起不来，降级成自动识别并把实情写进日志）
    if protocol is None:
        protocol = llm_cfg.get("protocol")
    proto = str(protocol or "auto").strip().lower()
    if proto not in PROTOCOLS:
        log.warning("llm.protocol=%r 不认识（可选：%s）→ 按 auto 处理（自动识别）",
                    protocol, " / ".join(PROTOCOLS))
        proto = "auto"
    if max_tokens is None:
        max_tokens = llm_cfg.get("max_tokens")
    # 思考强度（llm.reasoning_effort，OpenAI 生态请求体字段）：**探测结论说了才算**。
    # 门控规则见 _gated_reasoning_effort；None = 完全不注入（请求体与旧版逐字节相同）。
    reasoning_effort = _gated_reasoning_effort(llm_cfg.get("reasoning_effort"),
                                               llm_cfg, base_url, model)

    if client is not None or (base_url and api_key):
        common = dict(base_url=base_url, api_key=api_key, model=model,
                      supports_image=supports_image, retries=retries, timeout=timeout,
                      timeout_nonstream=timeout_nonstream, idle_timeout=idle_timeout,
                      total_budget=total_budget, headers=headers)
        if proto == "anthropic":
            return AnthropicLlmClient(client=client, max_tokens=max_tokens, **common)
        if proto == "responses":
            return OpenAIResponsesLlmClient(client=client, **common)
        # reasoning_effort 只给 openai 内芯：它是 /chat/completions 请求体的字段，
        # anthropic（max_tokens 必填的那条）与 responses（instructions 顶层）都没有这个键。
        openai_client = OpenAILlmClient(client=client, reasoning_effort=reasoning_effort, **common)
        if proto == "openai":
            return openai_client
        # auto：三条协议各造一个，交给识别门面。顺序 = 命中概率 × 保持历史行为：
        # openai 在前（绝大多数网关就是它），anthropic 次之，responses 最后。
        return AutoProtocolLlmClient(
            [openai_client,
             AnthropicLlmClient(client=client, max_tokens=max_tokens, **common),
             OpenAIResponsesLlmClient(client=client, **common)],
            primary_name="openai")
    # Echo 兜底必须**出声**：未配置 LLM 时对话会退化成"复读玩家的话"，
    # 这个现象极易被玩家报成"mod 坏了 / AI 不回话"，而旧实现完全静默、连日志都没有一行。
    # 这里点名缺哪一项 + 去哪补，让「没填 key」这个头号首启故障一眼可判。
    try:
        from .. import log_setup as _log_setup

        _missing = "、".join(k for k, v in (("llm.base_url", base_url), ("llm.api_key", api_key)) if not v)
        _log_setup.get_logger("agent_loop.llm.factory").warning(
            "未配置 LLM（缺 %s）→ 内芯退回 Echo 回显（只会复读玩家的话）。"
            "请在游戏内打开对话窗→右上角 ⚙ 配置面板，填写 base_url / api_key / model 并保存，"
            "或直接编辑 config.json 的 llm 块。", _missing or "base_url/api_key")
    except Exception:
        pass
    return EchoLlmClient()


def _gated_reasoning_effort(raw: Any, llm_cfg: Dict[str, Any],
                            base_url: Optional[str], model: Optional[str]) -> Optional[str]:
    """llm.reasoning_effort → 实际注入值；None = 不注入（请求体零改动）。

    门控规则（09-21，与探测器的判定矩阵配套）：
      · 值域只认 low / high / max（大小写归一）；其他一律拒—— 那些值在严格网关
        （实测 CodeBuddy 11150）会 400 掉整个回合，宽容网关则静默失效，两头都不好。
      · **config 里没有 llm.probe = 用户手编直通**：显式写了 effort 的高级用户自负其责
        （与 headers「留空 = 删键」同一条信任模型：手编文件是权威输入）。
      · probe 存在则必须**指纹匹配**（probe.base_url/model 与本次实际用的 base_url/model
        逐字一致 —— 探测结论是 per-model 的，11150 的报错原文自己都写着 "the current
        model"）且 **verdict ∈ {supported, no_think}** 才放行。no_think（键可用但模型
        不出思维流）也放行：注入本身无害，开不开由用户。
      · 任何不满足都**静默降级为不注入** + 一条 WARNING —— 与探测器「模糊判负」同姿态：
        最坏是少一个功能，绝不会多传参数把回合打成 400。
    """
    if raw is None:
        return None
    effort = str(raw).strip().lower()
    if not effort:
        return None
    if effort not in ("low", "high", "max"):
        log.warning("llm.reasoning_effort=%r 不认识（可选 low/high/max）→ 不附加", raw)
        return None
    probe = llm_cfg.get("probe")
    if not isinstance(probe, dict) or not probe.get("verdict"):
        return effort  # 没有探测结论 = 手编 config.json 的用户 → 直通
    p_url = str(probe.get("base_url") or "")
    p_model = str(probe.get("model") or "")
    if p_url != str(base_url or "") or p_model != str(model or ""):
        log.warning("llm.reasoning_effort=%s 的探测结论属于别的网关/模型"
                    "（probe: %s / %s ≠ 当前: %s / %s）→ 不附加；重新「测试连接」即可",
                    effort, p_url, p_model, base_url, model)
        return None
    verdict = str(probe.get("verdict"))
    if verdict not in ("supported", "no_think"):
        log.warning("llm.reasoning_effort=%s 的探测结论为 %r（当前模型不支持）→ 不附加",
                    effort, verdict)
        return None
    return effort


def _float_or_explicit_off(raw: Any) -> Optional[float]:
    """同 `_positive_float_or_none`，但**显式 0 原样保留为 0.0**（= 明确关掉这个看门狗）。

    停顿看门狗需要这个区分（见 openai_client.__init__ 的三态说明）：
      None = 没配 → 回落首字看门狗；0 = 配了但要求关掉；正数 = 用它。
    其他键仍走 `_positive_float_or_none`（0 与 null 同义）。
    """
    if raw in (0, "0", 0.0, "0.0"):
        return 0.0
    return _positive_float_or_none(raw)


def _positive_float_or_none(raw: Any) -> Optional[float]:
    """config 值 → 正浮点数；None/""/0/"0"/非法值/负数 → None（= 该上限不启用）。

    0 与 null 同义（"关掉这个上限"），与 timeout 字段的历史语义保持一致：
    旧代码里 `raw_to not in (None, "", 0, "0")` 就是这个意思，此处抽出来给 total_budget 复用。
    """
    if raw in (None, "", 0, "0"):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None