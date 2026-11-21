#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把**构建产物**换上游戏运行目录（`ModExportData/Mod_Jgmg5L/`）。

为什么单独一个脚本、而不是手抄两行 `cp`
------------------------------------------------------------------------------------
因为 09-17 的事故就是这么来的：`AgentLoopServer.exe` 在**运行中**被换掉，
而 PyInstaller onefile 的纯 Python 模块是**按需从 exe 归档里现读**的
（偏移量写死在编译期，`open(sys.executable)` + `seek(旧偏移)` + `zlib.decompress`），
于是"从没被 import 过的模块"再也读不出来：

    zlib.error: Error -3 while decompressing data: incorrect header check

现场特征极容易被误判成"代码 bug"：进程没死、别的功能全正常、**只有第一次走到的新功能炸**。
（PyInstaller 的源码只处理了「exe 被改名/删除」，没处理「同路径被换成另一个文件」——
而 WSL 下的常规换法"旧件改名成 .bak → 拷新件进来"正好落在那个盲区。）

所以本脚本把「先停进程」写成**硬闸**：目标进程在跑就拒绝部署，除非 `--force`。

⚠ 2026-09-19 补充：上面那条 **zlib 失效模式是 PyInstaller 独有的，Nuitka 下已不存在**。
实测：起 Nuitka 档服务后，把同路径的 exe 换成另一个二进制，再打 8 个该进程**从未走过**的
方法 —— 7 个正常、1 个是普通参数校验错，日志里 `zlib`/`decompress` 命中 **0**。
原因：Nuitka onefile 在**启动时一次性**把载荷解压到 `%TEMP%\\onefile_<pid>_<rand>\\`，
之后只读那个目录，**不再碰原 exe**。详见 `docs/release/PACKAGING.md` §6.5。

**但这个硬闸继续保留**，只是它防的东西换成了两条**仍然成立**的理由：
  ① C# 与 Python 的协议是**成对**的（例：`create_room` 只有新 exe 认），
     只换一边的典型后果是"点了没反应/未知方法"，而且不报错；
  ② 进程活着时换件 ⇒ **跑着的实例与盘上的产物不是同一个东西**，
     下次重启行为突变，排障时必被误导成"改了代码却没生效"（或反之）。

两个产物、两个进程
------------------------------------------------------------------------------------
    ModAssets/AgentLoopServer.exe   ← <构建目录>/pybuild/dist/    被 AgentLoopServer.exe 读
    ModCode/dll/MOD_Jgmg5L.dll      ← csharp/bin/<配置>/          被游戏进程读

**必须一起换**：C# 与 Python 的协议是成对的（例：`create_room` 只有新 exe 认）。
只换一边的典型后果是"点了没反应/未知方法"，而且不报错。

用法
------------------------------------------------------------------------------------
    python3 scripts/dev/deploy_runtime.py                 # 两个都换（先查进程）
    python3 scripts/dev/deploy_runtime.py --only dll      # 只换 DLL
    python3 scripts/dev/deploy_runtime.py --stop          # 先杀 AgentLoopServer 再换 exe
    python3 scripts/dev/deploy_runtime.py --tag checkboxfix
    python3 scripts/dev/deploy_runtime.py --check         # 只比对 md5，不写任何文件

环境变量：`GGBH_GAME_ROOT` 覆盖游戏根目录（默认 `E:\\SteamLibrary\\steamapps\\common\\鬼谷八荒`）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO.parent / f".{REPO.name}_build"

GAME_ROOT = Path(os.environ.get("GGBH_GAME_ROOT")
                 or os.environ.get("GGBH_GAME_ROOT", "")
                 or "/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒")
if not GAME_ROOT.is_dir():                      # E: 挂在别处，或干脆是在 Windows 侧跑（不是 WSL）
    for cand in (Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒"),
                 Path("/mnt/d/SteamLibrary/steamapps/common/鬼谷八荒"),
                 Path("E:/SteamLibrary/steamapps/common/鬼谷八荒"),
                 Path("D:/SteamLibrary/steamapps/common/鬼谷八荒")):
        if cand.is_dir():
            GAME_ROOT = cand
            break

MOD_DIR = GAME_ROOT / "ModExportData" / "Mod_Jgmg5L"

#: 两个 exe 后端各自的产物位置。**发行档是 Nuitka**（2026-09-19 起，见 docs/release/PACKAGING.md §6）——
#: `pyinstxtractor` 一条命令就能把 PyInstaller 档还原成 `.pyc`，所以它只配用于**本地自测**。
EXE_NUITKA = BUILD_DIR / "nuitka" / "AgentLoopServer.exe"
EXE_PYINSTALLER = BUILD_DIR / "pybuild" / "dist" / "AgentLoopServer.exe"

#: 后端指纹（与 `scripts/pack_release.py` 同一套判据，两处都改才算改）。
#: 只读尾部 1 MB：两个指纹都在末尾 88 字节内。
_FP_NUITKA = b"NUITKA"
_FP_PYINSTALLER = b"MEI\x0c\x0b\x0a\x0b\x0e"


def exe_backend(path: Path) -> str:
    """认产物是哪个后端打的：`'nuitka'` / `'pyinstaller'` / `'unknown'`。纯读文件。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - (1 << 20)))
            tail = f.read()
    except OSError:
        return "unknown"
    if _FP_NUITKA in tail:
        return "nuitka"
    if _FP_PYINSTALLER in tail:
        return "pyinstaller"
    return "unknown"


def resolve_exe_src(requested: str) -> Path:
    """决定这次部署用哪个 exe —— **默认发行档（Nuitka）**，只在它不存在时才退到自测档。

    退的时候必须**出声**：两种产物文件名相同、外面看不出区别，
    静默拿自测档当发行档部署，就是把源码保护白送了。
    """
    if requested:
        return Path(requested)
    if EXE_NUITKA.is_file():
        return EXE_NUITKA
    if EXE_PYINSTALLER.is_file():
        print("  ⚠ 没找到 Nuitka 发行档，退用 PyInstaller **自测档** —— 别拿它发布！")
        print(f"    发行档：{EXE_NUITKA}")
        print("    出它：cd /mnt/f/.agent_loop_build && "
              "./pybuild/venv-nuitka/Scripts/python.exe -X utf8 "
              "'F:\\agent_loop\\scripts\\build_exe_nuitka.py' --jobs 12")
        return EXE_PYINSTALLER
    return EXE_NUITKA          # 两个都没有 → 让 deploy() 报「源文件不存在」并给出提示


#: 产物 → (源文件, 目标文件, 读它的进程名)
ITEMS = {
    "exe": (EXE_NUITKA,
            MOD_DIR / "ModAssets" / "AgentLoopServer.exe",
            "AgentLoopServer"),
    "dll": (REPO / "csharp" / "bin" / "Release" / "MOD_Jgmg5L.dll",
            MOD_DIR / "ModCode" / "dll" / "MOD_Jgmg5L.dll",
            "guigubahuang"),
}

#: 查/停进程用的两个外部命令。WSL 里没有 PATH 上的 Windows 命令，只能走 /mnt/c 绝对路径；
#: 直接在 Windows 侧跑时它们是 PATH 上的裸名。**两条都要真能找到**，否则 `running()` 只会
#: 抛异常并按"可能在跑"处理 —— 那道"产物正被进程读着就别换"的硬闸就退化成逼人加 --force。
PS = shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
TASKKILL = shutil.which("taskkill.exe") or "/mnt/c/Windows/System32/taskkill.exe"


def md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def running(names) -> dict:
    """{进程名: [pid, ...]}（只列真的在跑的）。走 Windows 的 PowerShell —— WSL 看不见它们。"""
    out = {}
    for name in names:
        try:
            r = subprocess.run(
                [PS, "-NoProfile", "-Command",
                 f"Get-Process -Name '{name}' -ErrorAction SilentlyContinue | "
                 f"Select-Object -ExpandProperty Id"],
                capture_output=True, text=True, timeout=30)
        except Exception as e:                    # 拿不到就问号，别假装"没在跑"
            out[name] = ["?"]
            print(f"  ⚠ 查不到进程 {name}（{e}）—— 按「可能在跑」处理")
            continue
        pids = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().isdigit()]
        if pids:
            out[name] = pids
    return out


def stop(name: str) -> None:
    print(f"  停止 {name} …")
    subprocess.run([TASKKILL, "/IM", f"{name}.exe", "/T", "/F"],
                   capture_output=True, text=True, timeout=60)
    for _ in range(20):                            # 最多等 10 秒，确认真的没了
        if not running([name]):
            print(f"  ✓ {name} 已停止")
            return
        time.sleep(0.5)
    sys.exit(f"  ✗ {name} 停了 10 秒还在 —— 不部署（半死不活的状态最危险）")


def backup_tag(tag: str) -> str:
    return time.strftime("%m%d_%H%M") + "_before_" + re.sub(r"[^0-9A-Za-z_]+", "", tag or "deploy")


def deploy(key: str, tag: str, force: bool, dry: bool) -> bool:
    src, dst, proc = ITEMS[key]
    print(f"\n[{key}] {src}")
    print(f"   → {dst}")
    if not src.is_file():
        print(f"  ✗ 源文件不存在（先构建）：{src}")
        return False
    if not dst.parent.is_dir():
        print(f"  ✗ 目标目录不存在（游戏没装 / 路径不对）：{dst.parent}")
        return False

    src_md5 = md5(src)
    same = dst.is_file() and md5(dst) == src_md5
    print(f"  源 md5={src_md5}  目标 md5={md5(dst) if dst.is_file() else '(无)'}"
          + ("  ← 已经一致" if same else ""))

    live = running([proc])
    if live:
        msg = f"  ⚠ {proc} 正在运行（PID {','.join(live[proc])}）"
        if not force:
            print(msg + " —— **拒绝部署**（见本脚本头的 09-17 事故）。")
            print(f"     先停它：{Path(__file__).name} --stop   或加 --force 硬来")
            return False
        print(msg + " —— --force，硬来（换完之后**必须重启**它，否则读到的归档是错的）")

    if dry or same:
        return True

    if dst.is_file():
        bak = dst.with_name(dst.name + ".bak_" + backup_tag(tag))
        shutil.copy2(dst, bak)
        print(f"  备份 → {bak.name}")
    shutil.copy2(src, dst)
    got = md5(dst)
    ok = got == src_md5
    print(f"  {'✓' if ok else '✗'} 已写入 md5={got}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="把构建产物换上游戏运行目录（先停进程再换文件）")
    ap.add_argument("--only", choices=["exe", "dll"], help="只部署一个（默认两个都部署）")
    ap.add_argument("--tag", default="deploy", help="备份文件名里的用途标记（默认 deploy）")
    ap.add_argument("--force", action="store_true", help="进程在跑也硬换（换完必须重启它）")
    ap.add_argument("--stop", action="store_true", help="先停 AgentLoopServer 再换 exe")
    ap.add_argument("--check", action="store_true", help="只比对 md5，不写任何文件")
    ap.add_argument("--exe-src", default="", metavar="PATH",
                    help="指定用哪个 exe（默认 Nuitka 发行档；指到 PyInstaller 自测档会警告）")
    args = ap.parse_args()

    if not args.only or args.only == "exe":
        src = resolve_exe_src(args.exe_src)
        ITEMS["exe"] = (src, ITEMS["exe"][1], ITEMS["exe"][2])
        if src.is_file():
            kind = exe_backend(src)
            if kind == "nuitka":
                print(f"exe 后端 = Nuitka ✓（发行档）：{src}")
            elif kind == "pyinstaller":
                bar = "!" * 72
                print(bar)
                print("! 警告：这次部署的是 **PyInstaller** 产物，不是发行档！")
                print("!   `pyinstxtractor` 一条命令就能还原成 .pyc —— 只能用于本地自测。")
                print("!   详见 docs/release/PACKAGING.md §6。")
                print(bar)
            else:
                print(f"⚠ 认不出这个 exe 的后端：{src}（发布前请人工确认）")

    keys = [args.only] if args.only else ["exe", "dll"]
    print(f"游戏目录：{GAME_ROOT}")
    print(f"构建目录：{BUILD_DIR}")

    live = running(["AgentLoopServer", "guigubahuang"])
    for n, pids in live.items():
        print(f"  运行中：{n} PID={','.join(pids)}")
    if not live:
        print("  没有目标进程在跑 ✓")

    if args.stop and "AgentLoopServer" in live:
        stop("AgentLoopServer")

    ok = True
    for k in keys:
        ok = deploy(k, args.tag, args.force, args.check) and ok

    print()
    if args.check:
        print("（--check：只比对，没写文件）")
    elif ok:
        print("部署完成。★ 别忘了**重启游戏**（DLL 在启动时加载）与**重启服务器**（换 exe 后必须）。")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
