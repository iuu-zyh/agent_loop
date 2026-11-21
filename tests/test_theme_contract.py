"""深色主题共用件契约（10-22 主题统一）：跨面板色值单一来源 = `UI/Theme.cs`。

背景：对话与设置两个深色面板历史上各抄一份色板、连变量名都撞车（两边都有 `TextDim`，
值恰好一样）。"恰好一样"是漂移的前夜——下次只改一处，两面板同屏就分家。本契约把
共用色收进 Theme 并钉死**提取时的原值**：

1. Theme 六个共用件的值 = 2026-10-22 从两个 builder 原样提取的浮点数（改值必须过脑子）；
2. 两个深色 builder 的这六个语义色必须**引用 Theme**，不再自带字面量（回到各抄一份判红）；
3. 通讯录**不**在收编范围（白主题是刻意对标参考图的设计，不许被"统一"顺手毁掉）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp" / "UI"

#: 六个共用件的**局部常量名**（两个深色 builder 里都叫这个名）→ Theme 成员名
SHARED = {
    "PanelBg": "DarkPanelBg",
    "CtrlBg": "DarkCtrlBg",
    "CtrlBgActive": "DarkCtrlBgActive",
    "TextLight": "DarkTextLight",
    "TextDim": "DarkTextDim",
    "TextOk": "DarkTextOk",
}

# 提取时的原值（一个字节不许动；要动就是"全局改色"决策，连本契约一起改）
PINNED = {
    "DarkPanelBg": "new Color(0.043f, 0.043f, 0.075f, 0.95f)",
    "DarkCtrlBg": "new Color(0.16f, 0.18f, 0.26f, 1f)",
    "DarkCtrlBgActive": "new Color(0.24f, 0.27f, 0.38f, 1f)",
    "DarkTextLight": "new Color(0.94f, 0.95f, 0.98f, 1f)",
    "DarkTextDim": "new Color(0.60f, 0.62f, 0.70f, 1f)",
    "DarkTextOk": "new Color(0.55f, 0.85f, 0.60f, 1f)",
}


def _read(name: str) -> str:
    return (CS / name).read_text(encoding="utf-8")


def test_theme_values_pinned():
    src = _read("Theme.cs")
    for name, val in PINNED.items():
        assert re.search(rf"{name}\s*=\s*{re.escape(val)}\s*;", src), \
            f"Theme.{name} 的值漂了（应为 {val}）——全局改色请连本契约一起改"


def test_dark_builders_reference_theme_not_own_literals():
    """两个深色 builder 里这六个语义色的定义必须是 `= Theme.DarkX;`。"""
    for fname in ("ChatUiBuilder.cs", "ConfigUiBuilder.cs"):
        src = _read(fname)
        for local, member in SHARED.items():
            m = re.search(
                rf"private static readonly Color {local} = ([^;]+);", src)
            if m is None:
                continue                      # 该面板本来不用这支色（合法）
            rhs = m.group(1)
            assert f"Theme.{member}" in rhs, \
                f"{fname} 的 {local} 又自带字面量了（回到各抄一份 = 漂移前夜）：{rhs}"


def test_contact_light_theme_stays_own_by_design():
    """通讯录白主题是刻意设计（对标参考图），不许被"统一"顺手收编。"""
    src = _read("ContactUiBuilder.cs")
    assert "PageBg = new Color(0.961f, 0.969f, 0.973f, 1f)" in src, \
        "通讯录浅色页底被改走了深色主题——白主题是设计意图，不是漂移"
    assert "Theme.DarkPanelBg" not in src


#: 宣纸浅色主题的七个值（10-23 须知面板立的第二套）。口径 = docs/release/ui-skin-prompts.md §2。
PAPER = {
    "PaperPageBg": "new Color(0.961f, 0.945f, 0.894f, 1f)",
    "PaperCardBg": "new Color(0.996f, 0.988f, 0.969f, 1f)",
    "PaperHairline": "new Color(0.918f, 0.898f, 0.847f, 1f)",
    "PaperInk": "new Color(0.227f, 0.212f, 0.180f, 1f)",
    "PaperInkDim": "new Color(0.541f, 0.522f, 0.471f, 1f)",
    "PaperAccent": "new Color(0.298f, 0.651f, 0.612f, 1f)",
    "PaperKeycapBg": "new Color(1f, 0.992f, 0.969f, 1f)",
}


def test_paper_values_pinned():
    """宣纸浅色主题的值钉死（改值 = 全局改色决策，连本契约一起改）。"""
    src = _read("Theme.cs")
    for name, val in PAPER.items():
        assert re.search(rf"{name}\s*=\s*{re.escape(val)}\s*;", src), \
            f"Theme.{name} 的值漂了（应为 {val}）"


def test_paper_and_dark_coexist_deliberately():
    """两套色**同时存在**：深色给对话/设置，宣纸给须知。

    这一条防的是"以后有人觉得两套色多余，顺手把须知的纸色改成深色"——
    须知是整页长文，深底白字读三段就发晕；且设置窗预制件的底图本来就是宣纸，
    须知从设置窗打开，用同一套纸色才连贯。
    """
    src = _read("Theme.cs")
    assert "DarkPanelBg" in src and "PaperPageBg" in src, "两套主题必须并存"
    panel = (ROOT / "csharp" / "UI" / "NoticePanel.cs").read_text(encoding="utf-8")
    assert "Theme.PaperPageBg" in panel or "Theme.PaperCardBg" in panel, \
        "须知面板没有用宣纸主题"
    assert "Theme.DarkPanelBg" not in panel, \
        "须知面板混进了深色主题（两套色混用会让「纸底深卡」像贴错了图）"
