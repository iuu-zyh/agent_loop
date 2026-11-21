"""忙标（「对方正在斟酌…」）的**单一事实来源**契约。

为什么值得单独立一条
--------------------------------------------------------------------------------
忙标此前是"**谁最后碰它谁说了算**"：全工程 15 处 `SetBusy` 各写各的，判断标准互不相同
（玩家动作 / 历史内容 / 超时猜测 / UI 重建 / 群聊阶段）。真机事故（10-17，用户实测）：

    点「同意」→ 开窗点亮忙标 → **几百毫秒后历史回放落地** → `ResetContent` 灭掉它
    → 模型还要跑 15 秒，屏幕上**什么都没显示**（忙标只活了 2 秒）。
    `Player.log` 实证：`忙标 ON` → `历史回放落地` → `忙标 OFF`。

根因不是某一行写错，而是**没有单一事实来源**：`ChatWindow.ResetContent` 把
「内容被重建」当成了「回合已结束」，而这两件事毫无关系。

现行设计（用户拍板）= `TurnRegistry`：**按 NPC 名**记"有没有回合在跑"，
起点只有两个（玩家发消息 / 自主交互玩家点了同意），终点一个（收口帧 `npc_reply`）。
UI 只**读**它，不再**决定**它。

本文件钉的是**接线**（C# 纯逻辑那半由 `csharp/tests/TurnRegistryTests` 覆盖）：
状态机本身对，但接错地方一样白搭 —— 而且错法全是静默的。

★ 这里有一条**最容易踩反**的接线，单独立了断言 ★
    `End` 必须挂在 `ContactDuty`，**不能**挂在 `ChatPresenter.OnReplyEvent`。
    后者有一条 npc 准入过滤（`npcId != _window.CurrentNpcId → DiagDrop`），
    玩家把窗口切到别的 NPC 之后，这个 NPC 的收口帧**根本进不去**
    （真机日志实锤：`★帧被丢弃(npc不匹配 payload=姜萌 面板=江宇文) event=npc_reply npc=姜萌`）。
    写错地方 ⇒ 被切走的那个 NPC **永远停在"在跑"**，切回去是永久忙标 + 删除/压缩入口被锁死。

做法与 `test_tool_group_consistency.py` / `test_drama_segment_contract.py` 同款：
读**源码文本**做结构化断言（跨语言/跨文件的契约都靠这个路子）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"

CONTACT_DUTY = CS / "contacts" / "ContactDuty.cs"
TURN_REGISTRY = CS / "contacts" / "TurnRegistry.cs"
TURN_REGISTRY_CORE = CS / "contacts" / "TurnRegistryCore.cs"
CHAT_WINDOW = CS / "UI" / "ChatWindow.cs"
CHAT_PRESENTER = CS / "UI" / "ChatPresenter.cs"
INITIATIVE = CS / "npc" / "NpcInitiativeMonitor.cs"
MOD_MAIN = CS / "app" / "ModMain.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _body(src: str, decl: str, span: int = 8000) -> str:
    """取某个成员声明的**函数体**：从声明处到下一个同级成员（含其文档注释）为止。

    这类"按结构断言"的测试很脆，所以定位必须精确到函数体，不能在全文里搜 ——
    别处出现同样的字符串就会假绿（10-16 变异验证实测踩过）。
    下一成员的前导 `///` 文档注释也算边界：不切掉它，"下一个方法的注释里提到了
    SetBusy"就会让本方法的断言假红（10-17 实测踩过）。
    """
    i = src.index(decl)
    m = re.search(r"\n        (?:///|\[|private |internal |public |protected )", src[i + len(decl):])
    end = i + len(decl) + (m.start() if m else span)
    return src[i:end]


def _code(body: str) -> str:
    """把 `//` 行注释剥掉，只留代码。

    用途 = "**不许出现 X**" 这类断言：源码注释里为了说明"这里不再调 X"必然要写出 X 的名字，
    直接搜字符串会把**正确的注释**判成违规（10-17 实测踩过）。
    """
    return "\n".join(ln.split("//")[0] for ln in body.splitlines())


# ---------------------------------------------------------------- A. 那个陷阱


def test_turn_end_is_registered_before_every_early_return():
    """★ `TurnRegistry.End` 必须在 `ContactDuty`，且在**一切早退之前** ★

    这是整个设计里最容易写反的一处。`ChatPresenter` 收不到被切走 NPC 的收口帧，
    所以 `End` 只能挂在"能看到所有帧"的那一层；而 `ContactDuty.OnUiEvent` 里有三处早退
    （`!initiative` / `isError` / 各条分流），`End` 必须排在它们**前面** ——
    否则玩家主动对话（`initiative=false`）那条路永远不销账。
    """
    src = _read(CONTACT_DUTY)
    body = _code(_body(src, "private static void OnUiEvent(JObject payload)"))

    assert "TurnRegistry.End(" in body, (
        "ContactDuty 没有登记回合收口 —— 忙标将永远没人销账。"
        "注意：不能改挂在 ChatPresenter.OnReplyEvent（那里会因为 npc 不匹配而丢帧，见本文件头）。"
    )
    i_end = body.index("TurnRegistry.End(")

    # ① 必须**晚于**这两条"还没到销账的时候"的守卫：
    #    前者 = 不是收口帧（没有回合可销）；后者 = 拿不到 NPC 名（不知道给谁销）。
    for gate, why in (
        ('if (ev != "npc_reply") return;', "非收口帧过滤（不是 npc_reply 就没有回合可销）"),
        ("if (npc.Length == 0) return;", "NPC 名为空（不知道给谁销账）"),
    ):
        assert gate in body, f"ContactDuty 的判据形态变了，找不到：{gate}"
        assert body.index(gate) < i_end, (
            f"`TurnRegistry.End` 排在「{why}」**之前** —— 位置不对，应在该守卫之后"
        )

    # ② 必须**早于**这两条真正的分流早退（它们各自的路径同样要销账）
    for gate, why in (
        ("if (!initiative)", "玩家主动对话（initiative=false）在此早退"),
        ("if (isError) return;", "失败兜底帧在此早退"),
    ):
        assert gate in body, f"ContactDuty 的判据形态变了，找不到：{gate}"
        assert i_end < body.index(gate), (
            f"`TurnRegistry.End` 排在「{why}」**之后** —— 那条路径永远不会销账。\n"
            f"  位置要求：紧跟 `ContactStore.MarkLocal(npc, now);`，在所有分流早退之前。"
        )


def test_turn_end_is_not_wired_in_the_presenter():
    """反面钉子：`End` 不许出现在 `ChatPresenter` 里（它看不到被切走 NPC 的帧）。

    这条是"防回退"：将来有人觉得"收口帧当然该在 presenter 处理"，顺手搬过去，
    就会让切走过的 NPC 永久忙 —— 而且**只有切过 NPC 才复现**，极难查。
    """
    src = _read(CHAT_PRESENTER)
    assert "TurnRegistry.End(" not in src, (
        "`ChatPresenter` 里出现了 `TurnRegistry.End(` —— 收口销账必须留在 ContactDuty：\n"
        "  本类的 `OnUiEvent` 有一条 `npcId != _window.CurrentNpcId → DiagDrop` 准入过滤，\n"
        "  玩家切到别的 NPC 后，该 NPC 的 `npc_reply` **根本进不来**（真机日志实锤），\n"
        "  在这里销账 ⇒ 被切走的 NPC 永远停在「在跑」。"
    )


# ---------------------------------------------------------------- B. 内容重建不许碰忙标


def test_reset_content_does_not_touch_the_busy_label():
    """★ `ChatWindow.ResetContent` 不许再写忙标 ★ —— 这就是 10-17 事故的直接成因。

    它把「内容被重建」当成「回合已结束」。而开窗/换 NPC/历史回放**必然**走这里，
    于是"先点亮忙标、再回放历史"的路径（自主交互点同意那条）当场被自灭。
    """
    src = _read(CHAT_WINDOW)
    body = _code(_body(src, "private void ResetContent("))

    assert "SetBusy(" not in body, (
        "`ResetContent` 里又出现了 `SetBusy(...)` —— 内容重建 ≠ 回合结束。\n"
        "  开窗/换 NPC/历史回放都会走这里；写忙标就复现 10-17 事故：\n"
        "  点「同意」→ 忙标亮 → 历史回放落地 → 忙标灭 → 模型跑 15 秒，屏幕全空。\n"
        "  忙态请交给 `TurnRegistry` + `ChatPresenter.RefreshSoloBusy`。"
    )
    assert "SetBusyPhase(" not in body, "`ResetContent` 也不该碰忙标的**文案**（显隐与文案同源，一起归登记表驱动）"


# ---------------------------------------------------------------- C. 单聊读写口


def test_presenter_derives_solo_busy_from_the_registry():
    """单聊忙标必须**读**登记表（`RefreshSoloBusy`），而不是各写各的。"""
    src = _read(CHAT_PRESENTER)
    fn = _code(_body(src, "private void RefreshSoloBusy("))
    assert "TurnRegistry.IsRunning(" in fn, "`RefreshSoloBusy` 没有查登记表 —— 那它就不是单一事实来源"
    assert "_window.IsRoomMode" in fn, (
        "`RefreshSoloBusy` 没有排除群聊 —— 群聊忙态由 `room_state` 阶段驱动，不在登记表里，"
        "混进来会把群聊的忙标打乱"
    )

    # 换 NPC（用户明确要求「切回 A 要恢复」的那一处）：必须是刷新，不是无脑灭
    rebind = _code(_body(src, "public void OpenForNpc(string npcId)"))
    assert "RefreshSoloBusy(" in rebind, (
        "`OpenForNpc` 没有按登记表刷新 —— 切回一个**仍在跑**回合的 NPC 时忙标不会恢复。\n"
        "  这正是用户明确要求的行为：看 A（在跑）→ 切 B → 切回 A ⇒ 忙标要能恢复成 ON。"
    )
    assert "SetBusy(false)" not in rebind, (
        "`OpenForNpc` 里还有无条件的 `SetBusy(false)` —— 会把切回来那个 NPC 的真实忙态抹掉"
    )

    # 回合起点：玩家发消息要先登记再刷新
    assert 'TurnRegistry.Begin(npc, "玩家发消息")' in src, (
        "玩家发消息没有登记回合起点 —— 首 token 之前那段空窗（4~15s）忙标不会亮"
    )


def test_watchdog_checks_the_registry_not_only_the_clock():
    """看门狗要能发现"UI 忙、登记表空闲"这种不一致，而不是只靠超时猜。"""
    src = _read(CHAT_PRESENTER)
    wd = _code(_body(src, "private void BusyWatchdog()"))
    assert "TurnRegistry.IsRunning(" in wd, (
        "看门狗只按超时猜状态，没查登记表 —— 单一事实来源就名不副实：\n"
        "  UI 与登记表不一致时应当**立刻**对齐，而不是干等 BusyStaleSeconds。"
    )
    assert "RefreshSoloBusy(" in wd, "看门狗发现不一致后没有按登记表对齐"


# ---------------------------------------------------------------- D. 自主交互的两个起点


def test_both_initiative_paths_register_the_turn():
    """自主交互的两条发射路径都要登记起点 —— 否则异地传音的忙标永远不亮。

    两条路（同一个 `FireOne` 的两个分支）：
      · 同格/动作 → 玩家在确认窗点「同意」的 onOk 回调里发事件；
      · 异地/忙   → 直发（没有"玩家点击"这个收口点）。
    ⚠ 位置要在 onOk **之内**：确认窗弹着等玩家表态时不算忙（那时还没有回合在跑）。
    """
    src = _read(INITIATIVE)
    fire = _body(src, "private void FireOne(")   # 保留注释：onOk/onNo 的定位靠它们

    n = fire.count("TurnRegistry.Begin(")
    assert n == 2, (
        f"`FireOne` 里 `TurnRegistry.Begin(` 出现 {n} 次，应为 2 次"
        "（同意路径 + 直发路径各一次）"
    )
    # 同意路径那次必须在 onOk 回调之内（晚于 onOk:，早于 onNo:）
    i_ok = fire.index("onOk: () =>")
    i_no = fire.index("onNo: () =>")
    i_first = fire.index("TurnRegistry.Begin(")
    assert i_ok < i_first < i_no, (
        "同意路径的 `Begin` 不在 `onOk` 回调之内 —— 若放在弹确认窗之时，"
        "玩家还在犹豫时忙标就亮了（那时回合根本没开始）"
    )

    # 窗口预开那条路不许再直接写忙标（两处各写各的会让 presenter 字段与标签不一致）
    tick = _code(_body(src, "private void TickPendingOpen()"))
    assert "SetThinking(" not in tick, (
        "`TickPendingOpen` 又直接调 `ChatLauncher.SetThinking` —— 那是绕过登记表直接写窗口标签，\n"
        "  会让 `presenter._busy`（守卫看的）与 `BusyLabel`（玩家看的）不一致，\n"
        "  正是注释里点名过的「实机 bug 的最后一环」。忙标请由 `OpenForNpc → RefreshSoloBusy` 驱动。"
    )


# ---------------------------------------------------------------- E. 生命周期与群聊边界


def test_registry_is_cleared_on_world_change():
    """换存档必须清空：旧世界里"还在跑"的回合在新档里永远不会收口。"""
    src = _read(MOD_MAIN)
    assert "TurnRegistry.ClearAll(" in src, (
        "换存档没有清空回合登记表 —— 旧档里「在跑」的回合收口帧永远不会来，\n"
        "  新档里同名 NPC 的忙标会被**永久点亮**（且删除/压缩入口被误锁）。"
    )


def test_group_chat_busy_paths_are_still_there():
    """**群聊那套不动**：它的忙态由 `room_state` 阶段驱动，自成体系且工作正常。

    这条钉"我没顺手删掉群聊的忙标逻辑"。群聊的 `SetBusy` 调用点：
    `speak` 点名 / `end` 收尾 / 离群 / 群解散 / 群跳过 / 群发消息 / 群窗重放。
    """
    src = _read(CHAT_PRESENTER)
    room_sites = [
        'SetBusy(true);                            // 等 room_state(phase=end) 复位',
        'SetBusy(true, "群点名 " + who);',
    ]
    for site in room_sites:
        assert site in src, (
            f"群聊的忙标调用点不见了：{site!r}\n"
            f"  群聊忙态由 `room_state` 阶段驱动，不在 `TurnRegistry` 里 ——"
            f"不许一并「统一」掉。"
        )
    assert 'SetBusy(false, "回合收口")' in src, (
        "群聊收口的兜底直写不见了（`OnReplyEvent` 的 finally 里那条 `IsRoomMode` 分支）"
    )


# ---------------------------------------------------------------- F. 分层不许破


def test_core_stays_unity_free_so_it_can_be_unit_tested():
    """纯逻辑核必须**零 Unity / 零 ModMain 依赖**，否则链不进单测工程。

    这是本仓的既有分层约定（`UnitIdRegistryCore` / `HotkeyCore` / `ChatFrameQueue` 同款）：
    纯逻辑进 `*Core.cs`（可被 `csharp/tests` 直接链接），Unity 侧只留薄壳。
    """
    core = _code(_read(TURN_REGISTRY_CORE))
    for forbidden in ("UnityEngine", "ModMain", "Unity"):
        assert forbidden not in core, (
            f"`TurnRegistryCore.cs` 出现了 `{forbidden}` —— 它就再也链不进 "
            f"`csharp/tests/TurnRegistryTests` 了（那边是 net8.0、没有 Unity 引用）"
        )
    assert "Func<long>" in core, "核没有可注入的时钟 —— TTL 那类逻辑就没法在单测里推进时间"

    # 薄壳反过来必须真的把日志接上（不然真机排障时查不到"回合开始/结束"）
    shell = _read(TURN_REGISTRY)
    assert "ModMain.P" in shell, "薄壳没接日志 —— 真机上看不到回合起止"
    assert "TurnRegistryCore" in shell, "薄壳没有用核"

    # 单测工程必须真链了核源码（不是副本）
    proj = _read(CS / "tests" / "TurnRegistryTests" / "TurnRegistryTests.csproj")
    assert "TurnRegistryCore.cs" in proj, (
        "单测工程没有链接 `TurnRegistryCore.cs` —— 那些断言测的是别的东西"
    )
