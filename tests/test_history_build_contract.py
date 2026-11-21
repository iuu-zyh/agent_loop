"""历史回放**分帧构建**的接线契约（11-20 立）。

## 这份测试守什么

`csharp/UI/HistoryBuildCore.cs` 的判据已经有单测（`csharp/tests/HistoryBuildCoreTests`，
127 项断言）。但那个工程是 net8.0 控制台程序，**跟模组编译的不是同一套接线** ——
判据全绿、接线一处没接，用户侧一点变化都没有（本仓反复栽在这上面，见
`BubbleWidthCore` 的头注释）。

所以这份测试锁的是**接线**：

  ① `ReplaceHistory` 真的把大回放交给了帧回调，而且帧回调**自己会撤**；
  ② 每帧构建都经 `FrameTicker`（不许裸注册计时器 —— 世界重载后撤不掉，见
     `test_resident_tick_contract.py` 的量值表），且键在号段表里唯一；
  ③ **中途取消**（换 NPC / 关窗 / 又来一次回放）必须把帧回调撤干净；
  ④ 原来排在 `ReplaceHistory` 下一行的那些动作（消费缓冲帧 / 撤渲染闸 / 进删除模式）
     全部**后移到续跑**里 —— 否则分帧模式下它们会在历史还只有几行的时候就跑，
     症状是"关窗期间攒的话被回放擦掉 / 渲染闸永不落位 / 删除遮罩画在空列表上"，
     而且**零日志**。

## 为什么 ④ 要用"源码顺序 + 续跑存在"两条一起钉

只钉"续跑存在"的话，把 `DrainSoloFrames` 留在原地也能过（那就是 bug 本身）；
只钉顺序的话，两条都在续跑里、但续跑根本没被调用也能过。两条一起才等价于
"这些动作排在历史建完之后"。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"
WINDOW = CS / "UI" / "ChatWindow.cs"
PRESENTER = CS / "UI" / "ChatPresenter.cs"
CORE = CS / "UI" / "HistoryBuildCore.cs"
TICKER = CS / "app" / "FrameTicker.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _method_body(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员正文（含花括号）。签名必须唯一。"""
    assert src.count(signature) == 1, f"签名不唯一（{src.count(signature)} 次）：{signature}"
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError("大括号不配对：" + signature)


def _code_only(src: str) -> str:
    """去掉注释 —— 断言"不许出现某实现"时，注释里提到它不算。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//.*", "", src)


# ----------------------------------------------------------------------
# ① 判据核真的存在、真的被调用
# ----------------------------------------------------------------------

def test_threshold_goes_through_the_core():
    """分流判据必须问 `HistoryBuildCore`，不许在窗口里写死一个数字。

    写死的数字在实机上没法调（改一次要重编译整包），而"多少条该分帧"是本 bug
    唯一能靠参数权衡的东西 —— 它得跟单测里的表是同一个来源。
    """
    win = _read(WINDOW)
    assert "HistoryBuildCore.ShouldDefer(" in win, "ReplaceHistory 没有走分帧判据核"
    body = _method_body(win, "public void ReplaceHistory(JArray items)")
    assert "HistoryBuildCore.ShouldDefer(" in body, "分帧判据没在 ReplaceHistory 里"
    # 判据核不许引用任何 Unity 类型（否则 net8.0 单测编不过，等于没有单测）。
    # 只看**代码**：头注释里写"零 Unity 依赖"不算引用。
    core = _code_only(_read(CORE))
    for banned in ("UnityEngine", "GameObject", "Transform", "MonoBehaviour"):
        assert banned not in core, f"判据核引用了 {banned}，单测工程会编不过"


def test_build_loop_asks_the_core_every_frame():
    """每帧的循环必须**每建一条就问一次**判据，而不是"每帧固定 N 条"。

    固定条数在"一堆富文本行"的批次里照样会超预算（一行 5ms × 12 行 = 60ms），
    而那正是本 bug 要修的画面停顿。
    """
    body = _method_body(_read(WINDOW), "private void BuildHistoryBatch()")
    assert "HistoryBuildCore.ShouldStopFrame(" in body, "分帧循环没有问判据核"
    assert "Stopwatch" in body, "分帧循环没有量耗时 —— 判据是耗时预算，没有耗时就等于没判据"
    # 判据必须在**建之前**问（问一次建一条），不能先建一批再回头判
    assert body.index("ShouldStopFrame(") < body.index("ApplyHistoryItem("), \
        "判据必须早于建行 —— 先建完再判等于每帧建完整批，预算失效"


def test_frame_callback_stops_itself_when_done():
    """建完必须撤掉帧回调。

    不撤的话它会**每帧空跑**到下次回放为止（`Once` 去重只免同帧重复，不免下一帧），
    而体检表里 n/帧 会一直增长 —— 与 `test_resident_tick_contract.py` 钉的
    "常驻 tick 必须能被撤掉"是同一类要求。
    """
    win = _read(WINDOW)
    body = _method_body(win, "private void CompleteHistoryBuild()")
    assert "FrameTicker.Stop(FrameTicker.KHistoryBuild)" in body, \
        "构建收尾没有撤掉帧回调（它会每帧空跑到下次回放）"
    # 收尾之后 `_histBuilding` 必须是 false，否则 `IsBuildingHistory` 永远为真
    assert "_histBuilding = false;" in body, "收尾没有复位构建标志"
    # 幂等：两条路径（正常建完 / 异常收尾）都会进来，事件类动作不能发两次
    assert re.search(r"if\s*\(!\s*_histPending\s*\)\s*return;", body), \
        "收尾不幂等 —— 异常路径再来一次会把续跑跑两遍（缓冲帧被消费两次）"


def test_tick_key_is_registered_and_unique():
    """新键必须在号段表里，且不与既有键撞号。

    `Once` 的"本帧只跑一份"是**按键**判的：撞号意味着两个组件共用一格，
    后注册的那条永远不动，而日志里一切正常。
    """
    ticker = _read(TICKER)
    assert "KHistoryBuild" in ticker, "FrameTicker 里没有分帧构建的键"
    m = re.search(r"internal const int KHistoryBuild\s*=\s*(\d+)\s*;", ticker)
    assert m, "KHistoryBuild 不是 const int（号段表要求集中定义）"
    key = int(m.group(1))
    all_keys = [int(x) for x in re.findall(r"internal const int K\w+\s*=\s*(\d+)\s*;", ticker)]
    assert all_keys.count(key) == 1, f"键 {key} 被两个常量共用"


def test_registration_passes_owner():
    """实例方法的帧回调必须把实例作为 `owner` 传进去。

    不传的话，世界重载后 `Ensure` 看到"句柄在、还活着"就不重注册 ⇒ 回调永远跑在
    被 `Destroy` 掉的旧窗口上。后果是**历史再也建不完**（而日志只显示"转分帧构建"，
    没有下半句）——见 `test_resident_tick_contract.py` 里那段 owner 说明。
    """
    body = _method_body(_read(WINDOW), "private void BuildHistoryBatch()")
    ensure = [l for l in body.splitlines() if "FrameTicker.Ensure(" in l]
    assert ensure, "帧回调没有经 FrameTicker 注册"
    assert any(re.search(r",\s*this\s*\)", l) for l in ensure), \
        "注册没传 owner=this（世界重载后回调会跑在旧实例上，历史永远建不完）"


# ----------------------------------------------------------------------
# ② 中途取消：把帧回调撤干净
# ----------------------------------------------------------------------

def test_cancel_is_wired_into_reset_content():
    """`ResetContent` 必须先撤掉在飞的构建。

    它正拿着**上一份** items 的游标往这个 Content 里塞行，而 `ResetContent` 下面
    会把 `_rows` 清空 ⇒ 行注册表与界面永久错位（删除模式选中的行与实际气泡对不上），
    且**不报错**。
    """
    body = _method_body(_read(WINDOW), "private void ResetContent()")
    assert "CancelHistoryBuild(" in body, \
        "ResetContent 没撤掉在飞的构建 —— 换人/关窗后旧回放会继续往新内容里塞行"
    # 必须早于清表（`_rows.Clear()` / ExitSelecting 都在下面）
    cancel = body.index("CancelHistoryBuild(")
    for later in ("ExitSelecting()", "_rowCount = 0", "_rows.Clear()"):
        if later in body:
            assert cancel < body.index(later), f"取消必须早于 {later}（晚了就是在已清空的表上补行）"


def test_cancel_drops_the_pending_continuation():
    """取消时那段续跑必须**丢弃**，不许补跑。

    它绑的是被取消的那一次回放：拿旧 npc 去消费缓冲帧、撤一个已经不属于它的闸
    ⇒ 新的那次回放会少画一段（静默）。
    """
    body = _method_body(_read(WINDOW), "private void CancelHistoryBuild(string why)")
    assert "_histDone = null;" in body, "取消时没有丢弃续跑 —— 它会拿旧 npc 去消费新窗口的帧"
    assert "FrameTicker.Stop(FrameTicker.KHistoryBuild)" in body, "取消时没有撤掉帧回调"
    assert "_histPending = false;" in body, "取消时没有复位 pending 标志"


# ----------------------------------------------------------------------
# ③ 后续动作全部后移（本文件最要紧的一节）
# ----------------------------------------------------------------------

def _assert_after_continuation(path: Path, signature: str, must_be_inside: list[str],
                               must_stay_outside: list[str]) -> None:
    body = _method_body(_read(path), signature)
    replay_at = body.index("ReplaceHistory(")
    cont_at = body.index("AfterHistoryBuilt(")
    assert replay_at < cont_at, f"{signature}：续跑必须登记在 ReplaceHistory 之后"
    # 续跑块的边界：从 AfterHistoryBuilt( 起按括号配平取实参
    k = body.index("(", cont_at)
    depth = 0
    end = k
    for i in range(k, len(body)):
        if body[i] == "(":
            depth += 1
        elif body[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    inner = body[cont_at:end]
    for needle in must_be_inside:
        assert needle in inner, f"{signature}：`{needle}` 没进续跑 —— 分帧下它会跑在历史还很少的时候"
    for needle in must_stay_outside:
        # 只允许出现在 `ReplaceHistory` 与续跑之间之外的位置；这里查它在续跑之外的正文里
        outside = body[:cont_at] + body[end:]
        if needle in outside:
            # 允许 `ReplaceHistory` 之前的前置（例如 OpenForRoom），只要不在"回放之后、续跑之外"
            between = body[replay_at:cont_at]
            assert needle not in between, \
                f"{signature}：`{needle}` 留在 ReplaceHistory 与续跑之间 —— 分帧下时序错"


def test_solo_replay_actions_moved_after_build():
    """单聊：消费缓冲帧 / 撤渲染闸 / 进删除模式全部进续跑。

    时序（原本就有的硬要求）：`ReplaceHistory` → `DrainSoloFrames` → `ClearReplayGate`
    → `EnterSelecting`。分帧之后这一串必须整体后移，否则：
      · 先消费 ⇒ 被随后建的行顶掉/擦掉（"正在说的那半截"反而更没了）；
      · 先落闸 ⇒ 闸门期间到达的帧直接走直播，重演时画第二遍；
      · 先进选择 ⇒ 遮罩画在只有几行的列表上，剩下的行建出来都没有选择键。
    """
    _assert_after_continuation(
        PRESENTER,
        "private void SendOpenChatReplay(string npc)",
        must_be_inside=["DrainSoloFrames(npc)", "ClearReplayGate(", "EnterSelecting()"],
        must_stay_outside=["DrainSoloFrames(npc)"],
    )
    body = _method_body(_read(PRESENTER), "private void SendOpenChatReplay(string npc)")
    # 只看**回放之后**那一段。前面还有两类 `ClearReplayGate` 是**出口**，本来就该在回放之前：
    #   · 方法开头 `ClearReplayGate("未连接（请求未发）")` —— 不撤这个窗口会永久静默；
    #   · 回调里四五个早退（窗口已关 / 回执失败 / 无 data / NPC 已切 / 忙）——
    #     那些路径压根没有回放，落闸正是它们该做的事。
    from_replay = body[body.index("ReplaceHistory("):]
    inner_at = from_replay.index("AfterHistoryBuilt(")
    assert from_replay.index("DrainSoloFrames(npc)") > inner_at, "消费必须在续跑里"
    assert from_replay.index("ClearReplayGate(") > inner_at, "落闸必须在续跑里"
    # 续跑内部三者的相对顺序不变（这条是原契约，分帧不许打乱）
    assert from_replay.index("DrainSoloFrames(npc)") < from_replay.index("ClearReplayGate("), \
        "消费必须早于落闸（反过来闸门期间的帧会被重演画两遍）"


def test_room_replay_actions_moved_after_build():
    """群聊：首屏统计 / 清说话人 / 消费缓冲帧 / 进选择模式全部进续跑。

    额外一条群聊独有的：`SetBusy(false)` 必须**早于** `DrainRoomFrames`
    （未闭合轮的重放会 `SetBusy(true)`，反过来写就把"还在说"这个真状态抹掉了）。
    """
    _assert_after_continuation(
        PRESENTER,
        "public bool ApplyOpenRoom(JObject resp)",
        must_be_inside=["DrainRoomFrames(roomId, data[\"items\"] as JArray)",
                        "SetStats(data)", "SetBusy(false)"],
        must_stay_outside=["DrainRoomFrames(roomId, data[\"items\"] as JArray)"],
    )
    body = _method_body(_read(PRESENTER), "public bool ApplyOpenRoom(JObject resp)")
    inner_at = body.index("AfterHistoryBuilt(")
    assert body.index("DrainRoomFrames(roomId, data[\"items\"] as JArray)") > inner_at, "消费必须在续跑里"
    assert body.index("SetBusy(false)") < body.index("DrainRoomFrames(roomId, data[\"items\"] as JArray)"), \
        "SetBusy(false) 必须早于消费（未闭合轮的重放会 SetBusy(true)，反了就抹掉真状态）"


def test_delete_notices_moved_after_build():
    """删完的提示行必须等历史建完再插 —— 它在列表末尾，先插会被后建的行顶走。

    症状是"删成功了但屏幕上一个字都没说"，玩家会以为没生效再点一次。
    """
    pres = _read(PRESENTER)
    for sig, needle in (
        ("private void OnDeleteConfirmed(List<int> turns", "已删除 "),
        ("private void OnRoomDeleteConfirmed(List<string> uids", "条群记录"),
    ):
        body = _method_body(pres, sig)
        assert "AfterHistoryBuilt(" in body, f"{sig}：删完的提示没有等历史建完"
        inner_at = body.index("AfterHistoryBuilt(")
        assert body.index(needle) > inner_at, \
            f"{sig}：提示文案没有进续跑（分帧下它会被后建的行顶到列表中间）"


def test_no_replace_history_caller_forgets_the_continuation():
    """四个调用点全部登记续跑 —— 漏一个就是那个窗口静默地少一段。

    四个点：单聊开窗回执 / 群聊开窗回执 / 单聊删完 / 群聊删完。
    任何一处只调 `ReplaceHistory` 就接着做后续动作，在大历史下时序就是错的。
    """
    pres = _read(PRESENTER)
    sites = [m.start() for m in re.finditer(r"\.ReplaceHistory\(", pres)]
    assert len(sites) == 4, f"ReplaceHistory 调用点变成 {len(sites)} 个（预期 4）—— 新增的那处要一起改"
    for at in sites:
        # 调用点之后 400 字符内必须出现续跑登记（本仓那四处都在紧邻几行内）
        window = pres[at:at + 400]
        assert "AfterHistoryBuilt(" in window, \
            "有个 ReplaceHistory 调用点没登记续跑 —— 分帧下它的后续动时序是错的"


# ----------------------------------------------------------------------
# ④ 构建期间的直播帧：必须暂存，不许丢也不许插进历史中间
# ----------------------------------------------------------------------

def test_live_frames_are_held_while_building():
    """构建期间的直播帧必须**暂存**，既不能照画也不能丢。

    · 照画 → 新消息插进"还没建完的历史"中间，屏幕上凭空出现在旧消息之间，
      而且越早到的越靠中间（历史是一批批往下建的），建完之后永久错位；
    · 丢掉 → **群聊会真丢**：tee 的群分支有 `IsShowingRoom ⇒ return`
      （窗口正显示这个群时不缓冲），没有任何一方会把它补回来 —— 那句话永远不出现。

    这两条都是"看起来只是少一句/顺序差一点"的静默故障，所以钉在源码上。
    """
    pres = _read(PRESENTER)
    body = _method_body(pres, "private void OnUiEvent(JObject payload)")
    assert "IsBuildingHistory" in body, "OnUiEvent 没看构建状态 —— 构建期间直播帧会直接画进去"
    assert "_heldDuringBuild.Add(" in body, "构建期间的帧没有暂存（群聊那条路丢了就找不回来）"
    # ★ 判据必须是"构建中就暂存"，不许按事件类型枚举 ★
    #   枚举必然漏：`room_state` 的 error/end 分支会 AppendSystemNotice 建行、speak 分支改
    #   流式状态，而它**没有 kind 字段**，任何按 kind 过滤的判据都把它漏在外面。
    hold = body[body.index("if (_window.IsBuildingHistory)"):body.index("switch (ev0)")]
    for banned in ("IsPlayableStepKind", "IsSoloBuffered", 'ev0 == "'):
        assert banned not in hold, \
            f"暂存判据里出现了 `{banned}` —— 按事件类型枚举会漏掉 room_state/compact_summary"
    # 暂存必须**早于**分发（否则已经画出来了）
    assert body.index("_heldDuringBuild.Add(") < body.index("switch (ev0)"), \
        "暂存必须早于分发 —— 晚了就是先画再存"
    # 有上限，防无界
    assert "HeldDuringBuildMax" in body, "暂存表没有上限（构建卡住时会一直涨）"


def test_held_frames_are_flushed_after_build():
    """暂存的帧必须有**补画**出口，且排在 drain 之后。

    排在 drain 之后是对的次序：队列里那些是"关窗期间"的，暂存这些是"构建期间"的，
    后者更新。反过来的话屏幕上会出现"先看到最新一句，再看到更早的几句"。
    """
    pres = _read(PRESENTER)
    flush = _method_body(pres, "private void FlushHeldDuringBuild()")
    assert "_heldDuringBuild.Clear()" in flush, "补画没有清表 —— 同一批帧会被补画两遍"
    assert "DispatchUiEvent(" in flush, "补画没有走分发"
    # 分发必须绕过守卫（那批帧已经过过一次守卫了）
    disp = _method_body(pres, "private void DispatchUiEvent(JObject payload)")
    for guard in ("IsBuildingHistory", "_heldDuringBuild", "ArmReplayGate"):
        assert guard not in disp, f"DispatchUiEvent 里不该有守卫（{guard}）—— 补画会被再吞一次"

    for sig, drain in (
        ("private void SendOpenChatReplay(string npc)", "DrainSoloFrames(npc)"),
        ("public bool ApplyOpenRoom(JObject resp)", 'DrainRoomFrames(roomId, data["items"] as JArray)'),
    ):
        body = _method_body(pres, sig)
        assert "FlushHeldDuringBuild()" in body, f"{sig}：建完之后没有补画暂存帧"
        assert body.index(drain) < body.index("FlushHeldDuringBuild()"), \
            f"{sig}：补画必须晚于 drain（暂存的帧更新）"


def test_held_frames_are_dropped_on_window_switch():
    """换人 / 关窗必须把暂存表清掉。

    不清的话：A 窗构建期间暂存的帧，会补画到 B 窗（"把别人的话挂到这个人头上"）。
    分帧构建本身会随 `ResetContent` 被取消，但暂存表**没有别的出口**会清它。
    """
    pres = _read(PRESENTER)
    for sig in ("private void OnCloseClicked()", "public void OpenForNpc(string npcId)"):
        body = _method_body(pres, sig)
        assert "_heldDuringBuild.Clear()" in body, \
            f"{sig}：没清暂存表 —— 上一个窗口的帧会补画到下一个窗口"

def test_close_cancels_build():
    """关窗必须中止在飞的构建。

    `Close()` **不走** `ResetContent`（它只是把根节点 SetActive(false)），
    所以它是取消链路上唯一的漏口：构建会继续往隐藏节点里塞行，跑完还会执行那段续跑
    ——续跑里有 `EnterSelecting` / `DrainFrames`，对着一个已关闭的窗口做，
    症状是"下次开窗直接停在选择模式"或"莫名多出几行"，而且零日志。
    """
    body = _method_body(_read(WINDOW), "public void Close()")
    assert "CancelHistoryBuild(" in body, "Close 没中止分帧构建 —— 它会往隐藏节点里继续建，跑完还执行续跑"
