"""★ UGUI 构件只有一份（11-19 批次 4 第 1 步）★

## 背景

同一段"建节点 / 拉满 / 设层 / 找子节点"曾在 6 个文件里各写一遍：

| 构件 | 搬家前的份数 | 搬家前的位置 |
|---|---|---|
| `MakeRect` / `NewRect` | 3 + 3（同一个东西两个名字） | 三个 Builder + CompactEditPanel/NoticePanel/ToolPanel |
| `Stretch` / `StretchFill` | 6 逐字相同 + 2 只改名 | 全部 UI 文件 |
| `SetLayerRecursive` | 4 | 三个 Builder + NoticePanel |
| `EnsureEventSystem` | 3 逐字相同 | 三个 Builder |
| `FindGo` / `FindRt` / `FindTr` / `FindByPath` | 3+3+3+1（两套语义） | Ab 三面板 + NoticePanel |
| `AsTransform` / `AsRt` | 3（而 `UiRects.SelfOf` 存在却零调用） | 三个 Builder |

现在全部收进 `csharp/UI/UiKit.cs`，调用方用 `using static` 直接调。

## 为什么必须钉住

这些构件错了**一句日志都没有**：吃不吃射线错 → 按钮点不响；换行策略错 → 撑出窗口；
九宫格边距错 → 四角变形。只能在真机上一个一个点。所以至少要让"不许再长出第二份"这件事
自动可查，否则下一次改动又会散开。

## 本文件不测什么

不测像素。`MakeImage` / `MakeText` / `MakeClickable` 的参数化是第 2~3 步，届时在这里补。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"
UI = CS / "UI"
KIT = UI / "UiKit.cs"

#: 已收进 UiKit 的构件名（在别处出现定义就是回退）
MOVED = ("MakeRect", "NewRect", "Stretch", "StretchFill", "StretchTopBar",
         "SetLayerRecursive", "EnsureEventSystem",
         "FindTr", "FindGo", "FindRt", "FindComp", "FindByPath")

#: 必须走 UiKit 的调用方
CALLERS = ("ChatUiBuilder.cs", "ConfigUiBuilder.cs", "ContactUiBuilder.cs",
           "CompactEditPanel.cs", "NoticePanel.cs", "ToolPanel.cs",
           "AbChatPanel.cs", "AbConfigPanel.cs", "AbContactPanel.cs")


def _code(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"///.*", "", text)
    text = re.sub(r"//.*", "", text)
    return text


def _ui_files():
    return [p for p in UI.rglob("*.cs") if p.name != "UiKit.cs"]


def test_kit_exists():
    assert KIT.is_file(), "UI/UiKit.cs 不见了"


def test_no_second_copy_of_moved_helpers():
    """别处不许再出现这些名字的**定义**（调用不受限）。"""
    bad = []
    for p in _ui_files():
        src = _code(p.read_text(encoding="utf-8"))
        for name in MOVED:
            if re.search(r"static\s+[\w<>\[\]]+\s+" + name + r"\s*\(", src):
                bad.append("%s → %s" % (p.relative_to(CS), name))
    assert not bad, "又出现了 UiKit 的第二份实现：%s" % bad


def test_rect_transform_helpers_are_gone():
    """`AsTransform` / `AsRt` 这类私有转型助手一律换成 `UiRects.SelfOf`。"""
    bad = []
    for p in _ui_files():
        src = _code(p.read_text(encoding="utf-8"))
        if re.search(r"static\s+RectTransform\s+(AsTransform|AsRt)\s*\(", src):
            bad.append(str(p.relative_to(CS)))
    assert not bad, "私有 RectTransform 助手又出现了：%s" % bad


def test_no_duplicate_names_on_call_sites():
    """`NewRect(` / `StretchFill(` 这两个旧名字不许再出现在调用点（统一成 MakeRect / Stretch）。"""
    bad = []
    for p in _ui_files():
        src = _code(p.read_text(encoding="utf-8"))
        for name in ("NewRect", "StretchFill"):
            if re.search(r"(?<![A-Za-z_.])" + name + r"\s*\(", src):
                bad.append("%s → %s(" % (p.relative_to(CS), name))
    assert not bad, "旧名字的调用点没清干净：%s" % bad


def test_every_caller_uses_the_kit():
    for name in CALLERS:
        p = UI / name
        assert p.is_file(), "调用方文件不见了：%s" % name
        src = _code(p.read_text(encoding="utf-8"))
        assert "using static AgentLoopBridge.UiKit;" in src, "%s 没有接入 UiKit" % name


def test_kit_takes_rect_transforms_safely():
    """UiKit 里取 RectTransform 必须走 `UiRects.Of`（`as` 在本环境静默 null）。"""
    src = _code(KIT.read_text(encoding="utf-8"))
    assert "as RectTransform" not in src, "UiKit 里出现了 as RectTransform"
    assert "UiRects.Of(" in src, "UiKit 取 RectTransform 没走 UiRects.Of"


def _member_body(src: str, signature: str) -> str:
    """按大括号配对取一个成员正文（够用即可：这些构件内部没有字符串花括号）。"""
    i = src.index(signature)
    brace = src.index("{", i)
    depth, k = 0, brace
    while k < len(src):
        c = src[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return src[brace:k + 1]


def test_image_and_text_factories_are_delegating_or_unique():
    """`MakeImage` / `MakeText`：**实现只许在 UiKit**；别处只允许"皮肤包装"（转手调 UiKit）。

    皮肤差异是真实的（公告栏要单行+关富文本+行距 1.2；未读横幅要 13 号白字不换行），
    所以允许留包装；不允许的是"再写一遍 AddComponent + 调属性"。
    """
    for sig, kit_sig in (("MakeImage(string name, Transform parent, Sprite sprite)",
                          "public static RectTransform MakeImage("),
                         ("MakeText(string name, Transform parent, string text",
                          "public static Text MakeText(")):
        kit = KIT.read_text(encoding="utf-8")
        assert kit_sig in kit, "UiKit 里没有 %s 的实现" % sig.split("(")[0]
        for p in _ui_files():
            src = _code(p.read_text(encoding="utf-8"))
            name = sig.split("(")[0]
            for m in re.finditer(r"static\s+\w+\s+" + name + r"\s*\(", src):
                head = src[m.start():m.start() + 200]
                body = _member_body(src, head[head.index("(") - len(name) - 20:].strip()[:0] or head)
                assert "UiKit." + name + "(" in body, (
                    "%s 里的 %s 是**又写了一遍**（没有转手调 UiKit）" % (p.relative_to(CS), name))


def test_diagnostics_are_wired():
    """这一层坏了**不报错**，所以"看得见的日志"是唯一排障手段，不许被删。

    两条：
      · `FindByPath` 找不到节点时必须留一行（"界面正常但点了没反应"的最常见根因）；
      · 每个面板建完树自检一次（零尺寸 / 缩放 0 / 层级漏设 / 按钮无 target / 单行超宽）。
    """
    kit = _code(KIT.read_text(encoding="utf-8"))
    assert "[UiKit] 节点缺失" in kit, "FindByPath 缺失不再留日志"
    assert "public static void AuditSoon(" in kit, "建树自检入口不见了"
    for needle in ("零尺寸=", "缩放0=", "层错=", "无target=", "单行超宽="):
        assert needle in kit, "自检少了「%s」这一项" % needle

    builders = {
        "ChatUiBuilder.cs": '"Chat"',
        "ConfigUiBuilder.cs": '"Config"',
        "ContactUiBuilder.cs": '"Contact"',
        "NoticePanel.cs": '"Notice"',
        "ToolPanel.cs": '"ToolPanel"',
        "CompactEditPanel.cs": '"CompactEdit"',
    }
    for name, tag in builders.items():
        src = _code((UI / name).read_text(encoding="utf-8"))
        assert "UiKit.AuditSoon(" in src, "%s 建完树没有自检" % name
        assert tag in src, "%s 的自检没带 tag" % name
