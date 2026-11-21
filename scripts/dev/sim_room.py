"""群聊端到端模拟 —— 用**真编排器**跑一场聚谈，把每人实际看到/说了什么打印出来。

    cd /mnt/f && PYTHONPATH=/mnt/f python3 agent_loop/scripts/dev/sim_room.py

不需要游戏、不需要真 LLM（`StubGameBridge` + 脚本化 `StubLlmClient`）。

与 `sim_group_l1.py` 的区别（两者互补，都要留着）：
- `sim_group_l1.py` **手工扮演编排层**：自己调 `deliver`、自己写账本。
  它验证的是"L1 段在多人场景下成文是否正确"，与编排器无关。
- **本脚本走完整生产路径**：`ChatHub.handle_room_message` → `RoomFlow` → `_run_turn`
  → `agent.session`。它验证的是**编排器本身**（投递增量、调度、场合标记、游标、
  落盘一致性），不是一份手抄的近似实现。

覆盖场景：
  ① 开场三人接力        ② 模型视角的转录（说话人前缀）
  ③ 弃权（哨兵）        ④ 加人（新人看不到入群前）
  ⑤ 踢人（在场段差分）  ⑥ 关窗 / 重开
  ⑦ 存档 / 读档         ⑧ 不变量 I1 / I2 / I3 / I4
"""

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))   # /mnt/f

from agent_loop.agent_loop import AgentLoop                    # noqa: E402
from agent_loop.transport.bridge import StubGameBridge                   # noqa: E402
from agent_loop.session.history import project_ui_history              # noqa: E402
from agent_loop.session.history_ops import plan_delete, resolve_turn_ranges  # noqa: E402
from agent_loop.llm.stub_client import StubLlmClient           # noqa: E402
from agent_loop.room.room import PLAYER                             # noqa: E402
from agent_loop.room.room_flow import SILENT_SENTINEL               # noqa: E402
from agent_loop.session.session import collect_deleted_seqs            # noqa: E402
from agent_loop.transport.ws_channel import ChatHub                      # noqa: E402

ROOT = "/tmp/agent_loop_sim_room"
P = "韩立"
LIN, LI, WANG, ZHAO = "林婉清", "李四", "王五", "赵六"
CAST = (LIN, LI, WANG, ZHAO)

_FAIL = []


def check(cond, msg):
    print(f"    {'✔' if cond else '✘'} {msg}")
    if not cond:
        _FAIL.append(msg)
    return cond


def rule(title):
    print("\n" + "═" * 78)
    print(title)
    print("═" * 78)


class FakeWs:
    def __init__(self):
        self.frames = []

    async def send_event(self, event, **kw):
        self.frames.append({"event": event, **kw})


def _scripted(replies, default=SILENT_SENTINEL):
    state = {k: list(v) for k, v in replies.items()}

    async def fn(req):
        sys_text = str(req.get("system") or "")
        for who, seq in state.items():
            if who in sys_text and seq:
                return {"text": seq.pop(0) if len(seq) > 1 else seq[0]}
        return {"text": default}

    return StubLlmClient(fn)


def seed(replies):
    shutil.rmtree(ROOT, ignore_errors=True)
    b = StubGameBridge()
    for who in CAST:
        b.seed_unit(who, {
            "realm": "金丹", "sect": "化神殿", "pos": "永宁州·白帝城", "mood": 72,
            "player": {"name": P, "realm": "筑基", "relation": "好友", "intim": 120,
                       "same_grid": True},
            "others": [
                {"id": o, "name": o, "resolved": True, "sex": "女", "race": "人族",
                 "realm": "金丹后期", "sect": "白帝城", "age": 120, "mood": 80,
                 "beauty_label": "出众", "reputation_label": "名震一时",
                 "personality": {"仁善": 3}, "relation": "好友", "intim": 120,
                 "same_grid": True}
                for o in CAST if o != who
            ],
            "recent": "上月与韩立同行",
            "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"},
        })
    return AgentLoop.reset_for_test(storage_root=ROOT, bridge=b, llm=_scripted(replies))


def hub_of(loop):
    ws = FakeWs()
    return ChatHub(loop, ws, max_concurrent_turns=4), ws


def say(hub, room_id, text, first=()):
    asyncio.run(hub.handle_room_message(room_id, text, list(first)))


def msgs_of(agent):
    """投递进来的消息（player + peer），排除 L1 段与 nudge。"""
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
                out.append((src.get("speaker") or "player", blk.get("text") or ""))
    return out


def transcript(agent):
    """**模型实际看到的那一份**转录（带 `说话人：` 前缀的投影）。"""
    lines = []
    for m in agent._apply_speaker_labels(agent.session.derive_messages()):
        for blk in (m.get("content") or []):
            if not isinstance(blk, dict) or blk.get("type") != "text":
                continue
            t = (blk.get("text") or "").strip()
            if not t:
                continue
            tag = m.get("role")
            if t.startswith("Current runtime context"):
                lines.append(("  [L1]", t.split("——", 1)[-1].strip()[:58]))
            elif "轮到你说话了" in t:
                lines.append(("  [催]", t[:34] + "…"))
            else:
                lines.append((f"{tag:>6}", t[:62]))
    return lines


def l1_segments(agent):
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


def show_transcript(who, loop):
    ag = loop.get(who)
    print(f"\n  ┌─ {who} 的模型视角 ─────────────────────────────────────────")
    if ag is None:
        print("  │ （没建 agent —— 从未被点名，零成本）")
        print("  └" + "─" * 60)
        return
    for tag, line in transcript(ag):
        print(f"  │ {tag} {line}")
    print("  └" + "─" * 60)


# ══════════════════════════════════════════════════════════════════
def scene1_opening():
    rule("① 开场：玩家一句 → 三人接力（林婉清 → 李四 → 王五，王五弃权）")
    loop = seed({LIN: ["青须藤么……我听过些风声。"], LI: ["此事怕是有人做局。"],
                 WANG: [SILENT_SENTINEL]})
    hub, ws = hub_of(loop)
    room = hub.rooms.open_room([LIN, LI, WANG], title="青须藤之事")
    print(f"  建群 {room.id}（{room.title}）成员={room.members}")
    say(hub, room.id, "你们怎么看青须藤的事？", [LIN])

    print("\n  ── 房间流水（群窗看到的就是它）")
    for u in room.log:
        who = "玩家" if u.speaker == PLAYER else u.speaker
        print(f"     #{u.seq} {who:<8} {u.text}")

    for who in (LIN, LI, WANG):
        show_transcript(who, loop)

    check([u.speaker for u in room.log] == [PLAYER, LIN, LI], "流水 = 玩家 + 林婉清 + 李四（王五弃权不入流水）")
    check(not any(ws_speaker == WANG for ws_speaker in [f["speaker"] for f in ws.frames if f["event"] == "room_say"]),
          "弃权者没有 room_say 广播")
    check([t for _, t in msgs_of(loop.get(LIN))] == ["你们怎么看青须藤的事？"],
          "林婉清只收到玩家那句（自己的话不投给自己）")
    check([s for s, _ in msgs_of(loop.get(LI))] == ["player", LIN],
          "李四收到 玩家 + 林婉清（接力增量）")
    check(len(msgs_of(loop.get(WANG))) == 3, "王五排第三，收到全部三条 —— 弃权 ≠ 没听见")
    return loop, hub


def scene2_abstain():
    rule("② 弃权：不写流水、不广播，但**游标照常推进**")
    loop = seed({LIN: [SILENT_SENTINEL], LI: ["我来说两句。"]})
    hub, ws = hub_of(loop)
    room = hub.rooms.open_room([LIN, LI, WANG])
    say(hub, room.id, "谁先说？", [LIN])
    lin = loop.get(LIN)
    check([u.speaker for u in room.log] == [PLAYER, LI], "弃权者不进流水")
    check(lin.cursor_for(room.id) == 1, f"游标推到了 1（实际 {lin.cursor_for(room.id)}）")
    check([t for _, t in msgs_of(lin)] == ["谁先说？"], "而它确实收到了那些话")
    print(f"\n  林婉清弃权 → 游标 {lin.cursor_for(room.id)}，投递给它 {len(msgs_of(lin))} 条")


def scene3_join_kick():
    rule("③ 加人 / 踢人：新人看不到入群前的话；踢人后在场段差分重发")
    loop = seed({LIN: ["先说的这句。", "后说的这句。"], LI: ["入群后我才开口。"],
                 WANG: [SILENT_SENTINEL] * 4})
    hub, _ = hub_of(loop)
    room = hub.rooms.open_room([LIN])
    say(hub, room.id, "第一问", [LIN])
    print(f"  群里已有：{[u.text for u in room.log]}")

    hub.rooms.add_member(room.id, LI)
    print(f"  加入李四（入群位置 = {room.joined.get(LI)}，房内末位 = {room.last_seq}）")
    say(hub, room.id, "第二问", [LI])
    li_texts = [t for _, t in msgs_of(loop.get(LI))]
    check("第一问" not in li_texts, f"新人**没有**收到入群前的「第一问」（实收 {li_texts}）")
    check("先说的这句。" not in li_texts, "新人也没有收到入群前林婉清那句")
    check("第二问" in li_texts, "新人收到了入群后的内容")

    hub.rooms.add_member(room.id, WANG)
    wang = loop.get(WANG)
    check(wang is None, "刚加入、还没轮到的人**不建 agent**（零成本）")

    say(hub, room.id, "第三问", [LIN])
    before = [t for t in l1_segments(loop.get(LIN)) if "—— 在场：" in t]
    hub.rooms.kick(room.id, LI)
    say(hub, room.id, "第四问", [LIN])
    after = [t for t in l1_segments(loop.get(LIN)) if "—— 在场：" in t]
    print(f"\n  踢人前在场段：{before[-1].split('——', 1)[-1].strip()}")
    print(f"  踢人后在场段：{after[-1].split('——', 1)[-1].strip()}")
    check(len(after) > len(before), "踢人后在场段**重新发了一条**（差分触发）")
    check(LI not in after[-1], "新在场段里没有李四")
    check(loop.get(LI).scene == "", "被踢者立刻复位成单聊（否则单聊回合会在 1:1 窗静默消失）")


def scene4_close_reopen():
    rule("④ 关窗 / 重开：scene 复位、流水与游标续上、不重投不漏投")
    loop = seed({LIN: ["一句。"], LI: ["两句。"]})
    hub, _ = hub_of(loop)
    hub.rooms.k = 1
    room = hub.rooms.open_room([LIN, LI, WANG])
    say(hub, room.id, "第一问", [LIN])
    check(loop.get(LIN).scene == "", "回合结束后 scene 已复位（作用域是回合，不是「在群里」）")

    hub.rooms.close_room(room.id)
    check(all(loop.get(w) is None or loop.get(w).scene == "" for w in (LIN, LI, WANG)),
          "关窗后全员 scene 复位")

    again = hub.rooms.open_room([], room_id=room.id)
    check(again.id == room.id and [u.text for u in again.log] == ["第一问", "一句。"],
          "重开同一个 id，流水原样续上")

    lin = loop.get(LIN)
    say(hub, room.id, "第二问", [LIN])
    texts = [t for _, t in msgs_of(lin)]
    check(texts.count("第一问") == 1, f"旧发言不重投（实收 {texts}）")
    check("第二问" in texts, "新发言不漏投")


def scene5_save_load():
    rule("⑤ 存档 / 读档：记忆与游标在同一个文件、同一次 flush（不变量 I3）")
    loop = seed({LIN: ["存档前说的。"], LI: ["我也说。"], WANG: [SILENT_SENTINEL] * 4})
    hub, _ = hub_of(loop)
    room = hub.rooms.open_room([LIN, LI, WANG])
    say(hub, room.id, "第一问", [LIN])
    rid = room.id
    cursors = {w: loop.get(w).cursor_for(rid) for w in (LIN, LI, WANG)}
    room_log = [u.text for u in room.log]
    loop.flush_all()
    print(f"  已存档：{loop.rooms_root}/{rid}.jsonl + index.json")
    print(f"  游标：{cursors}")

    loop2 = AgentLoop.reset_for_test(storage_root=ROOT, bridge=loop.bridge, llm=loop.llm)
    n = loop2.load_rooms()
    room2 = loop2.load_room_log(rid)
    check(n == 1 and room2 is not None, f"读档载入 {n} 个群")
    check([u.text for u in room2.log] == room_log, "流水原样恢复")
    ok = True
    for w in (LIN, LI, WANG):
        loop2.create(w)
        if loop2.get(w).cursor_for(rid) != cursors[w]:
            ok = False
    check(ok, "每个成员的游标跟着**自己的 session** 一起活过来（不会「说听过了但记忆里没有」）")

    hub2, _ = hub_of(loop2)
    say(hub2, rid, "第二问", [LIN])
    texts = [t for _, t in msgs_of(loop2.get(LIN))]
    check(texts.count("第一问") == 1, f"读档后不重投（实收 {texts}）")
    check("第二问" in texts, "读档后不漏投")


def scene6_invariants():
    rule("⑥ 不变量：I1（每条消息都住在闭合回合里）/ I2（模型不读 Room.log）/ I4（账本存原文）")
    loop = seed({LIN: ["林的一句。"], LI: ["李的一句。"]})
    hub, _ = hub_of(loop)
    room = hub.rooms.open_room([LIN, LI, WANG])
    say(hub, room.id, "第一问", [LIN])

    i1_ok, i1_detail = True, ""
    for who in (LIN, LI):
        ag = loop.get(who)
        turns = [e["data"]["turn"] for e in ag.session.log if e.get("type") == "turn/start"]
        spans = {t: resolve_turn_ranges(ag.session, [t])[0] for t in turns}
        for _, _, in []:
            pass
        for ev in ag.session.log:
            if ev.get("type") != "user/message":
                continue
            d = ev.get("data") or {}
            if (d.get("source") or {}).get("plugin"):
                continue
            t = None
            for e2 in reversed(ag.session.log[:ev["seq"]]):
                if e2.get("type") == "turn/start":
                    t = e2["data"]["turn"]
                    break
            lo, hi = spans[t]
            if not (lo <= ev["seq"] <= hi):
                i1_ok = False
                i1_detail = f"{who} seq={ev['seq']} 归属 turn={t}，跨度=[{lo},{hi}]"
    check(i1_ok, f"每条投递都在它所属回合的删除跨度内 {i1_detail}")
    ag = loop.get(LI)
    pl = plan_delete(ag.session, [1])
    hit = collect_deleted_seqs(ag.session.log) | {s for a, b in pl["ranges"] for s in range(a, b + 1)}
    peer_seq = [e["seq"] for e in ag.session.log
                if e.get("type") == "user/message"
                and ((e.get("data") or {}).get("source") or {}).get("kind") == "peer"]
    check(pl["ok"] and peer_seq and peer_seq[0] in hit,
          f"删回合真能剔掉同席发言（plan_delete ok={pl['ok']}，peer seq={peer_seq} ∈ 剔除集）")

    import json as _json
    for who in (LIN, LI):
        a = loop.get(who)
        dumped = _json.dumps(a.session.derive_messages(), ensure_ascii=False)
        check("room/cursor" not in dumped, f"{who} 的模型上下文里没有 room/cursor（游标对模型隐形）")
        check(all(t not in dumped for _, t in []), "")
        ui = _json.dumps(project_ui_history(a.session, max_turns=0), ensure_ascii=False)
        check("room/cursor" not in ui, f"{who} 的 UI 投影里没有 room/cursor")

    # I4：账本存原文，前缀只在发给模型的那一份
    li_msgs = msgs_of(loop.get(LI))
    peer_raw = [t for s, t in li_msgs if s == LIN]
    check(peer_raw and not peer_raw[0].startswith(LIN),
          f"账本存原文（{peer_raw[0]!r}），`{LIN}：` 前缀只加在发给模型的那一份")


def scene7_events():
    rule("⑦ 事件流：C# 群窗靠 room_say 追加气泡、text_delta 带 room_id 路由")
    loop = seed({LIN: ["我说一句。"], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub, ws = hub_of(loop)
    room = hub.rooms.open_room([LIN, LI], title="测试")
    say(hub, room.id, "在吗", [LIN])
    kinds = {}
    for f in ws.frames:
        kinds[f["event"]] = kinds.get(f["event"], 0) + 1
    print(f"  收到事件：{kinds}")
    says = [f for f in ws.frames if f["event"] == "room_say"]
    check([f["speaker"] for f in says] == [PLAYER, LIN], "room_say 覆盖玩家与每位实际发言者")
    check(all(f.get("room_id") == room.id for f in says), "room_say 都带 room_id")
    deltas = [f for f in ws.frames if f["event"] == "text_delta"]
    check(deltas and all(f.get("room_id") == room.id for f in deltas),
          f"text_delta 带 room_id（{len(deltas)} 帧）—— 一个 NPC 在两个群里也路由得清")
    states = [f for f in ws.frames if f["event"] == "room_state"]
    check([f["phase"] for f in states][:-1] == ["speak"] * (len(states) - 1)
          and states and states[-1]["phase"] == "end",
          f"room_state = 每人一条点名帧 + 最后一条终帧（{[(f['phase'], f['speaking']) for f in states]}）")

    # ★ 全员弃权也必须有终帧 —— 否则群窗忙碌态永远不复位
    quiet = seed({LIN: [SILENT_SENTINEL], LI: [SILENT_SENTINEL], WANG: [SILENT_SENTINEL]})
    hub2, ws2 = hub_of(quiet)
    r2 = hub2.rooms.open_room([LIN, LI, WANG])
    say(hub2, r2.id, "有人在吗", [LIN])
    s2 = [f for f in ws2.frames if f["event"] == "room_state"]
    check([f["phase"] for f in s2][-1:] == ["end"] and not [f for f in ws2.frames if f["event"] == "room_say" and f["speaker"] != PLAYER],
          f"全员弃权：零 room_say，但仍有终帧（{len(s2)} 条 room_state）")


def main():
    scene1_opening()
    scene2_abstain()
    scene3_join_kick()
    scene4_close_reopen()
    scene5_save_load()
    scene6_invariants()
    scene7_events()

    rule("汇总")
    if _FAIL:
        print(f"  ✘ {len(_FAIL)} 项未通过：")
        for m in _FAIL:
            print(f"     - {m}")
        sys.exit(1)
    print("  ✔ 全部通过")


if __name__ == "__main__":
    main()
