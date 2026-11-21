"""群聊 @ 点名：把玩家输入里的 `@名字` 解析成收件人名单。

分层分权（与 `room.py` 的「纯数据」同一条规矩）：
- **本模块是纯函数**：输入字符串 + 成员名单，输出解析结果。不碰 Room、不碰 agent、
  不做 IO、不 import 任何内部模块 ⇒ 可以脱离运行时完整单测。
- 解析在 **Python 侧**做，不在 C#：
  ① 成员名单的真相在这里（`Room.members`），C# 手上的 `CurrentRoomMembers` 是回执的
     副本，可能落后于一次刚完成的拉人/踢人；
  ② 将来 UI 加 @ 补全（下拉候选）时，候选来源与解析规则必须是同一份，否则会出现
     "补全给了张三、解析认不出"这种只有玩家能发现的错位。

── 两条语义（用户 11-21 拍板）────────────────────────────────────────────
① **@ 了谁，这条消息就只进谁的账本**（没被 @ 的人完全不知道，与"私下点名"一致）；
② **@ 了谁，就只有谁能开口** —— 没被 @ 的人这一轮不发言，不补位。
   理由：@ 是明确点名，"我 @ 张三，李四跳出来接话"是错的。被 @ 的人弃权就是弃权
   （与既有 `【沉默】` 语义一致），这一轮到此为止。

── 没有 @ 时 ────────────────────────────────────────────────────────────
返回 `None`（不是空列表）。这个区别是**语义级**的，不是风格问题：
  · `None` = 没点名 ⇒ 全员收、全员可能开口（既有行为，逐字不变）；
  · `[]`   = 点了一个都不存在的名字 ⇒ 谁都不该收到。
混成同一个值，会让"@了一个已被踢掉的人"变成"发给全群"——那是最糟的方向
（玩家以为在私下点名，实际广播给了所有人）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

# `@` 的全角与半角。中文输入法下 `＠` 很常见（用户明确要求不区分全半角）。
_AT_CHARS = ("@", "＠")

# 名字的分隔符：空格、制表、换行、以及中文标点。
# 为什么要把中文标点算进去：玩家写「@张三，你过来一下」时，`，` 与名字之间**没有空格** ——
# 只按空格切会得到「张三，你过来一下」这个不存在的名字，于是解析失败、退回全员广播。
_NAME_STOP = set(" \t\r\n　,，.。!！?？;；:：、~～)）]】》\"'“”‘’")

# 剥掉 `@名字` 之后、紧随其后的那**一个**字符若在这个集合里，也一并剥掉
# （见 `strip_mentions` 的注释：只吃一个，不吃连续标点）。
_STRIP_AFTER = set(",，.。!！?？;；:：、")

# 一句话里最多认几个 @。防的是玩家粘贴一长串（或恶意构造）把一轮撑成 N 次模型调用。
# 超出的**不报错、不静默丢**：记进 `overflow`，由调用方决定怎么提示。
MAX_MENTIONS = 8


@dataclass
class MentionResult:
    """一次解析的结果。"""

    names: Optional[List[str]] = None
    """命中的**成员名**（已按 `members` 归一、去重、保序）。

    `None` = 这句话里没有 @（⇒ 全员收、全员可开口）。
    `[]`   = 有 @，但一个都对不上（⇒ 谁都不收；调用方该给玩家一句提示）。
    """

    unknown: List[str] = field(default_factory=list)
    """@ 了但不在群里的名字（原样保留，供提示"群里没有这个人"）。"""

    overflow: List[str] = field(default_factory=list)
    """超过 `MAX_MENTIONS` 的那部分（没解析，也没丢弃）。"""

    @property
    def has_at(self) -> bool:
        """这句话里有没有 @ 这个动作（无论解析成功与否）。"""
        return self.names is not None

    @property
    def recipients(self) -> Optional[List[str]]:
        """收件人名单：`None` = 全员；`[]` = 无人。"""
        return self.names


def parse_mentions(text: Any, members: Sequence[str]) -> MentionResult:
    """从玩家输入里解析 `@名字`。

    ── 匹配规则（刻意宽松，都是实测过的真实写法）────────────────────────
      · `@张三 你过来`      —— 空格分隔
      · `@张三，你过来`      —— 中文逗号紧贴（最常见的写法）
      · `＠张三`            —— 全角 @
      · `@张三@李四`        —— 连写（第二个 @ 即边界）
      · `@张三`（句末）      —— 文本结束即边界

    ── 名字怎么认 ──────────────────────────────────────────────────────
    **只认完整成员名**，不做前缀/模糊匹配。理由：模糊匹配在重名与近名上会**静默错人**
    （"@张三丰" 命中 "张三" ⇒ 消息进了错人的账本，而两边都看不出异常），
    这正是本工程反复写死的"比全空更糟"那一类。认不出就记进 `unknown`，让玩家看见
    "群里没有这个人"，比猜一个更安全。

    为什么按"最长优先"扫描：成员里有 "张三" 与 "张三丰" 时，`@张三丰` 必须命中后者。
    从当前 @ 位置起，尝试所有成员名，取**能匹配上的最长**那个。

    返回的 `MentionResult.names` 顺序 = 在文本里出现的顺序（去重），
    这个顺序会被 `plan_round` 用作发言优先序（先被 @ 的先说）。
    """
    src = str(text or "")
    member_list = [str(m) for m in (members or []) if str(m)]
    if not src or not member_list:
        return MentionResult(names=None if not _has_at(src) else [])

    if not _has_at(src):
        return MentionResult(names=None)

    # 最长优先：先把成员名按长度降序，逐个尝试。重名（同名不同人）在群里不可能存在
    # （成员名单是 npc_id，唯一），所以这里不需要处理重名。
    by_len = sorted(member_list, key=len, reverse=True)
    found: List[str] = []
    unknown: List[str] = []
    overflow: List[str] = []

    i = 0
    n = len(src)
    while i < n:
        if src[i] not in _AT_CHARS:
            i += 1
            continue
        # 找到一个 @，从它后面开始试名字
        start = i + 1
        hit = None
        for name in by_len:
            if src.startswith(name, start):
                hit = name
                break
        if hit is not None:
            if len(found) + len(unknown) < MAX_MENTIONS:
                if hit not in found:
                    found.append(hit)
            else:
                overflow.append(hit)
            i = start + len(hit)
            continue
        # 没匹配上任何成员：截到下一个分隔符，记成 unknown
        j = start
        while j < n and src[j] not in _NAME_STOP and src[j] not in _AT_CHARS:
            j += 1
        raw = src[start:j]
        if raw:
            if len(found) + len(unknown) < MAX_MENTIONS:
                if raw not in unknown:
                    unknown.append(raw)
            else:
                overflow.append(raw)
        i = j if j > start else start

    return MentionResult(names=found, unknown=unknown, overflow=overflow)


def _has_at(src: str) -> bool:
    return any(c in src for c in _AT_CHARS)


def _is_name_char(ch: str) -> bool:
    """这个字符能不能算"名字的一部分"（用于判断匹配是否到了名字的右边界）。

    只排除分隔符是不够的：`@张三丰` 里 `丰` 不是分隔符，却说明 `张三` 只是前缀。
    所以判据是"它看起来还像名字"，而不是"它是不是分隔符"。
    """
    return ch not in _NAME_STOP and ch not in _AT_CHARS


def strip_mentions(text: Any, names: Iterable[str]) -> str:
    """把已识别的 `@名字` 从正文里去掉（保留其余原文）。

    为什么要剥：`@张三 你过来` 里的 `@张三` 是**寻址信息**，不是话的内容。
    原样投给模型时，模型会看到「玩家：@张三 你过来」—— 它会以为玩家在打字面上
    的 @ 符号（真实聊天软件里 @ 是 UI 层，正文里不该出现），进而在回复里模仿
    「@玩家」这种游戏里根本不存在的写法。

    ★ 只剥**已识别成成员**的那些 ★：`@张三丰` 而群里只有 `张三` 时，
      `@张三` 三个字**不能**被剥（那会把正文切成「丰」）。判据是"这个位置起
      能匹配到某个**已命中**的名字"，与解析用的是同一套最长优先规则。

    找不到就原样返回 —— 这个函数不该让任何文本变得更差。
    """
    src = str(text or "")
    hit_names = [str(x) for x in (names or []) if str(x)]
    if not src or not hit_names:
        return src

    out: List[str] = []
    i = 0
    n = len(src)
    while i < n:
        if src[i] in _AT_CHARS:
            start = i + 1
            # ★ 与 `parse_mentions` 完全同一套判据：从当前位置取**最长**的完整匹配 ★
            # 只试 `names` 里那些名字，所以"没被识别成收件人的名字"天然不会被剥。
            # 反例（第一版就是这么错的）：`@张三丰` 而 `names=["张三"]` 时，
            # 按短名匹配会剥掉「@张三」、留下「丰」—— 正文被切碎，而玩家看不出原因。
            matched = None
            for name in sorted(hit_names, key=len, reverse=True):
                if not src.startswith(name, start):
                    continue
                # ★ 右边界必须成立 ★：名字后面紧跟的若仍是"名字字符"（中文/字母/数字），
                # 说明这只是**更长名字的前缀**，不是完整匹配。
                # 反例（第一版就是这么错的）：`@张三丰` 而 `names=["张三"]` 时，
                # 按前缀剥会剥掉「@张三」、留下「丰」—— 正文被切碎，玩家看不出原因。
                # 判据与 `parse_mentions` 的"只认完整成员名"同源：宁可整段不剥，
                # 也不要把一句话切成两截。
                nxt = start + len(name)
                if nxt < n and _is_name_char(src[nxt]):
                    continue
                matched = name
                break
            if matched is not None:
                # 跳过 "@名字"，再吃掉紧随其后的分隔符（空格 / 中文逗号句号 / 顿号）。
                #
                # ★ 为什么连中文标点一起吃 ★：「@张三，你过来」如果只吃空格，会剥成
                #   「，你过来」—— 开头的逗号在中文里没有依附对象，看起来像文本损坏。
                #   而「@张三，李四，你们俩过来」里第一个逗号是**分隔两个被 @ 的人**的，
                #   吃掉它也不会丢信息（人名本身已经把两人都记下了）。
                #   ★ 只吃**一个**：连吃会把「@张三，，你怎么了」这种原文里的重复标点抹掉。
                j = start + len(matched)
                if j < n and src[j] in _STRIP_AFTER:
                    j += 1
                    # 标点之后再吃空格（「@张三， 你过来」）
                    while j < n and src[j] in (" ", "\t", "　"):
                        j += 1
                else:
                    while j < n and src[j] in (" ", "\t", "　"):
                        j += 1
                i = j
                continue
        out.append(src[i])
        i += 1
    cleaned = "".join(out).strip()
    # 全剥光了（玩家只写了 "@张三"）：返回空串 —— 调用方据此判断"这是一次纯点名"，
    # 仍要落账（否则"我 @ 了他却没反应"），但正文为空。见 `room_flow.run_message`。
    return cleaned
