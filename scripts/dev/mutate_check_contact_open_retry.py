#!/usr/bin/env python3
"""变异验证（「点传没反应」事故）：把每个修复改回 bug 原样，确认对应测试变红。

本工程既定证明手法：绿色测试本身不证明什么，只有"改坏了会红"才证明测试真的在守着。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # scripts/dev/ → 工程根
OPENER = ROOT / "csharp/UI/ContactPanelOpener.cs"
PANEL = ROOT / "csharp/UI/AbContactPanel.cs"
HOTKEYS = ROOT / "csharp/UI/AbHotkeys.cs"
MODMAIN = ROOT / "csharp/app/ModMain.cs"

TESTFILE = "tests/test_contact_open_retry_contract.py"

MUTATIONS = [
    (
        "A Toggle 失败后不挂起（点击被丢弃 = 事故原样）",
        OPENER,
        """            if (TryOpenNow(toggle: true)) return;   // 闸门已开：立刻打开/关闭（常见情况，零延迟）
            ArmPending();                           // 否则挂起这次意图，交给常驻帧回调重试""",
        """            TryOpenNow(toggle: true);               // (变异：失败即丢弃，不挂起)""",
        "test_toggle_arms_pending_instead_of_dropping_the_click",
    ),
    (
        "B 常驻帧回调不再推进待开重试",
        HOTKEYS,
        """            try { ContactPanelOpener.TickPending(); }
            catch (Exception e) { ModMain.P("[AbHotkeys] TickPending: " + e.Message); }""",
        """            // (变异：TickPending 注册被删)""",
        "test_pending_tick_is_driven_by_the_resident_frame_callback",
    ),
    (
        "C 重试没有超时（永不放弃）",
        OPENER,
        """                if (Time.unscaledTime >= _deadline)
                {""",
        """                if (false)
                {""",
        "test_retry_does_not_run_forever",
    ),
    (
        "D 失败只写日志、不给可见反馈",
        OPENER,
        """                UnreadBanner.ShowSystem(hint);""",
        """                ModMain.P("[ContactPanelOpener] (变异：只写日志) " + hint);""",
        "test_failure_is_visible_not_just_logged",
    ),
    (
        "E 提示改用面板内控件（面板不存在时显示不出来）",
        OPENER,
        """                UnreadBanner.ShowSystem(hint);""",
        """                ModMain.ContactPresenterInstance.TogglePanel();""",
        "test_visible_hint_uses_hud_banner_not_panel_widget",
    ),
    (
        "F 同一原因每帧重复弹（刷屏）",
        OPENER,
        """            if (!timeout && _hintedReason == key) return;    // 同一原因不重复弹""",
        """            if (false) return;    // (变异：去重被删)""",
        "test_same_reason_does_not_spam_but_giveup_does_warn",
    ),
    (
        "G EnsureResident 失败路径不带原因",
        PANEL,
        """                    return Fail(why);""",
        """                    return false;""",
        "test_panel_reports_a_reason_on_every_failure_path",
    ),
    (
        "H 失败原因不翻译成玩家能懂的话",
        OPENER,
        """                else if (why == "智能体尚未连接") hint = "正在连接本地智能体，请稍候…";
                else if (why == "世界尚未加载完成") hint = "世界尚未就绪，请稍候…";
                else hint = "暂不能打开传音簿：" + why;""",
        """                else hint = why;""",
        "test_reason_reaches_the_player_readably",
    ),
    (
        "I 兑现挂起意图改用开关语义（会误关刚打开的面板）",
        OPENER,
        """            if (toggle) inst.TogglePanel();
            else if (!inst.IsOpen) inst.Show();     // 兑现打开意图：已开则不动（不误关）""",
        """            inst.TogglePanel();""",
        "test_open_path_checks_is_open_before_showing",
    ),
    (
        "J 直接点击丢掉开关语义（开着再也关不掉）",
        OPENER,
        """            if (TryOpenNow(toggle: true)) return;   // 闸门已开：立刻打开/关闭（常见情况，零延迟）""",
        """            if (TryOpenNow(toggle: false)) return;""",
        "test_direct_click_still_has_toggle_semantics",
    ),
]


def run_tests() -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TESTFILE, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    print("=" * 78)
    print("变异验证（点传没反应）：改回 bug → 期望变红；还原 → 期望变绿")
    print("=" * 78)

    rc, out = run_tests()
    if rc != 0:
        print("✗ 基线不绿，先修好再跑变异\n" + out[-3000:])
        return 1
    print(f"✓ 基线绿：{out.strip().splitlines()[-1]}\n")

    failures = []
    for name, path, good, bad, expect in MUTATIONS:
        original = path.read_text(encoding="utf-8")
        if good not in original:
            print(f"✗ {name}: 找不到待变异片段（源码变了？）")
            failures.append(name)
            continue
        try:
            path.write_text(original.replace(good, bad, 1), encoding="utf-8")
            rc, out = run_tests()
            caught = expect in out and rc != 0
            print(f"{'✓' if caught else '✗'} {name}")
            print(f"    期望 {expect} → {'RED（被捕获）' if caught else '没有变红！'}")
            if not caught:
                failures.append(name)
        finally:
            path.write_text(original, encoding="utf-8")

    rc, out = run_tests()
    print()
    if rc == 0:
        print(f"✓ 全部还原后绿：{out.strip().splitlines()[-1]}")
    else:
        print("✗ 还原后没有恢复绿色！\n" + out[-2000:])
        failures.append("restore")

    print()
    print("=" * 78)
    if failures:
        print(f"✗ {len(failures)} 项未被捕获：{failures}")
        return 1
    print(f"✓ 全部 {len(MUTATIONS)} 项变异都被捕获，且还原后绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
