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
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"

# 渲染层可能读到、但 C# **正则扫不到**的键。两类：
#   ① C# 用动态键写出（`SetAttr(t, dyn, pd, "age", "age", true)` 里 `t[key] = ...`）：
#      age / life —— `UnitSnapshot.SetAttr(..., key, ...)`，stats.attrs.personal 下的两个属性；
#   ② **根本不是 C# 发的**：`proposal` —— Python 群聊闸门（`room_flow` 提案制）合成的结果键，
#      只走"已提出"短路帧；三处渲染器都写了该分支，但 C# 永不发（单聊到不了那条路）。
#   ③ **Python 侧自己写进去的**：`display` —— `unit_refs.annotate` 在渲染前就地给"需要区分
#      同名者"的那条引用打上的唯一化显示名（渲染层优先读它）。C# 不产生也不需要它。
DYNAMIC_KEYS = {"age", "life", "proposal", "display"}

# C# 读、但刻意不进 schema 的参数（见文件头「三类允许的不一致」）
#
# 清理后只剩 `initiator` 一项：`target` / `item` / `item_name` / `count` 那几套
# 旧版兼容别名已按用户拍板删除（"链路确定了就别再兜底"），故白名单同步收窄 ——
# 白名单一旦比现实宽，就会把**新加的隐藏参数**一起放过去。
#
# ★ 09-17 新增四个 `*_id`（`target_id` / `seller_id` / `buyer_id` / `initiator_id`）。
#   它们**必须**留在白名单外（= 不许进 schema）—— 这是设计，不是遗漏：
#     · 它们是 **harness 侧**填的精确身份（群聊由房间身份表 `Room.member_unit_ids` 翻译，
#       单聊由 `open_chat` 带上来的 `agent.unit_id`），**模型从来看不到、也不该填**；
#     · 一旦进了 schema，模型就会开始自己编 id —— 而那正是 `inspect_unit` 那次事故的入口。
#   名字（`target` / `seller` / `buyer`）仍然由模型填，中文名照旧是模型侧唯一的指人方式。
# ★ 09-17 追加 `unit_id`：它原先是 `inspect_unit` 的**模型可见**参数，现在从 schema 里删掉了，
#   改由 harness 注入（`unit_refs.PERSON_ARGS["inspect_unit"] = (("target", "unit_id"),)`）。
#   于是 C# 仍然读 `args["unit_id"]`，但模型再也看不到它 —— 正是这个白名单存在的意义。
CS_ONLY_ALLOWED = {"initiator", "initiator_id", "unit_id", "target_id", "seller_id", "buyer_id"}


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


# 指人参数由**共用 helper** 读（09-17 起）：`target` / `seller` / `buyer` 的名字与精确 id
# 都在 `ResolveCounterparty` / `ResolveArgExact` 里读，各工具本体只剩一行调用。
# 不把这些 helper 算进来的话，本用例会把"读过了"误报成"死参数"—— 而它要防的是**真没人读**。
# ★ 但只能**按工具**算：某个工具调了 helper 才把 helper 读的键算给它
#   （无差别全算会让 `trade` 凭空"多读"一个 `target`，把白名单用例搞红）。
_SHARED_ARG_READERS = {
    "ResolveCounterparty": " WorldUnitBase ResolveCounterparty(JObject args, out string counterparty, out JObject fail)",
    "ResolveArgExact": " WorldUnitBase ResolveArgExact(JObject args, string key, string name)",
    "ResolveActor": " WorldUnitBase ResolveActor(JObject args)",
}


def _args_read(src: str, sigs) -> set:
    """**这个工具**真正会读到的 args 键（含它调用的共用 helper 读的那些）。"""
    out = set()
    bodies = []
    for s in sigs:
        try:
            bodies.append(_method_body(src, s))
        except ValueError:
            continue
    txt = "\n".join(bodies)
    # ① 本体直接读的
    for b in bodies:
        out |= set(re.findall(r'args\["([^"]+)"\]', b))
    # ② 交给共用 helper 读的（只在本体真的调了它时才算数）
    if "ResolveCounterparty(args," in txt:
        out.add("target")
        out |= _helper_literal_keys(src, _SHARED_ARG_READERS["ResolveCounterparty"])
    for k in re.findall(r'ResolveArgExact\(args,\s*"([^"]+)"', txt):
        out.add(k)
        out.add(k + "_id")          # helper 内部读 `<键>_id`（键名是拼出来的，正则看不到）
    # 发起方：11-20 起六个动作工具统一走 `ToolArgs.BindActor`（内部才调 `ResolveActor`，
    # 并且额外读 `initiator_id` 做死者精准定位）。两种写法都算。
    if "ResolveActor(args)" in txt or "BindActor(args," in txt:
        out.add("initiator")
        out.add("initiator_id")
    return out


def _helper_literal_keys(src: str, signature: str) -> set:
    """helper 本体里字面量出现的 `args["x"]` 与 `ResolveArgExact(args, "x", …)`。"""
    try:
        b = _method_body(src, signature)
    except ValueError:
        return set()
    out = set(re.findall(r'args\["([^"]+)"\]', b))
    for k in re.findall(r'ResolveArgExact\(args,\s*"([^"]+)"', b):
        out.add(k)
        out.add(k + "_id")
    return out


#: 渲染层读、但由**收口 helper 以变量名写出**的键（`PutIntimWithAfter(o, "k", v, a, b)` 的 k
#: 是字面量，`["k"] = ` 的扫描看不见；`PutIntim(PutIntim(o, k, Δ)` 的键在调用点写死）。
_HELPER_KEY_CALLS = re.compile(
    r"PutIntimWithAfter\([^,]+,\s*\"([^\"]+)\"|PutIntim\([^,]+,\s*\"([^\"]+)\"|"
    r"\[\"([^\"]+)\"\]\s*=")


def _keys_written(text: str) -> set:
    """JObject 里写出的键（字面量形态）。

    ★ 10-15 追加 `PutIntim(o, "key", Δ)` ★：好感键经这个收口 helper 写出——键是**字符串参数**
    而非索引器字面量，旧扫描认不出，会被误报成"渲染层读了没人写的键"。把 helper 的第 2 参
    也算进来：以后凡走收口 helper 的键自动对账，无需塞进 DYNAMIC_KEYS 白名单。
    """
    out = set(re.findall(r'\["([^"]+)"\]\s*=', text))
    out |= set(re.findall(r'new JObject\s*\{\s*\["([^"]+)"\]', text))
    # 收口 helper 写出的键（`PutIntim(o, "k", Δ)` / `PutIntimWithAfter(o, "k", v, a, b)`）
    out |= set(re.findall(r'PutIntim\([^,]+,\s*"([^"]+)"', text))
    with_after = set(re.findall(r'PutIntimWithAfter\([^,]+,\s*"([^"]+)"', text))
    out |= with_after
    # `PutIntimWithAfter` 还会发 `<key>_after`（回读真值），键名是拼出来的：
    #   `o[key + "_after"] = after;`
    # 不推导这一条，渲染层读 `<key>_after` 就会被误报成"没人写的键"。
    if 'key + "_after"' in text:
        out |= {k + "_after" for k in with_after}
    # `intimPair["buyer_to_seller"] = ...` 这种把键写在**局部聚合对象**上的形态
    out |= set(re.findall(r'intimPair\["([^"]+)"\]\s*=', text))
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
    "inspect_unit": [" JObject InspectUnit(JObject args)",
                     " JObject PageLogs(JObject snap, string filter, int page, int sinceMonth = 0, int untilMonth = 0)"],
    "search_units": [" JObject SearchUnits(JObject args)"],
    "query_world": [" JObject QueryWorld(JObject args)",
                    " JObject QueryWorldEvents(JObject args)",
                    " JObject QueryWorldRankings(JObject args)",
                    " JObject QueryWorldPlaces(string region, string cat, string name = null)",
                    # 10-29：detail/region/sect/master/post 这些参数由这批方法读
                    # （11-20 拆分后它们在 SectQueryTool/ActionTool 两个类里，
                    #  签名也变成收 `args` —— 扫描面按**签名**找，所以要跟着改）
                    " JObject QueryWorldSects(JObject args, string region = null, string name = null)",
                    " JObject QueryWorldRegions()"],
    "social_relation": [" JObject SocialRelation(JObject args)"],
    "movement": [" JObject Movement(JObject args)"],
    "world_ai_action": [" JObject WorldAiAction(JObject args)",
                        # 10-29 宗门人事：reason / sect / post / master / region 在这里读
                        " JObject SectJoin(WorldUnitBase npc, JObject args)",
                        " JObject SectLeave(WorldUnitBase npc, JObject args)"],
    "economy_item": [" JObject EconomyItem(JObject args)",
                     " JObject GiveLingshiOnly(WorldUnitBase npc, WorldUnitBase player, JArray items, string via, string target)"],
    "trade": [" JObject Trade(JObject args)"],
    "item_acquire": [" JObject ItemAcquire(JObject args)",
                     # 10-15 多件讨要：`items` 由这个 helper 读（不列进来会被判"死参数"）
                     " List<AcquireItem> ParseAcquireItems(JObject args)"],
    # 10-17 组队：`ToolExecutor.Team` 只是分发 + 弹窗，真正读参/写结果在 TeamTool 里，
    # 所以扫描面必须包含**两个文件**（`_method_body` 按签名找，TeamTool 那几条是静态方法）。
    "team": [" JObject Team(JObject args)",
             " JObject Execute(string op, WorldUnitBase self, WorldUnitBase counterparty,",
             " JObject Invite(WorldUnitBase self, WorldUnitBase who, string whoName)",
             " JObject Dismiss(WorldUnitBase self, WorldUnitBase who, string whoName)",
             " JObject Status(WorldUnitBase self, JObject args)"],
}


# ---------------------------------------------------------------------------

def test_dispatch_matches_tool_order():
    """C# Execute 的 case 分支 == schemas.TOOL_ORDER（少一个 = 模型能调但没人接）。"""
    S = _schemas()
    src = _cs_tools.tool_src()
    body = _method_body(src, "public JObject Execute(string name, JObject args)")
    cases = re.findall(r'case "([^"]+)":', body)
    assert cases == S.TOOL_ORDER, f"C# 分发 {cases} != TOOL_ORDER {S.TOOL_ORDER}"


def test_every_schema_param_is_read_by_csharp():
    """schema 声明的每个参数 C# 都必须真的读 —— 否则模型填了也没用（死参数）。"""
    S = _schemas()
    src = _cs_tools.tool_src()
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
    src = _cs_tools.tool_src()
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
    src = _cs_tools.tool_src()
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
    # `BattleOutcome.cs` 也在扫描面里（09-22）：战斗类工具的结果**不是** ToolExecutor 发的，
    # 而是那条链收口时由它组 `{player, lines, verdict_page, crossed_battle…}` 补发 response。
    # 少了这个文件，渲染层读 `player` 就会被当成"读了个没人写的键"——反过来也一样漏。
    # `PutIntim(o, "key", Δ)` 这类收口 helper 的键名要能被看见 ⇒ 用**原样**源码
    # （flat_src 会把 `IntimService.PutIntim` 抹成 `PutIntim`，正则仍然命中，但用原样更稳）
    ck = (_keys_written(_read(CS / "game" / "UnitSnapshot.cs"))
          | _keys_written(_cs_tools.tool_src())
          | _keys_written(_read(CS / "drama" / "BattleOutcome.cs"))
          # 10-17：`team` 的结果由 TeamTool 组装（ToolExecutor.Team 只负责分发/弹窗），
          # 少了这个文件，`members`/`joined`/`disbanded` 就会被当成"没人写的键"。
          | _keys_written(_read(CS / "tools" / "TeamTool.cs")))
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
    da = _read(ROOT / "agent" / "dialogue_agent.py")
    assert 'args["initiator"] = self.id' in da, \
        "harness 不再注入 initiator —— 5 个动作工具会全部以「未找到发起方」失败"
    src = _cs_tools.tool_src()
    for tool in ("social_relation", "movement", "world_ai_action", "economy_item", "item_acquire"):
        read = _args_read(src, CS_METHODS[tool])
        assert "initiator" in read, f"{tool} 不再读 initiator（actor 无从确定）"


def test_inventory_top_bound_matches_schema():
    """`inventory_top` 的上限必须与 schema 的 maximum 一致（否则静默变成"全量"）。"""
    S = _schemas()
    mx = S._ALL_MAP["inspect_unit"]["function"]["parameters"]["properties"]["inventory_top"]["maximum"]
    src = _cs_tools.tool_src()
    body = _method_body(src, " JObject InspectUnit(JObject args)")
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
    ck = _keys_written(_read(CS / "game" / "UnitSnapshot.cs")) | _keys_written(_cs_tools.tool_src())
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


# ---------------------------------------------------------------------------
# 动作工具的对手方 `target`（09-16：多聊支持）
# ---------------------------------------------------------------------------

TARGET_TOOLS = ["social_relation", "movement", "world_ai_action", "economy_item", "item_acquire"]

# C# 侧"非玩家分支"的锚点（读了 target 却不做区分 = 静默落回玩家，最危险）
CS_NPC_BRANCHES = {
    "social_relation": ["DirectEstablish", "DirectBreak", "DirectMarry"],
    "economy_item": ["if (toNpc)", "AddProps"],
    "item_acquire": ["if (toNpc)", "TakePropsFromBag"],
    "world_ai_action": ["只能是玩家"],
    # movement 没有"做不了的 op"：summon/teleport 都支持 NPC（位移是纯引擎动作，没有窗）。
    #
    # ★ 判据在 2026-10-22 整条换掉了，别照抄旧版 ★
    # 旧判据是"召唤分支按对手方分派：玩家走 `UnitActionMovePlayer`、NPC 走 `UnitActionMoveNPC`"。
    # 那两个类**都是赶路动作**：`Init` 按距离算 `day`（61 格 ⇒ 15 天），而 `UnitActionBase::Create`
    # 只看 `day > 0` 就扣 `residueDay`（**即使 `OnCreate` 走瞬移分支**）⇒ 一次传送把 NPC 当月
    # 行动力清零，紧接着的论道被 `Discovery.IsCreate` 的 `IsDayCost(5)` 拒掉（无 UI、无提示）。
    # 现在 summon / teleport 一律走 `UnitActionSetPoint`（引擎的零成本瞬移：无 `Init` ⇒ `day=0`
    # ⇒ 不扣天数；`OnCreate` 内调 `UnitInfoData::SetPoint` 维护坐标与格子索引；自带 12 条业务校验）。
    # 完整因果链见 `docs/archive/2026-10-22-论道静默失败/修法与清理.md`。
    # 锚点随之改为：① 仍按对手方解析（`cpName` 空 ⇒ 对手方是玩家）；② 走 SetPoint 而非 MoveNPC。
    "movement": ["ResolveCounterparty", "UnitActionSetPoint"],
}


def test_action_tools_declare_optional_target():
    """5 个动作工具都要有**可选** `target`（缺省 = 玩家），且不进 `required`。

    缺省语义只写在 schema 里（模型看不到代码）：措辞丢了，模型会以为 target 必填，
    于是要么不传（走玩家，行为对）要么编一个对象（行为错）。两件事一起钉。
    """
    S = _schemas()
    for tool in TARGET_TOOLS:
        params = S._ALL_MAP[tool]["function"]["parameters"]
        props = params["properties"]
        req = params.get("required") or []
        assert "target" in props, f"{tool} 没声明 target（模型无法表达「对某个 NPC」）"
        assert "target" not in req, f"{tool}: target 不该进 required（缺省 = 玩家）"
        desc = props["target"].get("description", "")
        assert "玩家" in desc and ("不填" in desc or "不要传" in desc), \
            f"{tool}.target 的描述没写缺省语义：{desc!r}"
    # trade 已有 seller/buyer；只读三件的 target 是"查询目标"（另一层语义）→ 都不该加动作对手方
    for tool in ("trade", "search_units", "query_world"):
        props = S._ALL_MAP[tool]["function"]["parameters"]["properties"]
        assert "target" not in props, f"{tool} 不该声明动作对手方的 target"


def test_csharp_target_paths_are_explicit():
    """C# 侧：5 个方法都真读 `target`、都走统一解析，且**绝不静默落回玩家**。

    静默落回是这条链上最危险的失败模式（"想对张三动手 → 打到玩家身上"、数据写错对象、
    不报错），所以三条一起钉：① 每个方法真读 `args["target"]`；② 交给 `ResolveCounterparty`；
    ③ 每个工具都有非玩家分支（直写或显式拒绝），不留"读了但没用"的空档。
    """
    src = _cs_tools.tool_src()
    for tool, anchors in CS_NPC_BRANCHES.items():
        body = _method_body(src, CS_METHODS[tool][0])
        # ★ 09-17：`args["target"]` 的**读取**搬进了共用 helper（各工具本体只剩一行调用），
        #   但"这个工具必须走统一解析"这条判据反而更要紧 —— 所以下面那条断言是主判据，
        #   而 target 确实被读到由 `test_every_schema_param_is_read_by_csharp` 统一保证。
        assert "ResolveCounterparty(" in body, f"{tool} 没走统一的对手方解析"
        for a in anchors:
            assert a in body, f"{tool} 缺少非玩家分支锚点：{a}"
    hb = _method_body(src, " WorldUnitBase ResolveCounterparty(JObject args, out string counterparty, out JObject fail)")
    assert "未找到" in hb and "fail =" in hb, "ResolveCounterparty 解析失败时没有显式 fail"
    assert "IsPlayerUnit" in hb, "ResolveCounterparty 没把「填了玩家名」归一成玩家"
    # ★ 09-17 新增的两条（都不许被后人"顺手简化"掉）：
    #   ① 精确 id 优先：有 id 就按 id 解析，没有/失效才按名 —— 这是"重名不画错人"的全部机制。
    assert "ResolveArgExact(args, \"target\", targetName)" in hb, \
        "ResolveCounterparty 没有优先用 target_id（退回按名 = 重名时落到别人身上）"
    #   ② 模型把 unitID 填进 target 时，要给一句**能自纠**的话（删掉 ResolveInner ③ 之后
    #      它不再"碰巧能用"，不提示的话模型只会收到一句看不出原因的"未找到"）。
    assert "UnitIdRegistry.NameOf(targetName)" in hb, \
        "unitID 误填进 target 时没有给出可自纠的提示"
    # ③ 发起方同样走精确（`initiator_id` 是 harness 注入的，模型看不到这个键）
    ab = _method_body(src, " WorldUnitBase ResolveActor(JObject args)")
    assert 'ResolveArgExact(args, "initiator", initiator)' in ab, \
        "发起方没有走精确解析（5 个动作工具的 actor 都靠它）"
    # ④ 两个方向都必须**回退按名**，且回退要留痕（否则"存档/身份同步出了问题"无从归因）
    ra = _method_body(src, " WorldUnitBase ResolveArgExact(JObject args, string key, string name)")
    assert 'args[key + "_id"]' in ra, "ResolveArgExact 没读 `<键>_id`"
    assert "ResolveEx(nm, id" in ra or "ResolveEx(" in ra, "ResolveArgExact 没走 unitID 精确路径"
    assert "回落按名" in ra, "id 失效时的回落要留痕"


def test_npc_branches_skip_player_windows():
    """非玩家分支不得开确认窗 / 占挂起坑 —— 窗是给玩家点的，NPC 之间弹了没人点（挂到 120s 超时）。

    这条是"免窗直写"这个产品口径的源码级哨兵：谁将来图省事把非玩家分支并回窗路径，立刻红。
    """
    src = _cs_tools.tool_src()
    eb = _method_body(src, " JObject EconomyItem(JObject args)")
    assert "hasProps && !toNpc" in eb, "economy_item 对 NPC 仍会占 DramaGate 挂起坑"
    sb = _method_body(src, " JObject SocialRelation(JObject args)")
    for m in ("DirectEstablish", "DirectBreak", "DirectMarry"):
        assert m in sb, f"social_relation 缺少 {m} 分支"
        db = _method_body(src, f" JObject {m}(")
        assert "ShowConfirm" not in db and "Claimed(" not in db, f"{m} 里出现了确认窗"
    print("✓ 非玩家分支：不占挂起坑、不开确认窗")


def test_item_acquire_lingshi_relaxed_only_for_npc_ask_for():
    """灵石讨要：偷窃恒拒；讨要对 NPC 免窗直转（09-17 提案制），对玩家走确认窗（2026-09-21）。

    四个面必须同时成立，谁被顺手改坏都会静默改变"钱袋子"的规则：
      ① 偷窃灵石一律拒绝（货币不是道具实例，原生 StealItem 只收 soleID，曾真机 NRE）；
      ② 对 NPC 的 ask_for 走 TransferLingshi 免窗直转（足额才转，与道具 ask_for 同口径）；
      ③ 对玩家的 ask_for 走自制确认窗（DramaItemAcquireBase 专属段），点「给他」才转账；
      ④ 确认窗正文里玩家是"给"的一方（立绘 left=玩家，09-12 教训）。
    """
    src = _cs_tools.tool_src()
    body = _method_body(src, " JObject ItemAcquire(JObject args)")
    assert 'if (op != "ask_for")' in body, "灵石偷窃的拒绝条件被改了（货币不能偷）"
    assert "TransferLingshi" in body, "NPC 讨要灵石的分支没了（收下灵石赠礼会永远失败）"
    assert "无法偷窃" in body, "灵石偷窃的拒绝分支被误删"
    assert "DramaItemAcquireBase" in body, "对玩家讨要灵石没用 item_acquire 专属确认窗段"
    assert "想向你讨要灵石" in body, "确认窗正文变了（玩家应是给的一方）"
    print("✓ 灵石：偷窃恒拒；讨要 NPC 免窗直转、玩家确认窗")
