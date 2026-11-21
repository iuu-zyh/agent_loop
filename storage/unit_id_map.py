"""unit_id_map — 世界级「名字 → unitID」映射表（Python 侧，**落盘**）

## 为什么必须落盘（09-17 用户拍板）

模型的历史里留着**唯一化显示名**（`益婉容(化神殿·结晶后期)`）—— 那是它与 harness
之间的信封，而钥匙（本表）只在内存的话：重进游戏后模型照样会念出那个显示名，
我们却**再也解不开**它（只能把带括号的整串当人名去查 ⇒ 查不到 ⇒ 退回裸名按名解析）。
那正是显示名要避免的事，而且**完全静默**。所以本表与会话账本**同寿命**：
跟着游戏存档固化（`save_happened → AgentLoop.flush_all`），不存档就一起丢。

## 落点与隔离

`<storage_root>/worlds/<world_id>/unit_ids.json` —— 与 `contacts.json` 同目录。
**目录本身就是存档隔离**（unitID 换档即失效），因此不需要像 C# 那样再存一个
`world` 字段做交叉校验（那份文件只有一份，必须自己记档）。

★ **与 C# 的 `<ModRoot>/unit_ids.json` 是两份东西，不要合并**：
  · C# 那份 = 「我曾**手握真身**」的**裸名**登记（给跨会话的按名解析用精确值），C# 独家写；
  · 本表    = 「**模型见过什么**」：裸名 + 唯一化显示名 + 已知重名的裸名，Python 独家写。
  所有者、键空间、失效条件都不同；合成一份必然出现"谁覆盖谁"的静默错误。

## 形状与容错

    {"version": 1, "units": {"名字或显示名": "unitID"}, "ambiguous": ["裸名", ...]}

文件损坏 / 键缺失 / 半截 → **当空表**（本表是加速器，不是正确性依赖），绝不抛。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Set

from .persistence import sanitize_world_id, world_root

UNIT_ID_MAP_FILE = "unit_ids.json"
_VERSION = 1


class UnitIdMapService:
    """`{名字/显示名 → unitID}` + 已知重名集合；随存档命名空间切换、随存档事件落盘。

    由装配层持有一份（`AgentLoop.identity`），**所有 agent 共享同一个对象**：
    知识是世界级的（李四是谁跟哪个 NPC 在说话无关），每人一份既浪费又会漂移。
    """

    def __init__(self, storage_root: str):
        self.base_root = Path(os.path.expanduser(storage_root))
        self.world_id: Optional[str] = None
        self._units: Dict[str, str] = {}
        self._ambiguous: Set[str] = set()
        self._loaded = False
        self._dirty = False
        self._apply_root()

    # ---------- 存档命名空间 ----------

    def _apply_root(self) -> None:
        self._root = Path(world_root(str(self.base_root), self.world_id))
        self._path = self._root / UNIT_ID_MAP_FILE

    def set_world(self, world_id: object) -> bool:
        """切换存档命名空间（返回是否变化）。**换档即丢弃内存表并等待重载** ——
        留着旧档的映射，最坏情形不是"查不到"（那会自愈），而是那个 id 在新档里
        恰好**是另一个人**（unitID 换档即失效）。"""
        wid = sanitize_world_id(world_id)
        changed = (wid != (self.world_id or ""))
        self.world_id = wid or None
        if changed:
            self._units = {}
            self._ambiguous = set()
            self._loaded = False
            self._dirty = False
            self._apply_root()
        return changed

    @property
    def root(self) -> str:
        """当前读写目录（诊断/测试用）。"""
        return str(self._root)

    @property
    def path(self) -> str:
        return str(self._path)

    # ---------- 读 ----------

    @property
    def units(self) -> Dict[str, str]:
        self.load()
        return self._units

    @property
    def ambiguous(self) -> Set[str]:
        self.load()
        return self._ambiguous

    def load(self) -> None:
        """惰性加载（幂等）。文件不存在/损坏/半截 → 空表，绝不抛。"""
        if self._loaded:
            return
        self._loaded = True
        try:
            if not self._path.exists():
                return
            raw = json.loads(self._path.read_text(encoding="utf-8") or "{}")
            if not isinstance(raw, dict):
                return
            units = raw.get("units")
            if isinstance(units, dict):
                for k, v in units.items():
                    k, v = str(k or "").strip(), str(v or "").strip()
                    if k and v:
                        self._units[k] = v
            amb = raw.get("ambiguous")
            if isinstance(amb, list):
                for x in amb:
                    s = str(x or "").strip()
                    if s:
                        self._ambiguous.add(s)
        except Exception:
            self._units = {}
            self._ambiguous = set()

    # ---------- 写 ----------

    def remember(self, key: object, unit_id: object) -> bool:
        """登记一条 `键 → unitID`（幂等）。空键 / 空 id → 忽略（"不知道"不许变成"空"）。"""
        k, uid = str(key or "").strip(), str(unit_id or "").strip()
        if not k or not uid:
            return False
        if self._units.get(k) == uid:
            return False
        self._units[k] = uid
        self._dirty = True
        return True

    def mark_ambiguous(self, name: object) -> bool:
        """把裸名记为"已知重名"（此后不再登记它的裸名键，改走唯一化显示名）。"""
        s = str(name or "").strip()
        if not s or s in self._ambiguous:
            return False
        self._ambiguous.add(s)
        self._dirty = True
        return True

    def discard(self, key: object) -> bool:
        """撤回一个键（发现它指向两个人时用 —— 登记谁都是错的）。"""
        k = str(key or "").strip()
        if not k or k not in self._units:
            return False
        self._units.pop(k, None)
        self._dirty = True
        return True

    def save(self) -> int:
        """原子落盘（**跟随游戏存档**：`AgentLoop.flush_all` 调）。返回写入条数。

        空表且文件不存在 ⇒ **不创建文件**（与 C# 那边"别凭空造一个空表"同款理由）；
        文件已存在则照写（表可能被缩小过，不写就让旧内容复活）。
        写失败**照抛**：调用方（存档钩子）自己决定记日志还是吞 —— 与 `contacts_store` 一致。
        """
        self.load()
        if not self._units and not self._ambiguous and not self._path.exists():
            return 0
        self._root.mkdir(parents=True, exist_ok=True)
        data = {
            "version": _VERSION,
            "units": dict(self._units),
            "ambiguous": sorted(self._ambiguous),
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        self._dirty = False
        return len(self._units)

    # ---------- 诊断 ----------

    def report(self) -> str:
        self.load()
        return (f"人名映射 {len(self._units)} 条（已知重名 {len(self._ambiguous)}）"
                f"｜world={self.world_id or '(未定存档·扁平)'}｜{self._path}")


def collect_from(agents) -> Dict[str, str]:
    """诊断/测试辅助：把若干 agent 的当前映射合成一张表（只读，不落盘）。"""
    out: Dict[str, str] = {}
    for a in agents or ():
        try:
            out.update(getattr(a, "unit_id_map", {}) or {})
        except Exception:
            continue
    return out


def keys_of(agents) -> List[str]:
    """诊断用：所有 agent 见过的键（排序）。"""
    return sorted(collect_from(agents).keys())
