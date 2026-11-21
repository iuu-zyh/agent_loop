#!/usr/bin/env python3
"""变异验证（world_id 名单清空事故）：把每个修复改回 bug 原样，确认对应测试变红。

本工程既定证明手法：绿色测试本身不证明什么，只有"改坏了会红"才证明测试真的在守着。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # scripts/dev/ → 工程根
WS = ROOT / "transport" / "ws_channel.py"
DUTY = ROOT / "csharp/contacts/ContactDuty.cs"
STORE = ROOT / "csharp/contacts/ContactStore.cs"
MODMAIN = ROOT / "csharp/app/ModMain.cs"
PRESENTER = ROOT / "csharp/UI/ContactPresenter.cs"

TESTFILE = "tests/test_world_gate_contact_wipe_contract.py"

MUTATIONS = [
    (
        "A Python 回包不带 world_known（客户端无从分辨空与未知）",
        WS,
        """            return {"contacts": self._contact_service.list_contacts(),
                    "world_known": not getattr(self._contact_service, "blocked", False)}""",
        """            return {"contacts": self._contact_service.list_contacts()}""",
        "test_list_rpcs_return_world_known_flag",
    ),
    (
        "B C# 不再校验 world_known（空表直接清镜像）",
        DUTY,
        """                if (resp["ok"]?.Value<bool>() != true) return;
                if (!WorldKnown(resp)) return;      // 未定档：空表是"拒绝回答"，不许当真相
                var arr = resp["data"]?["contacts"] as JArray;""",
        """                if (resp["ok"]?.Value<bool>() != true) return;
                var arr = resp["data"]?["contacts"] as JArray;""",
        "test_csharp_checks_world_known_before_writing_the_mirror",
    ),
    (
        "C WorldKnown 缺键时返回 false（老 Python 下通讯录永远空）",
        DUTY,
        """            if (tok == null || tok.Type == JTokenType.Null) return true;   // 老服务端：无此闸""",
        """            if (tok == null || tok.Type == JTokenType.Null) return false;""",
        "test_missing_flag_from_old_python_is_treated_as_known",
    ),
    (
        "D MarkWorldUnknown 顺手把镜像也清了（事故原样）",
        STORE,
        """            if (_worldUnknown) return;
            _worldUnknown = true;
            Fire();     // 面板开着时立刻如实改状态条""",
        """            _manualNames.Clear();
            _manualUnitIds.Clear();
            _worldUnknown = true;
            _manualLoaded = true;
            Fire();""",
        "test_unknown_world_marks_but_never_writes_data",
    ),
    (
        "E 重连不再重拉通讯录（不自愈）",
        MODMAIN,
        """                try { ContactDuty.RequestManualContacts(); ContactDuty.RequestSessions(); }
                catch (Exception ce) { P("[AgentLoopBridge] 重连后重拉通讯录失败: " + ce.Message); }""",
        """                // (变异：重拉被删)""",
        "test_reconnect_repulls_contacts",
    ),
    (
        "F 重拉排在 SendWorldHello 之前（身份未到，拉回还是空表）",
        MODMAIN,
        """                SendWorldHello();
                // ② 补投断线期间攒下的 save/load（WsClient 未连接时是直接回 ok:false、不排队）。""",
        """                try { ContactDuty.RequestManualContacts(); ContactDuty.RequestSessions(); }
                catch (Exception ce) { P("[AgentLoopBridge] 重连后重拉通讯录失败: " + ce.Message); }
                SendWorldHello();
                // ② 补投断线期间攒下的 save/load（WsClient 未连接时是直接回 ok:false、不排队）。""",
        "test_reconnect_repull_happens_after_world_hello",
    ),
    (
        "G 面板不再提示未定档（把残缺名单伪装成完整名单）",
        PRESENTER,
        """            if (_pick == PickMode.None && _search.Length == 0 && ContactStore.WorldUnknown)
                SetStatus(ModMain.WorldIdentityAvailable
                    ? "⚠ 存档身份尚未送达智能体，名单可能不完整 — 请稍候（进入世界后会自动重拉）"
                    : "⚠ 尚未取得存档身份，名单可能不完整 — 请先进入世界（存一次档或读一次档）",
                    StatusWarn);
            else if (_pick == PickMode.Invite)""",
        """            if (_pick == PickMode.Invite)""",
        "test_panel_surfaces_the_unknown_world_state",
    ),
    (
        "H 身份取不到时照样发注定为空的拉取",
        PRESENTER,
        """            if (ModMain.WorldIdentityAvailable)
            {
                ContactDuty.RequestManualContacts();
                ContactDuty.RequestSessions();
            }
            else
            {""",
        """            if (true)
            {
                ContactDuty.RequestManualContacts();
                ContactDuty.RequestSessions();
            }
            else
            {""",
        "test_show_skips_doomed_pull_when_identity_is_unavailable",
    ),
    (
        "I 跳过拉取但一声不吭",
        PRESENTER,
        """                ModMain.P("[ContactPresenter] 尚未取得存档身份（世界未加载）→ 本次不拉名单，"
                          + "仅显示关系层；进入世界后会自动重拉");""",
        """                ;""",
        "test_skipped_pull_is_not_silent",
    ),
    (
        "J ⚠ 文案不再区分成因（混成一句）",
        PRESENTER,
        """                SetStatus(ModMain.WorldIdentityAvailable
                    ? "⚠ 存档身份尚未送达智能体，名单可能不完整 — 请稍候（进入世界后会自动重拉）"
                    : "⚠ 尚未取得存档身份，名单可能不完整 — 请先进入世界（存一次档或读一次档）",
                    StatusWarn);""",
        """                SetStatus("⚠ 尚未取得存档身份，名单可能不完整", StatusWarn);""",
        "test_unknown_world_message_names_the_actual_cause",
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
    print("变异验证（world_id 名单清空）：改回 bug → 期望变红；还原 → 期望变绿")
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
