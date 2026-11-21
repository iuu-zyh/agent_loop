#!/usr/bin/env python3
"""把源码契约测试里「读 csharp/tools/ToolExecutor.cs」改为「读整个工具层」（见 tests/_cs_tools.py）。"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"

PAT_CALL = re.compile(r'\b(_read|_cs|_src|_tool|_load)\s*\(\s*(?:[A-Za-z_]\w*\s*/\s*)?"(?:csharp/)?tools/ToolExecutor\.cs"\s*\)')
PAT_READTEXT = re.compile(r'\(?\s*(?:[A-Za-z_][\w.()]*\s*/\s*)?"(?:csharp"|tools)"[^()\n]*"ToolExecutor\.cs"\s*\)?\s*\.read_text\([^()]*\)')
PAT_CONST = re.compile(r'^([A-Z][A-Z0-9_]*)\s*=\s*(?:os\.path\.join\([^\n]*\)|pathlib\.Path\([^\n]*\)|[^\n]*?)\s*(?:/\s*)?"(?:csharp/)?tools/ToolExecutor\.cs"\)?\s*$', re.M)


def add_import(text):
    if "import _cs_tools" in text:
        return text
    lines = text.split("\n")
    last = 0
    for i, l in enumerate(lines[:80]):
        if re.match(r"^(import|from)\s+\S", l):
            last = i
    lines.insert(last + 1, "import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）")
    return "\n".join(lines)


def main():
    changed = []
    for p in sorted(TESTS.glob("test_*.py")):
        t = p.read_text(encoding="utf-8")
        if "ToolExecutor.cs" not in t:
            continue
        orig = t
        t = PAT_CALL.sub("_cs_tools.tool_src()", t)
        t = PAT_READTEXT.sub("_cs_tools.tool_src()", t)
        t = PAT_CONST.sub(lambda m: f"{m.group(1)} = _cs_tools.TOOL_SRC", t)
        if t != orig:
            t = add_import(t)
            p.write_text(t, encoding="utf-8")
            changed.append(p.name)
    print(f"改了 {len(changed)} 个文件")
    left = [p.name for p in sorted(TESTS.glob("test_*.py")) if "ToolExecutor.cs" in p.read_text(encoding="utf-8")]
    print("仍有字面引用（需手工）：", left)
    return 0


if __name__ == "__main__":
    sys.exit(main())
