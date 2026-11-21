"""`csharp/state/` 这个目录的**边界契约**（10-25 立目录时写）。

## 为什么需要这个文件

"此刻世界是什么状态"这类判据出错**全是静默的**（该挡没挡 = 两场架叠在一起；
不该挡却挡 = NPC 哑掉不报错）。所以它们的归属地必须明确、依赖必须可数 ——
否则就会重演立目录之前那个形状：同一个"忙"字在三个地方有三份互不相同的口径
（`NpcInitiativeMonitor.BusyReason` / `YieldReason` / `ModMain` 里那个 lambda）。

本文件只钉三件事，都很便宜，但破一条都会让"状态"重新散开：

  ① **纯逻辑核零外部依赖**（`BattleFlowCore` 只能有 BCL）——
     它是唯一能编成 net8.0 控制台程序跑断言的部分，一旦它开始碰 Unity/ModMain，
     97 项断言立刻全部编不过；
  ② **整个 state/ 的对外依赖可以数清**（只有 4 个），多一个就要在这里显式加一行并写理由；
  ③ **state/ 不许反向依赖 drama/ 的业务类型**（`DramaGate` / `BattleOutcome` / `DramaSegment*`）——
     依赖方向必须是 drama/tools/UI **→** state，反过来就会成环，
     而环在这类项目里表现为"初始化顺序敏感、偶发不生效"，最难查。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "csharp" / "state"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_code(src: str) -> str:
    """去掉注释**与字符串字面量**，只留真正的代码。

    ★为什么必须剥字符串★ 判据的人话原因里有 `"DramaGate.HasPending"` 这种**文案**
    （逐字沿用旧实现，好让日志对得上）。不剥字符串就会把它当成"state/ 依赖了 DramaGate"，
    得出假红 —— 而这个测试一旦假红，下一个人就会把这条断言删掉（比假绿更糟）。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r'"(?:[^"\\]|\\.)*"', '""', src)
    src = re.sub(r"'(?:[^'\\]|\\.)*'", "''", src)
    return src


def _cs_files() -> list[Path]:
    assert STATE.is_dir(), "csharp/state/ 不见了"
    files = sorted(STATE.glob("*.cs"))
    assert files, "csharp/state/ 里一个 .cs 都没有"
    return files


def test_core_has_zero_external_dependencies():
    """纯逻辑核只许有 BCL —— 它必须在 WSL 里编得起来、跑得了断言。

    ★这条最容易破★ 随手在核里写一句 `ModMain.P(...)` 或 `NpcInitiativeMonitor.xxx()`，
    在 Windows 上编译照样通过（同一个程序集），但 `csharp/tests/BattleFlowCoreTests`
    会**立刻全部编不过**（那个工程只链接这一个文件，没有任何 Unity/游戏引用）。
    也就是说：破这条不是"风格问题"，是"97 项断言当场消失"。
    """
    core = _read(STATE / "BattleFlowCore.cs")
    # 去掉注释**与字符串**再查，否则头注释/原因文案里提到的类名会把测试弄红
    body = _strip_code(core)
    for banned in ("ModMain", "NpcInitiativeMonitor", "DramaGate", "BattleOutcome",
                   "UnityEngine", "Newtonsoft", "TickProfiler", "ModPaths", "g.world", "g.ui"):
        assert banned not in body, (
            f"纯逻辑核里出现了 `{banned}` —— 它必须零 Unity/零主工程依赖，"
            "否则 csharp/tests/BattleFlowCoreTests（那个工程只链接这一个文件）会编不过"
        )


def test_state_module_outward_dependencies_are_countable():
    """整个 state/ 的对外依赖必须能数清：**4 个**。

    多出任何一个都要在这里显式登记并写理由 —— 不是为了好看，是因为
    "状态模块悄悄长出一堆依赖"正是它退化成第二个 `NpcInitiativeMonitor` 的方式。
    """
    allowed = {
        "ModMain",      # 日志唯一出口（P）
        "ModPaths",     # 数据根（落盘 battle_flow_ids.json）
        "TickProfiler", # 帧耗时采样（0.25s 节流下每帧判据的卡顿账）
        # `NpcInitiativeMonitor` **已从白名单移除**（10-25 第二步）：两个电平现在
        # 由 `WorldStateFacts` 自己读，不再经那个类的转发。将来谁在 state/ 里重新
        # 引用它，说明状态判据又扩散回去了 —— 这条会当场变红。
    }
    seen = set()
    for p in _cs_files():
        body = _strip_code(_read(p))
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\.", body):
            seen.add(m.group(1))
    # 只保留"本仓自有类型"（排除 BCL/Unity 那些）
    own_prefixes = ("ModMain", "ModPaths", "NpcInitiativeMonitor", "TickProfiler",
                    "DramaGate", "BattleOutcome", "DramaSegment", "DeferredActionQueue",
                    "UnitIdRegistry", "ContactDuty", "ActionWatcher", "ChatGlobals")
    own = {s for s in seen if s.startswith(own_prefixes)}
    extra = own - allowed
    assert not extra, (
        "state/ 多出了对外依赖：" + "、".join(sorted(extra))
        + " —— 要么去掉，要么在本测试的 allowed 里登记并写清理由"
        "（状态模块悄悄长依赖，就是它退化成第二个 NpcInitiativeMonitor 的方式）"
    )


def test_state_module_does_not_depend_on_drama_business_types():
    """依赖方向只能是 drama/tools/UI **→** state，永不许反过来。

    反向依赖会成环（drama 的文件引用 state，state 又引用 drama 的台账/分段器），
    而环在这类 mod 里的表现是"初始化顺序敏感、偶发不生效" —— 最难查的那种。
    """
    for p in _cs_files():
        body = _strip_code(_read(p))
        for banned in ("DramaGate", "BattleOutcome", "DramaSegmentRecorder",
                       "DramaSegmentCore", "DeferredActionQueue", "DramaNativeClaim"):
            assert banned + "." not in body and banned + " " not in body, (
                f"{p.name} 反向引用了 `{banned}` —— 依赖方向必须是 drama → state，"
                "反过来会成环（表现为初始化顺序敏感、偶发不生效）"
            )
