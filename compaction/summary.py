"""compaction/summary — 压缩纪要的读取 / 可编辑判据 / 修改落账（纯逻辑）

为什么单独一个模块（而不是塞进 `compress.py`）：这三件事有**三个消费者**——
历史投影（`session/history.py`）、RPC（`transport/ws_channel.py`）、测试。
三者必须对"哪条纪要是活的、能不能改、改成什么形状"给出**同一个答案**；
放进压缩器会让"负责压缩"顺带背上"负责查账本"，也会让只读投影反向依赖压缩实现。

账本语义（改之前先读 `docs/plan-compaction-view.md` §1.3 / §2.3）：
  · 账本只追加 —— "修改"= 再追加一条 `user/message` + `surfaceOp: replace{start=end=旧seq}`，
    绝不原地改旧事件的 `data`（那会破坏回放与前缀缓存）。
  · `compactionId` **跨编辑不变**：删除逻辑的覆盖闭包（`history_ops._represented`）按它查
    `compaction/summary.shadowedSeqs`；换 id 会让改过的纪要先掉"我代表哪段原文"，
    于是"删光原文、纪要该不该一起忘"永远判不出来（保守保留 = 删不掉的记忆）。
  · 编辑事件在 `source.anchorSeq` 记**原始位置**：它的 log seq 排在末尾，但它占据的
    **时间线位置**是原纪要的位置（replace 回同一格）。历史投影靠这个把折叠行画对地方。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from .. import log_setup

log = log_setup.get_logger(__name__)

# 与 `compress.COMPACT_PLUGIN` 必须同值（那边是生产者）。此处不 import 它：
# `session/history.py` 只读投影，不该被"压缩实现"（pruner/LLM 那串依赖）牵着走。
# 同值由 `tests/test_compaction_summary_view.py` 直接断言，不会静默漂移。
SUMMARY_PLUGIN = "compact"

SUMMARY_OPEN = "<compacted-summary>"
SUMMARY_CLOSE = "</compacted-summary>"

# 修改审计事件（**不是** SURFACE_TYPES，重放时被忽略；只给人读账本/给测试定位）
EDIT_TYPE = "compaction/edited"

# 正文上限：纪要正常量级 1~3k 字；2 万字已经远超"一条纪要"，再大只会是误粘贴。
MAX_CHARS = 20000


class SummaryEditError(ValueError):
    """纪要修改请求不合法（不可编辑/已被取代/为空/超长）——fail-closed，账本一字不动。"""


# ---------------------------------------------------------------- 识别与取文

def is_summary(ev: Any) -> bool:
    """这条事件是不是压缩纪要节点（plugin user/message + plugin=="compact"）。"""
    if not isinstance(ev, dict) or ev.get("type") != "user/message":
        return False
    src = (ev.get("data") or {}).get("source") or {}
    return src.get("kind") == "plugin" and src.get("plugin") == SUMMARY_PLUGIN


def summary_text(ev: Any) -> str:
    """取纪要正文：拼接 text 块 → 剥掉 `<compacted-summary>` 标记 → strip。

    标记是给**模型/压缩材料**看的（`compress._transcript` 靠它认「既往纪要」），
    UI 与 RPC 都只要正文；剥在这里，两个消费者不必各写一份。
    """
    data = (ev.get("data") or {}) if isinstance(ev, dict) else {}
    parts: List[str] = []
    for b in data.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            parts.append(str(b["text"]))
    t = "".join(parts)
    if SUMMARY_OPEN in t or SUMMARY_CLOSE in t:
        t = t.replace(SUMMARY_OPEN, "").replace(SUMMARY_CLOSE, "")
    return t.strip()


def wrap(text: str) -> List[Dict[str, str]]:
    """正文 → 账本里的 content（带标记，与 `compress._replace` 逐字同形）。"""
    return [{"type": "text", "text": f"{SUMMARY_OPEN}\n{text}\n{SUMMARY_CLOSE}"}]


def normalize_text(text: Any) -> str:
    """规范化用户提交的正文：统一换行、防御性地剥掉误粘贴的标记、去首尾空白。"""
    t = str(text if text is not None else "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    if SUMMARY_OPEN in t or SUMMARY_CLOSE in t:
        t = t.replace(SUMMARY_OPEN, "").replace(SUMMARY_CLOSE, "")
    return t.strip()


# ---------------------------------------------------------------- 读：活的纪要

def _surface_nodes(session: Any) -> List[int]:
    try:
        return list(session.surface.nodes)
    except Exception:            # 裸 Session / 半构造对象：当作"没有 surface"
        return []


def _event(session: Any, seq: int) -> Optional[Dict[str, Any]]:
    try:
        ev = session.log[seq]
    except (IndexError, TypeError, KeyError):
        return None
    return ev if isinstance(ev, dict) else None


def info_of(session: Any, seq: int) -> Optional[Dict[str, Any]]:
    """把一条纪要节点整理成对外的 info（协议与投影共用同一形状）。"""
    ev = _event(session, seq)
    if ev is None or not is_summary(ev):
        return None
    src = (ev.get("data") or {}).get("source") or {}
    text = summary_text(ev)
    editable, reason = editability(session, seq)
    # 原始位置：编辑事件靠 `anchorSeq` 指回它；没编辑过就是自己。
    # 刻意用 `is None` 而不是 `or` —— seq 0 是合法值，`or` 会把它吞掉（假值陷阱）。
    _anchor = src.get("anchorSeq")
    return {
        "seq": int(seq),
        "anchor": int(_anchor) if _anchor is not None else int(seq),
        "compaction_id": str(src.get("compactionId") or ""),
        "text": text,
        "chars": len(text),
        "editable": bool(editable),
        "lock_reason": "" if editable else str(reason),
        "edited": bool(src.get("edited")),
        "edits": int(src.get("edits") or 0),
    }


def latest(session: Any) -> Optional[Dict[str, Any]]:
    """当前**仍生效**的最后一条纪要；没有则 None。

    ★ 扫 `surface.nodes` 而不是 `log`：一条纪要可能被改过多次（每次一条 replace），
      surface 里只留最后那条；被改掉的旧节点还在 log 里但已不在 surface。
      取"surface 顺序里的最后一条"天然等于"最新且仍生效的那条"。
    """
    for seq in reversed(_surface_nodes(session)):
        if is_summary(_event(session, seq)):
            return info_of(session, seq)
    return None


def _inside_turn(log: Any, seq: int) -> bool:
    """seq 这一刻是否**处在一个回合内部**（最近的 turn/start 还没被 turn/end 收口）。

    这是"自动压缩的纪要永远不可改"的判据 —— 见 `editability` 的 ②。
    不看 `session.has_open_turn()`（那只看**当前**是否开着）：纪要在回合中部落账，
    回合后来闭合了，"它落在回合内"这个事实不会因此消失。
    """
    open_start = None
    try:
        for ev in log[: int(seq) + 1]:
            t = ev.get("type") if isinstance(ev, dict) else None
            if t == "turn/start":
                open_start = ev.get("seq")
            elif t == "turn/end":
                open_start = None
    except Exception:
        return True              # 判不了 ⇒ 保守按"在回合内"（= 不可改）
    return open_start is not None


def editability(session: Any, seq: int) -> Tuple[bool, str]:
    """**账本推导**的可编辑判据（纯函数、可回放；运行时那一道在 RPC 里另判）。

    同时满足下面四条才可改（语义 =「这条纪要还没被任何一次请求用过」）：

      ① 该节点仍在 surface（不是被后来一次压缩折走的旧纪要）；
      ② 它**不在一个回合内部** —— 自动压缩发生在 `turn/start` 之后（`_turn`），
         所以自动产出的纪要**永远**落在回合里 ⇒ 永远不可改（用户拍板的窗口只对手动压缩存在）；
      ③ 它之后没有 `turn/start` —— 手动压缩之后玩家发消息 = 新一轮开始 ⇒ 锁定；
      ④ 它之后没有 `assistant/message` —— 防御性兜底（③ 已覆盖一切现实路径：
         回合必定以 turn/start 开头；留着是为了将来若有别的路径直接落 assistant 时仍能锁住）。
    """
    if int(seq) not in set(_surface_nodes(session)):
        return False, "这条纪要已被新的压缩取代，不能再修改"
    if _inside_turn(getattr(session, "log", []), int(seq)):
        return False, "本轮对话已经开始，压缩纪要不能再修改"
    try:
        tail = session.log[int(seq) + 1:]
    except Exception:
        tail = []
    for ev in tail:
        t = ev.get("type") if isinstance(ev, dict) else None
        if t == "turn/start":
            return False, "已开启新一轮对话，压缩纪要不能再修改"
        if t == "assistant/message":
            return False, "模型已基于这条纪要说过话，不能再修改"
    return True, ""


# ---------------------------------------------------------------- 写：唯一入口

def apply_edit(session: Any, compaction_id: str, text: Any, reason: str = "user") -> Dict[str, Any]:
    """把玩家的修改落进账本（**唯一写入口**）。返回修改后的 info（形状同 `latest`）。

    校验全部 fail-closed：任何一条不满足就抛 `SummaryEditError`，账本一字不动。
    成功时**先写审计、再写替换**（与压缩的 audit → message 顺序对称，纯为读账本时顺眼）。
    """
    info = latest(session)
    if info is None:
        raise SummaryEditError("这段对话还没有可修改的压缩纪要")
    cid = str(compaction_id or "")
    if not cid:
        raise SummaryEditError("缺少纪要标识（请重新点一次「修改」）")
    if cid != info["compaction_id"]:
        # 面板开着时又压了一次 / 换了 NPC：旧面板手里的 id 不再是"当前那条"
        raise SummaryEditError("这条纪要已被新的压缩取代，不能再修改")
    if not info["editable"]:
        raise SummaryEditError(info["lock_reason"] or "当前不能修改压缩纪要")
    new_text = normalize_text(text)
    if not new_text:
        raise SummaryEditError("压缩纪要不能为空")
    if len(new_text) > MAX_CHARS:
        raise SummaryEditError(f"压缩纪要过长（{len(new_text)} 字，上限 {MAX_CHARS} 字）")

    old_seq = int(info["seq"])
    edits = int(info["edits"]) + 1
    session.append(EDIT_TYPE, {
        "compactionId": cid,
        "fromSeq": old_seq,
        "charsBefore": int(info["chars"]),
        "charsAfter": len(new_text),
        "reason": str(reason or "user"),
    })
    ev = session.append(
        "user/message",
        {
            "role": "user",
            "content": wrap(new_text),
            # 新消息 id：与压缩折出来的那条同规约（`compress._replace` 也是新 uuid）——
            # 绝不复用旧节点 id，否则"这条消息"在两个版本间失去唯一性。
            "id": str(uuid.uuid4()),
            "source": {
                "kind": "plugin",
                "plugin": SUMMARY_PLUGIN,
                "compactionId": cid,
                "edited": True,
                "anchorSeq": int(info["anchor"]),
                "edits": edits,
            },
        },
        # ★ start == end：把**这一格**换成新节点。与压缩折叠一段区间是同一个算子
        #   （`SurfaceManager._apply` 的 replace 分支），所以重放路径天然一致。
        {"surfaceOp": {"op": "replace", "start": old_seq, "end": old_seq}},
    )
    new_seq = int(ev.get("seq"))
    out = info_of(session, new_seq) or {
        "seq": new_seq, "anchor": info["anchor"], "compaction_id": cid,
        "text": new_text, "chars": len(new_text), "editable": True,
        "lock_reason": "", "edited": True, "edits": edits,
    }
    log.info("压缩纪要已修改：%s seq %d→%d，%d→%d 字（第 %d 次）",
             cid[:8] or "?", old_seq, new_seq, int(info["chars"]), len(new_text), edits)
    return out


# ---------------------------------------------------------------- 历史投影辅助

def anchors(session: Any) -> Dict[int, Dict[str, Any]]:
    """{原始位置 seq: 当前生效的 info} —— 历史投影用它把折叠行画在**原来那一格**。

    只收 surface 里活着的纪要；编辑链只留最新一条（`info_of` 的 seq 是新的，
    `anchor` 是旧的），所以同一个位置不会被画两遍。
    """
    out: Dict[int, Dict[str, Any]] = {}
    for seq in _surface_nodes(session):
        if not is_summary(_event(session, seq)):
            continue
        info = info_of(session, seq)
        if info is not None:
            out[int(info["anchor"])] = info
    return out
