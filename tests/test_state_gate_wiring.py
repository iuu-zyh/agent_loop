"""「状态」模块（`csharp/state/`）的接线与纪律契约（10-25 第二步重构）。

## 这次重构干了什么

原先"此刻能不能做事"的判据散在**两处**，而且同一个"忙"字有**三份互不相同的口径**：

| 旧位置 | 内容 |
|---|---|
| `npc/NpcInitiativeMonitor.cs` 1017~1467（451 行）| 底层读数 + `BusyReason` + `YieldReason` + 一堆原始件 |
| `app/ModMain.cs` 里一个 27 行的 lambda | 排队器的五条件 |

现在分三层：`state/WorldStateFacts`（读数）/ `state/WorldStateCore`（组合，纯逻辑）/
`state/WorldStateGate`（接线）。`NpcInitiativeMonitor` 只留**同名转发**。

## 为什么转发而不是改 73 处引用

① 那些调用问的本来就是"现在什么状态"，语义没变，只是判据换了住址；
② 改 73 处引用是纯风险（其中 5 个文件在禁改清单里），没有收益；
③ 转发函数本身是活文档。

但转发有个**新的失败形态**：有人嫌转发绕，顺手在 `NpcInitiativeMonitor` 里把判据
**重新实现一遍**——那就回到了本次重构要消灭的"三份口径"。所以本文件钉三件事：

  ① 转发必须是**纯一行转发**（函数体里不许出现任何判据逻辑）；
  ② 五条件判据**只许**在 `WorldStateCore.ExecuteBlocker` 里，`ModMain` 里那 27 行 lambda 不许长回来；
  ③ 四个视角的**成员差异**必须显式存在（C# 侧由 `WorldStateCoreTests` 53 项断言钉住，
     这里钉"那句话还在不在"——防止有人把 `YieldReason` 合并进 `BusyReason` 时顺手删注释）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"
STATE = CS / "state"
MONITOR = CS / "npc" / "NpcInitiativeMonitor.cs"
MODMAIN = CS / "app" / "ModMain.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


# ---------------------------------------------------------------------------
# ① 转发必须是纯一行转发
# ---------------------------------------------------------------------------

_FORWARDED = [
    # (签名片段, 转发的目标)
    ("private static string SpeechBlockReason()", "WorldStateGate.SpeechBlockReason()"),
    ("private static string BusyReason()", "WorldStateGate.BusyReason()"),
    ("internal static string BusyReasonText()", "WorldStateGate.BusyReasonText()"),
    ("internal static bool IsPlayerBusy()", "WorldStateGate.IsPlayerBusy()"),
    ("internal static string YieldReason()", "WorldStateGate.YieldReason()"),
    ("internal static bool BattleActive()", "WorldStateFacts.BattleActive()"),
    ("internal static bool BattlePreShowing()", "WorldStateFacts.BattlePreShowing()"),
    ("internal static string DramaWindowName()", "WorldStateFacts.DramaWindowReason()"),
    ("internal static bool ActionShowing()", "WorldStateFacts.ActionShowing()"),
    ("internal static bool ActionActive()", "WorldStateFacts.ActionActive()"),
    ("internal static string LayerTopNameOfUi()", "WorldStateFacts.LayerTopNameOfUi()"),
    ("internal static string LayerSnapshot()", "WorldStateFacts.LayerSnapshot()"),
]


def test_forwarders_are_pure_one_liners():
    """`NpcInitiativeMonitor` 里那些状态入口必须**只剩一行转发**。

    ★这条是本次重构的守卫★ 如果有人在这里重新实现判据（哪怕只是"顺手优化一下顺序"），
    就又有了第二份口径 —— 而两份口径分叉时的表现是"同一时刻一边说忙、一边说不忙"，
    本仓已经踩过一次（`DramaWindowName` 与面板闸门那次）。
    """
    src = _read(MONITOR)
    for sig, target in _FORWARDED:
        # 找到这一行（形如 `... => 目标;`）
        m = re.search(re.escape(sig) + r"\s*=>\s*([^;]+);", src)
        assert m, f"`{sig}` 不再是单表达式转发 —— 判据必须留在 state/，这里只许转发"
        assert target in m.group(1), (
            f"`{sig}` 转发的目标变了（{m.group(1).strip()}）—— 应转发到 `{target}`"
        )


def test_forwarders_did_not_grow_a_body():
    """转发不许长出函数体（带花括号的 `{ ... }` 就说明有人往里塞逻辑了）。

    唯一的例外是 `IsDramaWindowShowing`：它本来就该是 `DramaWindowName` 的布尔形态
    （`test_drama_gate_contract.py` 钉着这条，逐字保留）。
    """
    src = _read(MONITOR)
    for sig, _ in _FORWARDED:
        # 允许 "=> expr;" 形态；若该签名后面跟的是 "{" 就是长出了函数体
        m = re.search(re.escape(sig) + r"\s*(\{|$)", src, re.M)
        assert not m, f"`{sig}` 长出了函数体 —— 判据不许回到 NpcInitiativeMonitor"


def test_drama_window_bool_form_is_preserved():
    """`IsDramaWindowShowing` 必须仍是 `DramaWindowName` 的布尔形态（逐字）。

    这条同时被 `test_drama_gate_contract.py` 钉着（那份是搬走之前写的，故意没改 ——
    它钉的是"唯一实现"这个**意图**，转发之后依然成立）。
    """
    src = _read(MONITOR)
    assert "internal static bool IsDramaWindowShowing() => DramaWindowName().Length > 0;" in src, (
        "IsDramaWindowShowing 不再是 DramaWindowName 的布尔形态 —— 有人绕开唯一实现另写了一套"
    )


def test_ui_visible_forwarder_keeps_signature():
    """`UiVisible` 有外部调用方（`DramaSegmentRecorder` / `DramaSegmentProbe`），签名不许变。"""
    src = _read(MONITOR)
    m = re.search(r"internal static bool UiVisible\(UnityEngine\.MonoBehaviour uiObj\)\s*=>\s*([^;]+);", src)
    assert m, "UiVisible 的转发签名变了（外部两个调用方会编不过）"
    assert "WorldStateFacts.UiVisible" in m.group(1), "UiVisible 没转发到事实层"


# ---------------------------------------------------------------------------
# ② 五条件判据只许有一份
# ---------------------------------------------------------------------------

def test_execute_blocker_lives_only_in_core():
    """排队器的五条件**只许**在 `WorldStateCore.ExecuteBlocker` 里。

    旧形态是 `ModMain.Init` 里一个 27 行 lambda。它的毛病不是长，而是**没法测**：
    `ModMain` 是 net472 + Unity，那个 lambda 在开发机上永远跑不到，
    于是"少一条判据"只能靠真机撞（09-24 那次 90 秒静默超时就是这么来的）。
    """
    core = _read(STATE / "WorldStateCore.cs")
    assert "public static string ExecuteBlocker(ref WorldStateSnapshot sn)" in core, \
        "五条件判据不在 WorldStateCore.ExecuteBlocker 里"
    body = _strip_comments(core)
    i = body.index("public static string ExecuteBlocker")
    seg = body[i:i + 1400]
    for must in ("剧情链收尾中", "战斗中", "另有一个动作在等玩家点选",
                 "玩家正被界面占着（", "不在主世界（世界界面未就绪）"):
        assert must in seg, f"五条件里少了 `{must}`"


def test_modmain_lambda_does_not_grow_back():
    """`ModMain` 的 `WaitBlocker` 必须保持"一行转发"，那 27 行 lambda 不许长回来。"""
    src = _read(MODMAIN)
    m = re.search(r"DeferredActionQueue\.WaitBlocker\s*=\s*\(\)\s*=>\s*\{(.*?)\n                \};",
                  src, re.S)
    assert m, "WaitBlocker 的赋值形态变了（找不到 `() => { ... };`）"
    body = _strip_comments(m.group(1)).strip()
    assert "WorldStateGate.ExecuteBlocker()" in body, "WaitBlocker 没转发到 WorldStateGate"
    assert body.count("\n") <= 2, (
        "WaitBlocker 又长出多行判据了 —— 五条件必须只有一份（WorldStateCore.ExecuteBlocker）"
    )
    # 判据关键词不许在 ModMain 里重新出现（除注释）
    for gone in ("剧情链收尾中", "另有一个动作在等玩家点选", "不在主世界（世界界面未就绪）"):
        assert gone not in _strip_comments(src), (
            f"`ModMain` 里又出现了判据文案 `{gone}` —— 那是第二份口径的起点"
        )


# ---------------------------------------------------------------------------
# ③ 剧情标记必须注入（漏注入 = 那两条判据恒 false，静默变松）
# ---------------------------------------------------------------------------

def test_drama_flags_are_injected():
    """`state/` 不许 import `drama/`（会成环），所以两个剧情标记由 `ModMain` 注入。

    ★漏注入的后果是最坏的那一类★ `ChainHold` / `HasPending` 恒 false ⇒
    排队器会在"链收尾中"就落刀（09-24 那个 90 秒静默超时换一种触发），
    而且**不报错**。所以：既要注入，也要在漏注入时吼一声。
    """
    mm = _read(MODMAIN)
    assert "WorldStateGate.Attach(" in mm, "主工程没注入剧情标记（ChainHold / HasPending）"
    assert "DramaGate.ChainHold" in mm, "没注入 ChainHold"
    assert "DramaGate.HasPending" in mm, "没注入 HasPending"

    gate = _read(STATE / "WorldStateGate.cs")
    assert "InstallReport()" in gate, "没有接线自检（漏注入会静默）"
    assert "未注入" in gate, "漏注入时没有告警路径"
    # 取不到一律当 false（= DramaGate 的空态，行为同改造前），不许当 true
    m = re.search(r"private static bool ReadInjected\(Func<bool> f, string what, ref bool warned\)(.*?)\n        \}",
                  gate, re.S)
    assert m, "ReadInjected 不见了"
    assert "return false;" in m.group(1), "未注入时应当当 false（当 true 会让动作全被卡住）"


def test_gate_does_not_import_drama_types():
    """`WorldStateGate` 只持有 `Func<bool>`，不许出现 `DramaGate` 类型引用（会成环）。"""
    gate = _strip_comments(_read(STATE / "WorldStateGate.cs"))
    for banned in ("DramaGate.", "BattleOutcome.", "DramaSegment"):
        assert banned not in gate, (
            f"`WorldStateGate` 直接引用了 `{banned}` —— 剧情标记必须靠注入，"
            "否则 drama ↔ state 成环（表现为初始化顺序敏感、偶发不生效）"
        )
    assert "Func<bool>" in gate, "注入用的委托没了"


# ---------------------------------------------------------------------------
# ④ 四视角的差异必须显式存在
# ---------------------------------------------------------------------------

def test_four_views_keep_their_documented_differences():
    """四个视角的**成员差异**必须仍在（C# 侧由 53 项断言钉住，这里钉注释与结构）。

    最关键的一条：`YieldReason` **刻意不含**对话窗。合并掉它的后果是
    模型在聊天里发起动作时排队器永远让路，动作被拖到玩家关窗（他可能聊很久）——
    用户 09-24 拍板过一次，别再翻案。
    """
    core = _read(STATE / "WorldStateCore.cs")
    for view in ("SpeechBlock", "BusyReason", "YieldReason", "ExecuteBlocker"):
        assert f"public static string {view}(" in core, f"视角 `{view}` 不见了"

    # Yield 必须走共用最小组合（而不是自己复制一份）
    m = re.search(r"public static string YieldReason\(ref WorldStateSnapshot sn\)(.*?)\n        \}",
                  core, re.S)
    assert m, "YieldReason 不见了"
    assert "GameHoldingReason" in m.group(1), "YieldReason 没走共用组合（复制一份就会分叉）"

    # 那条"刻意不含对话窗"的理由必须还在（删了它就没人知道这是刻意的）
    assert "刻意不含" in core and "对话窗" in core, \
        "「YieldReason 刻意不含对话窗」这条理由被删了 —— 下一个人会把它'补'回去"

    # Busy 必须**单列**战斗界面（不是取或），这条与 BattleActive 刻意不同
    # ⚠ 索引必须在**去掉注释之后**的同一份文本上取（否则偏移对不上，切片是空的）；
    #   而且必须切到**下一个方法之前**为止 —— 切到文件里"下一个 1800 字"会把
    #   ExecuteBlocker 的取或写法带进来，得出"Busy 也用了取或"的假结论（踩过）。
    bare = _strip_comments(core)
    i = bare.index("public static string BusyReason(")
    j = bare.index("public static bool IsPlayerBusy(", i)
    seg = bare[i:j]
    assert "IsBattleLevel" in seg and "BattleUiVisible" in seg, \
        "BusyReason 里战斗位与战斗界面必须各有一条"
    assert "IsBattleLevel ||" not in seg and "BattleUiVisible ||" not in seg, (
        "BusyReason 把两个战斗位合成取或了 —— 它要报的是'哪一位亮的'（排障线索），"
        "取或是 BattleActive（战斗位整体）的语义，两者刻意不同"
    )


def test_execute_blocker_uses_or_for_battle():
    """反过来：`ExecuteBlocker` 的"战斗中"**必须**是取或（它问的是"能不能落刀"）。

    与上一条成对：同一个"战斗"概念，`BusyReason` 单列、`ExecuteBlocker` 取或 ——
    这不是笔误，是两种语义。断言成对写上，免得有人"统一"掉。
    """
    bare = _strip_comments(_read(STATE / "WorldStateCore.cs"))
    i = bare.index("public static string ExecuteBlocker")
    j = bare.index("public static string Describe(", i)
    seg = bare[i:j]
    assert "sn.IsBattleLevel || sn.BattleUiVisible" in seg, \
        "ExecuteBlocker 的'战斗中'不再是取或 —— 结算期 isBattle 已落而界面还在，会提前落刀"


def test_layer_top_blocked_is_not_encoded_as_empty_name():
    """★10-25 复核修正★ 层顶"挡不挡人"不能用"名字是否为空"来编码。

    改造前那两个判据写的是 `if (UiVisible(top)) return "Mask 层有模态遮罩：" + UiName(top)`，
    搬进 `state/` 时一度简化成 `return UiVisible(m) ? UiName(m) : ""`，上层再判
    `!string.IsNullOrEmpty(name)`。两者只在**一个**情形下不同：层顶**可见、但名字读到空** ——
    `UiName` 在"读名抛异常"或"GameObject 名恰好为空"时给 ""，而 `UiVisible` 那时仍可能是
    true（它把"取可见性抛异常"当**保守可见**，宁可拦）。

    也就是说这一处简化的方向是 **fail-open**：模态遮罩层明明挡着，我们却去弹原生窗 ——
    正是本模块存在的理由（那些弹窗会沉进游戏系统队列不出队）。所以"可见"必须能被表达成
    一个**非空**串（占位符），而不是和"不可见"共用同一个空串。
    """
    facts = _read(STATE / "WorldStateFacts.cs")

    # 两个层判据都走同一个助手（免得一边加了占位符、一边忘了）
    for fn in ("MaskLayerName", "WindowLayerName"):
        m = re.search(r"internal static string " + fn + r"\(\)(.*?)\n        \}", facts, re.S)
        assert m, f"{fn} 不见了"
        assert "VisibleLayerName" in m.group(1), \
            f"{fn} 没走 VisibleLayerName（各写一套 ⇒ 迟早一层拦、一层不拦）"

    helper = re.search(r"private static string VisibleLayerName\(UIBase top\)(.*?)\n        \}",
                       facts, re.S)
    assert helper, "VisibleLayerName 不见了"
    h = helper.group(1)
    assert 'UnnamedLayerTop' in h, (
        "\"可见但没名字\"没有占位符 —— 会返回空串，上层据此判\"不挡\"，"
        "把模态遮罩挡着的时刻放行（fail-open）"
    )
    assert "return n.Length > 0 ? n : UnnamedLayerTop;" in h, \
        "占位符没用上（读名成功但为空时必须回落占位符）"

    # 占位符必须真的是非空串
    ph = re.search(r'private const string UnnamedLayerTop = "([^"]+)";', facts)
    assert ph and ph.group(1).strip(), "UnnamedLayerTop 是空串 —— 等于没写"

