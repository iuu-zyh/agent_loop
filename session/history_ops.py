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

**消息级变体**（09-17，群窗删除用）：`plan_message_delete` 与 `plan_delete` 规则同款，
只是把"要删什么"从**回合跨度**换成**显式节点集合**（由 uid / `room/utt` 锚点翻译而来）。
群窗删除是**逐成员 fan-out**：每人一份方案、各自过配对校验；任一成员不平衡 ⇒
整体拒绝（账本零写入）——不做"跳过某个成员"，否则会留下"有人忘了、有人记得"、
而 UI 无法表达的状态。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import log_setup
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


def live_turn_count(session) -> int:
    """**现存**轮数：回合跨度里还有节点留在 surface 的回合数。

    与 `UsageTracker.turns_seen`（累计轮数，从 step/end 重放）不是一回事：
    删除/压缩之后两者不再相等——累计是"花了多少轮的钱"，这个是"手上还剩几轮"。
    UI 把两者混着读就会出现"删光了还显示 20 轮"的困惑。
    """
    try:
        nodes = set(session.surface.nodes)
    except Exception:
        return 0
    n = 0
    for start, end in _turn_spans(session).values():
        if any(seq in nodes for seq in range(start, end + 1)):
            n += 1
    return n


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
    # 全部回合的跨度并集（不只本次选中的）：用来判"某个 seq 属不属于任何 turn"。
    in_some_turn: set = set()
    for _s, _e in _turn_spans(session).values():
        in_some_turn.update(range(_s, _e + 1))

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
                # 覆盖判据只看**属于某个 turn 的成员** —— 不属于任何 turn 的成员
                # （会话最前面那条 C# `@python-harness/contacts`「第一段传音」通知、
                #  turn 之外的其他会话级注入）永远进不了任何按回合的删除范围，
                # 拿它们当覆盖障碍 ⇒ 这段纪要**永远删不掉**。09-17 实机正是如此：
                # 用户"全部删除"后模型照旧读到纪要里那句「约定来年开春好好聚一聚」。
                # 注意：不能放宽成"不在 surface 就不算障碍"——被折走的原文本来就不在
                # surface（那正是纪要存在的原因），那样会把**部分覆盖豁免**一并打穿。
                turn_rep = ({v for v in rep if v in in_some_turn}
                            if rep is not None else set())
                if rep is not None and turn_rep <= (range_union | prior):
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


def plan_message_delete(session, targets: List[int]) -> Dict[str, Any]:
    """计算「按**消息**删除」的方案 —— 群窗删除一条发言时对**每个成员**跑一遍。

    与 `plan_delete`（按回合删）的关系：规则**同款**，粒度不同。那边把"要删什么"表达成
    `range_union`（整段回合的账本跨度），这边换成一个**显式的节点集合** `targets`
    （由 `DialogueAgent.room_utt_nodes` 从 uid / `room/utt` 锚点翻译而来）。于是：

    - 目标在目录里 ⇒ 剔除（`drop_seqs`）；
    - **纪要节点**：其代表的 `shadowedSeqs` 传递闭包（此前已删视为已覆盖）**全部**落在
      `targets` 里才删纪要；**部分覆盖 ⇒ 纪要原样保留**（强删会连带删掉别的记忆），
      并把"被折进去、无法逐字遗忘"的条目如实报进 `folded`；
    - `sourceEventSeqs` 替换节点：原文全被删才删（否则回包会变孤儿）；
    - 其它 plugin 注入：绝对豁免（是状态注入不是历史）；
    - **fail-closed**：模拟新目录跑 `pairing_balanced`，不平衡则 `ok=False`（调用方整体拒绝）。

    `ranges` / `turns` 恒为空 —— 消息级删除不该连坐整个回合（那正是它不用 plan_delete 的原因）。

    找不到的目标（没听到 / 后加入 / 此前已删）不计入 `found`，落在 `missing` 里 ⇒
    调用方跳过并如实报告，天然幂等。老账本缺 `shadowedSeqs` 时纪要覆盖不可判
    （`_represented` 返回 None）：**保守保留纪要**并记进 `unknown_summaries`。
    """
    want = {int(t) for t in (targets or [])}
    if not want:
        raise DeleteError("未指定要删除的消息")
    prior = collect_deleted_seqs(session.log)
    covered = want | prior
    log = session.log
    nodes = list(session.surface.nodes)

    drop: List[int] = []
    keep: List[int] = []
    dropped_summaries: List[Dict[str, Any]] = []
    kept_summaries: List[int] = []
    unknown_summaries: List[int] = []
    folded: set = set()
    found: set = {n for n in nodes if n in want}
    audits = _summary_audits(session)
    memo: Dict[int, Optional[set]] = {}

    for n in nodes:
        ev = log[n]
        t = ev.get("type")
        in_targets = n in want
        src = ((ev.get("data") or {}).get("source") or {})
        is_plugin_user = t == "user/message" and src.get("kind") == "plugin"

        if is_plugin_user:
            if src.get("plugin") == "compact":
                rep = _represented(session, n, audits, memo)
                if rep is None:
                    unknown_summaries.append(n)     # 不可判 ⇒ 保守保留，如实报告
                    continue
                hit = rep & want
                found |= hit
                if rep <= covered:
                    if not in_targets:
                        drop.append(n)              # 全覆盖但节点本体不在 targets
                    dropped_summaries.append({"seq": n, "represented": sorted(rep)})
                elif hit:
                    keep.append(n)                  # 部分覆盖：豁免
                    kept_summaries.append(n)
                    folded |= hit
            continue

        if ev.get("sourceEventSeqs"):
            srcs = {int(s) for s in ev["sourceEventSeqs"]}
            if srcs and srcs <= want:
                drop.append(n)                      # 原文全被删 ⇒ 替换节点跟着走
            elif in_targets:
                keep.append(n)                      # 原文还活着 ⇒ 回包不能消失
            continue
        # 普通对话节点：节点 ∈ targets 即剔除（默认成员规则，无需登记）

    affected = (want | set(drop)) - set(keep)
    new_nodes = [n for n in nodes if n not in affected]
    removed = [n for n in nodes if n in affected]
    ok = pairing_balanced(log, new_nodes)
    return {
        "ok": ok,
        "reason": None if ok else "剔除后该成员的账本会破坏 tool-call/result 配对（协议非法），已整体拒绝",
        "targets": sorted(want),
        "found": sorted(found),
        "missing": sorted(want - found),
        "folded": sorted(folded),
        "ranges": [],
        "turns": [],
        # ★ 落进事件的是**真正从目录里剔除的节点**（`_apply_delete` 按
        #   `ranges ∪ drop_seqs − keep_seqs` 机械剔除）。已被折进纪要、目录里早就没有的
        #   原始 seq **刻意不写**：写进去只会让 `collect_deleted_seqs` 把它当成"已删"，
        #   而内容其实还留在纪要里 —— 那是撒谎（"折了没法逐字删"由 `folded` 如实报告）。
        "drop_seqs": sorted(removed),
        "keep_seqs": sorted(keep),
        "removed_nodes": len(removed),
        "removed_chars": sum(_node_chars(log[n]) for n in removed),
        "dropped_summaries": dropped_summaries,
        "kept_summaries": kept_summaries,
        "unknown_summaries": unknown_summaries,
    }


def append_delete(session, plan: Dict[str, Any], reason: str = "user") -> Dict[str, Any]:
    """把通过校验的删除方案落成一条 history/delete 注记事件（账本只追加）。

    顺带做**图片回收**：被删区间里的图此后不再需要（`docs/archive/2026-10-18-图片进上下文.md` §二-⑤）。
    放在这里而不是调用方，因为"删回合"（`ws_channel`）与"群窗删消息"（`room_flow`）
    两条路都走这一个函数 —— 漏一条的形状是"删了，但图片永远占着存档目录"。
    """
    if not plan.get("ok"):
        raise DeleteError(plan.get("reason") or "删除方案未通过校验")
    ev = session.append(
        DELETE_TYPE,
        {
            "ranges": plan["ranges"],
            "drop_seqs": plan["drop_seqs"],
            "keep_seqs": plan["keep_seqs"],
            "turns": plan["turns"],
            "reason": reason,
        },
    )
    try:
        from ..storage.images import gc_session
        gc_session(session, why=f"delete:{reason}")
    except Exception:
        # 回收失败**不能**让删除失败：删除已经落账，重试由玩家决定
        log.warning("删除后图片回收失败（删除本身已生效）", exc_info=True)
    return ev
