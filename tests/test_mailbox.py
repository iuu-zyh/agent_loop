"""信箱（捎话，`social_relation op=chuan_hua`）—— 全流程契约 + 端到端模拟。

## 用户要的是什么（11-12）

「a 调用这个工具，就是给 b 传一句话，但是不是让 b 去立即互动……只有当玩家和 b 对话的时候
才会触发，就是每个 npc 对应的 agent 新增一个信箱」，实现方式「直接往别的 npc 的账本里面
去追加」，账本不存在时「完全可以懒创建啊，和用户和新的 npc 对话时候才创建账本一样」。

## 这个文件守的是**四件会静默出错的事**

1. **投递必须真进 B 的账本**，且**不能当场叫醒 B**（`inbox.next-turn`，不是 `next-step`、
   不是主动开口回合）。投错筐的症状是"B 当场回应了"或"永远读不到"，两者都不报错。
2. **`source.kind="mail"` 必须在四个消费方都认识** —— 少一处就是一句错记忆：
   `_apply_speaker_labels`（当成玩家说的 → 对着玩家应下别人的托付）、
   `session/history.py`（落进 `kind:user` 兜底 → 屏幕上变成玩家自己发的话）、
   `compaction/compress.py` + `prompts/compaction/default.md`（记成「玩家：…」，
   而纪要是永久节点，越传越歪）。这四处**逐个断言**，因为它们谁都不会报错。
3. **失败绝不静默成功**：投不进去（没接线 / 收信人取不到 / 超上限）必须把结果改判成
   `success:false`，否则模型读到的是一句"已捎到"，下一句就编"他答应了"。
4. **C# 与 Python 的键、上限、分工成对**：C# 只解析收信人 + 带回台词，Python 负责落到账本；
   `MailMaxChars` 两处同值；`chuan_hua` 在枚举里、在 C# 的 switch 里有分支。

## 端到端模拟（`test_full_flow_*`）

真的跑两条回合：A 调用工具 → 断言话进了 B 的 inbox 且 B 账本**没有**新回合 →
玩家与 B 说话 → 断言 B 的模型**读到了**那句话且归属正确。中间不 mock 投递路径。
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.transport.ws_channel import WsServer, ChatHub
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
ROOT_TMP = os.path.join(tempfile.gettempdir(), "agent_loop_mailbox")
A = "张三_捎话"
B = "李四_收信"
PLAYER = "韩立"


def _reset(bridge=None, llm=None):
    if os.path.isdir(ROOT_TMP):
        shutil.rmtree(ROOT_TMP, ignore_errors=True)
    AgentLoop.reset_for_test(storage_root=ROOT_TMP, bridge=bridge, llm=llm)


def _seed(bridge: StubGameBridge, name: str):
    bridge.seed_unit(name, {
        "realm": "元婴", "sect": "七星阁", "pos": "永宁州", "mood": 80, "power": 22000,
        "player": {"name": PLAYER, "realm": "筑基", "relation": "道友", "intim": 60,
                   "same_grid": True},
        "recent": "上月收下了一株青须藤",
    })


def _texts_of(msg) -> str:
    return "".join(b.get("text", "") for b in (msg.get("content") or [])
                   if isinstance(b, dict) and b.get("type") == "text")


def _joined(req) -> str:
    return "|".join(
        b.get("text", "")
        for m in req.get("messages", [])
        for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "text"
    )



def _mk(text: str, frm: str = A) -> dict:
    """投递用的消息体（真实路径里由 `DialogueAgent._settle_mail` 造，见那里的 `_mk`）。"""
    from agent_loop.agent import mailbox
    return {"role": "user", "id": f"mail-{frm}-{text[:6]}",
            "content": [{"type": "text", "text": text}],
            "source": {"kind": mailbox.MAIL_KIND, "from": frm}}


def _schemas():
    return importlib.import_module(f"{ROOT.name}.tools.schemas")


# ============================================================ A. 契约（跨语言 + 消费方）

def test_op_is_in_the_enum_and_has_a_message_argument():
    """`chuan_hua` 必须在枚举里，且 `message` 参数存在（否则模型没法表达"捎什么"）。"""
    S = _schemas()
    props = S.SOCIAL_RELATION["function"]["parameters"]["properties"]
    assert "chuan_hua" in props["op"]["enum"], "捎话不在 op 枚举里 —— 模型填不出来"
    assert "message" in props, "没有 message 参数 —— 模型只能把话塞进 ask_text（语义全错）"
    # 仍然只有 10 个顶层工具（用户 11-05 定的规矩：新能力走子分支，不许加第 11 个大类）
    assert len(S.TOOL_ORDER) == 10, f"顶层工具变成 {len(S.TOOL_ORDER)} 个了：{S.TOOL_ORDER}"


def test_csharp_handles_the_op_and_has_matching_limit():
    """C# 必须有 `chuan_hua` 分支，且台词上限与 Python 同值。

    跨语言配对最容易错的方式是"只改一边"：C# 没有 case ⇒ 落到 `default: Fail(unsupported…)`；
    上限不同 ⇒ C# 截到 200、Python 以为 300。两者都**不报错**，只是行为悄悄不同。
    """
    from agent_loop.agent import mailbox

    src = _cs_tools.tool_src()
    assert 'case "chuan_hua"' in src, "C# 的 SocialRelation 没有 chuan_hua 分支 → 会走 default 报 unsupported"
    assert "MailMaxChars" in src, "C# 没有 MailMaxChars 常量"
    m = re.search(r"MailMaxChars\s*=\s*(\d+)", src)
    assert m, "MailMaxChars 没有字面量初值"
    assert int(m.group(1)) == mailbox.MAIL_MAX_CHARS, (
        f"C# 上限 {m.group(1)} ≠ Python 的 {mailbox.MAIL_MAX_CHARS} —— 长台词会在一边被截、另一边不截")
    # 分工：C# 只带回台词，不落刀（账本是 Python 的物件）
    body = src[src.index('case "chuan_hua"'):]
    body = body[:body.index("default:")]
    assert '"message"' in body, "C# 分支没把台词带回给 Python"


def test_mail_op_passes_the_battle_gate():
    """捎话**不进战斗队列**：它不碰游戏世界，排队只会把话拖到战后再投。

    `BattleFlowCore.Grade` 把整个 `social_relation` 判成 `Queue`；不加例外的话，
    `ToolExecutor.Execute` 会先返回 `{queued:true}` —— Python 侧拿不到台词，
    这句话要等"此间事了"才可能补投，而链断就彻底丢了。
    """
    src = (ROOT / "csharp" / "state" / "BattleFlowCore.cs").read_text(encoding="utf-8")
    grade = src[src.index("public static ToolVerdict Grade("):]
    grade = grade[:grade.index("return ToolVerdict.Pass;\n        }")]
    assert 'op == "chuan_hua"' in grade, (
        "Grade 没有 chuan_hua 例外 —— 捎话会被排进战斗队列（话被拖后，甚至丢掉）")
    # 例外必须排在"整个 social_relation 一律 Queue"那句之前，否则永远命中不到（死分支）
    queue_line = '|| toolName == "social_relation")'
    assert queue_line in grade, "找不到兜底那句『social_relation 一律 Queue』—— 结构变了，本断言要跟着改"
    assert grade.index('op == "chuan_hua"') < grade.index(queue_line), \
        "例外写在兜底之后了，那是个死分支"


def test_source_kind_is_known_by_every_consumer():
    """`mail` 这个 kind 必须被四个消费方各自认识。

    这四处**都不会报错**，只是各自安静地做错一件事 —— 所以逐处断言。
    """
    from agent_loop.agent import mailbox

    kind = mailbox.MAIL_KIND
    assert kind == "mail"

    # ① 发前投影：不认识就只能落进 else（不加前缀）⇒ 模型当成玩家说的
    da = (ROOT / "agent" / "dialogue_agent.py").read_text(encoding="utf-8")
    label = da[da.index("def _apply_speaker_labels"):]
    label = label[:label.index("def _inline_image_refs")]
    assert 'kind == "mail"' in label, "`_apply_speaker_labels` 不认识 mail —— 会当成玩家说的话"
    assert "托人捎来" in label, "mail 的前缀没写明'托人捎来'（与同席的『说话人：』分不开）"

    # ② UI 投影：不认识就落进 `kind:user` 兜底 ⇒ 屏幕上变成玩家自己发的一句话
    #    这里**真跑一遍投影**而不是 grep：`kind` 的取值是 C# 那个 switch 认不认它的唯一依据。
    from agent_loop.session.history import project_ui_history
    from agent_loop.session.session import Session
    s = Session("npc-x")
    s.append("turn/start", {"turn": 1, "scene": ""}, {"surfaceOp": "append"})
    s.append("user/message", {"role": "user", "id": "m1",
                              "content": [{"type": "text", "text": "改日来寒舍一叙"}],
                              "source": {"kind": kind, "from": A, "from_id": "id-a"}},
             {"surfaceOp": "append"})
    s.append("turn/end", {"turn": 1}, {"surfaceOp": "append"})
    items = project_ui_history(s)["items"]
    assert len(items) == 1, f"投影出了 {len(items)} 条（mail 应当恰好一条）"
    it = items[0]
    assert it["kind"] == "peer", (
        f"mail 投影成了 kind={it['kind']!r} —— 若是 'user'，屏幕上会变成**玩家自己**说的话")
    assert it["speaker"] == A, f"没带上说话人（头像与归属都会错）：{it}"
    assert it.get("mail") is True, "没打 mail 标记（将来要区分『捎来的』与『当场的』就没依据了）"
    # `peer` 是 C# 回放 switch 里**已有**的分支 ⇒ 不必为一个新 kind 再加一条（switch 没有 default，
    # 未知 kind 会静默什么都不画 = "群窗开了但历史一片空白"那一类故障）。
    win = (ROOT / "csharp" / "UI" / "ChatWindow.cs").read_text(encoding="utf-8")
    # ★ 11-20 起 switch 在 `ApplyHistoryItem` 里（回放改分帧构建），`ReplaceHistory` 只剩分流。
    #   别退回 `win[win.index("ReplaceHistory"):]` 那种"切到文件尾"的写法 ——
    #   它连后面几十个方法一起扫，`case "peer":` 挪到哪儿都能过（等于没钉住）。
    _anchor = "private void ApplyHistoryItem(JToken token)"
    assert _anchor in win, "找不到 ApplyHistoryItem —— 回放的 switch 又搬家了，本测试要跟着走"
    hist_src = win[win.index(_anchor):]
    hist_src = hist_src[:hist_src.index("private void BuildHistoryBatch()")]
    assert 'case "peer":' in hist_src, "C# 回放没有 peer 分支 —— mail 会静默不画"

    # ③ 压缩材料：不认识就写成「玩家：…」，而纪要是永久节点
    comp = (ROOT / "compaction" / "compress.py").read_text(encoding="utf-8")
    assert '_kind == "mail"' in comp, "`_render_transcript` 不认识 mail —— 会被记成玩家说的"
    assert "（捎来的）" in comp, "压缩标记缺失：摘要器没法把『捎来的』与『同席』分开"

    # ④ 指令文件与兜底常量必须同时带这个落点（只有标记没落点 ⇒ 摘要器自己找地方塞）
    md = (ROOT / "prompts" / "compaction" / "default.md").read_text(encoding="utf-8")
    assert "（捎来的）" in md, "default.md 没有『捎来的』的归属图例"
    from agent_loop.compaction.compress import Compressor
    fb = Compressor(llm=None, prompts_root=ROOT / "prompts" / "__no_such_dir__")._load_instruction("__npc__")
    assert "（捎来的）" in fb, "compress 的兜底常量没跟上 default.md"


def test_usage_routes_the_op():
    """`tool_usage.txt` 必须点名路由 —— 否则模型只知道 op 存在，不知道何时该伸手。"""
    txt = (ROOT / "prompts" / "sections" / "tool_usage.txt").read_text(encoding="utf-8")
    line = next((ln for ln in txt.splitlines() if "social_relation" in ln), "")
    assert "捎话" in line, "路由行没提捎话"
    assert "当次无回应" in line or "才读到" in line, (
        "没写『当次没有回应』—— 模型会在工具返回后就写出对方听了的反应")


def test_render_says_no_reply_yet():
    """工具返回的文案必须同时说清：**送到了** + **他此刻不知情**。

    只说"已捎到"，模型会顺手写出"他听了点头应下"—— 那是对方**根本没有过**的反应。
    """
    from agent_loop.tools import text_render as tr
    out = tr.render("social_relation", {"op": "chuan_hua", "target": A},
                    {"success": True, "data": {"op": "chuan_hua", "target": A,
                                               "counterparty": B, "message": "改日来寒舍一叙",
                                               "delivered": True}})
    assert out and B in out, f"文案里没有收信人：{out!r}"
    assert "信箱" in out, f"没说送到哪里：{out!r}"
    assert "不知情" in out or "才读到" in out, f"没点明他此刻不知道：{out!r}"


# ============================================================ B. 投递语义

def test_deliver_goes_to_next_turn_and_does_not_wake():
    """投递只进 `next-turn`，**不**进 `next-step`、**不**开回合。

    `next-step` 会被**当前**回合的下一步领走 ⇒ B 会"当场回应"（正是用户不要的）。
    `next-turn` 在本回合第 1 步才被领，而本回合根本没开 ⇒ 一直躺在筐里等玩家。
    """
    bridge = StubGameBridge()
    _seed(bridge, B)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "嗯", "tool_calls": []}))
    loop = AgentLoop()
    from agent_loop.agent import mailbox

    b_agent = loop.create(B)
    err = mailbox.deliver(loop, _mk, from_id=A, to_id=B, text="改日来寒舍一叙")
    assert err is None, f"投递失败：{err}"
    assert len(b_agent.inbox.next_turn) == 1, "没进 next-turn"
    assert not b_agent.inbox.next_step, "进了 next-step ⇒ B 会在当前回合就回应"
    assert not [ev for ev in b_agent.session.log if ev.get("type") == "turn/start"], \
        "投递时开了回合 ⇒ B 立刻就互动了（用户明确不要）"
    msg = b_agent.inbox.next_turn[0]
    assert _texts_of(msg) == "改日来寒舍一叙"
    assert msg["source"]["kind"] == "mail"
    assert msg["source"]["from"] == A, "没记发信人 → B 不知道该谢谁"


def test_deliver_lazily_creates_the_ledger():
    """B 的账本不存在时**懒创建**（用户明确要求，与"和新 NPC 对话时创建"同一条路）。"""
    bridge = StubGameBridge()
    _seed(bridge, B)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "嗯", "tool_calls": []}))
    loop = AgentLoop()
    from agent_loop.agent import mailbox

    assert loop.get(B) is None and loop.get_any(B) is None, "前提错了：B 的账本已经在内存里"
    err = mailbox.deliver(loop, _mk, from_id=A, to_id=B, text="替我问候令堂")
    assert err is None, f"投递失败：{err}"
    b_agent = loop.get(B)
    assert b_agent is not None, "没懒创建 B 的账本"
    assert len(b_agent.inbox.next_turn) == 1, "建了账本却没把话放进去"


def test_deliver_refuses_self_and_empty():
    """自捎 / 空话必须在 Python 侧也拦住（C# 拦的是同一批，但 stub 与直调路径不走 C#）。"""
    bridge = StubGameBridge()
    _seed(bridge, B)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "嗯", "tool_calls": []}))
    loop = AgentLoop()
    from agent_loop.agent import mailbox

    assert mailbox.deliver(loop, _mk, from_id=B, to_id=B, text="喂") is not None, "自捎没被拦"
    assert mailbox.deliver(loop, _mk, from_id=A, to_id=B, text="   ") is not None, "空话没被拦"


def test_deliver_caps_pending_per_sender():
    """同一个发信人对同一个人、未读的太多就不再收 —— 防模型一口气连发十条刷屏。"""
    bridge = StubGameBridge()
    _seed(bridge, B)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "嗯", "tool_calls": []}))
    loop = AgentLoop()
    from agent_loop.agent import mailbox

    for i in range(mailbox.MAIL_MAX_PENDING):
        assert mailbox.deliver(loop, _mk, from_id=A, to_id=B, text=f"第{i}句") is None
    over = mailbox.deliver(loop, _mk, from_id=A, to_id=B, text="再来一句")
    assert over is not None, "超出上限仍然投进去了（B 要好几轮对话才读得完）"
    assert str(mailbox.MAIL_MAX_PENDING) in over, f"错误文案没说清上限：{over!r}"


def test_mail_survives_dispose_and_disk():
    """关窗（dispose=冻结）与落盘/读档后，未读的话必须还在。

    这是"往账本里追加"相对"进程内待办表"的全部意义：后者一重启就静默丢失，
    而 B 会表现得像从没收到过，**且没有任何报错**。
    """
    bridge = StubGameBridge()
    _seed(bridge, B)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "嗯", "tool_calls": []}))
    loop = AgentLoop()
    from agent_loop.agent import mailbox

    assert mailbox.deliver(loop, _mk, from_id=A, to_id=B, text="存档前捎的") is None
    loop.dispose(B)                       # 关窗 → 冻结
    loop.flush_all()                      # 存档
    AgentLoop.reset_for_test(storage_root=ROOT_TMP, bridge=bridge,
                             llm=StubLlmClient(fn=lambda req: {"text": "嗯", "tool_calls": []}))
    loop2 = AgentLoop()
    again = loop2.resume_if_on_disk(B)
    assert again is not None, "读档没取回 B 的账本"
    pend = mailbox.pending_from(again, A)
    assert len(pend) == 1, f"读档后未读的话没了（{len(pend)} 条）—— 这正是待办表方案会出的错"
    assert _texts_of(pend[0]) == "存档前捎的"


# ============================================================ C. 端到端模拟（真跑两条回合）

def test_full_flow_a_sends_then_b_reads_it_on_his_own_turn():
    """A 调工具捎话 → 话进 B 的信箱 → 玩家与 B 说话 → B **读到**且归属正确。"""
    bridge = StubGameBridge()
    _seed(bridge, A)
    _seed(bridge, B)
    calls = {"a": 0, "b": 0}
    seen_b = {}

    def _llm_fn(req):
        joined = _joined(req)
        # A 的第一次：调 chuan_hua；之后收口
        if A in joined and calls["a"] == 0:
            calls["a"] += 1
            return {"text": "", "tool_calls": [{
                "id": "c1",
                "name": "social_relation",
                "arguments": {"op": "chuan_hua", "target": B,
                              "message": "改日来寒舍一叙"},
            }]}
        if A in joined and B in joined:
            # B 的回合（L1 里会同时出现两个人）：记下它看到了什么
            seen_b["joined"] = joined
        return {"text": "好。", "tool_calls": []}

    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))

    async def scenario():
        # ① A 的回合：模型调 chuan_hua
        await hub.handle_message(A, "替我带句话给李四：改日来寒舍一叙")
        b_agent = loop.get_any(B)
        assert b_agent is not None, "A 调完之后 B 的账本还不存在 —— 投递没走通"
        assert len(b_agent.inbox.next_turn) == 1, "话没进 B 的信箱"
        assert not [ev for ev in b_agent.session.log if ev.get("type") == "turn/start"], \
            "A 捎话时 B 开了回合 —— 用户明确要求'不会立即互动'"

        # ② 玩家与 B 说话：这一刻 B 才读到
        await hub.handle_message(B, "我回来了")

    asyncio.run(scenario())

    b_agent = loop.get(B)
    mail_ev = [ev for ev in b_agent.session.log
               if ev.get("type") == "user/message"
               and (ev.get("data", {}).get("source") or {}).get("kind") == "mail"]
    assert mail_ev, "B 的账本里没有这条 mail 事件（读不到 = 功能没生效）"
    assert mail_ev[0]["data"]["source"]["from"] == A
    # ★ 归属：B 的模型看到的必须是「【张三托人捎来】…」，不是「玩家：…」
    assert "【张三_捎话托人捎来】" in (seen_b.get("joined") or ""), (
        f"B 没看到带归属的捎话前缀 —— 会被当成玩家说的。实际上下文片段："
        f"{(seen_b.get('joined') or '')[:400]}")


def test_full_flow_failed_delivery_is_reported_as_error():
    """投不进去时工具结果必须是失败 —— 模型不能被告知"已捎到"。"""
    bridge = StubGameBridge()
    _seed(bridge, A)
    _reset(bridge=bridge, llm=StubLlmClient(fn=lambda req: {"text": "好。", "tool_calls": []}))
    loop = AgentLoop()
    agent = loop.create(A)
    # 模拟 C# 回了成功、但收信人名字是空的（旧 DLL / 回包残缺）
    res = {"success": True, "data": {"op": "chuan_hua", "counterparty": "", "message": "喂"}}
    agent._settle_mail("social_relation", {"op": "chuan_hua"}, res)
    assert res["success"] is False, "残缺回包被当成投递成功了"
    assert res.get("error"), "没给模型任何错误说明"

    # 没接线（裸 agent）也必须明确失败，而不是静默什么都不做
    agent.loop = None
    res2 = {"success": True, "data": {"op": "chuan_hua", "counterparty": B, "message": "喂"}}
    agent._settle_mail("social_relation", {"op": "chuan_hua"}, res2)
    assert res2["success"] is False and res2.get("error"), "没接线时静默成功了"


# ============================================================ D. Inbox 领取语义（11-12 实测出来的）

def _imk(txt, kind=None, nid=None):
    n = _imk.i = getattr(_imk, "i", 0) + 1
    m = {"role": "user", "id": nid or f"im{n}", "content": [{"type": "text", "text": txt}]}
    if kind:
        m["source"] = {"kind": kind, "from": A}
    return m


def _fill(pairs):
    from agent_loop.session.inbox import Inbox
    from agent_loop.session.session import Session
    ib = Inbox(Session("t"))
    ib.splice("next-turn", 0, 0, [_imk(t, k) for t, k in pairs])
    return ib


def _pairs(msgs):
    return [(m["content"][0]["text"], (m.get("source") or {}).get("kind")) for m in msgs]


def test_mail_alone_never_starts_a_turn():
    """**只有捎话的筐不许撑起一个回合** —— 用户要的"不会立即互动"就落在这一条上。

    这是本功能最容易写错的地方，而且**不报错**：`has_pending` 若把捎话算进去，
    `run_until_idle` 会起一个回合，B 在没有玩家任何输入的情况下自说自话一句
    （实测过：回合数 2、首回合里没有玩家的话），然后下一回合才回应玩家。
    """
    ib = _fill([("捎的", "mail")])
    assert ib.has_pending is False, (
        "只有捎话时 has_pending 为 True ⇒ B 会凭空开一个回合自说自话（= 立即互动）")
    assert ib.claim("next-turn", 1) == [], "只有捎话时不该领出任何东西"
    assert len(ib.next_turn) == 1, "捎话被领走了却没起回合 —— 它应该原地留着等玩家"


def test_mail_rides_along_with_the_players_line():
    """捎话必须与玩家那条**同回合**读到，且两种先后顺序都要成立。

    [捎话, 玩家] 若只领一条 ⇒ 本回合没有玩家的话（B 对着空气说一句）；
    [玩家, 捎话] 若只领一条 ⇒ 捎话得等下一回合，同一句对话被拆成两次读。
    """
    ib = _fill([("捎的", "mail"), ("玩家的", None)])
    got = _pairs(ib.claim("next-turn", 1))
    assert got == [("捎的", "mail"), ("玩家的", None)], f"顺序/内容不对：{got}"
    assert ib.next_turn == []

    ib = _fill([("玩家的", None), ("捎的", "mail")])
    got = _pairs(ib.claim("next-turn", 1))
    assert ("玩家的", None) in got, f"玩家的话没领到：{got}"
    # 捎话排在玩家之后：本次可以留到下一回合，但**不能丢**，且下一回合必须领到
    assert len(ib.next_turn) + len(got) == 2, "捎话被静默丢弃了"


def test_player_messages_are_still_one_per_turn():
    """玩家连发多条的老语义**不许被捎话改动**：仍是一回合一条，逐条回应。"""
    ib = _fill([("一", None), ("二", None), ("三", None)])
    assert _pairs(ib.claim("next-turn", 1)) == [("一", None)]
    assert _pairs(ib.claim("next-turn", 2)) == [("二", None)]
    assert _pairs(ib.claim("next-turn", 3)) == [("三", None)]


def test_many_mails_never_crowd_out_the_players_line():
    """堆再多捎话，玩家的话也必须在**本回合**读到（硬保证，见 claim 的 ★）。"""
    ib = _fill([(f"捎{i}", "mail") for i in range(8)] + [("玩家的", None)])
    got = _pairs(ib.claim("next-turn", 1))
    assert ("玩家的", None) in got, (
        f"玩家的话被 {len(got)} 条捎话挤掉了 —— 这一回合 B 只读到捎话，像玩家白说了")


def test_session_does_not_import_agent_layer():
    """`session/` 不许反向 import `agent/`（依赖方向只能 agent → session，否则成环）。

    `_MAIL_KIND` 与 `agent/mailbox.MAIL_KIND` 是**两份字面量**，靠这条测试保证同值。
    """
    from agent_loop.agent import mailbox
    from agent_loop.session import inbox as inbox_mod
    assert inbox_mod._MAIL_KIND == mailbox.MAIL_KIND, (
        f"两处 kind 不同值：session={inbox_mod._MAIL_KIND!r} agent={mailbox.MAIL_KIND!r}")
    src = (ROOT / "session" / "inbox.py").read_text(encoding="utf-8")
    assert "from ..agent" not in src and "import agent" not in src, \
        "session/inbox.py 反向依赖了 agent 层（会成环）"
