"""微信式时间分隔行契约（10-22）：气泡不带日期后，日期改由**居中灰小字分隔行**呈现。

用户需求原话："实际消息可以带有日期，但是显示出来不要带有……当前所有的对话在回放里面
是没有时间区分的，我需要像微信那样，会显示时间（小字显示），粒度按游戏来（年月日），
同年同月只显示日，不同月则显示月和日，都不同再全部显示。"

本文件锁住三条**静默失效**的接线（判档规则本身由 C# 单测工程
`csharp/tests/TimeSepCoreTests/` 钉死，这里只钉"接没接、接在哪"）：

1. **只由玩家行渲染口驱动**：分隔行逻辑必须在 `ChatWindow.AppendUserMessage` 里、
   且插在气泡**之前**（AppendDivider 先于 new ChatBubble）。接到别处（比如 ChatPresenter
   各调用点）= 三个渲染出口各接一份，必漏一个 → "实时有日期行、重开窗口没了"。
2. **NPC 行不驱动**：账本里 NPC 文本没有日期，驱动了就是"实时能画、回放画不出"的
   两副面孔（本仓铁律）。
3. **状态随内容重建清零**：`ResetContent` 里必须复位 `_sepHas`，否则重放沿用上一会话
   的"上一条日期"，第一条的分隔行会被判成"同天不打"。

以及一条防漂移：`StripLeadStamp`（剥）与 `TryParseLeadStamp`（认）必须共用同一个扫描器
`MatchLeadStamp` —— 两处各写一份解析，"剥得掉却认不出"（分隔行消失）或反过来
"认得出却剥不掉"（日期骑在气泡上）都会静默发生。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


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


# ---------------------------------------------------------------- 1. 玩家行渲染口

def test_separator_wired_at_user_row_chokepoint():
    src = _read("UI/ChatWindow.cs")
    body = _method_body(src, "public RowMeta AppendUserMessage(string text)")
    assert "BubbleTextCore.TryParseLeadStamp(text" in body, \
        "玩家行渲染口没有解析行首戳（分隔行整条链路断头）"
    assert "TimeSepCore.Label(_sepHas" in body, "没走判档核心（判档逻辑被内联 = 单测钉不住）"
    assert "AppendDivider(sep)" in body, "判出档位却没插分隔行"
    # 顺序：分隔行必须**先于气泡**（Content 是 VerticalLayoutGroup，后插的排下面）
    assert body.index("AppendDivider(sep)") < body.index("new ChatBubble("), \
        "分隔行画在了玩家泡下边（微信式=时间在上、消息在下）"
    # 剥戳仍在（10-22 前半场：气泡不带日期）
    assert "BubbleTextCore.StripLeadStamp(text)" in body


def test_npc_rows_do_not_drive_separator():
    """NPC 泡/行动行不驱动：账本里它们没有日期，驱动了就是实时/回放两副面孔。"""
    src = _read("UI/ChatWindow.cs")
    for sig in ("public RowMeta AppendNpcStatic(", "public void AppendActionRow("):
        idx = src.find(sig)
        assert idx >= 0, f"锚点没了：{sig}"
        body = _method_body(src, sig)
        assert "TimeSepCore" not in body and "TryParseLeadStamp" not in body, \
            f"{sig} 里出现了分隔行驱动（NPC 侧无日期源，禁止）"


def test_presenter_does_not_duplicate_separator_logic():
    """ChatPresenter 不许自己判档/插行（渲染口已负责；两处判 = 两套规则）。"""
    src = _read("UI/ChatPresenter.cs")
    assert "TimeSepCore" not in src, "判档逻辑漏到调用方了"
    assert not re.search(r'AppendDivider\("[^"]*(?:日|月|年)', src), \
        "ChatPresenter 手插了日期样分隔行（该由渲染口统一驱动）"


# ---------------------------------------------------------------- 3. 状态复位

def test_reset_content_clears_separator_state():
    src = _read("UI/ChatWindow.cs")
    body = _method_body(src, "private void ResetContent()")
    assert "_sepHas = false" in body, \
        "内容重建不清分隔行状态 → 重放第一条沿用旧会话日期，档位判错"


# ---------------------------------------------------------------- 防漂移：一把尺

def test_strip_and_parse_share_one_scanner():
    """剥（StripLeadStamp）与认（TryParseLeadStamp）必须都走 MatchLeadStamp（单源）。"""
    src = _read("UI/BubbleTextCore.cs")
    strip = _method_body(src, "public static string StripLeadStamp(string s)")
    parse = _method_body(src, "public static bool TryParseLeadStamp(string s,")
    assert "MatchLeadStamp(s, out" in strip and "MatchLeadStamp(s, out" in parse, \
        "剥/认没共用扫描器（各写一份解析 = 下一次「剥得掉认不出」漂移的种子）"
    match = _method_body(src, "private static int MatchLeadStamp(string s,")
    assert "'日'" in match and "'年'" in match and "'月'" in match, \
        "扫描器必须认 年/月/日(可选) 全形（与 Python _TIME_STAMP_RE 镜像，见 test_game_time_stamp）"


def test_time_sep_core_is_pure():
    """判档核零 Unity（能编进 net8.0 单测工程的前提）。"""
    src = _read("UI/TimeSepCore.cs")
    assert "using UnityEngine" not in src and "UnityEngine." not in src
    assert "internal static class TimeSepCore" in src
