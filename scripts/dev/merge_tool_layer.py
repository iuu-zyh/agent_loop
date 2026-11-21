#!/usr/bin/env python3
"""工具层二次归并（11-20，一次性）：20 个文件 → 12 个文件。

按**业务域**合并，不再按"一个工具一个文件"：
  QueryTool            = InspectTool + SearchTool + WorldQueryTool
                         + SectQueryTool + GameText                  （查询域：含 sects topic 与查询侧文案）
  ActionTool           = WorldActionTool + SectActionTool + MartialService  （互动域：含宗门入/退与传功）
  MovementTool         = MovementTool                                （位移，独立一类）
  EconomyTool          = EconomyTool + TradeTool                     （财物：赠送 → 买卖）
  TeamTool             += TeamCallTool                               （入口并回领域类）
  ToolExecutor         += ActionCatalog                              （只有它在用）
  RelationTool / MovementTool / AcquireTool / IntimService / PropsService /
  NativeActionService / ToolArgs 原样保留（各有单一职责，不与别的域合并）

纯合并：成员逐字搬，只重写类名前缀。
"""
import pathlib
import re
import sys

TOOLS = pathlib.Path(__file__).resolve().parents[2] / "csharp" / "tools"

# 目标文件名（去 .cs）→ (类名, [来源文件名], 头注释的用途一句)
MERGE = {
    # 查询域（Python `tools/schemas.py`："# ---------- 查询域 ----------" 三件只读）
    "QueryTool": ("QueryTool",
                  ["InspectTool", "SearchTool", "WorldQueryTool", "GameText"],
                  "**查询域**：`inspect_unit`（档案 + 经历分页）/ `search_units` / `query_world`"
                  "（places/regions/events/rankings）+ 查询侧文案（本地化串/地名/气运名）。"),
    # 查询域 —— 宗门（query_world 的 topic=sects，实现自成一块）
    "SectQueryTool": ("SectQueryTool", ["SectQueryTool"],
                      "**查询域·宗门**：`query_world(topic=sects)` 的实现（名册 / 人事树 / 职位 / 分部）。"),
    # 动作域 —— 关系
    "RelationTool": ("RelationTool", ["RelationTool"],
                     "**动作域·关系**：`social_relation`（好感/结缘/婚姻/师徒/结义/捎话）+ 引擎裁决码表。"),
    # 动作域 —— 位移
    "MovementTool": ("MovementTool", ["MovementTool"],
                     "**动作域·位移**：`movement`（召唤/传送/行走）。"),
    # 动作域 —— 互动
    "ActionTool": ("ActionTool",
                   ["WorldActionTool", "SectActionTool", "MartialService"],
                   "**动作域·互动**：`world_ai_action`（切磋/攻击/双修/论道/邀约/传功 + 宗门入/退）"
                   "＋传功要用的功法解析。"),
    # 动作域 —— 财物（赠送 → 买卖）
    "EconomyTool": ("EconomyTool", ["EconomyTool", "TradeTool"],
                    "**动作域·财物**：`economy_item`（赠予：灵石直写 + 道具原生窗）+ `trade`（买卖）。"),
    "AcquireTool": ("AcquireTool", ["AcquireTool"],
                    "**动作域·财物**：`item_acquire`（偷窃/讨要/收下）。"),
    # 动作域 —— 组队
    "TeamTool": ("TeamTool", ["TeamTool", "TeamCallTool"],
                 "**动作域·组队**：`team` 的工具入口（参数校验 + 确认窗）＋领域逻辑（气运/槽位/同步）。"),
    # 分发（含"哪些算动作工具"与一句人话）
    "ToolExecutor": ("ToolExecutor", ["ActionCatalog"],
                     "「哪些工具算动作工具」+「一句话人话」（排队闸 / 玩家提示 / 办妥回执共用）。"),
}

RENAME = {
    "InspectTool": "QueryTool",
    "SearchTool": "QueryTool",
    "WorldQueryTool": "QueryTool",
    "MovementTool": "ActionTool",
    "WorldActionTool": "ActionTool",
    "SectActionTool": "ActionTool",
    "TradeTool": "EconomyTool",
    "TeamCallTool": "TeamTool",
    "GameText": "QueryTool",

    "MartialService": "ActionTool",
    "ActionCatalog": "ToolExecutor",
}


def split_file(name):
    """返回 (header, body)。body = 类体内成员（不含 class 行与两层花括号）。"""
    text = (TOOLS / f"{name}.cs").read_text(encoding="utf-8")
    m = re.search(r"\n\s*(?:public|internal|private)\s*(?:static\s+)?(?:sealed\s+)?class\s+\w+[^\n]*\n\s*\{\n", text)
    if not m:
        raise SystemExit(f"!! 认不出 {name}.cs 的类声明")
    header = text[:m.end()]
    body = text[m.end():]
    # 去掉尾部 `    }\n}\n`（类与 namespace 的收尾）
    tail = re.search(r"\n\s*\}\n\}\s*$", body)
    if not tail:
        raise SystemExit(f"!! 认不出 {name}.cs 的结尾")
    return header, body[:tail.start()]


def main():
    # 先把所有源文件读进内存，再写（避免边写边读）
    bodies = {}
    headers = {}
    for target, (cls, sources, _why) in MERGE.items():
        sources = list(sources)
        parts = []
        for s in sources:
            h, b = split_file(s)
            headers[s] = h
            parts.append(b.strip("\n"))
        bodies[target] = "\n\n".join(parts)

    for target, (cls, sources, why) in MERGE.items():
        text = bodies[target]
        for old, new in RENAME.items():
            text = re.sub(r"(?<![\w.])" + old + r"\.", new + ".", text)
        own_header = headers.get(target)
        if own_header and target != cls:
            head = None
        elif own_header and len(sources) > 1:
            head = own_header                      # 已有的文件头更详细，留它
        else:
            head = (
            "/// <summary>\n"
            f"/// {cls} —— {why}\n"
            "/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按业务域归并；成员逐字搬移。）\n"
            "/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。\n"
            "/// </summary>\n"
            "using System;\n"
            "using System.Collections.Generic;\n"
            "using Newtonsoft.Json.Linq;\n"
            "using static AgentLoopBridge.ToolResult;\n\n"
            "namespace AgentLoopBridge\n{\n"
            f"    internal static class {cls}\n    {{\n"
            )
        if head is None:
            raise SystemExit(f"!! {target} 头注释处理没定义")
        # ToolExecutor 是 public class（WsClient 持有实例），不重写头
        if target == "ToolExecutor":
            old_header, old_body = split_file("ToolExecutor")
            merged = old_body.strip("\n") + "\n\n" + text.strip("\n")
            merged = merged.replace("ActionCatalog.", "")
            # 并进来的 ActionCatalog 成员只在类内用 → private
            for m in ("IsActionTool", "OpOf", "DescribeAction"):
                merged = merged.replace(f"internal static bool {m}(", f"private static bool {m}(")
                merged = merged.replace(f"internal static string {m}(", f"private static string {m}(")
            merged = merged.replace("internal static readonly Dictionary<string, string> _ActionVerb",
                                    "private static readonly Dictionary<string, string> _ActionVerb")
            (TOOLS / f"{target}.cs").write_text(old_header + merged + "\n    }\n}\n", encoding="utf-8")
        else:
            (TOOLS / f"{target}.cs").write_text(head + text.strip("\n") + "\n    }\n}\n", encoding="utf-8")
        print(f"  {target}.cs  ← {', '.join(sources)}")

    # 全局前缀重写：别的文件也会引用被并掉的类（例：SectQueryTool 用 WorldQueryTool.BuildInfo）
    files = [q for q in (TOOLS.parent).rglob("*.cs")
             if "/obj/" not in q.as_posix() and "/bin/" not in q.as_posix() and "/tests/" not in q.as_posix()]
    touched = []
    for q in files:
        raw = q.read_bytes().decode("utf-8")
        t = raw
        for old, new in RENAME.items():
            t = re.sub(r"(?<![\w.])" + old + r"\.", new + ".", t)
        if t != raw:
            q.write_bytes(t.encode("utf-8"))
            touched.append(q.name)
    print("  前缀重写:", len(touched), "个文件")

    absorbed = []
    keep = set(MERGE)
    for target, (_cls, sources, _why) in MERGE.items():
        for s in sources:
            if s != target:
                absorbed.append(s)
    for s in sorted(set(absorbed)):
        p = TOOLS / f"{s}.cs"
        if p.exists():
            p.unlink()
            print(f"  删除 {s}.cs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
