"""生命周期 RPC 测试：open_chat（开窗=激活+回放）/ dispose_agent（关窗=冻结不落盘）/ 待销毁收尾 / 懒落盘
/ 存档语义（09-11：save_happened=固化增量进账本；load_happened=丢弃增量+销毁全部活/冻体）

生命周期契约（README.md 附录 A 桥契约，09-11 存档语义修订）：
  - open_chat = get or create（激活/resume）+ 历史投影；顺带撤销待销毁标记（关窗→立刻重开竞态）
  - dispose_agent：idle 即冻结（历史留内存 _frozen，**不落盘**）；回合在跑只记名（立即返回
    pending），回合收尾统一冻结
  - save_happened（C# 存档钩子）：所有活/冻 agent 历史固化进 jsonl（空账跳过）
  - load_happened（C# 进世界钩子）：未固化增量丢弃 + 全部活/冻 agent 销毁（读档=回到存档时刻）
  - get_history 保持只读不建活体（回归守护）
  - create/dispose 对空账本不落盘：浏览式开关窗零残留（list_sessions 的 mtime 语义不被污染）
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

from agent_loop.agent_loop import AgentLoop
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.ws_channel import WsServer, ChatHub

NPC = "林婉清_生命周期测试"


def _reset(llm=None) -> AgentLoop:
    root = tempfile.mkdtemp(prefix="agent_loop_lifecycle_")
    loop = AgentLoop.reset_for_test(storage_root=root, llm=llm)
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    return loop


def _hub(loop: AgentLoop) -> ChatHub:
    return ChatHub(loop, WsServer(port=0))


def test_open_chat_activates_unknown_npc():
    """open_chat 未知 NPC：建活体（激活）+ 返回空投影；空账本不落盘。"""
    loop = _reset()
    hub = _hub(loop)

    async def scenario():
        out = await hub.handle_request("open_chat", {"npc_id": NPC})
        assert out["npc_id"] == NPC
        assert out["items"] == [] and out["complete_turns"] == 0
        agent = loop.get(NPC)
        assert agent is not None, "open_chat 应激活（建）活体"
        # 幂等复用：再次 open_chat 不重复建
        out2 = await hub.handle_request("open_chat", {"npc_id": NPC})
        assert out2["npc_id"] == NPC
        assert loop.get(NPC) is agent
        # 空账本懒落盘：浏览式激活不留残文件
        assert not loop.path_for(NPC).exists(), "空账本 create 不应写盘"
        return agent

    asyncio.run(scenario())


def test_open_chat_cancels_pending_dispose():
    """关窗→立刻重开竞态：open_chat 撤销待销毁标记，活体不被销。"""
    loop = _reset()
    hub = _hub(loop)

    async def scenario():
        await hub.handle_request("open_chat", {"npc_id": NPC})
        hub._dispose_pending.add(NPC)  # 模拟「关窗时回合在跑」的记名
        await hub.handle_request("open_chat", {"npc_id": NPC})
        assert NPC not in hub._dispose_pending, "重开窗应撤销待销毁"
        assert loop.get(NPC) is not None

    asyncio.run(scenario())


def test_get_history_still_does_not_create():
    """get_history 保持只读：未知 NPC 返回空且不建活体（回归守护）。"""
    loop = _reset()
    hub = _hub(loop)

    async def scenario():
        out = await hub.handle_request("get_history", {"npc_id": NPC})
        assert out["items"] == [] and out["complete_turns"] == 0
        assert loop.get(NPC) is None, "get_history 不得建活体"

    asyncio.run(scenario())


def test_dispose_agent_idle_freezes_and_resume_replays_history():
    """有账后关窗（09-11 冻结语义）：dispose_agent 冻结不落盘；重开（open_chat）从冻结舱
    复活，历史完整回放。"""
    loop = _reset(llm=StubLlmClient(fn=lambda req: {"text": "道友安好。", "tool_calls": []}))
    hub = _hub(loop)

    async def scenario():
        await hub.handle_message(NPC, "第一句")
        assert loop.get(NPC) is not None
        out = await hub.handle_request("dispose_agent", {"npc_id": NPC})
        assert out == {"disposed": True}, out
        assert loop.get(NPC) is None, "idle 关窗应立即冻结（store 摘除）"
        assert not loop.path_for(NPC).exists(), "冻结不落盘：未存档前账本不得出现"

        # 重开 = 冻结舱复活：历史完整回放
        out2 = await hub.handle_request("open_chat", {"npc_id": NPC})
        assert loop.get(NPC) is not None
        assert out2["complete_turns"] >= 1
        assert any(
            it.get("kind") == "user" and "第一句" in it.get("text", "")
            for it in out2["items"]
        ), f"重开后应回放历史，items={out2['items']}"

    asyncio.run(scenario())


def test_dispose_agent_unknown_npc():
    loop = _reset()
    hub = _hub(loop)

    async def scenario():
        out = await hub.handle_request("dispose_agent", {"npc_id": NPC})
        assert out == {"disposed": False, "reason": "unknown_npc"}

    asyncio.run(scenario())


def test_dispose_agent_busy_marks_pending_and_flushes_after_turn():
    """回合中关窗：只记名立即返回（活体保留），回合毕收尾自动落盘销毁。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_fn(req):
        started.set()
        await release.wait()
        return {"text": "迟来的回复", "tool_calls": []}

    loop = _reset(llm=StubLlmClient(fn=_slow_fn))
    hub = _hub(loop)

    async def scenario():
        turn = asyncio.create_task(hub.handle_message(NPC, "你好"))
        await asyncio.wait_for(started.wait(), timeout=5)
        out = await hub.handle_request("dispose_agent", {"npc_id": NPC})
        assert out.get("disposed") is False and out.get("pending") is True, out
        assert loop.get(NPC) is not None, "回合在跑时活体必须保留"
        assert NPC in hub._dispose_pending

        release.set()
        await asyncio.wait_for(turn, timeout=10)

        assert loop.get(NPC) is None, "回合结束应收尾自动冻结"
        assert NPC not in hub._dispose_pending
        assert not loop.path_for(NPC).exists(), "冻结不落盘：未存档前账本不得出现"

        # 存档事件到达：增量固化，账本落盘（关窗期间的回复不丢）
        out_flush = await hub.handle_request("save_happened", {})
        assert out_flush.get("flushed", 0) >= 1, out_flush
        assert loop.path_for(NPC).exists(), "save_happened 后账本应固化落盘"

    asyncio.run(scenario())


def test_dispose_agent_empty_session_leaves_no_file():
    """浏览式开关窗（无任何消息）：create 与 dispose 都不写盘。"""
    loop = _reset()
    hub = _hub(loop)

    async def scenario():
        await hub.handle_request("open_chat", {"npc_id": NPC})
        out = await hub.handle_request("dispose_agent", {"npc_id": NPC})
        assert out == {"disposed": True}
        assert loop.get(NPC) is None
        assert not loop.path_for(NPC).exists(), "空账本开关窗零残留"

    asyncio.run(scenario())


def test_save_happened_flushes_and_load_happened_discards():
    """存档语义主链路（09-11）：聊→冻结（不落盘）→存档固化落盘→进世界丢弃→
    重开从上次固化状态恢复（读档 = 回到存档时刻）。"""
    loop = _reset(llm=StubLlmClient(fn=lambda req: {"text": "道友安好。", "tool_calls": []}))
    hub = _hub(loop)

    async def scenario():
        # ① 聊两回合
        await hub.handle_message(NPC, "存档前说的话")
        await hub.handle_message(NPC, "存档后又说的话")
        await hub.handle_request("dispose_agent", {"npc_id": NPC})   # 关窗冻结
        assert not loop.path_for(NPC).exists(), "未存档前不得落盘"

        # ② 存档：增量固化 → 账本出现
        out = await hub.handle_request("save_happened", {})
        assert out.get("flushed", 0) >= 1, out
        assert loop.path_for(NPC).exists(), "存档后账本应存在"

        # ③ 又聊一句（新增量）→ 读档/进世界：增量丢弃、活体销毁、账本保留
        await hub.handle_message(NPC, "读档前最后一句")
        out2 = await hub.handle_request("load_happened", {})
        assert out2.get("discarded", 0) >= 1, out2
        assert loop.get(NPC) is None, "load 后活体应清空"
        assert loop.path_for(NPC).exists(), "load 丢弃的是内存增量，磁盘账本保留"

        # ④ 重开：从上次固化状态恢复——「存档后又说的话」在，「读档前最后一句」不在
        out3 = await hub.handle_request("open_chat", {"npc_id": NPC})
        texts = [it.get("text", "") for it in out3["items"]]
        assert any("存档前说的话" in t for t in texts), f"固化内容应保留，items={out3['items']}"
        assert not any("读档前最后一句" in t for t in texts), "未固化增量应被丢弃"

    asyncio.run(scenario())


if __name__ == "__main__":
    test_open_chat_activates_unknown_npc()
    test_open_chat_cancels_pending_dispose()
    test_get_history_still_does_not_create()
    test_dispose_agent_idle_freezes_and_resume_replays_history()
    test_dispose_agent_unknown_npc()
    test_dispose_agent_busy_marks_pending_and_flushes_after_turn()
    test_dispose_agent_empty_session_leaves_no_file()
    test_save_happened_flushes_and_load_happened_discards()
    print("lifecycle rpc tests OK")
