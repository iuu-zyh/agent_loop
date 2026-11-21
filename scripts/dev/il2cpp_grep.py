#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""il2cpp_grep —— 在**游戏本体的 IL2CPP 原生码**里查方法（一次性建索引，之后秒查）。

## 为什么有这个脚本

`GameAssembly.dll`（120 MB）里是 il2cpp 编译后的原生码；托管接口程序集
（`MelonLoader/Managed/Assembly-CSharp.dll`）只有**声明**，没有方法体。
于是"某方法到底怎么判的"这类问题，只能反汇编原生码回答。

以前每次都要手抄一条 `cpp2ilrunner` 命令 + 现拼 VA，两个后果：
  ① 记不住用法 → 每次重学；
  ② VA 抄错一个十六进制位 → 反汇编出别的方法，而**输出看起来仍然像模像样**（最糟的形态）。

本脚本把这两件事固定下来：`type` 列方法、`xref` 查方法体、`code` 反汇编。

## 底层工具

`F:\\.cpp2il_work\\runner`（`cpp2ilrunner`）—— 一个反射调用 `LibCpp2IL` / `Cpp2IL.Core`
的小程序，支持 `tlist` / `raw` / `rawx` / `at` / `xrefi` / `tclosure`。
**不要**去跑全量 Cpp2IL 导出（几十分钟）；本脚本按需反汇编单个方法，秒级。

## 用法

    python3 scripts/dev/il2cpp_grep.py type UnitActionBase
        → 列出该类型全部方法 + VA（名字前缀匹配，可只写前几个字符）

    python3 scripts/dev/il2cpp_grep.py find IsDayCost
        → 不知道在哪个类里？全库按方法名找

    python3 scripts/dev/il2cpp_grep.py code UnitActionBase::IsDayCost
        → 反汇编（默认 320 字节；第三个参数可调：`code X 800`）

    python3 scripts/dev/il2cpp_grep.py xref UnitActionBase::IsDayCost
        → 谁调用了它（立即数 8 字节扫描）

## 已知坑（踩过，别再踩）

  · **`tlist` 收的是类型名前缀，不是方法名**。传方法名会静默输出空表
    （只打 `mainT=` / `raw=` 两行）—— 想按方法名找用 `find`。
  · **`rawx` 不按 `ret` 停**：`raw` 遇到第一个 `ret` 就截断，而很多方法在中间就 ret
    （分支返回）。看完整分支结构必须用 `rawx`（`code` 默认走它）。
  · **`jne`/`je` 后面跟的地址要自己算相对位置**：Iced 已经算好绝对地址了，
    但"跳到哪"要回看那段汇编。本脚本不替你解释逻辑，只给可靠的反汇编。
  · Nuitka/Unity 的 `int3` 填充很常见：看到一串 `int3` 就是方法结束。

## 索引缓存

首次查询会把「方法名 → VA」全表落到 `F:\\.cpp2il_work\\index.json`（约 20 万条）。
之后 `find` 走缓存（毫秒级）；`type` / `code` / `xref` 仍要跑一次 runner 初始化
（LibCpp2IL 加载 ~2-4 秒，无法避免）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_DIR = Path("/mnt/f/.cpp2il_work/runner")
RUNNER_PROJ = RUNNER_DIR / "runner.csproj"
#: 已编译产物：**优先直接跑它**（省掉 MSBuild 的 2-3 秒；`dotnet run --project` 还要求
#: 传 Windows 形式的路径，WSL 的 `/mnt/...` 会被 MSBuild 当成未知开关 —— 踩过）。
RUNNER_EXE = RUNNER_DIR / "bin/Release/net6.0/cpp2ilrunner.exe"
CACHE = Path("/mnt/f/.cpp2il_work/index.json")
DOTNET_CANDIDATES = [
    "/mnt/c/Program Files/dotnet/dotnet.exe",
    "C:/Program Files/dotnet/dotnet.exe",
]


def _dotnet() -> str:
    for p in DOTNET_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit("找不到 dotnet.exe（Windows 侧 .NET SDK）")


def _run(args: list[str], timeout: int = 300) -> str:
    """跑一次 cpp2ilrunner，返回 stdout（已剥掉前两行初始化噪音）。"""
    if RUNNER_EXE.exists():
        cmd = [str(RUNNER_EXE)] + args
        cwd = str(RUNNER_EXE.parent)
    else:
        cmd = [_dotnet(), "run", "--project",
               str(RUNNER_PROJ).replace("/mnt/f", "F:").replace("/", "\\"),
               "-c", "Release", "--"] + args
        cwd = str(RUNNER_DIR)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    out = p.stdout or ""
    if p.returncode != 0 and not out.strip():
        raise SystemExit("cpp2ilrunner 失败：\n" + (p.stderr or "")[-2000:])
    return out


def _strip_banner(out: str) -> list[str]:
    """去掉 `mainT=` / `raw=` 这类初始化行，返回正文行。"""
    lines = out.splitlines()
    return [ln for ln in lines
            if not ln.startswith(("mainT=", "raw=", "类型名候选:"))]


# ----------------------------------------------------------------------
# type：列出某类型的方法
# ----------------------------------------------------------------------

def cmd_type(prefix: str) -> int:
    body = [ln for ln in _strip_banner(_run(["tlist", prefix]))]
    if not body:
        print("没有匹配的类型。注意：`tlist` 收的是**类型名**前缀（不是方法名）。")
        return 1
    print("\n".join(body))
    return 0


# ----------------------------------------------------------------------
# find：按方法名全库找（走缓存）
# ----------------------------------------------------------------------

def _load_index() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def cmd_find(method: str, limit: int = 40) -> int:
    idx = _load_index()
    if not idx:
        print("索引不存在（首次 `find` 需要建索引，约 20 万条）。")
        print("建索引：本脚本的 `index` 子命令。")
        return 2
    hits = [(k, v) for k, v in idx.items() if method.lower() in k.lower()]
    if not hits:
        print("方法名里没有 %r" % method)
        return 1
    hits.sort()
    for k, v in hits[:limit]:
        print("%-70s %s" % (k, v))
    if len(hits) > limit:
        print("… 还有 %d 条（用更精确的名字收窄）" % (len(hits) - limit))
    return 0


def cmd_index() -> int:
    """把「类::方法 → VA」全表落盘。一次性，约 20 万条。"""
    print("正在建索引（LibCpp2IL 全量方法表，可能需要 1-3 分钟）…")
    # runner 没有"导出全表"的子命令，用 `tlist ""` 拿不到（StartsWith("") 匹配全部）
    out = _run(["tlist", ""], timeout=600)
    idx: dict[str, str] = {}
    cur = ""
    for ln in _strip_banner(out):
        s = ln.strip()
        if s.startswith("## "):
            cur = s[3:].strip()
            continue
        parts = s.split()
        if len(parts) == 2 and parts[0].startswith("0x"):
            va, name = parts
            idx["%s::%s" % (cur, name)] = va
    if not idx:
        print("没解析出任何方法：runner 的 `tlist \"\"` 可能不支持空前缀。")
        print("退路：用 `type <类型名>` 逐个查（本脚本的 `type` 子命令）。")
        return 1
    CACHE.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    print("索引已写 %s（%d 条）" % (CACHE, len(idx)))
    return 0


# ----------------------------------------------------------------------
# code / xref / at
# ----------------------------------------------------------------------

def _parse_target(spec: str) -> str:
    """`Type::Method` → VA（十六进制串）。也接受裸 VA（`0x1835083E0`）。"""
    if spec.lower().startswith("0x"):
        return spec[2:]
    if "::" not in spec:
        raise SystemExit("要写成 `类型::方法`（可先 `type <类型>` 查方法名），或直接给 0x 开头的 VA")
    idx = _load_index()
    if not idx:
        raise SystemExit("索引不存在 —— 先跑 `index` 子命令，或用 0x 开头的裸 VA。")
    if spec in idx:
        return idx[spec][2:]
    # 后缀匹配（忘记命名空间/嵌套类型时）
    hits = [(k, v) for k, v in idx.items() if k.endswith("::" + spec.split("::", 1)[1])]
    if len(hits) == 1:
        return hits[0][1][2:]
    if len(hits) > 1:
        print("有 %d 个同名方法，请写全 `类型::方法`：" % len(hits))
        for k, v in hits[:20]:
            print("   ", k, v)
        raise SystemExit(2)
    raise SystemExit("索引里没有 %r（先跑 `find <方法名>`）" % spec)


def cmd_code(spec: str, length: int = 320) -> int:
    va = _parse_target(spec)
    out = _strip_banner(_run(["rawx", va, str(length)]))
    print("\n".join(out))
    return 0


def cmd_xref(spec: str) -> int:
    va = _parse_target(spec)
    out = _strip_banner(_run(["xrefi", va], timeout=900))
    print("\n".join(out))
    return 0


def cmd_at(va: str) -> int:
    out = _strip_banner(_run(["at", va.replace("0x", "")]))
    print("\n".join(out))
    return 0


USAGE = """il2cpp_grep —— 在游戏原生码里查方法

  type <类型名前缀>        列出该类型的方法 + VA（注意：收类型名，不是方法名）
  find <方法名片段>        全库按方法名找（需先 index）
  index                    建/重建索引（一次性，约 20 万条）
  code <类型::方法> [字节]  反汇编（默认 320 字节，走 rawx 不截 ret）
  xref <类型::方法>        谁调用了它（立即数扫描，慢）
  at <0xVA>                这个地址是哪个方法

例子：
  python3 scripts/dev/il2cpp_grep.py type UnitActionBase
  python3 scripts/dev/il2cpp_grep.py code UnitActionBase::IsDayCost 400
"""


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE)
        return 0
    cmd = argv[1].lower()
    if cmd == "type" and len(argv) >= 3:
        return cmd_type(argv[2])
    if cmd == "find" and len(argv) >= 3:
        return cmd_find(argv[2])
    if cmd == "index":
        return cmd_index()
    if cmd == "code" and len(argv) >= 3:
        return cmd_code(argv[2], int(argv[3]) if len(argv) > 3 else 320)
    if cmd == "xref" and len(argv) >= 3:
        return cmd_xref(argv[2])
    if cmd == "at" and len(argv) >= 3:
        return cmd_at(argv[2])
    print(USAGE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
