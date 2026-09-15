"""tests/test_stats.py — 用量累计/命中率/耗时公式的纯逻辑验证（不调真模型）。"""

import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from agent_loop.stats import UsageTracker, map_usage, format_cache_hit_percent


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
