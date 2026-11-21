"""通讯录「最近」Tab 行右侧时间契约（10-22 UI 批次：微信式最近互动时间）。

判档规则本身由 C# 单测工程 `csharp/tests/ContactTimeCoreTests/` 钉死；
本文件只钉**接线**（单测全绿 + 一处没接 = 玩家侧一点变化都没有——老教训）：

1. 只在「最近」与「临时」两个 Tab 打（好友 Tab 按字母序，时间与排序无关；群聊 Tab 另走渲染路径）。
   ★ 10-24 扩到「临时」：那一页**按时间倒序**排（最近说过话的在前），不打时间玩家看不出
     这个顺序；而「好友」页仍然是字母序、时间与排序无关，**不许顺手也打上**。
2. 时钟整轮渲染取**一次**（行间不许漂移半秒，同屏两行"刚刚"打架）；
3. Time 节点走**运行时新建**（AB 预制件里没有它；与行内 ✕ 同款路径，两版共用）；
4. 无记录（LastInteract<=0）不打空标签；
5. 打时间时 Name 右边界让位（防长名字压字）；
6. 列表时间是**现实钟**（LastInteract=unix 秒），与聊天窗的游戏历分隔行是两套钟——
   各答各的问题（"多久没理这个人" vs "剧情上这句话是哪天"），不许混用/不许顺手"统一"。
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


def test_recent_tab_only_and_clock_once():
    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private void RebuildRows(List<ContactEntry> rows)")
    # 两个按时间排序的页各打时间；「好友」（字母序）与「群聊」（另一条渲染路径）都不许打。
    # 判据写成"恰好这两个 Tab"而不是"包含 Recent"：只写包含的话，将来有人顺手把
    # `|| _tab == Tab.Friend` 加上去也照样绿 —— 而好友页按字母序，时间在那里纯属噪音。
    m0 = re.search(r"bool showTime = ([^;]+);", body)
    assert m0, "找不到 showTime 的判据"
    expr = m0.group(1)
    assert "Tab.Recent" in expr and "Tab.Temp" in expr, \
        "时间标签必须由「最近」与「临时」两个页驱动（两者都按时间倒序）"
    assert "Tab.Friend" not in expr and "Tab.Group" not in expr, \
        "「好友」（字母序）与「群聊」（另走渲染路径）不许打时间标签"
    m = re.search(r"long nowSec = showTime \? DateTimeOffset\.UtcNow\.ToUnixTimeSeconds\(\) : 0;", body)
    assert m, "时钟必须整轮渲染取一次（挪进循环 = 行间漂移）"
    assert body.index("long nowSec") < body.index("for (int i = 0; i < shown; i++)"), \
        "nowSec 要在行循环之前"


def test_time_wired_with_runtime_created_node():
    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private void RebuildRows(List<ContactEntry> rows)")
    assert "ContactUiBuilder.BuildRowTimeText(clone.transform)" in body, \
        "运行时新建（AB 预制件与代码版共用一条路径，与行内 ✕ 同款）"
    assert 'Find("Time")' not in body and 'FindChild(clone.transform, "Time")' not in body, \
        "Time 不在预制件里，先 Find 会被预制件路径契约判红且永远拿不到——直接建即可"
    assert "ContactTimeCore.Format(nowSec" in body, "文案必须走纯核（内联判档 = 单测钉不住）"
    assert "showTime && e.LastInteract > 0" in body, "无记录不打空标签"
    assert "offsetMax = new Vector2(-166f" in body, "打时间的行 Name 右边界要让位（防压字）"


def test_builder_factory_shape():
    b = _read("UI/ContactUiBuilder.cs")
    body = _method_body(b, "public static Text BuildRowTimeText(Transform row)")
    assert 'MakeText("Time"' in body, "节点名契约：行直接子节点 Time"
    assert "raycastTarget = false" in body, "标签不抢整行按钮的点击"
    assert "TextAnchor.MiddleRight" in body


def test_core_is_pure_and_wall_clock():
    """纯核零 Unity；且用的是现实钟——不许把游戏历换算牵进来。"""
    src = _read("UI/ContactTimeCore.cs")
    assert "using UnityEngine" not in src
    for forbidden in ("CurrentTimeLabel", "CnDate", "roundMonth", "NpcInitiativeMonitor"):
        assert forbidden not in src, f"列表时间混进了游戏历({forbidden})——两套钟各管各的（见文件头）"


def test_no_ai_wording_in_new_time_ui_pieces():
    """新件套同一铁律：玩家可见文案不许出现 AI 字样（本批次加的文案只有中文档名，钉一下）。"""
    for f in ("UI/ContactTimeCore.cs", "UI/ContactUiBuilder.cs", "UI/ContactPresenter.cs"):
        src = _read(f)
        for lit in re.findall(r'"([^"\\\n]*)"', src):
            assert not re.search(r"(?<![A-Za-z])AI(?![A-Za-z])", lit), f"{f} 字面量带 AI：{lit!r}"
