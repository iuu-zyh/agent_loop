"""`tests/_cs_tools.py` 的自检（11-20 工具层拆分后新增）。

这个 helper 是 60 个源码契约测试的**唯一读取入口**，它错了会同时影响一大片：
  · `flat_src()` 抹前缀抹过头 → 断言"调了 TeamBattleFollowup.TryAddLog"变成"调了 TryAddLog"，
    测试红得莫名其妙（11-20 踩过一次，`test_sect_log_contract` 整组红）；
  · 抹少了 → 拆分前的裸调用断言全部落空，测试整片红却看不出是环境问题。
所以把边界钉死。
"""
from __future__ import annotations

import re
from pathlib import Path

import _cs_tools

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "csharp" / "tools"


def test_flat_src_strips_only_split_classes():
    """只抹 `SPLIT_CLASSES` 的前缀；别的层/别的独立类原样保留。"""
    flat = _cs_tools.flat_src()
    for cls in _cs_tools.SPLIT_CLASSES:
        assert f"{cls}." not in flat, f"flat_src 没抹掉 {cls}.（跨类调用断言会落空）"
    # 独立类必须保留 —— 抹掉它们等于把"调了谁"也抹了
    for keep in ("TeamBattleFollowup.", "UnitSnapshot.", "UnitLookup.", "DramaGate."):
        assert keep in flat, f"flat_src 把 {keep} 也抹了（它不属于 ToolExecutor 拆分面）"


def test_tool_src_keeps_the_real_prefixes():
    """`tool_src()` 是**原样**的（要查真实写法时用它），不能被抹平。"""
    src = _cs_tools.tool_src()
    assert any(f"{c}." in src for c in ("ToolArgs", "IntimService", "PropsService")), \
        "tool_src() 也把类名前缀抹了 —— 那它和 flat_src() 就没区别了"


def test_every_split_class_file_exists():
    """`SPLIT_CLASSES` 里的每个类都要有对应文件（改名/删类时别忘同步）。"""
    files = {p.stem for p in TOOLS.glob("*.cs")}
    missing = [c for c in _cs_tools.SPLIT_CLASSES if c not in files]
    assert not missing, f"SPLIT_CLASSES 里有不存在的类：{missing}"


def test_tool_file_reads_exactly_one_file():
    """`tool_file()` 只给那一个文件的源码（"写入层只许有 AddIntim"这类断言靠它）。"""
    one = _cs_tools.tool_file("IntimService.cs")
    assert "class IntimService" in one
    assert "class TeamTool" not in one
    assert one in _cs_tools.tool_src()


def test_tool_files_lists_the_layer():
    names = _cs_tools.tool_files()
    assert "tools/ToolExecutor.cs" in names and "tools/TeamTool.cs" in names
    assert all(n.startswith("tools/") and n.endswith(".cs") for n in names)
