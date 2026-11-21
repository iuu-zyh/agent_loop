"""contacts_store 测试：contacts.json 读写往返 / 幂等 add·remove / 坏数据兜底 / 原子写 / 会话索引"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.parse

import pytest

from agent_loop.storage.contacts_store import ContactsService


@pytest.fixture()
def root():
    d = tempfile.mkdtemp(prefix="agent_loop_contacts_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _svc(root):
    return ContactsService(root)


def test_add_list_remove_roundtrip(root):
    svc = _svc(root)
    assert svc.list_contacts() == []

    r1 = svc.add_contact("林婉清")
    assert r1["added"] is True and r1["count"] == 1
    r2 = svc.add_contact("萧炎")
    assert r2["added"] is True and r2["count"] == 2

    names = [c["npc_id"] for c in svc.list_contacts()]
    assert names == ["林婉清", "萧炎"]          # 保持添加顺序
    assert all(c["source"] == "manual" for c in svc.list_contacts())
    assert all(c["added_at"] > 0 for c in svc.list_contacts())

    r3 = svc.remove_contact("林婉清")
    assert r3["removed"] is True and r3["count"] == 1
    assert [c["npc_id"] for c in svc.list_contacts()] == ["萧炎"]


def test_add_idempotent_keeps_added_at(root):
    """重复 add 不重写不改 added_at。"""
    svc = _svc(root)
    svc.add_contact("林婉清")
    first = svc.list_contacts()[0]
    again = svc.add_contact("林婉清")
    assert again["added"] is False and again["count"] == 1
    now = svc.list_contacts()[0]
    assert now == first


def test_remove_idempotent_missing_ok(root):
    svc = _svc(root)
    r = svc.remove_contact("不存在")
    assert r["removed"] is False and r["count"] == 0


# ---------------------------------------------------------------------------
# 精确身份（unit_id）：能存 / 能取 / 能回填
#
# 为什么要持久化：Python 全链路 npc_id 是中文名，而这游戏重名是常态。C# 侧只有在
# NPC 面板点「加好友」那一刻真的握着 WorldUnitBase（panel.unit）才能拿到精确 unitID；
# 不存下来的话，每次读名单都只能按名全图猜，猜错**不报错、不缺字段**。
# ---------------------------------------------------------------------------


def _raw(root):
    """直接读盘（不看内存返回值）——"能存"这件事只有读文件才算验过。"""
    with open(os.path.join(root, "contacts.json"), encoding="utf-8") as f:
        return json.load(f)


def test_add_with_unit_id_persists_it(root):
    svc = _svc(root)
    svc.add_contact("林婉清", "AAA111")
    assert svc.list_contacts()[0]["unit_id"] == "AAA111"
    assert _raw(root)["contacts"][0]["unit_id"] == "AAA111"      # ★ 读盘确认，不只看返回值


def test_add_without_unit_id_omits_the_key(root):
    """老 DLL / 老调用方不带 unit_id → 记录里**根本没有这个键**（不是空串）。
    读侧据此区分"没有精确身份"与"精确身份是空"。"""
    svc = _svc(root)
    svc.add_contact("林婉清")
    assert "unit_id" not in svc.list_contacts()[0]
    assert "unit_id" not in _raw(root)["contacts"][0]


def test_idempotent_add_backfills_missing_unit_id(root, monkeypatch):
    """回填：老记录（无 ID）后来拿到真身 → 补上；但 added_at 不动、added 仍为 False。

    ★ 09-17 补：**把时钟推后 1000 秒再补写**。`added_at` 的粒度是**秒**，而这两次调用
    相隔只有微秒 ⇒ "顺手也把 `added_at` 刷成 now" 这种 bug 在同一秒内**写出来的值完全
    相同**，`after["added_at"] == before["added_at"]` 就退化成**同一秒内的恒真断言**
    （实测：在补写分支塞一句 `c["added_at"] = int(time.time())`，本测试照样全绿）。
    推后时钟，差异才必然可见 —— 那个键回答的是"什么时候加的好友"。
    """
    svc = _svc(root)
    svc.add_contact("林婉清")
    before = svc.list_contacts()[0]

    monkeypatch.setattr(time, "time", lambda: before["added_at"] + 1000)   # ★ 见 docstring
    r = svc.add_contact("林婉清", "AAA111")
    assert r["added"] is False            # 不是新建
    assert r["count"] == 1                # 没多出一条
    assert r.get("unit_id_updated") is True
    after = svc.list_contacts()[0]
    assert after["unit_id"] == "AAA111"
    assert after["added_at"] == before["added_at"]     # ★ 时间戳不许被刷新
    assert after["npc_id"] == before["npc_id"]


def test_idempotent_add_corrects_a_stale_unit_id(root):
    """纠错：存档变动后旧 ID 失效 → 以新的（来自真身）为准。"""
    svc = _svc(root)
    svc.add_contact("林婉清", "OLD999")
    r = svc.add_contact("林婉清", "NEW111")
    assert r["added"] is False and r.get("unit_id_updated") is True
    assert svc.list_contacts()[0]["unit_id"] == "NEW111"

    # 反向：**不带** ID 的重复调用不许把已有的抹掉（那是信息丢失，且静默）
    r2 = svc.add_contact("林婉清")
    assert "unit_id_updated" not in r2
    assert svc.list_contacts()[0]["unit_id"] == "NEW111"


def test_idempotent_add_without_unit_id_is_a_pure_noop(root):
    """不带 unit_id 的重复 add 必须与改动前**逐字段**一致（防"顺手也写点别的"）。"""
    svc = _svc(root)
    svc.add_contact("林婉清")
    first = svc.list_contacts()[0]
    svc.add_contact("林婉清")
    svc.add_contact("林婉清")
    assert svc.list_contacts()[0] == first


def test_blank_unit_id_is_treated_as_absent(root):
    """空白/None 一律当"没提供"（**不报错**）—— 老 DLL 不传是正常情形，不该让加好友失败。"""
    svc = _svc(root)
    for i, blank in enumerate((None, "", "   ", "\t")):
        r = svc.add_contact("空值%d" % i, blank)
        assert r["added"] is True
    assert [c["npc_id"] for c in svc.list_contacts()] == ["空值0", "空值1", "空值2", "空值3"]
    for c in svc.list_contacts():
        assert "unit_id" not in c
    # 已存在的记录 + 空 unit_id → 纯 no-op，也不许抛
    svc.add_contact("林婉清", "AAA111")
    svc.add_contact("林婉清", "")
    svc.add_contact("林婉清", None)
    assert svc.list_contacts()[-1]["unit_id"] == "AAA111"


def test_oversize_unit_id_rejected_without_side_effect(root):
    """超长 unit_id 会落盘并从此当真 → 必须拒绝，且不留半截副作用。"""
    svc = _svc(root)
    with pytest.raises(ValueError):
        svc.add_contact("林婉清", "X" * 100)
    assert svc.list_contacts() == []                    # 拒绝后没建出记录
    assert not os.path.exists(os.path.join(root, "contacts.json"))   # 也没写盘
    # 已有记录 + 超长 ID → 抛错，且旧值原样
    svc.add_contact("林婉清", "AAA111")
    with pytest.raises(ValueError):
        svc.add_contact("林婉清", "Y" * 100)
    assert svc.list_contacts()[0]["unit_id"] == "AAA111"


def test_unit_id_survives_reload_and_remove(root):
    """取：重新构造服务（模拟重启）仍能读到；移除好友后整条记录消失（含 ID）。"""
    _svc(root).add_contact("林婉清", "AAA111")
    svc = _svc(root)                                    # 新实例 = 重新读盘
    assert svc.list_contacts()[0]["unit_id"] == "AAA111"
    svc.remove_contact("林婉清")
    assert svc.list_contacts() == []


def test_bad_json_falls_back_to_empty(root):
    """坏 JSON → 空表兜底（读侧 fail-closed），且后续 add 能正常重建文件。"""
    path = os.path.join(root, "contacts.json")
    os.makedirs(root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{broken json!!")
    svc = _svc(root)
    assert svc.list_contacts() == []
    svc.add_contact("林婉清")
    assert [c["npc_id"] for c in svc.list_contacts()] == ["林婉清"]


def test_weird_shapes_fall_back_to_empty(root):
    os.makedirs(root, exist_ok=True)
    for content in ('{"contacts": "not-a-list"}', '[1,2,3]', ''):
        with open(os.path.join(root, "contacts.json"), "w", encoding="utf-8") as f:
            f.write(content)
        assert _svc(root).list_contacts() == []


def test_atomic_write_leaves_no_tmp(root):
    svc = _svc(root)
    svc.add_contact("林婉清")
    leftovers = [f for f in os.listdir(root) if f.endswith(".tmp")]
    assert leftovers == []
    # 落盘内容可读且结构正确
    with open(os.path.join(root, "contacts.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data["contacts"][0]["npc_id"] == "林婉清"


def test_empty_and_oversize_npc_id_rejected(root):
    svc = _svc(root)
    with pytest.raises(ValueError):
        svc.add_contact("   ")
    with pytest.raises(ValueError):
        svc.add_contact("字" * 100)
    assert svc.list_contacts() == []   # 拒绝后无副作用


def test_list_sessions_scans_jsonl_only(root):
    """会话索引：只认 *.jsonl，中文文件名 unquote 还原，mtime 为整秒；TTL 内走缓存。"""
    os.makedirs(root, exist_ok=True)
    npc = "林婉清_索引"
    p = os.path.join(root, urllib.parse.quote(npc, safe="") + ".jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"session":{}}\n')
    with open(os.path.join(root, "contacts.json"), "w", encoding="utf-8") as f:
        f.write("{}")   # 非 jsonl：必须被排除
    with open(os.path.join(root, "张三.jsonl"), "w", encoding="utf-8") as f:
        f.write("x")

    svc = _svc(root)
    out = svc.list_sessions()
    ids = {s["npc_id"] for s in out}
    assert ids == {npc, "张三"}
    assert all(isinstance(s["mtime"], int) and s["mtime"] > 0 for s in out)

    # TTL 缓存：新增文件后立刻再查不出现（5s 内）
    with open(os.path.join(root, urllib.parse.quote("新同伴", safe="") + ".jsonl"), "w", encoding="utf-8") as f:
        f.write("x")
    assert "新同伴" not in {s["npc_id"] for s in svc.list_sessions()}


def test_list_sessions_missing_dir_returns_empty(root):
    svc = ContactsService(os.path.join(root, "no_such_dir"))
    assert svc.list_sessions() == []


# ---------------------------------------------------------------------------
# 隐藏名单（hidden.json；行内 ✕ 的"关系层隐藏"侧，09-26 用户拍板）
#
# 语义：某 NPC 被隐藏后不再出现在通讯录（关系数据不动）；重现出口 = 他再次主动传音
# （C# 侧调 unhide_contact）。好友优先由 C# 渲染过滤保证，Python 只负责存这份名单。
# ---------------------------------------------------------------------------

def test_hidden_roundtrip_idempotent_and_backfill(root):
    """hide → list → unhide 往返；重复 hide 幂等（hidden_at 不动），补 id 与 add_contact 同款。"""
    svc = _svc(root)
    assert svc.list_hidden() == []

    r1 = svc.hide_contact("公冶抚")
    assert r1["hidden"] is True and r1["count"] == 1
    r2 = svc.hide_contact("姜萌", "AAA111")
    assert r2["hidden"] is True and r2["count"] == 2
    assert [h["npc_id"] for h in svc.list_hidden()] == ["公冶抚", "姜萌"]   # 保持隐藏顺序

    first = svc.list_hidden()[0]
    again = svc.hide_contact("公冶抚")
    assert again["hidden"] is False and again["count"] == 2
    assert svc.list_hidden()[0] == first, "重复 hide 不该刷新 hidden_at"

    # 老记录补空/纠错：带了的 id 与库中不同 → 更新（那条回答的是"这个人是谁"）
    r3 = svc.hide_contact("公冶抚", "BBB222")
    assert r3["hidden"] is False and r3.get("unit_id_updated") is True
    assert svc.list_hidden()[0]["unit_id"] == "BBB222"

    r4 = svc.unhide_contact("公冶抚")
    assert r4["unhidden"] is True and r4["count"] == 1
    assert svc.unhide_contact("公冶抚")["unhidden"] is False, "取消隐藏必须幂等（终态为准）"
    assert [h["npc_id"] for h in svc.list_hidden()] == ["姜萌"]


def test_hidden_omits_unit_id_key_when_absent(root):
    """不传 unit_id → 记录里**根本没有这个键**（与 contacts.json 同款字面形状，读侧据此区分）。"""
    svc = _svc(root)
    svc.hide_contact("林婉清")
    assert "unit_id" not in svc.list_hidden()[0]
    raw = json.load(open(os.path.join(root, "hidden.json"), encoding="utf-8"))
    assert "unit_id" not in raw["hidden"][0]


def test_hidden_oversize_unit_id_rejected_without_side_effect(root):
    """超长 unit_id 会落盘并从此当真 → 拒绝，且不留半截副作用（同 add_contact 铁律）。"""
    svc = _svc(root)
    with pytest.raises(ValueError):
        svc.hide_contact("林婉清", "X" * 100)
    assert svc.list_hidden() == []
    assert not os.path.exists(os.path.join(root, "hidden.json"))


def test_hidden_is_per_world(root):
    """隐藏名单与 contacts.json 同款：按存档隔离，换档不串号。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    svc.hide_contact("甲")
    assert [h["npc_id"] for h in svc.list_hidden()] == ["甲"]

    svc.set_world("W_B", "缪嘉歆")
    assert svc.list_hidden() == [], "换存档后看到了上一个存档的隐藏名单"
    svc.hide_contact("乙")

    svc.set_world("W_A", "唐炎")
    assert [h["npc_id"] for h in svc.list_hidden()] == ["甲"]


def test_hidden_bad_json_falls_back_to_empty(root):
    """坏 JSON → 空表兜底（fail-closed 读侧），不抛。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    os.makedirs(svc.root, exist_ok=True)
    with open(os.path.join(svc.root, "hidden.json"), "w", encoding="utf-8") as f:
        f.write("{ 这不是 json")
    assert svc.list_hidden() == []


def test_hidden_blocked_without_world(root):
    """生产形态未定档 → **拒绝读写扁平目录**（同 contacts.json 的闸门；空表是"拒绝回答"）。"""
    svc = ContactsService(root, require_world=True)
    assert svc.list_hidden() == []
    with pytest.raises(ValueError):
        svc.hide_contact("甲")
    with pytest.raises(ValueError):
        svc.unhide_contact("甲")
    assert not os.path.exists(os.path.join(root, "hidden.json"))


# ---------------------------------------------------------------------------
# 互动时间戳（interactions.json）—— 「最近」排序的**权威**源（10-21 用户报障）
#
# 报障原文：「NPC 主动传音后点开通讯录，他从『最近』里的位置没变」。
# 根因：旧判据取 max(文件 mtime, 内存时间戳)，而 mtime 是「上次**存档**」的时刻
#       —— 存档一次重写全部文件，十几个文件落在同一秒（真机 .953/.956），排序全相等。
# ⇒ 现在的契约（**改这一节前先读懂上面那段**）：
#   `list_sessions` 的 `last` 必须来自这里；排序侧 `last` 优先、`mtime` 只在缺失时兜底。
# ---------------------------------------------------------------------------

def test_touch_interaction_is_memory_only_until_flush(root):
    """记录互动**不写盘**（存档语义：不存档就退游戏 = 这段没发生）。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    svc.touch_interaction("姜萌", 1000)
    assert svc.list_interactions() == {"姜萌": 1000}
    assert not os.path.exists(os.path.join(svc.root, "interactions.json")), \
        "touch_interaction 不该自己写盘（落盘只由 save_happened → flush_interactions 驱动）"

    assert svc.flush_interactions() == 1
    with open(os.path.join(svc.root, "interactions.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data["interactions"] == {"姜萌": 1000}


def test_flush_interactions_noop_when_clean(root):
    """没有变更 → 不碰盘（返回 0）。存档很频繁，每次重写文件毫无必要。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    assert svc.flush_interactions() == 0
    assert not os.path.exists(os.path.join(svc.root, "interactions.json"))


def test_touch_interaction_is_monotonic(root):
    """乱序/时钟回拨不许把顺序弄反：只接受**更晚**的时间戳。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    svc.touch_interaction("姜萌", 2000)
    svc.touch_interaction("姜萌", 1500)          # 更早 → 忽略
    assert svc.list_interactions() == {"姜萌": 2000}
    svc.touch_interaction("姜萌", 2000)          # 相等 → 忽略（不产生无意义写盘）
    assert svc.list_interactions() == {"姜萌": 2000}
    svc.touch_interaction("姜萌", 2001)
    assert svc.list_interactions() == {"姜萌": 2001}


def test_touch_interaction_rejects_blank_and_bad_ts(root):
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    svc.touch_interaction("", 1000)
    svc.touch_interaction("   ", 1000)
    svc.touch_interaction("姜萌", 0)
    svc.touch_interaction("姜萌", -5)
    assert svc.list_interactions() == {}


def test_list_sessions_prefers_last_over_mtime(root):
    """★核心契约★ `last`（真的说话）必须独立于 `mtime`（上次存档）回传。

    这条钉住的是**数据**：C# 侧据此把 mtime 降级为兜底。数据这里错了，
    上层再怎么写都救不回来（旧实现只有 mtime，就是这次报障的形态）。
    """
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    os.makedirs(svc.root, exist_ok=True)
    for name in ("姜萌", "江宇文"):
        with open(os.path.join(svc.root, urllib.parse.quote(name, safe="") + ".jsonl"),
                  "w", encoding="utf-8") as f:
            f.write("{}\n")
    # 模拟"存档把两个文件刷成同一秒"
    same = 1792514472
    for name in ("姜萌", "江宇文"):
        os.utime(os.path.join(svc.root, urllib.parse.quote(name, safe="") + ".jsonl"),
                 (same, same))

    svc.touch_interaction("江宇文", same + 500)
    out = {s["npc_id"]: s for s in svc.list_sessions()}
    assert out["江宇文"]["mtime"] == same
    assert out["江宇文"]["last"] == same + 500, "last 没从 interactions 读出来"
    assert out["姜萌"]["last"] == 0, "没有互动记录的 NPC，last 必须是 0（不是 mtime，也不是 -1）"
    # ★ 按 last 排序：江宇文在前；姜萌 last=0（上层回落 mtime，两人 mtime 相等 → 顺序无所谓）
    assert out["江宇文"]["last"] > out["姜萌"]["last"]


def test_touch_interaction_invalidates_sessions_cache(root):
    """记了新的互动就必须丢会话索引缓存 —— 否则 5s 内打开通讯录读到旧 `last`。

    时间窗很短，但恰好覆盖「横幅弹出 → 玩家立刻点开通讯录」这条最自然的路径。
    """
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    os.makedirs(svc.root, exist_ok=True)
    with open(os.path.join(svc.root, urllib.parse.quote("姜萌", safe="") + ".jsonl"),
              "w", encoding="utf-8") as f:
        f.write("{}\n")

    assert {s["npc_id"]: s["last"] for s in svc.list_sessions()}["姜萌"] == 0
    svc.touch_interaction("姜萌", 12345)
    assert {s["npc_id"]: s["last"] for s in svc.list_sessions()}["姜萌"] == 12345, \
        "touch_interaction 之后 list_sessions 还返回旧缓存（5s TTL 没被作废）"


def test_interactions_persist_across_restart(root):
    """★核心契约★ 落盘之后**换一个 service 实例**还能读到（= 重启游戏后排序仍正确）。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    svc.touch_interaction("姜萌", 777)
    assert svc.flush_interactions() == 1

    svc2 = ContactsService(root)          # 模拟 Python 重启
    svc2.set_world("W_A", "唐炎")
    assert svc2.list_interactions() == {"姜萌": 777}, "重启后互动时间戳丢了"


def test_interactions_isolated_per_world(root):
    """换存档必须隔离：同名 NPC 在另一个存档里是**另一个人**。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    svc.touch_interaction("姜萌", 111)
    svc.flush_interactions()

    svc.set_world("W_B", "缪嘉歆")
    assert svc.list_interactions() == {}, "换存档后带上了上一个存档的互动时间戳"
    svc.touch_interaction("姜萌", 999)
    svc.flush_interactions()

    svc.set_world("W_A", "唐炎")
    assert svc.list_interactions() == {"姜萌": 111}, "换回原存档，原记录该还在"


def test_interactions_skipped_when_world_unknown(root):
    """生产形态未定档 → **不记也不写**（那段时间不知道属于哪个存档，写了就是串档）。"""
    svc = ContactsService(root, require_world=True)
    svc.touch_interaction("姜萌", 1000)
    assert svc.list_interactions() == {}
    assert svc.flush_interactions() == 0
    assert not os.path.exists(os.path.join(root, "interactions.json"))


def test_interactions_bad_json_falls_back_to_empty(root):
    """坏 JSON → 空表兜底（fail-closed），不抛；此时上层回落 mtime 排序。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    os.makedirs(svc.root, exist_ok=True)
    with open(os.path.join(svc.root, "interactions.json"), "w", encoding="utf-8") as f:
        f.write("{ 这不是 json")
    assert svc.list_interactions() == {}
    # 坏文件之后仍然能正常工作（不能因为一次坏读就永久失效）
    svc.touch_interaction("姜萌", 1234)
    assert svc.flush_interactions() == 1
    assert svc.list_interactions() == {"姜萌": 1234}


def test_interactions_tolerates_foreign_shapes(root):
    """异形数据逐条跳过：非 dict / 值为字符串 / 空名 / 非正数都不该让整份失效。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    os.makedirs(svc.root, exist_ok=True)
    with open(os.path.join(svc.root, "interactions.json"), "w", encoding="utf-8") as f:
        json.dump({"interactions": {"甲": "1234", "": 5, "乙": -1, "丙": 60}}, f)
    assert svc.list_interactions() == {"甲": 1234, "丙": 60}


# ---------------------------------------------------------------------------
# 关系层快照（known.json，10-27）
#
# 它存在的唯一理由：关系层是**每次渲染现取**游戏数据的，死者 `GetUnit(id)` 恒 null
# ⇒ 人一死就从通讯录里静默消失。死后也反推不出来（`ClearDieUnit` 清了关系簿、
# 死者快照的 `GetRelation(玩家)` 返回 None，真机确认）——"他是我的道侣"只有活着时能抓。
# 所以这份是**游戏会抹掉的那部分事实的唯一副本**，写丢一次就永久没了。
# ---------------------------------------------------------------------------

def test_known_sync_upserts_and_reports_changes(root):
    """新增 → 无变化（不写盘） → 关系/ID 变了才算 updated。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    assert svc.list_known() == []

    r1 = svc.sync_known([
        {"npc_id": "郦安", "unit_id": "FJLlLl", "relation": "道侣"},
        {"npc_id": "姜萌", "unit_id": "Xs6JDI"},
    ])
    assert r1 == {"added": 2, "updated": 0, "total": 2, "changed": True}

    # 原样再推一次：一个字节都不写（面板每次打开都会推，写盘要能省下来）
    r2 = svc.sync_known([{"npc_id": "郦安", "unit_id": "FJLlLl", "relation": "道侣"}])
    assert r2["changed"] is False and r2["added"] == 0 and r2["updated"] == 0

    # 关系变了（师傅）→ updated；**不删**没出现在本批里的姜萌（关系层这回合没他 ≠ 他不曾来过）
    r3 = svc.sync_known([{"npc_id": "郦安", "unit_id": "FJLlLl", "relation": "师傅"}])
    assert r3["updated"] == 1 and r3["total"] == 2
    assert {k["npc_id"] for k in svc.list_known()} == {"郦安", "姜萌"}
    assert [k["relation"] for k in svc.list_known() if k["npc_id"] == "郦安"] == ["师傅"]


def test_known_sync_never_deletes_on_shrinking_batch(root):
    """关系层取数失败会返回**空表** —— 若整份覆盖，快照就被清空、死者永久丢失。
    所以语义必须是"并集"。这条是本次改动的头号回归点。"""
    svc = _svc(root)
    svc.sync_known([{"npc_id": "管星贤", "unit_id": "AAA111"}])
    assert svc.sync_known([])["total"] == 1
    assert [k["npc_id"] for k in svc.list_known()] == ["管星贤"]


def test_known_sync_skips_bad_entries_without_losing_the_batch(root):
    """一条坏数据只丢那一条 —— 不许把整份关系层一起丢掉（丢的是死者，补不回来）。"""
    svc = _svc(root)
    r = svc.sync_known([
        {"npc_id": "郦安", "unit_id": "FJLlLl"},
        {"npc_id": "", "unit_id": "X"},                    # 空名
        "不是对象",
        {"npc_id": "超长" * 40, "unit_id": "Y"},            # 名字超长
        {"npc_id": "姜萌", "unit_id": "Z" * 100},           # unitID 超长 → 只丢这一条
        {"npc_id": "公冶抚", "unit_id": "BBB222"},
    ])
    assert [k["npc_id"] for k in svc.list_known()] == ["郦安", "公冶抚"]
    assert r["added"] == 2


def test_known_omits_absent_keys(root):
    """没有 unit_id / relation → 记录里**根本没有这两个键**（与 contacts.json 同款字面形状）。"""
    svc = _svc(root)
    svc.sync_known([{"npc_id": "林婉清"}])
    rec = svc.list_known()[0]
    assert "unit_id" not in rec and "relation" not in rec
    raw = json.load(open(os.path.join(svc.root, "known.json"), encoding="utf-8"))
    assert "unit_id" not in raw["known"][0]


def test_known_bad_json_falls_back_to_empty(root):
    """坏 JSON / 异形 → 空表兜底（fail-closed），下一次 sync 能正常重建。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    os.makedirs(svc.root, exist_ok=True)
    with open(os.path.join(svc.root, "known.json"), "w", encoding="utf-8") as f:
        f.write("{ 这不是 json")
    assert svc.list_known() == []
    svc.sync_known([{"npc_id": "姜萌", "unit_id": "Xs6JDI"}])
    assert [k["npc_id"] for k in svc.list_known()] == ["姜萌"]

    with open(os.path.join(svc.root, "known.json"), "w", encoding="utf-8") as f:
        json.dump({"known": "不是数组"}, f)               # 异形
    assert svc.list_known() == []


def test_known_is_world_scoped(root):
    """换存档必须整表隔离 —— unitID 跨档会整体失效，串档就是静默错人。"""
    svc = _svc(root)
    svc.set_world("W_A", "唐炎")
    svc.sync_known([{"npc_id": "甲", "unit_id": "A1"}])
    svc.set_world("W_B", "缪嘉歆")
    assert svc.list_known() == []
    svc.sync_known([{"npc_id": "乙", "unit_id": "B1"}])
    assert [k["npc_id"] for k in svc.list_known()] == ["乙"]
    svc.set_world("W_A", "唐炎")
    assert [k["npc_id"] for k in svc.list_known()] == ["甲"]


def test_known_blocked_until_world_known(root):
    """生产形态未定档：读返回空（拒绝回答）、写抛错（不许落进扁平目录）。"""
    svc = ContactsService(root, require_world=True)
    assert svc.list_known() == []
    with pytest.raises(ValueError):
        svc.sync_known([{"npc_id": "甲", "unit_id": "A1"}])
    svc.set_world("W_A", "唐炎")
    svc.sync_known([{"npc_id": "甲", "unit_id": "A1"}])
    assert [k["npc_id"] for k in svc.list_known()] == ["甲"]


def test_known_atomic_write_leaves_no_tmp(root):
    svc = _svc(root)
    svc.sync_known([{"npc_id": "甲", "unit_id": "A1"}])
    assert not os.path.exists(os.path.join(svc.root, "known.json.tmp"))
