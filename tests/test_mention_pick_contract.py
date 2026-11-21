"""群聊 @ 补全（C# UI）的**接线契约**。

为什么要单独一个文件：`csharp/tests/MentionPickCoreTests` 只测**判据**（该不该弹、
候选怎么过滤、选中怎么替换）。判据全绿 + 一处没接 = 玩家侧**毫无变化**，
而那种失败在真机上只表现为"打 @ 没反应"，看不出是判据错还是没接线。

本文件锁三件事：
  A. 三层是否真的串起来了（Update → PollMentionPick → MentionPickPanel）；
  B. 几个**顺序**约束（错了就是静默的坏行为，不是崩溃）；
  C. 与 Python 侧解析器的判据同源（漂移 = "UI 弹了候选、Python 认不出"）。

★ 特别钉住的一条：回车必须**先给浮层**、再给发送 ★
  顺序反了，玩家想选候选却把 `@张` 发了出去。这条没有任何报错，只能靠断言。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _cs_method(src: str, sig: str) -> str:
    """按花括号配平取一个 C# 方法体。

    为什么不用 `src[i:i+900]`：字符数是**猜**的，方法变短后切片会跨进下一个方法，
    断言于是在隔壁方法里找东西 —— 这次就是这样误报的（`IsOwnedBy` 明明不在
    `HideForce` 里，却被切片带了进来）。
    """
    i = src.find(sig)
    if i == -1:
        return ""
    j = src.find("{", i)
    if j == -1:
        return ""
    depth = 0
    k = j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k]
        k += 1
    return ""


def _poll_body(code: str) -> str:
    """取 `PollMentionPick` 的方法体（用花括号配平，不用魔法数字截断）。

    为什么不用 `code[i:i+4600]`：那个数字随注释增删而失效，一旦超出真实方法体，
    断言就会在**相邻方法**里找东西（假绿），或者在方法变长后漏掉尾部（假红）。
    """
    i = code.find("private bool PollMentionPick(")
    assert i != -1, "找不到 PollMentionPick"
    j = code.find("{", i)
    assert j != -1
    depth = 0
    k = j
    while k < len(code):
        if code[k] == "{":
            depth += 1
        elif code[k] == "}":
            depth -= 1
            if depth == 0:
                return code[j:k]
        k += 1
    raise AssertionError("PollMentionPick 方法体没有配平")


def _code(rel: str) -> str:
    """剥掉 C# 行注释（结构断言只看代码，不看注释）。"""
    return "\n".join(ln.split("//", 1)[0] for ln in _read(rel).splitlines())


PRESENTER = "csharp/UI/ChatPresenter.cs"
PANEL = "csharp/UI/MentionPickPanel.cs"
CORE = "csharp/UI/MentionPickCore.cs"


# ══════════════════════════════════════════════════════════════════
# A. 三层是否串起来了
# ══════════════════════════════════════════════════════════════════

def test_core_is_wired_into_presenter():
    """判据核必须被 presenter 真的调用（否则它只是个漂亮的孤立文件）。"""
    code = _code(PRESENTER)
    assert "MentionPickCore.Analyze(" in code, "presenter 没调 Analyze ⇒ 永远不知道该不该弹"
    assert "MentionPickCore.Filter(" in code, "presenter 没调 Filter ⇒ 候选永远是空的"
    assert "MentionPickCore.Accept(" in code, "presenter 没调 Accept ⇒ 选了也不改文本"


def test_panel_is_wired_into_presenter():
    code = _code(PRESENTER)
    for api in ("MentionPickPanel.EnsureBuilt(", "MentionPickPanel.SetCandidates(",
                "MentionPickPanel.Hide", "MentionPickPanel.AcceptHot()",
                "MentionPickPanel.MoveHot("):
        assert api in code, f"presenter 没调 {api}"


def test_poll_is_called_from_update():
    """`PollMentionPick` 必须挂在 `Update()` 里 —— 不挂就永远不会跑。"""
    code = _code(PRESENTER)
    i = code.find("private void Update()")
    assert i != -1
    body = code[i:i + 6000]
    assert "PollMentionPick(" in body, "Update 里没调 PollMentionPick"


def test_panel_builds_a_real_tree():
    """浮层必须真的建出可点的行（不是只存了个名字列表）。"""
    code = _code(PANEL)
    assert "AddComponent<Image>" in code, "浮层没有背景图 ⇒ 点不到"
    assert "AddComponent<Button>" in code, "候选行没有 Button ⇒ 点不了"
    assert "ClickUtils.Attach(" in code, \
        "没用 ClickUtils 挂点击（IL2CPP 下直接 AddListener(lambda) 会 CS1660）"


# ══════════════════════════════════════════════════════════════════
# B. 顺序约束（错了不崩溃，只是行为坏）
# ══════════════════════════════════════════════════════════════════

def test_mention_poll_runs_before_enter_send():
    """★ 回车必须先给浮层、再给发送 ★

    反了的话，玩家想选候选却把 `@张` 当成消息发了出去 —— 没有报错，
    玩家只会觉得"这个补全坏了"。这是本功能最容易被改坏的一处。
    """
    code = _code(PRESENTER)
    i_poll = code.find("if (PollMentionPick(input)) return;")
    i_send = code.find("OnSubmitRequested();\n                return;")
    assert i_poll != -1, "Update 里没有 `if (PollMentionPick(input)) return;`"
    assert i_send != -1, "没找到回车发送那一行"
    assert i_poll < i_send, "PollMentionPick 排在了回车发送之后 ⇒ 选候选会变成发送消息"


def test_enter_accepts_candidate_instead_of_sending():
    """浮层开着时，回车走 AcceptHot 并**消费**这次按键。"""
    code = _code(PRESENTER)
    i = code.find("private bool PollMentionPick(")
    assert i != -1
    body = code[i:i + 4600]
    assert "AcceptMentionPick()" in body, "回车路径没走接受候选"
    # 回车分支必须 return true（消费），否则会继续走到发送
    i_ret = body.find("if (keyEnter)")
    assert i_ret != -1, "回车分支不见了（或被改写成了别的形状）"
    tail = body[i_ret:i_ret + 260]
    assert "return true" in tail, "回车选中候选后没有 return true ⇒ 还会继续走到发送"


def test_steal_key_predicate_does_not_depend_on_volatile_caret():
    """★ 回车抢键判据必须**不依赖这一刻的光标** ★（11-21 实机事故的根因）

    `InputField`（单行）按回车会**先 Submit 并自动失焦**，失焦挪走 `caretPosition`。
    旧写法在回车那一帧重新 `Analyze(text, caret)`，算出"光标不在任何 @ 段里"
    ⇒ `Hide()` ⇒ `IsOpen == false` ⇒ 落到发送分支。

    实机证据（13:18 g2 群）：
      · Python 侧 `@ 解析失败（unknown=[]）` —— @ 后面是空的；
      · Player.log 里 `@ 补全选中` **零次** —— 回调压根没跑过。

    所以判据必须是"文本里有一个还没确定的 @ 段"（`_pickCtx`），它只随
    `RefreshMentionCandidates` 更新，与按键那一刻的光标无关。

    ★ 断言必须钉在 `picking` 这一行上 ★
      "body 里出现过 `_pickCtx.HasAt`" 是不够的 —— Esc 分支里也有它，
      把 `picking` 改回只看 `IsOpen` 时那种写法照样能过（变异实测：**抓不住**）。
    """
    code = _code(PRESENTER)
    body = _poll_body(code)
    # 精确抓 `bool picking = ...;` 这一行
    m = re.search(r"bool picking\s*=\s*([^;]+);", body)
    assert m is not None, "找不到 picking 判据（被改写形状了）"
    expr = m.group(1)
    assert "_pickCtx.HasAt" in expr, (
        "picking 判据没用到 _pickCtx ⇒ 回车那帧失焦后 IsOpen 变假，回车变成发送"
        "（11-21 事故原样复现）"
    )
    assert "MentionPickPanel.IsOpen" in expr, (
        "picking 判据没有 IsOpen ⇒ 没候选时也会吃掉回车"
    )


def test_keyboard_handled_before_recompute():
    """★ 键盘处理必须排在**重算候选之前** ★（11-21 事故的顺序根因）

    旧顺序「重算（可能 Hide）→ 看 `IsOpen` 吃键」有一个致命耦合：
    回车导致 InputField 失焦 → 失焦挪 caret → 重算算出"不在 @ 段里" → Hide
    → 回车落到发送分支。

    正确顺序：**意图（按键）先于数据（光标/文本）**。重算是副作用，
    副作用不许推翻玩家刚刚表达的意图。
    """
    code = _code(PRESENTER)
    body = _poll_body(code)
    i_key = body.find("if (keyEnter)")
    i_refresh = body.find("RefreshMentionCandidates(input, text, caret);")
    assert i_key != -1, "找不到回车处理"
    assert i_refresh != -1, "找不到重算调用"
    assert i_key < i_refresh, (
        "重算排在了键盘处理之前 ⇒ 回车那一帧的失焦会先把浮层收掉，回车变成发送"
        "（11-21 事故原样复现）"
    )


def test_accept_uses_stored_context_not_live_caret():
    """★ `ApplyMentionPick` 必须用 `_pickCtx` 替换，不许重新 Analyze ★

    重新 Analyze 读到的是"回车之后的光标"，`Accept` 于是走 `!ctx.HasAt` 分支
    ⇒ 什么都不换 ⇒ 玩家发出去的只有 `@`。
    """
    code = _code(PRESENTER)
    i = code.find("private void ApplyMentionPick(")
    assert i != -1
    body = code[i:i + 2800]
    assert "var ctx = _pickCtx;" in body, "没使用记录的候选来源"
    # 兜底可以留着，但必须是**兜底**：正常的 ctx 赋值必须在它之前
    i_ctx = body.find("var ctx = _pickCtx;")
    i_fallback = body.find("MentionPickCore.Analyze(")
    assert i_fallback == -1 or i_fallback > i_ctx, (
        "ApplyMentionPick 直接 Analyze 了实时光标 ⇒ 回车选中会失效（11-21 事故）"
    )
    assert "_pickCtx = new MentionPickCore.PickContext();" in body, "补全消费后没清 _pickCtx"


def test_accept_hot_falls_back_to_first_row():
    """★ 浮层开着 ⇒ 回车一定选中某一行 ★（`_hot == -1` 也要能选）

    `_hot` 会被 `Hide()` / 空列表置成 -1。若 `AcceptHot` 在 -1 时直接返回 false，
    玩家**看见**名字、按回车却什么也没发生，然后这一击落到发送分支。
    """
    code = _code(PANEL)
    i = code.find("public static bool AcceptHot()")
    assert i != -1
    body = code[i:i + 1400]
    assert "if (!IsOpen) return false;" in body, "IsOpen 早退不能再带 _hot 条件"
    assert "_hot = 0" in body, "没有兜底选第一行 ⇒ _hot==-1 时回车失效"


def test_send_path_clears_pick_state_and_logs_raw_text():
    """发送后必须清 `_pickCtx`，并把原文记一行日志。

    清状态：残留的 @ 段会让**下一句**的回车被莫名吃掉一次。
    记原文：11-21 那次事故里两份日志都查不到"玩家到底打了什么"，只能靠
    `unknown=[]` 反推 —— 这个盲区不该留第二次。
    """
    code = _code(PRESENTER)
    i = code.find("SendRoomMessage(")
    assert i != -1
    body = code[max(0, i - 1600):i]
    assert "_pickCtx = new MentionPickCore.PickContext();" in body, "发送后没清 _pickCtx"
    assert "原文=" in body, "群发言发送处没记原文 ⇒ 下次同样的事故还得靠反推"


def test_pick_state_cleared_on_every_hide_at_transition():
    """★ 每一处"随窗作废"的 `Hide()` 都必须同时清 `_pickCtx` ★

    `_pickCtx` 是 presenter 的**实例**字段，而 `MentionPickPanel` 是**静态**的。
    换窗/关窗/换人时若只 `Hide()` 不清 `_pickCtx`，那份"@ 段"会留到下一个窗口 ——
    新窗口里回车会被一个**属于上一个群**的 @ 段吃掉一次（或者反过来，
    `HasAt` 残留让 Esc 莫名吃掉一次按键）。失败形态全静默。

    这一条是给自己补的：11-21 第一次修的时候只在"发送"处清了，四处换窗口全漏。
    """
    code = _code(PRESENTER)
    # 找出所有 `MentionPickPanel.Hide();` 后面紧跟若干行（看是否出现 _pickCtx 清空）
    misses = []
    for m in re.finditer(r"MentionPickPanel\.Hide\(\);", code):
        tail = code[m.end():m.end() + 400]
        # 只检查"块级"收尾：Hide 之后 3 行内应出现 _pickCtx 清空或另一个 return/Hide
        window = "\n".join(tail.splitlines()[:4])
        if "_pickCtx = new MentionPickCore.PickContext();" in window:
            continue
        # 允许：Hide 在刷新函数里（那里自己管 _pickCtx），或后面紧跟 `_lastInputText` 但不属于换窗
        # 判据：凡是同时重置了 `_lastInputText` 的地方（= 换窗/换人/发送），都必须清 _pickCtx
        if "_lastInputText = null;" in window:
            misses.append(code[max(0, m.start() - 120):m.start() + 120])
    assert not misses, (
        "以下位置重置了输入框基准却没清 _pickCtx（会把旧 @ 段带到下一个窗口）：\n"
        + "\n---\n".join(misses)
    )


def test_panel_hide_is_owner_scoped():
    """★ 只有建树的那个 presenter 能收起浮层 ★（11-21 修跨实例干扰）

    `ChatPresenter` 一个面板宿主一个实例，实测同一进程里同时存在 5 个
    （Player.log `Init 完成 实例#1..#5` —— 换 NPC / 重开窗时旧的未必立刻销毁）。
    它们的 `Update()` 每帧都在跑，而非群聊实例会走 `!IsRoomMode ⇒ Hide()` 早退。
    若 Hide 无条件执行，**单聊窗每帧都在关群聊窗的候选浮层** —— 玩家看到
    "浮层闪一下就没了"，且完全没有日志。

    判据：所有 presenter 侧的 `Hide(...)` 调用都必须带 owner 参数。
    """
    code = _code(PRESENTER)
    bare = re.findall(r"MentionPickPanel\.Hide\(\s*\)", code)
    assert not bare, (
        "有 %d 处 `MentionPickPanel.Hide()` 没带 owner ⇒ 非所有者也能关别人的浮层"
        "（跨实例干扰，静默）" % len(bare)
    )
    panel = _code(PANEL)
    assert "IsOwnedBy" in panel, "面板没有 owner 判据"
    assert "if (owner != null && !IsOwnedBy(owner)) return;" in panel, \
        "Hide 没有按 owner 短路"
    # EnsureBuilt 必须登记 owner，否则 IsOwnedBy 永远为真（保护失效）
    assert "if (owner != null) _owner = owner;" in panel, "EnsureBuilt 没登记 owner"
    # 建树处必须把 this 传进去
    assert re.search(r"MentionPickPanel\.EnsureBuilt\([^;]*,\s*this\)", code), \
        "EnsureBuilt 调用没传 this ⇒ owner 永远是 null，保护形同虚设"


def test_window_identity_change_uses_force_hide():
    """★ "窗口身份变了"的出口必须用 `HideForce()`（越过 owner 判据）★

    带 owner 的 `Hide(this)` 在 owner 是**另一个** presenter 时会直接 return。
    而这几条路径是"这份候选与当前窗口已经无关"的**绝对判断**：
    关窗 / 换 NPC / 开新聊天 / 退出群聊 / 群被解散。
    若在这里被 owner 判据挡下 ⇒ 窗口关了、浮层还在，而它 `raycastTarget = true`
    ⇒ **吃掉点击且完全无日志**（玩家只会说"点哪都没反应"）。

    判据：凡是紧跟 `_window.OpenFor(` 或位于关窗收尾处的收起，都必须是 HideForce。
    """
    code = _code(PRESENTER)
    # ① 每一处 `OpenFor(` 之前 800 字内必须有 HideForce，且不能只有 Hide(this)
    for m in re.finditer(r"_window\.OpenFor\(", code):
        head = code[max(0, m.start() - 900):m.start()]
        assert "MentionPickPanel.HideForce();" in head, (
            "换绑/开窗前没有 HideForce ⇒ 旧候选（可能属于上一个群/上一个人）会留下来"
        )
    assert code.count("MentionPickPanel.HideForce();") >= 5, (
        "HideForce 的调用点少于 5 处：关窗、换 NPC×2、退出群聊、群解散都该有"
    )
    # ② 面板侧必须真的越过 owner
    panel = _code(PANEL)
    body = _cs_method(panel, "public static void HideForce()")
    assert body, "找不到 HideForce"
    assert "IsOwnedBy" not in body, "HideForce 里不该再判 owner（它存在的意义就是越过它）"


def test_suppress_flag_blocks_self_retrigger():
    """★ 改文本后必须挡住下一帧重算 ★

    选中候选时我们**写** input.text；下一帧轮询读到新值会立刻重算候选，
    表现为"选完了浮层又弹回来"（而且光标位置也被搅乱）。
    """
    code = _code(PRESENTER)
    assert "_suppressMentionPoll = true;" in code, "改文本前没设抑制标志"
    i = code.find("if (_suppressMentionPoll)")
    assert i != -1, "轮询里没检查抑制标志"
    body = code[i:i + 400]
    assert "_suppressMentionPoll = false;" in body, "标志没被复位 ⇒ 之后再也不重算候选"


def test_only_active_in_room_mode():
    """单聊不该弹 @ 候选（窗口就绑着一个人，弹了只是打扰）。"""
    code = _code(PRESENTER)
    i = code.find("private bool PollMentionPick(")
    body = code[i:i + 1400]
    assert "_window.IsRoomMode" in body, "没按群聊门控 ⇒ 单聊也会弹候选"


def test_hides_on_send_close_and_switch():
    """发送 / 关窗 / 换人 都必须收起浮层。

    ★ 关窗那条尤其重要 ★：浮层的树挂在窗口下面，窗口关了不收起会留一层
    **看不见但吃射线**的节点 —— 点聊天区没反应，且完全无日志（本仓踩过同类坑）。
    """
    code = _code(PRESENTER)
    # 发送处
    i_send = code.find("_ws.SendRoomMessage(_window.CurrentRoomId, msg);")
    assert i_send != -1
    assert re.search(r"MentionPickPanel\.(Hide|HideForce)\s*\([^)]*\)", code[max(0, i_send - 700):i_send]), \
        "发送前没收起浮层"
    # 关窗处
    i_close = code.find("private void OnCloseClicked()")
    assert i_close != -1
    # 关窗必须用 HideForce（越过 owner，理由见 test_window_identity_change_uses_force_hide）
    assert re.search(r"MentionPickPanel\.HideForce\s*\(\s*\)", code[i_close:i_close + 1200]), \
        "关窗时没有 HideForce ⇒ 会留一层吃射线的残影（且 owner 不匹配时 Hide 会直接 return）"
    # 换人处
    i_open = code.find("_window.OpenFor(id);")
    assert i_open != -1
    assert re.search(r"MentionPickPanel\.(Hide|HideForce)\s*\([^)]*\)", code[max(0, i_open - 900):i_open]), \
        "换 NPC 时没收起浮层（会弹出上一个群的人）"


def test_no_match_hides_panel():
    """用户拍板：没有匹配就不弹（打错名字时不打扰）。"""
    code = _code(PRESENTER)
    i = code.find("private void RefreshMentionCandidates(")
    assert i != -1
    body = code[i:i + 1400]
    assert "hits.Count == 0" in body, "没处理「一个候选都没有」"
    assert re.search(r"MentionPickPanel\.Hide\s*\([^)]*\)\s*;", body), "无候选时没隐藏浮层"


def test_panel_disposes_tree():
    """浮层要有销毁入口（否则窗口反复开关会累积树）。"""
    code = _code(PANEL)
    assert "public static void Dispose()" in code
    assert "Object.Destroy(_root)" in code or "Destroy(_root)" in code


# ══════════════════════════════════════════════════════════════════
# C. 与 Python 侧判据同源
# ══════════════════════════════════════════════════════════════════

def test_separator_tables_are_in_sync():
    """★ C# 的 `NameStop` 与 Python 的 `_NAME_STOP` 必须同源 ★

    漂移的后果是**最坏的那一种**：UI 弹出了候选、玩家点了、Python 却认不出这个名字
    ⇒ 消息要么进错人账本、要么被当成"@了不存在的人"。两边都看不出异常。
    """
    cs = _read(CORE)
    py = _read("room/mention.py")

    # 取出 C# 的 NameStop 数组内容。
    # ★ 不能只按 `'(.)'` 抓 ★：数组里有 `'\''`（单引号）与 `'"'`（双引号），
    #   前者的字面量是 4 个字符 `'` `\` `'` `'`，正则必须认转义，否则会把
    #   `'` 和 `\` 当成两个独立的"分隔符"（第一版就误报了这两个）。
    m = re.search(r"NameStop\s*=\s*new\s+char\[\]\s*\{(.*?)\};", cs, re.S)
    assert m, "没找到 C# 的 NameStop 数组"
    raw = m.group(1)
    cs_chars = set()
    for lit in re.findall(r"'(\\.|[^'\\])'", raw):
        if lit.startswith("\\"):
            cs_chars.add({"\\t": "\t", "\\n": "\n", "\\r": "\r",
                          "\\\\": "\\", "\\'": "'"}.get(lit, lit[1:]))
        else:
            cs_chars.add(lit)

    # 取出 Python 的 _NAME_STOP。
    # ★ 必须**按 Python 字符串字面量解转义**，不能直接拿源码原文比 ★
    #   源码里写的是 `set(" \t\r\n　,…\"'“”‘’")` —— 其中的 `\"` 是**双引号**、
    #   `\t` 是制表符。直接比原文会把 `\` 与 `"` 当成两个独立字符（第一版误报了 `\`）。
    #   用 `ast.literal_eval` 让 Python 自己解，最不容易错。
    import ast as _ast
    m2 = re.search(r"_NAME_STOP\s*=\s*set\((.+?)\)\s*$", py, re.M)
    assert m2, "没找到 Python 的 _NAME_STOP"
    py_chars = set(_ast.literal_eval(m2.group(1).strip()))

    missing_in_cs = py_chars - cs_chars
    assert not missing_in_cs, (
        f"这些分隔符 Python 有、C# 没有 ⇒ UI 会弹候选但 Python 认不出：{missing_in_cs}")


def test_at_chars_match():
    """`@` 的全半角两边都要认。"""
    cs = _read(CORE)
    assert "'@'" in cs and "'＠'" in cs, "C# 没同时认全角与半角 @"
    py = _read("room/mention.py")
    assert '"@"' in py and '"＠"' in py, "Python 没同时认全角与半角 @"


def test_candidate_limit_is_bounded():
    """候选数要有上限（浮层不该盖住整个窗口）。"""
    cs = _read(CORE)
    assert "MaxCandidates" in cs
    m = re.search(r"MaxCandidates\s*=\s*(\d+)", cs)
    assert m and 1 <= int(m.group(1)) <= 12, f"MaxCandidates 取值不合理：{m.group(1) if m else '?'}"


def test_core_has_no_unity_dependency():
    """★ 判据核必须零 Unity 依赖 ★ 否则 `csharp/tests/MentionPickCoreTests` 编不起来
    （那个测试工程是 net8.0，链接同一份源码）。"""
    cs = _read(CORE)
    for bad in ("using UnityEngine", "using UnityEngine.UI", "MonoBehaviour"):
        assert bad not in cs, f"判据核里出现了 {bad} ⇒ 无法编成 net8.0 单测"


def test_core_test_project_links_the_real_source():
    """测试工程必须链接**主工程的同一份源码**，不是副本。"""
    proj = _read("csharp/tests/MentionPickCoreTests/MentionPickCoreTests.csproj")
    assert "..\\..\\UI\\MentionPickCore.cs" in proj, \
        "测试工程没链接 MentionPickCore.cs 真源码"
