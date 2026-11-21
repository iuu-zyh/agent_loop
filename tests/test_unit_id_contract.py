"""重名消歧契约（09-13 真机事故）：`inspect_unit` 按名字查到了**另一个同名的人**。

**事故现场**（Player.log 铁证）：

    [UnitLookup] Resolve('益婉容') → ③全图按名#199 unitID=Xs6JDI

玩家面板上的益婉容是**结晶后期 / 声望 3379**，工具返回的却是**登仙境 / 声望 3129** ——
两个都是「益婉容」。旧实现的原话是「重名时取第一个匹配（真机随机 NPC 可能重名，**概率低**）」
—— **"概率低"是错的**：全图上千个单位、名字用字池有限，重名是常态。而且它**完全静默**：
不报错、不缺字段，只是把另一个人的档案端给模型。

本文件锁住整套防线（任一条失守都会退回"静默查错人"）：

  **C# 侧**
  1. 按名解析要**扫完全部**同名者（旧实现命中即 return，连有几个人都不知道）；
  2. `unit_id` 路径必须**回验 unitID 相等** —— `g.world.unit.GetUnit` 对查不到的串会
     **按名兜底**返回随便一个同名者，不回验就等于"精确查询"也是猜的；
  3. `unit_id` 给了就**不许回退按名**（回退 = 又猜一次）；
  4. **中文名不许交给 `GetUnit`** —— 它会按名兜底替我们选人，且绕过了同名计数；
  5. 关系簿 / 搜索结果的 `unit_id` 必须带出去（否则模型没有第二条路，只能拿名字回去猜）。

  **Python 侧（09-17 反转：模型侧零 id）**
  6. 渲染层**一个 id 都不许输出**；需要区分同名者时用 `unit_refs` 给的**唯一化显示名**
     （`益婉容(化神殿·结晶后期)`）—— 它是渲染出来的字符串、也是 Python 人名映射表的键，
     模型照抄即可精确指人，而它看到的仍然只是"名字"；
  7. 有歧义时必须在**返回正文最前面**提醒，说清"未必是你要找的人"，并给出改查办法
     （把 target 写成那个唯一名 —— 不是给 id）；
  8. 无歧义时**不许**出现这条提醒（狼来了会让模型忽略真提醒）；
  9. `inspect_unit` 的 schema 里**不许有 id 参数**，提示词也不许再提 id。

> 为什么反转：原设计（把 id 交给模型）确实修掉了"按名查错人"，但代价是模型会自己编 id、
> 把 id 当名字念。现在的分工是「**模型只认名字，harness 负责认人**」：
> C# 仍然把 `(name, unit_id)` 回传（本文件上半部分照旧钉着），Python 收进映射表、
> 渲染时抹掉 id、注入时再翻译回 id。
"""
from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _lookup_body() -> str:
    src = _read("game/UnitLookup.cs")
    return src[src.index("private static WorldUnitBase ResolveInner("):]


# ---------------------------------------------------------------- C#：按名解析

def test_name_scan_collects_all_matches():
    """按名解析必须扫完整个列表，把同名者全部收进 candidates（不许命中即 return）。"""
    body = _lookup_body()
    i = body.index("④ 中文名兜底") if "④ 中文名兜底" in body else body.index("全图按 GetName()")
    scan = body[i:]
    assert "candidates.Add(Describe(u));" in scan, "每个同名者都要进候选列表"
    # 关键：不能在 candidates.Add 之后立刻 return（那就是只取第一个、连数都不数）
    after_add = scan[scan.index("candidates.Add(Describe(u));"):]
    loop_end = after_add.index("if (firstUnit != null)")
    assert "return" not in after_add[:loop_end], \
        "命中后必须继续扫完（否则统计不到同名人数，也就无法上报歧义）"
    assert "candidates.Count > 1" in scan, "扫描结束后要按候选数判定歧义"


def test_unit_id_path_verifies_identity():
    """`unit_id` 路径必须回验 unitID 相等 —— GetUnit 会按名兜底返回同名者。"""
    body = _read("game/UnitLookup.cs")
    seg = body[body.index("if (hasId)"):]
    seg = seg[: seg.index("return null;\n            }")]
    assert "got == unitId" in seg, "必须比对 GetUnit 返回对象的 unitID 与请求的 unit_id"
    assert "②unitID不匹配" in seg, "不匹配要显式留痕（诊断串里点明 GetUnit 按名兜底了）"
    # 校验必须在"采用"之前：先比对、再返回
    assert seg.index("got == unitId") < seg.index("candidates.Add(Describe(byId))"), \
        "必须先验证身份再把对象交出去"


def _brace_block(src: str, start_idx: int) -> str:
    """取出 start_idx 之后第一对配平花括号的**内部**（不含外层括号）。"""
    i = src.index("{", start_idx)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1: j]
        j += 1
    raise AssertionError("花括号不配平")


def test_unit_id_never_falls_back_to_name():
    """给了 unit_id 就只认它：`if (hasId)` 块**每条路径都必须 return**，不许落到按名匹配。

    这条是"精确查询"承诺的全部重量所在 —— 只要有一条路径能掉出去，unit_id 查不到时就会
    静默按名选一个同名者，调用方还以为拿到的是精确结果（与本次事故同形）。
    故用花括号配平取块，断言块内**最后一条语句是 `return null;`**（= 无 fall-through），
    并顺带禁掉 `goto`（它正是"想办法掉出去"的写法）。
    """
    body = _lookup_body()
    blk = _brace_block(body, body.index("if (hasId)"))
    assert not re.search(r"\bgoto\b", blk), "不许用 goto 跳出精确分支"
    assert re.search(r"return\s+null;\s*$", blk.rstrip()), \
        "`if (hasId)` 块必须以 return null 收尾 —— 否则查不到时会继续走按名匹配（=又猜一次）"
    assert blk.count("return") >= 2, "命中与未命中两条路径都要显式返回"
    # 精确分支必须在按名扫描**之前**（顺序反了就成了"先猜再精确"，没有意义）
    assert body.index("if (hasId)") < body.index("全图按 GetName()")


def test_cjk_names_skip_getunit():
    """中文名不许交给 `g.world.unit.GetUnit`：它会按名兜底替我们选人、还绕过同名计数。

    ★ 09-17 起这条由**结构**保证，不再靠 `HasCjk` 判据：
    原「③ 非中文输入先试 GetUnit」分支已删除（"这串像不像中文"从来不是"这是不是 id"的
    判据），于是 `GetUnit` 只会被喂 **显式 unitID**（②）或**登记表里的 unitID**（⓪）。
    所以这里改钉**更强的性质**：`GetUnit` 的实参只能是那两种来源，且都必须回验相等。
    """
    src = _read("game/UnitLookup.cs")
    body = _lookup_body()
    # 剥注释再查 —— `HasCjk` 会出现在"它为什么被删"的说明里，那是文档不是代码
    code = re.sub(r"//[^\n]*", "", body)
    assert "HasCjk" not in code, \
        "HasCjk 判据应随 ③ 分支一起删除（留着它意味着又出现了「猜这串是不是 id」的路）"
    assert "if (!HasCjk(npcId))" not in code, "③ 分支（非中文输入先试 GetUnit）不该还在"
    # GetUnit 的两个合法调用点：⓪ 登记表、② 显式 unitId —— 两处都必须回验 unitID 相等
    assert code.count("g.world.unit.GetUnit(") == 2, \
        "GetUnit 只该在 ⓪(登记表) 与 ②(显式 unitId) 两处被调用，实际 %d 处" % code.count("g.world.unit.GetUnit(")
    assert code.count("got == pinned") == 1 and code.count("got == unitId") == 1, \
        "两个调用点都必须回验 unitID 相等（GetUnit 对查不到的串会按名兜底）"


# ---------------------------------------------------------------- C#：id 出口

def test_relationships_carry_unit_id():
    """关系簿（含道侣）必须带 unit_id —— 入参本来就是 ID，丢掉才是这次事故的根因。"""
    src = _read("game/UnitSnapshot.cs")
    for fn in ("private static JArray UnitNames(object list)",
               "private static JArray IntimBookNames(object relObj, bool friend)"):
        body = src[src.index(fn): src.index(fn) + 1800]
        assert '["unit_id"]' in body, f"{fn} 必须回传 unit_id"
    # relationships 组装段：12 容器 → UnitNames/IntimBookNames，道侣单独一段
    rel = src[src.index('if (Has("relationships"))'):]
    rel = rel[: rel.index('snap["relationships"] = rl;')]
    assert rel.count("UnitNames(") >= 9 and "IntimBookNames(" in rel, "关系容器都要走带 id 的收集器"
    assert rel.count('["unit_id"]') >= 1, "道侣（married）也要带 unit_id"


def test_search_results_carry_unit_id():
    """search_units 每行带 unit_id —— 否则模型只有关系簿一条路能拿到 id。"""
    src = _cs_tools.tool_src()
    i = src.index('["name"] = name,\n                        ["relation"] = relCn,')
    seg = src[i: i + 900]
    assert 'item["unit_id"] = ud.unitID;' in seg, "搜索结果必须带 unit_id"


def test_inspect_accepts_unit_id_and_reports_ambiguity():
    src = _cs_tools.tool_src()
    body = src[src.index(" JObject InspectUnit(JObject args)"):]
    body = body[: body.index('if (Has("brief"))')]
    assert 'args["unit_id"]' in body, "inspect_unit 必须接受 unit_id 参数"
    assert "ResolveEx(target," in body, "必须走 ResolveEx 以拿到候选列表"
    assert 'data["ambiguous"]' in body, "同名时必须把候选如实回传"
    assert 'data["unit_id"]' in body, "被查者自己的 unit_id 要无条件回传（下次可精确指人）"


# ---------------------------------------------------------------- C#：id 出口（给 harness 用）

def test_relationships_carry_unit_id():
    """关系簿（含道侣）必须带 unit_id —— **它是 Python 侧人名映射表的原料**。

    ★ 09-17 起 id 不再给模型看，但**必须继续由 C# 回传**：Python 的 `unit_refs` 正是
    从这些 `{name, unit_id}` 里把 `(名字 → id)` 收进映射表（渲染前收、渲染时抹掉）。
    少了它，"模型查到的那些人"就永远指不准（只能拿名字回去按名猜）。
    """
    src = _read("game/UnitSnapshot.cs")
    for fn in ("private static JArray UnitNames(object list)",
               "private static JArray IntimBookNames(object relObj, bool friend)"):
        body = src[src.index(fn): src.index(fn) + 1800]
        assert '["unit_id"]' in body, f"{fn} 必须回传 unit_id"
    # relationships 组装段：12 容器 → UnitNames/IntimBookNames，道侣单独一段
    rel = src[src.index('if (Has("relationships"))'):]
    rel = rel[: rel.index('snap["relationships"] = rl;')]
    assert rel.count("UnitNames(") >= 9 and "IntimBookNames(" in rel, "关系容器都要走带 id 的收集器"
    assert rel.count('["unit_id"]') >= 1, "道侣（married）也要带 unit_id"


def test_search_results_carry_unit_id():
    """search_units 每行带 unit_id —— 同上，给 Python 的映射表当原料。"""
    src = _cs_tools.tool_src()
    i = src.index('["name"] = name,\n                        ["relation"] = relCn,')
    seg = src[i: i + 900]
    assert 'item["unit_id"] = ud.unitID;' in seg, "搜索结果必须带 unit_id"


def test_inspect_reads_injected_unit_id_and_reports_ambiguity():
    """`inspect_unit` 仍要读 `args["unit_id"]` —— 但那是 **harness 注入**的（模型看不到它，
    它已从 schema 里删除）；同名歧义仍要如实回报候选（Python 据此给唯一化显示名）。"""
    src = _cs_tools.tool_src()
    body = src[src.index(" JObject InspectUnit(JObject args)"):]
    body = body[: body.index('if (Has("brief"))')]
    assert 'args["unit_id"]' in body, "inspect_unit 必须接住 harness 注入的 unit_id"
    assert "ResolveEx(target," in body, "必须走 ResolveEx 以拿到候选列表"
    assert 'data["ambiguous"]' in body, "同名时必须把候选如实回传（Python 据此唯一化显示名）"
    assert 'data["unit_id"]' in body, "被查者自己的 unit_id 要无条件回传（Python 的映射表原料）"


def test_relationship_lists_never_fabricate_a_name_from_the_id():
    """C# 的关系簿 / 道侣：**取不到名字时留空，绝不拿 unitID 顶替**。

    `name` 是全工程"人物名"的载体：Python 的 `unit_refs._pair` 收下 `{name, unit_id}`
    就按它登记映射表，渲染层再把 `name` 印给模型看 ⇒ 拿 id 顶替等于**把 unitID 当人名
    端给模型**（模型会照着念、甚至填进 `target`）。留空之后这条自然被跳过：
    `_pair` 只认"同时有 name 与 unit_id"，渲染侧也只印非空名字。
    """
    src = _read("game/UnitSnapshot.cs")
    # 去掉注释再断言：注释里可能**引用**被删掉的那句旧代码（本文件的注释正是如此）
    body = "\n".join(ln.split("//")[0] for ln in src.splitlines())
    assert "? idStr : nm" not in body, "关系簿把 unitID 当名字发出去了"
    assert "? mid : mn" not in body, "道侣把 unitID 当名字发出去了"
    assert '["name"] = nm ?? "",' in body and '["name"] = mn ?? "",' in body


def test_csharp_reads_exactly_the_keys_python_injects():
    """★ 跨语言契约：Python 的 `PERSON_ARGS`（指人参数 → 注入键）必须与 C# 的读键**同集合**。

    为什么需要这条：两边靠 `args[key + "_id"]` 这个**约定**接上，谁改了名字都不会报错 ——
    表现只是"注入的 id 静默失效、退回按名解析"，而按名解析在重名时正是**静默错人**
    （`ResolveArgExact` 读不到 id 就 `return UnitLookup.Resolve(nm)`，全图取第一个）。
    所以两侧的参数名要一起钉住：多一个、少一个都算漂移。
    """
    from agent_loop.tools import unit_refs

    src = _cs_tools.tool_src()
    assert 'args[key + "_id"]' in src, "通用约定没了：ResolveArgExact 必须读 <键>_id"
    assert 'ResolveEx(nm, id, out _)' in src, "有 id 时必须走 ②unitID精确"
    assert "return UnitLookup.Resolve(nm);" in src, "id 失效/未给 ⇒ 回落按名（契约的另一半）"

    cs_args = set(re.findall(r'ResolveArgExact\(args,\s*"([^"]+)"', src))
    if "ResolveActor(args)" in src:
        cs_args.add("initiator")
    py_args = {arg for pairs in unit_refs.PERSON_ARGS.values() for (arg, _id) in pairs}
    assert cs_args == py_args | {"initiator"}, (
        f"C# 读的指人参数 {sorted(cs_args)} 与 Python 注入的 {sorted(py_args | {'initiator'})} 不一致")

    # `inspect_unit` 是唯一的例外：它的注入键是**直读**的 `unit_id`（不走 ResolveArgExact）
    assert unit_refs.PERSON_ARGS["inspect_unit"] == (("target", "unit_id"),)
    assert 'args["unit_id"]' in src, "inspect_unit 直读 args[\"unit_id\"]"


# ---------------------------------------------------------------- Python：模型侧零 id

def test_named_refs_never_renders_id():
    """关系簿渲染**只出名字**；需要区分同名者时用 `display`（唯一化显示名）。

    ★ 09-17 反转：原实现输出 `益婉容(Xs6JDI)` 把 id 交给模型 —— 那是对 D.1 事故的修复，
    但代价是模型会自己编 id / 把 id 当名字念。现在 id 由映射表保管，模型只抄名字。
    """
    from agent_loop.tools.text_render import _named_refs
    assert _named_refs([{"name": "益婉容", "unit_id": "Xs6JDI"}]) == ["益婉容"]
    # 需要区分时：annotate 写进来的 display 优先
    assert _named_refs([{"name": "益婉容", "unit_id": "Xs6JDI",
                         "display": "益婉容(化神殿·结晶后期)"}]) == ["益婉容(化神殿·结晶后期)"]
    # 道侣是单个对象；旧载荷（纯字符串数组）照常可读
    assert _named_refs({"name": "唐炎", "unit_id": "BOlQu6"}) == ["唐炎"]
    assert _named_refs(["益婉容", "赵温韦"]) == ["益婉容", "赵温韦"]
    assert _named_refs("") == [] and _named_refs([]) == []
    # 半截数据：只有 id（没有名字）**什么都不输出** —— 以前会退化成裸 id 显示给模型
    assert _named_refs([{"unit_id": "Xs6JDI"}]) == []
    assert _named_refs([{"name": "益婉容"}]) == ["益婉容"]
    assert _named_refs([{"name": "", "unit_id": ""}]) == []


def test_relation_book_shows_names_not_ids():
    from agent_loop.tools.text_render import _relation_book
    t = _relation_book({"parent": [{"name": "益婉容", "unit_id": "Xs6JDI"}],
                        "married": {"name": "唐炎", "unit_id": "BOlQu6"},
                        "friend_units": [{"name": "唐乐咏", "unit_id": "yqVSYV",
                                          "display": "唐乐咏(化神殿)"}]})
    assert "父母：益婉容" in t and "道侣：唐炎" in t, t
    assert "好友：唐乐咏(化神殿)" in t, t
    assert "Xs6JDI" not in t and "BOlQu6" not in t and "yqVSYV" not in t, f"渲染漏出了 id：{t}"


def test_search_lines_show_names_not_ids():
    from agent_loop.tools.text_render import render_search
    t = render_search({}, {"total": 1, "items": [
        {"name": "赵勤", "sex": "女", "relation": "陌生", "intim": 0,
         "realm": "结晶后期", "sect": "散修", "region": "永宁州", "unit_id": "293n6V"}]})
    assert "赵勤" in t and "293n6V" not in t, t
    # 重名者：显示唯一化名字（模型照抄它就能精确指人）
    t2 = render_search({}, {"total": 2, "items": [
        {"name": "赵勤", "relation": "陌生", "intim": 0, "display": "赵勤(化神殿)"},
        {"name": "赵勤", "relation": "陌生", "intim": 1, "display": "赵勤(玄天宗)"}]})
    assert "赵勤(化神殿)" in t2 and "赵勤(玄天宗)" in t2, t2


def test_ambiguity_warning_is_loud_and_first():
    """有歧义 → 提醒必须在正文最前面、明说"未必是你要找的人"，并给出**改查办法**：
    把 target 写成那个带括号的唯一名（而不是给 id）。"""
    from agent_loop.tools import unit_refs
    from agent_loop.tools.text_render import render_inspect
    data = {"name": "益婉容", "sex": "女", "unit_id": "Xs6JDI", "sect": "化神殿", "realm": "结晶后期",
            "ambiguous": {"target": "益婉容", "matched": 3, "used": "Xs6JDI", "candidates": [
                {"unit_id": "Xs6JDI", "name": "益婉容", "realm": "结晶后期", "sect": "化神殿"},
                {"unit_id": "AbC123", "name": "益婉容", "realm": "登仙境", "sect": "玄天宗"},
                {"unit_id": "Zz9Q8w", "name": "益婉容", "realm": "筑基境", "sect": "散修"},
            ]}}
    unit_refs.annotate("inspect_unit", {"success": True, "data": data})   # 渲染前的那一步
    t = render_inspect({}, data)
    first = t.split("\n")[0]
    assert first.startswith("⚠️同名提醒"), f"提醒必须在最前面：{first}"
    assert "全图有 3 人同名" in first
    assert "未必是你要找的人" in first, "必须点明可能查错人，否则模型会当事实用"
    assert "益婉容(化神殿·结晶后期)" in first, "要说清本次给的是哪一位（显示名）"
    assert "益婉容(登仙境·玄天宗)" in first or "益婉容(玄天宗·登仙境)" in first, "其余同名者要列出来"
    assert "把 target 原样写成" in first, "要给出改查办法"
    for uid in ("Xs6JDI", "AbC123", "Zz9Q8w"):
        assert uid not in t, f"渲染漏出了 id：{uid}"


def test_no_warning_without_ambiguity():
    """无歧义（或只有 1 个候选 / 旧 C# 没有 ambiguous 字段）→ 不许出现提醒。"""
    from agent_loop.tools.text_render import render_inspect
    assert "同名提醒" not in render_inspect({}, {"name": "益婉容", "sex": "女"})
    assert "同名提醒" not in render_inspect({}, {
        "name": "益婉容", "ambiguous": {"target": "益婉容", "matched": 1, "used": "X1",
                                        "candidates": [{"unit_id": "X1", "name": "益婉容"}]}})


def test_inspect_never_shows_own_unit_id():
    from agent_loop.tools.text_render import render_inspect
    t = render_inspect({}, {"name": "赵勤", "sex": "女", "unit_id": "293n6V"})
    assert "293n6V" not in t and "赵勤" in t, t


def test_schema_has_no_unit_id_and_teaches_unique_names():
    from agent_loop.tools.schemas import INSPECT_UNIT
    props = INSPECT_UNIT["function"]["parameters"]["properties"]
    assert "unit_id" not in props, "inspect_unit 不许暴露 unit_id（模型不接触 id）"
    assert INSPECT_UNIT["function"]["parameters"]["required"] == []
    d = INSPECT_UNIT["function"]["description"]
    assert "重名" in d and "唯一名" in d, "工具描述要讲清重名时怎么指人（照抄唯一名）"
    assert "unit_id" not in d, "描述里也不许再让模型填 id"


def test_tool_usage_prompt_teaches_name_only():
    txt = (ROOT / "prompts" / "sections" / "tool_usage.txt").read_text(encoding="utf-8")
    assert "unit_id" not in txt, "提示词不许再提 id"
    assert "指人只写名字" in txt and "唯一名" in txt, "提示词要教：只写名字；重名照抄唯一名"
