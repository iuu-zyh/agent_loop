#!/usr/bin/env python3
"""变异验证 —— 气泡终稿去重的两层测试（09-16）。

**为什么必须有这个脚本**：本仓库吃过恒真断言的亏（`[t["name"] for t in ALL_TOOL_SCHEMAS]
== TOOL_ORDER` —— 左边就是从右边派生的，回退实现时测试照样绿）。所以"新增测试"这件事的
验收标准不是"跑绿了"，而是"**把修复逐个回退，对应的测试真的变红**"。这个脚本就是那条验收线，
留着它，下次改 `BubbleCommitPolicy` / `ChatWindow.FinishNpcBubble` 时能一条命令重跑。

两层各锁一半，缺一不可：
  · C# 单测（`csharp/tests/BubbleCommitPolicyTests`）—— 判定规则 + 帧序列场景，
    断言"屏幕上剩几条泡"。用的是**屏幕记账模拟器**（真实 ChatWindow 依赖 Unity，WSL 跑不了）。
  · Python 源码契约（`tests/test_bubble_dedup_contract.py`）—— 模拟器复刻的是**契约**，
    契约有没有被改回去只能靠源码断言：三条放正文的路都登记、判重命中撤重绘泡、
    `ResetContent` 不碰 `_curTurn`、策略类保持零 Unity 依赖、单测链接同一源文件。

用法：
    python3 scripts/dev/mutate_check_bubble_dedup.py          # 两层都跑（默认）
    python3 scripts/dev/mutate_check_bubble_dedup.py cs       # 只跑 C# 侧
    python3 scripts/dev/mutate_check_bubble_dedup.py py       # 只跑 Python 侧

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
POLICY = ROOT / "csharp/UI/BubbleCommitPolicy.cs"
SIM = ROOT / "csharp/tests/BubbleCommitPolicyTests/Program.cs"
CONTRACT = "tests/test_bubble_dedup_contract.py"
RUNNER = ROOT / "scripts/dev/run_csharp_tests.py"

# ---------------------------------------------------------------------------
# C# 侧：回退策略/模拟器里的规则，期望对应场景断言变红
# ---------------------------------------------------------------------------
CS_MUTATIONS = [
    (
        "① 回放不参与记账（窗口C 的成因）",
        SIM,
        '                if (!string.IsNullOrEmpty(body)) { _lastText = body; _lastTurn = turn; }',
        '                // mutated: 回放不登记记账',
        "S2 窗口C",
    ),
    (
        "② 判重命中时不撤流式重绘泡（模型复述的成因）",
        SIM,
        '                        if (hasStream) { _rows[_stream].Alive = false; _stream = -1; _buf = ""; }',
        '                        // mutated: 不撤重绘泡',
        "S4 复述同段",
    ),
    (
        "③ 同回合文本不同时新建而不是覆盖（规范化差异的成因）",
        POLICY,
        """            // 同回合并有可覆盖的行 → 覆盖（终稿的规范化差异走这里，绝不新建）
            if (curTurn > 0 && curTurn == lastRenderTurn && lastRowAlive)
                return BubbleCommit.OverwriteLastRow;
""",
        "",
        "S5 规范化差异",
    ),
    (
        "④ 判重不看末行是否还在（丢失风险）",
        POLICY,
        "                lastRowAlive &&\n",
        "",
        "S6 末行已销毁",
    ),
    (
        "⑤ 判重丢掉回合号（跨回合复读被误吞）",
        POLICY,
        "                curTurn == lastRenderTurn &&\n",
        "",
        "跨回合同文本",
    ),
]

# ---------------------------------------------------------------------------
# Python 侧：回退真实接线，期望对应测试函数变红
# ---------------------------------------------------------------------------
PY_MUTATIONS = [
    (
        "① 回放不登记终稿记账",
        WINDOW,
        """                            if (!string.IsNullOrEmpty(body))
                            {
                                _lastRenderText = body;
                                _lastRenderTurn = o["turn"]?.Value<int>() ?? -1;
                                _lastRenderRow = rm?.Row;
                            }""",
        "                            // mutated",
        "test_replay_registers_what_it_drew",
    ),
    (
        "② 判重命中时不撤重绘泡",
        WINDOW,
        "                        DestroyActiveStreamingBubble();\n                        if (initiative)",
        "                        if (initiative)",
        "test_skip_destroys_redrawn_streaming_bubble",
    ),
    (
        "③ 覆盖末行改成新建",
        WINDOW,
        "if (!string.IsNullOrEmpty(fullText)) SetRowText(_lastRenderRow, fullText);",
        "if (!string.IsNullOrEmpty(fullText)) BeginNpcBubble(fullText, false);",
        "test_finish_has_four_actions",
    ),
    (
        "④ ResetContent 顺手把 _curTurn 也清了",
        WINDOW,
        "            _lastRenderTurn = -1;\n            _lastRenderText = null;\n            _lastRenderRow = null;\n            _dividerTurn = -1;",
        "            _lastRenderTurn = -1;\n            _lastRenderText = null;\n            _lastRenderRow = null;\n            _curTurn = -1;\n            _dividerTurn = -1;",
        "test_reset_content_clears_bookkeeping_but_keeps_cur_turn",
    ),
    (
        "⑤ ChatWindow 里又冒出字符串判重",
        WINDOW,
        "                var action = BubbleCommitPolicy.Decide(",
        "                if (fullText == _lastRenderText) return;\n                var action = BubbleCommitPolicy.Decide(",
        "test_old_string_equality_judgement_is_gone_from_chatwindow",
    ),
    (
        "⑥ 判重不看末行是否还活着",
        POLICY,
        "                lastRowAlive &&\n",
        "",
        "test_policy_skip_conditions",
    ),
    (
        "⑦ 策略类引入 Unity 依赖（单测就编不了了）",
        POLICY,
        "namespace AgentLoopBridge\n{",
        "using UnityEngine;\n\nnamespace AgentLoopBridge\n{",
        "test_policy_is_unity_free",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_bubble_dedup"


def _run_cs():
    """走仓库的 runner（它会先清 obj/bin 强制重编）。"""
    p = _mutate_lib.run(
        [sys.executable, str(RUNNER)], cwd=str(ROOT),
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
        bad += _check_set("C# 单测（判定规则 + 帧序列场景）", CS_MUTATIONS,
                          lambda _e: _run_cs(), lambda: _run_cs())
    if which in ("all", "py"):
        bad += _check_set("Python 源码契约（真实接线）", PY_MUTATIONS,
                          lambda node: _run_py(node), lambda: _run_py(None))
    print("\n全部变异都被捕获" if bad == 0 else f"\n有 {bad} 项未被捕获（测试有洞）")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
