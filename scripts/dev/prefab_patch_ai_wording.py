#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prefab_patch_ai_wording.py —— 把 `UIConfigAi.prefab` 里残存的「AI」字样洗掉（09-18，用户要求）。

## 为什么需要它（以及为什么它不是唯一的一层）

用户反映"界面里不要出现 AI 字样"。查下来**玩家能看到的 "AI" 一共 5 处，全在配置面板的预制件里**：

    BG/Title/TitleLabel                   = "AI 设置"                    ← 窗口标题
    …/Content/LlmTimeoutRow/Label         = "AI 首字超时秒（即时生效）"
    …/Content/LlmTimeoutNsRow/Label       = "AI 单次超时秒·压缩（即时生效）"
    …/Content/LlmBudgetRow/Label          = "AI 硬顶秒·0=不设限"
    …/Content/LlmRetriesRow/Label         = "AI 失败重试次数（即时生效）"

（NPC 面板那枚「AI 对话」按钮、剧情窗那枚、以及状态条上的一串 "AI 服务…" 都是**代码里**写的，
改 C# 就生效，不经过这个脚本 —— 见 `NpcPanelButton.cs` / `DramaAiOption.cs` / `BrainLink.cs`。）

**为什么光改预制件不够**：`.ab` 是 Unity 打出来的，而本机重打 AB 需要 Unity 持有效许可证
（实测被挡：`BatchMode: Unity has not been activated with a valid License.`）。所以已经装在
玩家机器上的那份 AB 里，这 5 个字符串**还是旧的** —— 真正让玩家看到新文案的是运行期改写
（`AbConfigPanel` 的 `RelabelRow` × 4 + 标题那一行）。本脚本管的是**源头**：
下次谁重打 AB，打出来的就是干净文案，不必再靠运行期兜底。

两层的分工（缺一不可，别只留一层）：
  · 运行期改写 → 老 AB 也能立刻变干净（**玩家实际看到的**）；
  · 本脚本     → 预制件源头干净（**下次构建打出来的**）。

## 用法

    python3 scripts/dev/prefab_patch_ai_wording.py --dry-run      # 只看要改什么
    python3 scripts/dev/prefab_patch_ai_wording.py               # 原地改（先备份 .bak_ai_wording）
    python3 scripts/dev/prefab_patch_ai_wording.py --in <源> --out <目标>

幂等：改完再跑一次会报"已无 AI 字样"。找不到任何一处也**不算失败**（预制件可能已被别的
补丁脚本重建过），只在末尾说明。中文一律按预制件的既有写法转成 `\\uXXXX`，不引入裸 UTF-8。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

#: 默认目标 = 打包工程里那份「进游戏唯一真相」（见 docs/reference/ui-workflow.md §1）
DEFAULT_PREFAB = Path(
    "/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程"
    "/ResBuildABProject/Assets/Resources/UI/UIConfigAi.prefab"
)

#: 预览工程那份（存在才一起改；它不进包，但留着旧文案会让下一个人以为改漏了）
PREVIEW_PREFABS = [
    Path("/mnt/f/agent_loop/ui_preview/Assets/Resources/UI/UIConfigAi.prefab"),
]

#: 旧文案 → 新文案。键是**解码后的真实字符串**（脚本自己负责转义），值同。
#: 数值/语义一个字都没动 —— 只去掉 "AI " 前缀与窗口标题里的 AI 二字。
REPLACEMENTS = [
    ("AI 设置", "智能体设置"),
    ("AI 首字超时秒（即时生效）", "首字超时秒（即时生效）"),
    ("AI 单次超时秒·压缩（即时生效）", "压缩超时秒（即时生效）"),
    ("AI 硬顶秒·0=不设限", "硬顶秒·0=不设限"),
    ("AI 失败重试次数（即时生效）", "失败重试次数（即时生效）"),
]


def _esc(text: str) -> str:
    """非 ASCII → `\\uXXXX`（沿用预制件里中文的转义写法，Unity 照读）。"""
    out = []
    for ch in text:
        out.append(ch if ord(ch) < 128 else "\\u%04X" % ord(ch))
    return "".join(out)


def patch_one(path: Path, out: Path, dry: bool) -> int:
    """改一个预制件，返回改动处数。"""
    if not path.is_file():
        print(f"  · 跳过（不存在）：{path}")
        return 0
    text = path.read_text(encoding="utf-8")
    hits = 0
    for old, new in REPLACEMENTS:
        o, n = _esc(old), _esc(new)
        c = text.count(o)
        if c:
            print(f"  {c} 处：{old!r} → {new!r}")
            text = text.replace(o, n)
            hits += c
        elif n in text:
            print(f"  · 已是新文案：{new!r}")
        else:
            print(f"  ⚠ 两者都没有：{old!r}（预制件结构变了？）")
    if not hits:
        print("  ✓ 已无 AI 字样，无需改动")
        return 0
    if dry:
        print(f"  [dry-run] 将改写 {out}（{hits} 处）")
        return hits
    if out == path:
        bak = path.with_suffix(path.suffix + ".bak_ai_wording")
        if not bak.exists():
            shutil.copy2(path, bak)
            print(f"  备份 → {bak.name}")
    out.write_text(text, encoding="utf-8")
    print(f"  已写入 {out}（{hits} 处）")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="洗掉 UIConfigAi.prefab 里的 AI 字样（幂等）")
    ap.add_argument("--in", dest="inp", default=str(DEFAULT_PREFAB), help="源预制件")
    ap.add_argument("--out", dest="out", default="", help="目标预制件（默认与 --in 相同=原地改）")
    ap.add_argument("--dry-run", action="store_true", help="只打印要改什么，不写盘")
    ap.add_argument("--with-preview", action="store_true",
                    help="连预览工程那份一起改（默认只改打包工程那份）")
    args = ap.parse_args()

    src = Path(args.inp)
    dst = Path(args.out) if args.out else src
    print(f"[AI 文案] 目标 = {src}")
    total = patch_one(src, dst, args.dry_run)
    if args.with_preview:
        for p in PREVIEW_PREFABS:
            print(f"[AI 文案] 预览副本 = {p}")
            total += patch_one(p, p, args.dry_run)
    print(f"== 共 {total} 处 ==" if total else "== 无需改动 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
