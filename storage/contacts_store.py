"""contacts_store — 通讯录（手动好友）唯一写侧 + 会话索引

定位（对标 config_store 的哲学）：contacts.json 的唯一读写口。
- 手动加好友的持久层：npc_id + added_at + source（+ 可选 unit_id）一条记录，幂等 add/remove；
- 互动时间戳的持久层：npc_id → 「最后一次真正说话」的 unix 秒（见下面「最近」那段）；
- 会话索引：扫**当前存档目录**的 *.jsonl 只取文件名与 mtime（绝不读内容），
  供通讯录「最近」Tab 按最近互动排序；带短 TTL 缓存防反复开面板重复扫盘。

**「最近」排序的两个时间源，以及为什么必须分开（10-21 用户报障）**：
用户现象 ——「NPC 主动传音后点开通讯录，他从『最近』里的位置没变」。
根因不是"没记主动传音"（`npc_reply` 一到就记了），而是**旧实现取 `max(文件 mtime, 内存时间戳)`**，
而文件 mtime 是「上次**存档**的时刻」而不是「上次说话的时刻」：
`flush_all` 在存档时把**所有** agent 一次性重写，12 个文件在几十毫秒内写完
（真机实测：姜萌 .953 / 江宇文 .956，同一秒），截断成整秒后**全相等** ⇒ 排序退化成随机。
存档只发生在少数时刻，所以 mtime 通常比"最后一次对话"更旧 —— 看着就像"顺序从没变过"。

⇒ 现在的判据（**不要再改回 max**）：`last`（本文件的 `interactions`，真实互动时刻）
优先；**只有它缺失时**才回落到文件 mtime（给"没有新记录的老存档"一个可用排序）。
写成 max 的话，存档那一刻 mtime 立刻又盖过互动时间 ⇒ 原 bug 原样复发。

**身份来源（09-16 追加）**：`unit_id` 是这个名字的**精确身份**，由 C# 在**唯一一个必然握着
`WorldUnitBase` 的时刻**（NPC 面板「加好友」按钮的回调，那里 `panel.unit` 就是本人）读出并上行。
为什么必须持久化它：Python 全链路 `npc_id` 是**中文名**，而这游戏「重名是常态不是例外」，
C# 侧只能靠 `UnitLookup` 全图扫描猜一个 —— 猜错**不报错、不缺字段**，只是把另一个人的档案
端给模型（`UnitLookup` 文件头记的那次真机事故）。有了 unit_id，消费方就能走"先精确、后按名"。
**写侧铁律**：`unit_id` 只许来自"真实握在手里的对象"，**绝不许把按名解析的结果回写** ——
那是把猜测固化到磁盘、跨会话生效，比不记更糟（`UnitLookup.Pin` 注释同款理由）。

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

from .. import log_setup
from .persistence import sanitize_world_id, world_root

log = log_setup.get_logger(__name__)

CONTACTS_FILE = "contacts.json"
HIDDEN_FILE = "hidden.json"
# 互动时间戳（npc_id → 最后一次真正说话的 unix 秒）。与 contacts.json 分开的理由：
# 那份的读侧契约是"contacts 列表"（好友数/添加顺序），掺进第二类数据会让既有语义含糊
# —— 同 hidden.json 的分家理由。本文件**不含好友关系**，只是时间索引。
INTERACTIONS_FILE = "interactions.json"
# 关系层快照（「这个人曾经在我的通讯录里」）。与 contacts.json 分开的理由同 hidden.json：
# 那份的读侧契约是"手动好友列表"，而这份记的是**游戏关系数据曾经有过谁** ——
# 两者寿命语义不同（好友是玩家意图、关系是游戏事实），混在一个原子写里互相牵制。
#
# ★ 它要解决的唯一问题 ★：关系层是**每次渲染现取**游戏数据的（`CollectKnownUnits`），
#   而死者 `GetUnit(id)` 恒 null ⇒ 三条采集路全部 `if (u == null) return` ⇒ 人一死
#   就从通讯录里**静默消失**，连"已不在世间"都看不到，更打不开旧对话。
#   死后也反推不出他来过：`ClearDieUnit` 把关系簿清了，`DieData.relationData`
#   的 `GetRelation(玩家)` 返回 None（真机确认）——"他是我的道侣"这件事**只有活着时能抓**。
#   所以只能活着时留一份，死后按 unit_id 判定其是否已故、并把行补回列表。
#
# 写入时机：面板打开（`BuildRelations` 之后，内容没变不写）+ 游戏存档（与 flush_all 同刻）。
# 只增不删 —— 解不开的旧条目**没有副作用**：活人永远由关系层现取（解析得到 ⇒ 不补），
# 快照只在"这个 id 现在解析不到"时才被消费，所以断交/改嫁的活人不会借它复活。
KNOWN_FILE = "known.json"
_SESSIONS_TTL = 5.0  # 会话索引缓存秒数（开面板级频率足够新鲜）


class ContactsService:
    """通讯录后端服务（contacts.json 读写 + 会话索引），由装配层注入 ChatHub。

    目录随 `set_world()` 切换（存档命名空间）；切换会清会话索引缓存。

    `require_world`（09-18，与 `AgentLoop.require_world` 同源同义）：生产形态下未定档时
    **拒绝读写扁平根** —— 那里的文件不属于任何存档，读进来是"别人的好友"，写进去则是
    跨档串号（Python 重启窗口里最先出问题的就是它：加完好友切档就看不见了）。
    测试 / `chat_cli` / 手跑 server.py 保持 False（扁平布局是它们的正常语义）。
    """

    def __init__(self, storage_root: str, require_world: bool = False):
        self.base_root = Path(os.path.expanduser(storage_root))
        self.world_id: Optional[str] = None
        self.player_name: Optional[str] = None
        self.require_world = bool(require_world)
        # 「未定档被闸住」只提示一次（`list_contacts` 是面板轮询级频率，逐次 WARNING 会刷屏）
        self._blocked_warned = False
        self._sessions_cache: Optional[List[Dict[str, Any]]] = None
        self._sessions_ts = 0.0
        # 互动时间戳（npc_id → unix 秒）：**内存为真、落盘跟随存档**。
        # 懒加载：`_apply_root()` 之后首次用到时才读盘（换档会清掉重读）。
        self._interactions: Dict[str, int] = {}
        self._interactions_loaded = False
        self._interactions_dirty = False
        self._apply_root()

    # ---------- 未定档闸门 ----------

    def _blocked(self) -> bool:
        """生产形态 + 未定档 ⇒ 扁平根不可用（读写皆然）。"""
        if not (self.require_world and not self.world_id):
            return False
        if not self._blocked_warned:
            self._blocked_warned = True
            log.warning("未取得存档身份（Python 刚重启？）→ 通讯录**不读写扁平目录**"
                        "（那里的名单不属于任何存档）；C# 补发身份后自动恢复")
        return True

    @property
    def blocked(self) -> bool:
        """本服务此刻是否被"未定档"闸住（`list_*` 返回空是**拒绝回答**而不是"没有"）。

        ★ 为什么必须把它**显式暴露出去**（09-20 实机事故，勿删）★
        `list_contacts()` 被闸住时返回 `[]`，而"这个存档确实没有好友"也返回 `[]`
        —— 同一个值表达两件**完全不同**的事，调用方无从分辨。C# 侧把空回包当真相
        （`ContactStore.SetManualNames` 是**整体替换**语义：先 `Clear()` 再填），
        于是"我拒绝回答"被当成"你没有好友" ⇒ **把手上正确的名单清空**。
        表现（用户实证）：某次进游戏后通讯录**只剩关系层那一个人**（姜萌），
        手动加的好友全部消失 —— 而 `contacts.json` 里那几个人好好的。
        故 `ws_channel` 的两个 list RPC 必须把它一并回传，C# 据此决定"要不要信这个空表"。
        """
        return self._blocked()

    def _guard_write(self, op: str) -> None:
        """写侧闸门：拒绝把好友写进扁平目录。

        抛 `ValueError` 而不是静默吞：RPC 层会把它转成 `ok:false` + 文案（调用方看得见）。
        代价是这一刻点「加好友」不生效（C# 的按钮状态等 `list_contacts` 回来校准），
        但那是**可恢复**的（下一次存档/读档之后重试即可），而写进扁平目录是**静默丢**。
        """
        if self._blocked():
            raise ValueError(f"{op}失败：当前未取得存档身份（Python 刚重启？）"
                             "——请先在游戏内存一次档或读一次档，再试")

    # ---------- 存档命名空间 ----------

    def _apply_root(self) -> None:
        self._root = Path(world_root(str(self.base_root), self.world_id))
        self._path = self._root / CONTACTS_FILE
        self._interactions_path = self._root / INTERACTIONS_FILE
        self._known_path = self._root / KNOWN_FILE

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
            self._blocked_warned = False      # 换档后允许再提示一次（新档若也取不到身份）
            # 互动时间戳同理：上一档的"最后一次说话"在新档里毫无意义（同名不同人）。
            # 只丢内存、**不写盘** —— 旧档那份文件还在旧目录里，换回去照样读得到。
            self._interactions = {}
            self._interactions_loaded = False
            self._interactions_dirty = False
            self._apply_root()
        return changed

    @property
    def root(self) -> str:
        """当前读写目录（诊断/测试用）。"""
        return str(self._root)

    # ---------- 手动好友（contacts.json） ----------

    def list_contacts(self) -> List[Dict[str, Any]]:
        """手动好友全量（保持添加顺序）。未定档（生产形态）→ 空表，不读扁平目录。"""
        if self._blocked():
            return []
        data = self._load()
        return list(data.get("contacts", []))

    def add_contact(self, npc_id: str, unit_id: Optional[str] = None) -> Dict[str, Any]:
        """加好友（幂等）。

        `unit_id` **可选**：C# 在 NPC 面板点「加好友」那一刻手里握着 `WorldUnitBase`，
        从它身上读出来的 `unitID` 就是这个名字的**精确身份**（见文件头「身份来源」）。
        老 DLL 不传 / 老记录没有 → 一律当"没提供"，行为与本次改动前逐字段一致。

        幂等语义（已存在时）：
          · `added_at` **不动**、`added` 仍返回 `False`（那个键回答的是"是不是新建的"）；
          · 但**带了的 unit_id 与库中不同 → 更新它**。这同时覆盖两种情形：
            老记录补空、以及存档变动后纠正失效的旧 id。
          · 不带 unit_id → 完全不写盘（等价于改动前的"已存在即 return"）。
        """
        npc_id = _clean(npc_id)
        uid = _clean_unit_id(unit_id)
        self._guard_write("加好友")
        data = self._load()
        contacts = data.get("contacts", [])
        for c in contacts:
            if c.get("npc_id") != npc_id:
                continue
            if uid and c.get("unit_id") != uid:
                c["unit_id"] = uid
                self._save(data)
                return {"added": False, "count": len(contacts), "unit_id_updated": True}
            return {"added": False, "count": len(contacts)}
        rec = {"npc_id": npc_id, "added_at": int(time.time()), "source": "manual"}
        if uid:
            # 没有就**不写这个键** —— 保持老记录的字面形状，读侧据此判断"没有精确身份"
            rec["unit_id"] = uid
        contacts.append(rec)
        data["contacts"] = contacts
        self._save(data)
        return {"added": True, "count": len(contacts)}

    def remove_contact(self, npc_id: str) -> Dict[str, Any]:
        """移除好友（幂等）：不存在静默成功（移除语义以终态为准）。"""
        npc_id = _clean(npc_id)
        self._guard_write("移除好友")
        data = self._load()
        contacts = data.get("contacts", [])
        kept = [c for c in contacts if c.get("npc_id") != npc_id]
        if len(kept) == len(contacts):
            return {"removed": False, "count": len(kept)}
        data["contacts"] = kept
        self._save(data)
        return {"removed": True, "count": len(kept)}

    # ---------- 隐藏名单（hidden.json；行内 ✕ 的"关系层隐藏"侧） ----------
    #
    # 语义（09-26 用户拍板）：通讯录行内 ✕ = 尽量把这个人从列表移除 ——
    #   好友行 → 删好友；若关系层仍有他（或本来就是纯关系行）→ 记进隐藏名单，
    #   列表不再渲染。**关系数据一点不动**：道侣仍是道侣、剧情/好感照常。
    # 重现规则：该 NPC **再次主动传音**时由 C# 调 `unhide_contact` —— 唯一出口。
    # 好友优先：C# 渲染过滤时好友行不受隐藏影响（重新加为好友 = 立即显示）。
    #
    # 为什么独立文件而不是塞进 contacts.json：那份的读侧契约是"contacts 列表"，
    # 掺进第二类名单会让"好友数/顺序"这类既有语义含糊；隐藏是另一件事，独立文件最干净。

    def list_hidden(self) -> List[Dict[str, Any]]:
        """隐藏名单全量（保持隐藏顺序）。未定档（生产形态）→ 空表，不读扁平目录。"""
        if self._blocked():
            return []
        data = self._load_hidden()
        return list(data.get("hidden", []))

    def hide_contact(self, npc_id: str, unit_id: Optional[str] = None) -> Dict[str, Any]:
        """加入隐藏名单（幂等）。`unit_id` 语义与 `add_contact` 逐条一致：
        可选、空=没提供、带了的与库中不同则更新（覆盖老记录补空/纠正失效旧 id）。"""
        npc_id = _clean(npc_id)
        uid = _clean_unit_id(unit_id)
        self._guard_write("隐藏联系人")
        data = self._load_hidden()
        items = data.get("hidden", [])
        for it in items:
            if it.get("npc_id") != npc_id:
                continue
            if uid and it.get("unit_id") != uid:
                it["unit_id"] = uid
                self._save_hidden(data)
                return {"hidden": False, "count": len(items), "unit_id_updated": True}
            return {"hidden": False, "count": len(items)}
        rec = {"npc_id": npc_id, "hidden_at": int(time.time()), "source": "manual"}
        if uid:
            rec["unit_id"] = uid
        items.append(rec)
        data["hidden"] = items
        self._save_hidden(data)
        return {"hidden": True, "count": len(items)}

    def unhide_contact(self, npc_id: str) -> Dict[str, Any]:
        """移出隐藏名单（幂等）：不存在静默成功（终态为准）。"""
        npc_id = _clean(npc_id)
        self._guard_write("取消隐藏")
        data = self._load_hidden()
        items = data.get("hidden", [])
        kept = [x for x in items if x.get("npc_id") != npc_id]
        if len(kept) == len(items):
            return {"unhidden": False, "count": len(kept)}
        data["hidden"] = kept
        self._save_hidden(data)
        return {"unhidden": True, "count": len(kept)}

    # ---------- 关系层快照（known.json；死者不消失的**唯一**依据） ----------
    #
    # 语义：一条 = 「C# 在建通讯录时**手里握着真身**，因此确知这个 npc_id ↔ unit_id，
    #       而且他当时处在关系层（十容器／好友簿／仇人簿／关系记录簿）」。`relation`
    #       是那一刻的游戏关系中文（道侣／师傅／徒弟…），只在活着时抓得到 ——
    #       死后 `DieData.relationData.GetRelation(玩家)` 返回 None（真机确认）。
    #
    # ★ 它不是"缓存"，是**游戏已经抹掉的那部分事实的唯一副本** ★
    #   活人永远由关系层现取（解析得到 ⇒ 不消费快照）；只有 `GetUnit(id)` 解析不到的
    #   条目才被消费 —— 于是"断交/改嫁/离场的活人"不会借它复活（那类人解析得到，
    #   只是不在关系层了，照旧消失）。
    #
    # 只增不删：解不开的旧条目零副作用，而删错一次就永久丢了"他来过"这件事。

    def _load_known(self) -> Dict[str, Any]:
        """读 known.json → dict；缺文件/坏 JSON/异形 → 空表兜底（fail-closed 读侧）。"""
        try:
            raw = self._known_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("known"), list):
                return data
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return {"known": []}

    def _save_known(self, data: Dict[str, Any]) -> None:
        """tmp + os.replace 原子写（同 contacts.json）；写失败照抛，RPC 层转 ok:false。"""
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._known_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._known_path)

    def list_known(self) -> List[Dict[str, Any]]:
        """快照全量。未定档（生产形态）→ 空表，不读扁平目录（同 list_contacts）。"""
        if self._blocked():
            return []
        return list(self._load_known().get("known", []))

    def sync_known(self, items: Any) -> Dict[str, Any]:
        """把 C# 此刻看到的关系层**并进**快照（幂等 upsert，字段没变则整份不写盘）。

        `items` = `[{npc_id, unit_id?, relation?}]`，是关系层的**全量**（不是增量）。
        语义：
          · 新增 → 记 `first_seen` = now；
          · 已存在 → 只更新**真的变了**的 `unit_id` / `relation`，并刷新 `last_seen`；
          · **不删**任何条目（关系层这回合没有他，≠ 他不曾在我的名单里 ——
            那正是死者要用的那份记录；活人靠"解析得到"这一条自然不消费快照）。

        为什么不校验"完整性"就整份覆盖：关系层会因**暂时取不到数**（未进世界、桥失败）
        而返回空表 —— 整份覆盖会把快照清空，等于把死者永久删掉。改成并集后，
        "取数失败"最坏只是这次没新增，既有事实一条不丢。

        返回 `{added, updated, total, changed}`；`changed=False` 表示一个字节都没写。
        """
        if not isinstance(items, list):
            raise ValueError("sync_known_contacts 的 items 必须是数组")
        self._guard_write("同步通讯录快照")
        now = int(time.time())
        data = self._load_known()
        known = data.get("known", [])
        by_name: Dict[str, Dict[str, Any]] = {}
        for rec in known:
            if isinstance(rec, dict) and rec.get("npc_id"):
                by_name[str(rec["npc_id"])] = rec

        added = updated = 0
        bad = 0
        for it in items:
            if not isinstance(it, dict):
                bad += 1
                continue
            name = str(it.get("npc_id") or "").strip()
            if not name or len(name) > 64:
                # 单条不合格只跳过这一条 —— 批量同步不该因为一个坏名字丢掉整份关系层
                bad += 1
                continue
            uid = None
            try:
                uid = _clean_unit_id(it.get("unit_id"))
            except ValueError:
                # 超长 unitID：**这一条**跳过（不许让一条坏数据把整份关系层丢掉 ——
                # 那等于把已经死掉的人从快照里抹掉，而他们再也补不回来了）
                bad += 1
                continue
            rel = str(it.get("relation") or "").strip()[:32]
            rec = by_name.get(name)
            if rec is None:
                rec = {"npc_id": name, "first_seen": now, "last_seen": now, "seen": 1}
                if uid:
                    rec["unit_id"] = uid
                if rel:
                    rec["relation"] = rel
                known.append(rec)
                by_name[name] = rec
                added += 1
                continue
            dirty = False
            if uid and rec.get("unit_id") != uid:
                rec["unit_id"] = uid          # 补空 / 纠正失效旧 id（同 add_contact 口径）
                dirty = True
            if rel and rec.get("relation") != rel:
                rec["relation"] = rel
                dirty = True
            rec["last_seen"] = now
            rec["seen"] = int(rec.get("seen") or 0) + 1
            if dirty:
                updated += 1

        changed = bool(added or updated)
        if changed:
            data["known"] = known
            self._save_known(data)
        if bad:
            log.warning("通讯录快照同步：跳过 %d 条不合格条目（缺 npc_id / 超长 / 非对象）", bad)
        return {"added": added, "updated": updated, "total": len(known), "changed": changed}

    # ---------- 互动时间戳（「最近」排序的**权威**源；interactions.json） ----------
    #
    # 为什么需要它（10-21 用户报障，勿再简化成"用文件 mtime 就够了"）：
    #   文件 mtime 是「上次**存档**」的时刻，不是「上次说话」的时刻 —— `flush_all` 一次重写
    #   全部 agent，同批文件落在同一秒（真机：姜萌 .953 / 江宇文 .956）⇒ 排序全相等。
    #   而且存档稀疏：今天说的话在存档之前一个字都没落盘，mtime 还停在**上一次存档**，
    #   于是"主动传音之后他在『最近』里位置没变"。
    #
    # 判据（写死在 `list_sessions` 里，这里的注释只是解释）：`last` 优先，缺失才回落 mtime。
    # **不要写成 max(last, mtime)** —— 存档那一刻 mtime 立刻又盖过 last，原 bug 原样复发。

    def touch_interaction(self, npc_id: str, ts: Optional[float] = None) -> None:
        """记录"这个 NPC 刚刚真的说了一句"（`npc_reply` 到达时调）。

        **不写盘**：内存为真，落盘跟随存档（`flush_interactions`，由 `save_happened` 驱动）。
        与 `AgentLoop.dispose/flush_all` 同一套存档语义 —— 不存档就退游戏 = 这段没发生，
        通讯录的"最近"也不该记住它（否则重开游戏后排序会指向一段不存在的对话）。

        幂等/单调：只接受比已记录值**更新**的时间戳（乱序回包、时钟回拨都不会把顺序弄反）。
        未定档（生产形态）时**静默丢弃** —— 那段时间属于"不知道是哪个存档"，写了就是串档。
        """
        name = str(npc_id or "").strip()
        if not name:
            return
        if self.require_world and not self.world_id:
            return                                  # 未定档：宁可这次不记，也不串档
        sec = int(ts if ts is not None else time.time())
        if sec <= 0:
            return
        self._ensure_interactions()
        prev = self._interactions.get(name, 0)
        if sec <= prev:
            return
        self._interactions[name] = sec
        self._interactions_dirty = True
        # 缓存里存着上一版的 `last` ⇒ 必须丢掉，否则紧接着打开的通讯录读到旧值。
        # 放在这里而不是让调用方自己记得调：忘一次就是"刚收到传音、位置却没动"
        # （= 用户这次报的那个现象，只是时间窗从"存档级"缩到 5s）。
        self.invalidate_sessions_cache()

    def flush_interactions(self) -> int:
        """把内存里的互动时间戳落盘（`save_happened` 调）。返回写入条数；无变更 → 0（不碰盘）。

        **必须与 `flush_all` 同一次事件里落**：会话账本与"最近"排序是同一件事的两面，
        只落一边就会出现「账本有了这句话、通讯录却说他很久没说话」（反之亦然）。
        """
        if not self._interactions_dirty:
            return 0
        if self._blocked():
            return 0                                # 未定档：不写扁平目录（同 `_guard_write`）
        self._ensure_interactions()
        data = {"version": 1,
                "interactions": {k: int(v) for k, v in self._interactions.items() if v > 0}}
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            tmp = self._interactions_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                           encoding="utf-8")
            os.replace(tmp, self._interactions_path)
        except Exception:
            # 写失败不抛给存档流程（存档本身必须成功），但**留日志** ——
            # 否则"最近排序不对"又要从头查一遍。
            log.warning("互动时间戳落盘失败（%s）：下次存档会再试一次",
                        self._interactions_path, exc_info=True)
            return 0
        self._interactions_dirty = False
        return len(data["interactions"])

    def list_interactions(self) -> Dict[str, int]:
        """当前内存里的互动时间戳副本（诊断/测试用；不用它驱动渲染，渲染走 list_sessions）。"""
        self._ensure_interactions()
        return dict(self._interactions)

    def _ensure_interactions(self) -> None:
        """懒加载 interactions.json（换档后 `set_world` 已把 loaded 置假，这里自动重读）。"""
        if self._interactions_loaded:
            return
        self._interactions_loaded = True
        self._interactions = {}
        self._interactions_dirty = False
        try:
            raw = self._interactions_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except FileNotFoundError:
            return                                  # 老存档/首次运行：空表（回落 mtime）
        except Exception:
            log.warning("互动时间戳读取失败（%s）→ 本次回落会话文件 mtime 排序",
                        self._interactions_path, exc_info=True)
            return
        items = data.get("interactions") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            return
        for k, v in items.items():
            try:
                sec = int(v)
            except (TypeError, ValueError):
                continue
            if k and sec > 0:
                self._interactions[str(k)] = sec

    # ---------- 会话索引（「最近」排序源） ----------

    def list_sessions(self) -> List[Dict[str, Any]]:
        """当前存档目录下全部 *.jsonl 的 [{npc_id, mtime, last}]。

        只 scandir + stat，绝不读文件内容（会话文件可能很大）；
        TTL 内返回缓存。contacts.json / interactions.json 是 `.json` 后缀，天然不混入。
        未定档（生产形态）→ 空索引：不扫扁平目录（那里的 jsonl 不属于本存档）。

        **`last` 与 `mtime` 的语义分工（10-21，勿再合并成一个字段）**：
          - `mtime` = 文件最后**落盘**时刻 = 上次**存档**的时刻（一次存档全批同时刻，无区分度）；
          - `last`   = 这个 NPC **最后一次真的说话**的时刻（来自 `interactions.json`，
                       缺失时为 0）。
        **排序必须用 `last` 优先、`mtime` 只在 `last == 0` 时兜底**：C# 侧（`ContactStore.LastInteract`）
        严格按这个方向取值。写成 max 的话，存档那一刻 mtime 又盖过 last ⇒
        「主动传音后位置不变」原样复发（这就是这次报障的根因）。
        `last` 可以为 0（这个 NPC 只有账本、从没在新版本里说过话）—— 那不是错误。
        """
        if self._blocked():
            return []
        now = time.monotonic()
        if self._sessions_cache is not None and now - self._sessions_ts < _SESSIONS_TTL:
            return self._sessions_cache
        self._ensure_interactions()
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
                    out.append({"npc_id": npc_id, "mtime": mtime,
                                "last": int(self._interactions.get(npc_id, 0))})
        except OSError:
            out = []  # 目录不存在/不可读 → 空索引，通讯录降级为纯本地时间戳
        self._sessions_cache = out
        self._sessions_ts = now
        return out

    def invalidate_sessions_cache(self) -> None:
        """丢掉会话索引缓存（互动时间戳**变了**之后必须调，否则 5s 内的回包仍是旧 `last`）。

        为什么要它：`_SESSIONS_TTL` 缓存的是**整份列表**（含 `last`），而 `touch_interaction`
        每来一句 `npc_reply` 都会改 `last`。不丢弃的话，紧接着打开的通讯录拿到的是
        "这一句之前"的快照 —— 玩家刚收到传音就去点「传」，看到的位置还是旧的。
        （TTL 是 5s，窗口很短但**恰好覆盖**"横幅弹出 → 玩家立刻点开通讯录"这条最自然的路径。）
        """
        self._sessions_cache = None
        self._sessions_ts = 0.0

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

    # ---------- 隐藏名单内部（与 contacts.json 同款：原子写 / 坏文件空表兜底） ----------

    @property
    def _hidden_path(self) -> Path:
        """随 `set_world()` 切换（与 contacts.json 同目录同存档隔离）。"""
        return self._root / HIDDEN_FILE

    def _load_hidden(self) -> Dict[str, Any]:
        """读 hidden.json → dict；缺文件/坏 JSON/异形 → 空表兜底（fail-closed 读侧）。"""
        try:
            raw = self._hidden_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("hidden"), list):
                return data
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return {"hidden": []}

    def _save_hidden(self, data: Dict[str, Any]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._hidden_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._hidden_path)


def _clean(npc_id: str) -> str:
    npc_id = str(npc_id or "").strip()
    if not npc_id:
        raise ValueError("npc_id 不能为空")
    if len(npc_id) > 64:
        raise ValueError("npc_id 过长")
    return npc_id


def _clean_unit_id(unit_id: object) -> Optional[str]:
    """unitID 清洗。**与 `_clean` 刻意不对称**：

    - `_clean`（npc_id）空 → **报错**（那是调用方的 bug，名字是必填的）；
    - 这里空 / None → **返回 None**（"没提供"是正常情形：老 DLL 不传、老记录没有它，
      都不该让加好友整个失败）。这条不对称是刻意的，不是疏漏。

    超长 → 报错：unitID 是会被**持久化并当真**的值，异常长度不该落盘。
    格式不设约束（只限长度）—— unitID 的格式是游戏定的，不是我们的，卡死只会在
    游戏改格式时误伤（真机见过的形态是 6 位混合字母数字，如 `BOlQu6`/`Xs6JDI`）。
    """
    s = str(unit_id or "").strip()
    if not s:
        return None
    if len(s) > 64:
        raise ValueError("unit_id 过长")
    return s
