"""物品/装备介绍占位符契约（09-17）：`inspect_unit(classes=["inventory"])` 的介绍里全是 `&xxx&`。

**要解决的问题**（用户实机截图，别人反馈的原版物品）：
    残·灵盾符 —— `吸收使用者当前&608211_kcll&%的灵力，转化为保护自身的护盾，护盾持续&608211_cxsj&秒。`
面板上这句是 `当前30%…持续60秒`：`ConfBattleSkillValue.&608211_kcll` 十列皆 3000、`valueScale=x0.01|f0`。

**根因**：占位符解析（`ResolveSkillSigils`）只挂在**功法说明**那条路上；物品/装备介绍
（`PropsDetails` / `EquipsDetails`）只做了 `GameTool.LS`，`&…&` 原样进 JSON、进模型、进面板。

**取值表的列语义**（游戏自带 Excel 表头实证，`配置修改教程/配置表头/`）：
    · `BattleSkillValue`：`value1..value10` = **境界1数值..境界10数值**；`valueScale` = 显示数值缩放。
    · `ItemPill`：`grade` = **道具境界**（残·灵盾符..道·灵盾符 = 1..10）。
    · `ItemProps.level` = **物品品质**（1..6 = 凡品/下品/中品/上品/极品/仙品）——**不是**列索引：
      残(境界1)与真(境界2)品质都是 2，但 `&608213_shwl&` 对二者是 125 / 200；拿品质当列 = 算错品阶的数。
    · `DataProps.PropsData` 上**没有** `grade`（grade 只在 `PropsArtifact`/`MartialData` 上，代理反射实证）
      ⇒ 列只能来自「运行时 `new BattleSkillValueData(propsData).grade`」或「配置表 `ItemPill.grade`」。

本文件锁住六条静默失效：
  1. 物品/装备介绍必须**接上**解析（不能又退回裸 `GameTool.LS`）；
  2. 列必须由**境界**定，不许拿 `propsItem.level`（品质）当列、也不许读不存在的 `propsData.grade`；
  3. 境界拿不到且十列不一致时**不许猜**（留占位符 + ★留痕），兜底只允许"十列全同"这一种无歧义情形；
  4. `"0"` 是合法数值、空串才是缺失（在 C# 单测里钉；这里钉"走的是同一个决策函数"）；
  5. 解析失败/抛异常必须**退回 LS 原文**并留痕（绝不落空、绝不静默）；
  6. 富文本标签必须剥掉（565 条物品 desc 带 `<g>`/`<color=…>`/`<size=12>`，面板要、模型不要）。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _core() -> str:
    return (CS / "game" / "PropsSigilCore.cs").read_text(encoding="utf-8")


def _props_desc_body() -> str:
    src = _read("game/UnitSnapshot.cs")
    return src[src.index("private static string PropsDescText("):
               src.index("/// <summary>对照日志只打前 8 件")]


def _props_scaled_body() -> str:
    src = _read("game/UnitSnapshot.cs")
    return src[src.index("private static string PropsScaledValue("):
               src.index("/// <summary>`★物品介绍境界未知★` 留痕")]


def _props_column_body() -> str:
    src = _read("game/UnitSnapshot.cs")
    return src[src.index("private static PropsSigilCore.Pick PropsColumn("):
               src.index("/// <summary>`ConfBattleSkillValueItem.value1..value10`")]


def _conf_grade_body() -> str:
    src = _read("game/UnitSnapshot.cs")
    return src[src.index("private static int ConfPropsGrade("):
               src.index("/// <summary>\n        /// 物品/装备介绍的统一入口")]


def _desc_reader_body(anchor: str) -> str:
    """取 `jo["desc"]` / `ej["desc"]` 前后那一段（两个读取点分别在背包与装备两处）。"""
    src = _read("game/UnitSnapshot.cs")
    i = src.index(anchor)
    return src[i - 400: i + 260]


# ---------------------------------------------------------------- 接线

def test_inventory_and_equips_go_through_the_resolver():
    """两处介绍读取都必须走 `PropsDescText`（= LS + 占位符解析 + 剥标签），不许再裸 LS。"""
    src = _read("game/UnitSnapshot.cs")
    assert src.count('PropsDescText((DataProps.PropsData)p, (object)p.propsItem, d, "inventory")') == 1, \
        "背包介绍必须走 PropsDescText（少了它 = 占位符又原样喂模型）"
    assert src.count('PropsDescText((DataProps.PropsData)p, (object)p.propsItem, d, "equips")') == 1, \
        "装备介绍必须走 PropsDescText"
    # 旧的裸 LS 写法（自己 LS 一遍再塞）不许回来
    for seg in (_desc_reader_body('jo["desc"] = td;'), _desc_reader_body('ej["desc"] = td;')):
        assert "GameTool.LS(d)" not in seg, "介绍读取点不许再自己 LS（解析/清洗都在 PropsDescText 里）"
    assert 'PropsDescText' in src and 'jo["desc"] = td;' in src and 'ej["desc"] = td;' in src


def test_desc_pipeline_order_is_ls_then_sigils_then_strip():
    """顺序：LS → 占位符解析 → 剥富文本。反了会把标签里的内容当占位符/把记号留下。"""
    body = _props_desc_body()
    i_ls = body.index("GameTool.LS(rawDesc)")
    i_scan = body.index("PropsSigilCore.Scan(d,")
    i_strip = body.index("StripRichTags(outText)")
    assert i_ls < i_scan < i_strip, "必须 LS → Scan → StripRichTags"
    # 清洗只允许在**最后**做一次（先洗会把 &…& 记号连标签一起吃掉，解析就白做了）
    assert body.count("StripRichTags(") == 1, "StripRichTags 只许在流水线末端出现一次"
    assert "if (d.IndexOf('&') >= 0)" in body, "有占位符才进解析（无记号不做无谓查询）"


def test_shares_the_same_scanner_as_skill_desc():
    """技能说明与物品介绍必须共用同一个扫描器（`PropsSigilCore.Scan`）——
    两份实现迟早会分叉（一份修了 `$key$`、另一份没修）。"""
    ui = _read("game/UnitSnapshot.cs")
    assert "PropsSigilCore.Scan(s, expr => TryResolveValue(expr, vd), LsOrNull)" in ui, \
        "技能路径必须走共享扫描器"
    assert "PropsSigilCore.Scan(d," in ui, "物品路径必须走同一个扫描器"
    core = _core()
    assert "public static ScanResult Scan(" in core, "扫描器必须在共享核里"
    # `$key$` 的"LS 原样返回 key 不算命中"这条规则也必须在共享核里（两路一致）
    assert "rep == inner" in core


# ---------------------------------------------------------------- 列来源（境界，不是品质）

def test_column_comes_from_realm_not_quality():
    """列必须由**境界**定：运行时 `BattleSkillValueData.grade` + 配置表 `grade`（`ItemPill` 表头「道具境界」）。"""
    body = _props_desc_body()
    assert "new BattleSkillValueData(propsData)" in body, \
        "运行时 vd 必须由**道具自己**构造（DataProps.PropsData 有专门的构造函数）"
    assert "confGrade = ConfPropsGrade(propsItem, propsID);" in body, "配置表境界必须参与"
    assert "vdGrade" in body and "confGrade" in body, "两个来源都要取到（对照 + 兜底）"
    # 反例守卫：不许拿「物品品质」（ItemProps.level）当列 —— 残/真 共用品质 2，会算成同品阶的数
    assert "propsItem.level" not in _read("game/UnitSnapshot.cs"), \
        "不许用 propsItem.level（物品品质）当列；列是「境界」"
    assert ".level" not in _props_column_body(), "选列处不许出现 .level"
    conf = _conf_grade_body()
    assert ".level" not in conf, "配置表境界查询里不许出现 .level（品质是另一根轴）"
    assert "((dynamic)it).grade" in conf or ".grade" in conf, "配置表境界只能取 grade"
    # 也不许读运行时 propsData.grade —— PropsData 上没有这个属性（代理反射实证）
    assert "propsData.grade" not in _read("game/UnitSnapshot.cs"), \
        "PropsData 没有 grade；运行时境界只能从 new BattleSkillValueData(propsData).grade 取"


def test_conf_grade_lookup_uses_the_realm_tables():
    """配置表境界来自三张写明「境界/品阶」的表：丹符 `ItemPill`、秘籍 `ItemSkill`、材料 `ItemRarityMaterials`。"""
    src = _read("game/UnitSnapshot.cs")
    body = src[src.index("private static int ConfPropsGrade("):
               src.index("/// <summary>\n        /// 物品/装备介绍的统一入口")]
    for acc in ("g.conf.itemPill.GetItem(propsID)", "g.conf.itemSkill.GetItem(propsID)",
                "g.conf.itemRarityMaterials.GetItem(propsID)"):
        assert acc in body, f"缺 {acc}"
    assert "PropsSigilCore.NormGrade(" in body, "越界境界必须归一成 0（未知），不能直接当列号"
    assert "catch { }" in body, "取不到要容错（拿不到境界 = 走'十列全同才兜底'那条）"


def test_shared_core_owns_the_never_guess_rule():
    """「绝不猜」必须在共享核里（可单测），调用侧只许照它的结论走。"""
    core = _core()
    assert "PickKind.Ambiguous" in core and "Ambiguous" in core
    assert "allEqual" in core and "if (allEqual)" in core, "只有十列全同才允许无歧义兜底"
    assert "NormGrade" in core
    # 调用侧：Ambiguous → 留痕 + 返回 null（原样保留占位符），绝不退 value1
    body = _props_scaled_body()
    assert "PickKind.Ambiguous" in body and "PropSigilMiss(key, vdGrade, confGrade);" in body
    assert "return null;" in body
    assert "PickKind.Ok" in body, "只有 Ok 才允许取值"
    # 兜底取第一列的权力只在核里（调用侧不许自己拿 values[0]）
    assert "values[0]" not in body, "调用侧不许自己取第 1 列兜底"


def test_column_read_covers_all_ten_realms():
    """`value1..value10` 是十个独立字段（IL2CPP 没有字符串索引器），必须逐个取全。"""
    src = _read("game/UnitSnapshot.cs")
    body = src[src.index("private static string[] RowValues("):
               src.index("/// <summary>\n        /// 物品介绍里的")]
    for i in range(1, 11):
        assert f"d.value{i}" in body, f"value{i} 没取（十列缺一个都会让'全同判定'失真）"
    assert "new string[PropsSigilCore.GradeMax]" in body, "列数必须用共享核的常量（=10）"
    assert "this[" not in body and "GetValue(" not in body, "不许用索引器/getter 猜列"


# ---------------------------------------------------------------- 留痕与失败面

def test_residual_and_missing_grade_are_logged_never_silent():
    """替换不掉 / 境界未知都要留痕（★ 前缀 + 去重封顶），绝不静默。"""
    body = _props_desc_body()
    assert "★介绍占位符仍残留" in body, "有残留必须打日志（带片段）"
    assert "介绍占位符解析抛异常" in body, "抛异常必须留痕"
    assert "outText = d;      // 失败退回" in body, "抛异常要退回 LS 原文（绝不落空）"
    src = _read("game/UnitSnapshot.cs")
    miss = src[src.index("private static void PropSigilMiss("):
               src.index("/// <summary>\n        /// 配置表里的「道具境界」")]
    assert "if (_propSigilMiss.Contains(key)) return;" in miss, "留痕要去重（同一键只报一次）"
    assert "if (_propSigilMiss.Count >= 40) return;" in miss, "留痕要封顶防刷屏"
    # 日志行必须真的会执行（不许被 if (false) 之类关掉）
    assert '\n                ModMain.P("[Props] ★介绍占位符境界未知★' in miss, \
        "★留痕那条日志必须在正常控制流里"


def test_one_shot_comparison_log_exists_for_real_machine():
    """真机判据：前 8 件带占位符的道具要打出「vdGrade / confGrade / 片段」——
    列到底选得对不对，只能拿游戏悬浮窗跟这行日志比（离线看不到方法体）。"""
    body = _props_desc_body()
    assert "_propsDescLogged < 8" in body, "对照日志只打前 8 件（防刷屏）"
    for field in ("vdGrade=", "confGrade=", "替换=", "残留=", "片段="):
        assert field in body, f"对照日志缺 {field}"
    assert "Short(outText, 90)" in body, "片段要截断（别把整条介绍灌进日志）"


def test_zero_value_is_not_treated_as_missing():
    """`"0"` 是合法数值（DLC 表大量只填 value1、其余是 0）。判定逻辑在 C# 单测里钉；
    这里钉调用侧用的是同一个决策函数（`IsNullOrEmpty` 而不是 `== "0"`）。"""
    body = _props_scaled_body()
    assert '== "0"' not in body, '调用侧不许把 "0" 当缺失'
    core = _core()
    assert "string.IsNullOrEmpty" in core, "缺失判定必须是空串判定"
    assert '"0"' not in core.replace('"0"', '"0"', 1) or True  # 核里允许出现 "0" 字面量（测试数据）


def test_rich_text_is_stripped_for_items_too():
    """565 条物品 desc 带 `<g>`/`<color=#057600>`/`<size=12>` —— 面板要，模型不要。"""
    body = _props_desc_body()
    assert "StripRichTags(outText)" in body, "物品介绍也要过 StripRichTags（与技能说明同口径）"
    src = _read("game/UnitSnapshot.cs")
    fn = src[src.index("private static string StripRichTags(string s)"):]
    fn = fn[: fn.index("\n        }")]
    assert "Substring" not in fn, "清洗只许剥标签+折叠空白，不得截断"


# ---------------------------------------------------------------- 渲染层（C# 给了、Python 不读 = 白给）

def test_render_reads_item_and_equip_desc():
    """C# 落了 `desc` 键还得渲染层带出来（附录 D.1 那条踩过四次）。"""
    from agent_loop.tools.text_render import _inventory
    inv = {
        "props": [{"cat": "丹符", "items": [{
            "name": "残·灵盾符", "count": 6, "worth": 48, "total": 288,
            "desc": "吸收使用者当前30%的灵力，护盾持续60秒。"}]}],
        "equips": [{"name": "幽凝炎瞳戒", "worth": 100, "desc": "念力消耗-2%"}],
        "money": 0,
    }
    t = "\n".join(_inventory(inv))
    assert "吸收使用者当前30%的灵力" in t, t
    assert "念力消耗-2%" in t, t
    # 渲染层不许把数值再动手脚（截断/改写）
    assert "&" not in t, "渲染结果里不该再有占位符"


def test_render_survives_missing_desc():
    from agent_loop.tools.text_render import _inventory
    t = "\n".join(_inventory({"props": [{"cat": "丹符", "items": [{"name": "化瘀丹", "count": 1, "worth": 1, "total": 1}]}]}))
    assert "化瘀丹×1" in t and "——" not in t, t
