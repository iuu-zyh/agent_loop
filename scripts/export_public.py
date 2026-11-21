#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出公开发布版仓库（GitHub 用）。

设计原则
--------
1. **白名单**：顶层只导出 INCLUDE_TOP 列出的条目，其余一律不导出。
   以后新增顶层文件必须显式加进白名单，否则不会进发布包 —— 这是故意的，
   宁可漏带也不要误带（仓库根曾有 config.json，含真实 API key）。
2. **幂等**：可重复运行。目标目录用标记文件保护，防止误删别的目录。
3. **不改内容**：只做文件级筛选。注释清理是在源仓库里做的一次性重构，
   不属于本脚本职责（否则源仓库与发布包会渐行渐远）。

用法
----
    python scripts/export_public.py            # 执行导出
    python scripts/export_public.py --check    # 只报告，不写盘
    python scripts/export_public.py --dst DIR  # 指定目标目录
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------- 路径

SRC = Path(__file__).resolve().parent.parent
DST_DEFAULT = SRC.parent / "agent_loop_public"
MARKER = ".export_public_target"

# ---------------------------------------------------------------- 白名单

INCLUDE_TOP = [
    # 项目说明与版本库配置
    "README.md",
    ".gitignore",
    "LICENSE",
    # Python 主模块（包根三件：身份/导出 + 装配管家 + 横切日志设施）
    "__init__.py",
    "agent_loop.py",
    "log_setup.py",
    # 配置模板与依赖（注意：不含 config.json）
    "config.example.json",
    "requirements.txt",
    "pytest.ini",
    # 子包（按域划分：环境配置 / 落盘档案 / 账本投影 / 对话引擎 / 群聊 / 游戏通信 /
    # LLM 客户端 / 游戏工具 / 上下文压缩 / 提示词资产）
    "config",
    "storage",
    "session",
    "agent",
    "room",
    "transport",
    "llm",
    "tools",
    "compaction",
    "prompts",
    # C# 侧（bin/obj 由 EXCLUDE_DIRNAMES 递归剔除）
    "csharp",
    # 文档、脚本、测试
    "docs",
    "scripts",
    "tests",
]

# ---------------------------------------------------------------- 排除规则

#: 目录名（任意层级命中即整棵剪掉）
EXCLUDE_DIRNAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "bin",
    "obj",
    ".git",
    ".vs",
    ".idea",
    ".vscode",
}

#: 文件名 glob（任意层级命中即跳过）
EXCLUDE_FILENAMES = [
    "config.json",       # 含真实 API key，发布包只给 config.example.json
    "export_public.py",  # 本脚本自身：引用本地专属目录，发布包不需要
    "_unity_probe.log",  # 运行时探针日志
    "*.pyc",
    "*.pyo",
    "*.pdb",
    "*.user",
    "*.suo",
    "*.log",
    "Thumbs.db",
    ".DS_Store",
    "*.tmp",
]

#: 仓库相对路径级排除（公开仓库只放"面向用户"的文档）
#:
#: 原则：开发过程记录、发布运维手册、设计底稿一律不公开。
#: 源码仓库里保留原件（改动可逆），只是不进发布包。
EXCLUDE_RELPATHS = {
    # 发布运维（纯 maintainer）
    "docs/release/RELEASE_STATUS.md",
    "docs/release/PACKAGING.md",
    "docs/release/PUBLISH.md",
    "docs/release/SHOTS.md",
    "docs/release/COVER_PROMPT.md",
    # 内部设计底稿与实现细节
    "docs/features/brain-liveness.md",
    "docs/features/context-projection.md",
    "docs/reference/ui-interception-spec.md",
    "docs/release/ui-skin-prompts.md",
    # 内部复盘
    "docs/archive/2026-09-10-世界输入冻结.md",
    # 工具结果字段契约（内部实现约定）
    "docs/features/tool-result-contract.md",
    # 内容已并入 README，留着只会两边不一致
    "docs/WORKSHOP_PAGE.txt",
    # 中间稿
    "docs/mockups",
}

#: 顶层直接排除（不进 INCLUDE_TOP 即为排除，这里只用于报告说明原因）
EXCLUDE_TOP_REASON = {
    ".mine_0913": "旧版文件备份",
    ".pytest_cache": "测试缓存",
    ".workbuddy": "AI 记忆/会话目录（个人笔记）",
    "backup": "代码备份",
    "config.json": "★ 含真实 API key，绝对不可发布",
    "dist": "构建产物",
    "generated-images": "生成的图片",
    "logs": "运行时日志（可能含对话内容）",
    "reference": "★ 反编译的游戏源码 + 他人教程 + 第三方 DLL，版权风险",
    # 注：`tmp_decomp_tool.txt` / `tmp_tools_schema_dump.md` 11-20 已移入 `reference/`
    #（现为 reference/decomp_tool_dump.txt、reference/tools_schema_dump.md），
    # 随 `reference` 整目录一起排除，故不再单列。
    "_diag_initiative_force.txt.disabled": "已停用的诊断开关残留",
    "_unity_probe.log": "运行时探针日志",
    "__pycache__": "字节码缓存",
    "scripts/export_public.py": "本脚本自身（发布包不需要）",
}

# ---------------------------------------------------------------- 敏感扫描

SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "API key 形态 (sk-...)"),
    (re.compile(r"(?i)(api[_-]?key|secret|passwd|password)\s*[:=]\s*[\"'][A-Za-z0-9_\-\.]{16,}[\"']"), "疑似密钥赋值"),
    (re.compile(r"C:\\\\Users\\\\[A-Za-z0-9_]+"), "个人绝对路径 C:\\Users\\..."),
]

#: 允许出现的白名单（测试夹具里的假 key 等）
SECRET_ALLOWLIST = [
    "sk-verysecret-0123456789",  # tests/test_llm_probe.py 夹具
]


# ---------------------------------------------------------------- 工具


def should_skip_dir(name: str) -> bool:
    return name in EXCLUDE_DIRNAMES


def should_skip_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_FILENAMES)


def should_skip_relpath(rel: Path) -> bool:
    """仓库相对路径排除：命中 `docs/X.md` 本身，或落在 `docs/mockups/` 这类目录里。"""
    posix = rel.as_posix()
    return posix in EXCLUDE_RELPATHS or any(
        posix.startswith(p.rstrip("/") + "/") for p in EXCLUDE_RELPATHS
    )


def iter_source_files(src: Path):
    """产出 (相对路径, 绝对路径)，已应用白名单与排除规则。"""
    for top in INCLUDE_TOP:
        entry = src / top
        if not entry.exists():
            print(f"  [警告] 白名单条目不存在：{top}", file=sys.stderr)
            continue
        if entry.is_file():
            if not should_skip_file(entry.name) and top not in EXCLUDE_RELPATHS:
                yield Path(top), entry
            continue
        for path in sorted(entry.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(src)
            if any(should_skip_dir(part) for part in rel.parts[:-1]):
                continue
            if should_skip_file(path.name):
                continue
            if should_skip_relpath(rel):
                continue
            yield rel, path


def guard_destination(dst: Path) -> None:
    """清空目标目录，但只在确认它是本脚本的导出目标时才动手。"""
    if not dst.exists():
        dst.mkdir(parents=True)
        (dst / MARKER).write_text("此目录由 scripts/export_public.py 生成，可安全清空。\n", encoding="utf-8")
        return
    if not (dst / MARKER).exists():
        contents = list(dst.iterdir())
        if contents:
            sys.exit(
                f"\n[中止] 目标目录已存在且非空，且没有标记文件 {MARKER}：\n  {dst}\n"
                f"为避免误删，请手工确认后删除，或换一个 --dst。\n"
            )
        (dst / MARKER).write_text("此目录由 scripts/export_public.py 生成，可安全清空。\n", encoding="utf-8")
        return
    for child in dst.iterdir():
        # ⚠️ `.git` 必须保留：否则每次重跑导出都会把版本库连同历史一起删掉
        if child.name in (MARKER, ".git"):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def verify_isolated(dst: Path) -> int:
    """把导出结果复制到临时目录跑测试，返回 pytest 退出码。

    ⚠️ 必须用**真实副本**，不能用软链接：
    `tests/conftest.py` 会把 `Path(__file__).resolve().parent.parent.parent`
    插到 `sys.path[0]`。若目标目录是软链接，`resolve()` 会跟随到真实路径，
    上溯三级可能落回源仓库所在目录 —— 于是 `import agent_loop` 命中**源仓库**，
    造成「人设写进导出、却从源码读」的假失败。
    同理，临时目录里那一层必须叫 `agent_loop`，否则包名解析不到。
    """
    import subprocess
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="export_verify_"))
    try:
        pkg = tmp / "agent_loop"
        shutil.copytree(dst, pkg, ignore=shutil.ignore_patterns(MARKER))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "agent_loop/tests/", "-q"],
            cwd=str(tmp), capture_output=True, text=True, timeout=1800,
        )
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        for ln in lines[-6:]:
            print(f"  {ln}")
        if proc.returncode != 0 and proc.stderr.strip():
            print("  --- stderr ---")
            for ln in proc.stderr.strip().splitlines()[-8:]:
                print(f"  {ln}")
        return proc.returncode
    except FileNotFoundError:
        print("  ⚠️  找不到 pytest，跳过测试验证")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scan_secrets(dst: Path) -> int:
    """扫描导出结果，返回问题条数。"""
    hits = 0
    text_ext = {".py", ".cs", ".md", ".json", ".txt", ".ini", ".cfg", ".toml", ".xml", ".csproj", ".spec"}
    for path in sorted(dst.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in text_ext:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(a in line for a in SECRET_ALLOWLIST):
                continue
            for pat, label in SECRET_PATTERNS:
                if pat.search(line):
                    rel = path.relative_to(dst)
                    print(f"  ❌ {rel}:{lineno}  [{label}]  {line.strip()[:100]}")
                    hits += 1
    return hits


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser(description="导出公开发布版仓库")
    ap.add_argument("--check", action="store_true", help="只报告，不写盘")
    ap.add_argument("--verify", action="store_true", help="导出后在隔离副本里跑测试")
    ap.add_argument("--dst", type=Path, default=DST_DEFAULT, help="目标目录")
    args = ap.parse_args()

    src, dst = SRC, args.dst.resolve()

    print("=" * 68)
    print("导出公开发布版仓库")
    print("=" * 68)
    print(f"源仓库: {src}")
    print(f"目标:   {dst}")
    print(f"模式:   {'只检查（不写盘）' if args.check else '执行导出'}")
    print()

    # 1) 报告被排除的顶层条目
    print("--- 顶层排除（不导出）---")
    for name, reason in sorted(EXCLUDE_TOP_REASON.items()):
        if (src / name).exists():
            print(f"  ✂ {name:38s} {reason}")
    print()

    # 2) 收集文件
    files = list(iter_source_files(src))
    print(f"--- 待导出：{len(files)} 个文件 ---")
    print()

    if args.check:
        total = sum(p.stat().st_size for _, p in files)
        print(f"预计体积：{total / 1024 / 1024:.1f} MB")
        return 0

    # 3) 清空并复制
    guard_destination(dst)
    for rel, path in files:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    copied = [p for p in dst.rglob("*")
              if p.is_file() and p.name != MARKER and ".git" not in p.parts]
    total = sum(p.stat().st_size for p in copied)
    print(f"✅ 已复制 {len(copied)} 个文件，{total / 1024 / 1024:.1f} MB")
    print()

    # 4) 反查：源仓库里排除清单是否覆盖了所有顶层条目
    print("--- 白名单覆盖率自检 ---")
    accounted = set(INCLUDE_TOP) | set(EXCLUDE_TOP_REASON)
    for child in sorted(src.iterdir()):
        if child.name not in accounted:
            print(f"  ⚠️  顶层条目未被处理：{child.name}（既不在白名单也不在排除表）")
    print()

    # 5) 敏感扫描
    print("--- 敏感内容扫描 ---")
    hits = scan_secrets(dst)
    if hits:
        print(f"\n❌ 发现 {hits} 处疑似敏感内容，请处理后重跑。")
        return 1
    print("  ✅ 未发现 API key / 密钥 / 个人路径")
    print()

    # 6) 隔离副本跑测试（可选）
    if args.verify:
        print("--- 隔离副本测试验证 ---")
        rc = verify_isolated(dst)
        if rc != 0:
            print(f"\n❌ 隔离测试未通过（pytest 退出码 {rc}）")
            return 1
        print("  ✅ 隔离测试通过")
        print()

    print("=" * 68)
    print(f"导出完成：{dst}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
