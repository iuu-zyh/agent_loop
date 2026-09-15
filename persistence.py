"""Persistence — jsonl 落盘 + 崩后补 interrupted"""

from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any

from .session import Session

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


def save_session(session: Session, root: str = "~/.sessions"):
    path = session_path_for(session.id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 首行 header
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"session": {"id": session.id, "header": session.header}}, ensure_ascii=False) + "\n")
        for ev in session.log:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


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
