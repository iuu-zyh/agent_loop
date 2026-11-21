# -*- coding: utf-8 -*-
"""宗门查询（topic=sects）契约（10-29 新增）

用户原话：「目前查宗门信息，返回的结果很粗糙，只会返回宗主，总人数…… 游戏里面真的进一个宗门
是可以从面板里看到很多信息的，首先是宗门功法偏好…… 其次是可以看到宗门的具体人事。」
以及：「当前查询丹阳谷的话会返回所有的，包含主宗门和下面的，没有只查询一个州的。」

数据源与 RVA 见 `docs/plan-sect.md`。本文件钉住六件事：

  A. `region` 不再被 `byName` 短路（旧行 `if (!byName && !RegionOk(...))` 必须消失）
  B. 三档粒度都在：州内不跨州展开 / 分舵全名只回一条 / 未收窄时仍是族系
  C. 功法偏好**不许把 basFroze 写成"冰灵根"**（权威表是水灵根），且 12 条齐全
  D. 人事树读 `postData`（部门/师徒归属），读不到必须走扁平降级并留 `people_note`
  E. IL2CPP 泛型集合只许 Count + 索引器（禁 foreach）——分舵字典更是只许 `GetElders(dept)`
  F. 渲染层：不给 `detail` 时**一行一宗**（老格式），给了才追加子行；字段缺失不出空行
"""
from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
EXEC = _cs_tools.TOOL_SRC
SCHEMAS = ROOT / "tools" / "schemas.py"
RENDER = ROOT / "tools" / "text_render.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _sect_src() -> str:
    """`query_world(topic=sects)` 的实现 —— 11-20 起是独立文件 `tools/SectQueryTool.cs`。

    以前是"从 `QueryWorldSects` 切到 `QueryWorldEvents` 之前"，那是靠**相邻成员**当边界；
    拆开后它们分属两个文件，改用**整份文件**（这一份里只有宗门查询，边界就是文件边界）。
    """
    return _cs_tools.tool_file("SectQueryTool.cs")


# ---------------------------------------------------------------- C# 侧

def test_region_is_not_short_circuited_by_name():
    """A：旧写法 `if (!byName && !RegionOk(bi, region)) continue;` 是这次要修的病根。"""
    body = _sect_src()
    assert "!byName && !RegionOk" not in body
    # 候选集必须无条件按 region 收窄
    assert re.search(r"if \(!byRegion \|\| (?:QueryTool\.)?RegionOk\(kv\.Key, region\)\) schools\.Add\(kv\);", body)


def test_three_scope_levels_present():
    """B：state / self / family 三条分支都要在，且顶层回 scope。"""
    body = _sect_src()
    for tag in ('scope = "state"', 'scope = "self"', 'scope = "family"'):
        assert tag in body, tag
    assert '["scope"] = scope' in body
    # self 的判据：命中的是分舵全名，且它不是主宗
    assert "hitFullName && familyRoot.Pointer != hitMs.Pointer" in body


def test_sect_dispatcher_passes_args():
    """接线：detail 要从 args 进来，所以分发必须传 args。"""
    src = _read(EXEC)
    # EXEC.read_text() 是 flat_src()（工具层类名前缀已抹平）—— 这里只认"传了 args"
    assert 'case "sects":    return QueryWorldSects(args, region, (string)args["name"]);' in src


def test_bas_types_table_matches_localtext():
    """C：12 条功法偏好中文 = LocalText.json 的 modEffectType1Name_<key>。
    尤其 basFroze 是**水**灵根（按字面猜"冰"会错）。"""
    body = _sect_src()
    expect = {
        "basSword": "剑法资质", "basSpear": "枪法资质", "basBlade": "刀法资质",
        "basFist": "拳法资质", "basPalm": "掌法资质", "basFinger": "指法资质",
        "basFire": "火灵根", "basFroze": "水灵根", "basThunder": "雷灵根",
        "basWind": "风灵根", "basEarth": "土灵根", "basWood": "木灵根",
    }
    for k, v in expect.items():
        assert f'["{k}"] = "{v}"' in body, (k, v)
    assert "冰灵根" not in body
    # 运行期优先问游戏，备用表只是兜底
    assert "g.conf.roleEffect.GetValueName(key)" in body


def test_people_reads_postdata_and_degrades():
    """D：人事树读 postData（5 次 GetElders，不遍历字典）；读不到退扁平并写 people_note。"""
    body = _sect_src()
    assert "bd.postData" in body
    assert "pd.postMain" in body
    assert "pd.postBigElders" in body
    assert "pd.GetElders((SchoolDepartmentType)d)" in body
    assert "manageInherit" in body
    for k in ('["elders_flat"]', '["inherit_flat"]', '["people_note"]'):
        assert k in body, k


def test_no_foreach_over_il2cpp_lists():
    """E：IL2CPP 泛型集合禁 foreach（历史事故：无类型 List + foreach 直接静默全空）。
    `ReadStrList`/`ReadEnumList` 交回来的是**我们自己的** List，遍历它们没问题；
    禁的是直接遍历 `Il2CppSystem.Collections.Generic.List`/`Dictionary` 字段。"""
    body = _sect_src()
    il2cpp_field = re.compile(r"foreach\s*\([^)]*\bin\s+([^()]*?(?:\.npc|\.manage|\.postBigElders|\.basTypes|\.areaPoints)[^()]*?)\)")
    bad = il2cpp_field.findall(body)
    assert not bad, bad
    # 部门一律按枚举值逐个 GetElders，不许碰 postElders 字典本身
    assert "pd.postElders" not in body
    assert "pd.GetElders((SchoolDepartmentType)d)" in body


def test_no_unit_id_leaks_into_payload():
    """人物只出名字/境界/状态；内部 unitID 不进 JSON（历史先例：错误帧里不出现内部 ID）。"""
    body = _sect_src()
    assert '["unit_id"]' not in body
    assert '["unitID"]' not in body


# ---------------------------------------------------------------- schema 侧

def test_schema_has_detail_and_new_semantics():
    src = _read(SCHEMAS)
    m = re.search(r'"detail": \{(.*?)\n                \},', src, re.S)
    assert m, "schema 缺 detail"
    block = m.group(1)
    assert '"info"' in block and '"people"' in block and '"all"' in block
    # region 的新语义必须写进描述，否则模型还会以为"传 name 就忽略 region"
    assert "精确到该州" in src
    assert "只回那一条" in src


# ---------------------------------------------------------------- 渲染层

def _render(args: dict, data: dict) -> str:
    from tools.text_render import render
    out = render("query_world", args, {"success": True, "data": data})
    assert out, "渲染器应当命中"
    return out


def _sect(**kw) -> dict:
    base = {"name": "丹阳谷永宁州分舵", "region": "永宁州", "point": {"x": 33, "y": 150}}
    base.update(kw)
    return base


def test_render_without_detail_keeps_old_single_line():
    """F：不给 detail → 一行一宗（老格式），不许多出子行。"""
    out = _render({"topic": "sects"}, {"total": 1, "sects": [_sect(is_top=True, member_count=69, leader="濮阳坻")]})
    lines = out.split("\n")
    assert lines[0] == "宗门概览（共1个）"
    assert len(lines) == 2 and lines[1].startswith("· ")
    assert "功法偏好" not in out and "宗主：" not in out


def test_render_info_sections():
    out = _render({"topic": "sects", "detail": "info"}, {"total": 1, "sects": [_sect(
        bas_types=["剑法资质", "水灵根"],
        member={"total": 69, "big_elders": 2, "elders": 5},
        capacity={"big_elders": 2, "elders": 5},
        money=12000, faith_money=3500, power=8600,
        honor={"score": 3, "rank": 17},
        intim=[{"name": "天魔宗", "value": -40}],
    )]})
    assert "功法偏好：剑法资质、水灵根" in out
    assert "人数：大长老2/2、长老5/5，共69人" in out
    assert "经济：灵石12000、信仰3500、战力8600" in out
    assert "战绩：宗门大比 3分，第17名" in out
    assert "交情：天魔宗-40" in out


def test_render_member_without_breakdown_has_no_stray_comma():
    out = _render({"topic": "sects", "detail": "info"},
                  {"total": 1, "sects": [_sect(member={"total": 69})]})
    assert "人数：共69人" in out
    assert "，" not in out.split("人数：")[1].split("\n")[0]


def test_render_people_tree():
    out = _render({"topic": "sects", "detail": "people"}, {"total": 1, "sects": [_sect(people={
        "main": {"name": "濮阳坻", "realm": "金丹后期"},
        "big_elders": [{"name": "玄阳子", "realm": "元婴初期", "depts": ["招贤堂", "巡查堂"]}],
        "elders": [
            {"dept": "招贤堂", "name": "林清", "realm": "金丹中期",
             "inherit": [{"name": "苏小满", "realm": "筑基后期"}], "inner_count": 12},
            {"dept": "巡查堂", "vacant": True},
        ],
    })]})
    assert "宗主：濮阳坻（金丹后期）" in out
    assert "大长老（1）：" in out and "玄阳子（元婴初期）｜辖招贤堂、巡查堂" in out
    assert "长老（2）：" in out
    assert "招贤堂 林清（金丹中期）｜真传：苏小满（筑基后期）；内门12人" in out
    assert "巡查堂：空缺" in out


def test_render_dead_person():
    out = _render({"topic": "sects", "detail": "people"}, {"total": 1, "sects": [_sect(people={
        "main": {"name": "无咎", "status": "已故", "dead_month": "殁于三年二月"},
    })]})
    assert "宗主：无咎（已故，殁于三年二月）" in out


def test_render_degrade_note_and_flat_lists():
    out = _render({"topic": "sects", "detail": "people"}, {"total": 1, "sects": [_sect(
        people_note="postData 不可读，已退回扁平名单（缺部门与师徒归属）",
        people={"main": {"name": "濮阳坻"}, "elders_flat": [{"name": "林清"}], "inherit_flat": [{"name": "苏小满"}]},
    )]})
    assert "长老（1）：林清" in out
    assert "真传弟子（1）：苏小满" in out
    assert "人事提示：postData 不可读" in out


def test_render_scoped_head_and_note():
    """州内收窄时，标题要说明"只查了这一州"，并提示同族还有几处。"""
    out = _render({"topic": "sects", "name": "丹阳谷", "region": "永宁州", "detail": "info"}, {
        "total": 1, "scope": "state", "name": "丹阳谷", "region": "永宁州",
        "family_total": 3, "family_others": ["a", "b"],
        "note": "已按 region=「永宁州」收窄到 1 处；同族另有 2 处",
        "sects": [_sect()],
    })
    head = out.split("\n")[0]
    assert head.startswith("「丹阳谷」永宁州内的宗门（1处）")
    assert "同族另有 2 处" in head
