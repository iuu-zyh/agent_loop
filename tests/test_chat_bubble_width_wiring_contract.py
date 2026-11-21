"""气泡宽度**接线**契约（11-20，修「文字没到上限却撑满上限」时新增）。

## 为什么需要这一层（这是本仓第二次栽在同一个地方）

`BubbleWidthCore` 有 36 项单测，全绿。可 11-20 真机上气泡还是横到了天花板。
原因：**单测只测算式，测不到"接线绕过了判据"**。

真机的 bug 形状是：

    w = need > 0f ? Fit(need, cap) : cap;      // ← 量不出就按上限给

算式 `Fit` 本身没错，错在调用方把 `measured <= 0`（布局未结算、`preferredWidth` 读 0）
当成了"这段文字不需要宽度"。而气泡是**一次性**的：非流式气泡只在构造时
`SetText → ApplyBubbleWidth` 走一遍（流式靠每帧 `_deltaDirty` 刷新才会重算），
于是第一帧的读 0 被**永久固化**成满宽。

真机表现（用户 11-20 截图）：一段约 30 字的回话气泡横向撑满，
而**关窗重开**（走 `ReplaceHistory` 回放，那时布局早已结算）完全正常。

## 本文件钉四条

  ① 接线里**必须**先问 `ShouldWrite`，且 `ShouldWrite` 为假时**不许写** `sizeDelta`；
  ② 量不出时**不许**用 `cap` 兜底（那正是旧写法）；
  ③ 量不出必须排一次**下一帧重测**（否则等于"这一帧定终身"）；
  ④ 重测必须有**次数上限**（否则"永远量不出"会变成每帧一次的循环）。

判据归核（`BubbleWidthCore`，有 net8 单测）、接线归层（`ChatItemViews`）——
本文件守的是后者，两边合起来才算闭环。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"
VIEWS = CS / "UI" / "ChatItemViews.cs"
CORE = CS / "UI" / "BubbleWidthCore.cs"
TICKER = CS / "app" / "FrameTicker.cs"


def _code(p: Path) -> str:
    """剥掉 `//` 行注释（注释里写代码片段是合法的，不该被当实现）。"""
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        i = ln.find("//")
        if i >= 0 and not re.search(r'"\s*//', ln[:i]):
            ln = ln[:i]
        out.append(ln)
    return "\n".join(out)


def _method(src: str, sig: str) -> str:
    """按大括号配平截出方法体。"""
    i = src.index(sig)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
        k += 1
    raise AssertionError(f"方法体未闭合：{sig}")


# ══════════════════════════════════════════════════════════════════
# ① 写入闸：必须存在，且必须在写 sizeDelta 之前
# ══════════════════════════════════════════════════════════════════

def test_apply_checks_the_write_gate_before_writing():
    """`ApplyBubbleWidth` 必须先问 `ShouldWrite`，再写 `sizeDelta`。

    顺序是判据的全部：先写后判 = 判了也没用（宽度已经落下去了）。
    """
    body = _method(_code(VIEWS), "private void ApplyBubbleWidth()")
    assert "BubbleWidthCore.ShouldWrite(" in body, (
        "接线没有走 ShouldWrite 闸 —— 量不出文字宽时又会按上限给（11-20 那个 bug 的形态）"
    )
    i_gate = body.index("BubbleWidthCore.ShouldWrite(")
    i_write = body.index("sd.x = w;")
    assert i_gate < i_write, "ShouldWrite 判在写 sizeDelta **之后** —— 判了也没用，宽度已经落下了"


def test_no_cap_fallback_when_text_cannot_be_measured():
    """★ 量不出文字宽时**不许**拿 cap 兜底 ★ —— 这就是那个 bug 的原文。

    旧写法：`w = need > 0f ? Fit(need, cap) : cap;`
    扫全工具层的写法变体（`? ... : cap` / `= cap;` 紧跟在量不出分支里）。
    """
    body = _method(_code(VIEWS), "private void ApplyBubbleWidth()")
    # 旧形态一：三元表达式的 else 分支给 cap
    assert not re.search(r":\s*cap\s*;", body), (
        "接线里又出现了「量不出就给 cap」（`: cap;`）—— 这正是 11-20 满宽 bug 的写法"
    )
    # 旧形态二：直接 `w = cap`
    assert not re.search(r"\bw\s*=\s*cap\s*;", body), "接线里又出现了 `w = cap;`"


# ══════════════════════════════════════════════════════════════════
# ② 重测：必须排下一帧，且必须有次数上限
# ══════════════════════════════════════════════════════════════════

def test_unmeasurable_requests_a_remeasure():
    """量不出时必须排下一次重测 —— 否则"这一帧定终身"（气泡只有一次测量机会）。"""
    body = _method(_code(VIEWS), "private void ApplyBubbleWidth()")
    assert "RequestRemeasure()" in body, "量不出时没有排重测（等于第一帧读 0 就永远读 0）"
    # 重测必须在"闸为假"的分支里（不是别处随手调一下）。
    # 用 find 而不是 index：闸整体被删掉时应该报"闸没了"，不该抛 ValueError（那是测试自己的错，
    # 读报告的人会以为是环境问题 —— 11-20 变异验证时正是这么哑了一次）。
    i_gate = body.find("BubbleWidthCore.ShouldWrite(")
    assert i_gate >= 0, "接线里找不到 ShouldWrite 闸（先修上一条）"
    i_write = body.index("sd.x = w;")
    assert "RequestRemeasure()" in body[i_gate:i_write], "重测不在量不出的分支里"


def test_remeasure_is_bounded():
    """重测次数必须封顶 —— 否则"永远量不出"（字体缺失/行已销毁）会变成每帧循环。

    ⚠ 只断言 `MaxRemeasureTries` 出现在函数体里是**不够**的：把判断条件改成 `if (true)`
    之后那个标识符依然在场（11-20 变异验证实测漏网一次）。所以要钉的是
    **判断表达式本身**——标识符必须出现在 `if (...)` 的条件里，而不是散在别处。
    """
    views = _code(VIEWS)
    body = _method(views, "private void ApplyBubbleWidth()")
    assert "MaxRemeasureTries" in body, "重测没有次数上限（会变成每帧循环）"
    # 上限常量归核，不散落在接线里
    assert "MaxRemeasureTries" in _code(CORE), "MaxRemeasureTries 不在 BubbleWidthCore（判据该归核）"

    # ★ 关键：上限必须在 if 的**条件**里被比较，且不许被常量/恒真短路 ★
    m = re.search(r"if\s*\(([^)]*MaxRemeasureTries[^)]*)\)", body)
    assert m, "重测上限没有出现在 if 条件里（写上但没判 = 等于没有上限）"
    cond = m.group(1)
    assert "_remeasureTries" in cond and "<" in cond, (
        f"重测上限的判断写成了 `{cond.strip()}` —— 必须形如 `_remeasureTries < MaxRemeasureTries`"
    )
    # 计数器必须真的在加，否则上限等于没有
    assert "_remeasureTries++" in body, "重测计数器从不自增 —— 上限形同虚设"
    # 封顶之后不许再排：必须存在一个 else / 不再排的分支
    i_inc = body.index("_remeasureTries++")
    tail = body[i_inc:]
    assert "RequestRemeasure()" in tail, "自增之后没有再排重测（那重测就只发生一次）"
    # 自增后必须回到"不排"的收尾（else 分支），否则超限还会继续排
    assert re.search(r"else\s+(if|\{)", tail), (
        "封顶后没有兜底分支（超限仍会重排 → 每帧循环）"
    )


def test_remeasure_counter_is_guarded_not_always_true():
    """专门守「判断条件被改成恒真」这种变异。

    11-20 变异验证：把 `if (_remeasureTries < MaxRemeasureTries)` 改成 `if (true)`，
    原断言仍然全绿（字符串还在），但运行期上限已经消失。这条把它钉死。
    """
    body = _method(_code(VIEWS), "private void ApplyBubbleWidth()")
    # 不许出现恒真的重测条件（`if (true)` 紧跟着自增的那一段）
    for pat in (r"if\s*\(\s*true\s*\)\s*\{[^}]*_remeasureTries\+\+",
                r"if\s*\(\s*1\s*\)\s*\{[^}]*_remeasureTries\+\+"):
        assert not re.search(pat, body, re.S), (
            "重测条件被写成了恒真（`if (true)` / `if (1)`）—— 上限形同虚设"
        )


def test_pump_is_wired_to_the_resident_ticker():
    """重测队列必须挂在常驻帧回调上，且键集中在 `FrameTicker`（号段不许分裂）。"""
    views = _code(VIEWS)
    assert "FrameTicker.Ensure" in views, "重测队列没挂到 FrameTicker（气泡不是组件，挂不上 Update）"
    assert "FrameTicker.KBubbleWidth" in views, "重测队列用了写死的键号（号段必须集中在 FrameTicker）"
    ticker = _code(TICKER)
    assert "KBubbleWidth" in ticker, "FrameTicker 里没有 KBubbleWidth 常量"

    pump = _method(views, "private static void Pump()")
    # ★ "队列空 ⇒ 收摊"必须就是**第一处**判空的做法 ★
    #
    #   11-20 变异验证踩了三次才钉对：
    #     ① 只断言"出现过 FrameTicker.Stop("（Pump 里另有一处 catch 兜底也有 Stop）⇒
    #        删掉正常收摊那处仍全绿；
    #     ② 正则允许中间跳过 `{ return; }` ⇒ 变异体被后半句的 Stop 兜住，假绿；
    #     ③ 用 `any(...)` 遍历所有判空处 ⇒ 变异体 `{ return; }` 与正常收摊两处**同时**
    #        存在（前者是把 Stop 那行改坏的，后者还在），any 只要有就过 —— 又是假绿。
    #   最终判据：**第一处**判空（进入队列处理前的那一个）就必须带 Stop。它才是"空了就
    #   别再来"的那一句；后面的判空是"处理完顺手收摊"，删掉它不影响空转。
    empty_guards = re.findall(r"if\s*\(\s*_queue\.Count\s*==\s*0\s*\)\s*([^\n]*)", pump)
    assert empty_guards, "Pump 里没有「队列空」的判断"
    assert "FrameTicker.Stop(" in empty_guards[0], (
        "队列空了没有收摊（常驻空转）—— 第一处判空必须直接调用 FrameTicker.Stop(Key)"
        f"（现为：{empty_guards[0].strip()!r}）"
    )


# ══════════════════════════════════════════════════════════════════
# ③ 判据本身在核里（接线只许"问"，不许自己再写一份算式）
# ══════════════════════════════════════════════════════════════════

def test_gate_logic_lives_in_the_core():
    """`ShouldWrite` 的判据只在 `BubbleWidthCore`，接线里不许重复实现。"""
    core = _code(CORE)
    b = _method(core, "public static bool ShouldWrite(")
    assert "measured <= 0f" in b, "核心判据里没有「量不出就不写」"
    assert "newWidth <= 0f" in b, "核心判据里没有「宽度没算出就不写」"
    # 接线里不许自己再判一遍 measured（那会形成第二份判据）
    views = _method(_code(VIEWS), "private void ApplyBubbleWidth()")
    assert not re.search(r"if\s*\(\s*measured\s*<=\s*0", views), (
        "接线里又自己判了一次 measured <= 0 —— 判据必须只有一份（BubbleWidthCore.ShouldWrite）"
    )


# ══════════════════════════════════════════════════════════════════
# ④ 流式路径：宽度必须跟着文字长（11-20 用户纠正后的补充）
# ══════════════════════════════════════════════════════════════════

WINDOW = CS / "UI" / "ChatWindow.cs"


def test_streaming_flush_recomputes_width():
    """★ 流式每帧刷 Text 时**必须**同步重算宽度 ★

    这是 11-20 那个 bug 的另一半，比非流式那条更要紧：

        BeginNpcBubble("")   ← 起泡时文字是**空串**
          └ ChatBubble 构造 → SetText("") → ApplyBubbleWidth
                ⇒ 量不出（空文字）⇒ 旧代码 w = cap ⇒ 宽度定成上限 1060
        AppendDelta × N      ← 每帧只写 Text.text，**从不重算宽度**
                ⇒ 整段流式期间宽度永远是 1060

    所以流式不是"第一帧错了第二帧会纠正"，而是**根本没有第二次**。
    `LateUpdate` 的合并刷 Text 那一块必须跟一次 `OnStreamTextChanged()`。
    """
    win = _code(WINDOW)
    body = _method(win, "private void LateUpdate()")
    # 定位"合并刷 Text"那一块
    i_dirty = body.index("_deltaDirty && _bubbleText != null")
    i_brace = body.index("{", i_dirty)
    depth, k = 0, i_brace
    while k < len(body):
        if body[k] == "{":
            depth += 1
        elif body[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    block = body[i_brace : k + 1]
    assert "_bubbleText.text =" in block, "没找到流式刷 Text 的那一句（结构变了？）"
    assert "OnStreamTextChanged()" in block, (
        "流式刷 Text 时没有重算宽度 —— 宽度会停在 `BeginNpcBubble(\"\")` 那一刻（空文字量不出）"
        "算出的值上，整段流式都不再变。这正是用户 11-20 截图「短话横到天花板」的成因。"
    )


def test_streaming_view_is_held_and_released():
    """流式视图必须**持有**（才够重算宽度）并**在收口时解引用**。

    不持有 ⇒ `LateUpdate` 只有 `Text` 组件，拿不到 `_bubble` / `_refs`，没法重算。
    不解引用 ⇒ 下一回合拿上一回合的视图去算（静默 no-op，但语义脏）。
    """
    win = _code(WINDOW)
    assert "private ChatBubble _deltaView;" in win, "没有持有流式气泡视图（没法重算宽度）"
    # BeginNpcBubble 里必须赋值
    begin = _method(win, "public void BeginNpcBubble(")
    assert "_deltaView = b;" in begin, "BeginNpcBubble 没有记下视图对象"
    # 收口必须清
    clear = _method(win, "private void ClearStreamingState()")
    assert "_deltaView = null;" in clear, "收口没有解引用流式视图（下一回合会拿到已销毁的）"


def test_final_commit_recomputes_width_too():
    """终稿覆盖流式泡时也要重算宽度（终稿常与流式最后一帧长度不同）。"""
    win = _code(WINDOW)
    body = _method(win, "public void FinishNpcBubble(")
    i_case = body.index("BubbleCommit.OverwriteStreaming")
    seg = body[i_case : body.index("break;", i_case)]
    assert "OnStreamTextChanged()" in seg, "终稿覆盖流式泡后没重算宽度（定稿瞬间宽度可能还是旧值）"


def test_stream_width_entry_goes_through_the_gate():
    """流式宽度入口也必须走同一套判据（`ApplyBubbleWidth`），不许另写一份算式。"""
    views = _code(VIEWS)
    b = _method(views, "internal void OnStreamTextChanged()")
    assert "ApplyBubbleWidth()" in b, "流式入口没有走 ApplyBubbleWidth（会形成第二份算式）"
    # 不许在流式入口里自己写 sizeDelta
    assert "sizeDelta" not in b, "流式入口自己写了 sizeDelta —— 判据/写入必须都只在 ApplyBubbleWidth 里"


# ══════════════════════════════════════════════════════════════════
# ⑤ 群聊溢出：硬闸 + 单聊/群聊各打一条自检（11-20 第二次修复）
# ══════════════════════════════════════════════════════════════════

def test_bubble_never_exceeds_content_width():
    """★ 气泡宽度必须有"不许超出内容区"的硬闸 ★（11-20 群聊溢出）

    `Cap()` 减掉的头像/间距都来自**读数**：回放/换档那一帧读到脏值就会算出偏大的 cap，
    `Fit()` 会忠实照夹 ⇒ 气泡溢出可视区，被 `RectMask2D` 从右侧裁掉
    （用户群聊截图："宽度过宽导致有些都没显示完全"）。

    硬闸的判据**不依赖任何兄弟节点读数**，所以不管上游哪一步算歪都能兜住。
    """
    core = _code(CORE)
    assert "ClampToContent" in core, "核心没有「不许超出内容区」的硬闸"
    b = _method(core, "public static float ClampToContent(")
    assert "contentWidth - safety" in b, "硬闸没按「内容宽 − 安全边距」算限值"
    # 内容宽本身不可信时不许乱夹（交给回退链）
    assert "MinContentWidth" in b, "硬闸没判内容宽是否可信（脏值会被当成限值乱夹）"

    views = _method(_code(VIEWS), "private void ApplyBubbleWidth()")
    assert "ClampToContent(" in views, (
        "接线里没有调用硬闸 —— 群聊气泡仍可能溢出内容区被裁掉"
    )
    # 硬闸必须在写 sizeDelta 之前
    assert views.index("ClampToContent(") < views.index("sd.x = w;"), \
        "硬闸判在写 sizeDelta 之后 —— 已经写出去了，夹了也没用"


def test_diag_is_logged_once_per_mode():
    """自检行必须**单聊与群聊各一条**（原先只有进程级一条，群聊数值永远不进日志）。

    11-20 实测：日志里只有一条 `气泡盒=UserBubble`（单聊），群聊那屏一个数都没有，
    于是"群聊溢出"只能靠用户截图肉眼发现。
    """
    views = _code(VIEWS)
    assert "_diagLoggedRoom" in views, "没有群聊专用的自检标志（会与单聊共用一条）"
    assert "ShouldLogDiag()" in views, "没有统一的「这次要不要打自检」判据"
    b = _method(views, "private bool ShouldLogDiag()")
    assert "_diagLoggedRoom" in b and "_diagLogged" in b, "判据没有区分两种模式"
    # 分类依据必须是构造时的 speaker，不是"头像有没有显示"
    assert "_speakerName" in views, "没有记下 speaker（无法可靠区分单聊/群聊行）"
