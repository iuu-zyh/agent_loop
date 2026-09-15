# -*- coding: utf-8 -*-
"""删除提示词文件（09-12）：
- prompt_files.is_deletable / delete_prompt：白名单（能新建的才能删）、系统件 fail-closed、
  路径穿越防护、删除真实落盘；
- list_prompts 下发 deletable 标记；
- ChatHub delete_prompt RPC 路由（含旧 mock 服务不支持的明确报错）。
"""

import pytest

from agent_loop import prompt_files
from agent_loop.prompt_files import delete_prompt, is_deletable, list_prompts


def _make_root(tmp_path):
    (tmp_path / "sections").mkdir()
    (tmp_path / "personas").mkdir()
    (tmp_path / "traits").mkdir()
    (tmp_path / "compaction").mkdir()
    (tmp_path / "sections" / "world_basis.txt").write_text("旧世界观", encoding="utf-8")
    (tmp_path / "personas" / "default.txt").write_text("默认人设{npc_name}", encoding="utf-8")
    (tmp_path / "personas" / "_suffix.txt").write_text("尾部追加", encoding="utf-8")
    (tmp_path / "compaction" / "default.md").write_text("全局压缩指令", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# 可删判定 + 删除
# ---------------------------------------------------------------------------

def test_is_deletable_rules(tmp_path):
    """能新建的才能删：sections/default/_suffix 拒，新增件许。"""
    root = _make_root(tmp_path)
    assert not is_deletable("sections/world_basis.txt", root=root)
    assert not is_deletable("personas/default.txt", root=root)
    assert not is_deletable("personas/_suffix.txt", root=root)
    assert not is_deletable("compaction/default.md", root=root)
    assert not is_deletable("personas/不存在.txt", root=root)      # 不存在=不可删
    assert not is_deletable("", root=root)
    assert not is_deletable("../config.json", root=root)           # 穿越一律 False 不抛
    # 新增件
    (root / "personas" / "林婉清.txt").write_text("x", encoding="utf-8")
    (root / "traits" / "林婉清.json").write_text("{}", encoding="utf-8")
    (root / "compaction" / "林婉清.md").write_text("x", encoding="utf-8")
    assert is_deletable("personas/林婉清.txt", root=root)
    assert is_deletable("traits/林婉清.json", root=root)
    assert is_deletable("compaction/林婉清.md", root=root)
    print("✓ is_deletable 白名单规则")


def test_delete_prompt_whitelist(tmp_path):
    """系统件删除 fail-closed 拒绝且文件原样；新增件删除真实落盘。"""
    root = _make_root(tmp_path)
    for sys_rel in ("sections/world_basis.txt", "personas/default.txt",
                    "personas/_suffix.txt", "compaction/default.md"):
        with pytest.raises(ValueError):
            delete_prompt(sys_rel, root=root)
        assert (root / sys_rel).is_file(), f"系统件必须原样保留: {sys_rel}"
    (root / "personas" / "林婉清.txt").write_text("人设", encoding="utf-8")
    r = delete_prompt("personas/林婉清.txt", root=root)
    assert r == {"path": "personas/林婉清.txt", "deleted": True}
    assert not (root / "personas" / "林婉清.txt").exists()
    with pytest.raises(FileNotFoundError):
        delete_prompt("personas/林婉清.txt", root=root)            # 再删=不存在
    print("✓ delete_prompt：系统件拒 + 新增件落盘删除")


def test_list_prompts_deletable_flag(tmp_path):
    """list_prompts 每项带 deletable，与 _allow_create 同规约。"""
    root = _make_root(tmp_path)
    (root / "personas" / "林婉清.txt").write_text("x", encoding="utf-8")
    items = {i["path"]: i["deletable"] for i in list_prompts(root=root)}
    assert items["sections/world_basis.txt"] is False
    assert items["personas/default.txt"] is False
    assert items["personas/_suffix.txt"] is False
    assert items["compaction/default.md"] is False
    assert items["personas/林婉清.txt"] is True
    print("✓ list_prompts.deletable 标记")


# ---------------------------------------------------------------------------
# ChatHub delete_prompt RPC 路由
# ---------------------------------------------------------------------------

class _RealishPromptService:
    """薄封装真实 prompt_files（生产装配即此形态）。"""

    def __init__(self, root):
        self._root = root

    def delete_prompt(self, rel):
        return delete_prompt(rel, root=self._root)


class _OldMockPromptService:
    """旧版 mock：没有 delete_prompt（验证 fail-closed 而非静默成功）。"""

    def list_prompts(self):
        return []


def _hub(prompt_service=None):
    from agent_loop.ws_channel import ChatHub
    return ChatHub(None, None, prompt_service=prompt_service)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_hub_delete_prompt_routing(tmp_path):
    root = _make_root(tmp_path)
    (root / "personas" / "张三.txt").write_text("x", encoding="utf-8")
    hub = _hub(_RealishPromptService(root))
    out = _run(hub.handle_request("delete_prompt", {"path": "personas/张三.txt"}))
    assert out["deleted"] is True and out["effective"] == "hot"
    assert not (root / "personas" / "张三.txt").exists()
    with pytest.raises(ValueError):   # 系统件 → ValueError → 传输层 ok:false
        _run(hub.handle_request("delete_prompt", {"path": "personas/default.txt"}))
    print("✓ ChatHub delete_prompt 路由（hot 标记 + 系统件拒绝）")


def test_hub_delete_prompt_old_service_fails_closed():
    """旧 mock 无 delete_prompt → 明确报错，绝不静默成功。"""
    hub = _hub(_OldMockPromptService())
    with pytest.raises(ValueError):
        _run(hub.handle_request("delete_prompt", {"path": "personas/张三.txt"}))
    print("✓ 旧服务缺 delete_prompt 时 fail-closed")
