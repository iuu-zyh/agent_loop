"""富文本标签不得进入模型上下文（10-25 真机发现）。

## 事故形状
真机 L1 段里实测到 **61 处 `<color=#004FCA>` + 66 处 `</color>`**，样本：
`完成了宗门任务<color=#004FCA>妖物肆虐灵矿</color>。`
模型会把标签当正文读（还可能照着这个风格输出），纯噪声。

## 两个源头，两条都必须堵
  ① **C# 主路没剥**：`StripRichTags` 早在技能说明那条路上就有，但 `CleanLogText`
     **从没调用过它** —— 而经历文本走的正是 `LogItemText → CleanLogText`。
     ⇒ 症状：`近况` 段 29 条带标签（三个存档 20 个会话实测）。
  ② **Python 截断把标签切成半截**：`_name_desc` 是「先 `d[:60]` 再拼 `…`」，
     于是 `<color=#004FCA>阴阳…` 被切成 `<co…` —— 半个标签，比不剥更糟：
     它既不匹配标签正则、C# 也认不出，但照样是噪声。
     ⇒ 症状：`自身` 段出现 `阴阳<co…`（实测 1 段）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from agent_loop.agent.system_prompt import SystemPrompt  # noqa: E402

CS = ROOT / "csharp" / "game" / "UnitSnapshot.cs"


# ---------------------------------------------------------------- ① C# 主路

def test_cs_cleanlogtext_strips_rich_tags():
    """`CleanLogText` 必须剥富文本（它是经历文本的唯一出口）。"""
    src = CS.read_text(encoding="utf-8")
    i = src.index("internal static string CleanLogText(")
    body = src[i: i + 3000]
    body = body[: body.index("\n        }", 200)]
    assert "StripRichTags(" in body, (
        "CleanLogText 没剥富文本 —— 经历文本会带着 <color=...> 进模型上下文"
        "（实测 61+66 处）")


def test_cs_strip_handles_broken_tag():
    """C# 的括号机对**半截标签**的行为要看清：`<co…` 会被当成"进括号"吃到文本末尾。

    ★ 这不是我们的实现问题，是这条路径的固有代价 —— 所以 Python 侧必须在
    **截断之前**剥（见 `test_py_strip_before_truncate`），不能让半截标签流到 C#。
    """
    src = CS.read_text(encoding="utf-8")
    i = src.index("private static string StripRichTags(")
    body = src[i: i + 1800]
    assert "if (c == '<') { inTag = true; continue; }" in body
    assert "if (c == '>') { inTag = false; continue; }" in body


# ---------------------------------------------------------------- ② Python 侧

def test_py_strip_before_truncate():
    """★ 关键：**先剥标签、再截断**（顺序反了就会切出 `<co…`）。"""
    long_desc = "<color=#004FCA>" + "阴阳两仪之道，" * 20 + "</color>"
    out = SystemPrompt._name_desc("阴阳仙", long_desc)
    assert "<" not in out and ">" not in out, f"标签漏出来了：{out!r}"
    assert "…" in out, "超长注解应被截断并加省略号"
    # 半截标签那种形态（`<co…`）绝不允许出现
    assert not re.search(r"<[a-zA-Z]{0,3}…", out), f"截断产生了半截标签：{out!r}"


def test_py_strip_covers_all_tag_kinds():
    """通用括号机：`<g>`/`<y>`/`<size>`/`<sprite>` 这些游戏自带标签也要吃掉。

    为什么不用白名单：标签种类会变，白名单必然漏掉下一种；
    而"正文里真出现裸 `<…>`"实测 0/313 段，代价为零。
    """
    for raw, want in [
        ("<g>甲</g>", "甲"),
        ("<y>乙</y>", "乙"),
        ("<size=25>丙</size>", "丙"),
        ('<sprite name="icon">丁', "丁"),
        ("<color=#FF0000>戊</color>", "戊"),
        ("无标签", "无标签"),
    ]:
        got = SystemPrompt._strip_rich(raw)
        assert got == want, f"{raw!r} → {got!r}，期望 {want!r}"


def test_py_strip_is_safe_on_plain_text():
    """普通中文/标点/数字一律不动（剥的是 `<…>`，不是别的）。"""
    for s in ["完成了宗门任务，获得奖励。", "斩杀竹妖 ×59", "（3年7月）", ""]:
        assert SystemPrompt._strip_rich(s) == s, s


# ---------------------------------------------------------------- 端到端

def test_l1_segments_have_no_rich_tags():
    """端到端：把真机样本喂进 L1，任何段都不许出现 `<…>`。"""
    sp = SystemPrompt()
    raw = {
        "npc_id": "测试乙",
        "self": {
            "name": "测试乙", "sex": "女", "age": 20, "life": 90,
            "power": 3000, "health": 300, "talent": 80, "mood": 90,
            "luck": {"born": [{"name": "天妒", "desc": "<color=#004FCA>命途多舛</color>"}],
                     "added": [{"name": "道心", "desc": "<size=20>造成伤害+5%</size>"}]},
        },
        "player": {"name": "唐炎", "sex": "男", "luck": {"born": [], "added": [
            {"name": "瓶颈", "desc": "<color=#DB0000>修为停滞</color>"}]}},
        "recent": ["(1年1月) 完成了宗门任务<color=#004FCA>妖物肆虐灵矿</color>。"],
        "recent_with_player": ["(1年2月) 与唐炎论道<size=25>（胜）</size>"],
    }
    segs = sp.format_l1_context("测试乙", raw, scope="富文本自检")
    for name, txt in segs.items():
        assert "<" not in (txt or "") and ">" not in (txt or ""), \
            f"段 {name} 里漏出了富文本标签：{txt!r}"
