"""群成员的**精确身份**（`Room.member_unit_ids`）—— 数据层 / 落盘 / RPC 三层。

## 这条链解决什么

群成员名单的真相在 Python（`Room.members` = 中文名）。C# 侧要画某个成员的立绘、
或在他开口时取 L1 自身段，都得先把中文名换成 `WorldUnitBase` —— 而**重名是常态**
（`UnitLookup` 文件头记的那次真机事故：按名查到另一个人，不报错、不缺字段，
只是把别人的档案端给模型）。

C# 在**建群 / 拉人确认那一刻**手里正握着真身（通讯录关系层条目就是按 unitID 精确查出来的），
所以把 `{npc_id: unitID}` 随 `open_room` / `add_room_members` 一起上行，落进这里，
回包时再回给 C# 喂 `UnitLookup.Pin` —— 本次会话内零歧义。

## 本文件钉的四件事

  ① **落盘往返**：`to_meta` / `from_meta` / `index.json` 都要带上它（懒加载走名录，
     不落盘就等于每次读档都丢）；
  ② **只留当前成员**：与 `joined` 同款 —— 离群即失效，否则"重入群"会沿用旧身份；
  ③ **老存档兼容**：没有这个键 → 空表 → 消费侧回落按名，行为与本功能上线前一致；
  ④ **RPC 宽进**：`member_unit_ids` 是**可选**的，垃圾输入（非 dict / 空键 / 空值）
     只丢那一条，绝不让整次建群失败。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile

import pytest

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.storage.persistence import load_rooms_index, save_rooms_index
from agent_loop.room.room import PLAYER, Room
from agent_loop.transport.ws_channel import ChatHub, _unit_id_map

ROOT = "/tmp/agent_loop_test_room_member_identity"
LIN, LI, WANG = "林婉清", "李四", "王五"


class FakeWs:
    def __init__(self):
        self.frames = []

    async def send_event(self, event, **kw):
        self.frames.append({"event": event, **kw})


def _stub_llm():
    return StubLlmClient(lambda req: {"text": "嗯。", "tool_calls": []})


def _hub():
    """最小可用的生产路径 hub（建群/拉人不唤醒任何成员，所以不需要脚本化回复）。"""
    shutil.rmtree(ROOT, ignore_errors=True)
    b = StubGameBridge()
    for who in (LIN, LI, WANG):
        b.seed_unit(who, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州·白帝城",
                          "player": {"name": "韩立", "relation": "好友", "intim": 120}})
    loop = AgentLoop.reset_for_test(storage_root=ROOT, bridge=b, llm=_stub_llm())
    return ChatHub(loop, FakeWs(), max_concurrent_turns=4)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tmp_root():
    d = tempfile.mkdtemp(prefix="agent_loop_room_uid_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# ① 数据层：Round-trip / 裁剪 / 老存档
# ══════════════════════════════════════════════════════════════════

def test_member_unit_ids_roundtrip():
    r = Room(id="g1", members=[LIN, LI])
    r.add_member  # （只是让人一眼看到字段来自 add_member）
    r.member_unit_ids[LIN] = "Xs6JDI"
    r.member_unit_ids[LI] = "BOlQu6"

    meta = r.to_meta()
    assert meta["member_unit_ids"] == {LIN: "Xs6JDI", LI: "BOlQu6"}

    back = Room.from_meta(json.loads(json.dumps(meta)))
    assert back.member_unit_ids == {LIN: "Xs6JDI", LI: "BOlQu6"}
    assert back.unit_id_of(LIN) == "Xs6JDI"
    assert back.unit_id_of("查无此人") == ""      # 不知道 → 空串（不是 None、不是占位）


def test_meta_prunes_ids_of_non_members():
    """`to_meta` 只带当前成员的身份 —— 手工构造/将来迁移可能留下离群条目。"""
    r = Room(id="g1", members=[LIN])
    r.member_unit_ids = {LIN: "AAA111", LI: "BBB222"}    # LI 已不在 members 里
    assert r.to_meta()["member_unit_ids"] == {LIN: "AAA111"}

    back = Room.from_meta({"room_id": "g1", "members": [LIN],
                           "member_unit_ids": {LIN: "AAA111", LI: "BBB222"}})
    assert back.member_unit_ids == {LIN: "AAA111"}


def test_legacy_meta_without_the_key_loads_as_empty():
    """★ 本功能上线前的存档没有这个键 —— 必须读成空表，而不是抛异常/None。"""
    back = Room.from_meta({"room_id": "g1", "title": "旧群", "members": [LIN, LI],
                           "joined": {LIN: 0}, "last_seq": 7})
    assert back.member_unit_ids == {}
    assert back.unit_id_of(LIN) == ""
    assert back.member_unit_ids == {}          # 形状对：空 dict，消费侧 `.get` 安全
    # 异形值（不是 dict）同样兜底
    assert Room.from_meta({"room_id": "g2", "members": [LIN],
                           "member_unit_ids": ["不是字典"]}).member_unit_ids == {}


def test_add_member_takes_optional_unit_id_and_ignores_blank():
    r = Room(id="g1")
    assert r.add_member(LIN, joined_at=0, unit_id="Xs6JDI") is True
    assert r.member_unit_ids == {LIN: "Xs6JDI"}
    # 不带 / 空串 / 纯空白：**不写键**（"键不存在"才是明确的"不知道"）
    assert r.add_member(LI, joined_at=0) is True
    assert LI not in r.member_unit_ids
    assert r.add_member(WANG, joined_at=0, unit_id="   ") is True
    assert WANG not in r.member_unit_ids
    # 玩家不入列（老语义：`members` 只放 NPC），带了 id 也不写
    assert r.add_member(PLAYER, joined_at=0, unit_id="PLAYER") is False
    assert PLAYER not in r.member_unit_ids


def test_kick_drops_the_unit_id_and_rejoin_takes_a_new_one():
    """离群即失效 —— 留着会让"重入群"沿用可能已指向别人的旧身份。"""
    r = Room(id="g1")
    r.add_member(LIN, joined_at=0, unit_id="OLD999")
    assert r.kick(LIN) is True
    assert LIN not in r.member_unit_ids
    # 重入：必须用**新的** id（老调用点不给 → 就是"不知道"）
    r.add_member(LIN, joined_at=5, unit_id="NEW111")
    assert r.unit_id_of(LIN) == "NEW111"
    r.kick(LIN)
    r.add_member(LIN, joined_at=9)
    assert r.unit_id_of(LIN) == ""


# ══════════════════════════════════════════════════════════════════
# ② 落盘：index.json 往返 + 懒加载
# ══════════════════════════════════════════════════════════════════

def test_index_json_carries_ids_and_reloads(tmp_root):
    r = Room(id="g1", members=[LIN, LI], created=123)
    r.member_unit_ids = {LIN: "Xs6JDI"}
    save_rooms_index([r], tmp_root, [])

    metas = load_rooms_index(tmp_root)
    assert len(metas) == 1
    back = Room.from_meta(metas[0])            # 懒加载路径：log 为空
    assert back.member_unit_ids == {LIN: "Xs6JDI"}
    assert back.unit_id_of(LIN) == "Xs6JDI"
    assert back.unit_id_of(LI) == ""           # 只给了 LIN 的身份


def test_index_json_without_ids_still_loads(tmp_root):
    """老 `index.json`（没有这个键）→ 名录照常读出来。"""
    os.makedirs(tmp_root, exist_ok=True)
    with open(os.path.join(tmp_root, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"rooms": [{"room_id": "g1", "members": [LIN], "last_seq": 3}],
                   "retired": []}, f)
    metas = load_rooms_index(tmp_root)
    assert Room.from_meta(metas[0]).member_unit_ids == {}


# ══════════════════════════════════════════════════════════════════
# ③ RoomFlow：建群 / 重开补写
# ══════════════════════════════════════════════════════════════════

def test_open_room_new_room_records_ids():
    hub = _hub()
    room = hub.rooms.open_room([LIN, LI], member_unit_ids={LIN: "Xs6JDI"})
    assert room.members == [LIN, LI]
    assert room.unit_id_of(LIN) == "Xs6JDI"
    assert room.unit_id_of(LI) == ""           # 只覆盖一部分是正常的


def test_open_room_reopen_backfills_ids_for_old_members():
    """老群（建群时没身份）重开时 C# 手上有了 → 补上，**不动** others。"""
    hub = _hub()
    room = hub.rooms.open_room([LIN, LI], member_unit_ids={LIN: "Xs6JDI"})
    rid = room.id
    # 模拟"老群"：把身份清掉（等价于本功能上线前建的群）
    room.member_unit_ids.clear()

    again = hub.rooms.open_room([], room_id=rid, member_unit_ids={LI: "BOlQu6"})
    assert again.id == rid
    assert again.unit_id_of(LI) == "BOlQu6"
    assert again.unit_id_of(LIN) == ""         # 本次没给 → 保持"不知道"，不猜
    assert again.members == [LIN, LI]          # 名单没被改动


def test_open_room_reopen_does_not_overwrite_with_blank():
    """重开时给了**空集**不许把已有身份抹掉（缺省 = 不表态，不是"清空"）。"""
    hub = _hub()
    room = hub.rooms.open_room([LIN], member_unit_ids={LIN: "Xs6JDI"})
    again = hub.rooms.open_room([], room_id=room.id)
    assert again.unit_id_of(LIN) == "Xs6JDI"


def test_history_response_exposes_member_unit_ids():
    """群窗的填充源就是 `history()` —— 它不带身份的话 C# 永远拿不到。"""
    hub = _hub()
    room = hub.rooms.open_room([LIN, LI], member_unit_ids={LIN: "Xs6JDI"})
    out = hub.rooms.history(room.id, limit=0)
    assert out["members"] == [LIN, LI]
    assert out["member_unit_ids"] == {LIN: "Xs6JDI"}


# ══════════════════════════════════════════════════════════════════
# ④ RPC：open_room / add_room_members 的解析与回包
# ══════════════════════════════════════════════════════════════════

def test_rpc_open_room_parses_member_unit_ids():
    hub = _hub()

    async def go():
        out = await hub.handle_request("open_room", {
            "members": [LIN, LI], "limit": 0,
            "member_unit_ids": {LIN: "Xs6JDI", LI: "  "},     # LI 是空白 → 丢掉那一条
        })
        return out

    out = _run(go())
    assert out["member_unit_ids"] == {LIN: "Xs6JDI"}
    assert out["members"] == [LIN, LI]


def test_rpc_open_room_from_legacy_caller_still_works():
    """老 DLL 不传 member_unit_ids → 一切照旧（这是兼容性的核心一格）。"""
    hub = _hub()

    async def go():
        return await hub.handle_request("open_room", {"members": [LIN], "limit": 0})

    out = _run(go())
    assert out["members"] == [LIN]
    assert out["member_unit_ids"] == {}


def test_rpc_add_room_members_passes_ids_and_returns_them_all():
    hub = _hub()

    async def go():
        first = await hub.handle_request("open_room", {"members": [LIN], "limit": 0,
                                                       "member_unit_ids": {LIN: "Xs6JDI"}})
        rid = first["room_id"]
        add = await hub.handle_request("add_room_members", {
            "room_id": rid, "members": [LI], "member_unit_ids": {LI: "BOlQu6"}})
        return rid, add

    rid, add = _run(go())
    assert add["added"] == [LI]
    assert add["members"] == [LIN, LI]
    # ★ 回包给的是**全员**身份（不只新人）：老成员这一轮顺带也能补上
    assert add["member_unit_ids"] == {LIN: "Xs6JDI", LI: "BOlQu6"}


def test_rpc_add_room_members_without_ids_is_unchanged():
    hub = _hub()

    async def go():
        first = await hub.handle_request("open_room", {"members": [LIN], "limit": 0})
        return await hub.handle_request("add_room_members",
                                        {"room_id": first["room_id"], "members": [LI]})

    add = _run(go())
    assert add["added"] == [LI]
    assert add["members"] == [LIN, LI]
    assert add.get("member_unit_ids") == {}


def test_failed_open_room_does_not_leave_ids_behind():
    """建群失败（既无 room_id 又无 members）→ 抛错，且不留下任何群。"""
    hub = _hub()

    async def go():
        with pytest.raises(ValueError):
            await hub.handle_request("open_room", {"member_unit_ids": {LIN: "Xs6JDI"}})

    _run(go())
    assert hub.rooms.list_rooms() == []


# ══════════════════════════════════════════════════════════════════
# ⑤ `_unit_id_map` 清洗（宽进严出 —— 绝不让可选字段搞垮建群）
# ══════════════════════════════════════════════════════════════════

def test_unit_id_map_tolerates_garbage():
    assert _unit_id_map(None) == {}
    assert _unit_id_map("不是字典") == {}
    assert _unit_id_map([1, 2]) == {}
    assert _unit_id_map({}) == {}
    # 空键 / 空值 / 纯空白 → 丢那一条
    assert _unit_id_map({"": "Xs6JDI", "  ": "Y", LIN: "  ", LI: None}) == {}
    # 正常 + 混杂
    assert _unit_id_map({LIN: "Xs6JDI", "": "Z", LI: 123}) == {LIN: "Xs6JDI", LI: "123"}
    # 键值两端的空白必须去掉（否则 " 林婉清" 与 "林婉清" 会被当成两个人）
    assert _unit_id_map({f" {LIN} ": " Xs6JDI "}) == {LIN: "Xs6JDI"}


# ══════════════════════════════════════════════════════════════════
# ⑥ 边界翻译搬走了（09-17）
# ══════════════════════════════════════════════════════════════════
# 原先这里钉的是"闸门里的 `translate_member_ids`"。那道翻译已**整体搬到
# `DialogueAgent._inject_person_ids`**（群聊与单聊同一个注入点；映射表来源也扩成
# 自己 + 玩家 + 同席 + 查询学到的）—— 所以那一组测试跟着搬去了
# `tests/test_unit_refs.py`（本文件继续只管**房间身份表**这一层：落盘 / 裁剪 / 老存档 / RPC）。
