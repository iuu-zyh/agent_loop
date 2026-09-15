# -*- mode: python ; coding: utf-8 -*-
"""AgentLoopServer.spec —— 把 Python 侧打成一个自包含 exe（PyInstaller onefile）。

用法（在一个**干净**的 Windows venv 里，见 docs/PACKAGING.md）：
    pyinstaller --clean --noconfirm ^
        --distpath <构建目录>\\dist --workpath <构建目录>\\build ^
        scripts\\AgentLoopServer.spec
产物：<构建目录>\\dist\\AgentLoopServer.exe

为什么需要 spec 文件（而不是一串命令行参数）：
  本仓库的布局是**「仓库根 == Python 包自身」**（`F:\\agent_loop\\__init__.py`，
  即 import 名是 `agent_loop`）。这是非标准布局，PyInstaller 靠 `pathex` 才能
  把 `import agent_loop` 解析到 `<仓库根>`。而子模块清单又要**显式点名**——
  `collect_submodules('agent_loop')` 会把 `tests/ backup/ reference/` 一起扫进来
  （它们 import pytest，打包环境里根本没装，只会刷一屏 warning 并拖大体积）。
  这两件事都在本文件里一次说清，命令行只管路径。

关于 sys.frozen：
  `scripts/server.py` 的 `_bootstrap_sys_path()` 在 `sys.frozen` 为真时直接返回
  （PyInstaller 已把 `agent_loop` 放进 PYZ，sys.path 由引导器设好），此处无需配合。
"""

import os
import sys
from pathlib import Path

# SPECPATH 由 PyInstaller 注入（= 本 spec 所在目录），不要用 __file__
_HERE = Path(SPECPATH).resolve()          # <仓库根>/scripts
REPO = _HERE.parent                       # <仓库根> == Python 包 agent_loop 自身
PKG_PARENT = REPO.parent                  # 让 `import agent_loop` 能解析的那一层

# ---- 自带数据：prompts/ ----------------------------------------------------
# 只带这一样，且是**兜底**用途：发行形态下 prompts 由 <Mod根>\prompts 提供
# （用户可编辑、升级代码不覆盖，见 paths.prompts_root()）。内置一份的意义是
# 「用户把 prompts 删了/没拷全」时 exe 仍能起来并给出默认人设，而不是崩在启动期。
# **config.json 绝不内置**——那里面是用户的 API key（泄漏闸也会拦）。
datas = [(str(REPO / "prompts"), "agent_loop/prompts")]

# ---- 子模块清单：显式点名 --------------------------------------------------
hiddenimports = []

# ① 包内子包（llm / compaction / tools 各有 __init__.py，是真子包）
for sub in ("llm", "compaction", "tools"):
    hiddenimports.append("agent_loop." + sub)
    d = REPO / sub
    if d.is_dir():
        for f in sorted(d.glob("*.py")):
            if f.name != "__init__.py":
                hiddenimports.append("agent_loop.%s.%s" % (sub, f.stem))

# ② 包根层的平铺模块（仓库根就是包根，故 *.py 直接是 agent_loop.<名>）
for f in sorted(REPO.glob("*.py")):
    if f.name != "__init__.py":
        hiddenimports.append("agent_loop." + f.stem)

# ③ 第三方里「静态分析扫不到」的部分
#    websockets 是懒加载子模块的（顶层 __init__ 不 import 具体实现），
#    不显式收就会「本机侥幸能跑、换台机器 ModuleNotFoundError」。
from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

hiddenimports += collect_submodules("websockets")
# openai 的导入在函数体内（llm/openai_client.py 的懒导入），静态分析其实能找到，
# 但这是「缺了就退化成 Echo 复读」的隐性故障，显式钉死。
hiddenimports += ["openai", "openai.types", "openai.resources"]

# ---- 排除：明确用不到的大件（瘦身；排错了会在启动期就炸，故只排确定项）----
excludes = [
    "tkinter", "pydoc_data", "test", "pytest", "_pytest",
    "numpy", "matplotlib", "scipy", "pandas", "PIL",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "notebook",
    "setuptools", "pip", "wheel",
    # 我们自己的非包目录（仓库根 == 包根，这些若被误当子包扫进来会白拉依赖）
    "agent_loop.tests", "agent_loop.backup", "agent_loop.reference",
    "agent_loop.scripts", "agent_loop.csharp", "agent_loop.dist", "agent_loop.docs",
]

a = Analysis(
    [str(REPO / "scripts" / "server.py")],
    pathex=[str(PKG_PARENT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AgentLoopServer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # upx=False 是**有意的**：UPX 压缩过的自解压 exe 是国内杀软的重点误报特征
    # （360/火绒尤甚），换来的几 MB 体积不值得。见 docs/PACKAGING.md「杀软误报」。
    upx=False,
    runtime_tmpdir=None,
    # console=True 是**必须的**：PyInstaller>=5.7 的 --noconsole 会把
    # sys.stdout/stderr 设成 None，本项目的日志 handler 一写就 AttributeError 崩在启动期。
    # 「玩家不该看见黑框」这件事由 C# 侧 Launcher 的 CreateNoWindow 负责（它才是父进程）。
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
