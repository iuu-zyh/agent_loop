"""mailbox — NPC 之间的「捎话」：A 留一句话进 B 的信箱，B 下次与玩家说话时才读到

## 为什么是「往对方账本里追加」，而不是一张待办表

用户的要求（11-12）：「a 调用这个工具，就是给 b 传一句话，但不是让 b 去立即互动……
而是只有当玩家和 b 对话的时候才会触发，就是每个 npc 对应的 agent 新增一个信箱」，
并且「如果 npc 对应的账本没有的话，那完全可以懒创建」。

所以投递的目标就是 **B 自己的账本**：那句话必须成为 B 的**记忆**（下次压缩、下次重开窗、
下次读档都还在），而不是 harness 进程里的一份临时清单 —— 后者一重启就没了，而 B 会表现得
「从来没收到过」，且**没有任何报错**（`ChatHub._pending_drama` 之所以带 TTL，也正是因为
它活在进程里；信箱要的是相反的东西）。

## 为什么落在 `inbox.next-turn`，而不是直接 append 到 surface

`Inbox` 是「还没轮到处理的消息」的唯一容器，而它的内容随 `agent/inbox/spliced` 事件
**落进 B 的账本**（见 `session/inbox.py`）。于是三条语义同时成立：

  · 关窗（`AgentLoop.dispose` = 冻结）→ 信箱连着账本留在内存，重开窗原样还在；
  · 存档（`flush_all`）→ splice 事件随账本落盘，读档时 `Inbox.__init__` 重放还原；
  · 玩家**还没跟 B 说过话** → 它一直待在筐里，B 的 surface 里一个字都没有
    （= 用户要的「不会立即互动」）。

等到玩家真的跟 B 开口，`DialogueAgent._turn` 第 1 步 `inbox.claim("next-turn", …)` 把它
领出来、以 `user/message` + `surfaceOp: append` 落进 surface —— 那一刻 B 才「读到」。
这一段**不需要任何新代码**，它就是既有的领信/落面路径；本模块只负责「放进去」。

## ★ 一处改动要同步到四个消费方（少一处就是静默错误）

`source.kind == "mail"` 是这句话的唯一身份标记。wire 格式里它就是一个 `role:user` 的消息，
所以**每一个**按 role 猜「谁在说话」的地方都必须认识它：

  · `DialogueAgent._apply_speaker_labels` —— 不认识就当玩家说的，B 会对着**玩家**应下
    一件其实是别人托付的事；
  · `session/history.py`（UI 投影）—— 不认识就落进兜底的 `kind:user` 分支，屏幕上变成
    **玩家自己**发的一句话（`peer` 当初踩的就是这个坑）；
  · `compaction/compress.py`（压缩材料）—— 不认识就写成 `玩家：…`，而纪要是永久节点，
    B 会**一直**记成「玩家跟我说过这句话」，且越传越歪；
  · `prompts/compaction/default.md` + `compress` 的兜底常量 —— 标记有了却没有落点分节，
    摘要器仍会自己找地方塞（「（同席）」那条留下的教训）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import log_setup

log = log_setup.get_logger(__name__)

#: `source.kind`：这句话是**别的 NPC 托人捎来的**（不是玩家说的，也不是在场者说的）。
#: 取值一旦改动，上面文件头列的四个消费方要一起改（有契约测试钉住）。
MAIL_KIND = "mail"

#: 一句话的字数上限。**C# 侧（`RelationTool.SocialRelation` 的 `MailMaxChars`）是同名同值**：
#: 真正生效的是它那一刀（Python 用的是回传的那份），这里只在直调 `deliver()` 时兜底。
MAIL_MAX_CHARS = 200

#: 同一个发信人对同一个收信人、**还没被读到**的话最多留几条。
#: 为什么要有：模型完全可能一口气连发十条（每次调用都"成功"），而 B 要十次对话才读得完
#: —— 那不是捎话，是刷屏，而且会把 L1 近况与上下文挤满。
MAIL_MAX_PENDING = 3


def pending_from(agent: Any, from_id: str) -> List[Dict[str, Any]]:
    """该 agent 信箱里、还没被读到的、来自 `from_id` 的那些话。"""
    out: List[Dict[str, Any]] = []
    try:
        queue = agent.inbox.next_turn
    except Exception:
        return out
    for m in queue:
        if not isinstance(m, dict):
            continue
        src = m.get("source")
        if not isinstance(src, dict):
            continue
        if src.get("kind") != MAIL_KIND:
            continue
        if str(src.get("from") or "") == from_id:
            out.append(m)
    return out


def deliver(loop: Any, make_msg, *, from_id: str, to_id: str, text: str) -> Optional[str]:
    """把一句话投进 `to_id` 的信箱。

    返回 `None` = 投到了；返回字符串 = **人话错误**（没投）。调用方
    （`DialogueAgent._settle_mail`）会把带错误的结果改判成失败帧 —— 绝不静默成功：
    模型看到「已捎到」而其实没投，它下一句就会据此编出「他答应了」的剧情，
    而 B 那边一个字都没有。

    `make_msg(text) -> Dict` 由调用方给：**消息体归 agent 层造**（那里才有
    `create_user_message`），本模块只管"投到哪只筐、投没投进去"。这样也避免了
    `agent/mailbox.py` 反向 import `dialogue_agent`（同包反向依赖，且会成环）。

    收信人的账本按**三级**取（与开窗激活同一条路，见 `room_flow._memory_agent`）：
    活体 / 冻结舱（`get_any`）→ 磁盘（`resume_if_on_disk`）→ **懒创建**（`create`）。
    用户明确要求懒创建：「和用户和新的 npc 对话时候才创建账本一样」。
    """
    from_id = str(from_id or "").strip()
    to_id = str(to_id or "").strip()
    text = str(text or "").strip()
    if not from_id or not to_id:
        return "捎话缺少发信人或收信人，这句话没有投递。"
    if from_id == to_id:
        return "不能给自己捎话 —— 有话直接说即可。"
    if not text:
        return "捎话的内容是空的（message 没写），这句话没有投递。"
    if len(text) > MAIL_MAX_CHARS:
        text = text[:MAIL_MAX_CHARS]
    try:
        agent = loop.get_any(to_id)
        if agent is None:
            resume = getattr(loop, "resume_if_on_disk", None)
            agent = resume(to_id) if callable(resume) else None
        if agent is None:
            agent = loop.create(to_id)
    except Exception as e:                     # 磁盘账本读不出来：宁可报错，不许静默丢话
        log.warning("捎话：取「%s」的账本失败（本句话没有投递）", to_id, exc_info=True)
        return f"取「{to_id}」的账本失败，这句话没有投递（{e}）。"
    if agent is None:
        return f"取不到「{to_id}」的账本，这句话没有投递。"
    pending = pending_from(agent, from_id)
    if len(pending) >= MAIL_MAX_PENDING:
        return (f"你已经捎了 {len(pending)} 句话给{to_id}、他还没读到 —— "
                f"等他这几回与玩家说过话之后再说，别一次塞太多。")
    # 消息体由**调用方**造（`DialogueAgent`）后传进来：本模块只认 `source.kind` 与筐。
    # 这样 `agent/mailbox.py` 不需要 import `dialogue_agent`（那是同包**反向**依赖，
    # `test_no_stale_intra_package_relative_imports` 明令禁止，而且会成环）。
    agent.inbox.splice("next-turn", len(agent.inbox.next_turn), 0, [make_msg(text)])
    # ══ 探针（11-15 联调，验完删）══ 前缀统一 `[MAIL-PROBE]`，便于与 C# 侧一条 grep 串起来。
    # 这条是"投递成功"的现场证据；配对的 C# 那条是"优先队列拿到了"。两条都在 = 链路通。
    log.info("[MAIL-PROBE] 捎话投递：%s → %s（他未读第 %d 条）｜筐内共 %d 条｜正文=%s",
             from_id, to_id, len(pending) + 1, len(agent.inbox.next_turn), text[:60])
    return None


# 注：这里曾经有一个 `MAIL_PRIORITY_BONUS = 1000` 的加成常量（11-13 复核时删掉）。
# 那是"把加成加进 `Row.Intim`"那版设计的残留 —— 而那版设计**被否掉了**：
# 它与 `Row.Intim` 是同一个字段，`PickIntent`（按好感选意图池）和 `BuildReason`
# （按好感选措辞）都会读到假值，于是仇人会被排进正向意图池、话术变成"念及多日情谊"，
# 而两处都不报错。真正的实现是 C# 的 `InitiativeSortCore.Compare`：**两个独立排序键**
# （先 HasMail、后 Intim 降序），好感一格都不动。
# 留着这个常量只有一个后果：让人以为优先队列是"加成式"的，从而在 Python 侧做出
# 一个不存在的开关。判据在 C#，Python 只负责出名单 —— 见 `pending_map`。


def pending_senders(agent: Any) -> List[str]:
    """该 agent 信箱里**所有**未读捎话的发信人（去重、保持投递顺序）。"""
    out: List[str] = []
    try:
        queue = agent.inbox.next_turn
    except Exception:
        return out
    for m in queue:
        if not isinstance(m, dict):
            continue
        src = m.get("source")
        if not isinstance(src, dict) or src.get("kind") != MAIL_KIND:
            continue
        who = str(src.get("from") or "").strip()
        if who and who not in out:
            out.append(who)
    return out


def has_pending(agent: Any) -> bool:
    """该 agent 信箱里有没有**尚未读到**的捎话。"""
    try:
        queue = agent.inbox.next_turn
    except Exception:
        return False
    return any(isinstance(m, dict) and isinstance(m.get("source"), dict)
               and m["source"].get("kind") == MAIL_KIND for m in queue)


def pending_map(loop: Any) -> Dict[str, List[str]]:
    """全量快照：`{收信人 npc_id: [发信人真名, …]}`，只含**有未读捎话**的人。

    这是 C# 侧"优先队列"的唯一数据源（`list_mail_pending` RPC）。
    为什么由 Python 出这份名单而不是 C# 自己记：信箱内容是 **Python 账本**的一部分
    （`inbox.next-turn` + `spliced` 事件），C# 既读不到、也不该复制一份 ——
    复制体在 Python 重启 / 读档 / 关窗重开之后必然与真相分家，而那种分家**不会报错**，
    只会表现为"明明有信却从不优先"。

    扫描面只取**活体 + 冻结舱**：冻结舱 = 关窗未固化，它的信箱仍然是真实的未读状态
    （用户关掉窗口、明天再开，那封信还在）。磁盘上的账本**不在这里读** ——
    读取要反序列化整份 jsonl，而日节拍每换日都会问一次；那些人的 `Inbox` 只在
    真正开窗/投递时才会被唤醒，届时自然进这份名单。
    """
    out: Dict[str, List[str]] = {}
    # 直接遍历两个名册容器（活体 `store` + 冻结舱 `_frozen`）比逐个 `get` 便宜，
    # 而且能一次覆盖"有回合在跑"与"刚关窗"两种状态。
    seen: Dict[str, Any] = {}
    for attr in ("store", "_frozen"):
        book = getattr(loop, attr, None)
        if isinstance(book, dict):
            for npc_id, agent in list(book.items()):
                seen.setdefault(str(npc_id), agent)
    for npc_id, agent in seen.items():
        senders = pending_senders(agent)
        if senders:
            out[str(npc_id)] = senders
    return out
