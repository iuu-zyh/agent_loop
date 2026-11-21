"""通讯录 RPC 路由测试：list_contacts / add_contact / remove_contact / list_sessions
+ 新相识风味注入（首回合前 plugin 事件：进账本模型可见、不进 UI 投影、仅一次）
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import types
import urllib.parse

import pytest

from agent_loop.agent_loop import AgentLoop
from agent_loop.storage.contacts_store import ContactsService
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.transport.ws_channel import WsServer, ChatHub


def _make_hub(svc=None):
    """直连 handle_request 的轻量 ChatHub（loop/ws 不被 contact 路由触碰）。

    ⚠ `loop` 虽然是替身，但**必须带 `world_id`**：10-18 起 `list_*` 回包要回传
    "这个答案属于哪个存档"（`_world_fields`），而 `world_id` 是 hub 与 loop 的既有契约
    （`_world_tag()` 也在用）。空串 = 未定档，与这些用例的语境一致。
    """
    loop = types.SimpleNamespace(world_id="", player_name=None)
    return ChatHub(loop=loop, ws=None, contact_service=svc)


def test_contact_rpc_roundtrip():
    root = tempfile.mkdtemp(prefix="agent_loop_contact_rpc_")
    try:
        hub = _make_hub(ContactsService(root))

        async def scenario():
            out_add = await hub.handle_request("add_contact", {"npc_id": "林婉清"})
            assert out_add["added"] is True
            out_add2 = await hub.handle_request("add_contact", {"npc_id": "林婉清"})
            assert out_add2["added"] is False  # 幂等
            out_list = await hub.handle_request("list_contacts", {})
            assert [c["npc_id"] for c in out_list["contacts"]] == ["林婉清"]
            out_rm = await hub.handle_request("remove_contact", {"npc_id": "林婉清"})
            assert out_rm["removed"] is True
            out_list2 = await hub.handle_request("list_contacts", {})
            assert out_list2["contacts"] == []

        asyncio.run(scenario())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_contact_rpc_unit_id_roundtrip():
    """add_contact 带 unit_id → list_contacts 回包透出（C# 侧精确身份的端到端链路）。"""
    root = tempfile.mkdtemp(prefix="agent_loop_contact_rpc_uid_")
    try:
        hub = _make_hub(ContactsService(root))

        async def scenario():
            out = await hub.handle_request("add_contact", {"npc_id": "林婉清", "unit_id": "AAA111"})
            assert out["added"] is True
            listed = (await hub.handle_request("list_contacts", {}))["contacts"]
            assert listed[0]["unit_id"] == "AAA111"

            # 幂等 + 回填/纠错：换了真身给的 ID → 更新，但仍不是"新建"
            out2 = await hub.handle_request("add_contact", {"npc_id": "林婉清", "unit_id": "NEW111"})
            assert out2["added"] is False and out2.get("unit_id_updated") is True
            listed2 = (await hub.handle_request("list_contacts", {}))["contacts"]
            assert len(listed2) == 1 and listed2[0]["unit_id"] == "NEW111"

        asyncio.run(scenario())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_contact_rpc_without_unit_id_is_unchanged():
    """只传 npc_id（老 DLL / 老调用方）→ 回包与记录**没有 unit_id 键**，行为与今天一致。"""
    root = tempfile.mkdtemp(prefix="agent_loop_contact_rpc_legacy_")
    try:
        hub = _make_hub(ContactsService(root))

        async def scenario():
            await hub.handle_request("add_contact", {"npc_id": "林婉清"})
            await hub.handle_request("add_contact", {"npc_id": "林婉清"})   # 幂等
            listed = (await hub.handle_request("list_contacts", {}))["contacts"]
            # ★ 钉住"老记录没有这个键" —— C# 侧解析必须容错（`t?["unit_id"]`），
            #   不能假设它一定存在（否则老回包会让整条名单解析抛异常）
            assert "unit_id" not in listed[0]
            assert listed[0]["source"] == "manual"

            # 空串 unit_id（C# 侧"不知道"时理论上不会发，但发来也不许炸）
            await hub.handle_request("add_contact", {"npc_id": "萧炎", "unit_id": ""})
            listed2 = (await hub.handle_request("list_contacts", {}))["contacts"]
            assert "unit_id" not in [c for c in listed2 if c["npc_id"] == "萧炎"][0]

            # 显式 null 同理
            await hub.handle_request("add_contact", {"npc_id": "云含", "unit_id": None})
            listed3 = (await hub.handle_request("list_contacts", {}))["contacts"]
            assert "unit_id" not in [c for c in listed3 if c["npc_id"] == "云含"][0]

        asyncio.run(scenario())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_contact_rpc_rejects_oversize_unit_id():
    root = tempfile.mkdtemp(prefix="agent_loop_contact_rpc_bad_uid_")
    try:
        hub = _make_hub(ContactsService(root))

        async def scenario():
            with pytest.raises(ValueError):
                await hub.handle_request("add_contact", {"npc_id": "林婉清", "unit_id": "X" * 100})
            assert (await hub.handle_request("list_contacts", {}))["contacts"] == []

        asyncio.run(scenario())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_contact_rpc_sessions_and_errors():
    root = tempfile.mkdtemp(prefix="agent_loop_contact_rpc_err_")
    try:
        hub = _make_hub(ContactsService(root))

        async def scenario():
            # list_sessions：空目录 → 空（不抛）
            out = await hub.handle_request("list_sessions", {})
            assert out["sessions"] == []
            # 缺 npc_id → 明确报错（传输层转 ok:false）
            with pytest.raises(ValueError):
                await hub.handle_request("add_contact", {})
            with pytest.raises(ValueError):
                await hub.handle_request("remove_contact", {"npc_id": "  "})

        asyncio.run(scenario())

        # 服务未注入 → 明确报错
        bare = _make_hub(None)

        async def bare_scenario():
            with pytest.raises(ValueError):
                await bare.handle_request("list_contacts", {})
            with pytest.raises(ValueError):
                await bare.handle_request("add_contact", {"npc_id": "x"})
            with pytest.raises(ValueError):
                await bare.handle_request("list_sessions", {})

        asyncio.run(bare_scenario())
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 新相识风味注入
# ---------------------------------------------------------------------------

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_contacts_acq")
NPC = "林婉清_相识测试"
NOTE = ChatHub.ACQUAINTANCE_NOTE


def _reset(llm=None):
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=None, llm=llm)
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)


def test_acquaintance_note_injected_on_first_turn_only():
    """首回合：账本出现 plugin 相识事件（turn/start 之前），且模型可见（derive_messages 含原文）、UI 投影滤除；
    第二回合：不重复注入。"""
    seen_msgs = []
    _reset(llm=StubLlmClient(fn=lambda req: (seen_msgs.append(req["messages"]) or {"text": "初次见面。", "tool_calls": []})))
    loop = AgentLoop()

    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        await hub.handle_message(NPC, "你好")
        await hub.handle_message(NPC, "再说一句")

    asyncio.run(scenario())

    agent = loop.get(NPC)
    assert agent is not None
    log = agent.session.log

    # 恰好一条相识事件，且位于第一个 turn/start 之前
    notes = [ev for ev in log if ev.get("type") == "user/message"
             and (ev.get("data", {}).get("source", {}) or {}).get("plugin") == "@python-harness/contacts"]
    assert len(notes) == 1, f"相识事件应恰好注入一次，实际 {len(notes)}"
    first_turn = next(ev for ev in log if ev.get("type") == "turn/start")
    assert notes[0]["seq"] < first_turn["seq"]
    # surfaceOp append → 进 Surface（模型可见）
    assert notes[0].get("surfaceOp") == "append"
    assert any(
        b.get("type") == "text" and b.get("text") == NOTE
        for b in notes[0]["data"]["content"]
    )

    # 模型确实看到（首轮 messages 里有该文本；二轮同样还在——账本投影）
    assert any(
        isinstance(m, dict) and any(
            b.get("type") == "text" and b.get("text") == NOTE for b in (m.get("content") or [])
        )
        for m in seen_msgs[0]
    )

    # UI 投影滤除：project_ui_history 不含相识文本、也不含 plugin 用户消息
    from agent_loop.session.history import project_ui_history
    projected = project_ui_history(agent.session)
    texts = [it.get("text", "") for it in projected["items"]]
    assert all(NOTE not in t for t in texts)
    kinds = {it["kind"] for it in projected["items"]}
    assert "user" in kinds  # 玩家消息照常投影
    # 相识不占 turn：两次 player_message → complete_turns == 2
    assert projected["complete_turns"] == 2


def test_acquaintance_not_injected_for_resumed_session():
    """已有对话史的 session（落盘 resume）不注入——只在真正第一次开口时出现。"""
    _reset(llm=StubLlmClient(fn=lambda req: {"text": "好。", "tool_calls": []}))

    async def scenario():
        loop = AgentLoop()
        hub = ChatHub(loop, WsServer(port=0))
        await hub.handle_message(NPC, "第一句")
        loop.flush_all()   # 09-11 冻结语义：模拟存档事件固化（跨进程重启 = 从上次固化状态恢复）
        loop.dispose(NPC)  # 关窗冻结（不落盘）
    asyncio.run(scenario())

    # 重建（resume 同名 session）
    async def scenario2():
        loop = AgentLoop()
        hub = ChatHub(loop, WsServer(port=0))
        await hub.handle_message(NPC, "续聊一句")
        agent = loop.get(NPC)
        notes = [ev for ev in agent.session.log if ev.get("type") == "user/message"
                 and (ev.get("data", {}).get("source", {}) or {}).get("plugin") == "@python-harness/contacts"]
        assert len(notes) == 1, "resume 的 session 不应再次注入相识事件"
        return agent
    agent = asyncio.run(scenario2())
    # resume 后仍只有第一次那条（第一回合注入的）
    turns = [ev for ev in agent.session.log if ev.get("type") == "turn/start"]
    assert len(turns) == 2


# ---------------------------------------------------------------------------
# 隐藏名单 RPC（hide_contact / unhide_contact / list_hidden；09-26 用户拍板）
# ---------------------------------------------------------------------------

def test_hidden_rpc_roundtrip_and_world_known():
    """三个 RPC 端到端往返；`list_hidden` 必须回传 `world_known`（C# 的"不许把空表当真相"闸门）。"""
    root = tempfile.mkdtemp(prefix="agent_loop_hidden_rpc_")
    try:
        hub = _make_hub(ContactsService(root))

        async def scenario():
            out = await hub.handle_request("hide_contact", {"npc_id": "公冶抚", "unit_id": "AAA111"})
            assert out["hidden"] is True
            listed = await hub.handle_request("list_hidden", {})
            assert [h["npc_id"] for h in listed["hidden"]] == ["公冶抚"]
            assert listed["world_known"] is True

            out2 = await hub.handle_request("hide_contact", {"npc_id": "公冶抚"})   # 幂等
            assert out2["hidden"] is False
            assert len((await hub.handle_request("list_hidden", {}))["hidden"]) == 1

            out3 = await hub.handle_request("unhide_contact", {"npc_id": "公冶抚"})
            assert out3["unhidden"] is True
            assert (await hub.handle_request("list_hidden", {}))["hidden"] == []

        asyncio.run(scenario())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_hidden_rpc_rejects_blank_npc_id():
    """空 npc_id 是调用方的 bug → 明确报错，不许静默写进一条脏记录。"""
    root = tempfile.mkdtemp(prefix="agent_loop_hidden_rpc_blank_")
    try:
        hub = _make_hub(ContactsService(root))

        async def scenario():
            with pytest.raises(ValueError):
                await hub.handle_request("hide_contact", {"npc_id": "   "})
            with pytest.raises(ValueError):
                await hub.handle_request("unhide_contact", {})

        asyncio.run(scenario())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_hidden_rpc_world_unknown_is_flagged():
    """未定档（生产形态）→ 空表 + `world_known=False`；写侧抛错（C# 侧因此绝不把空表当真相）。"""
    root = tempfile.mkdtemp(prefix="agent_loop_hidden_rpc_unknown_")
    try:
        hub = _make_hub(ContactsService(root, require_world=True))

        async def scenario():
            out = await hub.handle_request("list_hidden", {})
            assert out["hidden"] == [] and out["world_known"] is False
            with pytest.raises(ValueError):
                await hub.handle_request("hide_contact", {"npc_id": "甲"})

        asyncio.run(scenario())
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_contact_rpc_roundtrip()
    test_contact_rpc_sessions_and_errors()
    test_hidden_rpc_roundtrip_and_world_known()
    test_hidden_rpc_rejects_blank_npc_id()
    test_hidden_rpc_world_unknown_is_flagged()
    test_acquaintance_note_injected_on_first_turn_only()
    test_acquaintance_not_injected_for_resumed_session()
    print("contacts rpc tests OK")
