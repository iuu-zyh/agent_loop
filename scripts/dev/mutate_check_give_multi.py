#!/usr/bin/env python3
"""变异验证 —— 多件赠送（09-17 用户实测"只有第一个物体成功，工具却说成功"）。

**为什么必须有**：这次的失败是**两层静默**叠出来的 —— 引擎只处理一件（不报错）、
我们的报告按"愿望单"宣布全部收下。任何一层单独"看起来正常"都足以让模型在游戏里
说"都给你了"。所以验收线是"把实现逐条回退，对应断言真的变红"。

覆盖：`tests/test_give_multi_contract.py`（10 项：C# 接线 7 + 渲染行为 3）。

用法：
    python3 scripts/dev/mutate_check_give_multi.py

注意：会**临时改源码再还原**（备份在系统临时目录），跑完确认末行是"全部变异都被捕获"。
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
TR = ROOT / "tools/text_render.py"
CONTRACT = "tests/test_give_multi_contract.py"

MUTATIONS = [
    # ── C# 接线 ──
    (
        "① 回到事故形态：多件也落回原生动作（只送出一件）",
        TOOL,
        "if (giveList.Count > 1)\n                {\n                    if (ShowDramaService.AutoConfirm)",
        "if (false)\n                {\n                    if (ShowDramaService.AutoConfirm)",
        "test_multi_item_no_longer_goes_through_the_native_action",
    ),
    (
        "② 多件分支不用自制窗（换成别的调用）",
        TOOL,
        """                    JObject opened = Claimed(wub, () => ShowDramaService.ShowConfirm(
                        ModIds.DramaEconomyItemBase,""",
        """                    JObject opened = Claimed(wub, () => (JObject)null /*mutated: 不走自制窗*/); if (false) ShowDramaService.ShowConfirm(
                        ModIds.DramaEconomyItemBase,""",
        "test_multi_item_no_longer_goes_through_the_native_action",
    ),
    (
        "③ 窗没开起来时不回滚（拆出来的道具凭空消失）",
        TOOL,
        """                        RollbackGive(wub, giveList);
                        return Fail("多件赠送的确认窗没能打开，道具已原样放回背包",""",
        """                        return Fail("多件赠送的确认窗没能打开，道具已原样放回背包",""",
        "test_rolled_back_whenever_the_items_left_the_bag",
    ),
    (
        "④ 入包异常时不回滚（失败即吞道具）",
        TOOL,
        """            catch (Exception e)
            {
                RollbackGive(giver, giveList);
                return Fail("道具已从背包取出但赠送失败，已原样放回：" + e.Message,""",
        """            catch (Exception e)
            {
                return Fail("道具已从背包取出但赠送失败，已原样放回：" + e.Message,""",
        "test_rolled_back_whenever_the_items_left_the_bag",
    ),
    (
        "⑤ accepted_count 报成 1（引擎真值被写死）",
        TOOL,
        '["accepted_count"] = giveList.Count,',
        '["accepted_count"] = 1,   // mutated',
        "test_result_reports_engine_truth_and_full_delivery",
    ),
    (
        "⑥ 不发 delivered_all（渲染层没法区分「全到」与「只到一件」）",
        TOOL,
        '["delivered_all"] = true,',
        '',
        "test_result_reports_engine_truth_and_full_delivery",
    ),
    (
        "⑦ 好感加在送礼方身上（方向写反：收礼的语义丢了）",
        TOOL,
        'ApplyGiftIntim(receiver, giver, "多件窗")',
        'ApplyGiftIntim(giver, receiver, "多件窗")',
        "test_side_effects_are_replicated_with_native_keys",
    ),
    (
        "⑧ 收礼方日志写成送礼方的 key（近况里看不到「我收到了」）",
        TOOL,
        'g.world.unitLog.AddLogData(receiver, "eventLogRoleGive21312", vals, cd);',
        'g.world.unitLog.AddLogData(receiver, "eventLogRoleGive21311", vals, cd);',
        "test_side_effects_are_replicated_with_native_keys",
    ),
    (
        "⑨ 日志缺单位上下文（{name|A}/{name|B} 没人填）",
        TOOL,
        "var cd = new UnitConditionData(giver, receiver);",
        "var cd = new UnitConditionData(giver, giver);   // mutated",
        "test_side_effects_are_replicated_with_native_keys",
    ),
    (
        "⑩ 好感基准常量漂移（配表基准 15，代码改成 0）",
        TOOL,
        "private const int IntimStepDefault = 15;",
        "private const int IntimStepDefault = 0;",
        "test_intim_constant_comes_from_conf_and_is_probed",
    ),
    (
        "⑪ 删掉原生路径的好感探针（只剩一侧，没法核对配表值）",
        TOOL,
        "ModMain.P(\"[Give] 好感探针 原生赠礼 收礼方对送礼方 \" + intimBeforeNative",
        "ModMain.P(\"[Give] 原生赠礼完成 \" + intimBeforeNative",
        "test_intim_constant_comes_from_conf_and_is_probed",
    ),
    # ── 好感写入层（10-12 建，10-15 改口径「引擎换算、只回读」）──
    (
        "⑲ 写入层又改回 SetIntim 写绝对值（绕过引擎的 RoleClose 衰减）",
        TOOL,
        "                if (delta > 0) rel.AddIntim(oid, delta, 0, \"\", true);",
        "                rel.SetIntim(oid, delta); if (false) rel.AddIntim(oid, delta, 0, \"\", true);",
        "test_intim_constant_comes_from_conf_and_is_probed",
    ),
    (
        "⑳ 多件窗不再写收礼方那份好感（道具收了、人情不记）",
        TOOL,
        'int got = ApplyGiftIntim(receiver, giver, "多件窗");',
        "int got = 0;",
        "test_side_effects_are_replicated_with_native_keys",
    ),
    (
        "㉑ 赠礼值不再读配表（写死 15：官方调表即漂移）",
        TOOL,
        "try { v = ConfIntim(g.conf.roleGive.closeParaA, IntimStepDefault); } catch { }",
        "v = IntimStepDefault;",
        "test_intim_constant_comes_from_conf_and_is_probed",
    ),
    (
        "㉒ 单件原生路径又回来「补差」（10-15 已定案只回读，不得再校正）",
        TOOL,
        "                            int want = GiftIntimValue();",
        "                            ApplyGiftIntim(player, wub, \"单件原生·补差\"); int want = GiftIntimValue();",
        "test_intim_constant_comes_from_conf_and_is_probed",
    ),
    # ── 未知实得收口（10-15 复核新增：MinValue 垃圾值不许漏进结果）──
    (
        "㉓ PutIntim 的未知过滤被拆（-2147483648 漏进结果字段）",
        TOOL,
        "if (o != null && key != null && delta != int.MinValue) o[key] = delta;",
        "o[key] = delta;   // mutated",
        "test_unknown_delta_never_leaks_into_result_fields",
    ),
    (
        "㉔ 收口点被绕开（某路径直接把写入层返回值塞进结果字段）",
        TOOL,
        'PutIntim(okWindow, "intim", intimWindow);',
        'okWindow["intim"] = intimWindow;   // mutated',
        "test_unknown_delta_never_leaks_into_result_fields",
    ),
    # ── 渲染层 ──
    # ── 好感渲染（10-15 补显示）──
    (
        "㉕ 好感句方向写反（收礼方→送礼方 变成 送礼方→收礼方）",
        TR,
        'note = _intim_delta(target, giver, data.get("intim"))',
        'note = _intim_delta(giver, target, data.get("intim"))',
        "test_renderers_surface_intim",
    ),
    (
        "㉖ 讨要双边只渲染一边（交付方的不满消失：被搜刮的人还越发喜欢他）",
        TR,
        '_intim_delta("你", target, data.get("intim_upset"))) if p)',
        'None) if p)',
        "test_renderers_surface_intim",
    ),
    (
        "⑫ 判据反向：引擎真值分支永不进入（回到「愿望单」口径）",
        TR,
        "        if accepted_count is not None and accepted is not False and accepted_count < len(ok_props):",
        "        if accepted_count is not None and accepted is not False and accepted_count > len(ok_props):",
        "test_renderer_reports_only_what_the_engine_accepted",
    ),
    (
        "⑬ 「收到的」当「没送到的」报（got/miss 切反）",
        TR,
        "            got = fmt(ok_props[:accepted_count])\n            miss = fmt(ok_props[accepted_count:])",
        "            got = fmt(ok_props[accepted_count:])\n            miss = fmt(ok_props[:accepted_count])",
        "test_renderer_reports_only_what_the_engine_accepted",
    ),
    (
        "⑭ 「收下的」按愿望单全列（引擎真值只用来算未送达）",
        TR,
        "            got = fmt(ok_props[:accepted_count])",
        "            got = fmt(ok_props)",
        "test_renderer_reports_only_what_the_engine_accepted",
    ),
    (
        "⑮ 不解释「为什么没送到」（只说未送达，模型会以为是被拒）",
        TR,
        "seg += f\"；未送达：{miss}（引擎一次只处理一件，其余已退回发起方）\"",
        "seg += f\"；未送达：{miss}\"",
        "test_renderer_reports_only_what_the_engine_accepted",
    ),
    (
        "⑯ 全拒收被新分支抢走（旧行为回归）",
        TR,
        "        if accepted_count is not None and accepted is not False and accepted_count < len(ok_props):",
        "        if accepted_count is not None and accepted_count < len(ok_props):",
        "test_old_shapes_do_not_regress",
    ),
    (
        "⑰ 单件成功文案被改写（旧行为回归）",
        TR,
        '            segs.append(f"{target}收下了赠礼（{prop_text}）")',
        '            segs.append(f"{target}收下了赠礼")',
        "test_old_shapes_do_not_regress",
    ),
    (
        "⑱ 缺 accepted_count 时也做推断（旧载荷被当新载荷处理）",
        TR,
        "        if accepted_count is not None and accepted is not False and accepted_count < len(ok_props):",
        "        if True:",
        "test_no_accepted_count_means_old_payload",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_give_multi"


def _run(node: str | None):
    cmd = [sys.executable, "-m", "pytest",
           CONTRACT if node is None else f"{CONTRACT}::{node}", "-q", "--no-header"]
    p = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    print("=== 多件赠送契约（C# 接线 + 渲染行为）===")
    files = sorted({m[1] for m in MUTATIONS})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    _BAK.mkdir(exist_ok=True)
    for f in files:
        shutil.copy2(f, _BAK / f.name)

    rc, out = _run(None)
    print("基线：exit=", rc)
    if rc != 0:
        print("★ 基线不是绿的，先修基线：\n", out[-1200:])
        return len(MUTATIONS) + 1

    bad = 0
    for name, path, old, new, expect in MUTATIONS:
        for f in files:
            shutil.copy2(_BAK / f.name, f)      # 每轮全量还原，防上一轮污染
        src = path.read_text(encoding="utf-8")
        if old not in src:
            print(f"✗ {name}: 找不到待替换文本（测试自身失效，需更新本脚本）")
            bad += 1
            continue
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        rc, out = _run(expect)
        ok = rc != 0 and expect in out
        print(f"{'✓' if ok else '✗'} {name} → exit={rc} 命中[{expect}]={expect in out}")
        if not ok:
            bad += 1
            print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])

    for f in files:
        shutil.copy2(_BAK / f.name, f)
    rc, out = _run(None)
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
