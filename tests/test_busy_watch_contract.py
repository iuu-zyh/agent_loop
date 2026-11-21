"""「玩家忙 / 战斗中」观测的**接线契约**（11-19 立）。

## 报障原话

"有用户反映检测会频繁显示自己正在忙或者正在处于战斗当中，但是实际不是。"

## 为什么这条报障查不出来

判据本身（`WorldStateCore.BusyReason`）没报错、没抛异常，只是结论与玩家看到的画面对不上。
而现有日志只把**结论**在若干事件点顺带打出去，缺三样东西：

  ① **原始电平** —— 看到"战斗中"，分不清是 `world.battle.isBattle` 真为真
     （游戏自己的位卡住）、还是 `UIBattleInfo` 残留、还是判据把异常吞成 false。
     **这几种修法完全相反**：一个只能等/反馈给游戏，一个要改我们的判据；
  ② **边沿** —— 用户说"频繁"，那是抖动；孤立时刻数不出"1 分钟里判了 40 次忙"；
  ③ **持续时长** —— "一直忙"与"每秒忙一下"是两种病。

本文件钉的就是这三样有没有真的接线上去 —— 判据本身的规则由
`csharp/tests/BusyWatchCoreTests`（40 项断言）负责，两边合起来才完整。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//.*", "", src)


# ---------- ① 原始电平必须能被记下来 ----------


def test_battle_level_exposes_raw_reading():
    """战斗位必须能报出**原始形态**，不能只给一个 bool。

    `IsBattleLevel()` 把三种情况压成同一个 false：`world==null`、`battle==null`、
    `isBattle` 真为 false。排查"报战斗中但其实没打"时，这三种的含义完全不同 ——
    尤其要看的是 **`isBattle` 真的是 true，还是我们的判据读错了**。
    """
    facts = _read(CS / "state" / "WorldStateFacts.cs")
    assert "internal static string BattleLevelRaw()" in facts, \
        "没有 BattleLevelRaw —— 报'战斗中'时无法区分是游戏位卡住还是我们读错"
    body = _code_only(facts)
    i = body.index("BattleLevelRaw()")
    seg = body[i:i + 700]
    for must in ("g.world == null", "battle", "isBattle", "读异常"):
        assert must in seg, f"BattleLevelRaw 没有区分「{must}」这一种情况"


def test_layer_reading_exposes_off_reason():
    """Mask / Window 层必须能报出"有节点但不可见"，以及是**哪种**不可见。

    这是误报最可能的形态：游戏把面板设成不可见却留在层顶，或者 Canvas 被关掉但节点还在。
    只报"忙/不忙"永远看不出这一层。
    """
    facts = _read(CS / "state" / "WorldStateFacts.cs")
    assert "internal static string LayerRaw(UILayer layer)" in facts, \
        "没有 LayerRaw —— 层判据不可见的原因（hierarchy / canvas）看不到"
    body = _code_only(facts)
    i = body.index("LayerRaw(UILayer layer)")
    seg = body[i:i + 900]
    for must in ("activeInHierarchy", "activeSelf", "Canvas", "enabled"):
        assert must in seg, f"LayerRaw 没有区分「{must}」这一种不可见"


def test_busy_evidence_bundles_the_deciding_levels():
    """要有**一次拼好**的现场串（判据直接依赖的那几个位），供边沿日志直接带出去。

    为什么不是让每处日志自己拼：那会重演本仓最忌的"同一个忙字多套口径" ——
    各处拼的字段集合不同，两次日志就没法横向比。
    """
    facts = _read(CS / "state" / "WorldStateFacts.cs")
    assert "internal static string BusyEvidenceRaw()" in facts, \
        "没有 BusyEvidenceRaw —— 边沿日志拿不到统一口径的现场"
    body = _code_only(facts)
    i = body.index("BusyEvidenceRaw()")
    seg = body[i:i + 900]
    for must in ("BattleLevelRaw", "BattleUiVisible", "LayerRaw", "ChatWindowVisible"):
        assert must in seg, f"BusyEvidenceRaw 漏了「{must}」（现场串字段不全就没法横向比）"


# ---------- ② 采样必须自己跑，不能只挂在调用点 ----------


def test_busy_watch_is_sampled_on_a_timer_not_only_on_events():
    """`BusyReason` 的调用点是**事件驱动**的（有主动开口/动作收尾才调）。

    "玩家明明不忙却被判忙"可能发生在一段**没有任何事件**的时间里 ——
    只挂在调用点就永远看不到它。所以必须有常驻采样。
    """
    mm = _read(CS / "app" / "ModMain.cs")
    assert "BusyWatch.Sample(" in mm, "BusyWatch.Sample 没有被调用 ⇒ 整套观测是死代码"
    assert "FrameTicker.KBusyWatch" in mm, \
        "BusyWatch 没有走 FrameTicker 的常驻帧回调（重载后会自动失效，与 11-19 那次故障同形）"
    # 必须用 unscaledTime：暂停时 scaled 会停，而"暂停期间一直判忙"正是要抓的形态之一
    i = mm.index("BusyWatch.Sample(")
    seg = mm[i:i + 200]
    assert "unscaledTime" in seg, \
        "采样用的是 scaledTime ⇒ 游戏暂停时采样停摆，暂停期的判据现场直接丢失"


def test_busy_watch_must_be_reset_on_world_change():
    """世界重载必须复位：旧世界"忙了多久"对新世界毫无意义。

    不复位会让出世界后第一行打出「出忙（忙了 900s）」这种假时长，
    而且统计窗口会跨世界累加，"近 60s 进忙 N 次"失去意义。
    """
    mm = _read(CS / "app" / "ModMain.cs")
    assert "BusyWatch.OnWorldChanged()" in mm, \
        "换世界没有复位忙判据观测 —— 会打出跨世界的假时长"


# ---------- ③ 三样输出缺一不可 ----------


def test_all_three_event_kinds_are_logged():
    """进忙/出忙/原因变化/持续忙/统计，五种事件都要有对应文案。

    缺任何一种都会漏掉一类故障形态：
      · 缺"原因变化" ⇒ 看着像同一段忙，其实判据换了一条（那是两个不同 bug）；
      · 缺"持续忙"   ⇒ "卡住不放"看不出来；
      · 缺"统计"     ⇒ 用户说的"频繁"无法量化（只能凭感觉说"很多"）。
    """
    bw = _read(CS / "state" / "BusyWatch.cs")
    for kind in ("BusyEvent.EnterBusy", "BusyEvent.LeaveBusy", "BusyEvent.ReasonChanged",
                 "BusyEvent.StillBusy", "BusyEvent.Stat"):
        assert kind in bw, f"BusyWatch 没有处理 {kind} 的文案"
    # 边沿类日志必须带现场（这是整套观测的意义所在）
    body = _code_only(bw)
    for kind in ("BusyEvent.EnterBusy", "BusyEvent.ReasonChanged", "BusyEvent.StillBusy"):
        i = body.index(kind)
        seg = body[i:i + 400]
        assert "BusyEvidenceRaw()" in seg, f"{kind} 的日志没带原始电平（等于又只记了结论）"


def test_busy_watch_uses_the_single_source_of_truth():
    """必须复用 `WorldStateGate.BusyReason`，绝不另写一套判据。

    本仓有过事故：面板闸门与忙判据各写一套，同一时刻一边说"剧情窗挡着"、
    一边说"玩家不忙"。观测件如果自己算一套，它记的就**不是**产品实际用的那个结论，
    那这份日志再详细也没有意义。
    """
    bw = _code_only(_read(CS / "state" / "BusyWatch.cs"))
    assert "WorldStateGate.BusyReason()" in bw, \
        "BusyWatch 没有复用 WorldStateGate.BusyReason —— 观测到的不是产品实际用的结论"


def test_busy_watch_has_a_kill_switch():
    """要有不需要重新编译的关闭方式（用户机器上万一采样本身出问题）。"""
    bw = _read(CS / "state" / "BusyWatch.cs")
    assert "_diag_no_busy_watch.txt" in bw, "没有关掉观测的哨兵文件（出问题只能重新编译）"
    # 开关走 ModPaths 而不是 DiagSwitches：后者在 #if AB_UI 里，而 state/ 不得依赖 UI 层
    assert "ModPaths.DiagDir" in bw, "哨兵路径没有走 ModPaths.DiagDir（发行版里会写错地方）"


def test_state_boundary_still_holds():
    """加了这个观测件之后，`state/` 的对外依赖仍然可数。

    与 `tests/test_state_module_boundary.py` 同源，这里再钉一次是因为**本类的引入最容易越界**：
    它要打日志（想引 `ModMain`）、要读开关（想引 `DiagSwitches`）、要判"忙"（想引 `DramaGate`）。
    三者都越界 —— 日志走注入、开关走 `ModPaths`、判据走 `WorldStateGate`。
    """
    bw = _code_only(_read(CS / "state" / "BusyWatch.cs"))
    for banned in ("DiagSwitches", "DramaGate.", "ModMain.P"):
        assert banned not in bw, (
            f"BusyWatch 直接引用了 `{banned}` —— state/ 不许反向依赖 drama/UI 层"
            "（日志走注入，开关走 ModPaths，判据走 WorldStateGate）"
        )
