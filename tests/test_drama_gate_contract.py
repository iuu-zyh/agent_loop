"""「剧情窗判据必须共用、300 帧静置窗不得回归」的契约（09-21）。

事故背景
------------------------------------------------------------------------------------
同一件事 ——「此刻有没有剧情窗挡着」—— 在两处各写了一套判据，且互不相同：

  · `NpcInitiativeMonitor.BusyReason`：判「可见 + 名字含 Drama 的家族」—— 这才是对的
    （实机日志实证：游戏自己的剧情窗叫 `UIDramaDialogue` / `UIDramaBase`，
    `UICustomDramaDyn` 全程 0 次 —— 按单一硬编码名查我方确认窗，根本认不出游戏剧情窗）；
  · `AbPanelProber`（面板创建闸门 ④）：按名查 `UICustomDramaDyn` 且**判存在** ——
    那是我方自制确认窗（`ShowDramaService` 用游戏剧情类造的），而管理器 `CloseUI`
    不一定销毁实例 ⇒ ① 认不出游戏自己的剧情窗；② 把"刚关掉、未销毁的确认窗"
    也算剧情中（"本局第一次同格互动"极易命中）。

为了补 ① 的缺口，又加了「`OpenMapDrama` 被调后 300 帧内一律算剧情中」的时间兜底（⑤）。
实机日志推翻了它的前提：`OpenMapDrama` 是读档期的任务/条件**批量检查器**
（每次进世界 35 次，栈上全是"检测破碎祭坛 / 处理夸父之忧任务 / 检测通关成就…"），
被调 ≠ 有剧情窗要开 ⇒ **每次进世界都被静置 300 帧**：玩家点「传」的横幅永远写着
"剧情/界面切换中"，场上却一个剧情窗都没有。

修法（本文件钉死的八件事）
------------------------------------------------------------------------------------
  ① 剧情窗判据**唯一实现**在 `NpcInitiativeMonitor.DramaWindowName()`（两族窗口）；
  ② 面板闸门（`AbPanelProber.GateReason`）里**不许有任何剧情判据**（09-21 二次修正，见下）；
  ③ 忙判据（`NpcInitiativeMonitor.BusyReason`）只许调共用判据，不许内联第二套；
  ④ 判**可见**不判存在 —— `CloseUI` 不销毁实例，判存在会把刚关的窗也算剧情中；
  ⑤ `UICustomDramaDyn` 不得再以"闸门判据"的形态出现（判据实现与诊断探针除外）；
  ⑥ 「300 帧静置窗」的四个标识符零残留；`DramaActivityHook` 降级为只记录不闸门；
  ⑦ 三个 Ab*Panel 的创建闸都必须走 `AbPanelProber.GateReason()`（不许各写各的）；
  ⑧ `DramaWindowName` 的两种动态措辞必须被 `ContactPanelOpener.HintFailure` 认得
    （翻译成人话，不许让原始字段冒充玩家提示 —— 09-21 排障被带偏的同一形态）。

★ ② 在同一天被推翻（09-21 二次修正）：面板闸门里的那道 ④ 整条删除 ★
------------------------------------------------------------------------------------
上面这套整改把 ④ 接上共用判据之后，剧情窗上那颗「AI 对话」按钮当天就点不动了：
玩家点它的那一刻剧情窗**必然**在显示，于是四条
`[AbChatPanel] 当前不宜创建（剧情窗开着（DramaDialogue））`。

而 ④ 在面板闸门上声称要防的那件事，**正是 09-10 事后复盘里第 2 号已证伪假说**
（`docs/archive/2026-09-10-世界输入冻结.md:172`：「OpenUI 撞上剧情窗展示 →
闸门后仍复现，证伪」—— 加了这道闸，输入冻结照样发生；定案双根因是 UI 生命周期违约
与立绘 API 误用）。也就是说它从来没保护过任何东西。

"两套判据分叉"那个教训没有作废，作废的是"面板闸门该有这道判据"：
  · `BusyReason` 问的是"玩家正忙吗，能不能**打断**他" → 剧情中不该打断，成立；
  · `GateReason` 问的是"能不能**建一个面板宿主**"，而它的调用方全是玩家亲手点开的
    （NPC 面板「AI 对话」/ 通讯录行 / 剧情窗按钮 / 对话窗 ⚙）→ 没有"打断"可言，不成立。
真正需要"剧情中别打扰玩家"的第三个调用方（`BrainLink` 的拉起失败横幅 —— 系统自己弹的）
改由它**自己**调 `IsDramaWindowShowing()`，不再冒充"世界就绪"。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSHARP = ROOT / "csharp"
MONITOR = CSHARP / "npc" / "NpcInitiativeMonitor.cs"
# ★10-25 第二步★ 状态判据整段搬到 `state/`（`NpcInitiativeMonitor` 只留一行转发）。
# 本文件钉的是"**判据只有一份、且判可见不判存在**"，那些实现现在住在这两个文件里；
# 断言盯新址，并额外要求那边的转发必须是一行直通（防止判据回流成第二份）。
FACTS = CSHARP / "state" / "WorldStateFacts.cs"
CORE = CSHARP / "state" / "WorldStateCore.cs"
PROBER = CSHARP / "UI" / "AbPanelProber.cs"
HOOK = CSHARP / "drama" / "DramaActivityHook.cs"
OPENER = CSHARP / "UI" / "ContactPanelOpener.cs"
CONTACT = CSHARP / "UI" / "AbContactPanel.cs"
CHAT = CSHARP / "UI" / "AbChatPanel.cs"
CONFIG = CSHARP / "UI" / "AbConfigPanel.cs"
BRAINLINK = CSHARP / "bridge" / "BrainLink.cs"

MONITOR_SRC = MONITOR.read_text(encoding="utf-8")
FACTS_SRC = FACTS.read_text(encoding="utf-8")
CORE_SRC = CORE.read_text(encoding="utf-8")
PROBER_SRC = PROBER.read_text(encoding="utf-8")
HOOK_SRC = HOOK.read_text(encoding="utf-8")
OPENER_SRC = OPENER.read_text(encoding="utf-8")
CONTACT_SRC = CONTACT.read_text(encoding="utf-8")
CHAT_SRC = CHAT.read_text(encoding="utf-8")
CONFIG_SRC = CONFIG.read_text(encoding="utf-8")
BRAINLINK_SRC = BRAINLINK.read_text(encoding="utf-8")

# 旧静置窗（⑤）的四个标识符：任何一处以**代码**形态重现即视为回归
QUIET_WINDOW_IDS = (
    "NotifyDramaOpening",
    "DramaQuietFramesNeeded",
    "EvalGate",
    "_dramaOpenedFrame",
)

# 代码里按名查我方确认窗的合法去处（判据实现 / 诊断探针）；
# 其余任何文件按名查它都应视为"判存在闸门"借尸还魂
OWN_DRAMA_NAME_REFS = re.compile(r"DramaUiOwnName|DramaUiProbeName|UICustomDramaDyn")


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
    """剥掉整行注释（//、///、块注释行）。行尾注释靠"该出现点全是整行注释"的现状绕开；
    若未来把判据改写成行尾注释紧跟代码的形态，下面的断言会失败 —— 那也是想要的。"""
    return "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("//", "///", "*", "/*"))
    )


def _cs_sources() -> list[tuple[Path, str]]:
    """全部 C# 源码（去注释后），排除 bin/obj 生成物。"""
    out = []
    for p in sorted(CSHARP.rglob("*.cs")):
        rel = p.relative_to(CSHARP).as_posix()
        if rel.startswith(("bin/", "obj/")) or "/bin/" in rel or "/obj/" in rel:
            continue
        out.append((p, _strip_comments(p.read_text(encoding="utf-8"))))
    return out


# ---------------------------------------------------------------------------
# ① 判据唯一实现
# ---------------------------------------------------------------------------


def test_drama_criterion_is_implemented_exactly_once():
    """★核心★ 剧情窗判据只能有一份实现：`NpcInitiativeMonitor.DramaWindowName`。

    事故的起点就是"两处各写一套、判据还不一致"—— 同一时刻面板闸门说"剧情窗挡着"、
    忙判据却说"玩家不忙"。实现再写第二份，两边结论还会再次分叉。
    """
    assert MONITOR_SRC.count("internal static string DramaWindowName()") == 1, (
        "DramaWindowName 出现了多个定义 —— 判据必须唯一实现"
    )
    # ★10-25 第二步加强★ 真实现现在在纯逻辑核里（`WorldStateCore.DramaWindowReason`），
    # 全仓**只许一份**；`NpcInitiativeMonitor.DramaWindowName` 必须只是它的转发。
    # 这两条合起来才等价于搬走之前那句"判据唯一实现"—— 只钉转发会让"另写一份实现"漏网。
    hits = [p.name for p in CSHARP.rglob("*.cs")
            if "state/WorldStateCore.cs" not in p.as_posix()
            and "/bin/" not in p.as_posix() and "/obj/" not in p.as_posix()
            and "public static string DramaWindowReason(bool ownDramaWindowVisible" in p.read_text(encoding="utf-8")]
    assert not hits, f"`DramaWindowReason` 的真实现在这些文件里也有一份：{hits} —— 判据必须唯一实现"
    assert CORE_SRC.count("public static string DramaWindowReason(bool ownDramaWindowVisible") == 1, \
        "纯逻辑核里 DramaWindowReason 必须恰好一份"
    assert re.search(r"internal static string DramaWindowName\(\)\s*=>\s*WorldStateFacts\.DramaWindowReason\(\);",
                     MONITOR_SRC), "NpcInitiativeMonitor.DramaWindowName 必须是转发（不许再放实现）"
    # 布尔形态必须是它的直通包装，不许另写一套判"有没有剧情窗"
    assert "internal static bool IsDramaWindowShowing() => DramaWindowName().Length > 0;" in MONITOR_SRC, (
        "IsDramaWindowShowing 不再是 DramaWindowName 的布尔形态 —— 有人绕开唯一实现另写了一套"
    )


# ---------------------------------------------------------------------------
# ② 面板闸门必须调共用判据
# ---------------------------------------------------------------------------


def test_panel_gate_has_no_drama_criterion_at_all():
    """★核心★ `GateReason` 只剩三条**世界就绪**判据，体内不许出现任何剧情判据。

    09-21 那道 ④ 把玩家亲手点的「对话」按钮拦死了（点它时剧情窗必然在显示），而它声称
    防的"OpenUI 撞剧情窗"是 09-10 复盘里的**已证伪**假说。删了不得再加回来 ——
    无论是共用判据、还是按名查我方确认窗的旧形态。
    """
    body = _strip_comments(_body(PROBER_SRC, "public static string GateReason()"))
    for gone in ("DramaWindowName", "IsDramaWindowShowing", "UICustomDramaDyn",
                 "DramaUiOwnName", "DramaUiProbeName", '"Drama"'):
        assert gone not in body, (
            f"面板闸门里又出现了剧情判据（{gone}）—— 那正是「玩家点了对话按钮却报"
            "『剧情窗开着，不宜创建』」的那道 ④；它防的事已被 09-10 复盘证伪，"
            "见本文件文件头"
        )
    # 三条世界就绪判据一条都不许跟着掉（09-10 那次输入冻结的真因就在这一层）
    assert "NpcInitiativeMonitor.CurrentGameDay()" in body, "① 日历判据丢了"
    assert "playerUnit" in body, "② 存档世界判据丢了"
    assert "UIType.MapMain" in body, "③ 世界 HUD 判据丢了"


def test_banner_carries_its_own_drama_check():
    """`BrainLink` 的失败横幅是**系统自己弹**的 → "剧情中别打断"这条诉求归它自己。

    它原先跟着面板闸门的 ④ 走（顺路拿到的），④ 删了不等于诉求消失；判据仍是共用的那一份，
    只是搬到调用方，不再冒充"世界就绪"。
    """
    assert "AbPanelProber.CanCreateNow()" in BRAINLINK_SRC, "世界就绪判据还在（不许顺手删）"
    assert "NpcInitiativeMonitor.IsDramaWindowShowing()" in BRAINLINK_SRC, (
        "横幅的剧情闸丢了：拉起失败时正演剧情，会凭空盖一层横幅在剧情窗上"
    )


def test_busy_gate_uses_the_shared_criterion_not_its_own():
    """★核心★ `BusyReason` 的剧情窗分支同样只许调共用判据，
    不许把"家族扫描"再内联抄一遍 —— 抄本必然漂移。"""
    # 10-25 第二步：`BusyReason` 的**组合**搬到了纯逻辑核（可单测），
    # 它必须调共用的 `DramaWindowReason`，而不是自己再判一遍。
    body = _strip_comments(_body(CORE_SRC, "public static string BusyReason(ref WorldStateSnapshot sn)"))
    assert "DramaWindowReason(" in body, (
        "忙判据没有调共用剧情判据 —— 玩家忙不忙与能不能建面板会用两把尺子"
    )
    assert "UICustomDramaDyn" not in body, "忙判据里又在按名查我方确认窗（应交给唯一实现）"
    assert 'IndexOf("Drama"' not in body, (
        "忙判据里内联了第二遍家族扫描 —— 家族匹配只属于 DramaWindowReason，抄本必然漂移"
    )


# ---------------------------------------------------------------------------
# ③ 判可见不判存在
# ---------------------------------------------------------------------------


def test_criterion_judges_visibility_not_existence():
    """★核心★ 两族窗口都必须**判可见**。

    `GetUI` 返回的是已创建实例，`CloseUI` 不一定销毁 —— 判存在会把
    "刚关掉、未销毁的确认窗"也算剧情中（"本局第一次同格互动"极易命中的旧坑）。

    10-21 segrec 零分配改造：层顶的可见性判定**上移到共用的 `TopLayerState`**（名字族判据的
    唯一实现搬家处）。本测试随迁：dyn 支保持原形态；层顶支改钉"走 `TopLayerState`"，并要求
    该助手**末尾真读** `UiVisible(top)`（可见性逐帧真读、不被缓存短路 —— 缓存的只是名字
    字符串；另一半由 `test_segrec_zero_alloc_contract` A 组锁死）。语义不放松。
    """
    body = _strip_comments(_body(FACTS_SRC, "internal static bool OwnDramaWindowVisible()"))
    assert "UiVisible(" in body, (
        "我方确认窗（dyn）没有判可见 —— 判存在会让刚关掉的确认窗继续拦人"
    )
    assert "dyn != null" not in body, "出现了 `dyn != null` —— 这是「判存在」旧判据的形态"
    assert "TopLayerState(out" in _strip_comments(_body(FACTS_SRC, "internal static string UiLayerTopName()")), \
        "家族支不再走层顶共用判据（唯一实现被绕开）"
    helper = _strip_comments(_body(FACTS_SRC, "internal static bool TopLayerState(out string name)"))
    assert "return UiVisible(top);" in helper, (
        "层顶可见性没有逐帧真读 —— 改造后它搬进了 TopLayerState，丢了它就等于层顶支判存在"
    )
    assert body.count("UiVisible(") + helper.count("UiVisible(") >= 2, \
        "两族窗口都应各有一次可见性判定（dyn 在判据处、层顶在助手段）"


def test_criterion_covers_both_drama_window_families():
    """判据必须**缺一不可**地覆盖两族窗口：
    ① 我方自制确认窗（按常量名查）＋ ② 游戏剧情窗家族（层顶名字含 Drama）。"""
    facts = _strip_comments(FACTS_SRC)
    assert "OwnDramaUiName" in facts and "UICustomDramaDyn" in facts, \
        "没有覆盖我方自制确认窗（赠送/偷窃/结缘确认都是它）"
    body = _strip_comments(_body(CORE_SRC, "public static string DramaWindowReason(bool ownDramaWindowVisible, string uiLayerTopName)"))
    assert 'IndexOf("Drama"' in body, (
        "没有按名字家族认游戏剧情窗 —— 硬编码单名认不出 UIDramaDialogue/UIDramaBase（实机 13/5 次）"
    )


# ---------------------------------------------------------------------------
# ④ 静置窗零残留 + 钩子只记录
# ---------------------------------------------------------------------------


def test_quiet_window_identifiers_are_gone_everywhere():
    """★核心★ 「300 帧静置窗」不许回归：四个旧标识符（去注释后）全仓 0 残留。

    它的前提已被实机日志证伪（OpenMapDrama = 读档期任务批量检查器，每次进世界 35 次），
    任何一处以代码形态重现，都会把"每次进世界静置 300 帧、横幅谎报剧情中"带回来。
    """
    for path, src in _cs_sources():
        hits = [ident for ident in QUIET_WINDOW_IDS if ident in src]
        assert not hits, f"{path.relative_to(ROOT)} 里仍有静置窗残留：{hits}"


def test_drama_activity_hook_records_and_never_gates():
    """★核心★ `DramaActivityHook` 只做记录（每 35 次一行），不许再参与任何闸门。

    它当年唯一的用途就是给静置窗记帧号；静置窗删了它就没有正确用途。
    若有人再把它接回闸门，"OpenMapDrama 被调 = 剧情中"的错前提就会跟着回来。
    """
    postfix = _strip_comments(_body(HOOK_SRC, "internal static void Postfix()"))
    assert "_calls++" in postfix and "ModMain.P(" in postfix, (
        "钩子不再记录被调次数 —— 保留它的唯一理由（核对'是任务扫描不是剧情窗'）没了"
    )
    for banned in ("GateReason", "CanCreateNow", "AbPanelProber", "DramaWindowName", "OpenUI"):
        assert banned not in postfix, f"DramaActivityHook.Postfix 里出现了 {banned} —— 它只许记录"

    # 全仓只允许 ModMain（挂载/撤销）引用它
    for path, src in _cs_sources():
        if "DramaActivityHook" in src:
            assert path.name in {"DramaActivityHook.cs", "ModMain.cs"}, (
                f"{path.name} 引用了 DramaActivityHook —— 钩子只许被挂载，不许被当闸门用"
            )


# ---------------------------------------------------------------------------
# ⑤ 三个面板的创建闸必须统一走 GateReason
# ---------------------------------------------------------------------------


def test_every_panel_gate_routes_through_gate_reason():
    """★核心★ 通讯录 / 对话窗 / 配置面板的懒创建都必须调 `AbPanelProber.GateReason()`。

    面板各写各的判据 = 同一个"能不能 OpenUI"的问题给出三个不同答案（09-21 事故形态）。
    """
    contact = _strip_comments(_body(CONTACT_SRC, "public static bool EnsureResident()"))
    assert "AbPanelProber.GateReason()" in contact, "通讯录创建闸没有走统一判据"
    assert "return Fail(gateWhy);" in contact, (
        "通讯录被拦时没有把闸门原因如实写进 LastFailureReason —— "
        "玩家横幅会退化成「未知原因」（原因与事实对不上 = 09-21 排障被带偏的根）"
    )

    chat = _strip_comments(_body(CHAT_SRC, "public static bool EnsureResident(WsClient ws)"))
    assert "AbPanelProber.GateReason()" in chat, "对话窗创建闸没有走统一判据"

    config = _strip_comments(_body(CONFIG_SRC, "public static bool EnsureResident()"))
    assert "AbPanelProber.GateReason()" in config, "配置面板创建闸没有走统一判据"


def test_shared_criterion_has_no_third_caller_and_no_name_probe():
    """判据的调用面收敛到两处（忙判据 + BrainLink 横幅）；面板闸门**不在其中**（09-21 二次修正）。

    按名查我方确认窗只许出现在判据实现里（且判可见）—— 那个"诊断探针"随 ④ 一起删了。
    """
    # BattleOutcome（09-22）：攻击链里"场上有剧情窗开着"是**续期证据**（玩家正在读，链没断，
    # 不该按静默收口）。它要的是同一个问题——"此刻有没有剧情窗挡着"——的同一个答案；
    # 自己再写一套"可见 + 名字含 Drama"就是本文件头钉死的那个病根。
    # 它拿这份判据**只续期、不拦人**：面板创建闸门仍然不许带任何剧情判据（见上两条）。
    #
    # DramaSegmentRecorder（09-22）：剧情相遇分段器的 W 位（"剧情窗还开着吗"）—— 判"关窗了这一瞬
    # 有没有转场接走"，是**观察**不是闸门（它只决定我们自己那一段原文攒到哪儿为止，不拦任何界面）。
    # 与上面那条同理：要的是同一个问题的**同一个答案**，自己再写一套"可见 + 名字含 Drama"
    # 就会与忙判据分叉（最坏：忙判据说"不忙"、分段器说"窗关着"，两边各自做自己的事）。
    #
    # DramaSegmentProbe（09-22）：**定标期的临时采样件**（只记日志、零判定），它的 W 就是
    # 上面那个 W —— 采样的意义正在于"量的是同一个量"。它同样不拦任何人；定标结束删除时，
    # 这一项也一并删掉。
    # WorldStateFacts（10-25 第二步）：判据的**实现地**搬到了这里 —— 它当然"出现"该名字。
    # 注意它不新增调用方：进来的仍是上面那几个，只是它们现在绕过转发直接问事实层。
    allowed_criterion_users = {"NpcInitiativeMonitor.cs", "BrainLink.cs", "BattleOutcome.cs",
                               "DramaSegmentRecorder.cs", "DramaSegmentProbe.cs",
                               "WorldStateFacts.cs"}
    for path, src in _cs_sources():
        if re.search(r"DramaWindowName|IsDramaWindowShowing", src):
            assert path.name in allowed_criterion_users, (
                f"{path.name} 里出现了剧情判据 —— 调用面只许忙判据与拉起失败横幅两处；"
                "第三处各写各的就会再次分叉，而第四处若长在**面板闸门**上，"
                "拦的就是玩家亲手点下去的那颗按钮"
            )

    # WorldStateFacts：按名查我方确认窗的**唯一实现**现在住这儿（`OwnDramaWindowVisible`，
    # 且它判可见）。NpcInitiativeMonitor 只剩转发，不再自己按名查。
    allowed_name_probes = {"NpcInitiativeMonitor.cs", "WorldStateFacts.cs", "WorldStateCore.cs"}
    for path, src in _cs_sources():
        for line in src.splitlines():
            if "GetUI(" in line and OWN_DRAMA_NAME_REFS.search(line):
                assert path.name in allowed_name_probes, (
                    f"{path.name} 在按名查我方确认窗（`{line.strip()}`）—— "
                    "按名查只许出现在判据实现里，且必须判可见"
                )


# ---------------------------------------------------------------------------
# ⑥ 共用判据的动态措辞必须被玩家提示认得
# ---------------------------------------------------------------------------


def test_hint_failure_translates_both_dynamic_drama_wordings():
    """★核心★ `DramaWindowName` 的两种动态输出（"我方确认窗 … 可见" /
    "剧情窗开着（XXX）"）都必须被 `HintFailure` 的 StartsWith 识别。

    漏一种，玩家就会看到「暂不能打开传音簿：剧情窗开着（UIDramaDialogue）」这种
    原始字段冒充的提示 —— 措辞与判据是两份文案，改一边漏一边就会对不上。
    """
    body = _strip_comments(_body(OPENER_SRC, "private static void HintFailure(bool timeout)"))
    assert 'StartsWith("剧情窗开着")' in body, (
        "HintFailure 认不得「剧情窗开着（XXX）」—— 游戏剧情窗拦人时会露原始字段"
    )
    assert 'StartsWith("我方确认窗")' in body, (
        "HintFailure 认不得「我方确认窗 … 可见」—— 我方确认窗拦人时会露原始字段"
    )


# ---------------------------------------------------------------------------
# ⑦ 超时对齐（跨语言）：到底谁能先裁决"这次等待为什么结束"
# ---------------------------------------------------------------------------


def test_slot_ttl_is_strictly_shorter_than_python_rpc_timeout():
    """★核心★ C# 槽位 TTL（无心跳那档）必须**严格小于** Python 的 RPC 超时。

    两侧一样长（曾都是 120s）是一条边界竞争：Python 的 deadline 从**发帧**起算、C# 的从
    `TryDefer` 起算（晚几毫秒）⇒ **永远 Python 先超时**：模型拿到的是「请求超时」（零信息失败，
    于是重试或编），而 C# 几十毫秒后 Resolve 出的「玩家未响应」回来时 future 已被 pop ——
    无人认领、静默丢弃（ws_channel `request()` 注释里点名的"结果丢"形状）。
    留出余量后，"这次等待为什么结束"永远由**能拿到结局的那一侧**先说出口。
    """
    gate = (CSHARP / "drama" / "DramaGate.cs").read_text(encoding="utf-8")
    m = re.search(r"DefaultExpireSeconds\s*=\s*(\d+)", gate)
    assert m, "读不到 DefaultExpireSeconds（常量被搬走或改名了？）"
    ttl = int(m.group(1))
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    rpc = float(cfg["network"]["request_timeout"])
    assert ttl < rpc, (
        f"C# 槽位 TTL（{ttl}s）不小于 Python RPC 超时（{rpc}s）—— 又回到 120 vs 120 那条边界竞争："
        "Python 永远先超时，「玩家未响应」那条唯一正确的结局会因无人认领被静默丢弃"
    )
    print(f"✓ 超时对齐：槽位 TTL {ttl}s < RPC 超时 {rpc}s（结局由 C# 先裁决）")


def test_python_pending_cap_covers_csharp_chain_hard_ceiling():
    """★核心★ Python 的 RPC 绝对上限必须**大于** C# 链的硬顶 + 一个心跳宽限。

    心跳能把 deadline 一直往后推（跨战斗那条链就是要这个），所以 Python 侧另加了一层天花板
    —— 但天花板若低于 C# 自己的硬顶，就会在**正常长等**里抢先掐断（比不加更坏）。
    两个数分居两仓，只能在这里钉住它们的相对大小。
    """
    wsc = (ROOT / "transport" / "ws_channel.py").read_text(encoding="utf-8")
    cap_m = re.search(r"PENDING_TOTAL_CAP\s*=\s*([\d.]+)", wsc)
    grace_m = re.search(r"PENDING_GRACE\s*=\s*([\d.]+)", wsc)
    assert cap_m and grace_m, "读不到 PENDING_TOTAL_CAP / PENDING_GRACE"
    cap, grace = float(cap_m.group(1)), float(grace_m.group(1))
    bo = (CSHARP / "drama" / "BattleOutcome.cs").read_text(encoding="utf-8")
    ceiling_m = re.search(r"HardCeilingSeconds\s*=\s*([\d.]+)f?", bo)
    assert ceiling_m, "读不到 BattleOutcome.HardCeilingSeconds"
    ceiling = float(ceiling_m.group(1))
    assert cap > ceiling + grace, (
        f"绝对上限（{cap}s）未超过 C# 硬顶（{ceiling}s）+ 心跳宽限（{grace}s）—— "
        "会在 C# 还没收口时把正常的长等掐断"
    )
    print(f"✓ 绝对上限 {cap}s > 硬顶 {ceiling}s + 宽限 {grace}s（长等不会被误掐）")


if __name__ == "__main__":
    import pytest
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
