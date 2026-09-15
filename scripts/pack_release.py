#!/usr/bin/env python3
"""pack_release.py — 把本 mod 打成**别人机器上能直接跑**的分发包。

为什么需要它（2026-09-13 打包审计）：
    这件事以前是手工的，而手工做必然踩三个坑：
      ① Python 端没跟包走 —— 旧 Launcher 写死 `F:\\agent_loop\\scripts\\server.py`，
         换台机器就找不到，玩家侧表现是"mod 装了但 AI 不说话"；
      ② 依赖没跟包走 —— requirements 只有 websockets，真调 LLM 才需要 openai，
         于是"能连上但不回话"；
      ③ 作者自己的 config.json（含真实 API key）被顺手打进压缩包。
    本脚本把这三件事变成机械动作，并**自带泄漏扫描**在最后一道拦下 ③。

产物布局（<ModRoot> = 压缩包里的 Mod_Jgmg5L/，落地即 ModExportData/Mod_Jgmg5L/）：
    Mod_Jgmg5L/
    ├── ModCode/dll/MOD_Jgmg5L.dll      ← C# 端（游戏只枚举这一层）
    ├── AgentLoop/                      ← Python 运行时 + 我们的代码（勿手改）
    │   ├── python.exe / python3xx.dll / python3xx.zip
    │   ├── Lib/site-packages/          ← websockets / openai 及其依赖（win_amd64 轮子）
    │   ├── server.py                   ← C# 拉起它
    │   └── agent_loop/                 ← 我们的包（--protect pyc 时只剩 .pyc）
    ├── ModRes/AssetBundle/             ← UI 预制体 AB
    ├── ModExcel/                       ← 剧情配置壳表
    ├── config.json                     ← 由 config.example.json 生成（**空 key**）
    ├── prompts/                        ← 用户可编辑提示词
    └── 安装说明.txt
使用：
    python3 scripts/pack_release.py                      # 全默认（cp313 + pyc 保护 + 打 zip）
    python3 scripts/pack_release.py --protect none       # 不保护（自己调试用）
    python3 scripts/pack_release.py --deploy             # 顺便装进游戏目录
    python3 scripts/pack_release.py --game-root 'D:\\Steam\\...\\鬼谷八荒'
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

# ---------------- 常量 ----------------

REPO = Path(__file__).resolve().parent.parent      # <树根>（本包自身所在目录）
MOD_ID = "Jgmg5L"
MOD_DIR_NAME = f"Mod_{MOD_ID}"
RUNTIME_DIR = "AgentLoop"                          # 运行时目录名（两种布局共用这个名字）

#: 数据/运行时放在哪一层（--layout）。必须与 csharp/ModPaths.RuntimeDirNames / DataRoot 对齐。
#:   root      = <Mod根>/AgentLoop + <Mod根>/config.json       （手工拼装、便携版）
#:   modassets = <Mod根>/ModAssets/AgentLoop + .../config.json （**官方管线**：编辑器导出会整个带走）
LAYOUT_ROOT = "root"
LAYOUT_MODASSETS = "modassets"
DEFAULT_PY = "3.13.15"                             # 与开发机同 minor（3.13.x），改动需回归

#: 构建缓存/中间产物目录 —— **刻意放在仓库之外**（仓库的兄弟目录）。
#: 为什么不能放 <仓库>/.pack-cache：本仓库的根目录**就是 Python 包本身**，往包里塞一棵
#: 带 1200+ 个第三方 .py 的暂存树，任何"rglob 整个包"的东西都会撞上——实测直接打挂了
#: tests/test_log_setup.py 的架构哨兵（它 rglob 包目录、要求除 log_setup 外没人配 logging，
#: 而 site-packages 里的 openai/httpx/distro 当然会配）。构建产物不该住在包里。
#: dist/ 留在仓库内是安全的：里面只有 zip，rglob("*.py") 看不进压缩包。
BUILD_DIR = REPO.parent / f".{REPO.name}_build"
CACHE = BUILD_DIR / "cache"

#: 自包含入口 exe 的文件名（PyInstaller onefile 产物）——**这是现在的发行形态**：
#: 一个文件搞定 Python 侧，用户零安装。必须与 `csharp/ModPaths.SelfContainedExeNames` 对齐。
EXE_NAME = "AgentLoopServer.exe"
#: exe 的默认取用位置（`--build-exe` 的产物也落这儿）。刻意在仓库之外，理由同 BUILD_DIR。
EXE_BUILD_DIR = BUILD_DIR / "pybuild"
EXE_OUT = EXE_BUILD_DIR / "dist" / EXE_NAME
#: 构建 exe 用的 Windows venv（干净环境：只装 requirements + pyinstaller）
EXE_VENV_PY = EXE_BUILD_DIR / "venv" / "Scripts" / "python.exe"
#: PyInstaller spec（在仓库里，是**受版本管理**的构建输入）
EXE_SPEC = REPO / "scripts" / "AgentLoopServer.spec"

#: 开发机覆盖哨兵（见 csharp/ModPaths.DevRootOverride）：内容是源码树路径。
#: 存在时游戏跑源码树而不是随包副本 —— 这是**开发便利**，绝不能进发行包。
DEV_ROOT_SENTINEL = "_dev_root.txt"

#: 仓库里永不进包的东西（**显式列出，不靠 glob 排除**——漏一个就是泄漏）
NEVER_SHIP = [
    "config.json",            # ★ 作者本人的配置，含真实 API key
    "pytest.ini", ".pytest_cache", "tests", "test",
    "scripts/dev", "scripts/chat_cli.py",
    "docs", "reference", "backup", "ui_preview", "generated-images", "logs",
    ".workbuddy", ".mine_0913", ".pack-cache", f".{REPO.name}_build", ".git", "dist",
    "tmp_decomp_tool.txt", "tmp_tools_schema_dump.md", DEV_ROOT_SENTINEL,
    # 注意：CACHE 默认在仓库之外，不在此列；这一行只是防有人用 --stage 指回仓库里
    "_diag_initiative_force.txt.disabled",
]

#: 高信号泄漏判据 —— 对**所有文件按原始字节**扫（含 .pyc / .dll 这类二进制）
LEAK_BYTES = [
    (re.compile(rb"sk-[A-Za-z0-9]{20,}"), "疑似 API key（sk- 开头）"),
    (re.compile(rb"[A-Za-z]:[\\/]{1,2}agent_loop"), "开发机源码树绝对路径"),
    (re.compile(rb"/mnt/[a-z]/[^\x00]{0,40}agent_loop"), "WSL 下开发机源码树路径"),
    (re.compile(rb"Users[\\/]{1,2}(iu|zyh)\b"), "开发机用户名"),
    (re.compile(rb"DecompDump"), "开发机反编工程路径"),
]

#: 宽松判据 —— 只扫文本文件（二进制里偶然撞上这几个字节不算数）
LEAK_TEXT = [
    (re.compile(r"/mnt/[a-z]/"), "WSL 挂载路径"),
    (re.compile(r"[A-Za-z]:\\\\?agent_loop"), "开发机绝对路径（转义形式）"),
]


def log(msg: str) -> None:
    print(f"[pack] {msg}", flush=True)


def die(msg: str) -> "None":
    print(f"[pack] ✗ {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# ---------------- 1) 便携运行时 ----------------

def fetch_runtime(pyver: str, dest: Path) -> None:
    """下载并解包 python.org 官方 embeddable 包到 dest。

    选它而不是 python-build-standalone / PyInstaller 的理由：
      · URL 完全可预测（不依赖 GitHub API、无 rate limit），官方源，国内可达
      · 11MB / 解包约 28MB，比 standalone 小一半以上
      · 行为就是标准 CPython，只是 stdlib 打成了 python3xx.zip（`._pth` 指过去即可）
    代价：`._pth` 存在时 `sys.path` 只由该文件决定（`PYTHONPATH` 与环境变量失效），
    所以下面必须显式把 `Lib\\site-packages` 写进去，并打开 `import site`。
    """
    zip_name = f"python-{pyver}-embed-amd64.zip"
    arch = CACHE / zip_name
    url = f"https://www.python.org/ftp/python/{pyver}/{zip_name}"
    if not arch.is_file():
        CACHE.mkdir(parents=True, exist_ok=True)
        log(f"下载便携运行时 {url}")
        try:
            urllib.request.urlretrieve(url, arch)
        except Exception as e:  # noqa: BLE001
            die(f"运行时下载失败（{e}）——可手动下载 {url} 放到 {arch} 后重试")
    else:
        log(f"复用缓存 {arch.name}")

    log(f"解包 → {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(arch) as z:
        z.extractall(dest)

    # 写 ._pth：把 site-packages 纳入 sys.path 并启用 site（部分轮子靠 .pth 生效）
    pth = next(dest.glob("python3*._pth"), None)
    if pth is None:
        die("解包后找不到 python3xx._pth —— 运行时包结构变了，请检查")
    pth.write_text(
        "\n".join([
            pth.stem + ".zip",          # 例如 python313.zip（stdlib）
            ".",
            r"Lib\site-packages",
            "import site",
            "",
        ]),
        encoding="ascii",
    )
    log(f"已写 {pth.name}（含 Lib\\site-packages + import site）")


# ---------------- 2) 依赖（跨平台下 win_amd64 轮子） ----------------

def install_deps_impl(dest: Path, pyver: str, req: Path) -> None:
    """把 requirements.txt 的依赖（**win_amd64 版**）解包进 dest/Lib/site-packages。

    关键点：本脚本通常在 Linux/WSL 下跑，直接 `pip install --target` 会拉到 Linux 轮子
    （pydantic-core / jiter / websockets 都是平台相关二进制），装进 Windows 运行时必崩。
    故走 `pip download --platform win_amd64 --python-version <ver> --only-binary=:all:`：
    只下载不安装、不执行任何代码，纯解包即得可用的 Windows site-packages。
    """
    short = "".join(pyver.split(".")[:2])           # "3.13.15" -> "313"
    wheels = CACHE / f"wheels-cp{short}"
    wheels.mkdir(parents=True, exist_ok=True)

    log(f"下载 {req.name} 的 win_amd64 轮子（cp{short}）")
    cmd = [
        sys.executable, "-m", "pip", "download",
        "--only-binary=:all:", "--platform", "win_amd64",
        "--python-version", pyver, "--implementation", "cp", "--abi", f"cp{short}",
        "--dest", str(wheels), "-r", str(req),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"轮子下载失败：\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")

    sp = dest / "Lib" / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    n = 0
    for whl in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(whl) as z:
            z.extractall(sp)
        n += 1
    log(f"已解包 {n} 个轮子 → Lib/site-packages")
    if n == 0:
        die("一个轮子都没下到 —— 检查 requirements.txt 或网络")


# ---------------- 3) 我们的代码 ----------------

def iter_package_files() -> list:
    """枚举要进包的 Python 文件（**白名单式**：包目录下全部 .py + prompts 资产）。

    用「根目录全部 *.py + 三个子包 + prompts」而不是手写文件清单：
    新增模块自动跟上，不会因为漏登记而在玩家机上 ImportError。
    """
    out = []
    for p in sorted(REPO.glob("*.py")):
        out.append(p)
    for sub in ("llm", "compaction", "tools"):
        out += sorted((REPO / sub).rglob("*.py"))
    out += sorted((REPO / "prompts").rglob("*"))
    return [p for p in out if p.is_file() and "__pycache__" not in p.parts]


def stage_code(rt: Path) -> None:
    """把 Python 端源码铺进 <rt>/agent_loop/ 与 <rt>/server.py。"""
    pkg = rt / "agent_loop"
    pkg.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in iter_package_files():
        rel = src.relative_to(REPO)
        dst = pkg / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    shutil.copy2(REPO / "scripts" / "server.py", rt / "server.py")
    log(f"已铺 Python 端：{n} 个文件 + server.py")


def protect_pyc(rt: Path) -> None:
    """源码保护第一层：我们的代码**只发字节码**，第三方依赖只做预编译缓存。

    ⚠ 先看清这一层的真实强度（2026-09 调研结论）：
      · 挡住谁：解压看看、复制走、改一行就发的普通玩家 —— 够了。
      · 挡不住谁：会搜工具的人。`pycdc` 已能啃到 3.11 左右，而 **PyLingual**
        （IEEE S&P 2025 论文，公开服务）自称覆盖 3.6 → 当前版本。
        所以 `.pyc` 是"防手滑"，不是"防逆向"。真要防，走 Nuitka 编译成 .pyd
        （根本没有字节码可反）。本条如实写在这里，免得日后误以为已经安全了。

    边界（第一版踩过的坑）：
      · **只管我们自己的代码**（`agent_loop/` + `server.py`）。第一版对整棵树 rglob，
        把 `Lib/site-packages` 里 1200 多个第三方 .py 也编了并删源 —— 那些是公开代码，
        保护它们零收益，却引入了"某些库依赖 __file__ / importlib.resources 取源码相对
        资源"的实打实风险。依赖改为**只预编译不删源**（下个函数）。
      · **必须 stripdir**：.pyc 会把编译时的绝对源码路径写进每个 code object 的
        co_filename。不剥的话 `strings` 一跑就是 `/mnt/f/agent_loop/...`——既泄漏开发机
        路径，又让打包位置可被推断。本脚本的泄漏闸第一次跑就抓到了这个（是真泄漏）。
      · 入口 `server.py` 也编成字节码并**改回 .py 名字**：C# 的候选名是 server.py，
        而 CPython 按内容而非扩展名执行脚本（实测跑 .pyc 内容正常）。
      · 清掉 `__pycache__`：残留的 .pyc 会在删源后被当缓存命中，掩盖"新包没编进去"。
    """
    targets = [rt / "server.py"] + sorted((rt / "agent_loop").rglob("*.py"))
    for py in targets:
        # stripdir 让 co_filename 变成相对路径（agent_loop/xxx.py），不再带开发机前缀
        if not compileall.compile_file(str(py), legacy=True, quiet=2, stripdir=str(rt)):
            die(f"编译失败：{py}")
    n = 0
    for py in targets:
        py.unlink()
        n += 1
    entry_pyc = rt / "server.pyc"
    if entry_pyc.is_file():
        entry_pyc.replace(rt / "server.py")
    for cache in list(rt.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    log(f"源码保护：{n} 个自有 .py → .pyc（源码已删、co_filename 已剥开发机路径）")


def precompile_deps(rt: Path) -> None:
    """给第三方依赖生成 `__pycache__/*.pyc`（**保留 .py**，不删源）。

    为什么做：Launcher 下发了 `PYTHONDONTWRITEBYTECODE=1`（发行包目录尽量别被写脏），
    于是 openai / pydantic 这种大块头每次冷启动都要在内存里重新编译一遍，
    实测拖慢启动。预编译成 .pyc 后既快又不牺牲可读性/可审计性 —— 依赖本来就是公开代码。
    """
    sp = rt / "Lib" / "site-packages"
    if not sp.is_dir():
        return
    compileall.compile_dir(str(sp), quiet=2, force=True, stripdir=str(rt))
    n = sum(1 for _ in sp.rglob("__pycache__/*.pyc"))
    log(f"依赖预编译：{n} 个 .pyc（.py 保留）")


# ---------------- 4) 游戏侧资产 ----------------

def stage_game_assets(root: Path, game_root: Path, project: "Path | None" = None) -> None:
    """取 AB / ModExcel（它们不在仓库里，是 Unity / 编辑器侧的产物）。

    AB 的来源**优先取模组编辑器工程**（`<ModProject>/ModRes/AssetBundle`），已部署的游戏目录
    降级为兜底。为什么改这个方向（2026-09-13）：原来只从游戏目录读，而游戏目录是**下游**
    —— 作者手工拷进游戏的那份一旦比工程旧，打包就会把新 AB 覆盖成旧的，包里的面板自然也对不上。
    工程是创作侧，应该它说了算。
    """
    if not game_root.is_dir():
        die(f"游戏目录不存在：{game_root}（用 --game-root 指定）")
    src_mod = game_root / "ModExportData" / MOD_DIR_NAME
    if not src_mod.is_dir():
        die(f"找不到已部署的 mod：{src_mod}")

    dst_modres = root / "ModRes"
    src_ab = None
    if project:
        cand = project / "ModRes" / "AssetBundle"
        if _ab_newest(cand) > 0:
            src_ab = cand
            log(f"AB 取自编辑器工程：{cand}")
    if src_ab is None:
        cand = src_mod / "ModRes" / "AssetBundle"
        if _ab_newest(cand) > 0:
            src_ab = cand
            log(f"AB 取自已部署的游戏目录：{cand}（工程里那份没有 .ab）")
    if src_ab is not None:
        shutil.copytree(src_ab, dst_modres / "AssetBundle",
                        ignore=shutil.ignore_patterns("*.bak*", "*.manifest.bak*"))
        n = sum(1 for _ in (dst_modres / "AssetBundle").rglob("*.ab"))
        log(f"已复制 ModRes/AssetBundle（{n} 个 .ab）")
    else:
        log("⚠ 未找到任何 .ab —— 包里将没有 UI 预制体（面板会开不出来）")

    src_excel = src_mod / "ModExcel"
    if src_excel.is_dir():
        shutil.copytree(src_excel, root / "ModExcel",
                        ignore=shutil.ignore_patterns("*.bak*"))
        log(f"已复制 ModExcel（{len(list((root / 'ModExcel').glob('*.json')))} 个 json）")
    else:
        log("⚠ 未找到 ModExcel —— 自制剧情窗会开不出来")

    stage_manifest(root, src_mod)


def stage_manifest(root: Path, src_mod: Path) -> None:
    """带上 mod 清单与封面图 —— 第一版漏了，是发行级缺口。

    `ModExportData.cache`（411 字节，官方编辑器导出的加密清单）：
      游戏是**按这个名字**找 mod 的 —— `Player.log` 里那行
      `加载模组：<mod目录>/ModExportData.cache`，调用栈是
      `ModMgr.LoadMod(String)` ← `ModMgr.LoadAllMod()`，即 LoadAllMod 枚举
      ModExportData/ 下的每个目录、再用这个固定文件名去 LoadMod。
      换句话说：**每个真实 mod 根层都有它**（本地部署有、工坊 20 个 mod 全有），
      而我们第一版的 zip 里没有 —— 全新安装的玩家目录里就不会有这个文件。
      它是否"必需"我没能实证（interop 反编只有 ModMgr 的空壳，没有方法体），
      但代价是 411 字节、收益是消除"游戏根本不认这个 mod"的可能 —— 这个赌不值得打。
      内容已扫过：无任何本机路径/用户名，且绑的是固定的 MID，对所有玩家一致。

    `ModProjectPreview.png`：工坊 mod 的封面图（实测 600KB~950KB 在根层）。
      没有它 mod 列表里就没有预览图。我们目前没有这张图 —— 只告警，不阻断打包。
    """
    cache = src_mod / "ModExportData.cache"
    if cache.is_file():
        shutil.copy2(cache, root / "ModExportData.cache")
        log(f"已复制 ModExportData.cache（{cache.stat().st_size} 字节，游戏按此名识别 mod）")
    else:
        log("⚠ 未找到 ModExportData.cache —— 玩家全新安装时目录里将缺这个文件，"
            "有「游戏不认 mod」的风险；请先在游戏里正常启用一次本 mod 再打包")

    preview = src_mod / "ModProjectPreview.png"
    if preview.is_file():
        shutil.copy2(preview, root / "ModProjectPreview.png")
        log(f"已复制 ModProjectPreview.png（{preview.stat().st_size // 1024}KB，mod 列表封面）")
    else:
        log("⚠ 无 ModProjectPreview.png —— mod 列表里不会显示封面图（不影响功能）")


def check_config_freshness(src_mod: Path, project_dir: "Path | None", allow_stale: bool) -> None:
    """配置表新鲜度闸：改了 xlsx 但忘了在编辑器里重新导出 → 包里是旧配置。

    为什么需要（2026-09-13，用户提出"最终形态应该走编辑器导出"后补）：
      `ModExcel/*.json` 与 `ModExportData.cache` **只能由官方编辑器产生**（实测三处 cache 的
      md5：编辑器 debug 运行输出 `dadd2cbc…`、编辑器「导出模组」输出 `2b754d33…`、
      游戏部署目录 `2b754d33…`）——可见 cache 是编辑器生成的，且**跟着输出目标变**，
      所以必须用「导出」那份、不能用「debug」那份。改了配置表就得重新导出一次。
      这个动作纯手工，忘了的后果是：包能跑、但跑的是旧剧情配置，而且**没有任何报错**。
      正是那种"发出去三天后才被发现"的错。故此处用 mtime 做一道机械检查。

    判据：编辑器工程里的任一 `*.xlsx` 比我们要打包的 cache 更新 → 说明改完没导出。
    """
    cache = src_mod / "ModExportData.cache"
    if not cache.is_file():
        return
    if project_dir is None or not project_dir.is_dir():
        log("配置新鲜度：未指定编辑器工程目录，跳过检查（--excel-project 可指定）")
        return
    try:
        cache_mtime = cache.stat().st_mtime
    except OSError:
        return
    newer = []
    for x in list(project_dir.rglob("Excel/*.xlsx")) + list(project_dir.rglob("ModExcel/*.xlsx")):
        try:
            if x.stat().st_mtime > cache_mtime:
                newer.append(x)
        except OSError:
            continue
    if not newer:
        log(f"配置新鲜度：OK（{len(list(project_dir.rglob('*.xlsx')))} 个 xlsx 都不晚于 cache）")
        return
    print("[pack] ⚠ 配置表比 ModExportData.cache 新 —— 很可能改了表但没在编辑器里重新导出：")
    for x in newer[:10]:
        print(f"    {x}")
    print(f"    cache 时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cache_mtime))}")
    if allow_stale:
        print("[pack] ⚠ 按 --allow-stale-config 继续打包（包里是旧配置）")
    else:
        die("请在模组编辑器里「导出模组」，让新的 ModExcel/cache 落到游戏目录后再打包；"
            "确实要用旧配置就加 --allow-stale-config")


def build_exe(clean: bool = True) -> Path:
    """用 PyInstaller 打出自包含的 `AgentLoopServer.exe`（约 15 MB，16 秒）。

    为什么用**独立的干净 venv**（`<构建目录>/pybuild/venv`）而不是开发机的 Python：
      exe 里装什么完全取决于打包环境装了什么——用开发环境打，赌的是"我这儿碰巧没装多余东西"。
      专用 venv 只装 `requirements.txt` + `pyinstaller`，产物可复现，也不会把开发机的包漏进去。

    前置（只需做一次，见 docs/PACKAGING.md）：
        py -3.12 -m venv <构建目录>\\pybuild\\venv
        <venv>\\python.exe -m pip install -U pip pyinstaller
        <venv>\\python.exe -m pip install -r requirements.txt
    """
    if not EXE_VENV_PY.is_file():
        die(f"打包用的 venv 不存在：{EXE_VENV_PY}\n"
            f"      先建它（只需一次）：\n"
            f"        py -3.12 -m venv {EXE_BUILD_DIR / 'venv'}\n"
            f"        {EXE_VENV_PY} -m pip install -U pip pyinstaller\n"
            f"        {EXE_VENV_PY} -m pip install -r {REPO / 'requirements.txt'}")
    if not EXE_SPEC.is_file():
        die(f"spec 不存在：{EXE_SPEC}")

    pyinstaller = EXE_VENV_PY.parent / "pyinstaller.exe"
    if not pyinstaller.is_file():
        die(f"venv 里没有 pyinstaller：{pyinstaller}\n"
            f"      装它：{EXE_VENV_PY} -m pip install -U pyinstaller")
    cmd = [str(pyinstaller), "--noconfirm",
           "--distpath", win_path(EXE_BUILD_DIR / "dist"),
           "--workpath", win_path(EXE_BUILD_DIR / "build"),
           win_path(EXE_SPEC)]
    if clean:
        cmd.insert(1, "--clean")
    log(f"构建 exe：{EXE_NAME}（PyInstaller onefile，约 15 MB / 16 秒）")
    r = subprocess.run(cmd, cwd=str(EXE_BUILD_DIR))
    if r.returncode != 0:
        die(f"PyInstaller 失败（退出码 {r.returncode}）")
    if not EXE_OUT.is_file():
        die(f"PyInstaller 报成功但没看到产物：{EXE_OUT}")
    return EXE_OUT


def win_path(p: Path) -> str:
    """把 `/mnt/f/x/y` 翻成 `F:\\x\\y`。

    **Windows 侧的程序（PyInstaller / python.exe）看不懂 WSL 的 /mnt/... 路径**——
    实测踩过：`pip install -r /mnt/f/agent_loop/requirements.txt` 直接报"文件不存在"。
    本脚本在 WSL 里跑，而它调用的构建工具在 Windows 侧，故凡是要喂给它们的路径都得先翻译。
    已经是 Windows 形式（或本来就在 Windows 上跑）的原样返回。
    """
    if os.name == "nt":
        return str(p)
    m = re.match(r"^/mnt/([a-z])/(.*)$", str(p))
    if m:
        return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")
    return str(p)


#: 真正**随包分发**的依赖（分布名, 许可, 主页）。
#: 刻意手工列出而不是扫 `*.dist-info`：打包 venv 里还有 PyInstaller 及其构建期依赖
#: （altgraph / pefile / pywin32-ctypes / packaging / setuptools / pip），
#: 它们**不进 exe**，把它们写进许可文件是错的。
SHIPPED_DISTS = [
    ("websockets", "BSD-3-Clause", "https://github.com/python-websockets/websockets"),
    ("openai", "Apache-2.0", "https://github.com/openai/openai-python"),
    ("httpx", "BSD-3-Clause", "https://github.com/encode/httpx"),
    ("httpcore", "BSD-3-Clause", "https://github.com/encode/httpcore"),
    ("h11", "MIT", "https://github.com/python-hyper/h11"),
    ("anyio", "MIT", "https://github.com/agronholm/anyio"),
    ("sniffio", "MIT / Apache-2.0", "https://github.com/python-trio/sniffio"),
    ("certifi", "MPL-2.0", "https://github.com/certifi/python-certifi"),
    ("idna", "BSD-3-Clause", "https://github.com/kjd/idna"),
    ("distro", "Apache-2.0", "https://github.com/python-distro/distro"),
    ("pydantic", "MIT", "https://github.com/pydantic/pydantic"),
    ("pydantic_core", "MIT", "https://github.com/pydantic/pydantic-core"),
    ("jiter", "MIT", "https://github.com/pydantic/jiter"),
    ("annotated_types", "MIT", "https://github.com/annotated-types/annotated-types"),
    ("typing_extensions", "PSF-2.0", "https://github.com/python/typing_extensions"),
    ("typing_inspection", "MIT", "https://github.com/typing-inspection/typing_inspection"),
    ("tqdm", "MPL-2.0 / MIT", "https://github.com/tqdm/tqdm"),
    ("colorama", "BSD-3-Clause", "https://github.com/tartley/colorama"),
]


def stage_licenses(data_dir: Path, pkg_dir: "Path | None") -> None:
    """往数据根写一份 `开源许可.txt`：列明随包组件与许可，并**附上许可原文**。

    为什么这不是可选项（2026-09-13 改 exe 形态时才发现的合规缺口）：
      旧形态靠 `AgentLoop/LICENSE.txt` 满足 PSF 的「版权声明须随二进制分发保留」；
      改成自包含 exe 后 CPython 被打进 exe 里，那份 txt 没有天然落点了。
      同理 openai 是 Apache-2.0（要求附 LICENSE 与 NOTICE），其余是各版 MIT/BSD。
      社区里没人管不等于没风险，而成本只是几十 KB 文本。

    许可原文从**打包环境**里就地取（`<site-packages>/<dist>.dist-info/`，注意新式轮子把许可
    放在 `licenses/` **子目录**里 —— 第一版只扫顶层，结果 18 个依赖里只捞到 2 个）；
    CPython 的在 `<venv>/pyvenv.cfg` 的 `home=` 指向的基础解释器目录下。
    取不到就只留索引与链接（并说一声），**绝不因为找不到原文而让打包失败**。
    """
    lines = [
        "本 mod 随包分发的开源组件及其许可",
        "=" * 62,
        "",
        "Python 侧程序（AgentLoopServer.exe / AgentLoop/）内含以下组件：",
        "",
        "  %-24s %-18s %s" % ("组件", "许可", "来源"),
    ]
    lines += ["  %-24s %-18s %s" % (n, lic, url) for n, lic, url in
              [("CPython", "PSF License 2.0", "https://docs.python.org/3/license.html")] + SHIPPED_DISTS]
    lines += [
        "",
        "本 mod 自身的代码版权归作者所有，保留一切权利；禁止转载与二次分发。",
        "各组件完整许可原文见下（顺序与上表一致）。",
        "",
        "=" * 62,
        "许可原文",
        "=" * 62,
        "",
    ]

    site = (pkg_dir / "Lib" / "site-packages") if pkg_dir \
        else (EXE_VENV_PY.parent.parent / "Lib" / "site-packages")
    found, missing = [], []

    def add(title: str, files) -> bool:
        got = False
        for f in files:
            try:
                body = f.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                try:
                    body = f.read_text(encoding="latin-1", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
            lines.extend(["-" * 62, f"【{title}】  （{f.parent.name}/{f.name}）", "-" * 62, "",
                          body.strip(), ""])
            got = True
        return got

    # ① CPython：从 pyvenv.cfg 的 home= 找基础解释器目录
    try:
        cfg = (pkg_dir or EXE_VENV_PY.parent.parent) / "pyvenv.cfg"
        home = None
        if cfg.is_file():
            for ln in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
                if ln.strip().lower().startswith("home"):
                    home = ln.split("=", 1)[1].strip()
                    break
        if home:
            lic = next((Path(win_to_wsl(home)) / n for n in ("LICENSE.txt", "LICENSE")
                        if (Path(win_to_wsl(home)) / n).is_file()), None)
            if lic and not add("CPython (PSF License 2.0)", [lic]):
                missing.append("CPython")
        else:
            missing.append("CPython")
    except Exception as e:  # noqa: BLE001
        missing.append(f"CPython({e})")

    # ② 随包依赖：dist-info 里的 LICENSE / COPYING / NOTICE（顶层与 licenses/ 子目录都要看）
    def dist_name_of(d: Path) -> str:
        """`websockets-17.1.dist-info` → `websockets`（PEP 503 归一化）。

        不能简单用 startswith 比前缀：`pydantic_core-…` 会被 `pydantic` 误匹配，
        而反过来把两侧都换成 `_` 也一样会撞（`pydantic_` 是 `pydantic_core_` 的前缀）。
        规范做法是**先砍掉版本号**再整体相等比较。
        """
        stem = d.name[:-len(".dist-info")] if d.name.endswith(".dist-info") else d.name
        return stem.rsplit("-", 1)[0].lower().replace("-", "_")

    for dist, lic_name, _url in SHIPPED_DISTS:
        key = dist.lower().replace("-", "_")
        info = None
        try:
            if site.is_dir():
                info = next((d for d in site.glob("*.dist-info") if dist_name_of(d) == key), None)
        except Exception:  # noqa: BLE001
            info = None
        if info is None:
            missing.append(dist)
            continue
        files = [f for f in sorted(info.rglob("*"))
                 if f.is_file() and f.suffix.lower() in ("", ".txt", ".md", ".rst")
                 # LICENCE 是英式拼法（tqdm 就用它），别只认 LICENSE
                 and any(k in f.name.upper() for k in ("LICENSE", "LICENCE", "COPYING", "NOTICE"))]
        if not add(f"{dist} ({lic_name})", files):
            missing.append(dist)

    if missing:
        lines += ["", "（以下组件未能在打包环境里取到许可原文，请按上表链接自行补齐："
                      + "、".join(missing) + "）", ""]
        log(f"⚠ 开源许可：{len(missing)}/{len(SHIPPED_DISTS) + 1} 个组件没取到原文（已留链接）："
            + "、".join(missing[:6]))
    else:
        log(f"开源许可：{len(SHIPPED_DISTS) + 1} 个组件的许可原文全部取到")

    (data_dir / "开源许可.txt").write_text("\n".join(lines), encoding="utf-8")
    log(f"已生成 {data_dir.name}/开源许可.txt（{(data_dir / '开源许可.txt').stat().st_size / 1024:.0f} KB）")


def win_to_wsl(s: str) -> str:
    """`win_path` 的反向：Windows 形式 → WSL 的 /mnt 形式（已经是就别动）。"""
    if os.name == "nt":
        return s
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if m:
        return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
    return s


def stage_exe(data_dir: Path, exe: Path) -> None:
    """把自包含 exe 复制进数据根（= `<ModAssets>\\` 或 `<Mod根>\\`）。

    与旧的 `stage_code` + `fetch_runtime` + `install_deps` 三件套是**替代关系**：
    那些步骤在拼一个"便携 CPython + 我们的 .pyc + 第三方 site-packages"的目录树，
    而 exe 把这三样一起吞了。走 exe 路线时它们全跳过。
    """
    if not exe.is_file():
        die(f"找不到 exe：{exe}\n"
            f"      先构建它：python3 scripts/pack_release.py --build-exe\n"
            f"      或指定已有产物：--exe <路径>")
    dst = data_dir / EXE_NAME
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, dst)
    log(f"→ {EXE_NAME}（{dst.stat().st_size / 1e6:.1f} MB，自包含）")


# ---------------- 3.5) AB 资源同步 ----------------

#: AB 的全部可能落点。**三个都要认、都要铺**，因为它们服务的是不同消费者：
#:   ① <ModProject>/ModRes/ResBuildABProject/Assets/AssetBundle
#:        **官方文档明写的路径**。《资源修改教程.docx》原文：「进入游戏调试时，是通过路径
#:        ModProject_XXXXXX\ModProject\ModRes\ResBuildABProject\Assets\AssetBundle 来查找
#:        AB 资源的」—— 也就是说编辑器的 AB 管线认这一处。「导出模组」是否同一套逻辑
#:        尚未实证，但按同一套处理最稳。
#:   ② <ModProject>/ModRes/AssetBundle
#:        发行槽位。**本 mod 的 C# 直载的就是它**（ModAbRes.PreloadAll 递归
#:        AssetBundle.LoadFromFile(<Mod根>/ModRes/AssetBundle/**/*.ab)），
#:        也是全部工坊 mod（含神识传音）成品里的实际形态。
#:   ③ <游戏>/Mod/modFQA/资源修改教程/ResBuildABProject/Assets/AssetBundle
#:        官方模板工作区。文档允许「把 ResBuildABProject 工程复制到其他地方」，
#:        作者实测就是在这一份里干活，故必须认。
def ab_slots(project: "Path | None", game_root: "Path | None") -> list:
    out = []
    if project:
        out.append(project / "ModRes" / "ResBuildABProject" / "Assets" / "AssetBundle")
        out.append(project / "ModRes" / "AssetBundle")
    if game_root:
        out.append(game_root / "Mod" / "modFQA" / "资源修改教程" / "ResBuildABProject"
                   / "Assets" / "AssetBundle")
    return out


def _ab_newest(d: Path) -> float:
    """目录里最新一个 .ab 的 mtime；没有 .ab 返回 0。"""
    try:
        return max((f.stat().st_mtime for f in d.rglob("*.ab")), default=0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def sync_ab(project: Path, game_root: "Path | None") -> None:
    """把 AB 构建产物同步到**所有槽位**，以「最新的一份」为准。

    为什么必须同步而不是各管各的：这三处现在是靠**手工复制**维持一致的（实测：作者把
    「更新AB」的产物手工拷到 ModRes/AssetBundle 与游戏部署目录）。手工同步迟早漏一处，
    而漏哪一处都会以「面板打不开」的形式暴露，且极难联想到是 AB 没铺到某个目录。

    方向取「最新 .ab 的时间戳」而不是写死某一处：作者可能在 Unity 工作区刚更新完 AB
    （那 ① 最新），也可能刚从别处拷了一份进来（那 ② 最新）。按时间戳自动判方向，
    两种习惯都对。全部空则什么也不做（不是所有人都在做 UI）。
    """
    slots = ab_slots(project, game_root)
    have = [(d, _ab_newest(d)) for d in slots]
    live = [(d, t) for d, t in have if t > 0]
    if not live:
        log("AB 同步：三处都没有 .ab，跳过（本 mod 若不需要 UI 可忽略）")
        return
    src, src_t = max(live, key=lambda x: x[1])
    log(f"AB 同步：以最新的一份为准 → {src}（{len(list(src.rglob('*.ab')))} 个 .ab）")

    n_sync = 0
    for dst, dst_t in have:
        if dst == src:
            continue
        if dst_t >= src_t:
            continue                      # 已经是同一批（或更新），不动
        # 只搬产物本身：*.ab 与 AssetBundle.manifest。
        # **不搬 Unity 的 .meta / *.ab.manifest** —— 那些是给 Unity 编辑器认资产用的，
        # 发行槽位里带上它们只会让包变脏（官方成品里也没有）。
        dst.mkdir(parents=True, exist_ok=True)
        cnt = 0
        for f in list(src.rglob("*.ab")) + list(src.glob("AssetBundle.manifest")):
            rel = f.relative_to(src)
            if f.name.endswith(".ab.meta"):
                continue
            t = dst / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)
            cnt += 1
        log(f"  → {dst}（{cnt} 个文件）")
        n_sync += 1
    if n_sync == 0:
        log("AB 同步：三处已一致，无需动作")


# ---------------- 3.6) 吃编辑器「导出模组」的产物 ----------------

#: 「导出模组」产物里**应该有**的东西。第三列 True = 缺了 mod 就是坏的。
#: 为什么要有这张表：编辑器导出到底搬哪些目录，官方文档只写了「进入游戏调试」搬
#: `bin/Release/*.dll`，「导出模组」一个字没写。所以只能导出一次、逐项核对。
#: 这张表就是那次核对的清单 —— 缺哪一项，答案就是「编辑器不搬它」，而不是「我忘了放」。
EXPORT_EXPECTED = [
    ("ModExportData.cache",            "游戏按它识别 mod（编辑器生成，加密）", True,  "file"),
    ("ModAssets/AgentLoopServer.exe",  "Python 侧全部（自包含，一个文件）",       True,  "file"),
    ("ModAssets/config.json",          "用户配置模板（api_key 必须为空）",        True,  "file"),
    ("ModAssets/prompts",              "提示词（用户可编辑）",                    True,  "dir"),
    ("ModCode/dll/MOD_Jgmg5L.dll",     "C# 端 —— **是否被导出带走就是要验的事**", True,  "file"),
    ("ModRes/AssetBundle",             "UI 用的 AB —— **同上**",                  True,  "ab"),
    ("ModExcel",                       "剧情配置壳表",                            False, "dir"),
    ("ModProjectPreview.png",          "工坊列表封面",                            False, "file"),
]


def inspect_export(export_dir: Path) -> bool:
    """逐项核对「导出模组」产物，返回「是否全部必需项都在」。

    只读，不改任何东西 —— 这个函数的意义就是把「编辑器到底搬了什么」变成一张能看的表。
    """
    log(f"核对导出产物：{export_dir}")
    all_ok = True
    rows = []
    for rel, why, required, kind in EXPORT_EXPECTED:
        p = export_dir / rel
        if kind == "ab":
            n = len(list(p.rglob("*.ab"))) if p.is_dir() else 0
            ok = n > 0
            detail = f"{n} 个 .ab" if ok else "空/不存在"
        elif kind == "dir":
            n = sum(1 for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0
            ok = n > 0
            detail = f"{n} 个文件" if ok else "空/不存在"
        else:
            ok = p.is_file()
            detail = f"{p.stat().st_size:,} 字节" if ok else "不存在"
        if required and not ok:
            all_ok = False
        mark = "OK" if ok else ("XX" if required else "!!")
        rows.append(f"  [{mark}] {rel:<34} {detail:<16} {why}")
    print("\n".join(rows))
    if all_ok:
        log("导出产物核对通过：全部必需项都在 → 这份就是可以直接发 / 直接装的完整包")
    else:
        print("\n  [XX] = 必需项缺失。**这就是「编辑器导出不搬它」的答案** —— "
              "改 pack_release.py 往对应槽位补，或改成手工拼包。", file=sys.stderr)
    return all_ok


def install_export(export_dir: Path, game_root: Path) -> None:
    """把导出产物装进游戏目录 —— 这样测的才是**玩家真拿到的那份**。

    与 `--deploy` 的区别（这是关键，别混）：`--deploy` 装的是**本脚本自己拼的树**
    （每样东西都按已知规则放好，所以「编辑器的规则对不对」它永远验不出来）；
    本函数装的是**编辑器导出的树**，验的才是真实发行链路。

    用户数据一律保留：`portrait_cache/`（立绘缓存，重建很慢）与 `ModAssets/config.json`
    （玩家填的 key）。**这两样丢了都是"测一次要重填一遍"的痛**。
    """
    dst = game_root / "ModExportData" / MOD_DIR_NAME
    log(f"把导出产物装进游戏：{export_dir} → {dst}")

    # ① 先把玩家数据摘出来（导出产物里那份 config.json 是空模板，绝不能盖掉已填的 key）
    keep_cfg = None
    for cand in (dst / "ModAssets" / "config.json", dst / "config.json"):
        if cand.is_file():
            try:
                k = (json.loads(cand.read_text(encoding="utf-8-sig")).get("llm") or {}).get("api_key")
                if k:
                    keep_cfg = cand.read_bytes()
                    log(f"已备份现有 config.json（含 key，来自 {cand.parent.name}/）")
                    break
            except Exception:  # noqa: BLE001
                pass

    # ② 清掉旧内容，只留立绘缓存
    if dst.is_dir():
        for item in dst.iterdir():
            if item.name == "portrait_cache":
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
    dst.mkdir(parents=True, exist_ok=True)

    # ③ 铺导出产物
    for item in export_dir.iterdir():
        t = dst / item.name
        if item.is_dir():
            shutil.copytree(item, t)
        else:
            shutil.copy2(item, t)

    # ④ 还回玩家数据
    if keep_cfg is not None:
        tgt = dst / "ModAssets" / "config.json"
        tgt.parent.mkdir(parents=True, exist_ok=True)
        tgt.write_bytes(keep_cfg)
        log(f"已还回 config.json（key 保住了）→ {tgt.relative_to(dst)}")
    log(f"已装好 → {dst}（现在启动游戏，测的就是编辑器导出的那份）")


def stage_dll(root: Path, config: str) -> None:
    dll = REPO / "csharp" / "bin" / config / "MOD_Jgmg5L.dll"
    if not dll.is_file():
        die(f"找不到编译产物 {dll}（先 dotnet build -c {config}）")
    dst = root / "ModCode" / "dll"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dll, dst / dll.name)
    log(f"已复制 DLL（{config}，{dll.stat().st_size} 字节，md5={md5(dll)[:12]}…）")


def stage_userdata(root: Path, data_dir: Path) -> None:
    """config.json（空 key 模板）+ prompts/（用户可编辑副本）+ 安装说明。

    data_dir 由 --layout 决定：root 布局 = Mod 根；modassets 布局 = <Mod根>/ModAssets。
    后者是官方管线要求的位置 —— 编辑器只搬 ModAssets/，放外层它不会带走。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "config.example.json", data_dir / "config.json")
    log(f"已生成 {data_dir.relative_to(root)}/config.json（由 config.example.json，**api_key 为空**）")

    dst = data_dir / "prompts"
    shutil.copytree(REPO / "prompts", dst, ignore=shutil.ignore_patterns("__pycache__"))
    log(f"已复制 {dst.relative_to(root)}（{sum(1 for _ in dst.rglob('*') if _.is_file())} 个文件，用户可编辑）")

    (root / "安装说明.txt").write_text(
        INSTALL_TXT.replace("@DATA@", data_dir.relative_to(root).as_posix() or "."),
        encoding="utf-8")


INSTALL_TXT = """\
【安装】把 Mod_Jgmg5L 整个文件夹放到：
    <你的鬼谷八荒安装目录>\\ModExportData\\
（即最终形如 …\\鬼谷八荒\\ModExportData\\Mod_Jgmg5L\\ModCode\\dll\\MOD_Jgmg5L.dll）
也可以从创意工坊订阅（本 mod 就是按创意工坊的格式打包的）。
然后启动游戏、读取任意存档进世界即可。**不需要安装 Python，不需要 pip**。

【首次必做】填 API Key
    mod 自带的是"空大脑"：不填 key 时 AI 只会复读你的话。
    两种填法（任选其一）：
      · 打开对话窗（NPC 面板「AI 对话」/ 传音簿点一行）→ 点右上角 ⚙ 齿轮 → 填 base_url / api_key / model → 保存（自动重启生效）
      · 直接编辑 @DATA@\\config.json 的 llm 块，然后重启游戏
    base_url 例：https://api.deepseek.com/v1   （填到 /v1 为止，不要再往后加 /chat/completions）

【怎么打开界面】对话窗：NPC 面板的「AI 对话」按钮 / 传音簿里点一行 / 剧情里的 AI 选项
    传音簿（通讯录）：大地图 HUD 上的「传」按钮
    配置面板：对话窗右上角的 ⚙ 齿轮（Esc 或面板自带的 ✕ 关闭）
    ⚠ 2026-09-14 起**不再有任何 F 键热键**：旧的 F9/F10/F11 已删除。
      原因不是键位不好，是它们按不动——帧回调被重复注册，同一次按键触发两次开关、
      开→关自己抵消了。上面三个界面入口都可用，功能没有任何缺失。
      如果你看到别处（工坊简介 / 旧教程）写着「按 F11 填 key」，那是指 **⚙ 齿轮**。

【出问题看这里】@DATA@\\logs\\agent_loop.log
    报障时把这个文件发给作者即可。
    想自己先查一步：在 @DATA@ 目录里开命令行跑
        AgentLoopServer.exe --selftest
    它会打出 Python 版本、数据根、提示词目录，并点名缺了哪个依赖。

【不要动】@DATA@\\AgentLoopServer.exe  （Python 侧程序本体）
          Mod_Jgmg5L\\ModCode\\     Mod_Jgmg5L\\ModRes\\     Mod_Jgmg5L\\ModExcel\\
【可以改】@DATA@\\config.json   @DATA@\\prompts\\
          （改了下次对话生效，无需重启）

【对话记录存在哪】C:\\Users\\<你的用户名>\\.sessions\\
    每个 NPC 一份 jsonl，按存档分子目录：worlds\\<存档ID>\\<NPC名>.jsonl；
    同目录下还有 contacts.json（传音簿名单）。
      · 想重新开始（忘掉所有对话）：删掉 .sessions 即可，mod 一行都不用动
      · 想彻底卸载：删掉 mod 文件夹 + 这个 .sessions 目录
      · 想换台机器接着玩：把 .sessions 整个拷过去
    ⚠ 只有**游戏内存档**才会把对话写进去。不存档直接退游戏，这段对话不算数
      （下次进世界会回到上次存档时的样子）—— 这是刻意的，不是丢数据。

【杀软提示】AgentLoopServer.exe 是自解压的单文件程序，个别安全软件（360/火绒等）
    可能误报。它只做三件事：监听 127.0.0.1 的本机端口、读写同目录下的文件、按你填的
    base_url 调 LLM 接口，不往别处联网、不写注册表。误报可加白名单，或去杀软官网提交申诉。
"""


# ---------------- 5) 泄漏扫描（最后一道闸） ----------------

def md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_leaks(root: Path, allow_dev_paths: bool = False) -> None:
    """在打包前扫全树：命中密钥/开发机路径即**中止**。

    这不是形式主义：作者本人的 config.json 就在同一个仓库里，而"顺手 cp -r"是
    打包事故里最常见的一种。机器能判的事就不要靠人记得。

    两档判据（都是踩过坑才这么分的）：
      · LEAK_BYTES：高信号，**按字节扫所有文件**。必须这样——
        `.pyc` 是二进制，而它会把编译时的绝对源码路径写进每个 code object 的
        co_filename；Debug 版 DLL 也会在 PE 的 CodeView(RSDS) 目录里写下 PDB 绝对路径。
        用文本方式扫会把这些当"读不懂"跳过。本闸第一次跑就把这两种都抓了出来。
      · LEAK_TEXT：宽松判据（如裸 `/mnt/c/`），只扫文本，避免二进制偶然撞字节误报。

    allow_dev_paths：只降级「开发机路径/用户名」这一类，**密钥类永不放行**——
    路径泄漏是难看，密钥泄漏是要赔钱的，两者不该共用一个开关。
    """
    KEY_RX = re.compile(rb"sk-[A-Za-z0-9]{20,}")
    hard, soft = [], []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except Exception:  # noqa: BLE001
            continue
        for rx, why in LEAK_BYTES:
            for m in rx.finditer(raw):
                line = f"  {why}: {p.relative_to(root)} … {m.group(0)[:70]!r}"
                (hard if rx.pattern == KEY_RX.pattern else soft).append(line)
        if p.suffix.lower() in (".dll", ".pyd", ".ab", ".png", ".jpg", ".zip", ".pyc", ".exe"):
            continue
        text = raw.decode("utf-8", errors="ignore")
        for rx, why in LEAK_TEXT:
            for m in rx.finditer(text):
                soft.append(f"  {why}: {p.relative_to(root)} … {m.group(0)[:70]}")

    # 自包含 exe 的额外提示：它**不是一个"更安全的 .pyc"**。
    # PyInstaller onefile 把我们的 .py 编译成 .pyc 后压进 PKG 归档，任何人都能用
    # pyinstxtractor 解出来、再用 decompyle 之类还原个七七八八。这里明说一句，
    # 免得"打成了一个文件"被误读成"源码保护好了"——真要保护得走 Nuitka（见 docs/PACKAGING.md）。
    if (root / "ModAssets" / EXE_NAME).is_file() or (root / EXE_NAME).is_file():
        log(f"⚠ {EXE_NAME} 是 PyInstaller 产物：**能防小白、不防有心人**"
            f"（pyinstxtractor 可解出 .pyc）。需要真源码保护请改用 Nuitka。")

    # config.json 必须存在且 api_key 为空。
    # **两种布局都要认**：root 布局在 <Mod根>/config.json，modassets 布局在
    # <Mod根>/ModAssets/config.json（前者是手工拼装，后者是官方管线）。
    # 第一版只查了 root 布局，加了 --layout modassets 之后直接误报"缺失"。
    cfg = next((c for c in (root / "config.json", root / "ModAssets" / "config.json") if c.is_file()), None)
    if cfg is None:
        hard.append("  config.json 缺失（两种布局都没找到；玩家侧会走全默认值）")
    else:
        try:
            key = (json.loads(cfg.read_text(encoding="utf-8-sig")).get("llm") or {}).get("api_key")
            if key:
                hard.append(f"  {cfg.relative_to(root)} 里带着 api_key（{str(key)[:8]}…）—— 绝不能发出去")
        except Exception:  # noqa: BLE001
            hard.append(f"  {cfg.relative_to(root)} 不是合法 json")

    if soft:
        if allow_dev_paths:
            print("[pack] ⚠ 开发机路径命中（按 --allow-dev-paths 降级为警告）：")
            print("\n".join(soft[:20]))
        else:
            hard += soft

    if hard:
        print("[pack] ✗ 泄漏扫描未通过：", file=sys.stderr)
        print("\n".join(hard[:40]), file=sys.stderr)
        if any("DLL" in h or ".dll" in h for h in soft) and not allow_dev_paths:
            print("\n  提示：Debug 版 DLL 会把 PDB 绝对路径写进 PE，请用 "
                  "`--config Release`（或 `dotnet build -c Release`）后重新打包。", file=sys.stderr)
        die("已中止打包（这是刻意的硬闸；确属误报再往 LEAK_* 里加白名单，别整个关掉）")
    log("泄漏扫描通过（无密钥 / 无开发机路径 / 未打包作者自己的 config.json）")


def install_to_project(root: Path, project: Path, layout: str) -> None:
    """把打包好的东西铺进**模组编辑器工程的标准槽位**，之后点「导出模组」即得完整包。

    槽位对照（依据：`Mod/modFQA/代码编写教程/代码编写教程.docx` 与导出产物实测）：
        ModAssets/          ← 官方「自带文件」槽位，**导出时逐字节原样带走**（已 md5 实证）。
                              运行时、提示词、配置模板都放这儿。
        ModCode/dll/        ← 编译产物槽位。官方文档说「进入游戏调试」会自动复制
                              `ModCode/ModMain/bin/Release/` 下的 DLL；这个 `dll/` 子目录
                              是否被「导出模组」带走**尚未实证**，故铺了之后再人工核对一次。
        ModRes/AssetBundle/ ← AB 槽位，同样待实证。

    已知代价：`ModAssets/` 会变成几十 MB（Python 运行时），导出时会被整体复制一遍。
    """
    if not project.is_dir():
        die(f"编辑器工程目录不存在：{project}（用 --project-dir 指定）")
    dst_assets = project / "ModAssets"
    dst_assets.mkdir(parents=True, exist_ok=True)

    # ① ModAssets：运行时 + 用户数据（官方管线唯一保证会被带走的地方）
    src_data = root / "ModAssets" if layout == LAYOUT_MODASSETS else root
    src_exe = src_data / EXE_NAME
    if src_exe.is_file():
        # 自包含 exe 形态：一个文件顶掉整个 RUNTIME_DIR 目录树。
        # 两种形态**互斥**——同时留一份会把包撑大一倍，而且 C# 优先跑 exe，
        # 那份文件夹版就成了纯垃圾（还可能让人误以为改它有用）。故顺手清掉旧的。
        stale = dst_assets / RUNTIME_DIR
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
            log(f"✗ 已删除旧的 {stale.relative_to(project)}（文件夹版运行时，被 exe 取代）")
        shutil.copy2(src_exe, dst_assets / EXE_NAME)
        log(f"→ {(dst_assets / EXE_NAME).relative_to(project)}（{src_exe.stat().st_size / 1e6:.1f} MB）")
    for name in (RUNTIME_DIR, "prompts"):
        src = src_data / name
        if not src.exists():
            continue
        t = dst_assets / name
        if t.exists():
            shutil.rmtree(t, ignore_errors=True)
        shutil.copytree(src, t)
        log(f"→ {t.relative_to(project)}")
    # 用户数据 + 合规文件：config / 提示词 / 开源许可，逐个平铺
    for name in ("config.json", "开源许可.txt"):
        if (src_data / name).is_file():
            shutil.copy2(src_data / name, dst_assets / name)
            log(f"→ {(dst_assets / name).relative_to(project)}")

    # ② ModCode/dll：编译产物
    src_dll = root / "ModCode" / "dll"
    if src_dll.is_dir():
        t = project / "ModCode" / "dll"
        t.mkdir(parents=True, exist_ok=True)
        # 只放需要的 DLL —— 官方文档明写「不要有多余的DLL」，多一个就可能让 mod 失效
        for f in sorted(src_dll.glob("*.dll")):
            shutil.copy2(f, t / f.name)
            log(f"→ {(t / f.name).relative_to(project)}")
            # **同一个 DLL 也铺一份到编辑器模板工程的输出目录**（`ModCode/ModMain/bin/Release/`）。
            # 为什么（发现，本轮唯一还没实证的事）：
            #   编辑器工程里原本就有官方生成的 C# 工程 `ModCode/ModMain/`（`ModMain.csproj` +
            #   `ModMain.sln` + 模板 `ModMain.cs`），而它的 `<AssemblyName>` 也是 **MOD_Jgmg5L**
            #   —— 与我们同名。官方教程的原话是「重新生成DLL，确保 `bin/Release` 目录下成功生成了
            #   `MOD_XXXXXX.dll`」，也就是说编辑器/VS 认的是**那个**位置。
            #   而「导出模组」到底搬 `ModCode/dll/` 还是搬 `ModCode/ModMain/bin/Release/`，
            #   官方文档没写、也还没实测（神识传音的成品包用的是 `ModCode/dll/`，故两处都铺，
            #   等用户点一次「导出模组」看产物就能定下来是哪一边）。
            # 副作用提醒：若在 VS 里构建 `ModMain.sln`，模板 stub 会把这里的同名 DLL 覆盖掉。
            tpl_out = project / "ModCode" / "ModMain" / "bin" / "Release"
            tpl_out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, tpl_out / f.name)
            log(f"→ {(tpl_out / f.name).relative_to(project)}（同名副本，见注释）")

    # ③ ModRes/AssetBundle：AB 资源
    src_ab = root / "ModRes" / "AssetBundle"
    if src_ab.is_dir():
        t = project / "ModRes" / "AssetBundle"
        if t.exists():
            shutil.rmtree(t, ignore_errors=True)
        shutil.copytree(src_ab, t)
        log(f"→ {t.relative_to(project)}（{sum(1 for _ in t.rglob('*.ab'))} 个 .ab）")

    # ④ 安装说明.txt：**必须落在编辑器工程根**，不能只落在游戏目录。
    #    原因（发布审计）：`deploy` 是"装进游戏目录"那一步，只有走它才写文件；
    #    而发布走的是「编辑器 → 导出模组」这条线，导出源是编辑器工程。之前只写游戏目录，
    #    于是「导出到干净目录」时说明文件凭空消失 —— 上一版靠手工拷贝掩盖了这个缺口。
    install_doc = root / "安装说明.txt"
    if install_doc.is_file():
        shutil.copy2(install_doc, project / "安装说明.txt")
        log("→ 安装说明.txt（编辑器工程根；导出时随包走）")

    log("已铺进编辑器工程。下一步：打开模组编辑器 → 点「导出模组」→ 核对产物里有没有 "
        "ModAssets/AgentLoopServer.exe 与 ModCode/dll/MOD_Jgmg5L.dll（后者是本次唯一未实证项）")


# ---------------- 6) 产出 ----------------

def make_zip(root: Path, out_dir: Path, tag: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"{MOD_DIR_NAME}-{tag}.zip"
    if zpath.exists():
        zpath.unlink()
    log(f"打包 {zpath.name}")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(root.parent))
    log(f"完成：{zpath}（{zpath.stat().st_size / 1e6:.1f} MB）")
    return zpath


def deploy(root: Path, game_root: Path) -> None:
    """整包覆盖式安装到游戏目录（仅覆盖本 mod 自己的目录）。

    **两种布局都认**（与 install_to_project / scan_leaks 同一口径）：
      · root 布局     数据与运行时在 <Mod根>\\ 下
      · modassets 布局 数据与运行时在 <Mod根>\\ModAssets\\ 下（官方管线，现在的发行形态）
    """
    dst = game_root / "ModExportData" / MOD_DIR_NAME
    if not dst.is_dir():
        die(f"目标不存在：{dst}（先在游戏里启用一次本 mod 以生成目录）")

    # ① 编辑器管的三个槽位：整目录覆盖
    for name in ("ModCode", "ModRes", "ModExcel"):
        t = dst / name
        if t.exists():
            shutil.rmtree(t, ignore_errors=True)
        src = root / name
        if src.exists():
            shutil.copytree(src, t)

    # ② 数据与运行时：按**源布局**落到对应位置
    src_assets = root / "ModAssets"
    nested = src_assets.is_dir()
    src_data = src_assets if nested else root
    dst_data = (dst / "ModAssets") if nested else dst
    dst_data.mkdir(parents=True, exist_ok=True)

    # exe 形态：先清掉旧的文件夹版运行时（两种形态互斥，见 install_to_project 的同一段）
    if (src_data / EXE_NAME).is_file():
        stale = dst_data / RUNTIME_DIR
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
            log(f"已删除 {stale}（旧的文件夹版运行时）")
        shutil.copy2(src_data / EXE_NAME, dst_data / EXE_NAME)
        log(f"已部署 {EXE_NAME}（{(dst_data / EXE_NAME).stat().st_size / 1e6:.1f} MB）")
    for name in (RUNTIME_DIR, "prompts"):
        t = dst_data / name
        if t.exists():
            shutil.rmtree(t, ignore_errors=True)
        src = src_data / name
        if src.exists():
            shutil.copytree(src, t)

    # ③ config.json：**绝不覆盖玩家已填的 key**。
    # 新旧布局切换时多一步「搬家」：老部署把 key 放在 <Mod根>\config.json，
    # 新布局认的是 <Mod根>\ModAssets\config.json —— 直接铺空模板 = 作者的 key 凭空消失、
    # AI 退回复读，而且极难联想到是部署换层导致的。故把老那份搬过去而不是丢掉。
    cfg_dst = dst_data / "config.json"
    old_cfg = (dst / "config.json") if nested else None
    if cfg_dst.is_file():
        log("保留目标已有的 config.json（不覆盖玩家的 key）")
    elif old_cfg is not None and old_cfg.is_file():
        shutil.copy2(old_cfg, cfg_dst)
        log(f"已把旧的 {old_cfg.relative_to(dst)} 搬到 ModAssets/config.json（保住里面填的 key）")
    else:
        shutil.copy2(src_data / "config.json", cfg_dst)
        log("已铺 config.json（目标原本没有）")

    # 许可合规文件：静态文本，跟着版本走，直接覆盖
    src_lic = src_data / "开源许可.txt"
    if src_lic.is_file():
        shutil.copy2(src_lic, dst_data / "开源许可.txt")
        log(f"已铺 {dst_data.name}/开源许可.txt")

    # ④ mod 清单同理：目标已有就不动（它是同一个文件，但没必要冒险覆盖一个能跑的安装）
    for name in ("ModExportData.cache", "ModProjectPreview.png"):
        src_f = root / name
        if src_f.is_file() and not (dst / name).is_file():
            shutil.copy2(src_f, dst / name)
            log(f"已铺 {name}（目标原本没有）")

    # ⑤ 清掉**被新布局取代的旧布局残留**（只在 modassets 布局下做）。
    # 为什么必须清：DataRoot 一旦认了 ModAssets/，根层那几份就成了"看着像配置、其实没人读"的
    # 死文件 —— 用户改它、发现没反应，然后来报"配置不生效"。这比多占 37MB 严重得多。
    # 唯一留手的是根层 config.json：万一两份内容不同（比如里面还留着没搬走的 key），
    # 改名保底而不是直接删。
    if nested:
        for name in (RUNTIME_DIR, "prompts"):
            stale = dst / name
            if stale.exists():
                shutil.rmtree(stale, ignore_errors=True)
                log(f"已删除旧布局残留 {name}/（已被 ModAssets/{name} 取代）")
        old_root_cfg = dst / "config.json"
        if old_root_cfg.is_file():
            try:
                same = md5(old_root_cfg) == md5(cfg_dst)
            except Exception:  # noqa: BLE001
                same = False
            if same:
                old_root_cfg.unlink()
                log("已删除旧布局残留 config.json（内容与 ModAssets/config.json 相同）")
            else:
                keep = dst / "config.json.old-layout"
                if keep.exists():
                    keep.unlink()
                old_root_cfg.rename(keep)
                log(f"⚠ 根层 config.json 与 ModAssets/config.json 内容不同，已改名为 {keep.name}"
                    f"（游戏只读 ModAssets 那份；确认没用的 key 再删它）")

    shutil.copy2(root / "安装说明.txt", dst / "安装说明.txt")
    dll = dst / "ModCode" / "dll" / "MOD_Jgmg5L.dll"
    log(f"已部署 → {dst}（DLL md5={md5(dll)}）")
    if (dst / DEV_ROOT_SENTINEL).is_file():
        log(f"⚠ 注意：{DEV_ROOT_SENTINEL} 存在 —— 游戏会跑**源码树**的活代码，"
            f"而不是刚部署的这份包。要验真实发包效果，先把它删掉/改名。")


def deploy_dev(game_root: Path) -> None:
    """**只推我们的 Python 代码**到已部署的运行时里 —— 秒级，供日常迭代。

    为什么需要它（方案 A 的直接后果）：
      C# 现在按 DLL 位置找 Python，所以游戏跑的是 `<Mod根>/AgentLoop/` 里**那份副本**，
      不再是 `F:\agent_loop` 的活源码。改一行 Python 若要走完整 `--deploy`，
      每次都要重下运行时/装轮子/重打 22MB 包 —— 迭代成本高到没人愿意用。
      本命令只做「清掉旧 agent_loop/ → 拷新的 agent_loop/ + server.py」，约 1 秒。
      改完在游戏里让 Python 重启（配置面板保存任意项即可触发重启编排）就生效。

    前提：先跑过一次完整 `--deploy`（便携 Python 运行时与依赖已就位）。
    **exe 形态下本命令不适用**：代码在 exe 里面，没有源码可推（会给明确指引而不是静默失败）。
    """
    mod_root = game_root / "ModExportData" / MOD_DIR_NAME
    for cand in (mod_root / "ModAssets" / EXE_NAME, mod_root / EXE_NAME):
        if cand.is_file():
            die(f"当前部署是**自包含 exe** 形态（{cand}），没有源码可推。\n"
                f"      · 改完 Python 要验打包形态 → 重建并重部署：\n"
                f"          python3 scripts/pack_release.py --build-exe --layout modassets --deploy\n"
                f"      · 想秒级迭代 → 走**源码树模式**（游戏直接跑活代码，不用打包）：\n"
                f"          把 exe 改名/移开，并在 {mod_root} 下放一个 {DEV_ROOT_SENTINEL}，\n"
                f"          内容一行 = {REPO}")
    dst_rt = mod_root / RUNTIME_DIR
    if not (dst_rt / "python.exe").is_file():
        die(f"目标运行时不存在：{dst_rt / 'python.exe'}\n"
            f"      先跑一次完整部署：python3 scripts/pack_release.py --deploy")
    dst_pkg = dst_rt / "agent_loop"
    if dst_pkg.exists():
        shutil.rmtree(dst_pkg)          # 整体换掉：删过的模块不会残留成幽灵
    n = 0
    for src in iter_package_files():
        d = dst_pkg / src.relative_to(REPO)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d)
        n += 1
    shutil.copy2(REPO / "scripts" / "server.py", dst_rt / "server.py")
    for cache in list(dst_rt.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)   # 免得旧 .pyc 盖住新源码
    log(f"开发部署完成：{n} 个文件 → {dst_pkg}（.py 明文，未做保护）")
    log("改完在游戏里打开配置面板（对话窗 ⚙）保存任意一项触发 Python 重启即生效")


# ---------------- main ----------------

def main() -> None:
    ap = argparse.ArgumentParser(description="打包可发行的 agent_loop mod")
    ap.add_argument("--exe", default=str(EXE_OUT),
                    help=f"自包含入口 exe 的路径（默认取上次构建产物 {EXE_OUT}）")
    ap.add_argument("--build-exe", action="store_true",
                    help="先用 PyInstaller 重新构建 exe，再铺包（约 16 秒）")
    ap.add_argument("--portable", action="store_true",
                    help="回退到旧的**便携 CPython + 源码树**形态（不再默认；仅兼容老部署）")
    ap.add_argument("--python-version", default=DEFAULT_PY, help=f"便携 CPython 版本（--portable 用；默认 {DEFAULT_PY}）")
    ap.add_argument("--protect", choices=["pyc", "none"], default="pyc",
                    help="--portable 形态的源码保护：pyc=只发字节码（默认）；none=连 .py 一起发")
    ap.add_argument("--config", choices=["Debug", "Release"], default="Release",
                    help="取哪个配置的 DLL（默认 Release：只有它不把 PDB 绝对路径写进 PE）")
    ap.add_argument("--layout", choices=[LAYOUT_ROOT, LAYOUT_MODASSETS], default=LAYOUT_MODASSETS,
                    help="数据/运行时的落点（默认 modassets = <Mod根>/ModAssets，**官方管线**，"
                         "编辑器「导出模组」会整个带走）；root = <Mod根> 根层，手工拼装/便携版用")
    ap.add_argument("--install-to-project", action="store_true",
                    help="把 DLL / AB / Python 端铺进模组编辑器工程的标准槽位，"
                         "之后在编辑器里点「导出模组」即得完整包")
    ap.add_argument("--project-dir", default=os.environ.get("GGBH_MOD_PROJECT_DIR", ""),
                    help="编辑器工程目录（--install-to-project 用；也可用 GGBH_MOD_PROJECT_DIR）")
    ap.add_argument("--excel-project", default=os.environ.get("GGBH_MOD_PROJECT", ""),
                    help="模组编辑器工程目录（查「改了 xlsx 没重新导出」；也可用 GGBH_MOD_PROJECT）")
    ap.add_argument("--allow-stale-config", action="store_true",
                    help="配置表比 cache 新时仍然打包（默认中止）")
    ap.add_argument("--allow-dev-paths", action="store_true",
                    help="把「开发机路径」类命中降级为警告（只给自己迭代用，别带这个开关发包）")
    ap.add_argument("--game-root", default=os.environ.get("GGBH_GAME_ROOT", ""),
                    help="鬼谷八荒安装目录（取 ModRes/ModExcel 用；也可用环境变量 GGBH_GAME_ROOT）")
    ap.add_argument("--out", default=str(REPO / "dist"), help="产物目录（默认只放 --zip 的产物）")
    ap.add_argument("--stage", default=str(BUILD_DIR / "stage"),
                    help="中间产物目录（默认在仓库之外，见 BUILD_DIR 注释）")
    # zip 是**传输容器**，不是发行形态：走创意工坊流程（编辑器「导出模组」→ 上传）根本用不到它，
    # 走「文件拷贝分发」也是拷目录。所以默认不压；要给人发单个文件时才 --zip。
    ap.add_argument("--zip", action="store_true", help="额外压一个 zip（默认不压；只在需要单文件传输时用）")
    ap.add_argument("--no-zip", action="store_true", help=argparse.SUPPRESS)  # 旧开关，现在是默认行为
    ap.add_argument("--deploy", action="store_true", help="打包后直接部署进游戏目录")
    ap.add_argument("--from-export", default="", metavar="DIR",
                    help="吃编辑器「导出模组」的产物：逐项核对完整性，并装进游戏目录"
                         "（测的才是玩家真拿到的那份；配合 --check-only 则只看不装）")
    ap.add_argument("--check-only", action="store_true",
                    help="配合 --from-export：只核对，不安装")
    ap.add_argument("--deploy-dev", action="store_true",
                    help="只推 Python 代码到已部署的运行时（秒级，日常迭代用；需先 --deploy 一次）")
    ap.add_argument("--skip-deps", action="store_true", help="跳过依赖安装（复用上次的 site-packages）")
    args = ap.parse_args()

    game_root = norm_path(args.game_root) if args.game_root else guess_game_root()

    # 最前头的独立入口：吃「导出模组」的产物（不打包、不铺工程，只核对 + 装游戏）
    if args.from_export:
        export_dir = norm_path(args.from_export)
        if not export_dir.is_dir():
            die(f"导出目录不存在：{export_dir}")
        # 容错：给的是**父目录**（如 F:\modtest）时，自动挑里面最新的那次导出。
        # 编辑器每次导出都新建 ModExportData_<时间戳>\Mod_Jgmg5L\，手抄那串时间戳太容易抄错。
        if not (export_dir / "ModExportData.cache").is_file():
            cands = sorted((d for d in export_dir.glob("ModExportData_*")
                            if (d / MOD_DIR_NAME / "ModExportData.cache").is_file()),
                           key=lambda d: d.name)
            if cands:
                export_dir = cands[-1] / MOD_DIR_NAME
                log(f"（在父目录里自动挑到最新一次导出：{export_dir}）")
            elif (export_dir / MOD_DIR_NAME).is_dir():
                export_dir = export_dir / MOD_DIR_NAME
        ok = inspect_export(export_dir)
        if args.check_only:
            return
        if not game_root:
            die("--from-export 装进游戏需要 --game-root 或 GGBH_GAME_ROOT")
        install_export(export_dir, game_root)
        if not ok:
            print("[pack] ⚠ 产物有缺项（上面标 XX 的）—— 已照样装进游戏，正好用来看缺了会怎样",
                  file=sys.stderr)
        return

    # 开发快车道：不碰运行时/依赖/压缩，只把代码推进去（秒级返回）
    if args.deploy_dev:
        if not game_root:
            die("--deploy-dev 需要 --game-root 或 GGBH_GAME_ROOT")
        deploy_dev(game_root)
        return

    use_exe = not args.portable
    log(f"仓库   = {REPO}")
    log(f"游戏   = {game_root or '(未指定，AB/ModExcel 会缺失)'}")
    log("形态   = " + (f"自包含 exe（{EXE_NAME}）" if use_exe
                        else f"便携 CPython {args.python_version} + 源码树，保护={args.protect}"))
    log(f"布局   = {args.layout}"
        + ("（运行时+配置放 ModAssets/，供编辑器导出带走）" if args.layout == LAYOUT_MODASSETS else ""))

    # 编辑器工程目录提前解析：AB 同步与「AB 从哪读」都要用它
    proj = norm_path(args.project_dir) if args.project_dir else guess_mod_project()

    # AB 三处槽位对齐（Unity 工作区 / 官方查找路径 / 发行槽位），以最新的一份为准。
    # 放在最前面：后面无论「取 AB 进包」还是「铺进工程」，读到的都是同一份最新的。
    if proj:
        sync_ab(proj, game_root)
    else:
        log("AB 同步：未找到编辑器工程目录（用 --project-dir 指定），跳过")

    stage = Path(args.stage)
    # 硬拒「暂存树放进仓库」：仓库根**就是 Python 包本身**，往里塞一棵含 1200+ 第三方 .py
    # 的暂存树，会打挂 tests/test_log_setup.py 的架构哨兵（它 rglob 整个包、要求除 log_setup
    # 外没人自行配置 logging，而 site-packages 里的 openai/httpx/distro 当然会配）。
    # 与其让那个哨兵去容忍构建垃圾，不如在这里挡下来 —— 不变量是「包里没有构建产物」。
    try:
        stage.resolve().relative_to(REPO.resolve())
        die(f"--stage 不能指向仓库内部（{stage}）。仓库根即 Python 包，塞进去会污染包内容。"
            f"请用默认值（{BUILD_DIR / 'stage'}）或仓库外的路径。")
    except ValueError:
        pass    # 在仓库外，正常
    if stage.exists():
        shutil.rmtree(stage)
    root = stage / MOD_DIR_NAME
    # 两种布局：modassets 把「运行时 + 用户数据」一起塞进官方槽位 ModAssets/，
    # 这样编辑器「导出模组」会整个带走（md5 实证：ModAssets 是逐字节原样复制）。
    data_dir = root / "ModAssets" if args.layout == LAYOUT_MODASSETS else root
    data_dir.mkdir(parents=True, exist_ok=True)

    if use_exe:
        # 现在的发行形态：一个 ~15MB 的自包含 exe 顶掉「便携 CPython + 我们的代码 + 第三方依赖」三件套
        exe_src = build_exe() if args.build_exe else norm_path(args.exe)
        stage_exe(data_dir, exe_src)
    else:
        rt = data_dir / RUNTIME_DIR
        rt.mkdir(parents=True)
        fetch_runtime(args.python_version, rt)
        if not args.skip_deps:
            install_deps_impl(rt, args.python_version, REPO / "requirements.txt")
        else:
            log("跳过依赖安装（--skip-deps）")
        stage_code(rt)
        precompile_deps(rt)
        if args.protect == "pyc":
            protect_pyc(rt)

    stage_dll(root, args.config)
    if game_root:
        stage_game_assets(root, game_root, proj)
        check_config_freshness(game_root / "ModExportData" / MOD_DIR_NAME,
                               norm_path(args.excel_project) if args.excel_project else guess_mod_project(),
                               args.allow_stale_config)
    stage_userdata(root, data_dir)
    # 许可合规：PSF/Apache 都要求版权声明随二进制分发保留（见函数注释）
    stage_licenses(data_dir, None if use_exe else data_dir / RUNTIME_DIR)

    scan_leaks(root, allow_dev_paths=args.allow_dev_paths)

    if args.zip:
        make_zip(root, Path(args.out),
                 "exe" if use_exe else f"cp{''.join(args.python_version.split('.')[:2])}-{args.protect}")
    if args.install_to_project:
        if not proj:
            die("--install-to-project 需要 --project-dir 或 GGBH_MOD_PROJECT_DIR")
        install_to_project(root, proj, args.layout)
    if args.deploy:
        if not game_root:
            die("--deploy 需要 --game-root 或 GGBH_GAME_ROOT")
        deploy(root, game_root)
    log(f"中间产物：{root}")


def norm_path(s: str) -> "Path | None":
    """把用户给的游戏目录统一成本机可用的 Path。

    本脚本通常在 **WSL** 里跑（pip download 需要 Linux 侧的 pip，且发包是开发机动作），
    但游戏装在 Windows 盘上，于是同一个目录有两种写法：
        Windows 形式  E:\\SteamLibrary\\steamapps\\common\\鬼谷八荒
        WSL 形式      /mnt/e/SteamLibrary/steamapps/common/鬼谷八荒
    两种都接受并自动翻译，免得每次都要记得自己在哪一侧。
    """
    if not s:
        return None
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if m and os.name != "nt":
        tail = m.group(2).replace("\\", "/")
        return Path(f"/mnt/{m.group(1).lower()}/{tail}")
    return Path(s)


def guess_mod_project() -> "Path | None":
    """猜模组编辑器工程目录（只用于「配置表是否比 cache 新」这道检查；找不到就跳过）。"""
    for c in ["/mnt/f/mod/ModProject_Jgmg5L/ModProject",
              "/mnt/d/mod/ModProject_Jgmg5L/ModProject",
              "/mnt/e/mod/ModProject_Jgmg5L/ModProject"]:
        if Path(c).is_dir():
            return Path(c)
    return None


def guess_game_root() -> "Path | None":
    """猜游戏目录：环境变量 → Steam 各库常见位置。"""
    env = norm_path(os.environ.get("GGBH_GAME_ROOT", ""))
    if env and env.is_dir():
        return env
    drives = ["e", "d", "f", "c"]
    subs = [
        "SteamLibrary/steamapps/common/鬼谷八荒",
        "Steam/steamapps/common/鬼谷八荒",
        "Program Files (x86)/Steam/steamapps/common/鬼谷八荒",
    ]
    for d in drives:
        for sub in subs:
            p = Path(f"/mnt/{d}/{sub}") if os.name != "nt" else Path(f"{d.upper()}:\\" + sub.replace("/", "\\"))
            if p.is_dir():
                return p
    return None


if __name__ == "__main__":
    main()
