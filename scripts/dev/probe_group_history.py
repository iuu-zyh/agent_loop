"""群聊下游消费方探针 —— 钉住「回合外消息」打穿投影/删除不变量的三个事实。

不需要游戏、不需要真 LLM。运行：
    cd /mnt/f && PYTHONPATH=/mnt/f python3 agent_loop/scripts/dev/probe_group_history.py

背景：P1 的 Room 投递若采用「回合外 `session.append(..., surfaceOp="append")`」
（见 `sim_group_l1.deliver`），则 `history.project_ui_history` 与
`history_ops._turn_spans` 共同依赖的不变量「每条 user/message 都属于某个
turn/start..turn/end」被打破。本脚本把后果跑出来，不靠推理。

对照：同席发言若走 **inbox**（`send(..., target="next-step", wake=False)`），
三处全部自洽 —— 见末尾「方案乙」段落。
"""

import asyncio

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.agent.dialogue_agent import create_user_message
from agent_loop.session.history import project_ui_history
from agent_loop.session.history_ops import plan_delete, resolve_turn_ranges
from agent_loop.session.session import collect_deleted_seqs
from agent_loop.llm.echo_client import EchoLlmClient

NPC, P = "林婉清", "韩立"
ROOM = ("李四", "王五")
ROOT = "/tmp/agent_loop_probe_group_history"


def _seed(*ids):
    b = StubGameBridge()
    for who in (NPC,) + tuple(ids):
        b.seed_unit(who, {
            "realm": "金丹", "pos": "永宁州", "mood": 72,
            "player": {"name": P, "realm": "筑基", "relation": "道侣", "intim": 180, "same_grid": True},
            "recent": "上月结为道侣",
            "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"},
        })
    loop = AgentLoop.reset_for_test(storage_root=ROOT, bridge=b, llm=EchoLlmClient())
    return loop


def _run(agent, text, source=None):
    agent.send(text, source=source)
    asyncio.run(agent.run_until_idle())


def deliver_out_of_turn(agent, speaker, text):
    """P1 现状形态：回合外直接 append（sim_group_l1.deliver 同款）。"""
    msg = (create_user_message(text) if speaker == "player"
           else create_user_message(text, source={"kind": "peer", "speaker": speaker}))
    agent.session.append("user/message", msg, {"surfaceOp": "append"})


def deliver_via_inbox(agent, speaker, text):
    """方案乙形态：走 inbox 的 next-step（回合内落面，**不**wake）。"""
    agent.send(text, target="next-step", wake=False,
               source=None if speaker == "player"
               else {"kind": "peer", "speaker": speaker})


def _show(agent, tag):
    out = project_ui_history(agent.session, max_turns=0)
    print(f"\n  ── {tag}")
    for it in out["items"]:
        who = f"peer({it['speaker']})" if it["kind"] == "peer" else it["kind"]
        print(f"     turn={it.get('turn')!s:>3} seq={it.get('seq')!s:>3} {who:<14} "
              f"{(it.get('text') or '')[:26]!r}")
    return out


def _spans(agent, turns):
    return {t: resolve_turn_ranges(agent.session, [t])[0] for t in turns}


# ══════════════════════════════════════════════════════════════════════
def case_out_of_turn():
    print("═" * 74)
    print("① 回合外 append（P1 现状形态）：投影归属 与 删除跨度 **不一致**")
    print("═" * 74)
    loop = _seed(*ROOM)
    a = loop.create(NPC)
    _run(a, "单聊第一句")
    deliver_out_of_turn(a, "李四", "【同席】我先打个招呼")
    deliver_out_of_turn(a, "player", "【群聊】你们都在啊")
    _run(a, "嗯，都在。")

    _show(a, "投影结果：回合外那两条被塞进了**上一个已闭合回合**")
    spans = _spans(a, (1, 2))
    print(f"\n    删除跨度：{spans}")
    for it in project_ui_history(a.session, max_turns=0)["items"]:
        if it["kind"] != "peer":
            continue
        lo, hi = spans[it["turn"]]
        ok = lo <= it["seq"] <= hi
        print(f"    peer(seq={it['seq']}) 投影归属 turn={it['turn']}，该 turn 跨度=[{lo},{hi}]"
              f" → {'可删' if ok else '★删不掉'}")
    print("    后果：UI 上删掉这一回合后，该 turn/start 也被删 ⇒ 这条 peer 落进 turn0 容器")
    print("          而**不再被发射**，看着像删掉了；但 surface 里它还在 ⇒ **模型仍记得**。")


def case_before_first_turn():
    print("\n" + "═" * 74)
    print("② 首个 turn 之前的回合外 append：**静默丢弃**")
    print("═" * 74)
    loop = _seed(*ROOM)
    a = loop.create(NPC)
    deliver_out_of_turn(a, "李四", "（我先到的）")
    out = _show(a, "投影结果")
    print(f"\n    complete_turns={out['complete_turns']} items={len(out['items'])}"
          f" → {'★整条消失' if not out['items'] else '可见'}")
    print("    原因：items 只在 turn/start 建容器；turn/start 之前的消息落进初始")
    print("          容器（turn=0，不在 turns 里）⇒ 永远不发射。")


def case_turn_ids_not_portable():
    print("\n" + "═" * 74)
    print("③ turn 号**会话局部** —— 同一条房间发言在两人账本里坐标不同")
    print("═" * 74)
    loop = _seed(*ROOM)
    a, b = loop.create(NPC), loop.create("李四")
    _run(a, "林：单聊 A")
    _run(a, "林：单聊 B")
    _run(b, "李：单聊 A")                    # 两人各自的单聊回合数不同
    for ag in (a, b):
        deliver_out_of_turn(ag, "player", "【房间】第一句")

    def coord(agent, needle):
        for ev in agent.session.log:
            if ev.get("type") != "user/message":
                continue
            d = ev.get("data") or {}
            if not any(isinstance(bl, dict) and bl.get("text") == needle
                       for bl in (d.get("content") or [])):
                continue
            for e2 in reversed(agent.session.log[:ev["seq"]]):
                if e2.get("type") == "turn/start":
                    return e2["data"]["turn"], ev["seq"]
        return None, None

    for who, ag in ((NPC, a), ("李四", b)):
        t, s = coord(ag, "【房间】第一句")
        print(f"    {who:<5} 账本 seq={s}  所属 turn={t}")
    print("    ⇒ 房间**无法用 turn 号**做跨成员标识：删除/去重/回放键都得另立（Room 坐标）。")


def case_via_inbox():
    print("\n" + "═" * 74)
    print("④ 对照 · 方案乙（走 inbox 的 next-step）：三处**全部自洽**")
    print("═" * 74)
    loop = _seed(*ROOM)
    a = loop.create(NPC)
    deliver_via_inbox(a, "player", "【群聊】你们都在啊")
    deliver_via_inbox(a, "李四", "林道友，别来无恙。")
    _run(a, "（轮到你说话了。）", source={"kind": "plugin", "plugin": "@python-harness/group"})

    out = _show(a, "投影结果：peer 落在**本回合**内部")
    spans = _spans(a, (1,))
    print(f"\n    删除跨度：{spans}")
    for it in out["items"]:
        if it["kind"] != "peer":
            continue
        lo, hi = spans[it["turn"]]
        print(f"    peer(seq={it['seq']}) 归属 turn={it['turn']}，跨度=[{lo},{hi}]"
              f" → {'★可删（投影与删除一致）' if lo <= it['seq'] <= hi else '删不掉'}")
    pl = plan_delete(a.session, [1])
    hit = collect_deleted_seqs(a.session.log) | set(range(pl["ranges"][0][0], pl["ranges"][0][1] + 1))
    print(f"    plan_delete([1]) ok={pl['ok']} ranges={pl['ranges']}，peer 在剔除集里? "
          f"{'是' if any(i['kind'] == 'peer' and i['seq'] in hit for i in out['items']) else '否'}")


if __name__ == "__main__":
    case_out_of_turn()
    case_before_first_turn()
    case_turn_ids_not_portable()
    case_via_inbox()
    print("\n" + "═" * 74)
    print("结论：① ② ③ 是同一条不变量的三个破口；④ 证明走 inbox 可以完全避开。")
    print("═" * 74)
