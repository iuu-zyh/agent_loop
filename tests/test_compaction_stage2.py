"""压缩 Stage2 测试：手动压缩（摘要替换+保留尾+重放）+ 配对平衡 + 决策 + fail-closed"""

import asyncio
import json
from types import SimpleNamespace

from agent_loop.session import Session
from agent_loop.persistence import save_session, load_session
from agent_loop import llm_adapter
from agent_loop.compaction import Compressor, COMPACT_PLUGIN

ROOT = "/tmp/agent_loop_test_compact2"
FK = "韩立"
NPC = "林婉清"


class StubSummaryLlm:
    """摘要桩：返回固定纪要。"""
    async def generate(self, system, messages, tools):
        return SimpleNamespace(
            text="<compacted-summary>\n## 角色\n- 林婉清 外冷内热\n## 关系\n- 道侣 好感180\n## 剧情\n- 上周白帝城共游\n</compacted-summary>",
            tool_calls=[],
        )


class FailLlm:
    """失败桩：返回空（模拟摘要失败）。"""
    async def generate(self, system, messages, tools):
        return SimpleNamespace(text="", tool_calls=[])


def _session(extra_pairs=36):
    s = Session(id=NPC, header={"id": NPC, "cwd": "/tmp"})
    for i in range(extra_pairs):
        s.append("user/message", {"role": "user", "content": [{"type": "text", "text": f"{FK}的话{i}，今日可安好东南山上"}], "id": f"u{i}"}, {"surfaceOp": "append"})
        s.append("assistant/message", {"message": {"role": "assistant", "content": [{"type": "text", "text": f"{NPC}回{i}，飞雪连天寒江独钓"}], "id": f"a{i}"}}, {"surfaceOp": "append"})
    return s


def test_manual_compact_replaces_keeps_tail_and_replays():
    s = _session()
    orig_nodes = list(s.surface.nodes)
    comp = Compressor(llm=StubSummaryLlm(), system_prompt=None, ctx_window=1500, retain_ratio=0.16)
    report = asyncio.run(comp.compact_now(s, NPC))
    assert report is not None
    # checkpoint 节点存在（source.plugin == compact）
    cps = [ev for ev in s.log if ev["type"] == "user/message" and (ev.get("data", {}).get("source", {}).get("plugin") == COMPACT_PLUGIN)]
    assert len(cps) == 1
    assert "外冷内热" in cps[0]["data"]["content"][0]["text"]
    # surface 大幅缩短
    assert len(s.surface.nodes) < len(orig_nodes)
    # compaction/summary 记账在
    assert any(ev["type"] == "compaction/summary" for ev in s.log)
    # 重放后一致：checkpoint 在，靠尾的原文仍在
    save_session(s, ROOT)
    loaded = load_session(NPC, ROOT)
    assert loaded is not None
    lmsgs = loaded.derive_messages()
    assert any(json.dumps(m) for m in lmsgs if "外冷内热" in json.dumps(m, ensure_ascii=False))
    assert any("林婉清回31" in json.dumps(m, ensure_ascii=False) for m in lmsgs)  # 较新尾部保留
    print(f"✓ 手动压缩：替换{report['range']} → checkpoint 节点，尾保留，重放一致")


def test_pairing_blockades_hanging_tool():
    # 压区里有一条无结果的 tool-call → 应拒绝切割
    s = Session(id="悬空", header={"id": "悬空", "cwd": "/tmp"})
    # 第一条 assistant 带 tool-call 且无 result（落在压区早段）
    m = llm_adapter.create_canonical_assistant_message("我去查查", [{"id": "tc1", "name": "inspect_unit", "arguments": {}}])
    s.append("assistant/message", {"message": m}, {"surfaceOp": "append"})
    for i in range(30):
        s.append("user/message", {"role": "user", "content": [{"type": "text", "text": f"话{i}一二三四五"}], "id": f"u{i}"}, {"surfaceOp": "append"})
        s.append("assistant/message", {"message": {"role": "assistant", "content": [{"type": "text", "text": f"回{i}六七八九十"}], "id": f"a{i}"}}, {"surfaceOp": "append"})
    before = len(s.log)
    comp = Compressor(llm=StubSummaryLlm(), ctx_window=800, retain_ratio=0.16)
    report = asyncio.run(comp.compact_now(s, "悬空"))
    assert report is None  # 悬空 tool-call 迫使拒绝
    assert len(s.log) == before  # session 未变
    print("✓ 配对平衡：悬空 tool-call 拒绝切割，session 不变")


def test_maybe_decision_threshold():
    # 历史少不超阈 → maybe 返回 None
    s = _session(2)
    before = len(s.log)
    comp = Compressor(llm=StubSummaryLlm(), ctx_window=10 ** 6, retain_ratio=0.16)
    header = {"system": "你是林婉清。", "tools": []}
    report = asyncio.run(comp.maybe(s, header))
    assert report is None
    assert len(s.log) == before
    print("✓ 自动决策：未超阈不压缩")


def test_summary_failure_fail_closed():
    s = _session()
    before = len(s.log)
    comp = Compressor(llm=FailLlm(), system_prompt=None, ctx_window=1500, retain_ratio=0.16)
    report = asyncio.run(comp.compact_now(s, NPC))
    assert report is None
    assert len(s.log) == before  # fail-closed：不改 session
    print("✓ fail-closed：摘要失败不改 session")


if __name__ == "__main__":
    test_manual_compact_replaces_keeps_tail_and_replays()
    test_pairing_blockades_hanging_tool()
    test_maybe_decision_threshold()
    test_summary_failure_fail_closed()
    print("\nStage2 压缩测试（手动/配对/决策/fail-closed）全部通过")