"""工具结果折叠契约（09-16）：默认折成一行摘要、点击展开全文、收口整组收起。

**用户报的症状**：行动记录里工具返回结果**全文直出**，一屏 28 行全是道具词条
（截图：`unit id: FmTzJ5` + 剑法/丹药/装备/宗门推荐信/石沉戒…占了整屏）。

**量化过的现状**（扫两个世界全部账本）：21 条工具结果，中位 267 字、p90 789 字、最长 908 字/17 行；
**工具原文合计 6901 字 > 同期台词旁白合计 6237 字** —— 这一屏里一半以上的可读内容是工具原文。

**定案**（用户 09-16）：
  · L1 组级：回合收口（他动完手、开始说话）整组收起 —— 这正是 README/WORKSHOP 已经承诺的
    「执行的时候自动展开，做完自动收起」（此前代码里 `Finish()` 明确把行动记录排除在外，
    文档与实现不符）。
  · L2 行级：每条结果默认折成一行摘要，点击展开；短结果（单行 ≤ 40 字）直出不折。
  · 位置：点开后**不跳**（用户选 B 档「不够就滚够」——装得下就一动不动，装不下只滚差的那一点）。

**边界铁律**：折叠只发生在视图层。Python `tools/text_render.py` 渲染出的字符串**同时**喂 LLM 与 UI
（`dialogue_agent.py` 的 `tool_result` 帧与账本 `tool/result` 是同一个 `text` 变量），
在渲染层砍它 = 模型看到的东西变少（`tests/test_text_render.py::test_no_truncation_anywhere` 是哨兵）。

规则本身由 C# 单测工程 `csharp/tests/ToolFoldPolicyTests` 跑（判定表 + 不变量矩阵 + 原文忠实性）；
本文件锁**接线**——两边合起来才完整。

本文件锁：
  A. 结果行走折叠路径，且展开态用原文（视图里不许出现截断）
  B. 折叠行整行可点（raycastTarget + Button + ClickUtils）
  C. 收口整组收起，且手动展开过的不收（手动优先）
  D. 「不够就滚够」的接线：先关跟随、等布局生效、只滚差的那一点、不写 verticalNormalizedPosition
  E. 所有 StepGroup/ThinkGroup 都接上 FoldToggled（漏一个就退化成"点开只看到结尾"）
  F. 策略类零 Unity 依赖 + 单测工程链接同一源文件 + 数据层不许被折叠动过
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"
WINDOW = CS / "UI" / "ChatWindow.cs"
ITEMS = CS / "UI" / "ChatItemViews.cs"
POLICY = CS / "UI" / "ToolFoldPolicy.cs"
TESTPROJ = CS / "tests" / "ToolFoldPolicyTests"
RUNNER = ROOT / "scripts" / "dev" / "run_csharp_tests.py"
MUTATOR = ROOT / "scripts" / "dev" / "mutate_check_tool_fold.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _braces(src: str, at: int) -> str:
    """从 `at` 之后第一个 `{` 起配对取整块正文（含花括号）。"""
    j = src.index("{", at)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
    raise AssertionError("大括号不配对")


def _method(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员的正文（含花括号）。签名必须唯一。"""
    assert src.count(signature) == 1, f"签名不唯一（{src.count(signature)} 次）：{signature}"
    return _braces(src, src.index(signature))


def _class(src: str, decl: str) -> str:
    """取一个类的整块正文（类名在全文件必须唯一 —— ChatItemViews.cs 里有多个类）。"""
    return _method(src, decl)


# ----------------------------------------------------------------------
# A. 结果行走折叠路径；展开态必须是原文
# ----------------------------------------------------------------------

def test_result_goes_through_fold_path():
    body = _method(_class(_read(ITEMS), "public class StepGroup"), "public void AddResult(string text, bool isError)")
    assert "AddResultLine(text ?? \"\", isError);" in body, "结果行必须走折叠渲染路径（AddResultLine）"
    assert "✓" not in body and "✗" not in body, "前缀只在 ToolFoldPolicy.Marker 里定义一处（避免两处漂移）"


def test_fold_line_uses_policy_for_both_states():
    body = _method(_read(ITEMS), "private void AddResultLine(string text, bool isError)")
    assert "ToolFoldPolicy.ShouldFold(text)" in body, "短结果不该被折（值不值得折由策略判）"
    assert "ToolFoldPolicy.ExpandByDefault(isError)" in body, "错误行默认展开由策略判"
    assert "ToolFoldPolicy.CollapsedLine(text, isError)" in body
    assert "ToolFoldPolicy.ExpandedLine(text, isError)" in body
    # 短结果直出：与折叠前的旧行为逐字一致（前置标记 + 原文）
    assert "ToolFoldPolicy.Marker(isError) + text" in body
    # 两态文本在点击前算好，点击只换 text（不重新截断、不重新拼）
    assert "expanded = !expanded;" in body


def test_expanded_state_is_raw_text_no_truncation_in_view():
    """★ 视图里**不许**出现对结果正文的截断/改写：展开态只能来自 `ExpandedLine(text, ...)`。
    截断只允许发生在策略的折叠态摘要里（`CollapsedLine` → `Substring`）。"""
    src = _read(ITEMS)
    body = _method(src, "private void AddResultLine(string text, bool isError)")
    assert "Substring" not in body, "结果行视图里不许截断（摘要在 ToolFoldPolicy 里）"
    assert "Replace(" not in body, "结果行视图里不许改写正文"
    # 策略里：只有折叠态摘要能截断
    pol = _read(POLICY)
    collapsed = _method(pol, "internal static string CollapsedLine(string text, bool isError)")
    expanded = _method(pol, "internal static string ExpandedLine(string text, bool isError)")
    assert "Substring" in collapsed, "折叠态摘要必须截断（否则折了还占一屏）"
    assert "Substring" not in expanded, "展开态一行都不许少（忠实性哨兵）"
    assert "text ?? \"\"" in expanded, "展开态必须原样拼原文"


# ----------------------------------------------------------------------
# B. 折叠行整行可点
# ----------------------------------------------------------------------

def test_fold_line_is_clickable():
    body = _method(_read(ITEMS), "private void AddResultLine(string text, bool isError)")
    assert "img.raycastTarget = true;" in body, (
        "UGUI 里点击要先射线命中一个 Graphic；结果行只有这张 Image 能吃射线（Text 的 raycastTarget 恒 false）"
    )
    assert "go.AddComponent<Button>()" in body
    assert "btn.targetGraphic = img;" in body
    assert "ClickUtils.Attach(btn," in body, "IL2CPP 下必须走 ClickUtils 三步写法"
    # 直出的短结果不该变成可点（没有可展开的东西）
    assert "img.raycastTarget = false;" in body, "短结果（不折）保持不可点"


def test_fold_click_reports_to_window():
    """点完要通知 ChatWindow 做位置校正 —— 否则贴底点开只看到结尾。"""
    body = _method(_read(ITEMS), "private void AddResultLine(string text, bool isError)")
    assert "FoldToggled" in body and "h(go)" in body, "结果行点击后必须回调 FoldToggled(本行)"
    think = _method(_read(ITEMS), "private void Toggle()")
    assert "FoldToggled" in think and "h(Root)" in think, "think 折叠同理（同一套手感）"
    group = _class(_read(ITEMS), "public class StepGroup")
    assert "internal Action<GameObject> FoldToggled;" in group, "StepGroup 必须暴露 FoldToggled 接线点"


# ----------------------------------------------------------------------
# C. 收口整组收起 + 手动优先
# ----------------------------------------------------------------------

def test_finish_collapses_group():
    body = _method(_class(_read(ITEMS), "public class StepGroup"), "public void Finish()")
    assert "_body.gameObject.SetActive(false);" in body, "收口必须真的收起整组（README 的「做完自动收起」）"
    assert "_bodyExpanded = false;" in body
    assert "_userToggled" in body, "手动展开过的组不许被收走（手动优先）"
    # 老行为（行动记录永不收起）必须消失
    assert "if (!_autoOpen && _body != null)" not in _read(ITEMS), (
        "旧的「只有 sys/ctx 才在收口时收起」判据必须删掉 —— 它正是行动记录永远占半屏的原因"
    )


def test_finish_refreshes_header_after_collapse():
    """标题箭头要跟着收起更新（旧代码先 RefreshHeader 再收起 → 收起了箭头还停在 ▾）。"""
    body = _method(_class(_read(ITEMS), "public class StepGroup"), "public void Finish()")
    assert body.index("RefreshHeader();") > body.index("SetActive(false)"), (
        "RefreshHeader() 必须在收起之后调用"
    )


def test_manual_toggle_wins():
    body = _method(_class(_read(ITEMS), "public class StepGroup"), "private void ToggleBody()")
    assert "_userToggled = true;" in body, "组头点击要标记手动接管"


# ----------------------------------------------------------------------
# D. 「不够就滚够」的接线
# ----------------------------------------------------------------------

def test_fold_toggle_turns_off_follow_and_records_row():
    body = _method(_read(WINDOW), "private void OnFoldToggled(GameObject row)")
    assert "_followingBottom = false;" in body, (
        "点开后必须关掉流式跟随（语义=用户接管屏幕，同滚轮上滚）："
        "否则下一批新内容一到就钉底，把他正在看的这段顶走"
    )
    assert "_foldAnchorRow = row;" in body
    assert "_foldAnchorWait = 0;" in body


def test_lateupdate_ticks_fold_anchor_before_pinning():
    body = _method(_read(WINDOW), "private void LateUpdate()")
    assert "TickFoldAnchor();" in body, "LateUpdate 必须推进折叠校正"
    assert body.index("TickFoldAnchor();") < body.index("if (!_followingBottom)"), (
        "校正必须在「钉底」之前接管这一帧（顺序反了 => 展开瞬间仍会被钉到底）"
    )
    assert "verticalNormalizedPosition" in body, "流式跟随本身要保留（只让折叠这一帧绕过它）"


def test_tick_waits_for_layout_then_scrolls_just_enough():
    body = _method(_read(WINDOW), "private void TickFoldAnchor()")
    assert "ToolFoldPolicy.RevealDelta(" in body, "滚多少必须由纯逻辑策略算（可单测）"
    assert "_foldAnchorWait" in body, (
        "Text 赋值只标脏布局，重建在帧末 —— 必须等行高真的变了再算，否则误判成「已经装得下」"
    )
    assert "content.anchoredPosition = new Vector2(" in body, (
        "只许挪 Content.anchoredPosition（绝对偏移）；写 verticalNormalizedPosition 会按比例跳"
    )
    assert "verticalNormalizedPosition" not in body, "校正路径不许碰归一化位置"
    assert "ClearFoldAnchor();" in body, "无论成败都要清状态（绝不把 _foldAnchorRow 挂死）"


def test_fold_anchor_state_is_bounded():
    """等待帧数必须有上限：布局万一不生效，也不能永久挂住（否则 LateUpdate 永远早退）。"""
    body = _method(_read(WINDOW), "private void TickFoldAnchor()")
    assert "_foldAnchorWait < 3" in body, "等待必须有上限"


# ----------------------------------------------------------------------
# E. 所有折叠组都接上 FoldToggled
# ----------------------------------------------------------------------

def test_all_stepgroups_go_through_factory():
    src = _read(WINDOW)
    assert src.count("new StepGroup(") == 1, (
        "StepGroup 只许在 NewStepGroup 工厂里 new（漏接 FoldToggled 的组会退化成「点开只看到结尾」）"
    )
    factory = _method(src, "private StepGroup NewStepGroup(string label = \"行动记录\", bool autoOpen = true)")
    assert "g.FoldToggled = OnFoldToggled;" in factory
    assert src.count("NewStepGroup(") >= 5, "四个建组点（直播/输入侧/回放两处）都得走工厂"


def test_thinkgroup_wires_fold_toggled():
    body = _method(_read(WINDOW), "public void BeginThink()")
    assert "_curThink.FoldToggled = OnFoldToggled;" in body


# ----------------------------------------------------------------------
# F. 策略类可单测 + 数据层没被动过
# ----------------------------------------------------------------------

def _strip_comments(src: str) -> str:
    """剥掉 C# 注释（含 `///` 文档注释），只留下代码 —— 检查 Unity 依赖时要看**代码**，
    不能因为文档注释里写了「不 using UnityEngine」就把自己判红。"""
    import re

    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def test_policy_is_unity_free():
    src = _strip_comments(_read(POLICY))
    for banned in ("using UnityEngine", "MonoBehaviour", "GameObject", "UnityEngine.", "Mathf."):
        assert banned not in src, f"策略类必须保持零 Unity 依赖，发现了：{banned}"
    assert "class ToolFoldPolicy" in src, "剥注释后仍应看到策略类本身（别把文件读空了）"


def test_unit_test_project_links_the_same_source():
    csproj = _read(TESTPROJ / "ToolFoldPolicyTests.csproj")
    assert r"..\..\UI\ToolFoldPolicy.cs" in csproj, "单测工程必须链接同一份策略源码"
    assert not (TESTPROJ / "ToolFoldPolicy.cs").exists(), "不许复制副本（会与真源码漂移）"


def test_runner_auto_discovers_test_projects():
    txt = _read(RUNNER)
    assert "TESTS_DIR" in txt and "glob(\"*/*.csproj\")" in txt, "跑法入口必须自动发现测试工程"


def test_csproj_is_valid_xml():
    """csproj 注释里不能出现连续两个减号（XML 规范）—— 已踩过两次 MSB4025。"""
    import xml.etree.ElementTree as ET

    for p in (TESTPROJ / "ToolFoldPolicyTests.csproj", CS / "AgentLoopBridge.csproj"):
        ET.parse(p)


def test_mutation_checker_exists():
    assert MUTATOR.exists(), "缺 scripts/dev/mutate_check_tool_fold.py（变异验证要可复现）"
    txt = _read(MUTATOR)
    assert "test_tool_fold_contract.py" in txt and "ToolFoldPolicyTests" in txt


def test_data_layer_untouched_by_folding():
    """★ 折叠只许发生在视图层：Python 那边喂模型与喂 UI 必须是**同一个字符串**。
    这里锁住 `dialogue_agent.py` 的分工——渲染结果既进账本（回放）又进 UI 帧，
    一旦有人在渲染层加截断，模型看到的内容也会跟着变少。"""
    da = _read(ROOT / "agent" / "dialogue_agent.py")
    assert 'text = text_render.render(name, args, res) or json.dumps(res, ensure_ascii=False)' in da, (
        "工具结果文本的产出方式变了（渲染层是「唯一叙述来源」，折叠不许碰它）"
    )
    assert '{"kind": "tool_result", "tool": name, "text": text' in da, (
        "UI 帧必须发**同一个** text（视图层折叠的输入前提）"
    )
    assert "create_canonical_tool_result_message(call_id, text" in da, (
        "账本 tool_result 必须用同一个 text（否则回放与直播内容不一致）"
    )
