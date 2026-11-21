"""「刚进游戏点传按钮没反应、要点好几遍」的契约（09-20 实机事故）。

用户现场
------------------------------------------------------------------------------------
「刚进游戏点击传按钮尝试打开UI但是没反应，需要点好几遍才行。」

根因（**不是闸门太严，是点击被丢弃**）
------------------------------------------------------------------------------------
`ContactPanelOpener.Toggle()` 原实现：`EnsureResident()` 返回 false 就
`ModMain.P(...)` 一行然后 `return` —— **那次点击蒸发，玩家零反馈、无重试**。
`EnsureResident` 有五条 `return false` 路径（Python 未连接 / 世界未就绪 / 剧情窗 /
资源注入失败 / 管理器登记冲突），各闸门的实测窗口与"刚进游戏"完全重叠：
    · Python 冷启中位 **67s**（最长 294s）—— 实测自用户日志的 23 次连接
    · HUD「传」钮自身注入最多 ~5.6s（`RetryDelays = {0.3, 0.8, 1.5, 3.0}`）
    · `MapMain` 晚建、剧情静置窗 300 帧（5~10s）
于是"要点好几遍"不是计数器，而是**"你点的那一下有没有落在闸门翻转之后"**。

修法 = 照搬工程已验证的范式
------------------------------------------------------------------------------------
`NpcInitiativeMonitor.TickPendingOpen`（其注释原话："旧版是'3 帧后只试一次'，失败就退化成
'回复到达时才弹窗'……现改为每帧重试、成功即止、5s 超时"）。对话窗那条路早就解决了同款问题，
**「传」按钮从来没接到它上面**。

语义：**一次点击 = 一次最终会兑现的意图**。

本文件钉死的五件事
------------------------------------------------------------------------------------
  ① 失败必须**挂起**而不是丢弃（一次点击最终能开）；
  ② 重试必须由**常驻**帧回调驱动（面板此刻还不存在，挂在面板上的 tick 跑不到）；
  ③ 失败必须**可见**（载体只能是 HUD 级横幅 —— 面板不存在，面板内提示显示不出来）；
  ④ 五条失败路径都要给出**准确原因**（"等一会儿会好"与"永远不会好"必须能分辨）；
  ⑤ 同一原因不许每帧刷屏，但**放弃时要再提示一次**（"放弃了"是新信息）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENER = ROOT / "csharp/UI/ContactPanelOpener.cs"
PANEL = ROOT / "csharp/UI/AbContactPanel.cs"
HOTKEYS = ROOT / "csharp/UI/AbHotkeys.cs"
MODMAIN = ROOT / "csharp/app/ModMain.cs"
AbPanelProberPath = ROOT / "csharp/UI/AbPanelProber.cs"

OPENER_SRC = OPENER.read_text(encoding="utf-8")
PANEL_SRC = PANEL.read_text(encoding="utf-8")
HOTKEYS_SRC = HOTKEYS.read_text(encoding="utf-8")
MODMAIN_SRC = MODMAIN.read_text(encoding="utf-8")
AbPanelProberSrc = AbPanelProberPath.read_text(encoding="utf-8")


def _body(src: str, signature: str) -> str:
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
    raise AssertionError(f"未找到方法体: {signature}")


def _strip_comments(src: str) -> str:
    return "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("//", "///", "*", "/*"))
    )


# ---------------------------------------------------------------------------
# ① 失败必须挂起，不是丢弃
# ---------------------------------------------------------------------------


def test_toggle_arms_pending_instead_of_dropping_the_click():
    """★核心★ `Toggle()` 失败时必须挂起意图 —— 这是"点一次就够"的全部来源。"""
    body = _strip_comments(_body(OPENER_SRC, "public static void Toggle()"))
    assert "TryOpenNow(toggle: true)" in body, (
        "Toggle 没有先尝试立即打开（且必须带开关语义：直接点击=开着就关）"
    )
    assert "ArmPending()" in body, "Toggle 失败后没有挂起意图（点击被丢弃 = 事故原样）"


def test_old_silent_drop_is_gone():
    """反向钉：不许留"记一行日志就 return"的旧路径。"""
    body = _strip_comments(_body(OPENER_SRC, "private static bool TryOpenNow(bool toggle)"))
    m = re.search(r"if \(!AbContactPanel\.EnsureResident\(\)\)\s*\{(.*?)\}", body, re.S)
    assert m, "找不到 EnsureResident 失败分支"
    branch = m.group(1)
    assert "return false" in branch, "失败分支应返回 false（由 Toggle 挂起），不该自行吞掉"
    assert "ArmPending" not in branch, "挂起应由 Toggle 统一负责，不要在两处各写一遍"


def test_pending_state_exists_and_is_queryable():
    """要有可查询的挂起状态（诊断/测试都靠它）。"""
    assert "_pending" in OPENER_SRC, "没有挂起标志"
    assert "HasPendingOpen" in OPENER_SRC, "挂起状态不可查询"


def test_pending_is_cleared_on_success_and_giveup():
    """成功与放弃都必须清状态，否则残留意图会在下一次莫名其妙弹窗。"""
    assert "_pending = false" in _strip_comments(_body(OPENER_SRC, "public static void ClearPending()"))
    # 超时放弃那条路要清
    tick = _strip_comments(_body(OPENER_SRC, "public static void TickPending()"))
    assert "ClearPending()" in tick, "超时/异常路径没有清挂起状态"


# ---------------------------------------------------------------------------
# ② 重试必须由常驻帧回调驱动
# ---------------------------------------------------------------------------


def test_pending_tick_is_driven_by_the_resident_frame_callback():
    """★核心★ `TickPending` 必须挂在常驻的 `AbHotkeys.OnFrame` 上。

    面板此刻**还不存在**，任何挂在面板上的 tick（`ContactPresenter.Update`）都跑不到
    —— 这正是"待开"这个状态存在的唯一场景。
    """
    body = _body(HOTKEYS_SRC, "public static void OnFrame()")
    assert "ContactPanelOpener.TickPending()" in body, (
        "常驻帧回调没有推进待开重试 —— 挂起的意图永远不会被兑现（等于没修）"
    )


def test_resident_frame_callback_is_registered():
    """光有调用不够 —— `AbHotkeys.OnFrame` 得真的被 `g.timer.Frame` 注册（09-20 事故同款）。"""
    # 11-19 起常驻帧回调统一走 `FrameTicker`（键 `KHotkeys`）—— 判据是"**真的被注册**"，
    # 不是"用哪套 API 注册"。旧写法 `g.timer.Frame(new Action(AbHotkeys.OnFrame))` 已被替换。
    assert re.search(r"FrameTicker\.Ensure\(FrameTicker\.KHotkeys,\s*\"hotkeys\",\s*AbHotkeys\.OnFrame\)", MODMAIN_SRC), (
        "AbHotkeys.OnFrame 没被注册 ⇒ 整段是死代码"
    )


def test_retry_has_a_delay_and_a_deadline():
    """要有首帧延迟 + 超时，避免同帧竞争与无限重试。"""
    body = _strip_comments(_body(OPENER_SRC, "public static void TickPending()"))
    assert "_delayFrames" in body, "没有首帧延迟"
    assert "_deadline" in body and "Time.unscaledTime" in body, "没有超时判据"
    assert "PendingTimeoutSec" in OPENER_SRC, "没有超时常量"


def test_retry_does_not_run_forever():
    """超时必须真的放弃（否则闸门若永不打开，会每帧无休止尝试）。"""
    body = _strip_comments(_body(OPENER_SRC, "public static void TickPending()"))
    assert re.search(r"if \(Time\.unscaledTime >= _deadline\)", body), "没有超时判断"
    m = re.search(r"if \(Time\.unscaledTime >= _deadline\)\s*\{(.*?)\n            \}", body, re.S)
    assert m and "ClearPending()" in m.group(1), "超时后没有放弃"


# ---------------------------------------------------------------------------
# ③ 失败必须可见
# ---------------------------------------------------------------------------


def test_failure_is_visible_not_just_logged():
    """★核心★ 失败必须走**可见**载体，不能只写日志。

    判据必须落在 `HintFailure` 的**代码路径**里（不是整个文件出现某个字符串）：
    否则把唯一的调用点换成日志，测试照样绿 —— 那条变异实测确实漏过一次。
    """
    body = _strip_comments(_body(OPENER_SRC, "private static void HintFailure(bool timeout)"))
    assert "UnreadBanner.ShowSystem" in body, (
        "HintFailure 没有走可见载体 —— 失败只写日志 = 玩家看到「什么都没发生」（事故原样）"
    )


def test_failure_hint_is_actually_reached_on_retry():
    """光有 `HintFailure` 方法不够 —— 重试循环必须真的调它（否则方法是死代码）。"""
    tick = _strip_comments(_body(OPENER_SRC, "public static void TickPending()"))
    assert "HintFailure(" in tick, (
        "重试循环没有调用 HintFailure —— 提示永远显示不出来（09-20 AbHotkeys 死代码同款）"
    )
    toggle = _strip_comments(_body(OPENER_SRC, "public static void Toggle()"))
    arm = _strip_comments(_body(OPENER_SRC, "private static void ArmPending()"))
    assert "HintFailure(" in arm or "HintFailure(" in toggle, (
        "挂起时没有立刻提示 —— 玩家会以为按钮坏了（要等满 5s 超时才看到反馈）"
    )


def test_visible_hint_uses_hud_banner_not_panel_widget():
    """载体必须是 HUD 级横幅 —— 面板此刻不存在，面板内提示显示不出来（旧未读横幅的教训）。"""
    body = _body(OPENER_SRC, "private static void HintFailure(bool timeout)")
    assert "UnreadBanner.ShowSystem" in body, "没有用 HUD 级横幅"
    for panel_widget in ("SetStatus(", "_refs.", "ContactPresenter"):
        assert panel_widget not in body, (
            f"失败提示用了面板内的 {panel_widget} —— 面板不存在时它显示不出来"
        )


def test_banner_budget_is_respected():
    """横幅有 34 字预算（`UnreadBanner.ShowSystem`），文案必须短。

    照抄一句长文案会被静默截断成半句 —— 这里只查**真正会送进横幅的字面量**：
    `hint = ...` 赋值右侧的字符串片段。查整个方法体会把代码本身也匹配进来
    （`re.findall` 跨行会吞掉非字符串内容），那是测试自己的 bug 而非代码的问题。
    """
    src = _strip_comments(_body(OPENER_SRC, "private static void HintFailure(bool timeout)"))
    assigns = re.findall(r"hint\s*=\s*(.+?);", src, re.S)
    assert assigns, "找不到 hint 赋值（文案构造被改写了？）"
    # 34 字预算里还要容下动态拼进来的一段，故静态片段留出余量
    for expr in assigns:
        for lit in re.findall(r'"([^"]*)"', expr):
            assert len(lit) <= 28, (
                f"提示文案片段过长（{len(lit)} 字，加上动态拼接会超 34 字预算）：{lit}"
            )


def test_same_reason_does_not_spam_but_giveup_does_warn():
    """同一原因不重复弹；但"放弃了"是新信息，必须再提示一次。"""
    body = _strip_comments(_body(OPENER_SRC, "private static void HintFailure(bool timeout)"))
    assert "_hintedReason == key" in body, "没有按原因去重（每帧重试会刷屏）"
    assert re.search(r"if \(!timeout && _hintedReason == key\) return;", body), (
        "去重逻辑没有给 timeout 让路 —— 放弃时玩家就不知道已经放弃了"
    )


# ---------------------------------------------------------------------------
# ④ 五条失败路径都要给准确原因
# ---------------------------------------------------------------------------


def test_panel_reports_a_reason_on_every_failure_path():
    """★核心★ `EnsureResident` 的每条 `return false` 都要带原因。

    五条原因该做的事完全不同："智能体尚未连接"是等几秒到一分多钟，
    "界面资源缺失（.ab 未部署？）"是**等一辈子也不会好**。只写日志 = 玩家只能靠猜。
    """
    body = _strip_comments(_body(PANEL_SRC, "public static bool EnsureResident()"))
    bare = re.findall(r"return false;", body)
    assert not bare, (
        f"EnsureResident 里还有 {len(bare)} 条裸 `return false` —— 那些路径不携带原因，"
        "玩家看到的提示会退化成「未知原因」"
    )
    assert body.count("Fail(") >= 5, f"失败路径只覆盖了 {body.count('Fail(')} 条，应有 ≥5 条"


def test_reason_is_cleared_on_success():
    """成功必须清原因，否则下一次成功的提示会带着上一次的失败文案。"""
    body = _strip_comments(_body(PANEL_SRC, "public static bool EnsureResident()"))
    assert body.count("LastFailureReason = null") >= 2, (
        "成功路径没有清 LastFailureReason（复用路径 + OpenUI 路径各需一处）"
    )


def test_reason_reaches_the_player_readably():
    """原因必须是**人话**，并映射到"该等还是该找人"的不同建议。

    09-21 修：AbPanelProber.GateReason() 改为如实报出**是哪一道闸门**
    （游戏日历尚未就绪 / 存档世界尚未加载 / 世界界面尚未就绪 / 剧情窗…），
    不再由 WorldIdentityAvailable 二选一出笼统的「世界尚未加载完成」。
    故这里钉的是**意图**：HintFailure 必须认得那几道闸门的原因原文并翻成人话，
    而不是钉死某一句旧文案 —— 否则文案变准确了，测试反而拦。
    """
    body = _strip_comments(_body(OPENER_SRC, "private static void HintFailure(bool timeout)"))
    assert "智能体尚未连接" in body, "没有把「智能体未连接」翻译成玩家能懂的话"
    # 三道"等一会儿会好"的闸门必须逐一覆盖 —— 少一道，玩家就会看到原始字段当提示
    for reason in ("存档世界尚未加载", "世界界面尚未就绪", "游戏日历尚未就绪"):
        assert f'"{reason}"' in body, f"没有覆盖「{reason}」这一道闸门"
    assert "请稍候" in body, "没有告诉玩家「这是等待问题」"


def test_gate_reasons_match_the_prober_wording():
    """HintFailure 里映射的原因原文必须与 AbPanelProber.GateReason() 产出**逐字一致** ——
    两边各写一份措辞，改一边漏一边，玩家就会看到「暂不能打开传音簿：世界界面尚未就绪」
    这种原始字段冒充人话（文案与事实对不上，09-21 排障被带偏的同一形态）。"""
    prober = _strip_comments(AbPanelProberSrc)
    opener = _strip_comments(_body(OPENER_SRC, "private static void HintFailure(bool timeout)"))
    # GateReason 能产出的每一条固定措辞（剧情窗原因来自 DramaWindowName，是动态句，不在此列）
    for reason in ("游戏日历尚未就绪", "存档世界尚未加载", "世界界面尚未就绪"):
        assert f'"{reason}"' in prober, f"AbPanelProber.GateReason 已不产出「{reason}」，测试口径过期"
        assert f'"{reason}"' in opener, f"HintFailure 没有覆盖「{reason}」（与 Prober 措辞对不上）"


def test_distinguishes_waitable_from_permanent_failures():
    """设计约束：文案必须能区分"等一会儿会好"与"等也不会好"。

    判据：至少两种不同的建议措辞存在（"请稍候" vs 直接陈述原因）。
    """
    body = _strip_comments(_body(OPENER_SRC, "private static void HintFailure(bool timeout)"))
    assert body.count("hint =") >= 3, "文案分支太少，无法区分可等待与永久性失败"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# ⑥ 自查发现的回归（09-20）：兑现挂起意图必须用"打开"，不能用"开关"
# ---------------------------------------------------------------------------


def test_pending_fulfilment_uses_open_semantics_not_toggle():
    """★核心★ 兑现挂起的意图只能"打开"，**已经开着就不许动**。

    事故（自查发现，真实场景）：玩家点「传」→ 闸门未开 → 挂起 → 0.6s 后世界就绪、
    `TickPending` 把面板打开 → 玩家手快又点一下（他以为还卡着）→ 若走开关语义
    就把**刚打开的面板关掉**。用户症状本就是"要点好几遍"，连点是常态 ——
    等于修了重试却引入"一闪就没了"。
    """
    tick = _strip_comments(_body(OPENER_SRC, "public static void TickPending()"))
    assert "TryOpenNow(toggle: false)" in tick, (
        "兑现挂起意图时用了开关语义 —— 会把玩家自己打开的面板误关"
    )


def test_direct_click_still_has_toggle_semantics():
    """反向钉：玩家**直接点击**必须保持开关语义（开着就关）—— 原行为一字不改。"""
    body = _strip_comments(_body(OPENER_SRC, "public static void Toggle()"))
    assert "TryOpenNow(toggle: true)" in body, (
        "直接点击丢了开关语义 —— 面板开着时再点会关不掉（原有行为被改坏）"
    )


def test_open_path_checks_is_open_before_showing():
    """`toggle: false` 分支必须判 `IsOpen` 再决定要不要 `Show()`。"""
    body = _strip_comments(_body(OPENER_SRC, "private static bool TryOpenNow(bool toggle)"))
    assert "inst.IsOpen" in body, "没有判断面板是否已经开着"
    assert re.search(r"else if \(!inst\.IsOpen\)\s*inst\.Show\(\)", body), (
        "没有「已开则不动」的分支 —— 会重复 Show 或误关"
    )
    assert re.search(r"if \(toggle\)\s*inst\.TogglePanel\(\)", body), (
        "开关语义分支不见了"
    )


def test_toggle_decision_is_a_parameter_not_a_duplicate_path():
    """两种语义必须是**同一个方法的一个参数**，不是复制成两份实现。

    复制两份的后果：以后改一处分身、另一处漂移（本工程在"名单与身份分两次写"
    上栽过同款跟头）。
    """
    assert OPENER_SRC.count("private static bool TryOpenNow") == 1, (
        "TryOpenNow 被复制成了多份 —— 两种语义必须由参数区分，否则改一处会漂移"
    )
