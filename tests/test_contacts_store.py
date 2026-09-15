"""contacts_store 测试：contacts.json 读写往返 / 幂等 add·remove / 坏数据兜底 / 原子写 / 会话索引"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse

import pytest

from agent_loop.contacts_store import ContactsService


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
