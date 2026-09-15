"""llm/router — LlmClient 门面路由：swap 热换内芯（Agent / Compressor 零改动）

分层职责（与 factory 各司其职）：
- factory 只管「怎么建」（参数 > env > config → Echo 兜底），router 只管「何时换」，
  Agent/DialogueAgent/Compressor 只认 LlmClient 协议单口 generate，永不知内芯可换。
- 装配层（scripts/server.py）建 router 注入全部消费者；config 改动经 set_config 后
  由装配层调 swap(factory 重建的新芯)，实现「大模型是 Agent 的插拔对象」。

换芯无撕裂约定：generate 开始的瞬间绑定当前内芯引用——swap 后新调用走新芯，
进行中的调用持旧引用收尾（流式不中断、已产 token 不重复）。
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from .base import LlmResult


class LlmRouter:
    """实现 LlmClient 协议的门面：generate 委托当前内芯，swap 原子换内芯。

    用法：
        router = LlmRouter(create_llm_client())
        ... router 注入 AgentLoop / Compressor ...
        router.swap(create_llm_client())   # config 改动后热换
    """

    def __init__(self, client: Any):
        if client is None:
            raise ValueError("LlmRouter 需要一个初始 LlmClient 实例")
        self._client = client

    # ---------- 当前内芯 ----------
    @property
    def client(self) -> Any:
        """当前内芯（仅供装配层审计/日志，业务侧一律走 generate）。"""
        return self._client

    def swap(self, new_client: Any) -> Any:
        """原子换内芯（单次赋值）：返回旧芯，None 拒绝。进行中的 generate 用旧芯收尾。"""
        if new_client is None:
            raise ValueError("swap 需要一个新的 LlmClient 实例")
        old = self._client
        self._client = new_client
        return old

    # ---------- LlmClient 协议（唯一业务口） ----------
    async def generate(
        self,
        system: str,
        messages: List[dict],
        tools: List[dict],
        on_token: Optional[Callable[[str], Any]] = None,
        on_reasoning: Optional[Callable[[str], Any]] = None,
    ) -> LlmResult:
        client = self._client  # 入口绑定：此后 swap 不影响本次调用（旧芯收尾，无撕裂）
        return await client.generate(system, messages, tools, on_token=on_token, on_reasoning=on_reasoning)
