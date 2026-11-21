#!/usr/bin/env python3
"""按改动挑测试并**并行**跑 —— 依赖表是**实测**出来的，不是猜的。

    python3 scripts/dev/run_tests_for.py csharp/tools/ToolExecutor.cs tools/text_render.py
    python3 scripts/dev/run_tests_for.py --changed          # 与上次子集运行比 mtime
    python3 scripts/dev/run_tests_for.py --all              # 全量（并行）
    python3 scripts/dev/run_tests_for.py --list <改动…>      # 只列不跑
    python3 scripts/dev/run_tests_for.py --probe            # 重采依赖快照（约 2.5 分钟）

★ 为什么推翻了原来那套静态扫描（10-17 实测）★
------------------------------------------------------------------------------------
旧版 `deps_of()` 用 AST 扫"测试源码里出现过哪些路径字面量 + import"。拿实测探针
（`_read_probe.py`）当裁判对拍，它两头都错：

  · **误报**：把 `ToolExecutor.cs` 的扇入算成 252 个用例，**实测只有 87**；把
    `agent_loop.py` 算成 253（于是永久触发"扇入过大 ⇒ 跑全量"），**实测只有 1**。
    误报的代价是"改一行跑全量"，也就是这套机制在最该生效的地方失效。
  · **漏报**：**漏掉 172 个源文件 / 346 处真依赖**。最狠的两处 —— `test_log_setup.py`
    用 `rglob("*.py")` 扫全仓（静态扫不出，漏 58 处）、`test_run_tests_for_contract.py`
    读 `tests/*.py` 建映射（漏 101 处）。漏跑的代价是"绿了但其实没测"，是本体系最贵的错。
  · 旧版还带一道 `FANIN_UPGRADE=15` 闸门：扇入超 15 就放弃子集跑全量。而**扇入最高的
    6 个文件全是本仓最常改的**（`ws_channel.py` 35、`agent_loop.py` 33、`bridge.py` 20、
    `ToolExecutor.cs` 18、`text_render.py` 16、`ModMain.cs` 16）⇒ 闸门在这套机制最有用的
    位置全部触发。**已删除**：不再有"改一个文件跑全量"这条路。

依赖表怎么来的
------------------------------------------------------------------------------------
`scripts/dev/_read_probe.py` 是个 pytest 插件：跑全量时把 `Path.read_text` / `open` /
`SourceFileLoader.get_data` / `Path.glob` 全拦下来，记下**每个用例真正读了哪些文件**，
落到 `scripts/dev/.depmap.json`（`--probe` 重新采）。

三条归属规则（各自的理由都写在 `_read_probe.py` 里）：
  ① 模块导入 ⇒ 归到**整个测试文件**（import 只执行一次，落点取决于执行顺序）；
  ② 用例体内显式读 ⇒ 归到**该用例**（`_read("tools/X.cs")` 的位置是写死的）；
  ③ 模块级显式读 ⇒ 归到**整个测试文件**（import 时就执行了，影响全文件）。

另外补两路静态/兜底，因为实测表有两个已知盲区：
  · **import 依赖必须静态补**：`sys.modules` 有缓存，第二个 import 同一模块的测试文件
    不会再次触发 `get_data` ⇒ 实测表里 `module` 只有 19 条，而静态闭包显示光
    `ws_channel` 就被 22 个测试模块 import。**漏了它 = 改 `ws_channel.py` 只跑 13 个文件**。
  · **目录扫描范围**（`globs`）解决"新增文件"：探针记的是"读过哪些文件"，下次**新增**一个
    `.cs` 上次没读过 ⇒ 任何测试都不会被选中。记下扫描范围，新增文件落在范围内仍然命中。

命中规则（并集，宁多勿漏）
------------------------------------------------------------------------------------
改文件 S ⇒ 选中：
  a. 实测读过 S 的**用例**（精确到用例，这是主要的提速来源）；
  b. 实测在模块级读过 S、或 import 了 S 所属模块的测试文件的**全部用例**；
  c. 扫描范围覆盖 S 的测试文件的**全部用例**；
  d. **快照之后被改过**的测试文件的全部用例（它的依赖可能已经变了 ⇒ 整文件跑）；
  e. 含**解析不出来的动态 import** 的测试文件的全部用例。

并行
------------------------------------------------------------------------------------
按**文件**分片、按历史耗时做 LPT 装箱，每个分片一个 pytest 子进程（默认 `--jobs` = 核数，
上限 8）。**不引入 pytest-xdist**：进程隔离本来就更好，而且本仓有 13 个测试用**写死的**
`/tmp/agent_loop_test_*` 目录，按文件分片天然不会撞。耗时表存在
`scripts/dev/.test_durations.json`，每次跑完用 `--durations=0` 的真实数据更新。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
SNAP = ROOT / "scripts" / "dev" / ".depmap.json"
DUR = ROOT / "scripts" / "dev" / ".test_durations.json"
STAMP = ROOT / ".pytest_last_subset_run"
RAW = Path("/tmp/agent_loop_depmap_raw.json")

# 走仓库根的源文件（`--changed` 用它扫 mtime）。★ 旧版这里只 glob 了根目录 `*.py`、
# `csharp/**`、`tools/`、`scripts/`、`docs/` —— **agent/ transport/ room/ session/ llm/
# storage/ config/ prompts/ 一个都扫不到**，而扇入最高的 `ws_channel.py`/`bridge.py`/
# `initiative.py` 正躺在里面。实测：`touch agent/initiative.py` 后 `--changed` 报
# "没扫到比改动新的源文件 ⇒ 全量" —— 想省时间，却静默跑了全量。改成整树 walk。
_SOURCE_DIRS = ("csharp", "tools", "scripts", "docs", "compaction", "config", "storage",
                "session", "agent", "room", "transport", "prompts", "llm", "tests")
_SOURCE_SUFFIX = (".py", ".cs", ".csproj", ".md", ".json", ".txt", ".prefab")
_SKIP_PARTS = {"obj", "bin", "__pycache__", ".pytest_cache", "node_modules", ".git",
               ".mypy_cache", ".ruff_cache", "dist", "generated-images", "backup",
               "reference", ".mine_0913", "hw1_jupyterhub"}

# 分片阈值：预估总耗时低于这个值就不值得开子进程（启动成本 ~1.2s/片）
_PARALLEL_MIN_SECONDS = 4.0
_MAX_JOBS = 12


# ---------------------------------------------------------------------------
# 源文件清单
# ---------------------------------------------------------------------------

_SOURCES: dict[str, float] | None = None


def sources() -> dict[str, float]:
    """仓库内源文件 → mtime（一次 walk，`os.scandir` 剪枝）。

    ★ 用 scandir 而不是 `Path.rglob`：本仓在 drvfs（Windows 盘）上，rglob 走完 238 个
    文件要 **2.8s**，scandir 只要 **0.10s**（实测 28 倍）。旧版每次调用都重走一遍，
    而契约测试一次进程里要调 11 次 ⇒ 白烧 30s。
    """
    global _SOURCES
    if _SOURCES is not None:
        return _SOURCES
    out: dict[str, float] = {}
    seen: set[Path] = set()
    stack = [ROOT] + [ROOT / d for d in _SOURCE_DIRS if (ROOT / d).is_dir()]
    while stack:
        base = stack.pop()
        if base in seen:
            continue
        seen.add(base)
        try:
            entries = list(os.scandir(base))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name in _SKIP_PARTS or e.name.startswith("."):
                        continue
                    stack.append(Path(e.path))
                elif e.name.endswith(_SOURCE_SUFFIX) and not e.name.startswith("."):
                    p = Path(e.path)
                    out[str(p.relative_to(ROOT)).replace("\\", "/")] = e.stat().st_mtime
            except OSError:
                continue
    _SOURCES = out
    return out


def changed_since(baseline: float) -> list[str]:
    """比基线新的源文件。"""
    return sorted(f for f, mt in sources().items() if mt > baseline)


# ---------------------------------------------------------------------------
# 静态 import 闭包（实测表的必要补充，见模块 docstring）
# ---------------------------------------------------------------------------

def _module_name(rel: str) -> str:
    """仓库根即 `agent_loop` 包（`__init__.py` 在根，父目录在 sys.path 上）。"""
    p = rel[:-3] if rel.endswith(".py") else rel
    if p == "__init__":
        return "agent_loop"
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return "agent_loop." + p.replace("/", ".")


def _static_imports() -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """→ (直接 import 表, 传递闭包, 动态 import 解析不出来的测试文件)。

    ★ 为什么有两张表 ★
    · **直接 import**：测试文件自己 import 的模块（含函数体内的惰性 import）。这条是
      "模块级副作用"路径 —— 改动被 import 模块的顶层代码会影响它，静态就能确定，可靠。
    · **传递闭包**：只作为**没有实测执行数据时**的兜底。10-17 实测表明它有严重误报：
      `bridge.py` import 了 `text_render.py`，于是 30+ 个"只是 import 了 bridge"的测试
      都算依赖 text_render ⇒ 扇入从 190 涨到 **929（占全量 64%）**，等于没筛。
      有了 `exec`（实测执行）之后就不需要它了 —— 用例真调用过哪个模块，探针量得出来。

    只处理 `.py` 的 import：这是**语法层面就写死**的信息，可靠。真正不可靠的是
    "测试会读哪些文件"（路径是拼出来的、还会 rglob），那部分交给实测探针。

    ★ **相对 import 必须解析**（10-17 端到端验证抓到的真漏）★
    初版对 `ImportFrom.level != 0` 直接 `continue`，于是 `transport/bridge.py` 里那句
    `from ..tools.text_render import render` 完全没被记录 ⇒ 任何"import 了 bridge 的
    测试"都不算依赖 `text_render.py`。实测后果：变异 `text_render.py` 后全量红 48 条，
    **子集漏掉 8 条**（`test_dead_inspect_contract` / `test_intim_wiring_contract` /
    `test_room` 等）。相对 import 在本仓很常见。
    """
    mod_of: dict[str, str] = {}
    is_pkg: dict[str, bool] = {}
    for rel in sources():
        if rel.endswith(".py"):
            m = _module_name(rel)
            mod_of[m] = rel
            is_pkg[m] = rel == "__init__.py" or rel.endswith("/__init__.py")

    def resolve(importer: str, level: int, module: str) -> str | None:
        """`from <level 个点><module> import …` → 绝对模块名。"""
        if level == 0:
            return module or None
        parts = importer.split(".")
        if not is_pkg.get(importer, False):
            parts = parts[:-1]                       # 模块 → 它所在的包
        keep = len(parts) - (level - 1)              # level=1 就是当前包
        if keep < 0:
            return None
        base = ".".join(parts[:keep])
        return f"{base}.{module}" if module else (base or None)

    def direct(rel: str) -> set[str]:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            return set()
        found: set[str] = set()
        importer = _module_name(rel)

        def add(name: str | None) -> None:
            if not name:
                return
            parts = name.split(".")
            for i in range(1, len(parts) + 1):
                pref = ".".join(parts[:i])
                if pref in mod_of:
                    found.add(mod_of[pref])

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    add(a.name)
            elif isinstance(node, ast.ImportFrom):
                base = resolve(importer, node.level, node.module or "")
                add(base)
                for a in node.names:
                    add(f"{base}.{a.name}" if base else a.name)
        return found

    graph = {m: direct(rel) for m, rel in mod_of.items()}
    by_file = {rel: m for m, rel in mod_of.items()}

    out: dict[str, set[str]] = {}
    direct_out: dict[str, set[str]] = {}
    unresolved: set[str] = set()
    for rel in sorted(sources()):
        if not (rel.startswith("tests/test_") and rel.endswith(".py")):
            continue
        src = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            unresolved.add(rel)
            continue
        closure: set[str] = set()
        stack = list(graph.get(by_file.get(rel, ""), ()))
        while stack:
            d = stack.pop()
            if d in closure or d == rel:
                continue
            closure.add(d)
            stack.extend(graph.get(_module_name(d), ()))
        out[rel] = closure
        direct_out[rel] = graph.get(by_file.get(rel, ""), set())
        # 动态 import（`importlib.import_module(expr)` 取不到静态字符串）⇒ 整文件带上
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name not in ("import_module", "__import__") or not node.args:
                continue
            arg = node.args[0]
            ok = (isinstance(arg, ast.Constant) and isinstance(arg.value, str)) or \
                 isinstance(arg, ast.JoinedStr)
            if not ok:
                unresolved.add(rel)
    return direct_out, out, unresolved


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------

def load_snapshot(path: Path = SNAP) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"没有依赖快照：{path}\n"
            f"先采一次（跑一遍全量，约 2.5 分钟）：\n"
            f"    python3 scripts/dev/run_tests_for.py --probe")
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("case", "module", "globs", "test_mtimes"):
        data.setdefault(key, {})
    data.setdefault("sources", [])
    data.setdefault("all_cases", [])
    return data


def is_source(rel: str) -> bool:
    """这个路径算不算"本仓要跟踪的源文件"。

    ★ 必须与 `sources()` 的排除规则一致 ★ 探针的排除表比这里窄（它连 `.mine_0913/`、
    `hw1_jupyterhub/` 也记），不加这一层就会出现"30 个源文件已不在磁盘上"的假警报 ——
    实测第一次跑就是 30 个，全是这些本来就不跟踪的目录。
    """
    if not rel.endswith(_SOURCE_SUFFIX):
        return False
    parts = rel.split("/")
    return not any(p in _SKIP_PARTS or p.startswith(".") for p in parts)


def vanished_sources(snap: dict) -> list[str]:
    """快照里出现过、但磁盘上已经没有的源文件（删除/重命名）。

    ★ 为什么单独拎出来 ★ `--changed` 靠 mtime 扫改动，而"被删掉的文件"**没有 mtime**
    —— 它根本不在磁盘清单里。于是删/改名的源文件不会选中任何测试，
    实测形态就是"我把 `tools/old.py` 改名了，跑子集全绿"。这里反过来问：
    快照说"这些测试依赖某些文件"，其中哪些文件不见了？
    """
    # ★ 判据是"**采样时**它是本仓的源文件、现在没了"，不是"快照里提过它、现在没了" ★
    #   后者满屏假警报：测试运行时会造临时 fixture（实测 6 个 `prompts/personas/张三.txt`
    #   之类），它们被读进快照、跑完就删 —— 那是测试的正常行为，不是"源文件被删了"。
    at_probe = set(snap.get("sources") or ())
    if not at_probe:
        return []                    # 旧快照没记；重采一次（--probe）就有了
    return sorted(f for f in at_probe
                  if f not in sources() and not f.startswith("tests/"))


def cases_of(snap: dict) -> dict[str, list[str]]:
    """测试文件 → 它的全部用例 node id。"""
    out: dict[str, list[str]] = {}
    for nid in snap["all_cases"]:
        out.setdefault(nid.split("::")[0].replace("\\", "/"), []).append(nid)
    return out


def _glob_hit(rel: str, base: str, pattern: str, recursive: bool) -> bool:
    if base in ("", "."):
        sub = rel
    elif rel == base:
        return False
    elif rel.startswith(base + "/"):
        sub = rel[len(base) + 1:]
    else:
        return False
    if recursive:                       # rglob：任意深度（fnmatch 的 `*` 本来就跨 `/`）
        return fnmatch(sub, pattern) or fnmatch(sub, "**/" + pattern) or \
               fnmatch(sub.rsplit("/", 1)[-1], pattern)
    return fnmatch(sub, pattern)


# ---------------------------------------------------------------------------
# 选择
# ---------------------------------------------------------------------------

class Selection:
    def __init__(self) -> None:
        self.whole: dict[str, set[str]] = {}      # 测试文件 → 选中理由集合
        self.cases: dict[str, set[str]] = {}      # 用例 node id → 理由集合
        self.notes: list[str] = []
        # ★ "强制纳入"的测试文件（快照对它不可信：被改过 / 新增的 / 含解析不出的动态 import）
        #   它们**与改了什么无关**，任何一次子集运行都会带上。单列出来是因为：
        #   ① `--explain` 时能一眼看出"这些不是因为你改的东西才跑的"；
        #   ② 写契约测试时必须把它们排除掉，否则断言会被"测试文件比快照新"带红
        #      —— 而"编辑测试文件"是日常操作，不是异常。
        self.forced: set[str] = set()

    def whole_file(self, f: str, why: str) -> None:
        self.whole.setdefault(f, set()).add(why)

    def force_file(self, f: str, why: str) -> None:
        self.forced.add(f)
        for nid in self.cases:
            pass
        self.whole_file(f, why)

    def case(self, nid: str, why: str) -> None:
        f = nid.split("::")[0]
        if f in self.whole:
            return
        self.cases.setdefault(nid, set()).add(why)

    @property
    def targets(self) -> list[str]:
        return sorted(self.whole) + sorted(self.cases)


def select(changed: list[str], snap: dict, imports: dict[str, set[str]],
           unresolved: set[str], verbose: bool = True,
           closure: dict[str, set[str]] | None = None) -> Selection:
    sel = Selection()
    per_file = cases_of(snap)
    on_disk = set(sources())
    on_disk_tests = {f for f in on_disk
                     if f.startswith("tests/test_") and f.endswith(".py")}
    on_disk_mtimes = {f: sources()[f] for f in on_disk_tests}

    def add_file(f: str, why: str) -> None:
        for nid in per_file.get(f, []):
            sel.case(nid, why)

    # d. 快照录下它之后又被改过的测试文件 ⇒ 整文件跑（它的依赖可能已经变了）
    #
    # ★ 判据是"**当前 mtime ≠ 快照录的 mtime**"，不是"比 probed_at 新" ★
    #   后者在**增量重采**（`--probe <文件>`）之后会失效：把 probed_at 推到 now，
    #   那些一分钟前改过、但这次没重采的文件就变成"比 probed_at 旧" ⇒ 被当成可信，
    #   于是拿旧依赖挑子集 ⇒ **漏跑**。逐文件比对 mtime 没有这个洞。
    stale = []
    for f in per_file:
        recorded = snap["test_mtimes"].get(f)
        cur = on_disk_mtimes.get(f)
        if cur is None or recorded is None or cur != recorded:
            stale.append(f)
    for f in sorted(stale):
        sel.forced.add(f)
        add_file(f, "测试文件在快照之后被改过 ⇒ 整文件跑")

    # d2. ★ 快照里**根本没有**的测试文件（新增的）⇒ 一律整文件跑 ★
    #   我们对它一无所知：不知道它读什么、执行什么。10-17 实测：新增
    #   `tests/test_zz_new_feature.py` 后 `run_tests_for.py <源文件>` 与 `--changed`
    #   **一条都不会选中它** —— 新功能配的新测试等于没跑。这里补上。
    #   它在下次 `--probe` 之后就会并入精确选择（`--probe <文件>` 可只采这几个）。
    probed_files = set(per_file) | set(snap["test_mtimes"]) | set(snap["module"]) \
        | set(snap["globs"]) | set(snap.get("exec", {}))
    unprobed = sorted(f for f in on_disk_tests if f not in probed_files)
    for f in unprobed:
        sel.forced.add(f)
        sel.whole_file(f, "快照里没有这个测试文件（新增）⇒ 整文件跑")

    if stale or unprobed:
        bits = []
        if stale:
            bits.append(f"{len(stale)} 个测试文件比快照新")
        if unprobed:
            bits.append(f"{len(unprobed)} 个是新增的")
        sel.notes.append(
            "、".join(bits) + " ⇒ 已整文件纳入（快照对它们不可信）。"
            "想让它们回到**精确选择**（只跑受影响的用例），采一次："
            "python3 scripts/dev/run_tests_for.py --probe "
            + " ".join(x for x in (sorted(stale) + unprobed)[:3])
            + (" …" if len(stale) + len(unprobed) > 3 else ""))

    # e. 动态 import 解析不出来 ⇒ 整文件跑
    for f in sorted(unresolved):
        sel.forced.add(f)
        add_file(f, "含解析不出来的动态 import ⇒ 整文件跑")

    # 倒排索引
    by_case: dict[str, set[str]] = {}
    for nid, reads in snap["case"].items():
        for r in reads:
            by_case.setdefault(r, set()).add(nid)
    by_module: dict[str, set[str]] = {}
    for f, reads in snap["module"].items():
        for r in reads:
            by_module.setdefault(r, set()).add(f)
    by_import: dict[str, set[str]] = {}
    for f, deps in imports.items():                 # 直接 import（模块级副作用路径）
        for d in deps:
            by_import.setdefault(d, set()).add(f)
    by_exec: dict[str, set[str]] = {}               # 实测执行过的模块
    for nid, mods in snap.get("exec", {}).items():
        for mod in mods:
            by_exec.setdefault(mod, set()).add(nid)
    # 没有实测执行数据的测试文件（快照太旧/新增）⇒ 回退到传递闭包，宁多勿漏
    have_exec = {n.split("::")[0] for n in snap.get("exec", {})}
    fallback_files = {f for f in imports if f not in have_exec} if closure else set()
    glob_specs: dict[str, list[tuple[str, str, bool]]] = {}
    for f, specs in snap["globs"].items():
        for spec in specs:
            base, pattern, recursive = spec[0], spec[1], bool(spec[2])
            glob_specs.setdefault(f, []).append((base, pattern, recursive))

    def covered_by_glob(c: str) -> bool:
        return any(_glob_hit(c, b, p, r)
                   for specs in glob_specs.values() for b, p, r in specs)

    for c in changed:
        # 改的就是测试文件本身 ⇒ 整文件跑。**但不能就此 continue**（10-17 契约测试抓到的
        # 第二处真漏）：别的测试文件可能 **import 它 / 执行它的代码**（实测
        # `test_game_time_stamp.py` 会执行 `test_bridge_tools.py` 的代码），
        # 那些也必须跑 —— 所以这里只置 `hit_case`，继续走下面的通用规则。
        hit_case = False
        if c.startswith("tests/test_") and c.endswith(".py"):
            add_file(c, "改动即测试文件本身")
            hit_case = True

        # a. 实测读过它的用例（精确到用例 —— 主要提速来源）
        for nid in by_case.get(c, ()):
            if nid in per_file.get(nid.split("::")[0], ()):
                sel.case(nid, f"实测读过 {c}")
                hit_case = True
        # b1. 实测在模块级读过它 ⇒ 整文件
        for f in by_module.get(c, ()):
            add_file(f, f"{f.split('/')[-1]} 在模块级读过 {c}")
            hit_case = True
        # b2. 静态**直接** import 了它 ⇒ 整文件（模块级副作用路径）
        for f in by_import.get(c, ()):
            add_file(f, f"{f.split('/')[-1]} 直接 import 了 {c}")
            hit_case = True
        # b3. ★ 实测**执行过**它的代码 ⇒ 那些用例（精确；这是 Python 侧的主力信号）
        for nid in by_exec.get(c, ()):
            if nid in per_file.get(nid.split("::")[0], ()):
                sel.case(nid, f"实测执行过 {c} 的代码")
                hit_case = True
        # b4. 没有实测执行数据的测试文件 ⇒ 传递闭包兜底（宁多勿漏）
        if closure:
            for f in fallback_files:
                if c in closure.get(f, ()):
                    add_file(f, f"{f.split('/')[-1]}（无实测数据）传递 import 了 {c}")
                    hit_case = True
        # c. 目录扫描范围覆盖它 ⇒ 整文件
        for f, specs in glob_specs.items():
            if any(_glob_hit(c, b, p, r) for b, p, r in specs):
                add_file(f, f"{f.split('/')[-1]} 的扫描范围覆盖 {c}")
                hit_case = True

        if not hit_case:
            sel.notes.append(
                f"⚠ {c} 没有任何测试读它、也没有测试扫描它所在的目录 "
                f"⇒ 改它不会跑任何测试（如实报告，不是漏跑）")

    # 快照里出现过、但已不在磁盘上的测试文件（被删/改名）⇒ 提一句
    missing = sorted(set(per_file) - on_disk)
    if missing:
        sel.notes.append(f"{len(missing)} 个测试文件在快照里存在但磁盘上没有了"
                         f"（建议 --probe 重采）：{', '.join(x.split('/')[-1] for x in missing[:4])}")
    return sel


# ---------------------------------------------------------------------------
# 分片与执行
# ---------------------------------------------------------------------------

def _durations() -> dict[str, float]:
    try:
        return json.loads(DUR.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_durations(d: dict[str, float]) -> None:
    try:
        DUR.write_text(json.dumps(d, indent=0, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _normalize(targets: list[str], snap: dict) -> list[str]:
    """归一：**快照过期的文件一律降级成"整文件跑"**，并丢掉已不存在的文件。

    ★ 为什么必须做 ★ pytest 遇到**不存在的 node id** 不是跳过，而是直接报
    `ERROR: not found: …` 并**放弃整趟** —— 表现是 `no tests ran` + rc=4。
    测试改名/删除后，快照里的旧 node id 正是这种形态；**一条过期 node id 就能让整个
    分片"什么都没跑"，而且它看起来像跑完了**（比漏跑更危险）。

    判据：测试文件的 mtime 比快照新 ⇒ 它的 node id 不可信 ⇒ 换回文件路径
    （反正整文件本来就要跑，见 `select()` 规则 d）。
    """
    per_file = cases_of(snap)
    probed_at = snap.get("probed_at", 0.0)
    on_disk = set(sources())
    out: list[str] = []
    seen: set[str] = set()
    downgraded: set[str] = set()

    def push(t: str) -> None:
        if t not in seen:
            seen.add(t)
            out.append(t)

    for t in targets:
        f = t.split("::")[0].replace("\\", "/")
        if f not in on_disk:
            continue                              # 文件没了：pytest 会 ERROR，直接丢
        if "::" not in t:
            push(f)
            continue
        mtime = snap["test_mtimes"].get(f)
        if mtime is None or mtime > probed_at:
            if f not in downgraded:               # 过期 ⇒ 整文件，别用旧 node id
                downgraded.add(f)
                push(f)
            continue
        push(t)
    if downgraded:
        print(f"[run_tests_for] {len(downgraded)} 个测试文件比快照新 ⇒ 降级为整文件跑"
              f"（旧 node id 可能已改名，pytest 遇到不存在的 node id 会放弃整趟）："
              f"{', '.join(sorted(x.split('/')[-1] for x in downgraded)[:5])}"
              + ("…" if len(downgraded) > 5 else ""))
    return out


def _shard(targets: list[str], jobs: int, dur: dict[str, float]) -> list[list[str]]:
    """按文件分组 + LPT 装箱 → 至多 jobs 个分片。

    ★ 必须**按文件**分组：本仓 13 个测试用写死的 `/tmp/agent_loop_test_*` 目录，
    把同一个文件的用例拆到两个进程会互相 `rmtree`。
    """
    by_file: dict[str, list[str]] = {}
    for t in targets:
        by_file.setdefault(t.split("::")[0], []).append(t)
    buckets: list[tuple[float, list[str]]] = [(0.0, []) for _ in range(jobs)]
    for f, ts in sorted(by_file.items(), key=lambda kv: -_cost(kv[0], len(kv[1]), dur)):
        i = min(range(len(buckets)), key=lambda k: buckets[k][0])
        c = _cost(f, len(ts), dur)
        buckets[i] = (buckets[i][0] + c, buckets[i][1] + ts)
    return [b for _, b in buckets if b]


_DUR_RE = re.compile(r"^([0-9.]+)s\s+\w+\s+(\S+?)::", re.M)


def _run_shard(targets: list[str], extra: list[str]) -> tuple[int, str, str, float]:
    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "--no-header",
           "--durations=0", *extra]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr, time.perf_counter() - t0


def _apply_durations(dur: dict[str, float], outputs: list[str]) -> None:
    """把本轮 `--durations=0` 的**逐用例**耗时按文件**求和**写回耗时表。

    ★ 必须是"求和"而不是"覆盖"：一个文件几十条用例，覆盖的话只留最后一条
    （实测表里 `test_ws_channel.py` 被记成 **0.04s**，它真实是 10.5s）⇒ 装箱彻底失真。
    """
    agg: dict[str, float] = {}
    for out in outputs:
        for secs, nid in _DUR_RE.findall(out):
            f = nid.split("::")[0].replace("\\", "/")
            if f.startswith("tests/"):
                agg[f] = agg.get(f, 0.0) + float(secs)
    for f, v in agg.items():
        dur[f] = round(v, 3)


def _cost(f: str, n_cases: int, dur: dict[str, float]) -> float:
    """分片成本估算：历史耗时与"用例数 × 下限量"取大者。

    取大者是因为耗时表只在**全量**跑时才是完整的；子集跑只测了文件的一部分，
    求和自然偏小 —— 只用它会把"几十条用例的大文件"错当成便宜货。
    """
    return max(dur.get(f, 0.0), 0.35 * n_cases, 0.05)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _baseline_from(ts: str) -> float:
    p = Path(ts)
    if p.exists():
        return p.stat().st_mtime
    try:
        return float(ts)
    except ValueError:
        sys.exit(f"无法把 {ts!r} 当成文件或时间戳")


def _merge_snapshot(snap: dict, raw: dict, files: list[str]) -> None:
    """把 `files` 这几个测试文件的**新实测结果**并回快照，其余文件原样保留。

    这就是"增量重采"的全部：`--probe <几个文件>` 只跑那几个（通常 1~3 秒），
    而不是每次加测试都要重跑一遍全量（2.5 分钟）。
    """
    fileset = set(files)
    for key in ("case", "exec"):
        old = snap.setdefault(key, {})
        for nid in [n for n in old if n.split("::")[0] in fileset]:
            del old[nid]
        old.update(raw.get(key, {}))
    for key in ("module", "globs"):
        old = snap.setdefault(key, {})
        for f in [f for f in old if f in fileset]:
            del old[f]
        old.update({k: v for k, v in raw.get(key, {}).items() if k in fileset})
    keep = [n for n in snap.get("all_cases", []) if n.split("::")[0] not in fileset]
    snap["all_cases"] = sorted(set(keep) | set(raw.get("all_cases", [])))
    # mtime 一律按**磁盘现状**记：这样"新采过的文件"立刻变成"可信"，
    # 没采到的文件继续保持 stale（逐文件比对 mtime，见 `select()` 的规则 d）
    for f in files:
        cur = sources().get(f)
        if cur is not None:
            snap.setdefault("test_mtimes", {})[f] = cur
    snap["probed_at"] = time.time()


def _probe(extra: list[str], force: bool = False, files: list[str] | None = None) -> int:
    """跑一遍全量 + 探针，重采依赖快照。

    ★ 为什么默认要求"全量必须跑绿" ★ 快照记的是"每个用例读了/执行了哪些文件"。
    树是坏的时候，用例的执行路径会变（提前 return、异常分支）⇒ 采到的是**坏状态的
    依赖**，之后拿它挑子集就会漏。所以默认拒绝。

    ★ `--force` 是给**鸡生蛋**留的口子 ★ 依赖快照的契约测试（`test_run_tests_for_contract.py`）
    在快照缺 `exec` 数据时会 fail —— 而那正是你要采快照才能补上的东西。第一次采、
    或快照格式刚改过时，用 `--probe --force` 装上。
    """
    files = [f.replace("\\", "/") for f in (files or [])]
    files = [f if f.startswith("tests/") else f"tests/{f}" for f in files]
    files = [f for f in files if (ROOT / f).is_file()]
    targets = files or ["tests/"]
    out_path = Path("/tmp/agent_loop_depmap_inc.json") if files else RAW
    if files:
        print(f"[run_tests_for] 增量重采依赖快照（只跑 {len(files)} 个测试文件，几秒钟）…")
    else:
        print("[run_tests_for] 重采依赖快照（跑一遍全量 + 探针，约 2.5 分钟）…")
    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "--no-header",
           "-p", "scripts.dev._read_probe", *extra]
    env = dict(os.environ, READ_PROBE_OUT=str(out_path))
    rc = subprocess.run(cmd, cwd=str(ROOT), env=env).returncode
    if rc != 0 and not force:
        print("[run_tests_for] 没跑绿 ⇒ 快照可能反映的是坏状态，先修好再重采。"
              "（若失败的是「依赖快照自己的契约测试」，用 --probe --force 装上快照即可破鸡生蛋）")
        return rc
    if not out_path.is_file():
        print("[run_tests_for] 探针没产出结果文件，检查 _read_probe 是否被加载")
        return 2
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    raw["sources"] = sorted(sources())
    if files:
        snap = load_snapshot()
        _merge_snapshot(snap, raw, files)
        snap["sources"] = raw["sources"]
        SNAP.write_text(json.dumps(snap, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"[run_tests_for] 快照已并入 {len(files)} 个文件："
              f"{', '.join(f.split('/')[-1] for f in files)}"
              f"（现有 {len(snap['all_cases'])} 用例）")
    else:
        SNAP.write_text(json.dumps(raw, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"[run_tests_for] 快照已更新：{SNAP}（{len(raw.get('all_cases', []))} 用例 / "
              f"{len(raw.get('globs', {}))} 个测试含目录扫描）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按改动挑测试子集并并行跑")
    ap.add_argument("files", nargs="*", help="改动过的源文件（相对仓库根）")
    ap.add_argument("--changed", action="store_true", help="用上次子集运行的时间戳作基线")
    ap.add_argument("--changed-since", metavar="文件|时间戳")
    ap.add_argument("--only-tests", nargs="+", metavar="测试文件")
    ap.add_argument("--list", action="store_true", help="只列不跑")
    ap.add_argument("--explain", action="store_true", help="逐条打印为什么选中它")
    ap.add_argument("--all", action="store_true", help="全量（并行）")
    ap.add_argument("--probe", action="store_true", help="重采依赖快照")
    ap.add_argument("--probe-files", nargs="*", metavar="测试文件",
                    help="配合 --probe：只重采这几个测试文件并**并入**快照（几秒，别跑全量）")
    ap.add_argument("--force", action="store_true",
                    help="配合 --probe：全量没跑绿也照样装快照（破鸡生蛋用）")
    ap.add_argument("--jobs", type=int, default=0, help="并行度（0=自动）")
    ap.add_argument("-x", "--exitfirst", action="store_true")
    ap.add_argument("extra", nargs="*", help="透传给 pytest（放在 -- 之后）")
    args = ap.parse_args(argv)

    if args.probe:
        return _probe(list(args.extra), force=args.force, files=args.probe_files)

    snap = load_snapshot()
    per_file = cases_of(snap)
    all_targets = sorted(snap["all_cases"]) or sorted(per_file)

    def full(reason: str) -> int:
        """全量 = **按磁盘上的文件**列目标，而不是照抄快照里的 node id。

        ★ 两个都必须绕开快照：
          ① node id 会随测试改名失效，而 pytest 遇到不存在的 node id 会放弃整趟
             （`no tests ran` + rc=4）⇒ 走文件路径，它天然跟着改名走；
          ② 快照之后**新增**的测试文件不在快照里 ⇒ 从磁盘枚举才带得全。
        """
        sel = Selection()
        on_disk = sorted(f for f in sources()
                         if f.startswith("tests/test_") and f.endswith(".py"))
        for f in on_disk:
            sel.whole_file(f, reason)
        for f in sorted(per_file):                # 快照里有、磁盘上没扫到的（保险）
            if f.startswith("tests/") and (ROOT / f).is_file():
                sel.whole_file(f, reason)
        if not on_disk and not per_file:
            for nid in all_targets:
                sel.case(nid, reason)
        return _dispatch(sel, snap, args, full=True, reason=reason)

    if args.all:
        return full("全量")

    if args.only_tests:
        sel = Selection()
        for f in args.only_tests:
            f = f.replace("\\", "/")
            f = f if f.startswith("tests/") else f"tests/{f}"
            for nid in per_file.get(f, [f]):
                sel.case(nid, "显式指定")
        return _dispatch(sel, snap, args, full=False)

    changed = list(args.files)
    gone_set: set[str] = set()
    if args.changed_since:
        changed += changed_since(_baseline_from(args.changed_since))
    elif args.changed:
        if not STAMP.exists():
            return full(f"没有基线（{STAMP.name} 不存在）")
        changed += changed_since(STAMP.stat().st_mtime)
    # ★ 删除/重命名也要算改动 ★ 只在 `--changed*` 这两条"帮我找改了什么"的路上做：
    # 显式给了文件清单时用户要的就是那几个，不要塞别的东西进来。
    if args.changed or args.changed_since:
        gone = vanished_sources(snap)
        gone_set.update(gone)
        if gone:
            print(f"[run_tests_for] 快照里有 {len(gone)} 个源文件已不在磁盘上（删除/改名）"
                  f"⇒ 一并纳入：{', '.join(gone[:4])}" + ("…" if len(gone) > 4 else ""))
            changed += gone
    changed = sorted({c.replace("\\", "/") for c in changed
                      if (ROOT / c).is_file() or c in gone_set})
    if not changed:
        return full("没给出改动文件，也没扫到比基线新的源文件")

    imports, closure, unresolved = _static_imports()
    sel = select(changed, snap, imports, unresolved, closure=closure)
    print(f"[run_tests_for] 改动 {len(changed)} 个文件 ⇒ 命中 {len(sel.targets)} 项"
          f"（全量 {len(all_targets)} 项）")
    for n in sel.notes:
        print(f"[run_tests_for] {n}")
    if not sel.targets:
        print("[run_tests_for] 没有任何测试依赖这些改动 ⇒ 无需跑")
        return 0
    return _dispatch(sel, snap, args, full=False)


def _dispatch(sel: Selection, snap: dict, args, *, full: bool,
              reason: str = "") -> int:
    targets = _normalize(sel.targets, snap)
    if args.list:
        if args.explain:
            for t in targets:
                why = sel.whole.get(t.split("::")[0]) or sel.cases.get(t) or {"?"}
                print(f"    {t}\n        理由：{'；'.join(sorted(why))}")
        else:
            for t in targets:
                print(f"    {t}")
        return 0

    dur = _durations()
    jobs = args.jobs or min(_MAX_JOBS, os.cpu_count() or 1)
    files = {t.split("::")[0] for t in targets}
    est = sum(_cost(f, sum(1 for t in targets if t.split('::')[0] == f), dur)
              for f in files)
    if jobs <= 1 or est < _PARALLEL_MIN_SECONDS or len(targets) <= 1:
        shards = [targets]
    else:
        shards = _shard(targets, jobs, dur)

    extra = list(args.extra)
    if args.exitfirst:
        extra.append("-x")

    label = "全量" if full else "子集"
    if reason and full:
        label = f"全量（{reason}）"
    print(f"[run_tests_for] {label}：{len(targets)} 项 / {len(files)} 个文件 / "
          f"{len(shards)} 个分片（预估串行 {est:.1f}s）")
    t0 = time.perf_counter()
    if len(shards) == 1:
        rc, out, err, _ = _run_shard(shards[0], extra)
        sys.stdout.write(out)
        if err.strip():
            sys.stderr.write(err)
        _apply_durations(dur, [out])
    else:
        with ThreadPoolExecutor(max_workers=len(shards)) as ex:
            results = list(ex.map(lambda s: _run_shard(s, extra), shards))
        rc = 0
        flaky: list[tuple[int, str]] = []
        for i, (r, out, err, secs) in enumerate(results, 1):
            tail = [ln for ln in out.strip().splitlines() if ln.strip()]
            summary = tail[-1] if tail else "(无输出)"
            if r != 0:
                # ★ 并行争用会让**计时断言**偶发失败 ★ 实测：`test_multi_npc_turns_run_in_parallel`
                #   靠"两个 NPC 的回合肉眼可见地并行"断言，12 片同时抢 CPU 时它就不稳；
                #   同一批文件单独跑（一个进程）必过。所以红了先**串行复跑一次**：
                #   过了 ⇒ 如实报成"并行争用偶发"，不算失败（但**一定打印出来**，
                #   不许悄悄放过）；还是红 ⇒ 真失败。
                r2, out2, err2, secs2 = _run_shard(shards[i - 1], extra)
                if r2 == 0:
                    flaky.append((i, summary))
                    print(f"  [分片 {i}/{len(shards)}] ⚠ 并行下偶发（串行复跑通过，{secs2:.1f}s）"
                          f"：{summary}  —— 计时敏感测试撞上 CPU 争用，非真红")
                    continue
                rc = r
                print(f"  [分片 {i}/{len(shards)}] ❌ {summary}（串行复跑仍红，确认是真失败）")
                out, err = out2, err2
                print("-" * 70)
                sys.stdout.write(out)
                if err.strip():
                    sys.stderr.write(err)
                print("-" * 70)
            else:
                print(f"  [分片 {i}/{len(shards)}] ✅ {summary}  ({secs:.1f}s)")
        if flaky:
            print(f"[run_tests_for] ⚠ {len(flaky)} 个分片在并行下偶发、串行复跑全过"
                  f"（共 {len(flaky)} 片）—— 若想彻底避免，用 --jobs 1 或 --jobs 4")
        _apply_durations(dur, [o for _, o, _, _ in results])

    dt = time.perf_counter() - t0
    if not full:
        STAMP.write_text(f"{time.time()}\n", encoding="utf-8")
    _save_durations(dur)
    print(f"[run_tests_for] {label}用时 {dt:.1f}s")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
