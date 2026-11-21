#!/usr/bin/env python3
"""变异验证 —— 物品/装备介绍占位符（`&608211_kcll&` 那类）的测试（09-17）。

**为什么必须有这个脚本**：本仓库的验收标准不是"测试跑绿了"，而是"**把实现逐个回退，对应测试真的变红**"。
这次的失败模式全是**静默**的：占位符原样喂模型（用户截图那种），或者**猜错列**给出一个"看着正常"的数
（`&608203_qtsh&` 境界1=80 / 境界10=5454，差 68 倍）。留这个脚本，下次动介绍取值/扫描时一条命令重跑。

覆盖的测试：
  · `tests/test_item_desc_sigil_contract.py` —— C# 接线（两处读取点、顺序、共享扫描器、列来源=境界、
    绝不猜、★留痕、对照日志、剥富文本）。
  · `csharp/tests/PropsSigilCoreTests` —— 取值列决策 + 扫描器的判定表（`run_csharp_tests.py` 跑）。

用法：
    python3 scripts/dev/mutate_check_props_sigil.py          # 全跑（默认）
    python3 scripts/dev/mutate_check_props_sigil.py py       # 只跑 Python 接线侧
    python3 scripts/dev/mutate_check_props_sigil.py cs       # 只跑 C# 判定表侧

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
UI = ROOT / "csharp/game/UnitSnapshot.cs"
CORE = ROOT / "csharp/game/PropsSigilCore.cs"

PY_CONTRACT = "tests/test_item_desc_sigil_contract.py"
CS_PROJ = "PropsSigilCoreTests"

# ---------------------------------------------------------------------------
# Python 接线侧：回退实现，期望对应测试变红
# ---------------------------------------------------------------------------
PY_MUTATIONS = [
    (
        "① 背包介绍退回裸 LS（占位符原样喂模型 = 用户投诉的那个）",
        UI,
        'PropsDescText((DataProps.PropsData)p, (object)p.propsItem, d, "inventory")',
        'GameTool.LS(d)',
        "test_inventory_and_equips_go_through_the_resolver",
    ),
    (
        "② 装备介绍退回裸 LS",
        UI,
        'PropsDescText((DataProps.PropsData)p, (object)p.propsItem, d, "equips")',
        'GameTool.LS(d)',
        "test_inventory_and_equips_go_through_the_resolver",
    ),
    (
        "③ 不再剥富文本（565 条 desc 的 <g>/<color>/<size> 直接进模型）",
        UI,
        "            string clean = StripRichTags(outText);",
        "            string clean = outText;",
        "test_rich_text_is_stripped_for_items_too",
    ),
    (
        "④ 拿「物品品质」当列（残/真 品质都是 2 → 算成同品阶的数）",
        UI,
        '                if (type == 1) it = g.conf.itemPill.GetItem(propsID);',
        '                if (type == 1) return (int)((dynamic)propsItem).level;   // mutated: 品质当境界',
        "test_column_comes_from_realm_not_quality",
    ),
    (
        "⑤ 境界未知时猜第一列（Ambiguous → 直接取值）",
        UI,
        """                PropSigilMiss(key, vdGrade, confGrade);
                return null;""",
        """                return ApplyValueScale(RowValues(g.conf.battleSkillValue.GetItem("&" + key))[0], ValueScaleOf(key));""",
        "test_shared_core_owns_the_never_guess_rule",
    ),
    (
        "⑥ 十列只取前九个（'全同判定'失真 → 该不猜的时候猜了）",
        UI,
        "            try { v[9] = d.value10; } catch { }",
        "            // mutated: 少取一列",
        "test_column_read_covers_all_ten_realms",
    ),
    (
        "⑦ 解析抛异常时落空（介绍整条消失）",
        UI,
        """                ModMain.P("[Props] 介绍占位符解析抛异常 slot=" + slotTag + " id=" + propsID + "：" + e.Message);
                outText = d;      // 失败退回"只做了 LS"的原文，绝不落空""",
        """                ModMain.P("[Props] 介绍占位符解析抛异常 slot=" + slotTag + " id=" + propsID + "：" + e.Message);
                outText = null;""",
        "test_residual_and_missing_grade_are_logged_never_silent",
    ),
    (
        "⑧ 境界未知不留痕（静默留占位符）",
        UI,
        '                ModMain.P("[Props] ★介绍占位符境界未知★ key=" + key',
        '                if (false) ModMain.P("[Props] ★介绍占位符境界未知★ key=" + key',
        "test_residual_and_missing_grade_are_logged_never_silent",
    ),
    (
        "⑨ 删掉真机对照日志（列选得对不对再也无从判断）",
        UI,
        '                                  + " vdGrade=" + vdGrade + " confGrade=" + confGrade',
        '                                  + ""',
        "test_one_shot_comparison_log_exists_for_real_machine",
    ),
    (
        "⑩ 顺序颠倒：先剥标签再解析（记号被洗掉一半）",
        UI,
        """            string outText = d;
            try
            {
                if (d.IndexOf('&') >= 0)
                {
                    // 闭包捕获本件的 vd/境界 —— 扫描器与技能说明共用同一段（PropsSigilCore.Scan）
                    var scan = PropsSigilCore.Scan(d,""",
        """            string outText = StripRichTags(d);
            try
            {
                if (false)
                {
                    var scan = PropsSigilCore.Scan(d,""",
        "test_desc_pipeline_order_is_ls_then_sigils_then_strip",
    ),
    (
        "⑪ 物品路径自己写一份扫描（不再与技能说明共用）",
        UI,
        """                    var scan = PropsSigilCore.Scan(d,
                        key => PropsScaledValue(key, vd, vdGrade, confGrade), LsOrNull);""",
        """                    var scan = new PropsSigilCore.ScanResult { Text = d, Replaced = 0, Residual = 0 };""",
        "test_shares_the_same_scanner_as_skill_desc",
    ),
    (
        "⑫ 运行时 vd 不再由道具自身构造（列来源整条断掉）",
        UI,
        "            try { vd = new BattleSkillValueData(propsData); } catch { }",
        "            try { vd = null; } catch { }",
        "test_column_comes_from_realm_not_quality",
    ),
]

# ---------------------------------------------------------------------------
# C# 判定表侧：改共享核，期望 `PropsSigilCoreTests` 变红
# ---------------------------------------------------------------------------
CS_MUTATIONS = [
    (
        "① 配置表境界不再优先（Excel 表头「道具境界」那条来源失效）",
        CORE,
        """            int c = NormGrade(confGrade);
            if (c > 0) return c;
            return NormGrade(vdGrade);""",
        """            return NormGrade(vdGrade);""",
        "__csharp__",
    ),
    (
        "② 境界不校验上界（11 被当成第 11 列）",
        CORE,
        "return (grade >= GradeMin && grade <= GradeMax) ? grade : 0;",
        "return (grade >= GradeMin) ? grade : 0;",
        "__csharp__",
    ),
    (
        "③ 境界不校验下界（-3 被当成列号）",
        CORE,
        "return (grade >= GradeMin && grade <= GradeMax) ? grade : 0;",
        "return (grade <= GradeMax) ? grade : 0;",
        "__csharp__",
    ),
    (
        "④ 境界未知时一律兜底第一列（= 猜，数值差几十倍）",
        CORE,
        """            if (allEqual)
            {
                r.Kind = PickKind.Ok;
                r.Grade = GradeMin;
                r.Value = first;
                return r;
            }
            r.Kind = PickKind.Ambiguous;
            return r;""",
        """            r.Kind = PickKind.Ok;
            r.Grade = GradeMin;
            r.Value = first;
            return r;""",
        "__csharp__",
    ),
    (
        "⑤ 该格为空也照样取值（空串冒充数值）",
        CORE,
        "            if (string.IsNullOrEmpty(v)) return r;       // 该格为空 → 当没有，留占位符",
        "            if (false) return r;",
        "__csharp__",
    ),
    (
        "⑥ 整行为空也照样取值（空串冒充数值）",
        CORE,
        "        if (string.IsNullOrEmpty(first)) return r;",
        "        if (false) return r;",
        "__csharp__",
    ),
    (
        "⑦ 列数不足也不防（短数组越界/误判）",
        CORE,
        "        if (values == null || values.Length < GradeMax) return r;",
        "        if (values == null) return r;",
        "__csharp__",
    ),
    (
        "⑧ LS 未命中不再判掉（把 key 当正文喂模型）",
        CORE,
        "                        if (!string.IsNullOrEmpty(rep) && rep == inner) rep = null;",
        "                        if (false) rep = null;",
        "__csharp__",
    ),
    (
        "⑨ 残留不再计数（真正没替换掉的记号静默留下）",
        CORE,
        "        res.Residual = CountSigils(res.Text);",
        "        res.Residual = 0;",
        "__csharp__",
    ),
    (
        "⑩ 空记号 `&&` 被当成占位符（无中生有）",
        CORE,
        "                if (close > i + 1)",
        "                if (close > i)",
        "__csharp__",
    ),
    (
        "⑪ `$key$` 不计入残留（本地化没替换成功却报告'已干净'）",
        CORE,
        "            if (s[i] != '&' && s[i] != '$') continue;",
        "            if (s[i] != '&') continue;",
        "__csharp__",
    ),
    (
        "⑫ 回调抛异常直接冒泡（整条介绍消失）",
        CORE,
        "                        try { rep = valueOf(inner); } catch { }",
        "                        rep = valueOf(inner);",
        "__csharp__",
    ),
    (
        "⑬ 文本/数值两族来源互换（$key$ 当数值查）",
        CORE,
        "                    if (c == '$')",
        "                    if (c == '&')",
        "__csharp__",
    ),
    (
        "⑭ 成功替换数不再累计（日志里永远显示替换 0 个）",
        CORE,
        "                    if (!string.IsNullOrEmpty(rep)) { o.Append(rep); res.Replaced++; i = close + 1; continue; }",
        "                    if (!string.IsNullOrEmpty(rep)) { o.Append(rep); i = close + 1; continue; }",
        "__csharp__",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_props_sigil"

# 每个变异落在哪个测试文件：按节点名查表（不猜、不重复）
_NODE_FILE = {m[4]: PY_CONTRACT for m in PY_MUTATIONS}


def _run_py(node: str | None):
    if node is None:
        cmd = [sys.executable, "-m", "pytest", PY_CONTRACT, "-q", "--no-header"]
    else:
        cmd = [sys.executable, "-m", "pytest", f"{PY_CONTRACT}::{node}", "-q", "--no-header"]
    p = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    return p.returncode, p.stdout + p.stderr


def _run_cs():
    """跑 PropsSigilCore 判定表（走 run_csharp_tests.py，它会先清 obj/bin 强制重编）。"""
    p = _mutate_lib.run(
        [sys.executable, "scripts/dev/run_csharp_tests.py", "--only", CS_PROJ, "--quiet"],
        cwd=str(ROOT), capture_output=True, text=True, errors="replace",
    )
    return p.returncode, p.stdout + p.stderr


def _check_set(label: str, mutations) -> int:
    print(f"\n=== {label} ===")
    files = sorted({m[1] for m in mutations})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    _BAK.mkdir(exist_ok=True)
    for f in files:
        shutil.copy2(f, _BAK / f.name)

    if any(m[4] == "__csharp__" for m in mutations):
        rc, out = _run_cs()
        print("基线（C# 判定表）：exit=", rc)
        if rc != 0:
            print("★ 基线不是绿的，先修基线：\n", out[-800:])
            return len(mutations) + 1
    else:
        rc, out = _run_py(None)
        print("基线（Python）：exit=", rc)
        if rc != 0:
            print("★ 基线不是绿的，先修基线：\n", out[-800:])
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
        if expect == "__csharp__":
            rc, out = _run_cs()
            ok = rc != 0
            hit = "判定表变红" if ok else "仍绿"
        else:
            rc, out = _run_py(expect)
            hit = f"命中[{expect}]={expect in out}"
            ok = rc != 0 and expect in out
        print(f"{'✓' if ok else '✗'} {name} → exit={rc} {hit}")
        if not ok:
            bad += 1
            print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])

    for f in files:                          # 还原
        shutil.copy2(_BAK / f.name, f)
    if any(m[4] == "__csharp__" for m in mutations):
        rc, out = _run_cs()
    else:
        rc, out = _run_py(None)
    print("还原后：exit=", rc)
    if rc != 0:
        print(f"★★ 还原失败！从 {_BAK} 手工恢复")
        return len(mutations) + 2
    return bad


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    bad = 0
    total = 0
    if which in ("all", "py"):
        total += len(PY_MUTATIONS)
        bad += _check_set("Python 接线侧（UnitSnapshot.cs）", PY_MUTATIONS)
    if which in ("all", "cs"):
        total += len(CS_MUTATIONS)
        bad += _check_set("C# 判定表侧（PropsSigilCore.cs）", CS_MUTATIONS)
    print()
    if bad == 0:
        print(f"全部变异都被捕获（{total}/{total}）")
        return 0
    print(f"有 {bad} 个变异没被捕获（共 {total} 个）—— 测试需要加强")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
