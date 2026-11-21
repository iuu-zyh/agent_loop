#!/usr/bin/env python3
"""跑 C# 侧单元测试（`csharp/tests/` 下的每个测试工程）。

为什么需要它：实机 UI 行为（气泡重复、工具结果折叠/展开）依赖 Unity，WSL 里跑不了；但**判定规则**
（`UI/BubbleCommitPolicy.cs`、`UI/ToolFoldPolicy.cs`）是刻意做成零 Unity 依赖的纯逻辑，可以被编成
net8.0 控制台程序跑断言。这个脚本就是那个入口，走 Windows 的 dotnet.exe（WSL 里没有 dotnet）。

测试工程**自动发现**：`csharp/tests/*/*.csproj` 有几个跑几个（加新测试工程不用改这个脚本）。

★ 必须先清 `obj/` `bin/` 再跑：`dotnet run` 是**增量**构建，而本脚本会在同一秒内反复
  改源文件（变异验证就是干这个的），mtime 落在同一时间戳刻度上时 MSBuild 会判定"无需重编"
  → 跑上一次的二进制 → 变异验证拿到假绿/假红。第一次写变异脚本时就踩到了这个坑
  （还原源码后仍报同一批失败，看起来像"还原失败"）。

用法：
    python3 scripts/dev/run_csharp_tests.py            # 跑全部测试工程，任一失败退出码非 0
    python3 scripts/dev/run_csharp_tests.py --quiet    # 每个工程只打最后一行结论
    python3 scripts/dev/run_csharp_tests.py --only ToolFoldPolicyTests   # 只跑一个（变异验证用，省时间）

环境变量：`GGBH_DOTNET` 覆盖 dotnet.exe 路径（默认探 C:\\Program Files\\dotnet\\dotnet.exe）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT / "csharp" / "tests"

_DOTNET_CANDIDATES = [
    os.environ.get("GGBH_DOTNET", ""),
    "/mnt/c/Program Files/dotnet/dotnet.exe",
    "C:/Program Files/dotnet/dotnet.exe",
    shutil.which("dotnet") or "",
]


def find_dotnet() -> str:
    for c in _DOTNET_CANDIDATES:
        if c and Path(c).exists():
            return c
    # WSL 里常见的失败面：PATH 上没有 dotnet、也没装 .NET Framework —— 明确说清楚，
    # 不要假装"测试通过"（这个脚本存在的前提就是"别让没跑过的测试看起来跑过了"）
    raise SystemExit(
        "找不到 dotnet.exe。请在 Windows 侧安装 .NET SDK，或用环境变量 GGBH_DOTNET 指定路径。"
    )


def discover(only: str | None) -> list[Path]:
    if not TESTS_DIR.is_dir():
        raise SystemExit(f"没有测试目录：{TESTS_DIR}")
    projs = sorted(TESTS_DIR.glob("*/*.csproj"))
    if only:
        projs = [p for p in projs if p.parent.name == only]
        if not projs:
            raise SystemExit(f"没有这个测试工程：{only}")
    if not projs:
        raise SystemExit(f"{TESTS_DIR} 下没有测试工程（*/​*.csproj）")
    return projs


def run_one(dotnet: str, proj: Path, quiet: bool) -> tuple[int, str]:
    rel = proj.relative_to(ROOT).as_posix()
    # ★ 强制重编（见模块 docstring）
    for d in ("obj", "bin"):
        shutil.rmtree(proj.parent / d, ignore_errors=True)

    p = subprocess.run(
        [dotnet, "run", "--project", rel],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        errors="replace",
    )
    out = p.stdout + p.stderr
    # CS1668 是本机 LIB 环境变量的历史噪音（4 条，与代码无关），别让它淹没真错误
    lines = [l for l in out.splitlines() if "CS1668" not in l]
    body = "\n".join(lines).strip()

    if quiet:
        tail = [l for l in lines if l.strip()][-1:] or ["<无输出>"]
        return p.returncode, f"[{proj.parent.name}] rc={p.returncode}  {tail[0]}"
    return p.returncode, f"----- {proj.parent.name} ({rel}) -----\n{body}"


def main() -> int:
    ap = argparse.ArgumentParser(description="跑 C# 策略单元测试（自动发现 csharp/tests/*/）")
    ap.add_argument("--quiet", action="store_true", help="每个工程只打最后一行结论")
    ap.add_argument("--only", default=None, help="只跑指定工程（目录名），如 ToolFoldPolicyTests")
    args = ap.parse_args()

    projs = discover(args.only)
    dotnet = find_dotnet()

    rc = 0
    for proj in projs:
        code, text = run_one(dotnet, proj, args.quiet)
        print(text)
        rc = rc or code

    if not args.quiet and len(projs) > 1:
        print("\n===== 汇总：%d 个测试工程，%s =====" % (
            len(projs), "全部通过" if rc == 0 else "有失败"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
