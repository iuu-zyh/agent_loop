"""Session — 内存流水账 + Surface/Header 投影（简化版）"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


SURFACE_TYPES = {"user/message", "assistant/message", "tool/result"}

# 软删除注记：**故意不进 SURFACE_TYPES** —— 老版本 SurfaceManager 对未知类型
# 早退忽略（session 重放不崩，仅照旧显示被删段，优雅降级）；新版本按 delete 算子处理。
DELETE_TYPE = "history/delete"


def collect_deleted_seqs(log) -> set:
    """扫账本里全部 history/delete 事件，汇总被遮蔽的 seq（区间成员 ∪ drop_seqs）。

    UI 投影过滤与删除决策（判断"此前已删除"）共用；history/delete 自身 seq 不在其中。
    """
    deleted: set = set()
    for ev in log:
        if ev.get("type") != DELETE_TYPE:
            continue
        data = ev.get("data") or {}
        for r in (data.get("ranges") or []):
            try:
                s, e = int(r[0]), int(r[1])
            except Exception:
                continue
            if e < s:
                s, e = e, s
            deleted.update(range(s, e + 1))
        for d in (data.get("drop_seqs") or []):
            try:
                deleted.add(int(d))
            except Exception:
                continue
    return deleted


def pairing_balanced(log, nodes) -> bool:
    """目录级配对校验：tool-call 全部有回包、回包不先行（+1/−1 过程不变负、结束归零）。

    删除/任何改目录操作的 fail-closed 兜底——不关心失衡怎么来的（崩溃残局、未来事件
    类型、实现 bug），只关心最终状态是否协议合法。
    """
    calls = 0
    for n in nodes:
        try:
            ev = log[n]
        except IndexError:
            return False
        t = ev.get("type")
        if t == "assistant/message":
            content = (ev.get("data", {}).get("message", {}) or {}).get("content", []) or []
            calls += sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool-call")
        elif t == "tool/result":
            calls -= 1
        if calls < 0:
            return False
    return calls == 0


def derive_event_message(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    t = event.get("type")
    data = event.get("data", {})
    if t == "user/message":
        return data
    if t == "assistant/message":
        msg = data.get("message", {})
        content = msg.get("content", [])
        if not content:
            return None
        return msg
    if t == "tool/result":
        return data.get("message")
    return None


class SurfaceManager:
    """增量 Surface 目录：nodes 为有序的 seq 列表"""

    def __init__(self, log: List[Dict[str, Any]]):
        self.log = log
        self.nodes: List[int] = []
        self.replace_generation = 0
        self._last_seq = -1
        # 重放已有 log
        for ev in log:
            self._apply(ev)

    def _apply(self, event: Dict[str, Any]):
        t = event.get("type")
        seq = event.get("seq")
        if t == DELETE_TYPE:
            self._apply_delete(event)
            self._last_seq = seq
            return
        surface_op = event.get("surfaceOp")
        if t not in SURFACE_TYPES or surface_op is None:
            self._last_seq = seq
            return
        if surface_op == "append":
            self.nodes.append(seq)
        elif isinstance(surface_op, dict) and surface_op.get("op") == "replace":
            start = surface_op["start"]
            end = surface_op["end"]
            try:
                s_idx = self.nodes.index(start)
                e_idx = self.nodes.index(end)
            except ValueError as e:
                raise ValueError(f"Surface replace range not found: {e}")
            if s_idx > e_idx:
                raise ValueError("Surface replace start after end")
            self.nodes[s_idx : e_idx + 1] = [seq]
            self.replace_generation += 1
        else:
            raise ValueError(f"invalid surfaceOp {surface_op}")
        self._last_seq = seq

    def _apply_delete(self, event: Dict[str, Any]):
        """history/delete：机械剔除——affected =（ranges ∪ drop_seqs）− keep_seqs。

        只做成员剔除，**不做任何策略推理**：哪些纪要可删（覆盖闭包）、哪些替换节点豁免
        （sourceEventSeqs 联动）、哪些注入绝对豁免，全部由编排层预计算写进事件 data。
        这样运行时与重放同一路径、确定性一致，且本函数可独立单测。
        幂等：成员本就不在目录里（如已被压缩折叠的原始 seq）静默跳过，绝不抛错。
        """
        data = event.get("data") or {}
        affected = collect_deleted_seqs([event])
        affected -= set(data.get("keep_seqs") or [])
        if not affected:
            return
        self.nodes = [n for n in self.nodes if n not in affected]


class Session:
    """内存 Session：id + log + 双投影"""

    def __init__(self, id: str, header: Optional[Dict[str, Any]] = None, events: Optional[List[Dict[str, Any]]] = None):
        self.id = id
        self.header: Dict[str, Any] = header or {"id": id}
        self.log: List[Dict[str, Any]] = list(events) if events else []
        # 重建时补 seq
        for i, ev in enumerate(self.log):
            if "seq" not in ev:
                ev["seq"] = i
            if "time" not in ev:
                ev["time"] = int(time.time() * 1000)
        self.surface = SurfaceManager(self.log)
        self._header_fold: Optional[Dict[str, Any]] = None
        self._header_fold_seq = 0
        self._fold_header()

    # ---------- 追加 ----------
    def append(self, type: str, data: Any, opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        opts = opts or {}
        seq = len(self.log)
        event: Dict[str, Any] = {
            "seq": seq,
            "time": int(time.time() * 1000),
            "type": type,
            "data": data,
        }
        # Surface 相关字段直接透传
        if "surfaceOp" in opts:
            event["surfaceOp"] = opts["surfaceOp"]
        if "sourceEventSeqs" in opts:
            event["sourceEventSeqs"] = opts["sourceEventSeqs"]
        # 校验并更新投影
        # SurfaceManager 会校验 seq 连续
        self.log.append(event)
        # 更新 Surface
        try:
            self.surface._apply(event)
        except Exception:
            # 回滚 log 以保持一致
            self.log.pop()
            raise
        # 更新 header 折叠
        if type == "request/header":
            self._fold_header()
        # 通知监听者（简化：无）
        return event

    # ---------- 投影 ----------
    def derive_messages(self) -> List[Dict[str, Any]]:
        msgs: List[Dict[str, Any]] = []
        for seq in self.surface.nodes:
            ev = self.log[seq]
            msg = derive_event_message(ev)
            if msg is not None:
                msgs.append(msg)
        return msgs

    def request_header(self) -> Optional[Dict[str, Any]]:
        return self._header_fold

    def _fold_header(self):
        # 取最后一条 request/header
        for ev in reversed(self.log):
            if ev.get("type") == "request/header":
                self._header_fold = ev["data"].get("header")
                return
        self._header_fold = None

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self.log

    # ---------- 辅助 ----------
    def has_open_turn(self) -> bool:
        for ev in reversed(self.log):
            if ev["type"] == "turn/start":
                return True
            if ev["type"] == "turn/end":
                return False
        return False
