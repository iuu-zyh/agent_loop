"""llm/stub_client — 注入用桩：fn(request)->result 归一为 LlmResult（对齐 DSH 测试注入 adapter）"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .base import LlmResult

Request = Dict[str, Any]
Result = Any


def _parse_args(args: Any) -> Dict[str, Any]:
    """arguments 兼容 str(JSON) / dict：str 解成 dict，解不动则兜 {"_raw": ...}"""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        s = args.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {"_raw": parsed}
        except Exception:
            return {"_raw": s}
    return {"_raw": str(args)}


class StubLlmClient:
    def __init__(self, fn: Optional[Callable[[Request], Awaitable[Result]]] = None):
        self.fn = fn

    async def generate(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        on_reasoning: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> LlmResult:
        request: Request = {"system": system, "messages": messages, "tools": tools}
        result = await self._call(request)
        lr = self._normalize(result)
        if on_reasoning is not None and lr.reasoning:
            r = on_reasoning(lr.reasoning)
            if asyncio.iscoroutine(r):
                await r
        if on_token is not None and lr.text:
            # 桩不支持真流式：整段文本回调一次（供上层验证 text_delta 通路）
            r = on_token(lr.text)
            if asyncio.iscoroutine(r):
                await r
        return lr

    async def _call(self, request: Request) -> Result:
        if self.fn is None:
            return {"text": "[stub] no fn", "tool_calls": []}
        res = self.fn(request)
        if asyncio.iscoroutine(res):
            res = await res
        return res

    def _normalize(self, result: Result) -> LlmResult:
        if not isinstance(result, dict):
            return LlmResult(text=str(result) if result is not None else "", tool_calls=[])
        text = result.get("text") or result.get("content") or ""
        reasoning = str(result.get("reasoning") or "")
        tool_calls: List[Dict[str, Any]] = []
        for tc in result.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tool_calls.append(
                {
                    "id": tc.get("id") or str(uuid.uuid4()),
                    "name": tc.get("name"),
                    "arguments": _parse_args(tc.get("arguments", {})),
                }
            )
        return LlmResult(text=text, reasoning=reasoning, tool_calls=tool_calls, raw=result,
                         usage=result.get("usage"), provider="stub", model="stub")