"""Room — 群聊的「交谈身份」+ 发言流水。

分层分权（与 session.py / persistence.py 同一套路）：
- **本模块是纯数据**：`Room` / `Utterance` 只描述「这个群是谁、有谁、说过什么」。
  不持有 agent、不碰 LLM、不做并发控制、不 import 任何内部模块。
  于是它可以被完整 `json.dumps`，也可以脱离运行时构造 —— 这是它该有的复杂度上限。
- **IO 在 `persistence.py`**（`save_room` / `load_room` / 名录索引），与
  `save_session` / `load_session` 同居一处，不在这里再开一套文件读写。
- **编排在 `room_flow.py`**（投递、调度、跑轮、进出群）。

三条否定的理由（见 `docs/archive/2026-09-17-群聊编排/设计定稿.md` §1.2）：
- **不是地点**：坐标不唯一（同格上可能有好几场聚谈），地名会撞车；
- **不是另一个 agent**：它不跑 LLM、没有 session、没有回合；
- **不是记忆的载体**：模型**从不读** `Room.log` —— NPC 的记忆在各自的 `session` 里。

两条读路径完全独立，只在「一次发言」这个写入事件上汇合：
    群窗回放 / 投递计算  ← Room.log（全群一份，客观，不可变）
    模型的大脑           ← agent.session（每人一份，主观，会被压缩和删除）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# 玩家的「说话人」标识。npc_id 是中文名，真名由 C# 在 L1 里给，这里只做逻辑身份。
PLAYER = "player"


@dataclass
class Utterance:
    """一条发言。`seq` 是**房间内**自增序号（从 1 起），增量投递与游标都用它。

    `uid = f"{room_id}#{seq}"` 是**全局唯一**标识 —— 会话里的 turn 号是会话局部的
    （实测：同一条房间发言在林婉清账本里是 turn 2、在李四账本里是 turn 1，
    见 `scripts/dev/probe_group_history.py` ③），所以跨成员定位只能靠 uid。
    现在就把 uid 存进投递的 `source`，将来做「群窗删除一条」才不用改数据。

    `text` 存**原文**，不带 `名字：` 前缀 —— 前缀由 `_apply_speaker_labels` 在发给
    模型时按 `source.kind` 现加。双重前缀「李四：李四：…」就是这么来的（不变量 I4）。
    """

    seq: int
    speaker: str
    text: str
    time: int = 0
    uid: str = ""
    mentions: Optional[List[str]] = None
    """被 @ 点名的成员（**None = 没点名，全员收**；非空 = 只有这些人的账本收到）。

    ★ 与 `narration` 的区别 ★：`narration` 管**怎么画**（气泡 vs 行动记录行），
    本字段管**谁能听到**。群窗（`visible_log` / `project_room_history`）**不看它** ——
    群窗是"这个群发生过什么"的客观记录，玩家 @ 了谁不影响群窗显示那一条。
    只有投递（`delta_for`）看它。

    为什么 None 与 [] 必须是两个值：见 `room/mention.py` 文件头。`None` = 没 @
    （全员，既有行为）；`[]` 理论上不该出现（点了一个都不存在的人时，编排层会
    退回全员并在群里给玩家一句提示，见 `room_flow.run_message`）。

    落盘：只有非空时才写这个键 —— 老流水/普通发言保持逐字节原样（同 `narration`）。"""

    pure_ping: bool = False
    """纯点名：玩家**只写了 `@名字`**，没有任何正文。

    两个消费方对它的处理**刻意不同**（这正是它必须是个字段、而不是"正文为空"的推断）：
      · 群窗 / 历史回放：显示 `text`（= 玩家真正打出的 `@张三`）；
      · 投递给模型：换成 `PURE_PING_TEXT` 一句促发语（见 `RoomFlow.deliver`）。
    为什么不能靠"`text` 为空"来判断：`Room.log` 里存的是原文（含 @名字），不是空串 ——
    空串会让群窗显示一条空气泡，比显示促发语更糟。

    落盘只在为真时写这个键（同 `narration` / `mentions`）。"""

    narration: bool = False
    """第三人称**叙述行**（不是谁开口说的话）：进 `Room.log`（成员经 `deliver` 照常读到），
    进群窗，但**画「行动记录」行、不画气泡**。

    为什么必须分这两类（09-22 用户报）：`（与唐炎切磋了一场 —— 唐炎：「厉害，我输了。」…）`
    这类行走气泡通道会被画成"这名 NPC 说了这句话" —— 那行里还夹着玩家的原话，
    挂在对方名下双重不对。用户拍板的形态是**工具返回结果那种行**。
    两端都靠这个标记：直播帧带 `narration:true`（`ChatPresenter.ApplyRoomSay` 转
    `ChatWindow.AppendActionRow`），重开窗时 `project_room_history` 把它发成
    `tool_result` 条目（`ReplaceHistory` 的既有「行动记录」分支）—— 所以**重开窗也还在**。
    它存在的意义是**别的成员别以为这一轮卡死了**（`_run_round` 串行等一整场战斗）。"""

    def __post_init__(self):
        if not self.uid:
            # 兜底。`Utterance` 不知道自己的房间，所以这里**造不出**规范的 `{room_id}#{seq}`。
            # 正常路径（`Room.say` / `from_dict`）都显式给 uid，走不到这里。
            # 故意用一个**一眼不像 uid** 的形状（不是 `张三#2` 那种看着很合理的串）——
            # uid 的用途是当"跨成员删除"的锚点，一个看似合法却并不全局唯一的串
            # 比一个明显残缺的串危险得多。
            self.uid = f"seq:{self.seq}"

    def to_dict(self) -> Dict[str, Any]:
        return {"seq": self.seq, "uid": self.uid, "speaker": self.speaker,
                "text": self.text, "time": self.time,
                # 只在真为叙述行时写这个键：老文件/普通发言保持逐字节原样
                **({"narration": True} if self.narration else {}),
                # 同理：没 @ 就不写。落盘形状对老存档零影响，新存档只在真发生 @ 时多一个键。
                # ★ 判据是 `is not None` 而不是真值 ★：`[]` 是**有语义**的（谁都不给），
                # 不落盘的话重启后它变回 None = 全员广播（见 `Utterance.mentions`）。
                **({"mentions": list(self.mentions)} if self.mentions is not None else {}),
                # 纯点名：不落盘的话重启后模型会收到「@张三」四个字符而不是促发语
                **({"pure_ping": True} if self.pure_ping else {})}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Utterance":
        _m = d.get("mentions")
        return cls(seq=int(d.get("seq") or 0),
                   speaker=str(d.get("speaker") or ""),
                   text=str(d.get("text") or ""),
                   time=int(d.get("time") or 0),
                   uid=str(d.get("uid") or ""),
                   # 必须读回来：不然重启后这条会被投给**全群**（@ 的私密性丢失）
                   # 同上：`[]` 必须读回成 `[]`（不是 None），否则重启后广播给全群
                   mentions=[str(x) for x in _m] if isinstance(_m, list) else None,
                   # 必须读回来：不然重启后群窗重放会把它当普通发言画成气泡
                   narration=bool(d.get("narration")),
                   # 同上：不读回来模型会收到「@张三」四个字符
                   pure_ping=bool(d.get("pure_ping")))


@dataclass
class Room:
    """一个群。`id` 是**稳定身份**：拉人踢人都不变（微信群语义）。

    为什么不从成员派生 id（曾这么设计、被推翻）：一拉人 key 就变 ⇒
    历史在 UI 上断成两段，老段**没有任何窗口入口**（孤儿）。
    所以 `scene`（= 本 id）必须是独立稳定字段，成员只是它的属性。

    `members` 只放 NPC；**玩家恒在场**，不入列（`speaker == PLAYER` 表示玩家）。

    刻意不放在这里的东西：
    - `cursors`：放各 agent 的 session 里（`room/cursor` 注记）。游标在 Room、
      记忆在 session ⇒ 两次落盘之间出错会**永久丢几条发言**（游标说听过了，
      投递不会再来）。放进 session 则「记忆与游标在同一个文件、同一次 flush」，
      不可能不一致（不变量 I3）。
    - `round_pointer` / `spoken_count`：调度状态是**运行期**的，挂起的轮次不值得
      持久化 —— 落盘只落「谁在群里 + 说过什么」。
    - 成员的关系/好感快照：那是 L1 的事（`raw.others[]`），每次取数现算。
    """

    id: str
    title: str = ""
    members: List[str] = field(default_factory=list)
    log: List[Utterance] = field(default_factory=list)
    created: int = 0
    # 入群位置：npc_id → 「他进群时房内已说到第几条」。**这不是游标。**
    #
    # 为什么必须有它：投递起点 = `cursor if cursor is not None else joined[me]`，
    # 而游标是**懒写**的（要投递时才落 `room/cursor`）。若没有入群位置，
    # 「建群的初始成员，还没轮到过」与「中途加入的新人」在数据上完全一样（都无游标），
    # 只能二选一 —— 要么初始成员永远收不到第一句（实测：第一句投给谁都是空），
    # 要么新人一进来就被灌入群前的全部历史。两个都不能要。
    #
    # 权威进度**仍然只在 session 里**（`room/cursor`），所以不变量 I3 不受影响：
    # 入群位置只决定「一个还没有游标的人该从哪开始」，一旦投过一次，
    # 后续完全由 session 里的游标说了算 —— 它丢了/对不上，最坏是首次投递起点错，
    # 不会造成「说听过了但记忆里没有」。
    joined: Dict[str, int] = field(default_factory=dict)
    # 成员的**精确 unitID**：npc_id → unitID。**可以缺项**（老群、或建群时 C# 手上没有
    # 那个人的精确身份）—— 缺了不影响任何语义，只是 C# 侧渲染他的立绘时要按名全图扫一遍，
    # 而**重名时那一次扫描可能扫到另一个人**（`UnitLookup` 文件头记的那次真机事故：
    # 不报错、不缺字段，只是把别人的档案端给模型）。
    #
    # 为什么存在房间里、而不是像手动好友那样另开一个文件：群成员名单的**唯一真相就在本类**
    # （`members`），把身份与名单放在同一次 `to_meta()` 落盘里，两者不可能不一致 ——
    # 分两个文件存就会出现"名单里有他、身份表里没有（或指向别人）"的中间态。
    #
    # 值从哪来：C# 在**建群 / 拉人确认那一刻**手里握着 `WorldUnitBase`（通讯录关系层条目
    # 就是按 unitID 精确查出来的），随 `open_room` / `add_room_members` 上行。C# 侧收到
    # `open_room` 回包后又把它喂回 `UnitLookup.Pin` —— 于是本次会话内所有按名查这些成员
    # 的地方（L1 自身段、立绘取图）都变成零歧义。
    #
    # ★ 它是**事实的副本**，不是权威：unitID 会在换存档后整体失效。所以消费侧（C# 的
    #   `UnitLookup.Pin`）**必须带失效自愈** —— 查不到就摘掉登记、回落按名。那份自愈已经在
    #   `ResolveInner` 的 ⓪ 分支里了（`_pinned.Remove` + 回落），这里不必重复实现。
    member_unit_ids: Dict[str, str] = field(default_factory=dict)
    # ★ 本群**群聊期间**累计的四桶用量（09-18）★：`{inputTokens(未缓存), cacheReadTokens,
    # cacheWriteTokens, outputTokens, turns, steps}` —— 由 `RoomFlow` 在每个成员的群内回合
    # **前后取差**归集（成员 agent 同时服务单聊，累计量是两条线之和，只有差才是群内的）。
    #
    # 为什么记在群里并落盘：底部统计行在群窗里要报"**本群期间**总共花了多少"，
    # 而"各自的上下文/占用率"在群聊里没有可加语义（每人一份账本）—— 能诚实相加的只有花费。
    # 落盘理由与 `last_seq` 同：读档后这一行必须还在（否则玩家一读档就以为记录被清了）。
    # 与 `deleted_seqs` 的差别：删消息**不退钱**（花费是真实发生的），所以这里不随删除回滚。
    group_usage: Dict[str, int] = field(default_factory=dict)

    # 名录（`index.json` / `<id>.jsonl` 首行）里记的流水末尾。**只在 `log` 未加载时兜底。**
    #
    # 为什么必须有它：读档走的是**懒加载** —— `AgentLoop.load_rooms()` 只读名录
    # （`from_meta(meta, log=[])`），流水等真要看某个群时才补读（`room_flow._require`）。
    # 而 `last_seq` 原先是从 `log` 现算的，于是**读档后每个群的 `last_seq` 都假报 0**：
    #   · `list_rooms`（群聊页/通讯录的群列表）每行显示「0 条」；
    #   · `flush_all` 会把 `to_meta()` 的 0 **写回 `index.json`**，把磁盘上的真值覆盖掉；
    #   · `next_seq` 随之从 1 重发，与磁盘旧流水**撞号**（静默数据损坏）。
    # 写路径本来由 `_require()` 的懒加载兜住（见其 docstring 的 ★），但**纯读的
    # `list_rooms` 绕过了那道守卫** —— 而它恰好是群列表 UI 的数据源，所以必须在数据层修。
    #
    # 兜底方向是安全的：`log` 一旦非空就以 `log` 为准（流水永远比名录权威）。
    meta_last_seq: int = 0
    # 被群窗删除的发言 seq（**墓标**，见 `docs/archive/2026-09-17-群聊编排/设计定稿.md` §9.2）。
    #
    # `Room.log` 保持**只增不改**：删除不是把那条从 `log` 里抠掉，而是记一个"此后谁都不许再提它"的
    # 墓碑。两个消费方都跳过它：
    #   · `delta_for` ⇒ **还没听到的人永不再收到**（已听到的由 fan-out 到各成员 session 剔除）；
    #   · `project_room_history` ⇒ 群窗与重开回放一致（不会"删了又出现"）。
    # 随 `to_meta()` 落盘 —— rooms 本来就是**全量重写**，所以它天然持久化，不需要新机制。
    #
    # 为什么不做成"从 log 里 pop"：seq 是投递游标与 uid 的坐标（`{room_id}#{seq}`），
    # 抠掉中间一条会让 `seq > cursor` 的比较与 uid 的解析全部错位；而"只增不改"是
    # `Room.log` 的既有硬性质（见 `say` 的 docstring）。墓标是唯一不破坏它的做法。
    deleted_seqs: set = field(default_factory=set)

    # ---------- 读 ----------

    @property
    def last_seq(self) -> int:
        """最新一条发言的 seq（空群 = 0）。游标与增量都以此为坐标。

        `log` 非空 ⇒ 取 `log` 末尾（权威）；`log` 为空 ⇒ 取名录里记的
        `meta_last_seq`（读档懒加载的中间态，见字段处注释）。
        """
        return self.log[-1].seq if self.log else int(self.meta_last_seq or 0)

    @property
    def next_seq(self) -> int:
        return self.last_seq + 1

    def has(self, npc_id: str) -> bool:
        return npc_id in self.members

    def delta_for(self, me: str, cursor: Optional[int]) -> List[Utterance]:
        """投给我的增量：`seq > 起点` 且 **speaker ≠ 我**。

        起点 = 我在这个房间的**游标**；还没有游标时退回我的**入群位置**：
        - 建群就在的初始成员：入群位置 = 0 ⇒ 收到全部（他要经历这一整场）；
        - 中途加入的新人：入群位置 = 加入时的 `last_seq` ⇒ **看不到入群前的话**
          （与微信一致，也让"不限人数"的长期会话不会一进来就被灌爆）。
        - 两个都没有（老存档、手工构造的群）：退回 `last_seq` —— 保守方向是
          "少听"而不是"被灌爆"，且这种情况只可能出现在数据缺失时。

        **自己的话不投给自己** —— 它已经在自己的 assistant 历史里了。
        重复投递会让它以为别人也说了同样的话。这也是游标可以「跳着走」的原因。

        **墓标 seq 一律跳过**：被群窗删掉的那条，谁都不该再听到 —— 对**还没被投递**的人
        而言这是唯一的一道拦截（已经收到的人由 fan-out 到各自 session 的 `history/delete` 剔除）。

        ★ @ 点名（11-21）：`u.mentions` 非空 ⇒ **只有被点名的人收到** ★
        没被 @ 的人连"玩家说过这句话"都不知道 —— 与真实聊天软件的 @ 语义一致
        （用户拍板："只进被@者的账本，未@者完全不知道"）。

        为什么过滤在这里、不在 `say` 时就不落账：`Room.log` 是**全群客观流水**
        （群窗回放、按 uid 删除、`last_seq` 坐标都依赖它完整）。@ 影响的是"谁听到"，
        不是"群里发生过什么"。把它做成"不落账"会让群窗看不见玩家自己说过的话 ——
        玩家明明发了，屏幕上却没有，比"别人没听到"更糟。
        """
        if cursor is not None:
            start = int(cursor)
        else:
            # None 容错（10-21）：`.get` 的默认值只护"键不存在"；joined 表里值被写成 null
            # （坏档/异形 index.json）时 int(None) 会炸掉整次投递。回落流水末尾。
            _jv = self.joined.get(str(me), self.last_seq)
            start = int(_jv) if _jv is not None else int(self.last_seq or 0)
        gone = self.deleted_seqs
        who = str(me or "")
        out: List[Utterance] = []
        for u in self.log:
            if u.seq <= start or u.speaker == who or u.seq in gone:
                continue
            # @ 过滤：点了名就只给被点名的人。
            # ★ 判据必须是 `is not None` 而不是真值 ★：`[]`（@ 了不存在的人）
            #   意味着"谁都不给"，用真值判断会把它当成"没点名" ⇒ 广播给全群。
            #   None = 没点名（全员，既有行为）；非 None = 只给名单里的人。
            if u.mentions is not None and who not in u.mentions:
                continue
            out.append(u)
        return out

    # ---------- 写（只增不改） ----------

    def say(self, speaker: str, text: str, time: Optional[int] = None,
            narration: bool = False,
            mentions: Optional[List[str]] = None,
            pure_ping: bool = False) -> Utterance:
        """追加一条发言。**唯一**的 log 写入口（`Room.log` 只增不改）。
        `narration=True` = 旁白（见 `Utterance.narration`）：进账本、不进群窗。
        `mentions` = 被 @ 的成员（见 `Utterance.mentions`）：只投给他们。"""
        import time as _time
        u = Utterance(seq=self.next_seq, speaker=str(speaker or ""), text=str(text or ""),
                      time=int(time if time is not None else _time.time() * 1000),
                      narration=bool(narration),
                      # ★ 必须区分 `None` 与 `[]` ★（见 `Utterance.mentions`）：
                      #   None = 没点名（全员收）；[] = 点了名但谁都不给（@ 了不存在的人）。
                      # 写成 `if mentions` 会把 [] 一起归一成 None ⇒ 那条消息广播给全群 ——
                      # 正是本功能最不能出的错（以为在私下点名，实际所有人都收到了）。
                      mentions=None if mentions is None
                               else [str(x) for x in mentions],
                      pure_ping=bool(pure_ping))
        u.uid = f"{self.id}#{u.seq}"
        self.log.append(u)
        return u

    # ---------- 删除（墓标；`log` 仍然只增不改） ----------

    def is_deleted(self, seq: Any) -> bool:
        """这条发言是否已被群窗删除（= 有没有墓标）。"""
        try:
            return int(seq) in self.deleted_seqs
        except Exception:
            return False

    def delete_utterance(self, seq: Any) -> bool:
        """给一条发言立墓标（幂等）。返回是否**新**立（已删/不存在 → False）。

        ★ 本方法**只动墓标**：把那条从 `log` 里抠掉会让 seq 坐标与 uid 全部错位
        （见字段处注释）。各成员 session 里的副本由编排层 fan-out 另行剔除 ——
        "群记录 + 各成员记忆，两边同删"是**编排层**的义务，不是本数据类能单方面完成的。
        """
        try:
            s = int(seq)
        except Exception:
            return False
        if s <= 0 or not any(u.seq == s for u in self.log) or s in self.deleted_seqs:
            return False
        self.deleted_seqs.add(s)
        return True

    def visible_log(self) -> List[Utterance]:
        """`log` 去掉墓标（群窗投影用；投递另有 `delta_for`，不必经这里）。"""
        if not self.deleted_seqs:
            return list(self.log)
        return [u for u in self.log if u.seq not in self.deleted_seqs]

    # ---------- 成员（可增可删，id 不变） ----------

    def add_member(self, npc_id: str, joined_at: int = 0, unit_id: str = "") -> bool:
        """加人。返回是否真的加了（幂等：已在群里返回 False）。

        `joined_at` = 他进群时房内已说到第几条（决定他的**投递起点**）：
        - 建群时的初始成员传 `0`（收到全部）；
        - 后来拉进来的人传当时的 `last_seq`（看不到入群前的话）。

        ★ 这个参数**不能省** —— 默认 0 只对"建群"这一次成立。`RoomFlow` 的两条
        入口（建群 / 中途加人）分别传了正确的值，别在别处随手调它。

        ★ 用**直接赋值**而不是 `setdefault`：重入群必须拿到**新的**入群位置。
        `setdefault` 在 `joined` 里已存在陈旧键时（读档合并、手工构造、将来加迁移）
        会保留旧值，而入群位置偏小 ⇒ 那人被灌入群前的全部历史。
        这里不是幂等写入点（上面已对 `in members` 早退），所以赋值是安全的。
        """
        who = str(npc_id or "").strip()
        if not who or who == PLAYER or who in self.members:
            return False
        self.members.append(who)
        # None 容错（10-21）：调用方把 joined_at 传成 None（Gemini 式显式 null 参数、
        # 或上游 `.get` 漏护）时，int(None) 当场炸整次加人。None = 不知道位置 = 从 0 起。
        self.joined[who] = int(joined_at) if joined_at is not None else 0
        # `unit_id` 可缺（老调用点、或 C# 手上没有精确身份）：**缺就什么都不写**，
        # 绝不写空串 —— 空串在消费侧会变成"有身份但是空的"这种没人能推理的中间态，
        # 而"这个键不存在"是明确的"不知道"。
        uid = str(unit_id or "").strip()
        if uid:
            self.member_unit_ids[who] = uid
        return True

    def kick(self, npc_id: str) -> bool:
        """踢人。返回是否真的踢了。**id 与 log 都不变**（他确实听过那些话）。

        这里**同时移除 `joined` 里那条**（不是保留）：
        `joined` 是"还没有游标的人该从哪开始"的兜底，而"重入群"应当是一次**新的入场**
        —— 他不在场时说的话，不该当成他亲耳听见的。
        移除之后 `add_member` 才能把新的入场位置写进去；若保留，`joined` 会停在
        他**上次**入场的位置（偏小）⇒ 被灌入群前历史。

        ★ 已知边界（保留游标优先的后果）：**已经投递过、又被踢、再拉回来**的人，
        游标仍留在他被踢前的位置（游标在 session 里，是权威进度，本方法不碰），
        于是重入后 `delta_for` 走的是游标、不是 `joined` ⇒ 他**会补上不在场期间的内容**。
        这是**刻意**的取舍：
        - 另一个方向（让 `joined` 压过游标）会在 `joined` 陈旧时**永久跳过**内容 ——
          那是不可恢复的损失，比"多听了几句"严重得多；
        - 本工程是「一个 NPC 一个 session」的**连续人生**，它的 session 里本来就有
          不在场期间在别处的经历（单聊、别的群），群里那几句与那些性质相同。
        要改成"重入即从现在起听"，正确做法是**重入时往 session 写一条新的
        `room/cursor`**（让权威进度自己反映这次入场），而不是让兜底值去压权威值。
        """
        who = str(npc_id or "").strip()
        if who not in self.members:
            return False
        self.members.remove(who)
        self.joined.pop(who, None)
        # 精确身份与 `joined` 一样：**离群即失效**。留着会让"重入群"沿用旧身份 ——
        # 而重入时 C# 若没给新的 id（老调用点），旧 id 可能已经指向别人（换存档场景）。
        # 一致性优先：不在群里就不该有身份记录。
        self.member_unit_ids.pop(who, None)
        return True

    def roster_for(self, npc_id: str) -> List[str]:
        """该成员此刻的「同席」名单 = 群成员去掉他自己（玩家恒在场，不写进来）。"""
        me = str(npc_id or "").strip()
        return [m for m in self.members if m != me]

    def unit_id_of(self, npc_id: str) -> str:
        """某成员的精确 unitID；**不知道就返回空串**（不是 None、不是占位）。

        返回空串的两种正常情形：① 这个群是在本功能上线前建的（老 `index.json` 没这个键）；
        ② 建群时 C# 手上没有那个人的精确身份（比如手动好友还没回填过）。
        两种都只意味着"渲染他的立绘要多扫一次全图"，不影响任何语义。
        """
        return self.member_unit_ids.get(str(npc_id or "").strip(), "")

    # ---------- 序列化 ----------

    def add_group_usage(self, delta: Optional[Dict[str, Any]]) -> None:
        """把一趟群内回合的用量增量并进本群账（负数/非数一律丢弃，绝不抛）。

        **不变量：只增不减** —— 花费是既成事实，删消息/压缩都不该让它变小；
        成员的重试会真实多花一次钱，所以它也会被如实计两次（与 `UsageTracker` 同规约）。
        """
        if not isinstance(delta, dict):
            return
        for k, v in delta.items():
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                self.group_usage[str(k)] = int(self.group_usage.get(str(k), 0)) + n

    def group_usage_block(self) -> Dict[str, Any]:
        """本群用量的**对外形状**（与 `tokenUsage` 同形 + turns/steps/sinceCreated）。"""
        from ..session.stats import shape_group_usage
        gu = self.group_usage or {}
        return shape_group_usage(gu, turns=int(gu.get("turns", 0) or 0),
                                 steps=int(gu.get("steps", 0) or 0), since=self.created)

    def to_meta(self) -> Dict[str, Any]:
        """名录条目（落 `index.json`，也是 `<id>.jsonl` 的首行）。

        `deleted_seqs` 也在这里 —— 两个落点都走 `to_meta()`，所以墓标随房间
        **全量重写**天然持久化（读档后群窗不会"删了又出现"）。
        """
        return {"room_id": self.id, "title": self.title,
                "members": list(self.members), "created": self.created,
                "last_seq": self.last_seq, "joined": dict(self.joined),
                # 只带**当前成员**的身份（防御性裁剪：手工构造/将来迁移可能留下离群条目）
                "member_unit_ids": {m: self.member_unit_ids[m]
                                    for m in self.members if m in self.member_unit_ids},
                "deleted_seqs": sorted(int(s) for s in self.deleted_seqs),
                # 本群累计用量：空表不写（老存档读进来也是空表，天然兼容）
                **({"group_usage": dict(self.group_usage)} if self.group_usage else {})}

    @classmethod
    def from_meta(cls, d: Dict[str, Any], log: Optional[Iterable[Utterance]] = None) -> "Room":
        joined = d.get("joined")
        deleted = d.get("deleted_seqs")
        uids = d.get("member_unit_ids")
        members = [str(m) for m in (d.get("members") or [])]
        return cls(id=str(d.get("room_id") or d.get("id") or ""),
                   title=str(d.get("title") or ""),
                   members=members,
                   log=list(log or []),
                   created=int(d.get("created") or 0),
                   joined={str(k): int(v) for k, v in (joined or {}).items()}
                          if isinstance(joined, dict) else {},
                   # 精确身份：**只留当前成员的**（与本功能上线前的存档天然兼容 ——
                   # 老房间没有这个键 → 空表 → C# 侧回落按名解析，行为与今天一致）。
                   member_unit_ids={str(k): str(v) for k, v in uids.items()
                                    if str(k) in members and str(v or "").strip()}
                          if isinstance(uids, dict) else {},
                   # ★ 必须读回来存下：懒加载时 `log` 是空的，不存的话 `last_seq`
                   #   会假报 0（`to_meta` 写它、`next_seq` 用它、群列表显示它）。
                   meta_last_seq=int(d.get("last_seq") or 0),
                   # 墓标同样要读回来：懒加载态下 `log` 为空，但 `project_room_history`
                   # 与 `delta_for` 一旦拿到流水就必须知道哪些不许出现。
                   deleted_seqs={int(s) for s in (deleted or [])} if isinstance(deleted, (list, set, tuple)) else set(),
                   # 本群累计用量：老存档没有这个键（→ 空表 → 那一行从 0 开始涨）
                   group_usage={str(k): int(v) for k, v in (d.get("group_usage") or {}).items()
                                if isinstance(v, (int, float))} if isinstance(d.get("group_usage"), dict) else {})

    def default_title(self) -> str:
        """没起名时的默认群名：`与甲、乙的聚谈`（成员空则为「空席」）。"""
        if not self.members:
            return "空席"
        shown = "、".join(self.members[:3])
        if len(self.members) > 3:
            shown += f" 等 {len(self.members)} 人"
        return f"与{shown}的聚谈"


def new_room_id(existing: Iterable[str], retired: Iterable[str] = ()) -> str:
    """分配一个**从未用过**的 room_id：`g1`, `g2`, …（与 `docs/archive/2026-09-17-群聊编排/设计定稿.md` 的示例一致）。

    可读、稳定、便于人工核对日志；不用随机串是因为 id 会出现在 UI 与
    投递/游标记录里，人要能一眼对上号。

    ★ `retired` = **已解散**的 id 高水位（落在 `index.json` 的 `retired` 键上）。
    必须一并排除，否则会出现本功能最阴的一个 bug：

        建 g1/g2 → 解散 g2 → 下一个新群**又叫 g2**。而老 g2 的成员 session 里
        躺着 `room/cursor {room_id:"g2", seq:17}`（解散时刻意**不清**，理由见
        `RoomFlow.dissolve_room`）。新 g2 从 seq=0 起，`cursor_for("g2")` 返回 17，
        `delta_for` 只给 `seq > 17` 的 ⇒ **这些人在新群里一句话都收不到**，
        不报错、不发日志，直到新群聊够 18 条才突然开始收到。

    "永不重用"比"解散时清游标"便宜得多：后者要引入新事件类型并维持
    "reset 之前一律不算"的读取规则，而 `cursor_for` 必须对四条下游路径隐形
    （SurfaceManager / history 投影 / 压缩材料 / Inbox），动它风险不成比例。
    """
    used = {str(x) for x in existing} | {str(x) for x in retired}
    n = 1
    while f"g{n}" in used:
        n += 1
    return f"g{n}"
