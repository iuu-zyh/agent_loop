#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/build_exe_nuitka.py —— Nuitka 档：把 Python 侧**编译**成自包含 exe。

与 PyInstaller 档（`scripts/AgentLoopServer.spec` + `pack_release.build_exe()`）的关系
------------------------------------------------------------------------------------
产物**同名、同位置、同启动契约** —— C# 侧一行都不用改：`ModPaths` 的判据是**可执行
文件名** `AgentLoopServer.exe`，它不关心这个文件是「打包的」还是「编译的」。
两者差别只在怎么造出来：

    PyInstaller : .py → .pyc → 塞进自解压归档   16 秒   pyinstxtractor 一条命令还原
    Nuitka      : .py → C  → 机器码            10~30 分 没有字节码可反

为什么单独一个脚本而不是塞进 pack_release.py：Nuitka 是**另一条工具链**（要 C 编译器、
构建时间长一个数量级、可能失败要退到 MinGW），和 16 秒的 PyInstaller 路径耦合在一起
只会让两条路互相拖累。等这条验通了再在 pack_release.py 里加一个 `--exe-backend` 开关。

**本脚本必须由 Windows 侧的 venv python 执行**，不要用 WSL 的 python3 跑它
------------------------------------------------------------------------------------
① Nuitka 要调 C 编译器，整个编译过程都在 Windows 侧；
② WSL 的环境变量**不会**传给 Windows 进程 —— 实测 `PYTHONPATH` 与自定义变量到了
   Windows 侧都是 `None`（`WSLENV` 没登记的一律丢）。而「让 `import agent_loop`
   被解析到」靠的正是 `PYTHONPATH`。这件事只能在 Windows 进程**内部**做，
   本脚本就是那个进程 —— 所以它自己设 env 再拉子进程，不指望外面传进来。

用法（在 WSL 里）
------------------------------------------------------------------------------------
    cd /mnt/f/.agent_loop_build          # 别在 /home 下跑：cwd 会变成 UNC 路径，cmd.exe 不认
    /mnt/f/.agent_loop_build/pybuild/venv-nuitka/Scripts/python.exe \\
        'F:\\agent_loop\\scripts\\build_exe_nuitka.py'

首次准备（只需一次）：
    py -3.12 -m venv F:\\.agent_loop_build\\pybuild\\venv-nuitka
    <venv>\\Scripts\\python.exe -m pip install -U pip nuitka zstandard ordered-set
    <venv>\\Scripts\\python.exe -m pip install -r 'F:\\agent_loop\\requirements.txt'

本机工具链（已实测可用，2026-09-14）
------------------------------------------------------------------------------------
    Nuitka       4.2.1 / Python 3.12.2 (CPython Official) / Windows 11 x86_64
    C 编译器     cl 14.5 —— MSVC 14.50.35717，来自 "Visual Studio 生成工具 2026"
                 (C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools)
    Windows SDK  10.0.26100.0，装在 **C:\\Windows Kits\\10\\**（不是 Program Files 那个路径！）
    ccache       Nuitka 自带的 clcache，第二次构建起命中缓存会快很多

    冒烟测试（hello world，onefile）：67 秒，MSVC 编译 7 个 C 文件，产物 3.8 MB 可正常运行。

    ⚠ 注意 `%INCLUDE%` 里有一条 **F:\\vs2022\\VC\\Tools\\MSVC\\14.30.30705\\include** 的死路径
      （VS2022 曾装在 F 盘、已删除，环境变量没清）。它排在有效路径后面，目前无害，
      但若哪天 cl 报奇怪的找不到头文件，先怀疑它。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# ---- 定位（本脚本在 Windows 侧跑，__file__ 天然是 Windows 路径）----------------
REPO = Path(__file__).resolve().parent.parent   # <仓库根> == Python 包 agent_loop 自身
PKG_PARENT = REPO.parent                        # 让 `import agent_loop` 能解析的那一层
SERVER_PY = REPO / "scripts" / "server.py"
PROMPTS = REPO / "prompts"
EXE_NAME = "AgentLoopServer.exe"

#: 构建缓存在**仓库之外**。仓库根就是 Python 包本身，构建垃圾不能住在包里。
DEFAULT_OUT = PKG_PARENT / ".agent_loop_build" / "nuitka"

#: 只带 prompts 且只作**兜底**（发行形态下 prompts 由 <Mod根>\prompts 提供，用户可编辑）。
#: **config.json 绝不内置** —— 那里面是用户的 API key，泄漏闸也会拦。
DATA_DIRS = [(PROMPTS, "agent_loop/prompts")]

#: 明确用不到的大件。Nuitka 只跟进真实 import，这份清单是**保险**而非主要瘦身手段：
#: 它防的是「某个 stdlib 模块顺手 import 了 tkinter」这类间接拖带。
EXCLUDES = [
    "tkinter", "pydoc_data", "test", "pytest", "_pytest",
    "numpy", "matplotlib", "scipy", "pandas", "PIL",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "notebook",
    "setuptools", "pip", "wheel",
    # 仓库根 == 包根，这些目录若被当成 agent_loop 的子包扫进来会白拉一堆依赖
    "agent_loop.tests", "agent_loop.backup", "agent_loop.reference",
    "agent_loop.scripts", "agent_loop.csharp", "agent_loop.dist", "agent_loop.docs",
]

#: 显式点名的包。`agent_loop` 是因为仓库布局非标准（根即包），不点名 Nuitka 未必当它是包；
#: `websockets` 顶层 `__init__` 不 import 具体实现（懒加载子模块），不点名会有
#: 「本机侥幸能跑、换台机器 ModuleNotFoundError」的隐性故障；
#: `openai` 在函数体内懒导入，缺了就静默退化成 Echo 复读（最难排查的一种"坏"）。
INCLUDE_PACKAGES = ["agent_loop", "websockets", "openai"]


def _say(stream, tag: str, msg: str) -> None:
    """Windows 控制台默认 GBK —— `✓`/`✗` 这类字符会让 print 自己抛 UnicodeEncodeError。

    2026-09-14 踩到：构建**成功**、exe 已落盘，脚本却在最后一行成功日志上崩掉，
    退出码 1、控制台一片红 —— 看起来像构建失败，实际白等一场。
    `errors="replace"` 兜住任何非 GBK 字符（提示词里迟早会有人塞 emoji / 生僻字），
    别让「打印日志」这件事有机会杀掉一个已经成功的构建。
    """
    text = f"[nuitka] {tag}{msg}"
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc, "replace"), file=stream, flush=True)


def log(msg: str) -> None:
    _say(sys.stdout, "", msg)


def die(msg: str) -> "None":
    _say(sys.stderr, "FAIL ", msg)
    raise SystemExit(1)


def preflight(args) -> None:
    """开跑前把「一定会失败」的情况挡掉 —— 编译一次十几分钟，不值得浪费在低级错误上。"""
    if os.name != "nt":
        die("本脚本必须由 **Windows 侧** 的 python 执行（Nuitka 要调 C 编译器）。\n"
            "       WSL 里请这样调：\n"
            "         cd /mnt/f/.agent_loop_build\n"
            "         /mnt/f/.agent_loop_build/pybuild/venv-nuitka/Scripts/python.exe "
            "'F:\\agent_loop\\scripts\\build_exe_nuitka.py'")
    if not SERVER_PY.is_file():
        die(f"入口脚本不存在：{SERVER_PY}")
    if not PROMPTS.is_dir():
        die(f"提示词目录不存在：{PROMPTS}")
    try:
        import nuitka  # noqa: F401
    except ImportError:
        die(f"当前解释器里没有 nuitka：{sys.executable}\n"
            f"      装它：{sys.executable} -m pip install -U nuitka zstandard ordered-set")
    if Path.cwd().drive == "":
        die("当前工作目录不是 Windows 盘符路径（UNC 路径下 cmd.exe 不工作）。\n"
            "      请先 cd 到 F: 之类的盘符目录再跑。")


def build_command(args) -> list:
    cmd = [sys.executable, "-m", "nuitka",
           "--onefile",
           "--assume-yes-for-downloads",     # ccache / zstandard / 依赖扫描器，别卡在交互提问
           "--windows-console-mode=force",    # **不能**用 disable：会把 sys.stdout/stderr 设成 None
           f"--output-filename={EXE_NAME}",
           f"--output-dir={args.out}",
           f"--jobs={args.jobs}"]
    if args.no_report is False:
        cmd.append(f"--report={args.out / 'nuitka-report.xml'}")
    if args.compiler != "auto":
        cmd.append({ "msvc": "--msvc=latest", "mingw64": "--mingw64" }[args.compiler])
    for pkg in INCLUDE_PACKAGES:
        cmd.append(f"--include-package={pkg}")
    for src, dest in DATA_DIRS:
        cmd.append(f"--include-data-dir={src}={dest}")
    for mod in EXCLUDES:
        cmd.append(f"--nofollow-import-to={mod}")
    if args.remove_output:
        cmd.append("--remove-output")
    cmd.append(str(SERVER_PY))
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="用 Nuitka 编译出 AgentLoopServer.exe（Windows 侧运行）")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"产物与中间目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--compiler", choices=["auto", "msvc", "mingw64"], default="auto",
                    help="C 编译器。auto = 让 Nuitka 自己挑（本机探测到 cl 14.5）")
    ap.add_argument("--jobs", type=int, default=8, help="并行编译数（默认 8）")
    ap.add_argument("--remove-output", action="store_true",
                    help="成功后删掉中间目录（首次调试别加，留着才能看生成的 C 代码）")
    ap.add_argument("--no-report", action="store_true", help="不生成 XML 报告")
    args = ap.parse_args()
    args.out = args.out.resolve()

    preflight(args)
    args.out.mkdir(parents=True, exist_ok=True)

    cmd = build_command(args)
    env = dict(os.environ)
    # ↓ 这两件事没法从外面传进来（WSL 环境变量到不了 Windows 进程），只能在这里做
    env["PYTHONPATH"] = str(PKG_PARENT)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    log(f"仓库根      = {REPO}")
    log(f"PYTHONPATH  = {PKG_PARENT}   ← 让 `import agent_loop` 解析到本仓库")
    log(f"解释器      = {sys.executable}")
    log(f"编译器      = {args.compiler}")
    log(f"产物        = {args.out / EXE_NAME}")

    # 探针：0.2 秒确认 `import agent_loop` 在**子进程**里解析得到。
    # 这一步很值——没有它，「env 没传给子进程」会以 Nuitka 内部
    # `FATAL: failed to locate package 'agent_loop'` 的形式炸出来，
    # 那句话指向的是 Nuitka 的包查找，跟真因（PYTHONPATH 没生效）差着两层（已踩过）。
    probe = subprocess.run(
        [sys.executable, "-c", "import agent_loop; print(agent_loop.__file__)"],
        env=env, capture_output=True, text=True)
    if probe.returncode != 0:
        die(f"子进程里 `import agent_loop` 失败 —— PYTHONPATH 没生效。\n"
            f"      PYTHONPATH = {env.get('PYTHONPATH')!r}\n"
            f"      {probe.stderr.strip()}")
    log(f"探针        = agent_loop → {probe.stdout.strip()}")

    log("命令：")
    log("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))

    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(args.out), env=env)
    dt = time.time() - t0

    if r.returncode != 0:
        die(f"Nuitka 失败（退出码 {r.returncode}，耗时 {dt:.0f}s）\n"
            f"      换编译器试试：--compiler mingw64（会自动下载 winlibs，完全不依赖 MSVC/SDK）")
    exe = args.out / EXE_NAME
    if not exe.is_file():
        die(f"Nuitka 报成功但没看到产物：{exe}")
    log(f"完成：{exe}（{exe.stat().st_size / 1e6:.1f} MB，耗时 {dt:.0f}s = {dt / 60:.1f} 分）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
