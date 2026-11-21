# -*- coding: utf-8 -*-
"""气泡正文**空行折叠**（`BubbleTextCore.Tighten`）的跨语言契约。

用户 09-17 的投诉里，"行间距/换行特别大"来自模型自发排版里的 `\\n\\n`：uGUI 的 `Text`
没有段间距，一个空行 = **一整行行高**（fontSize 19 ≈ 23 单位），那条回复 183 单位高的气泡里
46 单位是空白。折叠本身是纯逻辑（`csharp/tests/BubbleTextTests` 有 48 项断言的判定表），
但**写泡点有四个**（流式刷新 / 终稿覆盖 / 行覆盖 / 新建气泡），接漏任何一个都会让
"某一条路还是大空行"，而且**毫无报错**。所以这里把四个调用点钉在真实源码上。

为什么必须是四处而不是一处：
  · **流式**（`LateUpdate` 逐帧刷 `_delta`）与**终稿覆盖**（`OverwriteStreaming`）必须同款 ——
    否则定稿那一瞬间空行会突然消失，屏幕"跳一下"；
  · `SetRowText`（`OverwriteLastRow`：终稿与已显示文本有差异时覆盖旧行）**不经过**
    `ChatBubble.SetText`，漏了它就等于"同一段话，是否重复取决于走哪条判重分支"；
  · `ChatBubble.SetText` 是**新建气泡**的唯一入口（单聊/群聊终稿、历史重放、玩家自己发的
    都从这儿过）。

⚠ 诚实边界：C# 单测只证明"规则对"，这里只证明"接对了"。真机观感（空行真的没了）只能靠
  实机截图确认 —— 见 docs/reference/csharp-iron-laws.md 2026-09-17-气泡观感三件事.md 的验证步骤。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CORE = ROOT / "csharp" / "UI" / "BubbleTextCore.cs"
CSPROJ = ROOT / "csharp" / "AgentLoopBridge.csproj"
WIN = ROOT / "csharp" / "UI" / "ChatWindow.cs"
ITEMS = ROOT / "csharp" / "UI" / "ChatItemViews.cs"
TESTS_DIR = ROOT / "csharp" / "tests" / "BubbleTextTests"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _method(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员正文（含花括号）。签名必须唯一。"""
    src = _strip_comments(src)
    assert src.count(signature) == 1, "签名不唯一（%d 次）：%s" % (src.count(signature), signature)
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


# ----------------------------------------------------------------------
# ① 核本身：存在、纯逻辑、被主工程编译
# ----------------------------------------------------------------------

def test_core_exists_and_stays_unity_free():
    assert CORE.is_file(), "BubbleTextCore.cs 不见了"
    src = _read(CORE)
    assert "static string Tighten(string s)" in src, "找不到 Tighten(string) 入口"
    # 纯逻辑是它能被 net8.0 单测的**前提**：混进 Unity 类型就编不进测试工程
    assert "UnityEngine" not in src, "BubbleTextCore 必须零 Unity 依赖（否则测试工程编不过）"
    assert "using System.Text;" in src, "实现用 StringBuilder，缺 using"


def test_core_is_compiled_into_the_mod():
    """2026-09-21 目录整理后 BubbleTextCore 移入 UI/（显示层整形，归 UI 域），
    由 `UI\\*.cs` 通配覆盖 —— 断言"文件在通配罩住的目录里 + 通配条目存在"。
    （旧行为是根目录显式条目；漏登记 = CS0103 或静默不生效，这条契约不变。）"""
    assert CORE.parent.name == "UI", (
        "BubbleTextCore.cs 不在 UI/ 下 —— csproj 只有 UI\\*.cs 一条通配罩住它，"
        "挪出去而不登记就会静默脱离编译"
    )
    csproj = _read(CSPROJ)
    assert '<Compile Include="UI\\*.cs" />' in csproj, "csproj 缺 UI\\*.cs 通配条目"


def test_csharp_test_project_exists_and_links_the_same_file():
    assert (TESTS_DIR / "BubbleTextTests.csproj").is_file(), "缺测试工程"
    assert (TESTS_DIR / "Program.cs").is_file(), "缺测试程序"
    csproj = _read(TESTS_DIR / "BubbleTextTests.csproj")
    assert r'Compile Include="..\..\UI\BubbleTextCore.cs"' in csproj, \
        "测试工程必须链接**同一个源文件**（复制一份 = 测的不是模组里跑的那段）"


# ----------------------------------------------------------------------
# ② 四个写泡点全都折叠了（漏一个 = 某条路还是大空行）
# ----------------------------------------------------------------------

# ★ 10-31 起，写泡入口从 `Tighten`（只折空行）升级成 `Render`（折空行 + 富文本白名单）。
#   这一节钉的仍是**同一件事**：「四处写泡点都必须整形，一处都不能裸写」。
#   `Render` 内部就是 `Rich(Tighten(s))`（见 `test_rich_text_bubble_contract.py::
#   test_render_is_tighten_plus_rich`），所以折空行这条语义没有丢；具体该用哪个名字，
#   由新契约钉 —— 那里要求四处**一律** `Render`，直连 `Tighten` 会被它判红。
#
#   为什么这里放宽成"两者皆可"而不是直接改成 Render：这一节的职责是**折空行**这条老语义，
#   它不该因为后来多了一层富文本就整篇重写；而"必须 Render"是新增的**更强**约束，
#   放在新文件里、由新文件独家负责，两个文件各管一层，不重叠也不留空档。

_SHAPE = ("BubbleTextCore.Tighten(", "BubbleTextCore.Render(")


def _shaped(rhs: str) -> bool:
    return any(k in rhs for k in _SHAPE)


def test_streaming_refresh_tightens():
    body = _method(_read(WIN), "private void LateUpdate()")
    assert "BubbleTextCore.Render(_delta.ToString())" in body \
        or "BubbleTextCore.Tighten(_delta.ToString())" in body, \
        "流式刷新没走整形 ⇒ 流式期间空行照样出现"
    assert "_bubbleText.text = _delta.ToString();" not in body, \
        "流式刷新还留着裸写（整形被绕过了）"


def test_final_overwrite_tightens():
    """终稿覆盖活跃流式泡（`BubbleCommit.OverwriteStreaming` 分支）。"""
    body = _method(_read(WIN), "public void FinishNpcBubble(string fullText, bool initiative = false,"
                               " string speaker = null, string speakerId = null)")
    assert "BubbleTextCore.Render(fullText)" in body or "BubbleTextCore.Tighten(fullText)" in body, \
        "终稿覆盖没走整形 ⇒ 定稿瞬间空行会突然冒出来/消失（屏幕跳一下）"
    assert "_bubbleText.text = fullText;" not in body, "终稿覆盖还留着裸写"


def test_row_overwrite_tightens():
    """`OverwriteLastRow` 走的是 `SetRowText`（不经过 ChatBubble.SetText）。"""
    body = _method(_read(WIN), "private bool SetRowText(GameObject row, string text)")
    assert "BubbleTextCore.Render(text)" in body or "BubbleTextCore.Tighten(text)" in body, \
        "SetRowText 没走整形"
    assert 't.text = text ?? "";' not in body, "SetRowText 还留着裸写"


def test_new_bubble_tightens():
    """新建气泡的唯一入口（单聊/群聊终稿、历史重放、玩家消息）。"""
    body = _method(_read(ITEMS), "public void SetText(string text)")
    assert "BubbleTextCore.Render(text)" in body or "BubbleTextCore.Tighten(text)" in body, \
        "ChatBubble.SetText 没走整形"
    assert 'Text.text = text ?? "";' not in body, "ChatBubble.SetText 还留着裸写"


def test_no_raw_bubble_write_left_anywhere():
    """全仓扫一遍：`_bubbleText.text =` 的每一处都必须是整形调用。

    上一条按方法钉，这一条按**变量**兜底 —— 将来谁再加一条写泡分支（比如新的判重档），
    只有这条能拦住"新分支忘了整形"。
    """
    # 两个文件里写泡用的变量名不同：`ChatWindow` 是 `_bubbleText`（流式那块），
    # `ChatItemViews.ChatBubble` 是它自己的 `Text` 字段。**两种都要扫** ——
    # 老版本只扫 `_bubbleText`，而它在 ITEMS 里一处都没有，
    # 于是"一处都没找到"被当成通过，那个文件实际上**从来没被这条兜底覆盖过**。
    pats = (r"_bubbleText\.text\s*=\s*([^;]+);", r"(?<!_)\bText\.text\s*=\s*([^;]+);")
    for p, pat in zip((WIN, ITEMS), pats):
        src = _strip_comments(_read(p))
        hits = list(re.finditer(pat, src))
        assert hits, "%s 里一处气泡写都没找到（变量名变了？）" % p.name
        for m in hits:
            rhs = m.group(1).strip()
            assert _shaped(rhs), \
                "%s 里有一处写泡没整形：%s" % (p.name, rhs)
