"""llm/base — LlmClient 协议 + LlmResult（循环机唯一认可的口）

分层职责（对标 DSH dsh-llm/lib/index.js:1038 LlmRuntime 的 Service 抽象）：
- DialogueAgent 只认 LlmClient.generate(system, messages, tools) 一个口，不做 wire 翻译、不猜后端形态
- 三路（openai/stub/echo）是不同 LlmClient 实现，由 factory/AgentLoop 选，循环机永不知
- LlmResult.tool_calls 恒为 Canonical 近义形态 [{id, name, arguments: dict}]，落盘前不再归一
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class LlmClient(Protocol):
    """协议：接受 Canonical messages，返回 LlmResult。wire 互译在本实现内部

    on_token（可选）：逐 token 回调（awaitable），供流式转发（如 WS text_delta）。
    LLM 层负责"何时产 token、如何回调"，上层只注入钩子、不解释。
    """

    async def generate(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        on_reasoning: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> "LlmResult":
        ...


@dataclass
class LlmResult:
    text: str = ""
    # 思考内容（reasoning_content 字段 / content 内嵌 <think> 标签拆出），账本与 wire 不回传，仅供 UI 展示
    reasoning: str = ""
    # tool_calls 恒为 [{id, name, arguments: dict}]（arguments 已解析为 dict）
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    usage: Optional[Dict[str, Any]] = None
    # provider/model 仅供 surface 审计回填，非 wire 字段
    provider: Optional[str] = None
    model: Optional[str] = None