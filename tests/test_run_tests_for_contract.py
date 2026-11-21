"""`scripts/dev/run_tests_for.py` 的契约（10-17 改版：依赖表从"静态猜"改成"实测"）。

**为什么值得有测试**：这个脚本的产物是"**你这次改动不需要跑别的测试**"这个结论 ——
它一旦静默变松，我们就会在"绿了"的错觉里把没跑过的测试当成跑过了。

旧版（09-17）钉的是"映射表扫得出来"，但那张表是**静态猜**的，10-17 拿实测探针对拍
发现它两头都错（误报 3 倍扇入 + 漏掉 172 个源文件 / 346 处真依赖）。所以现在钉的
不是"表长什么样"，而是**几条可靠性属性**：

  1. **不跑全量**：`ws_channel.py` 这种高扇入改动也必须留在子集里，不许升级全量
     （旧版的 `FANIN_UPGRADE` 闸门正是死在这里 —— 扇入最高的 6 个文件全被它打成全量）；
  2. **不漏跑**：快照说"某用例读了 S"，那改 S 就必须选中它（这是本脚本存在的意义）；
  3. **`--changed` 覆盖全仓**：旧版只 glob 了根 `*.py` + `csharp/` + `tools/` + `scripts/`
     + `docs/`，**`agent/ transport/ room/ session/ llm/ storage/ config/ prompts/` 全扫不到**
     ⇒ `touch agent/initiative.py` 后它报"没扫到改动 ⇒ 全量"；
  4. **新增文件也命中**：扫描范围（`globs`）要覆盖到"快照里没有的新文件"；
  5. **`--list` 绝不允许真跑**（子进程都不该起）；
  6. **分片不许把同一个文件切开**：13 个测试用写死的 `/tmp/agent_loop_test_*` 目录，
     同一文件拆到两个进程会互相 `rmtree`。

全部走纯函数 / `--list`，只有 `_static_imports()` 会读一遍测试源码（~1s，这里缓存住）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/dev/run_tests_for.py"

_MOD = None

# 本仓最常改、扇入最高的几个文件（旧版闸门正是在这些上面失效的）
_HOT = ["transport/ws_channel.py", "agent_loop.py", "transport/bridge.py",
        "csharp/tools/ToolExecutor.cs", "tools/text_render.py", "csharp/app/ModMain.cs"]


def _load():
    """只加载一次（`_static_imports()` 要读一遍测试源码，别每个用例都重算）。"""
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("_rtf_under_test", RUNNER)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


@pytest.fixture(scope="module")
def rtf():
    return _load()


@pytest.fixture(scope="module")
def snap(rtf):
    if not rtf.SNAP.is_file():
        pytest.fail(
            f"没有依赖快照 {rtf.SNAP} ⇒ run_tests_for.py 不可用。\n"
            f"采一次：python3 scripts/dev/run_tests_for.py --probe")
    return rtf.load_snapshot()


@pytest.fixture(scope="module")
def picked(rtf, snap):
    """(改动文件 → Selection) 的缓存，避免每个用例重算 import 闭包。"""
    imports, closure, unresolved = rtf._static_imports()
    cache: dict[tuple, object] = {}

    def get(changed):
        key = tuple(changed)
        if key not in cache:
            cache[key] = rtf.select(list(changed), snap, imports, unresolved,
                                    closure=closure)
        return cache[key]
    return get


# ---------------------------------------------------------------- 核心：不漏跑

def test_selection_covers_every_case_the_snapshot_says_reads_it(rtf, snap, picked):
    """★ 本脚本存在的意义 ★ 快照说"这些用例读了 S" ⇒ 改 S 必须把它们全选中。

    这是**唯一真正重要的不变量**：做不到就是"绿了但其实没测"。旧版静态映射在
    172 个源文件 / 346 处上失效（`test_log_setup.py` 漏 58、`test_run_tests_for_contract.py`
    漏 101），因为它是猜的；现在按实测表选，这条必须逐条成立。
    """
    per_file = rtf.cases_of(snap)
    # 源文件 → 读过它的用例（用例级 + 模块级归属到整文件）
    truth: dict[str, set[str]] = {}
    for nid, reads in snap["case"].items():
        for r in reads:
            truth.setdefault(r, set()).add(nid)
    for f, reads in snap["module"].items():
        for r in reads:
            truth.setdefault(r, set()).update(per_file.get(f, []))

    sampled = sorted(truth)
    assert len(sampled) > 200, f"快照里只有 {len(sampled)} 个有依赖的源文件，快照不完整？"
    misses = []
    for S in sampled:
        sel = picked([S])
        got = set(sel.targets)
        whole = set(sel.whole)
        for nid in truth[S]:
            if nid in got or nid.split("::")[0] in whole:
                continue
            misses.append((S, nid))
    assert not misses, (
        f"{len(misses)} 处漏跑（快照说读了，但没被选中）：\n" +
        "\n".join(f"  改 {s} 漏 {n}" for s, n in misses[:10]))


# ---------------------------------------------------------------- 不许升级全量

def test_hot_files_never_escalate_to_the_full_suite(rtf, snap, picked):
    """★ 旧版闸门就死在这条上 ★ 扇入最高的 6 个文件，改任何一个都必须留在子集里。

    旧版 `FANIN_UPGRADE=15`：这些文件扇入 16~35 ⇒ 全部触发"升级全量"，而这 6 个
    恰好就是本仓最常改的。**闸门已删除**，这条防止它被重新加回来。
    """
    total = len(snap["all_cases"])
    for f in _HOT:
        got = set(picked([f]).targets)
        assert got, f"{f} 一个测试都没选中 —— 映射断了"
        assert len(got) < total, (
            f"改 {f} 把整个套件都拖进来了（{len(got)}/{total}）—— "
            f"「不许升级全量」这条契约被破坏")


def test_ordinary_change_stays_a_small_subset(rtf, snap, picked):
    """一次普通改动应当只牵出一小撮用例，不许接近全量。"""
    total = len(snap["all_cases"])
    for f in ["csharp/contacts/UnreadStore.cs", "csharp/tools/ActionWatcher.cs",
              "tools/schemas.py"]:
        got = len(picked([f]).targets)
        assert 0 < got < total * 0.5, f"改 {f} 牵出 {got}/{total} 项，太宽了"


# ---------------------------------------------------------------- --changed 覆盖

def test_changed_since_covers_every_source_dir(rtf):
    """★ 旧版真 bug ★ `--changed` 必须扫得到**全仓**，尤其是扇入最高的那几个目录。

    旧版 glob 只有 `csharp/*.cs`、`*.py`（根）、`tools/`、`compaction/`、`scripts/`、
    `docs/` ⇒ `agent/ transport/ room/ session/ llm/ storage/ config/ prompts/` 一个都
    扫不到。实测：`touch agent/initiative.py` 之后 `--changed` 报"没扫到比改动新的
    源文件 ⇒ 全量" —— **想省时间，却静默跑了全量**。
    """
    found = set(rtf.changed_since(0.0))          # 0 = 所有文件都"新"
    for d in ("agent", "transport", "room", "session", "llm", "storage", "config", "prompts"):
        assert any(f.startswith(d + "/") for f in found), \
            f"`--changed` 扫不到 {d}/（旧 bug 复发）"
    for f in ("transport/ws_channel.py", "transport/bridge.py", "agent/initiative.py"):
        assert f in found, f"{f} 不在 `--changed` 的扫描范围内"


def test_changed_since_skips_build_artifacts(rtf):
    """`dotnet build` 会重写 `csharp/tests/**/obj/*.cs`、跑测试会写 `__pycache__`：
    不能把它们当成"改动"（否则每次都挑到一堆无关文件；第一版就踩了）。"""
    found = rtf.changed_since(0.0)
    bad = [f for f in found if "/obj/" in f or "/bin/" in f or "__pycache__" in f
           or f.startswith("dist/") or f.startswith("backup/")]
    assert not bad, f"构建产物被当成改动了：{bad[:5]}"
    assert any(f.startswith("csharp/") for f in found), "连 csharp 源文件都没扫到，测试自身失效"
    assert rtf.changed_since(time.time() + 3600) == [], "未来时间戳不该扫出任何文件"


def test_relative_imports_are_resolved(rtf, snap, picked):
    """★ 10-17 端到端验证抓到的真漏 ★ 相对 import（`from ..tools.x import y`）必须解析。

    初版对 `ImportFrom.level != 0` 直接跳过 ⇒ `transport/bridge.py` 里那句
    `from ..tools.text_render import render` 完全没被记录。后果实测：变异
    `tools/text_render.py` 后全量红 48 条，**子集漏掉 8 条**。

    判据取"直接 import 表"：`transport/bridge.py` 必须出现在 `tools/text_render.py`
    的依赖方里。
    """
    imports, _closure, _unres = rtf._static_imports()
    depender = {f for f, deps in imports.items() if "tools/text_render.py" in deps}
    assert "tests/test_bridge_tools.py" in depender or \
           "transport/bridge.py" in {d for deps in imports.values() for d in deps}, \
        "相对 import 没被解析 —— `from ..tools.text_render import render` 这类写法会被漏掉"


def test_executed_modules_are_honoured(rtf, snap, picked):
    """★ 端到端逼出来的主力信号 ★ 用例**真的执行过**某个模块的代码 ⇒ 改它必须选中该用例。

    静态 import 闭包在这里两头不讨好：只算直接 import 会漏（用例是通过 bridge 转手
    调用的），算传递闭包又太粗（`text_render.py` 扇入 190 → 929）。所以靠 `exec` 实测。
    """
    exec_map = snap.get("exec") or {}
    if not exec_map:
        pytest.fail("快照里没有 `exec`（实测执行）数据 ⇒ 采快照时 `sys.monitoring` 没生效。"
                    "重采：python3 scripts/dev/run_tests_for.py --probe")
    per_file = rtf.cases_of(snap)
    # 源文件 → 执行过它的用例
    truth: dict[str, set[str]] = {}
    for nid, mods in exec_map.items():
        for m in mods:
            truth.setdefault(m, set()).add(nid)
    misses = []
    for S in sorted(truth):
        if not S.endswith(".py"):
            continue
        sel = picked([S])
        got, whole = set(sel.targets), set(sel.whole)
        for nid in truth[S]:
            if nid not in got and nid.split("::")[0] not in whole:
                misses.append((S, nid))
    assert not misses, (
        f"{len(misses)} 处漏跑（用例执行过这个模块的代码，却没被选中）：\n" +
        "\n".join(f"  改 {s} 漏 {n}" for s, n in misses[:10]))


# ---------------------------------------------------------------- 新增文件

def test_brand_new_file_inside_a_scan_scope_is_still_hit(rtf, snap, picked):
    """★ 静态/实测两条路的共同盲区 ★ 快照里**没有**的新文件，落在某测试的扫描范围内
    也必须命中它 —— 否则"新加一个 .cs"就是所有契约测试一起静默失效。

    只挑**通配符**扫描（`*.cs`）：像 `test_mod_entry_contract.py` 扫的是
    `**/ModMain.cs` 这种**具体文件名**，新文件本来就不该命中它。
    """
    scanners = {f: specs for f, specs in snap["globs"].items()
                if any(b == "csharp" and "*" in p and "/" not in p
                       for b, p, _r in specs)}
    assert scanners, "快照里一个通配符扫 csharp/ 的测试都没有？globs 没采到"
    fake = "csharp/UI/__BrandNewFileForContractTest.cs"
    assert not (rtf.ROOT / fake).exists(), "别用真实存在的路径做这个用例"
    got = set(picked([fake]).targets)
    assert got, f"新增文件 {fake} 没有命中任何测试 —— 扫描范围没生效"
    for f in scanners:
        assert any(t.startswith(f) for t in got), \
            f"{f} 扫 csharp 下的 *.cs，却没被新增文件 {fake} 命中"


def test_new_file_outside_every_scan_scope_is_not_falsely_included(rtf, snap, picked):
    """反向：新文件落在**所有**扫描范围之外时，不许假装命中一堆测试。

    这条钉的是"宁可多跑"的边界 —— 多跑要有理由；没理由还多跑，说明 glob 匹配写宽了。
    `docs/` 下没有任何测试扫描，`.md` 也不在任何模式里。
    """
    fake = "docs/__BrandNewUnscanned.md"
    assert not (rtf.ROOT / fake).exists()
    sel = picked([fake])
    # 同样排除"强制纳入"的（见 `Selection.forced` 的说明）
    extra = {t for t in sel.targets if t.split("::")[0] in sel.forced}
    assert not (set(sel.targets) - extra), \
        f"{fake} 不该命中任何测试，却选中了 {sorted(set(sel.targets) - extra)[:3]}"


# ---------------------------------------------------------------- 兜底

def test_stale_test_file_means_whole_file(rtf, snap):
    """快照之后被改过的测试文件 ⇒ 整文件跑（它的依赖可能已经变了）。"""
    victim = sorted(rtf.cases_of(snap))[0]
    fake = dict(snap)
    fake["test_mtimes"] = dict(snap["test_mtimes"])
    fake["test_mtimes"][victim] = snap.get("probed_at", 0.0) + 10_000
    sel = rtf.select(["csharp/contacts/UnreadStore.cs"], fake, {}, set())
    got = set(sel.targets) | set(sel.whole)
    assert any(t.startswith(victim) for t in got), \
        f"{victim} 比快照新，却没被整文件纳入"


def test_unresolvable_dynamic_import_means_whole_file(rtf, snap):
    """含解析不出来的动态 import 的测试文件 ⇒ 整文件跑（静态猜不准，宁可多跑）。"""
    victim = sorted(rtf.cases_of(snap))[0]
    sel = rtf.select(["csharp/contacts/UnreadStore.cs"], snap, {}, {victim})
    got = set(sel.targets) | set(sel.whole)
    assert any(t.startswith(victim) for t in got), f"{victim} 没被整文件纳入"


def test_unread_source_file_is_reported_not_silently_skipped(rtf, snap):
    """改一个"没人读、也没人扫"的源文件 ⇒ 如实报告，不许静默假装跑过了。

    用 `.md`（没有任何测试扫描 `.md`）；注意**不能**用 `csharp/*.cs` —— 那种路径
    本来就该被扫描型测试命中，那是正确行为而不是误报。
    """
    fake = "docs/__NobodyReadsThis.md"
    sel = rtf.select([fake], snap, {}, set())
    extra = {t for t in sel.targets if t.split("::")[0] in sel.forced}
    assert not (set(sel.targets) - extra), "这个文件不该命中任何测试"
    assert any("没有任何测试读它" in n for n in sel.notes), \
        f"没有如实报告「没有测试覆盖」，notes={sel.notes}"


# ---------------------------------------------------------------- 分片

def test_sharding_never_splits_a_file(rtf, snap):
    """★ 13 个测试用写死的 `/tmp/agent_loop_test_*` 目录 ★ 同一个文件的用例必须落进
    同一个分片，否则两个进程会互相 `rmtree`。"""
    targets = sorted(snap["all_cases"])
    shards = rtf._shard(targets, 8, {})
    assert len(shards) > 1, "8 个分片应当分出多于 1 片"
    seen: dict[str, int] = {}
    for i, shard in enumerate(shards):
        for t in shard:
            f = t.split("::")[0]
            if f in seen:
                assert seen[f] == i, f"{f} 被拆到分片 {seen[f]} 和 {i} —— 会撞 /tmp 目录"
            seen[f] = i
    assert sorted(t for s in shards for t in s) == targets


def test_sharding_is_roughly_balanced(rtf, snap):
    """按历史耗时装箱，不该出现"一片独吞、其余空转"。"""
    targets = sorted(snap["all_cases"])
    dur = {f: 1.0 for f in {t.split("::")[0] for t in targets}}
    shards = rtf._shard(targets, 8, dur)
    sizes = [len(s) for s in shards]
    assert max(sizes) <= 2 * (sum(sizes) / len(sizes)), f"分片严重不均：{sizes}"


# ---------------------------------------------------------------- 安全绳

def test_list_mode_never_runs_pytest(rtf, monkeypatch, capsys):
    """`--list` 只列不跑（连子进程都不许起）—— 否则"看一眼"就是又一轮全量。"""
    called = []
    monkeypatch.setattr(rtf.subprocess, "run", lambda *a, **k: called.append(a))
    rc = rtf.main(["--list", "csharp/tools/ToolExecutor.cs"])
    out = capsys.readouterr().out
    assert rc == 0 and not called, f"--list 竟然真跑了：{called}"
    assert "tests/" in out, out


def test_list_mode_never_runs_pytest_on_the_full_path(rtf, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(rtf.subprocess, "run", lambda *a, **k: called.append(a))
    rc = rtf.main(["--list", "--all"])
    assert rc == 0 and not called, "--list --all 真跑了全量"
    assert "tests/" in capsys.readouterr().out


def test_snapshot_missing_gives_an_actionable_error(rtf, tmp_path):
    """没有快照时必须**说清楚怎么采**，不许静默退化或跑全量。"""
    with pytest.raises(SystemExit) as e:
        rtf.load_snapshot(tmp_path / "nope.json")
    assert "--probe" in str(e.value), "报错里没告诉用户怎么采快照"


# ---------------------------------------------------------------- 新增/删除测试

def test_brand_new_test_file_is_always_run(rtf, snap, monkeypatch):
    """★ "新增功能 + 新增测试"最容易踩的坑 ★ 快照里**没有**的测试文件必须一律跑。

    10-17 实测：新增 `tests/test_zz_new_feature.py` 后，`run_tests_for.py <源文件>` 与
    `--changed` **一条都不会选中它** —— 新功能配的新测试等于没跑，而且看起来是绿的。
    我们对它一无所知（不知道它读什么、执行什么），只能整文件跑。
    """
    fake = "tests/test_zz_brand_new.py"
    real = rtf.sources()
    monkeypatch.setattr(rtf, "sources",
                        lambda: {**real, fake: real.get("tests/test_stats.py", 0.0)})
    # 改一个跟它毫无关系的文件，它也必须被带上
    sel = rtf.select(["csharp/contacts/UnreadStore.cs"], snap, {}, set())
    assert any(t.split("::")[0] == fake for t in sel.targets), \
        f"新增测试文件没被纳入：{sorted(sel.targets)[:5]}"


def test_edited_test_file_falls_back_to_whole_file(rtf, snap, monkeypatch):
    """测试文件被改过（mtime ≠ 快照录的）⇒ 整文件跑，**不靠 `probed_at`**。

    ★ 判据必须是"当前 mtime ≠ 快照记录的 mtime" ★ 用"比 probed_at 新"在**增量重采**之后
    会失效：probed_at 被推到 now，那些刚改过、但这次没重采的文件就变成"更旧"⇒ 被当成可信
    ⇒ 拿旧依赖挑子集 ⇒ **漏跑**。
    """
    victim = sorted(rtf.cases_of(snap))[0]
    fake = dict(snap)
    fake["test_mtimes"] = dict(snap["test_mtimes"])
    fake["test_mtimes"][victim] = snap["test_mtimes"].get(victim, 0.0) - 999.0   # 与当前不同
    fake["probed_at"] = 9e9                       # 故意把 probed_at 设成未来
    sel = rtf.select(["csharp/contacts/UnreadStore.cs"], fake, {}, set())
    got = set(sel.targets) | set(sel.whole)
    assert any(t.split("::")[0] == victim for t in got), \
        f"{victim} 的 mtime 与快照不符，却没被整文件纳入（判据错误地依赖了 probed_at）"


def test_deleted_source_file_is_reported(rtf, snap):
    """删除/重命名的源文件没有 mtime，`--changed` 扫不到 ⇒ 必须靠快照反查。"""
    real = rtf.sources()
    fake = dict(snap)
    fake["sources"] = sorted(set(real) | {"tools/__deleted_for_contract_test.py"})
    gone = rtf.vanished_sources(fake)
    assert "tools/__deleted_for_contract_test.py" in gone, gone


def test_vanished_detection_ignores_runtime_fixtures(rtf, snap):
    """★ 不能把测试运行时造的临时 fixture 当成"源文件被删了" ★。

    实测第一版报了 30 个假警报（`.mine_0913/`、`hw1_jupyterhub/` 这些本来就不跟踪的目录），
    收紧后又剩 6 个（`prompts/personas/张三.txt` 这类测试自己造完就删的）。
    正确判据是"**采样时**它是本仓的源文件、现在没了" —— 靠快照里的 `sources` 清单。
    """
    assert "sources" in snap, "快照没记采样时的源文件清单 ⇒ --probe 重采一次"
    gone = rtf.vanished_sources(snap)
    assert not gone, f"不该有真的删除，却报出 {gone[:5]}"


def test_vanished_detection_needs_the_recorded_list(rtf, snap):
    """旧快照没记 `sources` 时，宁可**什么都不报**，也不能满屏假警报。"""
    fake = {k: v for k, v in snap.items() if k != "sources"}
    assert rtf.vanished_sources(fake) == []


def test_incremental_probe_merge_keeps_other_files(rtf):
    """`--probe <文件>` 的合并**只动那几个文件**，其余必须原样保留。

    这是"新测试也能回到精确选择"的关键：加完测试跑一次增量采（几秒），
    而不是每次重跑全量（2.5 分钟）。
    """
    base = {
        "probed_at": 1.0, "note": "keep-me",
        "case": {"tests/a.py::t1": ["x.py"], "tests/b.py::t1": ["y.py"]},
        "module": {"tests/a.py": ["x.py"], "tests/b.py": ["y.py"]},
        "globs": {"tests/a.py": [["csharp", "*.cs", True]]},
        "exec": {"tests/a.py::t1": ["x.py"], "tests/b.py::t1": ["y.py"]},
        "test_mtimes": {"tests/a.py": 1.0, "tests/b.py": 1.0},
        "all_cases": ["tests/a.py::t1", "tests/b.py::t1"],
        "sources": ["x.py", "y.py"],
    }
    raw = {
        "case": {"tests/a.py::t1": ["x.py"], "tests/a.py::t2": ["z.py"]},
        "module": {"tests/a.py": ["x.py"]},
        "globs": {}, "exec": {"tests/a.py::t2": ["z.py"]},
        "test_mtimes": {"tests/a.py": 5.0},
        "all_cases": ["tests/a.py::t1", "tests/a.py::t2"],
    }
    snap = json.loads(json.dumps(base))
    rtf._merge_snapshot(snap, raw, ["tests/a.py"])
    assert snap["note"] == "keep-me", "合并把无关字段弄丢了"
    assert snap["case"]["tests/b.py::t1"] == ["y.py"], "b.py 的依赖被误删"
    assert "tests/a.py::t2" in snap["case"], "新增用例没并进来"
    assert snap["all_cases"] == ["tests/a.py::t1", "tests/a.py::t2", "tests/b.py::t1"] \
        or sorted(snap["all_cases"]) == ["tests/a.py::t1", "tests/a.py::t2", "tests/b.py::t1"]
    assert snap["test_mtimes"]["tests/b.py"] == 1.0, "b.py 的 mtime 被误改"
