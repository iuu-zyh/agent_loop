"""Inbox — 双筐排队 + spliced 账单持久化"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: 信箱消息的 `source.kind`。**与 `agent/mailbox.MAIL_KIND` 必须同值**（有契约测试钉）：
#: 这里不 import 那个常量，是因为依赖方向只能是 `agent → session`（反向会成环）。
_MAIL_KIND = "mail"


def _is_mail(msg: Any) -> bool:
    """这条筐里的消息是不是捎话（别人托来的话，不是玩家说的）。"""
    if not isinstance(msg, dict):
        return False
    src = msg.get("source")
    return isinstance(src, dict) and src.get("kind") == _MAIL_KIND


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
        """还有信要处理吗？**信箱（捎话）本身不算**。

        这是「捎话只在玩家与 B 说话时才生效」的**唯一实现点**（用户 11-12 的原话：
        「不是让 b 去立即互动……只有当玩家和 b 对话的时候才会触发」）。
        `run_until_idle` 用本属性决定要不要起回合：若把只有捎话的筐算成 pending，
        B 会**凭空开一个回合、对着空气自说自话一句**（实测过：回合数 2，首回合里
        没有任何玩家的话），然后再在下一回合回应玩家 —— 那正是用户不要的"立即互动"。
        """
        if self.next_step:
            return True
        return any(not _is_mail(m) for m in self.next_turn)

    def clear(self):
        # 先清 next-step 再清 next-turn（与 DSH 一致）
        self.splice("next-step", 0, len(self.next_step), [])
        self.splice("next-turn", 0, len(self.next_turn), [])

    def claim(self, target: str, turn: int) -> List[Dict[str, Any]]:
        """领信：先吃 next-step 全量，再吃 next-turn **一条玩家消息 + 它前面的捎话**。

        ## 为什么 next-turn 的领取比"一条"复杂（11-12 捎话配套）

        `next-turn` 每回合只吃一条，那是给"玩家连发三条"设计的上限：一条一句，逐回合回应。
        但**捎话不是玩家发的**，它只是搭车进来的一句话。若沿用"只吃一条"：

          · 筐 = [捎话, 玩家的话] ⇒ 本回合只领到捎话 ⇒ B 先对着空气说一句，
            玩家的话被推到下一回合（实测就是这个症状：回合数 2，首回合里没有玩家的话）；
          · 筐 = [玩家的话, 捎话] ⇒ 玩家的话落地，捎话留到下一回合 ⇒ 同一句话要分两次读。

        所以规则的准确表述是：**捎话不占那个"一条"的名额**。
        """
        claimed: List[Dict[str, Any]] = []
        # 吃 next-step 全量（不发 discarded 通知的静默版，简化直接 splice）
        if self.next_step:
            removed = self._splice_internal("next-step", 0, len(self.next_step), [], record=True)
            claimed.extend(removed)
        if target != "next-turn":
            return claimed
        # ★ 规则只有两句，但两句都是**硬保证**（11-12 复核时实测出来的）：
        #   ① **玩家的话绝不会被捎话挤掉**，且一定和它前面的捎话**同回合**读到 ——
        #      否则 B 会先对着空气自说自话一句（= 用户明确不要的"立即互动"），
        #      再在下一回合才回应玩家。
        #   ② **队里没有玩家的话时，一条都不领** —— 捎话绝不能自己撑起一个回合。
        #      （`has_pending` 已经为 False 挡掉了大多数场合，但 `next-step` 那条路
        #       仍会带着捎话进来，所以这里还要挡一道。）
        # 落地成：领到**第一条玩家消息（含）**为止，前面排着的捎话一并带上。
        # 数量有界：单人对单人未读上限 `agent/mailbox.MAIL_MAX_PENDING`，发信人再多也只是
        # 线性增长，且这些都是"玩家不在时积下的"，本就该一次读完。
        # 玩家连发三条的老语义**零改动**（没有捎话时，循环立刻停，仍是每回合一条）。
        stop_at = None
        for i, m in enumerate(self.next_turn):
            if not _is_mail(m):
                stop_at = i
                break
        if stop_at is None:
            return claimed          # 全是捎话：不起回合，原样留着等玩家真的开口
        for _ in range(stop_at + 1):
            claimed.extend(self._splice_internal("next-turn", 0, 1, [], record=True))
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
