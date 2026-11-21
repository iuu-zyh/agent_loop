# -*- coding: utf-8 -*-
"""test_python_layout_contract — Python 包目录布局契约。

2026-09-21 目录整理的"防回潮"闸门：
- 包根只许 3 个 .py（身份/导出、装配管家、横切日志设施），新模块一律进域子包；
- 10 个域子包必须齐全，且 pack_release / export_public 两份白名单必须跟上；
- llm_adapter 必须住在 llm/ 内被三个 client 同包引用，不许再出现跨包反向依赖。

这些契约按"文本路径"耦合（与 run_tests_for.py 的契约测试家族同一风格），
不依赖 import —— 故意如此：布局本身就是要被钉死的对象。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- 期望布局

#: 包根白名单：身份/导出、装配管家（对应 csharp 的 ModMain.cs 留根先例）、横切日志设施
ROOT_FILES = {"__init__.py", "agent_loop.py", "log_setup.py"}

#: 域子包清单（新 7 个 + 既有 3 个）
DOMAINS = ["config", "storage", "session", "agent", "room", "transport", "llm",
           "tools", "compaction", "prompts"]

#: 模块 → 域（整理后的唯一住所）
HOME = {
    "paths.py": "config", "textio.py": "config",
    "config_loader.py": "config", "config_store.py": "config",
    "persistence.py": "storage", "contacts_store.py": "storage", "unit_id_map.py": "storage",
    "session.py": "session", "inbox.py": "session", "history.py": "session",
    "history_ops.py": "session", "stats.py": "session",
    "dialogue_agent.py": "agent", "system_prompt.py": "agent", "initiative.py": "agent",
    "room.py": "room", "room_flow.py": "room", "room_history.py": "room",
    "ws_channel.py": "transport", "bridge.py": "transport",
    "llm_adapter.py": "llm", "prompt_files.py": "prompts",
}

#: 整理前旧相对导入形态 → 允许出现的域（同包引用合法；出现在别的域就是"跨包写成了同包形式"）
STALE = {
    "from .persistence import": {"storage"},
    "from .session import": {"session"},
    "from .inbox import": {"session"},
    "from .history import": {"session"},
    "from .history_ops import": {"session"},
    "from .stats import": {"session"},
    "from .config_loader import": {"config"},
    "from .room import": {"room"},
    "from .room_flow import": {"room"},
    "from .room_history import": {"room"},
    "from .system_prompt import": {"agent"},
    "from .dialogue_agent import": set(),
    "from .initiative import": {"agent"},
    "from .ws_channel import": {"transport"},
    "from .bridge import": {"transport"},
    "from .unit_id_map import": {"storage"},
    "from .contacts_store import": {"storage"},
    "from .paths import": {"config"},
    "from .textio import": {"config"},
    "from .llm import think": set(),
    "from .llm_adapter import": set(),
}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 包根

def test_package_root_only_has_the_three_residents():
    """包根平铺不许回潮：只许 3 件常驻，新模块必须进域目录。"""
    actual = {p.name for p in ROOT.glob("*.py")}
    intruders = actual - ROOT_FILES
    missing = ROOT_FILES - actual
    assert not intruders, f"包根出现平铺模块 {sorted(intruders)} —— 请放进对应域子包，别回到 2026-09-21 之前的平铺状态"
    assert not missing, f"包根常驻 3 件缺失：{sorted(missing)}"


def test_every_module_lives_in_its_domain():
    """23 个被整理的模块必须各就各位，且旧位置（包根）不再存在。"""
    for name, dom in HOME.items():
        assert (ROOT / dom / name).is_file(), f"{name} 应住在 {dom}/ 下"
        assert not (ROOT / name).exists(), f"{name} 在包根还有残留副本（会 shadow 域内正版）"


def test_domain_packages_exist_with_docstring_init():
    """10 个域子包必须存在且 __init__ 有说明。除 llm/tools/compaction（有 re-export）外，
    新 7 个的 __init__ 刻意保持空 re-export（防 log_setup → config_store → log_setup 环）。"""
    for dom in DOMAINS:
        init = ROOT / dom / "__init__.py"
        assert init.is_file(), f"域子包 {dom}/ 缺 __init__.py"
        assert init.read_text(encoding="utf-8").strip(), f"{dom}/__init__.py 是空的，补一行职责说明"


# ---------------------------------------------------------------- 关键依赖关系

def test_llm_adapter_is_same_package_for_all_clients():
    """三个 client 必须同包引用 llm_adapter，不许回退成 `from .. import llm_adapter` 跨包反向依赖。"""
    for client in ("anthropic_client.py", "openai_client.py", "responses_client.py"):
        body = _read(f"llm/{client}")
        assert "from . import llm_adapter" in body, f"llm/{client} 没有同包引用 llm_adapter"
        assert "from .. import llm_adapter" not in body, (
            f"llm/{client} 出现 `from .. import llm_adapter` —— llm_adapter 已归入 llm/，"
            "这是目录整理前跨包反向依赖的旧形态")
    assert (ROOT / "llm" / "llm_adapter.py").is_file()
    assert not (ROOT / "llm_adapter.py").exists()


def test_no_stale_intra_package_relative_imports():
    """包内不许残留整理前的旧相对导入形态：跨包关系写成了同包形式（指向不存在的东西）。"""
    offenders = []
    for dom in DOMAINS:
        for py in (ROOT / dom).rglob("*.py"):
            if py.name == "__init__.py":
                continue
            body = py.read_text(encoding="utf-8")
            for pat, allowed in STALE.items():
                if pat in body and dom not in allowed:
                    offenders.append(f"{py.relative_to(ROOT)}: {pat}")
    assert not offenders, "残留旧相对导入（跨包关系写成了同包形式）:\n" + "\n".join(offenders)


# ---------------------------------------------------------------- 白名单同步（防漏登记）

def test_pack_release_covers_all_domains():
    """pack_release 的子包清单必须覆盖全部域 —— 漏一个，玩家机上就 ImportError 一个。"""
    body = _read("scripts/pack_release.py")
    m = re.search(r"for sub in \(([^)]*)\)", body, re.S)
    assert m, "pack_release.py 里找不到子包清单"
    listed = re.findall(r'"([a-z_]+)"', m.group(1))
    missing = [d for d in DOMAINS if d not in listed]
    assert not missing, f"pack_release.py 子包白名单缺 {missing}"


def test_export_public_covers_all_domains_and_three_root_files():
    """export_public 的 INCLUDE_TOP 必须覆盖全部域 + 包根 3 件。"""
    body = _read("scripts/export_public.py")
    m = re.search(r"INCLUDE_TOP = \[(.*?)\]", body, re.S)
    assert m, "export_public.py 里找不到 INCLUDE_TOP"
    listed = set(re.findall(r'"([^"]+)"', m.group(1)))
    missing = [d for d in DOMAINS if d not in listed]
    assert not missing, f"export_public.py 白名单缺域目录 {missing}"
    for f in ROOT_FILES:
        assert f in listed, f"export_public.py 白名单缺包根文件 {f}"
