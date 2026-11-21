"""compaction/meter — token 估算（纯函数，供 compress 与 pruner 共用）

对标 DSH dsh-token-meter，但按本项目（中文角色扮演对话）修正计量：
- 非 CJK 字符按 `CHARS_PER_TOKEN` 字≈1 token（DSH 原版）
- CJK 汉字约 1 字≈1 token（对话几乎全中文，原启发式会严重低估）
纯测量，不改 session、不触发，保证两处消费者对同一内容估出同一数。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional

# 非 CJK 启发式密度（对齐 DSH）
CHARS_PER_TOKEN = 4
# 每个内容块的结构开销（JSON 包裹 + 类型标签）
BLOCK_OVERHEAD = 4
# CJK 计量：1 汉字 ≈ 1 token
CJK_TOKEN_PER_CHAR = 1
# 每条消息的 role framing 开销
MESSAGE_FRAMING = 4

# ---------- 图片（10-18：图片进上下文之后，估算必须认得它）----------
# 口径：取两家常见视觉计价的**较大者**做保守估计（宁可早一点压缩，也不要爆窗）：
#   · Anthropic 类： ceil(w*h / 750)
#   · OpenAI high-detail： 85 + 170 × ceil(w/512) × ceil(h/512)
# 这仍然只是**估算** —— 真实花费以实测 `usage` 为准；估算只喂压缩阈值与面板的「≈」。
IMAGE_MIN_TOKENS = 85
IMAGE_PX_PER_TOKEN = 750
IMAGE_TILE = 512
IMAGE_TILE_TOKENS = 170
# 没有宽高时按体积粗估（≈1 token / KB），只用于程序化调用/老数据兜底
IMAGE_TOKENS_PER_KB = 1.0

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

#: CJK 判定正则（**编译一次，进程内复用**）。
#:
#: ★ 为什么从"逐字符 + 9 次区间比较"改成正则 ★（11-15，用户报「用久了会卡」）
#: 旧实现是纯 Python 循环：每个字符都要跑一遍 `any(lo <= cp <= hi for ...)`。
#: 而它是**每回合**都要对**整个历史**跑一遍的（`Compressor.maybe` 每回合估一次上下文压力），
#: 历史里又堆满工具结果（一条几千字很常见）。实测：
#:   · 2 万字 → 4.8ms；20 万字 → 50ms；100 万字 → 245ms
#: 而一个 NPC 聊到后期，历史里几十万字是常态 ⇒ **每回合多卡几百毫秒**，
#: 且这个成本随对话线性增长 —— 这正是「用久了会卡」的主因之一。
#: 换成正则后同样内容 0.7ms（约 3.6 倍），且 `re` 在 C 层跑，不产生 Python 层对象。
#:
#: 正则**逐字区间与 `_CJK_RANGES` 完全同构**（有测试逐码点核对两者等价），
#: 别只改一边 —— 改了区间却忘了正则会让估算静默漂移，而估算喂的是压缩阈值。
_CJK_RE = re.compile(
    "[" + "".join(f"\\U{lo:08x}-\\U{hi:08x}" for lo, hi in _CJK_RANGES) + "]"
)


def _is_cjk(ch: str) -> bool:
    """单字符判定（保留给需要逐个判断的调用方；批量走 `estimate_text` 的正则）。"""
    return _CJK_RE.match(ch) is not None


def estimate_text(text: str) -> int:
    """按 CJK 密度加权估算一段文本的 token 数。

    ★ 性能（11-15）：这是**热路径**（每回合对整个历史跑一遍），所以 CJK 计数走
    `re.findall` 而不是 Python 逐字符循环 —— 见 `_CJK_RE` 的注释。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    non_cjk = len(text) - cjk
    cjk_tokens = cjk * CJK_TOKEN_PER_CHAR
    non_cjk_tokens = math.ceil(non_cjk / CHARS_PER_TOKEN)
    return cjk_tokens + non_cjk_tokens


def estimate_image_tokens(tokens: Any = None, w: Any = None, h: Any = None,
                          kb: Any = None, url: Any = None) -> int:
    """估**一张图**的 token 数（优先级：显式记录值 > 宽高 > 体积 > 下限）。

    `tokens` 是收图时（`DialogueAgent._ingest_images`）按 w/h 算好、写进账本块里的数：
    账本是估算的唯一输入，所以这里**优先用它** —— 两边算出同一个数，压缩器与
    `refresh_context_pressure` 才对得上（同一把尺子，见 `Compressor` 的注释）。

    ★ 绝不 `json.dumps` 整块算：`url` 若是 data URI，几万字符会被估成几万 token
      （一张图吃掉整个窗口）—— 旧实现 `estimate_blocks` 的 else 分支正是这个形状的地雷。
    """
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
        return tokens
    try:
        wi, hi = int(w), int(h)
    except (TypeError, ValueError):
        wi = hi = 0
    if wi > 0 and hi > 0:
        by_area = math.ceil(wi * hi / IMAGE_PX_PER_TOKEN)
        by_tile = IMAGE_MIN_TOKENS + IMAGE_TILE_TOKENS * math.ceil(wi / IMAGE_TILE) * math.ceil(hi / IMAGE_TILE)
        return max(IMAGE_MIN_TOKENS, by_area, by_tile)
    try:
        kbv = float(kb)
    except (TypeError, ValueError):
        kbv = 0.0
    if kbv <= 0 and isinstance(url, str) and url.startswith("data:"):
        # 只有 data URI（老形态/直连块）：按 base64 体积反推原字节
        _head, _sep, payload = url.partition(",")
        if "base64" in _head:
            kbv = max(0.0, len(payload) * 3.0 / 4.0 / 1024.0)
    if kbv > 0:
        return max(IMAGE_MIN_TOKENS, int(math.ceil(kbv * IMAGE_TOKENS_PER_KB)))
    return IMAGE_MIN_TOKENS


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
        elif t in ("image", "image_url"):
            # 图片是**按张**计价的（与像素/切片有关，与字节数关系不大）：
            # 走 estimate_image_tokens，绝不落到下面的 else 分支去 dump 整块。
            tokens += estimate_image_tokens(
                tokens=block.get("tokens"), w=block.get("w"), h=block.get("h"),
                kb=block.get("kb"), url=block.get("url"),
            ) + BLOCK_OVERHEAD
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