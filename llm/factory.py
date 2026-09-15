"""llm/factory — LlmClient 工厂：参数 > env > config(llm 块) → Echo 兜底（AgentLoop 唯一造口）

config 读取统一收编到 agent_loop/config_loader（分块 + 深合并 + 旧扁平 llm 键兼容），
工厂只读「llm 块」：base_url / api_key / model / image.enabled。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import LlmClient
from .openai_client import OpenAILlmClient
from .echo_client import EchoLlmClient

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
    total_budget: Optional[float] = None,
    headers: Optional[Dict[str, str]] = None,
) -> LlmClient:
    """按优先级显式参数 → env → config(llm 块) → Echo 兜底。

    - 显式 base_url+api_key 或 client 实例 → OpenAILlmClient
    - env 缺省时读 OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL
    - 仍未齐全且 use_config 时读项目根 config.json 的 llm 块（兼容旧扁平顶层键）
    - 图片能力：显式 supports_image > config image.enabled > model 白名单；仍未知放行
    - retries：网络/瞬时错误重试次数，缺省读 config llm.retries（默认 1，0 不重试）
    - timeout：**流式**单次请求上限秒，缺省读 config llm.timeout（默认 45）。
      语义是 httpx 的 read timeout = 相邻两块数据之间的间隔，非整个请求时长；
      null/0 = 保持 SDK 内建默认（单次 600s，是"卡住但不报错"的静默窗口来源）。
    - timeout_nonstream：**非流式**单次上限秒，缺省读 config llm.timeout_nonstream（默认 180）。
      非流式在整段生成完前一个字节都不发，所以这里量的是完整生成时长；唯一调用方是上下文压缩。
      缺省时回落 timeout。
    - total_budget：整个 generate（含全部重试）的挂钟硬顶秒，缺省读 config llm.total_budget
      （默认 100）；null/0 = 不设顶。**只约束流式路径**（玩家最坏等待由它封顶）。
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
        from ..config_loader import load_config

        llm_cfg = load_config(config_path).get("llm", {})
        base_url = base_url or llm_cfg.get("base_url")
        api_key = api_key or llm_cfg.get("api_key")
        model = model or llm_cfg.get("model")

    model = model or "test-model"

    # 备注：即便 base_url/api_key 已由显式参数齐全，仍要读 config 以解析图片能力
    if use_config and not llm_cfg:
        from ..config_loader import load_config

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
    if total_budget is None:
        total_budget = _positive_float_or_none(llm_cfg.get("total_budget"))
    # 附加请求头（llm.headers）：网关若要求自报家门（如 OpenCode Go 的 x-opencode-session
    # 与自定义 User-Agent），在此透传给 SDK 的 default_headers。
    if headers is None:
        raw_h = llm_cfg.get("headers")
        headers = {str(k): str(v) for k, v in raw_h.items()} if isinstance(raw_h, dict) and raw_h else None

    if client is not None or (base_url and api_key):
        return OpenAILlmClient(base_url=base_url, api_key=api_key, model=model, client=client,
                               supports_image=supports_image, retries=retries, timeout=timeout,
                               timeout_nonstream=timeout_nonstream, total_budget=total_budget,
                               headers=headers)
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