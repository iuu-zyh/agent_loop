"""多人化 L1 / SystemPrompt：动态段清单、在场段、同席段、离场清理、统一命名。

对应计划 `docs/archive/2026-09-17-群聊编排/计划-L1多人化.md` 的 T1–T12。
全部脱离游戏与真实 LLM（StubGameBridge + EchoLlmClient）。
"""

import asyncio
import json

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.agent.dialogue_agent import DialogueAgent, create_user_message
from agent_loop.session.history import project_ui_history
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.session.session import Session
from agent_loop.agent.system_prompt import SystemPrompt
from agent_loop.compaction.compress import Compressor


ROOT = "/tmp/agent_loop_test_l1_multi"
NPC = "林婉清"
PLAYER = "韩立"

_CTX_MARK = "Current runtime context —— "


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


def _others(*specs):
    """同席者明细（C# `raw.others` 的形态）。"""
    out = []
    for s in specs:
        out.append(dict({
            "sex": "女", "race": "人族", "realm": "金丹后期", "sect": "白帝城",
            "beauty_label": "出众", "reputation_label": "名震一时",
            "point": {"x": 31, "y": 78}, "same_grid": True,
            "relation": "好友", "intim": 87, "mood": 90,
            # 以下三项**故意放进 raw**：它们属于"查过资料库"，`format_other_segment` 必须无视
            "luck": {"born": [{"name": "天妒", "desc": "命途多舛"}]},
            "talent": 99, "power": 12345, "logs": ["秘密日志"],
        }, **s))
    return out


def _ctx_texts(agent):
    """账本里 L1 段消息的正文（按账本顺序）。"""
    out = []
    for ev in agent.session.log:
        if ev.get("type") != "user/message":
            continue
        src = (ev.get("data") or {}).get("source") or {}
        if src.get("plugin") != "@python-harness/system-prompt":
            continue
        c = (ev.get("data") or {}).get("content") or []
        if c and isinstance(c[0], dict) and c[0].get("type") == "text":
            out.append(c[0]["text"])
    return out


def _ctx_labels(agent):
    """账本里 L1 段消息的**中文标签**顺序（同席·李四 / 在场 / 自身 …）。"""
    labels = []
    for t in _ctx_texts(agent):
        if t.startswith(_CTX_MARK):
            labels.append(t[len(_CTX_MARK):].split("：", 1)[0])
    return labels


def _model_texts(agent):
    """**发给模型的那一份**消息文本（含说话人前缀；含工具返回的内嵌文本）。

    工具结果在 wire 上是 `role:tool`，文本嵌在 `{type:"tool-result", content:[{type:"text"...}]}`
    里 —— 不拆开就会漏看（`.py` 侧真的漏过一次：见 T8c 的教训）。
    """
    msgs = agent._apply_speaker_labels(
        agent._inline_image_refs(agent.session.derive_messages()))
    out = []
    for m in msgs:
        for b in (m.get("content") or []):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                out.append(b["text"])
            elif b.get("type") == "tool-result":
                for c in (b.get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "text":
                        out.append(c["text"])
    return out


def _run(agent, text, source=None):
    agent.send(text, source=source)
    asyncio.run(agent.run_until_idle())


# ---------------------------------------------------------------- T1 单聊基线

def test_t1_solo_segment_set_and_where_always_nonempty():
    """单聊：段清单 = time/self/player/where/recent_with_player/recent；`where`（所在）**恒非空**。

    「所在」是 09-24 由原「在场」段扩成的：并入自身位置、玩家位置与相对关系、同席者位置
    （位置只在这一处出现）；恒非空的撤回语义随之继承（见 `format_where_segment` 的 ★）。
    """
    loop, bridge = _seed()
    agent = loop.create(NPC)
    _run(agent, "道友安好")

    labels = _ctx_labels(agent)
    # 与玩家相关（recent_with_player）是新增基础段：无往来时整段不渲染，故这里是**子序列检查**
    # 而不是等值 —— 但各段的先后顺序必须钉死（先知道对面是谁，再读"我们之间发生过什么"）。
    # ★ 10-25 第四层拆分：新增「自身状态」/「玩家状态」两段（易变项从自身/玩家里摘出）★
    #   顺序：先"我是谁"，再"我现在什么状态"；对面同理。判据依据见 `_CTX_SEGMENTS` 上方。
    #   ⚠ 「玩家状态」在**本 fixture 里不渲染**：它只装玩家侧的魅力/声名/心境，而 `_seed`
    #   的 player 没给这三项 ⇒ 空段不发（这是刻意的：空就是"不知道"，写兜底反而是编造；
    #   它不承载状态撤回，那个职责在恒非空的「所在」段）。所以这里钉的是**子序列**。
    _seq = [x for x in labels if x in
            ("当前时间", "自身", "后天气运", "自身状态", "玩家", "玩家后天气运", "玩家状态")]
    assert _seq == ["当前时间", "自身", "后天气运", "自身状态",
                    "玩家", "玩家后天气运"], labels
    # 「行动口径」恒在**末尾**：它是最近位的那一条提醒（差分只在跨游戏日时重发），
    # 顺序一旦挪走就退化成"开局听过一次"，实测该调时的调用率会从 81% 掉回 48%。
    assert labels[-1] == "行动口径", labels
    assert labels[-2] == "近况", labels
    assert [x for x in labels if x in ("与玩家相关", "所在")] in (
        ["与玩家相关", "所在"], ["所在"]), labels
    # 「所在」紧跟「玩家后天气运」之后：先知道对面是谁、他的后天际遇，再说"我们在哪、身边还有谁"
    #   （玩家**有** beauty/reputation/mood 时，「玩家状态」会插在两者之间 —— 见上面 `_seq`）
    _i_luck = labels.index("玩家后天气运")
    assert labels[_i_luck + 1] in ("玩家状态", "所在"), labels
    assert "所在" in labels, labels
    where = [t for t in _ctx_texts(agent) if "—— 所在：" in t]
    assert len(where) == 1, where
    assert "此刻并无他人在场" in where[0], where[0]

    # 段名与标签（class 常量仍供外部引用）
    assert SystemPrompt._CTX_SEGMENTS[0] == "time"
    assert SystemPrompt._CTX_SEGMENTS == (
        "time", "self", "npc_luck", "npc_vital", "player", "player_luck",
        "player_vital", "where", "recent_with_player", "recent", "action")
    assert SystemPrompt._CTX_LABELS["npc_vital"] == "自身状态"
    assert SystemPrompt._CTX_LABELS["player_vital"] == "玩家状态"
    assert SystemPrompt.ctx_segment_of_label("自身状态") == "npc_vital"
    assert SystemPrompt.ctx_segment_of_label("玩家状态") == "player_vital"
    assert SystemPrompt._CTX_LABELS["where"] == "所在"
    assert SystemPrompt._CTX_LABELS["npc_luck"] == "后天气运"
    assert SystemPrompt._CTX_LABELS["player_luck"] == "玩家后天气运"
    assert SystemPrompt.ctx_segment_of_label("后天气运") == "npc_luck"
    assert SystemPrompt.ctx_segment_of_label("玩家后天气运") == "player_luck"
    assert SystemPrompt._CTX_LABELS["recent_with_player"] == "与玩家相关"
    assert SystemPrompt.ctx_label("other:李四") == "同席·李四"
    assert SystemPrompt.ctx_segment_of_label("同席·李四") == "other:李四"
    assert SystemPrompt.ctx_segment_of_label("所在") == "where"
    assert SystemPrompt.ctx_segment_of_label("与玩家相关") == "recent_with_player"
    assert SystemPrompt.ctx_segment_of_label("不存在的段") is None


# ---------------------------------------------------------------- T1b 气运两段（09-26）

def test_t1b_luck_added_split_and_always_nonempty():
    """★09-26 用户拍板★：**后天气运**从「自身」段拆成两段（`npc_luck` / `player_luck`）且
    **恒非空**；**先天气运恒定，留在「自身」/「玩家」段**（不进差分段）。

    三件都属"静默出事"：
      · 玩家气运此前**完全没进上下文**（数据本就在 C# 的 psnap 里，过去只挑了 realm/sect/sex/mood）；
      · 后天留在自身段 ⇒ 一次逆天改命/奇遇就要把整段自身（性格/爱好/容貌…）重发；
      · 两段若"空则不发"，后天气运被移除时"有→无"方向会失效（空段 `render_context_segment`
        返回 ""，差分收不到任何消息）—— 所以无后天也必须成文（用户拍板措辞
        "身无后天之气运，身体精神状况正常"）。
    """
    # ① 先天 + 后天：先天进主体段（自身/玩家），后天进独立段，互不重复
    loop, bridge = _seed()
    bridge._units[NPC]["luck"] = {"born": [{"name": "天煞孤星", "desc": "命途多舛"}],
                                  "added": [{"name": "紫气东来", "desc": ""}]}
    bridge._units[NPC]["player"]["luck"] = {"born": [{"name": "锦鲤附体", "desc": "福缘深厚"}],
                                            "added": [{"name": "霉运缠身", "desc": "诸事不顺"}]}
    agent = loop.create(NPC)
    _run(agent, "道友安好")
    texts = _ctx_texts(agent)
    self_t = [t for t in texts if "—— 自身：" in t][0]
    player_t = [t for t in texts if "—— 玩家：" in t][0]
    npc_luck = [t for t in texts if "—— 后天气运：" in t][0]
    player_luck = [t for t in texts if "—— 玩家后天气运：" in t][0]
    # 先天：留在自身/玩家段（不独立成段）
    assert "命带气运天煞孤星（命途多舛）" in self_t, self_t
    assert "命带气运锦鲤附体（福缘深厚）" in player_t, player_t
    # 后天：独立成段，且自身/玩家段**不含**（防重复 —— 否则后天一变整段自身又重发）
    assert "后天又得紫气东来" in npc_luck, npc_luck
    assert "后天又得霉运缠身（诸事不顺）" in player_luck, player_luck
    assert "紫气东来" not in self_t and "后天" not in self_t, "后天留在自身段"
    assert "霉运缠身" not in player_t, "后天留在玩家段"
    # 玩家后天段带**真名**作主语（独立段里"他"会指代不明）
    assert player_luck.startswith(_CTX_MARK + "玩家后天气运：" + PLAYER), player_luck

    # ② 都没有后天（seed 的常态）：两段仍成文（恒非空兜底），不是空段
    loop2, bridge2 = _seed()
    agent2 = loop2.create(NPC)
    _run(agent2, "道友安好")
    t2 = [t for t in _ctx_texts(agent2) if "—— 后天气运：" in t]
    p2 = [t for t in _ctx_texts(agent2) if "—— 玩家后天气运：" in t]
    assert t2 and "身无后天之气运，身体精神状况正常" in t2[0], t2
    assert p2 and "身无后天之气运，身体精神状况正常" in p2[0], p2

    # ③ 后天变化只重发**该段**：自身/玩家/所在三段一律不动
    def _n(agent_, label):
        return len([t for t in _ctx_texts(agent_) if f"—— {label}：" in t])
    base = {lbl: _n(agent, lbl) for lbl in ("自身", "玩家", "后天气运", "所在")}
    bridge._units[NPC]["luck"]["added"].append({"name": "福星高照", "desc": "诸事顺遂"})
    _run(agent, "哦？")
    assert _n(agent, "后天气运") == base["后天气运"] + 1, "后天变了该重发后天气运段"
    assert _n(agent, "自身") == base["自身"], "自身段被连累重发（拆段收益归零）"
    assert _n(agent, "玩家") == base["玩家"], "玩家段被连累重发"
    assert _n(agent, "所在") == base["所在"], "所在段被连累重发"


# ---------------------------------------------------------------- T2 同席段出现

def test_t2_other_segment_appears_and_is_visible_to_model():
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四"])
    bridge._units[NPC]["others"] = _others({"id": "李四", "name": "李四"})
    _run(agent, "李四兄也在？")

    labels = _ctx_labels(agent)
    assert "同席·李四" in labels, labels
    # 「所在」里点名了李四，人数 = 你 + 玩家 + 李四 = 3；同席者的位置也归这一段
    where = [t for t in _ctx_texts(agent) if "—— 所在：" in t][0]
    assert "李四" in where and "共 3 人" in where, where
    assert "与你同处一地" in where, where
    # 模型看得到该段（context 消息是 surface 成员 → derive_messages 含它）
    assert any("同席·李四" in t for t in _model_texts(agent)), _model_texts(agent)
    # 同席段带的是"与我的关系"，不是"与玩家的关系"
    other = [t for t in _ctx_texts(agent) if "—— 同席·李四：" in t][0]
    # 好感走**档位词**（`_intim_tier`，与自身段/玩家段同口径），不写裸数值：
    # 87 → 「日渐亲近」。裸数字会让模型拿面板数值当台词（「我对你好感87」）。
    assert "你与" in other and "结着好友之谊" in other and "日渐亲近" in other, other
    assert "87" not in other, f"同席段不得出现好感裸数值：{other}"
    # ★ 位置已整体搬走（09-24）★：同席段不许再出现坐标或同处/异地的措辞 ——
    #   否则同一份位置两处重复，且"他挪一步"就要重发整份可观测面
    assert "坐标" not in other and "同处一地" not in other and "异地相隔" not in other, other


# ---------------------------------------------------------------- T3 可观测面

def test_t3_other_segment_is_observable_surface_only():
    """同席段只写"抬眼能看到的"：气运 / 悟性 / 战力裸值 / 生平经历一律不出现。"""
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四"])
    bridge._units[NPC]["others"] = _others({"id": "李四", "name": "李四"})
    _run(agent, "在吗")

    other = [t for t in _ctx_texts(agent) if "—— 同席·李四：" in t][0]
    for leak in ("天妒", "命途多舛", "99", "12345", "秘密日志"):
        assert leak not in other, f"越界字段泄漏进同席段：{leak} → {other}"
    # 但可观测的档位词要在
    assert "容貌出众" in other and "在世人眼中名震一时" in other, other


def test_t3b_other_without_detail_does_not_fabricate():
    """只有名字、没有任何明细 → 整段留空，**绝不编造**（"一介散修"是断言，不是缺省值）。"""
    sp = SystemPrompt()
    assert sp.format_other_segment({"id": "李四", "name": "李四"}) == ""
    assert sp.format_other_segment({"id": "李四"}) == ""
    assert sp.format_other_segment({}) == ""
    # 但有任意一条实质信息就要成文
    assert "金丹" in sp.format_other_segment({"name": "李四", "realm": "金丹"})
    assert "一介散修" in sp.format_other_segment({"name": "李四", "realm": "金丹"})   # 有境界无宗门
    assert "一介散修" not in sp.format_other_segment({"name": "李四", "intim": 30})   # 没境界不敢说
    assert "好友" in sp.format_other_segment({"name": "李四", "relation": "好友", "intim": 30})

    # roster 点名了某人、但 C# 还没给明细：所在段仍声明他在场，同席段留空不发
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四"])
    bridge._units[NPC]["others"] = []          # 模拟"C# 还没实现 raw.others"
    _run(agent, "李四在吗")
    labels = _ctx_labels(agent)
    assert "所在" in labels, labels
    assert "同席·李四" not in labels, f"无明细不该成段：{labels}"
    assert "李四" in [t for t in _ctx_texts(agent) if "—— 所在：" in t][0]


# ---------------------------------------------------------------- T4 离场清理（三处）

def test_t4_leave_room_clears_all_three_places():
    """离场：屉 / `_last_l1` / `_retained_ctx` **三处**都要干净。"""
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四"])
    bridge._units[NPC]["others"] = _others({"id": "李四", "name": "李四"})
    _run(agent, "李四兄也在？")
    key = "other:李四"
    assert key in agent._last_l1 and key in agent._retained_ctx
    ctx_names = {c["name"] for c in agent.system_prompt.assemble(scope=NPC)["contexts"]}
    assert f"runtime:{key}" in ctx_names, ctx_names

    agent.set_roster([])                       # 回到单聊
    bridge._units[NPC]["others"] = []
    _run(agent, "只剩我们俩了")

    assert key not in agent._last_l1, "`_last_l1` 残留离场段"
    assert key not in agent._retained_ctx, "`_retained_ctx` 残留离场段"
    ctx_names2 = {c["name"] for c in agent.system_prompt.assemble(scope=NPC)["contexts"]}
    assert f"runtime:{key}" not in ctx_names2, f"屉里残留离场段：{ctx_names2}"


# ---------------------------------------------------------------- T5 ★ 核心回归

def test_t5_where_retracts_stale_company_in_transcript():
    """★核心回归★：进房间 → 回单聊，转录里**必须**出现一条"并无他人"的新在场声明。

    这是"空段发不出消息"那个洞的回归测试：旧实现里 `other:李四` 变空后静默，
    转录最后一条"李四在场"永远留着，NPC 回单聊还以为李四站在旁边。
    「所在」段恒非空（09-24 起由它继承 `present` 的职责）⇒ 出房间也是内容变化 ⇒ 差分必然通知模型。
    """
    loop, bridge = _seed()
    agent = loop.create(NPC)

    agent.set_roster(["李四"])
    bridge._units[NPC]["others"] = _others({"id": "李四", "name": "李四"})
    _run(agent, "李四兄也在？")

    agent.set_roster([])
    bridge._units[NPC]["others"] = []
    _run(agent, "现在只有我们俩")

    wheres = [t for t in _ctx_texts(agent) if "—— 所在：" in t]
    assert len(wheres) >= 2, f"出房间没有重发所在段：{wheres}"
    last = wheres[-1]
    assert "此刻并无他人在场" in last, f"最后一条在场声明没有撤回李四：{last}"
    assert "李四" not in last, last


# ---------------------------------------------------------------- T6 再进场行为

def test_t6_reenter_resends_where_and_other():
    """再进同一房间：`where`（所在）与同席段都应重发（离场时基线已被清掉）。"""
    loop, bridge = _seed()
    agent = loop.create(NPC)

    def enter():
        agent.set_roster(["李四"])
        bridge._units[NPC]["others"] = _others({"id": "李四", "name": "李四"})

    def leave():
        agent.set_roster([])
        bridge._units[NPC]["others"] = []

    enter(); _run(agent, "进来")
    n_other_1 = len([t for t in _ctx_texts(agent) if "—— 同席·李四：" in t])
    leave(); _run(agent, "出去")
    enter(); _run(agent, "又进来")

    n_other_2 = len([t for t in _ctx_texts(agent) if "—— 同席·李四：" in t])
    assert n_other_2 == n_other_1 + 1, f"再进场应恰好重发一次（{n_other_1} → {n_other_2}）"
    wheres = [t for t in _ctx_texts(agent) if "—— 所在：" in t]
    assert "此刻并无他人在场" in wheres[-2] and "李四" in wheres[-1], wheres


def test_t6b_swap_member_clears_leaver_keeps_stayer():
    """换人（李四走、王五留）：离场段清掉、**留下那段不动**、在场段重发。

    这是 T4（全员离场）与 T7（加人）都盖不到的第三种转移：
    名单**内容**变了但**人数**没变 —— 所以判据必须是"比对文本"而不是"比个数"。
    """
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四", "王五"])
    bridge._units[NPC]["others"] = _others({"id": "李四", "name": "李四"},
                                           {"id": "王五", "name": "王五"})
    _run(agent, "两位都在")
    n0_other = len([t for t in _ctx_texts(agent) if "—— 同席·" in t])

    agent.set_roster(["王五"])                      # 换人：人数 3 → 3
    bridge._units[NPC]["others"] = _others({"id": "王五", "name": "王五"})
    n0 = len(_ctx_texts(agent))
    _run(agent, "李四先走了")
    sent = _ctx_labels(agent)[n0:]

    assert sent == ["所在"], f"换人只该重发所在段（王五没变、李四离场不发撤回）：{sent}"
    assert "other:李四" not in agent._last_l1, "离场段没从屉镜像清掉"
    assert "other:李四" not in agent._retained_ctx, "离场段没从模型镜像清掉"
    ctx = {c["name"] for c in agent.system_prompt.assemble(scope=NPC)["contexts"]}
    assert "runtime:other:李四" not in ctx, f"离场段没从屉清掉：{ctx}"
    assert "other:王五" in agent._last_l1, "留下的人不该被清掉"
    # 王五那段**一次都没重发**（内容没变）
    assert len([t for t in _ctx_texts(agent) if "—— 同席·" in t]) == n0_other, "留下那段被无谓重发了"


# ---------------------------------------------------------------- T7 差分粒度 O(N)

def test_t7_per_person_diff_isolation():
    """一人一段的**全部意义**：只改王五，李四那段不许重发（否则成本退化成 O(N²)）。"""
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四", "王五"])
    bridge._units[NPC]["others"] = _others({"id": "李四", "name": "李四"},
                                           {"id": "王五", "name": "王五"})
    _run(agent, "两位都在")

    base_lisi = len([t for t in _ctx_texts(agent) if "—— 同席·李四：" in t])
    base_wangwu = len([t for t in _ctx_texts(agent) if "—— 同席·王五：" in t])
    base_where = len([t for t in _ctx_texts(agent) if "—— 所在：" in t])
    assert (base_lisi, base_wangwu, base_where) == (1, 1, 1)

    bridge._units[NPC]["others"][1]["mood"] = 10       # 只动王五的心情
    _run(agent, "王五你怎么了")

    assert len([t for t in _ctx_texts(agent) if "—— 同席·王五：" in t]) == base_wangwu + 1, "王五该重发"
    assert len([t for t in _ctx_texts(agent) if "—— 同席·李四：" in t]) == base_lisi, "李四不该重发"
    assert len([t for t in _ctx_texts(agent) if "—— 所在：" in t]) == base_where, "所在段不该重发（名单与位置都没变）"


def test_t7b_position_change_only_resends_where():
    """★09-24 本次改动的目的★：**位置**变化只重发「所在」段 —— 自身/玩家/同席三段一律不动。

    旧形态（位置散在三段里）：玩家在地图上挪一步、或同席者动了，就要把整段重发一遍
    （自身段含性格/气运/爱好，玩家段含关系/好感/魅力，同席段含整份可观测面）。
    现在位置只有「所在」一个出口，代价降到那一小段。
    """
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四"])
    bridge._units[NPC]["others"] = _others({"id": "李四", "name": "李四"})
    _run(agent, "都在么")

    def _n(label):
        return len([t for t in _ctx_texts(agent) if f"—— {label}：" in t])

    n_self, n_player, n_other, n_where = _n("自身"), _n("玩家"), _n("同席·李四"), _n("所在")

    # 玩家挪一步（并翻成异地）+ 同席者挪一步 —— 都不碰其余字段
    bridge._units[NPC]["player"]["point"] = {"x": 99, "y": 99}
    bridge._units[NPC]["player"]["same_grid"] = False
    bridge._units[NPC]["others"][0]["point"] = {"x": 12, "y": 34}
    bridge._units[NPC]["others"][0]["same_grid"] = False
    _run(agent, "我先走一步")

    assert _n("所在") == n_where + 1, "位置变了，「所在」必须重发"
    assert _n("自身") == n_self, "位置变化连累了自身段（位置已搬走，不该重发）"
    assert _n("玩家") == n_player, "位置变化连累了玩家段（位置已搬走，不该重发）"
    assert _n("同席·李四") == n_other, "位置变化连累了同席段（位置已搬走，不该重发）"
    # 且新段要如实写出"异地相隔"与坐标（传音判定的唯一出口）
    last_where = [t for t in _ctx_texts(agent) if "—— 所在：" in t][-1]
    assert "异地相隔" in last_where and "坐标(99,99)" in last_where, last_where
    assert "李四（在坐标(12,34)，与你异地相隔）" in last_where, last_where


# ---------------------------------------------------------------- T8 统一命名

def test_t8_speaker_labels_projection():
    """发给模型的 user 一律带 `说话人：原文`；舞台指令与系统注入不加。

    ★ 契约：**账本里存的是原文**（peer 的归属在 `source.speaker`，不在文本里），
    前缀由本投影在发给模型时才拼 —— 与玩家消息（无 source、原文）完全同构。
    将来 Room 编排层写入同席发言时，也必须落**原文**，否则会拼出「李四：李四：…」。
    """
    loop, _ = _seed()
    agent = loop.create(NPC)
    sess = agent.session
    sess.append("user/message", create_user_message("[1年3月] 你好"), {"surfaceOp": "append"})
    sess.append("user/message", create_user_message(
        "尚可。", source={"kind": "peer", "speaker": "李四"}), {"surfaceOp": "append"})
    sess.append("user/message", create_user_message(
        "（NPC主动传音：向玩家打个招呼）", source={"kind": "initiative", "intent": "greet"}),
        {"surfaceOp": "append"})
    sess.append("user/message", create_user_message(
        "Current runtime context —— 所在：并无他人。",
        source={"kind": "plugin", "plugin": "@python-harness/system-prompt"}), {"surfaceOp": "append"})

    texts = _model_texts(agent)
    assert "玩家：[1年3月] 你好" in texts, texts        # 时间戳留在冒号之后，原文一字不改
    assert "李四：尚可。" in texts, texts
    assert "（NPC主动传音：向玩家打个招呼）" in texts, texts   # initiative 不加
    assert not any(t.startswith("玩家：（NPC主动传音") for t in texts)
    assert "Current runtime context —— 所在：并无他人。" in texts, texts
    # 账本必须仍是原文（前缀只在"发给模型的那一份"上）
    ledger_user = [e for e in sess.log if e.get("type") == "user/message"]
    assert ledger_user[1]["data"]["content"][0]["text"] == "尚可。", "账本被写进了前缀"


def test_t8c_tool_results_get_no_speaker_prefix():
    """工具结果也是 `role:user`（wire 格式使然），但**绝不能**被当成玩家的话加前缀。

    判据靠 `source.kind == "tool"`；漏了这条会给每条工具返回都拼上「玩家：」，
    模型会把查询结果读成玩家说的话 —— 一个非常隐蔽的串台。
    """
    from agent_loop.llm import llm_adapter
    loop, _ = _seed()
    agent = loop.create(NPC)
    sess = agent.session
    sess.append("user/message", create_user_message("[1年3月] 查一下李四"), {"surfaceOp": "append"})
    sess.append("tool/result", {"message": llm_adapter.create_canonical_tool_result_message(
        "c1", "李四：金丹后期，白帝城", is_error=False)}, {"surfaceOp": "append"})
    sess.append("user/message", create_user_message(
        "同席者：李四。", source={"kind": "peer", "speaker": "李四"}), {"surfaceOp": "append"})

    texts = _model_texts(agent)
    assert "玩家：[1年3月] 查一下李四" in texts, texts
    assert "李四：金丹后期，白帝城" in texts, texts                     # 工具返回原文，无前缀
    assert not any(t.startswith("玩家：李四：金丹后期") for t in texts), texts
    # peer 段里正文自带"同席者："字样时，前缀照加（前缀是结构，不猜正文）
    assert any(t == "李四：同席者：李四。" for t in texts), texts


def test_t8b_projection_does_not_touch_ledger():
    """投影只改"发给模型的那一份"：账本原文不含前缀，且**不原地改**账本对象。"""
    loop, _ = _seed()
    agent = loop.create(NPC)
    sess = agent.session
    raw_msg = create_user_message("[1年3月] 你好")
    sess.append("user/message", raw_msg, {"surfaceOp": "append"})

    before = json.dumps(sess.log, ensure_ascii=False)
    derived = sess.derive_messages()
    obj_before = json.dumps(derived, ensure_ascii=False)
    out = agent._apply_speaker_labels(derived)

    assert json.dumps(sess.log, ensure_ascii=False) == before, "投影改了账本！"
    assert json.dumps(derived, ensure_ascii=False) == obj_before, "投影原地改了 derive_messages 的返回对象！"
    assert out[0]["content"][0]["text"] == "玩家：[1年3月] 你好"
    assert derived[0]["content"][0]["text"] == "[1年3月] 你好"


# ---------------------------------------------------------------- T9 压缩材料归属

def test_t9_compress_transcript_attributes_peer_not_player():
    """压缩材料里 peer 必须记成说话人本名（且带「（同席）」第三方标记）——
    记成「玩家」会被写进永久纪要。标记的作用见 tests/test_group_history.py 的 G2。"""
    sess = Session(id=NPC, header={"id": NPC})
    sess.append("user/message", create_user_message("[1年3月] 你们怎么看"),
                {"surfaceOp": "append"})
    sess.append("user/message", create_user_message(
        "此事蹊跷。", source={"kind": "peer", "speaker": "李四"}), {"surfaceOp": "append"})
    sess.append("assistant/message",
                {"message": {"role": "assistant", "content": [{"type": "text", "text": "我也觉得。"}]}},
                {"surfaceOp": "append"})
    sess.append("user/message", create_user_message(
        "Current runtime context —— 所在：李四（与你同处一地）。",
        source={"kind": "plugin", "plugin": "@python-harness/system-prompt"}), {"surfaceOp": "append"})

    comp = Compressor(llm=None, player_name="韩立")
    body = comp._render_transcript(sess, {"npc_name": NPC, "player_name": "韩立"})

    assert "李四（同席）：此事蹊跷。" in body, body
    assert "玩家：李四：" not in body, f"peer 被记成玩家：{body}"
    assert "韩立：[1年3月] 你们怎么看" in body, body
    assert f"{NPC}：我也觉得。" in body, body
    assert "所在" not in body, "plugin 段（L1）不该进压缩材料"


# ---------------------------------------------------------------- T10 UI 投影

def test_t10_ui_history_peer_is_not_player_bubble():
    sess = Session(id=NPC, header={"id": NPC})
    sess.append("turn/start", {"turn": 1})
    sess.append("user/message", create_user_message("[1年3月] 你们怎么看"), {"surfaceOp": "append"})
    sess.append("user/message", create_user_message(
        "此事蹊跷。", source={"kind": "peer", "speaker": "李四"}), {"surfaceOp": "append"})
    sess.append("assistant/message",
                {"message": {"role": "assistant", "content": [{"type": "text", "text": "我也觉得。"}]}},
                {"surfaceOp": "append"})
    sess.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})

    items = project_ui_history(sess, 0)["items"]
    kinds = [i["kind"] for i in items]
    assert kinds == ["user", "peer", "assistant"], kinds
    user_items = [i for i in items if i["kind"] == "user"]
    assert len(user_items) == 1, f"peer 被算成玩家气泡了：{user_items}"
    peer = [i for i in items if i["kind"] == "peer"][0]
    # UI 侧拿到的是**原文 + 独立说话人字段**（气泡该显示成谁说的话，由 UI 决定怎么拼）
    assert peer["speaker"] == "李四" and peer["text"] == "此事蹊跷。"


# ---------------------------------------------------------------- T11 重建去重

def test_t11_resync_takes_newest_per_segment_and_clears_dropped():
    """重建：倒序扫描 ⇒ **每段只认最新一条**（旧实现后遇到的更旧消息会覆盖新值）。

    同时验证：基线里存在、但 surface 已找不到的段（被压缩折走）要被作废。
    """
    sess = Session(id=NPC, header={"id": NPC})
    mk = lambda txt: create_user_message(
        txt, source={"kind": "plugin", "plugin": "@python-harness/system-prompt"})
    sess.append("turn/start", {"turn": 1})
    sess.append("user/message", mk("Current runtime context —— 当前时间：1年1月"), {"surfaceOp": "append"})
    sess.append("user/message", mk("Current runtime context —— 自身：旧值"), {"surfaceOp": "append"})
    sess.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    sess.append("turn/start", {"turn": 2})
    sess.append("user/message", mk("Current runtime context —— 当前时间：1年2月"), {"surfaceOp": "append"})
    sess.append("user/message", mk("Current runtime context —— 自身：新值"), {"surfaceOp": "append"})
    sess.append("turn/end", {"turn": 2, "reason": {"kind": "completed"}})

    agent = DialogueAgent(session=sess, system_prompt=SystemPrompt.instance(), llm=EchoLlmClient())
    assert agent._last_l1.get("time") == "1年2月", agent._last_l1
    assert agent._last_l1.get("self") == "新值", agent._last_l1
    assert "—— 当前时间：1年2月" in agent._retained_ctx.get("time", "")

    # 动态段也能从历史里认回来（旧实现用静态反查表，认不出「同席·X」）
    assert SystemPrompt.ctx_segment_of_label("同席·李四") == "other:李四"


def test_t11b_resync_invalidates_segment_folded_out_of_surface():
    """被 replace 折走的段（已不在 surface）必须作废 → 下一步重发，否则模型永久失明。"""
    sess = Session(id=NPC, header={"id": NPC})
    mk = lambda txt: create_user_message(
        txt, source={"kind": "plugin", "plugin": "@python-harness/system-prompt"})
    sess.append("turn/start", {"turn": 1})
    sess.append("user/message", mk("Current runtime context —— 自身：会被折走"), {"surfaceOp": "append"})
    sess.append("user/message", create_user_message("你好"), {"surfaceOp": "append"})
    sess.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})

    agent = DialogueAgent(session=sess, system_prompt=SystemPrompt.instance(), llm=EchoLlmClient())
    assert "自身：会被折走" in agent._retained_ctx.get("self", "")

    # 模拟压缩：把前两个 surface 节点替换成一条纪要（自身段随之从 surface 消失）
    nodes = sess.surface.nodes
    sess.append("user/message", create_user_message(
        "[既往纪要] 早前聊过一些事", source={"kind": "plugin", "plugin": "compact"}),
        {"surfaceOp": {"op": "replace", "start": nodes[0], "end": nodes[-1]}})

    lost = agent._resync_runtime_ctx_baselines()
    assert "self" in lost, f"被折走的段没作废：{lost} / retained={agent._retained_ctx}"
    assert agent._retained_ctx.get("self") is None


# ---------------------------------------------------------------- T12 回复剥前缀

def test_t12_collect_reply_strips_self_name_prefix():
    from agent_loop.transport.ws_channel import ChatHub
    assert ChatHub._strip_self_name_prefix("张三：你好啊", "张三") == "你好啊"
    assert ChatHub._strip_self_name_prefix("张三: 你好", "张三") == "你好"
    assert ChatHub._strip_self_name_prefix("  张三： 你好", "张三") == "你好"
    assert ChatHub._strip_self_name_prefix("张三说：你好", "张三") == "张三说：你好"   # 无冒号紧随 → 不动
    assert ChatHub._strip_self_name_prefix("你好", "张三") == "你好"
    assert ChatHub._strip_self_name_prefix("张三：你好", "") == "张三：你好"          # 无名字判据 → 不剥


def test_t12b_collect_reply_end_to_end():
    loop, _ = _seed()
    agent = loop.create(NPC)
    agent.session.append("assistant/message", {
        "message": {"role": "assistant", "content": [{"type": "text", "text": f"{NPC}：道友安好。"}]}
    }, {"surfaceOp": "append"})
    from agent_loop.transport.ws_channel import ChatHub
    assert ChatHub._collect_reply(agent) == "道友安好。"


# ---------------------------------------------------------------- 兜底：桥失败不清段

def test_bridge_failure_does_not_wipe_segments():
    """L1 取数失败**不能**被误判成"所有人都离场了" → 状态与屉都该原样保留。"""
    loop, bridge = _seed()
    agent = loop.create(NPC)
    _run(agent, "第一问")
    before = dict(agent._last_l1)
    assert before, "前提：首轮应写入若干段"

    def boom(_npc_id, roster=None, **kw):
        raise RuntimeError("桥炸了")

    bridge.get_context = boom
    _run(agent, "第二问")

    assert dict(agent._last_l1) == before, f"桥失败把 L1 状态抹了：{before} → {agent._last_l1}"
    ctx_names = {c["name"] for c in agent.system_prompt.assemble(scope=NPC)["contexts"]}
    assert "runtime:self" in ctx_names, f"桥失败把屉清了：{ctx_names}"


# ------------------------------------------------- T13 名单上行（C# 契约的前提）
#
# ★ 这两条守的是 **C# 侧能不能工作**：C# 要产出 `raw.others[]`，必须知道"该看谁"，
#   而名单的真相在 Python（将来的 `Room.members`）。不发名单 = C# 无从下手。

def test_t13_roster_is_passed_to_bridge():
    """名单必须**随 get_context 上行**（单聊上行空名单，不是不发）。

    ★ 09-17 加钉：**身份与名单同一次上行**（`roster_ids`），并且它按过滤后的名单裁过。
    理由：C# 没有 id 时只能全图按名扫，重名命中第一个、回填的 `name` 又与查询串逐字相同
    ⇒ Python 与模型**无法察觉**拿到的是另一个人的档案。
    """
    loop, bridge = _seed()
    agent = loop.create(NPC)
    seen = []
    orig = bridge.get_context

    def spy(npc_id, roster=None, **kw):
        seen.append((npc_id, None if roster is None else list(roster), dict(kw)))
        return orig(npc_id, roster=roster, **kw)

    bridge.get_context = spy
    agent.set_roster(["李四", "王五"], {"李四": "LiSi01", "王五": "Wang05", "赵六": "Zhao06"})
    _run(agent, "诸位在吗")
    assert seen, "前提：应当发生取数"
    assert seen[-1][0] == NPC, seen
    assert seen[-1][1] == ["李四", "王五"], f"roster 没上行：{seen}"

    # ★ 身份：与名单**同一次**上行，且**裁到名单里的人**（"赵六"不在名单里，不许留下）——
    #   名单与身份一旦不同集合，就会出现"给 A 的取数挂了 B 的档案"这种静默错人。
    ids = seen[-1][2].get("roster_ids")
    assert ids == {"李四": "LiSi01", "王五": "Wang05"}, f"roster_ids 不对：{ids}"

    # 单聊：空名单也要上行 —— C# 据此知道不必产出 others。
    # （WS 层对空名单**不发这个键**，好让抓包里"单聊/群聊"一眼可分，见 WsGameBridge。）
    agent.set_roster([])
    _run(agent, "单独问你一句")
    assert seen[-1][1] == [], f"单聊应上行空名单：{seen}"
    assert not seen[-1][2].get("roster_ids"), "名单空了，身份也必须跟着清空"


def test_t13c_roster_ids_cannot_drift_from_roster():
    """名单与身份**只能同一次写**（`set_roster(names, unit_ids)` 单入口）。

    分开写就会漂移成"名单是新的、身份是上一轮的"—— 那一回合的取数挂到**别人**身上，
    而名字看着完全正常（比全空更糟）。这里直接对字段本身下断言。
    """
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四", "王五"], {"李四": "LiSi01", "王五": "Wang05"})
    assert agent.roster == ["李四", "王五"] and agent.roster_ids == {"李四": "LiSi01", "王五": "Wang05"}
    # 换一批人但**不传身份** ⇒ 身份必须整份清空，绝不残留上一批的
    agent.set_roster(["王五"])
    assert agent.roster == ["王五"], agent.roster
    assert agent.roster_ids == {}, f"换名单后身份残留 = 静默错人：{agent.roster_ids}"
    # 名单为空（单聊）同理
    agent.set_roster(["李四"], {"李四": "LiSi01"})
    agent.set_roster([])
    assert agent.roster_ids == {}


def test_t13b_ws_bridge_sends_roster_only_when_nonempty():
    """WS 层：roster 非空才出现在 params 里（老 DLL 收到未知键会忽略，但没必要发）。"""
    import asyncio as _aio

    from agent_loop.transport.bridge import WsGameBridge

    class FakeWs:
        def __init__(self):
            self.sent = []

        async def request(self, method, params, timeout=None):
            self.sent.append((method, dict(params)))
            return {"success": True, "data": {"raw": {"npc_id": params.get("npc_id")}, "text": ""}}

    ws = FakeWs()
    b = WsGameBridge(ws)
    _aio.run(b.get_context("林婉清"))
    _aio.run(b.get_context("林婉清", roster=["李四"]))
    assert ws.sent[0][1] == {"npc_id": "林婉清"}, ws.sent[0]
    assert ws.sent[1][1] == {"npc_id": "林婉清", "roster": ["李四"]}, ws.sent[1]


# ------------------------------------------------- T14 「人已经不在了」以现实为准
#
# ★ `resolved:false` 是 C# 唯一能说"这个人其实不在了"的通道（死了/离场/换存档）。
#   不认它，「在场」段就会对模型宣称一个不存在的人还在场 —— 而「在场」段是**权威声明**。

def test_t14_resolved_false_drops_from_present():
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["李四", "王五"])
    bridge._units[NPC]["others"] = _others(
        {"id": "李四", "name": "李四"},
        {"id": "王五", "name": "王五", "resolved": False},
    )
    _run(agent, "都在么")

    where = [t for t in _ctx_texts(agent) if "—— 所在：" in t][0]
    assert "李四" in where, where
    assert "王五" not in where, f"已不在世界上的王五仍被宣称在场：{where}"
    assert "共 3 人" in where, where              # 你 + 玩家 + 李四
    # 不在名单里 ⇒ 连段都不该长出来
    assert "other:王五" not in agent._last_l1, agent._last_l1
    assert "同席·王五" not in _ctx_labels(agent), _ctx_labels(agent)


def test_t14b_missing_resolved_is_unknown_not_dead():
    """严格判据：`resolved` **缺失**（老 C# 根本不发）≠ 人没了 —— 不能误剔除。

    这是"未知"与"否"的分界：判据写成 falsy（`if not o.get("resolved")`）的话，
    所有老 DLL 的在场者会在一夜之间全部消失，而且**不报任何错**。
    """
    loop, bridge = _seed()
    agent = loop.create(NPC)
    agent.set_roster(["王五"])
    bridge._units[NPC]["others"] = _others({"id": "王五", "name": "王五"})   # 无 resolved 键
    _run(agent, "王五在么")

    _all = _ctx_texts(agent)
    where_l = [t for t in _all if "—— 所在：" in t]
    assert where_l, f"没有所在段：{_all}"
    where = where_l[0]
    assert "王五" in where, f"字段缺失被误判成死亡：{where} / 全部段={_all}"
    assert "other:王五" in agent._last_l1, agent._last_l1


# ------------------------------------------------- T15 道号（公开称谓 + 占位串过滤）

def test_t15_title_rendered_and_placeholder_filtered():
    """道号是**公开称谓**（旁人就这么叫他），要写；但 `[待真机]` 诊断占位串绝不能进上下文。"""
    sp = SystemPrompt()
    assert "道号「青霜」" in sp.format_other_segment({"name": "李四", "title": "青霜"})
    assert "道号" not in sp.format_other_segment({"name": "李四", "title": "[待真机]道号名(id=7)"})
    # 占位串不得被当成"实质信息"把空段救活（否则模型会读到一句开发者诊断）
    assert sp.format_other_segment({"name": "李四", "title": "[待真机]道号名(id=7)"}) == ""
    # 自身段与同席段**共用 `_title_phrase`**：同一份 raw，两段都不该漏出占位串
    segs = sp.format_l1_context("林婉清", {"title": "[待真机]道号名(id=7)", "realm": "金丹"})
    assert "[待真机]" not in segs["self"], segs["self"]


# ------------------------------------------------- T16 性格（与自身段同口径）

def test_t16_personality_rendered_with_annotation():
    """性格（含注解）进同席段，且与**自身段逐字同口径**（共用 `_personality_phrase`）。"""
    sp = SystemPrompt()
    per = {"inner": "仁善", "inner_desc": "与人为善，见不得他人受苦",
           "outer": ["正直"], "outer_desc": ["刚正不阿"]}
    other = sp.format_other_segment({"name": "李四", "realm": "金丹", "personality": per})
    assert "本性仁善（与人为善，见不得他人受苦）" in other, other
    assert "行事偏正直（刚正不阿）" in other, other

    # 同口径：抽出来的短语在自身段与同席段**逐字相同**（将来改一处不会漏另一处）
    phrase = sp._personality_phrase(per)
    assert phrase, "前提：这份性格应当成文"
    self_segs = sp.format_l1_context("李四", {"realm": "金丹", "personality": per})
    assert phrase in self_segs["self"], (phrase, self_segs["self"])
    assert phrase in other, (phrase, other)

    # 只有性格也算"实质信息"，不能因 bits 为空被整段丢弃
    assert sp.format_other_segment({"name": "李四", "personality": per}) != ""
    # 退化形态：只有外在、无注解
    assert "行事偏正直" in sp.format_other_segment({"name": "李四", "personality": {"outer": ["正直"]}})
    assert "本性" in sp.format_other_segment({"name": "李四", "personality": {"inner": "仁善"}})
    assert sp.format_other_segment({"name": "李四", "personality": {}}) == ""


# ============================================================ T17 C#↔Python 契约对齐
#
# 背景：C# 侧**没有测试工程**（`FakePythonLink` 只在注释里），真机也跑不了。
# 于是"两端键名对不上"这类 bug 只剩一道防线：**源码级对齐检查**。
# 这个仓库已经栽过三次同类事故 —— 注释里反复写着「C# raw 有字段 ≠ 模型看得到」
# （性格注解、性别、位置各一次），所以这条测试守的是**真实发生过的**故障模式。

# `raw.others[]` 的**完整契约**：左边是 C# 发的，右边是 Python 认的，必须一模一样。
_OTHERS_CONTRACT_KEYS = {
    "id", "resolved", "name",
    "sex", "race", "realm", "sect", "age", "title",
    "beauty_label", "reputation_label", "personality",
    "mood", "point", "relation", "intim", "same_grid",
}


def _csharp_others_keys():
    """从 `GameContext.BuildOthers` 正文里刮出它写进 `others[]` 的键名。

    只认两种形态：`entry["k"] = …` 与 entry 的初始化行 `var entry = new JObject { ["id"] = … }`。
    不能裸刮 `["k"] =` —— 那样会把嵌套的 `point` 对象（`["x"]/["y"]`）也算进来。
    """
    import re
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "csharp" / "game" / "GameContext.cs"
    if not p.is_file():
        return None
    src = p.read_text(encoding="utf-8")
    m = re.search(r"private static JArray BuildOthers\(.*?\n        \}", src, re.S)
    if not m:
        return None
    body = m.group(0)
    keys = set(re.findall(r'entry\["([A-Za-z_]+)"\]\s*=', body))
    init = re.search(r'var entry = new JObject \{\s*\["([A-Za-z_]+)"\]', body)
    if init:
        keys.add(init.group(1))
    return keys


def test_t17a_csharp_others_keys_match_python_contract():
    """C# `BuildOthers` 写出的键集合，必须与 Python 消费的契约**完全相等**。

    抓两类漂移：① 键名拼错（`beauty` vs `beauty_label`）—— 静默，模型只是"看不到"；
                ② 一边加了一边没加（C# 白传 / Python 空读）。
    """
    import pytest

    keys = _csharp_others_keys()
    if keys is None:
        pytest.skip("csharp/game/GameContext.cs 不在（发行版剥离源码的环境）")

    missing = _OTHERS_CONTRACT_KEYS - keys
    extra = keys - _OTHERS_CONTRACT_KEYS
    assert not missing, f"C# BuildOthers 少发了这些键，Python 会读空：{sorted(missing)}"
    assert not extra, f"C# 多发了这些键，Python 不认（白传）：{sorted(extra)}"


def test_t17b_every_contract_field_reaches_the_model():
    """契约里每个字段都要**真的成文**（不是收了就丢）—— 这才是最终目的。"""
    sp = SystemPrompt()
    full = {
        "id": "李四", "name": "李四", "resolved": True,
        "sex": "女", "race": "魔族", "realm": "金丹", "sect": "白帝城", "age": 34,
        "title": "青霜", "beauty_label": "出众", "reputation_label": "名震一时",
        "personality": {"inner": "仁善", "inner_desc": "与人为善"},
        "mood": 90, "point": {"x": 31, "y": 78},
        "relation": "好友", "intim": 87, "same_grid": True,
    }
    seg = sp.format_other_segment(full)
    for expect in ("李四", "一位女子", "身为魔族", "金丹修为", "隶属白帝城一脉",
                   "年约34岁", "道号「青霜」", "容貌出众", "在世人眼中名震一时",
                   "本性仁善（与人为善）",
                   "结着好友之谊", "日渐亲近"):
        assert expect in seg, f"{expect!r} 没成文：{seg}"
    # 位置与同格**不在同席段**（09-24 搬进「所在」段）；契约字段照旧必须成文，只是换了出处
    assert "坐标" not in seg and "同处一地" not in seg, seg
    where = sp.format_l1_context(NPC, {"player": {"name": PLAYER, "point": {"x": 31, "y": 78}},
                                       "others": [full]}, roster=["李四"])["where"]
    assert "坐标(31,78)" in where and "同处一地" in where, where
    # `id` / `resolved` 是**机器字段**（段名来源、名单过滤），不该漏进正文当台词
    assert "resolved" not in seg and "True" not in seg, seg
