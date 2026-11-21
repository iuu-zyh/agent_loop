"""Persistence — jsonl 落盘 + 崩后补 interrupted"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Iterable

from .. import log_setup
from ..session.session import Session

log = log_setup.get_logger(__name__)

# 存档命名空间子目录名（storage_root/worlds/<world_id>/…）
WORLDS_DIR = "worlds"


def sanitize_world_id(world_id: object) -> str:
    """存档 id 安全化（来自游戏的 unitID，实为短 ASCII，但不做假设）：
    百分号编码 + 截断 64 字符；空/None → 空串（= 不回退命名空间）。"""
    raw = str(world_id or "").strip()
    if not raw:
        return ""
    return urllib.parse.quote(raw, safe="")[:64]


def world_root(base_root: str, world_id: object = None) -> str:
    """**存档命名空间根目录**（09-12：通讯录/对话历史/最近索引按存档隔离）。

    `world_id`（= 玩家 unitID，由 C# 在 `load_happened`/`save_happened` 携带）为空时
    退回 `base_root` 扁平布局——测试、`chat_cli` 独立实例、尚未进世界的场景都走这条。
    换存档时 id 必变（实机：`FJLlLl` vs `BOlQu6`）→ 天然不会读到上一个存档的名单与历史。
    """
    base = os.path.expanduser(base_root)
    wid = sanitize_world_id(world_id)
    return os.path.join(base, WORLDS_DIR, wid) if wid else base


def session_path_for(npc_id: str, root: str = "~/.sessions") -> Path:
    # 中文 id 安全化
    safe = urllib.parse.quote(npc_id, safe="")
    # `~` 展开：默认值现在是 "~/.sessions"（跨平台），不展开会建出一个名叫 "~" 的目录。
    # world_root() 早已展开，此处补齐——两条入口对同一种写法给同一个答案。
    return Path(os.path.expanduser(str(root))) / f"{safe}.jsonl"


def save_session(session: Session, root: str = "~/.sessions",
                 append: bool = True) -> Dict[str, Any]:
    """把会话固化到 `<npc>.jsonl`。**默认增量追加**，必要时自动退回全量重写。

    ## 为什么从"全量重写"改成"增量追加"（11-15，用户："这样时间消耗太大了"）

    账本**只追加不改写**（删除 = 追一条 `history/delete` 注记、压缩 = 追一条 `replace`，
    既有事件一个字段都不动 —— 有测试钉住这条不变式）。既然内存里的 log 只往后长，
    那么"把整份 log 重新序列化一遍"就是纯浪费：

        轮数    文件大小    全量重写     纯追加
        200     0.63M      4.7ms      0.08ms
        1000    3.16M     26.4ms      0.19ms
        4000   12.67M     91.3ms      0.13ms

    追加是 **O(新增)**（常数 ~0.2ms），重写是 **O(全部)**。而存档是**同步**发生在
    游戏主线程那次 `save_happened` 里的，20 个 NPC × 1000 轮 ≈ 半秒 ⇒ 玩家体感
    "一按存档就卡一下"，且越玩越久越卡。

    ## 安全前提（三条，缺一不可 —— 都在下面逐条校验）

      ① **账本只追加**：已有事件的 `data` 在落盘后不再变。压缩/pruner/删除全是追加新事件，
         所以磁盘上的旧行与内存里的旧事件永远一致。
      ② **磁盘行数必须与内存 log 对得上**：内存里第 k 条事件 = 磁盘上第 k 条。
         对不上（文件被外部改过、上次写了一半、读档补过 interrupted close）⇒ **退回全量重写**。
         这条是防止"追加错位"，而追加错位的后果是**账本整体错乱且不报错**。
      ③ **namespace 由调用方把关**：`flush_all` 里那道"出处对账"必须仍然生效 ——
         增量追加比全量重写**更危险**：全量重写是把目标文件整份换掉（错得明显），
         而追加是往别人档案尾部续写（错得隐蔽）。所以 `flush_all` 的 namespace 检查
         一个字都不能松（见 `AgentLoop.flush_all`）。

    ## 返回

    `{"mode": "append"|"full"|"skip", "written": n, "reason": str}`
    （`mode` 用于测试与排障；正常调用方不用管。）
    """
    path = session_path_for(session.id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_events = session.log
    if not log_events:
        # 空账本：保留旧行为（空账不落盘由调用方决定；这里什么都不写，也不建文件）
        return {"mode": "skip", "written": 0, "reason": "empty"}

    if append:
        info = _try_append(path, session, log_events)
        if info is not None:
            return info
    # 全量重写（首落 / 行数对不上 / 外部改动）
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"session": {"id": session.id, "header": session.header}},
                           ensure_ascii=False) + "\n")
        for ev in log_events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    # 原子替换：崩在写的过程中不会留下半份账本（旧实现直接 open("w")，
    # 中途崩溃 = 整份历史变半截，那比"没存上"更糟）。
    os.replace(tmp, path)
    return {"mode": "full", "written": len(log_events), "reason": "rewrite"}


def _count_jsonl_events(path: Path) -> int:
    """数文件里有多少条**事件**行（首行 header 不算）。

    ★ 实现说明（11-15）★ 用**二进制分块数换行**，而不是逐行读文本再 strip：
      · 逐行读要先 UTF-8 解码整份文件（3MB 约 1.9ms），而这只是"数几个换行"；
      · 二进制分块 count 约 1.5ms，且**不产生 Python 字符串对象**（大文件下内存更稳）。
    首行 header 固定占一个换行 ⇒ 结果减 1。

    这一步仍是 O(文件大小) —— 但它比**全量重写**（逐条 json.dumps + 写）便宜一个数量级
    （3MB：数行 ~1.5ms vs 重写 ~26ms）。要彻底 O(1) 得在磁盘上记住进度，
    那会引入"记录与实际文件不一致"的新失败面，收益不抵风险。
    """
    try:
        nl = 0
        with open(path, "rb") as f:
            while True:
                b = f.read(1 << 19)
                if not b:
                    break
                nl += b.count(b"\n")
        return max(0, nl - 1)          # 减去首行 header
    except OSError:
        return -1


def _try_append(path: Path, session: Session, log_events: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """尝试只写新增的那些事件。返回 None = 不能追加（调用方退回全量重写）。

    ## 判据（任何一条不满足就放弃追加）

      · 文件必须已经存在（首落没有"旧内容"，但也可以直接全量写 —— 走全量更简单）；
      · 磁盘上的事件行数必须 **≤** 内存 log 长度，且**尾部完整**
        （最后一行必须以换行结尾：上次写一半会留下半行 JSON，追加会把两行粘在一起）；
      · 用行数当进度游标：磁盘有 k 条 ⇒ 内存里前 k 条已经在盘上 ⇒ 只写第 k..n 条。

    ## 为什么行数够用（不需要逐行比对内容）

    因为账本**只追加不改写**（前提 ①）。同样前提也让"磁盘 k 条 = 内存前 k 条"这条
    对应关系稳定成立 —— 除非有人在外部改过文件，而那种情况下**行数也对不上**
    （改少了 → 行数 < k 但内容已不同；改多了 → 走负数分支失败）。

    仍然做两道**廉价校验**（都只看最后一条，成本 O(1)）：
      · 磁盘最后一条事件的 `seq` 必须等于 `k-1`（seq 是账本内连续整数，对齐才说明
        这份文件是"同一条账本的前缀"）；
      · 磁盘最后一条事件的**内容指纹**必须与内存里第 `k-1` 条一致。
        只对 seq 是不够的 —— 文件被换成**另一份**账本时 seq 同样连续，
        而那种错位会让新旧两段历史拼在一起，**不报错、只是内容错乱**（实测过）。
    """
    if not path.exists():
        return None
    size = path.stat().st_size
    if size == 0:
        return None
    k = _count_jsonl_events(path)
    if k < 0 or k > len(log_events):
        return None
    if k == 0:
        return None                      # 只有 header 行：结构不标准，交给全量重写
    # 尾部必须完整（最后一行以换行结尾，否则是上次写了一半）
    try:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                return None
    except OSError:
        return None
    # 取磁盘最后一条事件（从文件尾部读一块，不整份加载）
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", "ignore")
        last_line = [ln for ln in tail.splitlines() if ln.strip()][-1]
        disk_last = json.loads(last_line)
    except Exception:
        return None
    if int(disk_last.get("seq", -1)) != k - 1:
        return None
    # ★ 纵深防御：磁盘比内存长 ⇒ 文件不是内存账本的前缀 ★
    # 上面的 `k > len(log_events)` 已经拦过一次，这里再显式判一次是**刻意的**：
    # 不能靠下一行 `log_events[k-1]` 抛 IndexError 被外层 `except` 吞掉来"意外"兜住 ——
    # 那条路径的语义是"出错了"，而这里其实是**一个明确的判断**，该留下能看懂的日志。
    if k > len(log_events):
        log.warning("增量落盘放弃：磁盘有 %d 条事件、内存只有 %d 条"
                    "（文件比内存长：旧进程没退干净？）→ 退回全量重写", k, len(log_events))
        return None
    # ★ 内容指纹：防"文件被换成另一份 seq 同样连续的账本"（静默错位）
    try:
        mem_last = log_events[k - 1]
        if _event_fingerprint(disk_last) != _event_fingerprint(mem_last):
            log.warning("增量落盘放弃：磁盘末条与内存第 %d 条内容不一致（文件被外部改过？）"
                        "→ 退回全量重写", k - 1)
            return None
    except Exception:
        return None
    todo = log_events[k:]
    if not todo:
        return {"mode": "append", "written": 0, "reason": "up-to-date"}
    try:
        with open(path, "a", encoding="utf-8") as f:
            for ev in todo:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return {"mode": "append", "written": len(todo), "reason": "incremental"}


def _event_fingerprint(ev: Dict[str, Any]) -> str:
    """一条事件的**内容指纹**（不含 seq/time —— 那两个在重放/补写时可能被适度调整）。

    用途只有一个：增量落盘前确认"磁盘上那条"与"内存里那条"确实是同一件事。
    用 md5 而不是逐字段比：事件可能带几千字的图像 ref / 工具结果，逐字段比要深比较。
    """
    payload = {
        "type": ev.get("type"),
        "data": ev.get("data"),
        "surfaceOp": ev.get("surfaceOp"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def load_session(npc_id: str, root: str = "~/.sessions") -> Session | None:
    path = session_path_for(npc_id, root)
    if not path.exists():
        return None
    events: List[Dict[str, Any]] = []
    header = {"id": npc_id}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "session" in obj:
                header = obj["session"].get("header", header)
                continue
            events.append(obj)
    # 崩后补：若尾部开 turn 未收口，补 turn/end:interrupted
    events = _apply_interrupted_closers(events)
    sess = Session(id=npc_id, header=header, events=events)
    return sess


# ══════════════════════════════════════════════════════════════════════
# Room（群聊流水）落盘 —— 与 session 同构：首行 header、其后逐行 JSON。
#
# 为什么房间**独立于**任何单个 session 落盘：一个还没人说过话的房间可能
# 一个 session 都还没建；而「玩家把这几个人叫到一起」这件事本身要能被列出来
# （通讯录里的群列表），否则关窗重开后无从知道 g3 存在。
#
# 目录布局（沿用现有存档命名空间 `world_root`）：
#     <storage_root>/worlds/<world_id>/
#         ├── <npc_id>.jsonl          ← 已有的：每个 NPC 的 session（含 room/cursor 注记）
#         └── rooms/
#             ├── index.json          房间名录：room_id → {title, members, created}
#             └── <room_id>.jsonl     发言流水（首行 header，其后每行一条 Utterance）
#
# **没有** `<npc_id>.jsonl` 在每个房间目录下 —— 那是旧设计的「每房间每成员一份会话」，
# 已被「一个 NPC = 一个 session」取代（见 docs/archive/2026-09-17-群聊编排/设计定稿.md 七-②）。
# ══════════════════════════════════════════════════════════════════════

ROOMS_DIR = "rooms"
ROOMS_INDEX = "index.json"


def rooms_root_for(base_root: str = "~/.sessions", world_id: object = None) -> str:
    """群聊流水根目录 = `<当前存档根>/rooms`（未定存档时 = `<storage_root>/rooms`）。"""
    return os.path.join(world_root(base_root, world_id), ROOMS_DIR)


def room_path_for(room_id: str, root: str) -> Path:
    """群流水文件路径。room_id 是 Python 生成的 `g1` 这类短 ASCII，但仍统一百分号编码
    （与 `session_path_for` 同规约：不对输入做假设，也不允许它跳出目录）。"""
    safe = urllib.parse.quote(str(room_id or ""), safe="")
    return Path(os.path.expanduser(str(root))) / f"{safe}.jsonl"


def save_room(room, root: str):
    """全量重写一个群的流水（天然增量语义：磁盘追平内存）。"""
    from ..room.room import Room  # 局部 import：避免持久化层在模块加载期就依赖 room

    if not isinstance(room, Room):
        raise TypeError("save_room 需要 Room 实例")
    path = room_path_for(room.id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"room": room.to_meta()}, ensure_ascii=False) + "\n")
        for u in room.log:
            f.write(json.dumps(u.to_dict(), ensure_ascii=False) + "\n")


def load_room(room_id: str, root: str):
    """读回一个群（含流水）。文件不存在返回 None。"""
    from ..room.room import Room, Utterance

    path = room_path_for(room_id, root)
    if not path.exists():
        return None
    meta: Dict[str, Any] = {"room_id": room_id}
    log: List[Any] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "room" in obj:
                meta = obj["room"] or meta
                continue
            log.append(Utterance.from_dict(obj))
    return Room.from_meta(meta, log=log)


def save_rooms_index(rooms: List[Any], root: str, retired: Iterable[str]):
    """写房间名录（`index.json`）。C# 列「有哪些群」只读这一个文件，不必扫全部 jsonl。

    `retired` = **已解散**的 room_id 高水位（防 id 重用，见 `room.new_room_id`）。

    ★ 刻意**不给默认值**：漏传的后果是下一次存档把退休名单抹掉，于是 `new_room_id`
      把刚解散的 id 又发出去，而老成员的 `room/cursor` 还指着它 ⇒ 那些人在新群里
      **收不到任何消息且不报错**。它在删群这条链上是**正确性的一部分**，不是可选元数据
      —— 所以宁可在调用点强迫想一次（全工程只有两个调用点）。
    """
    path = Path(os.path.expanduser(str(root))) / ROOMS_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"rooms": [r.to_meta() for r in rooms],
            "retired": sorted({str(x) for x in (retired or ())})}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))


def load_rooms_index(root: str) -> List[Dict[str, Any]]:
    """读房间名录。文件不存在/损坏 → 空表（列表是尽力而为的展示件，不该拖垮启动）。"""
    path = Path(os.path.expanduser(str(root))) / ROOMS_INDEX
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list((json.load(f) or {}).get("rooms") or [])
    except Exception:
        return []


def load_retired_rooms(root: str) -> List[str]:
    """读「已解散」的 room_id 高水位（与名录同在一个 `index.json`）。

    缺失/损坏 → 空表。**这份数据丢了不会立刻出错**，只会在某次删群之后
    让 id 有机会被重用（那时才炸，且是静默的）—— 所以它按"尽力而为"读，
    但与名录**必须同一次写落盘**（否则两者会不一致）。
    """
    path = Path(os.path.expanduser(str(root))) / ROOMS_INDEX
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = (json.load(f) or {}).get("retired") or []
        return [str(x) for x in raw]
    except Exception:
        return []


def delete_room_file(room_id: str, root: str) -> bool:
    """删掉某群的流水文件（**不可恢复**）。返回是否真的删了（文件不存在 → False，不算错误）。

    ★ 调用顺序有讲究：**先**把不含这个群的 index 落盘（提交点，同一次写里也落了
    退休 id），**再**删文件。反过来的话，一旦在两次写之间崩溃，index 里还留着这个群
    而它的流水已经没了 ⇒ 读档得到一个 `last_seq=N 但历史一片空白` 的**幽灵群**。
    按正确顺序崩，最坏只留下一个**孤儿 jsonl** —— 而 `load_rooms` 只读 index，
    孤儿文件对系统完全不可见，无害。

    （顺序由 `AgentLoop.retire_room` 保证；这里只管删。）
    """
    path = room_path_for(room_id, root)
    try:
        if not path.exists():
            return False
        path.unlink()
        return True
    except Exception:
        log.warning("删群流水失败（孤儿文件，对系统不可见）：%s", path)
        return False


def _apply_interrupted_closers(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """简化版 interruptedTurnClosers：若最后有 turn/start 无 turn/end，补 turn/end:interrupted"""
    if not events:
        return events
    has_open = False
    last_turn = None
    for ev in reversed(events):
        if ev.get("type") == "turn/end":
            has_open = False
            break
        if ev.get("type") == "turn/start":
            has_open = True
            last_turn = ev["data"].get("turn")
            break
    if has_open and last_turn is not None:
        # 补 step/end 若有开 step
        has_open_step = False
        last_step = None
        for ev in reversed(events):
            if ev.get("type") == "step/end":
                has_open_step = False
                break
            if ev.get("type") == "step/start" and ev["data"].get("turn") == last_turn:
                has_open_step = True
                last_step = ev["data"].get("step")
                break
        new_events = list(events)
        seq = len(new_events)
        now = new_events[-1].get("time", 0) + 1 if new_events else 0
        if has_open_step and last_step is not None:
            new_events.append({"seq": seq, "time": now, "type": "step/end", "data": {"turn": last_turn, "step": last_step}})
            seq += 1
            now += 1
        new_events.append({"seq": seq, "time": now, "type": "turn/end", "data": {"turn": last_turn, "reason": {"kind": "interrupted"}}})
        return new_events
    return events
