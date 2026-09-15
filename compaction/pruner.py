"""compaction/pruner — 工具结果压缩（LLM 摘要优先 + 确定性 head/marker/tail 兜底）

对标 DSH dsh-compaction-tool-result-pruner，但针对本项目（对话交互）把主路径改为
语义摘要：
- 主：复用注入的 `LlmClient` 对超预算 `tool/result` 做语义摘要。对话场景关键信息常
  分布在中段，纯 head/tail 折叠会把性格/关系/剧情等剪掉，语义摘要更贴合。
- 兜底：LLM 不可用（llm=None）或摘要失败时，退化为确定性 head/marker/tail 折叠，
  保证 fail-safe、不依赖 LLM。

只修 `tool/result` 的 content；不碰对话历史（那是 compress 的职责）。
摘要指令取自内置常量 `TOOL_PRUNE_INSTRUCTION`——**统一、不可配置、不读 prompts/**；
用户可自定义的只有历史压缩指令（`prompts/compaction/{npc_id}.md → default.md`）。
替换走现有 `surfaceOp: replace` + `sourceEventSeqs`，重放安全；
前插 `compaction/prune` 记账事件供 meter 统计不失真。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from . import meter
from .. import log_setup

log = log_setup.get_logger(__name__)

# 默认固定替代中段标记（确定性兜底用）
PRUNE_MARKER = "\n\n[... 工具结果中间已折叠 ...]\n\n"

# 工具结果压缩指令（**内置、统一、不可配置**）。
# 设计约定：所有 tool/result 共用这一条指令，不开放给配置 UI 修改；
# 用户可自定义的只有**历史压缩**指令（prompts/compaction/{npc_id}.md → default.md）。
# 此前 pruner 也会读 `{session_id}.md`，与历史压缩**撞用同一个文件**，而两者输出格式不同
# （<tool-result-summary> vs <compacted-summary>），一份文件无法同时满足 —— 故彻底解耦。
TOOL_PRUNE_INSTRUCTION = (
    "你是工具结果压缩器。把用户消息中 <tool-result-raw> 标签内的工具返回结果压缩成精简要点，"
    "用于在角色扮演对话中保留关键信息。\n"
    "要求：\n"
    "- 保留对后续对话最重要的信息：涉及的 NPC 人物、关系与称谓、当前剧情/事件进展、约定与承诺、关键数值与地点。\n"
    "- 省略无谓的模板化、重复性、技术性噪音（如内部字段名、调试信息、大段无关列表）。\n"
    "- 保持客观陈述，不暴露 AI/模型/程序身份，不添加原文没有的信息。\n"
    "- 只输出要点本身，不要回显原文、不要续写对话、不要调用工具。\n"
    "- 尽量精炼，用中文输出，不要长于原文的 1/4。"
)


def _codepoint_len(text: str) -> int:
    return len(text)  # Python str 迭代即码点，中文安全


class ToolResultPruner:
    """工具结果压缩：LLM 语义摘要优先，LLM 缺失/失败则确定性 head/tail 折叠。

    复用 `LlmClient.generate(system, messages, tools)` 单一接口做摘要（与 compress
    同源）。不内嵌 wire 翻译、不感知具体后端，保持各司其职。
    """

    def __init__(
        self,
        threshold_chars: int = 8192,
        head_chars: int = 4096,
        tail_chars: int = 1024,
        marker: str = PRUNE_MARKER,
        llm: Any = None,
        max_parallel: int = 5,
    ):
        if threshold_chars <= 0 or head_chars < 0 or tail_chars < 0:
            raise ValueError("prune 预算必须为正（threshold>0, head>=0, tail>=0）")
        if head_chars + _codepoint_len(marker) + tail_chars > threshold_chars:
            raise ValueError("head + marker + tail 不得超过 threshold")
        self.threshold_chars = threshold_chars
        self.head_chars = head_chars
        self.tail_chars = tail_chars
        self.marker = marker
        # 摘要 LLM（复用 LlmClient 接口）；None 则纯确定性
        self.llm = llm
        # 内容解析的并发上限（超预算结果多条时避免串行 N 次 LLM 调用）
        self.max_parallel = max(1, int(max_parallel))

    # ---------- 单条 tool-result 内容：LLM 语义摘要 ----------
    def _join_text(self, blocks: List[Dict[str, Any]]) -> str:
        """把一组 block 里的 text 按顺序拼成一段原文。"""
        return "".join(b.get("text", "") or "" for b in blocks if isinstance(b, dict) and b.get("type") == "text")

    async def _summarize_result(self, blocks: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """用 LlmClient 对 tool-result 文本做语义摘要，成功返回新 content 块列表；失败返回 None。"""
        raw = self._join_text(blocks)
        if not raw:
            return None
        # 指令整体作为 system（与 compress 同构）：指令是"角色 + 规则"，天然属于 system；
        # 用户消息只放待处理材料。指令取自内置常量 `TOOL_PRUNE_INSTRUCTION`（统一、不可配置）。
        messages = [{"role": "user", "content": [
            {"type": "text", "text": f"<tool-result-raw>\n{raw}\n</tool-result-raw>"}
        ]}]
        try:
            # 同 compress：此处是一次"静默等待"的 LLM 调用，用 stage 包住才能看见卡顿
            with log_setup.stage("compact.prune.summarize"):
                result = await self.llm.generate(TOOL_PRUNE_INSTRUCTION, messages, [])
        except Exception as e:
            # 曾经静默 return None：pruner 失败会让压缩退化为确定性头尾裁剪，原因完全不可见
            log.warning("工具结果摘要 LLM 调用失败（%s）：%s", type(e).__name__, e, exc_info=True)
            return None
        text = (getattr(result, "text", "") or "").strip()
        if not text:
            return None
        return [{"type": "text", "text": text}]

    # ---------- 单条 tool-result 内容：确定性修剪 ----------
    def _text_total(self, blocks: List[Dict[str, Any]]) -> int:
        n = 0
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "text":
                n += _codepoint_len(b.get("text", "") or "")
        return n

    def _prune_text_blocks(self, blocks: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """确定性兜底：超预算则替换 text 中段；未超返回 None。非 text 块（如图片）保序保留。"""
        total = self._text_total(blocks)
        if total <= self.threshold_chars:
            return None
        removed_start = self.head_chars
        removed_end = total - self.tail_chars
        pruned: List[Dict[str, Any]] = []
        consumed = 0
        marker_done = False
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text":
                pruned.append(block)
                continue
            points = list(block.get("text", "") or "")
            block_start = consumed
            block_end = block_start + len(points)
            head_end = min(len(points), max(0, removed_start - block_start))
            tail_start = min(len(points), max(0, removed_end - block_start))
            marker = self.marker if (not marker_done and block_start < removed_end and block_end > removed_start) else ""
            if marker:
                marker_done = True
            text = "".join(points[:head_end]) + marker + "".join(points[tail_start:])
            if text:
                pruned.append({**block, "text": text})
            consumed = block_end
        if not marker_done:
            return None
        return pruned

    # ---------- 主入口：两条路径合流 ----------
    async def _resolve_content(self, blocks: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """优先 LLM 语义摘要；LLM 缺失/失败则退确定性折叠。均无法压缩返回 None。

        **门槛先行（09-11 修）**：只有超预算的 tool-result 才值得压。此前门槛只加在确定性
        折叠那条路上，LLM 分支不做长度检查 → 每条（哪怕 25 字）都被白送一次 LLM 摘要，
        实测 18 条串行 = 358 秒，且把本可无损保留的短结果改写成了摘要（无谓信息损耗）。
        """
        if self._text_total(blocks) <= self.threshold_chars:
            return None
        if self.llm is not None:
            llm_pruned = await self._summarize_result(blocks)
            if llm_pruned is not None:
                return llm_pruned
        return self._prune_text_blocks(blocks)

    async def prune_session(self, session) -> Dict[str, Any]:
        """扫当前 surface 里所有超预算 tool/result，压缩（LLM 摘要或确定性折叠）+ 记账。

        **并发解析（09-11 修）**：先同步扫出候选，再以有界并发解析内容——此前在扫描循环里
        逐条 `await`，N 条超预算结果 = N 次串行 LLM 调用（每条十几秒），是"压缩好几分钟"的主因。
        记账/替换仍按原顺序同步执行（`session.append` 需保持确定性顺序）。
        """
        todo: List[Tuple[int, Any, Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]] = []
        for seq in list(session.surface.nodes):
            ev = session.log[seq]
            if ev.get("type") != "tool/result":
                continue
            message = (ev.get("data") or {}).get("message")
            if not isinstance(message, dict):
                continue
            result_block = next(
                (c for c in message.get("content", []) if isinstance(c, dict) and c.get("type") == "tool-result"),
                None,
            )
            if result_block is None:
                continue
            todo.append((seq, ev, message, result_block, result_block.get("content") or []))

        sem = asyncio.Semaphore(max(1, int(self.max_parallel)))

        async def _resolve_one(item):
            seq, ev, message, result_block, content = item
            async with sem:
                try:
                    pruned = await self._resolve_content(content)
                except Exception:
                    log.warning("工具结果压缩失败（seq=%s），保持原文", seq, exc_info=True)
                    pruned = None
            return seq, ev, message, result_block, pruned

        resolved = await asyncio.gather(*[_resolve_one(t) for t in todo]) if todo else []
        candidates = [r for r in resolved if r[4] is not None]

        pruned_records = []
        chars_removed = 0
        for seq, ev, message, result_block, pruned in candidates:
            before = self._text_total(result_block.get("content") or [])
            after = self._text_total(pruned)
            new_content = [c for c in message.get("content", []) if c is not result_block]
            new_content.insert(0, {**result_block, "content": pruned})
            new_msg = {**message, "content": new_content}
            # 记账：被替换段的价格（meter 用它维护客观统计）
            session.append(
                "compaction/prune",
                {
                    "shadowedRange": {"start": seq, "end": seq},
                    "shadowedSeqs": [seq],
                    "shadowedTokenCount": meter.estimate_message(message),
                },
            )
            session.append(
                "tool/result",
                {**ev.get("data", {}), "message": new_msg},
                {"surfaceOp": {"op": "replace", "start": seq, "end": seq}, "sourceEventSeqs": [seq]},
            )
            pruned_records.append({"originalSeq": seq, "callId": result_block.get("toolCallId"), "charsBefore": before, "charsAfter": after})
            chars_removed += before - after

        return {"pruned": pruned_records, "charsRemoved": chars_removed}