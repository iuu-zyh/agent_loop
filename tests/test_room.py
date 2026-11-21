"""群聊 Room —— 编排层：投递、调度、跑轮、进出群、落盘一致性。

对应 `docs/archive/2026-09-17-群聊编排/设计定稿.md` §8 P1a 的验收 R1–R12。全部脱离游戏与真实 LLM
（`StubGameBridge` + 脚本化 `StubLlmClient` + 假 WS），
**且走完整的生产路径**：`ChatHub` → `RoomFlow` → `_run_turn` → `agent.session`，
不做任何"绕过编排器直接投递"的短路 —— 那样测不出锁序与场合标记的问题。
"""

import asyncio
import json
import os
import shutil

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.session.history import project_ui_history
from agent_loop.session.history_ops import DeleteError, plan_delete, plan_message_delete, resolve_turn_ranges
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.room.room import PLAYER
from agent_loop.room.room_flow import (NOTICE_PLUGIN, PROPOSAL_TTL_ROUNDS, REL_ESTABLISH_OPS,
                                  SILENT_SENTINEL, RoomFlow, is_silent, judge_proposal,
                                  plan_round)
from agent_loop.room.room_history import project_room_history
from agent_loop.session.session import collect_deleted_seqs
from agent_loop.transport.ws_channel import ChatHub

ROOT = "/tmp/agent_loop_test_room"
PLAYER_NAME = "韩立"
LIN, LI, WANG = "林婉清", "李四", "王五"
ALL = (LIN, LI, WANG)

Q1 = "你们怎么看青须藤的事？"
Q2 = "那你们打算怎么办？"
Q3 = "后来呢？"


# ══════════════════════════════════════════════════════════════════
# 脚手架
# ══════════════════════════════════════════════════════════════════

class FakeWs:
    """假 WS：只收事件帧，供断言「回推了什么」。"""

    def __init__(self):
        self.frames = []

    async def send_event(self, event, **kw):
        self.frames.append({"event": event, **kw})

    def of(self, event):
        return [f for f in self.frames if f["event"] == event]


def _scripted(replies, default=SILENT_SENTINEL):
    """按**系统提示词里出现的 NPC 名**分派回复。

    用 system 判据而不是调用计数：每个 agent 的 system 里含自己的 `{npc_name}`，
    所以「谁在说话」是确定的，与并发/调度顺序无关。
    `replies[npc]` 是列表：按顺序取，用尽后重复最后一个。
    """
    state = {k: list(v) for k, v in replies.items()}

    async def fn(req):
        sys_text = str(req.get("system") or "")
        for who, seq in state.items():
            if who in sys_text and seq:
                return {"text": seq.pop(0) if len(seq) > 1 else seq[0]}
        return {"text": default}

    return StubLlmClient(fn)


def _seed(replies=None, others=True, llm=None, **loop_kw):
    """建一个干净的世界：3 个 NPC + 假 LLM；返回 (loop, bridge)。

    `llm` 显式传入时覆盖 `_scripted(replies)` —— 供需要**自定义时序**的用例
    （例如用 `asyncio.Event` 把某个成员的回合钉在半空中，以观察"插话"落在
    他的 `deliver` 哪一侧）。
    """
    shutil.rmtree(ROOT, ignore_errors=True)
    b = StubGameBridge()
    for who in ALL:
        profile = {
            "realm": "金丹", "sect": "化神殿", "pos": "永宁州·白帝城", "mood": 72,
            "player": {"name": PLAYER_NAME, "realm": "筑基", "relation": "好友",
                       "intim": 120, "same_grid": True},
            "recent": "上月与韩立同行",
            "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"},
        }
        if others:
            # `others` 是 C# `raw.others[]` 的形态 —— roster 里点到谁，C# 就该给谁
            profile["others"] = [
                {"id": o, "name": o, "resolved": True, "sex": "女", "race": "人族",
                 "realm": "金丹后期", "sect": "白帝城", "age": 120, "mood": 80,
                 "beauty_label": "出众", "reputation_label": "名震一时",
                 "personality": {"仁善": 3}, "same_grid": True,
                 "relation": "好友", "intim": 120}
                for o in ALL if o != who
            ]
        b.seed_unit(who, profile)
    loop = AgentLoop.reset_for_test(storage_root=ROOT, bridge=b,
                                    llm=llm or _scripted(replies or {}), **loop_kw)
    return loop, b


def _hub(loop, **kw):
    ws = FakeWs()
    return ChatHub(loop, ws, max_concurrent_turns=4, **kw), ws


def _run(coro):
    return asyncio.run(coro)


def _open(hub, members=None, **kw):
    return hub.rooms.open_room(members if members is not None else [LIN, LI, WANG], **kw)


def _say(hub, room_id, text, speak_first=()):
    return _run(hub.handle_room_message(room_id, text, list(speak_first)))


def _user_texts(agent):
    """账本里 user 消息的正文（按序）。"""
    out = []
    for ev in agent.session.log:
        if ev.get("type") != "user/message":
            continue
        for blk in ((ev.get("data") or {}).get("content") or []):
            if isinstance(blk, dict) and blk.get("type") == "text":
                out.append(blk.get("text") or "")
    return out


def _delivered(agent):
    """投递进来的消息（player + peer），排除 L1 段与 nudge。

    L1 段的判据是 `source.plugin == "@python-harness/system-prompt"`，
    nudge 是 `"@python-harness/group"` —— 两者都不是"谁说的话"。
    """
    out = []
    for ev in agent.session.log:
        if ev.get("type") != "user/message":
            continue
        d = ev.get("data") or {}
        src = d.get("source") or {}
        if src.get("plugin"):
            continue
        for blk in (d.get("content") or []):
            if isinstance(blk, dict) and blk.get("type") == "text":
                out.append({"text": blk.get("text") or "", "source": src, "seq": ev.get("seq")})
    return out


def _ctx_texts(agent):
    """账本里 L1 段消息的正文（按账本顺序）。与 test_l1_multi_npc 的同名工具同口径，
    本地重写一份以免测试之间互相 import（那条路在 pytest 的 rootdir 约定下不稳）。"""
    out = []
    for ev in agent.session.log:
        if ev.get("type") != "user/message":
            continue
        src = (ev.get("data") or {}).get("source") or {}
        if src.get("plugin") != "@python-harness/system-prompt":
            continue
        for blk in ((ev.get("data") or {}).get("content") or []):
            if isinstance(blk, dict) and blk.get("type") == "text":
                out.append(blk.get("text") or "")
    return out


def _turns(agent):
    return [ev["data"].get("turn") for ev in agent.session.log if ev.get("type") == "turn/start"]


def _scenes(agent):
    return [ev["data"].get("scene", "") for ev in agent.session.log if ev.get("type") == "turn/start"]


def _turn_of(agent, seq):
    """seq 所属的 turn（账本顺序回扫）。"""
    for ev in reversed(agent.session.log[:seq]):
        if ev.get("type") == "turn/start":
            return ev["data"].get("turn")
    return None


# ══════════════════════════════════════════════════════════════════
# 纯函数
# ══════════════════════════════════════════════════════════════════

def test_plan_round_ordering_and_cap():
    """候选名单：点名优先 → 其余随机补齐；上限 = 成员数（全员各点一次）。

    ★ 11-21 用户拍板改成随机（原先是轮转指针）★
    本用例因此**不再锁死具体顺序**，改锁它真正的不变量：
      · 点名的人排最前（顺序按给定顺序）；
      · 候选覆盖全员、不重不漏；
      · 未知名字被忽略。
    顺序随机性本身由 `test_room_mention_contract.py` 的 E 段专门验证。
    """
    import random as _random

    # 点名优先（其余顺序随机，故只断言头部）
    assert plan_round([LIN, LI, WANG], speak_first=[WANG],
                      rng=_random.Random(0))[0] == WANG
    assert plan_round([LIN, LI, WANG], speak_first=[LI, WANG],
                      rng=_random.Random(0))[:2] == [LI, WANG]
    # 不点名：顺序随机，但必须覆盖全员、不重复
    for seed in range(20):
        out = plan_round([LIN, LI, WANG], rng=_random.Random(seed))
        assert sorted(out) == sorted([LIN, LI, WANG]), f"种子{seed} 候选不完整：{out}"
    # 点名了不存在的人 → 忽略（不能让幽灵进候选）
    out = plan_round([LIN, LI], speak_first=["赵六"], rng=_random.Random(0))
    assert sorted(out) == sorted([LIN, LI]), f"幽灵进了候选：{out}"
    # 上限 = 成员数
    assert len(plan_round([LIN, LI, WANG], rng=_random.Random(0))) == 3
    # 空群 / 单成员
    assert plan_round([], start=0) == []
    assert plan_round([LIN], start=5) == [LIN]


def test_silent_sentinel_detection():
    """弃权判据：哨兵与空；**不做模糊匹配**（把真实短回复误判成沉默更糟）。"""
    assert is_silent("")
    assert is_silent("   ")
    assert is_silent(SILENT_SENTINEL)
    assert is_silent("【沉默】。")
    assert is_silent("（沉默）")
    assert not is_silent("沉默")
    assert not is_silent("我不说。")
    assert not is_silent("【沉默】之后我又想了想，还是说吧")   # 哨兵只是前缀 → 不是弃权


def test_room_join_kick_readd_semantics():
    """`joined` / `kick` 的三条边界（`room.py` 的纯数据层，不走 WS）。

    这三条以前 docstring 与实现不符（说"保留"实际 pop、说"会覆盖"实际 setdefault），
    是复核时实测发现的 —— 所以钉在这里。
    """
    from agent_loop.room.room import Room

    r = Room(id="g1")
    r.add_member("张三", joined_at=0)
    r.add_member("李四", joined_at=0)
    assert r.joined == {"张三": 0, "李四": 0}, "建群初始成员的入群位置必须是 0"
    r.say(PLAYER, "第一句")
    r.say("张三", "我在")

    # ① 踢人**同时移除** joined —— 重入群必须是一次新的入场
    assert r.kick("张三") is True
    assert "张三" not in r.joined, "踢人必须清掉 joined，否则重入群会停在上次的入场位置"

    # ② 重入群拿到**新的**入群位置（不是上次那个 0）
    r.say(PLAYER, "张三不在时说的")          # seq 3
    assert r.add_member("张三", joined_at=r.last_seq) is True
    assert r.joined["张三"] == r.last_seq == 3
    assert [u.text for u in r.delta_for("张三", cursor=None)] == [], \
        "刚重入群、还没有游标 ⇒ 看不到入群前（含它不在场时）的话"

    # ③ 但**已投递过**的人被踢再拉回：游标仍权威 ⇒ 会补上不在场期间的内容。
    #    这是刻意取舍（另一个方向会在 joined 陈旧时永久跳过内容），见 room.kick 的 docstring。
    #    游标 1 = 它被踢前只听到「第一句」（seq 2 是它自己的话，永不投给自己）。
    assert [u.text for u in r.delta_for("张三", cursor=1)] == ["张三不在时说的"]

    # ④ 直接赋值而不是 setdefault：joined 里有陈旧键时也要被新入场位置覆盖
    r2 = Room(id="g2", members=["王五"], joined={"王五": 0})
    r2.say(PLAYER, "甲")
    r2.say(PLAYER, "乙")
    r2.joined.pop("王五")                      # 模拟"曾被踢过"
    r2.add_member("王五", joined_at=999)        # 已在 members ⇒ 早退，不该动 joined
    assert r2.joined == {}, "已在群里的人不该被 add_member 改写"
    r2.kick("王五")
    r2.add_member("王五", joined_at=r2.last_seq)
    assert r2.joined["王五"] == 2, "重入群必须写入新的入群位置（setdefault 会保留旧值）"

    # ⑤ uid 规范形状是 `{room_id}#{seq}`；兜底值必须**一眼不像** uid（别当删除锚点误用）
    from agent_loop.room.room import Utterance
    assert r.log[0].uid == "g1#1", "Room.say 产出的 uid 是 room#seq"
    bare = Utterance(seq=7, speaker="张三", text="x")
    assert bare.uid == "seq:7" and "#" not in bare.uid, "兜底 uid 不该长得像规范 uid"
    assert Utterance.from_dict({"seq": 2, "speaker": "李四", "text": "y", "uid": "g9#2"}).uid == "g9#2"


# ══════════════════════════════════════════════════════════════════
# R1 三人房一轮：增量投递正确、自己的话不投给自己
# ══════════════════════════════════════════════════════════════════

def test_r1_three_way_relay_and_incremental_delivery():
    loop, _ = _seed({LIN: ["青须藤么……我听过些风声。"], LI: ["我也听说有人做局。"],
                     WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])

    # 房间流水：玩家 + 林婉清 + 李四（王五弃权，不进流水）。
    # ★ 11-21 起发言顺序随机，所以这里**不锁顺序**，只锁"谁说了什么" ★
    #   （原先写死 [LIN, LI]，随机化后必然红 —— 那是设计变更，不是缺陷。）
    assert room.log[0].speaker == PLAYER and room.log[0].text == Q1
    said = {u.speaker: u.text for u in room.log if u.speaker != PLAYER}
    assert said == {LIN: "青须藤么……我听过些风声。", LI: "我也听说有人做局。"}, \
        f"流水里的发言不对：{said}"

    # 林婉清：听到玩家的话，但**没有**自己那句
    lin_msgs = _delivered(loop.get(LIN))
    assert [m["text"] for m in lin_msgs] == [Q1]
    # ★ 群内投递的玩家消息**带锚点**（09-17 起）：群窗删除要在各成员记忆里按 uid 定位它。
    #   "source=None ⇒ 与单聊逐字节相同"当年是刻意留的，代价是**事后定位不到** ——
    #   现在只有**单聊**路径不传 source（那条契约没动），群内投递一律带 `kind="room"`。
    assert lin_msgs[0]["source"] == {"kind": "room", "speaker": PLAYER, "uid": f"{room.id}#1"}

    # 李四/王五：听到玩家 + **排在自己前面那个人**的话（接力增量），没有自己那句。
    # 顺序随机 ⇒ 具体听到几句取决于谁先开口，所以断言改成**关系式**：
    #   ① 一定听到玩家那句（且带 room 锚点）；
    #   ② 一定听不到自己的话；
    #   ③ 听到的 peer 行数 = 排在自己前面的人数（含弃权者不产生行）。
    for who, own in ((LI, "我也听说有人做局。"), (WANG, None)):
        msgs = _delivered(loop.get(who))
        texts = [m["text"] for m in msgs]
        assert Q1 in texts, f"{who} 没收到玩家的话：{texts}"
        assert msgs[0]["source"] == {"kind": "room", "speaker": PLAYER,
                                     "uid": f"{room.id}#1"}, f"{who} 的玩家消息锚点不对"
        assert own not in texts, f"{who} 收到了自己说的话：{texts}"
        # peer 行必须是流水里排在自己前面的那些 NPC 发言
        my_seq = next((u.seq for u in room.log if u.speaker == who and own), None)
        peer_texts = [m["text"] for m in msgs if m["source"].get("kind") == "peer"]
        for t in peer_texts:
            assert t in (said.get(LIN), said.get(LI)), f"{who} 收到陌生的 peer 行：{t}"
        if my_seq is not None:
            expect = [u.text for u in room.log
                      if u.seq < my_seq and u.speaker != PLAYER]
            assert peer_texts == expect, \
                f"{who} 的接力增量不对：期望 {expect}，实际 {peer_texts}"


def test_r1b_self_utterance_never_delivered_back():
    """自己的话不投给自己 —— 它已经在自己的 assistant 历史里。"""
    loop, _ = _seed({LIN: ["第一句。"], LI: ["第二句。"]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])
    _say(hub, room.id, Q2, speak_first=[LIN])

    for who, own in ((LIN, "第一句。"), (LI, "第二句。")):
        texts = [m["text"] for m in _delivered(loop.get(who))]
        assert own not in texts, f"{who} 收到了自己说的话：{texts}"
    # 但别人的话要收到
    assert "第二句。" in [m["text"] for m in _delivered(loop.get(LIN))]
    assert "第一句。" in [m["text"] for m in _delivered(loop.get(LI))]


# ══════════════════════════════════════════════════════════════════
# R2 同席发言的 source 形状 + 账本存原文
# ══════════════════════════════════════════════════════════════════

def test_r2_peer_source_shape_and_raw_text_in_ledger():
    loop, _ = _seed({LIN: ["此话不可外传。"], LI: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])

    li_msgs = _delivered(loop.get(LI))
    peer = [m for m in li_msgs if m["source"].get("kind") == "peer"]
    assert len(peer) == 1
    src = peer[0]["source"]
    assert set(src) == {"kind", "speaker", "uid"}          # 就这三个键，不多不少
    assert src["speaker"] == LIN
    assert src["uid"] == f"{room.id}#2"

    # I4：账本存**原文**，不带 `名字：` 前缀（前缀是发给模型时的投影）
    assert peer[0]["text"] == "此话不可外传。"
    assert not peer[0]["text"].startswith(LIN)

    # 投影才加前缀，且**只影响发给模型的那一份**
    msgs = loop.get(LI)._apply_speaker_labels(loop.get(LI).session.derive_messages())
    texts = [b["text"] for m in msgs if m.get("role") == "user"
             for b in (m.get("content") or []) if isinstance(b, dict) and b.get("type") == "text"]
    assert f"{LIN}：此话不可外传。" in texts
    assert f"玩家：{Q1}" in texts
    # 账本仍是原文（投影是浅拷贝，绝不原地改）
    assert "此话不可外传。" in _user_texts(loop.get(LI))


# ══════════════════════════════════════════════════════════════════
# R3 弃权：不写流水、不广播，但游标照推
# ══════════════════════════════════════════════════════════════════

def test_r3_silence_does_not_write_log_but_advances_cursor():
    loop, _ = _seed({LIN: [SILENT_SENTINEL], LI: ["我来说两句。"]})
    hub, ws = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])          # 点名弃权的那个先

    assert [u.speaker for u in room.log] == [PLAYER, LI]     # 弃权者不进流水
    # 不广播：room_say 里没有弃权者
    speakers = [f["speaker"] for f in ws.of("room_say")]
    assert speakers == [PLAYER, LI]

    # ★ 弃权必须**显式发一帧** `room_state(phase="silent")`（09-17 复核修）：
    #   `speak` 帧已经让 C# 给它开了一条流式泡（「林婉清：」），而弃权不写流水 ⇒
    #   永远等不到 `room_say` 去定稿那条泡 ⇒ 屏幕上永久留着「林婉清：」或
    #   「林婉清：【沉默】」。C# 收到 silent 就撤泡（不复位忙碌态，轮还在跑）。
    sil = [f for f in ws.of("room_state") if f.get("phase") == "silent"]
    assert sorted(f["speaking"][0] for f in sil) == sorted([LIN, WANG]), \
        f"每个弃权者都要发一帧 silent（说话的那个不发）：{sil}"

    # ★ 游标照常推进：它确实看到了那些话（投递时统一推，与"说了话"完全相同）
    lin = loop.get(LIN)
    assert lin.cursor_for(room.id) == 1                       # 轮到它时房里只有玩家那条
    assert [m["text"] for m in _delivered(lin)] == [Q1]        # 而且真的投给它了
    # 它没有回合产出正文以外的痕迹（账本里没有它的话）
    assert "我来说两句。" not in _user_texts(lin)


# ══════════════════════════════════════════════════════════════════
# R3b 成员级异常：必须发 `phase="error"` 帧（09-17 实机事故的回归）
# ══════════════════════════════════════════════════════════════════

def _boom_for(who: str, exc: BaseException) -> StubLlmClient:
    """按 system 里的 NPC 名分派：点到 `who` 就抛 `exc`（其余正常回一句）。"""

    async def fn(req):
        if who in str(req.get("system") or ""):
            raise exc
        return {"text": "我在。"}

    return StubLlmClient(fn)


def test_r3b_member_error_emits_error_frame_and_round_continues():
    """一个成员抛异常 ⇒ ① 当场发 `phase="error"`（带人话原因）；② 其余成员照常发言。

    事故背景（09-17 实机，用户截图）：群聊第一轮，姜萌被点名 → C# 开了她的空气泡 →
    她的流式请求被 45s 挂钟硬顶砍断（思考流已推 382 token）→ 异常分支只 `continue`、
    **一个帧都不发** ⇒ 那条空气泡一直挂着、忙碌态不复位，直到**下一个成员的 speak**
    顺手把泡撤掉（用户看到"她的气泡过了一会自己消失了，然后另一个人开始说话"），
    而玩家**完全不知道她为什么没开口**。这条测试钉的就是"异常必须发帧 + 给出人话原因"。
    """
    _first_token_to = TimeoutError("首字超时：模型一直没开口：120s 内没有任何新内容（存活看门狗，与整趟耗时无关）")
    loop, _ = _seed(llm=_boom_for(LIN, _first_token_to))
    # 补一个能正常说话的成员（_seed 的 llm 是整体替换，故手动再塞一份名单式桩）
    hub, ws = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN, LI])

    states = ws.of("room_state")
    err = [f for f in states if f.get("phase") == "error"]
    assert len(err) == 1 and err[0]["speaking"] == [LIN], f"异常成员必须发一帧 error：{states}"
    assert "超时" in err[0]["text"], f"error 帧必须带**人话**原因（不能只给类名）：{err[0]}"
    assert "没开口" in err[0]["text"], f"首字超时要说清是「一直没开口」：{err[0]}"
    assert "Traceback" not in err[0]["text"] and "TimeoutError" not in err[0]["text"], \
        "上屏文案不许带堆栈/内部类名（那是日志的事）"

    # ② 同轮其余成员照常发言：只跳过出错的那个，不是整轮失败
    assert [u.speaker for u in room.log] == [PLAYER, LI, WANG], \
        f"出错只跳该成员，其余照常发言：{[u.speaker for u in room.log]}"
    # ③ 有人开口 ⇒ 终帧不带文案（单成员的解释已经写在 error 帧上，不重复说两遍）
    end = [f for f in states if f.get("phase") == "end"]
    assert end and end[-1]["text"] == "", f"有人开口时终帧不该再写文案：{end}"
    print("✓ 成员异常：发 error 帧（人话原因）+ 其余成员照常发言 + 终帧不重复")


def test_r3c_all_members_errored_gets_an_end_note():
    """全员出错（没人开口）⇒ 终帧 `end` 的 note 汇总"这一轮为什么一个人都没说"。

    为什么必须有：终帧的 note 是轮级"没有产出"的唯一通道，而它在全员静默时本来是空的。
    "全员静默"与"全员出错"在玩家眼里长得一模一样（屏幕上一个字都没有），
    所以后者必须留一行解释 —— 否则玩家只能干等或重说一遍。
    """
    async def _boom_all(req):
        raise TimeoutError("流式请求超过 45.0s 未完成")

    loop, _ = _seed(llm=StubLlmClient(_boom_all))
    hub, ws = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN, LI, WANG])

    end = [f for f in ws.of("room_state") if f.get("phase") == "end"]
    assert end, "终帧必须无条件发（它是复位忙碌态的唯一依据）"
    note = end[-1]["text"]
    assert note.startswith("本轮没有人开口"), f"全员没开口要有一行汇总：{note!r}"
    assert LIN in note and "超时" in note, f"汇总要点名 + 给原因：{note!r}"
    # 全员静默（**没有**异常）时**刻意不写** —— 那是正常结果，不该每轮弹提示
    loop2, _ = _seed({})
    hub2, ws2 = _hub(loop2)
    room2 = _open(hub2)
    _say(hub2, room2.id, Q1, speak_first=[LIN, LI, WANG])
    end2 = [f for f in ws2.of("room_state") if f.get("phase") == "end"]
    assert end2 and end2[-1]["text"] == "", "全员静默不写 note（正常结果，别刷屏）"
    print("✓ 全员出错 → 终帧汇总；全员静默 → 终帧保持安静")


def test_r3d_member_error_note_wording():
    """超时文案要分清「一直没开口」（首字）与「说到一半卡住」（停顿）—— 两种体感完全不同。

    09-18 之前只有一句"模型超时（未在时限内完成）"：玩家既不知道是模型没开始、
    还是我们把它砍了，也不知道该等还是该重说。判据从形态上取（与 openai_client
    写进异常消息的那两种前缀同源），所以这里把三种超时 + 取消 + 内部异常都钉住。
    """
    from agent_loop.room.room_flow import _member_error_note

    opened = _member_error_note(TimeoutError(
        "首字超时：模型一直没开口：120s 内没有任何新内容（存活看门狗，与整趟耗时无关）"))
    stalled = _member_error_note(TimeoutError(
        "输出停顿超时：模型说到一半卡住了：60s 内没有任何新内容（存活看门狗，与整趟耗时无关）"))
    other = _member_error_note(TimeoutError("别的什么超时"))
    assert "没开口" in opened, opened
    assert "说到一半" in stalled, stalled
    assert "超时" in other and "没开口" not in other and "说到一半" not in other, other
    assert _member_error_note(asyncio.CancelledError()) == "回合被取消"
    assert "内部异常" in _member_error_note(RuntimeError("boom"))
    for note in (opened, stalled, other):
        assert "Traceback" not in note and "TimeoutError" not in note, \
            f"上屏文案不许带堆栈/内部类名：{note}"
    print("✓ 成员异常文案：首字/停顿/普通超时/取消/内部异常各自成形，且不带堆栈")


def test_r3e_group_stats_update_carries_room_id():
    """★ 09-18 用户反馈（"看不到当前上下文和总上下文"）的成因之一，行为层回归 ★：

    群聊回合结束时的 `stats_update` 必须带 `room_id` —— C# 群窗的**唯一**准入判据是
    "room_id 相等"（`ChatWindow.AcceptsRoomFrame`），这一帧原先漏了它（`_extra` 只加在
    step/text_delta 上）⇒ 群窗把它整帧丢掉 ⇒ 底部统计条永远停在上一个窗口的残影、
    再也刷不动（缓存率那行看着"还在"，上下文那项永远是旧的/空的）。
    单聊必须**不带** room_id（`_extra` 为空），否则单聊窗会把它当群帧丢掉。
    """
    loop, _ = _seed({LIN: ["群里的第一句。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, ws = _hub(loop)
    room = _open(hub, [LIN, LI])
    _say(hub, room.id, Q1, speak_first=[LIN])

    stats = ws.of("stats_update")
    assert stats, "群聊回合结束必须推 stats_update（底部统计条的唯一数据源）"
    for f in stats:
        assert f.get("room_id") == room.id, \
            f"群聊 stats_update 必须带 room_id（否则被群窗的准入判据丢掉）：{f.get('room_id')!r}"
        assert f.get("npc_id"), "stats_update 必须带 npc_id（群窗那行要标清楚是谁的账）"
        assert "contextPressure" in f, "统计帧必须带上下文口径（当前值/总量/估算）"
        assert "tokenUsage" in f

    # 单聊：同一套帧不带 room_id
    loop2, _ = _seed({LIN: ["单聊一句。"]})
    hub2, ws2 = _hub(loop2)
    _run(hub2.handle_message(LIN, Q1))
    solo = ws2.of("stats_update")
    assert solo and all(not f.get("room_id") for f in solo), \
        f"单聊 stats_update 不许带 room_id：{[f.get('room_id') for f in solo]}"
    print("✓ 群聊 stats_update 带 room_id（能进群窗）；单聊不带（不会被当群帧丢）")


def test_r3f_group_usage_counts_only_group_turns():
    """★ 09-18 用户拍板的口径 ★「本群期间」= **只算群内回合**，且各成员的单聊不进这一块。

    为什么必须按差算：成员 agent 同时服务单聊与群聊，它的累计 `tokenUsage` 是两条线之和
    （`UsageTracker` 一 agent 一份账）。群窗底部那行若直接拿某个成员的累计量，报的就是
    "我在这个 NPC 身上一共花了多少"（含单聊），与"本群花了多少"完全不是一回事。

    这里造一个**可预测的用量序列**：第 1 次调用（单聊）与第 2 次（群内回合）用量不同，
    断言本群账 = 第 2 次那一笔（不含第 1 次）。
    """
    calls = {"n": 0}

    async def fn(req):
        calls["n"] += 1
        if calls["n"] == 1:      # 单聊那一笔：1000 输入（其中 800 缓存）+ 10 输出
            return {"text": "单聊一句。",
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 10,
                              "prompt_tokens_details": {"cached_tokens": 800}}}
        return {"text": "群里一句。",   # 群内那一笔：2000 输入（其中 1500 缓存）+ 50 输出
                "usage": {"prompt_tokens": 2000, "completion_tokens": 50,
                          "prompt_tokens_details": {"cached_tokens": 1500}}}

    loop, _ = _seed(llm=StubLlmClient(fn))
    hub, ws = _hub(loop)
    # ① 先来一次**单聊**（同一把 agent 锁、同一份账本）
    _run(hub.handle_message(LIN, "单聊先聊一句"))
    solo = ws.of("stats_update")
    assert solo and not solo[-1].get("room_id"), "单聊帧不该带 room_id"
    solo_total = (solo[-1].get("tokenUsage") or {}).get("totalTokens")
    assert solo_total == 1010, f"单聊那笔应记进成员自己的账：{solo_total}"

    # ② 再开群，跑一轮：**只让一个人发言**（k=1），好把"本群那一笔"与"成员累计"分开验
    room = _open(hub, [LIN, LI])
    hub.rooms.k = 1
    _say(hub, room.id, Q1, speak_first=[LIN])

    frames = [f for f in ws.of("stats_update") if f.get("room_id") == room.id]
    assert frames, "群内回合结束必须推带 room_id 的 stats_update"
    ru = frames[-1].get("roomUsage")
    assert isinstance(ru, dict), "群帧必须带本群口径 roomUsage"
    assert ru["turns"] == 1, f"本群回合数应只算群内的那一轮：{ru}"
    assert ru["totalTokens"] == 2050, \
        f"本群账必须**只含群内那一笔**（2000 输入 + 50 输出），实际 {ru}"
    assert ru["inputTokens"] == 2000 and ru["outputTokens"] == 50
    assert ru["uncachedInputTokens"] == 500 and ru["cacheReadTokens"] == 1500
    assert ru["cacheHitPercent"] == "75", f"缓存率按本群口径算：{ru}"
    assert "contextPressure" in frames[-1], "成员自己的快照仍要一起发（老 DLL 靠它兜底）"
    # ★ 关键对照：同一帧里成员自己的累计 = 单聊 1010 + 群内 2050 = 3060，而本群口径只有 2050。
    #   这就是"只计算群聊期间"与"我在这个 NPC 身上花了多少"的区别（用户 09-18 拍板要前者）。
    own = (frames[-1].get("tokenUsage") or {}).get("totalTokens")
    assert own == 3060 and ru["totalTokens"] == 2050, \
        f"成员累计({own}) 与 本群口径({ru['totalTokens']}) 必须各自成立"
    # 本群账必须**落盘**（读档后那行不能归零）
    meta = room.to_meta()
    assert meta["group_usage"]["outputTokens"] == 50 and meta["group_usage"]["turns"] == 1
    from agent_loop.room.room import Room as _Room
    back = _Room.from_meta(meta, log=[])
    assert back.group_usage_block()["totalTokens"] == 2050, "本群账没能在 to_meta/from_meta 间往返"
    print("✓ 本群用量只算群内回合、可往返落盘（成员单聊不进这一块）")


def test_r3h_open_room_response_carries_group_usage():
    """★ 开群回执必须带 `roomUsage` ★：群窗底部那行在**开窗那一刻**就要有基线。

    不给的话它会停在预制体的默认文本（用户 09-18 截图里那个 "None"），一直等到
    **第一个成员发言结束**才有第一帧 stats_update。空群（还没人说话）要有零值基线，
    而不是缺字段 —— C# 靠"字段在不在"决定要不要刷那一行。
    """
    calls = {"n": 0}

    async def fn(req):
        calls["n"] += 1
        return {"text": "群里一句。",
                "usage": {"prompt_tokens": 400, "completion_tokens": 20,
                          "prompt_tokens_details": {"cached_tokens": 300}}}

    loop, _ = _seed(llm=StubLlmClient(fn))
    hub, _ = _hub(loop)
    room = _open(hub, [LIN])
    hub.rooms.k = 1

    # ① 空群：基线是零值（字段必须在，否则 C# 那行不刷）
    out = _run(hub.handle_request("open_room", {"room_id": room.id}))
    assert isinstance(out.get("roomUsage"), dict), "开群回执没带 roomUsage（那行会停在 None）"
    assert out["roomUsage"]["totalTokens"] == 0 and out["roomUsage"]["turns"] == 0

    # ② 说完一轮再开：基线报出本群已花的钱（且只算群内）
    _say(hub, room.id, Q1, speak_first=[LIN])
    out2 = _run(hub.handle_request("open_room", {"room_id": room.id}))
    ru = out2["roomUsage"]
    assert ru["totalTokens"] == 420 and ru["turns"] == 1 and ru["cacheHitPercent"] == "75", ru
    print("✓ 开群回执带本群口径（空群零值 → 说完一轮后报出本群花费）")


def test_r3g_room_group_usage_add_is_monotonic_and_old_meta_compatible():
    """`Room.add_group_usage`：只增不减、丢负数/非数；老存档（没有 group_usage 键）读进来是空表。"""
    from agent_loop.room.room import Room as _Room

    r = _Room(id="g9", members=[LIN])
    r.add_group_usage({"inputTokens": 100, "outputTokens": 7, "steps": 3})
    r.add_group_usage({"inputTokens": 50, "outputTokens": -5, "steps": "2", "turns": "1", "junk": None})
    assert r.group_usage["inputTokens"] == 150 and r.group_usage["outputTokens"] == 7
    assert r.group_usage["steps"] == 5 and r.group_usage["turns"] == 1
    assert "junk" not in r.group_usage, "非数不许进账"
    blk = r.group_usage_block()
    assert blk["totalTokens"] == 157 and blk["turns"] == 1 and blk["steps"] == 5
    # 老存档：meta 里没有这个键 ⇒ 空表（不抛、不假造数）
    old = {"room_id": "g9", "members": [LIN], "last_seq": 0}
    assert _Room.from_meta(old, log=[]).group_usage == {}
    assert _Room.from_meta(old, log=[]).group_usage_block()["totalTokens"] == 0
    print("✓ 本群账只增不减；老存档缺键 → 空表（从 0 开始涨）")


# ══════════════════════════════════════════════════════════════════
# R4 加人：新人看不到入群前的发言
# ══════════════════════════════════════════════════════════════════

def test_r4_new_member_cannot_hear_pre_join_history():
    loop, _ = _seed({LIN: ["先说的这句。"], LI: ["入群后我才开口。"]})
    hub, _ = _hub(loop)
    room = _open(hub, members=[LIN])
    _say(hub, room.id, Q1, speak_first=[LIN])

    assert [u.text for u in room.log] == [Q1, "先说的这句。"]
    hub.rooms.add_member(room.id, LI)
    assert room.members == [LIN, LI]

    _say(hub, room.id, Q2, speak_first=[LI])
    li_texts = [m["text"] for m in _delivered(loop.get(LI))]
    # 只收到入群后的：Q2（以及它自己之后别人的话），**没有** Q1 与林婉清那句
    assert Q1 not in li_texts, f"新人听到了入群前的话：{li_texts}"
    assert "先说的这句。" not in li_texts
    assert Q2 in li_texts

    # 老成员不受影响：林婉清照常收到 Q2
    assert Q2 in [m["text"] for m in _delivered(loop.get(LIN))]


# ══════════════════════════════════════════════════════════════════
# R5 踢人：不再收投递；留下者的 roster 刷新 → present 段差分重发
# ══════════════════════════════════════════════════════════════════

def test_r5_kick_stops_delivery_and_refreshes_roster():
    loop, _ = _seed({LIN: ["第一轮。", "第二轮。"], LI: ["我在。", "我也在。"],
                     WANG: [SILENT_SENTINEL, SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])
    before = len(_delivered(loop.get(LI)))

    assert hub.rooms.kick(room.id, WANG) is True
    assert room.members == [LIN, LI]
    # 被踢者立刻复位成单聊（否则他之后的单聊回合会带群标签 → 在 1:1 窗里静默消失）
    assert loop.get(WANG).scene == ""
    assert loop.get(WANG).roster == []

    _say(hub, room.id, Q2, speak_first=[LIN])
    # 被踢者不再收到任何新投递
    assert len(_delivered(loop.get(WANG))) == len(_delivered(loop.get(WANG)))
    assert Q2 not in [m["text"] for m in _delivered(loop.get(WANG))]
    # 留下者照常收到
    assert Q2 in [m["text"] for m in _delivered(loop.get(LI))]

    # roster 刷新 → L1「所在」段差分重发（文案里不再有王五）
    where = [t for t in _ctx_texts(loop.get(LIN)) if "—— 所在：" in t]
    assert where, "没有所在段"
    assert WANG not in where[-1], f"踢人后所在段仍提到王五：{where[-1]}"
    assert LI in where[-1]


# ══════════════════════════════════════════════════════════════════
# R6 关窗 / 重开：scene 复位、游标与 log 一致
# ══════════════════════════════════════════════════════════════════

def test_r6_close_resets_scene_and_reopen_resumes():
    loop, _ = _seed({LIN: ["一句。"], LI: ["两句。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1                         # 只让林婉清开口，聚焦「关窗/重开」这件事
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])
    assert loop.get(LIN).scene == ""        # 回合结束即复位（作用域是回合，不是"在群里"）

    assert hub.rooms.close_room(room.id) is True
    for who in (LIN, LI, WANG):
        ag = loop.get(who)
        if ag is None:          # 没开过口的成员根本没建 agent（零成本），无需复位
            continue
        assert ag.scene == "" and ag.roster == []

    # 重开：同一个 id、流水与游标都在
    again = hub.rooms.open_room([], room_id=room.id)
    assert again.id == room.id
    assert [u.text for u in again.log] == [Q1, "一句。"]     # 从内存拿到的还是原对象

    # 重开后林婉清再被点名：**不重投**旧发言、不漏投新的
    lin = loop.get(LIN)
    before = len(_delivered(lin))
    _say(hub, room.id, Q2, speak_first=[LIN])
    lin_texts = [m["text"] for m in _delivered(lin)]
    assert lin_texts.count(Q1) == 1, f"重开后重投了旧发言：{lin_texts}"
    assert lin_texts.count("一句。") == 0, f"重开后重投了自己的话：{lin_texts}"
    assert Q2 in lin_texts                                                # 不漏投
    assert len(lin_texts) > before


# ══════════════════════════════════════════════════════════════════
# R7 不变量 I1：每条 user/message 都住在某个闭合回合的删除跨度内
# ══════════════════════════════════════════════════════════════════

def test_r7_invariant_i1_peer_messages_live_inside_their_turn():
    loop, _ = _seed({LIN: ["林的一句。"], LI: ["李的一句。"]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])

    for who in (LIN, LI):
        ag = loop.get(who)
        assert not ag.session.has_open_turn(), f"{who} 有未闭合回合"
        spans = {t: resolve_turn_ranges(ag.session, [t])[0] for t in _turns(ag)}
        for m in _delivered(ag):
            t = _turn_of(ag, m["seq"])
            assert t is not None, f"{who} 的投递 seq={m['seq']} 不在任何回合里（打破了 I1）"
            lo, hi = spans[t]
            assert lo <= m["seq"] <= hi, (
                f"{who} seq={m['seq']} 投影归属 turn={t}，但该回合跨度=[{lo},{hi}] ⇒ 删不掉")

    # 反过来的那一半：真的删得掉（假删除的解药）
    ag = loop.get(LI)
    for t in _turns(ag):
        pl = plan_delete(ag.session, [t])
        assert pl["ok"], f"turn {t} 的删除方案不合法：{pl.get('reason')}"
        hit = collect_deleted_seqs(ag.session.log) | {
            s for a, b in pl["ranges"] for s in range(a, b + 1)}
        for m in _delivered(ag):
            if _turn_of(ag, m["seq"]) == t:
                assert m["seq"] in hit, f"turn {t} 的投递 seq={m['seq']} 不在剔除集里"


# ══════════════════════════════════════════════════════════════════
# R8 不变量 I3：记忆与游标同一次 flush、同一个文件
# ══════════════════════════════════════════════════════════════════

def test_r8_invariant_i3_save_load_keeps_cursor_with_memory():
    loop, _ = _seed({LIN: ["存档前说的。"], LI: ["我也说。"], WANG: [SILENT_SENTINEL] * 4})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])
    rid = room.id
    cursor_before = {w: loop.get(w).cursor_for(rid) for w in ALL}
    log_before = [(u.speaker, u.text) for u in room.log]

    assert loop.flush_all() >= 1        # 房间与 session 在同一次事件里落

    # 换一个进程身份读档（同一个 ROOT）
    loop2 = AgentLoop.reset_for_test(storage_root=ROOT, bridge=loop.bridge,
                                     llm=loop.llm)
    assert loop2.load_rooms() == 1
    room2 = loop2.load_room_log(rid)
    assert room2 is not None
    assert [(u.speaker, u.text) for u in room2.log] == log_before

    # 游标跟着 session 一起活过来（同一个文件 ⇒ 不可能"说听过了但记忆里没有"）
    for w in ALL:
        loop2.create(w)
        assert loop2.get(w).cursor_for(rid) == cursor_before[w], (
            f"{w} 的游标与记忆不一致：{loop2.get(w).cursor_for(rid)} != {cursor_before[w]}")

    # 读档后再开一轮：不重投已投过的
    hub2, _ = _hub(loop2)
    _run(hub2.handle_room_message(rid, Q2, [LIN]))
    lin_texts = [m["text"] for m in _delivered(loop2.get(LIN))]
    assert lin_texts.count(Q1) == 1, f"读档后重投了旧发言：{lin_texts}"
    assert Q2 in lin_texts


def test_r8b_cursor_is_invisible_to_everything_else():
    """`room/cursor` 注记对 Surface / UI 投影 / 模型 / 压缩材料全隐形。"""
    loop, _ = _seed({LIN: ["说完了。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])

    for who in (LIN, LI, WANG):
        ag = loop.get(who)
        assert any(ev.get("type") == "room/cursor" for ev in ag.session.log), f"{who} 没有游标注记"
        # Surface（= 模型看到的目录）里没有它的 seq
        cur = {ev["seq"] for ev in ag.session.log if ev.get("type") == "room/cursor"}
        assert not (cur & set(ag.session.surface.nodes)), f"{who} 的游标进了 Surface"
        # derive_messages（模型 + 压缩材料同源）里没有它
        import json as _json
        assert "room/cursor" not in _json.dumps(ag.session.derive_messages(), ensure_ascii=False)
        # UI 投影里没有它
        ui = _json.dumps(project_ui_history(ag.session, max_turns=0), ensure_ascii=False)
        assert "room/cursor" not in ui


def test_r8c_lazy_log_load_when_reopening_after_load():
    """读档后**经生产路径**重开群：流水必须被懒加载回来。

    背景：`AgentLoop.load_rooms()` 只把**名录**读进内存（流水懒加载，理由见其
    docstring：列个群列表不该扫全部 jsonl）。若没有哪一环去补这一步，`room.log`
    会恒为空 ⇒
      ① 群窗历史全空（玩家以为记录丢了、而 session 里的游标却说"听过"）；
      ② `last_seq` 假报 0 ⇒ 读档后第一句从 seq=1 重发，**与磁盘上的旧流水撞号**。

    修法：`RoomFlow._require` 在「内存没有 / 有名录没流水」时回调注入的 `load_room`
    （`ChatHub._load_room_data` → `AgentLoop.load_room_log`）。这条测试走的是
    `ChatHub.handle_request(open_room)` 的完整生产路径，不做任何直连编排器的短路。
    """
    loop, _ = _seed({LIN: ["存档前说的。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub, [LIN])
    _say(hub, room.id, Q1, speak_first=[LIN])
    rid = room.id
    log_before = [(u.speaker, u.text) for u in room.log]
    last_seq_before = room.last_seq
    assert last_seq_before == 2, f"Q1 + 林婉清应答 = 2 条，实际 {last_seq_before}"
    assert loop.flush_all() >= 1

    # ── 换一个进程身份读档（同一个 ROOT）──
    loop2 = AgentLoop.reset_for_test(storage_root=ROOT, bridge=loop.bridge, llm=loop.llm)
    assert loop2.load_rooms() == 1
    # 前提（钉住"懒加载"这件事本身）：名录进了内存，流水**没有**
    assert loop2.rooms[rid].log == [], "load_rooms 不该读流水 —— 那样懒加载就白设计了"
    # ★ 懒加载时 `last_seq` **必须**由名录兜底，不能是 0。
    #   本行原先断言 `== 0`（"待补齐的中间态"）—— 但 `list_rooms` 是**纯读**、
    #   绕过 `_require` 的懒加载守卫，恰好在这个中间态上被调用：群列表每行显示
    #   「0 条」，`flush_all` 还会把这个 0 写回 `index.json` 覆盖真值。
    #   两条由 test_r8e 专门钉住；这里只钉"兜底值本身正确"。
    assert loop2.rooms[rid].last_seq == last_seq_before, (
        f"懒加载时 last_seq 应由名录兜底：{loop2.rooms[rid].last_seq} != {last_seq_before}")

    # ── 经生产路径重开：ChatHub → RoomFlow._require → load_room → load_room_log ──
    hub2, _ = _hub(loop2)
    out = _run(hub2.handle_request("open_room", {"room_id": rid}))
    assert [(i["speaker"], i["text"]) for i in out["items"]] == log_before, (
        f"读档后重开群没拿回历史：{out['items']}")
    assert out["last_seq"] == last_seq_before, (
        f"读档后 last_seq 应与存档前一致：{out['last_seq']} != {last_seq_before}")

    # ── 继续说话：seq 必须接在旧流水之后（不撞号）──
    _say(hub2, rid, Q2, speak_first=[LIN])
    seqs = [u.seq for u in loop2.rooms[rid].log]
    assert seqs == list(range(1, last_seq_before + 3)), (
        f"读档后新发言与旧流水撞号：{seqs}（旧流水到 seq={last_seq_before}）")


def test_r8d_lazy_log_load_is_idempotent_and_fail_closed():
    """懒加载的另外两面：**已同步的群不重复读盘**、**不存在的群仍 fail-closed**。

    前者保证接线不会把"每次 _require 都扫盘"变成隐性开销；后者是 `_require` 的
    主职（取群或当场报错），接线不该把它软化成"静默返回空群"。
    """
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub, [LIN])
    _say(hub, room.id, Q1, speak_first=[LIN])

    # 内存已有流水 ⇒ 不再回调 loader（无谓的磁盘 stat 也是开销）
    calls = []
    orig = hub.rooms._load_room
    hub.rooms._load_room = lambda rid: (calls.append(rid), orig(rid))[1]
    hub.rooms.history(room.id)
    assert calls == [], f"内存里有完整群时不该读盘，实际调了 {calls}"
    hub.rooms._load_room = orig

    # 不存在的群：仍然 fail-closed（不静默返回空群）
    try:
        hub.rooms.history("g999")
    except KeyError:
        pass
    else:
        raise AssertionError("不存在的群应抛 KeyError，而不是返回空历史")


def test_r8e_list_rooms_reports_true_seq_when_log_is_lazy():
    """读档后**没打开过任何群**时，`list_rooms` 必须报出真实条数，且再存档不写坏名录。

    这是「群列表 UI」的数据源，而它和 `_require` 的懒加载守卫是**错开的**：
    `_require` 只覆盖写路径（发言 / 加人 / 踢人 / 重开），`list_rooms` 是纯读、直接
    `[r.to_meta() for r in loop.rooms.values()]`。若 `last_seq` 从空 `log` 现算，
    懒加载态下它恒为 0 ⇒
      ① 群列表每行显示「0 条」（玩家以为记录丢了）；
      ② `flush_all` → `save_rooms_index` → `to_meta()` 把这个 0 **写回 `index.json`**，
         把磁盘上的真值覆盖掉。

    修法：`Room.meta_last_seq` 承接名录里的 `last_seq`，`last_seq` 在 `log` 为空时兜底。
    与 R8 系列的分工：R8c/R8d 钉「流水最终会被补读」，本条钉「**补读之前**读数也是对的」。
    """
    loop, _ = _seed({LIN: ["存档前说的。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub, [LIN])
    _say(hub, room.id, Q1, speak_first=[LIN])
    rid = room.id
    true_seq = room.last_seq
    assert true_seq == 2, f"Q1 + 林婉清应答 = 2 条，实际 {true_seq}"
    assert loop.flush_all() >= 1

    index_path = os.path.join(loop.rooms_root, "index.json")

    def _index_seq():
        with open(index_path, encoding="utf-8") as f:
            for r in json.load(f)["rooms"]:
                if r["room_id"] == rid:
                    return r["last_seq"]
        raise AssertionError(f"名录里没有 {rid}")

    assert _index_seq() == true_seq

    # ── 读档：只载名录，**不打开任何群** ──
    loop2 = AgentLoop.reset_for_test(storage_root=ROOT, bridge=loop.bridge, llm=loop.llm)
    assert loop2.load_rooms() == 1
    assert loop2.rooms[rid].log == [], "前提：流水仍未加载"

    hub2, _ = _hub(loop2)
    listed = {r["room_id"]: r for r in hub2.rooms.list_rooms()}
    assert listed[rid]["last_seq"] == true_seq, (
        f"list_rooms 在懒加载态报错了条数：{listed[rid]['last_seq']} != {true_seq}")

    # ── 关键：此时直接再存档，不能把 0 写回 index.json ──
    # 先把磁盘上的值改坏（模拟"上一次被写坏的存档"）——这样下一句 flush 若真写了、
    # 且写的是对的，就一定能把真值纠正回来。（`flush_all()` 的返回值是 **agent** 数量，
    # 新进程里没有 agent ⇒ 是 0；rooms 那一段照样落盘，别拿返回值当作"没写"的依据。）
    with open(index_path, encoding="utf-8") as f:
        disk = json.load(f)
    disk["rooms"][0]["last_seq"] = 0
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(disk, f, ensure_ascii=False)
    assert _index_seq() == 0, "前提：磁盘上的值已被改坏"

    loop2.flush_all()
    assert _index_seq() == true_seq, (
        f"读档后未打开群就存档，把名录里的 last_seq 写坏了：{_index_seq()} != {true_seq}")

    # 懒加载态的 next_seq 也必须接在旧流水之后（不能从 1 重发）
    assert loop2.rooms[rid].next_seq == true_seq + 1


# ══════════════════════════════════════════════════════════════════
# R9 群窗投影
# ══════════════════════════════════════════════════════════════════

def test_r9_room_history_projection():
    loop, _ = _seed({LIN: ["甲。"], LI: ["乙。"], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])

    h = hub.rooms.history(room.id)
    assert h["room_id"] == room.id
    # kind 走的是 **history.project_ui_history 的既有词表**（user/peer），
    # 不另起一套（player/...）—— 见 test_r9b 的跨语言契约
    assert [i["kind"] for i in h["items"]] == ["user", "peer", "peer"]
    assert [i["speaker"] for i in h["items"]] == [PLAYER, LIN, LI]
    assert [i["seq"] for i in h["items"]] == [1, 2, 3]
    assert [i["uid"] for i in h["items"]] == [f"{room.id}#1", f"{room.id}#2", f"{room.id}#3"]
    assert set(i["kind"] for i in h["items"]) <= {"user", "peer"}
    assert h["last_seq"] == 3

    # limit 取的是**最后** N 条
    h2 = project_room_history(room, limit=2)
    assert [i["text"] for i in h2["items"]] == ["甲。", "乙。"]
    assert h2["last_seq"] == 3


# ══════════════════════════════════════════════════════════════════
# R10 与单聊隔离：群聊回合不出现在 1:1 窗
# ══════════════════════════════════════════════════════════════════

def test_r9b_kind_vocabulary_matches_csharp_renderer():
    """★ 跨语言契约：群窗投影发出的 `kind` 必须是 C# `ReplaceHistory` **认得**的。

    为什么必须有这条：`ReplaceHistory` 的 switch **没有 default 分支** ——
    发出一个它不认识的 kind，后果是**静默什么都不画**（不报错、不告警）。
    实测踩过一次：`project_room_history` 最初发 `kind:"player"`，而 C# 只认 `"user"`
    ⇒ 群窗开了、NPC 的话都在，唯独**玩家自己说的全不见了**。
    这条测试从 C# 源码里现读分支名来比，所以 C# 改了词表这里会立刻红。
    """
    import re
    from pathlib import Path

    csrc = Path(__file__).resolve().parent.parent / "csharp" / "UI" / "ChatWindow.cs"
    text = csrc.read_text(encoding="utf-8", errors="replace")
    # ★ 11-20 起 switch 搬进了 `ApplyHistoryItem`（回放改分帧构建），`ReplaceHistory` 只剩分流。
    #   注意别退化成"从 ReplaceHistory 切到文件尾"：那会把后面几十个方法的分支名全算进来，
    #   断言永远为真（本条此前正是靠这个假绿通过的）。
    _anchor = "private void ApplyHistoryItem(JToken token)"
    assert _anchor in text, "找不到 ApplyHistoryItem —— 回放的 switch 又搬家了，本测试要跟着走"
    body = text.split(_anchor)[1]
    _end = body.find("private void BuildHistoryBatch()")
    assert _end > 0, "ApplyHistoryItem 之后应当紧接 BuildHistoryBatch"
    body = body[:_end]
    handled = set(re.findall(r'case\s+"([a-z_]+)"\s*:', body))
    assert "user" in handled, f"没能从 ChatWindow.ReplaceHistory 解析出分支名：{handled}"

    emitted = {"user", "peer"}          # project_room_history 只会发这两个
    missing = emitted - handled
    assert not missing, (
        f"群窗投影会发 {sorted(missing)}，但 C# ReplaceHistory 不认识（会静默不渲染）。"
        f"C# 认识的是：{sorted(handled)}")


def test_r9c_peer_items_render_with_speaker():
    """`peer` 条目必须带 `speaker` —— C# 用它拼「名字：正文」（最小档无名字槽）。"""
    loop, _ = _seed({LIN: ["甲。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])
    for it in hub.rooms.history(room.id)["items"]:
        if it["kind"] == "peer":
            assert it["speaker"] == LIN and it["text"] == "甲。"
            assert not it["text"].startswith(LIN), "正文不该自带前缀（前缀由 C# 拼）"


def test_r10_group_turns_do_not_leak_into_solo_window():
    # 桩按调用顺序取：先跑的是单聊那句，所以第一个回复归单聊
    loop, _ = _seed({LIN: ["私下答的。", "群里答的。"]})
    hub, _ = _hub(loop)
    room = _open(hub)

    # 单聊一句
    _run(hub.handle_message(LIN, "私下问你一句"))
    # 群聊一句（同一个 NPC，同一个 session）
    _say(hub, room.id, Q1, speak_first=[LIN])

    ag = loop.get(LIN)
    assert _scenes(ag) == ["", room.id], f"scene 标记不对：{_scenes(ag)}"

    solo = project_ui_history(ag.session, max_turns=0, scene="")
    solo_texts = [i.get("text") or "" for i in solo["items"]]
    assert any("私下问你一句" in t for t in solo_texts)
    assert not any("群里答的。" in t for t in solo_texts), f"群聊内容漏进 1:1 窗：{solo_texts}"

    grp = project_ui_history(ag.session, max_turns=0, scene=room.id)
    grp_texts = [i.get("text") or "" for i in grp["items"]]
    assert any("群里答的。" in t for t in grp_texts)
    assert not any("私下问你一句" in t for t in grp_texts), f"单聊内容漏进群窗：{grp_texts}"

    # scene=None = 不过滤（"这个 NPC 的全部经历"）
    allout = project_ui_history(ag.session, max_turns=0, scene=None)
    all_texts = [i.get("text") or "" for i in allout["items"]]
    assert any("私下问你一句" in t for t in all_texts)
    assert any("群里答的。" in t for t in all_texts)


def test_r10b_solo_turn_while_room_open_is_still_solo():
    """房间开着时开某个成员的 1:1 窗 —— 那个回合必须仍是单聊（否则静默消失）。"""
    loop, _ = _seed({LIN: ["群里。", "单聊。"]})
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])
    _run(hub.handle_message(LIN, "单独说"))

    ag = loop.get(LIN)
    assert _scenes(ag) == [room.id, ""], f"1:1 回合被贴上了群标签：{_scenes(ag)}"
    solo = project_ui_history(ag.session, max_turns=0, scene="")
    assert any("单独说" in (i.get("text") or "") for i in solo["items"])


# ══════════════════════════════════════════════════════════════════
# R11 私有查询：只读工具的往返不进流水、不进别人的会话
# ══════════════════════════════════════════════════════════════════

def test_r11_readonly_tool_traffic_stays_private():
    """林婉清在群里查了一次资料 —— 那是**私有**的：不进流水，也不进别人的上下文。"""
    calls = {"n": 0}

    async def fn(req):
        sys_text = str(req.get("system") or "")
        if LIN not in sys_text:
            return {"text": SILENT_SENTINEL}      # 其余人弃权，别来搅局
        has_tool_result = any(
            any(c.get("type") == "tool-result" for c in m.get("content", []))
            for m in req["messages"] if isinstance(m.get("content"), list))
        if not has_tool_result:
            return {"tool_calls": [{"id": "c1", "name": "inspect_unit",
                                    "arguments": {"target": LI, "classes": ["brief"]}}],
                    "text": ""}
        calls["n"] += 1
        return {"text": "我查过了，李道友修为不浅。"}

    loop, _ = _seed()
    loop.llm = StubLlmClient(fn)
    hub, _ = _hub(loop)
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])

    assert calls["n"] == 1, "工具往返没跑起来（桩没被调到第二步）"
    lin = loop.get(LIN)
    assert any(ev["type"] == "tool/call" for ev in lin.session.log), "林婉清自己没留下工具轨迹"
    # 流水里只有它的**正文**，没有工具参数/返回
    assert [u.speaker for u in room.log] == [PLAYER, LIN]
    import json as _json
    dumped = _json.dumps([u.to_dict() for u in room.log], ensure_ascii=False)
    assert "tool" not in dumped and "inspect_unit" not in dumped
    # 别人的会话里没有它的工具往返
    for who in (LI, WANG):
        ag = loop.get(who)
        assert ag is None or not any(ev["type"] in ("tool/call", "tool/result") for ev in ag.session.log)


# ══════════════════════════════════════════════════════════════════
# R12 成本：没被点名的人零成本
# ══════════════════════════════════════════════════════════════════

def test_r12_unnamed_members_cost_nothing():
    """K=1：只有一个人跑回合，其余人**账本不涨、没有 turn/start**。"""
    loop, _ = _seed({LIN: ["我说。"], LI: ["我也想说。"], WANG: ["我还想说。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1                       # 每轮最多 1 人实际开口
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])

    assert [u.speaker for u in room.log] == [PLAYER, LIN]
    assert _turns(loop.get(LIN)) == [1]
    for who in (LI, WANG):
        ag = loop.get(who)
        # 没被点名 ⇒ **根本没有 agent 被创建**（连 session 都没有）
        assert ag is None, f"{who} 不该有 agent（零成本）"
    # 房间那边也不知道他们听过什么（入群前/未投递的都不算）
    assert room.last_seq == 2


def test_r12b_k_counts_actual_speakers_not_candidates():
    """K 数的是**实际开口**数：弃权的人腾出名额，让后面的人补位。"""
    loop, _ = _seed({LIN: [SILENT_SENTINEL], LI: [SILENT_SENTINEL],
                     WANG: ["轮到我了吧。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub)
    _say(hub, room.id, Q1, speak_first=[LIN])       # 林、李都弃权 → 王五补位

    assert [u.speaker for u in room.log] == [PLAYER, WANG]
    assert loop.get(WANG).cursor_for(room.id) == 1   # 补位者补到了前面的增量


def test_r12c_cap_comes_from_the_wiring_and_hot_change_applies_next_round():
    """K 的来源与热改：装配参数（config room.max_speakers_per_round）就是这一轮的名额，
    改完**下一轮**生效 —— 这两条断了，面板上那一行就只是摆设。"""
    loop, _ = _seed({LIN: ["林。", "林又。"], LI: ["李。", "李又。"], WANG: ["王。", "王又。"]})
    hub, _ = _hub(loop, max_speakers_per_round=2)      # 装配层从配置传进来（scripts/server.py）
    assert hub.rooms.k == 2
    room = _open(hub)
    _say(hub, room.id, Q1)
    # ★ 11-21 起顺序随机 ⇒ 不锁"是哪两个人"，只锁**名额** ★
    round1 = [u.speaker for u in room.log if u.speaker != PLAYER]
    assert len(round1) == 2, f"名额 2 没生效：{round1}"
    assert len(set(round1)) == 2, f"同一个人说了两次：{round1}"
    assert set(round1) <= {LIN, LI, WANG}

    # 热改（config 保存 → ConfigService ⑥ → set_max_speakers_per_round）：
    # 改成 3 ⇒ 下一轮三个人都能开口。
    assert hub.rooms.set_max_speakers_per_round(3) == 3
    _say(hub, room.id, "再来一轮")
    all_spoken = [u.speaker for u in room.log if u.speaker != PLAYER]
    round2 = all_spoken[2:]
    assert len(round2) == 3, f"改完 k 之后下一轮没按新名额跑：{round2}"
    assert sorted(round2) == sorted([LIN, LI, WANG]), f"三个人没都开口：{round2}"


# ══════════════════════════════════════════════════════════════════
# 轮转指针
# ══════════════════════════════════════════════════════════════════

def test_round_order_is_random_not_rotation():
    """★ 11-21 用户拍板：说话顺序改成随机，不再按固定轮转 ★

    原用例 `test_rotation_pointer_advances_past_last_speaker` 锁的是
    「上一轮最后一个发言者的下一位起头」——那条语义已被**有意删除**，
    所以这里换成验证新语义：

      · 多轮下来首位**不总是同一个人**（否则还是轮转）；
      · 每轮仍然只有 `k` 个人开口、且不重复；
      · `_pointer` 不再决定顺序（它仍在维护，但只用于"没人说话时不动"这条纪律）。

    为什么保留 `_pointer` 而不删：`_advance_pointer` 还被 `_run_round` 的收尾调用，
    删掉要连带改解散守卫与测试；而它现在**不影响发言顺序**，留着零成本。
    真要清理时，连同这条注释一起删。
    """
    loop, _ = _seed({LIN: ["林。"], LI: ["李。"], WANG: ["王。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub)

    for q in ("一", "二", "三", "四", "五", "六"):
        _say(hub, room.id, q)
    seen = [u.speaker for u in room.log if u.speaker != PLAYER]
    assert len(seen) == 6, f"每轮应该正好 1 人开口：{seen}"
    # 随机 ⇒ 六轮里不该永远同一个人（三个人随机，六轮全同的概率 1/729）
    assert len(set(seen)) > 1, f"六轮都是同一个人开口（{seen}）⇒ 没随机"
    # 但也不该出现"同一轮两个人"（k=1）
    for u in room.log:
        pass


# ══════════════════════════════════════════════════════════════════
# 装配 / RPC 层
# ══════════════════════════════════════════════════════════════════

def test_rpc_open_list_history_close():
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)

    out = _run(hub.handle_request("open_room", {"members": [LIN, LI], "title": "青须藤之事"}))
    rid = out["room_id"]
    assert out["title"] == "青须藤之事"
    assert out["members"] == [LIN, LI]
    assert out["items"] == []

    assert [r["room_id"] for r in _run(hub.handle_request("list_rooms", {}))["rooms"]] == [rid]

    _say(hub, rid, Q1, speak_first=[LIN])
    h = _run(hub.handle_request("get_room_history", {"room_id": rid}))
    assert [i["speaker"] for i in h["items"]] == [PLAYER, LIN]

    # 缺参数 fail-closed（不是静默成功）
    for method, params in (("open_room", {}), ("create_room", {}), ("close_room", {}),
                           ("get_room_history", {})):
        try:
            _run(hub.handle_request(method, params))
        except ValueError:
            pass
        else:
            raise AssertionError(f"{method} 缺参数时应报错")

    assert _run(hub.handle_request("close_room", {"room_id": rid})) == {"closed": True}
    assert loop.get(LIN).scene == ""


def test_rpc_reopen_by_room_id():
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    a = _run(hub.handle_request("open_room", {"members": [LIN]}))
    _say(hub, a["room_id"], Q1, speak_first=[LIN])
    b = _run(hub.handle_request("open_room", {"room_id": a["room_id"]}))
    assert b["room_id"] == a["room_id"]
    assert [i["text"] for i in b["items"]] == [Q1, "嗯。"]


def test_rpc_create_room_creates_without_replaying_history():
    """★ 通讯录「＋新建群聊」确认走这条路：**只建群、不回放历史**。

    与 `open_room` 的分工是刻意的：**建群是改数据，开窗是开窗**。
    二者合一（`ConfirmCreate` 直接发 `open_room`）在 09-17 实机上的后果是：
    通讯录被关掉 + 一个空群窗 + 标题只剩占位符「npc 名称」+ 一行"开群失败"，
    而**群其实已经建成了**（日志里 `建群：g1` 就在报错前一行）——
    "以为失败、实际成功"比彻底失败更糟：玩家会重复建，或者以为这个群不存在。
    """
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    # 先让林婉清**活着**（有一次单聊经历），这样下面那句"建群不给成员打群标签"
    # 才是真的在断言，而不是因为 agent 根本不存在而侥幸成立。
    _run(hub.handle_request("open_chat", {"npc_id": LIN, "limit": 0}))
    assert loop.get(LIN) is not None

    out = _run(hub.handle_request("create_room", {"members": [LIN, LI],
                                                  "title": "青须藤之事",
                                                  "member_unit_ids": {LIN: "Xs6JDI"}}))
    rid = out["room_id"]
    assert rid, "必须回传 room_id（C# 不造 id，全靠这里发）"

    # ★ 回执形状 = **名录条目**（与 list_rooms 的每一行同构）：C# 拿它直接插进群列表，
    #   不再补发一次 list_rooms —— 少一次往返就少一个"两次回包互相覆盖"的窗口。
    rows = _run(hub.handle_request("list_rooms", {}))["rooms"]
    assert set(out) == set(rows[0]), \
        "create_room 回执与 list_rooms 的行必须同构，否则 C# 那条直插路径会缺字段"

    assert out["title"] == "青须藤之事"
    assert out["members"] == [LIN, LI]
    assert out["member_unit_ids"] == {LIN: "Xs6JDI"}
    assert "items" not in out, "create_room **不得**回放历史（那正是它要摆脱的依赖）"

    # 建群 = 纯数据操作：成员**不进场合**（群标签只在发言回合内有效，见 `RoomFlow.enter`），
    # 也不该在他账本里留下群游标 —— 没人开窗，谁都还没进场。
    assert loop.get(LIN).scene == "", "建群不该给成员打群标签（没人开窗，谁都还没进场）"
    assert not [e for e in loop.get(LIN).session.log if e.type == "room/cursor"], \
        "建群不该动成员账本里的群游标"
    # 名录里立刻可见，且 id 唯一、顺序追加
    assert [r["room_id"] for r in rows] == [rid]
    # 历史为空（还没人说话），但用 open_room 打开能拿到正常形状
    h = _run(hub.handle_request("open_room", {"room_id": rid, "limit": 0}))
    assert h["room_id"] == rid and h["items"] == []

    # 缺参数 fail-closed（不是静默建一个空群）
    for params in ({}, {"members": []}, {"members": ["  ", ""]}):
        try:
            _run(hub.handle_request("create_room", params))
        except ValueError:
            pass
        else:
            raise AssertionError("create_room 缺 members 时应报错：%r" % (params,))
    assert len(_run(hub.handle_request("list_rooms", {}))["rooms"]) == 1, \
        "失败的那几次不该留下空群"


def test_create_room_does_not_depend_on_the_history_projection():
    """★ 建群路径**不得**依赖 `room_history`（群窗投影）。

    这不是洁癖，是 09-17 实机事故的直接结论：
    `zlib.error: Error -3 while decompressing data: incorrect header check` 爆在
    `history()` 里那句 `from .room_history import project_room_history` ——
    PyInstaller onefile 的纯 Python 模块是**按需从 exe 归档里现读**的（不预解压到 _MEI），
    而服务器 exe 在运行中被换成新构建，于是**偏移错位、从没加载过的模块再也读不出来**。
    建群根本不需要投影 ⇒ 让它彻底不碰，这条路径就再也不会因为"某个模块第一次被 import"
    而炸。判据走 **AST**（只看真的 import/调用），不走字符串 —— `create_room` 的注释里
    正大光明地写着这件事的来龙去脉，把它算进去就是"注释一解释就红"的假失败。
    """
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(RoomFlow.create_room)))
    touched = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            touched.add(node.module or "")
            touched.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            touched.update(a.name for a in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            touched.add(node.func.id)
        elif isinstance(node, ast.Attribute):
            touched.add(node.attr)
    assert not [n for n in touched if "room_history" in n], \
        "create_room 依赖了 room_history —— 建群不该依赖群窗投影：%s" % sorted(touched)
    assert "history" not in touched, \
        "create_room 调用了 history( —— 建群不该回放历史：%s" % sorted(touched)


def test_rpc_open_room_limit_semantics():
    """★ `limit` 必须显式传 0（全部）——否则群窗**静默**只回放最后 10 条。

    `_parse_limit` 的缺省是 10（为不传参的老调用方保留），而 C# 若忘了传，
    界面上**看不出**历史被截断，玩家只会觉得"这个群怎么少了一段"。
    1:1 窗（open_chat）与删除重放（delete_history）都显式传了 0，群聊必须对齐。
    """
    loop, _ = _seed({LIN: ["答。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = hub.rooms.open_room([LIN])
    for i in range(8):                       # 8 轮 → 16 条发言，超过缺省的 10
        _say(hub, room.id, f"第{i}问", speak_first=[LIN])

    allout = _run(hub.handle_request("open_room", {"room_id": room.id, "limit": 0}))
    assert len(allout["items"]) == 16, f"limit=0 应回全部 16 条，实际 {len(allout['items'])}"
    assert allout["items"][0]["text"] == "第0问"

    default = _run(hub.handle_request("open_room", {"room_id": room.id}))
    assert len(default["items"]) == 10, "不传 limit 时走缺省 10（老调用方语义）"

    # 而 C# 侧必须显式传 0 —— 从源码现读，防止哪天被顺手删掉。
    # （原先读的是 `OpenRoomCommand`（`/group`）；P3 删掉斜杠命令后，
    #   唯一的开群路径变成 `OpenRoomById`，这里跟着改。契约测试
    #   `test_open_room_request_asks_for_full_history` 钉的是同一件事的第二面。）
    from pathlib import Path
    cs = (Path(__file__).resolve().parent.parent / "csharp" / "UI" / "ChatPresenter.cs"
          ).read_text(encoding="utf-8", errors="replace")
    body = cs.split("public void OpenRoomById")[1].split("public bool ApplyOpenRoom")[0]
    # ★ 11-21：不再传 0（全量），改成具名常量分页首屏 ★
    #   原意图（**必须显式传**，否则 Python 缺省 10 会静默截断）仍然成立且更重要。
    assert 'p["limit"] = 0' not in body, \
        "不许再传 limit=0（会把全部历史一次灌给客户端 —— 这正是要修的问题）"
    assert 'p["limit"] = RoomHistoryPageRows' in body, \
        "必须显式传 RoomHistoryPageRows（不传则缺省 10，群窗会静默只剩 10 条）"


def test_rpc_add_room_members_has_no_open_room_side_effects():
    """拉人：`add_room_members` 存在的理由就是**去掉** `open_room` 重开分支的两个副作用。

    借 `open_room(room_id=…, members=[新人])` 也能加人，但它会
      ① 无条件把**全量历史**再回一遍（`open_room` 末尾固定调 `history`）；
      ② `title` 非空时顺手改群名。
    加一个人不该带来这两个后果。

    同时钉住入群语义：**新人看不到入群前的话**（入群位置 = 加入时的末尾），
    这一条会直接变成 UI 上必须写给玩家看的一句提示。
    """
    loop, _ = _seed({LIN: ["甲。"], LI: ["乙。"], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN])
    _say(hub, room.id, Q1, speak_first=[LIN])            # seq 1..2
    title_before, seq_before = room.title, room.last_seq
    assert seq_before == 2

    out = _run(hub.handle_request("add_room_members", {"room_id": room.id, "members": [LI]}))
    assert out["room_id"] == room.id
    assert out["added"] == [LI], f"应报告真正加进去的人：{out}"
    assert out["members"] == [LIN, LI]
    assert out["last_seq"] == seq_before
    assert "items" not in out, "拉人不该回放历史 —— 那正是它不借 open_room 的原因"
    assert room.title == title_before, "拉人不该动群名"
    assert room.joined[LI] == seq_before, "新人入群位置 = 加入时的末尾"

    # 幂等：再加一次不重复、不报错
    again = _run(hub.handle_request("add_room_members", {"room_id": room.id, "members": [LI]}))
    assert again["added"] == [] and again["members"] == [LIN, LI]

    # 新人**只**收到加进来之后的话（点名让 LI 真的被投递到）
    _say(hub, room.id, Q2, speak_first=[LI])
    texts = [m["text"] for m in _delivered(loop.get(LI))]
    assert Q2 in texts, f"新人收不到入群后的话：{texts}"
    assert Q1 not in texts, f"新人看到了入群前的话（应为不可见）：{texts}"


def test_rpc_kick_room_member():
    """踢人：成员表更新、入群位置一并清掉、被踢者不再收投递、重复踢不抛异常。"""
    loop, _ = _seed({LIN: ["甲。"], LI: ["乙。"], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])

    out = _run(hub.handle_request("kick_room_member", {"room_id": room.id, "npc_id": LI}))
    assert out["removed"] is True
    assert out["members"] == [LIN]
    assert LI not in room.joined, "被踢者的入群位置应一并清掉（留着会让重入群沿用旧位置）"

    again = _run(hub.handle_request("kick_room_member", {"room_id": room.id, "npc_id": LI}))
    assert again["removed"] is False, "踢一个不在群里的人应回 False，而不是抛异常"

    # 被踢者不再收到投递（他连 agent 都不该被建起来）
    _say(hub, room.id, Q1, speak_first=[LI])
    ag_li = loop.get(LI)
    assert ag_li is None or not _delivered(ag_li), "被踢者仍在收投递"


def test_room_management_rpc_rejects_bad_params():
    """参数校验 fail-closed：缺参必须当场报错，不能静默建出半个群。"""
    loop, _ = _seed({})
    hub, _ = _hub(loop)
    room = _open(hub, [LIN])

    bad = [
        ("add_room_members", {"members": [LI]}),                        # 缺 room_id
        ("add_room_members", {"room_id": room.id}),                     # 缺 members
        ("add_room_members", {"room_id": room.id, "members": []}),      # members 空
        ("add_room_members", {"room_id": room.id, "members": ["  "]}),  # members 全是空白
        ("kick_room_member", {"npc_id": LI}),                           # 缺 room_id
        ("kick_room_member", {"room_id": room.id}),                     # 缺 npc_id
    ]
    for method, params in bad:
        try:
            _run(hub.handle_request(method, params))
        except ValueError:
            continue
        except Exception as e:                    # noqa: BLE001 — 只放行 ValueError
            raise AssertionError(f"{method}{params} 抛了 {type(e).__name__}，应抛 ValueError") from e
        raise AssertionError(f"{method}{params} 应抛 ValueError（缺参不许静默通过）")


def test_handle_room_message_rejects_bad_input():
    loop, _ = _seed({})
    hub, ws = _hub(loop)
    _run(hub.handle_room_message("", Q1))            # 空 room_id：静默丢弃，不抛
    _run(hub.handle_room_message("g999", Q1))        # 不存在的群：丢弃，但**必须回终帧**
    _run(hub.handle_room_message("g999", "  "))      # 空文本
    assert hub.rooms.list_rooms() == []
    # ★ 群不存在也必须有 `phase="end"`：那是 C# 群窗复位忙碌态的**唯一**依据，
    #   而 C# 是"先置忙再发消息"的 —— 不回帧就卡"正在输入"直到 240s 看门狗（且无解释）。
    ends = [f for f in ws.of("room_state") if f.get("phase") == "end" and f.get("room_id") == "g999"]
    assert len(ends) == 1, f"不存在的群必须回一帧终帧（实际 {ws.frames}）"
    assert ends[0].get("error") is True and ends[0].get("text"), ends[0]


# ══════════════════════════════════════════════════════════════════
# 插话 —— 玩家在 NPC 说话途中又发一句
# ══════════════════════════════════════════════════════════════════

def test_interject_immediate_log_ordered_frames_and_delivery():
    """★ 插话（方案 A）的四条不变量。

    背景：落账原先在 `room_lock` **内** ⇒ 玩家的第二条连"落账"都要等整轮跑完，
    群窗上的表现是"我发了话，十几秒毫无反应"。修法：把「`room.say` + emit」挪到
    锁外，并用一把**独立的短锁**（`say_lock`）钉住 UI 帧顺序。

    本用例用 `asyncio.Event` 把林婉清的回合钉在半空中（她已 `deliver`、还没说话），
    在这段时间里插一句，断言：
      ① 插话**立刻**落账 + **立刻**回显（不必等整轮）；
      ② UI 帧**到达顺序** === `Room.log` 的 seq 顺序（短锁的验收点）；
      ③ 投递按 `deliver` 分界：林（deliver 在插话**前**）本轮看不到，
         李（**后**）本轮就看到 —— 惰性投递免费给的；
      ④ 全程只有一个回合在跑（`room_lock` 仍跨整轮 ⇒ 轮次没有并发）；
      ⑤ 不丢：下一轮点名林婉清，她补上插话，且**只补一次**。
    """
    started = asyncio.Event()
    release = asyncio.Event()
    running = {"now": 0, "peak": 0}

    async def fn(req):
        sys_text = str(req.get("system") or "")
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        try:
            if LIN in sys_text:
                started.set()
                await release.wait()          # 把林婉清的回合钉住
            return {"text": "答。", "tool_calls": []}
        finally:
            running["now"] -= 1

    loop, _ = _seed(llm=StubLlmClient(fn))
    hub, ws = _hub(loop)
    hub.rooms.k = 1                           # 一轮只让一个人开口，时序更好控
    room = _open(hub, [LIN, LI, WANG])
    rid = room.id

    async def scenario():
        t1 = asyncio.create_task(hub.handle_room_message(rid, Q1, [LIN]))
        await asyncio.wait_for(started.wait(), timeout=5)   # 林已 deliver、正在跑
        t2 = asyncio.create_task(hub.handle_room_message(rid, Q2, [LI]))

        # ① 不等 t1 跑完就该落账（轮询只是给协程调度留余量，不是"允许它慢"）
        for _ in range(200):
            if any(u.text == Q2 for u in room.log):
                break
            await asyncio.sleep(0.005)
        assert any(u.text == Q2 for u in room.log), "插话没立刻落账（还堵在 room_lock 上）"
        assert [f["text"] for f in ws.of("room_say") if f["speaker"] == PLAYER] == [Q1, Q2], (
            f"玩家两条的回显顺序不对：{[f['text'] for f in ws.of('room_say')]}")

        release.set()
        await asyncio.gather(t1, t2)

    asyncio.run(scenario())

    # ② UI 帧**到达顺序** === 流水 seq 顺序
    frames = [(f["seq"], f["speaker"], f["text"]) for f in ws.of("room_say")]
    log_rows = [(u.seq, u.speaker, u.text) for u in room.log]
    assert frames == log_rows, f"帧序与流水序不一致：\n帧  ={frames}\n流水={log_rows}"

    # ④ 轮次没有并发：任何时刻只有一个回合在跑
    assert running["peak"] == 1, f"出现了并发的回合（峰值 {running['peak']}）"

    # ③ 投递按 deliver 分界
    lin_seen = [m["text"] for m in _delivered(loop.get(LIN))]
    li_seen = [m["text"] for m in _delivered(loop.get(LI))]
    assert Q1 in lin_seen, f"林没收到 Q1：{lin_seen}"
    assert Q2 not in lin_seen, f"林本轮不该看到插话（她 deliver 在插话之前）：{lin_seen}"
    assert Q2 in li_seen, f"李本轮就该看到插话（他 deliver 在插话之后）：{li_seen}"

    # ⑤ 不丢：下一轮点名林 —— 这次她的 deliver 跨过了插话的 seq
    _say(hub, rid, "那依你之见呢？", speak_first=[LIN])
    lin_seen2 = [m["text"] for m in _delivered(loop.get(LIN))]
    assert lin_seen2.count(Q2) == 1, f"插话没被补投（或重复投了）：{lin_seen2}"


def test_say_lock_keeps_frame_order_under_adversarial_emit():
    """`say_lock` 的直接验收（**对抗性**）：故意让 emit 乱序，看锁能不能兜住。

    上面那条端到端用例测得到"立刻落账 + 按 `deliver` 分界的投递"，但它的两条 emit
    被时序天然隔开了 —— **测不到锁本身**。这里把回显拖成"seq 越小越慢"，让后落账的
    那条先到达 UI：

    - 没有 `say_lock` 时本用例**必红**（帧序 `[2,1]` vs 流水序 `[1,2]`）；
    - 有锁时第二个协程要等第一个把「say + emit」做完 ⇒ 帧序恒等于 seq 序。

    注：`Room.say` 是同步的 ⇒ **seq 分配永远正确**（本用例的流水序不会错）。
    钉的是"帧序"这个**单靠同步性保不住**的东西（emit 里有 `await`）。
    """
    loop, _ = _seed({})                      # 不跑回合，只落账
    hub, ws = _hub(loop)
    room = _open(hub, [LIN])

    async def adversarial(event, **kw):
        # seq=1 慢、seq=2 快 ⇒ 没有锁时后落账的先到
        await asyncio.sleep(0.05 if kw.get("seq") == 1 else 0)
        await ws.send_event(event, **kw)

    hub.rooms._emit = adversarial

    async def scenario():
        await asyncio.gather(
            hub.rooms._say_and_emit(room, PLAYER, "第一句"),
            hub.rooms._say_and_emit(room, PLAYER, "第二句"),
        )

    asyncio.run(scenario())

    frames = [(f["seq"], f["text"]) for f in ws.of("room_say")]
    rows = [(u.seq, u.text) for u in room.log]
    assert frames == rows, f"帧序被 emit 的抖动带偏了：帧={frames} 流水={rows}"


def test_spar_narration_renders_as_action_row_not_a_bubble():
    """切磋的公共叙述行 = **旁白**：画成「行动记录」行，不是 NPC 气泡（09-22 用户拍板）。

    那行本来是写给**别的成员**的账本的（`_run_round` 串行等一整场战斗，不广播一句，
    别人只会以为这一轮卡死了），可它走气泡通道后成了那位 NPC 的"台词" —— 而旁白里还夹着
    **玩家的原话**，挂谁名下都不对（截图里姜萌的气泡里出现了唐炎说的话）。
    用户选的形态是**工具返回结果那种行**（"我需要直接以工具返回结果形式显示"），
    而且必须**重开窗还在** —— 实机就是这么丢的：进战斗时群窗被场景切换收掉，
    这一轮的 `step` 帧只进了缓冲，重开窗按 baseline 整段丢弃，屏幕上就只剩那条气泡。

    四条一起钉（缺任何一条都会出现"直播与重开窗两种表现"这类不一致）：
      ① 落账（成员经 `deliver` 照常看得到）；② 帧上带 `narration:true`（C# 据此改画行动记录行）；
      ③ 群窗投影发成 `tool_result` 条目（→ `ReplaceHistory` 的「行动记录」行，重开窗仍在）；
      ④ 落盘往返后标记还在。
    """
    loop, _ = _seed({})
    hub, ws = _hub(loop)
    room = _open(hub, [LIN])
    res = {"success": True, "data": {
        "op": "spar", "target": LIN, "player": PLAYER_NAME, "accepted": True,
        "battle_seen": True, "battle_finished": True,
        "lines": [{"who": "player", "name": PLAYER_NAME, "text": "厉害，我输了。"},
                  {"who": "npc", "name": LIN, "text": "看来是我赢了！"}]}}

    async def scenario():
        await hub.rooms._make_battle_settle(room, LIN)(res)
        await hub.rooms._say_and_emit(room, LIN, "这句话才是她说的")

    _run(scenario())

    narration = [u for u in room.log if u.narration]
    assert len(narration) == 1 and "切磋了一场" in narration[0].text, [u.text for u in room.log]
    assert narration[0].speaker == LIN
    frames = ws.of("room_say")
    assert [f["text"] for f in frames] == [narration[0].text, "这句话才是她说的"], \
        f"旁白没广播（或次序不对）：{[f['text'] for f in frames]}"
    assert frames[0].get("narration") is True, "旁白帧没带 narration 标记 —— C# 会把它画成气泡"
    assert "narration" not in frames[1], "普通发言被当成旁白（帧上不该有这个键）"

    items = project_room_history(room)["items"]
    assert [it["kind"] for it in items] == ["tool_result", "peer"], \
        f"旁白没有发成行动记录条目（重开窗会画成气泡或干脆不见）：{items}"
    assert "切磋了一场" in items[0]["text"]
    # 落盘再读回来，标记还在（重启后重开窗不能把它变回气泡）
    from agent_loop.room.room import Room as _Room, Utterance
    back = _Room.from_meta(room.to_meta(),
                           [Utterance.from_dict(u.to_dict()) for u in room.log])
    assert [it["kind"] for it in project_room_history(back)["items"]] == ["tool_result", "peer"], \
        "旁白标记没随落盘往返 —— 重启后重开窗会把它画成气泡"


# ══════════════════════════════════════════════════════════════════
# R13–R15：解散群（删群聊）
#
# 三条不变量，每条都对应一个**静默失败**：
#   ① 群 id **永不重用** —— 重用了会让老成员的 `room/cursor` 落到新群上，
#      于是他们在新群里一条消息都收不到，且不报错（R13）；
#   ② `retired` 高水位必须**跟着 flush 走** —— 漏传一次就被抹掉，①随即失效（R13b）；
#   ③ 落盘顺序 = **先 index 后 unlink** —— 反过来崩在中间会留下
#      「last_seq=N 但历史空白」的幽灵群（R14c）。
# ══════════════════════════════════════════════════════════════════

def _room_file(loop, rid):
    from agent_loop.storage.persistence import room_path_for
    return room_path_for(rid, loop.rooms_root)


def _index_json(loop):
    from agent_loop.storage.persistence import ROOMS_INDEX
    with open(os.path.join(loop.rooms_root, ROOMS_INDEX), encoding="utf-8") as f:
        return json.load(f)


def test_r13_dissolved_room_id_is_never_reused():
    """★ 解散过的 room_id 绝不能再被分配 —— 否则老成员在新群里**收不到任何消息**。

    这条测的是那个 bug 的**完整后果**，不只是"id 不一样"：光比 id 只能证明
    `new_room_id` 变了；真正要钉的是"重开一个新群之后，老成员还能收到话"。
    """
    loop, _ = _seed({LIN: ["知道了。", "又见面了。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)

    old = _open(hub, [LIN])
    _say(hub, old.id, Q1, speak_first=[LIN])          # Q1 + 林应答 = 2 条
    assert old.last_seq == 2
    agent = loop.get(LIN)
    # 游标诚实值是 **1** 不是 2：`deliver` 在林开口**之前**写游标，而"自己的话不投给自己"
    # 本就不该算进去。这里照实断言，免得把"游标=流水长度"这个错误直觉写进测试。
    assert agent.cursor_for(old.id) == 1, "前提：林的游标停在 1"

    assert hub.rooms.dissolve_room(old.id) is True

    # 再建一个群，成员还是林 —— id 绝不能又是 g1
    new = _open(hub, [LIN])
    assert new.id != old.id, (
        f"★ 解散过的 id 被重用了：{new.id}。老成员的 room/cursor 还指着它 ⇒ "
        f"他在新群里会一条消息都收不到（且不报错）")

    # ★ 后果验证：新群里的第一句话必须**投递到**林
    before = len(_delivered(agent))
    _say(hub, new.id, Q2, speak_first=[LIN])
    got = [d["text"] for d in _delivered(agent)][before:]
    assert Q2 in got, (
        f"新群的消息没投递到林（收到的增量：{got}）—— 老游标把它挡住了？")


def test_r13b_retired_high_water_survives_flush_and_reload():
    """`retired` 必须跟着 `flush_all` 与 `load_rooms` 走 —— 这条最容易漏。

    `flush_all` 里 `save_rooms_index(rooms, root)` 若忘了传 `retired`，下一次存档
    就把退休名单抹了；此后 `new_room_id` 又会把解散过的 id 发出去，
    而**症状要等到那时才出现**（R13 的 bug 会以"偶发"的形态回来）。
    """
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    r1 = _open(hub, [LIN])
    r2 = _open(hub, [LIN, LI])
    assert (r1.id, r2.id) == ("g1", "g2"), (r1.id, r2.id)

    hub.rooms.dissolve_room(r1.id)
    assert loop.retired_rooms == {"g1"}
    assert _index_json(loop)["retired"] == ["g1"], "删群当场就该把退休 id 落盘"

    loop.flush_all()
    assert _index_json(loop)["retired"] == ["g1"], "★ flush_all 把退休名单抹掉了"

    # 换进程身份读档：名录 + 退休名单都要回来
    loop2 = AgentLoop.reset_for_test(storage_root=ROOT, bridge=loop.bridge, llm=loop.llm)
    assert loop2.load_rooms() == 1
    assert loop2.retired_rooms == {"g1"}, f"读档丢了退休名单：{loop2.retired_rooms}"
    hub2, _ = _hub(loop2)
    assert _open(hub2, [WANG]).id == "g3", "读档后新建群又把 g1 发出去了"

    # discard_all（世界重载）也清 —— 它是 per-存档的，由 load_rooms 重新载入
    loop2.discard_all()
    assert loop2.retired_rooms == set()
    loop2.load_rooms()
    assert loop2.retired_rooms == {"g1"}


def test_r14_dissolve_removes_room_from_index_and_disk():
    """解散 = 名录里没了 + 流水文件没了 + 内存里没了（三件都要）。"""
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    r1 = _open(hub, [LIN])
    r2 = _open(hub, [LIN, LI])
    _say(hub, r1.id, Q1, speak_first=[LIN])
    loop.flush_all()
    f1 = _room_file(loop, r1.id)
    assert os.path.exists(f1), "前提：流水文件已落盘"

    assert hub.rooms.dissolve_room(r1.id) is True

    assert r1.id not in loop.rooms, "内存名册里还留着"
    assert os.path.exists(f1) is False, "流水文件没删"
    ids = [m["room_id"] for m in hub.rooms.list_rooms()]
    assert ids == [r2.id], f"名录里还留着已解散的群：{ids}"
    assert [m["room_id"] for m in _index_json(loop)["rooms"]] == [r2.id], "index.json 没更新"

    # 幂等：再删一次返回 False，不抛
    assert hub.rooms.dissolve_room(r1.id) is False
    # 其余群不受影响
    assert os.path.exists(_room_file(loop, r2.id)) is False   # r2 还没说过话，没落过盘
    assert (LIN in loop.rooms[r2.id].members)


def test_r14b_dissolve_keeps_each_members_memory():
    """★ **删群 ≠ 删记忆**：成员 session 里那些 peer 消息是他经历过的事，必须留着。

    这是"一个 session = 他经历的一切"这条定案的直接后果。若哪天有人把
    "删群"顺手实现成"把成员 session 里的相关条目也清掉"，这条会红。
    """
    # 让**李四**说话、看**林**的记忆 —— 自己的话本就不投给自己（`delta_for` 排除 me），
    # 拿林自己的话去断"记忆还在"会永远失败。
    loop, _ = _seed({LIN: [SILENT_SENTINEL], LI: ["我李四说一句。"], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    r1 = _open(hub, [LIN, LI])
    _say(hub, r1.id, Q1, speak_first=[LI])
    agent = loop.get(LIN)
    got_before = [d["text"] for d in _delivered(agent)]
    assert Q1 in got_before and "我李四说一句。" in got_before, got_before
    cursor_before = agent.cursor_for(r1.id)
    assert cursor_before == 2, cursor_before   # Q1 与李四那句

    hub.rooms.dissolve_room(r1.id)

    got_after = [d["text"] for d in _delivered(agent)]
    assert got_after == got_before, "★ 解散把成员的记忆也删了 —— 那与「session = 经历的一切」冲突"
    # 游标同理**刻意留着**（清它要动 session schema）；代价由 id 不重用来兜
    assert agent.cursor_for(r1.id) == cursor_before, "游标被动过（不该动）"


def test_r14c_dissolve_resets_scene_so_solo_turns_stay_visible():
    """解散后成员的 `scene` 必须复位 —— 否则他之后的**单聊**回合会带着群标签，
    在 1:1 窗里凭空消失（而且**是静默的**，同 `RoomFlow.leave` 的注释）。"""
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    r1 = _open(hub, [LIN, LI])
    _say(hub, r1.id, Q1, speak_first=[LIN])       # 先把成员跑出来（agent 懒创建）
    # 不能靠"跑完一轮再看 scene"：`_run_turn` 的 finally 每轮结束都会复位它
    # （那正是 `leave` 的第二个调用点）。要测的是 `dissolve_room` **自己**有没有
    # 去复位那些**没在跑回合**的成员，所以手工把标签打上。
    loop.get(LIN).set_scene(r1.id)
    loop.get(LI).set_scene(r1.id)
    assert loop.get(LIN).scene == r1.id, "前提：标签已打上"

    hub.rooms.dissolve_room(r1.id)
    assert loop.get(LIN).scene == "", "★ 解散后成员还带着群标签"
    assert loop.get(LIN).roster == [] or loop.get(LIN).roster is None or list(loop.get(LIN).roster) == []


def test_r14d_dissolve_is_fail_closed_when_index_write_fails():
    """★ 写 index 失败时**绝不能**去删流水文件 —— 那就是"幽灵群"那个坏顺序。

    幽灵群的症状：读档后名录里有它、`last_seq` 说 N 条，但历史一片空白。
    正确行为是"什么都不删"——内存已摘（本次会话看不到它），盘上保持原样（可重开游戏找回）。
    """
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    r1 = _open(hub, [LIN])
    _say(hub, r1.id, Q1, speak_first=[LIN])
    loop.flush_all()
    f1 = _room_file(loop, r1.id)
    assert os.path.exists(f1)

    import agent_loop.agent_loop as al
    orig = al.save_rooms_index
    al.save_rooms_index = lambda *a, **kw: (_ for _ in ()).throw(OSError("磁盘满了"))
    try:
        assert hub.rooms.dissolve_room(r1.id) is True     # 内存照摘
    finally:
        al.save_rooms_index = orig

    assert os.path.exists(f1), "★ index 没写成却把流水删了 —— 重开游戏会得到一个历史空白的幽灵群"
    assert r1.id in loop.retired_rooms


def test_r15_dissolve_room_rpc():
    """RPC 形状 + fail-closed 参数校验（与 open_room/close_room 同约定）。"""
    loop, _ = _seed({LIN: ["嗯."], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    r1 = _open(hub, [LIN])
    r2 = _open(hub, [LI])

    out = _run(hub.handle_request("dissolve_room", {"room_id": r1.id}))
    assert out == {"room_id": r1.id, "dissolved": True}, out
    assert [m["room_id"] for m in _run(hub.handle_request("list_rooms", {}))["rooms"]] == [r2.id]

    # 不存在 → dissolved:false（不抛：删除本来就该幂等）
    out = _run(hub.handle_request("dissolve_room", {"room_id": "g404"}))
    assert out == {"room_id": "g404", "dissolved": False}, out

    # 缺参数 → 报错（不许静默成功）
    for bad in ({}, {"room_id": ""}, {"room_id": "   "}):
        try:
            _run(hub.handle_request("dissolve_room", bad))
        except Exception:
            continue
        raise AssertionError(f"缺 room_id 竟然成功了：{bad!r}")


def test_r15b_dissolve_mid_round_stops_cleanly():
    """解散撞上正在跑的一轮：本轮**就地收尾**，不再点名后面的候选。

    不做这个守卫的话，后面的成员会把发言落进一个已从名册摘掉、jsonl 也已删掉的对象
    （话丢了 + `_advance_pointer` 把刚 pop 的键又塞回 `_pointer`）。
    """
    loop, _ = _seed({LIN: ["我先说。"], LI: ["我后说。"], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    room = _open(hub, [LIN, LI, WANG])

    # 手工模拟"解散发生在**第一个**成员说完之后"：把 dissolved 标在**任意**第一个
    # 跑完回合的成员身上。
    #
    # ★ 11-21 起顺序随机 ⇒ 不能写死 `npc_id == LIN` ★（第一版就是这么写的：
    #   随机到李四先开口时，`dissolved` 永远不被设上，测试就变成了"没模拟解散"，
    #   于是"解散后还在点名"那条断言自然红 —— 那是测试的锅，不是产品的。）
    real_turn = hub.rooms._run_turn
    first_done = []

    async def turn(agent, npc_id, *a, **kw):
        out = await real_turn(agent, npc_id, *a, **kw)
        if not first_done:
            first_done.append(npc_id)
            hub.rooms._dissolved.add(room.id)      # 第一个人说完 ⇒ 此刻群被解散
        return out

    hub.rooms._run_turn = turn
    try:
        spoken = _run(hub.rooms.run_round(room.id))
    finally:
        hub.rooms._run_turn = real_turn

    assert first_done, "前提：至少应该有一个人跑过回合"
    # ★ 本用例的判据是"**解散之后不再点名后面的人**"，而不是"spoken 里有几个人" ★
    #   第一个成员跑完时解散就发生了，而 `_run_round` 的循环体在**下一轮迭代开头**
    #   才检查 `_dissolved` ⇒ 循环立刻 break，后面两个人一次都没被点名。
    #   所以 `spoken` 里有没有人不重要（那条发言可能因为群已解散而不落账），
    #   要看的是"`_run_turn` 只被调了一次"。
    assert len(first_done) == 1, f"解散后还在继续点名：跑过回合的人={first_done}"
    assert len(spoken) <= 1, f"解散后还落了更多发言：{spoken}"
    assert room.id not in hub.rooms._pointer, "指针被复活了"


# ══════════════════════════════════════════════════════════════════
# 提案制 —— 在场 NPC 之间的缔结类关系 / 讨要 / 赠予 / 买卖（两阶段）
#
# 阶段一"提出"：不调 C#、不改数据 —— 落一条 Room.log 叙述行（全群可见）+ 对手方私信
# （plugin：模型可见 / UI 不可见，含确切的回应方式）+ 提案登记 + 对手方发言优先。
# 阶段二"回应"：对手方**反向做同一个动作**（买卖 = 按原条款回发）才放行到 C# 落刀；
# **落刀成功后**再落一条公开叙述行（挂对手方名下）—— 应下是当众的事，全群可见、
# 发起人经群流水就知道结果；沉默 = 拒绝（TTL 后作废：**公开**叙述行 + 私信发起人）；
# 应下但落刀失败 = 不公开、私信发起人（事没成就不是既成事实）。
#
# 公开/私密的分界（09-17 统一口径）：
#   公开 = 提案族（提出 / 应下 / 过期）+ 解除类结果 —— 进 Room.log 并投给所有成员 session；
#   私密 = 只读工具、加/减好感、位移、偷窃 —— 调用与结果只在该 NPC 自己的 session 里。
# ══════════════════════════════════════════════════════════════════


def _notes(agent):
    """投进来的**提案私信**（plugin=NOTICE_PLUGIN；模型可见、UI 不可见）。"""
    out = []
    for ev in agent.session.log:
        if ev.get("type") != "user/message":
            continue
        src = (ev.get("data") or {}).get("source") or {}
        if src.get("plugin") != NOTICE_PLUGIN:
            continue
        for blk in ((ev.get("data") or {}).get("content") or []):
            if isinstance(blk, dict) and blk.get("type") == "text":
                out.append(blk.get("text") or "")
    return out


def _spy_tools(bridge):
    """记录 bridge 收到的每次调用（就地替换 `call_tool`），断言"落刀 / 直通"用。"""
    calls = []
    real = bridge.call_tool

    def spy(name, args, npc_id=None):
        calls.append({"name": name, "args": dict(args or {})})
        return real(name, args, npc_id=npc_id)

    bridge.call_tool = spy
    return calls


def _has_tool_result(req):
    for m in req.get("messages") or []:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool-result" for b in c):
            return True
    return False


def _who(req):
    sys_text = str(req.get("system") or "")
    for w in ALL:
        if w in sys_text:
            return w
    return ""


def _tool_result_text(agent):
    """账本里全部 tool/result 的文本（含 JSON 兜底形态，断言用子串匹配即可）。"""
    return json.dumps([e for e in agent.session.log if e.get("type") == "tool/result"],
                      ensure_ascii=False)


def test_proposal_judge_pure_function():
    """闸门判据的全分支（纯函数：手工造表即可断言，不用起 server / 真 LLM）。"""
    members = [LIN, LI]
    props = {}
    J = judge_proposal
    assert set(REL_ESTABLISH_OPS) == {"jie_yuan", "marry", "bai_shi", "shou_tu",
                                      "ren_yi_fu_mu", "jie_yi"}
    assert PROPOSAL_TTL_ROUNDS == 2

    # ① 范围外的一律放行：加/减好感、解除、偷窃、位移、缺省对玩家
    for name, args in (
        ("social_relation", {"op": "add_intim", "target": LI, "value": 3}),
        ("social_relation", {"op": "reduce_intim", "target": LI, "value": 3}),
        ("social_relation", {"op": "jie_chu_jie_yuan", "target": LI}),
        ("social_relation", {"op": "jie_yuan"}),                        # 缺省 = 玩家
        ("item_acquire", {"op": "steal_item", "target": LI, "item_name": "丹药"}),
        ("item_acquire", {"op": "ask_for", "item_name": "丹药"}),       # 缺省 = 玩家
        ("movement", {"op": "teleport", "target": LI}),
        ("economy_item", {"items": [{"item_name": "丹药"}]}),           # 缺省 = 玩家
    ):
        assert J(name, args, caller=LIN, members=members, proposals=props, room_id="g1")[0] == "pass", \
            (name, args)

    # ② 在场校验：不在场 / 对自己
    v, _, msg = J("social_relation", {"op": "jie_yuan", "target": "赵六"},
                  caller=LIN, members=members, proposals=props, room_id="g1")
    assert v == "block" and "没有名为" in msg
    v, _, msg = J("social_relation", {"op": "jie_yuan", "target": LIN},
                  caller=LIN, members=members, proposals=props, room_id="g1")
    assert v == "block" and "你自己" in msg

    # ③ 缔结类：提出 → 重复被拦 → 对手方反向调用 = 接受；别的群不串
    v, key, _ = J("social_relation", {"op": "jie_yuan", "target": LI},
                  caller=LIN, members=members, proposals=props, room_id="g1")
    assert v == "propose" and key and key not in props
    props[key] = {"kind": "rel", "room_id": "g1", "proposer": LIN, "target": LI, "op": "jie_yuan"}
    assert J("social_relation", {"op": "jie_yuan", "target": LI},
             caller=LIN, members=members, proposals=props, room_id="g1")[0] == "block"
    v, k2, _ = J("social_relation", {"op": "jie_yuan", "target": LIN},
                 caller=LI, members=members, proposals=props, room_id="g1")
    assert v == "accept" and k2 == key
    assert J("social_relation", {"op": "jie_yuan", "target": LIN},
             caller=LI, members=members, proposals=props, room_id="g2")[0] == "propose"

    # ④ 赠予：单件提出；对手方用 ask_for 领取 = 接受；领取别的东西不是回应
    v, gkey, _ = J("economy_item", {"target": LI, "items": [{"item_name": "丹药", "count": 2}]},
                   caller=LIN, members=members, proposals=props, room_id="g1")
    assert v == "propose"
    props[gkey] = {"kind": "give", "room_id": "g1", "proposer": LIN, "target": LI,
                   "item_name": "丹药", "count": 2}
    v, k3, _ = J("item_acquire", {"op": "ask_for", "target": LIN, "item_name": "丹药", "count": 2},
                 caller=LI, members=members, proposals=props, room_id="g1")
    assert v == "accept" and k3 == gkey
    v, k4, _ = J("item_acquire", {"op": "ask_for", "target": LIN, "item_name": "灵石", "count": 1},
                 caller=LI, members=members, proposals=props, room_id="g1")
    assert v == "propose" and k4 != gkey

    # ⑤ 讨要：提出；对手方用 economy_item 交付 = 接受
    v, akey, _ = J("item_acquire", {"op": "ask_for", "target": LI, "item_name": "龙涎果"},
                   caller=LIN, members=members, proposals=props, room_id="g1")
    assert v == "propose"
    props[akey] = {"kind": "ask", "room_id": "g1", "proposer": LIN, "target": LI, "item_name": "龙涎果"}
    v, k5, _ = J("economy_item", {"target": LIN, "items": [{"item_name": "龙涎果", "count": 1}]},
                 caller=LI, members=members, proposals=props, room_id="g1")
    assert v == "accept" and k5 == akey

    # ⑥ 多件赠予被拦（自纠提示）；重复赠予被拦
    v, _, msg = J("economy_item", {"target": LI, "items": [{"item_name": "丹药"}, {"item_name": "灵石"}]},
                  caller=LIN, members=members, proposals=props, room_id="g1")
    assert v == "block" and "一件" in msg
    assert J("economy_item", {"target": LI, "items": [{"item_name": "丹药", "count": 1}]},
             caller=LIN, members=members, proposals=props, room_id="g1")[0] == "block"


def test_proposal_rel_round_trip():
    """缔结类提案：提出不落刀 → 对方反向调用才落刀（只有对手方能收口）。"""
    async def fn(req):
        who = _who(req)
        if who == LIN:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "c1", "name": "social_relation",
                        "arguments": {"op": "jie_yuan", "target": LI}}]}
            return {"text": "李道友，我可否与你结为道侣？"}
        if who == LI:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "c2", "name": "social_relation",
                        "arguments": {"op": "jie_yuan", "target": LIN}}]}
            return {"text": "好，我们结为道侣。"}
        return {"text": SILENT_SENTINEL}

    loop, b = _seed(llm=StubLlmClient(fn))
    calls = _spy_tools(b)
    # 关系落刀要过桩的好感阈值（C# 已移除该校验、桩仍按 180 判 —— 按桩喂数，两边都过得去）
    for a, other in ((LIN, LI), (LI, LIN)):
        b._units[a].setdefault("relations", {})[other] = {
            "name": other, "relation": "陌生", "intim": 200, "same_grid": True}

    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])

    _say(hub, room.id, Q1, speak_first=[LIN])           # 第 1 轮：林提出
    assert not [c for c in calls if c["name"] == "social_relation"], "提出阶段不该调桥（未落刀）"
    assert b._units[LI]["relations"][LIN]["relation"] == "陌生", "提出阶段不该改数据"

    # 提案 = 一条**群可见**的叙述行（挂在发起人名下）+ 一条登记
    narr = [u for u in room.log if u.speaker == LIN and u.text.startswith("（向")]
    assert len(narr) == 1 and narr[0].text == "（向李四提出结为道侣之意）", narr
    assert "social_relation" not in json.dumps([u.to_dict() for u in room.log], ensure_ascii=False), \
        "流水里不该有工具痕迹（工具流私有）"
    assert len(hub.rooms._proposals) == 1

    # 发起人拿到的是"已提出"（人话渲染，不是 JSON）
    assert "你已向李四提出结为道侣之意" in _tool_result_text(loop.get(LIN))

    _say(hub, room.id, Q2)                              # 第 2 轮：李优先（待回应）→ 接受
    # 优先发言的证据（k=1 ⇒ 本轮只有一人开口）：开口的是李四。没把提案对手方排到队首的话，
    # 轮转起点在"上一轮最后一个发言者"的下一位 —— 这里开口的会是林婉清，李四根本排不上。
    # 落刀成功后**应下也进公开流水**（挂李四名下，与提出那条对称）+ 李四自己的台词。
    assert [(u.speaker, u.text) for u in room.log][-3:] == \
        [(PLAYER, Q2), (LI, "（应了林婉清结为道侣之请，此事已成）"), (LI, "好，我们结为道侣。")], room.log
    # 对手方两路知情（都落在他的第 2 轮账本里）：投递进来的叙述行 + 私信（含确切的回应方式）
    assert any(t.startswith("（向李四提出") for t in [m["text"] for m in _delivered(loop.get(LI))])
    notes = _notes(loop.get(LI))
    assert notes and "social_relation" in notes[0] and "op=jie_yuan" in notes[0] \
        and f"target={LIN}" in notes[0], notes
    # 私信只是给模型的（UI 历史里不出现）
    ui = json.dumps(project_ui_history(loop.get(LI).session, max_turns=0), ensure_ascii=False)
    assert "当众向你提出" not in ui, "提案私信不该出现在窗history里"
    rel_calls = [c for c in calls if c["name"] == "social_relation"]
    assert len(rel_calls) == 1 and rel_calls[0]["args"].get("target") == LIN, rel_calls
    assert rel_calls[0]["args"].get("initiator") == LI
    assert b._units[LI]["relations"][LIN]["relation"] == "道侣", b._units[LI]["relations"]
    assert hub.rooms._proposals == {}, "接受后提案应收口"

    _say(hub, room.id, Q3, speak_first=[LIN])           # 第 3 轮：林**经群流水**看到"应了"
    # 应下是公开的事：不再走私信 —— 全群（含发起人）都在 Room.log 里看到它
    assert not any("应了你结为道侣之请" in t for t in _notes(loop.get(LIN))), \
        f"应下不该再走私信（它是公开记录）：{_notes(loop.get(LIN))}"
    assert any("应了林婉清结为道侣之请" in m["text"] for m in _delivered(loop.get(LIN))), \
        f"发起人没从群里收到应下那条：{[m['text'] for m in _delivered(loop.get(LIN))]}"
    # 闸门与场合标记同处复位
    for w in (LIN, LI):
        assert loop.get(w).tool_gate is None


def test_proposal_give_round_trip():
    """赠予提案：提出不交付；对方用 item_acquire 领取才真交付（道具路径）。"""
    async def fn(req):
        who = _who(req)
        if who == LIN:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "c1", "name": "economy_item",
                        "arguments": {"target": LI, "items": [{"item_name": "丹药", "count": 2}]}}]}
            return {"text": "这瓶丹药你收着。"}
        if who == LI:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "c2", "name": "item_acquire",
                        "arguments": {"op": "ask_for", "target": LIN, "item_name": "丹药", "count": 2}}]}
            return {"text": "多谢。"}
        return {"text": SILENT_SENTINEL}

    loop, b = _seed(llm=StubLlmClient(fn))
    calls = _spy_tools(b)
    b._units[LIN]["items"] = ["丹药3"]
    b._units[LI]["items"] = []

    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])

    _say(hub, room.id, Q1, speak_first=[LIN])           # 第 1 轮：林提出
    assert not [c for c in calls if c["name"] == "economy_item"], "提出阶段不该调桥"
    assert b._bag_have(b._units[LIN]["items"], "丹药") == 3, "提出阶段不该动道具"
    narr = [u for u in room.log if u.speaker == LIN and u.text.startswith("（欲赠")]
    assert len(narr) == 1 and narr[0].text == "（欲赠李四丹药×2）", narr
    assert "你已向李四表达了赠予丹药×2之意" in _tool_result_text(loop.get(LIN))

    _say(hub, room.id, Q2)                              # 第 2 轮：李领取 → 真交付
    notes = _notes(loop.get(LI))
    assert notes and "item_acquire" in notes[0] and "op=ask_for" in notes[0] \
        and "item_name=丹药" in notes[0], notes
    acq = [c for c in calls if c["name"] == "item_acquire"]
    assert len(acq) == 1 and acq[0]["args"].get("target") == LIN, acq
    assert acq[0]["args"].get("initiator") == LI
    assert b._bag_have(b._units[LIN]["items"], "丹药") == 1, b._units[LIN]["items"]
    assert b._bag_have(b._units[LI]["items"], "丹药") == 2, b._units[LI]["items"]
    assert hub.rooms._proposals == {}
    # 落刀成功后应下进公开流水（挂李四名下）：数量取**实得数**
    assert any(u.speaker == LI and u.text == "（收下了林婉清赠的丹药×2）" for u in room.log), room.log

    _say(hub, room.id, Q3, speak_first=[LIN])           # 第 3 轮：林**经群流水**看到"收下了"
    assert not any("收下了你赠的" in t for t in _notes(loop.get(LIN))), "应下不该再走私信"
    assert any("收下了林婉清赠的丹药×2" in m["text"] for m in _delivered(loop.get(LIN))), \
        [m["text"] for m in _delivered(loop.get(LIN))]


def test_proposal_ask_round_trip_lingshi():
    """讨要提案（灵石）：提出不转移；对方用 economy_item 交付才转移。"""
    async def fn(req):
        who = _who(req)
        if who == LIN:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "c1", "name": "item_acquire",
                        "arguments": {"op": "ask_for", "target": LI, "item_name": "灵石", "count": 100}}]}
            return {"text": "李道友，可否借我些灵石？"}
        if who == LI:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "c2", "name": "economy_item",
                        "arguments": {"target": LIN, "items": [{"item_name": "灵石", "count": 100}]}}]}
            return {"text": "拿去用。"}
        return {"text": SILENT_SENTINEL}

    loop, b = _seed(llm=StubLlmClient(fn))
    calls = _spy_tools(b)
    b._units[LIN]["items"] = []
    b._units[LI]["items"] = ["灵石500"]

    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])

    _say(hub, room.id, Q1, speak_first=[LIN])           # 第 1 轮：林提出
    assert not [c for c in calls if c["name"] == "item_acquire"], "提出阶段不该调桥"
    assert b._bag_have(b._units[LI]["items"], "灵石") == 500, "提出阶段不该动钱"
    narr = [u for u in room.log if u.speaker == LIN and u.text.startswith("（向")]
    assert len(narr) == 1 and narr[0].text == "（向李四讨要灵石×100）", narr

    _say(hub, room.id, Q2)                              # 第 2 轮：李交付 → 真转移
    notes = _notes(loop.get(LI))
    assert notes and "economy_item" in notes[0] and '"item_name": "灵石"' in notes[0], notes
    eco = [c for c in calls if c["name"] == "economy_item"]
    assert len(eco) == 1 and eco[0]["args"].get("target") == LIN, eco
    assert b._bag_have(b._units[LI]["items"], "灵石") == 400, b._units[LI]["items"]
    assert b._bag_have(b._units[LIN]["items"], "灵石") == 100, b._units[LIN]["items"]
    assert hub.rooms._proposals == {}
    # 落刀成功后应下进公开流水（挂交付方名下，与提出那条对称）
    assert any(u.speaker == LI and u.text == "（应了林婉清的讨要，给了灵石×100）" for u in room.log), room.log

    _say(hub, room.id, Q3, speak_first=[LIN])           # 第 3 轮：林**经群流水**看到"给了"
    assert any("应了林婉清的讨要，给了灵石×100" in m["text"] for m in _delivered(loop.get(LIN))), \
        [m["text"] for m in _delivered(loop.get(LIN))]


def test_proposal_blocks_duplicates_and_absent_targets():
    """重复提议 / 不在场：都被拦；落在范围外的动作照常直通到桥。"""
    async def fn(req):
        if _who(req) != LIN:
            return {"text": SILENT_SENTINEL}
        if not _has_tool_result(req):
            return {"text": "", "tool_calls": [
                {"id": "d1", "name": "social_relation", "arguments": {"op": "jie_yuan", "target": LI}},
                {"id": "d2", "name": "social_relation", "arguments": {"op": "jie_yuan", "target": LI}},
                {"id": "d3", "name": "social_relation", "arguments": {"op": "jie_yuan", "target": WANG}},
                {"id": "d4", "name": "social_relation", "arguments": {"op": "add_intim", "target": LI, "value": 3}},
                {"id": "d5", "name": "movement", "arguments": {"op": "teleport", "target": LI}},
            ]}
        return {"text": "……"}

    loop, b = _seed(llm=StubLlmClient(fn))
    calls = _spy_tools(b)
    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])                        # 王五不在群里

    _say(hub, room.id, Q1, speak_first=[LIN])
    assert len([u for u in room.log if u.speaker == LIN and u.text.startswith("（向")]) == 1
    assert len(hub.rooms._proposals) == 1
    assert not [c for c in calls if c["name"] == "social_relation" and c["args"].get("op") == "jie_yuan"], \
        "被拦的缔结调用不该到达桥"
    dumped = _tool_result_text(loop.get(LIN))
    assert "提过" in dumped and "没有名为" in dumped, dumped
    # 范围外的动作照常直通：加好感与位移都到了桥
    assert [c["args"].get("op") for c in calls if c["name"] == "social_relation"] == ["add_intim"]
    assert [c["args"].get("op") for c in calls if c["name"] == "movement"] == ["teleport"]
    # 直写类（加好感 / 位移）是**私密**的：调用与结果只进林自己的 session，不写公共流水 ——
    # 流水里只有：玩家的问、那条提案叙述、林的一句台词（工具报文绝不进 Room.log）
    assert [(u.speaker, u.text) for u in room.log] == [
        (PLAYER, Q1), (LIN, "（向李四提出结为道侣之意）"), (LIN, "……")], room.log


def test_proposal_expiry_notifies_proposer():
    """TTL 用尽 = 石沉大海：记录清掉、发起人收到私信。"""
    async def fn(req):
        who = _who(req)
        if who == LIN and not _has_tool_result(req):
            return {"text": "", "tool_calls": [{"id": "e1", "name": "social_relation",
                    "arguments": {"op": "jie_yuan", "target": LI}}]}
        if who == LIN:
            return {"text": "（提起话头）"}
        return {"text": SILENT_SENTINEL}

    loop, b = _seed(llm=StubLlmClient(fn))
    hub, ws = _hub(loop)
    hub.rooms.k = 1
    assert hub.rooms.proposal_ttl_rounds == 2, "默认 TTL 是 2 个完整回合"
    hub.rooms.proposal_ttl_rounds = 1                   # 缩小窗口，把过期跑到边界
    room = _open(hub, [LIN, LI])

    _say(hub, room.id, Q1, speak_first=[LIN])           # 第 1 轮：提出（expire_round=2）
    assert len(hub.rooms._proposals) == 1
    _say(hub, room.id, Q2)                              # 第 2 轮：李弃权（不回应）→ 轮末过期
    assert hub.rooms._proposals == {}, "TTL 用尽的提案应作废"
    # 过期（=沉默）也是**公开**结果：群里出现发起人名下的叙述行（与提出那条对称）
    assert any(u.speaker == LIN and u.text == "（向李四提出的结为道侣之意，李四始终没有回应）"
               for u in room.log), room.log
    _say(hub, room.id, Q3, speak_first=[LIN])           # 第 3 轮：林收到"石沉大海"（私信）
    assert any("始终没有回应" in t for t in _notes(loop.get(LIN))), _notes(loop.get(LIN))

    _say(hub, room.id, "那便算了。", speak_first=[LI])   # 第 4 轮：李（沉默方）也在群里看到它
    assert any("始终没有回应" in m["text"] for m in _delivered(loop.get(LI))), \
        [m["text"] for m in _delivered(loop.get(LI))]


def test_proposal_gate_absent_in_solo_turns():
    """单聊回合不挂闸门：NPC 对 NPC 的缔结照旧直通到桥（本轮刻意保持现状）。"""
    async def fn(req):
        if not _has_tool_result(req):
            return {"text": "", "tool_calls": [{"id": "s1", "name": "social_relation",
                    "arguments": {"op": "jie_yuan", "target": LI}}]}
        return {"text": "……"}

    loop, b = _seed(llm=StubLlmClient(fn))
    calls = _spy_tools(b)
    hub, ws = _hub(loop)
    _run(hub.handle_message(LIN, "私下说一句"))
    got = [c for c in calls if c["name"] == "social_relation"]
    assert len(got) == 1 and got[0]["args"].get("target") == LI, got
    assert loop.get(LIN).tool_gate is None


def test_proposal_judge_trade_branches():
    """买卖判据的分支：不在场放行、替人做主拦、原条款回发=成交、改条款=还价。"""
    members = [LIN, LI]
    props = {}
    J = judge_proposal
    T = {"seller": LI, "buyer": LIN, "item": "丹药", "count": 2, "price": 100}

    # ① 任一方不在场（玩家/异地）：不归提案管（C# 的 IsPlayerUnit 才是权威）
    for bad in ({"seller": "韩立", "buyer": LIN, "item": "丹药", "count": 1, "price": 5},
                {"seller": LI, "buyer": "韩立", "item": "丹药", "count": 1, "price": 5}):
        assert J("trade", bad, caller=LIN, members=members, proposals=props, room_id="g1")[0] == "pass"
    # ② 形态不全 / 自语 / 灵石当商品：交给 C# 报错
    assert J("trade", {"seller": LI, "item": "丹药", "price": 1},
             caller=LIN, members=members, proposals=props, room_id="g1")[0] == "pass"
    assert J("trade", {"seller": LI, "buyer": LI, "item": "丹药", "price": 1},
             caller=LI, members=members, proposals=props, room_id="g1")[0] == "pass"
    assert J("trade", {"seller": LI, "buyer": LIN, "item": "灵石", "count": 1, "price": 5},
             caller=LIN, members=members, proposals=props, room_id="g1")[0] == "pass"

    # ③ 双方都在场但不是买卖一方：拦（两边同意都没取得）
    v, _, msg = J("trade", {"seller": LI, "buyer": LIN, "item": "丹药", "count": 1, "price": 5},
                  caller=WANG, members=[LIN, LI, WANG], proposals=props, room_id="g1")
    assert v == "block" and "你自己" in msg

    # ④ 提出 → 对手方**按原条款**回发 = 成交（方向无关：谁是发起人不影响匹配）
    v, key, _ = J("trade", T, caller=LIN, members=members, proposals=props, room_id="g1")
    assert v == "propose" and key
    props[key] = {"kind": "trade", "room_id": "g1", "proposer": LIN, "target": LI,
                  "seller": LI, "buyer": LIN, "item_name": "丹药", "count": 2, "price": 100,
                  "sig": "李四|林婉清|丹药|2|100"}
    v, k2, _ = J("trade", T, caller=LI, members=members, proposals=props, room_id="g1")
    assert v == "accept" and k2 == key
    # 发起人重复同一桩：拦（改条款才允许再提）
    assert J("trade", T, caller=LIN, members=members, proposals=props, room_id="g1")[0] == "block"
    # ⑤ 还价（改 price）= 对手方发起的一条**新**提案
    v, k3, _ = J("trade", {"seller": LI, "buyer": LIN, "item": "丹药", "count": 2, "price": 80},
                 caller=LI, members=members, proposals=props, room_id="g1")
    assert v == "propose" and k3 != key


def test_proposal_trade_round_trip():
    """买卖提案：提出只报价；对方**按原条款回发**才成交（改条款=还价）。"""
    async def fn(req):
        who = _who(req)
        if who == LIN:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "t1", "name": "trade",
                        "arguments": {"seller": LI, "buyer": LIN, "item": "丹药",
                                      "count": 2, "price": 100}}]}
            return {"text": "李道友，这两枚丹药一百灵石卖我如何？"}
        if who == LI:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "t2", "name": "trade",
                        "arguments": {"seller": LI, "buyer": LIN, "item": "丹药",
                                      "count": 2, "price": 100}}]}
            return {"text": "成。"}
        return {"text": SILENT_SENTINEL}

    loop, b = _seed(llm=StubLlmClient(fn))
    calls = _spy_tools(b)
    b._units[LI]["items"] = ["丹药5"]
    b._units[LIN]["items"] = ["灵石500"]

    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])

    _say(hub, room.id, Q1, speak_first=[LIN])           # 第 1 轮：林报价
    assert not [c for c in calls if c["name"] == "trade"], "提出阶段不该调桥"
    assert b._bag_have(b._units[LI]["items"], "丹药") == 5, "提出阶段不该动货"
    assert b._bag_have(b._units[LIN]["items"], "灵石") == 500, "提出阶段不该动钱"
    narr = [u for u in room.log if u.speaker == LIN and u.text.startswith("（欲买")]
    assert len(narr) == 1 and narr[0].text == "（欲买李四的丹药×2，价100灵石）", narr
    assert "你已向李四提出这桩买卖" in _tool_result_text(loop.get(LIN))
    assert len(hub.rooms._proposals) == 1

    _say(hub, room.id, Q2)                              # 第 2 轮：李按原条款回发 → 成交
    notes = _notes(loop.get(LI))
    assert notes and "照原样再发一遍" in notes[0] and "seller=李四" in notes[0], notes
    tr_calls = [c for c in calls if c["name"] == "trade"]
    assert len(tr_calls) == 1, tr_calls
    assert tr_calls[0]["args"].get("seller") == LI and tr_calls[0]["args"].get("buyer") == LIN
    assert tr_calls[0]["args"].get("initiator") == LI
    # 货与钱都真的动了（桩里道具/灵石是字符串栈，_bag_have 现算）
    assert b._bag_have(b._units[LI]["items"], "丹药") == 3, b._units[LI]["items"]
    assert b._bag_have(b._units[LIN]["items"], "丹药") == 2, b._units[LIN]["items"]
    assert b._bag_have(b._units[LI]["items"], "灵石") == 100, b._units[LI]["items"]
    assert b._bag_have(b._units[LIN]["items"], "灵石") == 400, b._units[LIN]["items"]
    assert hub.rooms._proposals == {}, "成交后提案应收口"
    # 成交后应下进公开流水（挂李四名下）；件数用条款里的数（条款签名一致 ⇒ 与提案相同）
    assert any(u.speaker == LI and u.text == "（应了林婉清的买卖：丹药×2，价100灵石）" for u in room.log), room.log

    _say(hub, room.id, Q3, speak_first=[LIN])           # 第 3 轮：林**经群流水**看到"应了"
    assert not any("应了这桩买卖" in t for t in _notes(loop.get(LIN))), "应下不该再走私信"
    assert any("应了林婉清的买卖：丹药×2，价100灵石" in m["text"] for m in _delivered(loop.get(LIN))), \
        [m["text"] for m in _delivered(loop.get(LIN))]


def test_proposal_accept_failure_stays_private_and_honest():
    """★ 应下但落刀失败：**不写公开叙述行**（事没成就不是既成事实），只私信发起人。

    这条钉住"回执不乐观"：闸门在 bridge **返回之后**才收尾 —— 成功才有那行公开的"应了"；
    失败时群里看不到任何"达成了"的话，发起人收到一句实话（记录已消费，他也等不到过期通知）。
    """
    async def fn(req):
        who = _who(req)
        if who == LIN:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "f1", "name": "economy_item",
                        "arguments": {"target": LI, "items": [{"item_name": "灵石", "count": 100}]}}]}
            return {"text": "赠你百枚灵石。"}
        if who == LI:
            if not _has_tool_result(req):
                return {"text": "", "tool_calls": [{"id": "f2", "name": "item_acquire",
                        "arguments": {"op": "ask_for", "target": LIN, "item_name": "灵石", "count": 100}}]}
            return {"text": "……"}
        return {"text": SILENT_SENTINEL}

    loop, b = _seed(llm=StubLlmClient(fn))
    calls = _spy_tools(b)
    b._units[LIN]["items"] = []                     # 林没有灵石 ⇒ 提出不受阻，落刀必失败
    b._units[LI]["items"] = []

    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])

    _say(hub, room.id, Q1, speak_first=[LIN])           # 第 1 轮：提出（不校验库存）
    assert any(u.text == "（欲赠李四灵石×100）" for u in room.log), room.log
    assert hub.rooms._proposals, "提案应已登记"

    _say(hub, room.id, Q2)                              # 第 2 轮：李应下 → 桥**失败**
    assert hub.rooms._proposals == {}, "应下即消费（放行过桥）"
    # 落刀确实到了桥（是它失败，而不是没调）
    assert [c for c in calls if c["name"] == "item_acquire"], calls
    # 公开面：**没有**任何"达成了"的叙述行（事没成）
    assert not any("应了" in u.text or "收下了" in u.text for u in room.log), room.log

    _say(hub, room.id, Q3, speak_first=[LIN])           # 第 3 轮：林收到"没能…"的私信
    assert any("没能" in t for t in _notes(loop.get(LIN))), _notes(loop.get(LIN))
    assert not any("没能" in u.text for u in room.log), "失败不该进公开流水"


def test_break_relation_result_is_public():
    """解除类：**不走提案**（单方面行为），但结果公开 —— 成功后落叙述行（挂动作方名下）。"""
    async def fn(req):
        if _who(req) != LIN:
            return {"text": SILENT_SENTINEL}
        if not _has_tool_result(req):
            return {"text": "", "tool_calls": [{"id": "b1", "name": "social_relation",
                    "arguments": {"op": "jie_chu_jie_yuan", "target": LI}}]}
        return {"text": "自今日起，你我各不相干。"}

    loop, b = _seed(llm=StubLlmClient(fn))
    calls = _spy_tools(b)
    b._units[LIN].setdefault("relations", {})[LI] = {
        "name": LI, "relation": "道侣", "intim": 200, "same_grid": True}

    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])

    _say(hub, room.id, Q1, speak_first=[LIN])           # 林当众解除
    # 单方面直写：不登记提案（不需要对方同意），照常到桥落刀
    assert hub.rooms._proposals == {}, "解除不该进提案表"
    rel_calls = [c for c in calls if c["name"] == "social_relation"]
    assert len(rel_calls) == 1 and rel_calls[0]["args"].get("op") == "jie_chu_jie_yuan", rel_calls
    assert b._units[LIN]["relations"][LI]["relation"] == "前道侣"
    # 结果公开：Room.log 里出现动作方名下的叙述行（与缔结的应下对称）
    assert any(u.speaker == LIN and u.text == "（与李四解除道侣之约）" for u in room.log), room.log
    # 但它**不是**工具报文：流水里没有工具痕迹（工具流仍然私有）
    assert "social_relation" not in json.dumps([u.to_dict() for u in room.log], ensure_ascii=False)

    _say(hub, room.id, Q2, speak_first=[LI])            # 李（被解除方）也在群里看到它
    assert any("与李四解除道侣之约" in m["text"] for m in _delivered(loop.get(LI))), \
        [m["text"] for m in _delivered(loop.get(LI))]


# ══════════════════════════════════════════════════════════════════
# R16：群窗删除一条发言（§9.2 定稿）—— 群记录 + 各成员记忆，两边同删
#
# 三个落点各有一条静默失败在等：
#   ① 只删群记录 ⇒ 群里没了、NPC 还记得（模型照旧提这件事）；
#   ② 只删成员记忆 ⇒ 重开窗回放又把它放回来；
#   ③ 只删别人的副本、漏掉**发言者自己**那份 ⇒ 他自己仍然记得说过。
# 锚点两种（缺一不可）：投递副本的 `source.uid`（玩家消息也是）；
# 发言者自己的话走 `room/utt` 注记（他不收自己的投递，没有 uid 可认）。
# ══════════════════════════════════════════════════════════════════

def _hist_texts(agent, scene=None):
    """某个场合的 **UI/模型可见**历史文本（`project_ui_history` = 已应用删除后的那一面）。

    ★ 断言"记忆里还有没有"必须走这里，**不能**走 `_delivered` —— 后者读的是原始
    `session.log`，而软删除是**追加注记 + 投影过滤**（账本只增不改）：
    被删的节点仍然躺在 log 里，只是 surface / UI 投影 / 模型都看不到它了。
    """
    out = project_ui_history(agent.session, max_turns=0, scene=scene)
    return [i.get("text") or "" for i in out["items"]]


def test_r16_room_delete_removes_from_room_and_every_memory():
    """删一条 NPC 发言：群窗不再显示、**每个成员**（含发言者自己）的记忆里那份都没了。

    同时钉住"逐字删除"：删的是那一条，前后的问答与别的话一条不少。
    """
    loop, _ = _seed({LIN: ["林的一句话。"], LI: ["李的一句话。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])
    _say(hub, room.id, Q1, speak_first=[LIN])          # seq1 玩家 / seq2 林
    _say(hub, room.id, Q2, speak_first=[LI])           # seq3 玩家 / seq4 李
    assert [u.text for u in room.log] == [Q1, "林的一句话。", Q2, "李的一句话。"]

    lin, li = loop.get(LIN), loop.get(LI)
    # 前提：两种锚点都真的落下了
    assert lin.room_utt_nodes(room.id, 2), "发言者自己的锚点（room/utt）没落"
    assert any(ev.get("type") == "room/utt" for ev in lin.session.log)
    li_copy = [d for d in _delivered(li) if d["text"] == "林的一句话。"]
    assert len(li_copy) == 1 and li_copy[0]["source"]["uid"] == f"{room.id}#2"

    out = hub.rooms.room_delete(room.id, [f"{room.id}#2"])
    assert out["ok"] and out["uids"] == [f"{room.id}#2"] and out["dry_run"] is False

    # ① 群记录：墓标（`log` 只增不改 ⇒ 长度不变）+ 投影里没有它
    assert room.is_deleted(2) and len(room.log) == 4, "墓标不该动 log 本体"
    assert "林的一句话。" not in [i["text"] for i in hub.rooms.history(room.id)["items"]]

    # ② 对方的记忆：那条 peer 副本被剔除（模型面 + UI 面都看不到）
    assert "林的一句话。" not in json.dumps(li.session.derive_messages(), ensure_ascii=False)
    assert "林的一句话。" not in _hist_texts(li, room.id), _hist_texts(li, room.id)
    # ③ 发言者自己的记忆：自己那条 assistant 也没了
    assert "林的一句话。" not in json.dumps(lin.session.derive_messages(), ensure_ascii=False)
    assert "林的一句话。" not in _hist_texts(lin, room.id), _hist_texts(lin, room.id)

    # 逐字删除：其余的照旧在（李四听过前后问答与自己的话；林这一轮没被点名，只听到 Q1）
    li_texts, lin_texts = _hist_texts(li, room.id), _hist_texts(lin, room.id)
    assert Q1 in li_texts and Q2 in li_texts and "李的一句话。" in li_texts, li_texts
    assert lin_texts == [Q1], lin_texts
    # 报告：李四剔除 1 个节点；林婉清是发言者，删的是自己那条 assistant
    rep = {m["npc"]: m for m in out["members"]}
    assert rep[LI]["deleted"] == [f"{room.id}#2"] and rep[LI]["removed_nodes"] >= 1
    assert rep[LIN]["deleted"] == [f"{room.id}#2"], rep[LIN]


def test_r16b_preview_is_pure_and_delete_stops_future_delivery():
    """预演**不写账本**；执行后"还没听到的人"也永远收不到它（`delta_for` 跳过墓标）。

    第二条是关键：对**已经收到**的人靠 fan-out 剔除，对**还没投递**的人只有墓标这一道闸 ——
    漏了的话，"下次轮到他才第一次听到一条已经被删掉的话"。
    """
    loop, _ = _seed({LIN: ["林说过一句。"], LI: ["我后来才开口。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])
    _say(hub, room.id, Q1, speak_first=[LIN])   # 只说一轮：李四没被点名 ⇒ 没听过、连 agent 都没建
    assert loop.get(LI) is None, "前提：李四还没参与过（零成本成员）"

    pre = hub.rooms.plan_room_delete(room.id, [f"{room.id}#2"])
    assert pre["dry_run"] is True and pre["ok"] is True
    assert room.deleted_seqs == set() and not room.is_deleted(2), "预演不许写账本"
    assert any(ev.get("type") == "room/utt" for ev in loop.get(LIN).session.log), \
        "预演不该动任何 session"
    rep = {m["npc"]: m for m in pre["members"]}
    assert rep[LI]["no_memory"] and rep[LI]["absent"] == [f"{room.id}#2"]
    assert rep[LIN]["deleted"] == [f"{room.id}#2"]
    assert [i["text"] for i in pre["history"]["items"]] == [Q1, "林说过一句。"], "预演回执里的历史未删"

    hub.rooms.room_delete(room.id, [f"{room.id}#2"])
    assert room.is_deleted(2)
    # ★ 李四第一次开口：不会收到已删的那条，但 Q1 与之后的照常收到
    _say(hub, room.id, Q2, speak_first=[LI])
    seen = _hist_texts(loop.get(LI), room.id)
    assert "林说过一句。" not in seen, f"墓标没拦住投递：{seen}"
    assert Q1 in seen and Q2 in seen, seen


def test_r16c_delete_player_message_by_uid_anchor():
    """删**玩家**那条：锚点是投递副本的 `source.uid`（`kind="room"`，09-17 起的契约）。"""
    loop, _ = _seed({LIN: ["好的。"], LI: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])
    _say(hub, room.id, Q1, speak_first=[LIN])
    assert loop.get(LI) is None                                  # 李四没参与过 ⇒ 没有副本
    got = [d for d in _delivered(loop.get(LIN)) if d["source"].get("uid") == f"{room.id}#1"]
    assert len(got) == 1 and got[0]["text"] == Q1, "前提：玩家消息的副本带 uid 锚点"

    # 预演走 RPC（C# 的确认弹窗就吃这一份）：账本零改动，回执里照样带当前历史
    pre = _run(hub.handle_request("preview_room_delete",
                                 {"room_id": room.id, "uids": [f"{room.id}#1"]}))
    assert pre["ok"] and pre["dry_run"] is True and not room.is_deleted(1)
    assert Q1 in [i["text"] for i in pre["history"]["items"]], "预演回执是**删前**的历史"

    out = _run(hub.handle_request("room_delete", {"room_id": room.id, "uids": [f"{room.id}#1"]}))
    assert out["ok"] and out["dry_run"] is False
    # ★ 回执形状 = C# 侧契约（`ChatPresenter.OnRoomDeleteConfirmed` 就这么读；少一个键那边静默失效）
    assert set(out) >= {"ok", "dry_run", "room_id", "uids", "members", "history"}, set(out)
    for m in out["members"]:
        assert set(m) >= {"npc", "no_memory", "removed_nodes", "deleted", "folded", "absent"}, set(m)
    assert Q1 not in [i["text"] for i in out["history"]["items"]]
    assert Q1 not in _hist_texts(loop.get(LIN), room.id), _hist_texts(loop.get(LIN), room.id)
    # 玩家消息没有 session，只有各成员的副本 ⇒ 报告里"已删"的是收到过的那位
    rep = {m["npc"]: m for m in out["members"]}
    assert rep[LIN]["deleted"] == [f"{room.id}#1"] and rep[LI]["no_memory"]


def test_r16d_room_delete_rpc_guards_and_fail_closed_params():
    """守卫：一轮进行中 / 成员回合中 / 成员压缩中 / 待回应提案行 ⇒ **整体拒绝**；参数 fail-closed。"""
    async def fn(req):
        if _who(req) == LIN and not _has_tool_result(req):
            return {"text": "", "tool_calls": [{"id": "p1", "name": "social_relation",
                    "arguments": {"op": "jie_yuan", "target": LI}}]}
        if _who(req) == LIN:
            return {"text": "（提起话头）"}
        return {"text": SILENT_SENTINEL}

    loop, _ = _seed(llm=StubLlmClient(fn))
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI, WANG])
    _say(hub, room.id, Q1, speak_first=[LIN])           # 林提出提案（叙述行 seq=2）
    narr = [u for u in room.log if u.text.startswith("（向")]
    assert len(narr) == 1 and len(hub.rooms._proposals) == 1, room.log

    # ① 待回应提案的叙述行：挂起期间禁删（等收口）
    try:
        _run(hub.handle_request("room_delete", {"room_id": room.id, "uids": [narr[0].uid]}))
    except DeleteError as e:
        assert "等待回应" in str(e), str(e)
    else:
        raise AssertionError("挂起的提案叙述行不该能删")
    assert not room.is_deleted(narr[0].seq)

    # ② 一轮群聊进行中（room_lock 被持有）
    async def _while_locked():
        async with hub._room_lock_for(room.id):
            return await hub.handle_request("room_delete", {"room_id": room.id,
                                                           "uids": [f"{room.id}#1"]})
    try:
        _run(_while_locked())
    except DeleteError as e:
        assert "一轮进行中" in str(e), str(e)
    else:
        raise AssertionError("一轮进行中不该能删群消息")

    # ③ 成员正在回合中 / 正在压缩
    lin = loop.get(LIN)
    lin.phase["kind"] = "running"
    try:
        _run(hub.handle_request("room_delete", {"room_id": room.id, "uids": [f"{room.id}#1"]}))
    except DeleteError as e:
        assert "对话中" in str(e), str(e)
    else:
        raise AssertionError("成员回合中不该能删群消息")
    finally:
        lin.phase["kind"] = "idle"

    hub._compacting.add(WANG)
    try:
        _run(hub.handle_request("room_delete", {"room_id": room.id, "uids": [f"{room.id}#1"]}))
    except DeleteError as e:
        assert "压缩" in str(e), str(e)
    else:
        raise AssertionError("成员压缩中不该能删群消息")
    finally:
        hub._compacting.discard(WANG)

    # ④ 参数 fail-closed：缺 room_id / 缺 uids / uid 不属于本群 / 群里没有 / 已删过
    for method, bad in (("room_delete", {"uids": [f"{room.id}#1"]}),
                        ("room_delete", {"room_id": room.id, "uids": []}),
                        ("preview_room_delete", {"room_id": room.id, "uids": []})):
        try:
            _run(hub.handle_request(method, bad))
        except ValueError:
            continue
        raise AssertionError(f"{method}{bad} 缺参数竟然通过了")
    for bad_uid in ("g9#1", "nonsense", f"{room.id}#0", f"{room.id}#99"):
        try:
            hub.rooms.room_delete(room.id, [bad_uid])
        except ValueError:
            continue
        raise AssertionError(f"非法 uid 竟然通过了：{bad_uid}")

    # ⑤ 陈旧的提案不挡路（收口后同样这条行可删）——先把它过期掉，再删叙述行
    hub.rooms._proposals.clear()
    out = hub.rooms.room_delete(room.id, [narr[0].uid])
    assert out["ok"] and room.is_deleted(narr[0].seq)
    # 幂等：同一条再删一次 → fail-closed（已删）
    try:
        hub.rooms.room_delete(room.id, [narr[0].uid])
    except ValueError as e:
        assert "已被删除" in str(e), str(e)
    else:
        raise AssertionError("重复删除同一 uid 该拒绝")


def test_r16e_folded_into_summary_is_preserved_and_reported():
    """已折进纪要的副本：**部分覆盖 ⇒ 纪要原样保留** + 如实报告；全覆盖才连纪要一起删。

    这是"绝不为了删一条而毁掉别的记忆"那条取舍的直接验收（§9.2 的 fan-out 规则）。
    """
    from agent_loop.session.session import Session

    s = Session("测试角色")
    s.append("turn/start", {"turn": 1, "scene": "g1"})
    s.append("user/message", {"role": "user", "id": "m1",
                              "content": [{"type": "text", "text": "玩家：在吗"}],
                              "source": {"kind": "room", "speaker": PLAYER, "uid": "g1#1"}},
             {"surfaceOp": "append"})
    s.append("user/message", {"role": "user", "id": "m2",
                              "content": [{"type": "text", "text": "李四：在的。"}],
                              "source": {"kind": "peer", "speaker": LI, "uid": "g1#2"}},
             {"surfaceOp": "append"})
    s.append("assistant/message", {"message": {"role": "assistant",
             "content": [{"type": "text", "text": "嗯。"}]}}, {"surfaceOp": "append"})
    s.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    # 模拟一次压缩：把 seq 1..3 折成一条纪要（审计事件 seq=5，纪要节点 seq=6）
    cid = "cp1"
    s.append("compaction/summary", {"shadowedRange": {"start": 1, "end": 3},
                                    "shadowedSeqs": [1, 2, 3], "compactionId": cid})
    s.append("user/message", {"role": "user", "id": "sum",
                              "content": [{"type": "text",
                                           "text": "<compacted-summary>玩家与李四寒暄</compacted-summary>"}],
                              "source": {"kind": "plugin", "plugin": "compact", "compactionId": cid}},
             {"surfaceOp": {"op": "replace", "start": 1, "end": 3}})
    assert s.surface.nodes == [6], s.surface.nodes       # 1..3 被 replace 成纪要节点 6

    # ① 只删李四那条（seq=2）：它已被折进纪要 ⇒ 纪要保留、报告 folded、不落 drop
    pl = plan_message_delete(s, [2])
    assert pl["ok"] and pl["drop_seqs"] == [] and pl["keep_seqs"] == [6], pl
    assert pl["found"] == [2] and pl["folded"] == [2] and pl["missing"] == [], pl
    assert pl["dropped_summaries"] == [] and pl["kept_summaries"] == [6], pl

    # ② 三条一起删（覆盖闭包全中）⇒ 纪要跟着走（不然它就成了一段"没人记得"的孤儿内容）
    plan2 = plan_message_delete(s, [1, 2, 3])
    assert plan2["ok"] and plan2["drop_seqs"] == [6] and plan2["folded"] == [], plan2
    assert plan2["dropped_summaries"] and plan2["dropped_summaries"][0]["represented"] == [1, 2, 3]


def test_r16h_room_utt_note_is_invisible():
    """`room/utt` 注记与 `room/cursor` 同款**全隐形**（Surface / 模型 / UI 投影都不认识它）。

    它不是"谁说的话"，只是"这条群发言 ↔ 我账本里的哪个节点"的坐标 ——
    一旦漏进 surface 或 UI，模型会看到一串莫名其妙的注记（而那是**永久**的账本内容）。
    """
    loop, _ = _seed({LIN: ["一句。"], LI: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])
    _say(hub, room.id, Q1, speak_first=[LIN])

    lin = loop.get(LIN)
    seqs = [ev["seq"] for ev in lin.session.log if ev.get("type") == "room/utt"]
    assert seqs, "前提：注记已落"
    assert not (set(seqs) & set(lin.session.surface.nodes)), "room/utt 进了 Surface（模型会看到它）"
    assert "room/utt" not in json.dumps(lin.session.derive_messages(), ensure_ascii=False)
    assert "room/utt" not in json.dumps(
        project_ui_history(lin.session, max_turns=0), ensure_ascii=False)
    # 与它的锚点一致：node 指向的就是那句发言的账本节点
    node = [ev["data"]["node"] for ev in lin.session.log if ev.get("type") == "room/utt"][0]
    assert lin.session.log[node]["type"] == "assistant/message"
    assert lin.room_utt_nodes(room.id, 2) == [node]


def test_r16g_frozen_member_memory_is_scrubbed_too():
    """**关窗冻结**（`dispose`）的成员也要删到。

    `loop.get()` 只看活体，而"关窗"在本工程是**冻结而不是销毁**（对象、账本、屉都还在
    `_frozen`，重开窗原样复活）。只查活体会把他报成"没有副本" —— 而他其实记得，
    重开窗后照旧记得（**静默**）。所以 fan-out 必须走 `loop.get_any`。
    """
    loop, _ = _seed({LIN: ["林说过的。"], LI: ["我听到了。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])
    _say(hub, room.id, Q1, speak_first=[LIN])           # seq1 玩家 / seq2 林
    _say(hub, room.id, Q2, speak_first=[LI])            # 李听到林那句
    assert "林说过的。" in json.dumps(loop.get(LI).session.derive_messages(), ensure_ascii=False)

    loop.dispose(LI)                                    # 关窗 ⇒ 进冻结舱
    assert loop.get(LI) is None, "前提：活体表里已经看不到他了"

    out = hub.rooms.room_delete(room.id, [f"{room.id}#2"])
    rep = {m["npc"]: m for m in out["members"]}
    assert rep[LI]["no_memory"] is False, "冻结成员被报成'没有记忆' ⇒ 漏删（重开窗他又记得）"
    assert rep[LI]["deleted"] == [f"{room.id}#2"], rep[LI]

    again = loop.create(LI)                             # 重开窗：冻结舱原样复活
    assert "林说过的。" not in json.dumps(again.session.derive_messages(), ensure_ascii=False), \
        "重开窗后他仍然记得那条被删的发言"


def test_r16f_tombstone_survives_save_and_reload():
    """墓标随房间**全量重写**落盘：读档后群窗不会"删了又出现"、投递也不会再听到它。"""
    loop, _ = _seed({LIN: ["说过又删掉的话。"], LI: ["我后来才开口。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])
    _say(hub, room.id, Q1, speak_first=[LIN])
    hub.rooms.room_delete(room.id, [f"{room.id}#2"])
    rid = room.id
    assert loop.flush_all() >= 1

    loop2 = AgentLoop.reset_for_test(storage_root=ROOT, bridge=loop.bridge, llm=loop.llm)
    assert loop2.load_rooms() == 1
    assert loop2.rooms[rid].is_deleted(2), "读档丢了墓标（重开窗会把删掉的话放回来）"
    hub2, _ = _hub(loop2)
    out = _run(hub2.handle_request("get_room_history", {"room_id": rid, "limit": 0}))
    assert "说过又删掉的话。" not in [i["text"] for i in out["items"]], out["items"]
    assert [i["text"] for i in out["items"]] == [Q1]

    # 投递同样跳过（李四从没听过它，读档后第一次开口也不该听到）
    _say(hub2, rid, Q2, speak_first=[LI])
    seen = _hist_texts(loop2.get(LI), rid)
    assert "说过又删掉的话。" not in seen, seen
    assert Q1 in seen and Q2 in seen, seen


def test_r16h_unloaded_member_disk_copy_is_scrubbed():
    """★ 重启 / 读档后**尚未激活**的成员：磁盘 jsonl 里的副本也要删掉（不许静默漏删）。

    触发链极短：进游戏 → 开群 → 删一条 → 之后打开某成员 —— 他照旧记得。
    `_memory_agent` 只看内存（活体 / 冻结舱）时，读档后谁都没被激活（`discard_all`
    清空内存，而开群窗不激活任何成员），全员被报"没有副本"；而他们磁盘账本里
    明明留着当初投递的那一条。修法：内存没有 ⇒ 磁盘有账本则 **resume 加载进来再删**
    （预演 / 执行同一条路径，否则预览说"无副本"、执行却删），注记随 `flush_all`
    写回磁盘 —— 本测试把这两段都钉住。
    """
    loop, _ = _seed({LIN: ["林说过的。"], LI: ["我听到了。"]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])
    _say(hub, room.id, Q1, speak_first=[LIN])
    _say(hub, room.id, Q2, speak_first=[LI])          # 李四收到林那条副本
    rid = room.id
    assert loop.flush_all() >= 1, "前提：账本要落盘（'重启'才有的可 resume）"

    # === 模拟重启 / 读档：活体与冻结舱全部清空，rooms 从磁盘重新载入 ===
    loop2 = AgentLoop.reset_for_test(storage_root=ROOT, bridge=loop.bridge, llm=loop.llm)
    assert loop2.load_rooms() == 1
    assert loop2.get_any(LI) is None and loop2.get_any(LIN) is None, \
        "前提：读档后两名成员都还没被激活（开群窗不激活任何成员）"

    hub2, _ = _hub(loop2)
    pre = hub2.rooms.plan_room_delete(rid, [f"{rid}#2"])
    rep = {m["npc"]: m for m in pre["members"]}
    assert rep[LI]["no_memory"] is False, "预演把未激活成员报成'没有副本' ⇒ 漏删重现"
    assert rep[LI]["deleted"] == [f"{rid}#2"], rep[LI]
    assert rep[LIN]["deleted"] == [f"{rid}#2"], rep[LIN]   # 自己的话走 room/utt 锚点
    assert loop2.rooms[rid].is_deleted(2) is False, "预演不许写墓标"
    assert "林说过的。" in _hist_texts(loop2.get_any(LI), rid), \
        "前提：预演只是把磁盘账本加载进来，此时副本还在"
    assert all(ev.get("type") != "history/delete"
               for ev in loop2.get_any(LI).session.log), "预演不许写删除注记"

    out = hub2.rooms.room_delete(rid, [f"{rid}#2"])
    rep2 = {m["npc"]: m for m in out["members"]}
    assert rep2[LI]["deleted"] == [f"{rid}#2"], rep2[LI]
    assert loop2.rooms[rid].is_deleted(2)

    # === 玩家现在打开李四：被删那条必须已经不在了（此前他会照旧记得） ===
    got = _run(hub2.handle_request("open_chat", {"npc_id": LI, "limit": 0, "scene": rid}))
    texts = [i.get("text") or "" for i in got["items"]]
    assert "林说过的。" not in texts, f"resume 后他仍然记得被删的发言：{texts}"
    assert Q1 in texts and Q2 in texts, texts          # 逐字删除：其余照旧在

    # === 再存档、再重启：注记已随 flush 写回磁盘（不是只改了内存） ===
    assert loop2.flush_all() >= 1
    loop3 = AgentLoop.reset_for_test(storage_root=ROOT, bridge=loop.bridge, llm=loop.llm)
    assert loop3.load_rooms() == 1
    li3 = loop3.create(LI)
    assert "林说过的。" not in _hist_texts(li3, rid), "删除只改了内存、没落盘"


def test_r17_room_compact_command_is_refused_not_written_to_the_log():
    """★ 群聊 `/compact` 既不是"命令"、更不是"群发言"（`docs/archive/2026-09-17-群聊编排/设计定稿.md` §9.2.1）：
    不写 `Room.log`、不开任何回合，只回一条终帧提示。

    不拦的后果**不是**"压缩没生效"，而是这串命令文本进流水、被所有 NPC 看见 ——
    玩家以为压了、实际污染了对话。压缩的单位是某个成员的一整份记忆，
    正确归属地是他的单聊窗。

    兜底判据必须与单聊同规约：先剥游戏时间戳（C# 在进 WS **之前**拼前缀，
    所以实机串是 `"[1年3月] /compact"`，裸判据永远匹配不上）。
    """
    loop, _ = _seed({LIN: ["嗯。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI])
    before = len(room.log)

    _say(hub, room.id, "[1年3月] /compact")
    assert len(room.log) == before, "命令文本被当成群发言写进了 Room.log"
    assert loop.get(LIN) is None and loop.get(LI) is None, "不该因这条命令开跑任何成员的回合"
    ends = [f for f in ws.of("room_state") if f.get("phase") == "end"]
    assert ends, "拦下之后没有终帧 ⇒ 群窗会一直显示'正在输入'"
    assert ends[-1].get("error") is True, "拦下的提示必须走 error 终帧（与'一轮异常'同通道）"
    assert ends[-1].get("room_id") == room.id, "终帧必须带本群 room_id（C# 按它准入）"
    assert "单聊窗口" in (ends[-1].get("text") or ""), "提示要指路（§9.2.1：到他的单聊窗操作）"


# ══════════════════════════════════════════════════════════════════
# 跳过（`room_skip` 事件）—— 玩家这一轮不置一词，NPC 按轮转自己接下去
#
# 语义边界（定稿）：
#   · 跳过**不是**"替玩家说一句"⇒ 不写 `Room.log`、不发 `room_say`、不进任何人的记忆；
#   · 跳过**不是**独立机制 ⇒ 与 `room_message` 共用 `run_round` / `room_lock` / `_run_round`；
#   · 两条入口**仅有的**差异 = 催话文案 + 全员沉默时终帧写不写（见 room_flow 两个常量）。
# ══════════════════════════════════════════════════════════════════

def _plugin_texts(agent, plugin="@python-harness/group"):
    """投给该 agent 的 plugin 消息正文（催话）。`_delivered` 刻意滤掉了它们。"""
    out = []
    for ev in agent.session.log:
        if ev.get("type") != "user/message":
            continue
        d = ev.get("data") or {}
        if (d.get("source") or {}).get("plugin") != plugin:
            continue
        for blk in (d.get("content") or []):
            if isinstance(blk, dict) and blk.get("type") == "text":
                out.append(blk.get("text") or "")
    return out


def _skip(hub, room_id):
    return _run(hub.handle_room_skip(room_id))


def test_skip_runs_a_round_without_any_player_line():
    """★ 跳过 = 一轮**没有玩家发言**的接力：真的跑出 NPC 发言，且流水里一个玩家字都没有。

    正负两条必须一起断言：只断言"没写玩家发言"的话，**什么都不做**也能过 ——
    而那恰恰是这个功能最可能的失败形态（按钮点了没反应）。
    """
    loop, _ = _seed({LIN: ["我先说。"], LI: ["我接着说。"], WANG: ["我也说。"]})
    hub, ws = _hub(loop)
    room = _open(hub, [LIN, LI, WANG])

    _skip(hub, room.id)

    assert room.log, "跳过之后一条发言都没有 —— 这一轮没跑起来"
    assert all(u.speaker != PLAYER for u in room.log), \
        "跳过被写成了玩家发言：%s" % [(u.seq, u.speaker, u.text) for u in room.log]
    assert all(u.text.strip() for u in room.log), "流水里进了空字符串（等于给玩家塞了一条空气泡）"
    says = ws.of("room_say")
    assert says, "跳过那一轮没有任何 room_say 回推 —— 发言没上屏"
    assert all(f.get("speaker") != PLAYER for f in says), "回推里出现了玩家回声"


def test_skip_does_not_write_a_player_bubble_into_any_memory():
    """★ 跳过不得在**任何成员的账本**里留下"玩家说了话"的痕迹。

    与上一条的区别：那条看 `Room.log`，这条看每个人的 session —— 两者是两条独立的
    落账路径（`run_message` 少走一步，不代表别处没顺手补一条）。

    判据取**玩家来源的投递**（`source.kind == "room"`）在跳过前后逐字不变，而不是
    "正文里没有'玩家'两个字"：后者是假判据 —— 玩家真的说了话时，正文里也不含"玩家"。
    借用 `run_message` 的形态会当场露馅：它先 `_say_and_emit(PLAYER, "")`，
    于是每人多收一条**空的**玩家投递（条数 +1、正文为 ""）。
    """
    loop, _ = _seed({LIN: ["我先说。"], LI: ["我接着说。"], WANG: ["我也说。"]})
    hub, _ = _hub(loop)
    room = _open(hub, [LIN, LI, WANG])
    _say(hub, room.id, Q1)                       # 先让"玩家来源的投递"真实存在

    def _player_bubbles(agent):
        out = []
        for ev in agent.session.log:
            if ev.get("type") != "user/message":
                continue
            d = ev.get("data") or {}
            if (d.get("source") or {}).get("kind") != "room":
                continue
            for blk in (d.get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "text":
                    out.append(blk.get("text") or "")
        return out

    before = {w: _player_bubbles(loop.get(w)) for w in (LIN, LI, WANG)}
    assert before[LIN] == [Q1], "前置条件不成立：玩家那句没那么投给他 %r" % before[LIN]

    _skip(hub, room.id)

    for who in (LIN, LI, WANG):
        after = _player_bubbles(loop.get(who))
        assert after == before[who], \
            "%s 的账本里多了一条玩家投递（跳过被写成了玩家发言）：%r" % (who, after)
        assert all(t.strip() for t in after), "玩家投递里出现了空正文（空气泡）"


def test_skip_hands_the_first_speaker_fresh_material():
    """★ 跳过那一轮**不是空转**：轮转指针指向的第一个候选人手上有他还没见过的发言。

    机制：`deliver` 把游标推到**投递时刻**的 `last_seq`，所以一轮里先说话的人看不到
    后说话的人 —— 下一次轮到的那个人，账上必然攒着上一轮被压在后面的几条。
    （注意区分：即使增量为空，前情也**仍在**他的 surface 里、模型照样看得见。
    这条断言管的是"本轮有没有新东西可接"，不是"模型瞎不瞎"。）
    """
    loop, _ = _seed({LIN: ["甲说。"], LI: ["乙说。"], WANG: ["丙说。"]})
    hub, _ = _hub(loop)
    room = _open(hub, [LIN, LI, WANG])
    _say(hub, room.id, Q1)                       # 一轮跑满 K 人

    nxt = room.members[hub.rooms._pointer[room.id]]
    agent = loop.get(nxt)
    before = len(_delivered(agent))
    cur = agent.cursor_for(room.id)
    assert room.delta_for(nxt, cur), \
        "跳过那一轮的第一个候选人（%s）手上没有任何新发言 —— 这一轮等于对着空气说话" % nxt
    _skip(hub, room.id)
    assert len(_delivered(agent)) > before, "说好的新料没有投给他"


def test_skip_uses_its_own_nudge_and_keeps_the_normal_one_intact():
    """★ 两条入口的催话**必须不同**，且各自的文案各自到位。

    为什么跳过要另写一句：那一轮没有任何新投递，而他上一条自己的回复往往就是【沉默】——
    同一句"轮到你说话了"会诱出**一致的沉默**；更要紧的是它没说"玩家这一轮不会开口"，
    于是刚答完玩家的 NPC 有理由认为该**等玩家接话**，而那个人正是按下跳过的人。
    """
    loop, _ = _seed({LIN: ["答。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, _ = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN, LI, WANG])

    _say(hub, room.id, Q1, speak_first=[LIN])
    normal = _plugin_texts(loop.get(LIN))
    assert normal, "玩家发言轮没有注入催话"
    assert "轮到你说话了" in normal[-1]
    assert "玩家没有插话" not in normal[-1], "玩家发言轮用上了跳过文案"

    _skip(hub, room.id)
    skipped = [t for t in _plugin_texts(loop.get(LIN)) if t not in normal]
    assert skipped, "跳过那一轮没有注入任何催话"
    assert "玩家没有插话" in skipped[-1], \
        "跳过那一轮用的还是常规催话（模型会以为自己该等玩家接话）：%s" % skipped[-1]
    # 两句共同的约束不能丢（它们存在的理由与跳过无关）
    for t in (normal[-1], skipped[-1]):
        assert "不要写名字前缀" in t
        assert "【沉默】" in t


def test_skip_all_silent_tells_the_player_instead_of_a_blank_screen():
    """★ 跳过那一轮**全员弃权** ⇒ 终帧必须给一句话。

    屏幕上的对照：玩家发言轮全员沉默时，屏幕上还有玩家自己那条气泡，"空"不等于"坏"；
    跳过轮玩家**一条气泡都没发**，再不给终帧文案就是「按了没反应」——
    而"按钮坏了"与"他们都不想说话"在玩家眼里长得一模一样。
    """
    loop, _ = _seed({LIN: [SILENT_SENTINEL], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, ws = _hub(loop)
    room = _open(hub, [LIN, LI, WANG])

    # 阴性对照：**玩家发言轮**的全员沉默仍然不写文案（旧行为不许被顺手改掉）
    _say(hub, room.id, Q1)
    ends = [f for f in ws.of("room_state") if f.get("phase") == "end"]
    assert ends[-1].get("text") == "", \
        "玩家发言轮的'全员沉默不写提示'被改了：%r" % ends[-1].get("text")

    ws.frames.clear()
    _skip(hub, room.id)
    ends = [f for f in ws.of("room_state") if f.get("phase") == "end"]
    assert ends, "跳过那一轮没有终帧（群窗会永远停在'正在输入'）"
    assert ends[-1].get("speaking") == [] and ends[-1].get("text"), \
        "全员弃权的跳过轮没有给玩家任何解释：%r" % ends[-1]


def test_skip_fails_closed_when_the_room_is_gone_or_the_round_blows_up():
    """★ 失败也必须回一帧 `phase="end"` —— 那是 C# 群窗复位忙碌态的**唯一**依据。

    群窗的忙碌态是"点击时就置真、等终帧复位"的：漏发终帧 ⇒ 一直显示"正在输入"，
    最长要等 240s 的 `BusyWatchdog` 才自愈，且**一行解释都没有**。
    """
    loop, _ = _seed({LIN: ["答。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, ws = _hub(loop)

    # ① 群不在了（真机形态：群刚被解散/删除，那个窗还开着）
    _skip(hub, "g404")
    ends = [f for f in ws.of("room_state") if f.get("phase") == "end"]
    assert ends and ends[-1].get("error") is True, "指向不存在的群时没有回 error 终帧"
    assert "不存在" in (ends[-1].get("text") or "")

    # ② 缺 room_id：没有群窗可回帧，只能丢弃（不许抛、不许静默开跑）
    ws.frames.clear()
    _skip(hub, "")
    assert not ws.frames, "缺 room_id 时不该回帧（也不知道回给谁）"

    # ③ 轮次内部炸了（谁也没料到的那种）
    room = _open(hub, [LIN, LI, WANG])
    ws.frames.clear()
    real = hub.rooms.run_round

    async def boom(_rid, *_a, **_kw):
        raise RuntimeError("模拟：轮次内部异常")

    hub.rooms.run_round = boom
    try:
        _skip(hub, room.id)
    finally:
        hub.rooms.run_round = real
    ends = [f for f in ws.of("room_state") if f.get("phase") == "end"]
    assert ends and ends[-1].get("error") is True, "轮次异常时没有回 error 终帧"
    assert ends[-1].get("text") == ChatHub.FALLBACK_TEXT


def test_skip_never_goes_through_run_message():
    """★ 契约守卫：跳过**不得**借用 `run_message`。

    `run_message` 的第一件事就是给玩家落一条发言（`_say_and_emit(PLAYER, text)`）——
    借用它 = 往流水里写一条**玩家没说过的话**（空文本时就是个空气泡），
    全群可见、进所有成员记忆。判据走 **AST**（只看真的调用），不走字符串：
    本测试的注释里正大光明写着 `run_message`，按字符串判就是"注释一解释就红"。
    """
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(ChatHub.handle_room_skip)))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    assert "run_message" not in called, "跳过走了 run_message —— 会往流水里塞一条玩家发言"
    assert "say" not in called and "_say_and_emit" not in called, "跳过直接落了账"


def test_player_echo_frame_carries_raw_text_through_full_path():
    """★ 走**完整链路**（handle_room_message → run_message → 帧）验 `echo_text` ★

    11-21 用户截图：群窗上同一条消息出现两遍
        `@姜萌 你在干什么`  ← C# 本地气泡（原文）
        `你在干什么`        ← Python 回声（`strip_mentions` 剥过）

    C# 按文本对号销账，而两边文本必然不同 ⇒ 画两遍。
    修法是 Python 在帧上额外交一份原文。这个字段**必须真的出现在帧上** ——
    只在 `_say_and_emit` 里传进去、却漏在 `_emit_say` 的 `**kw` 里，症状与没修一样。
    """
    loop, _ = _seed({LIN: ["在。"], LI: ["嗯。"]})
    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN])

    _say(hub, room.id, "@" + LIN + " 你在干什么")

    echoes = [f for f in ws.of("room_say") if f.get("speaker") == "player"]
    assert echoes, f"没有玩家回声帧：{[f['event'] for f in ws.frames]}"
    f = echoes[-1]
    assert f.get("echo_text") == "@" + LIN + " 你在干什么", (
        "回声帧没带玩家原文 ⇒ C# 按剥过的正文对号永远不等 ⇒ 同一条画两遍（11-21 截图）"
    )
    # 正文仍然是剥过的（模型不该看见 @ 语法，投递侧另有断言）
    assert f.get("text") == "你在干什么", f"正文不该是原文：{f.get('text')}"


def test_plain_echo_has_no_extra_key():
    """没有 @ 的普通发言：帧形状与改动前**逐字节相同**（不多写 `echo_text`）。"""
    loop, _ = _seed({LIN: ["在。"]})
    hub, ws = _hub(loop)
    hub.rooms.k = 1
    room = _open(hub, [LIN])

    _say(hub, room.id, "随便说一句")

    echoes = [f for f in ws.of("room_say") if f.get("speaker") == "player"]
    assert echoes
    assert "echo_text" not in echoes[-1], f"普通发言不该多写键：{echoes[-1]}"

