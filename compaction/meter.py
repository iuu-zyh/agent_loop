"""compaction/meter — token 估算（纯函数，供 compress 与 pruner 共用）

对标 DSH dsh-token-meter，但按本项目（中文角色扮演对话）修正计量：
- 非 CJK 字符按 `CHARS_PER_TOKEN` 字≈1 token（DSH 原版）
- CJK 汉字约 1 字≈1 token（对话几乎全中文，原启发式会严重低估）
纯测量，不改 session、不触发，保证两处消费者对同一内容估出同一数。
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

# 非 CJK 启发式密度（对齐 DSH）
CHARS_PER_TOKEN = 4
# 每个内容块的结构开销（JSON 包裹 + 类型标签）
BLOCK_OVERHEAD = 4
# CJK 计量：1 汉字 ≈ 1 token
CJK_TOKEN_PER_CHAR = 1
# 每条消息的 role framing 开销
MESSAGE_FRAMING = 4

# CJK 常用区块（含中文标点、全角符号）
_CJK_RANGES = (
    (0x4E00, 0x9FFF),   # 统一汉字
    (0x3400, 0x4DBF),   # 扩展A
    (0xF900, 0xFAFF),   # 兼容汉字
    (0x3000, 0x303F),   # 中文标点
    (0xFF00, 0xFFEF),   # 全角
    (0x2018, 0x201F),   # 成对引号/省略号等
    (0x2013, 0x2014),
    (0x2026, 0x2026),   # …
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def estimate_text(text: str) -> int:
    """按 CJK 密度加权估算一段文本的 token 数。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    non_cjk = len(text) - cjk
    cjk_tokens = cjk * CJK_TOKEN_PER_CHAR
    non_cjk_tokens = math.ceil(non_cjk / CHARS_PER_TOKEN)
    return cjk_tokens + non_cjk_tokens


def estimate_blocks(content: List[Dict[str, Any]]) -> int:
    """递归估一个 canonical content 块列表。"""
    tokens = 0
    for block in content:
        if not isinstance(block, dict):
            tokens += BLOCK_OVERHEAD + estimate_text(str(block))
            continue
        t = block.get("type")
        if t in ("text", "reasoning"):
            tokens += estimate_text(block.get("text", "") or "") + BLOCK_OVERHEAD
        elif t == "tool-call":
            tokens += estimate_text(block.get("name", "") or "") + estimate_text(block.get("arguments", "") or "") + BLOCK_OVERHEAD
        elif t == "tool-result":
            tokens += estimate_blocks(block.get("content") or []) + BLOCK_OVERHEAD
        else:
            tokens += BLOCK_OVERHEAD + estimate_text(json.dumps(block, ensure_ascii=False))
    return tokens


def estimate_message(message: Dict[str, Any]) -> int:
    """估一条 canonical 消息：内容 token + role framing。"""
    return estimate_blocks(message.get("content", []) or []) + MESSAGE_FRAMING


def estimate_system(system: str) -> int:
    return estimate_text(system or "") + MESSAGE_FRAMING


def estimate_tools(tools: List[Dict[str, Any]]) -> int:
    if not tools:
        return 0
    return estimate_text(json.dumps(tools, ensure_ascii=False)) + BLOCK_OVERHEAD


def estimate_header(header: Optional[Dict[str, Any]]) -> int:
    """估 canonical request/header：system + tools。header 缺省返回 0。"""
    if not header:
        return 0
    total = 0
    if header.get("system"):
        total += estimate_system(header["system"])
    if header.get("tools"):
        total += estimate_tools(header["tools"])
    return total