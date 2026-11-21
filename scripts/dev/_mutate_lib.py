#!/usr/bin/env python3
"""变异脚本的公共跑测层：把「每轮一个 pytest 子进程」换成**同进程多轮**。

**为什么**（09-17 实测）：`python3 -m pytest <单个契约文件> -q` 单次 **1.4s**，其中 ~1.2s 是
import + 收集的固定成本（空选择也要 2.56s）。变异脚本动辄 18~38 轮 ⇒ 光固定成本就 25~95s，
比整个全量测试（902 项 / 37s）还慢。同进程复用之后每轮只剩测试本身的时间（~50ms 量级）。

**等价性怎么保证**（不做这两件事就会**假绿**，比慢更糟）：

  1. **每轮清掉项目内已导入的模块**：契约测试里有的是在**模块级**读源码的
     （`SRC = _read(...)`），不清 `sys.modules` 就会拿上一轮的旧内容 —— 变异施加了、
     断言却看着旧文本，于是漏报。这里按 `__file__` 落在仓库内的模块整批剔除，
     强制重新 import（含 `tests/conftest.py`）。
  2. **`-p no:cacheprovider`**：不写 `.pytest_cache`（变异轮次之间不该互相影响，
     也不需要 `--lf`）。
  3. **字节码缓存一律绕开**（本模块 import 时即生效，两行都要）：`sys.dont_write_bytecode`
     让它**不写**，`sys.pycache_prefix` 指到仓库外的临时目录让它**不读**仓库树里的。
     这条不是洁癖，是 09-18 抓到的真事故：仓库在 Windows 盘（drvfs）上，`shutil.copy2`
     还原后源文件 mtime 与变异时**落在同一秒粒度里**（还有一类变异长度都不变，如 `sum`→`max`），
     CPython 的 "mtime+size" 判据于是认为缓存仍然有效，**继续执行上一轮的变异版字节码**：
     文件里明明写着 `"protocol": "auto"`，import 进来却是 `'chat'`。
     后果是变异结果**双向失真** —— 真变异可能报"没捕获"（假阴性），
     也可能把上一轮的变异算到这一轮头上（假阳性）。只 `dont_write_bytecode` 不够，
     它挡不住"读旧 pyc"。

保险绳：任何一步抛异常（pytest 重复进入内部状态出问题、插件报错）→ **自动回落到
子进程**并打印一行提示，绝不把"跑不起来"变成"没捕获"。也可用环境变量
`MUTATE_SUBPROCESS=1` 全程强制走子进程（对照用）。

★ 第二道保险绳：`guard(paths)` ★ —— 变异脚本是"改源码 → 跑测试 → 还原"，而**杀进程**
（Ctrl-C、工具超时发的 SIGTERM、容器回收）会让"还原"那一半永远不执行，把变异留在源码树里。
这不是假想：09-18 本会话被 60s 工具超时杀过两次，两次都留下变异（一次 `config_loader` 的
`protocol="chat"`、一次 `factory` 的 auto 不包两层），而它们**不报错**，只让之后所有观察失真
（源码树看着是好的、跑起来是错的）。故变异脚本必须在自己那份文件集上登记一次 `guard()`：
它把原文快照进内存，并挂上 SIGINT / SIGTERM / atexit 还原钩子 —— 怎么死都能还原。

用法（只在变异脚本内部用）：
    import _mutate_lib
    p = _mutate_lib.run([sys.executable, "-m", "pytest", FILE, "-q"], cwd=str(ROOT),
                        capture_output=True, text=True, errors="replace")
    rc = p.returncode; out = p.stdout + p.stderr
非 pytest 的命令（如 `run_csharp_tests.py`）原样交给 `subprocess.run`。
"""

from __future__ import annotations

import atexit
import io
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]

# ★ 变异轮次一律绕开字节码缓存（见模块 docstring 第 3 条：drvfs 上 copy2 还原后 mtime 判据会失效）★
#   dont_write_bytecode：不写；pycache_prefix（仓库外临时目录）：不读仓库树里那些可能陈旧的。
sys.dont_write_bytecode = True
sys.pycache_prefix = os.path.join(tempfile.gettempdir(), "agent_loop_mutate_nocache")


class Completed(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _pytest_args(cmd) -> list[str] | None:
    """从 `[python, -X, utf8, -m, pytest, ...]` 里剥出 pytest 自己的参数。"""
    cmd = [str(c) for c in cmd]
    if "pytest" not in cmd:
        return None
    i = cmd.index("pytest")
    return cmd[i + 1 :]


# ---------------------------------------------------------------- 意外死亡时的还原保险绳

_GUARD: "dict[Path, bytes]" = {}
_GUARD_INSTALLED = False


def _restore_all() -> None:
    """把登记过的文件逐字节写回（幂等；只写内容变了的那些）。"""
    for path, data in list(_GUARD.items()):
        try:
            if path.read_bytes() != data:
                path.write_bytes(data)
                print("  [mutate 保险绳] 已还原 %s（进程被中断，变异没来得及自己撤）" % path.name)
        except OSError:
            pass


def _on_signal(signum, _frame) -> None:
    _restore_all()
    # 还原后交回默认处置，让退出码/信号语义保持正常（否则调用方看不到"被杀"）
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    except Exception:  # noqa: BLE001
        raise SystemExit(128 + int(signum))


def guard(paths) -> None:
    """登记待变异文件：快照 + 装 SIGINT/SIGTERM/atexit 还原钩子（见模块 docstring 末尾）。"""
    global _GUARD_INSTALLED
    for p in paths:
        p = Path(p)
        _GUARD.setdefault(p, p.read_bytes())
    if _GUARD_INSTALLED:
        return
    _GUARD_INSTALLED = True
    atexit.register(_restore_all)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):      # 非主线程 / 平台不支持：退化为只靠 atexit
            pass


def _purge_project_modules() -> int:
    """清掉仓库内已导入的模块（按 __file__ 判定），强制下一轮重新 import。"""
    root = ROOT.resolve()
    n = 0
    for name, mod in list(sys.modules.items()):
        if name == "__main__" or name.split(".")[0] in ("_pytest", "pytest", "pluggy", "iniconfig"):
            continue
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            p = Path(f).resolve()
        except OSError:
            continue
        if p == root or root in p.parents:
            del sys.modules[name]
            n += 1
    return n


def run(cmd, **kwargs):
    """同 `subprocess.run` 的返回形状；pytest 命令走同进程，其余走真子进程。"""
    if os.environ.get("MUTATE_SUBPROCESS"):
        return subprocess.run(cmd, **kwargs)
    args = _pytest_args(cmd)
    if args is None:
        return subprocess.run(cmd, **kwargs)
    if "-p" not in args:
        args = args + ["-p", "no:cacheprovider"]

    # ★ 环境不同就必须走真子进程：`cwd=` 换了目录、或给了自定义 `env=` 时，
    #   同进程跑测的语义**不等价**（`python -m pytest` 会把 cwd 塞进 sys.path[0]，
    #   相对路径也按那个目录解析）。`mutate_check_manual_identity.py` 正是靠
    #   `cwd=ROOT.parent` + `agent_loop/tests/...` 来避开同名外壳文件 `agent_loop.py`
    #   —— 第一版同进程化把它的路径解析成了不存在，基线当场变红（脚本如实报错，没假绿）。
    cwd = kwargs.get("cwd")
    if (cwd and Path(cwd).resolve() != Path.cwd().resolve()) or kwargs.get("env"):
        return subprocess.run(cmd, **kwargs)

    # 快速路径：目标文件是"无装饰器、非 async"的纯契约测试 ⇒ 直接调函数，不开 pytest 会话。
    # 实测：pytest 每轮固定 0.6s（配置+插件+收集+收尾），直接调用 ~10ms —— 变异脚本动辄 20~40 轮，
    # 这是剩下的那笔大头。语义等价性：断言就是普通 assert，pytest 的重写只影响报错信息；
    # 一旦遇到 fixture/装饰器/async/跳过（skipif、importorskip）**立刻回落到 pytest**，
    # 免得"本应 skip 的测试被我们跑成了 fail"这种**假捕获**。
    if not os.environ.get("MUTATE_PYTEST"):
        direct = _try_direct(args)
        if direct is not None:
            _note_direct_once()
            return direct

    try:
        import pytest  # noqa: PLC0415  （延迟导入：非 pytest 路径不该付这个成本）
    except Exception:                                    # pragma: no cover
        return subprocess.run(cmd, **kwargs)
    _purge_project_modules()
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = int(pytest.main(args))
    except BaseException as e:                            # 保险绳：回落到子进程
        sys.stdout.write(f"[_mutate_lib] 同进程跑测失败（{type(e).__name__}: {e}）→ 回落子进程\n")
        sys.stdout.flush()
        return subprocess.run(cmd, **kwargs)
    return Completed(rc, buf.getvalue(), "")


# ---------------------------------------------------------------------------
# 快速路径：不开 pytest 会话，直接 import 测试模块并调用目标测试函数
# ---------------------------------------------------------------------------

_DIRECT_OK: set[str] = set()      # 允许走快速路径的测试文件（按内容判定一次）
_DIRECT_NO: set[str] = set()
_NOTED = False
_IGNORED_FLAGS = ("-x", "--exitfirst", "-k", "-m", "--lf", "--ff", "--last-failed",
                  "--maxfail", "--durations", "--cov", "--stepwise")


def _note_direct_once() -> None:
    global _NOTED
    if not _NOTED:
        _NOTED = True
        print("[_mutate_lib] 快速路径：直接调用测试函数（无 pytest 会话）；MUTATE_PYTEST=1 强制走 pytest")


def _direct_eligible(path: Path) -> bool:
    """无装饰器（skipif/parametrize/fixture…一律回落）且非 async 才允许直调。"""
    key = str(path)
    if key in _DIRECT_OK:
        return True
    if key in _DIRECT_NO:
        return False
    ok = False
    try:
        import ast  # noqa: PLC0415
        tree = ast.parse(path.read_text(encoding="utf-8"))
        ok = True
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test"):
                ok = False
                break
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
                ok = False
                break
    except Exception:
        ok = False
    (_DIRECT_OK if ok else _DIRECT_NO).add(key)
    return ok


def _try_direct(args: list[str]):
    """能直调就返回 Completed；不能（fixture/装饰器/跳过/导入失败）返回 None 让调用方回落。"""
    # 带值的短选项：其后那个 token 不是位置参数（`-p no:cacheprovider`、`-k expr`…）
    positional, skip_next = [], False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in ("-p", "-k", "-m", "--durations", "--maxfail", "--deselect", "--ignore"):
            skip_next = True
            continue
        if not a.startswith("-"):
            positional.append(a)
    if not positional or any(f in args for f in _IGNORED_FLAGS):
        return None
    if len(positional) > 1:                       # 多目标：留给 pytest（选择语义由它负责）
        return None
    target = positional[0]
    file_part, _, node = target.partition("::")
    path = Path(file_part)
    if not path.is_file() or not _direct_eligible(path):
        return None

    _purge_project_modules()
    # 直调路径没有 pytest 帮忙摆 sys.path（tests/conftest.py 是 pytest 才导入的），自己补：
    # 仓库根（`agent_loop` 包本体）与它的父目录（`import agent_loop` 要能解析）。
    for _p in (str(ROOT.parent), str(ROOT)):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import importlib.util  # noqa: PLC0415
    import uuid  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(f"_mut_{uuid.uuid4().hex[:8]}", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"[_mutate_lib] 测试模块导入失败（{type(e).__name__}: {e}）→ 回落 pytest")
        return None

    names = [node] if node else [n for n in dir(mod) if n.startswith("test_")]
    fails = []
    for n in names:
        fn = getattr(mod, n, None)
        if not callable(fn):
            return None                            # 名字对不上（参数化等）→ 回落
        try:
            fn()
        except BaseException as e:                 # noqa: BLE001
            if type(e).__name__ in ("Skipped", "XFailed", "OutcomeException", "_pytest.outcomes.Skipped"):
                print(f"[_mutate_lib] 目标被 skip（{n}）→ 回落 pytest（不能把 skip 当成'捕获'）")
                return None
            if type(e).__name__ == "SystemExit":
                return None
            fails.append((n, e))
    if not fails:
        return Completed(0, f"{len(names)} passed\n", "")
    body = "".join(f"FAILED {file_part}::{n} - {type(e).__name__}: {e}\n" for n, e in fails)
    return Completed(1, body + f"{len(fails)} failed, {len(names) - len(fails)} passed\n", "")
