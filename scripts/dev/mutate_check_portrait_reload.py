#!/usr/bin/env python3
"""变异验证：把每个修复**改回 bug 原样**，确认对应测试变红；还原后变绿。

这是本工程既定的证明手法：绿色测试本身不证明什么，只有"改坏了会红"才证明测试真的在守着。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # scripts/dev/ → 工程根
CACHE = ROOT / "csharp/portrait/PortraitCache.cs"
PRESENTER = ROOT / "csharp/UI/ContactPresenter.cs"
HOTKEYS = ROOT / "csharp/UI/AbHotkeys.cs"
MODMAIN = ROOT / "csharp/app/ModMain.cs"

TESTFILE = "tests/test_portrait_reload_contract.py"

# (名称, 文件, 原串(修复后), 变异串(改回 bug), 期望变红的测试名)
MUTATIONS = [
    (
        "A TryGet 只 return false（不驱逐僵尸条目）",
        CACHE,
        """                if (e.Tex != null) { tex = e.Tex; return true; }
                // 纹理已死 → 驱逐僵尸条目（Sprite 同理，它也可能已被销毁；先判后销，
                // 免得对已销毁对象再 Destroy 一次）
                DropEntry(id, e, "纹理已销毁");""",
        """                if (e.Tex != null) { tex = e.Tex; return true; }""",
        "test_tryget_evicts_poisoned_entry_instead_of_just_returning_false",
    ),
    (
        "B RequestLoad 用裸 ContainsKey 判已缓存",
        CACHE,
        """            if (_mem.TryGetValue(id, out var e))
            {
                if (e.Tex != null) return;          // 真的有活纹理：无需重载
                DropEntry(id, e, "请求重载时发现纹理已销毁");   // 僵尸：清掉，往下走重新入队
            }
            if (_loadQueue.Contains(id)) return;""",
        """            if (_mem.ContainsKey(id) || _loadQueue.Contains(id)) return;""",
        "test_load_paths_must_not_treat_a_bare_key_as_cached",
    ),
    (
        "C PumpLoad 用裸 ContainsKey 跳过",
        CACHE,
        """                if (_mem.TryGetValue(id, out var pe))
                {
                    if (pe.Tex != null) continue;                    // 已被别处读进来
                    DropEntry(id, pe, "读盘泵发现纹理已销毁");          // 僵尸：清掉再读
                }""",
        """                if (_mem.ContainsKey(id)) continue;""",
        "test_load_paths_must_not_treat_a_bare_key_as_cached",
    ),
    (
        "D ApplyAvatar 退回「只看 Unit」的早退",
        PRESENTER,
        """            if (avatarGo == null || e == null) return;
            var img = avatarGo.GetComponent<Image>();
            if (img == null) return;

            // ① 精确 unitID（条目自带，与 `Unit` 在不在场无关）
            string id = !string.IsNullOrEmpty(e.UnitId) ? e.UnitId : null;
            // ② 现场从真身读（手上握着 WorldUnitBase 时最权威）
            if (string.IsNullOrEmpty(id) && e.Unit != null) id = PortraitCache.IdOf(e.Unit);
            // ③ 按名兜底（内部有失败限流；解析不到返回 null，不排队）
            if (string.IsNullOrEmpty(id)) id = PortraitCache.IdByName(e.Name);""",
        """            if (avatarGo == null || e?.Unit == null) return;
            var img = avatarGo.GetComponent<Image>();
            if (img == null) return;
            string id = PortraitCache.IdOf(e.Unit);""",
        "test_apply_avatar_uses_exact_unitid_before_live_unit",
    ),
    (
        "E 常驻帧回调不再泵读盘",
        HOTKEYS,
        """            try { PortraitCache.PumpLoad(); }
            catch { }""",
        """            // (变异：泵被删)""",
        "test_pumpload_is_driven_by_the_resident_frame_callback",
    ),
    (
        "F ModMain 不再注册 AbHotkeys.OnFrame",
        MODMAIN,
        """                try { g.timer.Frame(new Action(AbHotkeys.OnFrame), 1, true); }""",
        """                // (变异：注册被删)""",
        "test_resident_frame_callback_is_actually_registered",
    ),
    (
        "G OnIntoWorldEvent 不再调 ResetForWorld",
        MODMAIN,
        """                try { PortraitCache.ResetForWorld(); }
                catch (Exception pce) { P("[AgentLoopBridge] 换存档后清空立绘像素缓存失败: " + pce.Message); }""",
        """                // (变异：ResetForWorld 注册被删)""",
        "test_reset_for_world_is_wired_into_the_world_reload_hook",
    ),
    (
        "H ResetForWorld 不清 _loadQueue",
        CACHE,
        """            _mem.Clear();
            _loadQueue.Clear();
            _dir = null;""",
        """            _mem.Clear();
            _dir = null;""",
        "test_reset_for_world_clears_mem_queue_and_dir",
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
    print("变异验证：改回 bug → 期望变红；还原 → 期望变绿")
    print("=" * 78)

    # 先确认基线绿
    rc, out = run_tests()
    if rc != 0:
        print("✗ 基线不绿，先修好再跑变异\n" + out[-3000:])
        return 1
    print(f"✓ 基线绿：{out.strip().splitlines()[-1]}\n")

    failures = []
    for name, path, good, bad, expect_test in MUTATIONS:
        original = path.read_text(encoding="utf-8")
        if good not in original:
            print(f"✗ {name}: 找不到待变异片段（源码变了？）")
            failures.append(name)
            continue
        try:
            path.write_text(original.replace(good, bad, 1), encoding="utf-8")
            rc, out = run_tests()
            caught = expect_test in out and rc != 0
            status = "✓" if caught else "✗"
            print(f"{status} {name}")
            print(f"    期望测试 {expect_test} → {'RED（被捕获）' if caught else '没有变红！'}")
            if not caught:
                failures.append(name)
        finally:
            path.write_text(original, encoding="utf-8")

    # 还原后必须重新全绿
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
