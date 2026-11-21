#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核对「运行期兜底」在**部署的那份 .ab** 上是否真的能命中。

## 为什么需要这个脚本

10-31 那一批 UI 修复（行距 / 富文本 / 九宫格 border / 内边距 / 对齐）全都走"两条腿"：

* **预制件那条腿** —— 改 `.png.meta` 或 `UIChatAi.prefab`，**必须重打 AB** 才进游戏；
* **运行期那条腿** —— `ChatWindow.ApplyBubbleTextLayoutFloor` 在 `Init` 里直接改组件。

而 AB 已经一个半月没重打（包是 09-17 的，prefab 是 10-31 的），**玩家实际跑的只有运行期那条腿**。
"兜底能不能命中"过去一直是靠**读 C# 代码 + 读 prefab** 推断的 —— 但 prefab 是新版、
`.ab` 是旧版，**两者结构未必一致**。代码写得再对，`.ab` 里没有那个节点/组件，
`FixBubbleLayout` 会在 `if (b == null) return false;` 处**静默跳过**，玩家什么都看不到。

所以这个脚本从 `.ab` 的真实字符串表里逐条核对前置结构。

## 用法

    python3 scripts/dev/check_ab_fallback.py                 # 用默认部署路径
    python3 scripts/dev/check_ab_fallback.py <x.ab>          # 指定包

退出码 0 = 全部齐备；1 = 有缺失（并指出是哪条兜底会失效）。

## 局限（诚实边界）

看的是**字符串表**（类型树里的字段名 + 节点名），只能证明"结构存在"，
**不能证明数值对**（比如 padding 当前是多少）。数值那条腿由
`tests/test_chat_bubble_layout_contract.py` 钉在常量层，运行时值只能上机看日志。
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

DEFAULT_AB = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/ModExportData/Mod_Jgmg5L"
                  "/ModRes/AssetBundle/ab/ui/uichatai.ab")

#: (用途, 要核对的结构, .ab 字符串表里该出现的键)
CHECKS = (
    ("FixBubbleLayout 前提", "NpcRow 模板", "NpcRow"),
    ("FixBubbleLayout 前提", "UserRow 模板", "UserRow"),
    ("FixBubbleLayout 前提", "NpcBubble 子节点", "NpcBubble"),
    ("FixBubbleLayout 前提", "UserBubble 子节点", "UserBubble"),
    ("FixBubbleLayout ①", "Text 对齐字段", "m_Alignment"),
    ("FixBubbleLayout ②", "VLG padding", "m_Padding"),
    ("FixBubbleLayout ②", "VLG 子项对齐", "m_ChildAlignment"),
    ("FixBubbleLayout ③", "行距字段", "m_LineSpacing"),
    ("FixBubbleLayout ③", "富文本开关", "m_RichText"),
    ("FixBubbleLayout ④", "Image 精灵字段", "m_Sprite"),
    ("ApplyBubbleTextLayoutFloor", "Templates 容器", "Templates"),
)


def dump_strings(ab: Path) -> str:
    """解包 .ab 并返回它的字符串表（复用 ab_probe.py，不重写解压逻辑）。

    ⚠ `ab_probe.main()` **不带参数**、直接读 `sys.argv`，所以这里临时替换 sys.argv
    （比去改 ab_probe 的签名更省 —— 那个脚本有别的调用方，不动它）。
    """
    import contextlib
    import io
    import ab_probe

    out = Path("/tmp/_ab_fallback_strings.txt")
    if out.is_file():
        out.unlink()                      # 防止探针失败时读到上一次的旧表
    saved = sys.argv
    buf = io.StringIO()
    try:
        sys.argv = ["ab_probe.py", str(ab), "--dump-strings", str(out)]
        with contextlib.redirect_stdout(buf):
            ab_probe.main()
    finally:
        sys.argv = saved
    if not out.is_file():
        raise RuntimeError("ab_probe 没产出字符串表：\n" + buf.getvalue())
    return out.read_text(encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ab = Path(argv[0]) if argv else DEFAULT_AB
    if not ab.is_file():
        print("找不到 .ab：%s" % ab)
        print("（游戏不在本机时这条检查跳过 —— 它不是代码正确性的前提）")
        return 2

    blob = dump_strings(ab)
    print("%-30s %-24s %s" % ("用途", "要核对的结构", "在 .ab 里"))
    print("-" * 66)
    missing = []
    for use, what, key in CHECKS:
        ok = key in blob
        if not ok:
            missing.append((use, what, key))
        print("%-30s %-24s %s" % (use, what, "✓" if ok else "✗ 缺"))

    print()
    if not missing:
        print("结论：全部齐备 —— 运行期兜底能命中（不依赖重打 AB）")
        return 0
    print("结论：%d 项缺失 ⇒ 下列兜底会**静默跳过**（玩家看不到修复）：" % len(missing))
    for use, what, key in missing:
        print("  · %s：缺 %s（找 %r）" % (use, what, key))
    return 1


if __name__ == "__main__":
    sys.exit(main())
