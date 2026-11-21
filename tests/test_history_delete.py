"""历史删除测试：软删注记 + 双投影过滤 + 与压缩逻辑的耦合（09-12）

覆盖：
  1) 基本删除：surface 与 UI 投影同步消失；每条 item 带 seq（C# 请求删除的定位依据）
  2) 重放安全：save → load 后目录/投影逐位一致（随存档固化）
  3) 老 Python 兼容守卫：history/delete 不在 SURFACE_TYPES（未知类型早退不崩）
  4) 坑 A：纪要 seq 落在 turn 跨度内 —— 部分覆盖豁免 / 全覆盖随删 / 跨界 drop_seqs
  5) 坑 B：pruner 替换节点 —— 原文活着豁免 / 原文被删跨界跟随；真实管线 prune-then-abort
  6) fail-closed：删后配对不平衡整体拒绝，账本不动
  7) 压缩耦合：先压缩后删除（全覆盖随删/部分豁免/多次删除叠加）、先删除后压缩、删→存→读→再压
  8) WS 往返：preview → delete → 重放历史；非法回合 ok:false
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
from types import SimpleNamespace
from typing import Dict, List

import pytest

from agent_loop.session.session import Session, SURFACE_TYPES, DELETE_TYPE, pairing_balanced
from agent_loop.storage.persistence import save_session, load_session
from agent_loop.session.history import project_ui_history
from agent_loop.session.history_ops import (DeleteError, plan_delete, append_delete,
                                    _summary_audits, _represented, live_turn_count)
from agent_loop.session.stats import UsageTracker
from agent_loop.compaction import Compressor
from agent_loop.compaction.pruner import ToolResultPruner
from agent_loop.transport.ws_channel import WsServer, ChatHub

NPC = "测试者"


# ---------- 桩与帮手 ----------

class _OkLlm:
    async def generate(self, system, messages, tools, **kw):
        return SimpleNamespace(text="<compacted-summary>\n## 纪要\n- 摘要内容\n</compacted-summary>",
                               tool_calls=[])


class _FailLlm:
    async def generate(self, system, messages, tools, **kw):
        return SimpleNamespace(text="", tool_calls=[])


def _mk_session() -> Session:
    return Session(id=NPC, header={"id": NPC, "cwd": "/tmp"})


def _add_turn(s: Session, n: int, user_text: str = None, reply_text: str = None,
              tool_result: str = None, call_id: str = None) -> Dict[str, int]:
    """追加一个完整回合，返回关键事件 seq。"""
    out: Dict[str, int] = {}
    out["start"] = s.append("turn/start", {"turn": n})["seq"]
    ev = s.append("user/message",
                  {"role": "user", "id": f"u{n}",
                   "content": [{"type": "text", "text": user_text or f"问{n}"}]},
                  {"surfaceOp": "append"})
    out["user"] = ev["seq"]
    if tool_result is not None:
        ev = s.append("assistant/message",
                      {"message": {"role": "assistant", "id": f"c{n}",
                                   "content": [{"type": "tool-call", "toolCallId": call_id or f"t{n}",
                                                "name": "工具", "arguments": {}}]}},
                      {"surfaceOp": "append"})
        out["call"] = ev["seq"]
        ev = s.append("tool/result",
                      {"message": {"role": "tool", "content": [
                          {"type": "tool-result", "toolCallId": call_id or f"t{n}",
                           "content": [{"type": "text", "text": tool_result}]}]}},
                      {"surfaceOp": "append"})
        out["result"] = ev["seq"]
    ev = s.append("assistant/message",
                  {"message": {"role": "assistant", "id": f"a{n}",
                               "content": [{"type": "text", "text": reply_text or f"答{n}"}]}},
                  {"surfaceOp": "append"})
    out["reply"] = ev["seq"]
    out["end"] = s.append("turn/end", {"turn": n, "reason": {"kind": "completed"}})["seq"]
    return out


def _all_text(s: Session) -> List[str]:
    out = []
    for m in s.derive_messages():
        for c in m.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                out.append(str(c.get("text") or ""))
    return out


def _is_sum(ev: Dict) -> bool:
    src = (ev.get("data") or {}).get("source") or {}
    return ev.get("type") == "user/message" and src.get("kind") == "plugin" and src.get("plugin") == "compact"


def _summary_seq(s: Session):
    for n in s.surface.nodes:
        if _is_sum(s.log[n]):
            return n
    return None


# ---------- 1) 基本删除 ----------

def test_delete_removes_turns_from_both_projections():
    s = _mk_session()
    for i in range(1, 6):
        _add_turn(s, i)
    plan = plan_delete(s, [2, 3])
    assert plan["ok"] and plan["removed_nodes"] > 0
    append_delete(s, plan)

    texts = _all_text(s)
    assert any("问1" in t for t in texts) and any("答5" in t for t in texts)
    assert not any("问2" in t or "问3" in t or "答2" in t for t in texts)

    out = project_ui_history(s, max_turns=20)
    assert out["complete_turns"] == 3, out
    ui_turns = {i["turn"] for i in out["items"]}
    assert 2 not in ui_turns and 3 not in ui_turns, ui_turns
    assert all("seq" in i for i in out["items"]), "每条 item 必须带 seq（C# 定位删除用）"
    assert [i["turn"] for i in out["items"]] == sorted(i["turn"] for i in out["items"])


# ---------- 2) 重放安全 ----------

def test_delete_replay_safe(tmp_path):
    s = _mk_session()
    for i in range(1, 6):
        _add_turn(s, i)
    append_delete(s, plan_delete(s, [2, 3]))
    save_session(s, root=str(tmp_path))

    s2 = load_session(NPC, root=str(tmp_path))
    assert s2.surface.nodes == s.surface.nodes, "重放后目录必须逐位一致"
    assert s2.derive_messages() == s.derive_messages()
    assert project_ui_history(s2, 20) == project_ui_history(s, 20)


# ---------- 3) 老版本兼容守卫 ----------

def test_delete_type_not_in_surface_types():
    """history/delete 必须不进 SURFACE_TYPES：老 Python 重放时对未知类型早退忽略（不崩）。"""
    assert DELETE_TYPE not in SURFACE_TYPES


# ---------- 4) 坑 A：纪要 seq 落在 turn 跨度内 ----------

def _build_summary_inside_turn(covered_turns: int = 3, host_turn: int = 4):
    """turns 1..covered_turns，然后一个「自动压缩」形态的 turn：turn/start → 纪要 → 回合内容。

    注意：宿主 turn 只有一个 turn/start（纪要跟在它后面），模拟自动压缩的真实落位
    （dialogue_agent 先 append turn/start 再跑压缩）⇒ 纪要 seq 必然落在宿主 turn 跨度内。
    返回 (session, 纪要节点 seq, 被折叠 victims)。
    """
    s = _mk_session()
    for i in range(1, covered_turns + 1):
        _add_turn(s, i)
    victims = list(s.surface.nodes)
    s.append("turn/start", {"turn": host_turn})            # ← 纪要落在这个 turn 跨度内
    s.append("compaction/summary",
             {"shadowedRange": {"start": victims[0], "end": victims[-1]},
              "shadowedSeqs": list(victims), "compactionId": "c1"})
    sum_seq = s.append("user/message",
                       {"role": "user", "id": "sum",
                        "content": [{"type": "text", "text": "<compacted-summary>纪要</compacted-summary>"}],
                        "source": {"kind": "plugin", "plugin": "compact", "compactionId": "c1"}},
                       {"surfaceOp": {"op": "replace", "start": victims[0], "end": victims[-1]}})["seq"]
    # 宿主回合自己的对话内容（不能再 append turn/start，否则跨度被第二个 start 抢走）
    s.append("user/message", {"role": "user", "id": f"u{host_turn}",
                             "content": [{"type": "text", "text": f"问{host_turn}"}]},
             {"surfaceOp": "append"})
    s.append("assistant/message",
             {"message": {"role": "assistant", "id": f"a{host_turn}",
                          "content": [{"type": "text", "text": f"答{host_turn}"}]}},
             {"surfaceOp": "append"})
    s.append("turn/end", {"turn": host_turn, "reason": {"kind": "completed"}})
    return s, sum_seq, victims


def test_summary_exempt_on_partial_coverage():
    """部分覆盖：删宿主 turn（含纪要 seq）→ turn 照删、纪要豁免（模型仍记得被折叠的更早历史）。"""
    s, sum_seq, _victims = _build_summary_inside_turn()
    plan = plan_delete(s, [4])
    assert plan["ok"]
    assert plan["keep_seqs"] == [sum_seq], plan
    assert plan["drop_seqs"] == []
    append_delete(s, plan)
    assert sum_seq in s.surface.nodes, "部分覆盖时纪要必须保留"
    assert any("纪要" in t for t in _all_text(s))
    assert not any("问4" in t for t in _all_text(s)), "宿主 turn 自己的内容照删"
    assert pairing_balanced(s.log, s.surface.nodes)


def test_summary_dropped_on_full_coverage():
    """全覆盖：删宿主 turn + 全部被折叠 turn → 纪要一起删（记得的事已不存在）。"""
    s, sum_seq, _victims = _build_summary_inside_turn()
    plan = plan_delete(s, [1, 2, 3, 4])
    assert plan["ok"]
    append_delete(s, plan)
    assert sum_seq not in s.surface.nodes
    assert not any("纪要" in t for t in _all_text(s))
    assert pairing_balanced(s.log, s.surface.nodes)


def test_summary_cross_boundary_drop_seqs():
    """全覆盖但纪要 seq 不在任何删除范围内（宿主 turn 未删）→ 经 drop_seqs 跨界剔除。"""
    s, sum_seq, _victims = _build_summary_inside_turn()
    plan = plan_delete(s, [1, 2, 3])                       # turn 4 保留
    assert plan["ok"]
    assert sum_seq in plan["drop_seqs"], plan
    append_delete(s, plan)
    assert sum_seq not in s.surface.nodes
    assert any("问4" in t for t in _all_text(s)), "turn 4 自身内容保留"
    assert not any("纪要" in t for t in _all_text(s))


# ---------- 5) 坑 B：pruner 替换节点 ----------

def test_replacement_exempt_when_original_survives():
    """删替换节点所在的 turn、原文在别处活着 → 替换节点豁免（否则孤儿回包/悬空调用）。"""
    s = _mk_session()
    t1 = _add_turn(s, 1, tool_result="O" * 50, call_id="c9")
    for i in range(2, 4):
        _add_turn(s, i)
    # 模拟自动压缩在 turn 6 跨度内做 pruner 替换（R 的账本 seq 在 turn 6，原文 O 在 turn 1）
    # 注意：turn 6 只有一个 turn/start，替换事件跟在它后面 ⇒ R 的 seq 落在 turn 6 跨度内
    s.append("turn/start", {"turn": 6})
    s.append("compaction/prune", {"shadowedRange": {"start": t1["result"], "end": t1["result"]},
                                  "shadowedSeqs": [t1["result"]], "shadowedTokenCount": 50})
    r_seq = s.append("tool/result",
                     {"message": {"role": "tool", "content": [
                         {"type": "tool-result", "toolCallId": "c9",
                          "content": [{"type": "text", "text": "R 压缩版"}]}]}},
                     {"surfaceOp": {"op": "replace", "start": t1["result"], "end": t1["result"]},
                      "sourceEventSeqs": [t1["result"]]})["seq"]
    s.append("user/message", {"role": "user", "id": "u6",
                             "content": [{"type": "text", "text": "问6"}]}, {"surfaceOp": "append"})
    s.append("assistant/message",
             {"message": {"role": "assistant", "id": "a6",
                          "content": [{"type": "text", "text": "答6"}]}},
             {"surfaceOp": "append"})
    s.append("turn/end", {"turn": 6, "reason": {"kind": "completed"}})

    plan = plan_delete(s, [6])
    assert plan["ok"]
    assert plan["keep_seqs"] == [r_seq] and plan["drop_seqs"] == [], plan
    append_delete(s, plan)
    assert r_seq in s.surface.nodes, "原文活着 → 替换节点必须保留（回包不能消失）"
    assert pairing_balanced(s.log, s.surface.nodes)
    # UI：turn 6 消失，工具结果仍由 turn 1 的原文展示（不缺行）
    out = project_ui_history(s, 20)
    assert not any(i["turn"] == 6 for i in out["items"])
    assert any(i["kind"] == "tool_result" for i in out["items"])

    # 反向：删原文所在 turn → 替换节点跨界跟随（drop_seqs），配对仍平衡
    plan2 = plan_delete(s, [1])
    assert r_seq in plan2["drop_seqs"], plan2
    append_delete(s, plan2)
    assert r_seq not in s.surface.nodes
    assert pairing_balanced(s.log, s.surface.nodes)


def test_prune_then_abort_leaves_standalone_replacement():
    """真实管线：prune 成功 + 摘要失败放弃 → 独立 R 存活；删原文所在 turn 时跨界跟随。"""
    s = _mk_session()
    for i in range(1, 4):
        _add_turn(s, i, tool_result=("长" * 300 if i == 1 else None),
                  call_id=f"t{i}" if i == 1 else None)
    pruner = ToolResultPruner(llm=_FailLlm(), threshold_chars=100, head_chars=20, tail_chars=10)
    comp = Compressor(llm=_FailLlm(), pruner=pruner, ctx_window=800, retain_ratio=0.16)
    report = asyncio.run(comp.compact_now(s, NPC))
    assert report is None, "摘要失败 → 整体压缩 fail-closed 放弃"

    r_seq = next(n for n in s.surface.nodes if s.log[n].get("sourceEventSeqs"))
    o_seq = s.log[r_seq]["sourceEventSeqs"][0]
    assert o_seq not in s.surface.nodes and r_seq in s.surface.nodes, "pruner 替换已生效且独立存活"

    plan = plan_delete(s, [1])                             # O 的 turn
    assert plan["ok"]
    assert r_seq in plan["drop_seqs"], plan
    append_delete(s, plan)
    assert r_seq not in s.surface.nodes
    assert pairing_balanced(s.log, s.surface.nodes)


# ---------- 6) fail-closed ----------

def test_pairing_fail_closed_rejects_and_leaves_log_untouched():
    """崩溃残局形态：call 在 turn 1、result 在 turn 2 → 删 turn 2 会悬空调用 → 整体拒绝。"""
    s = _mk_session()
    s.append("turn/start", {"turn": 1})
    s.append("user/message", {"role": "user", "id": "u1",
                             "content": [{"type": "text", "text": "问1"}]}, {"surfaceOp": "append"})
    s.append("assistant/message",
             {"message": {"role": "assistant", "id": "c1",
                          "content": [{"type": "tool-call", "toolCallId": "x",
                                       "name": "工具", "arguments": {}}]}},
             {"surfaceOp": "append"})
    s.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    s.append("turn/start", {"turn": 2})
    s.append("tool/result",
             {"message": {"role": "tool", "content": [
                 {"type": "tool-result", "toolCallId": "x",
                  "content": [{"type": "text", "text": "r"}]}]}},
             {"surfaceOp": "append"})
    s.append("turn/end", {"turn": 2, "reason": {"kind": "completed"}})

    plan = plan_delete(s, [2])
    assert not plan["ok"] and "配对" in plan["reason"], plan
    assert not any(ev.get("type") == DELETE_TYPE for ev in s.log), "拒绝时账本不得被写入"
    with pytest.raises(DeleteError):
        append_delete(s, plan)


def test_delete_blocked_during_open_turn():
    s = _mk_session()
    for i in range(1, 3):
        _add_turn(s, i)
    s.append("turn/start", {"turn": 3})                    # 半截回合
    with pytest.raises(DeleteError):
        plan_delete(s, [1])


# ---------- 7) 与压缩逻辑的耦合 ----------

def _compact_session(turns: int = 6, ctx: int = 400):
    s = _mk_session()
    for i in range(1, turns + 1):
        _add_turn(s, i)
    comp = Compressor(llm=_OkLlm(), ctx_window=ctx, retain_ratio=0.16)
    report = asyncio.run(comp.compact_now(s, NPC))
    return s, report


def test_compact_then_delete_full_coverage_drops_summary():
    """先压缩后删除：删光全部 turn（全覆盖）→ 纪要随删（drop_seqs 跨界）。"""
    s, report = _compact_session()
    assert report is not None, "前提：整体压缩成功"
    sum_seq = _summary_seq(s)
    assert sum_seq is not None

    plan = plan_delete(s, [1, 2, 3, 4, 5, 6])
    assert plan["ok"], plan
    append_delete(s, plan)
    assert sum_seq not in s.surface.nodes, "全覆盖 → 纪要必须随删"
    assert s.surface.nodes == [], "可删光：surface 为空合法"
    assert _all_text(s) == []
    assert pairing_balanced(s.log, s.surface.nodes)


def test_compact_then_delete_partial_keeps_summary():
    """先压缩后删除：只删部分被折叠 turn → 部分覆盖 → 纪要保留。

    手动压缩在 idle 跑，纪要落在 turn 之间的缝隙里、不在任何删除范围内 ⇒ 连豁免登记
    （keep_seqs）都不需要， untouched 即正确。
    """
    s, report = _compact_session(turns=8, ctx=400)
    assert report is not None
    sum_seq = _summary_seq(s)
    assert sum_seq is not None

    plan = plan_delete(s, [1])                             # 只删第 1 轮 → 必然部分覆盖
    assert plan["ok"]
    assert sum_seq not in plan["drop_seqs"], "部分覆盖不得随删"
    append_delete(s, plan)
    assert sum_seq in s.surface.nodes
    assert any("<compacted-summary>" in t for t in _all_text(s))
    assert not any("问1" in t for t in _all_text(s))


def test_prior_deleted_counts_as_covered():
    """多次删除叠加：第一次部分覆盖保留纪要，第二次凑齐覆盖 → 纪要随删。"""
    s, _report = _compact_session(turns=8, ctx=400)
    sum_seq = _summary_seq(s)
    append_delete(s, plan_delete(s, [1]))
    assert sum_seq in s.surface.nodes

    plan = plan_delete(s, [2, 3, 4, 5, 6, 7, 8])
    assert plan["ok"]
    assert sum_seq in plan["drop_seqs"], "此前已删 + 本次在删 = 全覆盖"
    append_delete(s, plan)
    assert sum_seq not in s.surface.nodes


def test_pre_turn_plugin_notice_does_not_block_full_coverage():
    """turn 1 之前的 plugin 通知不得把整段纪要豁免掉（09-17 实机事故，用户报"删了还记着"）。

    现场：C# 在会话最前面发一条 `@python-harness/contacts`「这是你们之间的第一段传音」，
    它的 seq 在 turn 1 的范围之前 ⇒ 任何按回合的删除范围都盖不到它。而 `_represented`
    把整段前缀（含这条）都算成纪要代表的成员 ⇒ `rep <= range_union` 恒 False ⇒
    **"全部删除"永远删不掉折叠记忆**，模型照旧读到 `## 约定与末` 里那句"约定来年开春
    好好聚一聚"，用户以为删干净了。

    判据要问的是「这段纪要还会不会替已删内容续命」——已经被折走（不在 surface）的成员
    本就没有独立可见的内容，不该挡路。
    """
    s = _mk_session()
    s.append("user/message",
             {"role": "user", "id": "first-contact",
              "content": [{"type": "text", "text": "（系统：这是你们之间的第一段传音。）"}],
              "source": {"kind": "plugin", "plugin": "@python-harness/contacts"}},
             {"surfaceOp": "append"})
    for i in range(1, 7):
        _add_turn(s, i)
    comp = Compressor(llm=_OkLlm(), ctx_window=400, retain_ratio=0.16)
    assert asyncio.run(comp.compact_now(s, NPC)) is not None, "前提：整体压缩成功"
    sum_seq = _summary_seq(s)
    assert sum_seq is not None
    rep = _represented(s, sum_seq, _summary_audits(s), {})
    assert 0 in rep, "前提：那条通知确实被折进了纪要（否则本用例没复现事故）"

    plan = plan_delete(s, [1, 2, 3, 4, 5, 6])
    assert plan["ok"], plan
    assert sum_seq in plan["drop_seqs"], "折叠记忆必须随删，否则模型还记得被删内容"
    append_delete(s, plan)
    assert sum_seq not in s.surface.nodes
    assert not any("compacted-summary" in t for t in _all_text(s))


def test_delete_then_compact_keeps_coherent():
    """先删除后压缩：压缩在删薄的 surface 上选段，纪要正常落账且被删内容不再出现。"""
    s2 = _mk_session()
    for i in range(1, 7):
        _add_turn(s2, i)
    append_delete(s2, plan_delete(s2, [1, 2]))
    comp = Compressor(llm=_OkLlm(), ctx_window=400, retain_ratio=0.16)
    report = asyncio.run(comp.compact_now(s2, NPC))
    assert report is not None, "删薄的上下文仍应可压缩"

    texts = _all_text(s2)
    assert not any("问1" in t or "问2" in t for t in texts), "被删内容不得复活"
    assert any("<compacted-summary>" in t for t in texts)
    assert pairing_balanced(s2.log, s2.surface.nodes)
    out = project_ui_history(s2, 20)
    assert not any(i.get("turn") in (1, 2) for i in out["items"])


def test_delete_save_load_then_compact(tmp_path):
    """删 → 存 → 读 → 再压缩：固化/回滚语义下全链路可用。"""
    s = _mk_session()
    for i in range(1, 7):
        _add_turn(s, i)
    append_delete(s, plan_delete(s, [1, 2]))
    save_session(s, root=str(tmp_path))

    s2 = load_session(NPC, root=str(tmp_path))
    assert s2.surface.nodes == s.surface.nodes
    comp = Compressor(llm=_OkLlm(), ctx_window=400, retain_ratio=0.16)
    report = asyncio.run(comp.compact_now(s2, NPC))
    assert report is not None
    save_session(s2, root=str(tmp_path))
    s3 = load_session(NPC, root=str(tmp_path))
    assert s3.surface.nodes == s2.surface.nodes
    assert pairing_balanced(s3.log, s3.surface.nodes)


def test_shadowed_seqs_recorded_by_compact():
    """整体压缩必须记 shadowedSeqs（实际被折叠成员），传递闭包可算。"""
    s, report = _compact_session(turns=6, ctx=400)
    assert report is not None
    audit = next(ev for ev in s.log if ev.get("type") == "compaction/summary")
    shadowed = audit["data"].get("shadowedSeqs")
    sr = audit["data"]["shadowedRange"]
    assert shadowed, "必须记录实际被折叠成员（纪要覆盖判断的依据）"
    assert sr["start"] in shadowed and sr["end"] in shadowed
    sum_seq = _summary_seq(s)
    rep = _represented(s, sum_seq, _summary_audits(s), {})
    assert rep is not None and rep, "传递闭包必须可计算"


# ---------- 8) WS 往返 ----------

def test_ws_delete_history_round_trip():
    sess = _mk_session()
    for i in range(1, 4):
        _add_turn(sess, i)

    refreshed = []

    class _FakeAgent:
        id = NPC
        session = sess
        phase = {"kind": "idle"}

        def refresh_context_pressure(self):
            # 删除改了 surface ⇒ 上下文口径必须跟着刷新（老实现在 ws_channel.delete_history 里调）
            refreshed.append(True)

    class _FakeLoop:
        def get(self, npc_id):
            return _FakeAgent() if npc_id == NPC else None

    async def scenario():
        ws = WsServer(port=0)
        hub = ChatHub(_FakeLoop(), ws)
        ws.register_request_handler(hub.handle_request)
        await ws.start()
        try:
            import websockets
            client = await websockets.connect(f"ws://127.0.0.1:{ws.bound_port}")
            try:
                async def rpc(rid: str, method: str, params: Dict):
                    await client.send(json.dumps({"type": "request", "req_id": rid,
                                                  "method": method, "params": params},
                                                 ensure_ascii=False))
                    return json.loads(await asyncio.wait_for(client.recv(), timeout=5))

                resp = await rpc("d1", "preview_delete_history", {"npc_id": NPC, "turns": [1]})
                assert resp["ok"] is True and resp["data"]["preview"]["ok"] is True, resp
                assert resp["data"]["preview"]["removed_nodes"] > 0

                resp2 = await rpc("d2", "delete_history", {"npc_id": NPC, "turns": [1]})
                assert resp2["ok"] is True and resp2["data"]["deleted"]["removed_nodes"] > 0, resp2
                hist = resp2["data"]["history"]
                assert not any(i["turn"] == 1 for i in hist["items"]), "删完直接回放新历史"
                assert {i["turn"] for i in hist["items"]} == {2, 3}, hist
                assert refreshed, "删除成功后必须刷新上下文口径（否则面板一直显示删除前的规模）"

                resp3 = await rpc("d3", "delete_history", {"npc_id": NPC, "turns": [99]})
                assert resp3["ok"] is False and "回合不存在" in resp3["error"], resp3
                assert len(refreshed) == 1, "失败的删除（账本没动）不该刷新"
            finally:
                await client.close()
        finally:
            await ws.stop()

    asyncio.run(scenario())


# ---------- 9) 压缩/删除下的**用量口径**（09-17） ----------

class _UsageLlm:
    """带用量回报的摘要桩：压缩调用的账就靠它验。"""

    def __init__(self, text: str = "<compacted-summary>\n## 纪要\n- 摘要内容\n</compacted-summary>",
                 usage: Dict | None = None):
        self.text = text
        self.usage = usage if usage is not None else {
            "prompt_tokens": 9000, "completion_tokens": 300,
            "prompt_tokens_details": {"cached_tokens": 8000}}
        self.calls = 0

    async def generate(self, system, messages, tools, **kw):
        self.calls += 1
        return SimpleNamespace(text=self.text, tool_calls=[], usage=self.usage)


def _mk_usage_session(turns: int = 3):
    s = _mk_session()
    for i in range(1, turns + 1):
        out = _add_turn(s, i)
        s.log[out["reply"]]["data"]["usage"] = {
            "prompt_tokens": 1000, "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 500}}
        s.append("step/end", {"turn": i, "step": 1})
    return s


def test_delete_never_refunds_spent_tokens():
    """删历史**不退钱**：累计用量按整本 log 重放（不是 surface）。

    这条错了会静默少算，而且**只有删过历史的会话**对不上账——没删过的永远看不出来。
    """
    s = _mk_usage_session(3)
    t0 = UsageTracker()
    t0.rebuild_from_log(s.log)
    before = t0.snapshot()["tokenUsage"]
    assert before["totalTokens"] > 0

    append_delete(s, plan_delete(s, [1, 2, 3]))
    assert s.surface.nodes == [], "前提：删光了"

    t1 = UsageTracker()
    t1.rebuild_from_log(s.log)
    after = t1.snapshot()["tokenUsage"]
    assert after["totalTokens"] == before["totalTokens"]
    assert after["billedInputTokens"] == before["billedInputTokens"]
    assert t1.snapshot()["sessionStats"]["turns"] == 3, "累计轮数按花费口径保留"


def test_live_turn_count_tracks_deletions():
    """现存轮数（liveTurns）与累计轮数（turns）是两件事，删除后必须分家。"""
    s = _mk_usage_session(3)
    assert live_turn_count(s) == 3
    append_delete(s, plan_delete(s, [2]))
    assert live_turn_count(s) == 2
    append_delete(s, plan_delete(s, [1, 3]))
    assert live_turn_count(s) == 0
    t = UsageTracker()
    t.rebuild_from_log(s.log)
    assert t.snapshot()["sessionStats"]["turns"] == 3, "累计不受删除影响"


def test_compaction_call_usage_is_handed_over_for_accounting():
    """摘要调用（整前缀、全会话最贵）= 真实扣费：压缩器必须把用量**交出来**。

    记账归属在会话侧（压缩器由 server.py 独立构造，不认识任何 agent/stats），
    所以这里只钉"交接"这一半；"记进账本 + 重启重放"那半在 test_usage_accounting.py
    用 agent 级跑（自动压缩路径）。
    """
    s = _mk_session()
    for i in range(1, 7):
        _add_turn(s, i)
    llm = _UsageLlm()
    comp = Compressor(llm=llm, ctx_window=400, retain_ratio=0.16)
    report = asyncio.run(comp.compact_now(s, NPC))
    assert report is not None and llm.calls >= 1

    pending = comp.take_pending_usage()
    assert len(pending) == llm.calls, "每次 generate 都要留下用量样本（含 pruner 的工具摘要）"
    t = UsageTracker()
    for raw in pending:
        assert t.add_maintenance_usage(raw)
    assert t.snapshot()["tokenUsage"]["maintenanceInputTokens"] == 9000 * llm.calls
    assert comp.take_pending_usage() == [], "取走即清空：不能重复记账"


def test_compaction_usage_recorded_even_when_summary_fails():
    """fail-closed 也要记账：摘要没按格式回、历史不动，但那次调用**已经花过钱**。"""
    s = _mk_session()
    for i in range(1, 7):
        _add_turn(s, i)
    llm = _UsageLlm(text="")   # 空文本 → _extract_summary 拿不到纪要 → fail-closed
    comp = Compressor(llm=llm, ctx_window=400, retain_ratio=0.16, compaction_retries=0)
    report = asyncio.run(comp.compact_now(s, NPC))
    assert report is None, "前提：摘要不可用 → fail-closed"
    assert not [e for e in s.log if e["type"] == "compaction/summary"], "历史必须原样不动"
    pending = comp.take_pending_usage()
    assert len(pending) == llm.calls == 1, "没压成也要留下用量样本"
    t = UsageTracker()
    for raw in pending:
        t.add_maintenance_usage(raw)
    assert t.snapshot()["tokenUsage"]["compactionCalls"] == 1
