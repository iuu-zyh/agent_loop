"""contacts_store — 通讯录（手动好友）唯一写侧 + 会话索引

定位（对标 config_store 的哲学）：contacts.json 的唯一读写口。
- 手动加好友的持久层：npc_id + added_at + source 一条记录，幂等 add/remove；
- 会话索引：扫**当前存档目录**的 *.jsonl 只取文件名与 mtime（绝不读内容），
  供通讯录「最近」Tab 按最近互动排序；带短 TTL 缓存防反复开面板重复扫盘。

**存档命名空间（09-12 用户拍板）**：实际读写目录 = `storage_root/worlds/<world_id>/`
（world_id = 玩家 unitID，由 C# 在 load_happened/save_happened 携带，见
`persistence.world_root`）。未定存档（测试 / chat_cli / 尚未进世界）退回扁平
`storage_root`。**换存档不再串号**——旧实现在全局 `storage_root` 放 contacts.json，
换存档后能看到上一个存档加的好友（用户实机发现）。

读侧（ChatHub）只经本模块；坏 JSON / 缺文件 → 空表兜底不抛；
写盘 tmp + os.replace 原子替换，写失败抛给 RPC 层转 ok:false。
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from .persistence import sanitize_world_id, world_root

CONTACTS_FILE = "contacts.json"
_SESSIONS_TTL = 5.0  # 会话索引缓存秒数（开面板级频率足够新鲜）


class ContactsService:
    """通讯录后端服务（contacts.json 读写 + 会话索引），由装配层注入 ChatHub。

    目录随 `set_world()` 切换（存档命名空间）；切换会清会话索引缓存。
    """

    def __init__(self, storage_root: str):
        self.base_root = Path(os.path.expanduser(storage_root))
        self.world_id: Optional[str] = None
        self.player_name: Optional[str] = None
        self._sessions_cache: Optional[List[Dict[str, Any]]] = None
        self._sessions_ts = 0.0
        self._apply_root()

    # ---------- 存档命名空间 ----------

    def _apply_root(self) -> None:
        self._root = Path(world_root(str(self.base_root), self.world_id))
        self._path = self._root / CONTACTS_FILE

    def set_world(self, world_id: object, player_name: object = None) -> bool:
        """切换存档命名空间（返回是否变化）。换存档后名单/最近索引都只看本存档。"""
        wid = sanitize_world_id(world_id)
        pn = str(player_name or "").strip() or None
        changed = (wid != (self.world_id or ""))
        self.world_id = wid or None
        if pn:
            self.player_name = pn
        if changed:
            self._sessions_cache = None       # 索引缓存跨存档残留 = 通讯录"最近"串号
            self._sessions_ts = 0.0
            self._apply_root()
        return changed

    @property
    def root(self) -> str:
        """当前读写目录（诊断/测试用）。"""
        return str(self._root)

    # ---------- 手动好友（contacts.json） ----------

    def list_contacts(self) -> List[Dict[str, Any]]:
        """手动好友全量（保持添加顺序）。"""
        data = self._load()
        return list(data.get("contacts", []))

    def add_contact(self, npc_id: str) -> Dict[str, Any]:
        """加好友（幂等）：已存在不重写不改 added_at。"""
        npc_id = _clean(npc_id)
        data = self._load()
        contacts = data.get("contacts", [])
        if any(c.get("npc_id") == npc_id for c in contacts):
            return {"added": False, "count": len(contacts)}
        contacts.append({"npc_id": npc_id, "added_at": int(time.time()), "source": "manual"})
        data["contacts"] = contacts
        self._save(data)
        return {"added": True, "count": len(contacts)}

    def remove_contact(self, npc_id: str) -> Dict[str, Any]:
        """移除好友（幂等）：不存在静默成功（移除语义以终态为准）。"""
        npc_id = _clean(npc_id)
        data = self._load()
        contacts = data.get("contacts", [])
        kept = [c for c in contacts if c.get("npc_id") != npc_id]
        if len(kept) == len(contacts):
            return {"removed": False, "count": len(kept)}
        data["contacts"] = kept
        self._save(data)
        return {"removed": True, "count": len(kept)}

    # ---------- 会话索引（「最近」排序源） ----------

    def list_sessions(self) -> List[Dict[str, Any]]:
        """当前存档目录下全部 *.jsonl 的 [{npc_id, mtime}]。

        只 scandir + stat，绝不读文件内容（会话文件可能很大）；
        TTL 内返回缓存。contacts.json 是 .json 后缀，天然不混入。
        """
        now = time.monotonic()
        if self._sessions_cache is not None and now - self._sessions_ts < _SESSIONS_TTL:
            return self._sessions_cache
        out: List[Dict[str, Any]] = []
        try:
            with os.scandir(self._root) as it:
                for entry in it:
                    try:
                        if not entry.is_file() or not entry.name.endswith(".jsonl"):
                            continue
                        mtime = int(entry.stat().st_mtime)
                    except OSError:
                        continue
                    npc_id = urllib.parse.unquote(entry.name[: -len(".jsonl")])
                    out.append({"npc_id": npc_id, "mtime": mtime})
        except OSError:
            out = []  # 目录不存在/不可读 → 空索引，通讯录降级为纯本地时间戳
        self._sessions_cache = out
        self._sessions_ts = now
        return out

    # ---------- 内部 ----------

    def _load(self) -> Dict[str, Any]:
        """读 contacts.json → dict；缺文件/坏 JSON/异形 → 空表兜底（fail-closed 读侧）。"""
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("contacts"), list):
                return data
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return {"contacts": []}

    def _save(self, data: Dict[str, Any]) -> None:
        """tmp + os.replace 原子写；写失败照抛（RPC 层转 ok:false，调用方有回音）。"""
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


def _clean(npc_id: str) -> str:
    npc_id = str(npc_id or "").strip()
    if not npc_id:
        raise ValueError("npc_id 不能为空")
    if len(npc_id) > 64:
        raise ValueError("npc_id 过长")
    return npc_id
