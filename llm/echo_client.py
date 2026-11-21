"""llm/echo_client — 无 LLM 时回显兜底（final fallback，clone 现有 DialogueAgent 末路）"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .base import LlmResult

# SystemPrompt L1 运行时上下文注入的前缀（system_prompt._ctx_line）——
# 这是发给模型的内部管线消息，不是玩家说的话；echo 兜底绝不能把它当回复念出来
# （否则 UI 会出现一条 NPC 气泡："[回显] Current runtime context —— 玩家：…"）。
_CTX_MARKER = "Current runtime context —— "


def _user_text(msg: Dict[str, Any]) -> str:
    content = msg.get("content") or []
    if isinstance(content, list) and content and isinstance(content[0], dict) and content[0].get("type") == "text":
        return content[0].get("text", "") or ""
    return str(content)


class EchoLlmClient:
    """把最后一条玩家 user 文本原样回显，用于脱离 LLM 调试/测试时收口 turn"""

    async def generate(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        on_reasoning: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> LlmResult:
        last = next(
            (
                m
                for m in reversed(messages)
                if m.get("role") == "user" and not _user_text(m).startswith(_CTX_MARKER)
            ),
            None,
        )
        text = "[空]"
        if last is not None:
            text = f"[回显] {_user_text(last)}"
        if on_token is not None and text:
            r = on_token(text)
            if asyncio.iscoroutine(r):
                await r
        return LlmResult(text=text, tool_calls=[], provider="echo", model="echo")
