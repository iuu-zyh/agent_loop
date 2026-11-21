#!/usr/bin/env python3
"""把 csharp/tools/ToolExecutor.cs 按「一个工具一个类」拆成多文件（纯搬移）。

★ 一次性脚本（11-19 已跑过）。**重跑会覆盖**各文件手工整理过的头注释/措辞，仅作留档。

- 成员文本逐字搬走，只把 `private` 改成 `internal static`（静态类成员必须 static；
  同程序集的兄弟类要互相调用）；
- 搬完自动补跨类限定（`XxxTool.Foo(...)`），由编译器复核；
- ToolExecutor.cs 只留「分发 + 信封判定」。
"""
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "csharp" / "tools" / "ToolExecutor.cs"
DST_DIR = ROOT / "csharp" / "tools"

GROUPS = {}


def put(cls, *names):
    for n in names:
        GROUPS[n] = cls


put("ActionCatalog", "IsActionTool", "OpOf", "DescribeAction", "_ActionVerb")
put("ToolArgs", "ResolveArgExact", "ResolveActor", "ResolveCounterparty", "WithCounterparty",
    "SameGrid", "GetIntimBetween", "SafeInt", "IntOf", "LongOf", "NameOfUnit", "IsPlayerUnit")
put("GameText", "LSOrRaw", "FateText", "AreaName", "AreaNameFallback", "AreaLabel")
put("InspectTool", "InspectUnit", "PageLogs", "TagTier")
put("SearchTool", "SearchUnits", "RelationMatch")
put("WorldQueryTool", "QueryWorld", "BuildInfo", "CollectNamedBuilds", "CollectTypedBuilds",
    "CollectIconTypedBuilds", "CollectIconTypedEvents", "ResolveBuild", "RegionOk",
    "QueryWorldPlaces", "RegionOfGrid", "RegionOfPoint", "QueryWorldRegions",
    "QueryWorldEvents", "QueryWorldRankings")
put("SectQueryTool", "QueryWorldSects", "ReadStrList", "ReadEnumList", "SectBasTypeCn",
    "SectBasTypeCnOf", "SectBasTypesOf", "SectDeptName", "SectPostCn", "SectPersonInto",
    "SectPerson", "AppendSectInfo", "SectPeopleTree", "ResolveSchoolByName", "SectRoster",
    "SectPostOf")
put("SectActionTool", "SectJoin", "SectLeave", "WriteSectLog", "SectActionRefuseText",
    "SectResolveMasterDept", "ResolveJoinSchoolId")
put("RelationTool", "SocialRelation", "ProbeRelationSet", "StartRelationAction", "DirectEstablish",
    "DirectBreak", "DirectMarry", "RelationOf", "RelationDiag", "RelCount",
    "CreateDummyRelationItem", "StartBreakWith", "BreakFieldsDirect", "BreakStillRelated",
    "BreakRelName", "RoleRelationItemOfType", "MailMaxChars")
put("MovementTool", "Movement")
put("WorldActionTool", "WorldAiAction")
put("EconomyTool", "EconomyItem", "GiveLingshiOnly", "GivePropsByWindow", "GiveSideEffects",
    "GiveItemsText", "GiveViaNote")
put("TradeTool", "Trade", "FindStackSoleID", "CountOf")
put("AcquireTool", "ItemAcquire", "AcquireItem", "ParseAcquireItems", "CountPropsByName",
    "AskMultiple", "PropsStack", "FindPropsStack")
put("TeamCallTool", "Team")
put("IntimService", "IntimBetween", "IntimStepDefault", "IntimStepMax", "LunDaoIntimEach",
    "TradeIntimEach", "ConfIntim", "GiftIntimValue", "AskforIntimValue", "AskforIntimReduceValue",
    "ApplyIntim", "PutIntim", "PutIntimWithAfter", "ApplyGiftIntim", "ApplyAskforIntim",
    "ApplyAskforUpset", "ApplyLunDaoIntim")
put("PropsService", "ReadLingshiHeld", "TransferLingshi", "TakePropsFromBag", "RollbackGive")
put("NativeActionService", "StartToUnitAiAction", "StartToUnitAiActionDeferred", "Claimed",
    "JoinPlayerAttack", "DeferNativeUnitAction", "NativeRefuseWhy", "Refused", "ExcStack1",
    "StartSelfAiAction", "NewAiActionCallback")
put("MartialService", "ResolveMartial", "FirstTeachableSkill", "MartialDisplayName")

KEEP = {"Execute", "IsOk"}

# 失效的孤儿注释（原 `UnitNameById` 被删后留下的 doc 注释，下面紧跟的是另一个成员）
STALE_ORPHANS = re.compile(r"unitID → 中文名（g\.world\.unit\.GetUnit），失败返回 null")
DECL = re.compile(r"^        (?:private|internal|public|protected)\s")


def member_name(head):
    m = re.search(r"\b(?:class|struct|enum)\s+(\w+)", head)
    if m:
        return m.group(1)
    m = re.search(r"(\w+)\s*(?:<[^>]*>)?\s*\(", head)
    if m:
        return m.group(1)
    m = re.search(r"(\w+)\s*(?:=|;)", head)
    return m.group(1) if m else ""


def find_members(lines):
    out, i = [], 0
    while i < len(lines):
        if not DECL.match(lines[i]):
            i += 1
            continue
        name = member_name(lines[i])
        start = i
        while start > 0:
            prev = lines[start - 1].strip()
            if prev.startswith("///") or prev.startswith("//"):
                start -= 1
            else:
                break
        depth, seen, j = 0, False, i
        while j < len(lines):
            for ch in lines[j]:
                if ch == "{":
                    depth += 1
                    seen = True
                elif ch == "}":
                    depth -= 1
            if seen and depth == 0:
                j += 1
                break
            if not seen and lines[j].rstrip().endswith(";"):
                j += 1
                break
            j += 1
        out.append((name, start, j))
        i = j
    return out


def rewrite(text):
    text = re.sub(r"^(        )private\s+static\s+readonly\s+", r"\1internal static readonly ", text, count=1, flags=re.M)
    text = re.sub(r"^(        )private\s+const\s+", r"\1internal const ", text, count=1, flags=re.M)
    text = re.sub(r"^(        )private\s+(?:static\s+)?class\s+", r"\1internal sealed class ", text, count=1, flags=re.M)
    text = re.sub(r"^(        )private\s+static\s+", r"\1internal static ", text, count=1, flags=re.M)
    text = re.sub(r"^(        )private\s+", r"\1internal static ", text, count=1, flags=re.M)
    text = re.sub(r"^(        )internal\s+(?!static)", r"\1internal static ", text, count=1, flags=re.M)
    # 修正上一步误加的 static（const / 嵌套类型都不许）
    text = text.replace("internal static const ", "internal const ")
    text = text.replace("internal static sealed class ", "internal sealed class ")
    text = text.replace("internal static class ", "internal sealed class ")
    return text


def is_comment(line):
    s = line.strip()
    return s.startswith("//") or s.startswith("///") or s.startswith("*")


def qualify(text, own, name2class):
    """把引用了别类成员的裸调用/标识符补上类名限定。"""
    out = []
    for line in text.split("\n"):
        if is_comment(line):
            out.append(line)
            continue
        for name, cls in name2class.items():
            if cls == own:
                continue
            # 声明行本身不许加限定
            if re.search(r"\b(class|struct|const|readonly|enum)\b", line) and re.search(r"\b" + name + r"\b", line) and "(" not in line.split(name)[0][-30:]:
                pass
            line = re.sub(r"(?<![\w.])" + name + r"(\s*\()", cls + r"." + name + r"\1", line)
            line = re.sub(r"(?<![\w.])" + name + r"(\s*<)", cls + r"." + name + r"\1", line)
            # 类型/常量/字段（不带括号）
            if re.search(r"(?<![\w.])" + name + r"(?![\w(<])", line) and not re.search(r"\b" + name + r"\s*[=;,)]", line.split("//")[0][:0] or r"$^"):
                line = re.sub(r"(?<![\w.])" + name + r"(?![\w(])", cls + "." + name, line)
        out.append(line)
    return "\n".join(out)


HEADER = '''/// <summary>
/// 工具执行器 —— request: call_tool 的**分发**（11-19 由 6711 行拆成一工具一类）。
///
/// 本文件只剩三件事：两个 `Execute` 重载（直发 / 带 npc_id 的排队闸）、`IsOk`。
/// 具体工具在哪个文件，按 `name` 一眼可查：
///
///   inspect_unit      → `tools/InspectTool.cs`       （含 `PageLogs` 分页）
///   search_units      → `tools/SearchTool.cs`
///   query_world       → `tools/WorldQueryTool.cs`    （places/regions/events/rankings）
///   query_world.sects → `tools/SectQueryTool.cs`     （宗门人事/名册/人物树）
///   宗门入/退         → `tools/SectActionTool.cs`
///   social_relation   → `tools/RelationTool.cs`
///   movement          → `tools/MovementTool.cs`
///   world_ai_action   → `tools/WorldActionTool.cs`
///   economy_item      → `tools/EconomyTool.cs`
///   trade             → `tools/TradeTool.cs`
///   item_acquire      → `tools/AcquireTool.cs`
///   team              → `tools/TeamCallTool.cs`      （领域逻辑仍在 `TeamTool.cs`）
///
/// 共用的判据各有唯一一份，别再往工具类里抄：
///   `ToolArgs`（发起方/对手方解析、JSON 取值）、`ActionCatalog`（"是不是动作工具"+一句人话）、
///   `IntimService`（好感读写与步长）、`PropsService`（灵石/背包转移）、
///   `NativeActionService`（原生动作下发 + 拒绝判据）、`MartialService`（功法解析）、
///   `GameText`（本地化串/地名/气运文案）。
///
/// 【分发形状不能改】`switch` 与全部执行链曾经都在这里，Python 契约测试
///   `test_dispatch_matches_tool_order` 锁的就是这个 `case` 顺序与集合。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    public class ToolExecutor
    {
'''


def main():
    lines = SRC.read_text(encoding="utf-8").split("\n")
    members = find_members(lines)
    names = [n for n, _, _ in members]
    missing = [n for n in GROUPS if n not in names]
    if missing:
        print("!! 分组表里有源码中找不到的成员：", missing)
        return 1
    unassigned = [n for n in names if n not in GROUPS and n not in KEEP]
    if unassigned:
        print("!! 有成员没分组：", unassigned)
        return 2

    name2class = dict(GROUPS)
    by_class, keep_chunks = {}, []
    prev_end = None
    for name, s, e in members:
        # 两个成员之间若还有注释（分节横幅 / 说明块），归**下面那个成员**，一起搬走
        if prev_end is not None and s > prev_end:
            gap = lines[prev_end:s]
            first = next((k for k, l in enumerate(gap) if l.strip()), None)
            if first is not None:
                block = "\n".join(gap[first:])
                if STALE_ORPHANS.search(block):
                    print(f"  （丢弃失效注释：{name} 之上）")
                else:
                    s = prev_end + first
        prev_end = e
        chunk = "\n".join(lines[s:e])
        if name in KEEP:
            keep_chunks.append(chunk)
            continue
        by_class.setdefault(GROUPS[name], []).append(rewrite(chunk))

    for cls, chunks in sorted(by_class.items()):
        body = qualify("\n\n".join(chunks), cls, name2class)
        head = (
            "/// <summary>\n"
            f"/// {cls} —— 工具层的一个类（11-19 拆分）：只放这一件事，逐字搬自原 `ToolExecutor.cs`。\n"
            "/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。\n"
            "/// </summary>\n"
            "using System;\n"
            "using System.Collections.Generic;\n"
            "using Newtonsoft.Json.Linq;\n"
            "using static AgentLoopBridge.ToolResult;\n\n"
            "namespace AgentLoopBridge\n{\n"
            f"    internal static class {cls}\n    {{\n"
        )
        path = DST_DIR / f"{cls}.cs"
        path.write_text(head + body + "\n    }\n}\n", encoding="utf-8")
        print(f"  {cls:22s} {len(body.splitlines()):5d} 行 → {path.relative_to(ROOT)}")

    kept = qualify("\n\n".join(keep_chunks), "__ToolExecutor__", name2class)
    SRC.write_text(HEADER + kept + "\n    }\n}\n", encoding="utf-8")
    print(f"  ToolExecutor           {len(kept.splitlines()):5d} 行（分发壳）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
