#!/usr/bin/env python3
"""依赖探针：跑一遍全量，**实测**每个用例真正依赖哪些源文件（不猜，只量）。

用法（由 `run_tests_for.py --probe` 自动调用，一般不用手跑）：

    python3 -m pytest tests/ -p scripts.dev._read_probe -q
    # → /tmp/agent_loop_depmap_raw.json

产物结构：
    {"cases": {"tests/x.py::test_a": ["tools/a.py", "csharp/B.cs"], ...},
     "file_level": {"tests/x.py": [...]},          # 该文件全部用例共有的依赖
     "probed_at": 1792..., "test_mtimes": {"tests/x.py": 1792...}}

**为什么不能用静态 AST 扫**（本仓 10-17 实测）
------------------------------------------------------------------------------------
原 `deps_of()` 按 import + 路径字面量猜依赖。拿本探针当裁判对拍，它：
  · 把 `ToolExecutor.cs` 的扇入算成 252 用例，**实测只有 87** —— 大量文件"提过这个文件名"
    但没有任何用例真的读它（只是常量/注释里出现过）；
  · 把 `agent_loop.py` 算成 253（触发全量闸门），**实测 1** —— 全是 `import agent_loop.xxx`
    推导出来的，那些用例真正读的是子模块；
  · **漏掉 172 个源文件 / 346 处真依赖** —— 最狠的是 `test_log_setup.py`（它 rglob 全仓
    `*.py`，静态扫不出来，漏 58 处）和 `test_run_tests_for_contract.py`（它读 `tests/*.py`
    建映射，漏 101 处）。漏跑 = "绿了但其实没测"，是这个体系里最贵的错。
静态分析**永远无法自证不漏**；跑一遍量出来则可以。

**两条归属规则**（都基于"为什么会有这次读"）
------------------------------------------------------------------------------------
① **模块导入（`SourceFileLoader.get_data`）→ 归到整个文件**
   CPython 的 import 有缓存：某个模块只被真正执行一次，落在哪个用例上取决于执行顺序。
   若按用例归属，改 `agent/initiative.py` 只会跑"恰好第一个 import 它的那个用例"。
   而且模块导入失败会让整个文件 collection error ⇒ 本来就该整文件跑。

② **显式读文件（`Path.read_text` / `open`）→ 归到具体用例**
   这些读的位置是写死的（`_read("tools/ToolExecutor.cs")`），精确到用例是安全的。
   发生在模块级（collect 阶段，`_tls.node` 为空）的读 ⇒ 归到该文件全部用例：
   它在 import 时就执行了，影响的是整个文件。

**量的是"读"，不是"写"**：契约测试的失效形态几乎都是"被断言的源文件内容变了"。
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("READ_PROBE_OUT", "/tmp/agent_loop_depmap_raw.json"))

# 只记"仓库内的源文件"；测试自己的临时件、site-packages、构建产物全滤掉
_SUFFIXES = (".cs", ".csproj", ".py", ".md", ".json", ".txt", ".prefab", ".ab", ".yaml", ".yml")
_SKIP_PARTS = {"obj", "bin", "__pycache__", ".pytest_cache", "node_modules", ".git",
               ".mypy_cache", ".ruff_cache"}

_tls = threading.local()          # .node = 当前用例 nodeid | None
_state = {
    "case": {},                   # nodeid → set(源文件)
    "module": {},                 # 测试文件 → set(源文件)   import/模块级读
    "globs": {},                  # 测试文件 → set((根目录, 模式))   目录扫描
    "exec": {},                   # nodeid → set(仓库内 .py)   该用例**真的执行过**它的代码
    "collecting": None,           # 正在 collect 的测试文件
}

# ★ 为什么还要"执行"这一个信号（10-17 端到端验证逼出来的）★
#   "读过这个文件"（read_text）与"import 过这个模块"（静态闭包）都不足以回答
#   "改这个文件会不会弄红这个用例"：
#     · 契约测试读 C# 源码 —— read_text 抓得到；
#     · 但 `tools/text_render.py` 这种**被调用**的模块，用例既不读它、也不一定直接
#       import 它（是 `bridge.py` 转手 import 的）。实测：变异 `text_render.py` 后
#       全量红 48 条，只靠"读 + 静态 import 闭包"会**漏 8 条**；而把闭包放宽到传递闭包
#       又太粗（`text_render.py` 扇入从 190 涨到 929，占全量 64%）。
#   `sys.monitoring` 的 `PY_START` 事件给出的是**真答案**：这个用例执行过哪些
#   仓库内 .py 的代码。实测开销 ≈ 0（10.40s → 10.39s，同一批测试）。
_MON_TOOL = 4


def _rel(path) -> str | None:
    try:
        p = Path(path).resolve()
        rel = p.relative_to(ROOT)
    except (OSError, ValueError, TypeError):
        return None
    if any(part in _SKIP_PARTS for part in rel.parts):
        return None
    return str(rel).replace("\\", "/") if str(rel).endswith(_SUFFIXES) else None


def _owner() -> str | None:
    """当前这些读该记在谁头上 —— 用例体内记用例，模块级/导入期记整个文件。"""
    node = getattr(_tls, "node", None)
    if node:
        return node.split("::")[0]
    return _state["collecting"]


_EXEC_CACHE: dict[str, str | None] = {}
_ROOT_S = str(ROOT)


def _fast_rel(fn: str) -> str | None:
    """`co_filename` → 仓库相对路径，**纯字符串判断，绝不做 syscall**。

    ★ 这里踩过一次大坑 ★ 第一版直接复用了 `_rel()`（内部 `Path(fn).resolve()`）：
    那是**每函数调用一次磁盘 syscall**。本仓在 drvfs（Windows 盘）上，全量跑下来
    直接把进程拖死 —— 表现是跑到 14% 就再也不动，`timeout 900` 才杀掉（EXIT=124）。
    监控回调是整个进程最热的一段代码，只能做纯内存操作。
    """
    if fn in _EXEC_CACHE:
        return _EXEC_CACHE[fn]
    out: str | None = None
    if fn.startswith(_ROOT_S + os.sep) or fn.startswith(_ROOT_S + "/"):
        rel = fn[len(_ROOT_S) + 1:].replace("\\", "/")
        if rel.endswith(".py") and "__pycache__" not in rel:
            out = rel
    _EXEC_CACHE[fn] = out
    return out


def _record_exec(code) -> None:
    """`PY_START`：这个用例正在执行某个函数 —— 记下它属于哪个仓库内 .py。

    `try/except` 是必需的：解释器**关停阶段**还会继续触发事件，那时模块全局已被
    置成 `None`（实测报 `TypeError: 'NoneType' object is not callable`，
    整个 pytest 退出码被带坏）。无异常时 try 的开销可忽略。
    """
    try:
        node = _tls.node
        if node is None:
            return
        rel = _fast_rel(getattr(code, "co_filename", "") or "")
        if rel is not None:
            _state["exec"].setdefault(node, set()).add(rel)
    except Exception:
        pass


def _monitor_install() -> None:
    import sys
    try:
        sys.monitoring.use_tool_id(_MON_TOOL, "agent_loop_read_probe")
        sys.monitoring.register_callback(_MON_TOOL, sys.monitoring.events.PY_START,
                                         lambda code, off: _record_exec(code))
        sys.monitoring.set_events(_MON_TOOL, sys.monitoring.events.PY_START)
    except Exception:                             # 老 Python 没有 sys.monitoring ⇒ 只少一路信号
        pass


def _monitor_uninstall() -> None:
    import sys
    try:
        sys.monitoring.set_events(_MON_TOOL, 0)
        sys.monitoring.free_tool_id(_MON_TOOL)
    except Exception:
        pass


def _record_glob(base, pattern, recursive: bool) -> None:
    """记下"这个测试扫了这个目录的这个模式"。

    **为什么读文件还不够**：探针记的是"它读过哪些文件"。可契约测试大量用
    `CS.rglob("*.cs")` 扫全目录 —— 下次**新增**一个 `.cs`，它上次没读过 ⇒ 快照里
    没有这个文件名 ⇒ 改它时不会选中这个测试。**新增文件是静态/实测两条路共同的盲区**，
    所以这里把"扫描范围"也记下来：只要新增文件落在范围内，就仍然命中。
    """
    owner = _owner()
    if not owner:
        return
    try:
        b = Path(base).resolve().relative_to(ROOT)
    except (OSError, ValueError):
        return                                  # 扫仓库外（/tmp、site-packages）不记
    _state["globs"].setdefault(owner, set()).add(
        (str(b).replace("\\", "/"), str(pattern), bool(recursive)))


def _record(path, *, import_like: bool) -> None:
    rel = _rel(path)
    if rel is None:
        return
    if import_like:
        # ① 模块导入 ⇒ 整个测试文件（import 只执行一次，落点随机）
        key = _owner()
        if key:
            _state["module"].setdefault(key, set()).add(rel)
        return
    node = getattr(_tls, "node", None)
    if node:                       # ② 用例体内显式读 ⇒ 该用例
        _state["case"].setdefault(node, set()).add(rel)
    elif _state["collecting"]:     # ②' 模块级（import 时）显式读 ⇒ 整个文件
        _state["module"].setdefault(_state["collecting"], set()).add(rel)


# ---------------------------------------------------------------- 打桩
_orig = {}


def _install() -> None:
    if _orig:
        return
    _orig["read_text"] = Path.read_text
    _orig["read_bytes"] = Path.read_bytes
    _orig["open"] = open
    _orig["glob"] = Path.glob
    _orig["rglob"] = Path.rglob
    from importlib.machinery import SourceFileLoader
    _orig["get_data"] = SourceFileLoader.get_data

    def read_text(self, *a, **kw):
        _record(self, import_like=False)
        return _orig["read_text"](self, *a, **kw)

    def read_bytes(self, *a, **kw):
        _record(self, import_like=False)
        return _orig["read_bytes"](self, *a, **kw)

    def _open(file, *a, **kw):
        if isinstance(file, (str, bytes, os.PathLike)):
            mode = a[0] if a else kw.get("mode", "r")
            if isinstance(mode, str) and "r" in mode and "+" not in mode:
                _record(file, import_like=False)
        return _orig["open"](file, *a, **kw)

    def get_data(self, path):
        # ★ 3.13 里 `io.open_code` **不会**被 import 调用（实测 0 命中）；真正走的是
        #   `SourceFileLoader.get_data` —— 它拿到的是 `.pyc`（有缓存时），所以只取
        #   同目录的 `.py` 名，用于归属到源文件。
        if isinstance(path, str) and "__pycache__" not in path:
            _record(path, import_like=True)
        elif isinstance(path, str) and path.endswith(".pyc") and "__pycache__" in path:
            _record(_pyc_to_py(path), import_like=True)
        return _orig["get_data"](self, path)

    def glob(self, pattern, *a, **kw):
        _record_glob(self, pattern, recursive=False)
        return _orig["glob"](self, pattern, *a, **kw)

    def rglob(self, pattern, *a, **kw):
        _record_glob(self, pattern, recursive=True)
        return _orig["rglob"](self, pattern, *a, **kw)

    Path.read_text = read_text
    Path.read_bytes = read_bytes
    import builtins
    builtins.open = _open
    SourceFileLoader.get_data = get_data
    Path.glob = glob
    Path.rglob = rglob


def _pyc_to_py(pyc: str) -> str:
    """`.../__pycache__/x.cpython-313.pyc` → `.../x.py`。"""
    d, base = os.path.split(pyc)
    name = base.split(".")[0]
    return os.path.join(os.path.dirname(d), name + ".py")


def _uninstall() -> None:
    if not _orig:
        return
    Path.read_text = _orig["read_text"]
    Path.read_bytes = _orig["read_bytes"]
    Path.glob = _orig["glob"]
    Path.rglob = _orig["rglob"]
    import builtins
    builtins.open = _orig["open"]
    from importlib.machinery import SourceFileLoader
    SourceFileLoader.get_data = _orig["get_data"]


# ---------------------------------------------------------------- pytest 钩子
def pytest_configure(config):
    _install()
    _monitor_install()


def pytest_collection_finish(session):
    """收集结束：此时模块级读已全部发生，把 collecting 关掉。"""
    _state["collecting"] = None


def pytest_collectstart(collector):
    """Module collector 在**导入测试模块之前**触发 ⇒ 这期间发生的读归给它。"""
    p = getattr(collector, "path", None)
    if p is not None and str(p).replace("\\", "/").split("/")[-1].startswith("test_"):
        _state["collecting"] = str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/")


def pytest_collectreport(report):
    _state["collecting"] = None


def pytest_runtest_setup(item):
    _install()
    _state["case"].setdefault(item.nodeid, set())


def pytest_runtest_call(item):
    _tls.node = item.nodeid


def pytest_runtest_teardown(item):
    _tls.node = None


def pytest_sessionfinish(session, exitstatus):
    _tls.node = None
    _state["collecting"] = None
    _monitor_uninstall()
    _uninstall()

    files = set(_state["module"])
    files |= {n.split("::")[0] for n in _state["case"]}
    test_mtimes = {}
    for f in sorted(files):
        p = ROOT / f
        try:
            test_mtimes[f] = p.stat().st_mtime
        except OSError:
            pass

    data = {
        "probed_at": time.time(),
        "case": {k: sorted(v) for k, v in sorted(_state["case"].items())},
        "module": {k: sorted(v) for k, v in sorted(_state["module"].items())},
        "globs": {k: sorted(v) for k, v in sorted(_state["globs"].items())},
        "exec": {k: sorted(v) for k, v in sorted(_state["exec"].items())},
        "test_mtimes": test_mtimes,
        "all_cases": sorted(_state["case"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"\n[read_probe] {len(data['case'])} 用例 / {len(data['module'])} 文件依赖 / "
          f"{len(data['globs'])} 测试含目录扫描 / {len(data['exec'])} 用例有执行记录 → {OUT}",
          file=sys.stderr)
