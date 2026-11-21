"""stats — 会话用量与耗时统计（纯计算 + 快照，不碰 WS、不碰 UI）

对齐 DSH（deepseek-harness @ 0.1.5）三投影的 Python 最小实现：
- tokenUsage：完整会话累计（uncached/output/cacheRead/cacheWrite 四桶互不重叠）
- sessionStats：全量轮/步计数与墙钟（llm/tool/TTFT/decode）
- contextPressure：最新 prompt 规模 + 下个请求预估 + 路由容量

本模块只算数、只出快照。WS 推送由 ws_channel.ChatHub 调 snapshot() 发送，
数字格式化（K/M、千分位、缓存率小数）由 C# UI 侧做，Python 只给原始整数
（另附 cache_hit_percent 字符串供透传，UI 可直接用也可重算）。

记账规则（与 DSH 一致）：
1. 同一 (turn, step) 的早样本（流式尾巴）与终样本（落盘消息）是同一笔钱，
   后值替换前值，不 double-count。实现只留一个 last 槽，依赖日志顺序保证：
   更晚 step 报数后，合法日志不再为更早 step 补数。
2. 同 step 内重试（llm/retry-started）是两次真实扣费，清槽后另计，不替换。
3. reasoning 已含在 output 内，不另加；DeepSeek/OpenAI 的 prompt_tokens 若含
   缓存命中，需先减掉 cacheRead 得到真正的未缓存输入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ---------- provider 用量 → 互不重叠四桶 ----------

def _as_dict(obj: Any) -> Dict[str, Any]:
    """dict / object 两种形态统一成 dict（None → {}）。"""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    out: Dict[str, Any] = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens",
              "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
              "prompt_tokens_details", "completion_tokens_details"):
        try:
            v = getattr(obj, k, None)
        except Exception:
            v = None
        if v is not None:
            out[k] = v
    return out


def map_usage(raw: Any) -> Optional[Dict[str, int]]:
    """把 provider 的 usage 换算成 DSH 互不重叠口径。

    接受 OpenAI 兼容形态（dict 或对象）：
      prompt_tokens / completion_tokens 必需；
      缓存读取 = prompt_tokens_details.cached_tokens ?? prompt_cache_hit_tokens；
      推理 = completion_tokens_details.reasoning_tokens（仅透传，不另计）。
    返回 None 表示无可用用量（如 stub/echo）；调用方应跳过累计。
    """
    d = _as_dict(raw)
    if not d:
        return None
    try:
        prompt = d.get("prompt_tokens")
        completion = d.get("completion_tokens")
        if not isinstance(prompt, int) or not isinstance(completion, int):
            # 对象形态兜底：开放兼容 int-like
            prompt = int(prompt)
            completion = int(completion)
    except Exception:
        return None
    if prompt < 0 or completion < 0:
        return None

    cache_read = 0
    details = d.get("prompt_tokens_details")
    if isinstance(details, dict):
        cr = details.get("cached_tokens")
        if isinstance(cr, int) and cr >= 0:
            cache_read = cr
    else:
        try:
            cr = getattr(details, "cached_tokens", None)
            if isinstance(cr, int) and cr >= 0:
                cache_read = cr
        except Exception:
            pass
    if cache_read == 0:
        hit = d.get("prompt_cache_hit_tokens")
        if isinstance(hit, int) and hit >= 0:
            cache_read = hit
        else:
            try:
                hit = getattr(raw, "prompt_cache_hit_tokens", None)
                if isinstance(hit, int) and hit >= 0:
                    cache_read = hit
            except Exception:
                pass
    # 防御：缓存数不可能超过 prompt 总数，超了按总数钳（脏数据不污染账本）
    if cache_read > prompt:
        cache_read = prompt
    uncached = prompt - cache_read

    out: Dict[str, int] = {
        "inputTokens": uncached,
        "outputTokens": completion,
        "cacheReadTokens": cache_read,
        "cacheWriteTokens": 0,  # OpenAI/DeepSeek 当前不报写入，留桶对齐 DSH
    }
    # 推理：仅透传，累计时不另加
    reasoning = None
    cdetails = d.get("completion_tokens_details")
    if isinstance(cdetails, dict):
        r = cdetails.get("reasoning_tokens")
        if isinstance(r, int) and r >= 0:
            reasoning = r
    if reasoning is not None:
        out["reasoningTokens"] = reasoning
    return out


# ---------- 缓存命中率（DSH 整数运算移植：非满命中绝不显示 100%） ----------

def _rounded_percent_units(cache_read: int, denominator: int, decimal_places: int) -> int:
    """把比率舍入到整数单位（decimal_places=0 → 百分点，=1 → 十分之一百分点），tie 向上。"""
    units_per_percent = 1 if decimal_places == 0 else 10
    scale = units_per_percent * 100
    doubled = scale * 2
    q = denominator // doubled
    r = denominator % doubled
    lo, hi = 0, scale
    while lo < hi:
        cand = (lo + hi + 1) // 2
        factor = cand * 2 - 1
        threshold = factor * q + -(-factor * r // doubled)  # ceil 除
        if cache_read >= threshold:
            lo = cand
        else:
            hi = cand - 1
    return lo


def usage_of(agent: Any) -> Dict[str, int]:
    """取某个 agent 当前的四桶用量（取不到 → 空表）。**绝不抛** —— 少记一笔不拖垮回合。"""
    try:
        get = getattr(getattr(agent, "stats", None), "usage_buckets", None)
        got = get() if callable(get) else None
        return dict(got) if isinstance(got, dict) else {}
    except Exception:
        return {}


def usage_delta(before: Dict[str, int], after: Dict[str, int]) -> Dict[str, int]:
    """两桶之差（只留正增量）。任何一项缺失按 0 处理。"""
    out: Dict[str, int] = {}
    for k, v in (after or {}).items():
        try:
            d = int(v) - int((before or {}).get(k, 0) or 0)
        except (TypeError, ValueError):
            continue
        if d > 0:
            out[str(k)] = d
    return out


def shape_group_usage(raw: Optional[Dict[str, Any]], *, turns: int = 0, steps: int = 0,
                      since: int = 0) -> Dict[str, Any]:
    """把「本群累计的四桶」整形成与 `tokenUsage` 同形的块（群聊窗底部那一行的口径）。

    为什么不复用某个成员的 `tokenUsage`（09-18 用户拍板的分工）：
      · 群聊里每个成员**各有各的账本与上下文** —— "各自的上下文/占用率"没有可加的语义，
        所以本群那一行**不报上下文**（报了就是把某一个人的数当全群的）；
      · 能诚实相加的只有**花费**：输入（= 缓存读 + 缓存写 + 未缓存）、输出、总额、缓存率；
      · 只算**群内回合**（RoomFlow 在成员回合前后取差归集，见 `UsageTracker.usage_buckets`），
        成员自己单聊花的钱不进这一块 —— 这正是"只计算群聊期间"。
    UI 必须把这一行标成"本群期间"（不然玩家会拿它跟单聊窗那行对比，以为是同一口径）。
    """
    d = raw if isinstance(raw, dict) else {}
    def _n(k: str) -> int:
        try:
            return max(0, int(d.get(k) or 0))
        except (TypeError, ValueError):
            return 0
    u, r, w, o = _n("inputTokens"), _n("cacheReadTokens"), _n("cacheWriteTokens"), _n("outputTokens")
    billed = u + r + w
    return {
        "uncachedInputTokens": u,
        "cacheReadTokens": r,
        "cacheWriteTokens": w,
        "inputTokens": billed,                 # 与 tokenUsage.billedInputTokens 同口径
        "outputTokens": o,
        "totalTokens": billed + o,
        "cacheHitPercent": format_cache_hit_percent(r, billed),
        "turns": max(0, int(turns or 0)),      # 群内跑过的成员回合数
        "steps": max(0, int(steps or 0)),
        "sinceCreated": max(0, int(since or 0)),   # 群建立时间戳（0 = 未知）
    }


def format_cache_hit_percent(cache_read: int, billed_input: int, decimal_places: int = 0) -> Optional[str]:
    """返回如 "79" / "99.95"（不带 % 号），billed==0 返回 None（整组不显示）。

    满命中（missed==0）才返回 "100"；非满命中若整数档会舍入成 100，则自动
    加小数位直到刚好低于 100（"99.9…x"），精度无上限。
    """
    if billed_input == 0:
        return None
    missed = billed_input - cache_read
    if missed == 0:
        return "100"
    units = _rounded_percent_units(cache_read, billed_input, decimal_places)
    full = 100 if decimal_places == 0 else 1000
    if units < full:
        if decimal_places == 0:
            return str(units)
        whole, tenth = divmod(units, 10)
        return str(whole) if tenth == 0 else f"{whole}.{tenth}"
    # 会舍入成 100 的非满命中：找最小可区分精度
    places = 1
    scaled_gap = missed * 200
    tens = billed_input // 10
    while scaled_gap <= tens:
        scaled_gap *= 10
        places += 1
    ones = billed_input % 10
    loss = 5
    for cand in range(1, 5):
        factor = cand * 2 + 1
        threshold = factor * tens + (factor * ones) // 10
        if scaled_gap <= threshold:
            loss = cand
            break
    return "99." + "9" * (places - 1) + str(10 - loss)


# ---------- 会话累计器 ----------

@dataclass
class UsageTracker:
    """一会话一实例（由 DialogueAgent 持有，随 agent 创建/销毁）。

    context_window：路由容量（config compaction.ctx_window），未知传 None，
    此时 contextPressure 快照不含占用率，UI 环不渲染。
    """

    context_window: Optional[int] = None
    uncached_input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # last 槽：同一 (turn, step) 替换用
    _last_key: Optional[tuple] = field(default=None, repr=False)
    _last_buckets: Optional[Dict[str, int]] = field(default=None, repr=False)
    # sessionStats 全量
    turns_seen: set = field(default_factory=set, repr=False)
    steps: int = 0
    tool_steps: int = 0  # 产出 tool_calls 的步数（只写参数、几乎零解码时长，不计入正文 TPS）
    llm_ms: float = 0.0
    tool_ms: float = 0.0
    ttft_ms: float = 0.0
    ttft_steps: int = 0
    decode_ms: float = 0.0  # DSH 口径：全部采样步（含工具步，用于对账）
    decode_tokens: int = 0
    text_decode_ms: float = 0.0  # 展示口径：仅纯文本步（不含 tool_calls），供 UI 显示 TPS
    text_decode_tokens: int = 0
    # contextPressure：最新 prompt 规模 + 采样时 surface
    pressure_tokens: Optional[int] = field(default=None, repr=False)
    _sampled_surface: Optional[int] = field(default=None, repr=False)
    surface_tokens: int = 0  # 调用方在压缩/删除/落盘时调 set_surface() 同步
    surface_turns: int = 0   # **现存**轮数（删除/压缩后 ≠ turns_seen——后者是累计）
    # 维护（压缩摘要）用量：**已经含在上面四桶里**（"总 = 输入 + 输出"必须始终成立，
    # 所以不另开互斥桶），这里只是"其中有多少是维护花的"的分解，供 UI 单列。
    # 口径：maintenance_input 是那次调用的**整份 prompt**（缓存 + 未缓存），与 UI 的
    # 「输入」（billedInputTokens）同口径。
    maintenance_input_tokens: int = 0
    maintenance_output_tokens: int = 0
    compaction_calls: int = 0

    # ----- 用量累计 -----
    def notify_retry(self, turn: int, step: int) -> None:
        """同 step 重试开始：清 last 槽，使下次样本另计（两次扣费）。"""
        if self._last_key == (turn, step):
            self._last_key = None
            self._last_buckets = None

    def add_usage(self, turn: int, step: int, raw_usage: Any) -> bool:
        """记一笔用量。返回 True=已计入，False=无可用用量被忽略。"""
        buckets = map_usage(raw_usage) if not (
            isinstance(raw_usage, dict) and "inputTokens" in raw_usage
        ) else raw_usage
        # 允许直接传已换算桶（含 reasoningTokens 可选）
        if buckets is None:
            return False
        try:
            u = int(buckets.get("inputTokens", 0))
            o = int(buckets.get("outputTokens", 0))
            r = int(buckets.get("cacheReadTokens", 0))
            w = int(buckets.get("cacheWriteTokens", 0))
        except Exception:
            return False
        if min(u, o, r, w) < 0:
            return False
        prev = self._last_buckets if self._last_key == (turn, step) else None
        if prev is not None:
            self.uncached_input_tokens -= prev["inputTokens"]
            self.output_tokens -= prev["outputTokens"]
            self.cache_read_tokens -= prev["cacheReadTokens"]
            self.cache_write_tokens -= prev["cacheWriteTokens"]
        self.uncached_input_tokens += u
        self.output_tokens += o
        self.cache_read_tokens += r
        self.cache_write_tokens += w
        self._last_key = (turn, step)
        self._last_buckets = {"inputTokens": u, "outputTokens": o,
                              "cacheReadTokens": r, "cacheWriteTokens": w}
        # 压力：prompt 侧规模（不含输出），采样点锚定当前 surface
        self.pressure_tokens = u + r + w
        self._sampled_surface = self.surface_tokens
        return True

    def add_maintenance_usage(self, raw_usage: Any) -> bool:
        """记一次**维护调用**（压缩摘要）的用量。返回 True=已计入。

        与 `add_usage` 的三点区别（都是刻意的）：

        ① **不进 (turn, step) 的 last 槽**：维护调用不在回合/步序里，重试就是两次
           真扣费，没有"流式早样本 vs 终样本"要替换的关系。
        ② **不动 contextPressure**：那次调用的 prompt 是"即将被折掉的历史"，
           不是下一次请求的规模——拿它当压力会让压缩后的上下文显示成一个大值
           （正好把压缩的效果抹掉）。
        ③ 计数进 `compaction_calls` / `maintenance_*` 分解桶，但金额**并入**四桶：
           `snapshot().tokenUsage` 的 "总 = 输入 + 输出" 必须始终对得上，
           另开互斥桶会让 UI 那行数字自相矛盾（UI 想单列时读 maintenance_*）。

        为什么要单独记：摘要调用是**整前缀**的一次调用（全会话最贵的一次），
        此前 `compress.py` 只取 `result.text`，`usage` 直接丢 → 每压一次就少算
        一整份上下文（实测量级：一次压缩约 0.8–1.2 万输入 token）。
        """
        buckets = map_usage(raw_usage) if not (
            isinstance(raw_usage, dict) and "inputTokens" in raw_usage
        ) else raw_usage
        if buckets is None:
            return False
        try:
            u = int(buckets.get("inputTokens", 0))
            o = int(buckets.get("outputTokens", 0))
            r = int(buckets.get("cacheReadTokens", 0))
            w = int(buckets.get("cacheWriteTokens", 0))
        except Exception:
            return False
        if min(u, o, r, w) < 0:
            return False
        self.uncached_input_tokens += u
        self.output_tokens += o
        self.cache_read_tokens += r
        self.cache_write_tokens += w
        self.maintenance_input_tokens += u + r + w
        self.maintenance_output_tokens += o
        self.compaction_calls += 1
        return True

    # ----- 计时 -----
    def record_step(self, turn: int, step: int, llm_ms: float,
                    ttft_ms: Optional[float],
                    decode_ms: Optional[float],
                    output_tokens: Optional[int],
                    has_tool_calls: bool = False) -> None:
        """一步结算的墙钟。缺失项传 None（整步退出该分项，不污染平均）。

        llm_ms：step/start → assistant 落盘；ttft：step/start → 首 token；
        decode：首 token → 落盘（仅当 output_tokens 已知才计入吞吐）。
        has_tool_calls：本步产出 tool_calls 时为 True——该步计入 DSH 口径的
        decode（对账用），但不计入正文 TPS（展示用）：工具参数是“想很久、
        吐很快”，混入平均会把体感速度带飞（如 34 → 127）。
        """
        self.turns_seen.add(turn)
        self.steps += 1
        if has_tool_calls:
            self.tool_steps += 1
        self.llm_ms += max(0.0, llm_ms)
        if ttft_ms is not None:
            self.ttft_ms += max(0.0, ttft_ms)
            self.ttft_steps += 1
        if decode_ms is not None and output_tokens is not None and decode_ms > 0:
            self.decode_ms += max(0.0, decode_ms)
            self.decode_tokens += max(0, output_tokens)
            if not has_tool_calls:
                self.text_decode_ms += max(0.0, decode_ms)
                self.text_decode_tokens += max(0, output_tokens)

    def record_tool(self, wall_ms: float) -> None:
        self.tool_ms += max(0.0, wall_ms)

    def set_surface(self, surface_tokens: int, turns: Optional[int] = None) -> None:
        """同步"当前 surface 规模"（token 估算值，与 pressure_tokens 同单位）。

        projected = pressure + (当前 surface − 采样时 surface) 这个推算靠它：
        没有调用方时 surface_tokens 恒 0 ⇒ projected ≡ pressure（09-17 前就是死代码）。
        压缩与删除都会改 surface，所以两处都必须调（`DialogueAgent.refresh_context_pressure`）。
        """
        self.surface_tokens = max(0, int(surface_tokens))
        if turns is not None:
            self.surface_turns = max(0, int(turns))

    def invalidate_pressure(self) -> None:
        """上下文规模**作废**：下一次实测之前不显示旧值（UI 显示"—"，不显示陈旧大数）。

        压缩/删除把 surface 改小了，`pressure_tokens` 还是**改之前那次请求**的实测值
        ⇒ 面板上的「上下文」看着像"压了没效果 / 删了没变小"。作废比"猜一个"诚实：
        估错会让人以为压缩没生效，而"—"只表示"还没测"。
        """
        self.pressure_tokens = None
        self._sampled_surface = None

    def usage_buckets(self) -> Dict[str, int]:
        """四个计费桶的当前值（+ 步数）。**群聊按"回合前后取差"归集本群用量**要用它。

        为什么用差而不是直接读累计：成员 agent 同时服务单聊与群聊，它的 `tokenUsage`
        是**两条线之和**；只有"这一趟群内回合增加了多少"才是"群聊期间"的量。
        差的正确性依赖 `add_usage` 的 (turn, step) 替换语义（流式早样本 vs 终样本）——
        取差跨过整趟回合，替换发生在窗口内部，故不影响结果。
        """
        return {"inputTokens": int(self.uncached_input_tokens),
                "cacheReadTokens": int(self.cache_read_tokens),
                "cacheWriteTokens": int(self.cache_write_tokens),
                "outputTokens": int(self.output_tokens),
                "steps": int(self.steps)}

    def set_context_window(self, n: Optional[int]) -> None:
        self.context_window = n if (isinstance(n, int) and n > 0) else None

    # ----- 快照（WS 推送的唯一出口，原子 dict） -----
    def snapshot(self) -> Dict[str, Any]:
        billed = self.uncached_input_tokens + self.cache_read_tokens + self.cache_write_tokens
        total = billed + self.output_tokens
        hit = format_cache_hit_percent(self.cache_read_tokens, billed)
        tps: Optional[float] = None
        if self.decode_ms > 0:
            tps = self.decode_tokens / (self.decode_ms / 1000.0)
        text_tps: Optional[float] = None
        if self.text_decode_ms > 0:
            text_tps = self.text_decode_tokens / (self.text_decode_ms / 1000.0)
        ttft_avg: Optional[float] = None
        if self.ttft_steps > 0:
            ttft_avg = self.ttft_ms / self.ttft_steps
        pressure = self.pressure_tokens
        projected = None
        if pressure is not None and self._sampled_surface is not None:
            projected = max(0, pressure + self.surface_tokens - self._sampled_surface)
        occupancy = None
        if projected is not None and self.context_window:
            # DSH Math.round（0.5 向上），不用 Python bankers round
            import math as _math
            occupancy = min(100, int(_math.floor(projected / self.context_window * 100 + 0.5)))
        # 估算口径（09-18 补）：实测值在三种情况下**故意作废**（刚开窗/resume 的 agent
        # 构造、压缩、删除）——那时 `pressureTokens`/`occupancyPercent` 都是 None，
        # 于是 UI 的「上下文」整段消失，看起来像"这一项被去掉了"（用户 09-18 反馈）。
        # 但"当前 surface 有多大"我们**一直知道**（`set_surface` 喂的估算，与压缩器选段
        # 用同一把尺子）⇒ 把它单独暴露出来：实测优先，没实测时 UI 退到估算并打上 ≈。
        # 两个字段**刻意分开**，不合进 occupancyPercent —— 那个字段的契约是"实测/推算"，
        # 混入估算会让"宁可不显示也不显示陈旧大数"这条判断失去意义。
        estimated = self.surface_tokens if self.surface_tokens > 0 else None
        occupancy_estimated = None
        if occupancy is None and estimated is not None and self.context_window:
            import math as _math2
            occupancy_estimated = min(100, int(_math2.floor(
                estimated / self.context_window * 100 + 0.5)))
        return {
            "tokenUsage": {
                "uncachedInputTokens": self.uncached_input_tokens,
                "cacheReadTokens": self.cache_read_tokens,
                "cacheWriteTokens": self.cache_write_tokens,
                "outputTokens": self.output_tokens,
                "billedInputTokens": billed,
                "totalTokens": total,
                "cacheHitPercent": hit,  # 字符串如 "79"，None=无输入不显示
                # 「其中有多少是维护（压缩摘要）花的」——已含在上面各桶里，UI 想单列时读它们
                "maintenanceInputTokens": self.maintenance_input_tokens,
                "maintenanceOutputTokens": self.maintenance_output_tokens,
                "compactionCalls": self.compaction_calls,
            },
            "sessionStats": {
                "turns": len(self.turns_seen),
                "steps": self.steps,
                "toolSteps": self.tool_steps,
                "llmMs": self.llm_ms,
                "toolMs": self.tool_ms,
                "ttftMs": self.ttft_ms,
                "ttftSteps": self.ttft_steps,
                "ttftAvgMs": ttft_avg,
                "decodeMs": self.decode_ms,
                "decodeTokens": self.decode_tokens,
                "tokensPerSecond": tps,  # DSH 口径（含工具步，对账用）
                "textDecodeMs": self.text_decode_ms,
                "textDecodeTokens": self.text_decode_tokens,
                "textTokensPerSecond": text_tps,  # 展示口径（仅纯文本步，C# 显示用这个）
            },
            "contextPressure": {
                "pressureTokens": pressure,
                "projectedTokens": projected if projected is not None else pressure,
                # 当前 surface 的估算（None = 空会话/取数失败）。实测缺席时 UI 用它 + ≈。
                "estimatedTokens": estimated,
                "contextWindow": self.context_window,
                "occupancyPercent": occupancy,          # 实测/推算口径（可能 None，语义不变）
                "estimatedOccupancyPercent": occupancy_estimated,   # 估算口径（仅实测缺席时有值）
                # 现存轮数：删除/压缩后与 sessionStats.turns（累计）不再相等——
                # UI 的"N轮/M步"是**花钱口径**，别让它被读成"历史还剩几轮"。
                "liveTurns": self.surface_turns,
            },
        }

    # ----- 从账本重建（resume 时调用，用量只从 assistant/message.usage 恢复） -----
    def rebuild_from_log(self, log: Any) -> None:
        """清空后按 log 顺序重放用量+计数。计时（ms）无法从账本恢复，保持 0。

        兼容两种用量形态：data.usage（新）与 data.message 无用量（旧/stub，跳过）。
        turn/step 计数从 step/end 恢复（与 DSH 一致，失败/取消步也算）。
        维护用量从 `dev/maintenance-usage` 恢复（09-17 起会话侧把压缩/工具摘要调用
        的用量统一落这条事件；它覆盖"压成/重试/没压成"所有路径）。

        ★ 口径：重放的是**整本 log**，不是 surface —— 删除历史**不退钱**。
          被删回合的 assistant/message 仍在 log 里，金额照计；改成按 surface 重放
          会静默少算（且有删除动作的会话从此对不上账）。
        """
        self.uncached_input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.maintenance_input_tokens = 0
        self.maintenance_output_tokens = 0
        self.compaction_calls = 0
        self._last_key = None
        self._last_buckets = None
        self.turns_seen = set()
        self.steps = 0
        self.pressure_tokens = None
        self._sampled_surface = None
        try:
            for ev in log or []:
                t = ev.get("type") if isinstance(ev, dict) else None
                data = ev.get("data") if isinstance(ev, dict) else None
                if not isinstance(data, dict):
                    continue
                if t == "assistant/message" and data.get("usage") is not None:
                    try:
                        self.add_usage(int(data.get("turn", 0)), int(data.get("step", 0)), data["usage"])
                    except Exception:
                        continue
                elif t == "step/end":
                    try:
                        self.turns_seen.add(int(data.get("turn", 0)))
                        self.steps += 1
                    except Exception:
                        continue
                elif t == "dev/maintenance-usage":
                    # 压缩/工具摘要调用的用量（可能多笔：重试过、或含 pruner 的几笔）
                    for raw in (data.get("usage") or []):
                        try:
                            self.add_maintenance_usage(raw)
                        except Exception:
                            continue
        except Exception:
            pass
