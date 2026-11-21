#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IL2CPP 方法反汇编 —— **缓存优先**，不再每次都跑 objdump。

为什么有它
------------------------------------------------------------------------------------
之前每查一个方法都要重新走一遍 WSL + objdump + 手工对 rvamap，慢且浪费上下文。
本脚本把「RVA + 长度」当缓存键，第一次反汇编后落盘到
`F:\\_dt_dump\\asm\\<方法名>_<rva>_<长度>.txt`（带符号注释），以后直接读文件。

用法
------------------------------------------------------------------------------------
    # 首次：反汇编 + 注释 + 落盘
    python scripts/dev/il2cpp_disasm.py 0x3D7DE10 0x640 WorldUnitLogMgr.UpdateLogData

    # 以后：命中缓存，只打印路径（加 --print 才输出内容）
    python scripts/dev/il2cpp_disasm.py 0x3D7DE10 0x640 WorldUnitLogMgr.UpdateLogData

    # 不写长度：自动取 rvamap 里下一个方法的 RVA 作为边界
    python scripts/dev/il2cpp_disasm.py 0x3D7DE10 - WorldUnitLogMgr.UpdateLogData

    # 看缓存目录
    python scripts/dev/il2cpp_disasm.py --list

依赖
------------------------------------------------------------------------------------
- `F:\\_dt_dump\\rvamap_all.txt`（RVA → 方法名，13 万条）
- WSL 里的 GNU objdump（Ubuntu 自带）
- 游戏根目录默认 `E:\\SteamLibrary\\steamapps\\common\\鬼谷八荒`
  （可用环境变量 `GGBH_GAME_ROOT` / `IL2CPP_DUMP_DIR` 覆盖）

**注意**：objdump 看到的是 raw 机器码，方法体语义仍需人工判读；
脚本只负责“把同一段机器码只取一次”。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

IMAGE_BASE = 0x180000000

_DEFAULT_DUMP = [
    r"F:\_dt_dump",
    "/mnt/f/_dt_dump",
]
_env_dump = os.environ.get("IL2CPP_DUMP_DIR", "").strip()
if _env_dump:
    DUMP_DIR = Path(_env_dump)
else:
    DUMP_DIR = next((Path(d) for d in _DEFAULT_DUMP if (Path(d) / "rvamap_all.txt").is_file()),
                    Path(_DEFAULT_DUMP[0]))
CACHE_DIR = DUMP_DIR / "asm"
RVAMAP = DUMP_DIR / "rvamap_all.txt"

_DEFAULT_ROOTS = [
    r"E:\SteamLibrary\steamapps\common\鬼谷八荒",
    "/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒",
]
_env_root = os.environ.get("GGBH_GAME_ROOT", "").strip()
if _env_root:
    GAME_ROOT = Path(_env_root)
else:
    GAME_ROOT = next((Path(r) for r in _DEFAULT_ROOTS if (Path(r) / "GameAssembly.dll").is_file()),
                     Path(_DEFAULT_ROOTS[0]))


def load_symbols() -> dict[int, str]:
    """RVA(int) → 方法名。只读一次，约 13 万行。"""
    sym: dict[int, str] = {}
    if not RVAMAP.is_file():
        return sym
    with RVAMAP.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            try:
                sym[int(parts[0], 16)] = parts[1].strip()
            except ValueError:
                continue
    return sym


def parse_int(s: str) -> int:
    s = (s or "").strip()
    if s in ("", "-", "auto"):
        return 0
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def next_rva(sym: dict[int, str], rva: int) -> int:
    """rvamap 里严格大于 rva 的下一个地址（用来推方法长度）。"""
    best = 0
    for k in sym:
        if k > rva and (best == 0 or k < best):
            best = k
    return best


def wsl_path(p: Path) -> str:
    """E:\\SteamLibrary\\... → /mnt/e/SteamLibrary/..."""
    s = str(p.resolve())
    if len(s) < 3 or s[1] != ":":
        return s.replace("\\", "/")
    return f"/mnt/{s[0].lower()}{s[2:].replace(chr(92), '/')}"


def run_objdump(rva: int, size: int) -> str:
    va = IMAGE_BASE + rva
    end = va + size
    dll = GAME_ROOT / "GameAssembly.dll"
    if not dll.is_file():
        raise SystemExit(f"找不到 {dll}（用 GGBH_GAME_ROOT 指定游戏根目录）")
    cmd = (
        f"objdump -d -M intel --start-address=0x{va:x} --stop-address=0x{end:x} GameAssembly.dll"
    )
    try:
        r = subprocess.run(
            ["bash", "-lc", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            cwd=str(GAME_ROOT),
        )
    except FileNotFoundError:
        raise SystemExit("找不到 bash/objdump —— 本脚本依赖 GNU objdump")
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"objdump 失败（exit={r.returncode}）：{r.stderr[:500]}")
    return r.stdout


def annotate(text: str, sym: dict[int, str]) -> str:
    """把 `call 0x...` 后面补上 `; 方法名`，方便直接读。"""
    out: list[str] = []
    for line in text.splitlines():
        m = re.search(r"call\s+0x([0-9a-f]+)", line)
        if m:
            name = sym.get(int(m.group(1), 16) - IMAGE_BASE)
            if name:
                line = line.rstrip() + "   ; " + name
        out.append(line)
    return "\n".join(out) + "\n"


def cache_name(method: str, rva: int, size: int) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.]+", "_", method or "anon")
    return f"{safe}_{rva:x}_{size:x}.txt"


def main() -> int:
    ap = argparse.ArgumentParser(description="IL2CPP 方法反汇编（缓存优先）")
    ap.add_argument("rva", nargs="?", help="方法 RVA，如 0x3D7DE10")
    ap.add_argument("size", nargs="?", default="-", help="字节长度（十六进制，- = 自动）")
    ap.add_argument("method", nargs="?", default="", help="方法名（只用于缓存文件名/打印）")
    ap.add_argument("--print", action="store_true", dest="do_print", help="命中缓存也输出内容")
    ap.add_argument("--list", action="store_true", help="列出缓存")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，重新反汇编")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.list:
        files = sorted(CACHE_DIR.glob("*.txt"))
        print(f"缓存目录：{CACHE_DIR}（{len(files)} 个）")
        for f in files:
            print("  " + f.name)
        return 0

    if not args.rva:
        ap.print_help()
        return 2

    rva = parse_int(args.rva)
    size = parse_int(args.size)
    sym = load_symbols()
    if size <= 0:
        nxt = next_rva(sym, rva)
        size = (nxt - rva) if nxt > rva else 0x200
        size = min(max(size, 0x40), 0x8000)

    name = args.method or sym.get(rva, "")
    dst = CACHE_DIR / cache_name(name, rva, size)

    if dst.is_file() and not args.refresh:
        print(f"[cache] {dst}")
        if args.do_print:
            print(dst.read_text(encoding="utf-8", errors="replace"))
        return 0

    print(f"[disasm] {name or '(unknown)'}  RVA=0x{rva:x}  size=0x{size:x}")
    text = annotate(run_objdump(rva, size), sym)
    dst.write_text(text, encoding="utf-8")
    print(f"[saved] {dst}")
    if args.do_print:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
