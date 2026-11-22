"""关系缔结的引擎裁决契约（09-17 两起事故：先是"静默失败"，再是"关系被强拆"）。

**事故一（10:50 收徒静默失败）**：确认窗弹出、玩家点"同意"，工具回
`你与姜萌结为师徒，但状态校验未通过（可能未生效，需核对）。` —— 模型据此宣布"师徒名分结下了"，
而关系簿一字未动。根因：`CreateAction` 的返回码没人接、`isTip=false` 连游戏自己的提示都关了、
渲染层只剩一句"可能未生效"。

**事故二（16:14 收徒把夫妻关系拆了）**：加了"预检"之后同一句 op **变成成功**，而用户原有的
**夫妻**关系被清掉、换成了师徒。根因是两条叠在一起：

  1. **语义读反了**：`UnitActionRelationSet.IsCreate` 返回 **0 = 同意 / 非 0 = 拒绝原因码**
     （`WorldUnitBase.CreateAction` @0x32B8D40：`code = IsCreate(isTip); if (code != 0) return code;`
     然后才 `Create();`）。旧注释按"≥1 = 已执行、0 = 拒绝"写 —— 于是引擎那句
     "不能建（码 3：两人之间已经有一条关系）"被当成了"可以建"。
  2. **预检与落刀共用同一个 act 实例**：`UnitActionBase.IsCreate` @0x35083B0 会把该实例的
     `isCreateCall` 置 true，而 `CreateAction` 见到这个标记就**跳过全部校验直接 `Create()`** ——
     预检反而把引擎的拒绝撬成了强行落刀；`OnCreate` 又是 `ClearRelation(unit,toUnit)` 双向清理 +
     写新关系（`GetRelation` 返回单个枚举、优先级 夫妻 > 道侣 > 师父 > 徒弟）⇒ **覆盖，不是叠加**。

**本文件锁住的东西**：
  · 引擎码表（C# 注释里的权威版 + Python 渲染措辞表）必须一致，且 **0 = 同意**；
  · **预检一律一次性实例**，落刀只调一次并把返回码带进载荷；预检拒绝 ⇒ 不落刀（`create_ret = -1`）；
  · `diag` 只报事实（境界 + 师徒位占用），读不到的计数是 `-1`（未知），不写 0 冒充"没有"；
  · 渲染层：引擎拒绝一律"未能结下"，`create_ret == 0` 却读不回也不写成成功；成功时若
    `relation_before != relation_after` 必须点明"旧关系被换掉了"。
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"

from agent_loop.tools import text_render as tr  # noqa: E402  （conftest 已把父目录放上 sys.path）
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）


def _cs(name: str) -> str:
    return (CS / name).read_text(encoding="utf-8")


def _body(src: str, sig: str) -> str:
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


def _on_ok(text: str) -> str:
    """StartRelationAction 的 onOk 闭包（从 `onOk: () =>` 到 `okTip:`）。

    必须先在**这个方法体**里找 —— 文件里 `onOk: () =>` 有好几处（结婚、赠礼…），
    直接 `src.index` 会切到别人的闭包上去，于是测试"绿"得毫无意义。
    """
    return text[text.index("onOk: () =>") : text.index("okTip:")]


def _start_relation() -> str:
    return _body(
        _cs_tools.tool_src(),
        " JObject StartRelationAction(WorldUnitBase npc, WorldUnitBase player, "
        "UnitRelationType type, string op, string askOverride = null)",
    )


def _direct_establish() -> str:
    return _body(
        _cs_tools.tool_src(),
        " JObject DirectEstablish(WorldUnitBase npc, WorldUnitBase other, "
        "UnitRelationType type, string op, string cpName)",
    )


def _probe_helper() -> str:
    return _body(
        _cs_tools.tool_src(),
        " int ProbeRelationSet(WorldUnitBase actor, WorldUnitBase other, "
        "UnitRelationType type, int addClose)",
    )


def _break_body() -> str:
    return _body(
        _cs_tools.tool_src(),
        " JObject StartBreakWith(WorldUnitBase npc, WorldUnitBase target, "
        "string op, string askOverride = null)",
    )


def _break_fields() -> str:
    return _body(
        _cs_tools.tool_src(),
        " string BreakFieldsDirect(string op, WorldUnitBase npc, WorldUnitBase target)",
    )


def _break_still() -> str:
    return _body(
        _cs_tools.tool_src(),
        " bool BreakStillRelated(string op, WorldUnitBase npc, WorldUnitBase target)",
    )


def _break_rel_name() -> str:
    return _body(_cs_tools.tool_src(), " string BreakRelName(string op)")


def _r(data: dict, args: dict | None = None) -> str:
    return tr.render("social_relation", args or {"op": "shou_tu"}, {"success": True, "data": data})


# 真机同款载荷（09-17 16:14 那次"成功"的收徒：引擎码 3 = 两人之间已经有一条关系，当时是夫妻）
REAL_REFUSAL = {
    "op": "shou_tu", "target": "姜萌", "relation": "师徒", "verified": False,
    "precheck": 3, "create_ret": -1, "relation_before": "夫妻", "relation_after": "夫妻",
    "diag": {"npc_realm": "炼气境中期", "player_realm": "炼气境初期",
             "npc_students": 0, "npc_masters": 0, "player_students": 0, "player_masters": 0},
}


# ---------------------------------------------------------------------------
# 引擎码：0 = 同意；非 0 = 拒绝（两起事故的共同根因）
# ---------------------------------------------------------------------------

def test_engine_code_table_says_zero_means_go():
    """权威表在 C# 注释里，且必须写明 **0 = 同意 / 非 0 = 拒绝原因码** 并列出 2/3/5/6/7/8。

    注释就是这份契约的说明书：09-17 那次强拆，起因就是注释里把两者写反了。
    """
    src = _cs_tools.tool_src()
    i = src.index("引擎关系裁决码表")
    table = src[i : src.index(" int ProbeRelationSet(")]
    assert "**0 = 引擎同意（可创建）；非 0 = 引擎拒绝，数字就是原因码。**" in table, \
        "注释里没有把 0/非 0 的语义写死 —— 上一版就是这里写反的"
    for code, frag in (("2", "目标是自己"), ("3", "已经有一条关系"), ("4", "认义父母"),
                       ("5", "同性"), ("6", "异性"), ("7", "已婚"), ("8", "还不是道侣"), ("9", "解除")):
        assert re.search(rf"^\s*//\s*{code}\s+.*{frag}", table, re.M), f"注释表缺码 {code}（{frag}）"
    # 反向读法的原文一句都不许留（它会被下一个人照抄）
    # 注意别误伤正确句"非 0 = 引擎拒绝"：钉的是旧注释里那几句**完整读法**。
    for wrong in ("0 = 引擎拒绝、什么都不做", "≥1 = 当场执行", "0 = 引擎判不能建",
                  "1=触发，0=创建被拒"):
        assert wrong not in src, f"注释里还留着反的语义：{wrong}"


def test_comment_table_and_renderer_table_agree():
    """C# 注释表（权威）与 Python 措辞表（`_RELATION_ENGINE_CODE`）必须同键 —— 两边各写一份会漂。"""
    src = _cs_tools.tool_src()
    table = src[src.index("引擎关系裁决码表") : src.index(" int ProbeRelationSet(")]
    cs_codes = {int(m.group(1)) for m in re.finditer(r"^\s*//\s*(\d+)\s+\S", table, re.M)}
    assert cs_codes >= {2, 3, 4, 5, 6, 7, 8, 9}, f"C# 注释表键不齐：{sorted(cs_codes)}"
    py_codes = set(tr._RELATION_ENGINE_CODE)
    assert py_codes == cs_codes | {0}, f"两张表不同步：C#={sorted(cs_codes)} Python={sorted(py_codes)}"


def test_zero_is_not_treated_as_refusal_anywhere():
    """`precheck == 0` / `create_ret < 1` 这种"0 = 拒绝"的判据必须全部消失。"""
    for name in ("tools/RelationTool.cs",):
        src = _cs(name)
        for bad in ("if (precheck == 0)", "precheck == 0", "create_ret < 1", "ret < 1"):
            assert bad not in src, f"{name} 里还有反的判据：{bad}"
        assert "if (precheck > 0)" in src, "预检拒绝没有按 `precheck > 0` 判"


# ---------------------------------------------------------------------------
# 预检：一次性实例 + 只问不落刀
# ---------------------------------------------------------------------------

def test_preflight_never_shares_the_instance_with_the_knife():
    """★ 事故二的直接成因：预检与落刀共用 act ⇒ `isCreateCall` 免检标记让落刀跳过全部校验。

    判据：`IsCreateAction` 只出现在一次性实例上；落刀的 `CreateAction` 一律**现 new 现用**，
    绝不复用任何变量（`CreateAction(act,` / `CreateAction(dact,` 这类写法一个都不许留）。
    """
    probe = _probe_helper()
    assert "actor.IsCreateAction(new UnitActionRelationSet(other, type, addClose), false)" in probe, \
        "预检没有用一次性实例（复用之 = 把 isCreateCall 免检标记递给落刀）"
    assert ".CreateAction(" not in probe.replace("IsCreateAction(", ""), "预检助手不该落刀"

    src = _cs_tools.tool_src()
    for line in src.splitlines():
        if "IsCreateAction(" not in line or line.lstrip().startswith(("//", "///", "*")):
            continue
        assert "IsCreateAction(new UnitActionRelationSet(" in line or "IsCreateAction(probe" in line, \
            f"`IsCreateAction` 用在了可复用的实例上：{line.strip()}"

    ok = _on_ok(_start_relation())
    assert "npc.CreateAction(new UnitActionRelationSet(player, type, addClose), true)" in ok, \
        "落刀没有现 new（复用预检实例 = 免检直通）"
    assert "IsCreateAction" not in ok, "onOk 里不该再问一次（问一次就污染一次）"
    direct = _direct_establish()
    assert "npc.CreateAction(new UnitActionRelationSet(other, type, addClose), false)" in direct, \
        "免窗落刀没有现 new"
    assert "IsCreateAction" not in direct, "免窗路径的预检该走 ProbeRelationSet（一次性实例）"

    b = _break_body()
    assert "npc.IsCreateAction(probe, false)" in b and "npc.CreateAction(act, false)" in b, \
        "解除路径的预检/落刀没分开实例"
    assert "npc.CreateAction(probe" not in b, "解除落刀复用了预检那个实例"


def test_preflight_refusal_does_not_reach_the_knife():
    """`precheck > 0`（引擎拒绝）⇒ 直接收口，**不落刀**，也不让玩家白点一次"同意"。"""
    ok = _start_relation()
    assert "if (precheck > 0)" in ok, "预检拒绝没有短路"
    i_cut = ok.index("if (precheck > 0)")
    i_create = ok.index("npc.CreateAction(new UnitActionRelationSet(player, type, addClose), true)")
    assert i_cut < i_create, "顺序不对：必须先判拒绝 → 才谈落刀"
    cut = ok[i_cut:i_create]
    for key, value in (("precheck", "precheck"), ("create_ret", "-1"),
                       ("relation_before", "relBefore"), ("relation_after", "RelationOf(npc, player)"),
                       ("diag", "RelationDiag(npc, player)"), ("verified", "false")):
        assert f'["{key}"] = {value}' in cut, f"预检拒绝的收口缺 {key}"
    # 弹窗必须在预检之后（引擎说不能建就不该弹窗）
    assert ok.index("ProbeRelationSet(") < ok.index("ShowConfirm("), \
        "预检在弹窗之后 —— 玩家会白点一次「同意」才被告知不行"
    direct = _direct_establish()
    assert "if (precheck > 0)" in direct and '["create_ret"] = -1' in direct, \
        "免窗路径的预检拒绝没走同款收口"


def test_preflight_unknown_still_goes_to_the_knife():
    """预检**问不出来**（-2，绝不能等价于"不能建"）⇒ 照旧落刀，让落刀那次自己判。

    实证：`UnitActionRoleGive.IsCreate` 单独调用会 NRE（依赖 OnCreate 期才就绪的瞬态字段）。
    """
    probe = _probe_helper()
    assert "return -2;" in probe and "catch" in probe, "预检助手没把「没问出来」与「同意」分开"
    # 短路条件是 `> 0`：-2 与 0 都会继续走到落刀
    assert "if (precheck > 0)" in _start_relation(), "短路条件不是 `> 0`（-2 会被拦下）"


def test_knife_verdict_is_captured_and_passed_through():
    """落刀只调一次，返回码必须接住并进载荷；落刀前的现状快照在落刀**之前**取。"""
    ok = _on_ok(_start_relation())
    assert "createRet = npc.CreateAction(new UnitActionRelationSet(player, type, addClose), true)" in ok, \
        "落刀的引擎码没人接（事故一的形态）"
    for key, value in (("create_ret", "createRet"), ("precheck", "precheck"),
                       ("relation_before", "relBeforeKnife"), ("relation_after", "RelationOf(npc, player)"),
                       ("diag", "RelationDiag(npc, player)")):
        assert f'["{key}"] = {value}' in ok, f"{key} 没进载荷（模型/渲染层看不到）"
    snap = "string relBeforeKnife = RelationOf(npc, player);"
    assert snap in ok and ok.index(snap) < ok.index("npc.CreateAction(new UnitActionRelationSet("), \
        "relation_before 不是在落刀**前**取的：引擎一旦覆盖旧关系就查不到原值了"
    direct = _direct_establish()
    for key in ("create_ret", "relation_before", "relation_after", "diag", "precheck"):
        assert f'["{key}"]' in direct, f"免窗路径漏了 {key}"


def test_diag_reports_realm_and_seat_occupancy_only():
    """`diag` = 境界 + 师徒位占用（只报事实；引擎的硬门槛是"已有关系"，另由引擎码交代）。"""
    d = _body(_cs_tools.tool_src(), " JObject RelationDiag(WorldUnitBase npc, WorldUnitBase target)")
    for key in ("npc_realm", "player_realm", "npc_students", "npc_masters",
                "player_students", "player_masters"):
        assert f'["{key}"]' in d, f"diag 缺 {key}"
    assert "UnitSnapshot.RealmText(npc)" in d and "UnitSnapshot.RealmText(target)" in d, \
        "境界没走 UnitSnapshot.RealmText —— 另起一套读数必然与 brief/L1 漂移"


def test_realm_text_reuses_the_panel_reader():
    """`RealmText` 必须复用 `RealmOf`（面板口径 + 09-13 越界事故的修复处），不许旁路。"""
    src = _cs("game/UnitSnapshot.cs")
    body = _body(src, "public static string RealmText(WorldUnitBase u)")
    assert "RealmOf(" in body, "RealmText 没有复用 RealmOf（境界会与面板/brief 不一致）"
    assert "GetGradeName(" not in body, "RealmText 直接查 GetGradeName = 09-13 那次越界错读的老路"


def test_rel_count_never_fakes_zero():
    """读不到的计数是 -1（未知）—— "读不到"与"没有"在这条链上是两件事。

    若把 null/异常吞成 0，渲染层就会写出"你没有师傅"这种**凭空的断言**，
    比不报更坏：它会把模型引到错误的结论上。
    """
    body = _body(_cs_tools.tool_src(), " int RelCount(object list)")
    assert "list == null ? -1" in body and "catch { return -1; }" in body, \
        "RelCount 把读不到的情况冒充成 0"


def test_renderer_reads_only_keys_the_establish_path_emits():
    """渲染层读的键必须在 C# 里真的写出来（否则模型永远看不到 —— 本仓踩过四次）。

    针对本文件的两个 helper 做**定向**核对：它们不在 `render_social` 的函数切片里，
    全仓那条 `test_no_renderer_reads_a_key_csharp_never_emits` 扫不到。
    """
    renderer = (ROOT / "tools" / "text_render.py").read_text(encoding="utf-8")
    i = renderer.index("def _social_refuse_facts(")
    j = renderer.index('@renderer("social_relation")')
    helper = renderer[i:j]
    written = set(re.findall(r'\["([^"]+)"\]\s*=', _cs_tools.tool_src()))
    read = set(re.findall(r'\.get\(\s*"([^"]+)"', helper)) - {"op"}
    missing = sorted(k for k in read if k not in written)
    assert not missing, f"渲染层读了 C# 不发的键：{missing}"
    assert read, "没扫到任何读取键，测试自身失效"


def test_all_five_establish_ops_share_the_diagnostics():
    """「其余关系」不是没做 —— 五类缔结**共用** `StartRelationAction` / `DirectEstablish`。

    这条钉的是"别再各写一份"：某个 op 一旦绕过共用方法（自己建动作），诊断立刻失效。
    """
    src = _cs_tools.tool_src()
    for op, typ in (("jie_yuan", "Lover"), ("bai_shi", "Master"), ("shou_tu", "Student"),
                    ("ren_yi_fu_mu", "ParentBack"), ("jie_yi", "BrotherBack")):
        case = src[src.index(f'case "{op}"'):]
        case = case[: case.index("case ", 10)] if "case " in case[10:] else case
        assert f"StartRelationAction(wub, player, UnitRelationType.{typ}" in case, \
            f"{op} 没走共用的 StartRelationAction（诊断会缺）"
        assert f"DirectEstablish(wub, player, UnitRelationType.{typ}" in case, \
            f"{op} 没走共用的免窗 DirectEstablish（NPC↔NPC 诊断会缺）"


def test_break_ops_report_the_verdict_but_never_block_on_it():
    """解除类：引擎码**只报不拦** —— 下面还有字段直写兜底（解除是单方面行为）。

    并且 `create_ret` 的读法要跟缔结类一致：**0 = 原生动作已执行**（旧注释写的「1=触发」是反的）。
    """
    b = _break_body()
    assert "npc.IsCreateAction(probe, false)" in b, "解除路径没有预检（isTip 必须 false）"
    assert "precheck" in b, "解除结果没带引擎码（没法诊断「为什么走了直写」）"
    # 预检与落刀之间**不许有任何提前 return**（一旦短路，字段直写兜底会被跳过 ⇒ 解除静默失效）
    # 只看代码行：注释里正好写着"绝不能据此提前 return"这句人话。
    after = b[b.index("int bwPre = -2"):]
    before_knife = after[: after.index("npc.CreateAction(act, false)")]
    code = "\n".join(l for l in before_knife.splitlines() if not l.lstrip().startswith("//"))
    assert "return" not in code, \
        f"解除路径在落刀前提前收口了 —— 字段直写兜底会被跳过：{[l for l in code.splitlines() if 'return' in l]}"
    assert "if (created == 0 && !stillAfterNative)" in b, \
        "原生解除的成败判据不是 `created == 0`（0 才是「引擎执行了」）"
    assert "if (created == 1)" not in b, "还留着反的判据 `created == 1`"


def test_ren_yi_fu_mu_can_be_dissolved():
    """认义父母必须能解除，且兜底清的是**互逆的两种容器**。

    引擎事实（`types36_fulldll.txt` 关系面板逐字读出）：`parentBack` = 我的义父母、
    `childrenBack` = 我的义子女；`UnitActionRoleBreakWith` **不带关系类型**，原生动作
    不保证覆盖义亲。所以字段兜底必须同时清两侧的两种容器 —— 只清 `npc.parentBack`
    会留下"对方仍把 npc 当义亲"的半解除状态，而且不报错。
    """
    op = "jie_chu_ren_yi_fu_mu"
    from agent_loop.tools import schemas as S
    spec = S.SOCIAL_RELATION["function"]["parameters"]["properties"]["op"]
    assert op in spec["enum"], "schema 没列出解除义父母"
    assert op in spec["description"], "op 释义里没有它（模型不知道该填什么）"

    src = _cs_tools.tool_src()
    i = src.index(f'case "{op}"')
    case = src[i: src.index('case "jie_yi"', i)]
    assert "UnitRelationType.ParentBack" in case, "解除前没判双方是否义亲"
    assert "DirectBreak(wub, player, op, cpName)" in case, "NPC↔NPC 没走免窗直写"
    assert "StartBreakWith(wub, player, op, askText)" in case, "玩家路径没走确认窗"

    fields = _break_fields()
    seg = fields[fields.index(f'case "{op}"'):]
    seg = seg[:seg.index("default:")]
    for token in ("nr.parentBack.Remove(targetId)", "tr.childrenBack.Remove(npcId)",
                  "nr.childrenBack.Remove(targetId)", "tr.parentBack.Remove(npcId)"):
        assert token in seg, f"字段兜底缺 {token}（会留下半解除状态）"

    still = _break_still()
    seg2 = still[still.index(f'case "{op}"'):]
    seg2 = seg2[:seg2.index("default:")]
    assert "tr.IsRelation" in seg2 and "ChildrenBack" in seg2, "读回只查了单侧/单容器"

    assert 'return "义父母"' in _break_rel_name(), "解除文案没映射到义父母"

    from agent_loop.room.room_flow import REL_BREAK_CN, REL_BREAK_OPS
    assert op in REL_BREAK_OPS and REL_BREAK_CN.get(op), "群聊解除类没纳入公开叙述"
    assert op in tr._BREAK_OPS and tr._SOCIAL_OP_CN.get(op) == "义父母"
    t = _r({"op": op, "target": "张三", "relation": "义父母", "readback_still_related": False})
    assert "解除义父母关系" in t and "读回确认关系已解除" in t, t


def test_marry_reports_the_same_state_snapshot():
    """结婚走字段直写（没有引擎动作 ⇒ 无 precheck/create_ret），但现状快照要齐。"""
    src = _cs_tools.tool_src()
    i = src.index('case "marry"')
    seg = src[i: src.index('case "divorce"')]
    for key in ("relation_before", "relation_after", "diag"):
        assert f'["{key}"]' in seg, f"marry 分支缺 {key}"
    assert "RelationOf(wub, player)" in seg and "RelationDiag(wub, player)" in seg


def test_give_lets_the_engine_check_and_consumes_its_code():
    """赠礼**不再免检、也不再自建预检**：让引擎在自己的时机过门，我们只消费返回码。

    09-24 真机两轮对照（`_diag_give_recheck.txt` 开关实验）定案：
      · 默认轮（曾设 `isCreateCall = true` 免检）：`CreateAction 返回码=0` → 窗弹出 → `receiveProps=1`；
      · recheck 轮（**不设免检**）：`实例.unit=(null) isCreateCall=False`，整栈 6 枚全给，
        `CreateAction 返回码=0` → **窗照样弹出** → `receiveProps=1`。
    ⇒ 引擎的门是好的（它在 `CreateAction` 内部调 `IsCreate`，那时发起方已绑）；我们手动调
      `giveAction.IsCreate(false)` 只会拿到 NRE（实例 `unit=(null)`，依赖 OnCreate 期才就绪的瞬态字段），
      而且它有"顺手把 `isCreateCall` 置真"的副作用 —— 09-17 关系强拆事故的同一形状。
    所以这里钉死：**两样都不许回来**，唯一的判据是 `CreateAction` 的返回码。
    """
    src = _cs_tools.tool_src()

    # ① 不许再设免检（只看代码行：注释里正解释着"为什么删掉它"）
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("//"):
            continue
        assert ".isCreateCall = true" not in s, f"免检又回来了：{s}"
    # ② 不许再自己调 IsCreate 预检（`IsCreateAction` 是关系那条路的合法助手，不管）
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("//") or "IsCreateAction" in s:
            continue
        assert ".IsCreate(" not in s, f"自建预检又回来了（只会 NRE，还可能污染 isCreateCall）：{s}"

    # ③ 唯一判据 = 返回码，且拒绝一律回滚 + 如实报
    i = src.index("var giveAction = new UnitActionRoleGive(")
    tail = src[i:]
    assert "int giveRet;" in tail and "wub.CreateAction(giveAction, true)" in tail, \
        "赠礼没消费 CreateAction 的返回码（引擎拒绝会被当成成功）"
    assert "if (giveRet != 0)" in tail, "返回码非 0 没有短路"
    cut = tail[tail.index("if (giveRet != 0)"):]
    cut = cut[: cut.index("\n                }")]
    for key in ("RollbackGive(wub, giveList)", "Refused(\"赠送\""):
        assert key in cut, f"引擎拒绝那条收口缺 {key}"

    # ④ 结论要写在旁边（否则下一个人会把免检"顺手加回来"救火）
    ctx = src[i: i + 2600]   # 结论注释紧跟在 `new UnitActionRoleGive(...)` 之后
    assert "引擎的门是好的" in ctx and "09-24" in ctx, \
        "免检删除的理由（09-24 两轮对照结论）没写在代码旁边"


# ---------------------------------------------------------------------------
# 渲染层：如实回执
# ---------------------------------------------------------------------------

def test_engine_refusal_is_reported_as_a_refusal():
    """真机同款载荷（引擎码 3 = 两人已有关系）→ 必须出现码 + "未能结下" + 现状事实。

    断言刻意钉**判决句的开头**：末尾那句"请如实告诉玩家未能结下"里也有"未能结下"四个字，
    只查子串的话，"已经结下（引擎码 3）……请如实告诉玩家未能结下"这种自相矛盾的回执会蒙混过关。
    """
    t = _r(dict(REAL_REFUSAL))
    assert t.startswith("你与姜萌的「师徒」未能结下"), t
    assert "已经结下" not in t, t
    assert "引擎码 3" in t and "已经有一条关系" in t, t
    assert "未落刀" in t and "关系簿未改动" in t, t
    assert "已是夫妻" in t, t                          # 真正的原因：旧关系还在
    assert "炼气境中期" in t and "炼气境初期" in t, t      # 现状事实照旧报
    assert "结为师徒。" not in t and "结为师徒，" not in t, t   # ★ 绝不能读成"结下了"


def test_knife_refusal_and_unread_are_rendered_apart():
    """落刀被驳回（码非 0）与"引擎执行了却读不回"（码 0）必须是两种话。"""
    t = _r({"op": "shou_tu", "target": "姜萌", "relation": "师徒", "verified": False,
            "precheck": -2, "create_ret": 3})
    assert t.startswith("你与姜萌的「师徒」未能结下"), t
    assert "驳回了这次关系变更" in t and "引擎码 3" in t and "未落刀" not in t, t
    assert "已经结下" not in t and "结为师徒。" not in t, t
    t2 = _r({"op": "shou_tu", "target": "姜萌", "relation": "师徒", "verified": False,
             "precheck": 0, "create_ret": 0})
    assert "读不回" in t2 and "人工核对" in t2, t2
    assert "结为师徒。" not in t2, t2


def test_old_payload_keeps_the_old_wording():
    """旧 C# 载荷（无 `create_ret` 也无 `precheck`）→ 旧文案原样保留：两侧升级不同步时不回归。"""
    t = _r({"op": "shou_tu", "target": "姜萌", "relation": "师徒", "verified": False})
    assert t == "你与姜萌结为师徒，但状态校验未通过（可能未生效，需核对）。", t


def test_success_is_unchanged_but_replacement_is_called_out():
    """成功文案不变；但 `relation_before != relation_after` 时必须点明旧关系被换掉了。

    事故二真正伤人的地方就是这里：夫妻被换成师徒，而回执只说"结为师徒"。
    """
    assert _r({"op": "jie_yuan", "target": "姜萌", "relation": "道侣", "verified": True,
               "relation_before": "陌生", "relation_after": "道侣"}) == "你与姜萌结为道侣。"
    t = _r({"op": "shou_tu", "target": "姜萌", "relation": "师徒", "verified": True,
            "relation_before": "夫妻", "relation_after": "师徒"})
    assert t.startswith("你与姜萌结为师徒。"), t
    assert "原有的「夫妻」" in t and "换成了「师徒」" in t, t
    # 结婚是"道侣→夫妻"的正常升级，也要看到这句（同一套判据，不搞特例）
    t2 = _r({"op": "marry", "target": "姜萌", "relation": "夫妻", "verified": True,
             "relation_before": "道侣", "relation_after": "夫妻"})
    assert "原有的「道侣」" in t2, t2
    assert "未返回状态校验" in _r({"op": "jie_yi", "target": "姜萌", "relation": "结义"})


def test_unknown_counts_never_produce_claims():
    """`-1`（读不到）与 0（真的没有）必须产出不同的话：未知时不写任何断言。

    断言刻意收在"位师傅"／"徒弟已满"这种**只有断言才会出现的字眼**上：
    只钉"已有师傅"会漏掉 `-1` 被当成真值时拼出的"你已有-1位师傅"。
    """
    t = _r({"op": "shou_tu", "target": "姜萌", "relation": "师徒", "verified": False,
            "precheck": 3, "create_ret": -1,
            "diag": {"npc_masters": -1, "player_masters": -1, "npc_students": -1, "player_students": -1}})
    assert "位师傅" not in t and "已有师傅" not in t and "徒弟已满" not in t, t
    t2 = _r({"op": "shou_tu", "target": "姜萌", "relation": "师徒", "verified": False,
             "precheck": 3, "create_ret": -1,
             "diag": {"npc_masters": 0, "player_masters": 2, "npc_students": 3, "player_students": 0}})
    assert "已有2位师傅" in t2 and "姜萌门下徒弟已满（3）" in t2, t2


def test_npc_to_npc_refusal_uses_the_pair_subject():
    """NPC↔NPC（免窗直写）失败时主语是"甲与乙"，不能写成"你与…"。"""
    t = _r({"op": "jie_yi", "target": "张三", "counterparty": "李四", "relation": "结义",
            "verified": False, "precheck": 3, "create_ret": -1, "relation_before": "陌生",
            "diag": {"player_masters": 1}})
    assert t.startswith("张三与李四的「结义」未能结下"), t
    assert "你已有师傅" not in t, t
    assert "李四已有师傅" in t, t


def test_break_rendering_explains_the_direct_write_path():
    """解除类：原生动作没跑起来（码非 0）→ 说明"走了字段直写"（是解释，不是失败）。"""
    t = _r({"op": "jie_chu_jie_yuan", "target": "姜萌", "relation": "道侣",
            "precheck": 3, "create_ret": 3, "readback_still_related": False})
    assert "字段直写" in t and "引擎码 3" in t, t
    assert "读回确认关系已解除" in t, t          # 结局口径不变：直写成功 = 解除成功
    t2 = _r({"op": "divorce", "target": "姜萌", "relation": "夫妻", "precheck": 0, "create_ret": 0,
             "readback_still_related": False})
    assert "字段直写" not in t2 and "预检" not in t2, t2
