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

  **Python 侧**
  6. 渲染层要把 id 显示出来（`益婉容(Xs6JDI)`）—— C# 给了、Python 不读 = 模型还是只能按名猜
     （附录 D.1 那条已经踩过四次）；
  7. 有歧义时必须在**返回正文最前面**提醒，且说清"未必是你要找的人"；
  8. 无歧义时**不许**出现这条提醒（狼来了会让模型忽略真提醒）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _lookup_body() -> str:
    src = _read("UnitLookup.cs")
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
    body = _read("UnitLookup.cs")
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
    """中文名不许交给 `g.world.unit.GetUnit`：它会按名兜底替我们选人、还绕过同名计数。"""
    src = _read("UnitLookup.cs")
    assert "private static bool HasCjk(" in src, "需要 CJK 判据"
    body = _lookup_body()
    i = body.index("if (!HasCjk(npcId))")
    j = body.index("④ 中文名兜底") if "④ 中文名兜底" in body else body.index("全图按 GetName()")
    assert i < j, "GetUnit 直查必须被 HasCjk 挡住，且排在按名扫描之前"


# ---------------------------------------------------------------- C#：id 出口

def test_relationships_carry_unit_id():
    """关系簿（含道侣）必须带 unit_id —— 入参本来就是 ID，丢掉才是这次事故的根因。"""
    src = _read("UnitSnapshot.cs")
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
    src = _read("ToolExecutor.cs")
    i = src.index('["name"] = name,\n                        ["relation"] = relCn,')
    seg = src[i: i + 900]
    assert 'item["unit_id"] = ud.unitID;' in seg, "搜索结果必须带 unit_id"


def test_inspect_accepts_unit_id_and_reports_ambiguity():
    src = _read("ToolExecutor.cs")
    body = src[src.index("private JObject InspectUnit(JObject args)"):]
    body = body[: body.index('if (Has("brief"))')]
    assert 'args["unit_id"]' in body, "inspect_unit 必须接受 unit_id 参数"
    assert "ResolveEx(target," in body, "必须走 ResolveEx 以拿到候选列表"
    assert 'data["ambiguous"]' in body, "同名时必须把候选如实回传"
    assert 'data["unit_id"]' in body, "被查者自己的 unit_id 要无条件回传（下次可精确指人）"


# ---------------------------------------------------------------- Python：渲染

def test_named_refs_renders_id_and_tolerates_old_payloads():
    from agent_loop.tools.text_render import _named_refs
    # 新载荷：名 + id
    assert _named_refs([{"name": "益婉容", "unit_id": "Xs6JDI"}]) == ["益婉容(Xs6JDI)"]
    # 道侣是单个对象
    assert _named_refs({"name": "唐炎", "unit_id": "BOlQu6"}) == ["唐炎(BOlQu6)"]
    # 旧载荷：纯字符串数组（照常可读，不出现空括号）
    assert _named_refs(["益婉容", "赵温韦"]) == ["益婉容", "赵温韦"]
    assert _named_refs("") == [] and _named_refs([]) == []
    # 半截数据：只有 id / 只有名 —— 都不许渲染成 "(Xs6JDI)" 这种缺主体的怪东西
    assert _named_refs([{"unit_id": "Xs6JDI"}]) == ["Xs6JDI"]
    assert _named_refs([{"name": "益婉容"}]) == ["益婉容"]
    assert _named_refs([{"name": "", "unit_id": ""}]) == []


def test_relation_book_shows_ids():
    from agent_loop.tools.text_render import _relation_book
    t = _relation_book({"parent": [{"name": "益婉容", "unit_id": "Xs6JDI"}],
                        "married": {"name": "唐炎", "unit_id": "BOlQu6"},
                        "friend_units": [{"name": "唐乐咏", "unit_id": "yqVSYV"}]})
    assert "父母：益婉容(Xs6JDI)" in t, t
    assert "道侣：唐炎(BOlQu6)" in t, t
    assert "好友：唐乐咏(yqVSYV)" in t, t


def test_search_lines_show_unit_id():
    from agent_loop.tools.text_render import render_search
    t = render_search({}, {"total": 1, "items": [
        {"name": "赵勤", "sex": "女", "relation": "陌生", "intim": 0,
         "realm": "结晶后期", "sect": "散修", "region": "永宁州", "unit_id": "293n6V"}]})
    assert "unit_id=293n6V" in t, t
    # 旧载荷没有 unit_id → 不出现空尾巴
    t2 = render_search({}, {"total": 1, "items": [{"name": "赵勤", "relation": "陌生", "intim": 0}]})
    assert "unit_id" not in t2, t2


def test_ambiguity_warning_is_loud_and_first():
    """有歧义 → 提醒必须在正文最前面，且明说"未必是你要找的人"。"""
    from agent_loop.tools.text_render import render_inspect
    data = {"name": "益婉容", "sex": "女", "unit_id": "Xs6JDI",
            "ambiguous": {"target": "益婉容", "matched": 3, "used": "Xs6JDI", "candidates": [
                {"unit_id": "Xs6JDI", "name": "益婉容", "realm": "登仙境", "reputation": 3129,
                 "point": {"x": 119, "y": 51}},
                {"unit_id": "AbC123", "name": "益婉容", "realm": "结晶后期", "reputation": 3379,
                 "point": {"x": 20, "y": 8}},
                {"unit_id": "Zz9Q8w", "name": "益婉容", "realm": "筑基境", "reputation": 100,
                 "point": {"x": 3, "y": 4}},
            ]}}
    t = render_inspect({}, data)
    first = t.split("\n")[0]
    assert first.startswith("⚠️同名提醒"), f"提醒必须在最前面：{first}"
    assert "全图有 3 人同名" in first
    assert "未必是你要找的人" in first, "必须点明可能查错人，否则模型会当事实用"
    assert "unit_id=Xs6JDI" in first and "登仙境" in first, "要说清本次给的是哪一位"
    assert "AbC123" in first and "Zz9Q8w" in first, "其余同名者要列出来供改查"
    assert "unit_id=\"…\"" in first or "unit_id=" in first, "要给出改查办法"


def test_no_warning_without_ambiguity():
    """无歧义（或只有 1 个候选 / 旧 C# 没有 ambiguous 字段）→ 不许出现提醒。"""
    from agent_loop.tools.text_render import render_inspect
    assert "同名提醒" not in render_inspect({}, {"name": "益婉容", "sex": "女"})
    assert "同名提醒" not in render_inspect({}, {
        "name": "益婉容", "ambiguous": {"target": "益婉容", "matched": 1, "used": "X1",
                                        "candidates": [{"unit_id": "X1", "name": "益婉容"}]}})


def test_inspect_shows_own_unit_id():
    from agent_loop.tools.text_render import render_inspect
    t = render_inspect({}, {"name": "赵勤", "sex": "女", "unit_id": "293n6V"})
    assert "unit_id：293n6V" in t, t


def test_schema_documents_unit_id():
    from agent_loop.tools.schemas import INSPECT_UNIT
    props = INSPECT_UNIT["function"]["parameters"]["properties"]
    assert "unit_id" in props, "inspect_unit 必须暴露 unit_id 参数"
    assert INSPECT_UNIT["function"]["parameters"]["required"] == [], \
        "target 不再是必填（给了 unit_id 就不需要名字）"
    d = INSPECT_UNIT["function"]["description"]
    assert "unit_id" in d and "重名" in d, "工具描述要讲清重名与 unit_id 的关系"


def test_tool_usage_prompt_teaches_id_first():
    txt = (ROOT / "prompts" / "sections" / "tool_usage.txt").read_text(encoding="utf-8")
    assert "unit_id" in txt and "重名" in txt, "提示词要教模型认 id 不认名字"
