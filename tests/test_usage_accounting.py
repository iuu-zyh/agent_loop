# -*- coding: utf-8 -*-
"""压缩/删除下的**用量与上下文口径**（09-17 定案，agent 级接线）。

背景：`stats.UsageTracker` 的两类口径必须分开，混起来就会在压缩/删除后给出误导数字：

  · **花费口径**（`tokenUsage` / `sessionStats`）：只增不减。删除历史**不退钱**，
    压缩摘要调用**要计钱**（整前缀、全会话最贵的一次）。重放的是**整本 log**。
  · **存货口径**（`contextPressure`）：跟着 surface 走。压缩/删除后旧实测值必须作废，
    否则面板上的「上下文」一直是重建前的规模，看着像"压了没效果／删了没变小"。

`tests/test_stats.py` 钉算法本身（桶、恒等式、作废、推算）；本文件钉**接线**：

  ① 自动压缩路径（`DialogueAgent._pre_step`）真的把摘要用量记进了 stats；
  ② 真的落了 `dev/maintenance-usage` 账本事件（重启后重放得出同样的数）；
  ③ 手动压缩路径（`compact_now`）同样记；
  ④ 删除历史不退款、`liveTurns` 会掉、`pressureTokens` 被作废。

每条都对应一个"只在实机上才看得出来"的静默错误：
数字少一截（②漏了就少一整个上下文）、看起来压缩无效（④漏了）、
或者删除被当成退款（重放改成按 surface 就会这样）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_PARENT = str(Path(__file__).resolve().parent.parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.compaction import Compressor
from agent_loop.session.history_ops import append_delete, plan_delete
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.session.stats import UsageTracker

ROOT = "/tmp/agent_loop_test_usage_accounting"
NPC = "林婉清"

# 摘要调用的"账单"：9000 输入（8000 命中缓存）+ 300 输出
SUMMARY_USAGE = {"prompt_tokens": 9000, "completion_tokens": 300,
                 "prompt_tokens_details": {"cached_tokens": 8000}}


class _UsageSummaryLlm:
    """摘要桩：报告用量（真模型也报，只是以前被丢掉）。"""

    def __init__(self):
        self.calls = 0

    async def generate(self, system, messages, tools, **kw):
        self.calls += 1
        return SimpleNamespace(
            text="<compacted-summary>\n## 角色\n- 林婉清 外冷内热\n</compacted-summary>",
            tool_calls=[], usage=dict(SUMMARY_USAGE))


def _seed_history(agent, pairs: int = 40):
    for i in range(pairs):
        agent.session.append(
            "user/message",
            {"role": "user", "id": f"u{i}",
             "content": [{"type": "text", "text": f"玩家的话{i}喦佬问你最近有没有去过白帝城镇守边关"}]},
            {"surfaceOp": "append"})
        agent.session.append(
            "assistant/message",
            {"message": {"role": "assistant", "id": f"a{i}",
                         "content": [{"type": "text", "text": f"林婉清回{i}炼气期吐纳闭关一口真气上九霄"}]}},
            {"surfaceOp": "append"})


def _run_auto_compact_turn(npc: str = NPC):
    llm = _UsageSummaryLlm()
    comp = Compressor(llm=llm, ctx_window=1200, retain_ratio=0.16, cool_down=0.0)
    loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), compactor=comp)
    agent = loop.create(npc)
    _seed_history(agent)
    agent.send("继续聊")
    asyncio.run(agent.run_until_idle())
    return agent, comp, llm


def test_auto_compaction_summary_usage_is_accounted_and_persisted():
    """自动压缩：摘要用量进 stats（活体）**且**落账（重放），两处数一致。"""
    agent, comp, llm = _run_auto_compact_turn()
    assert llm.calls >= 1, "前提：压缩真的调了摘要 LLM"

    tu = agent.stats.snapshot()["tokenUsage"]
    assert tu["compactionCalls"] == llm.calls, "维护调用次数要等于真实调用次数"
    assert tu["maintenanceInputTokens"] == 9000 * llm.calls, "整份 prompt（缓存 8000 + 未缓存 1000）"
    assert tu["maintenanceOutputTokens"] == 300 * llm.calls
    assert tu["totalTokens"] == tu["billedInputTokens"] + tu["outputTokens"], "总恒等于输入+输出"

    evs = [e for e in agent.session.log if e["type"] == "dev/maintenance-usage"]
    assert evs, "维护用量必须落账——否则重启后这部分总量彻底消失"
    assert len(evs[-1]["data"]["usage"]) == llm.calls
    assert comp.take_pending_usage() == [], "已被 agent 取走"

    # 重放路径（模拟重启）：同样的数
    t = UsageTracker()
    t.rebuild_from_log(agent.session.log)
    tu2 = t.snapshot()["tokenUsage"]
    assert tu2["maintenanceInputTokens"] == tu["maintenanceInputTokens"]
    assert tu2["compactionCalls"] == tu["compactionCalls"]


def test_manual_compaction_summary_usage_is_accounted_too():
    """手动压缩是另一个入口（/compact、面板按钮）：漏了就是"手动压的钱全不算"。"""
    llm = _UsageSummaryLlm()
    comp = Compressor(llm=llm, ctx_window=1200, retain_ratio=0.16, cool_down=0.0)
    loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), compactor=comp)
    agent = loop.create(NPC)
    _seed_history(agent)

    before = agent.stats.snapshot()["tokenUsage"]["compactionCalls"]
    report = asyncio.run(agent.compact_now())
    if report is None:      # 种子太短压不动时跳过（本用例只关心"压成了要记钱"）
        assert llm.calls == 0
        return
    tu = agent.stats.snapshot()["tokenUsage"]
    assert tu["compactionCalls"] == before + llm.calls > before
    assert any(e["type"] == "dev/maintenance-usage" for e in agent.session.log)


def test_delete_does_not_refund_and_invalidates_context_pressure():
    """删除：累计用量**不动**（钱花了），上下文规模作废 + 现存轮数掉下来。"""
    from agent_loop.session.history_ops import live_turn_count

    agent, comp, llm = _run_auto_compact_turn()
    # 造一个"有实测值"的状态：模拟上一次请求的用量样本
    agent.stats.add_usage(1, 1, {"prompt_tokens": 50000, "completion_tokens": 100})
    assert agent.stats.snapshot()["contextPressure"]["pressureTokens"] == 50000
    before = agent.stats.snapshot()
    assert live_turn_count(agent.session) > 0, "前提：还有现存回合"

    turns = [int(e["data"]["turn"]) for e in agent.session.log
             if e.get("type") == "turn/start" and isinstance(e.get("data"), dict)]
    plan = plan_delete(agent.session, turns)
    assert plan["ok"], plan
    append_delete(agent.session, plan, reason="test")
    agent.refresh_context_pressure()      # ws_channel.delete_history 成功后调的就是它

    after = agent.stats.snapshot()
    assert after["tokenUsage"]["totalTokens"] == before["tokenUsage"]["totalTokens"], "删历史不退钱"
    assert after["sessionStats"]["turns"] == before["sessionStats"]["turns"], "累计轮数不变"
    assert after["contextPressure"]["pressureTokens"] is None, "上下文实测值必须作废"
    assert after["contextPressure"]["occupancyPercent"] is None
    assert live_turn_count(agent.session) == 0
    assert after["contextPressure"]["liveTurns"] == 0, "刷新后 stats 里的现存轮数要跟上"
