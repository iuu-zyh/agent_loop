"""room_history — 群窗投影。

与 `history.project_ui_history` 的分工：
- `project_ui_history(session, scene=…)`：从**一个 NPC 的 session** 投影出"某个场合里
  这个 NPC 经历了什么"。1:1 窗用它（`scene=""`).
- **`project_room_history(room)`**：从 **`Room.log`** 投影出"这个群到底说过什么"。

为什么群窗不能读某个成员的 session（三条，都是实测事实）：
  ① 玩家自己没有 session —— 没有哪个成员能替玩家记全；
  ② 成员的会话会被压缩、被删、或**是后加入的**（入群前的话他根本没有）；
  ③ 同一条房间发言在不同成员账本里的 **turn 号不同**（实测：林婉清是 turn 2、
     李四是 turn 1）⇒ 跨成员不可比，删除/回放/去重键只能用 `Room` 坐标（`uid`）。

一句话分工：
    `Room.log` 回答「**当时到底说了什么**」—— 客观、不可变、全群一份。
    `session`  回答「**这个 NPC 现在记得什么**」—— 主观、会被压缩和删除、每人一份。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .room import PLAYER, Room


def _kind(speaker: str) -> str:
    """UI 侧的气泡类别 —— **刻意复用 `history.project_ui_history` 的既有词表**。

    玩家的发言是 `"user"`（不是 `"player"`）、同席 NPC 是 `"peer"`。
    为什么不另起一套：C# 的 `ChatWindow.ReplaceHistory` 是**共用的**渲染入口，
    它的 switch 里已有 `user`（玩家气泡）与 `peer`（同席气泡）两条分支；
    若这里发 `"player"`，那条会落进 switch 的**默认空档**（没有 default），
    表现是"群窗开了、NPC 的话都在，唯独玩家自己说的全不见了" —— 而且不报错。

    （`docs/archive/2026-09-17-群聊编排/设计定稿.md` §6.1 的示例里写的是 `"kind":"player"`，是笔误，已回写修正。）
    """
    return "peer" if speaker != PLAYER else "user"


def project_room_history(room: Room, limit: int = 0, before_seq: int = 0) -> Dict[str, Any]:
    """把一个群投影成群窗要的时间线。`limit > 0` 时只给**最后** limit 条。

    `before_seq > 0`（11-21 分页）：只取该 seq **之前**的发言，再按 limit 截最后 N 条。
    返回里的 `next_before_seq` = 本批最老一条的 seq，供客户端下一次翻页；0 = 已到顶。

    **群聊只需要 seq 一个锚**（不同于单聊的双锚）：群发言是平铺的 `Utterance`，
    每一条都带 `seq`，没有"整回合无 seq"的合成内容 —— 那个问题只存在于单聊投影
    （`turn_error` / `initiative` 之类的回合成条目）。

    返回形状与 `project_ui_history` 保持同族（`items` + 元信息），便于 C# 复用解析路径。
    不返回 turn 号 —— 群窗不绑会话回合（§9.2 已定：v1 群窗不提供删除）。

    **墓标跳过**（`Room.deleted_seqs`，见 §9.2）：被删的发言对群窗、重开回放
    （`open_room` 回执走的就是本函数）与 `get_room_history` 一律不可见 —— 与 `delta_for`
    的跳过是同一份数据的两面：**删了就该像没说过**（除了"它确实占用过 seq"这件事，
    而 seq 空洞对外不可见：`uid` 是显式的、序号不用连续）。

    **旁白发成 `tool_result` 条目**（`Utterance.narration`）：第三人称叙述行（如切磋那条）
    不是谁说的话 —— 画成气泡就是把它安到某个人头上，而行里还夹着**玩家的原话**（09-22 用户报）。
    `ReplaceHistory` 里 `tool_result` 已经是「**行动记录**」折叠行（与真实工具结果同一条渲染路径），
    于是重开窗看到的就是"工具返回结果"那个形态，与直播端 `AppendActionRow` 一致。
    它照常留在 `Room.log`、照常经 `delta_for` 投给成员 —— 那条行的用途本来就是
    "别的成员别以为这一轮卡死了"，不在成员可见性上打折扣。
    """
    items: List[Dict[str, Any]] = [
        {
            # 旁白复用 `tool_result` 这个既有 kind（→「行动记录」行）；其余按说话人分气泡类别
            "kind": "tool_result" if u.narration else _kind(u.speaker),
            "speaker": u.speaker,
            # 说话人的**精确 unitID**：C# 群窗每一行据此直接取立绘，**不走按名解析**。
            # 为什么必须随条目带（而不是让 C# 查那张房间级身份表）：重开回放是"没人显示
            # 这个群"期间攒下的时间线，回放那一刻 C# 手上未必还有这个群的身份；
            # 而按名解析在重名时会画上别人的脸，且**不报错、不缺字段**。
            # 玩家/查不到者恒为 ""：C# 那边玩家行零歧义、未知者宁可不画脸也不画错人。
            "speaker_id": "" if u.speaker == PLAYER else room.unit_id_of(u.speaker),
            "text": u.text,
            "seq": u.seq,
            "uid": u.uid,
            "time": u.time,
        }
        for u in room.visible_log()
    ]
    # 游标：只保留 before_seq 之前的发言（放在 limit 截断**之前** ——
    # 与单聊 `project_ui_history` 的口径一致：都是"该范围里最近 N 条"）
    try:
        _before = int(before_seq) if before_seq else 0
    except Exception:
        _before = 0
    if _before > 0:
        items = [it for it in items if isinstance(it.get("seq"), int) and it["seq"] < _before]

    if limit and limit > 0:
        items = items[-int(limit):]

    # 下一页的锚 = 本批最老一条的 seq；0 = 没有更早的了。
    # ⚠ 必须按**本批实际返回的** items 取，不能按过滤后的全集：后者会让客户端
    #   以为还有更早的而反复翻到同一页（单聊那边踩过同型的坑）。
    next_before = 0
    if items:
        first_seq = items[0].get("seq")
        if isinstance(first_seq, int):
            next_before = first_seq

    return {
        "next_before_seq": next_before,
        "room_id": room.id,
        "title": room.title,
        "members": list(room.members),
        # 成员的**精确 unitID**（可能只覆盖一部分）。**这是房间级的事实，不是全局登记**：
        # C# 侧只把它当"这个群有谁、各是谁"的随行数据用（踢人候选/预热），
        # **绝不写进全局表** —— 那会把单聊登记过的同名者静默覆盖掉（见 ChatPresenter
        # 里 `PinMemberUnitIds` 被删除的原因）。
        # 缺项是正常的（老群 / 建群时 C# 手上没有那个人的身份），消费侧必须容错。
        "member_unit_ids": {m: room.member_unit_ids[m]
                            for m in room.members if m in room.member_unit_ids},
        "items": items,
        "last_seq": room.last_seq,
        "complete_turns": 0,      # 群窗不绑会话回合；字段留形状，恒 0
    }
