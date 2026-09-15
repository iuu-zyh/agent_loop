"""Inbox — 双筐排队 + spliced 账单持久化"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class Inbox:
    """两只筐：next-turn（等下回合） next-step（插队到下步）"""

    def __init__(self, session):
        self.session = session
        self.state: Dict[str, List[Dict[str, Any]]] = {"next-turn": [], "next-step": []}
        # 重放已有 spliced 事件还原筐
        for ev in session.log:
            if ev.get("type") == "agent/inbox/spliced":
                self._apply(ev["data"])

    # ---------- 对外 ----------
    @property
    def next_turn(self) -> List[Dict[str, Any]]:
        return self.state["next-turn"]

    @property
    def next_step(self) -> List[Dict[str, Any]]:
        return self.state["next-step"]

    @property
    def has_pending(self) -> bool:
        return bool(self.next_turn or self.next_step)

    def clear(self):
        # 先清 next-step 再清 next-turn（与 DSH 一致）
        self.splice("next-step", 0, len(self.next_step), [])
        self.splice("next-turn", 0, len(self.next_turn), [])

    def claim(self, target: str, turn: int) -> List[Dict[str, Any]]:
        """领信：先吃 next-step 全量，再按需吃 next-turn 一条"""
        claimed: List[Dict[str, Any]] = []
        # 吃 next-step 全量（不发 discarded 通知的静默版，简化直接 splice）
        if self.next_step:
            removed = self._splice_internal("next-step", 0, len(self.next_step), [], record=True)
            claimed.extend(removed)
        if target == "next-turn" and self.next_turn:
            removed = self._splice_internal("next-turn", 0, 1, [], record=True)
            claimed.extend(removed)
        return claimed

    def splice(self, target: str, start: int, delete_count: int, inserted: List[Dict[str, Any]]):
        self._splice_internal(target, start, delete_count, inserted, record=True)

    def prepend(self, target: str, msg: Dict[str, Any]):
        self.splice(target, 0, 0, [msg])

    def remove(self, msg_id: str) -> bool:
        loc = self._locate(msg_id)
        if loc is None:
            return False
        target, idx = loc
        self.splice(target, idx, 1, [])
        return True

    def replace(self, old_id: str, new_msg: Dict[str, Any]) -> bool:
        loc = self._locate(old_id)
        if loc is None:
            return False
        target, idx = loc
        self.splice(target, idx, 1, [new_msg])
        return True

    # ---------- 内部 ----------
    def _locate(self, msg_id: str) -> Optional[tuple]:
        for target in ("next-turn", "next-step"):
            for idx, m in enumerate(self.state[target]):
                if m.get("id") == msg_id:
                    return (target, idx)
        return None

    def _splice_internal(self, target: str, start: int, delete_count: int, inserted: List[Dict[str, Any]], record: bool) -> List[Dict[str, Any]]:
        queue = self.state[target]
        # 归一化（简化：支持负数与越界截断）
        actual_start = max(0, min(start if start >= 0 else max(len(queue) + start, 0), len(queue)))
        actual_delete = max(0, min(delete_count, len(queue) - actual_start))
        if actual_delete == 0 and not inserted:
            return []
        # 校验重复 id（跨两筐）
        candidate = queue[:actual_start] + inserted + queue[actual_start + actual_delete :]
        seen = set()
        for t in ("next-turn", "next-step"):
            src = candidate if t == target else self.state[t]
            for m in src:
                mid = m.get("id")
                if mid in seen:
                    raise ValueError(f'message "{mid}" is already pending')
                seen.add(mid)
        # 持久化账单
        splice_data: Dict[str, Any] = {"target": target, "start": actual_start, "inserted": inserted}
        if actual_delete:
            splice_data["removedCount"] = actual_delete
            # 若是清空型（删后不插）记 outcome 供审计，这里简化：删且不插即 canceled
            if not inserted:
                splice_data["outcome"] = "canceled"
        if record:
            self.session.append("agent/inbox/spliced", splice_data)
            # 应用后返回被删的
            removed = queue[actual_start : actual_start + actual_delete]
            # 更新内存筐（用落盘后的 inserted，虽此处 same）
            queue[actual_start : actual_start + actual_delete] = inserted
            return removed
        else:
            # 仅内存（用于重放）
            removed = queue[actual_start : actual_start + actual_delete]
            queue[actual_start : actual_start + actual_delete] = inserted
            return removed

    def _apply(self, splice: Dict[str, Any]):
        target = splice["target"]
        start = splice["start"]
        removed = splice.get("removedCount", 0)
        inserted = splice.get("inserted", [])
        queue = self.state[target]
        queue[start : start + removed] = inserted
