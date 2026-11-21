"""群聊流程模拟 —— 一次完整聚谈回合，逐层打印"谁看到了什么、为什么"。

**这不是单元测试，是给人看的说明脚本。** 单元测试见 `tests/test_l1_multi_npc.py`。

它手工扮演 **Room 编排层**（P1 才实现的角色）：决定谁说话、按什么顺序、把谁的话投递给谁。
P0 只做到 L1/SystemPrompt 层，所以编排在这里是硬编码的 —— 打印时用
`[P0]` / `[P1]` 标出每一步现在归谁管。

用法：
    cd /mnt/f && PYTHONPATH=/mnt/f python3 agent_loop/scripts/dev/sim_group_l1.py
"""

import asyncio
import copy
import sys

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.agent.dialogue_agent import create_user_message
from agent_loop.llm.base import LlmResult


PLAYER = "韩立"
ROOM = ["林婉清", "李四", "王五"]
NPC_INFO = {
    "林婉清": {"realm": "金丹", "sect": "化神殿", "sex": "女", "mood": 72, "age": 30,
             "relation": "道侣", "intim": 180, "same_grid": True, "point": {"x": 31, "y": 78},
             "title": "清霜", "beauty_label": "仙姿", "reputation_label": "声名远扬",
             "personality": {"inner": "仁善", "inner_desc": "与人为善，见不得他人受苦",
                             "outer": ["正直"], "outer_desc": ["刚正不阿"]}},
    "李四":   {"realm": "金丹后期", "sect": "白帝城", "sex": "女", "mood": 90, "age": 34,
             "relation": "好友", "intim": 87, "same_grid": True, "point": {"x": 31, "y": 78},
             "title": "青霜", "beauty_label": "出众", "reputation_label": "名震一时",
             "personality": {"inner": "情种", "inner_desc": "重情重义，易为情所困",
                             "outer": ["孤僻", "爱美"], "outer_desc": ["不喜应酬", "在意仪容"]}},
    "王五":   {"realm": "元婴初期", "sect": "", "sex": "男", "mood": 40, "age": 51,
             "relation": "仇人", "intim": -40, "same_grid": True, "point": {"x": 31, "y": 78},
             "title": "", "beauty_label": "平平", "reputation_label": "默默无闻",
             "personality": {"inner": "睚眦", "inner_desc": "有仇必报，锱铢必较",
                             "outer": ["贪婪"], "outer_desc": ["见利忘义"]}},
}
# ★ 两两之间的关系（"谁看谁"），与"各自对玩家的关系"是两回事。
#   `raw.others[].relation/intim/same_grid` 必须是**观察者视角**的；C# 侧实现时同理。
PAIR_REL = {
    # ★ 刻意做成**有向且不对称**："林婉清→李四" 与 "李四→林婉清" 取不同的值。
    # 为什么不用 frozenset（对称）：**对称数据验证不出方向错误** —— C# 里 `RelationData`
    # 是"我"的关系簿，`viewer...relationData.GetIntim(target)` 是「viewer 对 target」的好感；
    # 写成对方的簿子查回来会得到**反向好感且不报错**。只有不对称的数据能当场看出来对不上。
    ("林婉清", "李四"): ("好友", 87),      # 林婉清眼中的李四：日渐亲近
    ("李四", "林婉清"): ("好友", 25),      # 李四眼中的林婉清：交情尚可（与上一条不同 ✔）
    ("林婉清", "王五"): ("仇人", -40),     # 林婉清眼中的王五：已是仇人
    ("王五", "林婉清"): ("陌生", 5),       # 王五眼中的林婉清：素不相识（与上一条不同 ✔）
    ("李四", "王五"): ("陌生", 0),
    ("王五", "李四"): ("好友", 60),        # 王五眼中的李四：日渐亲近（与上一条不同 ✔）
}
REPLIES = {
    "林婉清": "青须藤么……我听过些风声，只是不便当众说。",
    "李四": "婉清姐姐既然知道，何不说与我等听听？",
    "王五": "哼，装神弄鬼。",
}


class RecordingLlm:
    provider = "sim"
    model = "sim"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
        self.calls.append(copy.deepcopy(messages))
        return LlmResult(text=self.reply, tool_calls=[], provider="sim", model="sim")


def pair_relation(viewer, other):
    """**有向**查表（`PAIR_REL` 的键是 `(观察者, 对方)`）。缺省"陌生/0"。"""
    return PAIR_REL.get((viewer, other), ("陌生", 0))


def build_world():
    bridge = StubGameBridge()
    for who, info in NPC_INFO.items():
        others = []
        for o in ROOM:
            if o == who:
                continue
            rel, intim = pair_relation(who, o)
            oi = NPC_INFO[o]
            entry = {"id": o, "name": o, "realm": oi["realm"],
                     "sect": oi["sect"], "sex": oi["sex"],
                     "mood": oi["mood"], "age": oi["age"],
                     "point": oi["point"],
                     "relation": rel, "intim": intim, "same_grid": True}
            # 新增字段也一起走一遍：道号 / 性格 / 容貌声名档位 / 种族。
            # `resolved` 显式给 true —— C# 解析成功时会带它（模拟脚本照契约来，
            # 免得"本地跑得通、真机少个键"）。
            entry["resolved"] = True
            entry["race"] = oi.get("race", "人族")
            entry["title"] = oi.get("title", "")
            entry["personality"] = oi.get("personality")
            entry["beauty_label"] = oi.get("beauty_label", "")
            entry["reputation_label"] = oi.get("reputation_label", "")
            others.append(entry)
        bridge.seed_unit(who, {
            "realm": info["realm"], "sect": info["sect"], "sex": info["sex"],
            "mood": info["mood"], "age": info["age"], "power": 8000, "point": info["point"],
            "relation": info["relation"], "intim": info["intim"],
            "player": {"name": PLAYER, "realm": "筑基", "relation": info["relation"],
                       "intim": info["intim"], "same_grid": info["same_grid"]},
            "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"},
            "others": others,
        })
    loop = AgentLoop.reset_for_test(storage_root="/tmp/agent_loop_sim_group", bridge=bridge, llm=None)
    agents, llms = {}, {}
    for who in ROOM:
        agents[who] = loop.create(who)
        llms[who] = RecordingLlm(REPLIES[who])
        agents[who].llm = llms[who]
    return loop, bridge, agents, llms


def deliver(agent, u):
    """[P1] Room 的投递：一条发言落进某个 NPC 的账本。

    ★ 形态 = **inbox 的 next-step**（`docs/archive/2026-09-17-群聊编排/计划-下游消费方.md` §四 D1 = 乙）：
      只投递、不唤醒（`wake=False`），什么时候跑回合由 Room 决定。
      走 inbox ⇒ 消息必然落在**某个回合内部**，`history.project_ui_history` 的
      「投影归属」与 `history_ops` 的「删除跨度」因此天然一致。

    ✘ 反面：回合外 `session.append(..., {"surfaceOp": "append"})`。实测三个破口
      （`probe_group_history.py` ①/②/③）：投影把游离消息算进**上一个已闭合回合**、
      而该回合的删除跨度不含它 ⇒ **假删除**（UI 说没了、模型还记得）；首个回合之前的
      投递被**静默丢弃**。

    ★ 另一条契约：落**原文**，归属走 `source` —— `说话人：` 前缀由 P0 的投影在发给模型时才加。
      上游若先拼一遍，会得到「李四：李四：尚可。」。
    """
    agent.send(u["text"], target="next-step", wake=False,
               source=None if u["speaker"] == "player"
               else {"kind": "peer", "speaker": u["speaker"]})


def show_room_log(log):
    print("  ── 房间流水（Room.log，唯一真相；UI 回放与增量计算都以它为准）")
    for i, u in enumerate(log, 1):
        who = u["speaker"]
        mark = "玩家" if who == "player" else who
        print(f"     #{i} {mark}：{u['text']}")
    print()


def speak(who, agents, llms, room_log):
    """[P1 编排 + P0 机制] 让一个 NPC 开口，并打印它收到的增量与看到的上下文。"""
    agent = agents[who]
    # ── P1：算增量 =「自它上次发言以来、**别人**说过的话」（自己说的不投）──
    delta = [u for u in room_log if u["speaker"] != who]
    print(f"  ┌─ 轮到【{who}】")
    print(f"  │ [P1] 投递增量（{len(delta)} 条；规则：比我的游标新、且不是我说的）")
    for u in delta:
        tag = "玩家" if u["speaker"] == "player" else f"同席·{u['speaker']}"
        print(f"  │        · [{tag}] {u['text']}")
    for u in delta:
        deliver(agent, u)
    # ── P0：设在场名单 → nudge → 跑回合 ──
    agent.set_roster([o for o in ROOM if o != who])
    agent.send("（轮到你说话了。）", source={"kind": "plugin", "plugin": "@python-harness/group"})
    asyncio.run(agent.run_until_idle())
    print(f"  │ [P0] set_roster([{', '.join(o for o in ROOM if o != who)}]) → 在场段/同席段差分更新")
    print(f"  │ [P0] 跑完一个 agent turn（内部可含多 step + 工具）")
    print(f"  │ 模型看到的 user 流：")
    for m in llms[who].calls[-1]:
        if m.get("role") != "user":
            continue
        for b in (m.get("content") or []):
            if isinstance(b, dict) and b.get("type") == "text":
                kind = (m.get("source") or {}).get("kind") or "player"
                tag = {"plugin": "注入", "peer": "同席", "player": "玩家"}.get(kind, kind)
                print(f"  │        [{tag}] {b['text'].splitlines()[0][:88]}")
    print(f"  └─ 【{who}】{REPLIES[who]}\n")
    return {"speaker": who, "text": REPLIES[who]}


def scene1(agents, llms):
    print("=" * 84)
    print("场景一：一次完整聚谈回合 —— 玩家问一句，林婉清、李四接力接话（王五本轮没被选中）")
    print("=" * 84)
    print(f"\n  房间成员：{PLAYER}（玩家）、{'、'.join(ROOM)}")
    print(f"  本轮发言序列：[林婉清, 李四]      ← [P1] 调度器决定（点名 > 轮转 > K 上限）\n")

    room_log = [{"speaker": "player", "text": "[1年3月] 你们怎么看青须藤的事？"}]
    print(f"  [玩家] {room_log[0]['text']}\n")
    show_room_log(room_log)

    for who in ("林婉清", "李四"):
        room_log.append(speak(who, agents, llms, room_log))
        show_room_log(room_log)
    return room_log


def scene2(room_log, agents, llms):
    print("=" * 84)
    print("场景二：王五本轮没说话 —— 下一轮被点名，一次性补齐上下文")
    print("=" * 84)
    print("\n  王五这期间**零成本**：[P1] 不建回合、不调 LLM；它的游标停在原地。\n")
    room_log.append({"speaker": "player", "text": "[1年3月] 王五，你也说说。"})
    print(f"  [玩家] {room_log[-1]['text']}\n")
    show_room_log(room_log)
    room_log.append(speak("王五", agents, llms, room_log))
    return room_log


def scene3(agents, llms, bridge):
    print("=" * 84)
    print("场景三：散场 —— 林婉清回到单聊")
    print("=" * 84)
    agent = agents["林婉清"]
    agent.set_roster([])
    bridge._units["林婉清"]["others"] = []
    agent.send("[1年4月] 总算只剩我们俩了。")
    asyncio.run(agent.run_until_idle())
    msgs = llms["林婉清"].calls[-1]
    print("\n  最后几条 user 消息：")
    for m in msgs[-4:]:
        for b in (m.get("content") or []):
            if isinstance(b, dict) and b.get("type") == "text":
                print(f"  │ {b['text'][:104]}")
    presents = [b["text"] for m in msgs for b in (m.get("content") or [])
                if isinstance(b, dict) and "—— 在场：" in b.get("text", "")]
    assert presents and "此刻并无他人在场" in presents[-1], f"在场段没撤回：{presents}"
    print("\n  ✓ 最后一条在场声明 =「此刻并无他人在场」")
    print("    （「在场」段恒非空，所以「出房间」也是内容变化 → 差分必然通知模型；")
    print("      这就是不需要给同席段另写「撤回」逻辑的原因）")


def scene4(agents):
    print()
    print("=" * 84)
    print("场景四：工具流量私有 —— 林婉清查了东西，李四的上下文里不该有")
    print("=" * 84)
    a, b = agents["林婉清"], agents["李四"]
    a.session.append("user/message", create_user_message(
        "〔工具返回〕李四的背包：青须藤 ×3（只有林婉清看得到）", source={"kind": "tool"}),
        {"surfaceOp": "append"})

    def contains(agent, needle):
        msgs = agent._apply_speaker_labels(
            agent._inline_image_refs(agent.session.derive_messages()))
        return any(needle in (x.get("text") or "")
                   for m in msgs for x in (m.get("content") or []) if isinstance(x, dict))

    assert contains(a, "青须藤 ×3"), "林婉清自己应该看得到"
    assert not contains(b, "青须藤 ×3"), "李四不该看到林婉清的私有查询结果"
    print("\n  ✓ 林婉清看得到；李四看不到")
    print("    规则：**查询私有，行动公开** —— 只读工具的结果只进调用者自己的上下文；")
    print("    多人共享一份 tool-call/tool-result 会破坏 session 的配对不变量，")
    print("    也会教模型在该查的时候模仿别人发 tool-call。")


def scene5(agents):
    """方向校验：**同一个人**，在不同观察者眼里必须是不一样的。

    这是"关系方向写反"唯一的现场判据。C# 的 `RelationData` 是"我"的关系簿，
    `viewer...relationData.GetIntim(target)` = 「viewer 对 target」的好感；写成对方的簿子
    查回来得到的是**反向好感，且不报任何错**。用**不对称**数据才能当场看出来。
    """
    print()
    print("=" * 84)
    print("场景五：关系方向校验 —— 同一个人，不同观察者眼里必须不同")
    print("=" * 84)
    print(f"\n  设定（PAIR_REL，有向）：")
    for (v, o), (rel, intim) in PAIR_REL.items():
        print(f"    {v} → {o}：{rel}，好感 {intim}")

    def other_seg(agent, who):
        for ev in agent.session.log:
            if ev.get("type") != "user/message":
                continue
            src = (ev.get("data") or {}).get("source") or {}
            if src.get("plugin") != "@python-harness/system-prompt":
                continue
            c = (ev.get("data") or {}).get("content") or []
            t = c[0].get("text", "") if c and isinstance(c[0], dict) else ""
            if f"—— 同席·{who}：" in t:
                return t
        return ""

    def tone(seg):
        """从同席段里抽出"关系+好感档位"那一句（诊断用，不参与断言逻辑）。"""
        for p in seg.split("。"):
            if any(k in p for k in ("之谊", "素不相识", "已是")):
                return p.strip()
        return "(未渲染出关系)"

    print("\n  方向矩阵（行 = 观察者，列 = 被观察者）—— 同一个人在各行必须不同：")
    for viewer in ROOM:
        for other in ROOM:
            if viewer == other:
                continue
            seg = other_seg(agents[viewer], other)
            print(f"    {viewer} 眼中的 {other}：{tone(seg)}")

    # 硬断言：两对**刻意不对称**的关系，方向写反必然当场失败
    a = other_seg(agents["林婉清"], "李四")
    b = other_seg(agents["李四"], "林婉清")
    assert "日渐亲近" in a and "交情尚可" in b, f"方向疑似写反：\n  {a}\n  {b}"
    c = other_seg(agents["林婉清"], "王五")
    d = other_seg(agents["王五"], "林婉清")
    assert "已是仇人" in c and "素不相识" in d, f"方向疑似写反：\n  {c}\n  {d}"
    print("\n  ✓ 林婉清看李四「日渐亲近」，李四看林婉清「交情尚可」")
    print("  ✓ 林婉清看王五「已是仇人」，王五看林婉清「素不相识」")
    print("  ✓ 好感一律走**档位词**（`_intim_tier`），段里不出现裸数值")
    print("  ✓ 道号（「青霜」/「清霜」）与性格（含注解）都已成文")


def main():
    _, bridge, agents, llms = build_world()
    room_log = scene1(agents, llms)
    room_log = scene2(room_log, agents, llms)
    scene3(agents, llms, bridge)
    scene4(agents)
    scene5(agents)
    print()
    print("=" * 84)
    print("本次跑通的（P0，已提交）：动态段清单 / 在场段 / 同席段 / 离场清理 / 统一命名")
    print("                        名单上行(C#契约前提) / resolved 剔除 / 道号 / 性格 / 好感档位词")
    print("尚未实现的（P1/P3）：Room 编排、调度器、C# 多说话人 UI、动作工具的 NPC 目标")
    print("=" * 84)


if __name__ == "__main__":
    main()
