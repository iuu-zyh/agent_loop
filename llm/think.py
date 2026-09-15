"""llm/think — 模型思考内容分流（两来源归一，纯函数可测）

两种真实形态（实测 mimo-v2.5-free 等 reasoning 模型）：
- 来源①（首选）：OpenAI 兼容扩展字段 `reasoning_content`（非流式 message / 流式 delta）——
  content 本身是干净正文，**由 openai_client 直接提取**，不经过本模块的标签拆分。
- 来源②（兜底）：content 内嵌标签 `<think>...</think>` / `<thought>...</thought>` /
  `<reasoning>...</reasoning>`（vLLM 裸跑 R1 等部署常见）——本模块负责拆分。

提供两个工具：
- split_think(text)：一次性拆分（非流式 / 历史投影用）→ (reasoning, body)
- ThinkSplitter：流式增量状态机（逐 token 喂入，处理标签跨 token 切分），回调 (chunk, part)

账本约定：assistant/message 落盘**原文**（保真实、模型下轮可见），reasoning 只进观察者/UI 层。
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

# 识别的思考标签（小写归一；开/闭都容忍空白）
_THINK_TAGS = ("think", "thought", "reasoning")
_OPEN_RE = re.compile(r"<\s*(" + "|".join(_THINK_TAGS) + r")\s*>", re.IGNORECASE)
_CLOSE_RE = re.compile(r"</\s*(" + "|".join(_THINK_TAGS) + r")\s*>", re.IGNORECASE)

# 完整标签串（含开/闭），供流式「部分标签尾巴」判定
_ALL_TAG_STRINGS: Tuple[str, ...] = tuple(
    f"<{t}>" for t in _THINK_TAGS
) + tuple(
    f"</{t}>" for t in _THINK_TAGS
)
_MAX_TAG_LEN = max(len(s) for s in _ALL_TAG_STRINGS)


def split_think(text: Optional[str]) -> Tuple[str, str]:
    """一次性拆分 content 内嵌思考标签 → (reasoning, body)。

    - 成对标签：标签内归 reasoning，标签外归 body（可多段、可交错）
    - 未闭合：剩余部分整体归 reasoning（流被截断的兜底）
    - 无标签：返回 ("", 原文)
    """
    if not text:
        return "", text or ""
    reasoning: List[str] = []
    body: List[str] = []
    rest = text
    while rest:
        m = _OPEN_RE.search(rest)
        if m is None:
            body.append(rest)
            break
        if m.start() > 0:
            body.append(rest[: m.start()])
        tag = m.group(1)
        m2 = re.search(r"</\s*" + tag + r"\s*>", rest[m.end():], re.IGNORECASE)
        if m2 is None:
            reasoning.append(rest[m.end():])   # 未闭合兜底
            rest = ""
            break
        reasoning.append(rest[m.end(): m.end() + m2.start()])
        rest = rest[m.end() + m2.end():]
    return "".join(reasoning).strip(), "".join(body).strip()


def _partial_tag_len(tail: str) -> int:
    """tail 可能是某个完整标签的前缀（标签被切成两半）→ 返回该尾巴长度；否则 0。"""
    for k in range(min(len(tail), _MAX_TAG_LEN), 0, -1):
        s = tail[-k:]
        if any(t.startswith(s) for t in _ALL_TAG_STRINGS):
            return k
    return 0


class ThinkSplitter:
    """流式增量分流器：逐 token 喂 feed()，把 content 流按内嵌思考标签切成两通道。

    用法：
        sp = ThinkSplitter()
        sp.feed(delta, on_body=..., on_think=...)   # 标签跨 token 时自动缓冲判定
        sp.flush(on_body, on_think)                  # 流结束调用，吐出缓冲尾巴
    """

    def __init__(self):
        self._in_think = False
        self._pending = ""   # 尾部可能是「半个标签」的部分，缓冲到下一 token 判定

    def feed(self, token: Optional[str], on_body: Callable[[str], None], on_think: Callable[[str], None]) -> None:
        buf = self._pending + (token or "")
        self._pending = ""
        out: List[Tuple[str, str]] = []
        i, n = 0, len(buf)
        while i < n:
            m = (_CLOSE_RE if self._in_think else _OPEN_RE).search(buf, i)
            if m is not None:
                if m.start() > i:
                    out.append(("think" if self._in_think else "body", buf[i: m.start()]))
                i = m.end()
                self._in_think = not self._in_think
                continue
            # 无完整标签：除「可能是半个标签」的尾巴外全部吐出
            keep = _partial_tag_len(buf[i:])
            if n - keep > i:
                out.append(("think" if self._in_think else "body", buf[i: n - keep]))
            self._pending = buf[n - keep:]
            i = n
        for part, chunk in out:
            (on_think if part == "think" else on_body)(chunk)

    def flush(self, on_body: Callable[[str], None], on_think: Callable[[str], None]) -> None:
        """流结束：缓冲尾巴按当前通道吐出。"""
        if self._pending:
            chunk, self._pending = self._pending, ""
            (on_think if self._in_think else on_body)(chunk)
