"""发行后端契约（09-19）—— 把「上线一律用 Nuitka」钉成回归护栏。

**为什么要有这个文件**：换打包器这件事**没有任何一行代码会因此报错**。
两种产物的文件名、位置、启动契约完全一样（`AgentLoopServer.exe`），
C# 侧一行都不用改 —— 这既是优点，也是风险：
改回 PyInstaller 不会红、删掉一句警告也不会红、拿自测产物发布更不会红。

后果是不对称的：PyInstaller 档 `pyinstxtractor` 一条命令就还原成 `.pyc`，
整个提示词工程与编排逻辑直接送人。所以逐条钉在这里。

判据来源：`docs/release/PACKAGING.md` §6（含 §6.1 误报机制、§6.2 本机实测）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
PACKAGING = DOCS / "release" / "PACKAGING.md"
RELEASE_STATUS = DOCS / "release" / "RELEASE_STATUS.md"
NUITKA_BUILDER = ROOT / "scripts" / "build_exe_nuitka.py"

if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


def _pack_release():
    """导入 scripts/pack_release.py（它不在包内，按路径加载）。"""
    import importlib.util

    path = ROOT / "scripts" / "pack_release.py"
    spec = importlib.util.spec_from_file_location("_pack_release_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# ① 策略本身：发行档是 Nuitka，且工具认得出来
# --------------------------------------------------------------------------

def test_release_backend_is_nuitka():
    """代码里的发行档判据必须就是 nuitka —— 这是整套策略的单点开关。"""
    assert _pack_release().RELEASE_BACKEND == "nuitka"


def test_nuitka_output_path_is_distinct_from_pyinstaller():
    """两个后端的产物落点必须**不同**，否则「发的是哪个」就无从判断。"""
    pr = _pack_release()
    assert pr.EXE_NUITKA_OUT != pr.EXE_OUT
    assert pr.EXE_NUITKA_OUT.name == pr.EXE_NAME
    assert pr.EXE_OUT.name == pr.EXE_NAME  # 同名（C# 靠名字判据），但不同目录


def test_backend_fingerprints_are_distinct():
    """两个指纹不能互相包含，否则认门牌号会认错。"""
    pr = _pack_release()
    assert pr._FP_NUITKA not in pr._FP_PYINSTALLER
    assert pr._FP_PYINSTALLER not in pr._FP_NUITKA


def test_exe_backend_detects_both_real_artifacts():
    """用真实产物验指纹 —— 没有产物就跳过（干净 clone / CI 上打不出 18 MB 的 exe）。"""
    pr = _pack_release()
    checked = 0
    if pr.EXE_NUITKA_OUT.is_file():
        assert pr.exe_backend(pr.EXE_NUITKA_OUT) == "nuitka", \
            f"Nuitka 产物认成了 {pr.exe_backend(pr.EXE_NUITKA_OUT)}"
        checked += 1
    if pr.EXE_OUT.is_file():
        assert pr.exe_backend(pr.EXE_OUT) == "pyinstaller", \
            f"PyInstaller 产物认成了 {pr.exe_backend(pr.EXE_OUT)}"
        checked += 1
    if not checked:
        pytest.skip("本机没有构建产物，指纹已由 test_backend_fingerprints_are_distinct 覆盖")


def test_exe_backend_is_safe_on_non_exe_and_missing():
    """认不出来不能抛异常 —— 它要在铺包流程里跑，炸了就是打不出包。"""
    pr = _pack_release()
    assert pr.exe_backend(ROOT / "README.md") == "unknown"
    assert pr.exe_backend(ROOT / "does_not_exist_at_all.exe") == "unknown"


def test_report_warns_loudly_on_pyinstaller(capsys):
    """铺 PyInstaller 产物时必须**出声**。静默通过 = 这个护栏等于没有。"""
    pr = _pack_release()
    pr.report_exe_backend(pr.EXE_OUT)   # 文件不存在 → unknown 分支
    out = capsys.readouterr().out
    assert out.strip(), "report_exe_backend 什么都没打印"

    # 真产物在时，验证 pyinstaller 分支确实报警
    if pr.EXE_OUT.is_file():
        pr.report_exe_backend(pr.EXE_OUT)
        out = capsys.readouterr().out
        assert "PyInstaller" in out and "pyinstxtractor" in out, \
            "铺 PyInstaller 产物却没说清后果"


# --------------------------------------------------------------------------
# ② 文档：策略必须写在文档里，且旧的「先不切」不能残留
# --------------------------------------------------------------------------

def test_packaging_doc_declares_nuitka_release():
    doc = _text(PACKAGING)
    assert "Nuitka" in doc
    # 发行规则要在显眼处（标题/引言块）
    head = doc[:2000]
    assert "发行" in head and "Nuitka" in head, "文档开头没有点明发行档"


def test_packaging_doc_no_longer_says_defer_switch():
    """2026-09-14 的「先不切」决策块必须已被替换 —— 留着会误导下一个读文档的人。"""
    doc = _text(PACKAGING)
    assert "当前决策（2026-09-14）：先不切" not in doc
    # 「先不切」这个词只应出现在「为什么以前写的是先不切」的历史小节里
    if "先不切" in doc:
        idx = doc.index("先不切")
        around = doc[max(0, idx - 200):idx + 200]
        assert "为什么以前" in around or "历史" in around or "过期" in around, \
            "「先不切」还在正文里当结论用，应已降级为历史说明"


def test_packaging_doc_explains_av_mechanism_with_evidence():
    """误报一节必须给出机制与出处，不能只写「误报率高」。"""
    doc = _text(PACKAGING)
    assert "dropper" in doc, "没解释触发误报的行为机制"
    assert "temporary directory" in doc or "TEMP" in doc, "没点明「释放到临时目录」这条判据"
    assert "nuitka.net" in doc, "没引用 Nuitka 官方对误报的表述"
    assert "3842" in doc, "没引用微软给出判据的那个 issue"


def test_av_number_is_marked_as_unverified_research():
    """19/71 那个调研数字必须**明确标注未经本机实测**，否则会被当事实引用。"""
    doc = _text(PACKAGING)
    assert "19/71" in doc
    idx = doc.index("19/71")
    around = doc[max(0, idx - 300):idx + 300]
    assert "调研" in around or "未实测" in around or "复现" in around, \
        "19/71 没有标注它只是调研数字"


def test_release_status_records_nuitka_decision():
    doc = _text(RELEASE_STATUS)
    assert "上线一律 Nuitka" in doc or "发行档用哪个打包器" in doc
    assert "当前决策（2026-09-14）：先不切" not in doc


# --------------------------------------------------------------------------
# ③ 入口脚本与自测档的定位说明
# --------------------------------------------------------------------------

def test_nuitka_builder_declares_itself_release_path():
    src = _text(NUITKA_BUILDER)
    assert "发行路径" in src, "build_exe_nuitka.py 没说自己是发行路径"


def test_pyinstaller_builder_warns_it_is_not_release():
    """`--build-exe` 那条路太快太顺手，必须在 help 与 docstring 里都说清它不发行。"""
    src = _text(ROOT / "scripts" / "pack_release.py")
    assert "不是发行档" in src, "PyInstaller 路径没有「不是发行档」的标明"
    assert "pyinstxtractor" in src, "没说清 PyInstaller 档的实际后果"


# --------------------------------------------------------------------------
# ④ 产物内容：prompts 进包、字节码不进包（有产物才验）
# --------------------------------------------------------------------------

def test_nuitka_payload_has_no_bytecode():
    """换 Nuitka 的**全部意义**就是这一条：载荷里不能有可反编译的字节码。"""
    dist = _pack_release().EXE_NUITKA_OUT.parent / "server.dist"
    if not dist.is_dir():
        pytest.skip("没有 server.dist（未构建或用了 --remove-output）")
    leaked = [p for p in dist.rglob("*")
              if p.suffix in (".py", ".pyc", ".pyo")]
    assert not leaked, f"Nuitka 载荷里出现了字节码/源码：{[str(p) for p in leaked[:5]]}"


def test_nuitka_payload_prompts_match_repo():
    """exe 内置的那份 prompts 必须与仓库逐字节相同。

    这是**最容易静默出错的环节**：改了提示词、忘了重打包，玩家拿到的还是旧的，
    而仓库里怎么看都是对的。
    """
    dist = _pack_release().EXE_NUITKA_OUT.parent / "server.dist"
    bundled = dist / "agent_loop" / "prompts"
    if not bundled.is_dir():
        pytest.skip("没有 server.dist/agent_loop/prompts")
    src = ROOT / "prompts"
    # prompts/ 是"代码+资产"混合域（2026-09-21）：prompt_files.py / __init__.py 走
    # Nuitka --include-package 编译跟进，不在 DATA_DIRS 资产通道里，只比对资产文件。
    for f in sorted(p for p in src.rglob("*")
                    if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts):
        rel = f.relative_to(src)
        target = bundled / rel
        assert target.is_file(), f"产物里缺提示词：{rel}"
        assert target.read_bytes() == f.read_bytes(), f"提示词与仓库不一致：{rel}"


# --------------------------------------------------------------------------
# ⑤ 部署脚本：默认走发行档，进程闸不能被「Nuitka 已修好 zlib 那条」骗着删掉
# --------------------------------------------------------------------------

def _deploy_runtime():
    import importlib.util

    path = ROOT / "scripts" / "dev" / "deploy_runtime.py"
    spec = importlib.util.spec_from_file_location("_deploy_runtime_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_deploy_defaults_to_nuitka_artifact():
    """默认部署源必须是 Nuitka 发行档 —— 否则工具会默默把自测档铺上线。"""
    dr = _deploy_runtime()
    assert dr.ITEMS["exe"][0] == dr.EXE_NUITKA
    assert dr.EXE_NUITKA != dr.EXE_PYINSTALLER


def test_deploy_explicit_override_wins():
    dr = _deploy_runtime()
    # 显式指定的路径**原样采用**（不去搜默认产物）。比对走 Path 语义而不是字符串：
    # POSIX 上 str(Path("/a/b")) == "/a/b"，Windows 上同一对象是 "\a\b" —— 分隔符
    # 不是本条契约的内容，在 Windows 上按字符串比会假失败（09-21 实测）。
    from pathlib import Path as _P
    assert dr.resolve_exe_src("/somewhere/custom.exe") == _P("/somewhere/custom.exe")


def test_deploy_fingerprints_agree_with_pack_release():
    """两个脚本各存了一份指纹 —— 不一致就会出现「一个说发行档、一个说自测档」。"""
    pr, dr = _pack_release(), _deploy_runtime()
    assert pr._FP_NUITKA == dr._FP_NUITKA
    assert pr._FP_PYINSTALLER == dr._FP_PYINSTALLER
    if pr.EXE_NUITKA_OUT.is_file():
        assert dr.exe_backend(pr.EXE_NUITKA_OUT) == pr.exe_backend(pr.EXE_NUITKA_OUT) == "nuitka"
    if pr.EXE_OUT.is_file():
        assert dr.exe_backend(pr.EXE_OUT) == pr.exe_backend(pr.EXE_OUT) == "pyinstaller"


def test_deploy_gate_blocks_write_while_process_running(tmp_path, monkeypatch, capsys):
    """核心护栏：进程在跑时必须**拒绝且不改文件**。

    这条防的是 09-17 事故的同款操作，也防「Nuitka 已经修好了 zlib 那条、
    于是把闸删掉」这种过度推断 —— 闸现在防的是 C#/Python 协议成对 + 产物与实例不一致。
    """
    dr = _deploy_runtime()
    src = tmp_path / "src.exe"
    src.write_bytes(b"NEW-BUILD")
    dst = tmp_path / "game" / "AgentLoopServer.exe"
    dst.parent.mkdir()
    dst.write_bytes(b"OLD-RUNNING")

    monkeypatch.setitem(dr.ITEMS, "exe", (src, dst, "AgentLoopServer"))
    monkeypatch.setattr(dr, "running", lambda names: {"AgentLoopServer": ["4242"]})

    assert dr.deploy("exe", "t", force=False, dry=False) is False
    out = capsys.readouterr().out
    assert "拒绝部署" in out, "进程在跑却没有明说拒绝"
    assert dst.read_bytes() == b"OLD-RUNNING", "被闸住的部署居然改写了目标文件！"


def test_deploy_gate_releases_when_no_process(monkeypatch, tmp_path):
    """没有进程在跑时闸必须放行（否则这工具就没法用了）。"""
    dr = _deploy_runtime()
    src = tmp_path / "src.exe"
    src.write_bytes(b"NEW-BUILD")
    dst = tmp_path / "game" / "AgentLoopServer.exe"
    dst.parent.mkdir()
    dst.write_bytes(b"OLD")

    monkeypatch.setitem(dr.ITEMS, "exe", (src, dst, "AgentLoopServer"))
    monkeypatch.setattr(dr, "running", lambda names: {})

    assert dr.deploy("exe", "t", force=False, dry=False) is True
    assert dst.read_bytes() == b"NEW-BUILD", "放行了却没真的换上去"


def test_deploy_doc_note_says_gate_is_retained():
    """脚本头必须写明「zlib 那条没了，但闸保留」+ 两条新理由。"""
    src = _text(ROOT / "scripts" / "dev" / "deploy_runtime.py")
    assert "硬闸继续保留" in src, "没说清闸为什么还在"
    assert "成对" in src, "没给「协议成对」这条理由"
    assert "Nuitka" in src and "zlib" in src


def test_pack_release_defaults_to_nuitka_artifact():
    """`--exe` 不写时也要走发行档 —— 否则最省事的那条命令恰好铺的是自测档。"""
    pr = _pack_release()
    got = pr.resolve_exe("")
    if pr.EXE_NUITKA_OUT.is_file():
        assert got == pr.EXE_NUITKA_OUT, f"默认没走发行档，走了 {got}"
    else:
        assert got in (pr.EXE_NUITKA_OUT, pr.EXE_OUT)   # 发行档没建时才允许退


def test_pack_release_explicit_exe_wins():
    pr = _pack_release()
    assert pr.resolve_exe(r"F:\custom\my.exe").name == "my.exe"


def test_pack_release_warns_when_falling_back_to_pyinstaller(monkeypatch, tmp_path, capsys):
    """真的退到自测档时**必须出声**（文件名一样，铺完从包里看不出来）。"""
    pr = _pack_release()
    fake_nuitka = tmp_path / "nuitka" / "AgentLoopServer.exe"      # 不存在
    fake_pyi = tmp_path / "dist" / "AgentLoopServer.exe"
    fake_pyi.parent.mkdir(parents=True)
    fake_pyi.write_bytes(b"x")
    monkeypatch.setattr(pr, "EXE_NUITKA_OUT", fake_nuitka)
    monkeypatch.setattr(pr, "EXE_OUT", fake_pyi)
    assert pr.resolve_exe("") == fake_pyi
    out = capsys.readouterr().out
    assert "自测档" in out and "别拿它发布" in out, "退到自测档却没警告"


def test_leak_scan_exe_notice_branches_on_real_backend(tmp_path, capsys):
    """泄漏扫描里那句 exe 提示必须**按实际后端**说话。

    2026-09-19 实际踩到：它原先硬编码「是 PyInstaller 产物」，切 Nuitka 之后
    同一份日志里前一行说「exe 后端 = Nuitka ✓」、后一行说「是 PyInstaller」——
    自相矛盾且给出错的处置建议。这条测试防它退回去。
    """
    pr = _pack_release()
    src = _text(ROOT / "scripts" / "pack_release.py")

    # 源码层面：那句提示必须由 exe_backend(...) 的结果分支控制。
    # 用 log( 调用来定位，别用裸字符串 —— 注释里也会提到这句话（第一版就这么误判了）。
    call = src.index('log(f"⚠ {EXE_NAME} 是 PyInstaller 产物')
    head = src[max(0, call - 900):call]
    assert "exe_backend(exe_here)" in head, "exe 提示没有按实际后端分支"
    assert 'if kind == "pyinstaller":' in head, "没有 pyinstaller 分支"
    assert 'elif kind == "nuitka":' in src[call:call + 700], "没有 nuitka 分支"

    # 行为层面：放一个 Nuitka 产物进暂存树，泄漏扫描不能报 PyInstaller
    root = tmp_path / "Mod_Jgmg5L"
    (root / "ModAssets").mkdir(parents=True)
    (root / "ModAssets" / "config.json").write_text('{"llm": {"api_key": ""}}', encoding="utf-8")
    if not pr.EXE_NUITKA_OUT.is_file():
        pytest.skip("没有 Nuitka 产物可复制")
    import shutil
    shutil.copy2(pr.EXE_NUITKA_OUT, root / "ModAssets" / pr.EXE_NAME)

    pr.scan_leaks(root)
    out = capsys.readouterr().out
    assert "是 PyInstaller 产物" not in out, "对着 Nuitka 产物报了 PyInstaller"
    assert "Nuitka 产物" in out, "没报出真实的发行档后端"
