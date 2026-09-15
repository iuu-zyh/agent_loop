"""工具契约一致性：schemas.py ↔ C# ToolExecutor ↔ text_render.py（09-13 全量核对）

**为什么要机械化**：这条链上有四个独立的"声明面"，任何一处单方面改动都**不会报错**，
只是行为悄悄不对：

    schemas.py            模型看得到的参数/枚举/上限（模型据此生成参数）
    ToolExecutor.cs       C# 真正读的参数（读不到的键 = 模型填了也没用）
    ToolExecutor.cs        C# 真正发的 data 键
    text_render.py        真正进模型上下文的键（**没被读的键 = 模型永远看不到**）

第四行是这条链上最贵的坑 —— 本项目已经踩过四次同类（性格注解、性别、位置、道具 ID→中文名），
形态完全一样：C# 发了、渲染层没读、不报错、不缺段、日志干净，只是**模型"不知道"**。

本文件把这次人工核对固化成可重复的检查。它做**源码级**比对（不跑游戏），
所以新增字段时若只改了一侧，这里立刻红。

## 三类允许的"不一致"（都有明确理由，用白名单钉住）
1. `initiator` —— **由 harness 注入**（`dialogue_agent._execute_tool_calls`），
   刻意不进 schema：发起方恒为当前对话 NPC，绝不能让模型自己指定。
   5 个动作工具全靠它，注入一旦被删，全部会以「未找到发起方」失败 —— 故专门有一条测试盯着。
2. `target` —— 旧版兼容别名（`initiator ?? target`），现状无人写它，属死参数。
3. `item` / `item_name` / `count`（economy_item 顶层）—— 旧版单件形态兼容，同样不可达。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"

# 渲染层可能读到、但 C# 用**动态键**写出的字段（`SetAttr(t, dyn, pd, "age", "age", true)` 里
# `t[key] = ...`，正则看不见字面量）。审计时逐条确认过：
#   age / life —— `UnitSnapshot.SetAttr(..., key, ...)`，stats.attrs.personal 下的两个属性
DYNAMIC_KEYS = {"age", "life"}

# C# 读、但刻意不进 schema 的参数（见文件头「三类允许的不一致」）
#
# 清理后只剩 `initiator` 一项：`target` / `item` / `item_name` / `count` 那几套
# 旧版兼容别名已按用户拍板删除（"链路确定了就别再兜底"），故白名单同步收窄 ——
# 白名单一旦比现实宽，就会把**新加的隐藏参数**一起放过去。
CS_ONLY_ALLOWED = {"initiator"}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _schemas():
    import importlib
    import sys

    parent = str(ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module(f"{ROOT.name}.tools.schemas")


def _method_body(src: str, sig: str) -> str:
    """按大括号配平截出方法体（够用：这些方法体里没有含花括号的字面量）。"""
    i = src.index(sig)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
        k += 1
    raise AssertionError(f"方法体未闭合：{sig}")


def _args_read(src: str, sigs) -> set:
    """C# 从 args 里读出来的键。"""
    out = set()
    for s in sigs:
        try:
            b = _method_body(src, s)
        except ValueError:
            continue
        out |= set(re.findall(r'args\["([^"]+)"\]', b))
    return out


def _keys_written(text: str) -> set:
    """JObject 里写出的键（字面量形态）。"""
    out = set(re.findall(r'\["([^"]+)"\]\s*=', text))
    out |= set(re.findall(r'new JObject\s*\{\s*\["([^"]+)"\]', text))
    return out


def _keys_read(text: str) -> set:
    """渲染层从 **data** 读出的键（单双引号都算）。

    ★必须先剥掉 `args.get(...)` / `args[...]`★：渲染器签名是 `(args, data)`，
    `args` 是**模型传进来的参数**（如 `args.get("initiator")`、`args.get("op")`），
    它们不在 C# 的返回里，混进来会误报。
    """
    text = re.sub(r"args\.get\([^)]*\)", "", text)
    text = re.sub(r"args\[[^\]]*\]", "", text)
    out = set(re.findall(r'\.get\(\s*["\']([^"\']+)["\']', text))
    out |= set(re.findall(r'\[\s*["\']([^"\']+)["\']\s*\]', text))
    return out


def _renderer_fn(tr: str, tool: str) -> str:
    """按 @renderer("tool") 找真实函数名再切出来（函数名与工具名并不总相同）。"""
    m = re.search(r'@renderer\("' + re.escape(tool) + r'"\)\s*\ndef (\w+)\(', tr)
    assert m, f"text_render 里没有注册 {tool}"
    i = tr.index(f"def {m.group(1)}(")
    j = tr.find("\ndef ", i + 1)
    return tr[i: j if j > 0 else len(tr)]


# C# 侧各工具的方法（含它委派的 helper —— 只看主方法会漏读参数）
CS_METHODS = {
    "inspect_unit": ["private JObject InspectUnit(JObject args)",
                     "private static JObject PageLogs(JObject snap, string filter, int page, int sinceMonth = 0, int untilMonth = 0)"],
    "search_units": ["private JObject SearchUnits(JObject args)"],
    "query_world": ["private JObject QueryWorld(JObject args)",
                    "private JObject QueryWorldEvents(JObject args)",
                    "private JObject QueryWorldRankings(JObject args)",
                    "private JObject QueryWorldPlaces(string region, string cat)",
                    "private JObject QueryWorldSects(string region = null)",
                    "private JObject QueryWorldRegions()"],
    "social_relation": ["private JObject SocialRelation(JObject args)"],
    "movement": ["private JObject Movement(JObject args)"],
    "world_ai_action": ["private JObject WorldAiAction(JObject args)"],
    "economy_item": ["private JObject EconomyItem(JObject args)",
                     "private JObject GiveLingshiOnly(WorldUnitBase npc, WorldUnitBase player, JArray items, string via, string target)"],
    "trade": ["private JObject Trade(JObject args)"],
    "item_acquire": ["private JObject ItemAcquire(JObject args)"],
}


# ---------------------------------------------------------------------------

def test_dispatch_matches_tool_order():
    """C# Execute 的 case 分支 == schemas.TOOL_ORDER（少一个 = 模型能调但没人接）。"""
    S = _schemas()
    src = _read(CS / "ToolExecutor.cs")
    body = _method_body(src, "public JObject Execute(string name, JObject args)")
    cases = re.findall(r'case "([^"]+)":', body)
    assert cases == S.TOOL_ORDER, f"C# 分发 {cases} != TOOL_ORDER {S.TOOL_ORDER}"


def test_every_schema_param_is_read_by_csharp():
    """schema 声明的每个参数 C# 都必须真的读 —— 否则模型填了也没用（死参数）。"""
    S = _schemas()
    src = _read(CS / "ToolExecutor.cs")
    bad = {}
    for tool, sigs in CS_METHODS.items():
        declared = set(S._ALL_MAP[tool]["function"]["parameters"]["properties"])
        read = _args_read(src, sigs)
        dead = sorted(declared - read)
        if dead:
            bad[tool] = dead
    assert not bad, f"schema 声明了但 C# 从不读的参数：{bad}"


def test_csharp_only_params_are_allowlisted():
    """C# 读了 schema 没声明的参数 → 必须在白名单里（防有人偷偷加"模型可传的隐藏参数"）。"""
    S = _schemas()
    src = _read(CS / "ToolExecutor.cs")
    extra = set()
    for tool, sigs in CS_METHODS.items():
        declared = set(S._ALL_MAP[tool]["function"]["parameters"]["properties"])
        extra |= (_args_read(src, sigs) - declared)
    unexpected = sorted(extra - CS_ONLY_ALLOWED)
    assert not unexpected, (
        f"C# 多读了未声明的参数 {unexpected}：要么补进 schema，要么加进 CS_ONLY_ALLOWED 并写明理由"
    )


def test_enum_values_all_handled_by_csharp():
    """schema 的每个 enum 取值，C# 里都要出现（值写错 = 模型传了走进 default 分支）。"""
    S = _schemas()
    src = _read(CS / "ToolExecutor.cs")
    checks = [("social_relation", "op"), ("movement", "op"), ("world_ai_action", "op"),
              ("item_acquire", "op"), ("query_world", "topic"), ("inspect_unit", "log_filter")]
    missing = {}
    for tool, key in checks:
        enum = S._ALL_MAP[tool]["function"]["parameters"]["properties"][key].get("enum") or []
        text = "".join(_method_body(src, s) for s in CS_METHODS[tool] if s in src)
        miss = [v for v in enum if f'"{v}"' not in text]
        if miss:
            missing[f"{tool}.{key}"] = miss
    assert not missing, f"schema 列了但 C# 不认的枚举值：{missing}"


def test_no_renderer_reads_a_key_csharp_never_emits():
    """★核心★ 渲染层读的每个键，C# 必须真的发 —— 否则模型永远看不到（已踩四次的形态）。

    比对范围取 C# 侧**全量写出的键**（UnitSnapshot + ToolExecutor），因为 inspect 的块
    （inventory/logs/stats/...）由 UnitSnapshot 组装。动态键见 DYNAMIC_KEYS。
    """
    S = _schemas()
    tr = _read(ROOT / "tools" / "text_render.py")
    ck = _keys_written(_read(CS / "UnitSnapshot.cs")) | _keys_written(_read(CS / "ToolExecutor.cs"))
    # inspect 的各个块渲染器（其读取面分散在多个内部函数里）
    INSPECT_FNS = ["_persona", "_luck", "_stats", "_attrs_segments", "_martial", "_abilities",
                   "_inventory", "_relation_book", "_logs"]
    pk = set()
    for tool in S.TOOL_ORDER:
        pk |= _keys_read(_renderer_fn(tr, tool))
    for fn in INSPECT_FNS:
        i = tr.index(f"def {fn}(")
        j = tr.find("\ndef ", i + 1)
        pk |= _keys_read(tr[i: j if j > 0 else len(tr)])
    missing = sorted(pk - ck - DYNAMIC_KEYS)
    assert not missing, (
        f"渲染层读了但 C# 从未写出的键：{missing} —— "
        "模型拿不到这些字段（要么 C# 补发，要么渲染层别读，要么加进 DYNAMIC_KEYS）"
    )


def test_initiator_is_injected_by_harness():
    """5 个动作工具全靠 harness 注入的 `initiator` 当 actor。

    它**刻意不在 schema 里**（发起方恒为当前对话 NPC，不能让模型指定），
    通道只有 `dialogue_agent._execute_tool_calls` 这一处。漏了不会有任何编译期/测试期信号 ——
    要等真机上模型调 economy_item 才炸「未找到发起方 」。故专门钉住：
    Python 注入 + C# 读取，两头都在。
    """
    da = _read(ROOT / "dialogue_agent.py")
    assert 'args["initiator"] = self.id' in da, \
        "harness 不再注入 initiator —— 5 个动作工具会全部以「未找到发起方」失败"
    src = _read(CS / "ToolExecutor.cs")
    for tool in ("social_relation", "movement", "world_ai_action", "economy_item", "item_acquire"):
        read = _args_read(src, CS_METHODS[tool])
        assert "initiator" in read, f"{tool} 不再读 initiator（actor 无从确定）"


def test_inventory_top_bound_matches_schema():
    """`inventory_top` 的上限必须与 schema 的 maximum 一致（否则静默变成"全量"）。"""
    S = _schemas()
    mx = S._ALL_MAP["inspect_unit"]["function"]["parameters"]["properties"]["inventory_top"]["maximum"]
    src = _read(CS / "ToolExecutor.cs")
    body = _method_body(src, "private JObject InspectUnit(JObject args)")
    assert re.search(rf"if\s*\(invTop\s*>\s*{mx}\)\s*invTop\s*=\s*{mx};", body), \
        f"inventory_top 没有按 schema 的 maximum={mx} 钳制"


def test_schemas_has_no_stale_field_names():
    """schema 描述里引用的 snake_case 字段名必须真实存在。

    事故：`item_acquire` 的描述写着「props_detail 有每栈数量」，而 `props_detail`
    09-03 就被删了（改成逐栈对象）—— 模型被指引去找一个不存在的键，而且**不会报错**。

    判据刻意收窄成「**带下划线的 snake_case 标识符**」：那正是数据键的形态
    （`props_detail`/`item_name`/`log_page`…），而普通英文词、枚举值不会误伤。
    合法来源 = schema 参数名 ∪ 全部枚举值 ∪ C# 写出的键 ∪ 结果侧通用词。
    """
    S = _schemas()
    ck = _keys_written(_read(CS / "UnitSnapshot.cs")) | _keys_written(_read(CS / "ToolExecutor.cs"))
    params, enums = set(), set()
    for tool in S.TOOL_ORDER:
        props = S._ALL_MAP[tool]["function"]["parameters"]["properties"]
        for k, v in props.items():
            params.add(k)
            for ev in (v.get("enum") or []):
                enums.add(str(ev))
            if isinstance(v.get("items"), dict):
                for kk, vv in (v["items"].get("properties") or {}).items():
                    params.add(kk)
                    for ev in (vv.get("enum") or []):
                        enums.add(str(ev))
            for kk, vv in (v.get("properties") or {}).items():
                params.add(kk)
                for ev in (vv.get("enum") or []):
                    enums.add(str(ev))
    # 工具名（描述里互相引用，如"先 inspect_unit 确认"）与提示词段名（world_basis）不是数据键
    sections = {f.stem for f in (ROOT / "prompts" / "sections").glob("*.txt")}
    known = ck | params | enums | DYNAMIC_KEYS | set(S.TOOL_ORDER) | sections | {
        "npc_id", "world_id", "same_grid", "intim", "unit_id",  # 结果侧通用键
    }
    bad = []
    for tool in S.TOOL_ORDER:
        blob = str(S._ALL_MAP[tool])
        for ref in set(re.findall(r"\b([a-z]+_[a-z_]+)\b", blob)):
            if ref not in known:
                bad.append(f"{tool}: {ref}")
    assert not bad, (
        f"schema 描述引用了不存在的字段：{bad} —— 模型会去找一个根本没有的键（09-03 props_detail 同类事故）"
    )
