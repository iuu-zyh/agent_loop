#!/usr/bin/env python3
"""变异验证 —— 关系缔结的引擎裁决契约（09-17 两起事故：静默失败 → 关系被强拆）。

**为什么必须有这个脚本**：这条链上的失败模式**全是静默的** ——
  · 事故一：引擎拒绝（`IsCreate` 返回非 0）、我们丢掉返回码、`isTip` 关着、渲染层只剩
    "可能未生效" ⇒ 模型在游戏里宣布"师徒名分结下了"，而关系簿一字未动；
  · 事故二：把"0 = 同意 / 非 0 = 拒绝"读反 + 预检与落刀共用同一个 act 实例
    （`isCreateCall` 免检标记）⇒ 引擎的拒绝被撬成强行落刀，`OnCreate` 的 ClearRelation
    把用户原有的**夫妻**关系拆掉换成师徒。
两层里任何一层"看起来正常"，事故就会复现。所以验收标准不是"测试绿"，而是
"**把实现逐条回退成事故写法，对应测试真的变红**"。

覆盖：`tests/test_relation_diag_contract.py`（22 项：C# 接线 13 + 渲染行为 9）。

用法：
    python3 scripts/dev/mutate_check_relation_diag.py

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
TOOL = ROOT / "csharp/tools/ToolExecutor.cs"
SNAP = ROOT / "csharp/game/UnitSnapshot.cs"
TR = ROOT / "tools/text_render.py"
CONTRACT = "tests/test_relation_diag_contract.py"

# 常被复用的源码片段（多处出现，变异时带上下文锚定）
KNIFE = ("                    try { createRet = npc.CreateAction("
         "new UnitActionRelationSet(player, type, addClose), true); }")
SNAP_KNIFE = ("                    string relBeforeKnife = RelationOf(npc, player);\n"
              "                    int createRet;\n" + KNIFE)
PROBE_CUT = ("            int precheck = ProbeRelationSet(npc, player, type, addClose);\n"
             "            if (precheck > 0)")
DIRECT_CUT = ("            int precheck = ProbeRelationSet(npc, other, type, addClose);\n"
              "            if (precheck > 0)")
BREAK_PROBE = """                        try
                        {
                            var probe = new UnitActionRoleBreakWith();
                            ((UnitActionBase)probe).Init(target);
                            bwPre = npc.IsCreateAction(probe, false);
                        }
                        catch (Exception e) { try { ModMain.P("[Relation] 解除预检抛异常（按未知处理）: " + e.Message); } catch { } }"""

MUTATIONS = [
    # ── 引擎码：0 = 同意（事故二的第一层根因）──
    (
        "① 注释表把语义写反（回到事故当时的读法：0 = 拒绝）",
        TOOL,
        "//   **0 = 引擎同意（可创建）；非 0 = 引擎拒绝，数字就是原因码。**",
        "//   **0 = 引擎拒绝；非 0 = 引擎同意。**",
        "test_engine_code_table_says_zero_means_go",
    ),
    (
        "② 注释表少一个码（说明书与实现漂移）",
        TOOL,
        "//         8  结婚（Married）但两人还不是道侣\n",
        "",
        "test_engine_code_table_says_zero_means_go",
    ),
    (
        "③ Python 措辞表少一个码（两侧表漂移）",
        TR,
        '    8: "结婚前必须先结为道侣",\n',
        "",
        "test_comment_table_and_renderer_table_agree",
    ),
    (
        "④ 预检短路条件用反的读法（0 才收口 ⇒ 引擎拒绝被当成同意、照样落刀）",
        TOOL,
        '            if (precheck > 0)\n            {\n                try { ModMain.P("[Relation] 预检拒绝',
        '            if (precheck == 0)\n            {\n                try { ModMain.P("[Relation] 预检拒绝',
        "test_zero_is_not_treated_as_refusal_anywhere",
    ),
    # ── 预检：一次性实例 + 只问不落刀（事故二的第二层根因）──
    (
        "⑤ ★事故原样复现：预检与落刀共用同一个 act（isCreateCall 免检直通）",
        TOOL,
        SNAP_KNIFE,
        "                    var act = new UnitActionRelationSet(player, type, addClose);\n"
        "                    try { precheck = npc.IsCreateAction(act, false); } catch { }\n"
        "                    string relBeforeKnife = RelationOf(npc, player);\n"
        "                    int createRet;\n"
        "                    try { createRet = npc.CreateAction(act, true); }",
        "test_preflight_never_shares_the_instance_with_the_knife",
    ),
    (
        "⑥ 预检助手改用可复用实例（把免检标记留在外面）",
        TOOL,
        "try { return actor.IsCreateAction(new UnitActionRelationSet(other, type, addClose), false); }",
        "var shared = new UnitActionRelationSet(other, type, addClose);\n"
        "            try { return actor.IsCreateAction(shared, false); }",
        "test_preflight_never_shares_the_instance_with_the_knife",
    ),
    (
        "⑦ 免窗落刀复用预检实例",
        TOOL,
        "try { createRet = npc.CreateAction(new UnitActionRelationSet(other, type, addClose), false); }",
        "try { createRet = npc.CreateAction(dact, false); }",
        "test_preflight_never_shares_the_instance_with_the_knife",
    ),
    (
        "⑧ 解除落刀复用预检实例",
        TOOL,
        "                        created = npc.CreateAction(act, false);",
        "                        created = npc.CreateAction(probe, false);",
        "test_preflight_never_shares_the_instance_with_the_knife",
    ),
    (
        "⑨ 预检拒绝照旧落刀（引擎说不能建也不管）",
        TOOL,
        PROBE_CUT,
        "            int precheck = ProbeRelationSet(npc, player, type, addClose);\n            if (false)",
        "test_preflight_refusal_does_not_reach_the_knife",
    ),
    (
        "⑩ 免窗路径不按预检收口",
        TOOL,
        DIRECT_CUT,
        "            int precheck = ProbeRelationSet(npc, other, type, addClose);\n            if (false)",
        "test_preflight_refusal_does_not_reach_the_knife",
    ),
    (
        "⑪ 「没问出来」（-2）被当成拒绝 ⇒ 本来能结的关系被拦死",
        TOOL,
        PROBE_CUT,
        "            int precheck = ProbeRelationSet(npc, player, type, addClose);\n            if (precheck != 0)",
        "test_preflight_unknown_still_goes_to_the_knife",
    ),
    (
        "⑫ 预检整个拿掉（回到事故一的裸落刀）",
        TOOL,
        "            int precheck = ProbeRelationSet(npc, player, type, addClose);\n",
        "            int precheck = -2;   // mutated: 不做预检\n",
        "test_preflight_refusal_does_not_reach_the_knife",
    ),
    (
        "⑬ 预检拒绝时把 create_ret 写成 0（等于对外说「引擎执行了」）",
        TOOL,
        '                    ["create_ret"] = -1,                        // -1 = 预检就拒，没落刀',
        '                    ["create_ret"] = 0,',
        "test_preflight_refusal_does_not_reach_the_knife",
    ),
    (
        "⑭ 免窗预检拒绝也写成「引擎执行了」",
        TOOL,
        '                    ["precheck"] = precheck, ["create_ret"] = -1,',
        '                    ["precheck"] = precheck, ["create_ret"] = 0,',
        "test_preflight_refusal_does_not_reach_the_knife",
    ),
    # ── 落刀：只调一次 + 裁决码进载荷 + 快照在落刀前 ──
    (
        "⑮ 回到事故写法：落刀的引擎码没人接",
        TOOL,
        KNIFE,
        "                    try { npc.CreateAction(new UnitActionRelationSet(player, type, addClose), true); }",
        "test_knife_verdict_is_captured_and_passed_through",
    ),
    (
        "⑯ 落刀前不取现状快照（引擎覆盖旧关系后无从回溯）",
        TOOL,
        "                    string relBeforeKnife = RelationOf(npc, player);",
        '                    string relBeforeKnife = "";   // mutated: 快照不取了',
        "test_knife_verdict_is_captured_and_passed_through",
    ),
    (
        "⑰ 载荷不发 create_ret（模型看不到引擎裁决）",
        TOOL,
        '                        ["create_ret"] = createRet,                 // 0 = 引擎已执行；非 0 = 拒绝原因码',
        '                        ["create_ret"] = -1,',
        "test_knife_verdict_is_captured_and_passed_through",
    ),
    (
        "⑱ diag 里的境界另起一套读数（不走 RealmText ⇒ 与 brief/L1 漂移）",
        TOOL,
        'd["npc_realm"] = UnitSnapshot.RealmText(npc);',
        'd["npc_realm"] = g.conf.roleGrade.GetGradeName((int)npc.data.unitData.propertyData.gradeID);',
        "test_diag_reports_realm_and_seat_occupancy_only",
    ),
    (
        "⑲ RealmText 旁路 RealmOf（回到 09-13 那次的越界错读老路）",
        SNAP,
        "try { return RealmOf(((dynamic)u.data).dynUnitData, u.data.unitData.propertyData, null); }",
        "try { return g.conf.roleGrade.GetGradeName((int)u.data.unitData.propertyData.gradeID); }",
        "test_realm_text_reuses_the_panel_reader",
    ),
    (
        "⑳ RelCount 把「读不到」吞成 0（凭空断言「你没有师傅」）",
        TOOL,
        "try { return list == null ? -1 : (int)((dynamic)list).Count; } catch { return -1; }",
        "try { return list == null ? 0 : (int)((dynamic)list).Count; } catch { return 0; }",
        "test_rel_count_never_fakes_zero",
    ),
    (
        "㉑ 某个 op 绕过共用方法（收徒走成拜师的动作类型）",
        TOOL,
        "return StartRelationAction(wub, player, UnitRelationType.Student, op, askText);",
        "return StartRelationAction(wub, player, UnitRelationType.Master, op, askText);",
        "test_all_five_establish_ops_share_the_diagnostics",
    ),
    (
        "㉒ 赠礼的免检退回「靠探测副作用」（随时会被顺手清理掉）",
        TOOL,
        "                try { ((UnitActionBase)giveAction).isCreateCall = true; }",
        "                try { /* mutated: 不再显式免检 */ }",
        "test_give_bypasses_the_broken_gate_explicitly",
    ),
    (
        "㉓ 结婚分支缺现状快照（这条独立路径没人管）",
        TOOL,
        """                                ["relation_before"] = relBeforeMarry,
                                ["relation_after"] = RelationOf(wub, player),
                                ["diag"] = RelationDiag(wub, player),""",
        """                                ["relation_before"] = relBeforeMarry,""",
        "test_marry_reports_the_same_state_snapshot",
    ),
    (
        "㉔ 解除路径在预检后提前收口（字段直写兜底被跳过 ⇒ 解除静默失效）",
        TOOL,
        BREAK_PROBE,
        BREAK_PROBE + '\n                        if (bwPre > 0) return Ok(new JObject { ["op"] = op });   // mutated',
        "test_break_ops_report_the_verdict_but_never_block_on_it",
    ),
    (
        "㉕ 解除成败判据用反的读法（created == 1 才算成功）",
        TOOL,
        "                    if (created == 0 && !stillAfterNative)",
        "                    if (created == 1)",
        "test_break_ops_report_the_verdict_but_never_block_on_it",
    ),
    (
        "㉖ 渲染层读了一个 C# 不发的键（键名漂移 = 模型永远看不到）",
        TR,
        'd.get("npc_realm")',
        'd.get("npc_realm_cn")',
        "test_renderer_reads_only_keys_the_establish_path_emits",
    ),
    # ── 渲染层（text_render）──
    (
        "㉗ 预检拒绝也写成「结下了」（回到事故当时的口气）",
        TR,
        'f"{who}的「{relation}」未能结下：游戏引擎在动手之前就判定这个动作建不起来"',
        'f"{who}的「{relation}」已经结下（引擎码 {pre}）"',
        "test_engine_refusal_is_reported_as_a_refusal",
    ),
    (
        "㉘ 引擎码不翻译（模型只看到一个数字，猜不出为什么）",
        TR,
        'f"（引擎码 {pre}：{_relation_code_cn(pre)}），未落刀，关系簿未改动")',
        'f"（引擎码 {pre}），未落刀，关系簿未改动")',
        "test_engine_refusal_is_reported_as_a_refusal",
    ),
    (
        "㉙ 落刀被驳回也写成「结下了」",
        TR,
        'f"{who}的「{relation}」未能结下：游戏引擎驳回了这次关系变更"',
        'f"{who}的「{relation}」已经结下：游戏引擎驳回了这次关系变更"',
        "test_knife_refusal_and_unread_are_rendered_apart",
    ),
    (
        "㉚ 「引擎说执行了却读不回」写成成功",
        TR,
        '    return f"{who}结为{relation}，但引擎已执行（引擎码 0）却读不回该关系，需人工核对。"',
        '    return f"{who}结为{relation}。"',
        "test_knife_refusal_and_unread_are_rendered_apart",
    ),
    (
        "㉛ 删掉旧载荷分支（两侧升级不同步时行为回归）",
        TR,
        '        return f"{who}结为{relation}，但状态校验未通过（可能未生效，需核对）。"',
        '        return f"{who}结为{relation}。「无裁决码」"',
        "test_old_payload_keeps_the_old_wording",
    ),
    (
        "㉜ ★成功路径不点明「旧关系被换掉了」（事故二真正伤人的地方）",
        TR,
        '            note = f"（引擎把原有的「{before}」关系换成了「{after}」—— 引擎里一对人只记一条关系）"',
        '            note = ""',
        "test_success_is_unchanged_but_replacement_is_called_out",
    ),
    (
        "㉝ 成功路径文案被这次改动带偏",
        TR,
        '        return f"{head}。{note}"',
        '        return f"{head}（已缔结）。{note}"',
        "test_success_is_unchanged_but_replacement_is_called_out",
    ),
    (
        "㉞ 未知的「师傅数」被当成真值（拼出「你已有-1位师傅」）",
        TR,
        "    if pm is not None and pm >= 1:",
        "    if pm is not None and pm != 0:",
        "test_unknown_counts_never_produce_claims",
    ),
    (
        "㉟ 未知的「徒弟数」被当成真值（拼出「门下徒弟已满（-1）」）",
        TR,
        "    if ns is not None and ns >= 3:",
        "    if ns is not None and ns != 0:",
        "test_unknown_counts_never_produce_claims",
    ),
    (
        "㊱ NPC↔NPC 失败也用「你与…」当主语（指错人）",
        TR,
        '    who = f"{target}与{cp}" if cp else f"你与{target}"',
        '    who = f"你与{target}"',
        "test_npc_to_npc_refusal_uses_the_pair_subject",
    ),
    (
        "㊲ 解除类不解释「为什么走了直写」",
        TR,
        "            seg += f\"（原生解除动作没跑起来：引擎码 {_num(data.get('create_ret'))}，本次走字段直写）\"",
        '            seg += ""',
        "test_break_rendering_explains_the_direct_write_path",
    ),
    (
        "㊳ 解除类的引擎码判据用反（0 才解释走直写）",
        TR,
        '        if _num(data.get("create_ret")) not in (None, 0):',
        '        if _num(data.get("create_ret")) in (None, 0):',
        "test_break_rendering_explains_the_direct_write_path",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_relation_diag"


def _run_py(node: str | None):
    cmd = [sys.executable, "-m", "pytest",
           CONTRACT if node is None else f"{CONTRACT}::{node}", "-q", "--no-header"]
    p = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    print("=== 关系裁决契约（C# 接线 + 渲染行为）===")
    files = sorted({m[1] for m in MUTATIONS})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    _BAK.mkdir(exist_ok=True)
    for f in files:
        shutil.copy2(f, _BAK / f.name)

    rc, out = _run_py(None)
    print("基线：exit=", rc)
    if rc != 0:
        print("★ 基线不是绿的，先修基线：\n", out[-1200:])
        return len(MUTATIONS) + 1

    bad = 0
    for name, path, old, new, expect in MUTATIONS:
        for f in files:                      # 每轮先全量还原，防上一轮污染
            shutil.copy2(_BAK / f.name, f)
        src = path.read_text(encoding="utf-8")
        if old not in src:
            print(f"✗ {name}: 找不到待替换文本（测试自身失效，需更新本脚本）")
            bad += 1
            continue
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        rc, out = _run_py(expect)
        ok = rc != 0 and expect in out
        print(f"{'✓' if ok else '✗'} {name} → exit={rc} 命中[{expect}]={expect in out}")
        if not ok:
            bad += 1
            print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])

    for f in files:                          # 还原
        shutil.copy2(_BAK / f.name, f)
    rc, out = _run_py(None)
    print("还原后：exit=", rc)
    if rc != 0:
        print(f"★★ 还原失败！从 {_BAK} 手工恢复")
        return len(MUTATIONS) + 2
    print()
    if bad == 0:
        print(f"全部变异都被捕获（{len(MUTATIONS)}/{len(MUTATIONS)}）")
        return 0
    print(f"有 {bad} 个变异没被捕获（共 {len(MUTATIONS)} 个）—— 测试需要加强")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
