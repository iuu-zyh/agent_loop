"""tool-call / tool-result 协议配对（11-15，用户报「回合异常：invalid_request_error」）

## 用户看到的现象

    ！

    回合异常：Error code: 400 - {'error': {'message': "An assistant message with
    'tool_calls' must be followed by tool messages responding to each 'tool_call_id'.
    (insufficient tool messages following tool_calls message) ...

玩家侧的表现是「这个 NPC 从某一刻起就再也不回话了」，而日志里只有这一条 400 ——
**没有任何本地报错**，因为我们的账本本身是"写成功"的，只是形状不合法。

## 三种能造成它的路径（本文件逐个钉住）

  ① **`asyncio.gather` 里任一工具被取消**（玩家关窗、换档、连接重建、回合 abort）：
     gather 整体退出，**其余工具的结果丢失** —— 而 assistant 消息（含它们的 tool_calls）
     早就落过 surface 了。这是实测复现出来的那一条。
  ② **数量平衡但 id 错配**：`pairing_balanced` 原先只数个数（+1/−1），
     "1 个 call + 1 个 result 但 toolCallId 对不上"照样放行 ⇒ 删除/压缩把非法目录当合法。
  ③ **任何 early-return 分支漏写落账**：`_one` 有 6 处各写一遍 append，漏一处就是孤儿。

## 为什么值得单独一个文件

这类错误**在本地永远不报**：
  · 落账是成功的（surface 多了一条 assistant），`pairing_balanced` 若只数个数也过；
  · 只有下一次**真正发给服务商**时才 400，而那时上下文早已不在出错现场；
  · 而且它**不会自愈** —— 那条 assistant 永久留在账本里，之后每一回合都 400。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from agent_loop.llm import llm_adapter                       # noqa: E402
from agent_loop.session.session import Session, pairing_balanced   # noqa: E402

TMP = os.path.join(tempfile.gettempdir(), "tool_pairing_contract")


# ============================================================ 工具

def _session_with_call(call_id: str, *, use_toolcallid: bool = False,
                       text: str = "hi") -> Session:
    """造一个「assistant 声明了 1 个 tool-call」的账本（还没回包）。"""
    s = Session("t")
    s.append("user/message", {"role": "user", "id": "u1",
                              "content": [{"type": "text", "text": text}]},
             {"surfaceOp": "append"})
    blk = {"type": "tool-call", "name": "query_world", "arguments": "{}"}
    if use_toolcallid:
        blk["toolCallId"] = call_id
    else:
        blk["id"] = call_id
    s.append("assistant/message",
             {"message": {"role": "assistant", "content": [blk]}},
             {"surfaceOp": "append"})
    return s


def _add_result(s: Session, call_id: str | None):
    blk = {"type": "tool-result", "content": [{"type": "text", "text": "ok"}]}
    if call_id is not None:
        blk["toolCallId"] = call_id
    s.append("tool/result",
             {"message": {"role": "user", "content": [blk]}},
             {"surfaceOp": "append"})


def _wire(s: Session):
    return llm_adapter.canonical_to_openai("sys", s.derive_messages(), None)[0]


def _wire_is_legal(wire) -> bool:
    """按服务端的真实判据检查：每条带 tool_calls 的 assistant 后面，
    必须紧跟**逐个对应**的 role:tool 消息（id 集合相等）。"""
    for i, m in enumerate(wire):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            want = sorted(str(t["id"]) for t in m["tool_calls"])
            got = []
            for nxt in wire[i + 1:]:
                if nxt.get("role") == "tool":
                    got.append(str(nxt.get("tool_call_id")))
                else:
                    break
            if sorted(got) != want:
                return False
    return True


# ============================================================ ① 配对校验：数量对 ≠ 合法

def test_pairing_detects_missing_result():
    """两个 call、只回一个 ⇒ 非法（数量失衡）。这是取消路径留下的形状。"""
    s = _session_with_call("A")
    # 再补一个 call（同一条 assistant 里）
    s2 = Session("t")
    s2.append("assistant/message", {"message": {"role": "assistant", "content": [
        {"type": "tool-call", "id": "A", "name": "x", "arguments": "{}"},
        {"type": "tool-call", "id": "B", "name": "x", "arguments": "{}"},
    ]}}, {"surfaceOp": "append"})
    _add_result(s2, "A")
    assert pairing_balanced(s2.log, s2.surface.nodes) is False, (
        "两个 call 只回一个，配对校验却通过了 —— 下一请求必被 400 拒绝")


def test_pairing_detects_id_mismatch_with_balanced_count():
    """★ 数量平衡但 id 对不上 ⇒ **必须**判非法 ★

    原实现只数 +1/−1，这一条会**通过** —— 而这正是「玩家删了历史之后 NPC 再也不回话」
    的形态：删除方案被当成合法，落账后每一回合都 400。
    """
    s = _session_with_call("A")
    _add_result(s, "Z")          # 数量对（1:1），id 错配
    assert len(s.surface.nodes) == 3, "前提：1 call + 1 result"
    assert pairing_balanced(s.log, s.surface.nodes) is False, (
        "1 个 call + 1 个 result 但 id 不同，配对校验仍通过 —— "
        "服务端按 id 对齐，这一定会 400")


def test_pairing_accepts_correct_ids_in_any_order():
    """id 对得上就合法，**乱序到齐也算**（并发工具本就不保证顺序）。"""
    s = Session("t")
    s.append("assistant/message", {"message": {"role": "assistant", "content": [
        {"type": "tool-call", "id": "A", "name": "x", "arguments": "{}"},
        {"type": "tool-call", "id": "B", "name": "x", "arguments": "{}"},
    ]}}, {"surfaceOp": "append"})
    _add_result(s, "B")
    _add_result(s, "A")
    assert pairing_balanced(s.log, s.surface.nodes) is True


def test_pairing_rejects_result_before_any_call():
    """回包先行（没有等它的 call）⇒ 非法。"""
    s = Session("t")
    _add_result(s, "A")
    assert pairing_balanced(s.log, s.surface.nodes) is False


def test_pairing_tolerates_call_blocks_using_toolcallid_key():
    """`tool-call` 块上的 id **两种键都认**（`id` 与 `toolCallId`）。

    真机落盘用的是 `id`（`create_canonical_assistant_message`），而历史账本与测试夹具里
    也有 `toolCallId` 写法。只认一个会把**合法**账本判成非法 ⇒ 删除/压缩被整体拒绝
    （玩家看到"删不掉"）—— 过度收紧比放过更伤人。
    """
    s = _session_with_call("A", use_toolcallid=True)
    _add_result(s, "A")
    assert pairing_balanced(s.log, s.surface.nodes) is True


def test_pairing_tolerates_results_without_ids():
    """老账本的 result 没带 id ⇒ 按顺序认领，不参与 id 比对。"""
    s = _session_with_call("A")
    _add_result(s, None)          # 无 toolCallId
    assert pairing_balanced(s.log, s.surface.nodes) is True


# ============================================================ ② 真实回合：取消后自愈

def _bridge_and_loop(npc: str, llm_fn):
    from agent_loop.agent_loop import AgentLoop
    from agent_loop.llm.stub_client import StubLlmClient
    from agent_loop.transport.bridge import StubGameBridge

    shutil.rmtree(TMP, ignore_errors=True)
    bridge = StubGameBridge()
    bridge.seed_unit(npc, {"realm": "元婴", "player": {
        "name": "韩立", "relation": "道友", "intim": 60, "same_grid": True}})
    AgentLoop.reset_for_test(storage_root=TMP, bridge=bridge,
                             llm=StubLlmClient(fn=llm_fn))
    return bridge, AgentLoop()


def _surface_ids(agent):
    calls, results = [], []
    for seq in agent.session.surface.nodes:
        ev = agent.session.log[seq]
        if ev["type"] == "assistant/message":
            for b in ev["data"]["message"].get("content", []):
                if b.get("type") == "tool-call":
                    calls.append(str(b.get("id") or ""))
        elif ev["type"] == "tool/result":
            for b in ev["data"]["message"].get("content", []):
                if isinstance(b, dict) and b.get("toolCallId"):
                    results.append(str(b["toolCallId"]))
    return calls, results


def test_cancelled_tool_does_not_leave_an_orphan_call():
    """★ 核心回归：gather 里一个工具被取消，**其余工具的结果不许丢** ★

    这是实测复现出来的真故障：
      · assistant 声明了 A、B 两个 tool_call，已经落盘；
      · B 抛 `CancelledError`（玩家关窗/换档）⇒ `asyncio.gather` 整体退出；
      · A 其实跑完了，但它的结果**再也没落账**；
      · 下一回合把这条 assistant 发出去 ⇒ 400 invalid_request_error。

    修复后：缺失的那条被补成 error 结果，配对恢复完整。
    """
    NPC = "取消测试"
    calls = {"n": 0}

    def fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [
                {"id": "A", "name": "query_world", "arguments": {"topic": "region"}},
                {"id": "B", "name": "query_world", "arguments": {"topic": "region"}},
            ]}
        return {"text": "好", "tool_calls": []}

    bridge, loop = _bridge_and_loop(NPC, fn)

    orig = bridge.call_tool
    seen = {"i": 0}

    async def flaky(name, args, npc_id=""):
        seen["i"] += 1
        if seen["i"] >= 2:
            raise asyncio.CancelledError("模拟：工具执行中被取消（关窗/换档）")
        return orig(name, args, npc_id=npc_id)

    bridge.call_tool = flaky

    from agent_loop.transport.ws_channel import ChatHub, WsServer
    hub = ChatHub(loop, WsServer(port=0))
    try:
        asyncio.run(hub.handle_message(NPC, "查一下地理"))
    except BaseException:
        pass                     # 取消会逃逸，这是模拟的一部分

    agent = loop.get(NPC)
    declared, replied = _surface_ids(agent)
    assert declared, "前提错了：这条 assistant 没落 tool_calls"
    missing = [c for c in declared if c not in replied]
    assert not missing, (
        f"工具结果缺失且没被补写：{missing}｜声明={declared} 回包={replied}\n"
        f"这正是 invalid_request_error 的来源")
    assert pairing_balanced(agent.session.log, agent.session.surface.nodes), "账本配对仍不完整"
    assert _wire_is_legal(_wire(agent.session)), "投影出的请求仍不合法 —— 下一回合会 400"


def test_normal_path_writes_no_backfill():
    """正常路径**不许多写任何东西**（兜底只在真缺失时动手，零开销零噪音）。"""
    NPC = "正常测试"
    calls = {"n": 0}

    def fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [
                {"id": "A", "name": "query_world", "arguments": {"topic": "region"}},
                {"id": "B", "name": "query_world", "arguments": {"topic": "region"}},
                {"id": "C", "name": "inspect_unit", "arguments": {"target": NPC}},
            ]}
        return {"text": "好", "tool_calls": []}

    _bridge_and_loop(NPC, fn)
    from agent_loop.agent_loop import AgentLoop
    from agent_loop.transport.ws_channel import ChatHub, WsServer
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))
    asyncio.run(hub.handle_message(NPC, "查一下"))

    agent = loop.get(NPC)
    declared, replied = _surface_ids(agent)
    assert sorted(declared) == sorted(replied) == ["A", "B", "C"], (declared, replied)
    assert len(replied) == 3, f"正常路径多写了回包：{replied}"


def test_backfill_is_idempotent_when_nothing_is_missing():
    """`_backfill_missing_tool_results` 在全齐时返回 0 且**不落任何事件**。"""
    NPC = "幂等测试"

    def fn(req):
        return {"text": "好", "tool_calls": []}

    _bridge_and_loop(NPC, fn)
    from agent_loop.agent_loop import AgentLoop
    loop = AgentLoop()
    agent = loop.create(NPC)
    n0 = len(agent.session.log)
    got = agent._backfill_missing_tool_results([], 1, 1)
    assert got == 0
    assert len(agent.session.log) == n0, "没有缺失却落了事件"


# ============================================================ ③ C# 侧：不制造这种形状

def test_csharp_result_carries_the_same_call_id():
    """落账的 tool/result 必须带**发起时那个** callId（不能重新生成一个）。

    重新生成 id 是"数量平衡但 id 错配"的直接来源：本地一切正常、配对校验（老版只数个数）
    也过，只有发到服务商才 400。
    """
    src = (ROOT / "agent" / "dialogue_agent.py").read_text(encoding="utf-8")
    # 用"到下一个方法定义"的窗口，而不是固定字符数：`_execute_tool_calls` 前面有近百行注释，
    # 固定窗口会在加了几行注释之后悄悄滑走（判据就变成在测排版长度）。
    i = src.index("def _execute_tool_calls")
    j = src.index("def _backfill_missing_tool_results")
    seg = src[i:j]
    assert 'call_id = tc.get("id")' in seg, "call_id 没从模型给的 id 取 —— 会与 assistant 声明对不上"
    assert "create_canonical_tool_result_message(call_id," in src, (
        "落账时没用同一个 call_id")


# ============================================================ ④ 防御：检测到就删掉那条请求

def test_find_orphan_reports_the_broken_nodes():
    """三种坏形状都要能定位到**具体 seq**（防御机制的作用点）。"""
    from agent_loop.session.session import find_orphan_tool_calls

    # ① 两个 call 只回一个
    s = Session("t")
    s.append("assistant/message", {"message": {"role": "assistant", "content": [
        {"type": "tool-call", "id": "A", "name": "x", "arguments": "{}"},
        {"type": "tool-call", "id": "B", "name": "x", "arguments": "{}"},
    ]}}, {"surfaceOp": "append"})
    _add_result(s, "A")
    bad = find_orphan_tool_calls(s.log, s.surface.nodes)
    # assistant 与它那条已配对的 result 都要删（否则删完 assistant，result 又成孤儿）
    assert len(bad) == 2, f"应级联删掉 assistant + 它的 result，实际 {bad}"

    # ② 孤儿 result
    s2 = Session("t")
    _add_result(s2, "X")
    assert find_orphan_tool_calls(s2.log, s2.surface.nodes) == [0]

    # ③ id 错配
    s3 = _session_with_call("A")
    _add_result(s3, "Z")
    assert len(find_orphan_tool_calls(s3.log, s3.surface.nodes)) == 2


def test_find_orphan_is_empty_for_healthy_ledger():
    """健康账本必须返回空 —— 否则正常对话会被自愈逻辑误删。"""
    from agent_loop.session.session import find_orphan_tool_calls

    s = _session_with_call("A")
    _add_result(s, "A")
    assert find_orphan_tool_calls(s.log, s.surface.nodes) == []

    # 多轮 + 乱序到齐
    s2 = Session("t")
    s2.append("assistant/message", {"message": {"role": "assistant", "content": [
        {"type": "tool-call", "id": "A", "name": "x", "arguments": "{}"},
        {"type": "tool-call", "id": "B", "name": "x", "arguments": "{}"},
    ]}}, {"surfaceOp": "append"})
    _add_result(s2, "B")
    _add_result(s2, "A")
    assert find_orphan_tool_calls(s2.log, s2.surface.nodes) == []


def test_heal_removes_the_broken_request_and_makes_ledger_legal():
    """★ 用户要的防御：检测到坏形状 ⇒ **删掉那条请求**（不是补假结果）★

    为什么删而不要补：
      · 补一条「这次调用没完成」会改变模型看到的历史 —— 它会依据一条自己没真正得到的
        结果继续演（"我刚才查过了…"）；
      · 删掉只是把那半次尝试抹掉，模型看到「上一句我说了话」，干净。
    """
    NPC = "自愈单元"

    def fn(req):
        return {"text": "好", "tool_calls": []}

    _bridge_and_loop(NPC, fn)
    from agent_loop.agent_loop import AgentLoop
    loop = AgentLoop()
    agent = loop.create(NPC)

    # 造一段坏账本
    agent.session.append("assistant/message", {"message": {"role": "assistant", "content": [
        {"type": "tool-call", "id": "A", "name": "query_world", "arguments": "{}"},
        {"type": "tool-call", "id": "B", "name": "query_world", "arguments": "{}"},
    ]}}, {"surfaceOp": "append"})
    agent.session.append("tool/result", {"message": {"role": "user", "content": [
        {"type": "tool-result", "toolCallId": "A",
         "content": [{"type": "text", "text": "结果A"}]}]}}, {"surfaceOp": "append"})
    assert not pairing_balanced(agent.session.log, agent.session.surface.nodes)

    n = agent._heal_tool_pairing(where="单元测试")
    assert n == 2, f"应删掉 2 条，实际 {n}"
    assert pairing_balanced(agent.session.log, agent.session.surface.nodes), "修完仍不合法"
    # 账本只追加：坏节点还在 log 里，只是被 history/delete 遮蔽
    dels = [e for e in agent.session.log if e["type"] == "history/delete"]
    assert len(dels) == 1 and dels[0]["data"]["reason"] == "tool-pairing-heal", dels
    # 幂等
    assert agent._heal_tool_pairing(where="第二次") == 0


def test_heal_does_nothing_on_healthy_ledger():
    """健康账本**一条事件都不许多落**（自愈必须零副作用）。"""
    NPC = "自愈幂等"

    def fn(req):
        return {"text": "好", "tool_calls": []}

    _bridge_and_loop(NPC, fn)
    from agent_loop.agent_loop import AgentLoop
    loop = AgentLoop()
    agent = loop.create(NPC)
    n0 = len(agent.session.log)
    assert agent._heal_tool_pairing() == 0
    assert len(agent.session.log) == n0, "健康账本被误加了事件"


def test_heal_runs_before_the_request_is_sent():
    """自检必须在**组装 messages 之前**跑（先修 ⇒ 这一轮就能正常发出去）。

    放在发失败之后再修就晚了：那一轮已经废了（400 直接抛给玩家），修好也只能救下一轮。
    """
    src = (ROOT / "agent" / "dialogue_agent.py").read_text(encoding="utf-8")
    i_heal = src.index("self._heal_tool_pairing(where=f\"turn={turn} step={step}\")")
    i_msgs = src.index("messages = self._apply_speaker_labels(")
    i_gen = src.index("result = await self.llm.generate(")
    assert i_heal < i_msgs < i_gen, (
        "自检的位置不对：必须早于 messages 组装（现在等于发出去之后才修）")


def test_400_backstop_only_triggers_on_pairing_errors():
    """400 兜底只在**配对类**错误上动手，别把鉴权/余额错也当成配对问题。"""
    from agent_loop.agent.dialogue_agent import DialogueAgent

    real = Exception(
        "Error code: 400 - An assistant message with 'tool_calls' must be followed by "
        "tool messages responding to each 'tool_call_id'. (insufficient tool messages)")
    assert DialogueAgent._looks_like_tool_pairing_error(real) is True

    for other in ("Error code: 401 - invalid api key",
                  "Error code: 402 - insufficient balance",
                  "请求超时",
                  ""):
        assert DialogueAgent._looks_like_tool_pairing_error(Exception(other)) is False, other


# ============================================================ ⑤ 成本：常态 O(1)，不是每 step 全扫

def test_fast_path_is_o1_when_nothing_modified_the_directory():
    """**答复"每次发生都自检的话是全部遍历吗"**：常态不是。

    配对损坏**只可能由改动目录的动作产生**（history/delete、replace），两者都会标脏；
    `append` 永远不会让已有配对失效 —— 一条带 tool-call 的 assistant 刚落下时本来就
    等着回包，那一刻不算坏。所以没动过目录就直接跳过（O(1)）。
    """
    s = Session("t")
    s.surface.mark_pairing_checked()        # 模拟刚检完
    for i in range(50):
        s.append("user/message", {"role": "user", "id": f"u{i}",
                                  "content": [{"type": "text", "text": "x"}]},
                 {"surfaceOp": "append"})
    assert s.surface.pairing_check_needed() is False, (
        "只 append 就要求重扫 —— 那样每个 step 都会全遍历一遍账本")


def test_deleting_or_folding_marks_the_directory_dirty():
    """会改成员的两种动作必须标脏（否则真坏掉时永远不会被发现）。"""
    # ① history/delete
    s = Session("t")
    s.append("user/message", {"role": "user", "id": "u",
                              "content": [{"type": "text", "text": "x"}]}, {"surfaceOp": "append"})
    s.surface.mark_pairing_checked()
    s.append("history/delete", {"ranges": [[0, 0]], "drop_seqs": [], "keep_seqs": [],
                                "turns": [1], "reason": "user"})
    assert s.surface.pairing_check_needed() is True, "删了历史却没标脏 —— 坏掉的配对被漏检"

    # ② replace（压缩/pruner 折叠）
    s2 = Session("t")
    s2.append("user/message", {"role": "user", "id": "u",
                               "content": [{"type": "text", "text": "x"}]}, {"surfaceOp": "append"})
    s2.append("user/message", {"role": "user", "id": "u2",
                               "content": [{"type": "text", "text": "y"}]},
              {"surfaceOp": {"op": "replace", "start": 0, "end": 0}})
    s2.surface.mark_pairing_checked()
    s2.append("user/message", {"role": "user", "id": "u3",
                               "content": [{"type": "text", "text": "z"}]},
              {"surfaceOp": {"op": "replace", "start": 1, "end": 1}})
    assert s2.surface.pairing_check_needed() is True, "折叠了节点却没标脏"


def test_first_check_after_load_always_runs():
    """新会话/读档**首次必须扫**：老账本可能本来就是坏的。

    那不属于"被本次动作弄坏"，没有任何事件会为它标脏 —— 漏掉这一条的症状是
    「升级之后，早就坏掉的那个 NPC 依然永久 400」。

    ★ 判据必须看**脏标记本身**，不能只断言 `pairing_check_needed()` 为 True ★
    （倒计时初值恰好也是 0，两者都会给 True —— 那样这条测试对"漏标脏"完全不敏感，
    变异验证时确认过它抓不住。）
    """
    s = Session("t")
    assert s.surface.pairing_dirty is True, (
        "新会话没有把账本标成待检 —— 升级前就坏掉的 NPC 会一直 400")
    assert s.surface.pairing_check_needed() is True, "新会话首次自检被跳过了"

    # 空账本也要标脏（重放 0 条事件 ≠ 账本一定健康：老账本坏在**删过之后**，
    # 而那种坏在重放时不会重新触发标脏）
    s2 = Session("t", events=[])
    assert s2.surface.pairing_dirty is True


def test_periodic_calibration_catches_missed_dirty_flags():
    """漏标脏的兜底：每 `VERIFY_EVERY` 次自检强制全扫一次。

    这条防的是"今后某条新路径改了目录却忘了标脏"—— 那种漏检会让自愈**永久**失效，
    而周期校准把它降级成"最多晚 64 个 step"。
    """
    s = Session("t")
    s.surface.mark_pairing_checked()
    hits = [i for i in range(s.surface.VERIFY_EVERY * 3)
            if s.surface.pairing_check_needed()]
    assert len(hits) >= 2, f"周期校准没生效（{s.surface.VERIFY_EVERY} 次内一次都没强制扫）"
    assert hits[0] >= s.surface.VERIFY_EVERY - 1, (
        f"校准来得太早（第 {hits[0]} 次）—— 那样等于又回到每次都扫")


def test_heal_skips_scan_and_writes_nothing_via_fast_path():
    """快路径命中时：**不扫、不落事件、返回 0**（这是常态）。"""
    NPC = "快路径"

    def fn(req):
        return {"text": "好", "tool_calls": []}

    _bridge_and_loop(NPC, fn)
    from agent_loop.agent_loop import AgentLoop
    loop = AgentLoop()
    agent = loop.create(NPC)
    agent._heal_tool_pairing()             # 首次：会真扫一遍（新会话要校准）
    n0 = len(agent.session.log)
    assert agent._heal_tool_pairing() == 0  # 之后：快路径
    assert len(agent.session.log) == n0
