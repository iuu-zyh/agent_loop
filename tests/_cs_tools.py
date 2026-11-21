"""工具层源码的**聚合读取**（11-19 拆分后新增）。

为什么有它：`csharp/tools/ToolExecutor.cs` 曾经是 6711 行的"什么都放"文件，源码契约测试
直接读那一份文本。拆分后同一批实现散在 `csharp/tools/*.cs` 十多个类里（一工具一类）——
契约测试要断言的是**整个工具层**，不是"恰好叫 ToolExecutor 的那个文件"。

于是统一走这里：
    from _cs_tools import tool_src, TOOL_SRC
    src = tool_src()                 # 全部工具层源码拼起来
    src = TOOL_SRC.read_text(...)    # 老写法（把常量当 Path 用）照旧能跑

**别在测试里再写 `ROOT / "csharp" / "tools" / "ToolExecutor.cs"`** —— 那个文件现在只剩分发，
针落在别处的结果是"测试红了但代码是对的"（11-19 拆分时踩过）。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "csharp" / "tools"


def tool_src() -> str:
    """`csharp/tools/**/*.cs` 全部源码（按路径排序拼接，确定性）。"""
    parts = []
    for p in sorted(TOOLS_DIR.rglob("*.cs")):
        if "/obj/" in str(p) or "/bin/" in str(p):
            continue
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


#: 从 `ToolExecutor.cs` 拆出来 / 归并进去的类 —— 只有它们的前缀算"拆分带来的噪音"。
#: 加新类时同步这里（`test_cs_tools_helpers` 会核对：tools 下的 .cs 要么在这，
#: 要么是本来就不属于 ToolExecutor 的独立类）。
SPLIT_CLASSES = (
    "ToolExecutor", "ToolArgs", "QueryTool", "SectQueryTool", "RelationTool",
    "MovementTool", "ActionTool", "EconomyTool", "AcquireTool", "TeamTool",
    "IntimService", "PropsService", "NativeActionService",
)

_TOOL_CLASS_RE = None


def flat_src(src: str = "") -> str:
    """工具层源码，**去掉同类内的类名前缀**（`IntimService.ApplyGiftIntim(` → `ApplyGiftIntim(`）。

    为什么需要：拆分前这些调用写在同一份文件里，是裸调用；拆开后跨类调用带前缀。
    契约测试断言的是"**调没调**那个函数、参数对不对"，不是"写没写类名"——
    所以比对前把前缀抹平，判据保持不变，也不会因为将来再挪一次类就整片红。

    ⚠ 只抹**从 ToolExecutor.cs 拆出来的那些类**的前缀（`SPLIT_CLASSES`）。
      `TeamBattleFollowup.` / `UnitSnapshot.` / `UnitLookup.` 这类**不动** —— 它们本来就
      是别的文件里的独立类，抹掉会把"调了谁"这件事弄丢（踩过：断言
      `TeamBattleFollowup.TryAddLog(` 被抹成 `TryAddLog(`，测试红得莫名其妙）。
    """
    global _TOOL_CLASS_RE
    if _TOOL_CLASS_RE is None:
        names = sorted(SPLIT_CLASSES, key=len, reverse=True)
        _TOOL_CLASS_RE = re.compile(r"(?<![\w.])(?:" + "|".join(names) + r")\.")
    return _TOOL_CLASS_RE.sub("", src or tool_src())


def tool_file(name: str) -> str:
    """工具层里**某一个**类的源码（`name` 例：`RelationTool.cs`）。

    什么时候用它：断言的对象本来就是"写入层"/"关系工具"这一份实现（例如好感写入层
    只许 `IntimService` 有 `AddIntim`）。用聚合文本会把别处的同名调用也算进来。
    """
    p = TOOLS_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"工具层没有 {name}（现有：{[x.name for x in sorted(TOOLS_DIR.glob('*.cs'))]}")
    return p.read_text(encoding="utf-8")


def tool_files() -> list[str]:
    """工具层所有类的相对路径（`tools/Xxx.cs`），给"调用点只许在这一层"这类断言用。"""
    return [f"tools/{p.name}" for p in sorted(TOOLS_DIR.glob("*.cs"))]


class ToolSrc:
    """长得像 `pathlib.Path`，但 `read_text()` 给的是整个工具层的拼接。

    存在的唯一理由：历史上几十处测试写的是 `EXEC = ROOT / "csharp" / "tools" / "ToolExecutor.cs"`
    然后 `EXEC.read_text(...)`。把它换成本类实例，调用点一个字都不用改。
    """

    def read_text(self, encoding: str = "utf-8", errors: str | None = None) -> str:  # noqa: D401
        """**类名前缀已抹平**的工具层源码 —— 等价于拆分前"那一份 6711 行的文件"。

        历史测试写的都是 `EXEC = ROOT / "csharp" / "tools" / "ToolExecutor.cs"` 再
        `EXEC.read_text()`；那时候同类内是裸调用。现在跨类要带前缀，所以这里给"抹平版"，
        让判据（调没调、参数对不对）保持原样。需要看真实前缀时用 `tool_src()` / `tool_file()`。
        """
        return flat_src()

    def __fspath__(self) -> str:
        return str(TOOLS_DIR)

    def __str__(self) -> str:
        return "csharp/tools/*.cs"

    def __repr__(self) -> str:
        return "<ToolSrc csharp/tools/*.cs>"


TOOL_SRC = ToolSrc()
