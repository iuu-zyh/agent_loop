"""tests/test_stats.py — 用量累计/命中率/耗时公式的纯逻辑验证（不调真模型）。"""

import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from agent_loop.session.stats import UsageTracker, map_usage, format_cache_hit_percent


def test_map_usage_openai_cached():
    raw = {"prompt_tokens": 1000, "completion_tokens": 50,
           "prompt_tokens_details": {"cached_tokens": 790}}
    m = map_usage(raw)
    assert m == {"inputTokens": 210, "outputTokens": 50,
                 "cacheReadTokens": 790, "cacheWriteTokens": 0}


def test_map_usage_deepseek_hit_field():
    raw = {"prompt_tokens": 1000, "completion_tokens": 10, "prompt_cache_hit_tokens": 600}
    m = map_usage(raw)
    assert m["inputTokens"] == 400
    assert m["cacheReadTokens"] == 600


def test_map_usage_clamps_dirty_cache():
    m = map_usage({"prompt_tokens": 100, "completion_tokens": 5,
                   "prompt_tokens_details": {"cached_tokens": 999}})
    assert m["cacheReadTokens"] == 100
    assert m["inputTokens"] == 0


def test_map_usage_none_without_numbers():
    assert map_usage(None) is None
    assert map_usage({}) is None
    assert map_usage({"prompt_tokens": "x", "completion_tokens": 1}) is None


def test_replacement_same_step():
    tr = UsageTracker()
    tr.add_usage(1, 0, {"prompt_tokens": 100, "completion_tokens": 10})
    tr.add_usage(1, 0, {"prompt_tokens": 102, "completion_tokens": 11})
    s = tr.snapshot()
    assert s["tokenUsage"]["billedInputTokens"] == 102
    assert s["tokenUsage"]["outputTokens"] == 11
    assert s["tokenUsage"]["totalTokens"] == 113


def test_retry_adds_twice():
    tr = UsageTracker()
    tr.add_usage(1, 0, {"prompt_tokens": 100, "completion_tokens": 10})
    tr.notify_retry(1, 0)
    tr.add_usage(1, 0, {"prompt_tokens": 120, "completion_tokens": 12})
    s = tr.snapshot()
    assert s["tokenUsage"]["billedInputTokens"] == 220
    assert s["tokenUsage"]["outputTokens"] == 22


def test_cache_hit_edges():
    assert format_cache_hit_percent(0, 0) is None
    assert format_cache_hit_percent(100, 100) == "100"
    # 71698 未缓存 + 265367 缓存 = 337065 → 78.7% → 整数 79
    assert format_cache_hit_percent(265367, 337065) == "79"
    # 非满命中绝不显示 100
    hit = format_cache_hit_percent(9999, 10000)
    assert hit != "100" and hit.startswith("99.")


def test_session_stats_tps_and_ttft():
    tr = UsageTracker(context_window=32768)
    tr.record_step(1, 0, llm_ms=2000.0, ttft_ms=800.0, decode_ms=1000.0, output_tokens=20)
    tr.record_step(1, 1, llm_ms=1800.0, ttft_ms=None, decode_ms=None, output_tokens=None)
    s = tr.snapshot()
    st = s["sessionStats"]
    assert st["turns"] == 1 and st["steps"] == 2
    assert st["ttftSteps"] == 1
    assert abs(st["ttftAvgMs"] - 800.0) < 1e-6
    assert abs(st["tokensPerSecond"] - 20.0) < 1e-6
    assert abs(st["textTokensPerSecond"] - 20.0) < 1e-6


def test_tool_steps_excluded_from_text_tps():
    tr = UsageTracker()
    # 纯文本步：20 tok / 1s → 20 tok/s
    tr.record_step(1, 0, llm_ms=2000.0, ttft_ms=800.0, decode_ms=1000.0,
                   output_tokens=20, has_tool_calls=False)
    # 工具步：500 tok / 4ms（想很久吐很快），DSH 口径计入、正文口径排除
    tr.record_step(1, 1, llm_ms=6000.0, ttft_ms=5996.0, decode_ms=4.0,
                   output_tokens=500, has_tool_calls=True)
    s = tr.snapshot()
    st = s["sessionStats"]
    assert st["steps"] == 2 and st["toolSteps"] == 1
    assert st["tokensPerSecond"] > 500  # DSH 口径被带飞：520/1.004
    assert abs(st["textTokensPerSecond"] - 20.0) < 1e-6  # 展示口径保持体感


def test_snapshot_shape_matches_ws_contract():
    tr = UsageTracker(context_window=200000)
    tr.add_usage(1, 0, {"prompt_tokens": 1000, "completion_tokens": 50,
                        "prompt_tokens_details": {"cached_tokens": 790}})
    s = tr.snapshot()
    assert set(s.keys()) == {"tokenUsage", "sessionStats", "contextPressure"}
    assert s["tokenUsage"]["totalTokens"] == 1050
    assert s["contextPressure"]["pressureTokens"] == 1000
    assert s["contextPressure"]["contextWindow"] == 200000
    assert s["contextPressure"]["occupancyPercent"] == 1  # round(1000/200000*100)=1（Python bankers 取整 0.5 情况注意）


def test_rebuild_from_log():
    tr = UsageTracker()
    log = [
        {"type": "assistant/message",
         "data": {"turn": 1, "step": 0,
                  "usage": {"prompt_tokens": 100, "completion_tokens": 10}}},
        {"type": "step/end", "data": {"turn": 1, "step": 0}},
        {"type": "assistant/message",
         "data": {"turn": 1, "step": 1,
                  "usage": {"prompt_tokens": 200, "completion_tokens": 20,
                            "prompt_tokens_details": {"cached_tokens": 50}}}},
        {"type": "step/end", "data": {"turn": 1, "step": 1}},
    ]
    tr.rebuild_from_log(log)
    s = tr.snapshot()
    assert s["tokenUsage"]["billedInputTokens"] == 300
    assert s["tokenUsage"]["outputTokens"] == 30
    assert s["sessionStats"]["steps"] == 2


# ----------------------------------------------------------------------
# 压缩/删除下的口径（09-17）：维护调用记账 + 上下文规模作废/推算
# ----------------------------------------------------------------------

def test_maintenance_usage_lands_in_totals_and_breakdown():
    """压缩摘要调用是**整前缀**的一次真实扣费：必须进总量，同时留分解计数。

    "总 = 输入 + 输出"这条恒等式不能破（UI 那行数字要能自己对上），所以维护用量
    并入四桶、另给 maintenance_* 作为"其中多少"的分解，而不是另开互斥桶。
    """
    tr = UsageTracker(context_window=200000)
    tr.add_usage(1, 0, {"prompt_tokens": 1000, "completion_tokens": 50,
                        "prompt_tokens_details": {"cached_tokens": 400}})
    before = tr.snapshot()["tokenUsage"]

    assert tr.add_maintenance_usage({"prompt_tokens": 9000, "completion_tokens": 300,
                                     "prompt_tokens_details": {"cached_tokens": 8000}})
    tu = tr.snapshot()["tokenUsage"]
    # 并入
    assert tu["uncachedInputTokens"] == before["uncachedInputTokens"] + 1000
    assert tu["cacheReadTokens"] == before["cacheReadTokens"] + 8000
    assert tu["outputTokens"] == before["outputTokens"] + 300
    assert tu["totalTokens"] == tu["billedInputTokens"] + tu["outputTokens"], "总恒等于输入+输出"
    # 分解（整份 prompt 口径：缓存 + 未缓存）
    assert tu["maintenanceInputTokens"] == 9000
    assert tu["maintenanceOutputTokens"] == 300
    assert tu["compactionCalls"] == 1


def test_maintenance_usage_does_not_touch_context_pressure():
    """维护调用吃的是"即将被折掉的历史"，不是下一次请求的规模。

    若它更新 pressure，压缩刚把上下文压小、面板立刻又显示成一个万级大数
    ——正好把压缩的效果抹掉，而且下一次实测前一直是错的。
    """
    tr = UsageTracker()
    tr.add_usage(1, 0, {"prompt_tokens": 1000, "completion_tokens": 50})
    assert tr.snapshot()["contextPressure"]["pressureTokens"] == 1000
    tr.add_maintenance_usage({"prompt_tokens": 9000, "completion_tokens": 300})
    assert tr.snapshot()["contextPressure"]["pressureTokens"] == 1000, "维护调用不得改压力"


def test_maintenance_usage_rejects_garbage_and_counts_nothing():
    tr = UsageTracker()
    assert tr.add_maintenance_usage(None) is False
    assert tr.add_maintenance_usage({"foo": 1}) is False
    assert tr.snapshot()["tokenUsage"]["compactionCalls"] == 0


def test_rebuild_restores_maintenance_usage_from_ledger():
    """resume 时压缩那几笔必须回来（否则"重启后总 token 少一截"，且只有压过的会话对不上）。

    载体是 `dev/maintenance-usage`（会话侧统一落账），不是 `compaction/summary`——
    后者只在"真压成了"时才写，而维护开销在 fail-closed / 重试 / 工具摘要路径上同样发生。
    """
    tr = UsageTracker()
    log = [
        {"type": "assistant/message",
         "data": {"turn": 1, "step": 1, "usage": {"prompt_tokens": 500, "completion_tokens": 20}}},
        {"type": "step/end", "data": {"turn": 1, "step": 1}},
        {"type": "dev/maintenance-usage",
         "data": {"reason": "compact",
                  "usage": [{"prompt_tokens": 4000, "completion_tokens": 200,
                             "prompt_tokens_details": {"cached_tokens": 3000}},
                            {"prompt_tokens": 800, "completion_tokens": 40}]}},
    ]
    tr.rebuild_from_log(log)
    tu = tr.snapshot()["tokenUsage"]
    assert tu["maintenanceInputTokens"] == 4800      # 4000 + 800（整份 prompt 口径）
    assert tu["maintenanceOutputTokens"] == 240
    assert tu["compactionCalls"] == 2                # 两笔 = 两次调用（如重试 / 含工具摘要）
    assert tu["totalTokens"] == tu["billedInputTokens"] + tu["outputTokens"]
    # 无 usage 字段的老账本 / 别的维护事件：不记、不炸
    tr.rebuild_from_log([{"type": "dev/maintenance-usage", "data": {"reason": "compact"}}])
    assert tr.snapshot()["tokenUsage"]["compactionCalls"] == 0


def test_surface_projection_is_alive_and_invalidate_clears_stale_sample():
    """projected = pressure + (当前 surface − 采样时 surface)：此前全仓没人调 set_surface
    ⇒ projected 恒等于 pressure（死代码）。压缩/删除正是它存在的理由。"""
    tr = UsageTracker(context_window=1000)
    tr.add_usage(1, 0, {"prompt_tokens": 400, "completion_tokens": 10})
    cp = tr.snapshot()["contextPressure"]
    assert cp["pressureTokens"] == 400 and cp["projectedTokens"] == 400   # 采样时 surface=0

    tr.set_surface(700, turns=3)                                          # 历史长了
    cp = tr.snapshot()["contextPressure"]
    assert cp["projectedTokens"] == 1100, "推算要吃到 surface 增量"
    assert cp["occupancyPercent"] == 100, "占用率按窗口夹到 100"
    assert cp["liveTurns"] == 3

    tr.invalidate_pressure()                                              # 压缩/删除后
    cp = tr.snapshot()["contextPressure"]
    assert cp["pressureTokens"] is None and cp["projectedTokens"] is None
    assert cp["occupancyPercent"] is None, "宁可不显示，也不显示陈旧大数"
    assert cp["liveTurns"] == 3, "现存轮数不受压力作废影响"
    assert cp["estimatedTokens"] == 700, "当前 surface 的估算必须照旧给出来"
    assert cp["estimatedOccupancyPercent"] == 70, "估算口径的占用率要单独给（实测口径仍为 None）"


def test_estimated_context_is_exposed_when_measurement_is_void():
    """★ 09-18 用户反馈 ★「看不到当前上下文和总上下文」的根因之一：

    `pressureTokens` 在三种情况下被**故意作废**（刚开窗/resume 的 agent 构造、压缩、删除），
    于是只剩累计用量与缓存率可显示。但"当前 surface 有多大"我们一直知道
    （`set_surface` 的估算，与压缩器同一把尺子）⇒ 必须单独暴露，供 UI 退到"≈ 估算"，
    而不是让整项消失（旧 C# 把 contextWindow 也嵌在 pressureTokens>0 的分支里 ⇒ 连"总上下文"
    都一起不见了）。
    """
    tr = UsageTracker(context_window=100000)
    # ① 还没实测过（= 刚开窗那一刻）：只有估算
    tr.set_surface(26800, turns=21)
    tr.invalidate_pressure()
    cp = tr.snapshot()["contextPressure"]
    assert cp["pressureTokens"] is None and cp["occupancyPercent"] is None
    assert cp["estimatedTokens"] == 26800
    assert cp["estimatedOccupancyPercent"] == 27      # 26800/100000
    assert cp["contextWindow"] == 100000, "总窗口是配置事实，与有没有实测无关"
    # ② 实测过之后：实测优先，估算字段照旧（UI 不许再退回 ≈）
    tr.add_usage(1, 0, {"prompt_tokens": 2000, "completion_tokens": 10,
                        "prompt_tokens_details": {"cached_tokens": 1000}})
    cp = tr.snapshot()["contextPressure"]
    assert cp["pressureTokens"] == 2000 and cp["occupancyPercent"] == 2
    assert cp["estimatedOccupancyPercent"] is None, "有实测时估算口径必须为空（否则 UI 可能选错）"
    # ③ 空会话：估算也没有 ⇒ 两个字段都 None（UI 显示「—/窗口」，不是"整项消失"）
    empty = UsageTracker(context_window=100000)
    cp = empty.snapshot()["contextPressure"]
    assert cp["estimatedTokens"] is None and cp["estimatedOccupancyPercent"] is None
    assert cp["contextWindow"] == 100000
