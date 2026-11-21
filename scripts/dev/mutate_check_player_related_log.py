"""「与玩家相关」经历 · 变异自检（本仓标准验证法：变异必红，还原必绿）

13 个契约各挑一条承重墙做变异，验证测试真的钉得住。
判据：每条变异至少让一个测试变红；还原后全绿。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# ★ 必须用 sys.executable（宿主解释器），不许写死 "python3"：Windows 上 python3 常是
#   Microsoft Store 的应用执行别名 stub（WindowsApps\python3.exe）——它什么都不执行、
#   恒以 9009 退出。后果是 `_run_py` 的 returncode 恒非 0 ⇒ **每个变异都假报"红"**、
#   变异自检完全失真（2026-09-21 实证：19/19"全捕获"里 A 实际是漏网鱼）。
# ★ 测试面必须带上 test_log_layer_contract.py：UnitSnapshot 是两个特征共用的文件，
#   变异若误击「近况」（RecentTexts），钉它的契约在那边（2026-09-21 实证：变异 N
#   锚点歧义误击 RecentTexts 时，只跑本特征契约文件的结果是"全绿"，漏检）。
TEST = [sys.executable, "-m", "pytest",
        "tests/test_player_related_log_contract.py",
        "tests/test_log_layer_contract.py",
        "-q", "--no-header", "-x"]

UNIT = ROOT / "csharp" / "game" / "UnitSnapshot.cs"
GAME = ROOT / "csharp" / "game" / "GameContext.cs"
TOOLEX = ROOT / "csharp" / "tools" / "ToolExecutor.cs"
SP = ROOT / "agent" / "system_prompt.py"
SCHEMAS = ROOT / "tools" / "schemas.py"
RENDER = ROOT / "tools" / "text_render.py"


def _swap(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    assert old in src, f"锚点不在 {path.name}：{old[:60]}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _run_py() -> tuple[bool, str]:
    r = subprocess.run(TEST, cwd=ROOT, capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    return r.returncode != 0, out   # 返回 **红**（有测试失败 = 变异被捕获）


MUTATIONS: list[tuple[str, list[tuple[Path, str, str]]]] = [
    # A. 判据搬到渲染之后 —— RawDataText 只回已清洗文本，unitID 已蒸发，判不出玩家引用
    ("A：RawDataText 只回 CleanLogText 之后的文本（判定搬到渲染之后）",
     [(UNIT, 'try { string s = ((dynamic)d).GetLogString(); if (!string.IsNullOrEmpty(s)) return s; } catch { }',
       'try { string s = CleanLogTextPublic((string)((dynamic)d).GetLogString()); if (!string.IsNullOrEmpty(s)) return s; } catch { }')]),

    # B. 按玩家真名匹配 —— 同名 NPC 张冠李戴
    ("B：FieldHasPid 退化为 Contains 子串（可误伤）+ 允许按显示名判",
     [(UNIT, "if (field == pid) return true;", "if (field.Contains(pid)) return true;")]),

    # C. 空 pid 不拦 —— 空串当通配符，全表命中
    ("C：MentionsPlayer 不拦空 pid",
     [(UNIT, "if (string.IsNullOrEmpty(pid) || item == null) return false;",
       "if (item == null) return false;")]),

    # D. 原始层兜底不打标 —— raw 层命中的玩家往来会漏
    ("D：LogsArr 原始层不打 with_player 标",
     [(UNIT, 'if (RawRowHasPid(item, pid)) jo["with_player"] = true;', "")]),

    # E. 暂存层不打标 —— 本月刚写未落库的玩家往来会漏
    ("E：暂存层路径不打 with_player 标",
     [(UNIT, 'if (MentionsPlayer(item, pidP)) jo["with_player"] = true;', "")]),

    # F. 不命中也写 false —— 条目膨胀，且掩盖"缺键即 false"的约定
    ("F：不命中也写 with_player=false（键膨胀）",
     [(UNIT, 'if (MentionsPlayer(item, pid)) jo["with_player"] = true;',
       'jo["with_player"] = MentionsPlayer(item, pid);')]),

    # G. ToolExecutor 自己改写 with_player —— 绕开"判定必须在 CleanLogText 之前"的铁律
    ("G：ToolExecutor 越权改写 with_player",
     [(TOOLEX, 'try { return (bool)((JObject)it)["with_player"] == true; } catch { return false; }',
       'return true;')]),

    # H. PageLogs 不放行 with_me —— 模型传了 with_me，两个桶都被滤光（items 恒空）
    ("H：filterHit 不认 with_me",
     [(TOOLEX, 'if (filter == "with_me") return true;', "")]),

    # I. PageLogs 接了 with_me 却不按条目筛 —— 退化成第二个 all
    ("I：两桶收集不接 PlayerHit（with_me 退化成 all）",
     [(TOOLEX, 'foreach (var it in GetArr("vital")) if (MonthHit(it) && PlayerHit(it))',
       'foreach (var it in GetArr("vital")) if (MonthHit(it))'),
      (TOOLEX, 'foreach (var it in GetArr("regular")) if (MonthHit(it) && PlayerHit(it))',
       'foreach (var it in GetArr("regular")) if (MonthHit(it))')]),

    # J. 渲染层不认 with_me 的中文表头
    ("J：渲染层 fmap 丢了 with_me",
     [(RENDER, '"all": "全部", "with_me": "与我相关"', '"all": "全部"')]),

    # K. 渲染层不给 with_me 配 ★ 图例 —— 重要件混在里面，模型不知道 ★ 是什么
    ("K：mark_important 不含 with_me",
     [(RENDER, 'mark_important = flt in ("all", "with_me")', 'mark_important = flt == "all"')]),

    # L. GameContext 不发 recent_with_player —— 模型永远看不到（渲染层读的键 C# 不发）
    ("L：GameContext 不发 recent_with_player",
     [(GAME, '["recent_with_player"] = UnitSnapshot.RecentWithPlayer(ud.unitID),', "")]),

    # M. RecentWithPlayer 不按 with_player 过滤 —— 退化成第二个「近况」
    ("M：RecentWithPlayer 不筛 with_player",
     [(UNIT, 'if (!wp) continue;', "")]),

    # N. RecentWithPlayer 不去重 —— 两桶各存一条同月同文时会重复列出
    #    ★ 锚点必须带前文 `if (!wp) continue;` 消歧：`if (!seen.Add(mm + "|" + t)) continue;`
    #    这行在 `RecentTexts`（近况，L493）里**逐字同款**，裸锚 `replace(…, 1)` 会误击那边
    #    （2026-09-21 实证：误击后 RecentWithPlayer 纹丝没动，契约全绿 = 假阴性）。
    ("N：RecentWithPlayer 不去重",
     [(UNIT,
       'if (!wp) continue;\n'
       '                        string t = (string)it["text"] ?? "";\n'
       '                        if (t.Length == 0 || t == "(无文本)") continue;\n'
       '                        int mm = 0;\n'
       '                        int.TryParse((string)it["month"] ?? "", out mm);\n'
       '                        if (!seen.Add(mm + "|" + t)) continue;',
       'if (!wp) continue;\n'
       '                        string t = (string)it["text"] ?? "";\n'
       '                        if (t.Length == 0 || t == "(无文本)") continue;\n'
       '                        int mm = 0;\n'
       '                        int.TryParse((string)it["month"] ?? "", out mm);')]),

    # O. RecentWithPlayer 不降序 —— 「近况退回陈年旧事」原样重演
    ("O：RecentWithPlayer 改成升序",
     [(UNIT, 'merged.Sort((a, b) => ((int)b["m"]).CompareTo((int)a["m"]));\n                int take = Math.Min(RecentPlayerCap, merged.Count);',
       'merged.Sort((a, b) => ((int)a["m"]).CompareTo((int)b["m"]));\n                int take = Math.Min(RecentPlayerCap, merged.Count);')]),

    # P. schema 没列 with_me —— C# 处理了但模型看不见
    ("P：schema 的 log_filter 丢了 with_me",
     [(SCHEMAS, '"enum": ["important", "regular", "all", "with_me"],',
       '"enum": ["important", "regular", "all"],')]),

    # Q. 段没登记进 _CTX_SEGMENTS —— 差分/压缩找不到这段
    ("Q：_CTX_SEGMENTS 丢了 recent_with_player",
     [(SP, '"player", "where", "recent_with_player", "recent"',
       '"player", "where", "recent"')]),

    # R. 段标签没登记 —— ctx_segment_of_label 反查认不出，历史恢复会静默跳过
    ("R：_CTX_LABELS 丢了「与玩家相关」",
     [(SP, '"recent_with_player": "与玩家相关",', "")]),

    # S. format_l1_context 不读 raw.recent_with_player
    ("S：format_l1_context 不取数",
     [(SP, 'wp_raw = raw.get("recent_with_player")', 'wp_raw = raw.get("__never__")')]),
]


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="wp_backup_"))
    backups = {}
    for p in (UNIT, GAME, TOOLEX, SP, SCHEMAS, RENDER):
        backups[p] = tmpdir / p.name
        shutil.copy2(p, backups[p])

    results = []
    try:
        for name, edits in MUTATIONS:
            try:
                for path, old, new in edits:
                    _swap(path, old, new)
                red, out = _run_py()
            finally:
                for path, _o, _n in edits:
                    shutil.copy2(backups[path], path)
            results.append((name, red, out))
            print(("✓ 红 " if red else "✗ 未红 ") + name)
            if not red:
                print("    " + out.strip().splitlines()[-1] if out.strip() else "    (无输出)")
    finally:
        for path, bak in backups.items():
            shutil.copy2(bak, path)

    print("\n=== 变异自检 ===")
    caught = [n for n, red, _ in results if red]
    missed = [n for n, red, _ in results if not red]
    print(f"捕获 {len(caught)}/{len(results)}")
    if missed:
        for n in missed:
            print("  ✗ 未捕获：" + n)
        return 1

    # ★ `_run_py` 返回的是**红**语义（rc≠0 = 有测试失败）——这里必须取反判"绿"。
    #   （2026-09-21 修：原写 `green, out = _run_py()` 把红当绿，测试**全过**时反而
    #   报"还原后未全绿"，脚本永远以失败收场 —— 且越绿越必现。）
    still_red, out = _run_py()
    if still_red:
        print("✗ 还原后未全绿：")
        print(out[-1500:])
        return 1
    print("✓ 还原后全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
