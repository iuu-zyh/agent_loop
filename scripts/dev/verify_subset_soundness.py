#!/usr/bin/env python3
"""端到端验证 `run_tests_for.py` 的**充分性**：变异 → 全量 → 检查子集是否覆盖真红。

对一个源文件施加变异，跑**全量**记录真实变红的用例，再问：
**"改这个文件时 run_tests_for 选中的子集，是否覆盖了全量里所有变红的用例？"**

这是唯一有说服力的判据 —— 静态映射"看起来对"没有意义，要的是
**子集红了 = 全量也红在同样的地方**（子集没漏），且子集不必等于全量（够省）。

用法：python3 scripts/dev/verify_subset_soundness.py [--files f1 f2 …]
会临时改源码，跑完**逐字节还原**（md5 校验）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/dev/run_tests_for.py"

# (文件, 原文, 变异) —— 挑的都是"契约测试明确断言过"的点，保证变异真的能红
MUTATIONS: list[tuple[str, str, str]] = [
    ("agent/initiative.py",
     'ACTION_INTENTS: frozenset = frozenset({"gift", "lun_dao", "yao_yue", "spar", "attack"})',
     'ACTION_INTENTS: frozenset = frozenset({"gift", "lun_dao"})'),
    ("tools/text_render.py",
     "        _RENDERERS[name] = fn",
     "        _RENDERERS[name + \"_mutated\"] = fn"),
    ("csharp/contacts/UnreadStore.cs",
     "public static void ClearAll()",
     "public static void ClearAllRenamed()"),
    ("transport/bridge.py",
     "class WsGameBridge",
     "class WsGameBridgeRenamed"),
]

_FAILED_RE = re.compile(r"^FAILED (\S+)", re.M)
_ERROR_RE = re.compile(r"^ERROR (\S+)", re.M)


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def selected(changed: list[str]) -> set[str]:
    """问 runner：改这些文件会跑哪些用例（只列不跑）。"""
    out = subprocess.run([sys.executable, str(RUNNER), "--list", *changed],
                         cwd=str(ROOT), capture_output=True, text=True).stdout
    return {ln.strip() for ln in out.splitlines() if ln.strip().startswith("tests/")}


def full_suite_failures() -> tuple[set[str], str]:
    """跑全量（并行），收集真实变红的用例。"""
    p = subprocess.run([sys.executable, str(RUNNER), "--all"],
                       cwd=str(ROOT), capture_output=True, text=True)
    text = p.stdout + p.stderr
    bad = set(_FAILED_RE.findall(text)) | set(_ERROR_RE.findall(text))
    return bad, text


def subset_goes_red(changed: list[str]) -> tuple[bool, set[str]]:
    """**实用判据**：只跑子集，它自己会不会红？

    严格判据是"全量新增红的每一条都在子集里"。但真正要防的是**静默放行**：
    子集跑绿了、其实有回归。所以这里直接跑一遍子集问结果。
    两个判据都要看：严格判据保证"红在同一个地方"，实用判据保证"至少会红"。
    """
    p = subprocess.run([sys.executable, str(RUNNER), *changed],
                       cwd=str(ROOT), capture_output=True, text=True)
    text = p.stdout + p.stderr
    return p.returncode != 0, set(_FAILED_RE.findall(text)) | set(_ERROR_RE.findall(text))


def _covered(item: str, sel: set[str]) -> bool:
    """某条红是否落在子集里。

    ★ 必须**文件级**归一：收集错误报的是 `ERROR tests/x.py`（**没有 `::`**），
    而子集里放的是 `tests/x.py::test_y`。只做字符串相等会把"整文件都选了"误判成"漏"。
    """
    if item in sel:
        return True
    f = item.split("::")[0]
    return any(t == f or t.split("::")[0] == f for t in sel)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=None)
    args = ap.parse_args()
    cases = [m for m in MUTATIONS if not args.files or m[0] in args.files]

    print("=" * 78)
    print("基线：先跑一遍全量，记下**本来就红**的用例（并发会话改到一半的文件可能让树不绿）")
    print("=" * 78)
    base_bad, base_out = full_suite_failures()
    if base_bad:
        print(f"ℹ 基线已有 {len(base_bad)} 条红，接下来的判据是"
              f"「**变异新增的红**必须在子集里」（基线红会被扣掉）：")
        for b in sorted(base_bad)[:8]:
            print("   ", b)
    else:
        print("✅ 基线全绿")
    print()

    ok = True
    for rel, old, new in cases:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if old not in original:
            print(f"⚠ {rel}: 找不到锚点 `{old[:50]}`，跳过")
            ok = False
            continue
        before = md5(path)
        # ★ 往返自检（必须先做）：`read_text`/`write_text` 会做通用换行转换，遇到 CRLF
        #   文件会把 `\r\n` 静默写成 `\n` ⇒ "还原"之后字节数变了、还查不出来。
        #   这里先原样写回一次并校验 md5：**有损就直接放弃这个文件**（宁可不验证，
        #   也不能把一个源码文件的行尾改掉）。
        path.write_text(original, encoding="utf-8")
        if md5(path) != before:
            print(f"⚠ {rel}: 读写往返有损（换行/编码），**跳过**（不动它）")
            ok = False
            continue

        sel = selected([rel])
        try:
            path.write_text(original.replace(old, new, 1), encoding="utf-8")
            # ★ 两次运行都必须在**变异生效期间** ★ 第一版把子集那次放到 finally 之后，
            #   于是它跑的是已还原的树 —— 恒绿，"子集不会静默放行"这条判据完全失效。
            bad, out = full_suite_failures()
            red, subset_red_set = subset_goes_red([rel])
        finally:
            path.write_text(original, encoding="utf-8")
            if md5(path) != before:
                print(f"❌ {rel} 还原后 md5 不一致！请手工检查该文件")
                return 3

        new_bad = {b for b in bad if b not in base_bad}
        if not new_bad:
            print(f"⚠ {rel}: 变异没让任何用例**新增**变红（这个变异点没有被测试覆盖）"
                  f"—— 无法验证，换一个变异点")
            ok = False
            continue

        missed = {b for b in new_bad if not _covered(b, sel)}
        flag = "✅" if (not missed or red) else "❌"
        print(f"{flag} {rel}")
        print(f"     变异新增红 {len(new_bad)} 条 | 子集选中 {len(sel)} 项 | "
              f"严格漏 {len(missed)} 条 | 子集自己{'红 ✅' if red else '绿 ⚠'}")
        for b in sorted(new_bad)[:4]:
            print(f"       真红: {b}")
        for m in sorted(missed)[:6]:
            print(f"       ⚠ 严格判据漏掉: {m}")
        # ★ 真正要防的是"静默放行"：子集跑绿、全量却有新增红 ★
        if not red:
            print(f"       ❌ 子集跑绿了，但全量有 {len(new_bad)} 条新增红 —— 静默放行！")
            ok = False
        if missed:
            print(f"       （严格判据漏 {len(missed)} 条，但子集自己会红 ⇒ 回归仍会被发现）")
        print()

    print("=" * 78)
    print("判据 A（实用，必须过）：只跑子集时它自己会不会红 —— 防的是**静默放行**")
    print("判据 B（严格，最好过）：全量新增红的每一条是否都在子集里")
    print()
    print("结论：" + ("判据 A 全部通过 —— 任何回归都会被子集发现" if ok
                    else "有变异**静默放行**（子集绿、全量红），必须修"))
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
