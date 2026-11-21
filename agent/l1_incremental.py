"""L1「经历类」段（近况 / 与玩家相关）的**增量差分**核。

## 为什么需要它
这两个段原来是「取最近 N 条 → 全量成一个字符串 → 文本变了就整段重发」。
而经历日志是**只增**的：这个 NPC 每打一次副本、每升一次境界，段文本就整体变一次
⇒ **一条新增 = 整段重发**。实测（三个存档 20 个会话、313 条段消息）：
「近况」占总字数 **31%**、均长 301 字；「与玩家相关」占 9%、均长 208 字（旧版 5 条全量）。
合计约 40%，是 L1 里最大的一块。

## 口径（10-25 用户拍板）
两级去重，**顺序不能反**：
  ① **先按月份**：上次停在 5 月、现在是 6 月 ⇒ 只发 6 月的条目，不回头重发 5 月那些。
  ② **同月内按文本**匹配 ⇒ 只发新增的那几条。
实现上两者合流成一条：C# 给每条都**已经带了** `(N年M月) ` 前缀
（`UnitSnapshot.RecentTexts` / `RecentWithPlayer` 的 `CnYearMonth`），
所以「月份 + 文本」的唯一键就是**那条成品字符串本身**。
同一个事件在不同月发生 ⇒ 前缀不同 ⇒ 键不同 ⇒ 两条都发（这正是我们要的）。

## 不做全量重发，代价是什么（必须说清）
增量语义依赖"模型还看得见之前那条消息"。若这段被**上下文压缩**折走，
模型就永久失去更早的经历。所以：
  · `reset()` 是给"基线作废"用的 —— 压缩把它折走、或重开会话时调用，
    下一次渲染会**重新发全量**（这是"声明新状态"的唯一手段，与 `where` 段的 ★ 同一条道理）。
  · 调用方（`DialogueAgent._resync_runtime_ctx_baselines` 的 ② 段）已经在做这件事了，
    接上即可，不需要新机制。

## 为什么"上次那个增量文本"要留着
差分判据是「本次渲染 == 上次发的」⇒ 不相等才发。若"没有新增"时返回空串，
则紧随其后的每一步都会把这一段判成 `remove`（UI 上反复出现"已移除"），
而且段会从容器里消失、下次重新出现又是一次 `set`。
留着上一次的增量文本 ⇒ 无新增时文本稳定 ⇒ 不发消息、不发 remove。

纯逻辑、零依赖：由 `csharp/tests/...` 之外的 `tests/test_l1_incremental_segments.py` 直接测。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple


class IncrementalSegment:
    """一段「只增」内容的增量成文器（每个 NPC × 每个段各一个实例）。

    用法（在 `SystemPrompt.format_l1_context` 里）::

        seg = self._incr(scope, "recent")
        text = seg.render(items, renderer=..., marker="新增")
        # items = 当前的完整候选列表（已按新→旧排好、已截断）
    """

    def __init__(self) -> None:
        # 已发过的条目键（= 那条成品字符串本身，见模块头：月份已嵌在其中）
        self._sent: set = set()
        # 上一次渲染出的文本（"没有新增"时原样返回，避免空串引发 remove 抖动）
        self._last: str = ""

    # ------------------------------------------------------------------ 只读

    @property
    def last_text(self) -> str:
        return self._last

    @property
    def sent_count(self) -> int:
        return len(self._sent)

    def has_sent(self) -> bool:
        return bool(self._sent)

    # ------------------------------------------------------------------ 主逻辑

    def render(
        self,
        items: Sequence[str],
        renderer,
        marker: str = "",
    ) -> str:
        """算出本次该发的文本。

        · **首次**（没发过任何东西）：全量渲染，不加 marker（那是初始状态声明）；
        · **有新增**：只渲染新增的那些，加 `marker` 前缀（告诉模型"这是追加的"）；
        · **无新增**：原样返回上次的文本（可能为空串）—— 差分因此不触发。

        `renderer(list[str]) -> str` 由调用方给（编号、分隔符、措辞都在那边，
        本类只管"发哪些"，不管"怎么排"）。
        """
        seq: List[str] = []
        seen_now: set = set()
        for t in items or ():
            s = (t or "").strip()
            if not s:
                continue
            # 同一批里重复（C# 已按 month|text 去重，这里再兜一层：Python 侧可能合并多源）
            if s in seen_now:
                continue
            seen_now.add(s)
            seq.append(s)

        fresh = [s for s in seq if s not in self._sent]
        if not fresh:
            # 没有新增（含"候选整个为空"）：**原样返回上次的文本**。
            #   · 候选为空 ≠ 经历没了 —— 可能只是这一轮 C# 没取到（读经历有已知的
            #     "首访问命中未就绪缓存"问题，见 `UnitSnapshot.BuildLogs`）；把它当成
            #     "内容变空"会 emit `remove`，段从容器里消失、UI 反复显示"已移除"。
            #   · 真正的清空只由 `reset()` 负责。
            # （这两条原本是两个分支，行为等价 —— 合并成一处，免得读的人以为
            #   "候选为空"还有另一条专门的路径。）
            return self._last

        first = not self._sent
        for s in fresh:
            self._sent.add(s)
        body = renderer(fresh)
        if not body:
            return self._last
        self._last = body if first or not marker else (marker + body)
        return self._last

    def reset(self) -> None:
        """基线作废（压缩折走 / 重开会话 / 换人）⇒ 下次渲染重发**全量**。"""
        self._sent.clear()
        self._last = ""


class IncrementalRegistry:
    """`(scope, segment)` → `IncrementalSegment`。作用域隔离，换档/关窗可整批丢。"""

    def __init__(self) -> None:
        self._m: Dict[Tuple[str, str], IncrementalSegment] = {}

    def get(self, scope: str, name: str) -> IncrementalSegment:
        k = (str(scope or ""), str(name or ""))
        seg = self._m.get(k)
        if seg is None:
            seg = IncrementalSegment()
            self._m[k] = seg
        return seg

    def reset(self, scope: str, name: str) -> None:
        seg = self._m.get((str(scope or ""), str(name or "")))
        if seg is not None:
            seg.reset()

    def reset_scope(self, scope: str) -> None:
        s = str(scope or "")
        for k in [k for k in self._m if k[0] == s]:
            self._m.pop(k, None)

    def clear(self) -> None:
        self._m.clear()
