"""agent_loop package — NPC 对话专用 AgentLoop 框架（各司其职，分层分权）"""

# ---------------------------------------------------------------- 身份信息
# 署名水印的唯一事实来源（Python 侧）。改这里即可，启动日志会自动带上。
# C# 侧对应 csharp/About.cs，两边请保持一致。
#
# ⚠️ 这同时是**授权声明的一部分**：二次打包者若不移除这些常量，任何流出的副本
#    都会在启动日志里打出原作者信息；要抹掉就得主动改代码。
#    条款见仓库根 README「授权与声明」与 LICENSE。
__mod_name__ = "八荒智能体"
__version__ = "1.0.0"
__author__ = "iuu-zyh"
__repo__ = "https://github.com/iuu-zyh/agent_loop"
__license__ = "PolyForm Noncommercial 1.0.0"

from .agent_loop import AgentLoop
from .session import Session
from .inbox import Inbox
from .system_prompt import SystemPrompt
from .history import project_ui_history

# 桥与工具按需导入，不强制
try:
    from .bridge import GameBridge, StubGameBridge, WsGameBridge
except Exception:
    GameBridge = StubGameBridge = WsGameBridge = None  # type: ignore

try:
    from .tools import ALL_TOOL_SCHEMAS, TOOL_ORDER
except Exception:
    ALL_TOOL_SCHEMAS = TOOL_ORDER = None  # type: ignore

try:
    from .llm import LlmClient, LlmResult, OpenAILlmClient, StubLlmClient, EchoLlmClient, create_llm_client
except Exception:
    LlmClient = LlmResult = OpenAILlmClient = StubLlmClient = EchoLlmClient = create_llm_client = None  # type: ignore

try:
    from .compaction import Compressor, ToolResultPruner, CompactError
except Exception:
    Compressor = ToolResultPruner = CompactError = None  # type: ignore

try:
    from .stats import UsageTracker, map_usage, format_cache_hit_percent
except Exception:
    UsageTracker = map_usage = format_cache_hit_percent = None  # type: ignore

__all__ = [
    "AgentLoop", "Session", "Inbox", "SystemPrompt", "project_ui_history", "GameBridge", "StubGameBridge", "WsGameBridge",
    "ALL_TOOL_SCHEMAS", "TOOL_ORDER",
    "LlmClient", "LlmResult", "OpenAILlmClient", "StubLlmClient", "EchoLlmClient", "create_llm_client",
    "Compressor", "ToolResultPruner", "CompactError",
    "UsageTracker", "map_usage", "format_cache_hit_percent",
]
