"""压缩 × L1 运行时上下文：两条安全护栏（2026-09-14）

背景（`docs/context-projection.md` §7）：压缩的 `replace` 区间是在 `session.surface.nodes`
上**连续**取的 —— `start_seq, end_seq = nodes[0], nodes[cut_idx - 1]` —— 而 L1 段（伪装成
user 的 plugin 消息）也是 surface 节点、且夹在对话中间。于是压缩会：

  ① 把 L1 段当【对话材料】喂给摘要 LLM，且 `_render_transcript` 只按 `role` 判"谁在说话"
     → 渲染成 `玩家：Current runtime context —— 自身：你是林婉清…`
     （读起来就是"玩家宣称自己是林婉清"）。更糟的是纪要是永久节点，下次压缩还会把它喂回去。
  ② 把 L1 段一起折进纪要、从 surface 移除，而差分 B 的基线 `_retained_ctx[段]` 仍留着旧文本
     → 判"这段没变，不用发" → **段永久不再发**，模型静默失去运行时状态。

两条护栏：
  · `Compressor._render_transcript` 跳过 `source.kind == "plugin"`（入口拦人）
  · 压缩后从 surface 反查基线，被折走的段作废基线 → 下一步重发

对照既有口径：`history.py:82`（UI 投影）与 `history_ops.py:167`（删回合）**都读 kind 并豁免 L1**，
只有压缩这条路漏了 —— 这两条测试就是补那个洞的回归闸。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agent_loop.agent_loop import AgentLoop
from agent_loop.bridge import StubGameBridge
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.compaction.compress import Compressor, SUMMARY_OPEN

ROOT = "/tmp/agent_loop_test_compaction_ctx"
NPC = "林婉清"

# 上下文标记（与 system_prompt.render_context_segment 一致）
CTX_MARK = "Current runtime context —— "
SEGS = ("time", "self", "player", "recent")


def _make_bridge() -> StubGameBridge:
    b = StubGameBridge()
    b.seed_unit(NPC, {
        "realm": "金丹", "sect": "化神殿", "pos": "永宁州", "power": 8900,
        "player": {"name": "韩立", "realm": "筑基", "relation": "道侣", "intim": 180, "same_grid": True},
        "recent": "今日刚与韩立共游白帝城",
    })
    return b


def _seed_history(agent, pairs: int = 2) -> None:
    for i in range(pairs):
        agent.session.append(
            "user/message",
            {"role": "user", "content": [{"type": "text", "text": f"玩家的话{i}问你最近去过白帝城吗"}], "id": f"u{i}"},
            {"surfaceOp": "append"})
        agent.session.append(
            "assistant/message",
            {"message": {"role": "assistant", "content": [{"type": "text", "text": f"林婉清回{i}炼气吐纳闭关一口真气"}], "id": f"a{i}"}},
            {"surfaceOp": "append"})


class _SpySummaryLlm:
    """记录摘要调用收到的【材料】原文，并返回合法纪要。"""

    def __init__(self):
        self.materials: list = []

    async def generate(self, system, messages, tools):
        try:
            self.materials.append(messages[0]["content"][0]["text"])
        except Exception as e:                                   # pragma: no cover
            self.materials.append(f"<解析失败 {e}>")
        return SimpleNamespace(
            text=f"{SUMMARY_OPEN}\n## 角色\n- {NPC} 外冷内热\n</compacted-summary>",
            tool_calls=[])


def _forced_compactor(llm) -> Compressor:
    """阈值压到 0.05 + 冷却 0 → 每个回合边界都压缩（便于观察）。"""
    return Compressor(llm=llm, ctx_window=1200, retain_ratio=0.16,
                      cool_down=0.0, threshold_ratio=0.05)


def _ctx_segments(agent) -> dict:
    """surface 投影里现存的 L1 段：{英文段名: 完整文本}。

    ⚠ 渲染出来的是中文标签（`Current runtime context —— 自身：…`），要经 `_CTX_LABELS`
    反查回英文段名 —— 否则拿 `"self"` 去断言会永远失败（第一版就踩了这个，段其实都在）。
    """
    from agent_loop.system_prompt import SystemPrompt

    label_to_name = {v: k for k, v in SystemPrompt._CTX_LABELS.items()}
    out = {}
    for m in agent.session.derive_messages():
        c = m.get("content")
        if isinstance(c, list) and c and isinstance(c[0], dict):
            t = str(c[0].get("text", ""))
            if t.startswith(CTX_MARK):
                label = t[len(CTX_MARK):].split("：")[0].strip()
                name = label_to_name.get(label)
                if name:
                    out[name] = t
    return out


def _run(materials_llm, turns: int = 3):
    bridge = _make_bridge()
    comp = _forced_compactor(materials_llm)
    loop = AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient(), bridge=bridge, compactor=comp)
    agent = loop.create(NPC)
    _seed_history(agent, pairs=2)
    for t in range(1, turns + 1):
        agent.send(f"第{t}问")
        asyncio.run(agent.run_until_idle())
    return agent, comp


# --------------------------------------------------------------------------
# 护栏一：压缩材料不得包含 L1（回避"玩家宣称自己是林婉清"）
# --------------------------------------------------------------------------

def test_compaction_material_excludes_runtime_context():
    """摘要 LLM 收到的 <transcript> 里不得出现 `Current runtime context`。

    必须有第 2 轮 —— 压缩发生在 `_pre_step` **之前**，所以第 1 轮的压缩看不到任何 L1 段
    （那正是它"看起来没问题"的原因，也是既有测试漏判的原因）。
    """
    spy = _SpySummaryLlm()
    agent, _ = _run(spy, turns=2)

    assert spy.materials, "压缩根本没触发，测试前提不成立"
    hit = [m for m in spy.materials if "Current runtime context" in m]
    assert not hit, (
        "L1 段被当【对话材料】喂给摘要了（会渲染成 `玩家：Current runtime context —— …`，"
        f"摘要里就留下\"玩家宣称自己是{NPC}\"）：\n"
        + "\n".join(ln for ln in hit[0].split("\n") if "Current runtime context" in ln)[:400]
    )
    print(f"✓ 压缩材料已排除 L1（{len(spy.materials)} 次摘要调用）")


def test_compaction_material_still_has_dialogue():
    """护栏不能把对话一起滤掉：真玩家消息与 NPC 回复仍要在材料里。"""
    spy = _SpySummaryLlm()
    agent, _ = _run(spy, turns=2)
    body = "\n".join(spy.materials)
    assert "玩家的话" in body or "第一问" in body, "真玩家消息被误滤"
    assert NPC in body, "NPC 回复被误滤"
    print("✓ 压缩材料里对话部分完好（未误伤）")


# --------------------------------------------------------------------------
# 护栏二：压缩折走 L1 段后，基线作废 → 下一步重发
# --------------------------------------------------------------------------

def test_l1_segments_reenitted_after_compaction_when_unchanged():
    """★核心回归★ 压缩后 L1 完全不变，四段仍须重新出现在上下文里。

    这是修好前的失败点：段被 replace 折走、`_retained_ctx` 却说"已发过" → 段永久不再发。
    既有测试 `test_auto_compact_keeps_fresh_l1_for_next_turn` 覆盖不到 —— 它的 seed
    让 L1 **恰好变了**，靠"变化"过关。
    """
    spy = _SpySummaryLlm()
    agent, _ = _run(spy, turns=3)          # 全程不改 L1（get_context 是静态 seed）

    segs = _ctx_segments(agent)
    missing = [n for n in ("self", "player", "recent") if n not in segs]
    assert not missing, (
        f"压缩折走了 L1 段却不再重发，模型失去运行时状态：缺 {missing}；"
        f"现存 {sorted(segs)}（账本里共 "
        f"{sum(1 for e in agent.session.log if e.get('type') == 'user/message' and CTX_MARK in json.dumps(e.get('data', {}), ensure_ascii=False))} 条 ctx 消息）"
    )
    print(f"✓ 压缩后 L1 仍完整：{sorted(segs)}")


def test_l1_baseline_invalidated_only_for_evicted_segments():
    """逐段核对，不是无脑全作废：没被折走的段不该被作废（否则同一段会两条并存）。"""
    spy = _SpySummaryLlm()
    agent, _ = _run(spy, turns=3)
    # 现存段 → 基线必须与 surface 里那条**一致**（不是 None、也不是别的段的文本）
    segs = _ctx_segments(agent)
    assert segs, "压缩后一段都不剩，前提不成立"
    for name, text in segs.items():
        assert agent._retained_ctx.get(name) == text, (
            f"{name} 段仍在 surface 里，基线却没对上（会造成重复发送）："
            f"\n  基线 = {agent._retained_ctx.get(name)!r}\n  实际 = {text!r}")
    print(f"✓ 未被折走的段基线保持同步（{sorted(segs)}）")


def test_manual_compaction_also_invalidates(tmp_path):
    """手动压缩（/compact、面板按钮）走的是另一个入口，同样要作废基线。"""
    spy = _SpySummaryLlm()
    bridge = _make_bridge()
    comp = _forced_compactor(spy)
    loop = AgentLoop.reset_for_test(storage_root=ROOT + "_manual", llm=EchoLlmClient(),
                                    bridge=bridge, compactor=comp)
    agent = loop.create(NPC)
    _seed_history(agent, pairs=2)
    agent.send("第一问")
    asyncio.run(agent.run_until_idle())

    # 手动压缩（idle 时）
    report = asyncio.run(agent.compact_now())
    if report is None:
        print("· 手动压缩本次无可压切点（跳过）")
        return
    agent.send("第二问")
    asyncio.run(agent.run_until_idle())

    segs = _ctx_segments(agent)
    missing = [n for n in ("self", "player", "recent") if n not in segs]
    assert not missing, f"手动压缩后 L1 段丢失：缺 {missing}；现存 {sorted(segs)}"
    print(f"✓ 手动压缩后 L1 仍完整：{sorted(segs)}")


if __name__ == "__main__":
    test_compaction_material_excludes_runtime_context()
    test_compaction_material_still_has_dialogue()
    test_l1_segments_reenitted_after_compaction_when_unchanged()
    test_l1_baseline_invalidated_only_for_evicted_segments()
    test_manual_compaction_also_invalidates(None)
    print("\n压缩 × L1 护栏测试全部通过")
