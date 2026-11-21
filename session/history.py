"""history — UI 历史投影：session 账本 → 对话 UI 时间线（只读纯函数）

各司其职：只读 session.log（不全读，只取 UI 关心的 user/assistant/tool 事实），
不碰 WS、不碰 Agent、不改账本。与 Session.derive_messages 同思路，但走原始 log
而非 surface——surface 只投影 LLM 可见消息（丢 tool/call），而对话 UI 需要
完整的「用户 → 工具调用 → 工具结果 → 回复」时间线。

用户可见性筛选（对 user/message 按 source 分类）：
- 无 source                       → user         玩家消息
- source.kind == "initiative"     → initiative   NPC 主动开口（伪 user 舞台指令不显示为玩家气泡）
- source.kind == "peer"           → peer         同席 NPC 的话（带 speaker 归属，不是玩家气泡）
- source.kind == "plugin"         → 跳过          L1 runtime context / 压缩 checkpoint（内部物）
其余类型：assistant/message 取 text 块；tool/call、tool/result 按 toolCallId 天然配对；
turn/end(reason.kind=error) → turn_error（回放时显示系统提示）。

截断规则：只保留已闭合 turn（在最后一个 turn/end 处切），再取尾部 max_turns 个。
半截 turn 不入历史——UI 端「历史重放 + 直播追加」因此天然不重复、不交叠。

隐藏的还有一类：**替换节点**（`surfaceOp: {op:"replace"}`，压缩 / 工具结果剪枝折出来的
那一条）—— 它只对模型有意义，复述的内容账本里本来就有，投影它必然多一条（见函数内注释）。

例外：**压缩纪要**虽然也是"plugin + replace"，但它有自己的一种条目 `kind: "compaction"`
（10-31）—— 玩家要看得到压缩产出的纪要，否则整段早历史对他是隐形的。规矩：
  · 只在 `compaction.summary.anchors()` 给出的**原始位置**上吐一条，正文取**当前生效**的那版
    （改过多次也只吐最新一版，不会一个位置两行）；
  · `turn` 恒 0 / `scene` 恒 ""：给 turn 会让它跟着那个回合被选中删除；scene 标单聊是因为
    它是**某个 NPC 的记忆**、不属于某一场对话。
    ⚠ 已知盲区（不是本函数能修的）：下面的场合过滤是**按回合**做的，所以落在群聊回合里的纪要
    在 1:1 窗回放时仍会跟着那个回合一起被滤掉。数据/记忆不受影响（它仍在 surface 里），
    只是少显示一行；详见 `docs/plan-compaction-view.md` §7「已知限制」。
  · 仍在走原来那两条过滤：别的 plugin 注入照旧不进 UI，被后一次压缩折走的旧纪要也不吐。

场合过滤（多人化，`docs/archive/2026-09-17-群聊编排/计划-下游消费方.md` §3.3）：每个 item 继承**所在 turn 的
`scene`**（`turn/start.data.scene`；老账本没有该字段 ⇒ `""` = 单聊）。
`project_ui_history(..., scene="")` 只出单聊场合，是 1:1 窗的语义 ——
与直播行为对齐：`ChatPresenter.cs` 会丢掉 `npc_id` 不匹配本窗的帧，只有回放会把
群聊内容塞进 1:1 窗。`scene=None` = 不过滤（要"这个 NPC 的全部经历"时显式要）。
★ 过滤**不重编号**：item 的 `turn` 始终是账本原值 —— C# 的 `RowMeta.Turn` 直接把它
交回 `plan_delete` 当键，重编号会让删除删到别的回合。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..llm import think as _think
from ..agent.initiative import intent_label
from ..compaction.summary import anchors as _summary_anchors
from .session import collect_deleted_seqs


def _texts(content: Any) -> str:
    """拼接 content 块里的全部 text（安全容忍缺块/异形）。"""
    out: List[str] = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            out.append(str(b["text"]))
    return "".join(out)


def _turn_seqs(turn: Dict[str, Any]) -> List[int]:
    """该回合所有条目里出现过的 seq（升序去重）。

    用途：游标翻页的边界判定。回合内的 seq 一定递增（账本单调），
    但条目**不一定都带 seq**（`turn_error` / `compaction` 等合成条目没有），故要过滤。
    """
    out: List[int] = []
    for it in turn.get("items") or []:
        v = it.get("seq")
        if isinstance(v, int):
            out.append(v)
    out.sort()
    return out


def _turn_min_seq(turn: Dict[str, Any]) -> int:
    """该回合最早的 seq；没有 seq（纯合成回合）⇒ 0。"""
    seqs = _turn_seqs(turn)
    return seqs[0] if seqs else 0


def _turn_max_seq(turn: Dict[str, Any]) -> int:
    """该回合最晚的 seq（= 回合结束处）；没有 seq ⇒ 0。

    游标取它而不是 `_turn_min_seq`：以"回合结束处"当锚，
    下一页的判据 `该回合最大 seq < 游标` 才不会把跨越边界的回合重复返回。
    """
    seqs = _turn_seqs(turn)
    return seqs[-1] if seqs else 0


def _turn_before_cursor(turn: Dict[str, Any], before_seq: int, before_turn: int) -> bool:
    """该回合是否**整体**落在游标之前（游标翻页的筛选判据）。双锚，缺一不可。

    ── 为什么必须两个锚 ──────────────────────────────────────────────────
    账本里**有些回合一个 seq 都没有**（姜萌 43 回合里有 2 个：`initiative` /
    `turn_error` 这类合成条目）。只按 seq 判会把它们当成"永远更早"⇒ 每页都返回一次。
    实测症状：第 1 页 `[38..58]`、第 2 页 `[18..42]`，38/42 在两页里都出现。

    ── 判据 ──────────────────────────────────────────────────────────────
    1. `before_turn > 0` 且该回合 `turn < before_turn` ⇒ 更早（**优先**，对无 seq 回合有效）；
    2. 否则退到 seq：该回合**最大** seq `< before_seq` ⇒ 更早。
       用最大（回合结束处）而不是最小：用最小会让"跨越游标的回合"每页重复出现
       （它的首条 seq 恒小于游标，而游标按最小 seq 推进永远推不过它）。
    3. 两个都没有（无 seq 且无 turn）⇒ 保留：宁可多给也不能因为缺字段吞掉时间线。
    """
    t = turn.get("turn")
    if before_turn > 0 and isinstance(t, int):
        return t < before_turn
    if before_seq > 0:
        seqs = _turn_seqs(turn)
        if not seqs:
            return False          # 无 seq 走 turn 判定；turn 也没有 ⇒ 交给调用方（下面 return True）
        return seqs[-1] < before_seq
    return True


def project_ui_history(session: Any, max_turns: Any = 10, scene: Any = None,
                        before_seq: Any = None, before_turn: Any = None) -> Dict[str, Any]:
    """投影最近 max_turns 个完整 turn（可限定在 before_seq 之前 ⇒ 游标翻页）。

    **max_turns 语义（09-13）**：`None` 或 `<= 0` = **全部**（用户要求"历史要看全部的"，
    C# 侧固定传 `limit=0`）；正数 = 最近 N 个完整 turn（老行为；缺省 10 保持不变，
    向后兼容 `chat_cli`/测试/未显式传参的调用方）。

    **scene 语义（多人化）**：`None`（缺省）= **不过滤**，全部场合；
    字符串（含 `""`）= 只出该场合的回合（`""` 即单聊）。过滤发生在**截断之前**，
    所以 `max_turns` 是"该场合内最近 N 个回合"。详见模块 docstring。

    返回 {"items":[...], "complete_turns": N}：
    - items：按账本顺序的 UI 时间线，每条含 kind/scene/turn（/step/seq）等字段；
    - complete_turns：**参与本轮投影的**完整 turn 数（已按 scene 过滤、未删）——
      `scene=None` 时它等于账本里未删的完整回合总数（老语义不变）。
      ⚠ 全部加载时它恒等于已投影的回合数（C# 侧目前未使用该字段）。

    软删除过滤（09-12）：账本里 history/delete 注记的 seq（区间成员 ∪ drop_seqs）一律跳过。
    删除区间由完整 turn 跨度构成 ⇒ turn/start..end 一并被过滤 ⇒ 被删 turn 在时间线上整段
    消失，不会留下空 turn 壳。

    ── before_seq：游标翻页（11-21）────────────────────────────────────────
    `None` / `<= 0` = 不限定（老行为，逐字不变）。
    正数 = **只取该 seq 之前**的回合，再按 max_turns 截最近 N 个。

    为什么用 **seq 游标**而不是 offset：账本里有 `history/delete` 删除注记与压缩纪要
    替换，删掉一条会让其后所有下标前移 ⇒ offset 翻页会**重复或漏**。`seq` 单调递增、
    永不复用，游标不会漂移。

    判定"某回合是否在 before_seq 之前"用该回合 `items` 里的**最大** seq
    （= 回合结束处；用最小 seq 会让跨越游标的回合每页重复出现，见 `_turn_ends_before`。
    空回合无 seq ⇒ 视为**在之前**，直接保留：它是时间线的一部分，丢掉会让分隔条错位）。
    """
    try:
        _n = int(max_turns) if max_turns is not None else 0
    except Exception:
        _n = 10
    unlimited = _n <= 0
    deleted = collect_deleted_seqs(session.log)
    # 压缩纪要：{原始位置 seq: 当前生效的 info}。一条纪要可能被改过多次，这里只留最新那版，
    # 位置仍按**原来那一格**画（编辑是 replace 回同一格，见 `compaction/summary.py` 文件头）。
    try:
        compact_at = _summary_anchors(session)
    except Exception:                         # 裸/异形 session：投影退化成"没有纪要"，绝不整页瞎掉
        compact_at = {}
    turns: List[Dict[str, Any]] = []          # 每个：{"turn": n, "scene": s, "items": [...], "closed": bool}
    cur: Dict[str, Any] = {"turn": 0, "scene": "", "items": [], "closed": True}
    turn_no = 0
    step_no = 0

    for ev in session.log:
        seq = ev.get("seq")
        # ★ 压缩纪要**先于**两条过滤判：它的锚点是"plugin + replace"节点，走到下面就没了。
        #   锚点自己被删（比如它所在的回合被删）也照吐 —— 那条纪要仍然活在 surface 里，
        #   删的只是"它落在哪一回合"这个位置信息；此时 cur 停在上一回合末尾，位置正好。
        #   真正要判的是**当前节点**有没有被删（编辑后的节点 seq 与锚点不同）。
        _c = compact_at.get(seq) if seq is not None else None
        if _c is not None:
            if _c.get("seq") not in deleted and _c.get("text"):
                cur["items"].append({
                    "kind": "compaction",
                    "text": _c["text"],
                    "compaction_id": _c.get("compaction_id", ""),
                    "chars": int(_c.get("chars") or 0),
                    "editable": bool(_c.get("editable")),
                    "lock_reason": _c.get("lock_reason", ""),
                    # 不是任何回合的内容 ⇒ 不进删除模式的可选单位（见 `ChatWindow.BindKey`）。
                    "turn": 0,
                    "seq": _c.get("seq"),
                })
            continue
        if seq is not None and seq in deleted:
            continue
        # ★ 替换节点（`surfaceOp: {op:"replace"}`）**不进 UI**（09-17 复核补）：它是给模型侧的
        #   经济手段（把一段折成一个），复述的内容在账本里本来就有 ⇒ 投影它必然**多出一条**。
        #   两个生产者都不是"新内容"：
        #     ① 压缩（`compaction/compress.py`）折出来的是 **plugin user/message**，本来就按
        #        "内部物"滤除（下面那条 `kind == "plugin"`）；
        #     ② 工具结果剪枝（`compaction/pruner.py`）折出来的是**同 callId 的 `tool/result`** ——
        #        不滤就会在「行动记录」里看到"原文 + 摘要"**两条**（实测：9000 字原文 + 45 字摘要）。
        #        而屏幕当时显示的是原文（流式那条路），直播与重放必须逐字一致 ⇒ 留原文、滤替换节点。
        #   删除逻辑不受影响：那条路走 surface 目录（`history_ops`），不看本投影。
        surface_op = ev.get("surfaceOp")
        if isinstance(surface_op, dict) and surface_op.get("op") == "replace":
            continue
        t = ev.get("type")
        data = ev.get("data") or {}

        if t == "turn/start":
            # None 容错（10-21）：键存在但值为 null 时 `.get` 的默认值不生效，int(None)
            # 会炸掉**整次投影**（一条坏事件 = 历史全瞎）。回落"顺序 +1"的老语义。
            _tv = data.get("turn")
            turn_no = int(_tv) if _tv is not None else turn_no + 1
            step_no = 0
            # 场合是**回合级**属性（`dialogue_agent._turn` 落账）。老账本无此字段 ⇒ ""。
            cur = {"turn": turn_no, "scene": str(data.get("scene") or ""),
                   "items": [], "closed": False}
            turns.append(cur)
        elif t == "step/start":
            _sv = data.get("step")
            step_no = int(_sv) if _sv is not None else step_no + 1
        elif t == "user/message":
            source = data.get("source") or {}
            kind = source.get("kind")
            if kind == "plugin":
                continue  # L1 runtime context / 压缩 checkpoint：内部物，不进 UI
            if kind == "initiative":
                intent = str(source.get("intent", ""))
                cur["items"].append({
                    "kind": "initiative", "turn": turn_no,
                    "intent": intent,
                    "intent_text": intent_label(intent),   # UI 分隔条用短标签（09-12）
                    "reason": str(source.get("reason", "")),
                })
                continue
            if kind == "peer":
                # 同席 NPC 的话（多人化）：**绝不能落成玩家气泡**。
                # 本函数的兜底分支是"未知 kind → kind:user"，所以不加这一条的话，
                # 张三说的话会显示成玩家自己说的（现有测试大量断言 user 项条数，会一起失真）。
                # 归属靠 `source.speaker`，让 UI 能按说话人渲染；单说话人窗口可自行决定是否过滤。
                text = _texts(data.get("content"))
                if text:
                    cur["items"].append({
                        "kind": "peer", "speaker": str(source.get("speaker", "")),
                        "text": text, "turn": turn_no, "step": step_no, "seq": seq,
                    })
                continue
            if kind == "mail":
                # 别的 NPC 捎来的话（11-12，`agent/mailbox.py`）：同上，**不是玩家说的**。
                # 不加这条就会落进下面的 `kind:user` 兜底 ⇒ 屏幕上变成玩家自己发的一句话
                # （「同席」当初踩的就是这个坑）。归属取 `source.from`（投递时写入的发信人真名）。
                # 不单开一种 UI kind：它就是一条 NPC 侧的静态气泡，与 `peer` 同形
                # —— C# 侧因此**不需要**在 `ReplaceHistory` 的 switch 里加分支。
                text = _texts(data.get("content"))
                if text:
                    cur["items"].append({
                        "kind": "peer", "speaker": str(source.get("from", "")),
                        "speaker_id": str(source.get("from_id", "")),
                        "mail": True,
                        "text": text, "turn": turn_no, "step": step_no, "seq": seq,
                    })
                continue
            text = _texts(data.get("content"))
            if text:
                cur["items"].append({"kind": "user", "text": text, "turn": turn_no, "step": step_no, "seq": seq})
        elif t == "assistant/message":
            # 思考内容拆分：reasoning 存在则直接用；否则兜底拆 content 内嵌 <think> 标签。
            # think 独立成项（kind=think，UI 折叠展示），正文项只给 body
            data_reasoning = str(data.get("reasoning") or "")
            for b in ((data.get("message") or {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    txt = str(b["text"])
                    if data_reasoning:
                        _, body = _think.split_think(txt)   # 兜底剥标签（通常已干净）
                    else:
                        data_reasoning, body = _think.split_think(txt)
                    if data_reasoning:
                        cur["items"].append({"kind": "think", "text": data_reasoning, "turn": turn_no, "step": step_no, "seq": seq})
                    if body:
                        cur["items"].append({"kind": "assistant", "text": body, "turn": turn_no, "step": step_no, "seq": seq})
        elif t == "tool/call":
            try:
                args = json.loads(data.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {"raw": args}
            except Exception:
                args = {"raw": str(data.get("arguments", ""))}
            cur["items"].append({
                "kind": "tool_call", "turn": turn_no, "step": step_no,
                "call_id": str(data.get("callId", "")),
                "name": str(data.get("name", "")),
                "args": args,
                "seq": seq,
            })
        elif t == "tool/result":
            for b in ((data.get("message") or {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool-result":
                    cur["items"].append({
                        "kind": "tool_result", "turn": turn_no, "step": step_no,
                        "call_id": str(b.get("toolCallId", "")),
                        "text": _texts(b.get("content")),
                        "is_error": bool(b.get("isError", False)),
                        "seq": seq,
                    })
        elif t == "turn/end":
            reason = data.get("reason") or {}
            if reason.get("kind") == "error":
                cur["items"].append({
                    "kind": "turn_error", "turn": turn_no,
                    "text": str(reason.get("error") or reason.get("reason") or "回合异常"),
                })
            cur["closed"] = True

    closed = [x for x in turns if x["closed"]]
    if scene is not None:
        # ★ 过滤在**截断之前**：max_turns 的语义是"该场合内最近 N 个回合"。
        closed = [x for x in closed if x["scene"] == str(scene)]

    # ── before_seq：游标过滤（11-21 分页）─────────────────────────────────
    # 放在 scene 过滤之后、max_turns 截断之前：口径与"该场合内最近 N 个"一致。
    _before = 0
    try:
        _before = int(before_seq) if before_seq is not None else 0
    except Exception:
        _before = 0
    _before_turn = 0
    try:
        _before_turn = int(before_turn) if before_turn is not None else 0
    except Exception:
        _before_turn = 0
    if _before > 0 or _before_turn > 0:
        closed = [x for x in closed if _turn_before_cursor(x, _before, _before_turn)]

    picked = closed if unlimited else closed[-_n:]

    # ── 游标：本批最老回合 ⇒ 下一页的起点（11-21）────────────────────────
    # 返回 `next_before_seq` 与 `next_before_turn` **两个**，缺一不可：
    #   · `next_before_seq`  —— 账本里稳定的位置锚（删改不漂移，这是选 seq 当游标的原因）；
    #   · `next_before_turn` —— 回合号兜底，专门对付**整个回合没有 seq** 的情形
    #     （姜萌账本 43 回合里有 2 个：`initiative` / `turn_error` 这类合成内容）。
    #     只用 seq 的话那些回合会被判成"永远在游标之前" ⇒ **每页重复返回**
    #     （实测：第 1 页 `[38..58]`、第 2 页 `[18..42]`，38/42 反复出现）。
    #
    # 0 = 没有更早的了（C# 据此撤掉「加载更早」入口）。
    next_before = 0
    next_before_turn = 0
    # ★ 只有"本批之前确实还有更早的回合"时才给游标 ★
    #   判据：本批最老的那个回合在**过滤后的全集** `closed` 里不是第一个。
    #   直接看 `picked[0]` 就无条件给游标是错的（11-21 测试抓到）：
    #   一次取完全部时 `picked` 就是 `closed`，此时还报一个非零游标 ⇒
    #   C# 会一直显示「加载更早」，点下去返回空列表（看着能点、点了没反应）。
    if picked and closed and picked[0] is not closed[0]:
        oldest = picked[0]
        next_before_turn = int(oldest.get("turn") or 0)
        next_before = _turn_max_seq(oldest)
        if next_before <= 0:
            # 最老回合没有 seq：用它之前最近一个**有 seq** 回合的最大 seq 当锚
            # （`picked` 按时间正序，往前找不到就说明这是最早的一批）
            for x in reversed(picked):
                m = _turn_max_seq(x)
                if m > 0:
                    next_before = m
                    break

    items: List[Dict[str, Any]] = []
    for x in picked:
        for it in x["items"]:
            # 场合在**唯一的收口处**统一补上：item 是这里现造的 dict（不是账本对象），
            # 集中一处比在 8 个 append 点各写一遍更不容易漏。
            # ★ 压缩纪要是**某个 NPC 的记忆**，不是某一场对话的内容 ⇒ 恒按单聊场合标。
            #   （它落在群聊回合里时仍会被上面的回合级过滤带走 —— 已知盲区，见模块 docstring。）
            it["scene"] = "" if it.get("kind") == "compaction" else x["scene"]
            items.append(it)
    return {"items": items, "complete_turns": len(closed),
            "next_before_seq": next_before, "next_before_turn": next_before_turn}
