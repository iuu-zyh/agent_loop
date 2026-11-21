"""场合过滤 / 截断 / 删除 三条路径的可视化探针（解释用，一次性）。

    cd /mnt/f && PYTHONPATH=/mnt/f python3 agent_loop/scripts/dev/probe_scene_filter.py

造一个「单聊 → 群聊 → 单聊 → 群聊 → 单聊」交错的多场合账本，
把 project_ui_history 的过滤/截断结果与 plan_delete 的实际跨度逐行打出来。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_loop.agent_loop import AgentLoop            # noqa: E402
from agent_loop.transport.bridge import StubGameBridge           # noqa: E402
from agent_loop.session.history import project_ui_history      # noqa: E402
from agent_loop.session.history_ops import plan_delete, resolve_turn_ranges  # noqa: E402
from agent_loop.llm.echo_client import EchoLlmClient   # noqa: E402

ROOT = "/tmp/agent_loop_probe_scene"
NPC, PLAYER = "林婉清", "韩立"
G1, G2 = "g1", "g2"


def seed():
    b = StubGameBridge()
    b.seed_unit(NPC, {
        "realm": "金丹", "pos": "永宁州", "mood": 72,
        "player": {"name": PLAYER, "realm": "筑基", "relation": "好友",
                   "intim": 120, "same_grid": True},
        "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"},
    })
    return AgentLoop.reset_for_test(storage_root=ROOT, bridge=b, llm=EchoLlmClient())


def run(agent, text):
    agent.send(text)
    asyncio.run(agent.run_until_idle())


def build():
    """账本形状：单聊/群1/单聊/群2/单聊 —— 故意交错，让 turn 号稀疏。"""
    loop = seed()
    a = loop.create(NPC)
    plan = [
        (None, "私下一", "私下答一"),      # turn 1  单聊
        (G1,   "群里一", "群一答"),        # turn 2  群 g1
        (None, "私下二", "私下答二"),      # turn 3  单聊
        (G2,   "群里二", "群二答"),        # turn 4  群 g2
        (None, "私下三", "私下答三"),      # turn 5  单聊
    ]
    for scene, q, _ans in plan:
        a.set_scene(scene)
        run(a, q)
    a.set_scene(None)
    return loop, a, plan


def show_items(tag, out):
    print(f"  {tag}")
    by_turn = {}
    for it in out["items"]:
        if it["kind"] in ("user", "assistant"):
            by_turn.setdefault(it["turn"], []).append(it["text"])
    for t in sorted(by_turn):
        txt = " / ".join(by_turn[t])
        print(f"     turn={t}  scene={[i['scene'] for i in out['items'] if i['turn'] == t][0]!r:8} {txt}")
    print(f"     → items={len(out['items'])}  complete_turns={out['complete_turns']}")


def main():
    loop, a, plan = build()

    print("═" * 78)
    print("账本原始形状（turn 号由 DialogueAgent 分配，与场合无关）")
    print("═" * 78)
    for e in a.session.log:
        if e.get("type") == "turn/start":
            d = e["data"]
            print(f"   seq={e['seq']:>3}  turn/start turn={d['turn']}  scene={d.get('scene')!r}")

    print()
    print("═" * 78)
    print("① 三种 scene 参数的投影结果")
    print("═" * 78)
    show_items('scene=""（1:1 窗缺省）', project_ui_history(a.session, max_turns=0, scene=""))
    print()
    show_items(f'scene="{G1}"（群 g1）', project_ui_history(a.session, max_turns=0, scene=G1))
    print()
    show_items("scene=None（全部经历）", project_ui_history(a.session, max_turns=0, scene=None))

    print()
    print("═" * 78)
    print("② 截断发生在**过滤之后** —— max_turns 是「该场合内最近 N 个」")
    print("═" * 78)
    for n in (1, 2, 3):
        show_items(f'scene="" + max_turns={n}', project_ui_history(a.session, max_turns=n, scene=""))
        print()

    print("═" * 78)
    print("③ 删除：C# 回传的 turn 号 → plan_delete 查到的账本跨度")
    print("═" * 78)
    spans = {t: resolve_turn_ranges(a.session, [t])[0] for t in (1, 2, 3, 4, 5)}
    print(f"  扫全账本得 turn → [start_seq, end_seq]：{spans}")
    solo = project_ui_history(a.session, max_turns=0, scene="")
    solo_turns = sorted({i["turn"] for i in solo["items"]})
    print(f"  1:1 窗显示 {len(solo_turns)} 条，turn 号 = {solo_turns}  ← 稀疏，但**是账本原值**")
    print(f"  玩家点第 2 条 → C# RowMeta.Turn = {solo_turns[1]} → plan_delete([{solo_turns[1]}])")
    pl = plan_delete(a.session, [solo_turns[1]])
    scene_of = {e["data"]["turn"]: e["data"].get("scene") for e in a.session.log
                if e.get("type") == "turn/start"}
    print(f"  → 实际删的跨度 = {pl['ranges']}（= 账本 turn {solo_turns[1]}，"
          f"场合 scene={scene_of[solo_turns[1]]!r} —— 正是玩家看到的那条）✔ 正确")


    print()
    print("═" * 78)
    print("④ 反证：如果过滤后**重编号**（1,2,3…），会发生什么")
    print("═" * 78)
    renumbered = list(enumerate(solo_turns, start=1))
    print(f"  重编号映射：显示序号 → 真实 turn = {dict(renumbered)}")
    pick = 2
    real = dict(renumbered)[pick]
    print(f"  玩家点「显示第 {pick} 条」→ C# 传 turns=[{pick}]")
    pl2 = plan_delete(a.session, [pick])
    print(f"  → plan_delete([{pick}]) 删掉的跨度 = {pl2['ranges']}"
          f"（= 账本 turn {pick}，场合 scene={scene_of[pick]!r}）")
    print(f"     ★ 玩家以为在删「账本 turn {real}」（scene={scene_of[real]!r}），"
          f"实际删了「账本 turn {pick}」（scene={scene_of[pick]!r}）")
    print(f"     ★ {'同一个回合' if real == pick else '不同的回合 —— 而且不报错、没有日志、UI 无变化'}")
    left = project_ui_history(a.session, max_turns=0, scene="")
    print(f"  删完后重编号投影仍是 {len(sorted({i['turn'] for i in left['items']}))} 条"
          f" → 玩家看到「删了没反应」，再点一次又删掉一个看不见的回合")



    print()
    print("═" * 78)
    print("⑤ 为什么「过滤」必须在「截断」之前")
    print("═" * 78)
    turns_all = [e["data"] for e in a.session.log if e.get("type") == "turn/start"]
    print(f"  账本 {len(turns_all)} 个回合，场合 = "
          f"{[(t['turn'], t.get('scene') or '单聊') for t in turns_all]}")
    for n in (2, 3):
        # 实际顺序：先过滤后截断
        right = project_ui_history(a.session, max_turns=n, scene="")
        # 反证顺序：先截断后过滤（模拟）
        tail = turns_all[-n:]
        wrong_turns = [t["turn"] for t in tail if (t.get("scene") or "") == ""]
        print(f"  max_turns={n}")
        print(f"     先截断后过滤 → 取账本末 {n} 个回合 {[t['turn'] for t in tail]}，"
              f"其中单聊只剩 {wrong_turns} ⇒ {len(wrong_turns)} 条")
        print(f"     先过滤后截断 → {sorted({i['turn'] for i in right['items']})}"
              f" ⇒ {len(right['items']) // 2} 条  ✔ 实际实现")
    print("  ★ 「最近 N 个回合」若先截断，就变成「最近 N 个回合里**恰好是单聊**的那些」——")
    print("    玩家在群里连聊 10 轮之后，1:1 窗传 max_turns=10 会得到 **0 条**。")


if __name__ == "__main__":
    main()
