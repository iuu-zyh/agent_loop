"""信箱优先队列（11-13）—— 被传话的 NPC 优先自主交互。

## 用户要什么

「被传话的 npc 将进入一个优先队列，然后其会优先进行自主交互，优先队列比好感度同格之类的
优先级都要高……效果就是消息依旧是进了账本，但是对于优先队列的 npc 自主交互系统会优先激活，
然后就是要求其根据传话内容去主动交互即可」。

用户随后明确了**边界**（这一条决定了整个实现形态）：

> 「只改变排序（仍受一日一次限制），不会跳过 daily_chance」

所以本功能的正确性 = 两句话同时成立：
  ✔ 有未读捎话的人排到所有无信者之前（优先于好感/同格）；
  ✘ 但**一条闸门都没有被绕过** —— 日节拍、每日概率、单人双冷却、能说闸、忙闲照旧。

## 为什么值得单独一个文件

这三件事都是"改错了也不会报错"的形态：
  · 把优先队列写进 `daily_chance` 之前 → 变成"有信必触发"，与用户拍板相反，而且
    玩家挂机挂一夜会收到一串主动传音（体感是"这 NPC 抽风了"），却不报任何错；
  · 把加成加在 `Row.Intim` 上 → `PickIntent`/`BuildReason` 读到的就是假好感
    → 仇人走进正向意图池、话术变成"念及与你多日情谊"，**全都不报错**；
  · 优先名单只由 C# 自己维护一份 → Python 重启/读档后必然分家，表现为"明明有信却从不优先"。
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

CS = ROOT / "csharp"


def _mon() -> str:
    return (CS / "npc" / "NpcInitiativeMonitor.cs").read_text(encoding="utf-8")


def _mailbox():
    return importlib.import_module(f"{ROOT.name}.agent.mailbox")


def _initiative():
    return importlib.import_module(f"{ROOT.name}.agent.initiative")


# ============================================================ ① Python：名单是账本的派生物

def _mk_mail(text, frm):
    mb = _mailbox()
    return {"role": "user", "id": f"m-{frm}-{text[:4]}",
            "content": [{"type": "text", "text": text}],
            "source": {"kind": mb.MAIL_KIND, "from": frm}}


class _FakeAgent:
    def __init__(self, queue):
        from agent_loop.session.inbox import Inbox
        from agent_loop.session.session import Session

        self.inbox = Inbox(Session("fake"))
        if queue:
            self.inbox.splice("next-turn", 0, 0, queue)


class _FakeLoop:
    def __init__(self, store=None, frozen=None):
        self.store = store or {}
        self._frozen = frozen or {}


def test_pending_map_reports_only_people_with_unread_mail():
    """只报**真有未读**的人；读过（筐空）的必须消失 —— 否则他会永远排在前面。"""
    mb = _mailbox()
    with_mail = _FakeAgent([_mk_mail("改日一叙", "张三")])
    empty = _FakeAgent([])
    loop = _FakeLoop({"李四": with_mail, "王五": empty})
    got = mb.pending_map(loop)
    assert got == {"李四": ["张三"]}, got


def test_pending_map_covers_the_frozen_cabin_too():
    """冻结舱（关窗未固化）也必须算 —— 玩家关了窗、明天再开，那封信还在。

    只看 `store` 的话，关窗就等于"这封信不存在了"：他会从优先队列里消失，
    而账本里明明还有未读。这种不对称**不会报错**，只表现为"偶尔不优先"。
    """
    mb = _mailbox()
    frozen_agent = _FakeAgent([_mk_mail("东西备好了", "赵六")])
    loop = _FakeLoop(store={}, frozen={"孙七": frozen_agent})
    assert mb.pending_map(loop) == {"孙七": ["赵六"]}


def test_pending_map_dedupes_senders_and_keeps_order():
    """同一个发信人连捎三条 → 只算一个名字（名单是给舞台指令用的，不是计数）。"""
    mb = _mailbox()
    a = _FakeAgent([_mk_mail("一", "张三"), _mk_mail("二", "张三"), _mk_mail("三", "李四")])
    got = mb.pending_map(_FakeLoop({"王五": a}))
    assert got["王五"] == ["张三", "李四"], got


def test_priority_is_not_a_numeric_bonus_added_to_intim():
    """优先队列**不是**"给好感加一个大数字"，而是两个独立排序键。

    这条曾经断言 `MAIL_PRIORITY_BONUS >= 600` —— 而那个常量**根本没有被任何代码用过**
    （唯一读者就是这条断言本身），它描述的是"加进 `Row.Intim`"那版被否掉的设计。
    一条只为常量自身存在的测试会一直绿，却在你真把加成写进好感时照样绿。

    所以判据改成**否定式**：那个常量不该存在，而"不许动 Intim"要直接断言在 C# 上。
    为什么加成式设计是坏的：`Row.Intim` 随后被 `PickIntent`（按好感选意图池）与
    `BuildReason`（按好感选措辞）读取 —— 仇人 +1000 会走进正向池、话术变成
    "念及多日情谊"，两处都**不报错**，只是人设崩了。
    """
    mb = _mailbox()
    assert not hasattr(mb, "MAIL_PRIORITY_BONUS"), (
        "加成常量又回来了 —— 它就住在 `Row.Intim` 里，会污染 PickIntent/BuildReason。"
        "优先队列是 `InitiativeSortCore.Compare` 的两个独立键，不要改回加成式")

    # C#：比较函数只读 Intim，绝不写；且 HasMail 必须排在 Intim 之前
    core = (CS / "npc" / "InitiativeSortCore.cs").read_text(encoding="utf-8")
    body = core[core.index("public static int Compare("):]
    body = body[:body.index("\n        }")]
    for bad in ("Intim +=", "Intim =", "Intim++", "Intim--"):
        assert bad not in body, f"比较函数里改了 Intim（`{bad}`）—— 优先队列污染了好感"
    assert "a.HasMail" in body and "b.Intim.CompareTo" in body


# ============================================================ ② C#：优先队列**只改排序**

def test_sort_uses_hasmail_first_and_does_not_touch_intim():
    """`SortRows` 的比较键必须是「先看 HasMail、后看好感」，且**不修改 Intim**。"""
    # 判据已搬到 `InitiativeSortCore`（11-13）：监控器依赖 Unity，测不了；
    # 核心是纯逻辑，由 `csharp/tests/InitiativeSortCoreTests` 直接钉住（19 项断言）。
    core = (CS / "npc" / "InitiativeSortCore.cs").read_text(encoding="utf-8")
    i = core.index("public static int Compare(")
    seg = core[i:i + 700]
    assert "a.HasMail != b.HasMail" in seg, "排序没把 HasMail 放在第一判据 —— 优先队列不生效"
    assert "return b.Intim.CompareTo(a.Intim);" in seg, "第二判据（好感降序）被改动或丢了"
    assert seg.index("a.HasMail") < seg.index("b.Intim.CompareTo"), "好感排在了优先之前"
    # 绝不许把加成写进 Intim（那会污染 PickIntent / BuildReason）
    assert "Intim +=" not in seg and "Intim + " not in seg, (
        "比较函数里修改了 Intim —— PickIntent/BuildReason 读到的是假好感（仇人会进正向池）")
    # 监控器必须真的**用它**（定义在核心、没人调 = 优先队列不生效）
    mon = _mon()
    assert "InitiativeSortCore.SortOrder(" in mon, "监控器没调 InitiativeSortCore.SortOrder —— 判据成了死代码"


def test_sort_only_affects_order_and_bypasses_nothing():
    """用户拍板的边界：**只改排序**，daily_chance / 日节拍 / 冷却一条都不许绕过。

    这条防的是"顺手把优先队列做成豁免名单"——那种改动很像"更贴心地满足需求"，
    但与用户明确说的"不会跳过 daily_chance"相反，而且不报错。
    """
    mon = _mon()
    # 日概率闸必须仍在 `TryTriggerForDay` 的闸门段里，且**在**读候选之前
    i_gate = mon.index("if (_dailyChance < 100)")
    i_rel = mon.index("RelationNetwork.CollectKnownUnits(units, seen, contactIds)")
    assert i_gate < i_rel, "每日概率闸被挪到了候选构建之后 —— 优先队列可能越过它"
    # 冷却检查必须仍在 TryDispatch 的循环里
    i_disp = mon.index("private bool TryDispatch(")
    seg = mon[i_disp:i_disp + 2500]
    assert "IsCoolingDown(r.Name, curDay)" in seg, "冷却判据不见了"
    assert "if (!r.InContacts && !r.SameGrid) continue;" in seg, "资格闸不见了（非通讯录必须同格）"
    # HasMail **不许**出现在任何闸门判据里（它只能是排序键 + 意图选择）
    for gate in ("_dailyChance", "IsCoolingDown(", "!r.InContacts", "SpeechBlockReason"):
        assert "HasMail" not in gate, gate
    # 逐条确认：`HasMail` 在 C# 里只出现在 Row 定义、赋值、排序、意图选择这四处
    # 精确判据：`HasMail` **不许**出现在任何闸门函数的函数体里。
    # 用**大括号配对**取函数体（固定长度切片会把下一个函数/注释吃进来，
    # 那样测试就变成在测代码排版）。
    def _body(sig):
        i = mon.index(sig)
        j = mon.index("{", i)
        depth = 0
        for k in range(j, len(mon)):
            if mon[k] == "{":
                depth += 1
            elif mon[k] == "}":
                depth -= 1
                if depth == 0:
                    return mon[j:k + 1]
        return ""

    for fn in ("private void TryTriggerForDay(", "private bool IsCoolingDown(",
               "private static string SpeechBlockReason(", "private static string BusyReason(",
               "private bool DayRollGate("):
        if fn not in mon:
            continue
        assert "HasMail" not in _body(fn), (
            f"{fn} 的体内出现了 HasMail —— 优先队列正在豁免一条闸门"
            f"（用户明确：只改排序，不跳过 daily_chance）")

    # `TryDispatch` 是唯一**允许**出现 HasMail 的地方（它在那里只决定"派谁/说什么"），
    # 但只许出现一次，且必须在 `PickIntent` 之后（= 闸门已经全部过完）。
    disp = _body("private bool TryDispatch(")
    assert disp.count("HasMail") == 1, (
        f"TryDispatch 里 HasMail 出现 {disp.count('HasMail')} 次 —— 应当只有选 intent 那一处")
    i_mail = disp.index("HasMail")
    i_cool = disp.index("IsCoolingDown(r.Name, curDay)")
    assert i_cool < i_mail, "选 intent 排在了冷却检查之前 —— 闸门没走完就定下了意图"


def test_day_tick_refreshes_the_queue_without_blocking():
    """换日节拍必须**异步**刷新名单（`RequestMailPending`），不许同步等。

    日节拍在主线程跑（换日是玩家推进剧情的那一刻），同步等 RPC 会卡顿。
    用旧名单的后果只是"某人这次没排到最前"，与没有本功能等价 —— 可接受。
    """
    mon = _mon()
    i = mon.index("private void TryTriggerForDay(")
    seg = mon[i:i + 2200]
    assert "RequestMailPending();" in seg, "换日没刷新优先队列 —— 名单会一直停在上次的样子"
    # 必须排在闸门（日概率）之前：今天没抽中也要更新名单，否则"没抽中"与"名单过期"分不清
    assert seg.index("RequestMailPending()") < seg.index("_dailyChance"), (
        "刷新排在了日概率闸之后 —— 没抽中的那些天名单不会更新")
    # 且**不许 await / 同步等**（会把换日那一刻的主线程卡住）
    i_r = seg.index("RequestMailPending()")
    assert "await" not in seg[i_r - 40:i_r + 40].lower(), "刷新被同步等待了（主线程会卡顿）"


def test_mail_relay_intent_is_selected_only_after_all_gates():
    """`mail_relay` 必须在**所有闸门之后**才替换意图（它是"说什么"，不是"能不能说"）。"""
    mon = _mon()
    i = mon.index('? "mail_relay"')
    # 窗口取 1400：选 intent 那行前面隔着一段注释（追述三条闸门各自在哪），
    # 窗口太窄会把 `IsCoolingDown` 挤出视野，测试就变成在测注释长度。
    seg = mon[i - 1400:i + 400]
    for gate in ("IsCoolingDown(r.Name, curDay)", "!r.InContacts && !r.SameGrid"):
        assert gate in seg, f"意图选择前面少了闸门：{gate}"
        assert mon.index(gate) < i, f"{gate} 排在了意图选择之后"


# ============================================================ ③ 跨语言：名单与键成对

def test_python_serves_the_rpc_csharp_asks_for():
    """C# 问的 `list_mail_pending` 必须真的在 `handle_request` 里有分支，且回 `items` 键。"""
    py = (ROOT / "transport" / "ws_channel.py").read_text(encoding="utf-8")
    assert 'if method == "list_mail_pending":' in py, "Python 没提供 list_mail_pending"
    i = py.index('if method == "list_mail_pending":')
    seg = py[i:i + 900]
    assert '"items"' in seg, "回包没带 items 键（C# 读不到名单）"
    assert "pending_map" in seg, "没走 mailbox.pending_map（会各写一份，必然分家）"
    # C# 侧必须用同一个方法名，且**从 data 里读** items
    mon = _mon()
    assert 'SendRequest("list_mail_pending"' in mon, "C# 没请求这份名单"


def _strip_comments(src: str) -> str:
    """去掉 `//` 行注释（保留行号：注释行替换成空行，别把上下文挤到一行）。

    为什么必须去注释再断言：本文件里几条判据是"源码里不许出现某个字符串"，
    而**解释这个 bug 的注释本身就含有那个字符串**。不剥的话，测试会逼着人
    把注释删掉才肯变绿 —— 那正好把最该留的证据抹掉了。
    """
    out = []
    for line in src.splitlines():
        i = line.find("//")
        out.append("" if i == 0 else (line[:i] if i > 0 else line))
    return "\n".join(out)


def _method_body(src: str, sig: str) -> str:
    """按**大括号配对**取出一个 C# 方法的完整函数体（含签名）。

    比"从某个位置切 N 个字符"稳：加注释、加探针、换排版都不会让判据悄悄滑到隔壁函数去。
    """
    i = src.index(sig)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    return src[i:]


def test_csharp_reads_items_from_the_data_envelope():
    """★★ 名单在 `data` 里面，不是回包顶层 —— 这里曾经是一个**功能完全失效却不报错**的 bug ★★

    线上真实回包（`WsServer._run_request` 装帧，已按真机形态复刻）::

        {"type":"response","req_id":"u7","ok":true,
         "data":{"items":{"李四":["张三"]},"world_known":true,"world_id":"","world_epoch":0}}

    而 C# 原先写的是 `resp["items"]` —— 那是信封的**上一层**，恒为 null；偏偏
    `SetMailPending(null)` 是"合法的空答案"（它的注释里写着"空表也是有效答案，必须照写"），
    于是每次刷新都把名单**清空**。结果是：投递、账本、排序键、舞台指令全都对，
    唯独这份名单永远是空的 ⇒ **优先队列一次都没生效过，且没有任何报错**。

    比这条 bug 更值得注意的是：**原来的测试反过来把这个 bug 钉住了**
    （断言源文件里必须有 `resp["items"] as JObject`）。所以这里不再只比字符串，
    而是把回包**真的跑一遍**再按 C# 的读法取值 —— 判据与线上同源。
    """
    mon = _mon()
    # 窗口取到**整个 method 结束**（大括号配对），而不是固定字符数：固定窗口会在
    # 加注释/加探针之后悄悄滑走，判据就退化成"在测排版长度"（11-15 加探针时正撞上）。
    seg = _strip_comments(_method_body(mon, 'internal void RequestMailPending('))
    assert 'resp["items"]' not in seg, (
        "又在顶层读 items 了 —— 它在 data 里。这条读取恒为 null，而 null 是"
        "『合法空名单』⇒ 优先队列会静默失效（这正是 11-13 复核抓到的那个 bug）")
    assert 'resp["data"]' in seg, "没从 data 里读（协议信封：{ok, data, error}）"
    assert '["items"]' in seg, "读了 data 却没取 items"

    # ★ 语义判据：真跑一遍 handler，拿到**原样装帧**的回包，再按 C# 的路径去取。
    import asyncio
    import importlib

    from agent_loop.agent_loop import AgentLoop
    from agent_loop.agent.dialogue_agent import create_user_message
    from agent_loop.llm.stub_client import StubLlmClient
    from agent_loop.transport.bridge import StubGameBridge
    from agent_loop.transport.ws_channel import ChatHub, WsServer
    import os
    import shutil
    import tempfile

    TMP = os.path.join(tempfile.gettempdir(), "mail_envelope_probe")
    shutil.rmtree(TMP, ignore_errors=True)
    A, B = "张三_信封", "李四_信封"
    bridge = StubGameBridge()
    for n in (A, B):
        bridge.seed_unit(n, {"realm": "元婴", "player": {"name": "韩立", "relation": "道友",
                                                        "intim": 60, "same_grid": True}})
    AgentLoop.reset_for_test(storage_root=TMP, bridge=bridge,
                             llm=StubLlmClient(fn=lambda req: {"text": "好", "tool_calls": []}))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))
    mb = _mailbox()
    mk = lambda t: create_user_message(t, source={"kind": mb.MAIL_KIND, "from": A})
    assert mb.deliver(loop, mk, from_id=A, to_id=B, text="明日辰时落霞谷一见") is None

    payload = asyncio.run(hub.handle_request("list_mail_pending", {}))
    # 传输层的装帧（`WsServer._run_request`）—— 与线上逐字段一致
    frame = {"type": "response", "req_id": "u7", "ok": True, "data": payload}

    # ① 顶层**没有** items：这就是旧读法拿到 null 的原因
    assert "items" not in frame, (
        f"回包顶层出现了 items —— 那说明装帧方式变了，本测试与 C# 的读法都要跟着改：{list(frame)}")
    # ② C# 的读法（data → items）必须真的取得回名单
    got = (frame.get("data") or {}).get("items")
    assert got == {B: [A]}, f"按 C# 的读法取不到名单：{got!r}"


def test_senders_flow_into_the_stage_instruction():
    """发信人真名必须真的进到舞台指令里 —— 否则模型不知道"谁托的你"。"""
    mon = _mon()
    assert "MailSenderOf(r.Name)" in mon, "选 intent 时没把发信人带上"
    ini = _initiative()
    msg = ini.format_initiative_message(ini.MAIL_RELAY_INTENT, "张三", same_grid=True)
    assert "张三" in msg, msg
    assert "别把他的那句话当成玩家说的" in msg, (
        "没点明『别把他的话当成玩家说的』—— 模型会把捎话内容当成玩家的诉求")


def test_mail_relay_exempt_from_the_python_throttle():
    """`mail_relay` 要豁免 Python 的 300s 全局节流。

    理由与 `action_done` 同源：那是"防事件轰炸"的双保险，而优先队列**只改排序**、
    日节拍一天最多派一个人 —— 本来就低频。被它挡掉的后果是整条链路
    在最常见的情形下静默失效（玩家刚跟 A 说完话 → A 捎话 → 恰好撞上 300s 窗口 → 丢）。
    """
    py = (ROOT / "transport" / "ws_channel.py").read_text(encoding="utf-8")
    i = py.index("player_initiated = (")
    seg = py[i:i + 500]
    assert 'intent == "mail_relay"' in seg, "mail_relay 没豁免节流（会被 300s 窗口静默吞掉）"


# ============================================================ ④ 端到端：优先队列真的改变了排序

def _sort_rows(rows):
    """复刻 C# `SortRows` 的比较键（Python 版，拿同一份语义跑一遍）。

    为什么复刻而不是解析 C#：这条要验的是**语义**（有信者排前、内部仍按好感），
    而代码文本已被 ② 那几条钉住。复刻版一旦与 C# 走偏，②会把它们拉回来。
    洗牌段刻意不复刻（随机性不影响判据：我们只断言"有信者都在无信者之前"）。
    """
    return sorted(rows, key=lambda r: (not r["has_mail"], -r["intim"]))


def test_priority_puts_mail_recipients_ahead_of_higher_intimacy():
    """**核心语义**：一个 0 好感的人只要有信，就排在 300 好感的道侣前面。

    这正是用户说的「优先队列比好感度同格之类的优先级都要高」。
    """
    rows = [
        {"name": "道侣", "intim": 300, "same_grid": True, "has_mail": False},
        {"name": "同格陌生人", "intim": 0, "same_grid": True, "has_mail": False},
        {"name": "收信人", "intim": -50, "same_grid": False, "has_mail": True},
    ]
    order = [r["name"] for r in _sort_rows(rows)]
    assert order[0] == "收信人", f"有信者没排到最前：{order}"
    assert order[1:] == ["道侣", "同格陌生人"], f"无信者之间的好感序被改了：{order}"


def test_priority_keeps_intimacy_order_among_mail_recipients():
    """**有信的若干人之间**仍按好感降序 —— 优先只是"提前"，不是"重新洗牌"。"""
    rows = [
        {"name": "低", "intim": 10, "same_grid": True, "has_mail": True},
        {"name": "高", "intim": 200, "same_grid": False, "has_mail": True},
        {"name": "无信", "intim": 999, "same_grid": True, "has_mail": False},
    ]
    order = [r["name"] for r in _sort_rows(rows)]
    assert order == ["高", "低", "无信"], order


def test_priority_does_not_change_who_is_eligible():
    """优先队列**不放宽资格**：没有信的人照旧参与排序，有信的人也不因此免掉冷却/概率。

    这条与上面两条一起构成用户边界：排序变、闸门不变。
    """
    mon = _mon()
    # 资格判据（通讯录 or 同格）在 TryDispatch 里，且**不含** HasMail
    i = mon.index("private bool TryDispatch(")
    seg = mon[i:i + 2600]
    assert "if (!r.InContacts && !r.SameGrid) continue;" in seg
    assert "HasMail" not in seg.split("string intent")[0], (
        "资格/冷却段出现了 HasMail —— 优先队列正在给某些人开后门")


def test_end_to_end_mail_then_priority_then_relay(monkeypatch=None):
    """全链路：A 捎话 → Python 名单出现 B → C# 排序把 B 提前 → 回合里按托付内容说事。"""
    import asyncio
    import os
    import shutil
    import tempfile

    from agent_loop.agent_loop import AgentLoop
    from agent_loop.transport.bridge import StubGameBridge
    from agent_loop.llm.stub_client import StubLlmClient
    from agent_loop.transport.ws_channel import WsServer, ChatHub

    TMP = os.path.join(tempfile.gettempdir(), "mail_priority_e2e")
    shutil.rmtree(TMP, ignore_errors=True)
    A, B = "张三_优先", "李四_优先"
    bridge = StubGameBridge()
    for n in (A, B):
        bridge.seed_unit(n, {"realm": "元婴", "player": {
            "name": "韩立", "relation": "道友", "intim": 60, "same_grid": True}})
    seen = []

    def _fn(req):
        seen.append("|".join(
            b.get("text", "") for m in req.get("messages", [])
            for b in (m.get("content") or []) if isinstance(b, dict) and b.get("type") == "text"))
        return {"text": "好。", "tool_calls": []}

    AgentLoop.reset_for_test(storage_root=TMP, bridge=bridge, llm=StubLlmClient(fn=_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))
    hub.min_initiative_interval = 0.0
    mb = _mailbox()
    from agent_loop.agent.dialogue_agent import create_user_message

    # ① 投递
    mk = lambda t: create_user_message(t, source={"kind": mb.MAIL_KIND, "from": A})
    assert mb.deliver(loop, mk, from_id=A, to_id=B, text="明日辰时落霞谷一见") is None

    # ② Python 名单里出现 B（C# 侧优先队列的数据源）
    assert mb.pending_map(loop).get(B) == [A], mb.pending_map(loop)

    # ③ 优先队列入选后，C# 会派 mail_relay —— 这里直接按那条 intent 驱动一回合
    asyncio.run(hub.handle_initiative(B, "mail_relay", reason=A, same_grid=True))

    ctx = seen[-1]
    assert "落霞谷" in ctx, "捎话正文没进模型上下文"
    assert "受人所托" in ctx, "舞台指令没点明『受人所托』—— 模型会当成自己想找他"
    assert f"{A}托人捎了句话给你" in ctx, "舞台指令没点出发信人"
    assert "别把他的那句话当成玩家说的" in ctx, "没防住『把捎话当成玩家诉求』"

    # ④ 读过之后名单消失（否则他会永远排在最前）
    assert mb.pending_map(loop) == {}, f"读过了但名单还在：{mb.pending_map(loop)}"
