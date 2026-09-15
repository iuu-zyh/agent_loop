"""history_ops — 对话历史的写侧操作（软删除）

history.py 是只读投影（账本 → UI 时间线）；本模块是写侧：把用户在对话 UI 里的
「删除回合」请求变成一条 history/delete 注记事件。账本只追加——删除 = 追加注记 +
双投影过滤（surface 剔成员、UI 投影跳 seq），持久化时机统一交给存档钩子
（save_happened → flush_all），因此天然「随存档固化、读档回滚」（09-11 存档语义）。

删除算法定稿（09-12，与压缩逻辑耦合的取舍全部收敛在这里）：
- ranges：完整 turn 的账本跨度（turn/start..turn/end），由本模块从当前账本换算，
  绝不信任 C# 递来的裸 seq 区间；普通对话节点按 **seq 成员**剔除（seq 永不重排，
  已被压缩折叠的原始 seq 不在目录里 → 幂等跳过）；
- 纪要节点（plugin=compact 的 user/message）：**条件豁免**——当且仅当其代表的内容
  （shadowedSeqs 的传递闭包，含链式纪要；此前已删 seq 视为已覆盖）全部被删/在删时才
  删（drop_seqs，即使其 seq 不在任何 range 里）；部分覆盖则豁免（keep_seqs）；
- pruner 替换节点（带 sourceEventSeqs）：**原文全部在被删范围内才删**（原文的 turn
  被删 ⇒ 调用也被删 ⇒ 替换节点必须跟着走，否则孤儿回包）；否则豁免（原文还活着，
  回包不能消失）——R 的账本 seq 常落在压缩当时那个 turn 的跨度里，与 O 不同 turn；
- 其他 plugin 注入（L1 runtime context 等）：绝对豁免（是状态注入不是历史）；
- 兜底：剔除后对模拟出的新目录跑整体配对校验，不平衡整个拒绝（fail-closed），
  绝不落账残局。

决策（drop_seqs/keep_seqs）在编排层预计算写进事件；SurfaceManager._apply 只做
机械剔除——运行时与重放同一路径、确定性一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import log_setup
from .session import DELETE_TYPE, collect_deleted_seqs, pairing_balanced

log = log_setup.get_logger(__name__)


class DeleteError(ValueError):
    """删除请求不合法（回合不存在/未闭合/会破坏配对等）——fail-closed。"""


# ---------- turn → 账本跨度 ----------

def _turn_spans(session) -> Dict[int, List[int]]:
    """扫账本得 {turn: [start_seq, end_seq]}，只收已闭合回合。"""
    spans: Dict[int, List[int]] = {}
    open_turn: Optional[int] = None
    open_seq: Optional[int] = None
    for ev in session.log:
        t = ev.get("type")
        seq = ev.get("seq")
        if t == "turn/start":
            open_turn = int((ev.get("data") or {}).get("turn", -1))
            open_seq = seq
        elif t == "turn/end" and open_turn is not None:
            spans[open_turn] = [int(open_seq), int(seq)]
            open_turn, open_seq = None, None
    return spans


def resolve_turn_ranges(session, turns: List[int]) -> List[List[int]]:
    """把 UI 选的 turn 编号换算成账本 seq 区间（完整回合跨度）。"""
    if not turns:
        raise DeleteError("未选择任何回合")
    if session.has_open_turn():
        raise DeleteError("回合进行中，不能删除历史")
    spans = _turn_spans(session)
    missing = [int(t) for t in turns if int(t) not in spans]
    if missing:
        raise DeleteError(f"回合不存在或未闭合: {missing}（可删: {sorted(spans)}）")
    return [spans[int(t)] for t in turns]


# ---------- 纪要覆盖闭包 ----------

def _summary_audits(session) -> Dict[str, Dict[str, Any]]:
    audits: Dict[str, Dict[str, Any]] = {}
    for ev in session.log:
        if ev.get("type") == "compaction/summary":
            d = ev.get("data") or {}
            cid = d.get("compactionId")
            if cid:
                audits[cid] = d
    return audits


def _is_summary_node(ev: Dict[str, Any]) -> bool:
    if ev.get("type") != "user/message":
        return False
    src = (ev.get("data") or {}).get("source") or {}
    return src.get("kind") == "plugin" and src.get("plugin") == "compact"


def _represented(session, seq: int, audits, memo: Dict[int, Optional[set]]) -> Optional[set]:
    """纪要节点代表的账本 seq 集合（传递闭包，含链式纪要）。

    依赖 compaction/summary 审计事件里的 shadowedSeqs（09-12 起 _replace 会记，
    且必须用它而非 shadowedRange——整体压缩按目录位置吞成员，pruner 替换节点的
    seq 数值上在区间之外）。老账本只有数值区间、拿不到精确成员 ⇒ 返回 None
    （不可判 → 保守保留纪要）。victims 恒为更早的 seq，天然无环。
    """
    if seq in memo:
        return memo[seq]
    ev = session.log[seq]
    src = (ev.get("data") or {}).get("source") or {}
    audit = audits.get(src.get("compactionId")) or {}
    victims = audit.get("shadowedSeqs")
    if victims is None:
        memo[seq] = None
        return None
    out: set = set()
    for v in victims:
        v = int(v)
        out.add(v)
        if 0 <= v < len(session.log) and _is_summary_node(session.log[v]):
            sub = _represented(session, v, audits, memo)
            if sub is None:
                memo[seq] = None
                return None
            out |= sub
    memo[seq] = out
    return out


# ---------- 方案计算（纯函数，不改账本） ----------

def _node_chars(ev: Dict[str, Any]) -> int:
    data = ev.get("data") or {}
    blocks: Any = data.get("content")
    if ev.get("type") == "assistant/message":
        blocks = (data.get("message") or {}).get("content")
    elif ev.get("type") == "tool/result":
        blocks = (data.get("message") or {}).get("content")
    total = 0
    for b in blocks or []:
        if isinstance(b, dict) and b.get("type") == "text":
            total += len(str(b.get("text") or ""))
    return total


def plan_delete(session, turns: List[int]) -> Dict[str, Any]:
    """计算删除方案：ranges / drop_seqs / keep_seqs + 模拟新目录 + 配对校验。

    纯函数（不改账本、不落盘）。返回 dict：
    - ok/reason：模拟新目录配对是否合法（fail-closed 的依据）；
    - ranges/drop_seqs/keep_seqs：history/delete 事件的落账载荷；
    - removed_nodes/removed_chars/dropped_summaries/kept_summaries：preview 报告用。
    """
    ranges = resolve_turn_ranges(session, turns)
    range_union: set = set()
    for s, e in ranges:
        range_union.update(range(s, e + 1))
    prior = collect_deleted_seqs(session.log)      # 此前已删除（多次删除叠加视为已覆盖）
    log = session.log
    nodes = list(session.surface.nodes)

    drop: List[int] = []
    keep: List[int] = []
    dropped_summaries: List[Dict[str, Any]] = []
    kept_summaries: List[int] = []
    audits = _summary_audits(session)
    memo: Dict[int, Optional[set]] = {}

    for n in nodes:
        ev = log[n]
        t = ev.get("type")
        in_range = n in range_union
        src = ((ev.get("data") or {}).get("source") or {})
        is_plugin_user = t == "user/message" and src.get("kind") == "plugin"

        if is_plugin_user:
            if src.get("plugin") == "compact":
                rep = _represented(session, n, audits, memo)
                if rep is not None and rep <= (range_union | prior):
                    if not in_range:
                        drop.append(n)     # 全覆盖但 seq 在范围外（缝隙/保留尾/后续 turn）
                    dropped_summaries.append({"seq": n, "represented": sorted(rep)})
                elif in_range:
                    keep.append(n)         # 部分覆盖/旧账本不可判 → 豁免
                    kept_summaries.append(n)
            elif in_range:
                keep.append(n)             # 运行时注入等：绝对豁免
            continue

        if ev.get("sourceEventSeqs"):
            srcs = {int(s) for s in ev["sourceEventSeqs"]}
            if srcs and srcs <= range_union:
                if not in_range:
                    drop.append(n)         # 原文被删 → 替换节点即使不在范围也得走
            elif in_range:
                keep.append(n)             # 原文还活着 → 回包不能消失
            continue
        # 普通对话节点：seq ∈ range 即剔除（默认成员规则，无需登记）

    affected = (range_union | set(drop)) - set(keep)
    new_nodes = [n for n in nodes if n not in affected]
    removed = [n for n in nodes if n in affected]
    ok = pairing_balanced(log, new_nodes)
    return {
        "ok": ok,
        "reason": None if ok else "删除后会破坏 tool-call/result 配对（协议非法），已整体拒绝",
        "turns": [int(t) for t in turns],
        "ranges": ranges,
        "drop_seqs": drop,
        "keep_seqs": keep,
        "removed_nodes": len(removed),
        "removed_chars": sum(_node_chars(log[n]) for n in removed),
        "dropped_summaries": dropped_summaries,
        "kept_summaries": kept_summaries,
    }


def append_delete(session, plan: Dict[str, Any], reason: str = "user") -> Dict[str, Any]:
    """把通过校验的删除方案落成一条 history/delete 注记事件（账本只追加）。"""
    if not plan.get("ok"):
        raise DeleteError(plan.get("reason") or "删除方案未通过校验")
    return session.append(
        DELETE_TYPE,
        {
            "ranges": plan["ranges"],
            "drop_seqs": plan["drop_seqs"],
            "keep_seqs": plan["keep_seqs"],
            "turns": plan["turns"],
            "reason": reason,
        },
    )
