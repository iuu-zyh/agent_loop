"""存档命名空间（09-12）：通讯录 / 对话历史 / 最近索引按存档隔离

起因（用户实机）：换存档打开通讯录，里面还有上一个存档加的好友（姜萌/郦安），
「最近」与对话历史同样是扁平落盘 → 同名 NPC 在不同世界里会串号。

验证：
  1. world_root/sanitize_world_id：空 → 扁平；有 id → worlds/<id>；中文 id 编码；越界截断
  2. ContactsService.set_world：A 存档加的好友在 B 存档不可见，切回 A 仍在；落盘位置正确
  3. list_sessions 只扫当前存档目录（另一个存档的 jsonl 不混进「最近」），且切存档清缓存
  4. AgentLoop.set_world：sessions_root / path_for 随存档切换（会话落盘隔离）
  5. ChatHub._apply_world：load_happened/save_happened 帧带 world_id → 两处同时切换；
     缺 world_id → 沿用现状（不猜、不报错）
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile

from agent_loop.agent_loop import AgentLoop
from agent_loop.bridge import StubGameBridge
from agent_loop.contacts_store import ContactsService
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.persistence import sanitize_world_id, world_root
from agent_loop.ws_channel import WsServer, ChatHub

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_world_scope")
A = "FJLlLl"      # 当前存档（缪嘉歆）
B = "BOlQu6"      # 上一个存档（唐炎）


def _clean():
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)


# ---------- 1. 路径助手 ----------
def test_world_root_and_sanitize():
    assert world_root(ROOT, None) == ROOT
    assert world_root(ROOT, "") == ROOT
    assert world_root(ROOT, A) == os.path.join(ROOT, "worlds", A)
    # 中文 id（理论上不会出现：world_id 是游戏 unitID，但不做假设）→ 百分号编码
    assert sanitize_world_id("缪嘉歆") == "%E7%BC%AA%E5%98%89%E6%AD%86"
    assert "/" not in sanitize_world_id("../../etc")
    assert len(sanitize_world_id("x" * 500)) == 64
    print("✓ world_root/sanitize_world_id 通过")


# ---------- 2. 通讯录按存档隔离 ----------
def test_contacts_isolated_per_world():
    _clean()
    svc = ContactsService(ROOT)
    # 未定存档 → 扁平目录（兼容测试/chat_cli）
    assert svc.root == ROOT
    svc.add_contact("姜萌")

    assert svc.set_world(A, "缪嘉歆") is True
    assert svc.list_contacts() == [], "换存档后不得看到上一个存档的好友"
    svc.add_contact("云含")
    assert [c["npc_id"] for c in svc.list_contacts()] == ["云含"]
    path_a = os.path.join(ROOT, "worlds", A, "contacts.json")
    assert os.path.isfile(path_a)
    with open(path_a, encoding="utf-8") as f:
        assert [c["npc_id"] for c in json.load(f)["contacts"]] == ["云含"]

    assert svc.set_world(B, "唐炎") is True
    assert svc.list_contacts() == [], "另一个存档必须是全新名单"
    svc.add_contact("郦安")
    assert [c["npc_id"] for c in svc.list_contacts()] == ["郦安"]

    # 切回 A：A 的名单仍在（互不污染）
    assert svc.set_world(A) is True, "B → A 是切换"
    assert [c["npc_id"] for c in svc.list_contacts()] == ["云含"]
    assert svc.set_world(A) is False, "同 id 再切应报告未变化（幂等）"
    assert svc.set_world(B) is True
    assert [c["npc_id"] for c in svc.list_contacts()] == ["郦安"]
    # 扁平目录里那条（姜萌）不属于任何存档，不再被读到
    print("✓ 通讯录按存档隔离通过")


# ---------- 3. 「最近」索引按存档隔离 ----------
def test_sessions_index_isolated_per_world():
    _clean()
    svc = ContactsService(ROOT)
    svc.set_world(A)
    a_dir = os.path.join(ROOT, "worlds", A)
    b_dir = os.path.join(ROOT, "worlds", B)
    os.makedirs(a_dir, exist_ok=True)
    os.makedirs(b_dir, exist_ok=True)
    with open(os.path.join(a_dir, "云含.jsonl"), "w", encoding="utf-8") as f:
        f.write("{}\n")
    with open(os.path.join(b_dir, "郦安.jsonl"), "w", encoding="utf-8") as f:
        f.write("{}\n")
    assert [s["npc_id"] for s in svc.list_sessions()] == ["云含"]
    # 切存档必须让索引缓存失效（否则通讯录「最近」还是上一个存档的）
    svc.set_world(B)
    assert [s["npc_id"] for s in svc.list_sessions()] == ["郦安"]
    print("✓ 「最近」索引按存档隔离 + 切档清缓存通过")


# ---------- 4. 会话落盘目录按存档切换 ----------
def test_agent_loop_sessions_root_follows_world():
    _clean()
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=StubGameBridge(), llm=StubLlmClient())
    try:
        loop = AgentLoop()
        assert loop.sessions_root == ROOT, "未定存档时保持扁平布局"
        assert loop.set_world(A, "缪嘉歆") is True
        assert loop.sessions_root == os.path.join(ROOT, "worlds", A)
        assert str(loop.path_for("云含")).replace("\\", "/").endswith(f"worlds/{A}/%E4%BA%91%E5%90%AB.jsonl")
        assert loop.set_world(B) is True
        assert loop.world_id == B and loop.sessions_root == os.path.join(ROOT, "worlds", B)
        print("✓ AgentLoop 会话目录随存档切换通过")
    finally:
        AgentLoop.reset_for_test(storage_root=ROOT)
        _clean()


# ---------- 5. RPC 帧 → 命名空间（load/save_happened） ----------
def test_ws_handlers_apply_world_id():
    _clean()
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=StubGameBridge(), llm=StubLlmClient())
    try:
        loop = AgentLoop()
        svc = ContactsService(ROOT)
        hub = ChatHub(loop, WsServer(port=0), contact_service=svc)

        async def scenario():
            # 进世界帧带存档身份 → 两处同时切换
            out = await hub.handle_request("load_happened", {"world_id": A, "player_name": "缪嘉歆"})
            assert out["discarded"] == 0
            assert loop.world_id == A and loop.player_name == "缪嘉歆"
            assert svc.world_id == A
            assert hub._world_tag() == "缪嘉歆/" + A
            # 存档帧再断言一次（幂等，不炸）
            await hub.handle_request("save_happened", {"world_id": A, "player_name": "缪嘉歆"})
            assert loop.world_id == A
            # 换存档：B
            await hub.handle_request("load_happened", {"world_id": B, "player_name": "唐炎"})
            assert loop.world_id == B and svc.world_id == B
            # 旧 C#（帧里没有 world_id）：沿用现状、不猜、不抛
            await hub.handle_request("load_happened", {})
            assert loop.world_id == B and svc.world_id == B

        asyncio.run(scenario())
        print("✓ load/save_happened 携带 world_id → 命名空间同步切换通过")
    finally:
        AgentLoop.reset_for_test(storage_root=ROOT)
        _clean()


# ---------- 6. 主动开口分隔条的中文短标签 ----------
def test_intent_label_and_ui_projection():
    from agent_loop.history import project_ui_history
    from agent_loop.initiative import INITIATIVE_INTENTS, INITIATIVE_LABELS, intent_label
    from agent_loop.session import Session

    # 11 个真实意图 + 剧情分支都有短标签，且都非空
    assert set(INITIATIVE_INTENTS) <= set(INITIATIVE_LABELS)
    assert intent_label("missing") == "表达思念"
    assert intent_label("npc_custom_key") == "npc_custom_key", "未知键回落原键（不空白）"
    assert intent_label("") == ""

    s = Session(id="云含", header={"id": "云含"})
    s.append("turn/start", {"turn": 1})
    s.append("user/message", {"content": [{"type": "text", "text": "（NPC主动传音：向玩家表达思念）"}],
                              "source": {"kind": "initiative", "intent": "missing", "reason": "久未相见"}})
    s.append("assistant/message", {"message": {"content": [{"type": "text", "text": "好久不见。"}]}})
    s.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    items = project_ui_history(s, max_turns=5)["items"]
    ini = [i for i in items if i["kind"] == "initiative"]
    assert len(ini) == 1
    assert ini[0]["intent_text"] == "表达思念"
    # 关键：伪 user 正文绝不作为玩家气泡出现（UI 上看不到"模拟用户发送的消息"）
    assert not [i for i in items if i["kind"] == "user"], items
    print("✓ 意图短标签 + UI 投影不含伪 user 气泡通过")
