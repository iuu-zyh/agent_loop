"""压缩 Stage3 测试：DialogueAgent 自动接入（turn 边界）+ 最新 L1 追加 + 防抖"""

import asyncio
import json
from types import SimpleNamespace

from agent_loop.agent_loop import AgentLoop
from agent_loop.bridge import StubGameBridge
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.compaction import Compressor, COMPACT_PLUGIN

ROOT = "/tmp/agent_loop_test_compact3"
NPC = "林婉清"


class StubSummaryLlm:
    async def generate(self, system, messages, tools):
        return SimpleNamespace(text="<compacted-summary>\n## 角色\n- 林婉清 外冷内热\n</compacted-summary>", tool_calls=[])


def _seed_history(agent, pairs=40):
    for i in range(pairs):
        agent.session.append("user/message", {"role": "user", "content": [{"type": "text", "text": f"玩家的话{i}喦佬问你最近有没有去过白帝城镇守边关"}], "id": f"u{i}"}, {"surfaceOp": "append"})
        agent.session.append("assistant/message", {"message": {"role": "assistant", "content": [{"type": "text", "text": f"林婉清回{i}炼气期吐纳闭关一口真气上九霄"}], "id": f"a{i}"}}, {"surfaceOp": "append"})


def test_auto_compact_on_turn_boundary():
    comp = Compressor(llm=StubSummaryLlm(), ctx_window=1200, retain_ratio=0.16, cool_down=0.0)
    loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), compactor=comp)
    agent = loop.create(NPC)
    _seed_history(agent)
    agent.send("继续聊")
    asyncio.run(agent.run_until_idle())
    # 出现 checkpoint 摘要节点
    cps = [ev for ev in agent.session.log if ev["type"] == "user/message" and (ev.get("data", {}).get("source", {}).get("plugin") == COMPACT_PLUGIN)]
    assert len(cps) >= 1
    # 有新回复（echo）落盘
    assert any(ev["type"] == "assistant/message" for ev in agent.session.log)
    # surface 里既有摘要又有本轮新消息
    msgs = agent.session.derive_messages()
    assert any("外冷内热" in json.dumps(m, ensure_ascii=False) for m in msgs)
    print("✓ turn 边界自动压缩：摘要替换 + 本轮消息正常落盘")


def test_auto_compact_keeps_fresh_l1_for_next_turn():
    """压缩后下一回合 preStep 仍追加最新 L1 context（现场感不倒退）。"""
    bridge = StubGameBridge()
    bridge.seed_unit(NPC, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "power": 8900,
                            "player": {"name": "韩立", "realm": "筑基", "relation": "道侣", "intim": 180, "same_grid": True},
                            "recent": "今日刚与韩立共游白帝城"})
    comp = Compressor(llm=StubSummaryLlm(), ctx_window=1200, retain_ratio=0.16, cool_down=0.0)
    loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), bridge=bridge, compactor=comp)
    agent = loop.create(NPC)
    _seed_history(agent)
    # 第一轮触发压缩 + 产生 L1/context
    agent.send("第一问")
    asyncio.run(agent.run_until_idle())
    # 新一轮：preStep 会重取 L1 并 diff 追加最新 context
    agent.send("第二问")
    asyncio.run(agent.run_until_idle())
    msgs = agent.session.derive_messages()
    assert any("共游白帝城" in json.dumps(m, ensure_ascii=False) for m in msgs), "最新 L1 context 未在压缩后追加"
    print("✓ 压缩后新回合自动带出最新 L1 context")


def test_cool_down_prevents_retrigger():
    """冷却期内不再自动压缩（防反复烧模型）。"""
    comp = Compressor(llm=StubSummaryLlm(), ctx_window=1200, retain_ratio=0.16, cool_down=1000000.0)
    loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), compactor=comp)
    agent = loop.create("萧炎")
    _seed_history(agent, 50)
    agent.send("触发一次")
    asyncio.run(agent.run_until_idle())
    summaries = [ev for ev in agent.session.log if ev["type"] == "compaction/summary"]
    assert len(summaries) >= 1
    cps = [ev for ev in agent.session.log if ev["type"] == "user/message" and (ev.get("data", {}).get("source", {}).get("plugin") == COMPACT_PLUGIN)]
    # 冷却期内再触发，不应新增 checkpoint
    agent.send("又来")
    asyncio.run(agent.run_until_idle())
    cps2 = [ev for ev in agent.session.log if ev["type"] == "user/message" and (ev.get("data", {}).get("source", {}).get("plugin") == COMPACT_PLUGIN)]
    assert len(cps2) == len(cps), "冷却期内不应再次压缩"
    print(f"✓ 冷却防抖：二次触发未重复压缩（checkpoint 数保持 {len(cps)}）")


def test_manual_compact_only_idle():
    """手动压缩只能 idle 触发；running 时抛错。"""
    comp = Compressor(llm=StubSummaryLlm(), ctx_window=10 ** 6, retain_ratio=0.16, cool_down=0.0)
    loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), compactor=comp)
    agent = loop.create("张三")
    _seed_history(agent)
    # idle 手动可压
    report = asyncio.run(agent.compact_now())
    assert report is not None
    # running 期间手动压缩应拒绝
    agent.send("跑起来")
    try:
        before = agent.phase["kind"]
        # 构造 running：直接设 phase
        agent.phase = {"kind": "running", "turn": 1, "step": 0}
        asyncio.run(agent.compact_now())
        assert False, "running 时手动压缩应拒绝"
    except RuntimeError as e:
        assert "idle" in str(e)
    print("✓ 手动压缩仅 idle 可触发，running 拒绝")


class BoomSummaryLlm:
    """摘要必失败的 LLM：验证失败原因不再被静默吞掉（超时文案同 httpx）"""

    async def generate(self, system, messages, tools):
        raise TimeoutError("timed out")


def test_summary_failure_is_logged(tmp_path):
    """回归：摘要 LLM 失败曾静默 `return None`，玩家只看到"没有需要压缩的内容"，原因不可见。

    现在必须 WARNING 留痕（异常类型 + 原文）并给出「压缩放弃」结论，同时记下选中的段。
    """
    from agent_loop import log_setup

    log_path = tmp_path / "compact.log"
    log_setup.setup_logging({"logging": {"enabled": True, "level": "DEBUG", "file": str(log_path),
                                        "console": False, "rotation": "none",
                                        "slow_ms": 0, "slow_escalations": 0}}, force=True)
    try:
        comp = Compressor(llm=BoomSummaryLlm(), ctx_window=1200, retain_ratio=0.16, cool_down=0.0)
        loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), compactor=comp)
        agent = loop.create(NPC)
        _seed_history(agent)
        out = asyncio.run(comp.compact_now(agent.session, NPC))
    finally:
        log_setup.shutdown_logging()

    assert out is None, "摘要失败必须 fail-closed（不改历史）"
    text = log_path.read_text(encoding="utf-8")
    assert "摘要 LLM 调用失败" in text, text
    assert "timed out" in text, "异常原文要能看见（否则只能猜）"
    assert "压缩放弃" in text, "要给出明确结论而不是无声返回"
    assert "压缩选段" in text, "要记录选中了哪一段（seq 范围）"
    print("✓ 摘要失败可见化：WARNING + 放弃结论 + 选段记录")


def test_auto_compact_skip_reasons_are_logged(tmp_path):
    """自动压缩「为什么没压」也要看得见：冷却中 / 未超阈 各有 DEBUG 依据。"""
    from agent_loop import log_setup

    log_path = tmp_path / "skip.log"
    log_setup.setup_logging({"logging": {"enabled": True, "level": "DEBUG", "file": str(log_path),
                                        "console": False, "rotation": "none",
                                        "slow_ms": 0, "slow_escalations": 0}}, force=True)
    try:
        comp = Compressor(llm=StubSummaryLlm(), ctx_window=100000, retain_ratio=0.16, cool_down=999.0)
        loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), compactor=comp)
        agent = loop.create(NPC)
        _seed_history(agent, pairs=2)
        header = agent.session.request_header()

        async def run():
            await comp.maybe(agent.session, header)          # 远未超阈 → 跳过
            comp._cool_until[agent.session.id] = 9e9          # 人为置入冷却
            await comp.maybe(agent.session, header)           # 冷却中 → 跳过

        asyncio.run(run())
    finally:
        log_setup.shutdown_logging()

    text = log_path.read_text(encoding="utf-8")
    assert "自动压缩跳过：上下文" in text and "阈值" in text, text
    assert "自动压缩跳过：仍在冷却期" in text, text
    print("✓ 自动压缩跳过原因可见（未超阈 / 冷却中）")


if __name__ == "__main__":
    test_auto_compact_on_turn_boundary()
    test_auto_compact_keeps_fresh_l1_for_next_turn()
    test_cool_down_prevents_retrigger()
    test_manual_compact_only_idle()
    print("\nStage3 压缩测试（自动接入/防抖/L1/手动闸）全部通过")