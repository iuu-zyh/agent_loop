"""群聊 @ 点名契约（11-21 用户需求）。

用户原话：

    当前群聊里面用户发一句消息实际是进入所有npc的账本里面…我希望群聊里面发消息
    也可以让消息只单独进入某个npc的账本里面…通过@这个功能，如果用户发消息有@谁谁
    的话，那么这些消息只会进入到被@的npc的账本…当然回话的内容也看@，如果@谁就进入谁，
    所以群聊时候需要向模型说明@这个功能，还有就是说话的顺序改为随机

四条语义（用户逐条拍板）：
  ① **@ 了谁，这条消息就只进谁的账本** —— 没被 @ 的人完全不知道；
  ② **@ 了谁，就只有谁能开口** —— 没被 @ 的人这一轮不发言、不补位；
  ③ **没有 @ 时行为逐字不变**（全员收、全员可能开口）；
  ④ **说话顺序改成随机**（不再按固定轮转）。

外加两条安全边界：
  ⑤ @ 了一个不存在的人 ⇒ **不猜、也不退回全员广播**（那是"以为在私下点名、
     实际广播给所有人"，最糟的方向）；
  ⑥ `@名字` 这三个字要**从正文里剥掉**再给模型看（寻址信息不是话的内容）。

本文件同时钉住"数据层 / 编排层 / 提示词"三层，因为漏任一层都会静默失效：
  · 数据层漏 → 消息照样进所有人账本（`delta_for` 不过滤）；
  · 编排层漏 → 全员都开口（`only` 没传）；
  · 提示词漏 → 模型不知道 @ 是什么，回出 `@玩家` 这种游戏里不存在的写法。
"""

from __future__ import annotations

import ast
import inspect
import random
import re

import pytest

from agent_loop.room.mention import (MAX_MENTIONS, MentionResult, parse_mentions,
                                     strip_mentions)
from agent_loop.room.room import PLAYER, Room
from agent_loop.room.room_flow import (NUDGE_TEXT, NUDGE_TEXT_MENTION, plan_round)


LIN, LI, WANG = "林婉清", "李四", "王五"
MEMBERS = [LIN, LI, WANG]


# ══════════════════════════════════════════════════════════════════
# A. 解析核心（纯函数）
# ══════════════════════════════════════════════════════════════════

def test_no_at_returns_none_not_empty_list():
    """★ 最关键的一条区分：没 @ 是 `None`，@ 了不存在的人是 `[]` ★

    两者若混成同一个值，"@了一个已被踢掉的人"就会变成"发给全群" ——
    玩家以为在私下点名，实际广播给了所有人。
    """
    assert parse_mentions("大家好", MEMBERS).names is None
    assert parse_mentions("", MEMBERS).names is None
    assert parse_mentions("@赵六 你好", MEMBERS).names == []
    # 语义上：None = 全员收；[] = 无人收
    assert parse_mentions("大家好", MEMBERS).recipients is None
    assert parse_mentions("@赵六 你好", MEMBERS).recipients == []


def test_single_mention_with_space_and_punctuation():
    """最常见的三种真实写法。"""
    assert parse_mentions("@林婉清 你过来", MEMBERS).names == [LIN]
    assert parse_mentions("@林婉清，你过来", MEMBERS).names == [LIN]
    assert parse_mentions("@林婉清", MEMBERS).names == [LIN]
    assert parse_mentions("你过来@林婉清", MEMBERS).names == [LIN]


def test_fullwidth_at_is_accepted():
    """中文输入法下 `＠` 很常见（用户明确要求不区分全半角）。"""
    assert parse_mentions("＠林婉清 你过来", MEMBERS).names == [LIN]


def test_multiple_mentions_all_received():
    """多个 @ = 这几个都收到（用户拍板）。顺序 = 在文本里出现的顺序。"""
    r = parse_mentions("@林婉清 @李四 你俩过来", MEMBERS)
    assert r.names == [LIN, LI]
    assert parse_mentions("@李四@林婉清", MEMBERS).names == [LI, LIN]


def test_duplicate_mention_is_deduped():
    r = parse_mentions("@林婉清 @林婉清 在吗", MEMBERS)
    assert r.names == [LIN], f"重复 @ 应去重：{r.names}"


def test_longest_name_wins():
    """★ 成员里有「张三」与「张三丰」时，`@张三丰` 必须命中后者 ★

    不做最长优先的话，消息会进**错人的账本** —— 而两边都看不出异常
    （张三收到一句没头没尾的话，张三丰什么都没收到）。
    """
    members = ["张三", "张三丰"]
    assert parse_mentions("@张三丰 好久不见", members).names == ["张三丰"]
    assert parse_mentions("@张三 好久不见", members).names == ["张三"]


def test_unknown_name_is_recorded_not_guessed():
    r = parse_mentions("@赵六 你好", MEMBERS)
    assert r.names == []
    assert r.unknown == ["赵六"], f"没记下认不出的名字：{r.unknown}"


def test_partial_match_does_not_fire():
    """`@张三` 而群里只有「张三丰」时**不许**命中 —— 部分匹配就是静默错人。"""
    members = ["张三丰"]
    r = parse_mentions("@张三 你好", members)
    assert r.names == [], f"部分匹配命中了：{r.names}"


def test_empty_members_does_not_crash():
    assert parse_mentions("@张三", []).names == []
    assert parse_mentions("", []).names is None


def test_mention_cap_prevents_unbounded_rounds():
    """超过 MAX_MENTIONS 的部分记进 overflow，不静默丢。"""
    many = [f"人{i}" for i in range(MAX_MENTIONS + 4)]
    text = " ".join("@" + n for n in many)
    r = parse_mentions(text, many)
    assert len(r.names) == MAX_MENTIONS
    assert len(r.overflow) == 4, f"overflow 没记全：{r.overflow}"


def test_has_at_flag():
    """`has_at` 说的是"这句话里有没有 @ 这个动作"，与解析成功与否无关。"""
    assert parse_mentions("@赵六", MEMBERS).has_at is True
    assert parse_mentions("你好", MEMBERS).has_at is False


# ══════════════════════════════════════════════════════════════════
# B. 正文剥离（寻址信息不该进模型的眼睛）
# ══════════════════════════════════════════════════════════════════

def test_strip_removes_at_and_following_separator():
    assert strip_mentions("@林婉清 你过来", [LIN]) == "你过来"
    assert strip_mentions("@林婉清，你过来", [LIN]) == "你过来"
    assert strip_mentions("@林婉清，@李四 你们来", [LIN, LI]) == "你们来"


def test_strip_keeps_text_when_nothing_matched():
    """认不出就原样返回 —— 这个函数不该让任何文本变得更差。"""
    assert strip_mentions("@赵六 你好", []) == "@赵六 你好"
    assert strip_mentions("你好", [LIN]) == "你好"


def test_strip_only_removes_recognized_names():
    """★ `@张三丰` 而只剥「张三」时，不许把「丰」留下 ★"""
    assert strip_mentions("@张三丰 你好", ["张三"]) == "@张三丰 你好"
    assert strip_mentions("@张三丰 你好", ["张三", "张三丰"]) == "你好"


def test_strip_eats_only_one_punctuation():
    """连吃标点会把原文里的重复标点抹掉。"""
    assert strip_mentions("@林婉清，，你怎么了", [LIN]) == "，你怎么了"


def test_pure_ping_becomes_empty():
    """只写了 `@名字` ⇒ 剥完是空串，调用方据此补一句促发语。"""
    assert strip_mentions("@林婉清", [LIN]) == ""


# ══════════════════════════════════════════════════════════════════
# C. 数据层：@ 的消息只进被 @ 者的账本
# ══════════════════════════════════════════════════════════════════

def _room():
    return Room(id="g1", members=list(MEMBERS))


def test_mention_only_reaches_the_named_member():
    """★ 语义①：@ 了谁，这条消息就只进谁的账本 ★"""
    r = _room()
    r.say(PLAYER, "大家好")
    r.say(PLAYER, "你过来一下", mentions=[LIN])

    assert [u.text for u in r.delta_for(LIN, 0)] == ["大家好", "你过来一下"]
    assert [u.text for u in r.delta_for(LI, 0)] == ["大家好"], \
        "没被 @ 的人收到了 @ 消息"
    assert [u.text for u in r.delta_for(WANG, 0)] == ["大家好"]


def test_message_without_mention_reaches_everyone():
    """语义③：没有 @ 时行为逐字不变（全员收）。"""
    r = _room()
    r.say(PLAYER, "大家好")
    for me in MEMBERS:
        assert [u.text for u in r.delta_for(me, 0)] == ["大家好"]


def test_empty_mentions_reaches_nobody():
    """`mentions=[]` = 谁都不给（@ 了不存在的人时用）。"""
    r = _room()
    r.say(PLAYER, "这条谁都不该收到", mentions=[])
    for me in MEMBERS:
        assert r.delta_for(me, 0) == [], f"{me} 收到了不该收的"


def test_mention_does_not_affect_group_window():
    """★ 群窗是"群里发生过什么"的客观记录，@ 不影响它 ★

    玩家发了话，群窗必须看得见 —— 否则"我明明发了，屏幕上却没有"，
    比"别人没听到"更糟。
    """
    r = _room()
    r.say(PLAYER, "你过来一下", mentions=[LIN])
    assert [u.text for u in r.visible_log()] == ["你过来一下"]


def test_mention_survives_save_load():
    """★ 落盘必须带上 mentions ★ 否则重启后这条会被投给**全群**（私密性丢失）。"""
    r = _room()
    r.say(PLAYER, "你过来一下", mentions=[LIN])
    blob = r.log[0].to_dict()
    assert blob.get("mentions") == [LIN], f"没落盘：{blob}"

    from agent_loop.room.room import Utterance
    back = Utterance.from_dict(blob)
    assert back.mentions == [LIN]


def test_plain_message_does_not_gain_a_mentions_key():
    """老流水/普通发言保持逐字节原样（不写空键）。"""
    r = _room()
    r.say(PLAYER, "大家好")
    assert "mentions" not in r.log[0].to_dict()


def test_old_save_without_mentions_loads_as_none():
    """老存档没有这个键 ⇒ 当作"没点名"（全员收，既有行为）。"""
    from agent_loop.room.room import Utterance
    old = {"seq": 1, "uid": "g1#1", "speaker": PLAYER, "text": "大家好", "time": 0}
    assert Utterance.from_dict(old).mentions is None


def test_narrator_lines_still_reach_everyone():
    """旁白（行动记录行）不受 @ 影响 —— 它是"群里发生的事"，不是某人说的话。"""
    r = _room()
    r.say("系统", "（与唐炎切磋了一场）", narration=True)
    for me in MEMBERS:
        assert len(r.delta_for(me, 0)) == 1


# ══════════════════════════════════════════════════════════════════
# D. 编排层：@ 时只有被 @ 的人能开口
# ══════════════════════════════════════════════════════════════════

def test_only_param_restricts_candidates():
    """★ 语义②：`only` 是白名单，名单外的人**根本不进候选** ★

    与 `speak_first` 的区别是语义级的：
      · speak_first = 优先（其余人仍会补位）
      · only        = 只有这些人可能说话
    @ 必须走后者 —— 否则"我 @ 林婉清，李四接话"。
    """
    from agent_loop.room.room_flow import RoomFlow

    flow = RoomFlow(loop=None, run_turn=lambda *a, **k: None,
                    agent_lock=lambda *a, **k: None,
                    room_lock=lambda *a, **k: None,
                    max_speakers_per_round=3)
    src = inspect.getsource(RoomFlow._run_round)
    assert "only: Optional[Sequence[str]] = None" in src, "_run_round 没有 only 形参"
    assert "_pool = list(only) if only is not None else room.members" in src, \
        "候选池没收窄成 only"
    assert "plan_round(_pool" in src, "候选池没传给 plan_round"


def test_run_message_passes_only_when_mentioned():
    """`run_message` 必须把解析结果当 `only` 传下去，否则全员都开口。"""
    from agent_loop.room.room_flow import RoomFlow

    src = inspect.getsource(RoomFlow.run_message)
    assert "only=_m.names" in src, "没把 @ 名单传成 only ⇒ 全员都会开口"


def test_run_message_uses_mention_nudge():
    """被 @ 的人要收到**不同**的催话文案（他得知道"只有我听见了"）。"""
    from agent_loop.room.room_flow import RoomFlow

    src = inspect.getsource(RoomFlow.run_message)
    assert "NUDGE_TEXT_MENTION if _m.names else NUDGE_TEXT" in src, \
        "没按 @ 分岔催话文案"


def test_run_message_does_not_broadcast_on_unknown_mention():
    """★ 语义⑤：@ 了不存在的人 ⇒ 不退回全员广播 ★"""
    from agent_loop.room.room_flow import RoomFlow

    src = inspect.getsource(RoomFlow.run_message)
    i = src.find("if _m.names is not None and not _m.names:")
    assert i != -1, "没有处理「@ 了但一个都对不上」的分支"
    blk = src[i:i + 1200]
    assert "mentions=[]" in blk, "没有用 mentions=[] 让这条不投给任何人"
    assert "note=" in blk, "没给玩家一句提示（玩家会以为发成功了）"


def test_run_message_strips_mentions_before_storing():
    """正文要剥掉 @ 再落账（寻址信息不是话的内容）。"""
    from agent_loop.room.room_flow import RoomFlow

    src = inspect.getsource(RoomFlow.run_message)
    assert "strip_mentions(raw, _m.names or [])" in src, "没剥 @ 就直接落账"
    # 剥完为空（纯点名）要补促发语，否则模型收到一条空消息
    assert "pure_ping" in src, "没处理「只写了 @名字」这种纯点名"


def test_pure_ping_keeps_player_raw_text_in_log():
    """★ 纯点名（只写了 `@张三`）：流水里必须是**玩家原文**，不是促发语 ★

    复核时实测到的真 bug：第一版把「（玩家在叫你。）」当成正文落了账，
    于是群窗上显示玩家说了那句系统话 —— 他明明只打了 `@张三`。
    现在改成：流水存原文、促发语只在投递给模型的那一份里替换。
    """
    from agent_loop.room.room import PLAYER
    from agent_loop.room.room_flow import RoomFlow

    src = inspect.getsource(RoomFlow.run_message)
    i = src.find("pure_ping = bool(_m.names) and not body")
    assert i != -1, "没有识别纯点名"
    blk = src[i:i + 400]
    assert "body = raw" in blk, "纯点名时没把正文换回玩家原文 ⇒ 群窗会显示促发语"


def test_pure_ping_substitutes_only_when_delivering():
    """促发语只出现在**投递副本**上（`Room.log` 不动）。"""
    from agent_loop.room.room import PLAYER, Room
    from agent_loop.room.room_flow import PURE_PING_TEXT, RoomFlow

    class _S:
        def append(self, *a, **k): pass

    class _A:
        id = "张三"
        session = _S()
        def __init__(self): self.sent = []
        def cursor_for(self, rid): return 0
        def send(self, text, **kw): self.sent.append(text)

    room = Room(id="g1", members=["张三"])
    room.say(PLAYER, "@张三", mentions=["张三"], pure_ping=True)
    assert room.log[0].text == "@张三", "流水里必须是原文"

    flow = RoomFlow(None, run_turn=None, agent_lock=lambda n: None,
                    room_lock=lambda r: None, say_lock=lambda r: None,
                    turn_slot=lambda: None, emit=None, max_speakers_per_round=3)
    a = _A()
    flow.deliver(room, a)
    assert a.sent == [PURE_PING_TEXT], f"投递给模型的不是促发语：{a.sent}"
    assert room.log[0].text == "@张三", "投递不该改流水"


def test_pure_ping_survives_save_load():
    """`pure_ping` 必须落盘：不读回来的话重启后模型收到的是「@张三」四个字符。"""
    from agent_loop.room.room import PLAYER, Room, Utterance

    room = Room(id="g1", members=["张三"])
    room.say(PLAYER, "@张三", mentions=["张三"], pure_ping=True)
    blob = room.log[0].to_dict()
    assert blob.get("pure_ping") is True, f"没落盘：{blob}"
    assert Utterance.from_dict(blob).pure_ping is True


def test_plain_message_has_no_pure_ping_key():
    """普通发言不写这个键（老存档形状零变化）。"""
    from agent_loop.room.room import PLAYER, Room
    room = Room(id="g1", members=["张三"])
    room.say(PLAYER, "大家好")
    assert "pure_ping" not in room.log[0].to_dict()


def test_echo_frame_carries_player_raw_text():
    """★ 回声帧必须带**玩家原文**（`echo_text`），否则群窗把同一条画两遍 ★

    11-21 用户截图实证：群窗上同时出现
        `@姜萌 你在干什么`   ← C# 本地气泡（玩家输入的原文）
        `你在干什么`         ← Python 回声（`strip_mentions` 剥掉了 `@姜萌`）

    根因：C# 的待销账表按**文本相等**对号，而两边文本必然不同。
    修法：Python 在 `room_say` 里额外交一份原文，C# 用它销账。

    这条测试走**真链路**（`_say_and_emit` → `_emit_say` → emit），不是读源码字符串 ——
    因为"字符串在不在"这种断言挡不住"传了但没接到帧上"。
    """
    import asyncio
    from agent_loop.room.room import PLAYER, Room
    from agent_loop.room.room_flow import RoomFlow

    frames = []

    async def _emit(event, **kw):
        frames.append((event, kw))

    room = Room(id="gE", members=["姜萌"])
    # 不传 `say_lock`：缺省 `_null_say_lock` 是可用的异步空上下文；
    # 传 `lambda r: None` 反而会得到一个 None（不可 async with）。
    flow = RoomFlow(None, run_turn=None, agent_lock=lambda n: None,
                    room_lock=lambda r: None,
                    turn_slot=lambda: None, emit=_emit, max_speakers_per_round=3)

    async def _go():
        # 模拟 run_message 的落账那一段：正文是剥过的，原文另带
        await flow._say_and_emit(room, PLAYER, "你在干什么",
                                 mentions=["姜萌"], echo_text="@姜萌 你在干什么")

    asyncio.run(_go())
    says = [kw for ev, kw in frames if ev == "room_say"]
    assert says, f"没有发出 room_say 帧：{frames}"
    f = says[0]
    assert f.get("text") == "你在干什么", f"正文不该是原文：{f}"
    assert f.get("echo_text") == "@姜萌 你在干什么", (
        "回声帧没带玩家原文 ⇒ C# 按剥过的正文对号永远不等 ⇒ 同一条画两遍（11-21 截图）"
    )


def test_echo_text_omitted_when_same_as_body():
    """原文与正文相同时**不写** `echo_text`（无 @ 的普通发言帧保持逐字节原样）。"""
    import asyncio
    from agent_loop.room.room import PLAYER, Room
    from agent_loop.room.room_flow import RoomFlow

    frames = []

    async def _emit(event, **kw):
        frames.append((event, kw))

    room = Room(id="gF", members=["姜萌"])
    # 不传 `say_lock`：缺省 `_null_say_lock` 是可用的异步空上下文；
    # 传 `lambda r: None` 反而会得到一个 None（不可 async with）。
    flow = RoomFlow(None, run_turn=None, agent_lock=lambda n: None,
                    room_lock=lambda r: None,
                    turn_slot=lambda: None, emit=_emit, max_speakers_per_round=3)

    async def _go():
        await flow._say_and_emit(room, PLAYER, "大家好", echo_text="大家好")
        await flow._say_and_emit(room, "姜萌", "你好呀")

    asyncio.run(_go())
    says = [kw for ev, kw in frames if ev == "room_say"]
    assert len(says) == 2
    assert "echo_text" not in says[0], f"相同文本不该多写键：{says[0]}"
    assert "echo_text" not in says[1], f"NPC 发言不该有 echo_text：{says[1]}"


def test_run_message_parses_before_say():
    """★ 解析必须在 `say` 之前 ★ —— 落账时就要带上 mentions，事后补写等于没写。"""
    from agent_loop.room.room_flow import RoomFlow

    src = inspect.getsource(RoomFlow.run_message)
    i_parse = src.find("parse_mentions(raw, room.members)")
    i_say = src.find("await self._say_and_emit(room, PLAYER, body")
    assert i_parse != -1 and i_say != -1
    assert i_parse < i_say, "@ 解析排在了落账之后 ⇒ 这一条会被投给全群"


# ══════════════════════════════════════════════════════════════════
# E. 排序：随机（语义④）
# ══════════════════════════════════════════════════════════════════

def test_order_is_randomized():
    """★ 语义④：说话顺序随机，不再按固定轮转 ★"""
    seen = set()
    for i in range(60):
        out = tuple(plan_round(MEMBERS, rng=random.Random(i)))
        seen.add(out)
    assert len(seen) > 1, f"顺序恒定（{seen}）⇒ 还是轮转，没随机"


def test_all_permutations_reachable():
    """三个人应该 6 种排列都出现得到（不是只交换前两位）。"""
    perms = set(tuple(plan_round(MEMBERS, rng=random.Random(i))) for i in range(300))
    assert len(perms) == 6, f"只出现了 {len(perms)} 种排列：{perms}"


def test_first_speaker_is_roughly_uniform():
    """首位分布要接近均匀（真随机，不是"总是从第二个人开始"）。"""
    from collections import Counter
    c = Counter(plan_round(MEMBERS, rng=random.Random(i))[0] for i in range(600))
    assert len(c) == 3, f"首位只出现了 {len(c)} 个人：{c}"
    for who, n in c.items():
        assert 120 < n < 280, f"{who} 首位出现 {n}/600 次，分布不均：{c}"


def test_speak_first_still_wins_over_random():
    """被 @ / 提案对手方仍排最前 —— 随机只作用于"其余的人"。"""
    for i in range(30):
        out = plan_round(MEMBERS, speak_first=[WANG], rng=random.Random(i))
        assert out[0] == WANG, f"种子{i}：点名的人没排最前（{out}）"


def test_multiple_speak_first_keeps_given_order():
    out = plan_round(MEMBERS, speak_first=[WANG, LIN], rng=random.Random(7))
    assert out[:2] == [WANG, LIN], f"点名顺序被打乱了：{out}"


def test_named_person_not_duplicated():
    """点名的人不能在后面再出现一次。"""
    for i in range(30):
        out = plan_round(MEMBERS, speak_first=[LI], rng=random.Random(i))
        assert out.count(LI) == 1, f"种子{i}：{LI} 出现了两次（{out}）"


def test_candidate_list_keeps_full_membership():
    """候选名单仍覆盖全员（随机不是"少点几个人"）。"""
    for i in range(30):
        out = plan_round(MEMBERS, rng=random.Random(i))
        assert sorted(out) == sorted(MEMBERS), f"种子{i}：候选不全（{out}）"


def test_empty_and_single_member():
    assert plan_round([], rng=random.Random(0)) == []
    assert plan_round([LIN], rng=random.Random(0)) == [LIN]
    assert plan_round([LIN], speak_first=[LIN], rng=random.Random(0)) == [LIN]


def test_unknown_speak_first_is_ignored():
    """点名了不在群里的人 → 忽略（不能让幽灵进候选）。"""
    out = plan_round([LIN, LI], speak_first=["赵六"], rng=random.Random(3))
    assert sorted(out) == sorted([LIN, LI])


def test_rng_failure_does_not_break_the_round():
    """随机源炸了也不能让这一轮没人说话（保持原顺序即可）。"""
    class Boom:
        def shuffle(self, x):
            raise RuntimeError("rng 炸了")
    out = plan_round(MEMBERS, rng=Boom())
    assert sorted(out) == sorted(MEMBERS), f"rng 失败后候选丢了人：{out}"


def test_plan_round_does_not_seed_global_random():
    """★ 不许在 plan_round 里 seed 全局随机源 ★

    那会污染别处（战斗随机、掉落），让它们跟着可预测。

    ★ 断言只看**代码**、不看注释 ★：这段 docstring 本身就在解释"为什么不要 seed"，
      里面必然写着 `random.seed()` —— 不剥注释的话，解释越清楚测试越红
      （本文件第一版就这么红的，与 `test_turn_abort_contract` 同一个教训）。
    """
    src = inspect.getsource(plan_round)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    # 剥掉 docstring：三引号之间的内容整段去掉
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    assert "seed(" not in code, f"plan_round 里 seed 了随机源：{code[:400]}"


# ══════════════════════════════════════════════════════════════════
# F. 提示词：模型得知道 @ 是什么
# ══════════════════════════════════════════════════════════════════

def test_mention_nudge_exists_and_differs():
    """★ 语义⑥：被 @ 时要用**不同**的催话，且要讲清"只有你听见了" ★"""
    assert NUDGE_TEXT_MENTION != NUDGE_TEXT
    assert "只有你" in NUDGE_TEXT_MENTION, "没告诉模型这条只有他收到"
    assert "沉默" in NUDGE_TEXT_MENTION, "没保留弃权选项"


def test_mention_nudge_tells_model_not_to_use_at():
    """模型不该模仿出 `@玩家` 这种游戏里不存在的写法。"""
    assert "@" in NUDGE_TEXT_MENTION
    assert "不要用 @ " in NUDGE_TEXT_MENTION or "不要用@" in NUDGE_TEXT_MENTION, \
        "没告诉模型别写 @ 符号"


def test_regular_nudge_unchanged():
    """没有 @ 时文案必须与改动前逐字相同（既有行为零漂移）。"""
    assert NUDGE_TEXT == (
        "（轮到你说话了。只说你自己想说的话，不要写名字前缀，也不要替别人说话。"
        "若你此刻无话可说，只回【沉默】。）"
    )
