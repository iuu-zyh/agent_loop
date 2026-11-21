#!/usr/bin/env python3
"""变异验证（10-18「新开存档继承了通讯录」修复）：把每一条防线改回 bug 原样，确认对应测试变红。

本工程既定证明手法：绿色测试本身不证明什么，只有"改坏了会红"才证明测试真的在守着。
覆盖三条防线（Python 作废 / C# 换档清理 / 回包判废），共 11 项变异。

用法：python3 scripts/dev/mutate_check_world_identity_failclosed.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层 + 中断还原保险绳，见该文件顶部说明）

ROOT = Path(__file__).resolve().parent.parent.parent   # scripts/dev/ → 工程根
WS = ROOT / "transport" / "ws_channel.py"
MODMAIN = ROOT / "csharp/app/ModMain.cs"
DUTY = ROOT / "csharp/contacts/ContactDuty.cs"
STORE = ROOT / "csharp/contacts/ContactStore.cs"

TESTFILE = "tests/test_world_identity_failclosed.py"
PYTEST = [sys.executable, "-m", "pytest", TESTFILE, "-q", "--no-header", "-p", "no:cacheprovider"]

MUTATIONS = [
    # ---------------- 防线 ①：Python 进世界没身份 = 作废（不是沿用） ----------------
    (
        "A 进世界帧的身份宣告被无视（退回'沿用上一个存档'= 事故原样）",
        WS,
        """            if _identity_unknown(p):
                self._invalidate_world("load_happened")""",
        """            if False:
                self._invalidate_world("load_happened")""",
        "test_load_happened_without_identity_invalidates_instead_of_keeping",
    ),
    (
        "B 作废只清通讯录、不清 AgentLoop（loop 仍拿着旧档钥匙）",
        WS,
        '        had = self.loop.world_id or ""\n'
        '        self.loop.set_world("")             # 空 id → world_id=None + 人名映射表内存副本作废\n',
        '        had = self.loop.world_id or ""\n',
        "test_load_happened_without_identity_invalidates_instead_of_keeping",
    ),
    (
        "C 回包不带身份（跨档回包无从判废 = 09-20 的老口径）",
        WS,
        """            "world_id": self.loop.world_id or "",
            "world_epoch": self._world_epoch,""",
        """            "world_epoch": self._world_epoch,""",
        "test_list_replies_carry_world_identity_and_epoch",
    ),
    (
        "D 作废时顺手把群名录也载进来（读扁平目录 = 别人家的群）",
        WS,
        """                return {"discarded": n, "rooms": 0, "world_unknown": True}""",
        """                return {"discarded": n, "rooms": self.loop.load_rooms(), "world_unknown": True}""",
        "test_load_happened_without_identity_invalidates_instead_of_keeping",
    ),
    # ---------------- 防线 ②：C# 三态宣告 + 补发重试 ----------------
    (
        "E 无条件宣告 identity=unknown（world_hello 也作废 → 主菜单/重启把正常局面毁掉）",
        MODMAIN,
        "                else if (worldEvent)\n                {\n",
        "                else\n                {\n",
        "test_fill_identity_declares_unknown_only_on_world_events",
    ),
    (
        "F world_hello 也按世界事件宣告（连接事件被当成进世界）",
        MODMAIN,
        """            JObject p = new JObject();
            FillWorldIdentity(p);
            bool sent = SendWorldFrame("world_hello", p);""",
        """            JObject p = new JObject();
            FillWorldIdentity(p, true);
            bool sent = SendWorldFrame("world_hello", p);""",
        "test_fill_identity_declares_unknown_only_on_world_events",
    ),
    (
        "G 补发改用 world_hello（语义错位：连接事件不许当'进世界'用）",
        MODMAIN,
        """                        bool sent = SendWorldFrame("load_happened", q);""",
        """                        bool sent = SendWorldFrame("world_hello", q);""",
        "test_retry_resends_load_happened_with_a_seq_guard_and_a_deadline",
    ),
    (
        "H 补发去掉换档守卫（会拿旧世界读到的人去报新世界）",
        MODMAIN,
        """                        if (entrySeq != _worldEntrySeq) { _identityRetryActive = false; return; }""",
        """                        if (false) { _identityRetryActive = false; return; }""",
        "test_retry_resends_load_happened_with_a_seq_guard_and_a_deadline",
    ),
    (
        "H2 存档帧也清回包基准（一次缺身份的存档 ⇒ 此后所有回包被判废，面板无缘无故说"
        "「尚未取得存档身份」）",
        MODMAIN,
        """                FillWorldIdentity(p, true);     // 世界事件：读不到身份要**明说**（见其 summary 的三态）""",
        """                FillWorldIdentity(p, true, true);     // 世界事件：读不到身份要**明说**""",
        "test_fill_identity_declares_unknown_only_on_world_events",
    ),
    (
        "H3 清基准没挂在 worldEntry 上（world_hello 也清 ⇒ 主菜单/重连把基准抹掉）",
        MODMAIN,
        """                    if (worldEntry) ContactDuty.ExpectWorld(null);  // 只有进世界才把基准也清掉""",
        """                    ContactDuty.ExpectWorld(null);""",
        "test_only_world_entry_clears_the_reply_baseline",
    ),
    (
        "I 换档清理挪到重拉之后（旧档名单先渲染一帧 = 用户看到的症状）",
        MODMAIN,
        """                try { ContactDuty.ResetForWorld(); }
                catch (Exception rce) { P("[AgentLoopBridge] 换存档后作废通讯录镜像失败: " + rce.Message); }
                try { ContactDuty.RequestManualContacts(); ContactDuty.RequestSessions(); ContactDuty.RequestHidden(); }""",
        """                try { ContactDuty.RequestManualContacts(); ContactDuty.RequestSessions(); ContactDuty.RequestHidden(); }
                try { ContactDuty.ResetForWorld(); }
                catch (Exception rce) { P("[AgentLoopBridge] 换存档后作废通讯录镜像失败: " + rce.Message); }""",
        "test_world_change_resets_the_mirror_before_repulling",
    ),
    # ---------------- 防线 ③：回包判废 + 换档整表作废 ----------------
    (
        "J 回包判废被删（跨档回包直接写进镜像）",
        DUTY,
        """                if (!WorldMatches(resp, out rw)) { NoteStaleReply(); return; }   // 跨档回包：不许写""",
        """                if (false) { NoteStaleReply(); return; }""",
        "test_duty_never_writes_without_a_world_match",
    ),
    (
        "K 本机未确认世界时仍放行（fail-open —— 最危险的那一瞬）",
        DUTY,
        """            if (_expectedWorld.Length == 0) return false;      // 本机尚未确认是哪个世界 → 谁都不许写""",
        """            if (_expectedWorld.Length == 0) return true;""",
        "test_world_matches_is_fail_closed_and_backward_compatible",
    ),
    (
        "L 换档整表作废漏清本地时间戳/隐藏名单（跨档残留）",
        STORE,
        """            _localLast.Clear();        // 本地互动时间戳同理：上一档的"最近"排序不该带到新档
""",
        "",
        "test_store_reset_clears_every_cross_world_mirror",
    ),
]


def run_tests() -> tuple[int, str]:
    r = _mutate_lib.run(PYTEST, cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    print("=" * 78)
    print("变异验证（存档身份 fail-closed）：改回 bug → 期望变红；还原 → 期望变绿")
    print("=" * 78)

    _mutate_lib.guard([WS, MODMAIN, DUTY, STORE])   # 被 Ctrl-C / 超时杀掉也要还原（见 _mutate_lib.guard）

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
