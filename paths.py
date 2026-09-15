"""paths — 「用户数据根」的唯一解析口（config.json / prompts / logs 都锚它）。

为什么要有这个模块（2026-09-13 打包审计）：
    开发机布局里「包目录」与「数据目录」是同一层（`<源码树>/config_loader.py`
    与 `<源码树>/config.json` 并排），所以各模块直接用 `Path(__file__).parent`
    找 config/prompts/logs —— 在源码树里完全正确，一旦打包成发行版就全错：
    包会躺在 `<Mod根>\\AgentLoop\\agent_loop\\`，而 config.json / prompts 必须在
    `<Mod根>\\`（用户看得见、改得动、升级代码不覆盖）。

契约（与 C# 侧 `ModPaths.EnvDataRoot` 同源，勿单方面改名）：
    C# 拉起 Python 时下发环境变量 `AGENT_LOOP_DATA=<Mod根>`；
    本模块据此返回数据根。**未下发时退回包目录**——即开发机与全部单测的现有行为，
    一字不变（`python scripts/server.py`、`pytest` 都不需要设任何环境变量）。

各司其职：本模块只回答「目录在哪」，不读文件、不懂配置、没有任何第三方依赖
（所以 log_setup 也能安心 import 它）。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 数据根环境变量名（= C# ModPaths.EnvDataRoot）
ENV_DATA_ROOT = "AGENT_LOOP_DATA"

#: 本包所在目录（.../agent_loop/）。开发机 == 数据目录；发行版 == 代码目录。
PKG_DIR = Path(__file__).resolve().parent

#: 源码树根。开发机布局是 `<树根>/agent_loop/`（包是树根的子目录），
#: 故取包目录的父目录即为树根；发行版布局下这个值无意义，仅作最后的兜底候选。
TREE_ROOT = PKG_DIR.parent


def data_root() -> Path:
    """用户数据根：`AGENT_LOOP_DATA` 优先，未下发则用包目录本身。

    返回绝对路径。环境变量指向不存在的目录时**不抛错**——退回包目录并让上层的
    「文件不存在」逻辑正常降级（配置读不到有代码内默认值，绝不因路径问题崩启动）。
    """
    raw = os.environ.get(ENV_DATA_ROOT)
    if raw:
        try:
            p = Path(raw).expanduser()
            if p.is_dir():
                return p.resolve()
        except Exception:
            pass
    return PKG_DIR


def config_candidates() -> tuple:
    """config.json 探测候选（按优先级）。

    ① `<数据根>/config.json` —— 开发机 == 包目录（现状不变）；发行版 == Mod 根（用户可编辑）
    ② `<包目录>/config.json` —— 发行版里随包内置的默认配置（数据根被清掉时的兜底）
    ③ `<cwd>/config.json`    —— 历史行为兜底（手动在别处起 server 时）
    去重且保持顺序。
    """
    seen = []
    for p in (data_root() / "config.json", PKG_DIR / "config.json", Path(os.getcwd()) / "config.json"):
        if p not in seen:
            seen.append(p)
    return tuple(seen)


def prompts_root() -> Path:
    """提示词根目录。

    优先 `<数据根>/prompts`（发行版：用户可编辑、升级代码不覆盖）；
    不存在则退回 `<包目录>/prompts`（随包内置的默认提示词 / 开发机现状——两者同路径）。
    """
    user = data_root() / "prompts"
    if user.is_dir():
        return user
    return PKG_DIR / "prompts"


def logs_root() -> Path:
    """日志根目录 = `<数据根>/logs`。

    开发机数据根 == 包目录 → 仍是 `<包目录>/logs`（现状不变）；
    发行版 → `<Mod根>/logs`，用户看得见、能打包发给作者排障。
    """
    return data_root() / "logs"
