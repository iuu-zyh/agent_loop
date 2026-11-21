"""存档身份丢失（09-18 修）—— Python 重启窗口的行为验证。

## 病根（用户 09-18 追问「存储是否考虑不同存档」时查出来的）

`world_id`（= 玩家 unitID）**只活在 Python 进程内存里**（故意不落盘：存一个过期的存档身份
比没有更危险），而"补发身份"原先只有 `save_happened` / `load_happened` 两条事件。
于是 Python 重启（配置面板保存 / BrainLink 拉起 / 崩溃重生）之后：

  ① 开窗从**扁平目录**读账本 ⇒ 看不见本存档的历史（复现：历史条数 0）；
  ② 下一次存档把这份"从扁平目录读来的短账本"**整份覆盖**进 `worlds/<id>/` ⇒ 真存档的
     对话**静默消失**（复现：`['第一段…']` → `['第二段…']`）；
  ③ 这期间加的好友落进扁平 `contacts.json`，切档后看不见。

实机已经发生过一次 Python 中途重启（09-15 02:13，同一游戏 PID 23224 起了两次 Python），
当时只差一条消息就踩上。

## 修复的两层（本文件钉行为，接线契约见 test_world_identity_contract.py）

  · **C# 连接即补发 `world_hello`** + 断线期间的 save/load 排队补投（那是"身份不再丢"的根）；
  · **Python 三道闸门**（防漏网）：`world_hello` 只切命名空间不丢增量、`save_happened`
    换档先丢弃再固化、`flush_all` 按"账本出处"对账拒绝跨命名空间覆盖；
    再加生产形态（游戏拉起）下未定档时**不读写扁平目录**（`require_world`）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import urllib.parse

import pytest

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.storage.contacts_store import ContactsService
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.room.room import Room
from agent_loop.session.session import Session
from agent_loop.storage.persistence import save_session
from agent_loop.transport.ws_channel import WsServer, ChatHub

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_world_identity")
A = "BOlQu6"      # 唐炎（实机当前档）
B = "FJLlLl"      # 缪嘉歆（实机上一个档）
NPC = "云含"


def _clean():
    shutil.rmtree(ROOT, ignore_errors=True)


def _enc(name: str = NPC) -> str:
    return urllib.parse.quote(name, safe="") + ".jsonl"


def _world_file(world: str, npc: str = NPC) -> str:
    return os.path.join(ROOT, "worlds", world, _enc(npc))


def _flat_file(npc: str = NPC) -> str:
    return os.path.join(ROOT, _enc(npc))


def _boot(**kw) -> AgentLoop:
    return AgentLoop.reset_for_test(storage_root=ROOT, bridge=StubGameBridge(),
                                    llm=StubLlmClient(), **kw)


def _hub(loop: AgentLoop) -> ChatHub:
    return ChatHub(loop, WsServer(port=0))


def _req(hub: ChatHub, method: str, params=None):
    return asyncio.run(hub.handle_request(method, params or {}))


def _turn(sess, text: str) -> None:
    """往账本里写一个**闭合**回合（4 条事件）。"""
    n = len([e for e in sess.log if e.get("type") == "turn/start"]) + 1
    sess.append("turn/start", {"turn": n, "scene": ""})
    sess.append("user/message", {"role": "user",
                                 "content": [{"type": "text", "text": text}]})
    sess.append("assistant/message",
                {"message": {"content": [{"type": "text", "text": "（回）" + text}]}})
    sess.append("turn/end", {"turn": n, "reason": {"kind": "completed"}})


def _texts_of_log(log) -> list:
    out = []
    for ev in log:
        if ev.get("type") != "user/message":
            continue
        for b in (ev.get("data") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b["text"] or "")
    return out


def _session_texts(agent) -> list:
    return _texts_of_log(agent.session.log)


def _file_texts(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return _texts_of_log([json.loads(x) for x in f if x.strip()])


# ══════════════════════════════════════════════════════════════════
# 一、world_hello：重连补发身份
# ══════════════════════════════════════════════════════════════════

def test_hello_same_world_keeps_unflushed_increments():
    """★ 重连时 Python 可能**还活着**且手里握着没存档的内容 —— 补发身份不许把它们抹掉。

    这是 `world_hello` 与 `load_happened` 的语义分界：后者是"世界重载"，丢弃未固化增量是
    它的本分；前者只是"补一张身份证"。用错的那一个（C# 补发 load_happened）会让玩家
    刚聊完、WS 抖一下，内容就没了。
    """
    _clean()
    loop = _boot()
    hub = _hub(loop)
    _req(hub, "world_hello", {"world_id": A, "player_name": "唐炎"})
    assert loop.world_id == A
    agent = loop.create(NPC)
    _turn(agent.session, "还没存档的话")

    out = _req(hub, "world_hello", {"world_id": A, "player_name": "唐炎"})   # 重连再补一次
    assert out["changed"] is False and out["discarded"] == 0
    assert loop.get(NPC) is agent, "重连补发身份把活体丢了"
    assert _session_texts(agent) == ["还没存档的话"], "未固化增量被当作跨档残留丢了"
    _clean()


def test_hello_after_restart_restores_world_history():
    """★ 主线回归：重启 → 补发身份 → 开窗就能读到本存档的历史，且后续存档不再丢旧内容。

    修复前这条链是：开窗读到 0 条（从扁平目录读）→ 存档把 `['第一段…']` 覆盖成 `['第二段…']`。
    """
    _clean()
    loop = _boot()
    hub = _hub(loop)
    _req(hub, "load_happened", {"world_id": A, "player_name": "唐炎"})
    agent = loop.create(NPC)
    _turn(agent.session, "第一段：在存档A里说过的话")
    _req(hub, "save_happened", {"world_id": A, "player_name": "唐炎"})
    assert _file_texts(_world_file(A)) == ["第一段：在存档A里说过的话"]

    # ---- Python 重启：新进程、世界里、身份为空 ----
    loop = _boot()
    hub = _hub(loop)
    assert loop.world_id is None, "前提：重启后身份确实为空"
    out = _req(hub, "world_hello", {"world_id": A, "player_name": "唐炎"})
    assert out["changed"] is True and out["world"] == A

    agent2 = loop.create(NPC)
    assert _session_texts(agent2) == ["第一段：在存档A里说过的话"], \
        "补发身份后开窗仍读不到本存档的历史（修复点丢失）"
    _turn(agent2.session, "第二段：重启之后说的")
    _req(hub, "save_happened", {"world_id": A, "player_name": "唐炎"})
    assert _file_texts(_world_file(A)) == ["第一段：在存档A里说过的话", "第二段：重启之后说的"]
    _clean()


def test_hello_after_restart_loads_room_registry():
    """群名录只在 load_happened 里载入过一次 —— 重启后不补，通讯录里的群会"消失"到下次读档。"""
    _clean()
    loop = _boot()
    hub = _hub(loop)
    _req(hub, "load_happened", {"world_id": A, "player_name": "唐炎"})
    loop.rooms["g1"] = Room(id="g1", title="与云含的聚谈", members=[NPC])
    loop.flush_all()

    loop = _boot()
    hub = _hub(loop)
    assert loop.rooms == {}, "前提：重启后内存名录为空"
    out = _req(hub, "world_hello", {"world_id": A, "player_name": "唐炎"})
    assert out["rooms"] == 1 and "g1" in loop.rooms, "补发身份没把群名录带回来"
    _clean()


def test_hello_with_other_world_discards_stale_agents():
    """身份**真的变了**（玩家在断线期间读了别的档）→ 内存里那些活体属于上一个存档，必须丢。

    它们的两条出路都是错的：写进新目录 = 覆盖新档账本，留着 = 账本是别人家的。
    """
    _clean()
    loop = _boot()
    hub = _hub(loop)
    _req(hub, "load_happened", {"world_id": A, "player_name": "唐炎"})
    agent = loop.create(NPC)
    _turn(agent.session, "存档A 的话")

    out = _req(hub, "world_hello", {"world_id": B, "player_name": "缪嘉歆"})
    assert out["changed"] is True and out["discarded"] == 1
    assert loop.get(NPC) is None
    # 换档后的固化绝不写进新档（账本是别人的）
    assert loop.flush_all() == 0
    assert not os.path.exists(_world_file(B)), "上一个存档的账本被写进了新档"
    _clean()


def test_hello_without_identity_keeps_namespace():
    """主菜单 / 旧 DLL：帧里没有 `world_id` → 沿用现状 + 不抛（开机首次连接就是这个形态）。"""
    _clean()
    loop = _boot()
    hub = _hub(loop)
    _req(hub, "load_happened", {"world_id": A, "player_name": "唐炎"})
    out = _req(hub, "world_hello", {})
    assert out["changed"] is False and out["world"] == A
    assert loop.world_id == A
    _clean()


# ══════════════════════════════════════════════════════════════════
# 二、旧路径（没有 hello，身份只在存档帧到达）：先丢弃再固化
# ══════════════════════════════════════════════════════════════════

def test_save_with_new_identity_discards_stale_flat_agents():
    """含答案的对照：这条链就是修复前**静默覆盖**真存档账本的那条。

    修复后的判据有两条，缺一不可：`worlds/A` 里还是老内容（没被覆盖），
    且扁平目录里**没有**新写进去的账本。
    """
    _clean()
    loop = _boot()
    hub = _hub(loop)
    _req(hub, "load_happened", {"world_id": A, "player_name": "唐炎"})
    agent = loop.create(NPC)
    _turn(agent.session, "第一段")
    _req(hub, "save_happened", {"world_id": A, "player_name": "唐炎"})

    loop = _boot()                     # 重启：身份为空
    hub = _hub(loop)
    agent2 = loop.create(NPC)
    _turn(agent2.session, "第二段：身份未知时说的")
    out = _req(hub, "save_happened", {"world_id": A, "player_name": "唐炎"})

    assert out["discarded"] == 1 and out["flushed"] == 0
    assert _file_texts(_world_file(A)) == ["第一段"], "真存档的历史被短账本覆盖了（09-18 那条 bug）"
    assert not os.path.exists(_flat_file()), "身份未知时的账本被写进了扁平目录"
    _clean()


def test_flush_guard_skips_agents_from_another_namespace():
    """纵深防御：即使有人绕过 handler 直接 `set_world`，flush 也不许跨命名空间覆盖。"""
    _clean()
    loop = _boot()
    loop.set_world(A)
    agent = loop.create(NPC)
    _turn(agent.session, "A 的话")
    assert loop.flush_all() == 1

    loop.set_world(None)               # 直接切回未定档（模拟绕过 ChatHub 的路径）
    other = loop.create("姜萌")
    _turn(other.session, "扁平窗口的话")
    loop.set_world(B)                  # 再切到另一个档

    assert loop.flush_all() == 0, "出处不符的账本被固化了"
    assert not os.path.exists(_world_file(B, "姜萌")), "别的命名空间读来的账本写进了 B 档"
    assert _file_texts(_world_file(A)) == ["A 的话"], "A 档的账本被改了"
    _clean()


# ══════════════════════════════════════════════════════════════════
# 三、生产形态（游戏拉起）：未定档时扁平目录一律不碰
# ══════════════════════════════════════════════════════════════════

def test_require_world_blocks_flat_read_and_write():
    _clean()
    os.makedirs(ROOT, exist_ok=True)
    # 扁平目录里放一份"不属于任何存档"的旧账本（历史遗留 / 上一个窗口的残留）
    legacy = Session(id=NPC, header={"id": NPC})
    _turn(legacy, "扁平目录里旧的那句")
    save_session(legacy, ROOT)

    loop = _boot(require_world=True)
    agent = loop.create(NPC)
    assert agent.session.log == [], "生产形态下从扁平目录恢复了账本（读到的是别人的记忆）"
    _turn(agent.session, "身份未知时说的")
    assert loop.flush_all() == 0
    assert _file_texts(_flat_file()) == ["扁平目录里旧的那句"], "扁平账本被改了"

    # 身份补发之后一切照常（闸门只在"未定档"这一种状态下落下）
    loop.set_world(A)
    assert loop.flush_all() == 0, "出处不符（这份账本是从扁平目录起的）→ 仍不许写"
    agent2 = loop.create("姜萌")
    _turn(agent2.session, "身份明确后说的")
    assert loop.flush_all() == 1
    assert _file_texts(_world_file(A, "姜萌")) == ["身份明确后说的"]
    _clean()


def test_require_world_retire_room_does_not_write_flat():
    _clean()
    loop = _boot(require_world=True)
    loop.retire_room("g1")
    assert not os.path.exists(os.path.join(ROOT, "rooms", "index.json")), \
        "未定档时删群写进了扁平目录"
    _clean()


def test_require_world_contacts_fail_closed():
    _clean()
    os.makedirs(ROOT, exist_ok=True)
    # 扁平目录里预置一份"不属于任何存档"的名单（历史遗留）—— 不预置的话"返回空表"与
    # "根本没读"分不开，闸门被摘掉也照样绿（变异验证抓到过这个假绿）。
    with open(os.path.join(ROOT, "contacts.json"), "w", encoding="utf-8") as f:
        json.dump({"contacts": [{"npc_id": "旧档的故人", "added_at": 1, "source": "manual"}]}, f)

    svc = ContactsService(ROOT, require_world=True)
    assert svc.list_contacts() == [], "未定档时读到了扁平目录的好友"
    assert svc.list_sessions() == [], "未定档时扫了扁平目录的 jsonl"
    with pytest.raises(ValueError):
        svc.add_contact(NPC)
    with pytest.raises(ValueError):
        svc.remove_contact(NPC)
    assert [c["npc_id"] for c in json.load(open(os.path.join(ROOT, "contacts.json"),
                                                encoding="utf-8"))["contacts"]] == ["旧档的故人"], \
        "未定档时改了扁平目录的名单"

    svc.set_world(A, "唐炎")            # 身份补发
    assert svc.add_contact(NPC)["added"] is True
    assert os.path.isfile(os.path.join(ROOT, "worlds", A, "contacts.json"))
    assert [c["npc_id"] for c in svc.list_contacts()] == [NPC], "新档名单里混进了扁平目录的好友"
    _clean()


def test_dev_mode_keeps_flat_layout():
    """反向断言：非生产形态（测试 / chat_cli / 手跑 server.py）**保持**扁平布局的原有语义
    —— 闸门一刀切会把整条开发链路堵死（pytest 与 chat_cli 全靠它）。"""
    _clean()
    loop = _boot()                     # require_world 默认 False
    agent = loop.create(NPC)
    _turn(agent.session, "开发形态的话")
    assert loop.flush_all() == 1
    assert os.path.exists(_flat_file())

    svc = ContactsService(ROOT)
    assert svc.add_contact(NPC)["added"] is True
    assert os.path.isfile(os.path.join(ROOT, "contacts.json"))
    _clean()
