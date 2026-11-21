"""「↓ N条新消息」浮动钮契约（10-22 UI 批次）。

智能滚底只做了一半：上翻历史后新回复**零提示**。补一个浮钮，全部状态挂在既有的
跟随状态机上。本文件钉的是"接线与口径"，视觉件在 builder 只查形状：

1. 计数只在**未跟随且非重放保持期**发生（重放几百行不许灌出假角标）；
2. 重放（ResetContent）清计数并复位跟随；人肉滚回底部（AtBottom 自愈）清计数；
3. 点击复用 `HoldScrollAtBottom`——钉底+保持帧那套时序坑只许有一份实现；
4. 显隐/文案每帧一次刷在 LateUpdate 的**所有早退分支之前**（折叠校正/保持期
   的 return 不许把角标状态冻住）；
5. Builder 不挂回调（本仓分工：视觉在 builder、行为在窗口），默认隐藏。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp" / "UI"


def _read(name: str) -> str:
    return (CS / name).read_text(encoding="utf-8")


def _method_body(src: str, sig: str) -> str:
    body = src[src.index(sig):]
    depth, started = 0, False
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                return body[: i + 1]
    raise AssertionError(f"方法体没闭合：{sig}")


def test_register_row_counts_only_while_unfollowed():
    win = _read("ChatWindow.cs")
    body = _method_body(win, "private RowMeta RegisterRow(GameObject row)")
    assert "if (!_followingBottom && _pinHoldFrames == 0) _newRowsWhileUnfollowed++;" in body, \
        "计数必须同时躲开『跟随中』与『重放保持期』（后者漏躲=开窗顶假角标）"


def test_replay_and_manual_return_clear_the_counter():
    win = _read("ChatWindow.cs")
    reset = _method_body(win, "private void ResetContent()")
    assert "_newRowsWhileUnfollowed = 0;" in reset and "_followingBottom = true;" in reset, \
        "重放要清计数并复位跟随（两件事缺一都出假角标）"
    assert "if (AtBottom()) { _followingBottom = true; _newRowsWhileUnfollowed = 0; }" in win, \
        "人肉滚回底部必须连计数一起清（只恢复跟随不清计数 = 角标挂在屏幕上不走）"


def test_click_reuses_hold_scroll_and_hides_immediately():
    win = _read("ChatWindow.cs")
    body = _method_body(win, "private void ScrollToNewest()")
    assert "HoldScrollAtBottom(2)" in body, "钉底必须复用既有时序件（第二份钉底实现=第二个坑）"
    assert "_newMsgBtn.SetActive(false)" in body, "点击后立刻收角标，不等下一帧"


def test_badge_refresh_runs_before_early_returns():
    win = _read("ChatWindow.cs")
    body = _method_body(win, "private void LateUpdate()")
    i_refresh = body.index("RefreshNewMsgBadge();")
    i_fold = body.index("TickFoldAnchor()")
    assert i_refresh < i_fold, "刷新要排在所有早退分支之前（折叠/保持期的 return 不许冻住角标）"


def test_close_hides_badge():
    win = _read("ChatWindow.cs")
    body = _method_body(win, "public void Close()")
    assert "_newMsgBtn.SetActive(false)" in body, "关窗掩蔽角标（LateUpdate 关窗后不再刷）"


def test_label_wording_tiers():
    win = _read("ChatWindow.cs")
    body = _method_body(win, "private void RefreshNewMsgBadge()")
    assert '"↓ 新消息"' in body and '"↓ 9+条新消息"' in body, "1 条与 9+ 两个特档不许退化成数字直出"


def test_builder_factory_shape():
    b = _read("ChatUiBuilder.cs")
    body = _method_body(b, "public static GameObject MakeNewMsgButton(Transform parent, out Text label)")
    assert 'MakeImage("NewMsgBtn"' in body
    assert "SetActive(false)" in body, "出厂隐藏：显隐权在窗口状态机"
    assert "ClickUtils" not in body and "onClick" not in body, "Builder 不挂回调（分工：行为在窗口）"
