#!/usr/bin/env python3
"""变异验证 —— 工具结果折叠（L1 收口收起 / L2 行级折叠 / 「不够就滚够」）的两层测试（09-16）。

**为什么必须有这个脚本**：本仓库吃过恒真断言的亏（`[t["name"] for t in ALL_TOOL_SCHEMAS] == TOOL_ORDER`
—— 左边就是从右边派生的）。所以"新增测试"的验收标准不是"跑绿了"，而是"**把修复逐个回退，
对应的测试真的变红**"。这个脚本就是那条验收线；留着它，下次动 ToolFoldPolicy / Finish /
LateUpdate 的折叠校正时能一条命令重跑。

两层各锁一半，缺一不可：
  · C# 单测（`csharp/tests/ToolFoldPolicyTests`）—— 判定表 + 原文忠实性 + 「滚够」不变量矩阵。
    纯逻辑（零 Unity），所以能在 WSL 里用 dotnet 跑。
  · Python 源码契约（`tests/test_tool_fold_contract.py`）—— 单测测的是**规则**；
    规则有没有真的接进 ChatWindow/ChatItemViews（以及数据层有没有被顺手改坏）只能靠源码断言。

用法：
    python3 scripts/dev/mutate_check_tool_fold.py          # 两层都跑（默认）
    python3 scripts/dev/mutate_check_tool_fold.py cs       # 只跑 C# 侧
    python3 scripts/dev/mutate_check_tool_fold.py py       # 只跑 Python 侧

注意：会**临时改源码再还原**（备份在系统临时目录），跑完请确认末行是"全部变异都被捕获"。
"""

from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）


import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINDOW = ROOT / "csharp/UI/ChatWindow.cs"
ITEMS = ROOT / "csharp/UI/ChatItemViews.cs"
POLICY = ROOT / "csharp/UI/ToolFoldPolicy.cs"
AGENT = ROOT / "agent" / "dialogue_agent.py"
CONTRACT = "tests/test_tool_fold_contract.py"
RUNNER = ROOT / "scripts/dev/run_csharp_tests.py"
CS_PROJ = "ToolFoldPolicyTests"

# ---------------------------------------------------------------------------
# C# 侧：回退纯逻辑里的规则，期望对应断言变红
# ---------------------------------------------------------------------------
CS_MUTATIONS = [
    (
        "① 什么都不折（回到「全文直出」）",
        POLICY,
        "internal const int BriefChars = 40;",
        "internal const int BriefChars = 100000;",
        "41 字 → 折",
    ),
    (
        "② 展开态偷偷截断（忠实性哨兵）",
        POLICY,
        'return ArrowExpanded + Marker(isError) + (text ?? "");',
        'return ArrowExpanded + Marker(isError) + (text ?? "").Substring(0, System.Math.Min(BriefChars, (text ?? "").Length));',
        "★ 展开态长度守恒",
    ),
    (
        "③ 摘要不剥富文本标签（屏幕上出现半个 <color）",
        POLICY,
        "            string visible = Visible(text);\n            string brief =",
        "            string visible = CollapseWhitespace(text);\n            string brief =",
        "★ 摘要已剥富文本标签",
    ),
    (
        "④ 错误行不再默认展开",
        POLICY,
        "        internal static bool ExpandByDefault(bool isError)\n        {\n            return isError;",
        "        internal static bool ExpandByDefault(bool isError)\n        {\n            return false;",
        "错误行默认展开",
    ),
    (
        "⑤ 「不够就滚够」恒不滚（点开只看到结尾）",
        POLICY,
        "            return need > 0f ? need : 0f;",
        "            return 0f;",
        "差 320 → 只滚 320",
    ),
    (
        "⑥ 滚够时没有上盖（把行顶推出视口上沿）",
        POLICY,
        "            if (need > cap) need = cap;\n",
        "",
        "行比视口高",
    ),
    (
        "⑦ 纯空白/纯标签也当成可折（折出一行空摘要）",
        POLICY,
        "            if (visible.Length == 0) return false;",
        "",
        "纯空白 → 不折",
    ),
]

# ---------------------------------------------------------------------------
# Python 侧：回退真实接线，期望对应测试函数变红
# ---------------------------------------------------------------------------
PY_MUTATIONS = [
    (
        "① 折叠行整行不可点（点开没反应）",
        ITEMS,
        "            img.raycastTarget = true;               // 折叠行整行可点（命中 Image → Button）",
        "            img.raycastTarget = false;",
        "test_fold_line_is_clickable",
    ),
    (
        "② 收口不收起（回到「行动记录永远展开」）",
        ITEMS,
        "            if (!_userToggled && _body != null && _body.gameObject.activeSelf)",
        "            if (false)",
        "test_finish_collapses_group",
    ),
    (
        "③ 手动接管被忽略（收口把用户点开的组收走）",
        ITEMS,
        "            _userToggled = true;\n            _bodyExpanded = !_bodyExpanded;",
        "            _bodyExpanded = !_bodyExpanded;",
        "test_manual_toggle_wins",
    ),
    (
        "④ 标题箭头不跟着收起更新（▾ 与收起状态不符）",
        ITEMS,
        """            if (!_userToggled && _body != null && _body.gameObject.activeSelf)
            {
                _body.gameObject.SetActive(false);
                _bodyExpanded = false;
            }
            RefreshHeader();""",
        """            RefreshHeader();
            if (!_userToggled && _body != null && _body.gameObject.activeSelf)
            {
                _body.gameObject.SetActive(false);
                _bodyExpanded = false;
            }""",
        "test_finish_refreshes_header_after_collapse",
    ),
    (
        "⑤ 短结果也被折（为一行字也要点一次）",
        ITEMS,
        "            if (!ToolFoldPolicy.ShouldFold(text))",
        "            if (false)",
        "test_fold_line_uses_policy_for_both_states",
    ),
    (
        "⑥ 视图里偷偷截断展开态",
        ITEMS,
        "            string full = ToolFoldPolicy.ExpandedLine(text, isError);",
        "            string full = ToolFoldPolicy.ExpandedLine(text, isError).Substring(0, 100);",
        "test_expanded_state_is_raw_text_no_truncation_in_view",
    ),
    (
        "⑦ 点开后不关跟随（新内容把刚展开的顶走）",
        WINDOW,
        "            _foldAnchorRow = row;\n            _foldAnchorHeight = RowHeight(row);\n            _foldAnchorWait = 0;\n            _followingBottom = false;",
        "            _foldAnchorRow = row;\n            _foldAnchorHeight = RowHeight(row);\n            _foldAnchorWait = 0;",
        "test_fold_toggle_turns_off_follow_and_records_row",
    ),
    (
        "⑧ 校正挪到「钉底」之后（展开瞬间仍被钉到底）",
        WINDOW,
        # 锚点跟着 `_pinHoldFrames`（批量重建保持期）插到两者之间而更新：
        # 变异意图不变 —— 把校正挪到**所有钉底逻辑之后**（含 `!_followingBottom` 早退那条），
        # 于是 `test_lateupdate_ticks_fold_anchor_before_pinning` 的顺序断言必须变红。
        """                if (_foldAnchorRow != null) { TickFoldAnchor(); return; }
                // ★ 批量重建后的保持期：无条件钉底 + 忽略"位置不在底部"（见 `_pinHoldFrames`）。
                //   放在折叠校正之后、常规跟随之前：折叠校正是用户动作、优先级更高。
                if (_pinHoldFrames > 0)
                {
                    _pinHoldFrames--;
                    _followingBottom = true;
                    _lastChildCount = _r.Content.childCount;
                    _lastContentH = _r.Content.rect.height;
                    _r.Scroll.verticalNormalizedPosition = 0f;
                    return;
                }
                if (!_followingBottom)
                {
                    // 未跟随：用户滚/拖回底部 → 自愈恢复跟随（判据是**位置**不是滚轮方向）
                    if (AtBottom()) _followingBottom = true;
                    return;
                }""",
        """                // ★ 批量重建后的保持期：无条件钉底 + 忽略"位置不在底部"（见 `_pinHoldFrames`）。
                if (_pinHoldFrames > 0)
                {
                    _pinHoldFrames--;
                    _followingBottom = true;
                    _lastChildCount = _r.Content.childCount;
                    _lastContentH = _r.Content.rect.height;
                    _r.Scroll.verticalNormalizedPosition = 0f;
                    return;
                }
                if (!_followingBottom)
                {
                    // 未跟随：用户滚/拖回底部 → 自愈恢复跟随（判据是**位置**不是滚轮方向）
                    if (AtBottom()) _followingBottom = true;
                    return;
                }
                if (_foldAnchorRow != null) { TickFoldAnchor(); return; }""",
        "test_lateupdate_ticks_fold_anchor_before_pinning",
    ),
    (
        "⑨ 校正改成写归一化位置（按比例跳）",
        WINDOW,
        "                        content.anchoredPosition = new Vector2(content.anchoredPosition.x, s + delta);",
        "                        _r.Scroll.verticalNormalizedPosition += delta;",
        "test_tick_waits_for_layout_then_scrolls_just_enough",
    ),
    (
        "⑩ 某个建组点绕过工厂（那一组漏接 FoldToggled）",
        WINDOW,
        "                    block = NewStepGroup(labelTitle, autoOpen: false);",
        "                    block = new StepGroup(_r, _r.Content, labelTitle, autoOpen: false);",
        "test_all_stepgroups_go_through_factory",
    ),
    (
        "⑪ 策略类引入 Unity 依赖（C# 单测就编不了了）",
        POLICY,
        "namespace AgentLoopBridge\n{",
        "using UnityEngine;\n\nnamespace AgentLoopBridge\n{",
        "test_policy_is_unity_free",
    ),
    (
        "⑫ 数据层被顺手截断（模型看到的东西也变少）",
        AGENT,
        "text = text_render.render(name, args, res) or json.dumps(res, ensure_ascii=False)",
        "text = (text_render.render(name, args, res) or json.dumps(res, ensure_ascii=False))[:200]",
        "test_data_layer_untouched_by_folding",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_tool_fold"


def _run_cs():
    """走仓库的 runner（它会先清 obj/bin 强制重编），只跑折叠这一个工程。"""
    p = _mutate_lib.run(
        [sys.executable, str(RUNNER), "--only", CS_PROJ], cwd=str(ROOT),
        capture_output=True, text=True, errors="replace",
    )
    return p.returncode, p.stdout + p.stderr


def _run_py(node: str | None):
    target = CONTRACT if node is None else f"{CONTRACT}::{node}"
    p = _mutate_lib.run(
        [sys.executable, "-m", "pytest", target, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True, errors="replace",
    )
    return p.returncode, p.stdout + p.stderr


def _check_set(label: str, mutations, run, run_baseline) -> int:
    """跑一组变异，返回未被捕获的数量。"""
    print(f"\n=== {label} ===")
    files = sorted({m[1] for m in mutations})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    _BAK.mkdir(exist_ok=True)
    for f in files:
        shutil.copy2(f, _BAK / f.name)

    rc, out = run_baseline()
    print("基线：exit=", rc)
    if rc != 0:
        print("★ 基线不是绿的，先修基线：\n", out[-600:])
        return len(mutations) + 1

    bad = 0
    for name, path, old, new, expect in mutations:
        for f in files:                      # 每轮先全量还原，防上一轮污染
            shutil.copy2(_BAK / f.name, f)
        src = path.read_text(encoding="utf-8")
        if old not in src:
            print(f"✗ {name}: 找不到待替换文本（测试自身失效，需更新本脚本）")
            bad += 1
            continue
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        rc, out = run(expect)
        ok = rc != 0 and expect in out
        print(f"{'✓' if ok else '✗'} {name} → exit={rc} 命中[{expect}]={expect in out}")
        if not ok:
            bad += 1
            print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])

    for f in files:                          # 还原
        shutil.copy2(_BAK / f.name, f)
    rc, out = run_baseline()
    print("还原后：exit=", rc)
    if rc != 0:
        print(f"★★ 还原失败！从 {_BAK} 手工恢复")
        return len(mutations) + 2
    return bad


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    bad = 0
    if which in ("all", "cs"):
        bad += _check_set("C# 单测（折叠规则 + 摘要 + 滚够不变量）", CS_MUTATIONS,
                          lambda _e: _run_cs(), lambda: _run_cs())
    if which in ("all", "py"):
        bad += _check_set("Python 源码契约（真实接线 + 数据层）", PY_MUTATIONS,
                          lambda node: _run_py(node), lambda: _run_py(None))
    print("\n全部变异都被捕获" if bad == 0 else f"\n有 {bad} 项未被捕获（测试有洞）")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
