"""群聊下游消费方：压缩材料归属 + UI 回放的场合过滤（对应 `docs/archive/2026-09-17-群聊编排/计划-下游消费方.md` 的 G1–G12）。

全部脱离游戏与真实 LLM（StubGameBridge + EchoLlmClient）。
"""

import asyncio

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.compaction.compress import Compressor
from agent_loop.agent.dialogue_agent import create_user_message
from agent_loop.session.history import project_ui_history
from agent_loop.session.history_ops import plan_delete
from agent_loop.agent.initiative import format_initiative_message
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.session.session import Session, collect_deleted_seqs
from agent_loop.transport.ws_channel import ChatHub


ROOT = "/tmp/agent_loop_test_group_history"
NPC = "林婉清"
PLAYER = "韩立"
ROOM = "room:白帝城"


def _seed():
    bridge = StubGameBridge()
    bridge.seed_unit(NPC, {
        "realm": "金丹", "sect": "化神殿", "pos": "永宁州·白帝城", "mood": 72, "power": 8900,
        "player": {"name": PLAYER, "realm": "筑基", "relation": "道侣", "intim": 180, "same_grid": True},
        "recent": "上月与韩立结为道侣",
        "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"},
    })
    loop = AgentLoop.reset_for_test(storage_root=ROOT, bridge=bridge, llm=EchoLlmClient())
    return loop, bridge


def _run(agent, text, source=None):
    agent.send(text, source=source)
    asyncio.run(agent.run_until_idle())


def _deliver(agent, speaker, text):
    """同席投递的**契约形态**（D1 = 乙）：走 inbox 的 next-step，**不** wake。

    回合外 `session.append` 是**错的**——那会让消息不属于任何回合，投影与删除随之错位。
    见 `scripts/dev/probe_group_history.py` 的 ①/②/④ 三段对照。
    """
    agent.send(text, target="next-step", wake=False,
               source=None if speaker == "player"
               else {"kind": "peer", "speaker": speaker})


def _turns(session):
    return [e for e in session.log if e.get("type") == "turn/start"]


def _render(session, npc=NPC, player=PLAYER):
    return Compressor(llm=None, player_name=player)._render_transcript(
        session, {"npc_name": npc, "player_name": player})


def _append(session, text, source=None, role="user"):
    msg = {"role": role, "content": [{"type": "text", "text": text}], "id": f"m{len(session.log)}"}
    if source:
        msg["source"] = source
    session.append("user/message", msg, {"surfaceOp": "append"})


# ══════════════════════════════════════════════════ G1–G3 压缩材料归属

def test_g1_initiative_renders_as_stage_direction_not_player():
    """`initiative` 是括号舞台指令（`initiative.py`），**不是玩家真言**。

    只看 role 会渲染成「玩家：（NPC主动传音：…）」⇒ 纪要记下"玩家主动传音说要打招呼"，
    而纪要是永久节点、下次压缩还喂回来 ⇒ 越传越歪。与 plugin 是同一 bug 类。
    """
    s = Session(NPC)
    _append(s, format_initiative_message("greet", "想起旧事"),
            source={"kind": "initiative", "intent": "greet", "reason": "想起旧事"})
    out = _render(s)
    assert "[舞台指令] （NPC主动传音：" in out, out
    assert f"{PLAYER}：（NPC主动传音" not in out, out
    assert "玩家：" not in out, out


def test_g2_peer_line_marked_as_third_party():
    """同席发言带「（同席）」标记 —— 摘要器据此知道这是**房间里另一个角色**。

    不加标记时 `李四：…` 与玩家别名无法区分，而压缩指令的 6 个分节彻底以玩家为中心
    ⇒ 第三方的话最可能被折进「与玩家（{player_name}）的关系」。
    """
    s = Session(NPC)
    _append(s, "尚可。", source={"kind": "peer", "speaker": "李四"})
    out = _render(s)
    assert "李四（同席）：尚可。" in out, out
    assert f"{PLAYER}：尚可" not in out, out


def test_g3_plugin_injection_still_skipped():
    """L1 运行时上下文（plugin）仍被入口拦掉 —— 不能因为加了新分支就漏掉它。"""
    s = Session(NPC)
    _append(s, "Current runtime context —— 自身：你是林婉清",
            source={"kind": "plugin", "plugin": "@python-harness/system-prompt"})
    _append(s, "你好。")
    out = _render(s)
    assert "Current runtime context" not in out, out
    assert f"{PLAYER}：你好。" in out, out


# ══════════════════════════════════════════════════ G4–G7 场合标签与过滤

def test_g4_turn_start_carries_scene_and_set_scene_normalizes():
    """`scene` 随 `turn/start` 落账；`set_scene` 把 None/空白归一成 `""`。"""
    loop, _ = _seed()
    a = loop.create(NPC)
    _run(a, "第一句")
    assert _turns(a.session)[-1]["data"]["scene"] == "", "缺省必须是单聊场合"

    a.set_scene(f"  {ROOM}  ")
    assert a.scene == ROOM, "两侧空白应被 strip"
    _run(a, "房间里的答复")
    assert _turns(a.session)[-1]["data"]["scene"] == ROOM

    a.set_scene(None)
    assert a.scene == "", "出房必须能复位成单聊"


def test_g5_scene_filter_splits_solo_and_room():
    """`scene=""` 只出单聊；`scene=None` 不过滤；其它值只出该场合。"""
    loop, _ = _seed()
    a = loop.create(NPC)
    _run(a, "单聊这句")
    a.set_scene(ROOM)
    _run(a, "房间里这句")
    a.set_scene(None)
    _run(a, "出房后这句")

    texts = lambda out: [i.get("text") for i in out["items"] if i["kind"] == "user"]

    solo = project_ui_history(a.session, max_turns=0, scene="")
    assert texts(solo) == ["单聊这句", "出房后这句"], texts(solo)

    room = project_ui_history(a.session, max_turns=0, scene=ROOM)
    assert texts(room) == ["房间里这句"], texts(room)

    every = project_ui_history(a.session, max_turns=0, scene=None)
    assert texts(every) == ["单聊这句", "房间里这句", "出房后这句"], texts(every)


def test_g6_filtered_items_keep_ledger_turn_numbers():
    """★ 过滤**不重编号**：item 的 `turn` 始终是账本原值。

    C# 的 `RowMeta.Turn` 直接把它交回 `plan_delete` 当键；一旦按显示序号重编号，
    删除就会删到别的回合上（而且不报错）。
    """
    loop, _ = _seed()
    a = loop.create(NPC)
    _run(a, "单聊 1")
    a.set_scene(ROOM)
    _run(a, "房间 1")
    a.set_scene(None)
    _run(a, "单聊 2")

    solo = project_ui_history(a.session, max_turns=0, scene="")
    assert sorted({i["turn"] for i in solo["items"]}) == [1, 3], "账本 turn 原值 1/3（不是显示序号 1/2）"
    assert solo["complete_turns"] == 2, "complete_turns 也是过滤后的口径"

    room = project_ui_history(a.session, max_turns=0, scene=ROOM)
    assert sorted({i["turn"] for i in room["items"]}) == [2]


def test_g7_peer_item_carries_speaker_and_scene():
    """peer 项带 `speaker`（显示名）与 `scene`，且**不**计成玩家气泡。"""
    loop, _ = _seed()
    a = loop.create(NPC)
    a.set_scene(ROOM)
    _deliver(a, "player", "你们都在啊")
    _deliver(a, "李四", "林道友，别来无恙。")
    _run(a, "（轮到你说话了。）", source={"kind": "plugin", "plugin": "@python-harness/group"})

    items = project_ui_history(a.session, max_turns=0, scene=ROOM)["items"]
    peers = [i for i in items if i["kind"] == "peer"]
    assert len(peers) == 1, items
    assert peers[0]["speaker"] == "李四"
    assert peers[0]["text"] == "林道友，别来无恙。", "账本存原文，前缀由投影加"
    assert peers[0]["scene"] == ROOM
    assert [i["text"] for i in items if i["kind"] == "user"] == ["你们都在啊"], "玩家的仍算玩家"
    assert project_ui_history(a.session, max_turns=0, scene="")["items"] == [], "单聊场合应为空"


# ══════════════════════════════════════════════════ G8–G10 删除一致性

def test_g8_inbox_delivery_keeps_projection_and_deletion_consistent():
    """★ D1=乙 的核心收益：**投影归属**与**删除跨度**一致，peer 删得掉。

    对照 `scripts/dev/probe_group_history.py` ①：回合外 append 时投影说 turn=1、
    但 turn 1 的删除跨度不含它 ⇒ 假删除（UI 说没了、模型还记得）。
    """
    from agent_loop.session.history_ops import resolve_turn_ranges

    loop, _ = _seed()
    a = loop.create(NPC)
    _deliver(a, "player", "你们都在啊")
    _deliver(a, "李四", "林道友，别来无恙。")
    _run(a, "（轮到你说话了。）", source={"kind": "plugin", "plugin": "@python-harness/group"})

    items = project_ui_history(a.session, max_turns=0)["items"]
    peers = [i for i in items if i["kind"] == "peer"]
    assert len(peers) == 1, items

    home = peers[0]["turn"]
    lo, hi = resolve_turn_ranges(a.session, [home])[0]
    assert lo <= peers[0]["seq"] <= hi, (
        f"peer seq={peers[0]['seq']} 必须落在它所属 turn({home}) 的跨度 [{lo},{hi}] 内，"
        "否则删除会漏掉它而 UI 看不出来")

    plan = plan_delete(a.session, [home])
    assert plan["ok"] is True, plan["reason"]
    deleted = collect_deleted_seqs(a.session.log) | set(range(plan["ranges"][0][0], plan["ranges"][0][1] + 1))
    assert peers[0]["seq"] in deleted, "plan_delete 必须把 peer 消息一并剔除"
    # 落账后重投影：该 peer 确实不见了（账本 → UI 全链路一致）
    from agent_loop.session.history_ops import append_delete
    append_delete(a.session, plan)
    after = project_ui_history(a.session, max_turns=0, scene=None)["items"]
    assert [i for i in after if i["kind"] == "peer"] == [], after


def test_g9_parse_scene_wire_semantics():
    """线协议：缺省/`null` → `""`（单聊，默认）；`"*"` → `None`（不过滤）；其它 → 原样。

    ★ `null` 必须与"缺省"同义：C# 序列化器把没赋值的字段发成 null 时，
    **不能**因此静默关掉过滤（那会退回"1:1 窗混进群聊内容"的老行为）。
    """
    assert ChatHub._parse_scene({}) == ""
    assert ChatHub._parse_scene({"scene": None}) == "", "null 与缺省同义，都走默认（单聊）"
    assert ChatHub._parse_scene({"scene": "*"}) is None, "唯一的不过滤出口"
    assert ChatHub._parse_scene({"scene": " * "}) is None, "容忍两侧空白"
    assert ChatHub._parse_scene({"scene": ROOM}) == ROOM
    assert ChatHub._parse_scene({"scene": ""}) == "", "显式空串 = 单聊，不做二次猜测"
    assert ChatHub._parse_scene({"npc_id": NPC}) == "", "无关参数不该影响判据"


def test_g10_default_solo_filter_is_a_noop_until_p1_stamps_scenes():
    """★ 缺省过滤今天必须是**可证的无操作**：没人设 scene 时，全部回合都属于单聊。

    这条保证"不传 scene 的旧 C# 行为不变"，也让 P1 只需给群聊回合盖章即可生效。
    """
    loop, _ = _seed()
    a = loop.create(NPC)
    _run(a, "一")
    _run(a, "二")
    assert all(t["data"]["scene"] == "" for t in _turns(a.session))

    default = project_ui_history(a.session, max_turns=0)                 # 不过滤（老调用方）
    solo = project_ui_history(a.session, max_turns=0, scene="")          # 新缺省（UI 线协议）
    assert default["items"] == solo["items"]
    assert default["complete_turns"] == solo["complete_turns"] == 2


# ══════════════════════════════════════════════════ G11–G12 线协议与跨场合删除

def test_g11_history_out_echoes_scene_and_npc():
    """`_history_out` 回执带 `npc_id` 与 `scene`，让 C# 知道自己拿到的是哪个切片。"""
    loop, _ = _seed()
    a = loop.create(NPC)
    _run(a, "一")
    out = ChatHub._history_out(a, NPC, 0, ROOM)
    assert out["npc_id"] == NPC and out["scene"] == ROOM
    assert out["items"] == [], "场合对不上就该是空的，而不是退化成全部"

    out2 = ChatHub._history_out(a, NPC, 0, None)
    assert out2["scene"] is None and len(out2["items"]) > 0


def test_g12_deleting_a_room_turn_leaves_solo_turns_alone():
    """删房间回合只影响房间回合；单聊回合**原封不动**（场合是回合级属性，不会连坐）。"""
    loop, _ = _seed()
    a = loop.create(NPC)
    _run(a, "单聊 1")
    a.set_scene(ROOM)
    _run(a, "房间 1")
    a.set_scene(None)
    _run(a, "单聊 2")

    from agent_loop.session.history_ops import append_delete
    plan = plan_delete(a.session, [2])
    assert plan["ok"] is True, plan["reason"]
    append_delete(a.session, plan)

    solo = project_ui_history(a.session, max_turns=0, scene="")
    assert [i.get("text") for i in solo["items"] if i["kind"] == "user"] == ["单聊 1", "单聊 2"]
    assert project_ui_history(a.session, max_turns=0, scene=ROOM)["items"] == []


# ══════════════════════════════════════════════════ G13–G16 压缩指令（NPC 视角）

_NO_SUCH_ROOT = "/tmp/agent_loop_no_such_prompts_dir"


def _instruction(prompts_root=None, npc="__no_such_npc__"):
    """走**真实加载路径**取指令：先找 {npc}.md，回落 default.md；prompts_root 不存在则走兜底常量。"""
    return Compressor(llm=None, prompts_root=prompts_root)._load_instruction(npc)


def test_g13_default_instruction_keeps_load_bearing_phrases():
    """`default.md` 里三处是别的测试与运行时的硬契约，改动时别误删。"""
    text = _instruction()
    assert "对话压缩器" in text, "声明身份（test_compaction_fixes 断言 system 含它）"
    assert "<compacted-summary>" in text and "</compacted-summary>" in text, "输出结构标记"
    assert "绝不调用工具" in text, "铁律（test_compaction_fixes 断言）"
    assert "绝不续写对话" in text, "铁律：纪要不得变成活对话"


def test_g14_instruction_is_second_person_not_first_not_player_centric():
    """★ 纪要必须用**第二人称「你」**写 —— 与 L1 运行时上下文同一套人称。

    纪要与 L1 段走**同一个通道**（`role:user` 且 `source.kind=plugin`，都不加说话人前缀）。
    L1 说的是「你是林婉清，一位女子…」；纪要若写「我外冷内热」，模型就同时读到两套自我描述。
    更糟的是 user 消息里的「我」会被读成「**玩家**在说话」，而玩家并不叫林婉清。
    """
    text = _instruction()
    assert "第二人称" in text and "「你」" in text, "必须点明人称：以「你」记"
    assert "第一人称" not in text, "第一人称是被明确否掉的那一种，不该还留在指令里"
    assert "本人的记忆" in text, "必须点明这是「{npc_name} 本人的记忆」而不是档案"
    assert "玩家已知与尚不知道" not in text, "旧措辞是**上帝视角**（玩家的知识状态），不是 NPC 的记忆"
    assert "对玩家的整体态度" not in text, "旧措辞只认玩家，多人场合漏掉在场其他人"


def test_g15_section_titles_are_nouns_matching_the_l1_labels():
    """分节名用**名词短语**，与 L1 抽屉的段标签（自身 / 玩家 / 在场 / 同席·X / 近况）同风格。

    这是跨模块的视觉一致：模型每回合都在读 `自身：…` `同席·李四：…`，
    纪要用同一套标签一眼能对上。正文才是第二人称。
    """
    text = _instruction()
    for sec in ("## 自身", "## 与{player_name}", "## 同席", "## 经历",
                "## 约定", "## 心境", "## 认知边界"):
        assert sec in text, f"缺分节 {sec}"
    assert "## 我" not in text, "分节名不该带第一人称"
    assert "我眼中的其他人" not in text, "旧（第一人称）分节名应已改名"


def test_g15b_peer_marker_names_its_landing_section():
    """「（同席）」标记必须配一个**落点分节** —— 只有标记没落点，模型仍会自己找地方塞。"""
    text = _instruction()
    assert "（同席）" in text, "必须解释这个标记（由 compress._render_transcript 产出）"
    assert "记入「经历」" in text and "「同席」里" in text, "标记必须指名落点分节"


def test_g16_all_template_placeholders_are_renderable():
    """模板里的 `{xxx}` 必须都能被 `_render_template` 替换掉。

    `_render_template` 只遍历**传入的**变量做 `str.replace` —— 写错名字（`{npc}` 而非
    `{npc_name}`）不会报错，会**静默原样留在 system 里**发给模型。
    """
    import re as _re

    comp = Compressor(llm=None)
    for text in (_instruction(), _instruction(_NO_SUCH_ROOT)):
        rendered = comp._render_template(text, {"npc_name": NPC, "player_name": PLAYER})
        leftover = sorted(set(_re.findall(r"\{(\w+)\}", rendered)))
        assert not leftover, f"有未渲染的占位符 {leftover}（变量名写错了？）"
        assert NPC in rendered and PLAYER in rendered, "变量必须真的替换进去"


def test_g17_fallback_instruction_matches_the_file():
    """无指令文件时的兜底常量必须与 `default.md` **同口径**，否则删/改名文件会静默退回旧行为。"""
    fb = _instruction(_NO_SUCH_ROOT)
    assert "对话压缩器" in fb and "绝不调用工具" in fb, "硬契约"
    assert "第二人称" in fb and "「你」" in fb, "人称口径必须跟上"
    assert "（同席）" in fb and "[舞台指令]" in fb, "归属口径必须跟上"
