"""llm — LLM 适配层：LlmClient 协议 + openai/anthropic/stub/echo 实现 + factory/router
（各司其职，循环机只认 LlmClient）"""

from .base import LlmClient, LlmResult
from .openai_client import OpenAILlmClient
from .anthropic_client import AnthropicLlmClient
from .responses_client import OpenAIResponsesLlmClient
from .auto_client import AutoProtocolLlmClient
from .stub_client import StubLlmClient
from .echo_client import EchoLlmClient
from .factory import create_llm_client
from .router import LlmRouter

__all__ = ["LlmClient", "LlmResult", "OpenAILlmClient", "AnthropicLlmClient", "OpenAIResponsesLlmClient",
           "AutoProtocolLlmClient", "StubLlmClient", "EchoLlmClient", "create_llm_client", "LlmRouter"]
